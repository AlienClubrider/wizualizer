"""Stopping a Java recording for good: the helper, the test JVM it holds suspended, and the build.

A test JVM stopped at a JDWP event ignores SIGTERM, and with Gradle it is a child of the daemon,
not of wiz. So stopping goes: SIGTERM the helper (its shutdown hook ends the test JVM over JDWP),
SIGKILL any JVM still started with this recording's agent address, then give the build a moment
to fail on its own (a Gradle daemon goes back to idle instead of staying busy) before killing its
process group. Every run still going when wiz exits is stopped the same way.
"""

from __future__ import annotations

import atexit
import os
import signal
import subprocess
import threading

HELPER_GRACE_S = 5
BUILD_GRACE_S = 15


class LiveRecording:
    """One recording's processes: the helper, the build (once started), the agent's address."""

    def __init__(self, helper: subprocess.Popen, address: str = ""):
        self.helper = helper
        self.address = address
        self.build: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._stopped = False
        _live.add(self)

    def stop(self) -> None:
        """Stop everything this recording started. Safe to call more than once, from any thread."""
        with self._lock:
            if self._stopped:
                return
            self._stopped = True
        _end(self.helper, HELPER_GRACE_S)
        if self.address:
            for pid in test_jvms(self.address):
                _signal(pid, signal.SIGKILL)
        if self.build is not None:
            try:
                self.build.wait(timeout=BUILD_GRACE_S)
            except subprocess.TimeoutExpired:
                _end_group(self.build)
        self.forget()

    def forget(self) -> None:
        _live.discard(self)


_live: set[LiveRecording] = set()


@atexit.register
def stop_all() -> None:
    """wiz is exiting (Ctrl+C, SIGTERM, an error) with recordings still running: stop them."""
    for run in list(_live):
        run.stop()


def build_session() -> dict:
    """Popen arguments that put the build in its own process group, so all of it can be stopped."""
    return {"start_new_session": True} if os.name == "posix" else {}


def test_jvms(address: str) -> list[int]:
    """PIDs of JVMs started with the debug agent pointed at `address` ("127.0.0.1:PORT").

    Only a process with its own -agentlib:jdwp=... argument matches: the Maven or Gradle command
    line carries the same text inside a -D property and is left alone."""
    if os.name != "posix":
        return []
    try:
        out = subprocess.run(["ps", "ax", "-ww", "-o", "pid=,args="], capture_output=True, text=True,
                             timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return parse_ps(out, address)


def parse_ps(ps_output: str, address: str) -> list[int]:
    needle = f"address={address}"
    pids = []
    for line in ps_output.splitlines():
        parts = line.split()
        if len(parts) < 2 or not parts[0].isdigit():
            continue
        if any(a.startswith("-agentlib:jdwp=") and needle in a.split(",") for a in parts[1:]):
            pids.append(int(parts[0]))
    return [p for p in pids if p != os.getpid()]


def _end(proc: subprocess.Popen, grace: float) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def _end_group(proc: subprocess.Popen) -> None:
    """SIGTERM the build's process group, then SIGKILL whatever is left of it (a forked surefire JVM)."""
    if os.name != "posix":
        _end(proc, HELPER_GRACE_S)
        return
    _killpg(proc.pid, signal.SIGTERM)
    try:
        proc.wait(timeout=HELPER_GRACE_S)
    except subprocess.TimeoutExpired:
        pass
    _killpg(proc.pid, signal.SIGKILL)
    proc.wait()


def _killpg(pgid: int, sig: int) -> None:
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def _signal(pid: int, sig: int) -> None:
    try:
        os.kill(pid, sig)
    except (ProcessLookupError, PermissionError):
        pass
