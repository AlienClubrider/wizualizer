"""The functions under test, each with its runs: what the web app lists.

A test is only a way to run a production function. The page lists the
functions; each run of one comes from a test, labelled by what differs between
the runs (their inputs), not by the test's name. Which function a test is about
is first read from the test's source (static, before anything runs) and then
refined from its recording. Pure rules, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field

MAX_LABEL = 90
MAX_PART = 40


@dataclass(frozen=True)
class EntryPoint:
    """A production function a test calls, and with what."""

    file: str  # relative to the project root
    qualname: str  # as the recorders name it: "QuoteService.quote", "shipping_cents", "new Account"
    signature: str = ""  # "quote(QuoteRequest request)"
    line: int = 0
    inputs: tuple[tuple[str, str], ...] = ()  # (parameter, value as text), in parameter order
    via: str = ""  # how the test reaches it, if not a plain call: "POST /quotes"
    result: str = ""  # what it returned (or raised), once recorded

    @property
    def key(self) -> str:
        return f"{self.file}::{self.qualname}"

    def to_json(self) -> dict:
        return {"key": self.key, "file": self.file, "qualname": self.qualname, "signature": self.signature,
                "line": self.line, "inputs": [list(kv) for kv in self.inputs], "via": self.via,
                "result": self.result}

    @classmethod
    def from_json(cls, d: dict) -> "EntryPoint":
        return cls(d["file"], d["qualname"], d.get("signature", ""), d.get("line", 0),
                   tuple((k, v) for k, v in d.get("inputs", [])), d.get("via", ""), d.get("result", ""))


@dataclass
class Run:
    flow_id: str
    test: str  # the test's own name, shown small as the run's source
    inputs: tuple[tuple[str, str], ...]
    recorded: bool  # the function was confirmed by a recording
    result: str = ""
    label: str = ""


@dataclass
class Function:
    key: str
    file: str
    qualname: str
    signature: str
    line: int
    language: str
    via: str = ""
    known: bool = True  # False: the tests whose function is found only by recording them
    runs: list[Run] = field(default_factory=list)

    @property
    def display(self) -> str:
        """Class.method, or module.function for a Python function outside a class."""
        if self.language == "python" and "." not in self.qualname and self.file:
            module = self.file.rsplit("/", 1)[-1].removesuffix(".py")
            return f"{module}.{self.qualname}" if module != "__init__" else self.qualname
        return self.qualname


def choose(static: EntryPoint | None, recorded: dict | None) -> tuple[EntryPoint | None, bool]:
    """The function a test is about: (entry point, confirmed by a recording).

    recorded is a recording summary: {"entry": EntryPoint json or None, "calls": {key: EntryPoint json}}.
    The static guess stands when the recording called it (with the recorded inputs); else the
    first production function the test called itself.
    """
    if recorded:
        calls = recorded.get("calls") or {}
        if static and static.key in calls:
            got = EntryPoint.from_json(calls[static.key])
            return EntryPoint(static.file, static.qualname, static.signature or got.signature,
                              static.line or got.line, got.inputs or static.inputs, static.via, got.result), True
        if recorded.get("entry"):
            return EntryPoint.from_json(recorded["entry"]), True
    return static, False


def catalog(flows: list, static: dict[str, EntryPoint | None], recorded: dict[str, dict]) -> list[Function]:
    """Functions under test, in first-seen test order, then one group for tests not placed yet.

    flows: discovery.Flow; static: flow id -> entry point read from the test source;
    recorded: flow id -> recording summary (see choose).
    """
    by_key: dict[str, Function] = {}
    unknown: dict[str, Function] = {}
    for flow in flows:
        entry, confirmed = choose(static.get(flow.id), recorded.get(flow.id))
        run = Run(flow.id, flow.name, entry.inputs if entry else (), confirmed, entry.result if entry else "")
        if entry is None:
            fn = unknown.setdefault(flow.language, Function(
                f"?{flow.language}", "", "", "", 0, flow.language, known=False))
        else:
            fn = by_key.setdefault(entry.key, Function(
                entry.key, entry.file, entry.qualname, entry.signature, entry.line, flow.language, entry.via))
            fn.signature = fn.signature or entry.signature
            fn.via = fn.via or entry.via
        fn.runs.append(run)
    out = list(by_key.values()) + list(unknown.values())
    for fn in out:
        if fn.known:
            labels = run_labels([r.inputs for r in fn.runs], [r.result for r in fn.runs])
        else:
            labels = [humanize(r.test) for r in fn.runs]
        for r, label in zip(fn.runs, labels):
            r.label = label
    return out


def run_labels(inputs: list[tuple[tuple[str, str], ...]], results: list[str] | None = None) -> list[str]:
    """One label per run: the inputs that differ between the runs (all of them if none differ).

    Object and dict values are compared field by field, so two requests that differ only in
    their amount are labelled by the amount. Runs with the same inputs (the mocks differ) are told
    apart by what they returned, when recorded, else numbered."""
    flat = [dict(_flatten(run)) for run in inputs]
    keys: list[str] = []
    for run in flat:
        keys += [k for k in run if k not in keys]
    differ = [k for k in keys if len({v.get(k) for v in flat}) > 1]
    if not differ:  # nothing differs: show the inputs as written
        flat = [dict(run) for run in inputs]
        keys = []
        for run in flat:
            keys += [k for k in run if k not in keys]
        differ = keys
    labels = []
    for i, v in enumerate(flat):
        parts = [_part(k, v[k]) for k in differ if k in v]
        label = ", ".join(parts) if parts else ("no inputs" if len(flat) == 1 else f"run {i + 1}")
        labels.append(label if len(label) <= MAX_LABEL else label[: MAX_LABEL - 1] + "…")
    results = results or [""] * len(labels)
    before = list(labels)
    for i, label in enumerate(before):
        twins = [j for j, other in enumerate(before) if other == label]
        if len(twins) > 1 and results[i] and len({results[j] for j in twins}) > 1:
            labels[i] = f"{label} → {_clip(results[i], MAX_PART)}"
    if len(set(labels)) < len(labels):  # same inputs (or none): number them so the runs stay apart
        seen: dict[str, int] = {}
        counts = {label: labels.count(label) for label in labels}
        for i, label in enumerate(labels):
            if counts[label] > 1:
                seen[label] = seen.get(label, 0) + 1
                labels[i] = f"{label} · run {seen[label]}"
    return labels


def _part(key: str, value: str) -> str:
    name = key.rsplit(".", 1)[-1]
    value = _clip(value, MAX_PART)
    return value if name.isdigit() else f"{name}={value}"


def _clip(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def _flatten(run: tuple[tuple[str, str], ...]) -> list[tuple[str, str]]:
    out = []
    for k, v in run:
        fields = object_fields(v)
        if fields:
            out += [(f"{k}.{fk}", fv) for fk, fv in fields]
        else:
            out.append((k, v))
    return out


def object_fields(text: str) -> list[tuple[str, str]] | None:
    """Fields of a value written as `Type(a=1, b=2)`, `{'a': 1}`, `{"a": 1}` or `{a=1}`, else None."""
    text = text.strip()
    if text.endswith(")") and "(" in text and text[: text.index("(")].replace(".", "").isidentifier():
        inner = text[text.index("(") + 1:-1]
    elif text.startswith("{") and text.endswith("}"):
        inner = text[1:-1]
    else:
        return None
    fields = []
    for item in split_top(inner):
        for sep in ("=", ":"):
            k, found, v = item.partition(sep)
            k = k.strip().strip("'\"")
            if found and k.replace("_", "a").replace("-", "a").isalnum():
                fields.append((k, v.strip()))
                break
        else:
            return None
    return fields or None


def split_top(text: str, sep: str = ",") -> list[str]:
    """Split at sep outside brackets and quotes."""
    parts, depth, quote, cur = [], 0, "", ""
    for ch in text:
        if quote:
            cur += ch
            if ch == quote and not cur.endswith("\\" + quote):
                quote = ""
            continue
        if ch in "'\"":
            quote = ch
        elif ch in "([{<":
            depth += 1
        elif ch in ")]}>":
            depth -= 1
        elif ch == sep and depth == 0:
            parts.append(cur.strip())
            cur = ""
            continue
        cur += ch
    if cur.strip():
        parts.append(cur.strip())
    return parts


def humanize(test_name: str) -> str:
    """test_small_order_pays_shipping / smallOrderPaysShipping -> 'small order pays shipping'."""
    name = test_name.split("[", 1)[0]
    for prefix in ("test_", "test"):
        if name.startswith(prefix) and len(name) > len(prefix):
            name = name[len(prefix):]
            break
    words: list[str] = []
    for part in name.replace("_", " ").split():
        cur = ""
        for ch in part:
            if ch.isupper() and cur and not cur[-1].isupper():
                words.append(cur)
                cur = ch
            else:
                cur += ch
        words.append(cur)
    text = " ".join(w if sum(c.isupper() for c in w) > 1 else w.lower() for w in words if w)
    param = test_name[len(test_name.split("[", 1)[0]):]
    return (text or test_name) + (f" {param}" if param else "")


MAX_CALLS = 300
_SELF = ("self", "cls", "this")


def recording_summary(trace: dict) -> dict:
    """What a recording says about the functions a test ran: the first production function the
    test called itself (entry) and every production function called, with its first inputs."""
    functions, frames = trace["functions"], trace["frames"]
    calls: dict[str, dict] = {}
    first_frame: dict[int, str] = {}  # frame of a function's first call -> its key
    entry = None
    for step in trace["steps"]:
        if step["kind"] == "return" and step["frame"] in first_frame:
            key = first_frame.pop(step["frame"])
            calls[key]["result"] = (f"raises {step.get('type', '')}" if step.get("raised")
                                    else str(step.get("value", "")))
            continue
        if step["kind"] != "call" or step.get("resume"):
            continue
        frame = frames[str(step["frame"])]
        fn = functions[frame["fn"]]
        if fn.get("test"):
            continue
        ep = EntryPoint(fn["file"], fn["qualname"], _start_label(fn), fn["line"],
                        tuple((k, v) for k, v in (step.get("args") or {}).items() if k not in _SELF))
        if ep.key not in calls and len(calls) < MAX_CALLS:
            calls[ep.key] = ep.to_json()
            first_frame[step["frame"]] = ep.key
        parent = frames.get(str(frame.get("parent"))) if frame.get("parent") is not None else None
        if entry is None and (parent is None or functions[parent["fn"]].get("test")):
            entry = calls.get(ep.key, ep.to_json())
    return {"entry": entry, "calls": calls}


def _start_label(fn: dict) -> str:
    nodes = (fn.get("flowchart") or {}).get("nodes") or []
    return nodes[0]["label"] if nodes else ""
