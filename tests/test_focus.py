from wizualizer.focus import function_under_test, is_test_file, in_test_code


def test_test_files_python_and_java():
    for rel in ("tests/test_orders.py", "pkg/orders_test.py", "conftest.py", "tests/helpers.py",
                "server/src/test/java/a/FakeClock.java", "src/main/java/a/OrderServiceTest.java",
                "src/main/java/a/OrderServiceIT.java"):
        assert is_test_file(rel), rel
    for rel in ("shop/orders.py", "server/src/main/java/a/TransferService.java", "shop/testing_mode.py",
                "src/main/java/a/Contest.java"):
        assert not is_test_file(rel), rel


FUNCTIONS = {
    "tests/test_x.py:1": {"file": "tests/test_x.py"},
    "tests/test_x.py:9": {"file": "tests/test_x.py"},  # a helper in the test file
    "shop/orders.py:5": {"file": "shop/orders.py"},
    "shop/pricing.py:20": {"file": "shop/pricing.py"},
    "shop/pricing.py:40": {"file": "shop/pricing.py"},
    "run_me.py:1": {"file": "run_me.py"},
}
# test → helper → place_order → (pricing 20, pricing 40); then the test calls pricing 40 again
FRAMES = {
    "1": {"fn": "tests/test_x.py:1", "parent": None},
    "2": {"fn": "tests/test_x.py:9", "parent": 1},
    "3": {"fn": "shop/orders.py:5", "parent": 2},
    "4": {"fn": "shop/pricing.py:20", "parent": 3},
    "5": {"fn": "shop/pricing.py:40", "parent": 3},
}
STEPS = [{"kind": "call", "frame": f} for f in (1, 2, 3, 4, 5)]
LINES = {"shop/orders.py:5": range(5, 15), "shop/pricing.py:20": range(20, 30), "shop/pricing.py:40": range(40, 50)}
TEST_FNS = {"tests/test_x.py:1", "tests/test_x.py:9"}


def test_test_functions_are_test_files_and_the_entry_frame():
    frames = {**FRAMES, "1": {"fn": "run_me.py:1", "parent": None}}  # entry outside a test file
    assert in_test_code(FUNCTIONS, FRAMES) == TEST_FNS
    assert in_test_code(FUNCTIONS, frames) == TEST_FNS | {"run_me.py:1"}


def test_focus_is_first_production_call_without_changes():
    assert function_under_test(STEPS, FRAMES, TEST_FNS, LINES, {}) == "shop/orders.py:5"


def test_focus_moves_to_the_first_called_function_with_changed_lines():
    changed = {"shop/pricing.py": {44, 22}}
    assert function_under_test(STEPS, FRAMES, TEST_FNS, LINES, changed) == "shop/pricing.py:20"
    assert function_under_test(STEPS, FRAMES, TEST_FNS, LINES, {"shop/pricing.py": {44}}) == "shop/pricing.py:40"


def test_changed_lines_in_the_test_or_uncalled_code_do_not_steer_focus():
    changed = {"tests/test_x.py": {2}, "shop/pricing.py": {99}}
    assert function_under_test(STEPS, FRAMES, TEST_FNS, LINES, changed) == "shop/orders.py:5"


def test_no_focus_when_the_test_calls_no_production_code():
    assert function_under_test(STEPS[:2], FRAMES, TEST_FNS, LINES, {}) is None
