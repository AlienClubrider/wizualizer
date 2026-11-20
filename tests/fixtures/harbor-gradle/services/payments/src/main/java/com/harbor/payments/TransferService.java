package com.harbor.payments;

import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class TransferService {
  private final FraudCheckClient fraud;
  private final TransferStore store;

  public TransferService(FraudCheckClient fraud, TransferStore store) {
    this.fraud = fraud;
    this.store = store;
  }

  @Transactional
  public TransferReceipt transfer(TransferRequest request) {
    long id = store.save(request.account(), request.amount());
    int score = fraud.score(request.account(), request.amount());
    if (score > 80) {
      throw new IllegalStateException("transfer blocked: fraud score " + score);
    }
    return new TransferReceipt(id, request.account(), request.amount(), score);
  }
}
