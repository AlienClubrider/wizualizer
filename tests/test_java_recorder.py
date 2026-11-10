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


FOCUS = "OrderService.placeOrder"  # the PR changed it, and it is the first function the test calls


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
    # the test method is tagged as test code; the viewer opens on the first changed function it reaches
    test_fn = frames["1"]["fn"]
    assert fns[test_fn]["test"] and fns[test_fn]["qualname"] == "OrderServiceTest.orderOver50ShipsFreeWithCoupon"
    assert [f["test"] for k, f in fns.items() if k != test_fn] == [False] * (len(fns) - 1)
    assert fns[trace["meta"]["focus"]]["qualname"] == FOCUS
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


STOCK = """\
package shop;

public interface Stock {
  boolean reserve(String sku, int qty);
}
"""
WAREHOUSE = """\
package shop;

public class Warehouse implements Stock {
  @Override
  public boolean reserve(String sku, int qty) {
    if (qty <= 0) {
      return false;
    }
    return !sku.isEmpty();
  }
}
"""
RESERVATIONS = """\
package shop;

public class Reservations {
  private final Stock stock;
  private final Catalog catalog;

  public Reservations(Stock stock, Catalog catalog) {
    this.stock = stock;
    this.catalog = catalog;
  }

  public String hold(String sku, int qty) {
    Product p = catalog.get(sku);
    boolean ok = stock.reserve(sku, qty);
    return ok ? p.name().toUpperCase() : "none";
  }
}
"""
RESERVATIONS_TEST = """\
package shop;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import org.junit.jupiter.api.Test;

class ReservationsTest {
  @Test
  void holdsWhatTheStockReserves() {
    Stock stock = mock(Stock.class);
    Catalog catalog = mock(Catalog.class);
    when(stock.reserve("MUG", 2)).thenReturn(true);
    when(catalog.get("MUG")).thenReturn(new Product("MUG", "Enamel mug", 1_800, 350));
    assertEquals("ENAMEL MUG", new Reservations(stock, catalog).hold("MUG", 2));
  }
}
"""
MOCKITO = """
    <dependency><groupId>org.mockito</groupId><artifactId>mockito-core</artifactId><version>5.17.0</version><scope>test</scope></dependency>
    <dependency><groupId>net.bytebuddy</groupId><artifactId>byte-buddy</artifactId><version>1.17.8</version><scope>test</scope></dependency>
    <dependency><groupId>net.bytebuddy</groupId><artifactId>byte-buddy-agent</artifactId><version>1.17.8</version><scope>test</scope></dependency>
  </dependencies>"""


@pytest.fixture(scope="module")
def mocked_shop(tmp_path_factory):
    """The Java shop with a Mockito test: an interface mock and a mock of a class (Mockito's inline mocks)."""
    repo = tmp_path_factory.mktemp("java-mocked")
    shutil.copytree(EXAMPLES / "java-shop", repo, dirs_exist_ok=True, ignore=shutil.ignore_patterns("target"))
    pom = repo / "pom.xml"
    pom.write_text(pom.read_text().replace("\n  </dependencies>", MOCKITO, 1))
    main, test = repo / "src/main/java/shop", repo / "src/test/java/shop"
    (main / "Stock.java").write_text(STOCK)
    (main / "Warehouse.java").write_text(WAREHOUSE)
    (main / "Reservations.java").write_text(RESERVATIONS)
    (test / "ReservationsTest.java").write_text(RESERVATIONS_TEST)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    return repo


def test_mocked_calls_point_at_the_real_code_with_arguments_and_results(mocked_shop, source):
    trace = make_trace(JavaRecorder(), source, RecordRequest("shop.ReservationsTest#holdsWhatTheStockReserves",
                                                             mocked_shop, mocked_shop))
    assert trace["meta"]["outcome"] == "passed", trace["meta"]
    fns, frames = trace["functions"], trace["frames"]
    hold = next(k for k, f in fns.items() if f["qualname"] == "Reservations.hold")
    from_hold = [c for c in trace["calls"] if frames[str(c["frame"])]["fn"] == hold]
    by_name = {c["name"]: c for c in from_hold}
    # an interface mock: resolved to the one implementation, arguments named from its source
    reserve = by_name["reserve"]
    assert reserve["mock"] and reserve["type"] == "shop.Stock" and reserve["value"] == "true"
    assert reserve["args"] == {"sku": '"MUG"', "qty": "2"}
    assert [fns[t]["qualname"] for t in reserve["targets"]] == ["Warehouse.reserve"]
    assert fns[reserve["targets"][0]]["ran"] is False and fns[reserve["targets"][0]]["flowchart"]
    # a class mocked inline: the class's own method, which did not run
    get = by_name["get"]
    assert get["mock"] and get["args"] == {"sku": '"MUG"'} and get["value"].startswith('Product(sku="MUG"')
    assert [fns[t]["qualname"] for t in get["targets"]] == ["Catalog.get"]
    # JDK calls: values, no target
    upper = by_name["toUpperCase"]
    assert upper["type"] == "java.lang.String" and upper["value"] == '"ENAMEL MUG"' and "targets" not in upper
    # real calls are unchanged: frames of their own
    called = [fns[frames[str(s["frame"])]["fn"]]["qualname"] for s in trace["steps"] if s["kind"] == "call"]
    assert "Reservations.hold" in called and "Catalog.get" not in called and "Warehouse.reserve" not in called


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


def test_changed_flows_probe_runs_the_suite_once(shop):
    """Only-changes mode on Java: one Maven run, breakpoints only on the changed lines."""
    from wizualizer.languages import JavaLanguage
    from wizualizer.workspace import Workspace

    ws = Workspace(shop, {"java": JavaLanguage(shop)})
    ids = [f.id for f in ws.flows()]
    assert "shop.OrderServiceTest#orderOver50ShipsFreeWithCoupon" in ids
    assert "shop.EdgeCaseTest#unknownSkuThrows" in ids
    scan = ws.scan()["tests"]
    assert scan["shop.OrderServiceTest#orderOver50ShipsFreeWithCoupon"]["lines"] == 4
    assert scan["shop.OrderServiceTest#smallOrderPaysShipping"]["lines"] == 5
    edge = scan["shop.EdgeCaseTest#unknownSkuThrows"]  # a new test: it changed, but runs no changed code
    assert edge["lines"] == 0 and edge["test_lines"] >= 1
