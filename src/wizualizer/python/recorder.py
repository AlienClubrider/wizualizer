"""Python Recorder: runs one pytest node id under the sys.monitoring tracer."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from ..ports import RecordingFailed, RecordRequest

_SRC = Path(__file__).resolve().parents[2]  # the directory holding the wizualizer package


class PythonRecorder:
    language = "python"
    name = "python-sys.monitoring"

    def record(self, request: RecordRequest) -> dict:
        python = request.interpreter or default_interpreter(request.root)
        with tempfile.TemporaryDirectory(prefix="wiz-") as tmp:
            out = Path(tmp) / "recording.json"
            env = {
                **os.environ,
                "WIZ_OUT": str(out),
                "WIZ_ROOT": str(request.root),
                "WIZ_MAX_STEPS": str(request.max_steps),
                "PYTHONPATH": os.pathsep.join(filter(None, [str(_SRC), os.environ.get("PYTHONPATH")])),
                "PYTHONDONTWRITEBYTECODE": "1",
            }
            cmd = [
                python, "-m", "pytest", request.test_id,
                "-p", "wizualizer.python.pytest_plugin",
                "-p", "no:cacheprovider",
                "-q", "--no-header", "-rN",
            ]
            proc = subprocess.run(cmd, cwd=request.cwd, env=env, capture_output=True, text=True)
            if not out.exists():
                raise RecordingFailed(
                    f"pytest did not produce a recording (exit {proc.returncode}).\n"
                    f"command: {' '.join(cmd)}\n{proc.stdout[-3000:]}{proc.stderr[-3000:]}"
                )
            recording = json.loads(out.read_text(encoding="utf-8"))
        if not recording["steps"]:
            raise RecordingFailed(
                f"no project code ran for {request.test_id!r} (did the node id match a test?)\n"
                f"{proc.stdout[-3000:]}{proc.stderr[-3000:]}"
            )
        recording["pytest_output"] = proc.stdout[-4000:]
        return recording


def default_interpreter(root: Path) -> str:
    """The project's own virtualenv if it has one, else the interpreter running wiz."""
    for candidate in (root / ".venv" / "bin" / "python", root / "venv" / "bin" / "python",
                      root / ".venv" / "Scripts" / "python.exe"):
        if candidate.exists():
            return str(candidate)
    return sys.executable
