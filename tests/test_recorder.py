import importlib.util
import json
import subprocess
import sys
import textwrap

import pytest

from wizualizer.diff import changed_lines
from wizualizer.ports import RecordingFailed, RecordRequest
from wizualizer.python.recorder import PythonRecorder
from wizualizer.python.source import PythonSource
from wizualizer.python.tracer import Tracer, project_file_filter
from wizualizer.trace import SCHEMA, VERSION, make_trace
from wizualizer.viewer.render import render_html

MODULE = textwrap.dedent("""\
    class Counter:
        def __init__(self):
            self.n = 0

        def bump(self, by):
            self.n += by
            return self.n


    def run(items):
        c = Counter()
        total = sum(x for x in items)
        for x in items:
            c.bump(x)
        try:
            fail(total)
        except ValueError:
            pass
        return c.n


    def fail(v):
        raise ValueError(f"bad {v}")
""")


def load(tmp_path):
    path = tmp_path / "mod.py"
    path.write_text(MODULE)
    spec = importlib.util.spec_from_file_location("wiz_test_mod", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def record(tmp_path, fn, *args, max_steps=10_000):
    tracer = Tracer(str(tmp_path), project_file_filter(str(tmp_path)), max_steps=max_steps)
    tracer.start()
    try:
        fn(*args)
    finally:
        tracer.stop()
    return tracer.result()


def test_records_calls_lines_returns_and_exceptions(tmp_path):
    mod = load(tmp_path)
    rec = record(tmp_path, mod.run, [2, 3])
    kinds = [s["kind"] for s in rec["steps"]]
    assert kinds[0] == "call" and kinds[-1] == "return"
    names = {f["qualname"] for f in rec["functions"].values()}
    assert names == {"run", "Counter.__init__", "Counter.bump", "fail"}  # genexpr folded into run
    final = rec["steps"][-1]
    assert final["value"] == "5"
    exc = [s for s in rec["steps"] if s["kind"] == "exception"]
    assert exc and exc[0]["type"] == "ValueError" and exc[0]["message"] == "bad 5"
    unwound = [s for s in rec["steps"] if s.get("raised")]
    assert unwound and unwound[0]["type"] == "ValueError"


def test_locals_snapshots_and_actors(tmp_path):
    mod = load(tmp_path)
    rec = record(tmp_path, mod.run, [2, 3])
    bump_calls = [s for s in rec["steps"] if s["kind"] == "call" and rec["frames"][str(s["frame"])]["fn"].endswith(":5")]
    assert [c["args"]["by"] for c in bump_calls] == ["2", "3"]
    assert bump_calls[1]["args"]["self"] == "Counter(n=2)"
    actors = {f["actor"] for f in rec["frames"].values()}
    assert actors == {"mod", "Counter#1"}
    run_frame = next(k for k, f in rec["frames"].items() if f["fn"].endswith(":10"))
    bump_frames = [f for f in rec["frames"].values() if f["fn"].endswith(":5")]
    assert all(str(f["parent"]) == run_frame for f in bump_frames)


def test_only_project_code_is_recorded(tmp_path):
    mod = load(tmp_path)
    rec = record(tmp_path, mod.run, [1])
    assert all(f["file"] == "mod.py" for f in rec["functions"].values())


def test_step_budget_truncates(tmp_path):
    mod = load(tmp_path)
    rec = record(tmp_path, mod.run, list(range(50)), max_steps=20)
    assert rec["truncated"] and len(rec["steps"]) == 20


SHOP = {
    "calc.py": "def price(q):\n    if q > 10:\n        return q * 9\n    return q * 10\n",
    "tests/test_calc.py": "from calc import price\n\n\ndef test_small():\n    assert price(2) == 20\n\n\ndef test_other():\n    assert price(1) == 10\n",
    "pyproject.toml": "[tool.pytest.ini_options]\npythonpath = ['.']\n",
}


def git(repo, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", *args],
                   cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    for rel, text in SHOP.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    (tmp_path / "calc.py").write_text("def price(q):\n    return q * 10\n")
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "base")
    git(tmp_path, "checkout", "-qb", "pr")
    (tmp_path / "calc.py").write_text(SHOP["calc.py"])
    git(tmp_path, "commit", "-qam", "bulk price")
    return tmp_path


def test_pytest_recorder_end_to_end_with_change_coverage(repo):
    request = RecordRequest("tests/test_calc.py::test_small", repo, repo, interpreter=sys.executable)
    changes, info = changed_lines(repo, "main")
    trace = make_trace(PythonRecorder(), PythonSource(), request, changes, info)
    assert (trace["schema"], trace["version"]) == (SCHEMA, VERSION)
    assert trace["meta"]["outcome"] == "passed"
    assert trace["coverage"]["files"]["calc.py"] == {"2": "ran", "3": "not_run"}
    assert trace["functions"]["calc.py:1"]["flowchart"]["nodes"][1]["label"] == "if q > 10"
    assert trace["files"]["calc.py"]["changed"] == [2, 3]
    assert trace["functions"]["tests/test_calc.py:4"]["test"] is True
    assert trace["functions"]["calc.py:1"]["test"] is False
    assert trace["meta"]["focus"] == "calc.py:1"
    assert trace["meta"]["show_test_steps"] is False
    json.dumps(trace)  # serialisable

    html = render_html(trace)
    assert "__TRACE_JSON__" not in html and "/*__JS__*/" not in html
    for banned in ("https://", "fetch(", "XMLHttpRequest", "<link", "src=\"http"):
        assert banned not in html


def test_unknown_node_id_fails_clearly(repo):
    request = RecordRequest("tests/test_calc.py::nope", repo, repo, interpreter=sys.executable)
    with pytest.raises(RecordingFailed):
        PythonRecorder().record(request)


def test_failing_test_is_recorded_as_failed(repo):
    (repo / "tests" / "test_calc.py").write_text("from calc import price\n\ndef test_bad():\n    assert price(2) == 21\n")
    request = RecordRequest("tests/test_calc.py::test_bad", repo, repo, interpreter=sys.executable)
    rec = PythonRecorder().record(request)
    assert rec["test"]["outcome"] == "failed"
    assert rec["steps"][-1]["kind"] == "return" and rec["steps"][-1].get("raised")



def outside_call(trace, name):
    calls = [c for c in trace["calls"] if c["name"] == name]
    assert len(calls) == 1, trace["calls"]
    return calls[0]


def test_mocked_call_points_at_the_real_code_with_its_arguments_and_result(mocked_trace):
    t = mocked_trace
    assert t["meta"]["outcome"] == "passed"
    charge = outside_call(t, "charge")
    assert charge["mock"] is True and charge["type"] == "app.gateway.PaymentGateway"
    assert charge["args"] == {"account": "'ann'", "cents": "3"}  # named after the implementation's parameters
    assert charge["value"] == "'ok-1'"
    assert charge["targets"] == ["app/gateway.py:11"]  # the implementation, not the abstract method
    impl = t["functions"]["app/gateway.py:11"]
    assert impl["ran"] is False and impl["qualname"] == "CardGateway.charge" and impl["flowchart"]["nodes"]
    assert "app/gateway.py" in t["files"]
    caller = t["functions"][t["frames"][str(charge["frame"])]["fn"]]
    assert caller["qualname"] == "Checkout.pay" and charge["line"] == 10


def test_monkeypatched_function_is_a_stub_for_the_real_one(mocked_trace):
    stub = outside_call(mocked_trace, "send_receipt")
    assert stub.get("stub") is True and stub["targets"] == ["app/gateway.py:17"]
    assert stub["args"] == {"email": "'ann@x'", "text": "'ok-1'"} and stub["value"] == "False"


def test_real_calls_still_get_frames_and_library_calls_no_targets(mocked_trace):
    t = mocked_trace
    called = [t["functions"][t["frames"][str(s["frame"])]["fn"]]["qualname"] for s in t["steps"] if s["kind"] == "call"]
    assert called == ["test_pay", "Checkout.__init__", "Checkout.pay", "Checkout.total"]
    assert not [c for c in t["calls"] if c["name"] in ("pay", "total")]
    upper = outside_call(t, "upper")
    assert upper["type"] == "builtins.str" and "targets" not in upper
