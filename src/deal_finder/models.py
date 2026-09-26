"""Typed data that flows between the tasks, the scorer and the web app."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field, field_validator


class FabricPolicy(str, Enum):
    NATURAL = "natural"  # natural fibres only, nothing synthetic
    STRETCH = "stretch"  # natural fibres, plus up to 5% elastane
    ANY = "any"


class Region(BaseModel):
    code: str
    name: str
    currency: str
    symbol: str


REGIONS: dict[str, Region] = {
    r.code: r
    for r in [
        Region(code="au", name="Australia", currency="AUD", symbol="A$"),
        Region(code="us", name="United States", currency="USD", symbol="$"),
        Region(code="gb", name="United Kingdom", currency="GBP", symbol="£"),
        Region(code="ca", name="Canada", currency="CAD", symbol="C$"),
        Region(code="nz", name="New Zealand", currency="NZD", symbol="NZ$"),
    ]
}

CATEGORIES = [
    "Shirts",
    "T-shirts & Polos",
    "Knitwear",
    "Trousers",
    "Jeans",
    "Shorts",
    "Outerwear",
    "Tailoring",
    "Shoes",
    "Accessories",
]


class HuntRequest(BaseModel):
    """What the user asks for."""

    brief: str = Field(min_length=3, max_length=500)
    budget: float = Field(gt=0, le=100_000)
    region: str = "au"
    sizes: str = Field(default="", max_length=200)
    categories: list[str] = Field(default_factory=list)
    fabric: FabricPolicy = FabricPolicy.NATURAL
    min_discount: int = Field(default=0, ge=0, le=90)
    notes: str = Field(default="", max_length=500)

    @field_validator("region")
    @classmethod
    def _known_region(cls, v: str) -> str:
        v = v.lower()
        if v not in REGIONS:
            raise ValueError(f"region must be one of {sorted(REGIONS)}")
        return v

    @property
    def region_info(self) -> Region:
        return REGIONS[self.region]

    def crew_inputs(self) -> dict[str, str]:
        """Flatten the request into the {placeholders} used in the YAML files."""
        r = self.region_info
        fabric_rules = {
            FabricPolicy.NATURAL: "Natural fibres only (cotton, linen, wool, "
            "merino, cashmere, silk, hemp, leather, suede). Nothing synthetic.",
            FabricPolicy.STRETCH: "Natural fibres, but up to 5% elastane for "
            "stretch is acceptable.",
            FabricPolicy.ANY: "Any fabric is acceptable.",
        }
        return {
            "brief": self.brief,
            "budget": f"{self.budget:.0f}",
            "currency": r.currency,
            "region_name": r.name,
            "sizes": self.sizes or "not specified",
            "categories": ", ".join(self.categories) or "whatever suits the brief",
            "fabric_rule": fabric_rules[self.fabric],
            "min_discount": str(self.min_discount),
            "notes": self.notes or "none",
        }


# ── Task outputs ───────────────────────────────────────────────


class PlannedItem(BaseModel):
    category: str = Field(description=f"One of: {', '.join(CATEGORIES)}")
    garment: str = Field(description="Specific garment, e.g. 'Oxford button-down shirt'")
    fabric: str
    colour: str
    fit_note: str
    priority: int = Field(description="1 = must have, 3 = nice to have")
    queries: list[str] = Field(description="2 to 3 shopping search queries")


class ShoppingPlan(BaseModel):
    items: list[PlannedItem]


class Lead(BaseModel):
    """A product seen in a search result or on a product page."""

    category: str
    garment: str = Field(description="Which planned garment this product is for")
    name: str
    retailer: str
    url: str = Field(description="Copied verbatim from a tool result")
    price: float = Field(description="Current selling price as a number")
    original_price: float | None = Field(
        default=None, description="Pre-sale / RRP price if the product is marked down"
    )
    currency: str
    image_url: str | None = None
    composition: str | None = Field(
        default=None, description="Fabric composition text, e.g. '100% cotton'"
    )
    in_stock: bool | None = None
    verified: bool = Field(
        default=False, description="True only if the product page confirmed the price"
    )


class LeadList(BaseModel):
    leads: list[Lead]


class Assessment(BaseModel):
    url: str
    style_fit: int = Field(ge=0, le=10, description="How well it suits the brief, 0-10")
    justification: str = Field(description="One sentence on why it is (or is not) a good buy")


class AssessmentList(BaseModel):
    assessments: list[Assessment]


# ── Final result ───────────────────────────────────────────────


class Deal(Lead):
    discount_pct: int = 0
    savings: float = 0
    fabric_verdict: str = "unknown"  # natural | stretch | synthetic | unknown
    style_fit: int = 0
    justification: str = ""
    score: int = 0
    label: str = "Fair"
    eligible: bool = True
    rejection: str | None = None
    picked: bool = False


class Report(BaseModel):
    brief: str
    currency: str
    budget: float
    deals: list[Deal]
    total: float
    total_savings: float
    remaining: float
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def picks(self) -> list[Deal]:
        return [d for d in self.deals if d.picked]
