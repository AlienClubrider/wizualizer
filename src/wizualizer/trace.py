"""Assemble a trace (docs/trace-format.md) from a recording, the sources and the PR diff."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from . import change_coverage
from .diff import FileChange
from .ports import Recorder, RecordRequest, SourceModel

SCHEMA = "wizualizer.trace"
VERSION = 1


def make_trace(
    recorder: Recorder,
    source_model: SourceModel,
    request: RecordRequest,
    changes: dict[str, FileChange] | None = None,
    diff_info: dict | None = None,
    read_file=None,
) -> dict:
    """Record one test and enrich it into a full trace.

    read_file(rel_path) -> str | None lets tests avoid the filesystem.
    """
    read_file = read_file or (lambda rel: _read(request.root / rel))
    recording = recorder.record(request)
    return build_trace(recording, source_model, request, recorder, changes or {}, diff_info, read_file)


def build_trace(recording, source_model, request, recorder, changes, diff_info, read_file) -> dict:
    functions = recording["functions"]
    frames = recording["frames"]
    steps = recording["steps"]

    executed: dict[str, set[int]] = {}
    called: dict[str, set[int]] = {}
    for step in steps:
        fn = functions[frames[str(step["frame"])]["fn"]]
        if step["kind"] == "line":
            executed.setdefault(fn["file"], set()).add(step["line"])
        elif step["kind"] == "call":
            called.setdefault(fn["file"], set()).add(fn["line"])

    wanted = set(executed) | set(called) | {p for p in changes if source_model.handles(p)}
    files: dict[str, dict] = {}
    for rel in sorted(wanted):
        text = read_file(rel)
        if text is None:
            continue
        entry: dict = {"source": text}
        if rel in changes:
            entry["changed"] = sorted(changes[rel].added)
            entry["removed_after"] = sorted(changes[rel].removed_after)
        files[rel] = entry

    for fn in functions.values():
        text = files.get(fn["file"], {}).get("source")
        fn["flowchart"] = source_model.flowchart(text, fn["line"]) if text is not None else None

    per_file: dict[str, dict[int, str]] = {}
    for rel, change in changes.items():
        if not source_model.handles(rel) or rel not in files or _is_test_file(rel):
            continue
        statuses = change_coverage.classify_lines(
            change.added,
            source_model.statements(files[rel]["source"]),
            executed.get(rel, set()),
            called.get(rel, set()),
        )
        per_file[rel] = statuses

    test = recording.get("test", {})
    return {
        "schema": SCHEMA,
        "version": VERSION,
        "meta": {
            "test": test.get("test", request.test_id),
            "outcome": test.get("outcome", "unknown"),
            "message": test.get("message", ""),
            "note": test.get("note", ""),
            "duration_ms": test.get("duration_ms"),
            "language": recorder.language,
            "recorder": recorder.name,
            "created": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "truncated": recording.get("truncated", False),
            "max_steps": request.max_steps,
            "diff": diff_info,
        },
        "files": files,
        "functions": functions,
        "frames": frames,
        "steps": steps,
        "coverage": {
            "files": {rel: {str(k): v for k, v in st.items()} for rel, st in sorted(per_file.items())},
            "summary": change_coverage.summarize(per_file),
            "skipped_test_files": sorted(p for p in changes if _is_test_file(p)),
        } if diff_info else None,
    }


def _is_test_file(rel: str) -> bool:
    name = rel.rsplit("/", 1)[-1]
    return (
        name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py"
        or "/tests/" in f"/{rel}" or "/test/" in f"/{rel}"
    )


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
