package com.harbor.payments;

import jakarta.validation.Valid;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class TransferController {
  private final TransferService transfers;

  public TransferController(TransferService transfers) {
    this.transfers = transfers;
  }

  @PostMapping("/transfers")
  public TransferReceipt create(@Valid @RequestBody TransferRequest request) {
    return transfers.transfer(request);
  }

  @ExceptionHandler(IllegalStateException.class)
  @ResponseStatus(HttpStatus.CONFLICT)
  public String blocked(IllegalStateException e) {
    return e.getMessage();
  }
}
