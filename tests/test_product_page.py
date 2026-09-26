import json

from deal_finder.tools.product_page import extract_facts, is_public_http_url


def page(ld, extra=""):
    return f"""<html><head>
    <script type="application/ld+json">{json.dumps(ld)}</script>
    <meta property="og:image" content="https://cdn.shop.com/og.jpg">
    </head><body><h1>Shirt</h1><p>Was $120</p><p>Composition: 100% cotton</p>
    <script>var price = 1;</script>{extra}</body></html>"""


def test_reads_json_ld_product_in_graph():
    ld = {"@context": "https://schema.org", "@graph": [
        {"@type": "BreadcrumbList"},
        {"@type": "Product", "name": "Oxford Shirt", "brand": {"name": "Acme"},
         "material": "Cotton", "image": ["https://cdn.shop.com/1.jpg"],
         "offers": {"@type": "Offer", "price": "79.95", "priceCurrency": "AUD",
                    "availability": "https://schema.org/InStock",
                    "priceSpecification": [{"@type": "UnitPriceSpecification",
                                            "priceType": "https://schema.org/StrikethroughPrice",
                                            "price": 120}]}},
    ]}
    facts = extract_facts(page(ld))
    assert facts["name"] == "Oxford Shirt"
    assert facts["brand"] == "Acme"
    assert facts["price"] == "79.95"
    assert facts["currency"] == "AUD"
    assert facts["availability"] == "InStock"
    assert facts["original_price"] == 120
    assert facts["image"] == "https://cdn.shop.com/1.jpg"
    assert "Was $120" in facts["page_text"]
    assert "Composition: 100% cotton" in facts["page_text"]
    assert not any("var price" in t for t in facts["page_text"])


def test_falls_back_to_meta_tags():
    html = """<html><head>
    <meta property="og:title" content="Chinos">
    <meta property="product:price:amount" content="59.00">
    <meta property="product:price:currency" content="AUD">
    </head><body></body></html>"""
    facts = extract_facts(html)
    assert facts == {"name": "Chinos", "price": "59.00", "currency": "AUD"}


def test_blocks_private_addresses():
    assert not is_public_http_url("http://127.0.0.1/admin")
    assert not is_public_http_url("http://localhost:8000/")
    assert not is_public_http_url("file:///etc/passwd")
    assert not is_public_http_url("http://169.254.169.254/latest/meta-data")
