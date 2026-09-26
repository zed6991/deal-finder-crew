from datetime import datetime, timedelta, timezone

import pytest

from deal_finder.deals import Filters, mix_stores, score_row, search, size_matches
from deal_finder.normalize import from_shopify
from deal_finder.stores import BY_KEY

from .conftest import raw


def at(days_ago):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(timespec="seconds")


def load(db, store, raws, when=None):
    s = BY_KEY[store]
    return db.record(store, [from_shopify(s, r) for r in raws], at=when)


def test_record_tracks_price_changes(db):
    load(db, "mjbale", [raw("coat", "Wool Coat", 500, ptype="Outerwear")], at(10))
    result = load(db, "mjbale", [raw("coat", "Wool Coat", 350, 500, ptype="Outerwear")], at(2))
    assert result["changed"] == 1
    row = db.product("mjbale:coat")
    assert (row["price"], row["prev_price"], row["low_price"], row["high_price"]) == (350, 500, 350, 500)
    assert [h["price"] for h in db.history("mjbale:coat")] == [500, 350]

    d = score_row(row, {})
    assert d["drop_pct"] == 30 and d["discount_pct"] == 30
    assert "Just dropped" in d["badges"] and "Lowest price seen" in d["badges"]


def test_products_that_vanish_are_marked_inactive(db):
    load(db, "mjbale", [raw("a", "Oxford Shirt", 99), raw("b", "Linen Shirt", 99)])
    assert load(db, "mjbale", [raw("a", "Oxford Shirt", 99)])["removed"] == 1
    assert db.product("mjbale:b")["active"] == 0
    assert search(db, Filters())["total"] == 1


def test_storewide_sales_count_for_half(db):
    row = {"id": "x", "store": "gazman", "brand": "Gazman", "title": "Shirt", "url": "u", "image": None,
           "category": "Shirts", "colour": None, "composition": "100% cotton", "fabric": "natural",
           "sizes": "[]", "price": 50.0, "was_price": 100.0, "in_stock": 1}
    honest = score_row(row, {"gazman": 0.3})
    inflated = score_row(row, {"gazman": 0.9})
    assert inflated["score"] < honest["score"]
    assert "Store-wide sale" in inflated["badges"]


def test_score_prefers_real_savings(db):
    load(db, "mjbale", [
        raw("sq", "Pocket Square", 20, 50, ptype="Accessories", tags=["comp:100% Silk"]),
        raw("coat", "Wool Coat", 300, 750, ptype="Outerwear", tags=["comp:100% Wool"]),
    ])
    items = search(db, Filters())["items"]
    assert items[0]["title"] == "Wool Coat"


def test_search_filters(db):
    load(db, "mjbale", [
        raw("navy-knit", "Navy Merino Crew", 99, 199, ptype="Knitwear", tags=["comp:100% Merino"]),
        raw("grey-knit", "Grey Crew", 99, 110, ptype="Knitwear", tags=["comp:80% Wool 20% Nylon"]),
        raw("chino", "Stone Chino", 89, 149, ptype="Trousers", sizes=("30", "34")),
        raw("gone", "Sold Out Crew", 50, 100, ptype="Knitwear", available=False),
    ])
    titles = lambda f, sizes=None: [d["title"] for d in search(db, f, sizes)["items"]]  # noqa: E731
    assert set(titles(Filters(categories=["Knitwear"]))) == {"Navy Merino Crew", "Grey Crew"}
    assert titles(Filters(min_discount=30, categories=["Knitwear"])) == ["Navy Merino Crew"]
    assert titles(Filters(fabric="natural", categories=["Knitwear"])) == ["Navy Merino Crew"]
    assert titles(Filters(q="navy merino")) == ["Navy Merino Crew"]
    assert titles(Filters(categories=["Trousers"], my_sizes=True), {"bottoms": "32"}) == []
    assert titles(Filters(categories=["Trousers"], my_sizes=True), {"bottoms": "32, 34"}) == ["Stone Chino"]
    assert "Sold Out Crew" not in titles(Filters())


@pytest.mark.parametrize(("wanted", "available", "ok"), [
    ("M", ["S", "M", "L"], True),
    ("m", ["S", "M"], True),
    ("XL", ["S", "M"], False),
    ("32", ["30", "32R"], True),
    ("40, 100", ["96", "100"], True),
    ("9", ["UK 9", "UK 10"], True),
    ("9", ["39", "40"], False),
    ("M", [], True),
])
def test_size_matches(wanted, available, ok):
    assert size_matches(wanted, available) is ok


def test_mix_stores_interleaves_but_keeps_order_within_a_store():
    items = [{"store": "a", "score": 90 - i} for i in range(5)] + [{"store": "b", "score": 80}]
    mixed = mix_stores(items)
    assert [d["store"] for d in mixed[:4]].count("b") == 1
    assert [d["score"] for d in mixed if d["store"] == "a"] == [90, 89, 88, 87, 86]
