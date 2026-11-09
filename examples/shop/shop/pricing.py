from dataclasses import dataclass
from datetime import date

FREE_SHIPPING_FROM_CENTS = 5_000
BASE_SHIPPING_CENTS = 499
HEAVY_PARCEL_G = 20_000
HEAVY_SURCHARGE_CENTS = 1_500


class CouponNotApplicable(ValueError):
    pass


@dataclass(frozen=True)
class Coupon:
    code: str
    percent_off: int
    expires: date
    min_subtotal_cents: int = 0


def coupon_discount_cents(subtotal_cents, coupon, today):
    if coupon is None:
        return 0
    if today > coupon.expires:
        raise CouponNotApplicable(f"{coupon.code} expired on {coupon.expires}")
    if subtotal_cents < coupon.min_subtotal_cents:
        raise CouponNotApplicable(f"{coupon.code} needs a subtotal of {coupon.min_subtotal_cents}")
    return subtotal_cents * coupon.percent_off // 100


def shipping_cents(subtotal_cents, weight_g):
    if subtotal_cents >= FREE_SHIPPING_FROM_CENTS:
        return 0
    cost = BASE_SHIPPING_CENTS
    if weight_g > HEAVY_PARCEL_G:
        cost += HEAVY_SURCHARGE_CENTS
    return cost
