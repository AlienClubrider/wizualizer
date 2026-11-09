"""Find every test in a project. Each test is a "flow" the web app can record and play back.

Python: ask pytest (`--collect-only`), in the project's own interpreter.
Java: read the test sources for JUnit test annotations (no build, no JVM), so the
list shows up instantly even on a project that has never been built.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from .focus import is_test_file
from .java.project import SKIP_DIRS, _package_of
from .ports import RecordingFailed


@dataclass(frozen=True)
class Flow:
    id: str  # what `wiz run` takes: tests/test_x.py::TestA::test_b, or pkg.FooTest#bar
    language: str
    file: str  # relative to the project root
    group: str  # the class (or "" for a module-level pytest function)
    name: str

    def to_json(self) -> dict:
        return asdict(self)


# ---- Python ---------------------------------------------------------------------------------

def python_flows(root: Path, python: str) -> list[Flow]:
    cmd = [python, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", "--no-header"]
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    proc = subprocess.run(cmd, cwd=root, env=env, capture_output=True, text=True, timeout=600)
    flows = parse_pytest_collect(proc.stdout)
    if not flows and proc.returncode not in (0, 5):  # 5: no tests collected
        raise RecordingFailed(f"pytest could not collect the tests (exit {proc.returncode}):\n"
                              f"{(proc.stdout + proc.stderr)[-3000:]}")
    return flows


def parse_pytest_collect(text: str) -> list[Flow]:
    """Node ids from `pytest --collect-only -q`: one per line until the summary."""
    flows = []
    for line in text.splitlines():
        line = line.strip()
        if not line or "::" not in line or line.startswith(("=", "<")) or " " in line.split("::", 1)[0]:
            continue
        parts = line.split("::")
        flows.append(Flow(line, "python", parts[0], "::".join(parts[1:-1]), parts[-1]))
    return flows


# ---- Java -----------------------------------------------------------------------------------

TEST_ANNOTATIONS = {"Test", "ParameterizedTest", "RepeatedTest", "TestFactory", "TestTemplate"}
_TOKEN = re.compile(
    r'//[^\n]*|/\*.*?\*/|"""(?:.|\n)*?"""|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\''  # comments, strings
    r"|@\s*[\w.]+|\b(?:class|interface|enum|record)\s+\w+|\w+\s*\(|[{};()]",
    re.DOTALL,
)


def java_flows(root: Path) -> list[Flow]:
    flows: list[Flow] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(filenames):
            path = Path(dirpath) / name
            rel = path.relative_to(root).as_posix()
            if not name.endswith(".java") or not is_test_file(rel):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            pkg = _package_of(path)
            for cls, method in java_test_methods(text):
                full = f"{pkg}.{cls}" if pkg else cls
                flows.append(Flow(f"{full}#{method}", "java", rel, full, method))
    return flows


def java_test_methods(source: str) -> list[tuple[str, str]]:
    """(binary class name, method) for each JUnit 4/5 test method. Nested classes are Outer$Inner.

    A small scanner, not a parser: it tracks class nesting by braces and takes the
    first `name(` after a test annotation as the method.
    """
    out: list[tuple[str, str]] = []
    classes: list[tuple[str, int]] = []  # (name, brace depth of its body)
    depth = 0
    pending_class: str | None = None
    want_method = False
    paren = 0
    for m in _TOKEN.finditer(source):
        tok = m.group(0)
        if tok.startswith(("//", "/*", '"', "'")):
            continue
        if tok.startswith("@"):
            if paren == 0 and tok[1:].strip().rsplit(".", 1)[-1] in TEST_ANNOTATIONS:
                want_method = True
            continue
        if tok == "(":
            paren += 1
        elif tok == ")":
            paren = max(0, paren - 1)
        elif tok.endswith("("):
            paren += 1
            if want_method and paren == 1 and classes:
                out.append(("$".join(c for c, _ in classes), tok[:-1].strip()))
                want_method = False
        elif tok == "{":
            depth += 1
            if pending_class:
                classes.append((pending_class, depth))
                pending_class = None
        elif tok == "}":
            if classes and classes[-1][1] == depth:
                classes.pop()
            depth -= 1
        elif tok == ";":
            want_method = want_method and paren > 0
        elif paren == 0:  # class|interface|enum|record Name
            pending_class = tok.split()[-1]
    return out
