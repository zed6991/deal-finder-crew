"""Sign-in, cron and the serverless-friendly sync path."""

import time

import pytest
from fastapi.testclient import TestClient

from deal_finder import auth
from deal_finder.normalize import from_shopify
from deal_finder.stores import BY_KEY
from deal_finder.sync import Syncer
from deal_finder.web.app import create_app

from .conftest import raw


def fetch(store):
    return [raw("shirt", "Oxford Shirt", 99, 150, ptype="Shirts", tags=["mens"])]


@pytest.fixture
def make(db, monkeypatch):
    for var in ("APP_PASSWORD", "CRON_SECRET", "SERPER_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    db.record("mjbale", [from_shopify(BY_KEY["mjbale"], raw("coat", "Wool Coat", 300, 700, ptype="Outerwear"))])

    def build(**env):
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        return TestClient(create_app(db, Syncer(db, fetch), auto_sync=False))

    return build


def test_open_when_no_password_locally(make):
    c = make()
    assert c.get("/").status_code == 200
    assert c.get("/api/deals").json()["total"] == 1
    assert c.get("/api/status").json()["signed_in"] is False


def test_hosted_without_password_shows_setup(make, monkeypatch):
    monkeypatch.setattr(auth, "required", lambda: True)
    c = make()
    r = c.get("/")
    assert r.status_code == 503 and "APP_PASSWORD" in r.text
    assert c.get("/api/deals").status_code == 503
    assert c.get("/login").status_code == 200  # still reachable


def test_password_protects_pages_and_api(make):
    c = make(APP_PASSWORD="hunter2-long-enough")
    r = c.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert c.get("/api/deals").status_code == 401
    assert c.get("/static/app.css").status_code == 200  # assets stay public
    assert c.get("/login").status_code == 200

    assert c.post("/api/login", json={"password": "wrong"}).status_code == 401
    assert c.post("/api/login", json={"password": "hunter2-long-enough"}).status_code == 204
    assert c.get("/api/deals").json()["total"] == 1
    assert c.get("/").status_code == 200

    c.post("/api/logout")
    assert c.get("/api/deals").status_code == 401


def test_changing_password_signs_everyone_out(make, monkeypatch):
    c = make(APP_PASSWORD="first-password")
    c.post("/api/login", json={"password": "first-password"})
    monkeypatch.setenv("APP_PASSWORD", "second-password")
    assert c.get("/api/deals").status_code == 401


def test_tokens_expire_and_resist_tampering(monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "pw-for-tokens")
    t = auth.token(now=1000)
    assert auth.valid(t, now=1001)
    assert not auth.valid(t, now=1000 + auth.LIFETIME + 1)
    expires, sig = t.split(".")
    assert not auth.valid(f"{int(expires) + 999}.{sig}", now=1001)
    assert not auth.valid("garbage", now=1001) and not auth.valid(None)


def test_cron_needs_the_secret(make):
    c = make(APP_PASSWORD="pw", CRON_SECRET="s3cret-cron-value")
    assert c.get("/api/cron/sync").status_code == 401
    assert c.get("/api/cron/sync", headers={"Authorization": "Bearer wrong"}).status_code == 401
    r = c.get("/api/cron/sync", headers={"Authorization": "Bearer s3cret-cron-value"})
    assert r.status_code == 200
    body = r.json()
    assert "mjbale" not in body["synced"]  # synced moments ago, so not stale
    assert "gazman" in body["synced"] and "error" not in body["synced"]["gazman"]
    assert body["skipped"] == []


def test_cron_refuses_when_no_secret_set(make):
    c = make()
    assert c.get("/api/cron/sync", headers={"Authorization": "Bearer "}).status_code == 401


def test_sync_until_stops_at_the_deadline(db):
    stores = [BY_KEY["mjbale"], BY_KEY["gazman"], BY_KEY["oxford"]]
    calls = []

    def slow(store):
        calls.append(store.key)
        return fetch(store)

    s = Syncer(db, slow)
    out = s.sync_until(time.monotonic() - 1, stores)
    assert out["synced"] == {} and len(out["skipped"]) == 3
    out = s.sync_until(time.monotonic() + 60, stores)
    assert set(out["synced"]) == {"mjbale", "gazman", "oxford"}
    assert s.sync_until(time.monotonic() + 60, stores) == {"synced": {}, "skipped": []}  # all fresh now


def test_catalogue_cache_refreshes_after_a_sync(db):
    first = db.catalogue()
    assert db.catalogue() is first  # served from memory
    db.record("gazman", [from_shopify(BY_KEY["gazman"], raw("tee", "Linen Tee", 40, 80, ptype="T-Shirt", vendor="GAZMAN"))])
    assert len(db.catalogue()) == len(first) + 1


def test_unchanged_products_are_not_rewritten(db):
    p = from_shopify(BY_KEY["mjbale"], raw("knit", "Merino Knit", 99, 199, ptype="Knitwear"))
    db.record("mjbale", [p], at="2026-01-01T00:00:00+00:00")
    db.record("mjbale", [p], at="2026-01-02T00:00:00+00:00")
    row = db.product("mjbale:knit")
    assert row["last_seen"] == "2026-01-02T00:00:00+00:00" and row["first_seen"] == "2026-01-01T00:00:00+00:00"
    assert len(db.history("mjbale:knit")) == 1
