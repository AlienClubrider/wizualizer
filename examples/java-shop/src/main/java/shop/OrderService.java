package shop;

import java.time.LocalDate;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

public class OrderService {
  private final Catalog catalog;
  private final Map<String, Coupon> coupons = new HashMap<>();
  private final LocalDate today;

  public OrderService(Catalog catalog, List<Coupon> coupons, LocalDate today) {
    this.catalog = catalog;
    for (Coupon c : coupons) {
      this.coupons.put(c.code(), c);
    }
    this.today = today;
  }

  public Receipt placeOrder(OrderRequest request) {
    Cart cart = new Cart();
    for (OrderRequest.Line line : request.lines()) {
      Product product = catalog.get(line.sku());
      cart.add(product, line.qty());
    }
    long subtotal = cart.subtotalCents();
    Coupon coupon = request.coupon() == null ? null : coupons.get(request.coupon());
    long discount = Pricing.couponDiscountCents(subtotal, coupon, today);
    long shipping = Pricing.shippingCents(subtotal - discount, cart.weightG());
    return new Receipt(subtotal, discount, shipping, subtotal - discount + shipping);
  }
}
