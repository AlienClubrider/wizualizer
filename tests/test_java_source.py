"""Java SourceModel: javac's parser (through the helper) plus the flowchart builder."""

import shutil

import pytest

from wizualizer.java.flowchart import build_flowchart
from wizualizer.java.source import JavaSource

needs_jdk = pytest.mark.skipif(not shutil.which("javac"), reason="needs a JDK")

SRC = """\
package demo;

import java.util.List;

public class Account {
  static final long LIMIT = 500;
  private long balance = 0;
  private String owner;

  @Deprecated
  public long withdraw(long amount) {
    if (amount <= 0) {
      throw new IllegalArgumentException("amount");
    } else if (amount > LIMIT) {
      return -1;
    }
    long fee;
    balance -= amount;
    return balance;
  }

  int scan(List<Integer> xs) {
    int seen = 0;
    outer:
    for (int x : xs) {
      if (x < 0) {
        continue;
      }
      for (int i = 0; i < x; i++) {
        if (i == 3) {
          break outer;
        }
        seen++;
      }
    }
    while (seen > 10) {
      seen--;
    }
    do {
      seen++;
    } while (seen < 2);
    xs.forEach(x -> {
      owner = "x" + x;
    });
    return seen;
  }

  String kind(int code) {
    String out = "?";
    switch (code) {
      case 1:
        out = "one";
      case 2:
        out = "small";
        break;
      default:
        out = "big";
    }
    return switch (code) {
      case 0 -> "zero";
      default -> out;
    };
  }

  void safe(Runnable r) {
    try {
      r.run();
    } catch (IllegalStateException | IllegalArgumentException e) {
      owner = null;
    } finally {
      balance = 0;
    }
  }
}
"""


@pytest.fixture(scope="module")
def src():
    model = JavaSource()
    yield model
    if model._server:
        model._server.close()


def labels(fc):
    return {n["label"]: n for n in fc["nodes"]}


def edges(fc):
    lab = {n["id"]: n["label"] for n in fc["nodes"]}
    return {(lab[e["from"]], lab[e["to"]], e.get("label")) for e in fc["edges"]}


@needs_jdk
def test_statements_for_coverage(src):
    st = {s.lines[0]: s for s in src.statements(SRC)}
    assert st[6].measurable is False  # static field: runs at class load
    assert st[7].measurable is True  # instance field initializer: runs in the constructor
    assert 8 not in st  # field without initializer: no code
    assert st[10].lines == (10, 11)  # method header owns its annotation; counts as run if called
    assert st[12].lines == (12, 12)  # `if` owns its condition
    assert 17 not in st  # `long fee;` compiles to nothing
    assert st[42].lines == (42, 44) and st[43].lines == (43, 43)  # a lambda body's statements are statements
    assert st[68].lines == (68, 68)  # catch clause (the handler's first instruction)
    assert 66 not in st  # plain `try {` compiles to nothing


@needs_jdk
def test_if_else_if_chain_throw_and_return(src):
    fc = src.flowchart(SRC, 10)
    assert fc["nodes"][0]["label"] == "withdraw(amount)" and fc["nodes"][0]["lines"] == [10, 10]
    n = labels(fc)
    assert n["else if (amount > LIMIT)"]["depth"] == n["if (amount <= 0)"]["depth"]
    e = edges(fc)
    assert ("if (amount <= 0)", 'throw new IllegalArgumentException("amount");', "yes") in e
    assert ('throw new IllegalArgumentException("amount");', "end", "throw") in e
    assert ("else if (amount > LIMIT)", "balance -= amount;", "no") in e
    assert ("return balance;", "end", None) in e
    assert "long fee;" not in n  # compiles to nothing, so no node


@needs_jdk
def test_loops_labeled_break_continue_do_while_and_folded_lambda(src):
    fc = src.flowchart(SRC, 22)
    e = edges(fc)
    assert ("for (int x : xs)", "if (x < 0)", "next") in e
    assert ("continue;", "for (int x : xs)", None) in e
    assert ("break outer;", "while (seen > 10)", None) in e  # leaves both loops
    assert ("seen++;", "for (int i = 0; i < x; i++)", None) in e
    assert ("for (int x : xs)", "while (seen > 10)", "done") in e
    assert ("while (seen < 2)", "seen++;", "yes") in e
    lambda_stmt = next(n for n in fc["nodes"] if n["label"].startswith("xs.forEach"))
    assert lambda_stmt["lines"] == [42, 44]  # lambda lines light up the statement holding it


@needs_jdk
def test_switch_fallthrough_break_default(src):
    e = edges(src.flowchart(SRC, 48))
    assert ("switch (code)", "case 1", None) in e
    assert ("case 1", "case 2", "no") in e
    assert ('out = "one";', 'out = "small";', None) in e  # falls through
    assert ("break;", 'return switch (code) { case 0 -> "zero"; defaul…', None) in e
    assert ("default", 'out = "big";', "yes") in e


@needs_jdk
def test_try_catch_finally(src):
    fc = src.flowchart(SRC, 65)
    e = edges(fc)
    assert ("try", "catch (IllegalStateException | IllegalArgumentE…", "error") in e
    assert ("r.run();", "balance = 0;", None) in e
    assert ("owner = null;", "balance = 0;", None) in e
    assert ("balance = 0;", "end", None) in e


@needs_jdk
def test_unknown_line_has_no_flowchart(src):
    assert src.flowchart(SRC, 3) is None


def test_builder_on_a_hand_written_outline():
    """The flowchart builder alone, no JDK: a do-while whose body continues."""
    method = {"name": "f", "line": 1, "params": ["n"], "body": [
        {"k": "do", "l": [5, 5], "t": "while (n > 0)", "body": {"k": "block", "body": [
            {"k": "if", "l": [3, 3], "t": "if (n == 2)", "then": {"k": "continue", "l": [3, 3], "t": "continue;", "label": None}, "else": None},
            {"k": "stmt", "l": [4, 4], "t": "n--;"},
        ]}},
        {"k": "return", "l": [6, 6], "t": "return n;"},
    ]}
    e = edges(build_flowchart(method))
    assert ("continue;", "while (n > 0)", None) in e
    assert ("while (n > 0)", "if (n == 2)", "yes") in e
    assert ("while (n > 0)", "return n;", "no") in e
