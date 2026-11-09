# wizualizer

Step through the code a PR changes, from its tests: flowchart, variables,
sequence diagram, and which changed lines the test actually ran.

`wiz run` runs **one test** under a recorder, writes what the code did step by
step to `trace.json`, and turns it into **one self-contained HTML file** you
open in any browser. Fully local: no network calls, no CDN, no telemetry (the
HTML even sets a Content-Security-Policy that blocks all network access).

![Flowchart mid-play](docs/screenshots/flowchart.png)

What you get:

- **Flowchart** of the function being run (follows calls, or pin one), with the
  current step lit, the path taken so far, PR-changed nodes striped, and
  changed nodes the test never reached outlined in red.
- **Variables** of the current frame: values at this step, what just changed
  (old value struck through), the call stack.
- **Sequence diagram** of calls and returns between objects and modules.
- **Source** with the current line and the PR's changed lines.
- **PR lines**: changed-line coverage. Which lines the PR changed ran in this
  test, and which never ran. Shown as a headline number at the top too.
- Play / pause / step / scrub / speed, "next Δ" to jump to the next changed
  line that runs. Keys: space, ← →, Home/End, `n`.
- Works on a phone: views become tabs (flowchart first), controls stick to the
  bottom with big tap targets, diagrams scroll inside their box and have zoom
  buttons.

![PR changed-line coverage](docs/screenshots/coverage.png)

## Install

Needs Python 3.12+ (the recorder uses `sys.monitoring`) and git.

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

No runtime dependencies. `pytest` is a dev dependency here; the project you
record needs its own pytest.

## Demo (one command)

```sh
./examples/demo.sh
```

It builds a throwaway git repo in `.wiz/demo-shop` from `examples/shop` (a
small order-pricing service): `main` is the code before the PR, branch
`free-shipping` applies `examples/shop-pr.patch` (free shipping over $50, a
heavy-parcel surcharge, coupon minimum spend). Then it records
`tests/test_orders.py::test_order_over_50_ships_free_with_coupon` and prints
the HTML path. Expect `4 ran, 5 never ran`: the test never takes the paid
shipping path or the coupon-minimum rejection, which is exactly what a
reviewer should notice.

## Use it on a real repo

```sh
cd ~/work/some-service                     # your checkout, on the PR branch
/path/to/wizualizer/.venv/bin/wiz run tests/test_billing.py::test_refund --base main
# → .wiz/tests_test_billing.py__test_refund.html (+ .json)
```

- `--base REF`: changed lines are everything since `git merge-base REF HEAD`,
  including uncommitted and untracked files. Leave it out to skip PR marking.
- `--python PATH`: interpreter to run pytest with. Default: the project's
  `.venv/bin/python` (or `venv/`), else the one running `wiz`. It must be 3.12+
  and have pytest; wizualizer itself does not need to be installed there.
- `--max-steps N` (default 10000): recording stops there and the viewer says
  "truncated".
- `--out DIR` (default `.wiz`). Add `.wiz/` to your `.gitignore`.
- A node id that matches several tests (a file, a parametrized test) records
  the first one only.

What gets recorded: code under the git root, minus virtualenvs and
site-packages. Test functions are recorded (they are the entry point) but test
files are left out of the coverage number. Only the test call phase is traced;
fixtures are not.

## Limits

- Python only for now. Java and TypeScript are planned through the Debug
  Adapter Protocol: [docs/next-languages.md](docs/next-languages.md).
- Values are rendered strings, bounded (depth 3, 8 items, 240 chars). Objects
  with a custom `__repr__` are shown with it, so a slow or side-effecting
  `__repr__` runs during recording.
- Main thread only. Threads, subprocesses and async tasks running on other
  threads are not recorded.
- Comprehensions, generator expressions and lambdas are folded into their
  enclosing function.
- Flowcharts are approximate around `try`/`finally` (a `return` inside `try`
  is drawn straight to the end) and treat `match` cases as a chain of checks.
- Coverage judges statements, not branches: `x = a if c else b` counts as run
  either way. Module- and class-level lines are "import-time" and not judged.
- Memory (heap) view is not built yet: [docs/memory-view.md](docs/memory-view.md).

## How it fits together

```
test ──▶ Recorder ──▶ recording ──▶ make_trace() ──▶ trace.json ──▶ viewer (HTML)
                                     ▲     ▲
                         SourceModel ┘     └ git diff --base
```

- `src/wizualizer/ports.py`: the Recorder and SourceModel interfaces.
- `src/wizualizer/python/`: sys.monitoring tracer + pytest plugin (stdlib only,
  loaded into the project's own interpreter), AST statement map and flowchart
  builder.
- `src/wizualizer/diff.py`, `change_coverage.py`, `trace.py`: language-neutral.
- `src/wizualizer/viewer/`: the HTML/CSS/JS, inlined by `render.py`.
- Trace format: [docs/trace-format.md](docs/trace-format.md).

## Development

```sh
.venv/bin/pytest            # unit + integration tests
node scripts/screenshots.js # re-render docs/screenshots (needs Playwright)
```
