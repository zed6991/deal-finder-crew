"""Storage: products, their price history, saved items and settings.

SQLite on your own machine; Postgres (e.g. Neon) when DATABASE_URL is set,
which is how the hosted version keeps its data between requests.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from deal_finder.normalize import Product

ON_VERCEL = bool(os.environ.get("VERCEL"))
DEFAULT_PATH = Path(os.environ.get("DEAL_FINDER_DB", "/tmp/deals.db" if ON_VERCEL else "data/deals.db"))


def database_url() -> str | None:
    return os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL")


SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id TEXT PRIMARY KEY,
    store TEXT NOT NULL,
    brand TEXT, title TEXT, url TEXT, image TEXT,
    category TEXT, colour TEXT, composition TEXT, fabric TEXT,
    sizes TEXT, tags TEXT,
    price DOUBLE PRECISION NOT NULL, was_price DOUBLE PRECISION, in_stock INTEGER,
    low_price DOUBLE PRECISION, high_price DOUBLE PRECISION, prev_price DOUBLE PRECISION,
    price_changed_at TEXT, first_seen TEXT, last_seen TEXT,
    active INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS products_store ON products(store);
CREATE TABLE IF NOT EXISTS prices (
    product_id TEXT NOT NULL, at TEXT NOT NULL, price DOUBLE PRECISION NOT NULL, was_price DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS prices_product ON prices(product_id);
CREATE TABLE IF NOT EXISTS saved (
    product_id TEXT PRIMARY KEY, saved_price DOUBLE PRECISION, saved_at TEXT
);
CREATE TABLE IF NOT EXISTS syncs (
    store TEXT PRIMARY KEY, at TEXT, ok INTEGER, count INTEGER,
    on_sale INTEGER, error TEXT
);
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY, value TEXT, at TEXT
);
"""

PRODUCT_COLS = [
    "id", "store", "brand", "title", "url", "image", "category", "colour", "composition",
    "fabric", "sizes", "tags", "price", "was_price", "in_stock", "low_price", "high_price",
    "prev_price", "price_changed_at", "first_seen", "last_seen", "active",
]
# Fields whose change means the stored row must be rewritten.
WATCHED = [c for c in PRODUCT_COLS if c not in ("last_seen", "active")]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_time(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _upsert(table: str, cols: list[str], key: str) -> str:
    """INSERT ... ON CONFLICT DO UPDATE: the same SQL works in SQLite and Postgres."""
    sets = ", ".join(f"{c} = excluded.{c}" for c in cols if c != key)
    return (
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))}) "
        f"ON CONFLICT ({key}) DO UPDATE SET {sets}"
    )


class DB:
    def __init__(self, path: Path | str | None = None, url: str | None = None) -> None:
        self.url = url if url is not None else (None if path else database_url())
        self.lock = threading.RLock()
        self._catalogue: tuple[str, list[dict]] | None = None
        if self.url:
            self.kind = "postgres"
            self._connect_pg()
        else:
            self.kind = "sqlite"
            path = Path(path or DEFAULT_PATH)
            if str(path) != ":memory:":
                path.parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(str(path), check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.executescript(SCHEMA)

    @property
    def persistent(self) -> bool:
        """False when data would vanish between requests (SQLite in /tmp on Vercel)."""
        return self.kind == "postgres" or not ON_VERCEL

    # ── Connection plumbing ───────────────────────────────────

    def _connect_pg(self) -> None:
        import psycopg
        from psycopg.rows import dict_row

        # prepare_threshold=None keeps it working through poolers such as PgBouncer.
        self.conn = psycopg.connect(self.url, row_factory=dict_row, prepare_threshold=None, connect_timeout=10)
        with self.conn.transaction():
            for stmt in filter(str.strip, SCHEMA.split(";")):
                self.conn.execute(stmt)

    def _sql(self, sql: str) -> str:
        return sql.replace("?", "%s") if self.kind == "postgres" else sql

    def _ensure(self) -> None:
        if self.kind == "postgres" and (self.conn.closed or self.conn.broken):
            self._connect_pg()

    def query(self, sql: str, args: tuple | list = ()) -> list[dict]:
        with self.lock:
            self._ensure()
            rows = self.conn.execute(self._sql(sql), args).fetchall()
            if self.kind == "postgres":
                self.conn.commit()
                return rows
            return [dict(r) for r in rows]

    def _write(self, steps: list[tuple[str, list | tuple]]) -> None:
        """Run statements in one transaction. A list of tuples means executemany."""
        with self.lock:
            self._ensure()
            if self.kind == "postgres":
                with self.conn.transaction(), self.conn.cursor() as cur:
                    for sql, args in steps:
                        if isinstance(args, list):
                            if args:
                                cur.executemany(self._sql(sql), args)
                        else:
                            cur.execute(self._sql(sql), args)
            else:
                with self.conn:
                    for sql, args in steps:
                        if isinstance(args, list):
                            self.conn.executemany(sql, args)
                        else:
                            self.conn.execute(sql, args)

    # ── Catalogue ─────────────────────────────────────────────

    def record(self, store: str, products: list[Product], at: str | None = None) -> dict:
        """Upsert one store's catalogue, writing only what changed."""
        at = at or now()
        old = {r["id"]: r for r in self.query(
            f"SELECT {', '.join(PRODUCT_COLS)} FROM products WHERE store = ?", (store,))}
        rows, prices, seen = [], [], set()
        added = changed = 0
        for p in products:
            if p.id in seen:
                continue
            seen.add(p.id)
            prev = old.get(p.id)
            new = dict(
                id=p.id, store=p.store, brand=p.brand, title=p.title, url=p.url, image=p.image,
                category=p.category, colour=p.colour, composition=p.composition, fabric=p.fabric,
                sizes=json.dumps(p.sizes), tags=json.dumps(p.tags), price=p.price,
                was_price=p.was_price, in_stock=int(p.in_stock), last_seen=at, active=1,
            )
            if prev is None:
                added += 1
                new.update(low_price=p.price, high_price=p.price, prev_price=None,
                           price_changed_at=None, first_seen=at)
                prices.append((p.id, at, p.price, p.was_price))
            else:
                moved = abs(prev["price"] - p.price) > 0.005
                was_moved = (prev["was_price"] or 0) != (p.was_price or 0)
                new.update(
                    low_price=min(prev["low_price"] or p.price, p.price),
                    high_price=max(prev["high_price"] or p.price, p.price),
                    prev_price=prev["price"] if moved else prev["prev_price"],
                    price_changed_at=at if moved else prev["price_changed_at"],
                    first_seen=prev["first_seen"],
                )
                if moved or was_moved:
                    changed += 1
                    prices.append((p.id, at, p.price, p.was_price))
                if all(prev[c] == new[c] for c in WATCHED) and prev["active"]:
                    continue  # unchanged: only last_seen moves, done in bulk below
            rows.append(tuple(new[c] for c in PRODUCT_COLS))

        gone = [(pid,) for pid in old if pid not in seen]
        on_sale = sum(1 for p in products if p.was_price)
        self._write([
            (_upsert("products", PRODUCT_COLS, "id"), rows),
            ("INSERT INTO prices (product_id, at, price, was_price) VALUES (?, ?, ?, ?)", prices),
            ("UPDATE products SET last_seen = ? WHERE store = ? AND active = 1", (at, store)),
            ("UPDATE products SET active = 0 WHERE id = ?", gone),
            (_upsert("syncs", ["store", "at", "ok", "count", "on_sale", "error"], "store"),
             (store, at, 1, len(seen), on_sale, None)),
            (_upsert("kv", ["key", "value", "at"], "key"), ("catalogue_version", json.dumps(at + store), at)),
        ])
        return {"added": added, "changed": changed, "removed": len(gone), "count": len(seen)}

    def sync_failed(self, store: str, error: str) -> None:
        self._write([(
            "INSERT INTO syncs (store, at, ok, count, on_sale, error) VALUES (?, ?, 0, 0, 0, ?) "
            "ON CONFLICT (store) DO UPDATE SET ok = 0, error = excluded.error, at = excluded.at",
            (store, now(), error[:300]),
        )])

    def syncs(self) -> dict[str, dict]:
        return {r["store"]: r for r in self.query("SELECT * FROM syncs")}

    def catalogue(self) -> list[dict]:
        """Every active product, cached in memory until the next sync writes."""
        version = self.get("catalogue_version", "")
        if self._catalogue and self._catalogue[0] == version:
            return self._catalogue[1]
        rows = self.query(
            "SELECT id, store, brand, title, url, image, category, colour, composition, fabric, "
            "sizes, price, was_price, in_stock, low_price, prev_price, price_changed_at, first_seen "
            "FROM products WHERE active = 1"
        )
        for r in rows:
            r["sizes"] = json.loads(r["sizes"] or "[]")
        self._catalogue = (version, rows)
        return rows

    def product(self, pid: str) -> dict | None:
        rows = self.query("SELECT * FROM products WHERE id = ?", (pid,))
        return rows[0] if rows else None

    def history(self, pid: str) -> list[dict]:
        return self.query("SELECT at, price, was_price FROM prices WHERE product_id = ? ORDER BY at", (pid,))

    # ── Saved items ───────────────────────────────────────────

    def save(self, pid: str) -> None:
        row = self.product(pid)
        if row is None:
            raise KeyError(pid)
        self._write([(
            "INSERT INTO saved (product_id, saved_price, saved_at) VALUES (?, ?, ?) "
            "ON CONFLICT (product_id) DO NOTHING",
            (pid, row["price"], now()),
        )])

    def unsave(self, pid: str) -> None:
        self._write([("DELETE FROM saved WHERE product_id = ?", (pid,))])

    def saved(self) -> dict[str, dict]:
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
        self._write([(_upsert("kv", ["key", "value", "at"], "key"), (key, json.dumps(value), now()))])
