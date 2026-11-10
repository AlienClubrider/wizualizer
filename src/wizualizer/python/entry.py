"""Which production function a pytest test calls, read from the source (no test runs).

The first call in the test body to a function or method defined in the project
(not in test code): a function imported from a project module, a method on an
object built from a project class (directly, through a local, or through a
pytest fixture that returns one). Inputs are the call's arguments as written.
"""

from __future__ import annotations

import ast
from pathlib import Path

from ..focus import is_test_file
from ..functions import EntryPoint

MAX_VALUE = 400  # labels are clipped later; keep whole values so fields can be compared


class _Project:
    """Project modules by dotted name, parsed on demand."""

    def __init__(self, root: Path):
        self.root = root
        self._trees: dict[Path, ast.Module | None] = {}

    def module_file(self, dotted: str) -> Path | None:
        parts = dotted.split(".")
        for base in (self.root, self.root / "src"):
            for cand in (base.joinpath(*parts).with_suffix(".py"), base.joinpath(*parts, "__init__.py")):
                if cand.is_file():
                    return cand
        return None

    def tree(self, path: Path) -> ast.Module | None:
        if path not in self._trees:
            try:
                self._trees[path] = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, SyntaxError, UnicodeDecodeError, ValueError):
                self._trees[path] = None
        return self._trees[path]

    def rel(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()


def entry_points(root: Path, flows: list) -> dict[str, EntryPoint | None]:
    """flow id -> the production function its test calls first, or None if not found."""
    project = _Project(root)
    out: dict[str, EntryPoint | None] = {}
    for flow in flows:
        try:
            out[flow.id] = _entry(project, flow)
        except (RecursionError, ValueError):
            out[flow.id] = None
    return out


def _entry(project: _Project, flow) -> EntryPoint | None:
    test_path = project.root / flow.file
    tree = project.tree(test_path)
    if tree is None:
        return None
    name = flow.name.split("[", 1)[0]
    body = tree.body
    for cls in [c for c in flow.group.split("::") if c]:
        found = next((n for n in body if isinstance(n, ast.ClassDef) and n.name == cls), None)
        if found is None:
            return None
        body = found.body
    test = next((n for n in body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name), None)
    if test is None:
        return None
    imports = _imports(project, tree, test_path)
    fixtures = _fixtures(project, test_path, imports)
    case = flow.name[len(name):].strip("[]")
    return _first_call(project, test, imports, fixtures, case)


def _imports(project: _Project, tree: ast.Module, here: Path) -> dict[str, tuple]:
    """Local name -> ("module", file) or ("member", file, attr) for names from project modules."""
    out: dict[str, tuple] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            module = node.module
            if node.level:
                pkg = ".".join(here.relative_to(project.root).parent.parts)
                module = ".".join(p for p in [pkg.rsplit(".", node.level - 1)[0] if node.level > 1 else pkg, module] if p)
            for alias in node.names:
                sub = project.module_file(f"{module}.{alias.name}")
                if sub is not None and not is_test_file(project.rel(sub)):
                    out[alias.asname or alias.name] = ("module", sub)
                    continue
                f = project.module_file(module)
                if f is not None and not is_test_file(project.rel(f)):
                    out[alias.asname or alias.name] = ("member", f, alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                f = project.module_file(alias.name)
                if f is not None and not is_test_file(project.rel(f)):
                    out[alias.asname or alias.name] = ("module", f)
    return out


def _fixtures(project: _Project, test_path: Path, imports: dict) -> dict[str, tuple[Path, str]]:
    """Fixture name -> (file, class) for fixtures (this file, then conftest.py upwards) that return an
    instance of a project class."""
    out: dict[str, tuple[Path, str]] = {}
    files = [test_path]
    d = test_path.parent
    while True:
        files.append(d / "conftest.py")
        if d == project.root or project.root not in d.parents:
            break
        d = d.parent
    for path in files:
        tree = project.tree(path) if path.is_file() else None
        if tree is None:
            continue
        local_imports = imports if path == test_path else _imports(project, tree, path)
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name in out:
                continue
            if not any("fixture" in ast.unparse(d) for d in node.decorator_list):
                continue
            for sub in ast.walk(node):
                value = sub.value if isinstance(sub, (ast.Return, ast.Yield)) else None
                cls = _class_of_call(value, local_imports) if value is not None else None
                if cls:
                    out[node.name] = cls
                    break
    return out


def _class_of_call(node: ast.AST, imports: dict) -> tuple[Path, str] | None:
    """(file, class name) if node is `ProjectClass(...)` or `module.ProjectClass(...)`."""
    if not isinstance(node, ast.Call):
        return None
    f = node.func
    if isinstance(f, ast.Name) and imports.get(f.id, ("",))[0] == "member":
        return imports[f.id][1], imports[f.id][2]
    if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and imports.get(f.value.id, ("",))[0] == "module":
        return imports[f.value.id][1], f.attr
    return None


def _first_call(project: _Project, test, imports: dict, fixtures: dict, case: str) -> EntryPoint | None:
    types: dict[str, tuple[Path, str]] = {}  # local or fixture name -> (file, class)
    values: dict[str, ast.AST] = {}  # local name -> what it was assigned
    for arg in test.args.args:
        if arg.arg in fixtures:
            types[arg.arg] = fixtures[arg.arg]
    for stmt in ast.walk(test):
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
            values.setdefault(stmt.targets[0].id, stmt.value)
            cls = _class_of_call(stmt.value, imports)
            if cls:
                types.setdefault(stmt.targets[0].id, cls)
    calls = sorted((n for n in ast.walk(test) if isinstance(n, ast.Call)), key=lambda n: (n.lineno, n.col_offset))
    for call in calls:
        target = _resolve(project, call.func, imports, types)
        if target is None:
            continue
        path, qualname, fn = target
        params = [a.arg for a in fn.args.posonlyargs + fn.args.args if a.arg not in ("self", "cls")]
        inputs = _inputs(call, params, values)
        if case:
            inputs = (("case", case),) + inputs
        line = min([fn.lineno] + [d.lineno for d in fn.decorator_list])
        return EntryPoint(project.rel(path), qualname, f"{fn.name}({', '.join(params)})", line, inputs)
    return None


def _resolve(project: _Project, func: ast.AST, imports: dict, types: dict):
    """(file, qualname, def node) of a project function or method being called, else None."""
    if isinstance(func, ast.Name) and imports.get(func.id, ("",))[0] == "member":
        _, path, name = imports[func.id]
        return _lookup(project, path, [name])
    if not isinstance(func, ast.Attribute):
        return None
    owner = func.value
    if isinstance(owner, ast.Name) and imports.get(owner.id, ("",))[0] == "module":
        return _lookup(project, imports[owner.id][1], [func.attr])
    if isinstance(owner, ast.Name) and imports.get(owner.id, ("",))[0] == "member":  # Class.static_method(...)
        _, path, cls = imports[owner.id]
        return _lookup(project, path, [cls, func.attr])
    cls = types.get(owner.id) if isinstance(owner, ast.Name) else _class_of_call(owner, imports)
    if cls:
        return _lookup(project, cls[0], [cls[1], func.attr])
    return None


def _lookup(project: _Project, path: Path, names: list[str]):
    tree = project.tree(path)
    body = tree.body if tree else []
    node = None
    for name in names:
        node = next((n for n in body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                     and n.name == name), None)
        if node is None:
            return None
        body = node.body
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return None  # a class: building an object is setup, not the function under test
    return path, ".".join(names), node


def _inputs(call: ast.Call, params: list[str], values: dict[str, ast.AST]) -> tuple[tuple[str, str], ...]:
    out = []
    for i, arg in enumerate(call.args):
        if isinstance(arg, ast.Starred):
            break
        out.append((params[i] if i < len(params) else str(i), _text(arg, values)))
    for kw in call.keywords:
        if kw.arg:
            out.append((kw.arg, _text(kw.value, values)))
    order = {p: i for i, p in enumerate(params)}
    return tuple(sorted(out, key=lambda kv: order.get(kv[0], len(order))))


def _text(node: ast.AST, values: dict[str, ast.AST]) -> str:
    if isinstance(node, ast.Name) and node.id in values:
        node = values[node.id]
    text = " ".join(ast.unparse(node).split())
    return text if len(text) <= MAX_VALUE else text[: MAX_VALUE - 1] + "…"
