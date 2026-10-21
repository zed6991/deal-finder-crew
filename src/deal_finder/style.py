"""Style labels: which looks and occasions each piece suits.

Every product gets labels from free rules (category, name, colour, fabric and
shop). With an OpenRouter key, new products can also be labelled by a small
Claude model, about US$0.0001 a product, capped per day and stored, so each
product is only ever labelled once. Model labels replace the rule labels for
looks, occasions, formality and pattern.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field

from deal_finder.db import DB, now

log = logging.getLogger(__name__)

AESTHETICS = {
    "old_money": "Old money",
    "smart_casual": "Smart casual",
    "tailored": "Tailored",
    "minimalist": "Minimalist",
    "coastal": "Coastal",
    "rugged": "Rugged",
    "streetwear": "Streetwear",
}
OCCASIONS = {
    "office": "Office",
    "date_night": "Date night",
    "wedding": "Wedding",
    "weekend": "Weekend",
    "holiday": "Holiday",
}
# The formality (1 casual to 5 formal) each look and occasion allows.
AESTHETIC_RANGE = {
    "old_money": (2, 5), "smart_casual": (2, 4), "tailored": (3, 5), "minimalist": (1, 4),
    "coastal": (1, 3), "rugged": (1, 3), "streetwear": (1, 2),
}
OCCASION_RANGE = {"office": (3, 5), "date_night": (2, 4), "wedding": (4, 5), "weekend": (1, 3), "holiday": (1, 3)}

LABEL_MODEL = os.environ.get("DEAL_FINDER_LABEL_MODEL", "anthropic/claude-haiku-5.5")
LABEL_CAP = int(os.environ.get("DEAL_FINDER_LABEL_CAP", "3000"))  # products a day
LABEL_VERSION = 1  # bump when the vocabulary changes, to label again
BATCH = 40
OPENROUTER_URL = "https://openrouter.ai/api"


# ── Free rules ────────────────────────────────────────────────

def _rx(pattern: str) -> re.Pattern:
    return re.compile(pattern, re.I)


BASE_FORMALITY = {
    "Suits": 5, "Tailoring": 4, "Shirts": 3, "Polos": 2, "T-shirts": 1, "Knitwear": 3, "Sweats": 1,
    "Outerwear": 3, "Trousers": 3, "Jeans": 2, "Shorts": 1, "Swim": 1, "Shoes": 3, "Accessories": 3,
}
# Per category, the first matching rule sets the formality.
FORMALITY_RULES: dict[str, list[tuple[re.Pattern, int]]] = {
    "Shirts": [(_rx(r"dress shirt|business|formal|poplin|french cuff|evening"), 4),
               (_rx(r"camp|resort|cuban|short sleeve|s/s|hawaiian|overshirt|flannel|denim|chambray|cord|shacket|printed"), 2)],
    "Polos": [(_rx(r"knit|merino|wool|cashmere|long sleeve"), 3)],
    "Knitwear": [(_rx(r"hood"), 2)],
    "Outerwear": [(_rx(r"overcoat|top ?coat|trench|car coat|peacoat|pea coat|wool coat|cashmere"), 4),
                  (_rx(r"puffer|anorak|windbreaker|rain|spray|bomber|hood|fleece|gilet|vest|denim|track|shell|parka"), 2)],
    "Trousers": [(_rx(r"suit|dress pant|wool|pleat|tailored|formal|flannel"), 4),
                 (_rx(r"jogger|track|sweat|lounge|trackpant"), 1),
                 (_rx(r"drawstring|cargo|elastic|parachute|utility"), 2)],
    "Shorts": [(_rx(r"tailored|chino|linen|pleat"), 2)],
    "Shoes": [(_rx(r"sandal|slide|espadrille|thong|flip|slipper"), 1),
              (_rx(r"sneaker|trainer|runner|court|canvas|boat|deck"), 2),  # before "derby": "Derby Sneaker"
              (_rx(r"oxford|derby|brogue|monk|dress shoe|patent|wholecut"), 4)],
    "Accessories": [(_rx(r"\bties?\b|bow tie"), 5),
                    (_rx(r"pocket square|cufflink|tie bar"), 4),
                    (_rx(r"\bcap\b|beanie|bucket|tote|backpack|bag"), 1),
                    (_rx(r"\bhat\b"), 2)],
}
PATTERNED = _rx(
    r"stripe|check|plaid|gingham|houndstooth|print|floral|graphic|logo|camo|paisley|tartan|"
    r"\bdots?\b|polka|jacquard|tie.?dye|hawaiian|fair ?isle|argyle|windowpane|tropical|motif|pattern|breton"
)
LOUD = _rx(r"\b(red|pink|orange|yellow|purple|lilac|lavender|lime|teal|fuchsia|coral|mint|aqua|neon|multi|bright|mustard)\b")
PREMIUM_CLASSIC = {"mjbale", "pjohnson", "aquila", "harrolds"}

# For each look: words that suggest it, colours that suit it, words that rule
# it out, and shops whose whole range leans that way.
SIGNALS: dict[str, dict] = {
    "old_money": dict(
        words=_rx(r"cashmere|merino|cable|linen|oxford|polo|loafer|chino|pleat|blazer|tweed|cardigan|quarter zip|half zip|"
                  r"suede|seersucker|houndstooth|herringbone|flannel|camel|cricket|crew ?neck|silk|boat shoe|knit"),
        colours=_rx(r"navy|cream|ecru|camel|stone|white|brown|olive|green|sky|oat|beige|tan|chocolate|bone|ivory"),
        never=_rx(r"hood|graphic|logo|cargo|jogger|track|distressed|ripped|puffer|sneaker|trainer|runner|\btee\b|t-shirt|sweat|camo|swim|\bshorts?\b"),
        shops=PREMIUM_CLASSIC, shop_votes=1, natural=True, threshold=3,
    ),
    "smart_casual": dict(
        words=_rx(r"chino|oxford|knit|polo|merino|loafer|suede|overshirt|linen|blazer|unstructured|chelsea|derby|cardigan|crew|trouser"),
        colours=None, never=_rx(r"hood|jogger|track|graphic|swim|sweat|slide|thong"), shops=set(), natural=False, threshold=2,
    ),
    "tailored": dict(
        words=_rx(r"tailored|suit|blazer|wool|dress|business|french cuff|double breasted|tuxedo|\bties?\b|pocket square|derby|oxford shoe|brogue|monk"),
        colours=None, never=_rx(r"hood|jogger|track|graphic|swim|sweat|denim|cargo|sneaker"), shops=set(), natural=False, threshold=2,
    ),
    "minimalist": dict(
        words=_rx(r"essential|boxy|relaxed|clean|minimal|heavyweight|organic|plain|mock neck|raw|core|everyday|classic"),
        colours=_rx(r"\b(black|white|grey|gray|navy|stone|ecru|charcoal|bone|oat|cream|sand)\b"),
        never=PATTERNED, shops={"bassike", "calibre"}, natural=False, threshold=3,
    ),
    "coastal": dict(
        words=_rx(r"linen|resort|camp|cuban|short sleeve|stripe|breton|seersucker|terry|towel|espadrille|swim|boardshort|crochet|"
                  r"drawstring|\bshorts?\b|sandal|boat shoe|deck|panama|straw|summer|riviera|polo"),
        colours=_rx(r"white|sky|blue|sand|cream|ecru|navy|stone|bone"), never=_rx(r"wool|flannel|tweed|puffer|boot|corduroy|fleece"),
        shops={"venroy"}, natural=False, threshold=3,
    ),
    "rugged": dict(
        words=_rx(r"denim|\bjeans?\b|boot|chore|workwear|flannel|corduroy|\bcords?\b|canvas|waxed|field|utility|suede|leather|heritage|"
                  r"selvedge|chambray|moleskin|shearling|trucker|plaid|overshirt|military|cargo"),
        colours=_rx(r"brown|tan|olive|khaki|rust|indigo|charcoal|tobacco|chocolate|army"),
        never=_rx(r"silk|tuxedo|swim|pocket square|\bties?\b|loafer|cashmere"),
        shops=set(), natural=False, threshold=2,
    ),
    "streetwear": dict(
        words=_rx(r"hood|graphic|logo|oversized|boxy|cargo|jogger|track|sweat|puffer|bomber|sneaker|trainer|bucket hat|\bcap\b|"
                  r"beanie|varsity|tie.?dye|baggy|skate|parachute|windbreaker|anorak|\btee\b"),
        colours=None, never=_rx(r"silk|tuxedo|suit|blazer|loafer|derby|oxford shoe|pocket square|\bties?\b|cashmere"),
        shops=set(), natural=False, threshold=2,
    ),
}


def formality(category: str, title: str) -> int:
    for rx, level in FORMALITY_RULES.get(category, []):
        if rx.search(title):
            return level
    return BASE_FORMALITY.get(category, 3)


def occasions_for(category: str, level: int, aesthetics: list[str]) -> list[str]:
    out = []
    if level >= 3 and category not in ("Swim", "Shorts", "T-shirts", "Sweats"):
        out.append("office")
    if 2 <= level <= 4 and category not in ("Swim", "Shorts", "Sweats"):
        out.append("date_night")
    if level >= 4:
        out.append("wedding")
    if level <= 3 and category != "Suits":
        out.append("weekend")
    if "coastal" in aesthetics or category in ("Swim", "Shorts"):
        out.append("holiday")
    return out


@lru_cache(maxsize=60_000)
def rule_style(category: str, title: str, colour: str, composition: str, store: str, fabric: str) -> dict:
    """Labels from the product's own details. Free and instant."""
    text = f"{title} {colour} {composition}"
    level = formality(category, title)
    patterned = bool(PATTERNED.search(f"{title} {colour}"))
    aesthetics = []
    for key, s in SIGNALS.items():
        lo, hi = AESTHETIC_RANGE[key]
        if not lo <= level <= hi or s["never"].search(title):
            continue
        votes = min(len(set(m.group(0).lower() for m in s["words"].finditer(text))), 3)
        votes += bool(s["colours"] and s["colours"].search(f"{colour} {title}"))
        votes += s.get("shop_votes", 2) if store in s["shops"] else 0
        votes += bool(s["natural"] and fabric == "natural")
        if key == "tailored":
            votes += 2 if category in ("Suits", "Tailoring") else level >= 4
        if key == "smart_casual" and level == 3:
            votes += 1
        if votes >= s["threshold"]:
            aesthetics.append(key)
    return {
        "aesthetics": aesthetics,
        "occasions": occasions_for(category, level, aesthetics),
        "formality": level,
        "patterned": patterned,
        "loud": bool(LOUD.search(f"{colour} {title}")),
        "source": "rules",
    }


def style_of(r: dict) -> dict:
    """A product's labels: the model's when it has labelled it, else the rules'."""
    s = rule_style(r.get("category") or "", r.get("title") or "", r.get("colour") or "",
                   r.get("composition") or "", r.get("store") or "", r.get("fabric") or "")
    ai = r.get("ai_style")
    if not ai:
        return s
    try:
        ai = json.loads(ai) if isinstance(ai, str) else ai
    except ValueError:
        return s
    return {**s, **{k: ai[k] for k in ("aesthetics", "occasions", "formality", "patterned") if k in ai}, "source": "ai"}


def fits(style: dict, aesthetic: str | None = None, occasion: str | None = None) -> bool:
    return (not aesthetic or aesthetic in style["aesthetics"]) and (not occasion or occasion in style["occasions"])


def formality_range(aesthetic: str | None, occasion: str | None) -> tuple[int, int]:
    """Where the look and the occasion overlap; the occasion wins if they do not."""
    lo, hi = AESTHETIC_RANGE.get(aesthetic or "", (1, 5))
    olo, ohi = OCCASION_RANGE.get(occasion or "", (1, 5))
    if max(lo, olo) <= min(hi, ohi):
        return max(lo, olo), min(hi, ohi)
    return olo, ohi


# ── Labelling with a model (optional, paid) ───────────────────

class Label(BaseModel):
    i: int = Field(description="The product's number in the list")
    aesthetics: list[Literal[tuple(AESTHETICS)]] = Field(default_factory=list)  # type: ignore[valid-type]
    occasions: list[Literal[tuple(OCCASIONS)]] = Field(default_factory=list)  # type: ignore[valid-type]
    formality: int = Field(ge=1, le=5)
    patterned: bool = False


class Labels(BaseModel):
    items: list[Label]


SYSTEM = f"""You label menswear for an outfit builder. For each numbered product
give the looks it suits (any of: {", ".join(AESTHETICS)}; none is fine), the
occasions it suits (any of: {", ".join(OCCASIONS)}), formality from 1 (gym,
beach) to 5 (black tie), and whether it is patterned. old_money means quiet,
classic, well-made pieces (cashmere, oxford shirts, chinos, loafers), not logos
or athleisure. Label every product, in order."""


def available() -> bool:
    return bool(os.environ.get("OPENROUTER_API_KEY"))


_quota = threading.Lock()


def used_today(db: DB) -> int:
    return db.get(f"label-count:{date.today()}", 0)


def _reserve(db: DB, n: int) -> int:
    """Claim up to n of today's labels before calling the model, so shops
    labelling at the same time cannot overshoot the cap together."""
    with _quota:
        used = used_today(db)
        granted = max(0, min(n, LABEL_CAP - used))
        if granted:
            db.put(f"label-count:{date.today()}", used + granted)
        return granted


def counts(db: DB) -> dict:
    r = db.query(
        "SELECT COUNT(*) total, COUNT(s.product_id) labelled FROM products p "
        "LEFT JOIN styles s ON s.product_id = p.id AND s.version = ? WHERE p.active = 1", (LABEL_VERSION,))[0]
    return {"total": r["total"], "labelled": r["labelled"]}


def _ask(products: list[dict]) -> list[Label]:
    import anthropic

    client = anthropic.Anthropic(base_url=OPENROUTER_URL, auth_token=os.environ["OPENROUTER_API_KEY"], api_key=None)
    lines = "\n".join(
        f"{i}. {p['brand']} | {p['title']} | {p['category']} | {p['colour'] or '-'} | {p['composition'] or '-'}"
        for i, p in enumerate(products)
    )
    response = client.messages.create(
        model=LABEL_MODEL,
        max_tokens=80 * len(products) + 400,
        system=SYSTEM,
        messages=[{"role": "user", "content": lines}],
        tools=[{"name": "label_products", "description": "Record the labels.", "input_schema": Labels.model_json_schema()}],
        tool_choice={"type": "tool", "name": "label_products"},
    )
    block = next((b for b in response.content if getattr(b, "type", "") == "tool_use"), None)
    if not block:
        return []
    out = []
    for item in (block.input or {}).get("items", []):
        # Keep the words we know; a made-up one ("casual") should not sink the batch.
        item = {**item, "aesthetics": [a for a in item.get("aesthetics") or [] if a in AESTHETICS],
                "occasions": [o for o in item.get("occasions") or [] if o in OCCASIONS]}
        try:
            out.append(Label.model_validate(item))
        except ValueError:
            continue
    return out


def label_new(db: DB, store: str | None = None, budget_s: float = 60, workers: int = 4) -> dict:
    """Label products the model has not seen yet, within the daily cap and a time budget."""
    if not available():
        return {"labelled": 0, "skipped": "no OPENROUTER_API_KEY"}
    if used_today(db) >= LABEL_CAP:
        return {"labelled": 0, "skipped": "daily cap reached"}
    sql = (
        "SELECT p.id, p.brand, p.title, p.category, p.colour, p.composition FROM products p "
        "LEFT JOIN styles s ON s.product_id = p.id AND s.version = ? WHERE p.active = 1 AND s.product_id IS NULL"
    )
    args: list = [LABEL_VERSION]
    if store:
        sql += " AND p.store = ?"
        args.append(store)
    todo = db.query(sql + " ORDER BY p.id", args)
    if not todo:
        return {"labelled": 0}

    deadline = time.monotonic() + budget_s
    batches = [todo[i:i + BATCH] for i in range(0, len(todo), BATCH)]
    done = failed = 0

    def run(batch: list[dict]) -> int:
        if time.monotonic() > deadline:
            return 0
        batch = batch[:_reserve(db, len(batch))]
        if not batch:
            return 0
        try:
            labels = _ask(batch)
        except Exception as exc:  # network, quota, bad output: the rules still label it
            log.warning("labelling failed: %s", exc)
            return -1
        at = now()
        rows = [
            (batch[x.i]["id"], x.model_dump_json(exclude={"i"}), LABEL_MODEL, LABEL_VERSION, at)
            for x in labels if 0 <= x.i < len(batch)
        ]
        db.write_styles(rows)
        return len(rows)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for n in pool.map(run, batches):
            if n < 0:
                failed += 1
            else:
                done += n
    return {"labelled": done, "failed_batches": failed, "left": len(todo) - done}
