"""Python change probe: run the whole pytest suite once and note which test runs which watched lines."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

from ..ports import RecordingFailed
from .recorder import _SRC


def probe(root: Path, python: str, watch: dict[str, list[int]]) -> dict[str, dict]:
    """test node id -> {"lines": {file: [lines]}, "starts": {file: [lines]}} (see probe_plugin.py)."""
    if not watch:
        return {}
    with tempfile.TemporaryDirectory(prefix="wiz-") as tmp:
        spec, out = Path(tmp) / "watch.json", Path(tmp) / "hits.json"
        spec.write_text(json.dumps({"root": str(root), "watch": watch}), encoding="utf-8")
        env = {
            **os.environ,
            "WIZ_PROBE": str(spec),
            "WIZ_PROBE_OUT": str(out),
            "PYTHONPATH": os.pathsep.join(filter(None, [str(_SRC), os.environ.get("PYTHONPATH")])),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        cmd = [python, "-m", "pytest", "-p", "wizualizer.python.probe_plugin", "-p", "no:cacheprovider",
               "-q", "--no-header", "-rN"]
        proc = subprocess.run(cmd, cwd=root, env=env, capture_output=True, text=True)
        if not out.exists():
            raise RecordingFailed(f"the test run for changed lines did not finish (exit {proc.returncode}).\n"
                                  f"{(proc.stdout + proc.stderr)[-3000:]}")
        return json.loads(out.read_text(encoding="utf-8"))
