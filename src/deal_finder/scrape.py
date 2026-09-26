"""Read the men's sale listings of shops that are not on Shopify.

THE ICONIC and David Jones have no /products.json, but their sale listing
pages carry each product's name, brand, price, was-price and a link. We read a
capped number of pages, politely, and turn each card into a plain dict that
`normalize.from_listing` understands:

    {"id", "brand", "title", "url", "image", "hint", "price", "was", "sizes"}

`hint` is the shop's own category path, used before the title for categories.
An empty `sizes` means the shop did not say, which size filters treat as a match.
"""

from __future__ import annotations

import html
import json
import re
import time
from collections.abc import Callable

import requests

from deal_finder.stores import Store

HEADERS = {
    "User-Agent": "DealFinder/1.0 (personal price tracker; +https://github.com/zed6991/deal-finder-crew)",
    "Accept": "text/html",
    "Accept-Language": "en-AU",
}

# (listing path, most pages to read). Pages are ~60 items at THE ICONIC and
# 24 at David Jones; the caps keep one shop's sync well under a minute or two.
LISTINGS: dict[str, list[tuple[str, int]]] = {
    "theiconic": [("/mens-clothing-sale/", 15), ("/mens-shoes-sale/", 5)],
    "davidjones": [("/sale/men", 40)],
}


def _money(text: str | None) -> float | None:
    try:
        v = float(str(text).replace(",", "").lstrip("$"))
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


# --- THE ICONIC -------------------------------------------------------------

_ICONIC_DATA = re.compile(
    r'dl\.push\((\{\s*"event"\s*:\s*"ti\.data\.products"\s*,\s*"eventData"\s*:\s*\{\s*"list"\s*:\s*"catalog"\s*\}.*?\})\);\s*\n',
    re.S,
)
_ICONIC_CARD = re.compile(r'data-ti-track-product="([A-Z0-9]+)"')
_SIZE_PREFIX = re.compile(r"^(AU|US|UK|EU|International|Denim)\s+", re.I)


def parse_iconic(page: str, base: str = "https://www.theiconic.com.au") -> list[dict]:
    """Products on one THE ICONIC listing page."""
    m = _ICONIC_DATA.search(page)
    if not m:
        return []
    data = json.loads(m.group(1)).get("products") or {}
    # Each card starts at its tracking attribute; its first link and image are the product's.
    starts = [(c.group(1), c.start()) for c in _ICONIC_CARD.finditer(page)]
    links: dict[str, tuple[str | None, str | None]] = {}
    for i, (sku, start) in enumerate(starts):
        block = page[start: starts[i + 1][1] if i + 1 < len(starts) else start + 8000]
        href = re.search(r'href="(/[^"#]+\.html)"', block)
        img = re.search(r'<img[^>]+src="(https://[^"]+)"', block)
        links.setdefault(sku, (href and href.group(1), img and img.group(1)))

    out = []
    for sku, p in data.items():
        if p.get("gender") not in (None, "", "male", "unisex"):
            continue
        href, image = links.get(sku, (None, None))
        price = p.get("price") or {}
        out.append({
            "id": sku,
            "brand": html.unescape(p.get("brand") or ""),
            "title": html.unescape(p.get("name") or ""),
            "url": f"{base}{href}" if href else f"{base}/catalog/?q={sku}",
            "image": image,
            "hint": (p.get("category") or "").replace("/", " / "),
            "price": _money(price.get("final")),
            "was": _money(price.get("original")),
            "sizes": list(dict.fromkeys(_SIZE_PREFIX.sub("", s).strip() for s in (p.get("sizes") or {}).values())),
        })
    return out


# --- David Jones ------------------------------------------------------------

_DJ_CARD = re.compile(r'<article class="ProductCard.*?</article>', re.S)
_DJ_PRICE = re.compile(r"Price is now \$([\d,.]+), it was \$([\d,.]+)|Price \$([\d,.]+)")


def parse_davidjones(page: str, base: str = "https://www.davidjones.com") -> list[dict]:
    """Products on one David Jones listing page."""
    out = []
    for card in _DJ_CARD.findall(page):
        href = re.search(r'href="(/product/[^"]+)"', card)
        name = re.search(r"<h2[^>]*>([^<]+)</h2>", card)
        price = _DJ_PRICE.search(card)
        if not (href and name and price):
            continue
        brand = re.search(r"<p[^>]*__brand[^>]*>([^<]+)</p>", card)
        img = re.search(r'<img[^>]+src="(https://[^"]+)"', card)
        now, was, only = price.groups()
        sizes_block = re.search(r'aria-label="Sizes"[^>]*>(.*?)</ul>', card, re.S)
        sizes: list[str] = []
        # A "More" button means the card lists only some sizes: say nothing rather than mislead.
        if sizes_block and ">More<" not in sizes_block.group(1):
            sizes = [html.unescape(s).strip() for s in re.findall(r"<span[^>]*>([^<]+)</span>", sizes_block.group(1))]
        out.append({
            "id": href.group(1).rsplit("-", 1)[-1],
            "brand": html.unescape(brand.group(1)).strip() if brand else "",
            "title": html.unescape(name.group(1)).strip(),
            "url": f"{base}{href.group(1)}",
            "image": img and img.group(1),
            "hint": "",
            "price": _money(now or only),
            "was": _money(was),
            "sizes": sizes,
        })
    return out


PARSERS: dict[str, Callable[[str], list[dict]]] = {
    "theiconic": parse_iconic,
    "davidjones": parse_davidjones,
}


def fetch_listing(store: Store, session: requests.Session | None = None, pause: float = 1.0) -> list[dict]:
    """Read a shop's sale listings page by page until a page adds nothing or the cap is hit."""
    http = session or requests.Session()
    parse = PARSERS[store.key]
    products: dict[str, dict] = {}
    for path, max_pages in LISTINGS[store.key]:
        for page in range(1, max_pages + 1):
            resp = http.get(f"{store.base_url}{path}", params={"page": page} if page > 1 else None,
                            headers=HEADERS, timeout=30)
            if resp.status_code == 404 and page > 1:
                break  # ran past the last page
            resp.raise_for_status()
            batch = parse(resp.text)
            new = [p for p in batch if p["id"] not in products]
            products.update((p["id"], p) for p in new)
            if not new:
                break
            time.sleep(pause)  # be polite
    return list(products.values())
