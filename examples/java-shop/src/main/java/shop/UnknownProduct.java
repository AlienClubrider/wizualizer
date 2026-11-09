package shop;

public class UnknownProduct extends RuntimeException {
  public UnknownProduct(String sku) {
    super("no product " + sku);
  }
}
