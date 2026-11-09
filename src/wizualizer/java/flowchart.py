"""Build the flowchart (control-flow graph) of one Java method from its outline.

The outline is the statement tree the Java helper makes with javac's parser
(helper/wiz/Outline.java). Output is the same as the Python builder (see
docs/trace-format.md, "flowchart"): nodes in source order with the line span
each owns, and edges with optional yes/no/next/done/error labels.
"""

from __future__ import annotations

from dataclasses import dataclass, field

LABEL_MAX = 48

Exit = tuple[int, str | None]  # (node id, edge label)


@dataclass
class _Target:
    """Something `break` (and for loops, `continue`) can jump to."""

    is_loop: bool
    labels: set[str] = field(default_factory=set)
    head: int | None = None  # continue target; None until known (do-while)
    breaks: list[Exit] = field(default_factory=list)
    continues: list[Exit] = field(default_factory=list)


@dataclass
class _Ctx:
    end: int
    targets: list[_Target] = field(default_factory=list)
    handlers: list[int] = field(default_factory=list)  # innermost try's first catch (placeholder id)
    pending_label: str | None = None  # a label waiting for the statement it names


class _Builder:
    def __init__(self):
        self.nodes: list[dict] = []
        self.edges: list[dict] = []
        self._next_placeholder = -1  # -1 is the end node until it is placed

    def node(self, kind: str, label: str, lines: list[int], depth: int) -> int:
        nid = len(self.nodes)
        self.nodes.append({"id": nid, "kind": kind, "label": _clip(label), "lines": list(lines), "depth": depth})
        return nid

    def link(self, preds: list[Exit], to: int) -> None:
        for src, label in preds:
            edge = {"from": src, "to": to}
            if label:
                edge["label"] = label
            if edge not in self.edges:
                self.edges.append(edge)

    def placeholder(self) -> int:
        self._next_placeholder -= 1
        return self._next_placeholder

    def retarget(self, old: int, new: int) -> None:
        for e in self.edges:
            if e["to"] == old:
                e["to"] = new

    # statements -------------------------------------------------------------------------
    def block(self, stmts: list[dict], preds: list[Exit], ctx: _Ctx, depth: int) -> list[Exit]:
        for s in stmts:
            preds = self.stmt(s, preds, ctx, depth)
        return preds

    def stmt(self, s: dict | None, preds: list[Exit], ctx: _Ctx, depth: int) -> list[Exit]:
        if s is None:
            return preds
        method = getattr(self, f"s_{s['k']}", None)
        if method:
            return method(s, preds, ctx, depth)
        n = self.node("stmt", s["t"], s["l"], depth)
        self.link(preds, n)
        return [(n, None)]

    def s_block(self, s, preds, ctx, depth):
        if ctx.pending_label:  # a labeled block: `break label` leaves it
            target = _Target(is_loop=False, labels={ctx.pending_label})
            ctx.pending_label = None
            ctx.targets.append(target)
            exits = self.block(s["body"], preds, ctx, depth)
            ctx.targets.pop()
            return exits + target.breaks
        return self.block(s["body"], preds, ctx, depth)

    def s_labeled(self, s, preds, ctx, depth):
        ctx.pending_label = s["label"]
        exits = self.stmt(s["body"], preds, ctx, depth)
        ctx.pending_label = None
        return exits

    def s_return(self, s, preds, ctx, depth):
        n = self.node("return", s["t"], s["l"], depth)
        self.link(preds, n)
        self.link([(n, None)], ctx.end)
        return []

    def s_throw(self, s, preds, ctx, depth):
        n = self.node("raise", s["t"], s["l"], depth)
        self.link(preds, n)
        self.link([(n, "throw")], ctx.handlers[-1] if ctx.handlers else ctx.end)
        return []

    def s_break(self, s, preds, ctx, depth):
        n = self.node("break", s["t"], s["l"], depth)
        self.link(preds, n)
        target = self._target(ctx, s.get("label"), loops_only=False)
        if target:
            target.breaks.append((n, None))
        return []

    def s_continue(self, s, preds, ctx, depth):
        n = self.node("continue", s["t"], s["l"], depth)
        self.link(preds, n)
        target = self._target(ctx, s.get("label"), loops_only=True)
        if target:
            if target.head is None:
                target.continues.append((n, None))
            else:
                self.link([(n, None)], target.head)
        return []

    def _target(self, ctx: _Ctx, label: str | None, loops_only: bool) -> _Target | None:
        for t in reversed(ctx.targets):
            if label is not None:
                if label in t.labels:
                    return t
            elif t.is_loop or (not loops_only and not t.labels):  # loop, or an unlabeled switch
                return t
        return None

    def s_if(self, s, preds, ctx, depth, prefix=""):
        n = self.node("decision", prefix + s["t"], s["l"], depth)
        self.link(preds, n)
        exits = self.stmt(s["then"], [(n, "yes")], ctx, depth + 1)
        other = s.get("else")
        if other is None:
            exits.append((n, "no"))
        elif other["k"] == "if":
            exits += self.s_if(other, [(n, "no")], ctx, depth, prefix="else ")
        else:
            exits += self.stmt(other, [(n, "no")], ctx, depth + 1)
        return exits

    def _loop(self, ctx: _Ctx, head: int | None) -> _Target:
        target = _Target(is_loop=True, head=head)
        if ctx.pending_label:
            target.labels.add(ctx.pending_label)
            ctx.pending_label = None
        ctx.targets.append(target)
        return target

    def _loop_with_head(self, s, preds, ctx, depth, kind, labels):
        n = self.node(kind, s["t"], s["l"], depth)
        self.link(preds, n)
        target = self._loop(ctx, n)
        body_exits = self.stmt(s["body"], [(n, labels[0])], ctx, depth + 1)
        ctx.targets.pop()
        self.link(body_exits, n)
        return [(n, labels[1])] + target.breaks

    def s_while(self, s, preds, ctx, depth):
        return self._loop_with_head(s, preds, ctx, depth, "decision", ("yes", "no"))

    def s_for(self, s, preds, ctx, depth):
        return self._loop_with_head(s, preds, ctx, depth, "loop", ("next", "done"))

    s_foreach = s_for

    def s_do(self, s, preds, ctx, depth):
        target = self._loop(ctx, None)
        first = len(self.nodes)
        body_exits = self.stmt(s["body"], preds, ctx, depth + 1)
        ctx.targets.pop()
        cond = self.node("decision", s["t"], s["l"], depth)
        self.link(body_exits + target.continues, cond)
        if first != cond:
            self.link([(cond, "yes")], first)
        return [(cond, "no")] + target.breaks

    def s_switch(self, s, preds, ctx, depth):
        n = self.node("stmt", s["t"], s["l"], depth)
        self.link(preds, n)
        target = _Target(is_loop=False)
        ctx.targets.append(target)
        exits: list[Exit] = []
        nxt: list[Exit] = [(n, None)]
        fall: list[Exit] = []
        has_default = False
        for case in s["cases"]:
            c = self.node("decision", case["t"], case["l"], depth + 1)
            self.link(nxt, c)
            if case["default"]:
                has_default = True
            body_exits = self.block(case["body"], [(c, "yes")] + fall, ctx, depth + 2)
            if case["rule"]:
                exits += body_exits
                fall = []
            else:
                fall = body_exits  # falls through into the next case
            nxt = [(c, "no")]
        ctx.targets.pop()
        exits += fall + target.breaks
        if not has_default:
            exits += nxt
        return exits

    def s_try(self, s, preds, ctx, depth):
        n = self.node("try", s["t"], s["l"], depth)
        self.link(preds, n)
        catches = s.get("catches") or []
        placeholder = self.placeholder()  # throws in the body point here until catches exist
        if catches:
            ctx.handlers.append(placeholder)
        exits = self.stmt(s["body"], [(n, None)], ctx, depth + 1)
        if catches:
            ctx.handlers.pop()
        first = None
        for c in catches:
            cn = self.node("except", c["t"], c["l"], depth)
            first = cn if first is None else first
            self.link([(n, "error")], cn)
            exits = exits + self.stmt(c["body"], [(cn, None)], ctx, depth + 1)
        if first is not None:
            self.retarget(placeholder, first)
        if s.get("finally"):
            exits = self.stmt(s["finally"], exits, ctx, depth)
        return exits

    def s_sync(self, s, preds, ctx, depth):
        n = self.node("with", s["t"], s["l"], depth)
        self.link(preds, n)
        return self.stmt(s["body"], [(n, None)], ctx, depth + 1)

    def s_class(self, s, preds, ctx, depth):
        n = self.node("def", s["t"], s["l"], depth)
        self.link(preds, n)
        return [(n, None)]


def build_flowchart(method: dict) -> dict:
    """method: one entry of the helper's outline["methods"]."""
    b = _Builder()
    line = method["line"]
    start = b.node("start", f"{method['name']}({', '.join(method['params'])})", [line, line], 0)
    exits = b.block(method["body"], [(start, None)], _Ctx(end=-1), 1)
    b.link(exits, -1)
    end = b.node("end", "end", [0, 0], 0)  # never matches a line: lit on return
    b.retarget(-1, end)
    return {"nodes": b.nodes, "edges": b.edges}


def _clip(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= LABEL_MAX else text[: LABEL_MAX - 1] + "…"
