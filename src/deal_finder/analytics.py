"""Market-wide numbers for the Insights page: what is on sale, where, and how
prices have moved lately. Everything is computed from the cached catalogue and
the price history, so it costs nothing to show."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone

from deal_finder.db import DB
from deal_finder.deals import mix_stores, sale_shares, score_row
from deal_finder.stores import BY_KEY

DAYS = 30
TOP = 8
BUCKETS = [(0, 20, "Under 20%"), (20, 30, "20–29%"), (30, 40, "30–39%"),
           (40, 50, "40–49%"), (50, 60, "50–59%"), (60, 101, "60%+")]

# A drop is a price lower than the one recorded before it for the same product.
DROPS_SQL = """
SELECT substr(at, 1, 10) AS day, COUNT(*) AS drops FROM (
    SELECT at, price, LAG(price) OVER (PARTITION BY product_id ORDER BY at) AS prev FROM prices
) t
WHERE prev IS NOT NULL AND price < prev - 0.005 AND at >= ?
GROUP BY substr(at, 1, 10)
"""


def _off(d: dict) -> int:
    return max(d["discount_pct"], d["drop_pct"])


def _avg(values: list[int]) -> int:
    return round(sum(values) / len(values)) if values else 0


def _breakdown(items: list[dict], key: str) -> dict[str, dict]:
    groups: dict[str, dict] = defaultdict(lambda: {"items": 0, "on_sale": 0, "offs": []})
    for d in items:
        g = groups[d[key]]
        g["items"] += 1
        if _off(d):
            g["on_sale"] += 1
            g["offs"].append(_off(d))
    return groups


def _drops_by_day(db: DB, at: datetime) -> list[dict]:
    start = (at - timedelta(days=DAYS - 1)).date()
    counts = {r["day"]: r["drops"] for r in db.query(DROPS_SQL, (start.isoformat(),))}
    days = [(start + timedelta(days=i)).isoformat() for i in range(DAYS)]
    return [{"day": d, "drops": int(counts.get(d, 0))} for d in days]


def summary(db: DB, at: datetime | None = None) -> dict:
    at = at or datetime.now(timezone.utc)
    shares = sale_shares(db)
    items = [score_row(r, shares, at) for r in db.catalogue() if r["in_stock"]]
    on_sale = [d for d in items if _off(d)]

    stores = [
        {"key": k, "name": BY_KEY[k].name if k in BY_KEY else k, "tier": BY_KEY[k].tier if k in BY_KEY else "mid",
         "items": g["items"], "on_sale": g["on_sale"], "avg_discount": _avg(g["offs"])}
        for k, g in _breakdown(items, "store").items()
    ]
    categories = [
        {"category": c, "items": g["items"], "on_sale": g["on_sale"], "avg_discount": _avg(g["offs"])}
        for c, g in _breakdown(items, "category").items() if c
    ]
    ranked = sorted(items, key=lambda d: (-d["score"], -d["saving"]))
    drops = sorted((d for d in items if d["drop_pct"]), key=lambda d: (-d["drop_pct"], -d["score"]))

    return {
        "totals": {
            "tracked": len(items),
            "on_sale": len(on_sale),
            "great": sum(1 for d in items if d["label"] == "Great deal"),
            "savings": round(sum(d["saving"] for d in items), 2),
            "avg_discount": _avg([_off(d) for d in on_sale]),
            "best_discount": max((_off(d) for d in items), default=0),
            "shops": len(stores),
        },
        "stores": sorted(stores, key=lambda s: (-s["on_sale"], s["name"])),
        "categories": sorted(categories, key=lambda c: (-c["on_sale"], c["category"])),
        "discounts": [{"label": label, "count": sum(1 for d in on_sale if lo <= _off(d) < hi)}
                      for lo, hi, label in BUCKETS],
        "drops_by_day": _drops_by_day(db, at),
        "recent_drops": drops[:TOP],
        "top_deals": mix_stores(ranked)[:TOP],
    }
