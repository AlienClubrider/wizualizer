package com.harbor.payments;

import java.math.BigDecimal;

/** Exchange rates from the rates service (an external HTTP API; mocked in tests). */
public interface FxRateClient {
  BigDecimal rate(String from, String to);
}
