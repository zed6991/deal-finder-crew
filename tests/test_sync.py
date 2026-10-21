from deal_finder.stores import BY_KEY
from deal_finder.sync import Syncer, fetch_shopify

from .conftest import raw


class FakeResponse:
    def __init__(self, status, products=None):
        self.status_code = status
        self._products = products or []
        self.headers = {"Retry-After": "0"}

    def json(self):
        return {"products": self._products}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def get(self, url, params, headers, timeout):
        self.calls.append(params["page"])
        page = self.pages.pop(0)
        if isinstance(page, Exception):
            raise page
        return page


def test_fetch_pages_until_short_page_and_retries_throttling(monkeypatch):
    monkeypatch.setattr("deal_finder.sync.time.sleep", lambda s: None)
    full = [raw(f"p{i}", "Shirt", 10) for i in range(250)]
    import requests

    session = FakeSession([
        FakeResponse(200, full), FakeResponse(503),
        requests.ConnectionError("Connection reset by peer"), FakeResponse(200, full[:3]),
    ])
    got = fetch_shopify(BY_KEY["mjbale"], session)
    assert len(got) == 253
    assert session.calls == [1, 2, 2, 2]


def test_one_broken_shop_does_not_stop_the_rest(db):
    def fetch(store):
        if store.key == "gazman":
            raise RuntimeError("503")
        return [raw("shirt", "Oxford Shirt", 99, 150, ptype="Shirts")]

    results = Syncer(db, fetch).sync([BY_KEY["mjbale"], BY_KEY["gazman"]])
    assert results["mjbale"]["count"] == 1
    assert "error" in results["gazman"]
    syncs = db.syncs()
    assert syncs["mjbale"]["ok"] == 1 and syncs["gazman"]["ok"] == 0
    assert [s.key for s in Syncer(db, fetch).stale([BY_KEY["mjbale"], BY_KEY["gazman"]])] == ["gazman"]
