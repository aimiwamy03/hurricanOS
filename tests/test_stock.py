"""Nimble product matching keeps useful products and rejects common false positives."""

from __future__ import annotations

from agent.stock import matching_products, product_rows, status_from_products


def _product(
    name: str,
    *,
    product_id: str = "1",
    seller: str = "Walmart.com",
    availability: str = "In stock",
    price: float = 10.0,
) -> dict:
    return {
        "product_id": product_id,
        "product_name": name,
        "product_seller": seller,
        "product_availability": availability,
        "product_price": price,
        "product_url": f"https://www.walmart.com/ip/{product_id}?tracking=yes",
        "product_image": f"https://images.example/{product_id}.png?size=180",
    }


def test_bottled_water_keeps_gallon_bottles_and_rejects_reusable_bottles() -> None:
    products = [
        _product("Great Value Purified Drinking Water, PET 1 Gallon Bottle", product_id="water"),
        _product("Insulated Reusable Water Bottle, 32 oz", product_id="reusable"),
        _product("Replacement Refrigerator Water Filter", product_id="filter"),
    ]
    assert [row["product_id"] for row in matching_products("bottled_water", products)] == ["water"]


def test_marketplace_sellers_and_duplicate_products_do_not_count_as_pickup() -> None:
    products = [
        _product("Energizer AA Batteries, 8 Pack", product_id="aa"),
        _product("Energizer AA Batteries, 8 Pack", product_id="aa"),
        _product("Duracell AA Batteries, 12 Pack", product_id="market", seller="Third Party"),
    ]
    assert [row["product_id"] for row in matching_products("aa_batteries", products)] == ["aa"]


def test_product_rows_keep_individual_product_details() -> None:
    product = _product("Battery Camping Lantern", product_id="lantern", price=18.97)
    product["product_price_per_unit"] = "$18.97/ea"
    rows = product_rows("lantern", [product])
    assert rows == [{
        "product_id": "lantern",
        "name": "Battery Camping Lantern",
        "price": 18.97,
        "unit_price": "$18.97/ea",
        "in_stock": 1,
        "availability": "In stock",
        "url": "https://www.walmart.com/ip/lantern",
        "image": "https://images.example/lantern.png",
    }]


def test_status_uses_only_matching_available_products() -> None:
    products = [
        _product("Manual Can Opener", product_id="one"),
        _product("Electric Can Opener", product_id="wrong"),
    ]
    assert status_from_products("can_opener", products) == ("low", 10.0)
    assert status_from_products("sandbags", products) == ("unknown", None)


def test_home_depot_listings_match_without_a_stock_field() -> None:
    from agent.stock import _hd_product

    products = [
        _hd_product({"name": "Dyna-Flo 3500-Watt Gasoline Portable Generator", "product_id": "gen", "price": 399, "url": "https://www.homedepot.com/p/gen", "store_location": "Hilo"}),
        _hd_product({"name": "Generator Cover, Waterproof", "product_id": "cover", "price": 29}),
        _hd_product({"name": "Canopy Weight Bags", "product_id": "weights", "price": 19}),
    ]
    gens = matching_products("generator", products, pickup_only=False)
    assert [row["product_id"] for row in gens] == ["gen"]
    rows = product_rows("generator", products, pickup_only=False)
    assert rows[0]["in_stock"] == 0
    assert rows[0]["price"] == 399
    assert matching_products("sandbags", products, pickup_only=False) == []


def test_gas_can_rejects_chafing_fuel() -> None:
    products = [
        _product("Scepter Ameri-Can FG4G113 1 Gallon Gas Can", product_id="can"),
        _product("Sterno Canned Heat Wick Cans - 6-Hour Burn Time, Chafing Dish Fuel Can", product_id="sterno"),
    ]
    assert [row["product_id"] for row in matching_products("gas_can", products)] == ["can"]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS {name}")
