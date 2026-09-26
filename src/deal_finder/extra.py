"""Optional paid search for shops that block catalogue reads.

THE ICONIC, David Jones and Country Road are searched through Serper's Google
Shopping endpoint (about A$0.002 a query; 2,500 free on sign-up). Results are
cached for a day and queries are capped per day, so costs stay predictable.
These results carry a current price only, so they are shown beside the
catalogue deals rather than scored against them.
"""

from __future__ import annotations

import hashlib
import os
import re
from datetime import date, timedelta

import requests

from deal_finder.db import DB
from deal_finder.normalize import _MENS, _SKIP, _WOMENS, category_of
from deal_finder.stores import SEARCHED

SERPER_URL = "https://google.serper.dev/shopping"
DAILY_CAP = int(os.environ.get("DEAL_FINDER_SEARCH_CAP", "25"))
CACHE_FOR = timedelta(hours=24)


def available() -> bool:
    return bool(os.environ.get("SERPER_API_KEY"))


def _price(text: str | None) -> float | None:
    m = re.search(r"\d[\d,]*(?:\.\d+)?", (text or "").replace(" ", " "))
    return float(m.group(0).replace(",", "")) if m else None


def _store_for(source: str) -> str | None:
    s = source.lower().replace(" ", "")
    for store in SEARCHED:
        if store.name.lower().replace(" ", "") in s:
            return store.key
    return None


def used_today(db: DB) -> int:
    return db.get(f"serper-count:{date.today()}", 0)


def search(db: DB, query: str, post=requests.post) -> dict:
    """Men's results from the blocked shops for one query."""
    query = " ".join(query.split())[:120]
    key = "serper:" + hashlib.sha256(query.lower().encode()).hexdigest()[:24]
    if (hit := db.get(key, max_age=CACHE_FOR)) is not None:
        return {"items": hit, "cached": True, "used_today": used_today(db), "cap": DAILY_CAP}
    if not available():
        raise RuntimeError("Add SERPER_API_KEY to your .env file to search these shops.")
    if used_today(db) >= DAILY_CAP:
        raise RuntimeError(f"Reached today's limit of {DAILY_CAP} paid searches.")

    resp = post(
        SERPER_URL,
        headers={"X-API-KEY": os.environ["SERPER_API_KEY"], "Content-Type": "application/json"},
        json={"q": f"mens {query}", "gl": "au", "num": 40},
        timeout=20,
    )
    resp.raise_for_status()
    db.put(f"serper-count:{date.today()}", used_today(db) + 1)

    items = []
    for r in resp.json().get("shopping", []):
        store = _store_for(r.get("source") or "")
        title = r.get("title") or ""
        price = _price(r.get("price"))
        link = r.get("link") or ""
        if not store or not price or not link.startswith("http"):
            continue
        if _SKIP.search(title) or (_WOMENS.search(title) and not _MENS.search(title)):
            continue
        items.append({
            "store": store,
            "store_name": next(s.name for s in SEARCHED if s.key == store),
            "title": title,
            "price": price,
            "url": link,
            "image": r.get("imageUrl"),
            "category": category_of(title),
        })
    db.put(key, items)
    return {"items": items, "cached": False, "used_today": used_today(db), "cap": DAILY_CAP}
