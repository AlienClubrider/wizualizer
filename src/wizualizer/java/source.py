"""Java SourceModel: statement spans and flowcharts, from javac's parser via the helper."""

from __future__ import annotations

import atexit
import hashlib
import json
from typing import Callable

from ..ports import Statement
from .flowchart import build_flowchart
from .helper import OutlineServer, find_jdk


class JavaSource:
    language = "java"

    def __init__(self, outline: Callable[[str], dict] | None = None, java_home: str | None = None):
        """outline(source) -> {"methods": [...], "statements": [...]}; default: the Java helper."""
        self._outline = outline or self._helper_outline
        self._java_home = java_home
        self._server: OutlineServer | None = None
        self._cache: dict[str, dict] = {}

    def _helper_outline(self, source: str) -> dict:
        if self._server is None:
            self._server = OutlineServer(find_jdk(self._java_home))
            atexit.register(self._server.close)
        return json.loads(self._server.outline(source))

    def handles(self, path: str) -> bool:
        return path.endswith(".java")

    def outline(self, source: str) -> dict:
        key = hashlib.sha256(source.encode("utf-8")).hexdigest()
        if key not in self._cache:
            self._cache[key] = self._outline(source)
        return self._cache[key]

    def statements(self, source: str) -> list[Statement]:
        out = self.outline(source)
        return [Statement(first, (lo, hi), bool(meas)) for first, lo, hi, meas in out.get("statements", [])]

    def flowchart(self, source: str, function_line: int) -> dict | None:
        for method in self.outline(source).get("methods", []):
            if method["line"] == function_line:
                return build_flowchart(method)
        return None
