import pytest
from fastapi.testclient import TestClient

from deal_finder import extra
from deal_finder.normalize import from_shopify
from deal_finder.stores import BY_KEY
from deal_finder.sync import Syncer
from deal_finder.web.app import create_app

from .conftest import raw


@pytest.fixture
def client(db, monkeypatch):
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    s = BY_KEY["mjbale"]
    db.record("mjbale", [from_shopify(s, r) for r in [
        raw("coat", "Navy Wool Coat", 300, 700, ptype="Outerwear", tags=["comp:100% Wool"]),
        raw("shirt", "White Oxford Shirt", 60, 150, ptype="Shirts", colour="White"),
        raw("chino", "Stone Chino", 80, 160, ptype="Trousers", sizes=("30", "32")),
    ]])
    syncer = Syncer(db, fetch=lambda store: [])
    return TestClient(create_app(db, syncer, auto_sync=False))


def test_serves_the_app(client):
    assert "Deal Finder" in client.get("/").text
    assert client.get("/static/app.js").status_code == 200
    st = client.get("/api/status").json()
    mj = next(s for s in st["stores"] if s["key"] == "mjbale")
    assert mj["products"] == 3 and mj["on_sale"] == 3
    assert not st["empty"] and "Knitwear" in st["categories"]


def test_deals_and_filters(client):
    r = client.get("/api/deals").json()
    assert r["total"] == 3 and r["items"][0]["title"] == "Navy Wool Coat"
    r = client.get("/api/deals", params={"category": "Shirts"}).json()
    assert [d["title"] for d in r["items"]] == ["White Oxford Shirt"]
    assert client.get("/api/deals", params={"sort": "nonsense"}).status_code == 422


def test_product_save_and_watchlist(client):
    pid = "mjbale:coat"
    d = client.get(f"/api/products/{pid}").json()
    assert d["price"] == 300 and len(d["history"]) == 1 and d["saved"] is False
    assert client.put(f"/api/saved/{pid}").status_code == 204
    saved = client.get("/api/saved").json()
    assert saved[0]["id"] == pid and saved[0]["change"] == 0
    assert client.get("/api/deals").json()["items"][0]["saved"] is True
    assert client.delete(f"/api/saved/{pid}").status_code == 204
    assert client.get("/api/saved").json() == []
    assert client.put("/api/saved/mjbale:nope").status_code == 404


def test_sizes_setting_filters_deals(client):
    client.put("/api/settings", json={"sizes": {"bottoms": "34"}})
    r = client.get("/api/deals", params={"my_sizes": True, "category": "Trousers"}).json()
    assert r["total"] == 0
    client.put("/api/settings", json={"sizes": {"bottoms": "32"}})
    assert client.get("/api/deals", params={"my_sizes": True, "category": "Trousers"}).json()["total"] == 1


def test_outfit(client):
    r = client.post("/api/outfit", json={"brief": "navy coat and white shirt", "budget": 400}).json()
    assert [s["pick"]["title"] for s in r["slots"]] == ["Navy Wool Coat", "White Oxford Shirt"]
    assert r["total"] == 360 and r["saved"] == 490


def test_paid_search_needs_a_key(client):
    r = client.get("/api/extra", params={"q": "blazer"})
    assert r.status_code == 400 and "SERPER_API_KEY" in r.json()["detail"]


def test_sync_one_shop(client):
    assert client.post("/api/sync/mjbale").json() == {"error": "no menswear found in the catalogue"}
    assert client.post("/api/sync/countryroad").status_code == 404
    assert client.post("/api/sync/nope").status_code == 404


def test_paid_search_filters_caches_and_caps(db, monkeypatch):
    monkeypatch.setenv("SERPER_API_KEY", "k")
    monkeypatch.setattr(extra, "DAILY_CAP", 1)
    calls = []

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"shopping": [
                {"title": "Men's Navy Blazer", "source": "Country Road", "price": "A$199.00", "link": "https://www.countryroad.com.au/x"},
                {"title": "Navy Blazer", "source": "Some Other Shop", "price": "$99", "link": "https://other/x"},
                {"title": "Women's Blazer", "source": "Country Road", "price": "$250", "link": "https://www.countryroad.com.au/y"},
            ]}

    def post(*args, **kwargs):
        calls.append(kwargs["json"]["q"])
        return Resp()

    first = extra.search(db, "navy blazer", post=post)
    assert [(i["store"], i["price"]) for i in first["items"]] == [("countryroad", 199.0)]
    assert extra.search(db, "navy blazer", post=post)["cached"] is True
    with pytest.raises(RuntimeError, match="limit"):
        extra.search(db, "brown loafers", post=post)
    assert calls == ["mens navy blazer"]
