"""The languages `wiz` can work with in a project: how to discover, record and probe their tests.

Adapters over python/ and java/. The workspace only sees the Language interface,
so tests can swap in fakes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from .discovery import Flow, java_flows, python_flows
from .ports import Recorder, SourceModel


class Language(Protocol):
    name: str

    def discover(self) -> list[Flow]: ...

    def recorder(self) -> Recorder: ...

    def source(self) -> SourceModel: ...

    def interpreter(self) -> str | None: ...

    def probe(self, flows: list[Flow], watch: dict[str, list[int]]) -> dict[str, dict]:
        """Run these tests once; test id -> {"lines": {file: [watched lines run]}, "starts": {...}}."""
        ...


class PythonLanguage:
    name = "python"

    def __init__(self, root: Path, python: str | None = None):
        from .python.recorder import default_interpreter

        self.root = root
        self.python = python or default_interpreter(root)
        self._source = None

    def discover(self) -> list[Flow]:
        return python_flows(self.root, self.python)

    def recorder(self) -> Recorder:
        from .python.recorder import PythonRecorder

        return PythonRecorder()

    def source(self) -> SourceModel:
        from .python.source import PythonSource

        self._source = self._source or PythonSource()
        return self._source

    def interpreter(self) -> str | None:
        return self.python

    def probe(self, flows: list[Flow], watch: dict[str, list[int]]) -> dict[str, dict]:
        from .python.probe import probe

        wanted = {f.id for f in flows}
        return {t: h for t, h in probe(self.root, self.python, watch).items() if t in wanted}


class JavaLanguage:
    name = "java"

    def __init__(self, root: Path, java_home: str | None = None):
        self.root = root
        self.java_home = java_home
        self._source = None

    def discover(self) -> list[Flow]:
        return java_flows(self.root)

    def recorder(self) -> Recorder:
        from .java.recorder import JavaRecorder

        return JavaRecorder()

    def source(self) -> SourceModel:
        from .java.source import JavaSource

        self._source = self._source or JavaSource(java_home=self.java_home)
        return self._source

    def interpreter(self) -> str | None:
        return self.java_home

    def probe(self, flows: list[Flow], watch: dict[str, list[int]]) -> dict[str, dict]:
        from .java.probe import probe

        return probe(self.root, [(f.id, self.root / f.file) for f in flows], watch, self.java_home)
