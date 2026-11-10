"""Workspace use cases: list, record (cached), only-changes. Fake languages, a real git repo."""

import subprocess
import sys
import textwrap

import pytest

from wizualizer.discovery import Flow
from wizualizer.functions import EntryPoint
from wizualizer.languages import PythonLanguage
from wizualizer.python.source import PythonSource
from wizualizer.store import Store
from wizualizer.workspace import Workspace

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@localhost", "-c", "commit.gpgsign=false"]
PRICING = textwrap.dedent("""\
    def shipping(total):
        if total >= 50:
            return 0
        return 5


    def tax(total):
        return total // 10
""")
TESTS = textwrap.dedent("""\
    from pricing import shipping, tax


    def test_free_shipping():
        assert shipping(60) == 0


    def test_tax():
        assert tax(100) == 10
""")


def git(repo, *args):
    subprocess.run([*GIT, *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "pricing.py").write_text(PRICING)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_pricing.py").write_text(TESTS)
    (tmp_path / "conftest.py").write_text("import sys, os\nsys.path.insert(0, os.path.dirname(__file__))\n")
    (tmp_path / ".gitignore").write_text("__pycache__/\n")
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "start")
    return tmp_path


class FakeLanguage:
    """Records instantly and counts how often it was asked to."""

    name = "python"

    def __init__(self, root, hits=None):
        self.root = root
        self.recorded = []
        self.hits = hits or {}
        self.probed = 0

    def discover(self):
        return [Flow("tests/test_pricing.py::test_free_shipping", "python", "tests/test_pricing.py", "",
                     "test_free_shipping")]

    def recorder(self):
        lang = self

        class R:
            language, name = "python", "fake"

            def record(self, request):
                lang.recorded.append(request.test_id)
                fn = {"file": "pricing.py", "line": 1, "name": "shipping", "qualname": "shipping"}
                return {
                    "functions": {"pricing.py:1": fn},
                    "frames": {"1": {"fn": "pricing.py:1", "parent": None, "actor": "pricing"}},
                    "steps": [{"kind": "call", "frame": 1, "args": {}},
                              {"kind": "line", "frame": 1, "line": 2, "locals": {}},
                              {"kind": "return", "frame": 1, "value": "0"}],
                    "test": {"test": request.test_id, "outcome": "passed"},
                }
        return R()

    def source(self):
        return PythonSource()

    def interpreter(self):
        return None

    def entry_points(self, flows):
        return {f.id: EntryPoint("pricing.py", "shipping", "shipping(subtotal)", 1, (("subtotal", "6000"),))
                for f in flows}

    def probe(self, flows, watch):
        self.probed += 1
        return self.hits


def test_diff_without_git_says_so(tmp_path):
    ws = Workspace(tmp_path, {"python": FakeLanguage(tmp_path)})
    d = ws.diff()
    assert not d.available and d.reason == "not a git repository"


def test_diff_defaults_to_main_and_counts_code_changes(repo):
    ws = Workspace(repo, {"python": FakeLanguage(repo)})
    d = ws.diff()
    assert not d.available and d.reason.startswith("no code changes against main")
    (repo / "pricing.py").write_text(PRICING.replace("total >= 50", "total >= 40"))
    (repo / "NOTES.md").write_text("not code\n")
    d = ws.diff()
    assert d.available and d.base == "main" and (d.files, d.lines) == (1, 1)


def test_wiz_cache_never_shows_up_as_a_change(repo):
    Workspace(repo, {"python": FakeLanguage(repo)})
    out = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True).stdout
    assert out == ""


def test_trace_is_recorded_once_and_again_after_an_edit(repo):
    lang = FakeLanguage(repo)
    ws = Workspace(repo, {"python": lang})
    flow_id = "tests/test_pricing.py::test_free_shipping"
    assert ws.trace(flow_id, record=False) is None
    first = ws.trace(flow_id)
    assert first["meta"]["outcome"] == "passed"
    assert ws.trace(flow_id)["meta"]["created"] == first["meta"]["created"]
    assert lang.recorded == [flow_id]
    flow = ws.flow(flow_id)
    assert ws.cached(flow, ws.store.source_hash(), ws.diff())["steps"] == 3

    (repo / "pricing.py").write_text(PRICING + "\n# edited\n")
    assert ws.trace(flow_id, record=False) is None  # stale: not served
    ws.trace(flow_id)
    assert lang.recorded == [flow_id, flow_id]
    assert len(list((repo / ".wiz" / "flows").glob("*-*.json"))) == 1  # the old one is gone


def test_scan_keeps_tests_that_run_changed_lines(repo):
    git(repo, "checkout", "-qb", "pr")
    (repo / "pricing.py").write_text(PRICING.replace("total >= 50", "total >= 40"))
    lang = FakeLanguage(repo, hits={
        "tests/test_pricing.py::test_free_shipping": {"lines": {"pricing.py": [2]}},
        "tests/test_pricing.py::test_tax": {"lines": {"pricing.py": [8]}},
    })
    ws = Workspace(repo, {"python": lang})
    assert ws.cached_scan() is None
    scan = ws.scan()
    assert scan["tests"] == {"tests/test_pricing.py::test_free_shipping": {
        "lines": 1, "test_lines": 0, "files": {"pricing.py": [2]}}}
    assert ws.cached_scan() == scan
    assert lang.probed == 1


def test_scan_and_record_with_real_pytest(repo):
    """End to end on a real project: the probe finds the one test that runs the changed line."""
    git(repo, "checkout", "-qb", "pr")
    (repo / "pricing.py").write_text(PRICING.replace("return 5", "return 7"))
    ws = Workspace(repo, {"python": PythonLanguage(repo, sys.executable)})
    assert [f.id for f in ws.flows()] == ["tests/test_pricing.py::test_free_shipping",
                                          "tests/test_pricing.py::test_tax"]
    assert ws.scan()["tests"] == {}  # neither test pays shipping

    (repo / "pricing.py").write_text(PRICING.replace("return total // 10", "return total // 10 + 0"))
    scan = ws.scan()
    assert list(scan["tests"]) == ["tests/test_pricing.py::test_tax"]
    assert scan["tests"]["tests/test_pricing.py::test_tax"]["lines"] == 1

    trace = ws.trace("tests/test_pricing.py::test_tax")
    assert trace["meta"]["outcome"] == "passed"
    assert trace["coverage"]["summary"]["ran"] == 1


def test_store_key_ignores_docs_and_build_output(repo):
    store = Store(repo)
    before = store.source_hash()
    (repo / "README.md").write_text("docs\n")
    (repo / "target").mkdir()
    (repo / "target" / "x.class").write_bytes(b"\0")
    (repo / "sub").mkdir()
    (repo / "sub" / "__pycache__").mkdir()
    (repo / "sub" / "__pycache__" / "m.pyc").write_bytes(b"\0")
    assert store.source_hash() == before
    (repo / "new_module.py").write_text("x = 1\n")
    assert store.source_hash() != before
