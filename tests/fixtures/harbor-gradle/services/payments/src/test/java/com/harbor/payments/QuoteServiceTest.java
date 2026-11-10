package com.harbor.payments;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import java.math.BigDecimal;
import org.junit.jupiter.api.Test;

class QuoteServiceTest {
  @Test
  void convertsWhatIsLeftAfterTheFee() {
    FxRateClient rates = mock(FxRateClient.class);
    when(rates.rate("GBP", "EUR")).thenReturn(new BigDecimal("1.17"));
    Quote q = new QuoteService(rates).quote(new QuoteRequest(new BigDecimal("200.00"), "GBP", "EUR"));
    assertEquals(new BigDecimal("230.49"), q.receives());
  }
}
