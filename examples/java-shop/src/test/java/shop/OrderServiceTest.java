package shop;

import static org.junit.jupiter.api.Assertions.assertEquals;

import java.time.LocalDate;
import java.util.List;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

class OrderServiceTest {
  private OrderService service;

  @BeforeEach
  void setUp() {
    Catalog catalog =
        new Catalog(
            List.of(
                new Product("MUG", "Enamel mug", 1_800, 350),
                new Product("BEANS", "Coffee beans 1kg", 2_400, 1_000),
                new Product("GRINDER", "Hand grinder", 6_500, 900)));
    List<Coupon> coupons = List.of(new Coupon("WELCOME10", 10, LocalDate.of(2026, 12, 31), 3_000));
    service = new OrderService(catalog, coupons, LocalDate.of(2026, 11, 9));
  }

  @Test
  void orderOver50ShipsFreeWithCoupon() {
    Receipt receipt =
        service.placeOrder(
            new OrderRequest(
                List.of(
                    new OrderRequest.Line("MUG", 2),
                    new OrderRequest.Line("BEANS", 1),
                    new OrderRequest.Line("MUG", 1)),
                "WELCOME10"));

    assertEquals(7_800, receipt.subtotalCents());
    assertEquals(780, receipt.discountCents());
    assertEquals(0, receipt.shippingCents());
    assertEquals(7_020, receipt.totalCents());
  }

  @Test
  void smallOrderPaysShipping() {
    Receipt receipt = service.placeOrder(new OrderRequest(List.of(new OrderRequest.Line("MUG", 1)), null));

    assertEquals(499, receipt.shippingCents());
    assertEquals(2_299, receipt.totalCents());
  }
}
