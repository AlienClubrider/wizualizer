package com.harbor.payments;

import java.math.BigDecimal;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;

@Component
class HttpFxRateClient implements FxRateClient {
  private final RestClient http;

  HttpFxRateClient(@Value("${rates.url:http://rates.internal}") String baseUrl) {
    this.http = RestClient.create(baseUrl);
  }

  @Override
  public BigDecimal rate(String from, String to) {
    return http.get().uri("/rates/{from}/{to}", from, to).retrieve().body(BigDecimal.class);
  }
}
