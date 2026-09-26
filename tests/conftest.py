import os

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


PG_URL = os.environ.get("TEST_DATABASE_URL")
BACKENDS = ["sqlite", "postgres"] if PG_URL else ["sqlite"]


@pytest.fixture(params=BACKENDS)
def db(request):
    """Every database test runs on SQLite, and on Postgres when TEST_DATABASE_URL is set."""
    if request.param == "sqlite":
        yield DB(":memory:")
        return
    import psycopg

    with psycopg.connect(PG_URL, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    database = DB(url=PG_URL)
    yield database
    database.conn.close()


@pytest.fixture
def mjbale():
    return BY_KEY["mjbale"]
