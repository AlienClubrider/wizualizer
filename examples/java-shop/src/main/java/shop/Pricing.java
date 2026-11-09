package shop;

import java.time.LocalDate;

public final class Pricing {
  static final long FREE_SHIPPING_FROM_CENTS = 5_000;
  static final long BASE_SHIPPING_CENTS = 499;
  static final int HEAVY_PARCEL_G = 20_000;
  static final long HEAVY_SURCHARGE_CENTS = 1_500;

  private Pricing() {}

  public static long couponDiscountCents(long subtotalCents, Coupon coupon, LocalDate today) {
    if (coupon == null) {
      return 0;
    }
    if (today.isAfter(coupon.expires())) {
      throw new CouponNotApplicable(coupon.code() + " expired on " + coupon.expires());
    }
    if (subtotalCents < coupon.minSubtotalCents()) {
      throw new CouponNotApplicable(coupon.code() + " needs a subtotal of " + coupon.minSubtotalCents());
    }
    return subtotalCents * coupon.percentOff() / 100;
  }

  public static long shippingCents(long subtotalCents, int weightG) {
    if (subtotalCents >= FREE_SHIPPING_FROM_CENTS) {
      return 0;
    }
    long cost = BASE_SHIPPING_CENTS;
    if (weightG > HEAVY_PARCEL_G) {
      cost += HEAVY_SURCHARGE_CENTS;
    }
    return cost;
  }
}
