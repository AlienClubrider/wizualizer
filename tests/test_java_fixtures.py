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


TRANSFERS = "com.harbor.payments.TransferControllerTest#"


def hops(rec: dict) -> list[str]:
    return [n["label"] for n in rec["framework"]]


def test_spring_slice_shows_the_framework_hops_and_draws_the_mocked_client_as_a_mock(gradle_root, monkeypatch, tmp_path):
    stats = tmp_path / "stats.tsv"
    monkeypatch.setenv("WIZ_RECORDER_STATS", str(stats))
    rec = record(gradle_root, TRANSFERS + "movesMoney")
    assert rec["test"]["outcome"] == "passed"
    called = functions_called(rec)
    assert called[1:4] == ["TransferController.create", "TransferService.transfer", "TransferStore.save"]
    assert "TransferAudit.audited" in called
    assert "FraudCheckClient.score" not in called  # @MockitoBean of a class: a mock, not its real code
    by_fn = {rec["functions"][rec["frames"][str(c["frame"])]["fn"]]["qualname"]: c for c in rec["calls"] if c["name"] == "score"}
    score = by_fn["TransferService.transfer"]
    assert score["mock"] and score["value"] == "10" and score["args"] == {"account": '"ACC-1"', "amount": "25.00"}
    assert hops(rec) == [
        "Spring MVC dispatched POST /transfers",
        "@Transactional TransferService.transfer: began a transaction",
        "@AfterReturning advice TransferAudit.audited",
        "@Transactional TransferService.transfer: committed",
        "Spring MVC answered POST /transfers: 200",
    ]
    # Nothing is watched while the Spring context starts: only the test class loading, before the test runs.
    fields = dict(f.split("=", 1) for f in stats.read_text().split("\t")[1:])
    assert int(fields["before_window"]) <= 5


def test_spring_slice_validation_rejecting_the_request_is_one_step(gradle_root):
    rec = record(gradle_root, TRANSFERS + "rejectsABlankAccount")
    assert rec["test"]["outcome"] == "passed"
    assert "TransferController.create" not in functions_called(rec)
    assert hops(rec) == [
        "Spring MVC dispatched POST /transfers",
        "@Valid rejected the request: account: must not be blank",
        "Spring MVC answered POST /transfers: 400",
    ]


def test_spring_slice_a_thrown_exception_rolls_the_transaction_back(gradle_root):
    rec = record(gradle_root, TRANSFERS + "blocksARiskyTransfer")
    assert rec["test"]["outcome"] == "passed"
    assert "TransferController.blocked" in functions_called(rec)
    assert "@Transactional TransferService.transfer: rolled back" in hops(rec)
    assert hops(rec)[-1] == "Spring MVC answered POST /transfers: 409"
    raised = [s for s in rec["steps"] if s["kind"] == "return" and s.get("raised")]
    assert raised and all(s["type"] == "IllegalStateException" for s in raised)
    assert len(rec["calls"]) < 60  # no library internals recorded while the exception unwinds


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
