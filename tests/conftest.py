"""Shared fixtures: a small Python project whose test mocks and stubs project code."""

import sys
import textwrap

import pytest

from wizualizer.ports import RecordRequest
from wizualizer.python.recorder import PythonRecorder
from wizualizer.python.source import PythonSource
from wizualizer.trace import make_trace


GATEWAY = textwrap.dedent("""\
    from abc import ABC, abstractmethod


    class PaymentGateway(ABC):
        @abstractmethod
        def charge(self, account, cents):
            ...


    class CardGateway(PaymentGateway):
        def charge(self, account, cents):
            if cents <= 0:
                raise ValueError("nothing to charge")
            return f"card:{account}:{cents}"


    def send_receipt(email, text):
        return True
""")
CHECKOUT = textwrap.dedent("""\
    from . import gateway


    class Checkout:
        def __init__(self, payments):
            self.payments = payments

        def pay(self, account, items):
            total = self.total(items)
            ref = self.payments.charge(account, total)
            gateway.send_receipt(account + "@x", ref)
            return ref.upper()

        def total(self, items):
            return sum(items)
""")
CHECKOUT_TEST = textwrap.dedent("""\
    from unittest.mock import Mock

    from app import gateway
    from app.checkout import Checkout
    from app.gateway import PaymentGateway


    def test_pay(monkeypatch):
        payments = Mock(spec=PaymentGateway)
        payments.charge.return_value = "ok-1"
        monkeypatch.setattr(gateway, "send_receipt", lambda email, text: False)
        assert Checkout(payments).pay("ann", [1, 2]) == "OK-1"
""")


@pytest.fixture(scope="session")
def mocked_trace(tmp_path_factory):
    root = tmp_path_factory.mktemp("mocked")
    (root / "app").mkdir()
    (root / "tests").mkdir()
    for rel, text in {"app/__init__.py": "", "app/gateway.py": GATEWAY, "app/checkout.py": CHECKOUT,
                      "tests/__init__.py": "", "tests/test_checkout.py": CHECKOUT_TEST}.items():
        (root / rel).write_text(text)
    request = RecordRequest("tests/test_checkout.py::test_pay", root, root, interpreter=sys.executable)
    return make_trace(PythonRecorder(), PythonSource(), request)
