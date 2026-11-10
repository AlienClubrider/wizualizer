"""Records what project code does, step by step, using sys.monitoring (PEP 669).

Stdlib only: this module runs inside the process under test. It produces the
`steps` / `frames` / `functions` part of a trace (see docs/trace-format.md).
"""

from __future__ import annotations

import ast
import linecache
import os
import sys
import threading
from typing import Callable

from ..focus import is_test_file
from .safe_repr import safe_repr

M = sys.monitoring
E = M.events
MAX_LOCALS = 40
MAX_CALLS = 3000
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
        # Calls from project code into code that is not recorded as a frame: mocks, stubs, libraries.
        self.calls: list[dict] = []
        self._out_tool: int | None = None  # a second tool: local events on the called code only
        self._out_codes: set = set()
        self._pending: dict[int, dict] = {}  # id(caller frame) -> its call in progress
        self._site_names: dict[tuple, str | None] = {}

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
        cb(self._tool, E.CALL, self._on_call)
        M.set_events(self._tool, _EVENTS | E.CALL)
        try:
            self._out_tool = _claim_tool_id(exclude=self._tool)
        except RuntimeError:
            self._out_tool = None  # no second tool id: calls are listed without arguments or results
        if self._out_tool is not None:
            M.register_callback(self._out_tool, E.PY_START, self._on_out_start)
            M.register_callback(self._out_tool, E.PY_RETURN, self._on_out_return)

    def stop(self) -> None:
        if self._tool is None:
            return
        M.set_events(self._tool, 0)
        for event in (E.PY_START, E.PY_RESUME, E.PY_RETURN, E.PY_YIELD, E.LINE, E.RAISE, E.PY_UNWIND, E.CALL):
            M.register_callback(self._tool, event, None)
        M.free_tool_id(self._tool)
        self._tool = None
        if self._out_tool is not None:
            for code in self._out_codes:
                M.set_local_events(self._out_tool, code, 0)
            M.register_callback(self._out_tool, E.PY_START, None)
            M.register_callback(self._out_tool, E.PY_RETURN, None)
            M.free_tool_id(self._out_tool)
            self._out_tool = None
        self._out_codes.clear()
        self._pending.clear()
        self._keepalive.clear()

    def result(self) -> dict:
        return {
            "steps": self.steps,
            "frames": {str(k): v for k, v in self.frames.items()},
            "functions": self.functions,
            "calls": [{k: v for k, v in c.items() if not k.startswith("_")} for c in self.calls],
            "truncated": self.truncated,
        }

    # callbacks -------------------------------------------------------------
    def _on_start(self, code, offset):
        if code.co_name in _INLINE or not self.is_project(code.co_filename):
            return M.DISABLE  # comprehensions and lambdas are folded into their caller
        if not self._mine():
            return None
        frame = sys._getframe(1)
        self._passed_on(frame, code)
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
        self._settle(frame)
        fid = self._live.get(id(frame)) or self._new_frame(frame, code)
        self._emit({"kind": "line", "frame": fid, "line": line, "locals": _locals(frame)})
        return None

    def _on_return(self, code, offset, value):
        if code.co_name in _INLINE or not self.is_project(code.co_filename):
            return M.DISABLE  # comprehensions and lambdas are folded into their caller
        if self._mine():
            frame = sys._getframe(1)
            self._settle(frame)
            self._leave(frame, {"kind": "return", "value": safe_repr(value)}, forget=True)
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
            self._settle(frame, exc)
            if fid is not None:
                self._emit({"kind": "exception", "frame": fid, "line": frame.f_lineno, **_exc(exc)})
        return None

    def _on_unwind(self, code, offset, exc):
        if self.is_project(code.co_filename) and self._mine():
            frame = sys._getframe(1)
            self._settle(frame, exc)
            self._leave(frame, {"kind": "return", "raised": True, **_exc(exc)}, forget=True)
        return None

    # calls into code that is not recorded ---------------------------------------
    def _on_call(self, code, offset, callable_, arg0):
        if code.co_name in _INLINE or not self.is_project(code.co_filename):
            return M.DISABLE
        if not self._mine() or len(self.calls) >= MAX_CALLS:
            return None
        frame = sys._getframe(1)
        fid = self._live.get(id(frame))
        if fid is None:
            return None
        self._settle(frame)
        if isinstance(callable_, type):
            init = getattr(getattr(callable_, "__init__", None), "__code__", None)
            if init is not None and self.is_project(init.co_filename):
                return None  # a project class: its __init__ gets a frame
        target = _code_of(callable_)
        mock = _is_mock(callable_)
        stub = False
        if target is not None and not mock and self.is_project(target.co_filename):
            # A test function or lambda called from code under test stands in for it (monkeypatch).
            stub = is_test_file(self._rel(target.co_filename)) and not is_test_file(self._rel(code.co_filename))
            if not stub:
                return None  # project code: a frame of its own, or folded into this one (lambdas, genexprs)
        name = self._site_name(code, offset) or getattr(callable_, "__name__", None) or type(callable_).__name__
        owner = None if stub else _owner(callable_)
        args = {} if arg0 is M.MISSING else {"arg0": safe_repr(arg0)}
        objclass = getattr(callable_, "__objclass__", None)  # a C method loaded off its object: arg0 is self
        if isinstance(objclass, type) and arg0 is not M.MISSING and isinstance(arg0, objclass):
            owner, args = f"{objclass.__module__}.{objclass.__qualname__}", {}
        call = {
            "frame": fid, "line": frame.f_lineno, "after": len(self.steps) - 1,
            "name": name, "type": owner, "args": args,
        }
        if mock:
            call["mock"] = True
        elif stub:
            call["stub"] = True
        if self._out_tool is not None and target is not None and not isinstance(callable_, type):
            call["_code"] = target
            if target not in self._out_codes:
                self._out_codes.add(target)
                M.set_local_events(self._out_tool, target, E.PY_START | E.PY_RETURN)
        self.calls.append(call)
        self._pending[id(frame)] = call
        return None

    def _on_out_start(self, code, offset):
        frame = sys._getframe(1)
        call = self._pending.get(id(frame.f_back)) if frame.f_back is not None else None
        if call is not None and call.get("_code") is code and "_started" not in call:
            call["_started"] = True
            call["args"] = _call_args(frame, code)
        return None

    def _on_out_return(self, code, offset, value):
        frame = sys._getframe(1)
        if frame.f_back is None:
            return None
        call = self._pending.get(id(frame.f_back))
        if call is not None and call.get("_code") is code and call.get("_started"):
            call["value"] = safe_repr(value)
            del self._pending[id(frame.f_back)]
        return None

    def _settle(self, frame, exc: BaseException | None = None) -> None:
        """The caller moved on: its call in progress is over (a C function, or it raised)."""
        call = self._pending.pop(id(frame), None)
        if call is not None and exc is not None and "value" not in call:
            e = _exc(exc)
            call.update({"raised": True, "error": f"{e['type']}: {e['message']}" if e["message"] else e["type"]})

    def _passed_on(self, frame, code) -> None:
        """A project function started under a call in progress: a wrapper passed the call on."""
        back = frame.f_back
        while back is not None:
            call = self._pending.get(id(back))
            if call is not None:
                if call["name"] == code.co_name and not call.get("mock"):
                    self.calls.remove(call)
                    del self._pending[id(back)]
                return
            if self._live.get(id(back)) is not None:
                return
            back = back.f_back

    def _site_name(self, code, offset) -> str | None:
        """The called name as the source spells it: `repo.save(x)` -> "save"."""
        key = (code, offset)
        if key not in self._site_names:
            self._site_names[key] = _called_name(code, offset)
        return self._site_names[key]

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


def _code_of(callable_):
    """The Python code a call runs first, or None (C functions, classes)."""
    if isinstance(callable_, type):
        return None
    func = getattr(callable_, "__func__", callable_)
    code = getattr(func, "__code__", None)
    if code is None:
        code = getattr(getattr(type(callable_), "__call__", None), "__code__", None)
    return code


def _is_mock(callable_) -> bool:
    mod = sys.modules.get("unittest.mock")
    if mod is None:
        return False
    try:
        return isinstance(callable_, mod.NonCallableMock) or isinstance(getattr(callable_, "mock", None), mod.NonCallableMock)
    except Exception:  # noqa: BLE001
        return False


def _owner(callable_) -> str | None:
    """Qualified name of the class a called method belongs to (the spec's, for a mock), else its module."""
    try:
        mod = sys.modules.get("unittest.mock")
        if mod is not None and isinstance(callable_, mod.NonCallableMock):
            m = callable_
            while m is not None:
                spec = m.__dict__.get("_spec_class")
                if spec is not None and m is not callable_:
                    return f"{spec.__module__}.{spec.__qualname__}"
                m = m.__dict__.get("_mock_parent")
            return None
        target = getattr(callable_, "__self__", None)
        if target is not None and not isinstance(target, type(sys)):
            cls = target if isinstance(target, type) else type(target)
            return f"{cls.__module__}.{cls.__qualname__}"
        qual = getattr(callable_, "__qualname__", None)
        mod_name = getattr(callable_, "__module__", None)
        if qual and mod_name:
            return f"{mod_name}.{qual.rsplit('.', 1)[0]}" if "." in qual else mod_name
    except Exception:  # noqa: BLE001
        pass
    return None


def _called_name(code, offset) -> str | None:
    try:
        positions = list(code.co_positions())[offset // 2]
        line, end_line, col, end_col = positions
        if None in positions:
            return None
        lines = [linecache.getline(code.co_filename, n) for n in range(line, end_line + 1)]
        if len(lines) == 1:
            text = lines[0][col:end_col]
        else:
            text = lines[0][col:] + "".join(lines[1:-1]) + lines[-1][:end_col]
        node = ast.parse(text.strip(), mode="eval").body
    except Exception:  # noqa: BLE001
        return None
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Attribute):
            return func.attr
        if isinstance(func, ast.Name):
            return func.id
    return None


def _call_args(frame, code) -> dict[str, str]:
    """Arguments as the caller passed them: named parameters, then *args and **kwargs spread out."""
    out: dict[str, str] = {}
    try:
        loc = frame.f_locals
        n = code.co_argcount + code.co_kwonlyargcount
        names = list(code.co_varnames[:n])
        flags = code.co_flags
        star = code.co_varnames[n] if flags & 0x04 else None
        kw = code.co_varnames[n + (1 if star else 0)] if flags & 0x08 else None
        for name in names:
            if name in ("self", "cls") or name not in loc:
                continue
            out[name] = safe_repr(loc[name])
        for i, v in enumerate(loc.get(star, ()) if star else ()):
            out[f"arg{i}"] = safe_repr(v)
        for k, v in (loc.get(kw, {}) if kw else {}).items():
            out[k] = safe_repr(v)
    except Exception:  # noqa: BLE001
        pass
    return dict(list(out.items())[:MAX_LOCALS])


def _claim_tool_id(exclude: int | None = None) -> int:
    for tool in (M.DEBUGGER_ID, 3, 4, M.PROFILER_ID):
        if tool != exclude and M.get_tool(tool) is None:
            M.use_tool_id(tool, "wizualizer")
            return tool
    raise RuntimeError("no free sys.monitoring tool id (another debugger or profiler is attached)")
