"""Build a control-flow graph (flowchart) of one Python function from its AST.

Output (see docs/trace-format.md, "flowchart"):
  nodes: [{id, kind, label, lines: [first, last], depth}]   in source order
  edges: [{from, to, label?}]

Node kinds: start, end, stmt, decision, loop, return, raise, break, continue,
try, except, with, def. `lines` is the source span the node owns, so a viewer
can light up the node for whatever line a debugger reports.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

LABEL_MAX = 48

Exit = tuple[int, str | None]  # (node id, edge label)


@dataclass
class _Loop:
    head: int
    breaks: list[Exit] = field(default_factory=list)


@dataclass
class _Ctx:
    end: int
    loops: list[_Loop] = field(default_factory=list)
    handlers: list[list[int]] = field(default_factory=list)  # innermost try's except nodes


class _Builder:
    def __init__(self, source: str):
        self.source = source
        self.nodes: list[dict] = []
        self.edges: list[dict] = []
        self._next_placeholder = -1  # -1 is the end node until it is placed

    def node(self, kind: str, label: str, first: int, last: int, depth: int) -> int:
        nid = len(self.nodes)
        self.nodes.append({"id": nid, "kind": kind, "label": _clip(label), "lines": [first, last], "depth": depth})
        return nid

    def link(self, preds: list[Exit], to: int) -> None:
        for src, label in preds:
            edge = {"from": src, "to": to}
            if label:
                edge["label"] = label
            if edge not in self.edges:
                self.edges.append(edge)

    # blocks ----------------------------------------------------------------
    def block(self, stmts: list[ast.stmt], preds: list[Exit], ctx: _Ctx, depth: int) -> list[Exit]:
        for i, stmt in enumerate(stmts):
            if i == 0 and _is_docstring(stmt):
                continue
            preds = self.stmt(stmt, preds, ctx, depth)
        return preds

    def stmt(self, s: ast.stmt, preds: list[Exit], ctx: _Ctx, depth: int) -> list[Exit]:
        method = getattr(self, f"s_{type(s).__name__}", None)
        if method:
            return method(s, preds, ctx, depth)
        n = self.node("stmt", _text(s), s.lineno, s.end_lineno or s.lineno, depth)
        self.link(preds, n)
        return [(n, None)]

    def s_Return(self, s, preds, ctx, depth):
        n = self.node("return", _text(s), s.lineno, s.end_lineno, depth)
        self.link(preds, n)
        self.link([(n, None)], ctx.end)
        return []

    def s_Raise(self, s, preds, ctx, depth):
        n = self.node("raise", _text(s), s.lineno, s.end_lineno, depth)
        self.link(preds, n)
        target = ctx.handlers[-1][0] if ctx.handlers else ctx.end
        self.link([(n, "raise")], target)
        return []

    def s_Break(self, s, preds, ctx, depth):
        n = self.node("break", "break", s.lineno, s.lineno, depth)
        self.link(preds, n)
        if ctx.loops:
            ctx.loops[-1].breaks.append((n, None))
        return []

    def s_Continue(self, s, preds, ctx, depth):
        n = self.node("continue", "continue", s.lineno, s.lineno, depth)
        self.link(preds, n)
        if ctx.loops:
            self.link([(n, None)], ctx.loops[-1].head)
        return []

    def s_If(self, s, preds, ctx, depth, keyword="if"):
        n = self.node("decision", f"{keyword} {_expr(s.test)}", s.lineno, s.test.end_lineno, depth)
        self.link(preds, n)
        exits = self.block(s.body, [(n, "yes")], ctx, depth + 1)
        if s.orelse:
            if len(s.orelse) == 1 and isinstance(s.orelse[0], ast.If) and self._is_elif(s.orelse[0]):
                exits += self.s_If(s.orelse[0], [(n, "no")], ctx, depth, keyword="elif")
            else:
                exits += self.block(s.orelse, [(n, "no")], ctx, depth + 1)
        else:
            exits.append((n, "no"))
        return exits

    def s_While(self, s, preds, ctx, depth):
        n = self.node("decision", f"while {_expr(s.test)}", s.lineno, s.test.end_lineno, depth)
        self.link(preds, n)
        return self._loop_body(s, n, ctx, depth, ("yes", "no"))

    def s_For(self, s, preds, ctx, depth):
        label = f"for {_expr(s.target)} in {_expr(s.iter)}"
        n = self.node("loop", label, s.lineno, s.iter.end_lineno, depth)
        self.link(preds, n)
        return self._loop_body(s, n, ctx, depth, ("next", "done"))

    s_AsyncFor = s_For

    def _loop_body(self, s, head, ctx, depth, labels):
        loop = _Loop(head)
        ctx.loops.append(loop)
        body_exits = self.block(s.body, [(head, labels[0])], ctx, depth + 1)
        ctx.loops.pop()
        self.link(body_exits, head)
        exits = [(head, labels[1])]
        if s.orelse:
            exits = self.block(s.orelse, exits, ctx, depth + 1)
        return exits + loop.breaks

    def s_With(self, s, preds, ctx, depth):
        items = ", ".join(_expr(i.context_expr) + (f" as {_expr(i.optional_vars)}" if i.optional_vars else "") for i in s.items)
        n = self.node("with", f"with {items}", s.lineno, s.body[0].lineno - 1 if s.body[0].lineno > s.lineno else s.lineno, depth)
        self.link(preds, n)
        return self.block(s.body, [(n, None)], ctx, depth + 1)

    s_AsyncWith = s_With

    def s_Try(self, s, preds, ctx, depth):
        n = self.node("try", "try", s.lineno, s.lineno, depth)
        self.link(preds, n)
        placeholder = self._placeholder()  # raises in the body point here until handlers exist
        if s.handlers:
            ctx.handlers.append([placeholder])
        exits = self.block(s.body, [(n, None)], ctx, depth + 1)
        if s.handlers:
            ctx.handlers.pop()
        if s.orelse:
            exits = self.block(s.orelse, exits, ctx, depth + 1)
        first_handler = None
        for h in s.handlers:
            label = "except" + (f" {_expr(h.type)}" if h.type else "") + (f" as {h.name}" if h.name else "")
            hn = self.node("except", label, h.lineno, h.lineno, depth)
            first_handler = hn if first_handler is None else first_handler
            self.link([(n, "error")], hn)
            exits = exits + self.block(h.body, [(hn, None)], ctx, depth + 1)
        if first_handler is not None:
            self._retarget(placeholder, first_handler)
        if s.finalbody:
            exits = self.block(s.finalbody, exits, ctx, depth)
        return exits

    s_TryStar = s_Try

    def _placeholder(self) -> int:
        self._next_placeholder -= 1
        return self._next_placeholder

    def _retarget(self, old: int, new: int) -> None:
        for e in self.edges:
            if e["to"] == old:
                e["to"] = new

    def s_Match(self, s, preds, ctx, depth):
        n = self.node("stmt", f"match {_expr(s.subject)}", s.lineno, s.subject.end_lineno, depth)
        self.link(preds, n)
        exits: list[Exit] = []
        nxt: list[Exit] = [(n, None)]
        for case in s.cases:
            guard = f" if {_expr(case.guard)}" if case.guard else ""
            c = self.node("decision", f"case {ast.unparse(case.pattern)}{guard}", case.pattern.lineno, case.pattern.end_lineno, depth + 1)
            self.link(nxt, c)
            exits += self.block(case.body, [(c, "yes")], ctx, depth + 2)
            nxt = [(c, "no")]
        return exits + nxt

    def s_FunctionDef(self, s, preds, ctx, depth):
        n = self.node("def", f"def {s.name}(…)", s.lineno, s.lineno, depth)
        self.link(preds, n)
        return [(n, None)]

    s_AsyncFunctionDef = s_FunctionDef

    def s_ClassDef(self, s, preds, ctx, depth):
        n = self.node("def", f"class {s.name}", s.lineno, s.lineno, depth)
        self.link(preds, n)
        return [(n, None)]

    def _is_elif(self, inner: ast.If) -> bool:
        lines = self.source.splitlines()
        line = lines[inner.lineno - 1] if inner.lineno - 1 < len(lines) else ""
        return line.lstrip().startswith("elif")


def build_flowchart(func: ast.FunctionDef | ast.AsyncFunctionDef, source: str) -> dict:
    b = _Builder(source)
    args = ", ".join(a.arg for a in func.args.posonlyargs + func.args.args + func.args.kwonlyargs)
    start = b.node("start", f"{func.name}({args})", func.lineno, func.lineno, 0)
    exits = b.block(func.body, [(start, None)], _Ctx(end=-1), 1)
    b.link(exits, -1)
    # The end node never matches a line: a viewer lights it up on return.
    end = b.node("end", "end", 0, 0, 0)
    b._retarget(-1, end)
    return {"nodes": b.nodes, "edges": b.edges}


def _is_docstring(stmt: ast.stmt) -> bool:
    return isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str)


def _expr(node: ast.AST) -> str:
    return " ".join(ast.unparse(node).split())


def _text(s: ast.stmt) -> str:
    return " ".join(ast.unparse(s).split())


def _clip(text: str) -> str:
    return text if len(text) <= LABEL_MAX else text[: LABEL_MAX - 1] + "…"
