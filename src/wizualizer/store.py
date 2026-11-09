"""The .wiz/ cache: recorded traces and change scans, keyed so a stale entry is never served.

A key covers everything a recording depends on: the test id, the project's
source files (content hash) and the diff base. Edit a file, or switch the
base, and the key changes. Each test keeps only its latest entry.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

from .java.project import SKIP_DIRS

# Files that cannot change what a test does: docs and images.
INERT_SUFFIXES = {".md", ".rst", ".txt", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".pdf", ".lock"}
HASH_LIMIT = 4 * 1024 * 1024  # bigger files are keyed by size and mtime instead of content


class Store:
    def __init__(self, root: Path, dirname: str = ".wiz"):
        self.root = root
        self.dir = root / dirname
        self._hashes: dict[str, tuple[int, int, str]] = {}  # rel -> (size, mtime_ns, digest)

    def ensure(self) -> None:
        """Create .wiz/ with a .gitignore of its own, so it never shows up as a change."""
        self.dir.mkdir(parents=True, exist_ok=True)
        ignore = self.dir / ".gitignore"
        if not ignore.exists():
            ignore.write_text("# wizualizer's cache; safe to delete\n*\n", encoding="utf-8")

    # ---- keys ---------------------------------------------------------------------------------

    def source_hash(self) -> str:
        """Content hash of every file a test could read (tracked and untracked, minus ignored)."""
        h = hashlib.sha256()
        for rel in self._files():
            if Path(rel).suffix.lower() in INERT_SUFFIXES:
                continue
            path = self.root / rel
            try:
                st = path.stat()
            except OSError:
                continue
            cached = self._hashes.get(rel)
            if cached and cached[:2] == (st.st_size, st.st_mtime_ns):
                digest = cached[2]
            elif st.st_size > HASH_LIMIT:
                digest = f"{st.st_size}:{st.st_mtime_ns}"
            else:
                try:
                    digest = hashlib.sha256(path.read_bytes()).hexdigest()
                except OSError:
                    continue
                self._hashes[rel] = (st.st_size, st.st_mtime_ns, digest)
            h.update(rel.encode() + b"\0" + digest.encode() + b"\n")
        return h.hexdigest()

    def _files(self) -> list[str]:
        proc = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=self.root, capture_output=True,
        )
        if proc.returncode == 0:
            return sorted(p for p in proc.stdout.decode("utf-8", "replace").split("\0")
                          if p and not _build_output(p))
        out = []
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and d != "__pycache__" and not d.startswith(".")]
            for name in filenames:
                out.append((Path(dirpath) / name).relative_to(self.root).as_posix())
        return sorted(out)

    @staticmethod
    def key(*parts: str) -> str:
        return hashlib.sha256("\0".join(parts).encode()).hexdigest()[:16]

    # ---- traces -------------------------------------------------------------------------------

    def trace_path(self, test_id: str, key: str) -> Path:
        return self.dir / "flows" / f"{slug(test_id)}-{key}.json"

    def load_trace(self, test_id: str, key: str) -> dict | None:
        return _load(self.trace_path(test_id, key))

    def save_trace(self, test_id: str, key: str, trace: dict) -> Path:
        path = self.trace_path(test_id, key)
        _write(path, trace)
        index = self.index()
        old = index.get(test_id)
        if old and old["key"] != key:
            self.trace_path(test_id, old["key"]).unlink(missing_ok=True)
        meta, cov = trace["meta"], trace.get("coverage")
        index[test_id] = {
            "key": key,
            "outcome": meta["outcome"],
            "steps": len(trace["steps"]),
            "truncated": meta["truncated"],
            "coverage": cov["summary"] if cov else None,
        }
        _write(self.dir / "flows" / "index.json", index)
        return path

    def index(self) -> dict[str, dict]:
        """test id -> {key, outcome, steps, truncated, coverage} of its latest recording."""
        return _load(self.dir / "flows" / "index.json") or {}

    # ---- change scans -------------------------------------------------------------------------

    def load_scan(self, key: str) -> dict | None:
        return _load(self.dir / f"changes-{key}.json")

    def save_scan(self, key: str, scan: dict) -> None:
        for old in self.dir.glob("changes-*.json"):
            old.unlink(missing_ok=True)
        _write(self.dir / f"changes-{key}.json", scan)


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def _build_output(rel: str) -> bool:
    """Build and cache output a project may not ignore (target/, build/, __pycache__/)."""
    *dirs, name = rel.split("/")
    return name.endswith(".pyc") or any(d in SKIP_DIRS or d == "__pycache__" for d in dirs)


def slug(test_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", test_id.replace("::", "__")).strip("_")[:120]


def _load(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
