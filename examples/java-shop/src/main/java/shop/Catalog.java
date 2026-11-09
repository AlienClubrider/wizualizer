package shop;

import java.util.HashMap;
import java.util.List;
import java.util.Map;

public class Catalog {
  private final Map<String, Product> bySku = new HashMap<>();

  public Catalog(List<Product> products) {
    for (Product p : products) {
      bySku.put(p.sku(), p);
    }
  }

  public Product get(String sku) {
    Product p = bySku.get(sku);
    if (p == null) {
      throw new UnknownProduct(sku);
    }
    return p;
  }
}
