"""Which lines a PR changed: parse `git diff -U0` output and ask git for it."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

_FILE = re.compile(r"^\+\+\+ (?:b/)?(.*)$")
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


@dataclass
class FileChange:
    added: set[int] = field(default_factory=set)  # new or modified lines, in the new file
    removed_after: set[int] = field(default_factory=set)  # pure deletions happened just after this line


def parse_unified_diff(text: str) -> dict[str, FileChange]:
    """Map new-file path -> changed lines, from a unified diff (any context size)."""
    changes: dict[str, FileChange] = {}
    current: FileChange | None = None
    new_line = 0
    for raw in text.splitlines():
        if raw.startswith("+++ "):
            m = _FILE.match(raw)
            path = m.group(1).strip() if m else ""
            current = None if path == "/dev/null" else changes.setdefault(path, FileChange())
            continue
        if raw.startswith("--- ") or raw.startswith("diff ") or current is None:
            continue
        m = _HUNK.match(raw)
        if m:
            new_line = int(m.group(3))
            new_count = 1 if m.group(4) is None else int(m.group(4))
            if new_count == 0:  # pure deletion: new_line is the line before the gap
                current.removed_after.add(new_line)
            continue
        if raw.startswith("+"):
            current.added.add(new_line)
            new_line += 1
        elif raw.startswith(" "):
            new_line += 1
        # "-" lines and "\\ No newline" do not advance the new file
    return {p: c for p, c in changes.items() if c.added or c.removed_after}


class GitError(RuntimeError):
    pass


def git_root(path: Path) -> Path | None:
    proc = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=path, capture_output=True, text=True)
    return Path(proc.stdout.strip()) if proc.returncode == 0 else None


def changed_lines(repo: Path, base: str) -> tuple[dict[str, FileChange], dict]:
    """Lines changed since the merge-base of `base` and HEAD, including uncommitted work."""
    merge_base = _git(repo, "merge-base", base, "HEAD").strip()
    diff = _git(repo, "diff", "-U0", "--no-color", "--no-ext-diff", "--relative", merge_base)
    # Untracked files are part of the change too.
    untracked = _git(repo, "ls-files", "--others", "--exclude-standard").splitlines()
    changes = parse_unified_diff(diff)
    for rel in untracked:
        try:
            n = len((repo / rel).read_text(encoding="utf-8").splitlines())
        except (OSError, UnicodeDecodeError):
            continue
        changes[rel] = FileChange(added=set(range(1, n + 1)))
    head = _git(repo, "rev-parse", "--short", "HEAD").strip()
    return changes, {"base": base, "merge_base": merge_base[:12], "head": head}


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    if proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


BASE_CANDIDATES = ("main", "master", "origin/main", "origin/master")


def default_base(repo: Path) -> str:
    """The branch a PR is usually based on: main or master (local, else origin). HEAD if neither exists,
    which leaves only uncommitted work as the change."""
    for ref in BASE_CANDIDATES:
        proc = subprocess.run(["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
                              cwd=repo, capture_output=True, text=True)
        if proc.returncode == 0:
            return ref
    return "HEAD"
