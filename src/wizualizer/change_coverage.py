"""Changed-line coverage: which lines a PR changed ran in this test, and which never ran.

Pure rules, no I/O. A changed line is judged by the statement that owns it, so
a change on the second line of a multi-line call counts as run when the call
ran. Lines that own no statement (blank, comments, `else:`) are "neutral".
Statements that run at import time (module or class level) are "import-time":
a test run cannot tell us anything about them.
"""

from __future__ import annotations

from .ports import Statement

RAN = "ran"
NOT_RUN = "not_run"
IMPORT_TIME = "import_time"
NEUTRAL = "neutral"


def classify_lines(
    changed: set[int],
    statements: list[Statement],
    executed: set[int],
    called_defs: set[int],
) -> dict[int, str]:
    """Status for each changed line.

    executed:    lines the recorder saw run (line events) in this file
    called_defs: first lines (co_firstlineno) of functions that were called
    """
    owner: dict[int, Statement] = {}
    for st in statements:
        lo, hi = st.lines
        for line in range(lo, hi + 1):
            # Inner statements are listed after their headers; the innermost wins.
            owner[line] = st
    ran_statements = {id(owner[line]) for line in executed if line in owner}

    out: dict[int, str] = {}
    for line in sorted(changed):
        st = owner.get(line)
        if st is None:
            out[line] = NEUTRAL
        elif id(st) in ran_statements or st.lines[0] in called_defs:
            out[line] = RAN
        elif not st.measurable:
            out[line] = IMPORT_TIME
        else:
            out[line] = NOT_RUN
    return out


def summarize(per_file: dict[str, dict[int, str]]) -> dict:
    counts = {RAN: 0, NOT_RUN: 0, IMPORT_TIME: 0, NEUTRAL: 0}
    for statuses in per_file.values():
        for status in statuses.values():
            counts[status] += 1
    measured = counts[RAN] + counts[NOT_RUN]
    counts["measured"] = measured
    counts["percent"] = round(100 * counts[RAN] / measured) if measured else None
    return counts
