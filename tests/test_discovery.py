import subprocess
import sys
import textwrap

from wizualizer.discovery import java_flows, java_test_methods, parse_pytest_collect, python_flows
from wizualizer.project import detect


def test_parse_pytest_collect_keeps_node_ids_only():
    out = textwrap.dedent("""\
        tests/test_a.py::test_one
        tests/test_a.py::TestCart::test_two[1-2]
        tests/sub/test_b.py::TestX::TestInner::test_three

        3 tests collected in 0.01s
    """)
    flows = parse_pytest_collect(out)
    assert [f.id for f in flows] == [
        "tests/test_a.py::test_one",
        "tests/test_a.py::TestCart::test_two[1-2]",
        "tests/sub/test_b.py::TestX::TestInner::test_three",
    ]
    assert (flows[1].file, flows[1].group, flows[1].name) == ("tests/test_a.py", "TestCart", "test_two[1-2]")
    assert flows[2].group == "TestX::TestInner"
    assert flows[0].group == ""


JAVA = textwrap.dedent("""\
    package com.acme;

    import org.junit.jupiter.api.*;

    // @Test void notMe() {}
    class BankTest {
        @Test
        void movesMoney() { String s = "@Test fake("; }

        /* @Test void alsoNotMe() */
        @ParameterizedTest
        @ValueSource(ints = {1, 2})
        void rejects(int amount) {}

        void helper() {}

        @org.junit.Test public void junit4Style() throws Exception {}

        @Nested
        class WhenEmpty {
            @Test
            @DisplayName("shows (nothing)")
            void showsNothing() { new Runnable() { public void run() {} }; }
        }

        @Test void afterNested() {}
    }
""")


def test_java_test_methods_reads_annotations_and_nesting():
    assert java_test_methods(JAVA) == [
        ("BankTest", "movesMoney"),
        ("BankTest", "rejects"),
        ("BankTest", "junit4Style"),
        ("BankTest$WhenEmpty", "showsNothing"),
        ("BankTest", "afterNested"),
    ]


def test_java_flows_only_reads_test_files(tmp_path):
    test_dir = tmp_path / "src/test/java/com/acme"
    test_dir.mkdir(parents=True)
    (test_dir / "BankTest.java").write_text(JAVA)
    main_dir = tmp_path / "src/main/java/com/acme"
    main_dir.mkdir(parents=True)
    (main_dir / "Bank.java").write_text("package com.acme;\nclass Bank { @Test void looksLikeATest() {} }\n")
    ids = [f.id for f in java_flows(tmp_path)]
    assert ids[0] == "com.acme.BankTest#movesMoney"
    assert "com.acme.BankTest$WhenEmpty#showsNothing" in ids
    assert not any("Bank#" in i for i in ids)
    assert {f.file for f in java_flows(tmp_path)} == {"src/test/java/com/acme/BankTest.java"}


def test_python_flows_asks_pytest(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text(
        "def test_a():\n    pass\n\nclass TestB:\n    def test_c(self):\n        pass\n")
    assert [f.id for f in python_flows(tmp_path, sys.executable)] == [
        "tests/test_x.py::test_a", "tests/test_x.py::TestB::test_c"]


def test_detect_names_each_suite(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "app.py").write_text("")
    (tmp_path / "server").mkdir()
    (tmp_path / "server" / "settings.gradle.kts").write_text("")
    (tmp_path / "server" / "api").mkdir()
    (tmp_path / "server" / "api" / "build.gradle.kts").write_text("")  # a subproject: same build
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "pom.xml").write_text("<project/>")
    project = detect(tmp_path, python=sys.executable, java_home=None)
    assert [(s.tool, s.dir) for s in project.suites] == [
        ("pytest", "."), ("gradle", "server"), ("maven", "tools")]
    assert project.describe() == "pytest, gradle in server/, maven in tools/"


def test_detect_says_what_is_missing(tmp_path):
    (tmp_path / "notes.txt").write_text("hi")
    project = detect(tmp_path, python=sys.executable)
    assert project.suites == []
    assert "no pytest, Maven or Gradle project" in project.problems[0]


def test_detect_reports_python_without_pytest(tmp_path):
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    venv = tmp_path / "env"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)], check=True)
    project = detect(tmp_path, python=str(venv / "bin" / "python"))
    assert any("pytest is not installed" in p for p in project.problems)
