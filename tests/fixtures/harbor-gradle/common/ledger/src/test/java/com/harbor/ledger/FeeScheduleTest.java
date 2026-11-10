package com.harbor.ledger;

import static org.junit.jupiter.api.Assertions.assertEquals;

import java.math.BigDecimal;
import org.junit.jupiter.api.Test;

class FeeScheduleTest {
  private final FeeSchedule fees = FeeSchedule.standard();

  @Test
  void smallDomesticTransfersAreFree() {
    assertEquals(new BigDecimal("0.00"), fees.feeFor(new BigDecimal("20.00"), true));
  }

  @Test
  void internationalTransfersPayAtLeastTheMinimum() {
    assertEquals(new BigDecimal("2.00"), fees.feeFor(new BigDecimal("20.00"), false));
  }

  @Test
  void largeTransfersPayThePercentage() {
    assertEquals(new BigDecimal("15.00"), fees.feeFor(new BigDecimal("1000.00"), false));
  }
}
