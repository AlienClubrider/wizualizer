"""The two things a language must provide to wizualizer.

A language plugs in with a Recorder (runs one test and returns what happened)
and a SourceModel (reads source files: which lines are statements, and the
flowchart of each function). Everything else (diff, change coverage, the
viewer) is language-neutral and works on the trace format in
docs/trace-format.md.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class RecordRequest:
    test_id: str  # e.g. "tests/test_orders.py::test_free_shipping"
    root: Path  # project root; only code under it is recorded
    cwd: Path  # where the test runner is started
    max_steps: int = 10_000
    interpreter: str | None = None  # language runtime to use, if the recorder needs one
    progress: Callable[[str], None] | None = field(default=None, compare=False)  # e.g. "> Task :app:test"


class RecordingFailed(RuntimeError):
    """str() is the whole story for a terminal. reason (one line), hint and log (the full build
    output) are there for the web app to show them apart."""

    def __init__(self, message: str, reason: str = "", hint: str = "", log: str = ""):
        super().__init__(message)
        self.reason = reason or message.split("\n", 1)[0]
        self.hint = hint
        self.log = log


class Recorder(Protocol):
    language: str
    name: str

    def record(self, request: RecordRequest) -> dict:
        """Run one test and return {steps, frames, functions, truncated, test}.

        Paths in the result are relative to request.root, with "/" separators.
        """
        ...


@dataclass(frozen=True)
class Statement:
    """One executable statement, as coverage sees it."""

    first: int  # the line a debugger reports when the statement runs
    lines: tuple[int, int]  # inclusive span of source lines it owns
    measurable: bool  # False if it runs at import time, not inside a function


class SourceModel(Protocol):
    language: str

    def handles(self, path: str) -> bool: ...

    def statements(self, source: str) -> list[Statement]: ...

    def flowchart(self, source: str, function_line: int) -> dict | None:
        """Flowchart of the function whose definition starts at function_line."""
        ...
