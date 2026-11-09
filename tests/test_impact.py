from wizualizer.diff import FileChange
from wizualizer.impact import summarize, watch_lines
from wizualizer.ports import Statement

# shop/pricing.py: a function (def on 1) with a two-line call on 3-4 and a return on 5.
PRICING = [
    Statement(1, (1, 5), False),  # def: runs at import time
    Statement(2, (2, 2), True),
    Statement(3, (3, 4), True),
    Statement(5, (5, 5), True),
]
TEST = [Statement(1, (1, 2), False), Statement(2, (2, 2), True)]


def test_watch_lines_covers_the_statements_that_own_changed_lines():
    changes = {"shop/pricing.py": FileChange(added={4}), "README.md": FileChange(added={1})}
    # Line 4 is the second line of the call on 3: watch where the call starts. The def owns 1-5 too.
    assert watch_lines(changes, {"shop/pricing.py": PRICING}) == {"shop/pricing.py": [1, 3]}


def test_summarize_counts_changed_code_and_test_lines_per_test():
    changes = {
        "shop/pricing.py": FileChange(added={2, 4, 5}),
        "tests/test_pricing.py": FileChange(added={2}),
    }
    statements = {"shop/pricing.py": PRICING, "tests/test_pricing.py": TEST}
    hits = {
        "tests/test_pricing.py::test_a": {"lines": {"shop/pricing.py": [3], "tests/test_pricing.py": [2]}},
        "tests/test_pricing.py::test_b": {"lines": {"shop/pricing.py": [2, 5]}},
        "tests/test_pricing.py::test_c": {"lines": {"other.py": [1]}},  # nothing changed there
    }
    out = summarize(hits, changes, statements)
    assert out["tests/test_pricing.py::test_a"] == {
        "lines": 1, "test_lines": 1,
        "files": {"shop/pricing.py": [4], "tests/test_pricing.py": [2]},
    }
    assert out["tests/test_pricing.py::test_b"]["lines"] == 2
    assert "tests/test_pricing.py::test_c" not in out


def test_a_called_function_counts_its_changed_signature():
    changes = {"shop/pricing.py": FileChange(added={1})}
    hits = {"t": {"starts": {"shop/pricing.py": [1]}}}
    assert summarize(hits, changes, {"shop/pricing.py": PRICING})["t"]["lines"] == 1
