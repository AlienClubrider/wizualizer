# trace.json format (schema `wizualizer.trace`, version 1)

`wiz run` writes this next to the HTML viewer (`.wiz/<test>.json`). It is the
contract between recorders (one per language) and the viewer. The viewer only
reads this file; it knows nothing about Python.

Rules: all paths are relative to the project root with `/` separators. All
values are strings made by a bounded, crash-proof renderer (depth 3, 8 items
per container, 80 chars per string, 240 chars total). Line numbers are 1-based.
A new required field, or a changed meaning, bumps `version`.

```jsonc
{
  "schema": "wizualizer.trace",
  "version": 1,
  "meta": {
    "test": "tests/test_orders.py::test_x",   // what was recorded
    "outcome": "passed",                       // passed | failed | skipped | not run
    "message": "",                             // failure text, if any
    "note": "",                                // e.g. "node id matched 3 tests; recorded only the first"
    "duration_ms": 8.6,
    "language": "python",                      // python | java
    "recorder": "python-sys.monitoring",       // java: "java-jdi"
    "created": "2026-11-09T05:00:00+00:00",
    "truncated": false,                        // true if the step budget ran out
    "max_steps": 10000,
    "diff": { "base": "main", "merge_base": "56d715886b09", "head": "f69f584" }  // null without --base
  },

  // Source of every file that ran or that the PR changed (for the source view).
  "files": {
    "shop/pricing.py": {
      "source": "…",
      "changed": [4, 6, 7, 27, 28],            // PR: added/modified lines (new-file numbers)
      "removed_after": [12]                    // PR: lines were deleted right after these
    }
  },

  // Every function that ran. Key = "<file>:<first line>" (decorator line if decorated).
  "functions": {
    "shop/pricing.py:32": {
      "name": "shipping_cents", "qualname": "shipping_cents",
      "file": "shop/pricing.py", "line": 32,
      "flowchart": {                            // null if source was unavailable
        "nodes": [
          // in source order; lines = the source span the node owns ([0,0] for end)
          { "id": 0, "kind": "start", "label": "shipping_cents(subtotal_cents, weight_g)", "lines": [32, 32], "depth": 0 },
          { "id": 1, "kind": "decision", "label": "if subtotal_cents >= FREE_SHIPPING_FROM_CENTS", "lines": [33, 33], "depth": 1 }
        ],
        "edges": [ { "from": 0, "to": 1 }, { "from": 1, "to": 2, "label": "yes" } ]
      }
    }
  },

  // One entry per function activation. actor = who acts in the sequence diagram:
  // an object ("Cart#1"), a class (classmethods) or a module ("pricing").
  "frames": {
    "5": { "fn": "shop/orders.py:21", "parent": 1, "actor": "OrderService#1" }
  },

  // What happened, in order. The viewer's play/step/scrub walks this list.
  "steps": [
    { "kind": "call", "frame": 5, "line": 21, "args": { "self": "OrderService(…)", "request": "{…}" } },
    { "kind": "line", "frame": 5, "line": 22, "locals": { "self": "…", "request": "…" } },
    { "kind": "exception", "frame": 9, "line": 26, "type": "ValueError", "message": "…" },
    { "kind": "return", "frame": 5, "line": 30, "value": "Receipt(…)" },
    { "kind": "return", "frame": 9, "line": 26, "raised": true, "type": "ValueError", "message": "…" }
  ],

  // PR changed-line coverage; null without --base.
  "coverage": {
    "files": { "shop/pricing.py": { "27": "ran", "28": "not_run", "4": "import_time" } },
    "summary": { "ran": 4, "not_run": 5, "import_time": 4, "neutral": 0, "measured": 9, "percent": 44 },
    "skipped_test_files": ["tests/test_orders.py"]
  }
}
```

## Java traces

Java uses the same version 1 schema: nothing new is required and no field
changes meaning. What the values look like:

- `functions` keys are `<file>:<line>` with the line of the method's first
  annotation or modifier. Constructors are named `new Account` (qualname
  `new Outer.Account`), methods `Account.withdraw`.
- `locals` holds the locals in scope (parameters included) and then one
  `this.<field>` entry per instance field. Values are Java-flavoured:
  `"text"`, `'c'`, `null`, `Account(id=1, balance=100)`, `[1, 2]`,
  `{key=value}`, `Kind.CREDIT`, `2026-11-09`, `mock UserAccountService`.
- `return` of a `void` method has `"value": "void"`; a constructor's value
  is the new object.
- `meta.note` says which build ran the test, e.g. `"gradle in server"`.
- Coverage: `import_time` means "runs at class load" (static field
  initializers, static blocks). Instance field initializers are judged like
  statements (they run in the constructor). Declarations without an
  initializer, `else`, `try`, `finally`, `case` labels and closing braces are
  `neutral`: javac emits no code for them. Test files also include
  `*Test.java`, `*Tests.java`, `*IT.java` and anything under `test/`.

## Step semantics

- `line`: the line is **about to run**; `locals` is the frame's state before it
  (debugger convention). A variable "changed" at step *n* was changed by the
  previous line of the same frame.
- `call` opens a frame (`args` are the parameters). `"resume": true` means a
  generator or coroutine resumed an existing frame.
- `return` closes a frame: `value` normally; `"yield": true` for a generator
  suspending (the frame stays alive); `"raised": true` with `type`/`message`
  when it exits by exception.
- `exception` is where an exception was raised inside project code (it may be
  caught later).

Comprehensions, generator expressions and lambdas are folded into the function
that contains them: they get no frame of their own.

## Coverage statuses

Each changed line is judged by the statement that owns it (so the second line
of a multi-line call counts as run when the call ran).

| status | meaning |
|---|---|
| `ran` | the statement ran in this test (a changed `def` line counts as run if the function was called) |
| `not_run` | executable inside a function, but never ran in this test |
| `import_time` | module or class level: it runs on import, a test run can't judge it |
| `neutral` | blank line, comment, `else:`/`finally:` and the like |

`percent` = ran / (ran + not_run). Test files (`test_*.py`, `*_test.py`,
`conftest.py`, anything under `tests/` or `test/`) are excluded.
