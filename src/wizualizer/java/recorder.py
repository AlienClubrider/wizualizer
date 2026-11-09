"""Java Recorder: runs one JUnit test through the project's Maven or Gradle build with a JDI
recorder attached over JDWP (see helper/wiz/Recorder.java)."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from ..ports import RecordingFailed, RecordRequest
from .helper import JdkMissing, find_jdk, helper_command
from .project import Build, JavaTest, ProjectError, SourceIndex, detect_build, gradle_init_script, resolve_test

CONNECT_TIMEOUT_MS = 30 * 60 * 1000  # a first build may download dependencies


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

        with tempfile.TemporaryDirectory(prefix="wiz-") as tmp:
            out = Path(tmp) / "recording.json"
            args = ["record", "--root", str(request.root), "--out", str(out), "--test", test.id,
                    "--max-steps", str(request.max_steps), "--timeout", str(CONNECT_TIMEOUT_MS)]
            for r in index.roots:
                args += ["--source-root", str(r)]
            for p in index.packages:
                args += ["--package", p]
            rec = subprocess.Popen(helper + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            err_chunks: list[str] = []
            drain = threading.Thread(target=lambda: err_chunks.append(rec.stderr.read()), daemon=True)
            drain.start()
            try:
                address = _listening_address(rec)
                started = time.time()
                build_out = _run_build(build, test, address, jdk, Path(tmp))
                try:
                    rec.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    pass
            finally:
                if rec.poll() is None:
                    rec.kill()
                    rec.wait()
            drain.join(timeout=5)
            helper_err = "".join(err_chunks)
            if not out.exists():
                raise RecordingFailed(
                    f"the test JVM never connected to the recorder.\n{build.tool} output:\n{build_out[-4000:]}\n{helper_err[-2000:]}"
                )
            recording = json.loads(out.read_text(encoding="utf-8"))

        if not recording["steps"]:
            raise RecordingFailed(
                f"{test.id} did not run (is the class and method name right?).\n"
                f"{build.tool} output:\n{build_out[-4000:]}"
            )
        report = read_report(build.report(test), test, not_before=started)
        if report:
            recording["test"].update(report)
        recording["test"]["note"] = f"{build.tool} in {_rel(build.root, request.root)}"
        recording["build_output"] = build_out[-4000:]
        return recording


def _listening_address(proc: subprocess.Popen) -> str:
    assert proc.stdout
    line = proc.stdout.readline()
    if not line.startswith("LISTENING "):
        proc.wait(timeout=10)
        raise RecordingFailed(f"the Java helper did not start (exit {proc.returncode}): {line}")
    # Keep draining so the helper never blocks on a full pipe.
    threading.Thread(target=proc.stdout.read, daemon=True).start()
    host, port = line.split()[1].rsplit(":", 1)
    return f"127.0.0.1:{port}"


def _run_build(build: Build, test: JavaTest, address: str, jdk: Path, tmp: Path) -> str:
    agent = f"-agentlib:jdwp=transport=dt_socket,server=n,suspend=y,address={address}"
    init = None
    if build.tool == "gradle":
        init = tmp / "wiz-init.gradle"
        init.write_text(gradle_init_script(agent), encoding="utf-8")
    cmd = build.command(test, agent, init)
    env = {**os.environ, "JAVA_HOME": str(jdk)}
    proc = subprocess.run(cmd, cwd=build.root, env=env, capture_output=True, text=True)
    return f"$ {' '.join(cmd)}\n{proc.stdout}{proc.stderr}"


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
