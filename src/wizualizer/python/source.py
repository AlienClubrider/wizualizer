"""Python SourceModel: statement spans (for change coverage) and flowcharts."""

from __future__ import annotations

import ast

from ..ports import Declaration, Statement
from .flowchart import build_flowchart

_FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)
_SCOPES = (*_FUNCS, ast.ClassDef, ast.Lambda)


class PythonSource:
    language = "python"

    def handles(self, path: str) -> bool:
        return path.endswith(".py")

    def statements(self, source: str) -> list[Statement]:
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return []
        out: list[Statement] = []
        _collect(tree.body, in_function=False, out=out)
        return out

    def declarations(self, source: str) -> list[Declaration]:
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return []
        out: list[Declaration] = []
        for node in tree.body:
            if isinstance(node, _FUNCS):
                out.append(_declaration(node, None))
            elif isinstance(node, ast.ClassDef):
                out.extend(_declaration(f, node) for f in node.body if isinstance(f, _FUNCS) and not _abstract(f))
        return out

    def flowchart(self, source: str, function_line: int) -> dict | None:
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return None
        for node in ast.walk(tree):
            if isinstance(node, _FUNCS) and _def_start(node) == function_line:
                return build_flowchart(node, source)
        return None


def _declaration(fn, cls: ast.ClassDef | None) -> Declaration:
    params = [a.arg for a in (*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs)]
    if cls is not None and params and params[0] in ("self", "cls"):
        params = params[1:]
    supers = tuple(b.id if isinstance(b, ast.Name) else b.attr for b in (cls.bases if cls else ())
                   if isinstance(b, (ast.Name, ast.Attribute)))
    return Declaration(fn.name, f"{cls.name}.{fn.name}" if cls else fn.name, _def_start(fn),
                       cls.name if cls else "", supers, tuple(params))


def _abstract(fn) -> bool:
    return any((d.id if isinstance(d, ast.Name) else getattr(d, "attr", "")) == "abstractmethod"
               for d in fn.decorator_list)


def _def_start(node) -> int:
    """co_firstlineno: the first decorator line, else the def line."""
    return min([node.lineno, *(d.lineno for d in node.decorator_list)])


def _collect(body: list[ast.stmt], in_function: bool, out: list[Statement]) -> None:
    for stmt in body:
        children = _child_blocks(stmt)
        if isinstance(stmt, ast.Match):
            header_end = max(stmt.lineno, stmt.cases[0].pattern.lineno - 1)
            out.append(Statement(stmt.lineno, (stmt.lineno, header_end), in_function))
            for case in stmt.cases:
                c_end = max(case.pattern.lineno, case.body[0].lineno - 1)
                out.append(Statement(case.pattern.lineno, (case.pattern.lineno, c_end), in_function))
                _collect(case.body, in_function, out)
            continue
        if isinstance(stmt, _FUNCS + (ast.ClassDef,)):
            # The def/class header runs when the enclosing scope runs. A function
            # header counts as run if the function was called (see coverage).
            start = _def_start(stmt)
            body_start = stmt.body[0].lineno
            out.append(Statement(stmt.lineno, (start, max(stmt.lineno, body_start - 1)), in_function))
            _collect(stmt.body, in_function=isinstance(stmt, _FUNCS), out=out)
            continue
        if children:
            first_child = min(s.lineno for block in children for s in block)
            header_end = max(stmt.lineno, first_child - 1)
            out.append(Statement(stmt.lineno, (stmt.lineno, header_end), in_function))
            for block in children:
                _collect(block, in_function, out)
            for handler in getattr(stmt, "handlers", []):
                h_end = max(handler.lineno, handler.body[0].lineno - 1)
                out.append(Statement(handler.lineno, (handler.lineno, h_end), in_function))
                _collect(handler.body, in_function, out)
            continue
        if _is_docstring(stmt):
            continue
        out.append(Statement(stmt.lineno, (stmt.lineno, stmt.end_lineno or stmt.lineno), in_function))


def _child_blocks(stmt: ast.stmt) -> list[list[ast.stmt]]:
    blocks = []
    for field in ("body", "orelse", "finalbody"):
        block = getattr(stmt, field, None)
        if isinstance(block, list) and block and isinstance(block[0], ast.stmt):
            blocks.append(block)
    return blocks


def _is_docstring(stmt: ast.stmt) -> bool:
    return isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str)
