"""Records what project code does, step by step, using sys.monitoring (PEP 669).

Stdlib only: this module runs inside the process under test. It produces the
`steps` / `frames` / `functions` part of a trace (see docs/trace-format.md).
"""

from __future__ import annotations

import os
import sys
import threading
from typing import Callable

from .safe_repr import safe_repr

M = sys.monitoring
E = M.events
MAX_LOCALS = 40
_EVENTS = (
    E.PY_START | E.PY_RESUME | E.PY_RETURN | E.PY_YIELD | E.LINE | E.RAISE | E.PY_UNWIND
)


def project_file_filter(root: str, extra_excludes: tuple[str, ...] = ()) -> Callable[[str], bool]:
    """True for files under `root` that are not installed packages or virtualenvs."""
    root = os.path.realpath(root)
    excluded = [os.path.realpath(p) for p in (sys.prefix, sys.base_prefix, *extra_excludes)]
    excluded = [p for p in excluded if not root.startswith(p + os.sep) and p != root]
    skip_parts = {"site-packages", "dist-packages", ".venv", "venv", ".tox", "node_modules", ".nox"}
    cache: dict[str, bool] = {}

    def is_project(filename: str) -> bool:
        hit = cache.get(filename)
        if hit is None:
            path = os.path.realpath(filename) if not filename.startswith("<") else ""
            hit = (
                bool(path)
                and path.startswith(root + os.sep)
                and not any(path.startswith(p + os.sep) for p in excluded)
                and not (set(path[len(root) :].split(os.sep)) & skip_parts)
            )
            cache[filename] = hit
        return hit

    return is_project


_INLINE = frozenset({"<genexpr>", "<lambda>", "<listcomp>", "<setcomp>", "<dictcomp>"})


class Tracer:
    """Collects call / line / return / exception events for project code on one thread."""

    def __init__(self, root: str, is_project: Callable[[str], bool], max_steps: int = 10_000):
        self.root = os.path.realpath(root)
        self.is_project = is_project
        self.max_steps = max_steps
        self.steps: list[dict] = []
        self.frames: dict[int, dict] = {}
        self.functions: dict[str, dict] = {}
        self.truncated = False
        self._tool: int | None = None
        self._thread: int | None = None
        self._live: dict[int, int] = {}  # id(frame) -> frame id
        self._stack: list[int] = []
        self._next_fid = 1
        self._instances: dict[int, str] = {}
        self._class_counts: dict[str, int] = {}
        self._keepalive: list[object] = []  # stops id() reuse for actors
        self._paths: dict[str, str] = {}

    # lifecycle -------------------------------------------------------------
    def start(self) -> None:
        self._tool = _claim_tool_id()
        self._thread = threading.get_ident()
        M.restart_events()  # re-arm locations an earlier run disabled
        cb = M.register_callback
        cb(self._tool, E.PY_START, self._on_start)
        cb(self._tool, E.PY_RESUME, self._on_resume)
        cb(self._tool, E.PY_RETURN, self._on_return)
        cb(self._tool, E.PY_YIELD, self._on_yield)
        cb(self._tool, E.LINE, self._on_line)
        cb(self._tool, E.RAISE, self._on_raise)
        cb(self._tool, E.PY_UNWIND, self._on_unwind)
        M.set_events(self._tool, _EVENTS)

    def stop(self) -> None:
        if self._tool is None:
            return
        M.set_events(self._tool, 0)
        for event in (E.PY_START, E.PY_RESUME, E.PY_RETURN, E.PY_YIELD, E.LINE, E.RAISE, E.PY_UNWIND):
            M.register_callback(self._tool, event, None)
        M.free_tool_id(self._tool)
        self._tool = None
        self._keepalive.clear()

    def result(self) -> dict:
        return {
            "steps": self.steps,
            "frames": {str(k): v for k, v in self.frames.items()},
            "functions": self.functions,
            "truncated": self.truncated,
        }

    # callbacks -------------------------------------------------------------
    def _on_start(self, code, offset):
        if code.co_name in _INLINE or not self.is_project(code.co_filename):
            return M.DISABLE  # comprehensions and lambdas are folded into their caller
        if not self._mine():
            return None
        frame = sys._getframe(1)
        fid = self._new_frame(frame, code)
        self._emit({"kind": "call", "frame": fid, "line": frame.f_lineno, "args": _locals(frame)})
        return None

    def _on_resume(self, code, offset):
        if code.co_name in _INLINE or not self.is_project(code.co_filename):
            return M.DISABLE  # comprehensions and lambdas are folded into their caller
        if not self._mine():
            return None
        frame = sys._getframe(1)
        fid = self._live.get(id(frame))
        if fid is None:
            fid = self._new_frame(frame, code)
        else:
            self._stack.append(fid)
        self._emit({"kind": "call", "frame": fid, "line": frame.f_lineno, "resume": True, "args": _locals(frame)})
        return None

    def _on_line(self, code, line):
        if code.co_name in _INLINE or not self.is_project(code.co_filename):
            return M.DISABLE  # comprehensions and lambdas are folded into their caller
        if not self._mine():
            return None
        frame = sys._getframe(1)
        fid = self._live.get(id(frame)) or self._new_frame(frame, code)
        self._emit({"kind": "line", "frame": fid, "line": line, "locals": _locals(frame)})
        return None

    def _on_return(self, code, offset, value):
        if code.co_name in _INLINE or not self.is_project(code.co_filename):
            return M.DISABLE  # comprehensions and lambdas are folded into their caller
        if self._mine():
            self._leave(sys._getframe(1), {"kind": "return", "value": safe_repr(value)}, forget=True)
        return None

    def _on_yield(self, code, offset, value):
        if code.co_name in _INLINE or not self.is_project(code.co_filename):
            return M.DISABLE  # comprehensions and lambdas are folded into their caller
        if self._mine():
            self._leave(sys._getframe(1), {"kind": "return", "value": safe_repr(value), "yield": True}, forget=False)
        return None

    def _on_raise(self, code, offset, exc):
        # Non-local event: returning DISABLE is not allowed here.
        if self.is_project(code.co_filename) and self._mine():
            frame = sys._getframe(1)
            fid = self._live.get(id(frame))
            if fid is not None:
                self._emit({"kind": "exception", "frame": fid, "line": frame.f_lineno, **_exc(exc)})
        return None

    def _on_unwind(self, code, offset, exc):
        if self.is_project(code.co_filename) and self._mine():
            self._leave(sys._getframe(1), {"kind": "return", "raised": True, **_exc(exc)}, forget=True)
        return None

    # internals -------------------------------------------------------------
    def _mine(self) -> bool:
        return threading.get_ident() == self._thread and not self.truncated

    def _new_frame(self, frame, code) -> int:
        fid = self._next_fid
        self._next_fid += 1
        fn = self._function_id(code)
        self.frames[fid] = {
            "fn": fn,
            "parent": self._stack[-1] if self._stack else None,
            "actor": self._actor(frame, code),
        }
        self._live[id(frame)] = fid
        self._stack.append(fid)
        return fid

    def _leave(self, frame, step: dict, forget: bool) -> None:
        fid = self._live.get(id(frame))
        if fid is None:
            return
        step = {"kind": step.pop("kind"), "frame": fid, "line": frame.f_lineno, **step}
        self._emit(step)
        if fid in self._stack:
            del self._stack[self._stack.index(fid) :]
        if forget:
            self._live.pop(id(frame), None)

    def _emit(self, step: dict) -> None:
        if len(self.steps) >= self.max_steps:
            self.truncated = True
            if self._tool is not None:
                M.set_events(self._tool, 0)
            return
        self.steps.append(step)

    def _rel(self, filename: str) -> str:
        rel = self._paths.get(filename)
        if rel is None:
            rel = os.path.relpath(os.path.realpath(filename), self.root).replace(os.sep, "/")
            self._paths[filename] = rel
        return rel

    def _function_id(self, code) -> str:
        file = self._rel(code.co_filename)
        fn = f"{file}:{code.co_firstlineno}"
        if fn not in self.functions:
            self.functions[fn] = {
                "name": code.co_name,
                "qualname": getattr(code, "co_qualname", code.co_name),
                "file": file,
                "line": code.co_firstlineno,
            }
        return fn

    def _actor(self, frame, code) -> str:
        """Who is acting in the sequence diagram: an object (Cart#1), a class, or a module."""
        if code.co_argcount and code.co_varnames[0] in ("self", "cls"):
            target = frame.f_locals.get(code.co_varnames[0])
            if isinstance(target, type):
                return target.__name__
            if target is not None:
                return self._instance_label(target)
        stem = os.path.splitext(os.path.basename(code.co_filename))[0]
        return stem

    def _instance_label(self, obj: object) -> str:
        label = self._instances.get(id(obj))
        if label is None:
            cls = type(obj).__name__
            n = self._class_counts.get(cls, 0) + 1
            self._class_counts[cls] = n
            label = f"{cls}#{n}"
            self._instances[id(obj)] = label
            self._keepalive.append(obj)
        return label


def _locals(frame) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        items = list(frame.f_locals.items())
    except Exception:  # noqa: BLE001
        return out
    for name, value in items:
        if name.startswith(("@", "__")):
            continue  # pytest assertion-rewrite temporaries, dunders
        if len(out) >= MAX_LOCALS:
            out["…"] = f"+{len(items) - MAX_LOCALS} more"
            break
        out[name] = safe_repr(value)
    return out


def _exc(exc: BaseException) -> dict:
    try:
        message = str(exc)
    except Exception:  # noqa: BLE001
        message = "<str() failed>"
    return {"type": type(exc).__name__, "message": message[:200]}


def _claim_tool_id() -> int:
    for tool in (M.DEBUGGER_ID, 3, 4, M.PROFILER_ID):
        if M.get_tool(tool) is None:
            M.use_tool_id(tool, "wizualizer")
            return tool
    raise RuntimeError("no free sys.monitoring tool id (another debugger or profiler is attached)")
