from datetime import datetime, timedelta, timezone

from deal_finder.analytics import summary
from deal_finder.normalize import from_shopify
from deal_finder.stores import BY_KEY

from .conftest import raw

DAY1 = "2026-09-01T00:00:00+00:00"
DAY2 = "2026-09-02T00:00:00+00:00"
AT = datetime(2026, 9, 3, tzinfo=timezone.utc)


def seed(db):
    mj = BY_KEY["mjbale"]
    db.record("mjbale", [from_shopify(mj, r) for r in [
        raw("coat", "Navy Wool Coat", 300, 700, ptype="Outerwear", tags=["comp:100% Wool"]),
        raw("shirt", "White Oxford Shirt", 60, 150, ptype="Shirts"),
        raw("tee", "Plain Tee", 40, ptype="T-shirts"),
    ]], at=DAY1)
    # The tee drops from $40 to $30 on day two.
    db.record("mjbale", [from_shopify(mj, r) for r in [
        raw("coat", "Navy Wool Coat", 300, 700, ptype="Outerwear", tags=["comp:100% Wool"]),
        raw("shirt", "White Oxford Shirt", 60, 150, ptype="Shirts"),
        raw("tee", "Plain Tee", 30, ptype="T-shirts"),
    ]], at=DAY2)


def test_totals_and_breakdowns(db):
    seed(db)
    s = summary(db, at=AT)
    t = s["totals"]
    assert t["tracked"] == 3 and t["on_sale"] == 3
    assert t["savings"] == 400 + 90 + 10
    assert t["best_discount"] == 60  # the shirt: $150 down to $60

    mj = next(x for x in s["stores"] if x["key"] == "mjbale")
    assert mj["items"] == 3 and mj["on_sale"] == 3 and mj["name"] == "M.J. Bale"
    cats = {c["category"]: c for c in s["categories"]}
    assert cats["Outerwear"]["avg_discount"] == 57 and cats["T-shirts"]["on_sale"] == 1

    buckets = {b["label"]: b["count"] for b in s["discounts"]}
    assert buckets["20–29%"] == 1 and buckets["50–59%"] == 1 and buckets["60%+"] == 1


def test_price_drops_by_day_and_top_lists(db):
    seed(db)
    s = summary(db, at=AT)
    days = {d["day"]: d["drops"] for d in s["drops_by_day"]}
    assert len(days) == 30 and days["2026-09-02"] == 1 and days["2026-09-01"] == 0
    assert [d["title"] for d in s["recent_drops"]] == ["Plain Tee"]
    assert s["top_deals"][0]["title"] == "Navy Wool Coat"


def test_empty_catalogue(db):
    s = summary(db, at=AT)
    assert s["totals"]["tracked"] == 0 and s["stores"] == [] and s["top_deals"] == []
    assert sum(d["drops"] for d in s["drops_by_day"]) == 0


def test_old_drops_fall_outside_the_window(db):
    seed(db)
    later = AT + timedelta(days=60)
    assert sum(d["drops"] for d in summary(db, at=later)["drops_by_day"]) == 0
