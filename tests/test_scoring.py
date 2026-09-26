import pytest

from deal_finder.models import Assessment, FabricPolicy, HuntRequest, Lead
from deal_finder.scoring import discount, fabric_verdict, finalize, parse_price, score, to_markdown


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("A$1,299.00", 1299.0),
        ("AU$59.90 now", 59.9),
        ("$80", 80.0),
        (49.5, 49.5),
        ("free", None),
        (None, None),
    ],
)
def test_parse_price(text, expected):
    assert parse_price(text) == expected


@pytest.mark.parametrize(
    ("composition", "verdict"),
    [
        ("100% cotton", "natural"),
        ("100% Merino Wool", "natural"),
        ("98% cotton, 2% elastane", "stretch"),
        ("90% cotton 10% elastane", "synthetic"),
        ("60% cotton, 40% polyester", "synthetic"),
        ("Linen and viscose blend", "synthetic"),
        ("Full grain leather", "natural"),
        ("Oxford button-down, 100% cotton", "natural"),
        ("Cotton with elastane", "stretch"),
        ("", "unknown"),
        (None, "unknown"),
        ("Machine wash cold", "unknown"),
    ],
)
def test_fabric_verdict(composition, verdict):
    assert fabric_verdict(composition) == verdict


def test_discount():
    assert discount(60, 100) == (40, 40.0)
    assert discount(100, 100) == (0, 0.0)
    assert discount(100, None) == (0, 0.0)
    assert discount(120, 100) == (0, 0.0)


def test_score_rewards_discount_fit_and_checks():
    best = score(60, 10, True, "natural")
    assert best == 100
    assert score(0, 5, False, "unknown") < score(30, 5, False, "unknown")
    assert score(30, 5, True, "natural") > score(30, 5, False, "natural")


def lead(url, garment, price, original=None, composition="100% cotton", **kw):
    return Lead(
        category=kw.pop("category", "Shirts"), garment=garment, name=f"{garment} {price}",
        retailer="Shop", url=url, price=price, original_price=original,
        currency=kw.pop("currency", "AUD"), composition=composition, **kw,
    )


REQ = HuntRequest(brief="smart casual", budget=200, region="au")


def test_finalize_picks_one_per_garment_within_budget():
    leads = [
        lead("https://a.com/1", "shirt", 50, 100),        # 50% off
        lead("https://a.com/2", "shirt", 40),             # full price
        lead("https://a.com/3", "chinos", 90, 120),
        lead("https://a.com/4", "blazer", 150, 300),      # too dear once others picked
    ]
    report = finalize(REQ, leads, [])
    picked = {d.url for d in report.picks}
    assert "https://a.com/1" in picked and "https://a.com/2" not in picked
    assert report.total <= REQ.budget
    assert report.remaining == pytest.approx(REQ.budget - report.total)
    assert report.total_savings == sum(d.savings for d in report.picks)
    assert [d.score for d in report.deals] == sorted((d.score for d in report.deals), reverse=True)


def test_finalize_applies_rules():
    req = HuntRequest(brief="basics", budget=500, region="au", min_discount=20, fabric=FabricPolicy.NATURAL)
    leads = [
        lead("https://a.com/poly", "shirt", 30, 60, composition="100% polyester"),
        lead("https://a.com/usd", "tee", 30, 60, currency="USD"),
        lead("https://a.com/small", "knit", 90, 100),
        lead("https://a.com/oos", "belt", 30, 60, in_stock=False),
        lead("https://a.com/dear", "coat", 900, 1200),
        lead("https://a.com/ok", "chinos", 50, 100),
    ]
    report = finalize(req, leads, [])
    reasons = {d.url: d.rejection for d in report.deals}
    assert reasons["https://a.com/poly"] == "Fabric does not meet your rules"
    assert "USD" in reasons["https://a.com/usd"]
    assert "20%" in reasons["https://a.com/small"]
    assert reasons["https://a.com/oos"] == "Out of stock"
    assert reasons["https://a.com/dear"] == "Over budget on its own"
    assert reasons["https://a.com/ok"] is None
    assert [d.url for d in report.picks] == ["https://a.com/ok"]


def test_stretch_policy_allows_small_elastane():
    blend = [lead("https://a.com/s", "chinos", 50, composition="98% cotton 2% elastane")]
    strict = finalize(HuntRequest(brief="basics", budget=100, fabric="natural"), blend, [])
    loose = finalize(HuntRequest(brief="basics", budget=100, fabric="stretch"), blend, [])
    assert not strict.deals[0].eligible
    assert loose.deals[0].eligible


def test_finalize_drops_links_no_tool_returned():
    leads = [lead("https://real.com/p", "shirt", 50), lead("https://made-up.com/p", "tee", 20)]
    report = finalize(REQ, leads, [], seen_urls={"https://REAL.com/p/"})
    assert [d.url for d in report.deals] == ["https://real.com/p"]


def test_finalize_merges_assessments_and_dedupes():
    leads = [lead("https://a.com/1", "shirt", 50), lead("https://a.com/1/", "shirt", 50, 80)]
    report = finalize(REQ, leads, [Assessment(url="https://a.com/1", style_fit=9, justification="Sharp.")])
    assert len(report.deals) == 1
    d = report.deals[0]
    assert (d.style_fit, d.justification, d.discount_pct) == (9, "Sharp.", 38)


def test_markdown_keeps_links_verbatim():
    url = "https://a.com/p?id=1&colour=navy"
    md = to_markdown(finalize(REQ, [lead(url, "shirt", 50, 100)], []), "A$")
    assert f"]({url})" in md
    assert "~~A$100.00~~" in md and "50% off" in md
