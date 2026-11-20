package com.harbor.payments;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Positive;
import java.math.BigDecimal;

public record TransferRequest(@NotBlank String account, @Positive BigDecimal amount) {}
