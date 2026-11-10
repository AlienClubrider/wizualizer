from wizualizer.discovery import Flow
from wizualizer.functions import (EntryPoint, catalog, choose, humanize, object_fields, recording_summary,
                                  run_labels)


def flow(name, cls="com.acme.QuoteTest"):
    return Flow(f"{cls}#{name}", "java", "src/test/java/com/acme/QuoteTest.java", cls, name)


QUOTE = EntryPoint("src/main/java/com/acme/QuoteService.java", "QuoteService.quote", "quote(QuoteRequest request)", 12)


def with_inputs(ep, **kw):
    return EntryPoint(ep.file, ep.qualname, ep.signature, ep.line, tuple(kw.items()), ep.via)


def test_runs_are_labelled_by_the_fields_that_differ():
    labels = run_labels([
        (("request", 'QuoteRequest(amount=1000.00, from="USD", to="EUR")'),),
        (("request", 'QuoteRequest(amount=20.00, from="USD", to="USD")'),),
    ])
    assert labels == ['amount=1000.00, to="EUR"', 'amount=20.00, to="USD"']
    assert run_labels([(("amount", "20.00"), ("domestic", "true")), (("amount", "20.00"), ("domestic", "false"))]) == [
        "domestic=true", "domestic=false"]


def test_one_run_shows_all_its_inputs_and_twins_are_told_apart():
    assert run_labels([(("a", "1"), ("b", "2"))]) == ["a=1, b=2"]
    assert run_labels([()]) == ["no inputs"]
    assert run_labels([(), ()]) == ["run 1", "run 2"]
    same = (("amount", "100"),)
    assert run_labels([same, same]) == ["amount=100 · run 1", "amount=100 · run 2"]
    # once recorded, what they returned tells them apart
    assert run_labels([same, same], ["Response(code=OK)", "raises NotFound"]) == [
        "amount=100 → Response(code=OK)", "amount=100 → raises NotFound"]


def test_object_fields_reads_the_usual_shapes():
    assert object_fields('Quote(amount=1, to="EUR")') == [("amount", "1"), ("to", '"EUR"')]
    assert object_fields("{'lines': [1, 2], 'coupon': None}") == [("lines", "[1, 2]"), ("coupon", "None")]
    assert object_fields('{"amount": 1000.00}') == [("amount", "1000.00")]
    assert object_fields("[1, 2]") is None and object_fields('"text"') is None and object_fields("f(1, 2)") is None
    assert object_fields('Request("123", 100)') == [("0", '"123"'), ("1", "100")]
    assert run_labels([(("r", 'Request("999", 100)'),), (("r", 'Request("123", 500)'),), (("r", 'Request("123", 100)'),)]) \
        == ['"999", 100', '"123", 500', '"123", 100']


def test_catalog_groups_runs_by_function_and_keeps_unplaced_tests_apart():
    flows = [flow("bigTransfer"), flow("smallTransfer"), flow("contextLoads", "com.acme.AppTest")]
    static = {flows[0].id: with_inputs(QUOTE, amount="1000"), flows[1].id: with_inputs(QUOTE, amount="20"),
              flows[2].id: None}
    fns = catalog(flows, static, {})
    assert [f.qualname for f in fns] == ["QuoteService.quote", ""]
    quote, other = fns
    assert [(r.label, r.test, r.recorded) for r in quote.runs] == [
        ("amount=1000", "bigTransfer", False), ("amount=20", "smallTransfer", False)]
    assert not other.known and [r.label for r in other.runs] == ["context loads"]


def test_recording_confirms_the_static_guess_with_recorded_inputs():
    recorded = {"entry": None, "calls": {QUOTE.key: with_inputs(QUOTE, request="QuoteRequest(amount=5)").to_json()}}
    got, confirmed = choose(with_inputs(QUOTE, request="new QuoteRequest(5)"), recorded)
    assert confirmed and got.key == QUOTE.key and got.inputs == (("request", "QuoteRequest(amount=5)"),)


def test_recording_corrects_a_wrong_or_missing_guess():
    filter_ep = EntryPoint("src/main/java/com/acme/AuthFilter.java", "AuthFilter.doFilter", line=3)
    recorded = {"entry": filter_ep.to_json(), "calls": {filter_ep.key: filter_ep.to_json()}}
    assert choose(QUOTE, recorded) == (filter_ep, True)  # the guess never ran
    assert choose(None, recorded) == (filter_ep, True)
    assert choose(QUOTE, None) == (QUOTE, False)


def test_recording_summary_finds_the_entry_and_first_inputs_and_results():
    trace = {
        "functions": {
            "t.py:1": {"file": "tests/t.py", "line": 1, "qualname": "test_x", "test": True},
            "s.py:5": {"file": "shop/s.py", "line": 5, "qualname": "Shop.buy", "test": False,
                       "flowchart": {"nodes": [{"label": "buy(sku, qty)"}]}},
            "s.py:9": {"file": "shop/s.py", "line": 9, "qualname": "price", "test": False},
        },
        "frames": {"1": {"fn": "t.py:1", "parent": None}, "2": {"fn": "s.py:5", "parent": 1},
                   "3": {"fn": "s.py:9", "parent": 2}, "4": {"fn": "s.py:5", "parent": 1}},
        "steps": [
            {"kind": "call", "frame": 1, "args": {}},
            {"kind": "call", "frame": 2, "args": {"self": "Shop()", "sku": "'MUG'", "qty": "2"}},
            {"kind": "call", "frame": 3, "args": {"sku": "'MUG'"}},
            {"kind": "return", "frame": 3, "value": "1800"},
            {"kind": "return", "frame": 2, "value": "3600"},
            {"kind": "call", "frame": 4, "args": {"self": "Shop()", "sku": "'X'", "qty": "1"}},
            {"kind": "return", "frame": 4, "raised": True, "type": "UnknownProduct"},
        ],
    }
    s = recording_summary(trace)
    assert s["entry"]["key"] == "shop/s.py::Shop.buy"
    assert s["entry"]["inputs"] == [["sku", "'MUG'"], ["qty", "2"]] and s["entry"]["result"] == "3600"
    assert s["entry"]["signature"] == "buy(sku, qty)"
    assert set(s["calls"]) == {"shop/s.py::Shop.buy", "shop/s.py::price"}
    assert s["calls"]["shop/s.py::price"]["result"] == "1800"


def test_humanize():
    assert humanize("test_small_order_pays_shipping") == "small order pays shipping"
    assert humanize("smallOrderPaysShipping") == "small order pays shipping"
    assert humanize("testCredit_ReturnsOk") == "credit returns ok"
    assert humanize("test_x[3-USD]") == "x [3-USD]"
    assert humanize("parsesURLs") == "parses URLs"


def test_display_name_is_class_method_or_module_function():
    def fn(file, qualname, language):
        return catalog([Flow("t", language, "t", "", "t")], {"t": EntryPoint(file, qualname)}, {})[0].display
    assert fn("shop/pricing.py", "shipping_cents", "python") == "pricing.shipping_cents"
    assert fn("shop/orders.py", "OrderService.place_order", "python") == "OrderService.place_order"
    assert fn("src/main/java/a/Fees.java", "Fees.feeFor", "java") == "Fees.feeFor"
