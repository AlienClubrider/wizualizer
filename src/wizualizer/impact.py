"""Which tests touch the PR's changed lines. Pure rules, no I/O.

A probe runs the whole test suite once, watching only the statements that own
a changed line (`watch_lines`). It reports, per test, which watched lines ran
(and, for Python, which watched function definitions were called). `summarize`
turns that into a changed-line count per test, judged like change coverage.
"""

from __future__ import annotations

from . import change_coverage
from .diff import FileChange
from .focus import is_test_file
from .ports import Statement


def watch_lines(changes: dict[str, FileChange], statements: dict[str, list[Statement]]) -> dict[str, list[int]]:
    """file -> lines a probe must watch: the first line and the reported line of each changed statement."""
    out: dict[str, list[int]] = {}
    for rel, change in changes.items():
        stmts = statements.get(rel)
        if not stmts:
            continue
        lines: set[int] = set()
        for st in stmts:
            lo, hi = st.lines
            if any(lo <= line <= hi for line in change.added):
                lines.update((st.first, lo))
        if lines:
            out[rel] = sorted(lines)
    return out


def summarize(
    hits: dict[str, dict],
    changes: dict[str, FileChange],
    statements: dict[str, list[Statement]],
) -> dict[str, dict]:
    """test id -> {"lines": changed code lines it ran, "test_lines": changed lines in test files it ran,
    "files": {file: [changed lines it ran]}}. Tests that touch nothing are left out.

    hits: test id -> {"lines": {file: [ran lines]}, "starts": {file: [first lines of called defs]}}
    """
    out: dict[str, dict] = {}
    for test, hit in hits.items():
        ran_files: dict[str, list[int]] = {}
        code = tests = 0
        for rel in set(hit.get("lines", {})) | set(hit.get("starts", {})):
            if rel not in changes or rel not in statements:
                continue
            statuses = change_coverage.classify_lines(
                changes[rel].added, statements[rel],
                set(hit.get("lines", {}).get(rel, ())), set(hit.get("starts", {}).get(rel, ())),
            )
            ran = sorted(line for line, st in statuses.items() if st == change_coverage.RAN)
            if not ran:
                continue
            ran_files[rel] = ran
            if is_test_file(rel):
                tests += len(ran)
            else:
                code += len(ran)
        if ran_files:
            out[test] = {"lines": code, "test_lines": tests, "files": ran_files}
    return out
