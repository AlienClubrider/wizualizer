import ast

from wizualizer.python.flowchart import build_flowchart
from wizualizer.python.source import PythonSource


def chart(src):
    return build_flowchart(ast.parse(src).body[0], src)


def by_label(fc):
    return {n["label"]: n for n in fc["nodes"]}


def edges(fc):
    lab = {n["id"]: n["label"] for n in fc["nodes"]}
    return {(lab[e["from"]], lab[e["to"]], e.get("label")) for e in fc["edges"]}


def test_if_else_branches_and_return():
    fc = chart("def f(x):\n    if x > 1:\n        y = 1\n    else:\n        y = 2\n    return y\n")
    assert [n["kind"] for n in fc["nodes"]] == ["start", "decision", "stmt", "stmt", "return", "end"]
    e = edges(fc)
    assert ("if x > 1", "y = 1", "yes") in e
    assert ("if x > 1", "y = 2", "no") in e
    assert ("y = 1", "return y", None) in e and ("y = 2", "return y", None) in e
    assert ("return y", "end", None) in e


def test_elif_chain_is_flat_and_labelled():
    fc = chart("def f(x):\n    if x:\n        a()\n    elif x == 2:\n        b()\n")
    n = by_label(fc)
    assert n["elif x == 2"]["depth"] == n["if x"]["depth"]
    assert ("if x", "elif x == 2", "no") in edges(fc)
    assert ("elif x == 2", "end", "no") in edges(fc)


def test_loop_back_edge_break_and_continue():
    src = "def f(xs):\n    for x in xs:\n        if x:\n            continue\n        if x < 0:\n            break\n        use(x)\n    return 1\n"
    e = edges(chart(src))
    assert ("for x in xs", "if x", "next") in e
    assert ("continue", "for x in xs", None) in e
    assert ("use(x)", "for x in xs", None) in e
    assert ("break", "return 1", None) in e
    assert ("for x in xs", "return 1", "done") in e


def test_while_loop():
    e = edges(chart("def f(n):\n    while n:\n        n -= 1\n    return n\n"))
    assert ("while n", "n -= 1", "yes") in e and ("n -= 1", "while n", None) in e
    assert ("while n", "return n", "no") in e


def test_try_except_and_raise_inside_try_goes_to_handler():
    src = "def f():\n    try:\n        raise ValueError()\n    except ValueError as e:\n        handle(e)\n    finally:\n        done()\n"
    e = edges(chart(src))
    assert ("raise ValueError()", "except ValueError as e", "raise") in e
    assert ("try", "except ValueError as e", "error") in e
    assert ("handle(e)", "done()", None) in e


def test_raise_outside_try_goes_to_end():
    assert ("raise X", "end", "raise") in edges(chart("def f():\n    raise X\n"))


def test_docstring_is_skipped_and_nodes_carry_line_spans():
    fc = chart('def f():\n    """doc"""\n    x = g(\n        1,\n    )\n')
    stmt = by_label(fc)["x = g(1)"]
    assert stmt["lines"] == [3, 5]


def test_source_model_finds_decorated_function_by_first_line():
    src = "import functools\n\n@functools.cache\ndef f():\n    return 1\n"
    fc = PythonSource().flowchart(src, 3)
    assert fc is not None and fc["nodes"][0]["label"] == "f()"
    assert PythonSource().flowchart(src, 4) is None


def test_match_statement():
    src = "def f(x):\n    match x:\n        case 1:\n            a()\n        case _:\n            b()\n"
    e = edges(chart(src))
    assert ("match x", "case 1", None) in e and ("case 1", "case _", "no") in e
