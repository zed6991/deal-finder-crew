import pytest

from deal_finder.normalize import category_of, fabric_verdict, from_shopify, is_mens
from deal_finder.stores import BY_KEY

from .conftest import raw


@pytest.mark.parametrize(("text", "category"), [
    ("SHIRTS URBAN SHORT SLV", "Shirts"),
    ("Griffin Short Sleeve Shirt", "Shirts"),
    ("Walk Shorts", "Shorts"),
    ("Swim Short", "Swim"),
    ("L/S Tee", "T-shirts"),
    ("Suit Pant", "Trousers"),
    ("Suit Jacket", "Tailoring"),
    ("Navy Suit", "Suits"),
    ("Oxford Shirt", "Shirts"),
    ("Tan Oxford Brogue", "Shoes"),
    ("Chelsea Boots", "Shoes"),
    ("Merino Crew Neck", "Knitwear"),
    ("Wool Overcoat", "Outerwear"),
    ("Silk Tie", "Accessories"),
    ("Lapel Pin", None),
])
def test_category_of(text, category):
    assert category_of(text) == category


@pytest.mark.parametrize(("text", "verdict"), [
    ("100% cotton", "natural"),
    ("98% cotton, 2% elastane", "stretch"),
    ("90% cotton 10% elastane", "synthetic"),
    ("60% wool 40% polyester", "synthetic"),
    ("Full grain leather", "natural"),
    ("Linen viscose blend", "synthetic"),
    (None, "unknown"),
    ("Dry clean only", "unknown"),
])
def test_fabric_verdict(text, verdict):
    assert fabric_verdict(text) == verdict


def test_gender_rules():
    mens, mixed = BY_KEY["mjbale"], BY_KEY["oxford"]
    assert is_mens(mens, "Shirts Casual Navy")
    assert not is_mens(mens, "Womens Shirt")
    assert not is_mens(mixed, "Knitwear Urban")            # unmarked in a mixed shop
    assert is_mens(mixed, "Knitwear Gender_MENS")
    assert not is_mens(mixed, "LADIES KNITWEAR Gender_WOMENS")


def test_from_shopify_reads_prices_sizes_and_fabric(mjbale):
    p = from_shopify(mjbale, raw("horatio-peacoat", "Horatio Peacoat", 399, 699, ptype="Outerwear",
                                 tags=["comp:100% Wool", "colour:horatio-peacoat-black"]))
    assert p.id == "mjbale:horatio-peacoat"
    assert p.url == "https://www.mjbale.com/products/horatio-peacoat"
    assert (p.category, p.price, p.was_price, p.discount_pct) == ("Outerwear", 399, 699, 43)
    assert p.sizes == ["S", "M", "L"] and p.colour == "Navy"
    assert (p.composition, p.fabric) == ("100% Wool", "natural")
    assert p.brand == "M.J. Bale" and p.in_stock


def test_from_shopify_composition_from_description(mjbale):
    p = from_shopify(mjbale, raw("chino", "Tapered Chino", 99, ptype="Trousers",
                                 body="<p>Made in Italy.</p><p>98% Cotton 2% Elastane</p>"))
    assert p.fabric == "stretch"
    assert p.was_price is None and p.discount_pct == 0


def test_from_shopify_drops_non_menswear_and_extras(mjbale):
    assert from_shopify(mjbale, raw("gc", "Gift Card", 50, ptype="Gift Card")) is None
    assert from_shopify(mjbale, raw("brush", "Premium Shoe Brush", 25, ptype="Shoes")) is None
    assert from_shopify(mjbale, raw("sock", "Stripe Socks", 20, ptype="Socks")) is None
    assert from_shopify(BY_KEY["oxford"], raw("dress", "Zoe Dress", 50, ptype="DRESSES URBAN")) is None
    assert from_shopify(mjbale, raw("pin", "Lapel Pin", 30, ptype="Seasonal Acc")) is None


def test_brushed_fabric_is_not_a_brush(mjbale):
    p = from_shopify(mjbale, raw("b", "Brushed Twill Check Shirt", 60, ptype="Long Sleeve Shirt"))
    assert p is not None and p.category == "Shirts"


def test_sold_out_and_one_size(mjbale):
    p = from_shopify(mjbale, raw("tie", "Silk Tie", 59, sizes=("One Size",), available=False))
    assert p.sizes == [] and not p.in_stock


def test_shop_overrides(mjbale):
    p = from_shopify(mjbale, raw("ruvolo", "Ruvolo Jacket", 299, 899, ptype="Jackets"))
    assert p.category == "Tailoring"
    other = from_shopify(BY_KEY["gazman"], raw("j", "Rain Jacket", 99, ptype="Jackets", vendor="GAZMAN"))
    assert other.category == "Outerwear" and other.brand == "Gazman"


def test_dress_boots_are_shoes_not_dresses(mjbale):
    boot = from_shopify(mjbale, raw("boot", "Talan Chelsea Boot", 169, ptype="Dress Boots"))
    assert boot is not None and boot.category == "Shoes"
