from wizualizer.change_coverage import IMPORT_TIME, NEUTRAL, NOT_RUN, RAN, classify_lines, summarize
from wizualizer.python.source import PythonSource

SRC = """\
LIMIT = 10


def shipping(total, weight):
    if total >= LIMIT:
        return 0
    cost = compute(
        weight,
    )
    # a comment
    return cost
"""


def statuses(changed, executed, called=frozenset()):
    return classify_lines(set(changed), PythonSource().statements(SRC), set(executed), set(called))


def test_lines_that_ran_and_lines_that_did_not():
    out = statuses({5, 6, 7, 11}, executed={5, 6})
    assert out == {5: RAN, 6: RAN, 7: NOT_RUN, 11: NOT_RUN}


def test_continuation_line_counts_as_run_when_its_statement_ran():
    assert statuses({8}, executed={7}) == {8: RAN}


def test_module_level_lines_are_import_time_and_comments_neutral():
    assert statuses({1, 10, 2}, executed={5}) == {1: IMPORT_TIME, 10: NEUTRAL, 2: NEUTRAL}


def test_def_line_counts_as_run_when_the_function_was_called():
    assert statuses({4}, executed=set(), called={4}) == {4: RAN}
    assert statuses({4}, executed=set()) == {4: IMPORT_TIME}


def test_summary_counts_only_measurable_lines_in_percent():
    s = summarize({"a.py": {1: RAN, 2: NOT_RUN, 3: IMPORT_TIME}, "b.py": {4: RAN}})
    assert (s["ran"], s["not_run"], s["measured"], s["percent"]) == (2, 1, 3, 67)
    assert summarize({})["percent"] is None
