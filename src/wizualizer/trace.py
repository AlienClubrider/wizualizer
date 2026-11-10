"""Assemble a trace (docs/trace-format.md) from a recording, the sources and the PR diff."""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

from . import call_targets, change_coverage
from .focus import function_under_test, is_test_file, in_test_code
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
    show_test_steps: bool = False,
) -> dict:
    """Record one test and enrich it into a full trace.

    read_file(rel_path) -> str | None lets tests avoid the filesystem.
    """
    read_file = read_file or (lambda rel: _read(request.root / rel))
    recording = recorder.record(request)
    return build_trace(recording, source_model, request, recorder, changes or {}, diff_info, read_file, show_test_steps)


def build_trace(
    recording, source_model, request, recorder, changes, diff_info, read_file, show_test_steps=False,
    list_files=None,
) -> dict:
    """list_files() -> project-relative paths of every source file (for matching mocked calls)."""
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

    test_fns = in_test_code(functions, frames)
    for fid, fn in functions.items():
        fn["test"] = fid in test_fns
    calls = recording.get("calls", [])
    link_calls(calls, functions, frames, files, source_model, read_file,
               list_files or (lambda: _source_files(request.root)))
    focus = function_under_test(
        steps, frames, test_fns,
        {fid: _span(fn) for fid, fn in functions.items()},
        {rel: c.added for rel, c in changes.items()},
    )

    per_file: dict[str, dict[int, str]] = {}
    for rel, change in changes.items():
        if not source_model.handles(rel) or rel not in files or is_test_file(rel):
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
            "focus": focus,
            "show_test_steps": show_test_steps,
        },
        "files": files,
        "functions": functions,
        "frames": frames,
        "steps": steps,
        "calls": calls,
        "coverage": {
            "files": {rel: {str(k): v for k, v in st.items()} for rel, st in sorted(per_file.items())},
            "summary": change_coverage.summarize(per_file),
            "skipped_test_files": sorted(p for p in changes if is_test_file(p)),
        } if diff_info else None,
    }


def link_calls(calls, functions, frames, files, source_model, read_file, list_files) -> None:
    """Point each call that ran no project code at the project function(s) it stood for
    (calls[].targets), adding those functions (with "ran": false) and their files to the trace."""
    def from_test(c):
        frame = frames.get(str(c["frame"]))
        return frame is None or functions[frame["fn"]].get("test")

    wanted = [c for c in calls if not from_test(c)]
    if not wanted:
        return
    rels = [r for r in list_files() if source_model.handles(r) and not is_test_file(r)]
    wanted = [c for c in wanted if c.get("mock") or c.get("stub") or not c.get("type")
              or _declared_in(c["type"], rels)]
    names = {c["name"] for c in wanted}
    texts: dict[str, str] = {}
    declared = {}
    for rel in rels:
        text = files[rel]["source"] if rel in files else read_file(rel)
        if text is None or not any(call_targets.mentions(text, n) for n in names):
            continue
        texts[rel] = text
        declared[rel] = source_model.declarations(text)
    below = call_targets.subtypes(declared)
    for c in wanted:
        targets = []
        for rel, d in call_targets.resolve(c, declared, below):
            fid = f"{rel}:{d.line}"
            if fid not in functions:
                files.setdefault(rel, {"source": texts[rel]})
                functions[fid] = {"name": d.name, "qualname": d.qualname, "file": rel, "line": d.line, "test": False,
                                  "ran": False, "flowchart": source_model.flowchart(texts[rel], d.line)}
            targets.append(fid)
        if targets:
            c["targets"] = targets
            _name_args(c, functions[targets[0]], declared)


def _name_args(call: dict, target: dict, declared) -> None:
    """A mock's arguments come as arg0, arg1, ... (generated code has no names): use the target's."""
    args = call.get("args") or {}
    if not args or list(args) != [f"arg{i}" for i in range(len(args))]:
        return
    for d in declared.get(target["file"], ()):
        if d.line == target["line"] and len(d.params) == len(args):
            call["args"] = dict(zip(d.params, args.values()))
            return


def _declared_in(type_name: str, rels: list[str]) -> bool:
    """Is this type (or module) declared in one of the project files? "com.x.Outer$Inner" -> com/x/Outer.java."""
    parts = type_name.split("$", 1)[0].split(".")
    stems = {r.rsplit(".", 1)[0] for r in rels}
    for k in range(len(parts), 0, -1):
        path = "/".join(parts[:k])
        if any(s == path or s.endswith("/" + path) for s in stems):
            return True
    return False


_SKIP_DIRS = {".git", "node_modules", "build", "target", ".gradle", ".venv", "venv", "__pycache__", "dist",
              ".wiz", ".idea", ".tox", ".nox", "site-packages", ".mvn"}


def _source_files(root: Path) -> list[str]:
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
        for name in filenames:
            out.append(name if rel_dir == "." else f"{rel_dir}/{name}")
    return sorted(out)


def _span(fn: dict) -> range:
    """Source lines of a function, from its flowchart."""
    nodes = (fn.get("flowchart") or {}).get("nodes", [])
    last = max((n["lines"][1] for n in nodes), default=fn["line"])
    return range(fn["line"], max(last, fn["line"]) + 1)


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
