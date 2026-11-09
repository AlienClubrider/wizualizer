"""The `wiz` use cases for one project: list its flows, record one, find the ones a change touches.

Ties discovery, the recorders, the diff and the .wiz/ cache together. The web
app and the CLI are thin adapters over this.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path

from . import impact
from .diff import FileChange, GitError, changed_lines, default_base, git_root
from .discovery import Flow
from .languages import Language
from .ports import RecordingFailed, RecordRequest
from .store import Store
from .trace import make_trace


@dataclass
class DiffState:
    available: bool
    reason: str = ""  # why not, in plain words
    base: str | None = None
    merge_base: str | None = None
    changes: dict[str, FileChange] = field(default_factory=dict)  # only files a language handles
    info: dict | None = None  # trace meta.diff

    @property
    def files(self) -> int:
        return len(self.changes)

    @property
    def lines(self) -> int:
        return sum(len(c.added) for c in self.changes.values())

    def to_json(self) -> dict:
        return {"available": self.available, "reason": self.reason, "base": self.base,
                "merge_base": self.merge_base, "files": self.files, "lines": self.lines}


class Workspace:
    def __init__(self, root: Path, languages: dict[str, Language], base: str | None = None,
                 max_steps: int = 10_000, store: Store | None = None):
        self.root = root
        self.languages = languages
        self.base = base
        self.max_steps = max_steps
        self.store = store or Store(root)
        self.store.ensure()
        self._flows: tuple[str, list[Flow]] | None = None  # (source hash, flows)
        self._lock = threading.Lock()

    # ---- flows ------------------------------------------------------------------------------

    def flows(self, source_hash: str | None = None) -> list[Flow]:
        """Every test in the project, rediscovered when the sources change."""
        source_hash = source_hash or self.store.source_hash()
        with self._lock:
            if self._flows and self._flows[0] == source_hash:
                return self._flows[1]
            found: list[Flow] = []
            for lang in self.languages.values():
                found.extend(lang.discover())
            self._flows = (source_hash, found)
            return found

    def flow(self, flow_id: str) -> Flow:
        for f in self.flows():
            if f.id == flow_id:
                return f
        raise KeyError(flow_id)

    # ---- diff -------------------------------------------------------------------------------

    def diff(self) -> DiffState:
        if git_root(self.root) is None:
            return DiffState(False, "not a git repository")
        base = self.base or default_base(self.root)
        try:
            changes, info = changed_lines(self.root, base)
        except GitError as exc:
            return DiffState(False, f"cannot diff against {base}: {exc}", base)
        handled = {rel: c for rel, c in changes.items()
                   if c.added and any(lang.source().handles(rel) for lang in self.languages.values())}
        if not handled:
            against = "uncommitted" if base == "HEAD" else f"against {base} ({info['merge_base'][:7]}) or uncommitted"
            return DiffState(False, f"no code changes {against}", base, info["merge_base"], info=info)
        return DiffState(True, "", base, info["merge_base"], handled, info)

    # ---- recordings -------------------------------------------------------------------------

    def trace_key(self, flow: Flow, source_hash: str, diff: DiffState) -> str:
        return Store.key(flow.id, source_hash, diff.base or "", diff.merge_base or "", str(self.max_steps))

    def cached(self, flow: Flow, source_hash: str, diff: DiffState) -> dict | None:
        """Summary of the cached recording of this flow, if it is current."""
        entry = self.store.index().get(flow.id)
        return entry if entry and entry["key"] == self.trace_key(flow, source_hash, diff) else None

    def trace(self, flow_id: str, record: bool = True) -> dict | None:
        """The flow's trace from the cache, else recorded now (None if not cached and record=False)."""
        flow = self.flow(flow_id)
        source_hash, diff = self.store.source_hash(), self.diff()
        key = self.trace_key(flow, source_hash, diff)
        trace = self.store.load_trace(flow.id, key)
        if trace is not None or not record:
            return trace
        lang = self.languages[flow.language]
        request = RecordRequest(flow.id, self.root, self.root, self.max_steps, lang.interpreter())
        trace = make_trace(lang.recorder(), lang.source(), request,
                           diff.changes, diff.info if diff.available else None)
        self.store.save_trace(flow.id, key, trace)
        return trace

    # ---- only changes -----------------------------------------------------------------------

    def scan_key(self, source_hash: str, diff: DiffState) -> str:
        return Store.key("changes", source_hash, diff.base or "", diff.merge_base or "")

    def cached_scan(self, source_hash: str | None = None, diff: DiffState | None = None) -> dict | None:
        diff = diff or self.diff()
        if not diff.available:
            return None
        return self.store.load_scan(self.scan_key(source_hash or self.store.source_hash(), diff))

    def scan(self) -> dict:
        """Run the test suites once, watching the changed lines: test id -> {lines, test_lines, files}."""
        source_hash, diff = self.store.source_hash(), self.diff()
        if not diff.available:
            raise RecordingFailed(f"nothing to compare: {diff.reason}")
        flows = self.flows(source_hash)
        tests: dict[str, dict] = {}
        for name, lang in self.languages.items():
            source = lang.source()
            statements = {}
            for rel in diff.changes:
                if source.handles(rel):
                    text = _read(self.root / rel)
                    if text is not None:
                        statements[rel] = source.statements(text)
            watch = impact.watch_lines(diff.changes, statements)
            mine = [f for f in flows if f.language == name]
            if watch and mine:
                hits = lang.probe(mine, watch)
                tests.update(impact.summarize(hits, diff.changes, statements))
        scan = {"tests": tests, "diff": diff.to_json()}
        self.store.save_scan(self.scan_key(source_hash, diff), scan)
        return scan


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
