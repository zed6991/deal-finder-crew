"""Score products as deals, and search them.

A deal score (0-100) rewards a genuine markdown on a well-made piece:

* Markdown, up to 45 points, reaching the maximum at 60% off. The markdown is
  the larger of the shop's "was" price discount and any drop we saw ourselves
  in the last 14 days. Shops that mark most of their range down (Gazman,
  Oxford) have their "was" discounts counted at half, since those "was"
  prices are rarely charged.
* Dollars saved, up to 10 points at $200 or more, so a coat at 40% off beats
  a pocket square at 60% off.
* Lowest price we have recorded, once we have tracked it for 3+ days: 15.
* Fabric: natural 15, natural with a little stretch 9, unknown 5, synthetic 0.
* Shop tier: premium 15, mid 8.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

from deal_finder.db import DB, parse_time
from deal_finder.stores import BY_KEY

STOREWIDE_SHARE = 0.6
RECENT_DROP = timedelta(days=14)
TRACKED_ENOUGH = timedelta(days=3)

SIZE_GROUPS = {
    "tops": ["Shirts", "Polos", "T-shirts", "Knitwear", "Sweats", "Outerwear"],
    "bottoms": ["Trousers", "Jeans", "Shorts", "Swim"],
    "tailoring": ["Suits", "Tailoring"],
    "shoes": ["Shoes"],
}
GROUP_OF = {c: g for g, cats in SIZE_GROUPS.items() for c in cats}


@dataclass
class Filters:
    q: str = ""
    categories: list[str] = field(default_factory=list)
    stores: list[str] = field(default_factory=list)
    min_discount: int = 0
    max_price: float | None = None
    fabric: str = "any"  # any | natural | stretch (natural + stretch)
    my_sizes: bool = False
    premium_only: bool = False
    include_storewide: bool = True
    in_stock: bool = True
    sort: str = "score"  # score | discount | price | price_desc | newest
    limit: int = 60
    offset: int = 0


def sale_shares(db: DB) -> dict[str, float]:
    return {
        k: (r["on_sale"] or 0) / r["count"]
        for k, r in db.syncs().items()
        if r["count"]
    }


def size_matches(wanted: str, available: list[str]) -> bool:
    """Loose size match: 'M' fits 'M', '32' fits '32R', '9' fits 'UK 9'."""
    if not available:
        return True  # one size, or the shop does not say
    norm = lambda s: re.sub(r"[^a-z0-9.]", "", s.lower())  # noqa: E731
    have = [norm(a) for a in available]
    for want in filter(None, (norm(w) for w in re.split(r"[,/]", wanted))):
        for h in have:
            if h == want or (want[0].isdigit() and re.fullmatch(rf"(uk|us|au|eu)?{re.escape(want)}[a-z]{{0,2}}", h)):
                return True
    return False


def score_row(row: sqlite3.Row | dict, shares: dict[str, float], at: datetime | None = None) -> dict:
    """Everything the UI shows about one product, including its deal score."""
    at = at or datetime.now(timezone.utc)
    r = dict(row)
    store = BY_KEY.get(r["store"])
    price, was = r["price"], r["was_price"]
    was_pct = round((was - price) / was * 100) if was and was > price else 0
    storewide = shares.get(r["store"], 0) >= STOREWIDE_SHARE

    drop_pct = 0
    changed = parse_time(r.get("price_changed_at"))
    prev = r.get("prev_price")
    just_dropped = bool(prev and prev > price and changed and at - changed <= RECENT_DROP)
    if just_dropped:
        drop_pct = round((prev - price) / prev * 100)

    effective = max(was_pct * (0.5 if storewide else 1), drop_pct)
    first = parse_time(r.get("first_seen"))
    tracked = bool(first and at - first >= TRACKED_ENOUGH)
    lowest = tracked and r.get("low_price") is not None and price <= r["low_price"] + 0.005

    saved = max((was - price) * (0.5 if storewide else 1) if was_pct else 0,
                (prev - price) if just_dropped else 0)
    pts = 45 * min(effective, 60) / 60
    pts += 10 * min(saved, 200) / 200
    pts += 15 if lowest else 0
    pts += {"natural": 15, "stretch": 9, "unknown": 5}.get(r.get("fabric"), 0)
    pts += 15 if store and store.tier == "premium" else 8
    score = round(pts)

    badges = []
    if just_dropped:
        badges.append("Just dropped")
    if lowest and (was_pct or drop_pct):
        badges.append("Lowest price seen")
    if storewide and was_pct:
        badges.append("Store-wide sale")

    return {
        "id": r["id"],
        "store": r["store"],
        "store_name": store.name if store else r["store"],
        "tier": store.tier if store else "mid",
        "brand": r["brand"],
        "title": r["title"],
        "url": r["url"],
        "image": r["image"],
        "category": r["category"],
        "colour": r["colour"],
        "composition": r["composition"],
        "fabric": r["fabric"],
        "sizes": json.loads(r["sizes"] or "[]") if isinstance(r["sizes"], str) else r["sizes"] or [],
        "price": price,
        "was_price": was if was_pct else None,
        "discount_pct": was_pct,
        "drop_pct": drop_pct,
        "saving": round(max(was - price if was_pct else 0, prev - price if just_dropped else 0), 2),
        "prev_price": prev if just_dropped else None,
        "low_price": r.get("low_price"),
        "in_stock": bool(r["in_stock"]),
        "first_seen": r.get("first_seen"),
        "score": score,
        "label": "Great deal" if score >= 75 else "Good deal" if score >= 55 else "Fair",
        "badges": badges,
    }


def search(db: DB, f: Filters, sizes: dict[str, str] | None = None) -> dict:
    """Filter in SQL, score in Python, then sort and page."""
    where, args = ["active = 1"], []
    if f.in_stock:
        where.append("in_stock = 1")
    if f.categories:
        where.append(f"category IN ({','.join('?' * len(f.categories))})")
        args += f.categories
    stores = [s for s in f.stores if s in BY_KEY]
    if f.premium_only:
        premium = [k for k, s in BY_KEY.items() if s.tier == "premium"]
        stores = [s for s in stores if s in premium] if stores else premium
    if stores:
        where.append(f"store IN ({','.join('?' * len(stores))})")
        args += stores
    if f.max_price:
        where.append("price <= ?")
        args.append(f.max_price)
    if f.fabric == "natural":
        where.append("fabric = 'natural'")
    elif f.fabric == "stretch":
        where.append("fabric IN ('natural', 'stretch')")
    for word in f.q.split()[:8]:
        where.append("(title LIKE ? OR brand LIKE ? OR colour LIKE ? OR category LIKE ? OR composition LIKE ?)")
        args += [f"%{word}%"] * 5

    rows = db.query(f"SELECT * FROM products WHERE {' AND '.join(where)}", args)
    shares = sale_shares(db)
    at = datetime.now(timezone.utc)
    items = []
    for row in rows:
        d = score_row(row, shares, at)
        if max(d["discount_pct"], d["drop_pct"]) < f.min_discount:
            continue
        if not f.include_storewide and "Store-wide sale" in d["badges"] and not d["drop_pct"]:
            continue
        if f.my_sizes and sizes:
            wanted = sizes.get(GROUP_OF.get(d["category"], ""), "")
            if wanted and not size_matches(wanted, d["sizes"]):
                continue
        items.append(d)

    keys = {
        "score": lambda d: (-d["score"], -d["saving"]),
        "discount": lambda d: (-max(d["discount_pct"], d["drop_pct"]), -d["score"]),
        "price": lambda d: (d["price"], -d["score"]),
        "price_desc": lambda d: (-d["price"], -d["score"]),
        "newest": lambda d: (d["first_seen"] or "", d["score"]),
    }
    items.sort(key=keys.get(f.sort, keys["score"]), reverse=f.sort == "newest")
    if f.sort == "score" and not f.stores:
        items = mix_stores(items)
    return {
        "total": len(items),
        "items": items[f.offset: f.offset + f.limit],
    }


def mix_stores(items: list[dict], penalty: float = 4.0) -> list[dict]:
    """Interleave shops so one big sale does not fill the first pages.

    Each item already shown from a shop costs its next item `penalty` points.
    Items arrive sorted by score, so each shop's queue stays in order.
    """
    queues: dict[str, list[dict]] = {}
    for d in items:
        queues.setdefault(d["store"], []).append(d)
    shown = {k: 0 for k in queues}
    heads = {k: 0 for k in queues}
    out = []
    while len(out) < len(items):
        best = max(
            (k for k in queues if heads[k] < len(queues[k])),
            key=lambda k: queues[k][heads[k]]["score"] - penalty * shown[k],
        )
        out.append(queues[best][heads[best]])
        heads[best] += 1
        shown[best] += 1
    return out


def as_dict(f: Filters) -> dict:
    return asdict(f)
