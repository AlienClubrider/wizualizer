package com.harbor.payments;

import java.math.BigDecimal;

public record QuoteRequest(BigDecimal amount, String from, String to) {}
