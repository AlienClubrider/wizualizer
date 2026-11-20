package com.harbor.payments;

import java.math.BigDecimal;

public record TransferReceipt(long id, String account, BigDecimal amount, int fraudScore) {}
