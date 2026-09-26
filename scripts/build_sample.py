"""Rebuild src/deal_finder/sample_hunt.json from an earlier real run.

The products, prices and links come from the first version of output/deals.md.
They are a snapshot: prices may have changed since.
"""

from datetime import datetime, timezone
from pathlib import Path

from deal_finder.models import Assessment, HuntRequest, Lead
from deal_finder.scoring import finalize
from deal_finder.service import Hunt

request = HuntRequest(brief="Smart casual, 30 year old male", budget=400, region="au")
L = lambda **kw: Lead(currency="AUD", verified=False, **kw)  # noqa: E731
leads = [
    L(category="Shirts", garment="White shirt", name="Favourite White Shirt",
      retailer="Van Heusen", price=79.95, composition="100% cotton",
      url="https://www.vanheusen.com.au/features/favourite-white-shirt"),
    L(category="Knitwear", garment="Crew neck jumper", name="Men's Merino Crew Neck Sweater (Grey)",
      retailer="UNIQLO", price=59.90, composition="100% merino wool",
      url="https://www.uniqlo.com/au/en/products/E484096-000/00"),
    L(category="Tailoring", garment="Blazer", name="Men's Cotton Stretch Blazer (Navy)",
      retailer="MYER", price=119.98, composition="Cotton with elastane",
      url="https://www.myer.com.au/c/men/mens-clothing/mens-coats-jackets/blazers"),
    L(category="Trousers", garment="Chinos", name="Men's Chino Pants (Stone)",
      retailer="Industrie", price=89.95, composition="100% cotton",
      url="https://www.industrie.com.au/collections/chino-pants"),
    L(category="Accessories", garment="Belt", name="Men's Full Grain Leather Belt",
      retailer="Buckle 1922", price=49.95, composition="Full grain leather",
      url="https://www.buckle.com.au/collections/mens-full-grain-leather-belts"),
]
fit = {
    "vanheusen": (9, "A crisp all-cotton white shirt is the base of any smart-casual kit."),
    "uniqlo": (8, "Fine merino at this price layers over the shirt and wears all year."),
    "myer": (7, "A navy blazer lifts the outfit, but the stretch blend breaks the natural-fibre rule."),
    "industrie": (8, "Stone chinos pair with everything else here."),
    "buckle": (7, "A full grain belt ties the look together and will outlast cheaper ones."),
}
assessments = [
    Assessment(url=l.url, style_fit=v[0], justification=v[1])
    for l in leads for k, v in fit.items() if k in l.url
]
report = finalize(request, leads, assessments)
hunt = Hunt(
    id="sample0000001", request=request, status="done", sample=True, report=report,
    created_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
    finished_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
)
out = Path(__file__).parents[1] / "src/deal_finder/sample_hunt.json"
out.write_text(hunt.model_dump_json(indent=2))
print(f"wrote {out}: {len(report.picks)} picks, total {report.total}")
