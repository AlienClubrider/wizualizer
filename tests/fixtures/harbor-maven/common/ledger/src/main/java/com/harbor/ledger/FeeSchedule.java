package com.harbor.ledger;

import java.math.BigDecimal;
import java.math.RoundingMode;

/** Transfer fees: a percentage with a floor, waived for small domestic transfers. */
public final class FeeSchedule {
  private final BigDecimal percent;
  private final BigDecimal minimum;
  private final BigDecimal waivedBelow;

  public FeeSchedule(BigDecimal percent, BigDecimal minimum, BigDecimal waivedBelow) {
    this.percent = percent;
    this.minimum = minimum;
    this.waivedBelow = waivedBelow;
  }

  public static FeeSchedule standard() {
    return new FeeSchedule(new BigDecimal("0.015"), new BigDecimal("2.00"), new BigDecimal("50.00"));
  }

  public BigDecimal feeFor(BigDecimal amount, boolean domestic) {
    if (domestic && amount.compareTo(waivedBelow) < 0) {
      return BigDecimal.ZERO.setScale(2);
    }
    BigDecimal fee = amount.multiply(percent).setScale(2, RoundingMode.HALF_UP);
    if (fee.compareTo(minimum) < 0) {
      fee = minimum;
    }
    return fee;
  }
}
