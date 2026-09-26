"""Local SQLite store: products, their price history, saved items and settings."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from deal_finder.normalize import Product

DEFAULT_PATH = Path(os.environ.get("DEAL_FINDER_DB", "data/deals.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id TEXT PRIMARY KEY,
    store TEXT NOT NULL,
    brand TEXT, title TEXT, url TEXT, image TEXT,
    category TEXT, colour TEXT, composition TEXT, fabric TEXT,
    sizes TEXT, tags TEXT,
    price REAL NOT NULL, was_price REAL, in_stock INTEGER,
    low_price REAL, high_price REAL, prev_price REAL,
    price_changed_at TEXT, first_seen TEXT, last_seen TEXT,
    active INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS products_store ON products(store);
CREATE INDEX IF NOT EXISTS products_category ON products(category);
CREATE TABLE IF NOT EXISTS prices (
    product_id TEXT NOT NULL, at TEXT NOT NULL, price REAL NOT NULL, was_price REAL
);
CREATE INDEX IF NOT EXISTS prices_product ON prices(product_id);
CREATE TABLE IF NOT EXISTS saved (
    product_id TEXT PRIMARY KEY, saved_price REAL, saved_at TEXT
);
CREATE TABLE IF NOT EXISTS syncs (
    store TEXT PRIMARY KEY, at TEXT, ok INTEGER, count INTEGER,
    on_sale INTEGER, error TEXT
);
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY, value TEXT, at TEXT
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_time(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class DB:
    def __init__(self, path: Path | str = DEFAULT_PATH) -> None:
        path = Path(path)
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.executescript(SCHEMA)

    def query(self, sql: str, args: tuple | list = ()) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, args).fetchall()

    # ── Catalogue ─────────────────────────────────────────────

    def record(self, store: str, products: list[Product], at: str | None = None) -> dict:
        """Upsert one store's catalogue; log every price change."""
        at = at or now()
        changed = added = 0
        with self.lock, self.conn:
            old = {
                r["id"]: r
                for r in self.conn.execute(
                    "SELECT id, price, was_price, low_price, high_price, prev_price, "
                    "price_changed_at, first_seen FROM products WHERE store = ?", (store,)
                )
            }
            seen = set()
            for p in products:
                if p.id in seen:
                    continue
                seen.add(p.id)
                prev = old.get(p.id)
                fields = dict(
                    id=p.id, store=p.store, brand=p.brand, title=p.title, url=p.url,
                    image=p.image, category=p.category, colour=p.colour,
                    composition=p.composition, fabric=p.fabric,
                    sizes=json.dumps(p.sizes), tags=json.dumps(p.tags),
                    price=p.price, was_price=p.was_price, in_stock=int(p.in_stock),
                    last_seen=at, active=1,
                )
                if prev is None:
                    added += 1
                    fields.update(low_price=p.price, high_price=p.price, prev_price=None,
                                  price_changed_at=None, first_seen=at)
                    self._log_price(p, at)
                else:
                    moved = abs(prev["price"] - p.price) > 0.005
                    was_moved = (prev["was_price"] or 0) != (p.was_price or 0)
                    fields.update(
                        low_price=min(prev["low_price"] or p.price, p.price),
                        high_price=max(prev["high_price"] or p.price, p.price),
                        prev_price=prev["price"] if moved else prev["prev_price"],
                        price_changed_at=at if moved else prev["price_changed_at"],
                        first_seen=prev["first_seen"],
                    )
                    if moved or was_moved:
                        changed += 1
                        self._log_price(p, at)
                cols = ", ".join(fields)
                marks = ", ".join("?" for _ in fields)
                self.conn.execute(
                    f"INSERT OR REPLACE INTO products ({cols}) VALUES ({marks})",
                    list(fields.values()),
                )
            gone = [pid for pid in old if pid not in seen]
            self.conn.executemany("UPDATE products SET active = 0 WHERE id = ?", [(g,) for g in gone])
            on_sale = sum(1 for p in products if p.was_price)
            self.conn.execute(
                "INSERT OR REPLACE INTO syncs (store, at, ok, count, on_sale, error) VALUES (?, ?, 1, ?, ?, NULL)",
                (store, at, len(seen), on_sale),
            )
        return {"added": added, "changed": changed, "removed": len(gone), "count": len(seen)}

    def _log_price(self, p: Product, at: str) -> None:
        self.conn.execute(
            "INSERT INTO prices (product_id, at, price, was_price) VALUES (?, ?, ?, ?)",
            (p.id, at, p.price, p.was_price),
        )

    def sync_failed(self, store: str, error: str) -> None:
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT INTO syncs (store, at, ok, count, on_sale, error) VALUES (?, ?, 0, 0, 0, ?) "
                "ON CONFLICT(store) DO UPDATE SET ok = 0, error = excluded.error, at = excluded.at",
                (store, now(), error[:300]),
            )

    def syncs(self) -> dict[str, sqlite3.Row]:
        return {r["store"]: r for r in self.query("SELECT * FROM syncs")}

    def product(self, pid: str) -> sqlite3.Row | None:
        rows = self.query("SELECT * FROM products WHERE id = ?", (pid,))
        return rows[0] if rows else None

    def history(self, pid: str) -> list[dict]:
        return [dict(r) for r in self.query(
            "SELECT at, price, was_price FROM prices WHERE product_id = ? ORDER BY at", (pid,)
        )]

    # ── Saved items ───────────────────────────────────────────

    def save(self, pid: str) -> None:
        row = self.product(pid)
        if row is None:
            raise KeyError(pid)
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO saved (product_id, saved_price, saved_at) VALUES (?, ?, ?)",
                (pid, row["price"], now()),
            )

    def unsave(self, pid: str) -> None:
        with self.lock, self.conn:
            self.conn.execute("DELETE FROM saved WHERE product_id = ?", (pid,))

    def saved(self) -> dict[str, sqlite3.Row]:
        return {r["product_id"]: r for r in self.query("SELECT * FROM saved ORDER BY saved_at DESC")}

    # ── Settings and cache ────────────────────────────────────

    def get(self, key: str, default=None, max_age: timedelta | None = None):
        rows = self.query("SELECT value, at FROM kv WHERE key = ?", (key,))
        if not rows:
            return default
        if max_age and datetime.now(timezone.utc) - parse_time(rows[0]["at"]) > max_age:
            return default
        return json.loads(rows[0]["value"])

    def put(self, key: str, value) -> None:
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO kv (key, value, at) VALUES (?, ?, ?)",
                (key, json.dumps(value), now()),
            )
