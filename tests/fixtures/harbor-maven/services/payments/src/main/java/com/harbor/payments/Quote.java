package com.harbor.payments;

import java.math.BigDecimal;

public record Quote(BigDecimal amount, BigDecimal fee, BigDecimal rate, BigDecimal receives, String currency) {}
