from dataclasses import dataclass


class UnknownProduct(LookupError):
    pass


@dataclass(frozen=True)
class Product:
    sku: str
    name: str
    price_cents: int
    weight_g: int


class Catalog:
    def __init__(self, products):
        self._by_sku = {p.sku: p for p in products}

    def get(self, sku):
        try:
            return self._by_sku[sku]
        except KeyError:
            raise UnknownProduct(sku) from None
