"""Read a product page and pull out the facts that matter for a deal."""

from __future__ import annotations

import ipaddress
import json
import re
import socket
from typing import Any
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from deal_finder.progress import Tracker

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.5 Safari/605.1.15"
    ),
    "Accept-Language": "en-AU,en;q=0.9",
}
KEYWORDS = re.compile(
    r"(was|rrp|save|sale|off|now|cotton|linen|wool|merino|cashmere|silk|"
    r"polyester|elastane|nylon|viscose|composition|material|fabric|in stock|sold out)",
    re.I,
)


def is_public_http_url(url: str) -> bool:
    """Refuse anything that is not a public http(s) address."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            return False
    return True


def fetch(url: str, max_redirects: int = 5) -> requests.Response:
    """GET a page, checking every redirect hop is still a public address."""
    for _ in range(max_redirects + 1):
        if not is_public_http_url(url):
            raise ValueError("not a public web address")
        resp = requests.get(url, headers=HEADERS, timeout=15, allow_redirects=False)
        if resp.is_redirect and resp.headers.get("location"):
            url = requests.compat.urljoin(url, resp.headers["location"])
            continue
        resp.raise_for_status()
        return resp
    raise requests.TooManyRedirects(f"more than {max_redirects} redirects")


def _walk(node: Any):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def _is_product(node: dict) -> bool:
    t = node.get("@type")
    types = t if isinstance(t, list) else [t]
    return any(str(x).lower() in ("product", "productgroup") for x in types)


def extract_facts(html: str) -> dict:
    """Pull price facts from JSON-LD, meta tags and visible text."""
    soup = BeautifulSoup(html, "html.parser")
    facts: dict[str, Any] = {}

    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        product = next((n for n in _walk(data) if _is_product(n)), None)
        if not product:
            continue
        facts["name"] = product.get("name")
        facts["brand"] = (product.get("brand") or {}).get("name") if isinstance(
            product.get("brand"), dict
        ) else product.get("brand")
        facts["material"] = product.get("material")
        image = product.get("image")
        facts["image"] = image[0] if isinstance(image, list) and image else image
        offers = [n for n in _walk(product.get("offers", [])) if "price" in n or "lowPrice" in n]
        if offers:
            o = offers[0]
            facts["price"] = o.get("price") or o.get("lowPrice")
            facts["currency"] = o.get("priceCurrency")
            facts["availability"] = str(o.get("availability", "")).rsplit("/", 1)[-1] or None
            for spec in _walk(o.get("priceSpecification", [])):
                kind = str(spec.get("priceType", "")).lower()
                if "strikethrough" in kind or "listprice" in kind or "srp" in kind:
                    facts["original_price"] = spec.get("price")
        break

    for prop, key in [
        ("og:title", "name"),
        ("og:image", "image"),
        ("product:price:amount", "price"),
        ("og:price:amount", "price"),
        ("product:price:currency", "currency"),
        ("product:sale_price:amount", "sale_price"),
        ("product:original_price:amount", "original_price"),
    ]:
        tag = soup.find("meta", attrs={"property": prop}) or soup.find("meta", attrs={"name": prop})
        if tag and tag.get("content") and not facts.get(key):
            facts[key] = tag["content"]

    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    lines = [ln.strip() for ln in soup.get_text("\n").splitlines()]
    hits = [ln for ln in lines if 3 < len(ln) < 160 and KEYWORDS.search(ln)]
    facts["page_text"] = list(dict.fromkeys(hits))[:40]
    return {k: v for k, v in facts.items() if v not in (None, "", [])}


class PageInput(BaseModel):
    url: str = Field(..., description="The exact product URL, copied from a search result")


class ProductPageTool(BaseTool):
    name: str = "read_product_page"
    description: str = (
        "Open a product page and return its current price, original (was) price "
        "if marked down, currency, fabric/material, stock and image. Use it to "
        "confirm a product before recommending it."
    )
    args_schema: type[BaseModel] = PageInput
    tracker: Any = Field(default=None, exclude=True)

    def _run(self, url: str) -> str:
        tracker = self.tracker if isinstance(self.tracker, Tracker) else None
        try:
            resp = fetch(url)
        except ValueError as exc:
            return f"Refused: {exc}"
        except requests.RequestException as exc:
            if tracker:
                tracker.page_done(url, ok=False)
            return f"Could not load page: {exc}"
        facts = extract_facts(resp.text[:2_000_000])
        facts["final_url"] = resp.url
        if tracker:
            tracker.saw(url, resp.url)
            tracker.page_done(url, ok=True)
        return json.dumps(facts, ensure_ascii=False)
