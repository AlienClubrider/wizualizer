"""Java Recorder: runs one JUnit test through the project's Maven or Gradle build with a JDI
recorder attached over JDWP (see helper/wiz/Recorder.java).

The recorder waits for as long as the build runs: a big multi-project build can configure and
compile for many minutes before the test JVM starts. When the build ends without the test JVM
connecting, diagnose.explain says why in one line, with a hint and the full build output.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..ports import RecordingFailed, RecordRequest
from . import containers, diagnose
from .helper import JdkMissing, find_jdk, helper_command
from .project import (Build, JavaTest, ProjectError, SourceIndex, detect_build, gradle_init_script, gradle_task,
                      resolve_test)

HELPER_EXIT_WAIT_S = 600  # after the test JVM is gone, the helper still writes the recording
LOG_LIMIT = 2_000_000  # characters of build output kept for the failure report


class JavaRecorder:
    language = "java"
    name = "java-jdi"

    def record(self, request: RecordRequest) -> dict:
        try:
            jdk = find_jdk(request.interpreter)
            index = SourceIndex.scan(request.root)
            test = resolve_test(request.test_id, index)
            build = detect_build(test.file, request.root)
            helper = helper_command(jdk)
        except (ProjectError, JdkMissing) as exc:
            raise RecordingFailed(str(exc)) from exc
        if containers.uses_testcontainers(test.file, index.classes) and not containers.docker_reachable():
            d = diagnose.docker_needed()
            raise RecordingFailed(f"{d.reason}.\n{d.hint}", d.reason, d.hint)
        task = test_task(test)

        with tempfile.TemporaryDirectory(prefix="wiz-") as tmp:
            out = Path(tmp) / "recording.json"
            args = ["record", "--root", str(request.root), "--out", str(out), "--test", test.id,
                    "--max-steps", str(request.max_steps), "--timeout", "0"]
            for r in index.roots:
                args += ["--source-root", str(r)]
            for p in index.packages:
                args += ["--package", p]
            run = attach_and_build(helper + args, build,
                                   lambda agent, init: build.command(test, agent, init, task), jdk, request.progress)
            if not out.exists():
                raise failure(build, run, test.id, task)
            recording = json.loads(out.read_text(encoding="utf-8"))

        if not recording["steps"]:
            raise RecordingFailed(
                f"{test.id} did not run (is the class and method name right?).\n"
                f"{build.tool} output:\n{run.output[-4000:]}", log=run.output)
        report = read_report(build.report(test, task), test, not_before=run.started)
        if report:
            recording["test"].update(report)
        recording["test"]["note"] = f"{build.tool} in {_rel(build.root, request.root)}"
        recording["build_output"] = run.output[-4000:]
        return recording


def test_task(test: JavaTest) -> str:
    """The Gradle Test task to run: WIZ_GRADLE_TEST_TASK if set, else the one of the test's source set."""
    return os.environ.get("WIZ_GRADLE_TEST_TASK") or gradle_task(test.file)


def extra_args(tool: str) -> list[str]:
    """Arguments the user adds to every build wiz runs: WIZ_GRADLE_ARGS or WIZ_MAVEN_ARGS."""
    return shlex.split(os.environ.get("WIZ_GRADLE_ARGS" if tool == "gradle" else "WIZ_MAVEN_ARGS", ""))


def failure(build: Build, run: "BuildRun", test: str, task: str) -> RecordingFailed:
    """The test JVM never connected: why, what to do, and the whole build output."""
    d = diagnose.explain(build.tool, run.output, run.exit_code, test, task)
    if run.helper_err.strip() and not run.output.strip():
        d = diagnose.Diagnosis("the Java recorder stopped before the build started", run.helper_err.strip()[-500:])
    shown = run.output if len(run.output) <= 12_000 else "…\n" + run.output[-12_000:]
    message = (f"Recording failed: {d.reason}.\nFix: {d.hint}\n\n"
               f"{build.tool} output (exit code {run.exit_code}):\n{shown}")
    if run.helper_err.strip():
        message += f"\nrecorder:\n{run.helper_err[-2000:]}"
    return RecordingFailed(message, d.reason, d.hint, run.output[-LOG_LIMIT:])


@dataclass
class BuildRun:
    output: str  # "$ command" then everything the build printed (stdout and stderr, interleaved)
    exit_code: int | None
    connected: bool  # the test JVM connected to the recorder
    helper_err: str
    started: float  # when the build started (time.time())


def _listening_address(proc: subprocess.Popen) -> str:
    assert proc.stdout
    line = proc.stdout.readline()
    if not line.startswith("LISTENING "):
        proc.wait(timeout=10)
        raise RecordingFailed(f"the Java helper did not start (exit {proc.returncode}): {line}")
    host, port = line.split()[1].rsplit(":", 1)
    return f"127.0.0.1:{port}"


def attach_and_build(helper_cmd: list[str], build: Build, command, jdk: Path,
                     progress: Callable[[str], None] | None = None) -> BuildRun:
    """Start the helper (it listens for the test JVM), then run the build with the debug agent.

    command(agent, init_script) -> the build command line. Waits for the build to finish, however
    long it takes; the helper is stopped then unless the test JVM connected to it.
    """
    rec = subprocess.Popen(helper_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    err_chunks: list[str] = []
    drain = threading.Thread(target=lambda: err_chunks.append(rec.stderr.read()), daemon=True)
    drain.start()
    connected = threading.Event()
    note = progress or (lambda _msg: None)
    try:
        address = _listening_address(rec)

        def watch_helper():
            for line in rec.stdout:  # keeps draining so the helper never blocks on a full pipe
                if line.startswith("CONNECTED"):
                    connected.set()
                    note("the test JVM connected: recording the test")

        threading.Thread(target=watch_helper, daemon=True).start()
        agent = f"-agentlib:jdwp=transport=dt_socket,server=n,suspend=y,address={address}"
        init = gradle_init_script() if build.tool == "gradle" else None
        started = time.time()
        output, exit_code = _run_build(build, command(agent, init) + extra_args(build.tool), jdk, note)
        if connected.is_set():
            try:
                rec.wait(timeout=HELPER_EXIT_WAIT_S)
            except subprocess.TimeoutExpired:
                pass
    finally:
        if rec.poll() is None:
            rec.kill()
            rec.wait()
    drain.join(timeout=5)
    return BuildRun(output, exit_code, connected.is_set(), "".join(err_chunks), started)


def _run_build(build: Build, cmd: list[str], jdk: Path, note: Callable[[str], None]) -> tuple[str, int | None]:
    env = {**os.environ, "JAVA_HOME": str(jdk)}
    note(f"{build.tool}: starting")
    proc = subprocess.Popen(cmd, cwd=build.root, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, text=True, errors="replace", bufsize=1)
    lines = [f"$ {shlex.join(cmd)}\n"]
    size = 0
    assert proc.stdout
    for line in proc.stdout:
        lines.append(line)
        size += len(line)
        if size > 2 * LOG_LIMIT:  # keep memory bounded on a chatty build
            text = "".join(lines)[-LOG_LIMIT:]
            lines, size = [text], len(text)
        msg = diagnose.milestone(build.tool, line)
        if msg:
            note(msg)
    return "".join(lines), proc.wait()


def read_report(path: Path, test: JavaTest, not_before: float = 0.0) -> dict | None:
    """Outcome of one test method from a JUnit XML report (Surefire and Gradle write the same format)."""
    try:
        if path.stat().st_mtime < not_before - 1:
            return None  # stale report from an earlier run
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return None
    for case in root.iter("testcase"):
        if case.get("name", "").split("(", 1)[0] != test.method:
            continue
        for tag, outcome in (("failure", "failed"), ("error", "failed"), ("skipped", "skipped")):
            el = case.find(tag)
            if el is not None:
                message = el.get("message") or (el.text or "").strip().split("\n", 1)[0]
                return {"outcome": outcome, "message": message[:500]}
        seconds = case.get("time")
        out = {"outcome": "passed", "message": ""}
        if seconds:
            try:
                out["duration_ms"] = round(float(seconds) * 1000, 1)
            except ValueError:
                pass
        return out
    return None


def _rel(path: Path, root: Path) -> str:
    try:
        rel = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)
    return rel or "."
