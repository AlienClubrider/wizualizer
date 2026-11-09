"""pytest plugin for the change probe: which test runs which watched lines.

Loaded into the project's own interpreter by python/probe.py. Configured by:
  WIZ_PROBE      JSON file {"root": ..., "watch": {rel_path: [lines]}}
  WIZ_PROBE_OUT  where to write {nodeid: {"lines": {rel: [...]}, "starts": {rel: [...]}}}

Cheap by design: every location reports once per test and is then disabled
(sys.monitoring.DISABLE); the next test re-arms them with restart_events().
Setup, call and teardown all count, so code run by fixtures counts too.
Stdlib + pytest only.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

M = sys.monitoring
_watch: dict[str, set[int]] = {}
_root = ""
_rel_cache: dict[str, str | None] = {}
_hits: dict[str, dict[str, dict[str, set[int]]]] = {}
_current: str | None = None
_tool: int | None = None


def _enabled() -> bool:
    return bool(os.environ.get("WIZ_PROBE"))


def _rel(filename: str) -> str | None:
    rel = _rel_cache.get(filename, "")
    if rel == "":
        path = os.path.realpath(filename)
        rel = os.path.relpath(path, _root).replace(os.sep, "/") if path.startswith(_root + os.sep) else None
        rel = rel if rel in _watch else None
        _rel_cache[filename] = rel
    return rel


def _record(kind: str, rel: str, line: int) -> None:
    _hits.setdefault(_current, {}).setdefault(kind, {}).setdefault(rel, set()).add(line)


def _on_line(code, line):
    if _current is not None:
        rel = _rel(code.co_filename)
        if rel is not None and line in _watch[rel]:
            _record("lines", rel, line)
    return M.DISABLE


def _on_start(code, offset):
    if _current is not None:
        rel = _rel(code.co_filename)
        if rel is not None and code.co_firstlineno in _watch[rel]:
            _record("starts", rel, code.co_firstlineno)
    return M.DISABLE


def pytest_configure(config):
    global _watch, _root, _tool
    if not _enabled():
        return
    with open(os.environ["WIZ_PROBE"], encoding="utf-8") as fh:
        spec = json.load(fh)
    _root = os.path.realpath(spec["root"])
    _watch = {rel: set(lines) for rel, lines in spec["watch"].items()}
    for tool in (M.COVERAGE_ID, 3, 4, M.PROFILER_ID, M.DEBUGGER_ID):
        if M.get_tool(tool) is None:
            M.use_tool_id(tool, "wizualizer-probe")
            _tool = tool
            break
    else:
        raise RuntimeError("no free sys.monitoring tool id (is coverage or a debugger attached?)")
    M.register_callback(_tool, M.events.LINE, _on_line)
    M.register_callback(_tool, M.events.PY_START, _on_start)
    M.set_events(_tool, M.events.LINE | M.events.PY_START)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    global _current
    if not _enabled():
        yield
        return
    _current = item.nodeid
    M.restart_events()
    try:
        yield
    finally:
        _current = None


def pytest_sessionfinish(session, exitstatus):
    if not _enabled():
        return
    if _tool is not None:
        M.set_events(_tool, 0)
        M.free_tool_id(_tool)
    out = {test: {kind: {rel: sorted(lines) for rel, lines in files.items()} for kind, files in kinds.items()}
           for test, kinds in _hits.items()}
    with open(os.environ["WIZ_PROBE_OUT"], "w", encoding="utf-8") as fh:
        json.dump(out, fh)
