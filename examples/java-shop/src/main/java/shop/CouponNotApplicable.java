package shop;

public class CouponNotApplicable extends RuntimeException {
  public CouponNotApplicable(String message) {
    super(message);
  }
}
