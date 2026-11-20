package com.harbor.payments;

import java.math.BigDecimal;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;

/** Risk scores from the fraud service (an external HTTP API; mocked in tests). A class, not an interface. */
@Component
public class FraudCheckClient {
  private final RestClient http;

  public FraudCheckClient(@Value("${fraud.url:http://fraud.internal}") String baseUrl) {
    this.http = RestClient.create(baseUrl);
  }

  public int score(String account, BigDecimal amount) {
    Integer score = http.get().uri("/score/{account}?amount={amount}", account, amount).retrieve().body(Integer.class);
    return score == null ? 100 : score;
  }
}
