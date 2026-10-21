import json

from deal_finder.normalize import from_listing
from deal_finder.scrape import fetch_listing, parse_davidjones, parse_iconic
from deal_finder.stores import BY_KEY
from deal_finder.sync import Syncer, normalise

ICONIC = BY_KEY["theiconic"]
DJ = BY_KEY["davidjones"]


def iconic_page(*items):
    """A THE ICONIC listing page: a data-layer blob plus one card per product."""
    products = {
        sku: {"id": sku, "name": name, "brand": "Levi s", "gender": gender, "category": cat,
              "price": {"original": was, "markdown": was, "final": now},
              "sizes": {f"{sku}-1": "AU M", f"{sku}-2": "Denim W32/L32"}}
        for sku, name, gender, cat, now, was in items
    }
    blob = json.dumps({"event": "ti.data.products", "eventData": {"list": "catalog"}, "products": products})
    cards = "".join(
        f'<div class="product" data-ti-track-product="{sku}"><a href="/{sku.lower()}-1.html">'
        f'<img src="https://img1.theiconic.com.au/{sku}.jpg"></a></div>'
        for sku, *_ in items
    )
    return f"<script>dl.push({blob});\n</script>{cards}"


def dj_card(pid, name, now, was=None, sizes=("S", "M"), more=False):
    price = f"Price is now ${now}, it was ${was}" if was else f"Price ${now}"
    size_items = "".join(f"<li><span>{s}</span></li>" for s in sizes) + ("<li><button>More</button></li>" if more else "")
    return (
        f'<article class="ProductCard-module__x__root"><a href="/product/brand-{name.lower().replace(" ", "-")}-{pid}">'
        f'<img alt="" src="https://www.davidjones.com/productimages/{pid}.jpg"/>'
        f'<p class="ProductCard-module__x__brand functionalBodyS">Gant</p><h2 class="n">{name}</h2>'
        f'<span style="position:absolute">{price}</span>'
        f'<ul aria-label="Sizes" class="s">{size_items}</ul></a></article>'
    )


def test_parse_iconic_reads_prices_sizes_links_and_skips_womens():
    page = iconic_page(
        ("LE1", "511 Slim Fit Jeans", "male", "Clothing/Jeans/Slim", "79.95", "109.95"),
        ("WO1", "Midi Dress", "female", "Clothing/Dresses", "50.00", "90.00"),
    )
    [item] = parse_iconic(page)
    assert item["id"] == "LE1" and item["brand"] == "Levi s"
    assert (item["price"], item["was"]) == (79.95, 109.95)
    assert item["sizes"] == ["M", "W32/L32"]
    assert item["url"] == "https://www.theiconic.com.au/le1-1.html"
    assert item["image"] == "https://img1.theiconic.com.au/LE1.jpg"


def test_parse_iconic_without_data_layer_is_empty():
    assert parse_iconic("<html>blocked</html>") == []


def test_parse_davidjones_reads_sale_and_full_price_cards():
    page = dj_card("111", "Oxford Shirt", "89.00", "139.00") + dj_card("222", "Slim Chino", "149.00", more=True)
    shirt, chino = parse_davidjones(page)
    assert shirt["id"] == "111" and shirt["brand"] == "Gant"
    assert (shirt["price"], shirt["was"]) == (89.0, 139.0)
    assert shirt["sizes"] == ["S", "M"]
    assert shirt["url"] == "https://www.davidjones.com/product/brand-oxford-shirt-111"
    assert (chino["price"], chino["was"]) == (149.0, None)
    assert chino["sizes"] == []  # "More" means a partial list: say nothing


def test_from_listing_categorises_and_skips():
    base = {"id": "1", "brand": "Gant", "url": "u", "image": None, "hint": "", "sizes": ["M"], "price": 89.0, "was": 139.0}
    shirt = from_listing(DJ, {**base, "title": "Oxford Shirt"})
    assert shirt.id == "davidjones:1" and shirt.category == "Shirts" and shirt.discount_pct == 36
    assert shirt.fabric == "unknown"
    assert from_listing(DJ, {**base, "title": "Cotton Trunks 3 Pack"}) is None
    assert from_listing(DJ, {**base, "title": "Women's Oxford Shirt"}) is None
    assert from_listing(DJ, {**base, "title": "Lapel Pin"}) is None
    # The shop's category path decides when the title is vague.
    assert from_listing(ICONIC, {**base, "title": "The 511", "hint": "Clothing / Jeans / Slim"}).category == "Jeans"
    # A was-price at or below the price is not a discount.
    assert from_listing(DJ, {**base, "title": "Oxford Shirt", "was": 80.0}).was_price is None


class FakeResponse:
    def __init__(self, status, text=""):
        self.status_code, self.text = status, text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def get(self, url, params, headers, timeout):
        self.calls.append((url, (params or {}).get("page", 1)))
        return self.pages.pop(0)


def test_fetch_listing_stops_when_a_page_adds_nothing(monkeypatch):
    monkeypatch.setattr("deal_finder.scrape.LISTINGS", {"davidjones": [("/sale/men", 10)]})
    one, two = dj_card("1", "Oxford Shirt", "89.00", "139.00"), dj_card("2", "Slim Chino", "99.00")
    session = FakeSession([FakeResponse(200, one), FakeResponse(200, two), FakeResponse(200, two)])
    got = fetch_listing(DJ, session, pause=0)
    assert [p["id"] for p in got] == ["1", "2"]
    assert session.calls == [("https://www.davidjones.com/sale/men", 1), ("https://www.davidjones.com/sale/men", 2),
                             ("https://www.davidjones.com/sale/men", 3)]


def test_listing_shop_syncs_into_the_database(db):
    page = iconic_page(("LE1", "511 Slim Fit Jeans", "male", "Clothing/Jeans/Slim", "79.95", "109.95"))
    result = Syncer(db, lambda store: parse_iconic(page)).sync([ICONIC])
    assert result["theiconic"]["count"] == 1
    assert [p.id for p in normalise(ICONIC, parse_iconic(page))] == ["theiconic:LE1"]
