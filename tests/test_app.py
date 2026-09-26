import time

import pytest
from fastapi.testclient import TestClient

from deal_finder.models import Assessment, Lead
from deal_finder.scoring import finalize
from deal_finder.service import HuntStore, load_sample
from deal_finder.web.app import create_app


def fake_runner(request, tracker):
    tracker.search_done("mens shirt sale", 3)
    tracker.next_stage()
    url = "https://shop.example/shirt"
    tracker.saw(url)
    lead = Lead(category="Shirts", garment="shirt", name="Shirt", retailer="Shop",
                url=url, price=50, original_price=100, currency="AUD",
                composition="100% cotton", verified=True)
    return finalize(request, [lead], [Assessment(url=url, style_fit=8, justification="Good.")],
                    seen_urls=tracker.seen_urls)


def broken_runner(request, tracker):
    raise RuntimeError("search quota used up")


@pytest.fixture
def make_client(tmp_path, monkeypatch):
    monkeypatch.setattr("deal_finder.service.OUTPUT_DIR", tmp_path)

    def make(runner=fake_runner, keys=()):
        app = create_app(HuntStore(tmp_path), runner=runner, check_keys=lambda: list(keys))
        return TestClient(app)

    return make


def wait_for(client, hunt_id, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        hunt = client.get(f"/api/hunts/{hunt_id}").json()
        if hunt["status"] in ("done", "failed"):
            return hunt
        time.sleep(0.05)
    raise AssertionError("hunt did not finish")


BODY = {"brief": "smart casual", "budget": 300, "region": "au"}


def test_serves_the_app(make_client):
    c = make_client()
    assert "Deal Finder" in c.get("/").text
    assert c.get("/static/app.js").status_code == 200
    cfg = c.get("/api/config").json()
    assert {r["code"] for r in cfg["regions"]} >= {"au", "us", "gb"}
    assert "Knitwear" in cfg["categories"]


def test_hunt_runs_and_reports(make_client):
    c = make_client()
    started = c.post("/api/hunts", json=BODY)
    assert started.status_code == 202
    hunt = wait_for(c, started.json()["id"])
    assert hunt["status"] == "done"
    deal = hunt["report"]["deals"][0]
    assert (deal["discount_pct"], deal["picked"], deal["label"]) == (50, True, "Great deal")
    assert hunt["progress"]["searches"] == 1
    listed = c.get("/api/hunts").json()
    assert listed[0]["picks"] == 1 and listed[0]["savings"] == 50
    md = c.get(f"/api/hunts/{hunt['id']}/markdown").text
    assert "https://shop.example/shirt" in md


def test_failed_hunt_keeps_the_error(make_client):
    c = make_client(runner=broken_runner)
    hunt = wait_for(c, c.post("/api/hunts", json=BODY).json()["id"])
    assert hunt["status"] == "failed"
    assert "quota" in hunt["error"]


def test_refuses_to_start_without_keys(make_client):
    c = make_client(keys=["SERPER_API_KEY"])
    r = c.post("/api/hunts", json=BODY)
    assert r.status_code == 400
    assert "SERPER_API_KEY" in r.json()["detail"]


def test_validates_requests(make_client):
    c = make_client()
    assert c.post("/api/hunts", json={**BODY, "budget": -5}).status_code == 422
    assert c.post("/api/hunts", json={**BODY, "region": "mars"}).status_code == 422
    assert c.get("/api/hunts/..%2Fsecret").status_code == 404


def test_sample_and_delete(make_client):
    c = make_client()
    sample = c.post("/api/hunts/sample").json()
    assert sample["sample"] is True and sample["picks"] >= 1
    assert c.delete(f"/api/hunts/{sample['id']}").status_code == 204
    assert c.get(f"/api/hunts/{sample['id']}").status_code == 404


def test_unfinished_hunts_fail_on_restart(tmp_path, make_client):
    store = HuntStore(tmp_path)
    stuck = load_sample()
    stuck.id, stuck.status, stuck.report = "stuck1", "running", None
    store.save(stuck)
    c = make_client()
    assert c.get("/api/hunts/stuck1").json()["status"] == "failed"


def test_sample_is_consistent():
    hunt = load_sample()
    r = hunt.report
    assert r.total == pytest.approx(sum(d.price for d in r.picks))
    assert r.total <= r.budget
    assert all(d.url.startswith("https://") for d in r.deals)
