package com.harbor.payments;

import com.harbor.ledger.FeeSchedule;
import java.math.BigDecimal;
import java.math.RoundingMode;
import org.springframework.stereotype.Service;

@Service
public class QuoteService {
  private final FxRateClient rates;
  private final FeeSchedule fees = FeeSchedule.standard();

  public QuoteService(FxRateClient rates) {
    this.rates = rates;
  }

  public Quote quote(QuoteRequest request) {
    if (request.amount().signum() <= 0) {
      throw new IllegalArgumentException("amount must be positive");
    }
    boolean domestic = request.from().equals(request.to());
    BigDecimal fee = fees.feeFor(request.amount(), domestic);
    BigDecimal rate = domestic ? BigDecimal.ONE : rates.rate(request.from(), request.to());
    BigDecimal receives = request.amount().subtract(fee).multiply(rate).setScale(2, RoundingMode.HALF_UP);
    return new Quote(request.amount(), fee, rate, receives, request.to());
  }
}
