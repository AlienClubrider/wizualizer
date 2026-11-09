from datetime import date

import pytest

from shop.catalog import Catalog, Product
from shop.orders import OrderService
from shop.pricing import Coupon


@pytest.fixture
def service():
    catalog = Catalog([
        Product("MUG", "Enamel mug", 1_800, 350),
        Product("BEANS", "Coffee beans 1kg", 2_400, 1_000),
        Product("GRINDER", "Hand grinder", 6_500, 900),
    ])
    coupons = [Coupon("WELCOME10", 10, expires=date(2026, 12, 31), min_subtotal_cents=3_000)]
    return OrderService(catalog, coupons, today=date(2026, 11, 9))


def test_order_over_50_ships_free_with_coupon(service):
    receipt = service.place_order({
        "lines": [{"sku": "MUG", "qty": 2}, {"sku": "BEANS", "qty": 1}, {"sku": "MUG", "qty": 1}],
        "coupon": "WELCOME10",
    })

    assert receipt.subtotal_cents == 7_800
    assert receipt.discount_cents == 780
    assert receipt.shipping_cents == 0
    assert receipt.total_cents == 7_020


def test_small_order_pays_shipping(service):
    receipt = service.place_order({"lines": [{"sku": "MUG", "qty": 1}]})

    assert receipt.shipping_cents == 499
    assert receipt.total_cents == 2_299
