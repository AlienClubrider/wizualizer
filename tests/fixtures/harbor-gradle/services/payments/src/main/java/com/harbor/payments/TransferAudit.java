package com.harbor.payments;

import org.aspectj.lang.annotation.AfterReturning;
import org.aspectj.lang.annotation.Aspect;
import org.springframework.stereotype.Component;

/** Notes every completed transfer (an aspect, so the service does not have to). */
@Aspect
@Component
public class TransferAudit {
  private long lastAudited;

  @AfterReturning(pointcut = "execution(* com.harbor.payments.TransferService.transfer(..))", returning = "receipt")
  public void audited(TransferReceipt receipt) {
    lastAudited = receipt.id();
  }
}
