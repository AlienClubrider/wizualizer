package shop;

import java.util.ArrayList;
import java.util.List;

public class Cart {
  public static class LineItem {
    final Product product;
    int quantity;

    LineItem(Product product, int quantity) {
      this.product = product;
      this.quantity = quantity;
    }

    long totalCents() {
      return product.priceCents() * quantity;
    }
  }

  private final List<LineItem> items = new ArrayList<>();

  public LineItem add(Product product, int quantity) {
    if (quantity <= 0) {
      throw new IllegalArgumentException("quantity must be positive, got " + quantity);
    }
    for (LineItem item : items) {
      if (item.product.sku().equals(product.sku())) {
        item.quantity += quantity;
        return item;
      }
    }
    LineItem item = new LineItem(product, quantity);
    items.add(item);
    return item;
  }

  public long subtotalCents() {
    return items.stream().mapToLong(item -> item.totalCents()).sum();
  }

  public int weightG() {
    int total = 0;
    for (LineItem item : items) {
      total += item.product.weightG() * item.quantity;
    }
    return total;
  }
}
