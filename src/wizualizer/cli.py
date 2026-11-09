"""The `wiz` command.

  wiz                     web app for the project in this directory (same as `wiz serve`)
  wiz record --all        record every flow into the .wiz/ cache (or name the tests)
  wiz list [--changed]    print the flows (only those that run changed lines)
  wiz run <test>          record one test and write .wiz/<test>.html + .json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import webbrowser
from pathlib import Path

from .diff import GitError, changed_lines, git_root
from .ports import RecordingFailed, RecordRequest
from .trace import make_trace
from .viewer.render import render_html


COMMANDS = ("serve", "record", "list", "run")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or (argv[0] not in COMMANDS and argv[0] not in ("-h", "--help", "--version")):
        argv.insert(0, "serve")
    parser = argparse.ArgumentParser(
        prog="wiz", description="Step through what your tests run: flowchart, variables, calls, changed lines.")
    parser.add_argument("--version", action="version", version=f"wiz {_version()}")
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--base", help="git ref the change is based on (default: merge-base with main/master)")
    common.add_argument("--python", help="interpreter to run pytest with (default: project .venv, else this one)")
    common.add_argument("--java-home", help="JDK to record Java tests with (default: $JAVA_HOME, else javac on PATH)")
    common.add_argument("--max-steps", type=int, default=10_000, help="stop recording a test after this many steps")

    serve = sub.add_parser("serve", parents=[common], help="web app listing every test as a flow (the default)")
    serve.add_argument("--port", type=int, help="port on 127.0.0.1 (default: 7357, else any free one)")
    serve.add_argument("--no-open", action="store_true", help="do not open a browser")

    rec = sub.add_parser("record", parents=[common], help="record flows into the .wiz/ cache")
    rec.add_argument("tests", nargs="*", help="test ids (as `wiz list` prints them)")
    rec.add_argument("--all", action="store_true", help="record every flow not cached yet")

    lst = sub.add_parser("list", parents=[common], help="print every flow")
    lst.add_argument("--changed", action="store_true", help="only flows that run changed lines (runs the tests once)")

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
    if args.command == "run":
        return run_one(args)
    try:
        return {"serve": cmd_serve, "record": cmd_record, "list": cmd_list}[args.command](args)
    except RecordingFailed as exc:
        print(f"wiz: {exc}", file=sys.stderr)
        return 1


def run_one(args) -> int:
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


def open_workspace(args):
    """Detect the project in the current directory. Returns (workspace, project) or exits with what is missing."""
    from .languages import JavaLanguage, PythonLanguage
    from .project import detect
    from .workspace import Workspace

    root = Path.cwd()
    project = detect(root, args.python, args.java_home)
    if not project.suites:
        print(f"wiz: {project.problems[0]}", file=sys.stderr)
        raise SystemExit(2)
    languages = {}
    if "python" in project.languages:
        languages["python"] = PythonLanguage(root, args.python)
    if "java" in project.languages:
        languages["java"] = JavaLanguage(root, args.java_home)
    return Workspace(root, languages, base=args.base, max_steps=args.max_steps), project


def cmd_serve(args) -> int:
    from .web.server import App, serve

    ws, project = open_workspace(args)
    for problem in project.problems:
        print(f"wiz: {problem}", file=sys.stderr)
    app = App(ws, {"name": ws.root.name, "kind": project.describe(), "problems": project.problems})
    try:
        server = serve(app, args.port)
    except OSError as exc:
        print(f"wiz: cannot listen on 127.0.0.1:{args.port}: {exc}", file=sys.stderr)
        return 2
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"wiz: {project.describe()} in {ws.root}")
    print(f"wiz: open {url}  (Ctrl+C to stop)", flush=True)
    if not args.no_open and _has_browser():
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def _has_browser() -> bool:
    """Only open a browser where there is a desktop (never a text browser in this terminal)."""
    return sys.platform in ("darwin", "win32") or bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def cmd_record(args) -> int:
    ws, _ = open_workspace(args)
    flows = ws.flows()
    if args.tests:
        known = {f.id: f for f in flows}
        missing = [t for t in args.tests if t not in known]
        if missing:
            print(f"wiz: no such test: {', '.join(missing)} (see `wiz list`)", file=sys.stderr)
            return 2
        flows = [known[t] for t in args.tests]
    elif not args.all:
        print("wiz: name the tests to record, or pass --all", file=sys.stderr)
        return 2
    source_hash, diff = ws.store.source_hash(), ws.diff()
    failed = 0
    for i, flow in enumerate(flows, 1):
        prefix = f"[{i}/{len(flows)}] {flow.id}"
        if ws.cached(flow, source_hash, diff):
            print(f"{prefix}: cached")
            continue
        started = time.time()
        try:
            trace = ws.trace(flow.id)
        except RecordingFailed as exc:
            failed += 1
            print(f"{prefix}: FAILED to record: {str(exc).splitlines()[0] if str(exc) else exc}")
            continue
        print(f"{prefix}: {trace['meta']['outcome']}, {len(trace['steps'])} steps, {time.time() - started:.1f}s",
              flush=True)
    return 1 if failed else 0


def cmd_list(args) -> int:
    ws, _ = open_workspace(args)
    flows = ws.flows()
    if not args.changed:
        for f in flows:
            print(f.id)
        return 0
    diff = ws.diff()
    if not diff.available:
        print(f"wiz: {diff.reason}", file=sys.stderr)
        return 0
    try:
        scan = ws.cached_scan(diff=diff) or ws.scan()
    except RecordingFailed as exc:
        print(f"wiz: {exc}", file=sys.stderr)
        return 1
    for f in flows:
        hit = scan["tests"].get(f.id)
        if hit:
            extra = " (test changed)" if hit["test_lines"] else ""
            print(f"{f.id}\t{hit['lines']} changed line{'' if hit['lines'] == 1 else 's'}{extra}")
    return 0


def _version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("wizualizer")
    except PackageNotFoundError:
        return "dev"


def guess_language(test_id: str) -> str:
    """pytest node ids have "::" or ".py"; Java tests are Class#method."""
    if "::" in test_id or test_id.split("::", 1)[0].endswith(".py"):
        return "python"
    return "java" if "#" in test_id else "python"


def _slug(test_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", test_id.replace("::", "__")).strip("_")[:120]


if __name__ == "__main__":
    raise SystemExit(main())
