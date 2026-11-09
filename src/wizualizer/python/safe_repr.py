"""Bounded, crash-proof value rendering for trace snapshots.

Stdlib only: this module is imported inside the process under test.
"""

from __future__ import annotations

import dataclasses
import datetime
import decimal
import enum
import pathlib
import uuid

MAX_DEPTH = 3
MAX_ITEMS = 8
MAX_STR = 80
MAX_TOTAL = 240

_SCALARS = (
    int, float, complex, bool, type(None), bytes,
    datetime.date, datetime.time, datetime.timedelta, decimal.Decimal, uuid.UUID, pathlib.PurePath,
)


def safe_repr(value: object, depth: int = MAX_DEPTH) -> str:
    """Render a value as a short string. Never raises."""
    try:
        text = _render(value, depth)
    except Exception as exc:  # noqa: BLE001 - must never crash the test
        text = f"<{_type_name(value)}: repr failed ({type(exc).__name__})>"
    return _clip(text, MAX_TOTAL)


def _render(value: object, depth: int) -> str:
    if isinstance(value, str):
        return repr(_clip(value, MAX_STR)) if len(value) > MAX_STR else repr(value)
    if isinstance(value, enum.Enum):
        return f"{type(value).__name__}.{value.name}"
    if isinstance(value, _SCALARS):
        return _clip(repr(value), MAX_STR)
    if depth <= 0:
        return f"<{_type_name(value)}>"
    if isinstance(value, dict):
        return _render_dict(value, depth)
    if isinstance(value, (list, tuple, set, frozenset)):
        return _render_seq(value, depth)
    if isinstance(value, type):
        return f"<class {value.__qualname__}>"
    if callable(value) and hasattr(value, "__qualname__"):
        return f"<function {value.__qualname__}>"
    if dataclasses.is_dataclass(value):
        fields = [(f.name, getattr(value, f.name, "?")) for f in dataclasses.fields(value) if f.repr]
        return _render_object(value, fields, depth)
    if type(value).__repr__ is object.__repr__:
        attrs = _plain_attrs(value)
        if attrs is not None:
            return _render_object(value, attrs, depth)
    return _clip(repr(value), MAX_STR)


def _render_dict(value: dict, depth: int) -> str:
    parts = []
    for i, (k, v) in enumerate(value.items()):
        if i >= MAX_ITEMS:
            parts.append(f"…+{len(value) - MAX_ITEMS}")
            break
        parts.append(f"{_render(k, depth - 1)}: {_render(v, depth - 1)}")
    return "{" + ", ".join(parts) + "}"


def _render_seq(value, depth: int) -> str:
    items = list(value) if isinstance(value, (set, frozenset)) else value
    parts = [_render(v, depth - 1) for v in items[:MAX_ITEMS]]
    if len(items) > MAX_ITEMS:
        parts.append(f"…+{len(items) - MAX_ITEMS}")
    if isinstance(value, tuple):
        return "(" + ", ".join(parts) + ("," if len(parts) == 1 else "") + ")"
    if isinstance(value, (set, frozenset)):
        return "{" + ", ".join(parts) + "}" if parts else "set()"
    return "[" + ", ".join(parts) + "]"


def _render_object(value: object, fields: list, depth: int) -> str:
    parts = []
    for i, (name, v) in enumerate(fields):
        if i >= MAX_ITEMS:
            parts.append("…")
            break
        parts.append(f"{name}={_render(v, depth - 1)}")
    return f"{_type_name(value)}(" + ", ".join(parts) + ")"


def _plain_attrs(value: object):
    d = getattr(value, "__dict__", None)
    if isinstance(d, dict):
        return [(k, v) for k, v in d.items() if not k.startswith("__")]
    slots = getattr(type(value), "__slots__", None)
    if slots:
        names = [slots] if isinstance(slots, str) else list(slots)
        return [(n, getattr(value, n, "<unset>")) for n in names]
    return None


def _type_name(value: object) -> str:
    try:
        return type(value).__name__
    except Exception:  # noqa: BLE001
        return "object"


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"
