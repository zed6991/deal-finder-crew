"""Download shop catalogues into the database. Free: no API keys involved."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import requests

from deal_finder.db import DB, parse_time
from deal_finder.normalize import Product, from_shopify
from deal_finder.stores import FEEDS, Store

log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "DealFinder/1.0 (personal price tracker; +https://github.com/zed6991/deal-finder-crew)",
    "Accept": "application/json",
}
PAGE_SIZE = 250
MAX_PAGES = 40
STALE_AFTER = timedelta(hours=12)


def fetch_shopify(store: Store, session: requests.Session | None = None, pause: float = 1.0) -> list[dict]:
    """Every product in a Shopify shop, one page of 250 at a time."""
    http = session or requests.Session()
    products: list[dict] = []
    for page in range(1, MAX_PAGES + 1):
        for attempt in range(4):
            resp = http.get(
                f"{store.base_url}/products.json",
                params={"limit": PAGE_SIZE, "page": page},
                headers=HEADERS,
                timeout=30,
            )
            if resp.status_code not in (429, 500, 502, 503, 504) or attempt == 3:
                break
            # Shops throttle quick page runs; wait as asked, or back off.
            wait = resp.headers.get("Retry-After", "")
            time.sleep(float(wait) if wait.replace(".", "").isdigit() else 2 ** (attempt + 1))
        resp.raise_for_status()
        batch = resp.json().get("products", [])
        products.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        time.sleep(pause)  # be polite
    return products


def normalise(store: Store, raw: Iterable[dict]) -> list[Product]:
    return [p for p in (from_shopify(store, r) for r in raw) if p]


class Syncer:
    """Keeps the catalogue fresh. One sync runs at a time."""

    def __init__(self, db: DB, fetch: Callable[[Store], list[dict]] = fetch_shopify) -> None:
        self.db = db
        self.fetch = fetch
        self._lock = threading.Lock()
        self.running: set[str] = set()

    def stale(self, stores: list[Store] = FEEDS) -> list[Store]:
        done = self.db.syncs()
        cutoff = datetime.now(timezone.utc) - STALE_AFTER
        return [
            s for s in stores
            if s.key not in done or not done[s.key]["ok"] or parse_time(done[s.key]["at"]) < cutoff
        ]

    def sync_store(self, store: Store) -> dict:
        self.running.add(store.key)
        try:
            products = normalise(store, self.fetch(store))
            if not products:
                raise RuntimeError("no menswear found in the catalogue")
            result = self.db.record(store.key, products)
            log.info("%s: %s", store.name, result)
            return result
        except Exception as exc:  # one broken shop must not stop the rest
            log.warning("%s failed: %s", store.name, exc)
            self.db.sync_failed(store.key, f"{type(exc).__name__}: {exc}")
            return {"error": str(exc)}
        finally:
            self.running.discard(store.key)

    def sync(self, stores: list[Store] | None = None, workers: int = 4) -> dict[str, dict]:
        stores = FEEDS if stores is None else stores
        if not self._lock.acquire(blocking=False):
            return {}
        try:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                return dict(zip((s.key for s in stores), pool.map(self.sync_store, stores)))
        finally:
            self._lock.release()

    def sync_in_background(self, stores: list[Store] | None = None) -> bool:
        if self._lock.locked():
            return False
        threading.Thread(target=self.sync, args=(stores,), daemon=True, name="sync").start()
        return True

    def keep_fresh(self, every: timedelta = timedelta(hours=1)) -> None:
        """Background loop: sync whatever has gone stale."""

        def loop() -> None:
            while True:
                stale = self.stale()
                if stale:
                    self.sync(stale)
                time.sleep(every.total_seconds())

        threading.Thread(target=loop, daemon=True, name="keep-fresh").start()
