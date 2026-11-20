"""The recorder never hangs and never leaves a JVM behind (tests/fixtures/stalls-maven).

Each test there once hung the recorder or orphaned the test JVM: a map rendered while the test
thread was inside a JDK call (a lambda run by computeIfAbsent), an exception leaving the test
method, a test that never ends. Needs a JDK and Maven; the first run downloads JUnit.
"""

import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from wizualizer.java.recorder import JavaRecorder
from wizualizer.ports import RecordingFailed, RecordRequest

FIXTURE = Path(__file__).parent / "fixtures" / "stalls-maven"
SRC = Path(__file__).resolve().parents[1] / "src"
pytestmark = [
    pytest.mark.skipif(not (shutil.which("javac") and shutil.which("mvn")), reason="needs a JDK and Maven"),
    pytest.mark.skipif(os.name != "posix", reason="process checks use ps"),
]


@pytest.fixture(scope="module")
def stalls(tmp_path_factory):
    root = tmp_path_factory.mktemp("stalls") / "stalls-maven"
    shutil.copytree(FIXTURE, root, ignore=shutil.ignore_patterns("target", ".wiz"))
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    return root


def record(root: Path, test: str, timeout_s: float = 120) -> dict:
    return JavaRecorder().record(RecordRequest(f"stalls.StallsTest#{test}", root, root, timeout_s=timeout_s))


def processes_under(root: Path) -> list[str]:
    """Processes whose command line mentions the project (helper, mvn, the surefire JVM)."""
    out = subprocess.run(["ps", "ax", "-ww", "-o", "pid=,args="], capture_output=True, text=True).stdout
    return [line.strip() for line in out.splitlines()
            if str(root) in line and line.split(None, 1)[0] != str(os.getpid())]


def wait_until_gone(root: Path, seconds: float = 30) -> list[str]:
    deadline = time.time() + seconds
    while time.time() < deadline:
        left = processes_under(root)
        if not left:
            return []
        time.sleep(1)
    return processes_under(root)


def test_a_map_read_while_a_jdk_call_runs_a_lambda_does_not_hang(stalls):
    rec = record(stalls, "groupsRoutesByPrefix")
    assert rec["test"]["outcome"] == "passed"
    maps = [s["locals"]["this.byPrefix"] for s in rec["steps"] if "this.byPrefix" in s.get("locals", {})]
    assert '{"/a"=["/a/b", "/a/c"], "/d"=["/d"]}' in maps


def test_an_exception_leaving_the_test_method_ends_the_recording_there(stalls):
    rec = record(stalls, "parseThrowsUncaught")
    assert rec["test"]["outcome"] == "failed"
    last = rec["steps"][-1]
    assert last["kind"] == "return" and last["raised"] and last["type"] == "IllegalArgumentException"
    assert rec["frames"][str(last["frame"])]["parent"] is None  # the test method's own frame
    assert not [c for c in rec["calls"] if c["type"].startswith("org.junit")]  # nothing recorded after it


def test_jdk_collections_and_values_are_read_from_their_fields(stalls):
    rec = record(stalls, "countsEverything")
    seen = {}
    for s in rec["steps"]:
        seen.update(s.get("locals", {}))
    expected = {
        "list": '["a", "b"]', "fixed": "[1, 2, 3]", "one": '["solo"]', "sorted": '["x", "y"]', "hashed": '["h"]',
        "hash": '{"k"=1}', "linked": '{"first"=1, "second"=2}', "tree": '{"a"=1, "b"=2}', "small": '{"only"=9}',
        "concurrent": '{"c"=3}', "deque": '["p", "q"]', "chain": '["l1", "l2"]', "values": "[1, 2]",
        "keys": '["first", "second"]', "view": '["a", "b"]', "many": "[1, 2, 3, 4, 5, 6, 7, 8, …+2]",
        "price": "12.50", "big": "-123456789012345678901234567890", "day": "2026-11-20",
        "epoch": "1970-01-01T00:00:00Z", "wait": "PT1H30M", "id": "00000000-0000-0001-0000-000000000002",
        "uri": "https://example.com/a?b=c",
    }
    assert {name: seen.get(name) for name in expected} == expected


def test_a_test_that_never_ends_times_out_in_one_line_and_leaves_no_jvm(stalls):
    started = time.time()
    with pytest.raises(RecordingFailed) as failed:
        record(stalls, "spinsForever", timeout_s=10)
    assert time.time() - started < 120
    assert failed.value.reason.endswith("was still running after 10 s under the recorder, so wiz stopped it")
    assert "--timeout" in failed.value.hint
    assert wait_until_gone(stalls) == []


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGKILL], ids=["sigterm", "kill-9"])
def test_stopping_wiz_mid_recording_stops_the_test_jvm_and_the_build(stalls, sig):
    env = {**os.environ, "PYTHONPATH": str(SRC)}
    wiz = subprocess.Popen([sys.executable, "-m", "wizualizer.cli", "run", "stalls.StallsTest#spinsForever",
                            "--timeout", "0", "--out", str(stalls / ".wiz")],
                           cwd=stalls, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        deadline = time.time() + 180
        while not [p for p in processes_under(stalls) if "-agentlib:jdwp=" in p and "suspend=y" in p]:
            assert wiz.poll() is None and time.time() < deadline, "the test JVM never started"
            time.sleep(0.5)
        time.sleep(3)  # well inside the test
        wiz.send_signal(sig)
        out, _ = wiz.communicate(timeout=60)
    finally:
        if wiz.poll() is None:
            wiz.kill()
    if sig == signal.SIGTERM:
        assert wiz.returncode == 130 and "wiz: stopped" in out
    assert wait_until_gone(stalls, 45) == []
