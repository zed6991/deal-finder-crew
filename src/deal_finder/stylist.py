"""Turn a style brief into a shopping list, then fill it from the catalogue.

Reading the brief is free by default (presets and keyword rules). With an
Anthropic API key (or an OpenRouter key) it can use one small Claude call
instead, cached per brief so asking again costs nothing.

A look ("old money") and an occasion ("office") can come from the brief or be
picked directly. They steer which pieces rank first, using each product's
style labels (`style.py`). Up to three different looks are built, each within
budget, mixing shops, and avoiding clashes: two patterned pieces, two loud
colours, or one piece far more formal than the rest.
"""

from __future__ import annotations

import hashlib
import os
import re
from typing import Literal

from pydantic import BaseModel, Field

from deal_finder.db import DB
from deal_finder.deals import Filters, search
from deal_finder.normalize import CATEGORIES, _RULES_RE
from deal_finder.style import AESTHETICS, OCCASIONS, formality_range

Category = Literal[
    "Suits", "Tailoring", "Shirts", "Polos", "T-shirts", "Knitwear", "Sweats",
    "Outerwear", "Trousers", "Jeans", "Shorts", "Swim", "Shoes", "Accessories",
]
MODEL = os.environ.get("DEAL_FINDER_MODEL", "claude-opus-5")
OPENROUTER_URL = "https://openrouter.ai/api"
Aesthetic = Literal[tuple(AESTHETICS)]  # type: ignore[valid-type]
Occasion = Literal[tuple(OCCASIONS)]  # type: ignore[valid-type]
LOOKS = 3
REUSED = 25  # points against repeating a piece from an earlier look


class Slot(BaseModel):
    category: Category
    keywords: list[str] = Field(default_factory=list, description="Words to look for in product names, e.g. 'linen', 'loafer'")
    colours: list[str] = Field(default_factory=list)


class Plan(BaseModel):
    look: str = Field(description="A short name for the look, e.g. 'Smart casual'")
    slots: list[Slot] = Field(description="3 to 7 pieces, most important first")
    fabric: Literal["any", "natural", "stretch"] = "any"
    aesthetic: Aesthetic | None = Field(None, description="The overall look, if the brief suggests one")
    occasion: Occasion | None = Field(None, description="What it is for, if the brief says")
    source: Literal["preset", "keywords", "ai"] = "preset"


def S(category: str, *keywords: str) -> Slot:
    return Slot(category=category, keywords=list(keywords))


PRESETS: list[tuple[str, str, list[Slot]]] = [
    (r"wedding|formal|black tie|cocktail|race ?day|gala", "Wedding guest", [
        S("Suits", "suit"), S("Shirts", "white", "dress", "business"), S("Shoes", "derby", "oxford", "loafer"),
        S("Accessories", "tie"), S("Accessories", "pocket square"),
    ]),
    (r"office|work|business|corporate|interview", "Office", [
        S("Shirts", "business", "shirt"), S("Trousers", "trouser", "wool"), S("Tailoring", "blazer", "sport"),
        S("Shoes", "derby", "loafer"), S("Knitwear", "merino"), S("Accessories", "belt"),
    ]),
    (r"\bdate\b|night out|evening", "Date night", [
        S("Shirts", "linen", "oxford", "knit"), S("Trousers", "trouser", "chino", "pleat"),
        S("Knitwear", "merino", "polo", "fine"), S("Shoes", "loafer", "suede", "chelsea"),
        S("Outerwear", "jacket", "suede", "overshirt"),
    ]),
    (r"smart|dinner|drinks|party|lunch", "Smart casual", [
        S("Shirts", "oxford", "linen"), S("Knitwear", "merino", "knit"), S("Trousers", "chino", "trouser"),
        S("Tailoring", "blazer", "unstructured"), S("Shoes", "loafer", "suede"),
    ]),
    (r"summer|beach|holiday|resort|tropic|linen|hot", "Summer", [
        S("Shirts", "linen", "resort", "camp"), S("Shorts", "linen", "chino"), S("Polos", "knit", "polo"),
        S("Trousers", "linen", "drawstring"), S("Shoes", "loafer", "espadrille", "sneaker"),
    ]),
    (r"winter|cold|snow|layer|autumn", "Winter", [
        S("Outerwear", "coat", "overcoat", "wool"), S("Knitwear", "merino", "cashmere", "wool"),
        S("Trousers", "wool", "flannel"), S("Shirts", "oxford", "flannel"), S("Shoes", "boot", "chelsea"),
    ]),
    (r"weekend|casual|relaxed|street|errand|travel", "Weekend", [
        S("T-shirts", "tee"), S("Jeans", "denim"), S("Knitwear", "knit"), S("Outerwear", "jacket", "overshirt"),
        S("Shoes", "sneaker"),
    ]),
]
DEFAULT = PRESETS[3]  # smart casual
PRESET_OCCASION = {"Wedding guest": "wedding", "Office": "office", "Date night": "date_night", "Summer": "holiday", "Weekend": "weekend"}
OCCASION_PRESET = {o: next(p for p in PRESETS if p[1] == name) for name, o in PRESET_OCCASION.items()}

AESTHETIC_WORDS = {
    "old_money": r"old money|quiet luxury|preppy|\bivy\b|country club",
    "tailored": r"tailored|sharp|dapper",
    "minimalist": r"minimal|understated|clean lines",
    "coastal": r"coastal|riviera|amalfi|mediterranean",
    "rugged": r"rugged|workwear|heritage|outdoorsy",
    "streetwear": r"streetwear|street style|\bstreet\b|hype",
    "smart_casual": r"smart casual",
}
# Pieces for a look picked on its own, with no occasion or brief.
AESTHETIC_PIECES = {
    "old_money": [S("Knitwear", "cashmere", "merino", "cable"), S("Shirts", "oxford", "linen"), S("Trousers", "chino", "pleat", "linen"),
                  S("Tailoring", "blazer", "linen"), S("Shoes", "loafer", "suede")],
    "smart_casual": DEFAULT[2],
    "tailored": [S("Suits", "suit"), S("Shirts", "business", "dress"), S("Shoes", "derby", "oxford", "loafer"), S("Accessories", "tie")],
    "minimalist": [S("T-shirts", "heavyweight", "tee"), S("Trousers", "relaxed", "trouser"), S("Knitwear", "crew", "knit"),
                   S("Outerwear", "jacket", "coat"), S("Shoes", "sneaker", "leather")],
    "coastal": PRESETS[4][2],
    "rugged": [S("Shirts", "flannel", "overshirt", "chambray"), S("Jeans", "selvedge", "denim"), S("Outerwear", "chore", "field", "jacket"),
               S("Shoes", "boot")],
    "streetwear": [S("Sweats", "hood", "sweat"), S("T-shirts", "tee", "graphic"), S("Trousers", "cargo", "jogger"),
                   S("Outerwear", "bomber", "puffer"), S("Shoes", "sneaker")],
}

COLOURS = [
    "navy", "black", "white", "grey", "gray", "charcoal", "blue", "sky", "green", "olive",
    "khaki", "stone", "beige", "sand", "tan", "brown", "cognac", "camel", "cream", "ecru",
    "burgundy", "red", "pink", "rust", "orange", "yellow", "purple", "lilac",
]


def aesthetic_in(text: str) -> str | None:
    return next((k for k, rx in AESTHETIC_WORDS.items() if re.search(rx, text, re.I)), None)


def plan_free(brief: str, aesthetic: str | None = None, occasion: str | None = None) -> Plan:
    """No-cost reading: named garments win, else a matching preset or look."""
    text = brief.lower()
    aesthetic = aesthetic or aesthetic_in(text)
    colours = [c for c in COLOURS if re.search(rf"\b{c}\b", text)]
    fabric = "natural" if re.search(r"natural|no synthetic|no polyester", text) else "any"

    # Named garments, each with the colour written just before it:
    # "a navy blazer, white shirt and brown loafers".
    found = []
    for cat, rx in _RULES_RE:
        for m in rx.finditer(text):
            before = text[max(0, m.start() - 24): m.start()].split()[-2:]
            own = [c for c in COLOURS if c in before]
            found.append((m.start(), Slot(category=cat, keywords=[m.group(0).strip()], colours=own)))
    slots, seen = [], set()
    for _, slot in sorted(found, key=lambda x: x[0]):
        key = (slot.category, slot.keywords[0])
        if key not in seen:
            seen.add(key)
            slots.append(slot)
    if slots:
        return Plan(look="Your list", slots=slots[:7], fabric=fabric, aesthetic=aesthetic, occasion=occasion, source="keywords")

    preset = OCCASION_PRESET.get(occasion or "") or next((p for p in PRESETS if re.search(p[0], text)), None)
    if preset:
        name, pieces = preset[1], preset[2]
        occasion = occasion or PRESET_OCCASION.get(name)
    elif aesthetic:
        name, pieces = AESTHETICS[aesthetic], AESTHETIC_PIECES[aesthetic]
    else:
        name, pieces = DEFAULT[1], DEFAULT[2]
    if aesthetic and AESTHETICS[aesthetic] != name:
        name = f"{AESTHETICS[aesthetic]} · {name}"
    return Plan(
        look=name,
        slots=[s.model_copy(update={"colours": colours}) for s in pieces],
        fabric=fabric,
        aesthetic=aesthetic,
        occasion=occasion,
        source="preset",
    )


def _anthropic_key() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def _use_openrouter() -> bool:
    """An OpenRouter key wins, so a stray ANTHROPIC_API_KEY cannot shadow it."""
    return bool(os.environ.get("OPENROUTER_API_KEY"))


def ai_available() -> bool:
    return _anthropic_key() or _use_openrouter()


def openrouter_model() -> str:
    return os.environ.get("OPENROUTER_MODEL", f"anthropic/{MODEL}")


def ai_model() -> str:
    return openrouter_model() if _use_openrouter() else MODEL


SYSTEM = f"""You are a menswear stylist planning a small outfit from Australian shops.
Turn the shopper's brief into 3 to 7 pieces, most important first.
Each piece has a category (one of: {", ".join(CATEGORIES)}), 1 to 4 short
keywords likely to appear in product names (fabrics, cuts, styles), and any
colours that suit the brief. Set fabric to "natural" only if the shopper asks
for natural fibres, "stretch" if they allow a little stretch, else "any".
Set aesthetic (one of: {", ".join(AESTHETICS)}) and occasion (one of:
{", ".join(OCCASIONS)}) when the brief suggests them."""


def _ask_anthropic(brief: str) -> Plan | None:
    import anthropic

    client = anthropic.Anthropic()
    response = client.beta.messages.parse(
        model=MODEL,
        max_tokens=4000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "low"},
        system=SYSTEM,
        messages=[{"role": "user", "content": brief}],
        output_format=Plan,
    )
    return response.parsed_output if response.stop_reason != "refusal" else None


def _ask_openrouter(brief: str) -> Plan | None:
    """OpenRouter speaks the Messages API but not Anthropic's betas, so the
    plan comes back through a forced tool call instead of structured output."""
    import anthropic

    client = anthropic.Anthropic(base_url=OPENROUTER_URL, auth_token=os.environ["OPENROUTER_API_KEY"], api_key=None)
    schema = Plan.model_json_schema()
    schema["properties"].pop("source", None)
    response = client.messages.create(
        model=openrouter_model(),
        max_tokens=4000,
        system=SYSTEM,
        messages=[{"role": "user", "content": brief}],
        tools=[{"name": "plan_outfit", "description": "Record the outfit plan.", "input_schema": schema}],
        tool_choice={"type": "tool", "name": "plan_outfit"},
    )
    block = next((b for b in response.content if getattr(b, "type", "") == "tool_use"), None)
    return Plan.model_validate(block.input) if block else None


def plan_ai(brief: str, db: DB | None = None) -> Plan:
    """One Claude call, cached per brief. Falls back to the free reading."""
    key = "plan:" + hashlib.sha256(f"{ai_model()}|{brief.strip().lower()}".encode()).hexdigest()[:24]
    if db and (hit := db.get(key)):
        return Plan.model_validate(hit)

    plan = _ask_openrouter(brief) if _use_openrouter() else _ask_anthropic(brief)
    if plan is None or not plan.slots:
        return plan_free(brief)
    plan = plan.model_copy(update={"source": "ai", "slots": plan.slots[:7]})
    if db:
        db.put(key, plan.model_dump())
    return plan


def plan_for(
    brief: str, db: DB | None = None, use_ai: bool = False, aesthetic: str | None = None, occasion: str | None = None,
) -> Plan:
    """Read the brief. A look or occasion picked directly beats what the brief implies."""
    if brief.strip() and use_ai and ai_available():
        try:
            plan = plan_ai(brief, db)
            return plan.model_copy(update={
                "aesthetic": aesthetic or plan.aesthetic or aesthetic_in(brief),
                "occasion": occasion or plan.occasion,
            })
        except Exception:  # network, quota, bad key: the free reading still works
            pass
    return plan_free(brief, aesthetic, occasion)


def _text(item: dict) -> str:
    return f"{item['title']} {item['colour'] or ''} {item['composition'] or ''}".lower()


def _has(item: dict, words: list[str]) -> bool:
    text = _text(item)
    return any(re.search(rf"\b{re.escape(w.lower())}", text) for w in words)


def style_bonus(d: dict, plan: Plan) -> float:
    """How well a piece suits the plan's look and occasion, in deal-score points."""
    if not (plan.aesthetic or plan.occasion):
        return 0
    st = d["style"]
    pts = 0.0
    if plan.aesthetic:
        pts += 25 if plan.aesthetic in st["aesthetics"] else -10
    if plan.occasion:
        pts += 15 if plan.occasion in st["occasions"] else -25
    lo, hi = formality_range(plan.aesthetic, plan.occasion)
    pts -= 12 * max(lo - st["formality"], st["formality"] - hi, 0)
    return pts


def rank_for(slot: Slot, items: list[dict], strict: bool, plan: Plan | None = None) -> list[dict]:
    """Best matches first.

    For a list the shopper wrote ("navy blazer"), the named garment and colour
    are requirements whenever anything meets them. For a preset look they only
    lift matching pieces up the ranking. The plan's look and occasion lift
    pieces labelled for them.
    """
    if strict:
        for words in (slot.colours, slot.keywords):
            if words and any(_has(d, words) for d in items):
                items = [d for d in items if _has(d, words)]
    for d in items:
        d["fit"] = round(
            d["score"] + (30 if _has(d, slot.keywords) else 0) + (20 if _has(d, slot.colours) else 0)
            + (style_bonus(d, plan) if plan else 0), 1)
    return sorted(items, key=lambda d: -d["fit"])


def clash(d: dict, chosen: list[dict]) -> float:
    """Points against a piece for how badly it sits with the pieces already chosen."""
    if not chosen:
        return 0
    st = d["style"]
    pts = 0.0
    if st["patterned"] and any(c["style"]["patterned"] for c in chosen):
        pts += 20  # one patterned piece per outfit
    if st["loud"] and any(c["style"]["loud"] for c in chosen):
        pts += 12  # one bright colour per outfit
    mean = sum(c["style"]["formality"] for c in chosen) / len(chosen)
    if abs(st["formality"] - mean) > 1.5:
        pts += 15  # a suit jacket with track pants
    pts += 6 * sum(c["store"] == d["store"] for c in chosen)  # mix shops
    return pts


def family(d: dict) -> str:
    """Shops name a style once and sell it in many colours ("Phifer Navy Trouser",
    "Phifer Charcoal Trouser"), so a different look needs a different first word."""
    return f"{d['store']}:{(d['title'].split() or [''])[0].lower()}"


def _summary(picks: list[dict | None], budget: float) -> dict:
    chosen = [p for p in picks if p]
    total = round(sum(p["price"] for p in chosen), 2)
    return {
        "picks": picks,
        "total": total,
        "saved": round(sum(p["saving"] for p in chosen), 2),
        "remaining": round(budget - total, 2),
        "shops": len({p["store"] for p in chosen}),
    }


def build_outfit(
    db: DB,
    plan: Plan,
    budget: float,
    sizes: dict[str, str] | None = None,
    base: Filters | None = None,
) -> dict:
    """Up to three different looks within budget; the first fills `slots`."""
    base = base or Filters()
    pools = []
    for slot in plan.slots:
        f = Filters(
            categories=[slot.category], stores=base.stores, fabric=plan.fabric if plan.fabric != "any" else base.fabric,
            premium_only=base.premium_only, include_storewide=base.include_storewide,
            min_discount=base.min_discount, my_sizes=base.my_sizes, limit=400,
        )
        items = search(db, f, sizes)["items"]
        pools.append(rank_for(slot, items, strict=plan.source != "preset", plan=plan)[:60])

    looks: list[dict] = []
    earlier: set[str] = set()  # product families used by earlier looks
    for _ in range(LOOKS):
        taken: set[str] = set()
        chosen: list[dict] = []
        picks: list[dict | None] = []
        remaining = budget
        for i, pool in enumerate(pools):
            # Leave enough for the cheapest option of every slot still to fill.
            reserve = sum(min((d["price"] for d in p if d["id"] not in taken), default=0) for p in pools[i + 1:])
            options = [d for d in pool if d["id"] not in taken]
            within = [d for d in options if d["price"] <= remaining - reserve] or [d for d in options if d["price"] <= remaining]
            # Pieces from earlier looks may return, but only when nothing new fits nearly as well.
            pick = max(within, key=lambda d: d["fit"] - clash(d, chosen) - (REUSED if family(d) in earlier else 0), default=None)
            if pick:
                taken.add(pick["id"])
                chosen.append(pick)
                remaining -= pick["price"]
            picks.append(pick)
        ids = [p and p["id"] for p in picks]
        if not chosen or any(ids == [p and p["id"] for p in look["picks"]] for look in looks):
            break
        looks.append(_summary(picks, budget))
        earlier |= {family(d) for d in chosen}

    first = looks[0]["picks"] if looks else [None] * len(pools)
    slots = []
    for i, (slot, pool) in enumerate(zip(plan.slots, pools)):
        pick = first[i]
        # Other looks' pieces first, so swapping between looks stays one tap away.
        others = [look["picks"][i] for look in looks[1:] if look["picks"][i]]
        alternates, seen = [], {pick["id"]} if pick else set()
        for d in [*others, *pool]:
            if d["id"] not in seen and len(alternates) < 8:
                seen.add(d["id"])
                alternates.append(d)
        slots.append({"slot": slot.model_dump(), "pick": pick, "alternates": alternates})

    top = looks[0] if looks else _summary(first, budget)
    return {
        "plan": plan.model_dump(),
        "budget": budget,
        "total": top["total"],
        "saved": top["saved"],
        "remaining": top["remaining"],
        "slots": slots,
        "looks": looks,
    }
