"""Recording on realistic builds: tests/fixtures/harbor-gradle (multi-project, buildSrc convention
plugins, configuration cache and build cache on, a convention that *sets* the test JVM's jvmArgs)
and tests/fixtures/harbor-maven (aggregator with modules two folders deep, Surefire argLine), both
with a Spring Boot service tested in-JVM through MockMvc with its external client mocked.

The first run downloads Gradle, Spring Boot and their plugins, as building these projects would.
Set WIZ_SKIP_SLOW=1 to skip.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from wizualizer.java import containers
from wizualizer.java.recorder import JavaRecorder
from wizualizer.ports import RecordingFailed, RecordRequest

FIXTURES = Path(__file__).parent / "fixtures"
pytestmark = [
    pytest.mark.skipif(not shutil.which("javac"), reason="needs a JDK"),
    pytest.mark.skipif(os.environ.get("WIZ_SKIP_SLOW") == "1", reason="WIZ_SKIP_SLOW=1"),
]
CONTROLLER_TEST = "com.harbor.payments.QuoteControllerTest#quotesAnInternationalTransfer"


def copy(name: str, tmp_path: Path) -> Path:
    root = tmp_path / name
    shutil.copytree(FIXTURES / name, root, ignore=shutil.ignore_patterns("build", "target", ".gradle", ".wiz"))
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    return root


def record(root: Path, test: str, notes: list | None = None) -> dict:
    return JavaRecorder().record(RecordRequest(test, root, root, progress=(notes.append if notes is not None else None)))


def functions_called(rec: dict) -> list[str]:
    return [rec["functions"][rec["frames"][str(s["frame"])]["fn"]]["qualname"] for s in rec["steps"] if s["kind"] == "call"]


@pytest.fixture(scope="module")
def gradle_root(tmp_path_factory):
    return copy("harbor-gradle", tmp_path_factory.mktemp("g"))


def test_gradle_spring_mockmvc_test_records_through_controller_and_service(gradle_root):
    notes: list[str] = []
    rec = record(gradle_root, CONTROLLER_TEST, notes)
    assert rec["test"]["outcome"] == "passed"
    called = functions_called(rec)
    assert called[:3] == ["QuoteControllerTest.quotesAnInternationalTransfer", "QuoteController.create",
                          "QuoteService.quote"]
    assert "FeeSchedule.feeFor" in called  # into the other Gradle project
    assert "the test JVM connected: recording the test" in notes
    assert any(n.startswith("Gradle: :services:payments:") for n in notes)


def test_gradle_records_again_with_the_configuration_cache_reused(gradle_root):
    record(gradle_root, "com.harbor.ledger.FeeScheduleTest#largeTransfersPayThePercentage")
    rec = record(gradle_root, "com.harbor.ledger.FeeScheduleTest#largeTransfersPayThePercentage")
    assert "Configuration cache entry reused." in rec["build_output"]
    assert functions_called(rec)[1] == "FeeSchedule.feeFor"


def test_gradle_compile_error_says_so_in_one_line(gradle_root, tmp_path):
    root = copy("harbor-gradle", tmp_path)
    svc = root / "services/payments/src/main/java/com/harbor/payments/QuoteService.java"
    svc.write_text(svc.read_text().replace("return new Quote(", "return new Quote(oops, "))
    with pytest.raises(RecordingFailed) as err:
        record(root, CONTROLLER_TEST)
    assert err.value.reason == "the build failed to compile before the test could run"
    assert "compileJava FAILED" in err.value.log and "--init-script" in err.value.log


def test_testcontainers_test_without_docker_fails_at_once(gradle_root, monkeypatch):
    it = gradle_root / "services/payments/src/test/java/com/harbor/payments/LedgerStoreIT.java"
    it.write_text("package com.harbor.payments;\nimport org.testcontainers.junit.jupiter.Testcontainers;\n"
                  "@Testcontainers class LedgerStoreIT { @org.junit.jupiter.api.Test void saves() {} }\n")
    monkeypatch.setattr(containers, "docker_reachable", lambda: False)
    try:
        with pytest.raises(RecordingFailed, match="Docker is not available"):
            record(gradle_root, "com.harbor.payments.LedgerStoreIT#saves")
    finally:
        it.unlink()


@pytest.mark.skipif(not shutil.which("mvn"), reason="needs Maven")
def test_maven_spring_mockmvc_test_in_a_nested_module(tmp_path):
    root = copy("harbor-maven", tmp_path)
    rec = record(root, CONTROLLER_TEST)
    assert rec["test"]["outcome"] == "passed"
    assert functions_called(rec)[1:3] == ["QuoteController.create", "QuoteService.quote"]
    assert "Reactor Summary" in rec["build_output"] and "common-ledger" in rec["build_output"]  # -pl -am from the root
