package shop;

import java.time.LocalDate;

public record Coupon(String code, int percentOff, LocalDate expires, long minSubtotalCents) {}
