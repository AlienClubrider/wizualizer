"""Static function-under-test detection: which production function each test calls, and with what."""

import textwrap
from pathlib import Path

from wizualizer.discovery import Flow, java_flows, parse_pytest_collect
from wizualizer.java.entry import entry_points as java_entries
from wizualizer.python.entry import entry_points as python_entries

FIXTURES = Path(__file__).parent / "fixtures"
EXAMPLES = Path(__file__).parent.parent / "examples"


def write(root, files):
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(text))


def by_name(root, entries, flows):
    return {f.name: entries(root, flows)[f.id] for f in flows}


# ---- Java -----------------------------------------------------------------------------------

def test_java_mockmvc_request_maps_to_the_controller_method():
    root = FIXTURES / "harbor-gradle"
    got = by_name(root, java_entries, java_flows(root))
    big = got["quotesAnInternationalTransfer"]
    assert (big.qualname, big.signature, big.via) == ("QuoteController.create", "create(QuoteRequest request)",
                                                      "POST /quotes")
    assert big.file == "services/payments/src/main/java/com/harbor/payments/QuoteController.java"
    assert big.inputs == (("request", '{"amount": 1000.00, "from": "USD", "to": "EUR"}'),)


def test_java_plain_calls_on_fields_and_new_objects():
    root = FIXTURES / "harbor-gradle"
    got = by_name(root, java_entries, java_flows(root))
    fee = got["largeTransfersPayThePercentage"]
    assert (fee.qualname, fee.inputs) == ("FeeSchedule.feeFor", (("amount", "1000.00"), ("domestic", "false")))
    svc = got["convertsWhatIsLeftAfterTheFee"]  # new QuoteService(rates).quote(...)
    assert (svc.qualname, svc.inputs) == ("QuoteService.quote", (("request", 'QuoteRequest(200.00, "GBP", "EUR")'),))


def test_java_field_set_in_before_each():
    got = by_name(EXAMPLES / "java-shop", java_entries, java_flows(EXAMPLES / "java-shop"))
    small = got["smallOrderPaysShipping"]
    assert small.qualname == "OrderService.placeOrder"
    assert small.inputs == (("request", 'OrderRequest([OrderRequest.Line("MUG", 1)], null)'),)


SPRING = {
    "src/main/java/com/acme/bank/TransferService.java": """\
        package com.acme.bank;
        public interface TransferService {
          Receipt transfer(TransferRequest request);
        }
        """,
    "src/main/java/com/acme/bank/TransferServiceImpl.java": """\
        package com.acme.bank;
        import org.springframework.stereotype.Service;
        @Service
        public class TransferServiceImpl implements TransferService {
          private final Ledger ledger;
          @Override
          public Receipt transfer(TransferRequest request) {
            return ledger.move(request); // a { brace } in a comment
          }
        }
        """,
    "src/main/java/com/acme/bank/web/AccountController.java": """\
        package com.acme.bank.web;
        @RestController
        @RequestMapping(value = "/api/accounts")
        class AccountController {
          @GetMapping("/{id}/balance")
          Balance balance(@PathVariable String id, @RequestParam(defaultValue = "EUR") String currency) {
            return null;
          }
          @RequestMapping(path = "/{id}/close", method = RequestMethod.POST)
          void close(@PathVariable("id") String id) {}
        }
        """,
    "src/test/java/com/acme/bank/TransferServiceImplTest.java": """\
        package com.acme.bank;
        import static org.mockito.Mockito.when;
        @ExtendWith(MockitoExtension.class)
        class TransferServiceImplTest {
          @Mock private Ledger ledger;
          @InjectMocks private TransferServiceImpl service;
          @Test
          void movesMoney() {
            when(ledger.move(any())).thenReturn(null);
            TransferRequest request = TransferRequest.builder().from("A").to("B").amount(new BigDecimal("5.00")).build();
            Receipt r = service.transfer(request);
          }
          @Nested
          class WhenFrozen {
            @Test
            void refuses() {
              service.transfer(TransferRequest.builder().from("F").to("B").amount(BigDecimal.ONE).build());
            }
          }
        }
        """,
    "src/test/java/com/acme/bank/TransferViaInterfaceTest.java": """\
        package com.acme.bank;
        @SpringBootTest
        class TransferViaInterfaceTest {
          @Autowired TransferService transfers;
          @MockitoBean Ledger ledger;
          @Test void movesMoney() { transfers.transfer(new TransferRequest("A", "B", 7)); }
        }
        """,
    "src/test/java/com/acme/bank/web/AccountControllerTest.java": """\
        package com.acme.bank.web;
        @WebMvcTest(AccountController.class)
        class AccountControllerTest {
          @Autowired MockMvc mvc;
          @Test void balanceInDollars() throws Exception {
            mvc.perform(get("/api/accounts/{id}/balance", "acc-1").param("currency", "USD")).andExpect(status().isOk());
          }
          @Test void closes() throws Exception {
            mvc.perform(MockMvcRequestBuilders.post("/api/accounts/acc-9/close")).andExpect(status().isOk());
          }
          @Test void webTestClient() {
            client.get().uri("/api/accounts/acc-2/balance").exchange();
          }
          @Test void contextLoads() {}
        }
        """,
}


def test_java_spring_shapes(tmp_path):
    write(tmp_path, SPRING)
    flows = java_flows(tmp_path)
    got = {f.id.split(".")[-1]: java_entries(tmp_path, flows)[f.id] for f in flows}
    moves = got["TransferServiceImplTest#movesMoney"]
    assert (moves.qualname, moves.inputs) == (
        "TransferServiceImpl.transfer", (("request", 'TransferRequest(from="A", to="B", amount=5.00)'),))
    assert got["TransferServiceImplTest$WhenFrozen#refuses"].qualname == "TransferServiceImpl.transfer"
    # an interface with one implementation: the implementation, as the recording will name it
    assert got["TransferViaInterfaceTest#movesMoney"].file == "src/main/java/com/acme/bank/TransferServiceImpl.java"
    bal = got["AccountControllerTest#balanceInDollars"]
    assert (bal.qualname, bal.via) == ("AccountController.balance", "GET /api/accounts/{id}/balance")
    assert bal.inputs == (("id", '"acc-1"'), ("currency", '"USD"'))
    close = got["AccountControllerTest#closes"]
    assert (close.qualname, close.inputs) == ("AccountController.close", (("id", "acc-9"),))
    assert got["AccountControllerTest#webTestClient"].qualname == "AccountController.balance"
    assert got["AccountControllerTest#contextLoads"] is None


# ---- Python ---------------------------------------------------------------------------------

def test_python_fixture_returning_a_project_object():
    root = EXAMPLES / "shop"
    flows = parse_pytest_collect("tests/test_orders.py::test_order_over_50_ships_free_with_coupon\n"
                                 "tests/test_orders.py::test_small_order_pays_shipping\n")
    got = by_name(root, python_entries, flows)
    small = got["test_small_order_pays_shipping"]
    assert (small.file, small.qualname, small.signature) == ("shop/orders.py", "OrderService.place_order",
                                                             "place_order(request)")
    assert small.inputs == (("request", "{'lines': [{'sku': 'MUG', 'qty': 1}]}"),)


def test_python_shapes(tmp_path):
    write(tmp_path, {
        "shop/__init__.py": "",
        "shop/pricing.py": """\
            def shipping_cents(subtotal_cents, weight_g=0):
                return 0

            class Cart:
                def __init__(self, items):
                    self.items = items

                def total(self, coupon=None):
                    return 0
            """,
        "tests/conftest.py": """\
            import pytest
            from shop.pricing import Cart

            @pytest.fixture
            def cart():
                return Cart([1, 2])
            """,
        "tests/test_pricing.py": """\
            import pytest
            from shop import pricing
            from shop.pricing import Cart, shipping_cents
            from .helpers import nothing

            def test_free():
                assert shipping_cents(6_000, weight_g=900) == 0

            def test_module_call():
                big = 50_000
                assert pricing.shipping_cents(big) == 0

            def test_fixture(cart):
                assert cart.total(coupon="TEN") == 0

            def test_local():
                c = Cart([])
                c.total()

            @pytest.mark.parametrize("cents", [100, 9000])
            def test_param(cents):
                shipping_cents(cents)

            class TestCart:
                def test_inline(self):
                    assert Cart([3]).total() == 0

            def test_nothing_known():
                assert len([]) == 0
            """,
    })
    flows = [Flow(f"tests/test_pricing.py::{n}", "python", "tests/test_pricing.py", g, n.split("::")[-1])
             for n, g in [("test_free", ""), ("test_module_call", ""), ("test_fixture", ""), ("test_local", ""),
                          ("test_param[9000]", ""), ("TestCart::test_inline", "TestCart"),
                          ("test_nothing_known", "")]]
    got = by_name(tmp_path, python_entries, flows)
    assert (got["test_free"].qualname, got["test_free"].inputs) == (
        "shipping_cents", (("subtotal_cents", "6000"), ("weight_g", "900")))
    assert got["test_free"].signature == "shipping_cents(subtotal_cents, weight_g)"
    assert got["test_module_call"].inputs == (("subtotal_cents", "50000"),)
    assert (got["test_fixture"].qualname, got["test_fixture"].inputs) == ("Cart.total", (("coupon", "'TEN'"),))
    assert got["test_local"].qualname == "Cart.total"
    assert got["test_param[9000]"].inputs == (("case", "9000"), ("subtotal_cents", "cents"))
    assert got["test_inline"].qualname == "Cart.total"
    assert got["test_nothing_known"] is None
