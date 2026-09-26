import pytest

from deal_finder import stylist
from deal_finder.deals import Filters
from deal_finder.normalize import from_shopify
from deal_finder.stores import BY_KEY
from deal_finder.stylist import Plan, Slot, build_outfit, plan_for, plan_free

from .conftest import raw


@pytest.fixture(autouse=True)
def _no_real_openrouter_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)


def test_presets():
    assert plan_free("smart casual dinner").look == "Smart casual"
    assert plan_free("Summer wedding in Byron").look == "Wedding guest"
    assert plan_free("office").look == "Office"
    assert plan_free("something nice").look == "Smart casual"
    plan = plan_free("weekend, navy and olive")
    assert plan.look == "Weekend" and plan.slots[0].colours == ["navy", "olive"]


def test_written_list_keeps_each_colour_with_its_garment():
    plan = plan_free("I need a navy blazer, white shirt and brown loafers")
    assert plan.source == "keywords"
    got = [(s.category, s.keywords[0], s.colours) for s in plan.slots]
    assert got == [("Tailoring", "blazer", ["navy"]), ("Shirts", "shirt", ["white"]), ("Shoes", "loafer", ["brown"])]


def test_ai_is_skipped_without_a_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    assert plan_for("office", use_ai=True).source == "preset"


def test_ai_plans_are_cached(db, monkeypatch):
    plan = Plan(look="Cached", slots=[Slot(category="Shirts")], source="ai")
    brief = "linen for a long lunch"
    import hashlib
    key = "plan:" + hashlib.sha256(f"{stylist.MODEL}|{brief}".encode()).hexdigest()[:24]
    db.put(key, plan.model_dump())
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    assert plan_for(brief, db, use_ai=True).look == "Cached"  # no network call made


def seed(db):
    s = BY_KEY["mjbale"]
    rows = [
        raw("navy-blazer", "Navy Linen Blazer", 250, 600, ptype="Blazer"),
        raw("white-blazer", "White Linen Blazer", 150, 600, ptype="Blazer", colour="White"),
        raw("white-shirt", "White Oxford Shirt", 60, 150, ptype="Shirts", colour="White"),
        raw("blue-shirt", "Blue Oxford Shirt", 40, 150, ptype="Shirts", colour="Blue"),
        raw("loafer", "Brown Suede Loafer", 150, 300, ptype="Shoes", colour="Brown"),
        raw("sneaker", "White Sneaker", 90, 200, ptype="Shoes", colour="White"),
    ]
    db.record("mjbale", [from_shopify(s, r) for r in rows])


def test_outfit_follows_the_written_list(db):
    seed(db)
    plan = plan_free("navy blazer, white shirt and brown loafers")
    out = build_outfit(db, plan, 600, {}, Filters())
    assert [s["pick"]["title"] for s in out["slots"]] == ["Navy Linen Blazer", "White Oxford Shirt", "Brown Suede Loafer"]
    assert out["total"] == 460 and out["remaining"] == 140


def test_outfit_leaves_room_for_every_piece(db):
    seed(db)
    plan = Plan(look="x", slots=[Slot(category="Tailoring"), Slot(category="Shirts"), Slot(category="Shoes")])
    out = build_outfit(db, plan, 300, {}, Filters())
    picks = [s["pick"] for s in out["slots"]]
    assert all(picks) and out["total"] <= 300


class FakeClient:
    def __init__(self, parsed, stop="end_turn"):
        self.calls = []
        outer = self

        class Messages:
            def parse(self, **kwargs):
                outer.calls.append(kwargs)
                return type("R", (), {"stop_reason": stop, "parsed_output": parsed})()

        self.beta = type("Beta", (), {"messages": Messages()})()


def test_ai_call_shape_and_cache(db, monkeypatch):
    import anthropic

    parsed = Plan(look="Long lunch", slots=[Slot(category="Shirts", keywords=["linen"])])
    fake = FakeClient(parsed)
    monkeypatch.setattr(anthropic, "Anthropic", lambda: fake)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")

    plan = plan_for("linen for a long lunch", db, use_ai=True)
    assert plan.source == "ai" and plan.look == "Long lunch"
    call = fake.calls[0]
    assert call["model"] == stylist.MODEL
    assert call["output_format"] is Plan
    assert call["fallbacks"] == "default" and "server-side-fallback-2026-07-01" in call["betas"]
    assert call["output_config"] == {"effort": "low"}

    plan_for("linen for a long lunch", db, use_ai=True)
    assert len(fake.calls) == 1  # second ask served from the cache


def test_ai_refusal_falls_back_to_free_reading(db, monkeypatch):
    import anthropic

    monkeypatch.setattr(anthropic, "Anthropic", lambda: FakeClient(None, stop="refusal"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    assert plan_for("office", db, use_ai=True).source == "preset"


class FakeOpenRouterClient:
    def __init__(self, plan: dict):
        self.calls = []
        outer = self

        class Messages:
            def create(self, **kwargs):
                outer.calls.append(kwargs)
                block = type("B", (), {"type": "tool_use", "name": "plan_outfit", "input": plan})()
                return type("R", (), {"stop_reason": "tool_use", "content": [block]})()

        self.messages = Messages()


def test_openrouter_key_turns_ai_on_and_routes_through_openrouter(db, monkeypatch):
    import anthropic

    monkeypatch.setenv("ANTHROPIC_API_KEY", "stale")  # an OpenRouter key still wins
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test")
    fake = FakeOpenRouterClient({"look": "Long lunch", "slots": [{"category": "Shirts", "keywords": ["linen"]}]})
    made = []
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: made.append(kw) or fake)

    assert stylist.ai_available()
    plan = plan_for("linen for a long lunch", db, use_ai=True)
    assert plan.source == "ai" and plan.look == "Long lunch"
    assert made[0]["base_url"] == "https://openrouter.ai/api" and made[0]["auth_token"] == "or-test"
    call = fake.calls[0]
    assert call["model"] == stylist.openrouter_model()
    assert call["tool_choice"] == {"type": "tool", "name": "plan_outfit"}

    plan_for("linen for a long lunch", db, use_ai=True)
    assert len(fake.calls) == 1  # cached
