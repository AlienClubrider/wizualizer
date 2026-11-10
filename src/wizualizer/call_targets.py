"""Which project function a call stood for, when the call ran no project code (pure rules).

A recorder lists calls from project code that opened no frame: into a mock (Mockito, unittest.mock),
a test's stub (monkeypatch), the JDK or a library. Each has the called name and, when known, the
type it was called on (the mocked interface, the spec class). This module matches such a call
against the project's declarations: the type's own method, or the method of every class that
extends or implements it. No I/O.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from .ports import Declaration

MAX_TARGETS = 8


def called_type(call: Mapping) -> str | None:
    """Simple name of the type a call was made on: "com.x.UserAccountService" -> "UserAccountService",
    "com.x.Outer$Inner" -> "Inner", "app.gateway.PaymentGateway" -> "PaymentGateway"."""
    t = call.get("type")
    if not t:
        return None
    return t.replace("$", ".").rsplit(".", 1)[-1]


def subtypes(declared: Mapping[str, Iterable[Declaration]]) -> dict[str, set[str]]:
    """Type -> every project type that extends or implements it, directly or not."""
    parents: dict[str, set[str]] = {}
    for decls in declared.values():
        for d in decls:
            if d.owner:
                parents.setdefault(d.owner, set()).update(d.supers)
    below: dict[str, set[str]] = {}
    for child, ps in parents.items():
        seen, todo = set(), list(ps)
        while todo:
            p = todo.pop()
            if p in seen or p == child:
                continue
            seen.add(p)
            below.setdefault(p, set()).add(child)
            todo.extend(parents.get(p, ()))
    return below


def resolve(call: Mapping, declared: Mapping[str, Iterable[Declaration]],
            below: Mapping[str, set[str]] | None = None) -> list[tuple[str, Declaration]]:
    """The project functions this call stands for, as (file, declaration).

    With a type: that type's method, or the method of each project type below it. Without one
    (an unspecced mock, a stub): every project function of that name, if there are few enough.
    The argument count narrows the choice when it can.
    """
    name = call.get("name") or ""
    if not name or name.startswith("new "):
        return []
    below = subtypes(declared) if below is None else below
    owner = called_type(call)
    hits = [(f, d) for f, decls in declared.items() for d in decls if d.name == name]
    if owner is not None:
        owners = {owner} | set(below.get(owner, ()))
        hits = [(f, d) for f, d in hits if d.owner in owners]
        if not hits:
            # A module-level function called through its module (app.gateway.send_receipt).
            module = (call.get("type") or "").replace(".", "/")
            hits = [(f, d) for f, decls in declared.items() for d in decls
                    if d.name == name and not d.owner and f.rsplit(".", 1)[0].endswith(module)]
    arity = call.get("arity")
    if arity is not None:
        exact = [(f, d) for f, d in hits if len(d.params) == arity]
        hits = exact or hits
    # Implementations first; the called type's own body (an abstract class's, a default method) last.
    hits.sort(key=lambda h: (h[1].owner == owner, h[0], h[1].line))
    return hits if len(hits) <= MAX_TARGETS else []


def mentions(text: str, name: str) -> bool:
    """Cheap pre-filter: could this source declare a function called `name`?"""
    return f"{name}(" in text or f"{name} (" in text
