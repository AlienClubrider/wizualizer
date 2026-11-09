from dataclasses import dataclass
from datetime import date

from wizualizer.python.safe_repr import MAX_TOTAL, safe_repr


class Boom:
    def __repr__(self):
        raise RuntimeError("nope")


class Plain:
    def __init__(self):
        self.a = 1
        self.b = [1, 2]


@dataclass
class Item:
    sku: str
    when: date


def test_broken_repr_never_raises():
    assert safe_repr(Boom()) == "<Boom: repr failed (RuntimeError)>"


def test_plain_objects_show_their_attributes():
    assert safe_repr(Plain()) == "Plain(a=1, b=[1, 2])"


def test_dataclasses_and_dates():
    assert safe_repr(Item("MUG", date(2026, 1, 2))) == "Item(sku='MUG', when=datetime.date(2026, 1, 2))"


def test_size_and_depth_are_bounded():
    assert len(safe_repr(list(range(10_000)))) < 80
    assert safe_repr([[[[["deep"]]]]]) == "[[[<list>]]]"
    assert len(safe_repr({str(i) * 50: "x" * 500 for i in range(100)})) <= MAX_TOTAL


def test_self_referencing_containers():
    a = []
    a.append(a)
    assert safe_repr(a).startswith("[[[")
