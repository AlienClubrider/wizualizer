package shop;

public record Receipt(long subtotalCents, long discountCents, long shippingCents, long totalCents) {}
