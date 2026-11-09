"""`wiz run <test> [--base REF]`: record a test and write .wiz/<test>.html + .json."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from .diff import GitError, changed_lines, git_root
from .ports import RecordingFailed, RecordRequest
from .trace import make_trace
from .viewer.render import render_html


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wiz", description="Record a test and play it back.")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="record one test and write an HTML viewer")
    run.add_argument("test", help="pytest node id (tests/test_x.py::test_y) or Java test (com.example.FooTest#bar)")
    run.add_argument("--base", help="git ref the PR is based on (e.g. main); marks changed lines")
    run.add_argument("--out", default=".wiz", help="output directory (default: .wiz)")
    run.add_argument("--python", help="interpreter to run pytest with (default: project .venv, else this one)")
    run.add_argument("--java-home", help="JDK to record Java tests with (default: $JAVA_HOME, else javac on PATH)")
    run.add_argument("--lang", choices=("python", "java"), help="language of the test (default: guessed from its id)")
    run.add_argument("--test-steps", action="store_true",
                     help="open the viewer with the test's own steps shown (default: only the code under test)")
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

    lang = args.lang or guess_language(args.test)
    try:
        if lang == "java":
            from .java.recorder import JavaRecorder
            from .java.source import JavaSource

            request = RecordRequest(args.test, root, cwd, args.max_steps, args.java_home)
            trace = make_trace(JavaRecorder(), JavaSource(java_home=args.java_home), request, changes, diff_info,
                               show_test_steps=args.test_steps)
        else:
            from .python.recorder import PythonRecorder
            from .python.source import PythonSource

            request = RecordRequest(args.test, root, cwd, args.max_steps, args.python)
            trace = make_trace(PythonRecorder(), PythonSource(), request, changes, diff_info,
                               show_test_steps=args.test_steps)
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
    if meta["focus"]:
        print(f"function under test: {trace['functions'][meta['focus']]['qualname']}")
    if cov:
        s = cov["summary"]
        print(f"changed lines: {s['ran']} ran, {s['not_run']} never ran"
              + (f", {s['import_time']} {'at class load' if meta['language'] == 'java' else 'import-time'}"
                 if s["import_time"] else ""))
    print(html.resolve())
    return 0


def guess_language(test_id: str) -> str:
    """pytest node ids have "::" or ".py"; Java tests are Class#method."""
    if "::" in test_id or test_id.split("::", 1)[0].endswith(".py"):
        return "python"
    return "java" if "#" in test_id else "python"


def _slug(test_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", test_id.replace("::", "__")).strip("_")[:120]


if __name__ == "__main__":
    raise SystemExit(main())
