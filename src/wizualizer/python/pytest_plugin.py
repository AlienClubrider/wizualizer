"""pytest plugin loaded into the process under test by `wiz run`.

Configured through environment variables (set by the runner):
  WIZ_OUT        where to write the raw recording (JSON)
  WIZ_ROOT       project root; only files under it are recorded
  WIZ_MAX_STEPS  step budget (default 10000)

Stdlib + pytest only, so it works in any project's interpreter (3.12+).
"""

from __future__ import annotations

import json
import os
import time

import pytest

from .tracer import Tracer, project_file_filter

_state: dict = {"outcome": "not run", "phases": {}, "message": ""}
_tracer: Tracer | None = None


def _enabled() -> bool:
    return bool(os.environ.get("WIZ_OUT"))


def pytest_collection_modifyitems(session, config, items):
    """Record exactly one test: keep the first collected item."""
    if _enabled() and len(items) > 1:
        rest = items[1:]
        del items[1:]
        config.hook.pytest_deselected(items=rest)
        _state["note"] = f"node id matched {len(rest) + 1} tests; recorded only the first"


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    global _tracer
    if not _enabled():
        yield
        return
    root = os.environ.get("WIZ_ROOT") or str(item.config.rootpath)
    plugin_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    _tracer = Tracer(
        root,
        project_file_filter(root, extra_excludes=(plugin_dir,)),
        max_steps=int(os.environ.get("WIZ_MAX_STEPS", "10000")),
    )
    _state["test"] = item.nodeid
    started = time.perf_counter()
    _tracer.start()
    try:
        yield
    finally:
        _tracer.stop()
        _state["duration_ms"] = round((time.perf_counter() - started) * 1000, 1)


def pytest_runtest_logreport(report):
    if not _enabled():
        return
    _state["phases"][report.when] = report.outcome
    if report.failed and not _state["message"]:
        _state["message"] = str(report.longreprtext)[-2000:]
    phases = _state["phases"]
    if any(v == "failed" for v in phases.values()):
        _state["outcome"] = "failed"
    elif phases.get("setup") == "skipped" or phases.get("call") == "skipped":
        _state["outcome"] = "skipped"
    elif phases.get("call") == "passed":
        _state["outcome"] = "passed"


def pytest_sessionfinish(session, exitstatus):
    if not _enabled():
        return
    recording = _tracer.result() if _tracer else {"steps": [], "frames": {}, "functions": {}, "truncated": False}
    recording["test"] = {k: v for k, v in _state.items() if k != "phases"}
    with open(os.environ["WIZ_OUT"], "w", encoding="utf-8") as fh:
        json.dump(recording, fh)
