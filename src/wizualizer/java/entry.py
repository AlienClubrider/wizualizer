"""Which production method a JUnit test calls, read from the source (no build, no JVM).

The first call in the test method to a method of a project class that is not
test code and not a mock: on a field or local of that type (`@InjectMocks`,
`@Autowired`, `new`, set in `@BeforeEach`), on `new Type(...)`, or a static
call. A MockMvc or WebTestClient request is matched to the controller method
whose `@RequestMapping`/`@GetMapping`/... path and HTTP method it hits.

A small scanner, not a parser: comments and strings are masked, class bodies
are split into members by braces. Good enough to find the entry point and
show the inputs as written; the recording confirms or corrects it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..focus import is_test_file
from ..functions import EntryPoint, split_top
from .project import SourceIndex

MAX_VALUE = 400  # labels are clipped later; keep whole values so fields can be compared
MOCK_ANNOTATIONS = {"Mock", "MockBean", "MockitoBean"}
HTTP = ("get", "post", "put", "patch", "delete")
_STRING = re.compile(r'"""(?:.|\n)*?"""|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])\'')
_COMMENT = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)
_CLASS = re.compile(r"\b(class|interface|enum|record)\s+(\w+)")
_METHOD = re.compile(r"(\w+)\s*\(([^()]*(?:\([^()]*\)[^()]*)*)\)\s*(?:throws\s+[\w.,\s]+)?$", re.S)
_ANNOTATION = re.compile(r"@([\w.]+)(\s*\((?:[^()]|\([^()]*\))*\))?")
_MODIFIERS = re.compile(r"\b(public|protected|private|static|final|abstract|synchronized|default|transient|volatile)\b")
_NOT_METHODS = {"if", "for", "while", "switch", "catch", "synchronized", "return", "new", "try", "else", "do"}


# ---- scanning ---------------------------------------------------------------------------------

@dataclass
class JMethod:
    name: str
    params: list[tuple[str, str]]  # (type, name)
    annotations: dict[str, str]  # simple name -> argument text (strings restored)
    line: int
    body: str = ""  # masked
    ctor: bool = False
    body_param: str = ""  # the @RequestBody parameter of a controller method


@dataclass
class JClass:
    qual: str  # Outer.Inner
    kind: str  # class | interface | enum | record
    header: str  # masked, with annotations
    annotations: dict[str, str] = field(default_factory=dict)
    fields: list[str] = field(default_factory=list)  # masked declarations
    methods: list[JMethod] = field(default_factory=list)


@dataclass
class JFile:
    path: Path
    package: str
    imports: dict[str, str]  # simple name -> full name
    classes: dict[str, JClass]  # qual -> class
    strings: list[str]

    def restore(self, text: str) -> str:
        return re.sub(r'"§(\d+)"', lambda m: self.strings[int(m.group(1))], text)


def scan(path: Path) -> JFile | None:
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    strings: list[str] = []

    def keep(m: re.Match) -> str:
        text = m.group(0)
        if text.startswith("/"):
            return re.sub(r"[^\n]", " ", text)
        if text.startswith("'"):
            return "'?'"
        strings.append(text)
        return f'"§{len(strings) - 1}"'

    masked = re.sub(r"//[^\n]*|/\*.*?\*/|" + _STRING.pattern, keep, source, flags=re.S)
    jf = JFile(path, "", {}, {}, strings)
    stack: list[tuple[JClass, int]] = []  # class, depth of its body
    open_method: tuple[JMethod, int, int] | None = None  # method, its body depth, body start
    depth, seg_start = 0, 0
    for i, ch in enumerate(masked):
        if ch not in "{};":
            continue
        seg = masked[seg_start:i]
        at_class = bool(stack) and stack[-1][1] == depth and open_method is None
        if ch == ";":
            if depth == 0:
                m = re.match(r"\s*(package|import)\s+(static\s+)?([\w.*]+)", seg)
                if m and m.group(1) == "package":
                    jf.package = m.group(3)
                elif m and not m.group(2) and not m.group(3).endswith("*"):
                    jf.imports[m.group(3).rsplit(".", 1)[-1]] = m.group(3)
            elif at_class and "(" not in seg.split("=", 1)[0]:
                stack[-1][0].fields.append(seg.strip())
            seg_start = i + 1
            continue
        if ch == "{":
            header = seg.strip()
            if open_method is None and (depth == 0 or at_class):
                c = _CLASS.search(_ANNOTATION.sub(" ", header))
                if c:
                    outer = stack[-1][0].qual + "." if stack else ""
                    cls = JClass(outer + c.group(2), c.group(1), header, _annotations(header, jf))
                    jf.classes[cls.qual] = cls
                    stack.append((cls, depth + 1))
                elif at_class:
                    plain = _ANNOTATION.sub(" ", header)
                    m = _METHOD.search(plain)
                    if m and m.group(1) not in _NOT_METHODS and "=" not in plain[: m.start()]:
                        cls = stack[-1][0]
                        ctor = m.group(1) == cls.qual.rsplit(".", 1)[-1]
                        meth = JMethod(m.group(1), _params(m.group(2)), _annotations(header, jf),
                                       masked.count("\n", 0, seg_start + len(seg) - len(seg.lstrip())) + 1, ctor=ctor)
                        rb = re.search(r"@RequestBody\b[^,]*?(\w+)\s*(?:,|\)\s*(?:throws[\w.,\s]*)?$)", header)
                        meth.body_param = rb.group(1) if rb else ""
                        cls.methods.append(meth)
                        open_method = (meth, depth + 1, i + 1)
            depth += 1
        else:  # "}"
            depth -= 1
            if open_method and open_method[1] == depth + 1:
                open_method[0].body = masked[open_method[2]:i]
                open_method = None
            elif stack and stack[-1][1] == depth + 1:
                stack.pop()
        seg_start = i + 1
    return jf


def _annotations(header: str, jf: JFile) -> dict[str, str]:
    return {m.group(1).rsplit(".", 1)[-1]: jf.restore((m.group(2) or "").strip()[1:-1].strip())
            for m in _ANNOTATION.finditer(header)}


def _params(text: str) -> list[tuple[str, str]]:
    out = []
    for p in split_top(_ANNOTATION.sub(" ", text)):
        words = _MODIFIERS.sub(" ", p).split()
        if len(words) >= 2:
            out.append((" ".join(words[:-1]), words[-1]))
    return out


def _decl(text: str) -> tuple[str, str, str, set[str]] | None:
    """(type, name, initializer, annotations) of a field or local declaration."""
    annotations = {m.group(1).rsplit(".", 1)[-1] for m in _ANNOTATION.finditer(text)}
    plain = _MODIFIERS.sub(" ", _ANNOTATION.sub(" ", text)).strip()
    left, eq, init = plain.partition("=")
    m = re.match(r"([\w.]+(?:\s*<.*>)?(?:\s*\[\s*\])*)\s+(\w+)\s*$", left.strip(), re.S)
    if not m or m.group(1) in ("return", "throw", "new", "else"):
        return None
    return m.group(1), m.group(2), init.strip() if eq else "", annotations


def _raw(type_text: str) -> str:
    return re.sub(r"<.*>", "", type_text).replace("[]", "").strip()


# ---- the project ------------------------------------------------------------------------------

class _Project:
    def __init__(self, root: Path, index: SourceIndex):
        self.root = root
        self.index = index
        self._files: dict[Path, JFile | None] = {}
        self._impls: dict[str, list[tuple[Path, str]]] | None = None
        self._routes: list[tuple[str, re.Pattern, list[str], Path, JClass, JMethod]] | None = None

    def file(self, path: Path) -> JFile | None:
        if path not in self._files:
            self._files[path] = scan(path)
        return self._files[path]

    def production(self, path: Path) -> bool:
        return not is_test_file(path.relative_to(self.root).as_posix())

    def resolve(self, type_text: str, ctx: JFile) -> tuple[Path, JClass] | None:
        """A production class named in ctx (imported, same package, or unique in the project)."""
        name = _raw(type_text)
        first, _, rest = name.partition(".")
        candidates = []
        if first in ctx.imports:
            candidates.append(ctx.imports[first] + (f".{rest}" if rest else ""))
        candidates += [f"{ctx.package}.{name}" if ctx.package else name, name]
        for full in candidates:
            hit = self._class(full)
            if hit:
                return hit
        simple = [n for n in self.index.classes if n.rsplit(".", 1)[-1] == first]
        if len(simple) == 1:
            return self._class(simple[0] + (f".{rest}" if rest else ""))
        return None

    def _class(self, full: str) -> tuple[Path, JClass] | None:
        parts = full.split(".")
        for i in range(len(parts), 0, -1):  # longest package+top-level class prefix that is a file
            top = ".".join(parts[:i])
            path = self.index.classes.get(top)
            if path is None:
                continue
            if not self.production(path):
                return None
            jf = self.file(path)
            qual = ".".join(parts[i - 1:])
            if jf and qual in jf.classes:
                return path, jf.classes[qual]
            return None
        return None

    def find_method(self, path: Path, cls: JClass, name: str, arity: int | None, depth: int = 0):
        """(path, class, method) for name on cls, its single implementation (interfaces) or superclass."""
        if cls.kind == "interface" or "abstract" in cls.header.split("class", 1)[0]:
            impls = self.implementations(cls.qual.rsplit(".", 1)[-1])
            if len(impls) == 1 and depth < 3:
                ipath, iqual = impls[0]
                jf = self.file(ipath)
                if jf and iqual in jf.classes:
                    found = self.find_method(ipath, jf.classes[iqual], name, arity, depth + 1)
                    if found:
                        return found
        methods = [m for m in cls.methods if m.name == name and not m.ctor]
        if methods:
            exact = [m for m in methods if arity is None or len(m.params) == arity]
            return path, cls, (exact or methods)[0]
        m = re.search(r"\bextends\s+([\w.]+)", _ANNOTATION.sub(" ", cls.header))
        if m and depth < 3:
            jf = self.file(path)
            sup = self.resolve(m.group(1), jf) if jf else None
            if sup:
                return self.find_method(sup[0], sup[1], name, arity, depth + 1)
        return None

    def implementations(self, simple: str) -> list[tuple[Path, str]]:
        if self._impls is None:
            self._impls = {}
            for path in self.index.classes.values():
                if not self.production(path):
                    continue
                jf = self.file(path)
                for c in (jf.classes.values() if jf else ()):
                    m = re.search(r"\bimplements\s+(.+)$", _ANNOTATION.sub(" ", c.header), re.S)
                    if c.kind != "class" or not m:
                        continue
                    for iface in split_top(m.group(1)):
                        self._impls.setdefault(_raw(iface).rsplit(".", 1)[-1], []).append((path, c.qual))
        return self._impls.get(simple, [])

    def routes(self):
        """(HTTP method or "", path regex, path variable names, file, class, method) of every controller method."""
        if self._routes is None:
            self._routes = []
            for path in self.index.classes.values():
                if not self.production(path):
                    continue
                jf = self.file(path)
                for c in (jf.classes.values() if jf else ()):
                    if not ({"RestController", "Controller"} & set(c.annotations)):
                        continue
                    bases = _mapping_paths(c.annotations.get("RequestMapping", "")) or [""]
                    for m in c.methods:
                        for ann, verb in (("GetMapping", "get"), ("PostMapping", "post"), ("PutMapping", "put"),
                                          ("PatchMapping", "patch"), ("DeleteMapping", "delete"),
                                          ("RequestMapping", "")):
                            if ann not in m.annotations:
                                continue
                            args = m.annotations[ann]
                            if not verb:
                                vm = re.search(r"RequestMethod\.(\w+)", args)
                                verb = vm.group(1).lower() if vm else ""
                            for base in bases:
                                for sub in _mapping_paths(args) or [""]:
                                    full = "/" + "/".join(p for p in (base + "/" + sub).split("/") if p)
                                    names = re.findall(r"\{(\w+)[^}]*\}", full)
                                    rx = re.compile("^" + re.sub(r"\\\{\w+[^}]*\\\}", "([^/]+)",
                                                                 re.escape(full)) + "/?$")
                                    self._routes.append((verb, rx, names, path, c, m))
        return self._routes


def _mapping_paths(args: str) -> list[str]:
    """Paths in @RequestMapping("/a"), (value = {"/a", "/b"}), (path = "/a", method = ...)."""
    if not args:
        return []
    m = re.search(r"\b(?:value|path)\s*=\s*(\{[^}]*\}|\"[^\"]*\")", args)
    text = m.group(1) if m else (args if args.lstrip().startswith(("\"", "{")) else "")
    return [s.strip('"') for s in re.findall(r"\"[^\"]*\"", text)]


# ---- the test ---------------------------------------------------------------------------------

def entry_points(root: Path, flows: list, index: SourceIndex | None = None) -> dict[str, EntryPoint | None]:
    """flow id -> the production method its test calls first, or None if not found."""
    project = _Project(root, index or SourceIndex.scan(root))
    out: dict[str, EntryPoint | None] = {}
    for flow in flows:
        try:
            out[flow.id] = _entry(project, flow)
        except (RecursionError, ValueError, IndexError):
            out[flow.id] = None
    return out


def _entry(project: _Project, flow) -> EntryPoint | None:
    jf = project.file(project.root / flow.file)
    if jf is None:
        return None
    cls_name = flow.group[len(jf.package) + 1:] if jf.package else flow.group
    test_cls = jf.classes.get(cls_name.replace("$", "."))
    if test_cls is None:
        return None
    method = next((m for m in test_cls.methods if m.name == flow.name), None)
    if method is None:
        return None
    # Names in scope: fields of the test class and its outer classes, then the method's locals.
    scope: dict[str, tuple[str, str, bool]] = {}  # name -> (type, initializer, mocked)
    quals = cls_name.replace("$", ".").split(".")
    for k in range(1, len(quals) + 1):
        c = jf.classes.get(".".join(quals[:k]))
        for f in (c.fields if c else ()):
            d = _decl(f)
            if d:
                scope[d[1]] = (d[0], d[2], bool(d[3] & MOCK_ANNOTATIONS) or _is_mock(d[2]))
    for p_type, p_name in method.params:
        scope[p_name] = (p_type, "", False)
    for stmt in split_top(method.body, ";"):
        d = _decl(stmt.strip().split("{")[-1] if "{" in stmt and "=" not in stmt.split("{")[-1][:1] else stmt.strip())
        if d:
            t = d[0]
            if t == "var":
                nm = re.match(r"new\s+([\w.]+)", d[2])
                t = nm.group(1) if nm else t
            scope[d[1]] = (t, d[2], _is_mock(d[2]))
    body = method.body
    found = []
    for m in re.finditer(r"\bperform\s*\(", body):
        hit = _mockmvc(project, jf, body, m.end(), scope)
        if hit:
            found.append((m.start(), hit))
    for m in re.finditer(r"\.\s*(get|post|put|patch|delete)\s*\(\s*\)\s*\.\s*uri\s*\(\s*\"§(\d+)\"", body):
        hit = _web_request(project, jf, m.group(1), jf.strings[int(m.group(2))][1:-1], body, m.end(), scope)
        if hit:
            found.append((m.start(), hit))
    for m in re.finditer(r"(\bnew\s+[\w.]+\s*(?:<[^>]*>)?\s*\()|\b(\w+)\s*\.\s*(\w+)\s*\(", body):
        hit = _call(project, jf, m, body, scope)
        if hit:
            found.append((m.start(), hit))
    found.sort(key=lambda x: x[0])
    return found[0][1] if found else None


def _is_mock(init: str) -> bool:
    return bool(re.match(r"(Mockito\.)?mock\s*\(", init.strip()))


def _args_at(body: str, open_paren: int) -> tuple[str, int]:
    """Text inside the parentheses that open just before open_paren, and the index after ')'."""
    depth = 1
    i = open_paren
    while i < len(body) and depth:
        if body[i] in "([{":
            depth += 1
        elif body[i] in ")]}":
            depth -= 1
        i += 1
    return body[open_paren:i - 1], i


def _call(project: _Project, jf: JFile, m: re.Match, body: str, scope) -> EntryPoint | None:
    if m.group(1):  # new Type(...).method(...)
        type_name = re.match(r"new\s+([\w.]+)", m.group(1)).group(1)
        _, after = _args_at(body, m.end())
        tail = re.match(r"\s*\.\s*(\w+)\s*\(", body[after:])
        if not tail:
            return None
        name = tail.group(1)
        args, _ = _args_at(body, after + tail.end())
        target = project.resolve(type_name, jf)
    else:
        recv, name = m.group(2), m.group(3)
        if recv in scope:
            type_name, _, mocked = scope[recv]
            if mocked:
                return None
        elif recv[:1].isupper():
            type_name = recv  # static call: Type.method(...)
        else:
            return None
        target = project.resolve(type_name, jf)
        args, _ = _args_at(body, m.end())
    if target is None:
        return None
    arg_list = split_top(args) if args.strip() else []
    found = project.find_method(target[0], target[1], name, len(arg_list))
    if not found:
        return None
    path, cls, meth = found
    inputs = tuple((meth.params[i][1] if i < len(meth.params) else str(i), _value(a, jf, scope))
                   for i, a in enumerate(arg_list))
    return _entry_point(project, path, cls, meth, inputs)


def _entry_point(project, path, cls, meth, inputs, via="") -> EntryPoint:
    sig = f"{meth.name}({', '.join(f'{_short(t)} {n}' for t, n in meth.params)})"
    return EntryPoint(path.relative_to(project.root).as_posix(), f"{cls.qual}.{meth.name}", sig, meth.line,
                      inputs, via)


def _short(type_text: str) -> str:
    return re.sub(r"\b(?:[a-z]\w*\.)+", "", " ".join(type_text.split()))


def _mockmvc(project, jf, body: str, start: int, scope) -> EntryPoint | None:
    args, _ = _args_at(body, start)
    m = re.match(r"\s*(?:MockMvcRequestBuilders\s*\.\s*)?(get|post|put|patch|delete|request)\s*\(\s*"
                 r"(?:HttpMethod\.(\w+)\s*,\s*)?\"§(\d+)\"", args)
    if not m:
        return None
    verb = (m.group(2) or m.group(1)).lower()
    uri_args, _ = _args_at(args, args.index("(", m.start(1)) + 1)
    return _web_request(project, jf, verb, jf.strings[int(m.group(3))][1:-1], args, 0, scope,
                        split_top(uri_args)[1:])


def _web_request(project, jf, verb: str, path: str, text: str, after: int, scope,
                 uri_vars: list[str] | None = None) -> EntryPoint | None:
    clean = path.split("?", 1)[0]
    for rverb, rx, names, fpath, cls, meth in project.routes():
        if rverb and rverb != verb:
            continue
        hit = rx.match(clean if not uri_vars else re.sub(r"\{\w+\}", "x", clean))
        if not hit:
            continue
        inputs: list[tuple[str, str]] = []
        values = uri_vars or list(hit.groups())
        for k, v in zip(names, values):
            inputs.append((k, _value(v, jf, scope) if uri_vars else v))
        rest = text[after:]
        for pm in re.finditer(r"\.\s*(?:param|queryParam)\s*\(\s*\"§(\d+)\"\s*,", rest):
            val, _ = _args_at(rest, pm.end())
            inputs.append((jf.strings[int(pm.group(1))][1:-1], _value(val, jf, scope)))
        cm = re.search(r"\.\s*(?:content|bodyValue)\s*\(", rest)
        if cm:
            val, _ = _args_at(rest, cm.end())
            body_param = meth.body_param or "body"
            inputs.append((body_param, _value(val, jf, scope)))
        return _entry_point(project, fpath, cls, meth, tuple(inputs), f"{verb.upper()} {path}")
    return None


def _value(text: str, jf: JFile, scope, depth: int = 0) -> str:
    """An argument as the user would read it: strings restored, locals replaced by what they hold,
    common wrappers dropped (`new BigDecimal("20.00")` -> 20.00)."""
    t = " ".join(text.split())
    w = re.fullmatch(r"\w+\s*\.\s*writeValueAsString\s*\((.*)\)", t)  # objectMapper.writeValueAsString(x)
    if w:
        t = w.group(1).strip()
    if t in scope and scope[t][1] and depth < 2:
        return _value(scope[t][1], jf, scope, depth + 1)
    t = re.sub(r"new\s+BigDecimal\s*\(\s*\"§(\d+)\"\s*\)", lambda m: jf.strings[int(m.group(1))][1:-1], t)
    t = re.sub(r"(?:new\s+BigDecimal|BigDecimal\s*\.\s*valueOf|Long\s*\.\s*valueOf|Integer\s*\.\s*valueOf)"
               r"\s*\(\s*([-\w.]+)\s*\)", r"\1", t)
    t = re.sub(r"\b(?:List\s*\.\s*of|Set\s*\.\s*of|Arrays\s*\.\s*asList)\s*\(\s*", "[", t)
    t = re.sub(r"\(\s+", "(", t)
    t = re.sub(r"\bnew\s+([\w.]+)\s*(?:<[^>]*>)?\s*\(", r"\1(", t)
    t = re.sub(r"\b(?:[a-z_]\w*\.)+(?=[A-Z])", "", t)  # com.acme.dto.Transfer -> Transfer
    t = _builder(t)
    t = jf.restore(t)
    if re.fullmatch(r'"\s*\{.*\}\s*"', t):  # a JSON body in a string
        t = t[1:-1].replace('\\"', '"').strip()
    if t.count("[") > t.count("]"):  # List.of(a, b) became [a, b): close the list
        t = _close_lists(t)
    return t if len(t) <= MAX_VALUE else t[: MAX_VALUE - 1] + "…"


def _builder(t: str) -> str:
    """Type.builder().a(1).b(2).build() -> Type(a=1, b=2)."""
    m = re.fullmatch(r"([\w.]+)\s*\.\s*builder\s*\(\s*\)(.*)\.\s*build\s*\(\s*\)", t.strip())
    if not m:
        return t
    fields, rest = [], m.group(2)
    while rest.strip():
        f = re.match(r"\s*\.\s*(\w+)\s*\(", rest)
        if not f:
            return t
        val, end = _args_at(rest, f.end())
        fields.append(f"{f.group(1)}={val.strip()}")
        rest = rest[end:]
    return f"{m.group(1)}({', '.join(fields)})"


def _close_lists(t: str) -> str:
    out, stack = [], []
    for ch in t:
        if ch in "[(":
            stack.append(ch)
        elif ch == ")" and stack and stack[-1] == "[":
            stack.pop()
            out.append("]")
            continue
        elif ch in ")]" and stack:
            stack.pop()
        out.append(ch)
    return "".join(out)
