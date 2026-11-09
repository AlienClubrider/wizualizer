from dataclasses import dataclass

from .cart import Cart
from .pricing import coupon_discount_cents, shipping_cents


@dataclass(frozen=True)
class Receipt:
    subtotal_cents: int
    discount_cents: int
    shipping_cents: int
    total_cents: int


class OrderService:
    def __init__(self, catalog, coupons, today):
        self.catalog = catalog
        self.coupons = {c.code: c for c in coupons}
        self.today = today

    def place_order(self, request):
        cart = Cart()
        for line in request["lines"]:
            product = self.catalog.get(line["sku"])
            cart.add(product, line["qty"])
        subtotal = cart.subtotal_cents()
        coupon = self.coupons.get(request.get("coupon"))
        discount = coupon_discount_cents(subtotal, coupon, self.today)
        shipping = shipping_cents(subtotal - discount, cart.weight_g())
        return Receipt(subtotal, discount, shipping, subtotal - discount + shipping)
