"""`wiz run <test> [--base REF]`: record a test and write .wiz/<test>.html + .json."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from .diff import GitError, changed_lines, git_root
from .ports import RecordingFailed, RecordRequest
from .python.recorder import PythonRecorder
from .python.source import PythonSource
from .trace import make_trace
from .viewer.render import render_html


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wiz", description="Record a test and play it back.")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="record one test and write an HTML viewer")
    run.add_argument("test", help="pytest node id, e.g. tests/test_x.py::test_y")
    run.add_argument("--base", help="git ref the PR is based on (e.g. main); marks changed lines")
    run.add_argument("--out", default=".wiz", help="output directory (default: .wiz)")
    run.add_argument("--python", help="interpreter to run pytest with (default: project .venv, else this one)")
    run.add_argument("--max-steps", type=int, default=10_000, help="stop recording after this many steps")
    args = parser.parse_args(argv)

    cwd = Path.cwd()
    root = git_root(cwd) or cwd
    changes, diff_info = {}, None
    if args.base:
        try:
            changes, diff_info = changed_lines(root, args.base)
        except GitError as exc:
            print(f"wiz: {exc}", file=sys.stderr)
            return 2

    request = RecordRequest(args.test, root, cwd, args.max_steps, args.python)
    try:
        trace = make_trace(PythonRecorder(), PythonSource(), request, changes, diff_info)
    except RecordingFailed as exc:
        print(f"wiz: {exc}", file=sys.stderr)
        return 1

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = _slug(args.test)
    (out_dir / f"{stem}.json").write_text(json.dumps(trace, indent=1), encoding="utf-8")
    html = out_dir / f"{stem}.html"
    html.write_text(render_html(trace), encoding="utf-8")

    meta, cov = trace["meta"], trace["coverage"]
    print(f"test {meta['outcome']}: {len(trace['steps'])} steps recorded"
          + (" (truncated)" if meta["truncated"] else ""))
    if cov:
        s = cov["summary"]
        print(f"changed lines: {s['ran']} ran, {s['not_run']} never ran"
              + (f", {s['import_time']} import-time" if s["import_time"] else ""))
    print(html.resolve())
    return 0


def _slug(test_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", test_id.replace("::", "__")).strip("_")[:120]


if __name__ == "__main__":
    raise SystemExit(main())
