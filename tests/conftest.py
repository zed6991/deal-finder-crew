import pytest

from deal_finder.db import DB
from deal_finder.stores import BY_KEY


def raw(handle, title, price, was=None, *, ptype="", vendor="M.J.Bale", tags=(), sizes=("S", "M", "L"),
        available=True, body="", colour="Navy"):
    """A /products.json entry, as Shopify sends it."""
    return {
        "handle": handle, "title": title, "product_type": ptype, "vendor": vendor,
        "tags": list(tags), "body_html": body,
        "options": [{"name": "Colour"}, {"name": "Size"}],
        "variants": [
            {"option1": colour, "option2": s, "price": f"{price:.2f}",
             "compare_at_price": f"{was:.2f}" if was else None, "available": available}
            for s in sizes
        ],
        "images": [{"src": f"https://cdn.shopify.com/{handle}.jpg"}],
    }


@pytest.fixture
def db():
    return DB(":memory:")


@pytest.fixture
def mjbale():
    return BY_KEY["mjbale"]
