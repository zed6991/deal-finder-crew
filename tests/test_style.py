import json

from deal_finder import style
from deal_finder.deals import Filters, search
from deal_finder.normalize import from_shopify
from deal_finder.stores import BY_KEY
from deal_finder.style import formality_range, rule_style, style_of

from .conftest import raw


def labels(category, title, colour="", composition="", store="peterjackson", fabric="unknown"):
    return rule_style(category, title, colour, composition, store, fabric)


def test_rules_label_looks():
    knit = labels("Knitwear", "Cashmere Crew Neck Knit", "Camel", "100% Cashmere", "mjbale", "natural")
    assert "old_money" in knit["aesthetics"] and "streetwear" not in knit["aesthetics"]
    hoodie = labels("Sweats", "Logo Hoodie", "Black")
    assert hoodie["aesthetics"] == ["streetwear"] and hoodie["patterned"]
    suit = labels("Suits", "Navy Wool Suit", "Navy", "100% Wool", "mjbale", "natural")
    assert "tailored" in suit["aesthetics"] and suit["formality"] == 5
    assert "coastal" in labels("Shirts", "Resort Camp Collar Shirt", "White", "100% Linen", "venroy", "natural")["aesthetics"]
    assert "rugged" in labels("Jeans", "Slim Selvedge Jean", "Indigo")["aesthetics"]
    assert "minimalist" in labels("T-shirts", "Organic Heavyweight Tee", "Black", store="bassike")["aesthetics"]


def test_rules_label_occasions_by_formality():
    assert labels("Shoes", "Leather Derby", "Black")["formality"] == 4
    assert labels("Shoes", "Leather Sneaker", "White")["formality"] == 2
    assert labels("Shoes", "Odin Grey Derby Sneakers")["formality"] == 2
    assert labels("Trousers", "Pleated Wool Trouser")["formality"] == 4
    assert labels("Trousers", "Drawstring Jogger")["formality"] == 1
    shorts = labels("Shorts", "Linen Short")
    assert "office" not in shorts["occasions"] and "holiday" in shorts["occasions"]
    shirt = labels("Shirts", "Business Shirt", "White")
    assert {"office", "wedding"} <= set(shirt["occasions"]) and "holiday" not in shirt["occasions"]
    assert labels("Accessories", "Silk Tie")["occasions"] == ["office", "wedding"]


def test_model_labels_replace_rule_labels():
    row = {"category": "T-shirts", "title": "Tee", "store": "academy",
           "ai_style": json.dumps({"aesthetics": ["minimalist"], "occasions": ["weekend"], "formality": 1, "patterned": False})}
    s = style_of(row)
    assert s["aesthetics"] == ["minimalist"] and s["source"] == "ai"
    assert style_of({**row, "ai_style": "not json"})["source"] == "rules"


def test_formality_range_prefers_the_occasion():
    assert formality_range("old_money", "office") == (3, 5)
    assert formality_range("streetwear", "wedding") == (4, 5)  # no overlap: dress for the occasion
    assert formality_range(None, None) == (1, 5)


def seed(db):
    s = BY_KEY["mjbale"]
    db.record("mjbale", [from_shopify(s, r) for r in [
        raw("knit", "Cashmere Crew Knit", 200, 400, ptype="Knitwear", colour="Camel", tags=["comp:100% Cashmere"]),
        raw("hood", "Logo Hoodie", 60, 120, ptype="Sweats", colour="Black"),
        raw("short", "Linen Short", 50, 100, ptype="Shorts", colour="White"),
    ]])


def test_deals_filter_by_look_and_occasion(db):
    seed(db)
    titles = lambda **kw: [d["title"] for d in search(db, Filters(**kw))["items"]]  # noqa: E731
    assert titles(style="old_money") == ["Cashmere Crew Knit"]
    assert titles(style="streetwear") == ["Logo Hoodie"]
    assert "Linen Short" not in titles(occasion="office")
    assert titles(occasion="holiday") == ["Linen Short"]
    assert search(db, Filters())["items"][0]["style"]["source"] == "rules"


class FakeOpenRouter:
    """Stands in for the Anthropic SDK client pointed at OpenRouter."""

    def __init__(self, labels):
        self.calls = []
        outer = self

        class Messages:
            def create(self, **kwargs):
                outer.calls.append(kwargs)
                lines = kwargs["messages"][0]["content"].splitlines()
                items = [labels(i, line) for i, line in enumerate(lines)]
                block = type("B", (), {"type": "tool_use", "input": {"items": items}})()
                return type("R", (), {"content": [block]})()

        self.messages = Messages()


def use_fake(monkeypatch, labels):
    import anthropic

    fake = FakeOpenRouter(labels)
    made = []
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: made.append(kw) or fake)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test")
    return fake, made


def test_model_labels_are_stored_once_and_used(db, monkeypatch):
    seed(db)
    fake, made = use_fake(monkeypatch, lambda i, line: {
        "i": i, "aesthetics": ["coastal"] if "Short" in line else [], "occasions": ["holiday", "weekend"], "formality": 2,
    })
    result = style.label_new(db)
    assert result["labelled"] == 3 and len(fake.calls) == 1
    assert made[0]["base_url"] == style.OPENROUTER_URL and made[0]["auth_token"] == "or-test"
    assert fake.calls[0]["model"] == style.LABEL_MODEL
    assert fake.calls[0]["tool_choice"] == {"type": "tool", "name": "label_products"}

    short = next(d for d in search(db, Filters())["items"] if d["title"] == "Linen Short")
    assert short["style"]["source"] == "ai" and short["style"]["aesthetics"] == ["coastal"]
    assert style.counts(db) == {"total": 3, "labelled": 3}
    assert style.used_today(db) == 3

    assert style.label_new(db)["labelled"] == 0
    assert len(fake.calls) == 1  # nothing new, no call


def test_unknown_words_from_the_model_are_dropped(db, monkeypatch):
    seed(db)
    use_fake(monkeypatch, lambda i, line: {"i": i, "aesthetics": ["goblincore", "rugged"], "occasions": ["rave"],
                                           "formality": 9 if "Hoodie" in line else 2})
    assert style.label_new(db)["labelled"] == 2  # the hoodie's formality is impossible, so the rules keep it
    knit = next(d for d in search(db, Filters())["items"] if d["title"] == "Cashmere Crew Knit")
    assert knit["style"]["aesthetics"] == ["rugged"] and knit["style"]["occasions"] == []


def test_labelling_respects_the_daily_cap(db, monkeypatch):
    seed(db)
    monkeypatch.setattr(style, "LABEL_CAP", 2)
    fake, _ = use_fake(monkeypatch, lambda i, line: {"i": i, "aesthetics": [], "occasions": [], "formality": 3})
    assert style.label_new(db)["labelled"] == 2
    assert style.label_new(db) == {"labelled": 0, "skipped": "daily cap reached"}
    assert len(fake.calls) == 1


def test_no_key_no_calls(db):
    seed(db)
    assert style.label_new(db)["labelled"] == 0
    assert not style.available()
