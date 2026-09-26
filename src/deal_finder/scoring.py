"""Deterministic deal scoring.

The agents judge style; this module does the arithmetic. Keeping discounts,
fabric checks, budgets and link provenance in plain code means the numbers in
the final report can be trusted even when a model slips.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from deal_finder.models import (
    Assessment,
    Deal,
    FabricPolicy,
    HuntRequest,
    Lead,
    Report,
)

NATURAL = (
    "cotton", "linen", "flax", "wool", "merino", "cashmere", "silk", "hemp",
    "alpaca", "mohair", "camel", "yak", "leather", "suede", "nubuck", "shearling",
    "ramie", "jute",
)
STRETCH = ("elastane", "spandex", "lycra")
SYNTHETIC = (
    "polyester", "nylon", "polyamide", "acrylic", "polypropylene", "polyurethane",
    "viscose", "rayon", "modal", "lyocell", "tencel", "acetate", "microfibre",
    "microfiber", "faux leather", "vegan leather", "pu ", "recycled poly",
)

_PERCENT = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%\s*([a-z][a-z \-]*)", re.I)


def parse_price(value: str | float | int | None) -> float | None:
    """Pull a number out of strings like 'A$1,299.00' or 'AU$59.90 now'."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) if value >= 0 else None
    m = re.search(r"\d[\d,]*(?:\.\d+)?", value.replace(" ", " "))
    if not m:
        return None
    return float(m.group(0).replace(",", ""))


def fabric_verdict(composition: str | None) -> str:
    """Classify a composition string as natural, stretch, synthetic or unknown."""
    if not composition:
        return "unknown"
    text = f" {composition.lower()} "
    parts = _PERCENT.findall(text)
    if parts:
        stretch_pct = 0.0
        for pct, fibre in parts:
            fibre = f" {fibre.strip()} "
            if any(s in fibre for s in SYNTHETIC):
                return "synthetic"
            if any(s in fibre for s in STRETCH):
                stretch_pct += float(pct)
        if stretch_pct > 5:
            return "synthetic"
        return "stretch" if stretch_pct else "natural"
    if any(s in text for s in SYNTHETIC):
        return "synthetic"
    if any(s in text for s in STRETCH):
        return "stretch"
    if any(n in text for n in NATURAL):
        return "natural"
    return "unknown"


def fabric_allowed(verdict: str, policy: FabricPolicy) -> bool:
    if policy is FabricPolicy.ANY:
        return True
    if verdict == "synthetic":
        return False
    if verdict == "stretch":
        return policy is FabricPolicy.STRETCH
    return True  # natural, or unknown: kept but scored lower


def discount(price: float, original: float | None) -> tuple[int, float]:
    if not original or original <= price:
        return 0, 0.0
    saved = round(original - price, 2)
    return round(saved / original * 100), saved


def score(discount_pct: int, style_fit: int, verified: bool, verdict: str) -> int:
    """0-100. Discount and style dominate; verification and fabric break ties."""
    pts = min(discount_pct, 60) / 60 * 45
    pts += max(0, min(style_fit, 10)) / 10 * 35
    pts += 10 if verified else 0
    pts += {"natural": 10, "stretch": 6, "unknown": 3}.get(verdict, 0)
    return round(pts)


def label(points: int) -> str:
    if points >= 75:
        return "Great deal"
    if points >= 55:
        return "Good deal"
    return "Fair"


def normalise_url(url: str) -> str:
    return url.strip().rstrip("/").lower()


def finalize(
    request: HuntRequest,
    leads: Iterable[Lead],
    assessments: Iterable[Assessment],
    seen_urls: set[str] | None = None,
) -> Report:
    """Merge leads with assessments, score them and pick an outfit in budget.

    ``seen_urls`` holds every link a tool actually returned. When given, any
    lead whose URL never came out of a tool is dropped as a likely invention.
    """
    currency = request.region_info.currency
    fits = {normalise_url(a.url): a for a in assessments}
    seen = {normalise_url(u) for u in seen_urls} if seen_urls is not None else None

    deals: dict[str, Deal] = {}
    for lead in leads:
        key = normalise_url(lead.url)
        if not key.startswith("http"):
            continue
        if seen is not None and key not in seen:
            continue
        pct, saved = discount(lead.price, lead.original_price)
        verdict = fabric_verdict(lead.composition)
        fit = fits.get(key)
        style_fit = fit.style_fit if fit else 5
        pts = score(pct, style_fit, lead.verified, verdict)

        rejection = None
        if lead.currency.upper() != currency:
            rejection = f"Priced in {lead.currency}, not {currency}"
        elif not fabric_allowed(verdict, request.fabric):
            rejection = "Fabric does not meet your rules"
        elif pct < request.min_discount:
            rejection = f"Less than {request.min_discount}% off"
        elif lead.in_stock is False:
            rejection = "Out of stock"
        elif lead.price > request.budget:
            rejection = "Over budget on its own"

        deal = Deal(
            **lead.model_dump(),
            discount_pct=pct,
            savings=saved,
            fabric_verdict=verdict,
            style_fit=style_fit,
            justification=fit.justification if fit else "",
            score=pts,
            label=label(pts),
            eligible=rejection is None,
            rejection=rejection,
        )
        # The same product can surface from several queries; keep the best.
        if key not in deals or deals[key].score < deal.score:
            deals[key] = deal

    ranked = sorted(deals.values(), key=lambda d: (-d.score, d.price))

    # One pick per garment, best score first, while the budget lasts.
    remaining = request.budget
    covered: set[str] = set()
    for d in ranked:
        slot = d.garment.lower() or d.category.lower()
        if not d.eligible or slot in covered or d.price > remaining:
            continue
        d.picked = True
        covered.add(slot)
        remaining = round(remaining - d.price, 2)

    picks = [d for d in ranked if d.picked]
    return Report(
        brief=request.brief,
        currency=currency,
        budget=request.budget,
        deals=ranked,
        total=round(sum(d.price for d in picks), 2),
        total_savings=round(sum(d.savings for d in picks), 2),
        remaining=remaining,
    )


def to_markdown(report: Report, symbol: str = "$") -> str:
    """Render a buyer's guide. Links are printed exactly as stored."""
    money = lambda v: f"{symbol}{v:,.2f}"  # noqa: E731
    lines = [
        f"# Deals: {report.brief}",
        "",
        f"Budget {money(report.budget)} {report.currency} · "
        f"Spend {money(report.total)} · Saved {money(report.total_savings)}",
        "",
        "## Picks",
        "",
    ]
    picks = report.picks
    if not picks:
        lines += ["No products fitted the budget and rules.", ""]
    for d in picks:
        was = f" ~~{money(d.original_price)}~~" if d.discount_pct else ""
        off = f" · **{d.discount_pct}% off**" if d.discount_pct else ""
        lines += [
            f"### {d.garment.title()} — {d.name}",
            f"{d.retailer} · **{money(d.price)}**{was}{off} · {d.label} ({d.score})",
            "",
            *( [f"> {d.justification}", ""] if d.justification else [] ),
            f"[View at {d.retailer}]({d.url})",
            "",
        ]
    others = [d for d in report.deals if not d.picked and d.eligible]
    if others:
        lines += ["## Also worth a look", "", "| Product | Retailer | Price | Off | Score |",
                  "|---|---|---|---|---|"]
        for d in others:
            lines.append(
                f"| [{d.name}]({d.url}) | {d.retailer} | {money(d.price)} | "
                f"{d.discount_pct}% | {d.score} |"
            )
        lines.append("")
    return "\n".join(lines)
