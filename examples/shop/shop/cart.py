from dataclasses import dataclass, field


@dataclass
class LineItem:
    product: object
    quantity: int

    def total_cents(self):
        return self.product.price_cents * self.quantity


@dataclass
class Cart:
    items: list = field(default_factory=list)

    def add(self, product, quantity):
        if quantity <= 0:
            raise ValueError(f"quantity must be positive, got {quantity}")
        for item in self.items:
            if item.product.sku == product.sku:
                item.quantity += quantity
                return item
        item = LineItem(product, quantity)
        self.items.append(item)
        return item

    def subtotal_cents(self):
        return sum(item.total_cents() for item in self.items)

    def weight_g(self):
        return sum(item.product.weight_g * item.quantity for item in self.items)
