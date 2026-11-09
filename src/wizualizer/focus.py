"""Which code is the test, and which function the test is about.

The test only drives the code under test. These pure rules let the trace tag
test code (so the viewer can hide it) and pick the function the viewer opens
on. No I/O.
"""

from __future__ import annotations

from collections.abc import Iterable


def is_test_file(rel: str) -> bool:
    """pytest test files and conftest, JUnit test classes, anything under tests/ or test/ (src/test/**)."""
    name = rel.rsplit("/", 1)[-1]
    return (
        name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py"
        or name.endswith(("Test.java", "Tests.java", "IT.java"))
        or "/tests/" in f"/{rel}" or "/test/" in f"/{rel}"
    )


def in_test_code(functions: dict[str, dict], frames: dict[str, dict]) -> set[str]:
    """Ids of functions that are test code: those in test files, plus the entry frame's function."""
    out = {fid for fid, fn in functions.items() if is_test_file(fn["file"])}
    out |= {f["fn"] for f in frames.values() if f.get("parent") is None}
    return out


def function_under_test(
    steps: list[dict],
    frames: dict[str, dict],
    test_fns: set[str],
    lines_of: dict[str, Iterable[int]],
    changed: dict[str, set[int]],
) -> str | None:
    """The production function the viewer should open on.

    The first production function (in call order) that contains a PR-changed
    line; without one, the first production function the test calls itself.
    lines_of: function id -> its source lines; changed: file -> changed lines.
    """
    called: list[str] = []
    for step in steps:
        if step["kind"] != "call":
            continue
        fid = frames[str(step["frame"])]["fn"]
        if fid not in test_fns and fid not in called:
            called.append(fid)
    for fid in called:
        file = fid.rsplit(":", 1)[0]
        if changed.get(file, set()) & set(lines_of.get(fid, ())):
            return fid
    return called[0] if called else None
