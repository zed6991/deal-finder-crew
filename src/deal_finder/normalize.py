"""Turn a shop's raw product into one clean, comparable record."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

from deal_finder.stores import Store

CATEGORIES = [
    "Suits", "Tailoring", "Shirts", "Polos", "T-shirts", "Knitwear", "Sweats",
    "Outerwear", "Trousers", "Jeans", "Shorts", "Swim", "Shoes", "Accessories",
]

# First match wins, so the order settles clashes: "t-shirt" before "shirt",
# "suit pant" before "suit", "swim short" before "short", "oxford shirt"
# stays a shirt because shoes need a shoe word.
_RULES: list[tuple[str, str]] = [
    ("Swim", r"swim|boardshort|board short"),
    ("T-shirts", r"t-?shirt|\btee\b|\btees\b|\btank\b|singlet"),
    ("Polos", r"\bpolo"),
    ("Trousers", r"suit pant|suit trouser|dress pant|trouser|chino|\bpants?\b|slack"),
    ("Jeans", r"\bjeans?\b|denim pant"),
    ("Shorts", r"\bshorts?\b"),
    ("Suits", r"\bsuits?\b(?! jacket)|tuxedo|dinner suit"),
    ("Tailoring", r"blazer|sport ?coat|sports? jacket|suit jacket|waistcoat|\bvest\b"),
    ("Shoes", r"shoe|boot|loafer|sneaker|trainer|derby|brogue|chukka|\bmonk\b|slipper|sandal|espadrille"),
    ("Shirts", r"shirt|overshirt"),
    ("Knitwear", r"knit|sweater|jumper|cardigan|crew ?neck|quarter zip|1/4 zip|merino|cashmere"),
    ("Sweats", r"hood|sweat|fleece|track ?top"),
    ("Outerwear", r"jacket|coat|parka|bomber|gilet|puffer|trench|overcoat|anorak"),
    ("Accessories", r"\btie\b|\bties\b|belt|pocket square|scarf|\bhat\b|\bcap\b|beanie|wallet|cufflink|\bbag\b|glove|tie bar"),
]
_RULES_RE = [(c, re.compile(p, re.I)) for c, p in _RULES]

# Things that are not clothes worth hunting, or not for men.
_SKIP = re.compile(
    r"gift ?card|underwear|\bbriefs?\b|\btrunks?\b|\bsocks?\b|sleepwear|pyjama|"
    r"fragrance|candle|\bhome\b|kids|junior|optical|sunglass|\bsun\b|eyewear|"
    r"dress(?:es)?\b(?! (?:shirt|shoe|pant|trouser))|\bskirt|\bbra\b|bikini|"
    r"alteration|voucher|sample|\bdonation|shipping protection|insurance|"
    r"\bbrush\b|shoe care|\bpolish\b|shoe tree|\blaces\b|insole|protector|\bcleaner\b|shoe horn",
    re.I,
)
# Shop words that mean something narrower than the general rules assume.
TYPE_OVERRIDES = {
    ("mjbale", "jackets"): "Tailoring",  # suit separates and sport coats
}

_MENS = re.compile(r"\b(men|mens|men's|male|him|gentlemen)\b", re.I)
_WOMENS = re.compile(r"\b(women|womens|women's|ladies|female|her|womenswear)\b", re.I)

NATURAL = (
    "cotton", "linen", "flax", "wool", "merino", "cashmere", "silk", "hemp",
    "alpaca", "mohair", "camel", "yak", "leather", "suede", "nubuck", "shearling",
    "ramie", "jute", "calfskin",
)
STRETCH = ("elastane", "spandex", "lycra")
SYNTHETIC = (
    "polyester", "nylon", "polyamide", "acrylic", "polypropylene", "polyurethane",
    "viscose", "rayon", "modal", "lyocell", "tencel", "acetate", "microfibre",
    "microfiber", "faux leather", "vegan leather", "recycled poly", "cupro",
)
_PERCENT = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%\s*([a-z][a-z \-]*)", re.I)
_COMPOSITION = re.compile(
    r"(?:\d{1,3}\s*%\s*[A-Za-z][A-Za-z ]{2,30}?(?:,|\s|$|\.|/|&|and)\s*){1,5}", re.I
)
_SIZE_OPTION = re.compile(r"^(size|chest|waist|shoe size|fit size)$", re.I)
_COLOUR_OPTION = re.compile(r"^colou?r$", re.I)


@dataclass
class Product:
    id: str
    store: str
    brand: str
    title: str
    url: str
    image: str | None
    category: str
    colour: str | None
    composition: str | None
    fabric: str
    sizes: list[str]
    price: float
    was_price: float | None
    in_stock: bool
    tags: list[str] = field(default_factory=list)

    @property
    def discount_pct(self) -> int:
        if not self.was_price or self.was_price <= self.price:
            return 0
        return round((self.was_price - self.price) / self.was_price * 100)


def fabric_verdict(composition: str | None) -> str:
    """natural, stretch (natural + up to 5% elastane), synthetic or unknown."""
    if not composition:
        return "unknown"
    text = f" {composition.lower()} "
    parts = _PERCENT.findall(text)
    if parts:
        stretch = 0.0
        for pct, fibre in parts:
            fibre = f" {fibre.strip()} "
            if any(s in fibre for s in SYNTHETIC):
                return "synthetic"
            if any(s in fibre for s in STRETCH):
                stretch += float(pct)
        if stretch > 5:
            return "synthetic"
        return "stretch" if stretch else "natural"
    if any(s in text for s in SYNTHETIC):
        return "synthetic"
    if any(s in text for s in STRETCH):
        return "stretch"
    if any(n in text for n in NATURAL):
        return "natural"
    return "unknown"


_SLEEVE = re.compile(r"\b(short|long)[- ]?(sleeved?|slv)\b|\b[sl]/s\b", re.I)
ONE_SIZE = re.compile(r"^(all|one size|os|o/s|default title|osfa|n/?a)$", re.I)


def category_of(*texts: str) -> str | None:
    """First category whose rule matches, trying each text in turn."""
    for text in texts:
        text = _SLEEVE.sub(" ", text or "")
        for cat, rx in _RULES_RE:
            if rx.search(text):
                return cat
    return None


def is_mens(store: Store, blob: str) -> bool:
    blob = re.sub(r"[_:>\-/|]", " ", blob)
    if store.gender == "mens":
        return not (_WOMENS.search(blob) and not _MENS.search(blob))
    return bool(_MENS.search(blob)) and not _WOMENS.search(blob)


def _strip_html(text: str | None) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", text or ""))


def composition_of(tags: list[str], body: str) -> str | None:
    for t in tags:
        key, _, val = t.partition(":")
        if val and key.lower().strip() in ("comp", "x-ap21r_comp", "composition", "fabric", "material"):
            if re.search(r"\d\s*%|" + "|".join(NATURAL + SYNTHETIC), val, re.I):
                return re.sub(r"\s+", " ", val).strip()
    m = _COMPOSITION.search(_strip_html(body))
    return re.sub(r"\s+", " ", m.group(0)).strip(" ,.&/") if m else None


def _money(value) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def from_shopify(store: Store, raw: dict) -> Product | None:
    """Normalise one /products.json entry, or None if it is not menswear."""
    tags = raw.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",")]
    title = html.unescape(raw.get("title") or "").strip()
    ptype = raw.get("product_type") or ""
    vendor = raw.get("vendor") or store.name
    blob = " ".join([vendor, ptype, title, *tags])

    if not title or _SKIP.search(f"{ptype} {title}"):
        return None
    if not is_mens(store, blob):
        return None
    category = TYPE_OVERRIDES.get((store.key, ptype.lower())) or category_of(ptype, title, " ".join(tags))
    if not category:
        return None

    options = raw.get("options") or []
    size_idx = next((i for i, o in enumerate(options) if _SIZE_OPTION.match(o.get("name", ""))), None)
    colour_idx = next((i for i, o in enumerate(options) if _COLOUR_OPTION.match(o.get("name", ""))), None)

    variants = [v for v in raw.get("variants") or [] if _money(v.get("price"))]
    if not variants:
        return None
    live = [v for v in variants if v.get("available", True)]
    pool = live or variants
    cheapest = min(pool, key=lambda v: float(v["price"]))
    price = float(cheapest["price"])
    was = _money(cheapest.get("compare_at_price"))
    if was and was <= price:
        was = None

    sizes: list[str] = []
    if size_idx is not None:
        for v in live:
            s = v.get(f"option{size_idx + 1}")
            if s and not ONE_SIZE.match(str(s).strip()) and s not in sizes:
                sizes.append(str(s))
    colour = None
    if colour_idx is not None:
        colour = cheapest.get(f"option{colour_idx + 1}")
    if not colour:
        colour = next((t.split(":", 1)[1] for t in tags if t.lower().startswith(("colour:", "x-colour:", "swatch:", "ap21colour:"))), None)

    images = raw.get("images") or []
    image = images[0].get("src") if images else None
    composition = composition_of(tags, raw.get("body_html") or "")

    brand = vendor.strip()
    squash = lambda t: re.sub(r"[^a-z0-9]", "", t.lower())  # noqa: E731
    if (
        squash(brand).startswith(squash(store.name))
        or re.search(r"^(mens|womens|unisex)$|pty ltd|shop|venroy -|academy (women|kids)", brand, re.I)
    ):
        brand = store.name

    return Product(
        id=f"{store.key}:{raw.get('handle') or raw.get('id')}",
        store=store.key,
        brand=brand,
        title=title,
        url=f"{store.base_url}/products/{raw.get('handle')}",
        image=image,
        category=category,
        colour=colour,
        composition=composition,
        fabric=fabric_verdict(composition),
        sizes=sizes,
        price=price,
        was_price=was,
        in_stock=bool(live),
        tags=[t for t in tags if len(t) < 60][:40],
    )
