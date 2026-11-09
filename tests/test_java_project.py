import os

import pytest

from wizualizer.cli import guess_language
from wizualizer.java.project import JavaTest, ProjectError, SourceIndex, detect_build, resolve_test
from wizualizer.java.recorder import read_report


def write(root, files):
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        if rel.endswith(("gradlew", "mvnw")):
            p.chmod(0o755)


SOURCES = {
    "app/src/main/java/com/acme/bank/Account.java": "package com.acme.bank;\nclass Account {}\n",
    "app/src/test/java/com/acme/bank/AccountTest.java": "package com.acme.bank;\nclass AccountTest {}\n",
    "app/build/generated/Junk.java": "package junk;\n",  # build output: ignored
    "misplaced/Stray.java": "package com.acme.other;\n",  # not in its package directory: ignored
}


def test_source_index_finds_package_roots_and_classes(tmp_path):
    write(tmp_path, SOURCES)
    index = SourceIndex.scan(tmp_path)
    assert index.roots == [tmp_path / "app/src/main/java", tmp_path / "app/src/test/java"]
    assert index.packages == ["com.acme.bank"]
    assert set(index.classes) == {"com.acme.bank.Account", "com.acme.bank.AccountTest"}


def test_resolve_test_by_full_name_simple_name_nested_and_path(tmp_path):
    write(tmp_path, SOURCES)
    index = SourceIndex.scan(tmp_path)
    test_file = tmp_path / "app/src/test/java/com/acme/bank/AccountTest.java"
    assert resolve_test("com.acme.bank.AccountTest#deposits", index) == JavaTest("com.acme.bank.AccountTest", "deposits", test_file)
    assert resolve_test("AccountTest#deposits()", index).cls == "com.acme.bank.AccountTest"
    assert resolve_test("AccountTest$Nested#x", index).cls == "com.acme.bank.AccountTest$Nested"
    assert resolve_test(f"{test_file}#x", index).cls == "com.acme.bank.AccountTest"
    with pytest.raises(ProjectError):
        resolve_test("com.acme.bank.AccountTest", index)  # no method
    with pytest.raises(ProjectError):
        resolve_test("NopeTest#x", index)


def test_maven_single_module_uses_wrapper(tmp_path):
    write(tmp_path, {"pom.xml": "<project/>", "mvnw": "#!/bin/sh\n", "src/test/java/T.java": ""})
    b = detect_build(tmp_path / "src/test/java/T.java", tmp_path)
    assert (b.tool, b.root, b.module, b.executable) == ("maven", tmp_path, tmp_path, str(tmp_path / "mvnw"))
    cmd = b.command(JavaTest("a.FooTest$Inner", "bar", tmp_path), "-agentlib:x")
    assert "-Dtest=a.FooTest*Inner#bar" in cmd and "-Dmaven.surefire.debug=-agentlib:x" in cmd
    assert "-pl" not in cmd
    assert b.report(JavaTest("a.FooTest", "bar", tmp_path)) == tmp_path / "target/surefire-reports/TEST-a.FooTest.xml"


def test_maven_multi_module_builds_only_the_test_module(tmp_path):
    write(tmp_path, {"pom.xml": "<project/>", "mvnw": "", "core/pom.xml": "<project/>", "core/src/test/java/T.java": ""})
    b = detect_build(tmp_path / "core/src/test/java/T.java", tmp_path)
    assert (b.root, b.module, b.module_path) == (tmp_path, tmp_path / "core", "core")
    cmd = b.command(JavaTest("a.T", "m", tmp_path), "-agentlib:x")
    assert cmd[cmd.index("-pl") + 1] == "core" and "-am" in cmd


def test_gradle_multi_project_runs_the_module_test_task(tmp_path):
    write(tmp_path, {
        "settings.gradle.kts": "", "gradlew": "#!/bin/sh\n",
        "services/bank/build.gradle.kts": "", "services/bank/src/test/java/T.java": "",
    })
    b = detect_build(tmp_path / "services/bank/src/test/java/T.java", tmp_path)
    assert (b.tool, b.root, b.executable) == ("gradle", tmp_path, str(tmp_path / "gradlew"))
    init = tmp_path / "init.gradle"
    cmd = b.command(JavaTest("a.T", "m", tmp_path), "-agentlib:x", init)
    assert cmd[1:] == ["--console=plain", "--no-scan", "--init-script", str(init), ":services:bank:test", "--tests", "a.T.m"]
    assert b.report(JavaTest("a.T", "m", tmp_path)) == tmp_path / "services/bank/build/test-results/test/TEST-a.T.xml"


def test_gradle_project_below_the_git_root(tmp_path):
    # RuneStone-Bank layout: the Gradle build lives in server/, the git root above it.
    write(tmp_path, {"server/settings.gradle.kts": "", "server/build.gradle.kts": "", "server/gradlew": "",
                     "server/src/test/java/T.java": ""})
    b = detect_build(tmp_path / "server/src/test/java/T.java", tmp_path)
    assert (b.root, b.module, b.module_path) == (tmp_path / "server", tmp_path / "server", "")
    assert b.command(JavaTest("a.T", "m", tmp_path), "-agentlib:x")[-3] == "test"


def test_no_build_file_or_tool_is_a_clear_error(tmp_path, monkeypatch):
    write(tmp_path, {"src/T.java": ""})
    with pytest.raises(ProjectError, match="no pom.xml"):
        detect_build(tmp_path / "src/T.java", tmp_path)
    write(tmp_path, {"pom.xml": "<project/>"})
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    with pytest.raises(ProjectError, match="mvn"):
        detect_build(tmp_path / "src/T.java", tmp_path)


SUREFIRE = """<?xml version="1.0"?>
<testsuite name="a.FooTest" tests="2">
  <testcase name="good" classname="a.FooTest" time="0.012"/>
  <testcase name="bad" classname="a.FooTest" time="0.003">
    <failure message="expected: &lt;1&gt; but was: &lt;2&gt;" type="org.opentest4j.AssertionFailedError">trace</failure>
  </testcase>
</testsuite>"""

GRADLE = """<?xml version="1.0"?>
<testsuite name="a.FooTest" tests="1"><testcase name="good()" classname="a.FooTest" time="1.5"/></testsuite>"""


def test_read_report_surefire_and_gradle_formats(tmp_path):
    (tmp_path / "s.xml").write_text(SUREFIRE)
    (tmp_path / "g.xml").write_text(GRADLE)
    assert read_report(tmp_path / "s.xml", JavaTest("a.FooTest", "good", tmp_path)) == {
        "outcome": "passed", "message": "", "duration_ms": 12.0}
    bad = read_report(tmp_path / "s.xml", JavaTest("a.FooTest", "bad", tmp_path))
    assert bad == {"outcome": "failed", "message": "expected: <1> but was: <2>"}
    assert read_report(tmp_path / "g.xml", JavaTest("a.FooTest", "good", tmp_path))["outcome"] == "passed"
    assert read_report(tmp_path / "missing.xml", JavaTest("a.FooTest", "good", tmp_path)) is None
    os.utime(tmp_path / "g.xml", (1, 1))
    assert read_report(tmp_path / "g.xml", JavaTest("a.FooTest", "good", tmp_path), not_before=1000) is None  # stale


def test_guess_language():
    assert guess_language("tests/test_x.py::test_y") == "python"
    assert guess_language("tests/test_x.py") == "python"
    assert guess_language("com.acme.FooTest#bar") == "java"
    assert guess_language("FooTest#bar") == "java"
