"""Which code is the test, and which function the test is about.

The test only drives the code under test. These pure rules let the trace tag
test code (so the viewer can hide it) and pick the function the viewer opens
on. No I/O.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

# src/<source set>/<language>/...: Maven and Gradle source sets. Only test sets hold tests.
_SOURCE_SET = re.compile(r"(?:^|/)src/([^/]+)/(?:java|kotlin|groovy|scala|resources)/")
# Methods the framework calls around the request rather than code the test is about.
FRAMEWORK_HOOKS = {
    "Bean", "InitBinder", "ModelAttribute", "PostConstruct", "PreDestroy", "EventListener",
    "BeforeEach", "AfterEach", "BeforeAll", "AfterAll", "Before", "After",
    "pytest.fixture", "fixture",
}
_ACCESSOR = re.compile(r"(?:get|set|is|has)[A-Z_]\w*$")
_CTOR = {"__init__", "__new__", "__post_init__"}
_WORD = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")


def is_test_source_set(name: str) -> bool:
    """test, testFixtures, integrationTest, it, dockerTest…: a JVM source set that holds tests."""
    return name == "it" or "test" in name.lower()


def is_test_file(rel: str) -> bool:
    """Test code: pytest test files and conftest, JUnit test classes, files in a JVM test source set
    (src/test/**, src/integrationTest/**…), and anything under a tests/ or test/ folder outside
    a JVM source set. A `test` package inside src/main/java is production code."""
    name = rel.rsplit("/", 1)[-1]
    if (name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py"
            or name.endswith(("Test.java", "Tests.java", "IT.java"))):
        return True
    source_set = _SOURCE_SET.search(rel)
    if source_set:
        return is_test_source_set(source_set.group(1))
    return "/tests/" in f"/{rel}" or "/test/" in f"/{rel}"


def in_test_code(functions: dict[str, dict], frames: dict[str, dict]) -> set[str]:
    """Ids of functions that are test code: those in test files, plus the entry frame's function."""
    out = {fid for fid, fn in functions.items() if is_test_file(fn["file"])}
    out |= {f["fn"] for f in frames.values() if f.get("parent") is None}
    return out


def leading_annotations(source: str, line: int) -> set[str]:
    """Annotation / decorator names on the declaration that starts at `line` (1-based):
    `@Bean`, `@pytest.fixture(...)` -> {"Bean", "pytest.fixture"}."""
    out: set[str] = set()
    for text in source.splitlines()[line - 1:line + 19]:
        text = text.split("//", 1)[0].split("#", 1)[0]
        if not text.lstrip().startswith("@"):
            # the declaration line: `@Bean Foo foo(@Valid Foo f)` -> only what precedes the parameters
            out.update(re.findall(r"@([\w.]+)", text.split("(", 1)[0]))
            break
        out.update(re.findall(r"@([\w.]+)", text))
    return out


def _words(text: str) -> list[str]:
    """Lower-case words, one plural `s` dropped: 'processFindForms' -> [process, find, form]."""
    words = [w.lower() for w in _WORD.findall(text)]
    return [w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w for w in words]


def _method(qualname: str) -> tuple[str, str]:
    """('Owner', 'setName') from 'Owner.setName'; ('Email', '<init>') from 'new Email'."""
    if qualname.startswith("new "):
        return qualname[4:].rsplit(".", 1)[-1], "<init>"
    cls, _, method = qualname.rpartition(".")
    return cls.rsplit(".", 1)[-1], method


def _contains(seq: list[str], sub: list[str]) -> bool:
    return bool(sub) and any(seq[i:i + len(sub)] == sub for i in range(len(seq) - len(sub) + 1))


def subject_of(test_qualname: str) -> tuple[str, list[str]]:
    """What a test names: (class under test, words of the test method).
    'OwnerControllerTests.processFindFormSuccess' -> ('OwnerController', [process, find, form, succes])."""
    cls, method = _method(test_qualname)
    for suffix in ("Tests", "Test", "IT", "Spec"):
        if cls.endswith(suffix) and len(cls) > len(suffix):
            cls = cls[: -len(suffix)]
            break
    else:
        cls = cls[4:] if cls.startswith("Test") else ""
    words = [w for w in _words(method) if w not in ("test", "should", "when", "then", "given", "expect")]
    return cls, words


def function_under_test(
    steps: list[dict],
    frames: dict[str, dict],
    test_fns: set[str],
    lines_of: dict[str, Iterable[int]],
    changed: dict[str, set[int]],
    functions: dict[str, dict] | None = None,
    annotations: dict[str, set[str]] | None = None,
) -> str | None:
    """The production function the viewer should open on.

    The first production function (in call order) that contains a PR-changed
    line. Without one, the best match for what the test names, in this order:
    the test's method name and class (`OwnerControllerTests#processFindFormSuccess`
    -> `OwnerController.processFindForm`), its method name, its class, then any
    real production code; among functions the test calls itself first. Constructors, getters/setters, framework hooks
    (`@Bean`, `@InitBinder`, `@ModelAttribute`…) and calls that ran no line (an
    inline mock) only win when nothing else ran.
    lines_of: function id -> its source lines; changed: file -> changed lines;
    functions: id -> {"qualname": …}; annotations: id -> annotation names.
    """
    functions = functions or {}
    annotations = annotations or {}
    called: list[str] = []
    ran_lines: dict[str, int] = {}
    calls: dict[str, int] = {}
    from_test: set[str] = set()  # called straight from test code: the test's own act/arrange calls
    entry = None
    for step in steps:
        frame = frames[str(step["frame"])]
        fid = frame["fn"]
        if step["kind"] == "line":
            ran_lines[fid] = ran_lines.get(fid, 0) + 1
        if step["kind"] != "call":
            continue
        calls[fid] = calls.get(fid, 0) + 1
        parent = frames.get(str(frame.get("parent")))
        if entry is None and parent is None:
            entry = fid
        if parent is not None and parent["fn"] in test_fns:
            from_test.add(fid)
        if fid not in test_fns and fid not in called:
            called.append(fid)
    for fid in called:
        file = fid.rsplit(":", 1)[0]
        if changed.get(file, set()) & set(lines_of.get(fid, ())):
            return fid
    if not called:
        return None
    if not functions:
        return called[0]

    callers = {frames[str(f["parent"])]["fn"] for f in frames.values()
               if f.get("parent") is not None and str(f["parent"]) in frames}
    test_cls, test_words = subject_of(functions.get(entry, {}).get("qualname", "")) if entry else ("", [])

    def rank(fid: str) -> int:
        cls, method = _method(functions.get(fid, {}).get("qualname", ""))
        trivial = (
            method == "<init>" or method in _CTOR
            or bool(annotations.get(fid, set()) & FRAMEWORK_HOOKS)
            or not ran_lines.get(fid)
        )
        accessor = (bool(_ACCESSOR.match(method)) and fid not in callers
                    and ran_lines.get(fid, 0) <= 2 * calls.get(fid, 1))
        words = _words(method)
        names_method = (not trivial and len(method) >= 4 and _contains(test_words, words))
        names_class = bool(test_cls) and cls == test_cls
        if names_method and names_class:
            return 0
        if names_method:
            return 1
        if trivial:
            return 5
        if accessor:
            return 4
        return 2 if names_class else 3

    # What the test calls itself beats what that code calls in turn (the controller, not the
    # service it delegates to). Naming the class under test beats that (a controller behind a
    # filter), and plain production code beats setters, constructors and mocks the test ran.
    ranks = {fid: rank(fid) for fid in called}
    order = [(0, False), (1, True), (2, False), (3, True), (1, False), (3, False), (4, False), (5, False)]
    for r, direct_only in order:
        for fid in called:
            if ranks[fid] == r and (fid in from_test or not direct_only):
                return fid
    return called[0]
