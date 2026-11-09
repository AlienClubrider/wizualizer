package shop;

import java.util.List;

public record OrderRequest(List<Line> lines, String coupon) {
  public record Line(String sku, int qty) {}
}
