"""What kind of project a directory is: which test suites live in it and what is missing to run them.

File-system reads only (plus asking the project's interpreter for its version).
A project can hold several suites, e.g. a Python service with a Gradle module.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .java.project import GRADLE_FILES, GRADLE_SETTINGS, SKIP_DIRS

PY_MARKERS = ("pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini", "setup.py", "conftest.py")
MAX_DEPTH = 3  # how deep to look for a build file (e.g. a Gradle build in server/)


@dataclass(frozen=True)
class Suite:
    language: str  # "python" | "java"
    tool: str  # "pytest" | "maven" | "gradle"
    dir: str  # where its build file is, relative to the project root ("." for the root)


@dataclass
class Project:
    root: Path
    suites: list[Suite] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)  # what stops a suite from running, in plain words

    @property
    def languages(self) -> set[str]:
        return {s.language for s in self.suites}

    def describe(self) -> str:
        if not self.suites:
            return "no tests found"
        return ", ".join(s.tool + ("" if s.dir == "." else f" in {s.dir}/") for s in self.suites)


def detect(root: Path, python: str | None = None, java_home: str | None = None) -> Project:
    project = Project(root)
    for dirpath, dirnames, filenames in _walk(root):
        names = set(filenames)
        tool = ("gradle" if names & set(GRADLE_FILES + GRADLE_SETTINGS)
                else "maven" if "pom.xml" in names else None)
        if tool:
            project.suites.append(Suite("java", tool, Path(dirpath).relative_to(root).as_posix()))
            dirnames[:] = []  # modules below belong to this build
    if _is_python_project(root):
        project.suites.insert(0, Suite("python", "pytest", "."))
    elif not project.suites:
        project.problems.append(
            "no pytest, Maven or Gradle project here: run wiz in the project root (where pyproject.toml, "
            "pytest.ini, pom.xml or build.gradle is)"
        )
    if "python" in project.languages:
        problem = python_problem(python or default_interpreter(root))
        if problem:
            project.problems.append(problem)
    if "java" in project.languages:
        problem = java_problem(java_home)
        if problem:
            project.problems.append(problem)
    return project


def _walk(root: Path):
    base = len(root.parts)
    for dirpath, dirnames, filenames in os.walk(root):
        depth = len(Path(dirpath).parts) - base
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")
                             and d not in ("src", "node_modules", "site-packages"))
        if depth >= MAX_DEPTH:
            dirnames[:] = []
        yield dirpath, dirnames, filenames


def _is_python_project(root: Path) -> bool:
    if any((root / m).exists() for m in PY_MARKERS):
        pyproject = root / "pyproject.toml"
        # A pyproject.toml alone could belong to a tool config in a Java repo; want Python code too.
        return not pyproject.exists() or any(root.glob("*.py")) or any(_python_files(root))
    return any(root.glob("tests/test_*.py")) or any(root.glob("test_*.py"))


def _python_files(root: Path):
    for dirpath, dirnames, filenames in _walk(root):
        for name in filenames:
            if name.endswith(".py"):
                yield name
                return


def default_interpreter(root: Path) -> str:
    from .python.recorder import default_interpreter as find

    return find(root)


def python_problem(python: str) -> str | None:
    """None if `python` is 3.12+ and has pytest, else what to fix."""
    try:
        proc = subprocess.run(
            [python, "-c", "import sys; print('%d.%d' % sys.version_info[:2]); import pytest"],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"cannot run the project's Python ({python}): {exc}"
    version = proc.stdout.strip()
    if version and tuple(map(int, version.split("."))) < (3, 12):
        return f"the project's Python ({python}) is {version}; wiz needs 3.12+ (pass --python)"
    if proc.returncode != 0:
        return f"pytest is not installed in the project's Python ({python}): install it there, or pass --python"
    return None


def java_problem(java_home: str | None) -> str | None:
    from .java.helper import JdkMissing, find_jdk

    try:
        find_jdk(java_home)
    except JdkMissing as exc:
        return f"Java tests found but {exc}"
    return None
