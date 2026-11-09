"""Thin integration tests: record examples/java-shop through Maven and through Gradle.

They need a JDK and Maven (and Gradle for the Gradle test); the first run downloads
JUnit and the build plugins into ~/.m2 / ~/.gradle.
"""

import glob
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from wizualizer.diff import changed_lines
from wizualizer.java.recorder import JavaRecorder
from wizualizer.java.source import JavaSource
from wizualizer.ports import RecordingFailed, RecordRequest
from wizualizer.trace import make_trace
from wizualizer.viewer.render import render_html

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
pytestmark = pytest.mark.skipif(not (shutil.which("javac") and shutil.which("mvn")), reason="needs a JDK and Maven")

EDGE_TEST = """\
package shop;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.util.List;
import org.junit.jupiter.api.Test;

class EdgeCaseTest {
  private final Catalog catalog = new Catalog(List.of(new Product("MUG", "Enamel mug", 1_800, 350)));

  @Test
  void unknownSkuThrows() {
    assertThrows(UnknownProduct.class, () -> catalog.get("NOPE"));
  }

  @Test
  void wrongTotalFails() {
    assertEquals(1, catalog.get("MUG").priceCents());
  }
}
"""

GRADLE_BUILD = """\
plugins { id 'java' }
repositories { mavenCentral() }
dependencies {
  testImplementation 'org.junit.jupiter:junit-jupiter:5.11.4'
  testRuntimeOnly 'org.junit.platform:junit-platform-launcher:1.11.4'
}
test { useJUnitPlatform() }
"""


def git(repo, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", *args],
                   cwd=repo, check=True, capture_output=True)


@pytest.fixture(scope="module")
def shop(tmp_path_factory):
    """The Java shop as a git repo: `main` before the PR, HEAD on the PR branch."""
    repo = tmp_path_factory.mktemp("java-shop")
    shutil.copytree(EXAMPLES / "java-shop", repo, dirs_exist_ok=True, ignore=shutil.ignore_patterns("target"))
    (repo / ".gitignore").write_text("target/\nbuild/\n.gradle/\n")
    git(repo, "init", "-q", "-b", "main")
    git(repo, "apply", "-R", str(EXAMPLES / "java-shop-pr.patch"))
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "before")
    git(repo, "checkout", "-qb", "pr")
    git(repo, "apply", str(EXAMPLES / "java-shop-pr.patch"))
    (repo / "src/test/java/shop/EdgeCaseTest.java").write_text(EDGE_TEST)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "pr")
    return repo


@pytest.fixture(scope="module")
def source():
    model = JavaSource()
    yield model
    if model._server:
        model._server.close()


def run(repo, source, test_id, max_steps=10_000):
    changes, info = changed_lines(repo, "main")
    return make_trace(JavaRecorder(), source, RecordRequest(test_id, repo, repo, max_steps), changes, info)


def check_shop_trace(trace):
    assert trace["meta"]["outcome"] == "passed" and trace["meta"]["language"] == "java"
    s = trace["coverage"]["summary"]
    assert (s["ran"], s["not_run"], s["import_time"]) == (4, 5, 3)
    pricing = trace["coverage"]["files"]["src/main/java/shop/Pricing.java"]
    assert pricing["32"] == "not_run" and pricing["28"] == "ran"  # heavy surcharge never ran; free shipping did
    names = {f["qualname"] for f in trace["functions"].values()}
    assert {"OrderService.placeOrder", "Cart.add", "Catalog.get", "Pricing.shippingCents", "new Cart.LineItem"} <= names
    assert not any("lambda" in n for n in names)  # folded into Cart.subtotalCents
    assert all(f["flowchart"] for f in trace["functions"].values())
    actors = {f["actor"] for f in trace["frames"].values()}
    assert {"OrderServiceTest#1", "OrderService#1", "Cart#1", "Catalog#1", "Pricing"} <= actors

    steps, frames, fns = trace["steps"], trace["frames"], trace["functions"]
    fn_of = lambda s: fns[frames[str(s["frame"])]["fn"]]["qualname"]  # noqa: E731
    ret = next(s for s in steps if s["kind"] == "return" and fn_of(s) == "OrderService.placeOrder")
    assert ret["value"] == "Receipt(subtotalCents=7800, discountCents=780, shippingCents=0, totalCents=7020)"
    line = next(s for s in steps if s["kind"] == "line" and fn_of(s) == "OrderService.placeOrder" and "cart" in s["locals"])
    assert line["locals"]["this.today"] == "2026-11-09"
    assert line["locals"]["this.coupons"].startswith('{"WELCOME10"=Coupon(code="WELCOME10"')
    calls = [s for s in steps if s["kind"] == "call" and fn_of(s) == "Pricing.shippingCents"]
    assert calls[0]["args"] == {"subtotalCents": "7020", "weightG": "2050"}
    # every frame closes, and lines are ones the debugger reported inside the method
    assert sum(s["kind"] == "call" for s in steps) == sum(s["kind"] == "return" for s in steps)


def test_maven_end_to_end_with_change_coverage(shop, source):
    trace = run(shop, source, "shop.OrderServiceTest#orderOver50ShipsFreeWithCoupon")
    check_shop_trace(trace)
    assert trace["meta"]["note"] == "maven in ."
    json.dumps(trace)
    html = render_html(trace)
    for banned in ("https://", "fetch(", "XMLHttpRequest", "<link", "src=\"http"):
        assert banned not in html


def test_exception_steps_and_failed_outcome(shop, source):
    trace = run(shop, source, "EdgeCaseTest#unknownSkuThrows")
    assert trace["meta"]["outcome"] == "passed"
    exc = [s for s in trace["steps"] if s["kind"] == "exception"]
    assert exc and exc[0]["type"] == "UnknownProduct" and exc[0]["message"] == "no product NOPE"
    raised = [s for s in trace["steps"] if s.get("raised")]
    assert raised and raised[0]["type"] == "UnknownProduct"

    failed = run(shop, source, "shop.EdgeCaseTest#wrongTotalFails")
    assert failed["meta"]["outcome"] == "failed"
    assert "expected: <1> but was: <1800>" in failed["meta"]["message"]


def test_step_budget_truncates(shop, source):
    trace = run(shop, source, "shop.OrderServiceTest#orderOver50ShipsFreeWithCoupon", max_steps=15)
    assert trace["meta"]["truncated"] and len(trace["steps"]) == 15


def test_unknown_test_method_fails_clearly(shop):
    with pytest.raises(RecordingFailed, match="did not run"):
        JavaRecorder().record(RecordRequest("shop.OrderServiceTest#nope", shop, shop))


def _gradle() -> str | None:
    found = os.environ.get("WIZ_TEST_GRADLE") or shutil.which("gradle")
    if found:
        return found
    cached = sorted(glob.glob(os.path.expanduser("~/.gradle/wrapper/dists/gradle-*-bin/*/gradle-*/bin/gradle")))
    return cached[-1] if cached else None


@pytest.mark.skipif(not _gradle(), reason="needs Gradle (on PATH, WIZ_TEST_GRADLE, or a wrapper download)")
def test_gradle_end_to_end(shop, source, tmp_path, monkeypatch):
    repo = tmp_path / "gradle-shop"
    shutil.copytree(shop, repo, ignore=shutil.ignore_patterns("target"))
    git(repo, "rm", "-q", "pom.xml")
    (repo / "build.gradle").write_text(GRADLE_BUILD)
    (repo / "settings.gradle").write_text("rootProject.name = 'java-shop'\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "gradle")
    monkeypatch.setenv("PATH", f"{Path(_gradle()).parent}{os.pathsep}{os.environ['PATH']}")
    trace = run(repo, source, "shop.OrderServiceTest#orderOver50ShipsFreeWithCoupon")
    check_shop_trace(trace)
    assert trace["meta"]["note"] == "gradle in ."
