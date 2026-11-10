package com.harbor.payments;

import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class QuoteController {
  private final QuoteService quotes;

  public QuoteController(QuoteService quotes) {
    this.quotes = quotes;
  }

  @PostMapping("/quotes")
  public Quote create(@RequestBody QuoteRequest request) {
    return quotes.quote(request);
  }

  @ExceptionHandler(IllegalArgumentException.class)
  @ResponseStatus(HttpStatus.BAD_REQUEST)
  public String badRequest(IllegalArgumentException e) {
    return e.getMessage();
  }
}
