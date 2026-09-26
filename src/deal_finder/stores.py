"""The shops Deal Finder reads.

Shopify shops publish their whole catalogue at /products.json, with sale and
full prices, for free. Shops that block that are reached through paid search
(Serper) only when you ask.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Store:
    key: str
    name: str
    domain: str
    tier: Literal["premium", "mid"]
    # "mens": menswear shop, drop anything marked women's.
    # "mixed": keep only items marked men's.
    gender: Literal["mens", "mixed"] = "mens"
    kind: Literal["shopify", "search"] = "shopify"

    @property
    def base_url(self) -> str:
        return f"https://{self.domain}"


STORES: list[Store] = [
    Store("mjbale", "M.J. Bale", "www.mjbale.com", "premium"),
    Store("pjohnson", "P. Johnson", "www.pjt.com", "premium"),
    Store("harrolds", "Harrolds", "www.harrolds.com.au", "premium", "mixed"),
    Store("aquila", "Aquila", "www.aquila.com.au", "premium"),
    Store("venroy", "Venroy", "www.venroy.com.au", "premium", "mixed"),
    Store("bassike", "Bassike", "www.bassike.com", "premium", "mixed"),
    Store("calibre", "Calibre", "www.calibre.com.au", "premium"),
    Store("peterjackson", "Peter Jackson", "www.peterjacksons.com", "mid"),
    Store("industrie", "Industrie", "www.industrie.com.au", "mid"),
    Store("academy", "Academy Brand", "www.academybrand.com", "mid", "mixed"),
    Store("jacjack", "Jac+Jack", "www.jacandjack.com", "mid", "mixed"),
    Store("oxford", "Oxford", "www.oxfordshop.com.au", "mid", "mixed"),
    Store("gazman", "Gazman", "www.gazman.com.au", "mid"),
    # Block automated catalogue reads; searched through Serper on request.
    Store("theiconic", "THE ICONIC", "www.theiconic.com.au", "mid", "mixed", "search"),
    Store("davidjones", "David Jones", "www.davidjones.com", "premium", "mixed", "search"),
    Store("countryroad", "Country Road", "www.countryroad.com.au", "mid", "mixed", "search"),
]

BY_KEY = {s.key: s for s in STORES}
FEEDS = [s for s in STORES if s.kind == "shopify"]
SEARCHED = [s for s in STORES if s.kind == "search"]
