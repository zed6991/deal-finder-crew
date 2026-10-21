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


def test_looks_and_occasions_from_the_brief():
    plan = plan_free("old money date night")
    assert (plan.aesthetic, plan.occasion, plan.look) == ("old_money", "date_night", "Old money · Date night")
    plan = plan_free("", aesthetic="old_money")
    assert plan.look == "Old money" and plan.slots[0].category == "Knitwear"
    plan = plan_free("navy and stone", occasion="office")
    assert plan.look == "Office" and plan.slots[0].colours == ["navy", "stone"]
    assert plan_free("summer wedding").occasion == "wedding"


def test_picked_look_beats_the_ai_reading(db, monkeypatch):
    plan = Plan(look="Cached", slots=[Slot(category="Shirts")], aesthetic="streetwear", source="ai")
    import hashlib
    db.put("plan:" + hashlib.sha256(f"{stylist.ai_model()}|lunch".encode()).hexdigest()[:24], plan.model_dump())
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    assert plan_for("lunch", db, use_ai=True, aesthetic="old_money").aesthetic == "old_money"
    assert plan_for("lunch", db, use_ai=True).aesthetic == "streetwear"


def wardrobe(db):
    """Two shops, so looks can mix them."""
    for store, rows in {
        "mjbale": [
            raw("oxford", "White Oxford Shirt", 70, 150, ptype="Shirts", colour="White"),
            raw("gingham", "Gingham Check Shirt", 60, 150, ptype="Shirts", colour="Blue"),
            raw("chino", "Stone Chino", 80, 160, ptype="Trousers", colour="Stone"),
            raw("loafer", "Suede Loafer", 150, 300, ptype="Shoes", colour="Brown"),
        ],
        "aquila": [
            raw("linen", "Linen Shirt", 65, 140, ptype="Shirts", colour="Navy", vendor="Aquila"),
            raw("trouser", "Pleated Wool Trouser", 120, 240, ptype="Trousers", colour="Navy", vendor="Aquila"),
            raw("stripe", "Stripe Trouser", 70, 200, ptype="Trousers", colour="Navy", vendor="Aquila"),
            raw("derby", "Leather Derby", 160, 320, ptype="Shoes", colour="Black", vendor="Aquila"),
            raw("runner", "Track Runner Sneaker", 60, 200, ptype="Shoes", colour="White", vendor="Aquila"),
        ],
    }.items():
        db.record(store, [from_shopify(BY_KEY[store], r) for r in rows])


def test_several_different_looks_within_budget(db):
    wardrobe(db)
    plan = Plan(look="x", slots=[Slot(category="Shirts"), Slot(category="Trousers"), Slot(category="Shoes")])
    out = build_outfit(db, plan, 400, {}, Filters())
    looks = out["looks"]
    assert len(looks) >= 2
    assert all(look["total"] <= 400 and all(look["picks"]) for look in looks)
    tops = [look["picks"][0]["id"] for look in looks]
    assert len(set(tops)) == len(tops)  # each look leads with a different shirt
    assert [s["pick"]["id"] for s in out["slots"]] == [p["id"] for p in looks[0]["picks"]]
    assert out["total"] == looks[0]["total"]
    # Another look's pieces are offered as swaps.
    assert looks[1]["picks"][0]["id"] in [d["id"] for d in out["slots"][0]["alternates"]]


def test_looks_avoid_clashes_and_mix_shops(db):
    wardrobe(db)
    plan = Plan(look="x", slots=[Slot(category="Shirts"), Slot(category="Trousers"), Slot(category="Shoes")])
    for look in build_outfit(db, plan, 600, {}, Filters())["looks"]:
        assert sum(p["style"]["patterned"] for p in look["picks"]) <= 1
        assert look["shops"] == 2


def test_occasion_steers_the_picks(db):
    wardrobe(db)
    plan = plan_free("", occasion="office")
    out = build_outfit(db, plan.model_copy(update={"slots": [Slot(category="Trousers"), Slot(category="Shoes")]}), 600, {}, Filters())
    picks = [p for look in out["looks"] for p in look["picks"]]
    assert picks and all("office" in p["style"]["occasions"] for p in picks)
    assert "Track Runner Sneaker" not in [p["title"] for p in picks]
    # With no occasion, the cheap, heavily discounted runner is fair game.
    free = build_outfit(db, Plan(look="x", slots=[Slot(category="Shoes")]), 600, {}, Filters())
    assert "Track Runner Sneaker" in [look["picks"][0]["title"] for look in free["looks"]]


def test_looks_do_not_repeat_one_style_in_other_colours(db):
    s = BY_KEY["mjbale"]
    db.record("mjbale", [from_shopify(s, r) for r in [
        raw("phifer-navy", "Phifer Navy Trouser", 99, 349, ptype="Trousers"),
        raw("phifer-grey", "Phifer Charcoal Trouser", 99, 349, ptype="Trousers", colour="Charcoal"),
        raw("brazza", "Brazza Trouser", 120, 349, ptype="Trousers"),
    ]])
    looks = build_outfit(db, Plan(look="x", slots=[Slot(category="Trousers")]), 300, {}, Filters())["looks"]
    assert len(looks) >= 2
    assert {p["title"].split()[0] for p in (looks[0]["picks"][0], looks[1]["picks"][0])} == {"Phifer", "Brazza"}
