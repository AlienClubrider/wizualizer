# Architecture

```
            ┌──────────── web/server.py (functions page, API) ─── cli.py (wiz, record, list, run)
            ▼
      workspace.py  ── use cases: list functions, record one (cached), find changed ones
       │    │    │
       │    │    └─ store.py   .wiz/ cache, keyed by test + source hash + diff base
       │    └────── impact.py  which tests run changed lines (pure rules)
       └─────────── languages.py ── python/ , java/  (adapters)

test ──▶ Recorder ──▶ recording ──▶ make_trace() ──▶ trace.json ──▶ viewer (HTML)
                                     ▲     ▲
                         SourceModel ┘     └ git diff (diff.py)
```

Dependencies point inward. The pure rules (`change_coverage.py`, `focus.py`,
`impact.py`, `diff.parse_unified_diff`) do no I/O and have unit tests. A
language plugs in through two ports in `ports.py` (`Recorder`, `SourceModel`)
plus discovery and a change probe (`languages.py`).

- `project.py`: what suites a directory holds (pytest, Maven, Gradle) and
  what is missing to run them.
- `discovery.py`: tests as flows. pytest `--collect-only`; Java by reading
  test sources.
- `functions.py`: the functions under test and their runs, labelled by the
  inputs that differ (pure rules). `python/entry.py` and `java/entry.py` read
  which production function each test calls from its source; a recording's
  summary (stored in the `.wiz/` index) confirms or corrects it.
- `python/`: `sys.monitoring` tracer and pytest plugin (stdlib only, loaded
  into the project's own interpreter via `PYTHONPATH`), the change-probe
  plugin, AST statements and flowcharts.
- `java/`: build detection, the JDI recorder and probe adapters (with
  `diagnose.py`: why a build never reached the test, in one line), flowcharts,
  and `helper/` (the Java side).
- `viewer/`: the HTML/CSS/JS, inlined by `render.py` into one file with a
  Content-Security-Policy that blocks all network access. `theme.css` holds
  both palettes, shared with the functions page.
- `web/`: the local server (`http.server`, 127.0.0.1 only; refuses other
  `Host` headers and POSTs without an `X-Wiz` header) and the functions page.
- Trace format: [trace-format.md](trace-format.md).

## Dependencies

None at runtime: the standard library, plus the project's own pytest or the
JDK's own `jdk.jdi`/`jdk.compiler`. `pytest` is a dev dependency.
