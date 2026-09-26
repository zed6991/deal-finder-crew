"""Turn a style brief into a shopping list, then fill it from the catalogue.

Reading the brief is free by default (presets and keyword rules). With an
Anthropic API key (or an OpenRouter key) it can use one small Claude call
instead, cached per brief so asking again costs nothing.
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

Category = Literal[
    "Suits", "Tailoring", "Shirts", "Polos", "T-shirts", "Knitwear", "Sweats",
    "Outerwear", "Trousers", "Jeans", "Shorts", "Swim", "Shoes", "Accessories",
]
MODEL = os.environ.get("DEAL_FINDER_MODEL", "claude-opus-5")
OPENROUTER_URL = "https://openrouter.ai/api"


class Slot(BaseModel):
    category: Category
    keywords: list[str] = Field(default_factory=list, description="Words to look for in product names, e.g. 'linen', 'loafer'")
    colours: list[str] = Field(default_factory=list)


class Plan(BaseModel):
    look: str = Field(description="A short name for the look, e.g. 'Smart casual'")
    slots: list[Slot] = Field(description="3 to 7 pieces, most important first")
    fabric: Literal["any", "natural", "stretch"] = "any"
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
    (r"smart|dinner|date|drinks|party|lunch", "Smart casual", [
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
DEFAULT = PRESETS[2]  # smart casual

COLOURS = [
    "navy", "black", "white", "grey", "gray", "charcoal", "blue", "sky", "green", "olive",
    "khaki", "stone", "beige", "sand", "tan", "brown", "cognac", "camel", "cream", "ecru",
    "burgundy", "red", "pink", "rust", "orange", "yellow", "purple", "lilac",
]


def plan_free(brief: str) -> Plan:
    """No-cost reading: named garments win, else a matching preset."""
    text = brief.lower()
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
        return Plan(look="Your list", slots=slots[:7], fabric=fabric, source="keywords")

    name, pieces = next(((n, p) for rx, n, p in PRESETS if re.search(rx, text)), DEFAULT[1:])
    return Plan(
        look=name,
        slots=[s.model_copy(update={"colours": colours}) for s in pieces],
        fabric=fabric,
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
for natural fibres, "stretch" if they allow a little stretch, else "any"."""


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


def plan_for(brief: str, db: DB | None = None, use_ai: bool = False) -> Plan:
    if use_ai and ai_available():
        try:
            return plan_ai(brief, db)
        except Exception:  # network, quota, bad key: the free reading still works
            pass
    return plan_free(brief)


def _text(item: dict) -> str:
    return f"{item['title']} {item['colour'] or ''} {item['composition'] or ''}".lower()


def _has(item: dict, words: list[str]) -> bool:
    text = _text(item)
    return any(re.search(rf"\b{re.escape(w.lower())}", text) for w in words)


def rank_for(slot: Slot, items: list[dict], strict: bool) -> list[dict]:
    """Best matches first.

    For a list the shopper wrote ("navy blazer"), the named garment and colour
    are requirements whenever anything meets them. For a preset look they only
    lift matching pieces up the ranking.
    """
    if strict:
        for words in (slot.colours, slot.keywords):
            if words and any(_has(d, words) for d in items):
                items = [d for d in items if _has(d, words)]
    bonus = lambda d: (30 if _has(d, slot.keywords) else 0) + (20 if _has(d, slot.colours) else 0)  # noqa: E731
    return sorted(items, key=lambda d: -(d["score"] + bonus(d)))


def build_outfit(
    db: DB,
    plan: Plan,
    budget: float,
    sizes: dict[str, str] | None = None,
    base: Filters | None = None,
) -> dict:
    """Pick one piece per slot within budget, best matches first."""
    base = base or Filters()
    pools = []
    for slot in plan.slots:
        f = Filters(
            categories=[slot.category], stores=base.stores, fabric=plan.fabric if plan.fabric != "any" else base.fabric,
            premium_only=base.premium_only, include_storewide=base.include_storewide,
            min_discount=base.min_discount, my_sizes=base.my_sizes, limit=400,
        )
        items = search(db, f, sizes)["items"]
        pools.append(rank_for(slot, items, strict=plan.source != "preset"))

    used: set[str] = set()
    remaining = budget
    slots = []
    for i, (slot, pool) in enumerate(zip(plan.slots, pools)):
        # Leave enough for the cheapest option of every slot still to fill.
        reserve = sum(min((d["price"] for d in p if d["id"] not in used), default=0) for p in pools[i + 1:])
        pick = next((d for d in pool if d["id"] not in used and d["price"] <= remaining - reserve), None)
        if pick is None:
            pick = next((d for d in pool if d["id"] not in used and d["price"] <= remaining), None)
        if pick:
            used.add(pick["id"])
            remaining = round(remaining - pick["price"], 2)
        alternates = [d for d in pool if d["id"] not in used][:6]
        slots.append({"slot": slot.model_dump(), "pick": pick, "alternates": alternates})

    picks = [s["pick"] for s in slots if s["pick"]]
    return {
        "plan": plan.model_dump(),
        "budget": budget,
        "total": round(sum(p["price"] for p in picks), 2),
        "saved": round(sum(p["saving"] for p in picks), 2),
        "remaining": remaining,
        "slots": slots,
    }
