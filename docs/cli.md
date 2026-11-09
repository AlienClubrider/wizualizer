# CLI reference

Run every command in the project root (where `pyproject.toml`, `pytest.ini`,
`pom.xml` or `build.gradle(.kts)` is, or the repo above a `server/`-style
build folder; build files are looked for up to 3 levels down).

## `wiz` / `wiz serve`

Starts the web app on `127.0.0.1` and prints its URL. Port 7357 if free, else
any free port. It opens a browser only on a desktop (macOS, Windows, or Linux
with `$DISPLAY`/`$WAYLAND_DISPLAY`); on a server, open the printed URL.

The page lists every test as a flow, grouped by file and class, with search
(press `/`). Open a flow: it is recorded (progress shows on the row) and the
viewer opens. Recordings are cached in `.wiz/`, so reopening is instant until a
source file or the diff base changes. "← flows" in the viewer goes back.

| Option | |
|---|---|
| `--port N` | listen on this port only |
| `--no-open` | never open a browser |
| `--base REF` | what "changed" means: lines changed since `git merge-base REF HEAD`, plus uncommitted and untracked files. Default: `main`, else `master`, else `origin/main`, `origin/master`; with none of those, only uncommitted work |
| `--python PATH` | interpreter for pytest. Default: the project's `.venv`/`venv`, else `$VIRTUAL_ENV`, else a `python3` on PATH that has pytest |
| `--java-home DIR` | JDK. Default: `$JAVA_HOME`, else `javac` on PATH |
| `--max-steps N` | stop recording a test after N steps (default 10000); the viewer says "truncated" |

## `wiz record`

```sh
wiz record --all                     # every flow that is not cached yet
wiz record 'tests/test_x.py::test_y' # or name them (ids as `wiz list` prints them)
```

Same options as `serve` (minus port/browser). Exit code 1 if any test could not
be recorded (a failing test still records fine).

## `wiz list`

Prints every flow id. `wiz list --changed` prints only the flows that run
changed lines, with the count (see [only-changes.md](only-changes.md)).

## `wiz run <test>`

Records one test and writes a self-contained `.wiz/<test>.html` (+ `.json`)
you can open anywhere or attach to a CI run. No server.

- `<test>`: a pytest node id (`tests/test_billing.py::test_refund`) or a Java
  test (`com.acme.BankTest#movesMoney`, simple class name if unique, or a path
  to the `.java` file; nested: `OuterTest$Inner#method`).
- `--base REF`: mark changed lines (leave out to skip).
- `--out DIR` (default `.wiz`), `--python`, `--java-home`, `--max-steps`.
- `--lang python|java`: when the id is ambiguous.
- `--test-steps`: open with the test's own steps shown (see below).
- A node id that matches several tests records the first one only.

## The viewer

Opens on the **function under test**: the first function the change touches
that the test reaches, else the first one the test calls. The test's own
steps are recorded but hidden; "show test steps" under the flowchart brings
them back. Test code is `test_*.py`, `*_test.py`, `conftest.py`,
`*Test.java`, `*Tests.java`, `*IT.java`, and anything under `tests/` or
`test/`. Changed lines in test files are listed as "test changes", outside the
coverage number.

Keys: space play/pause, ← → step, Home/End, `n` next changed line. The ☾/☀
button switches dark and light (remembered; follows the system until you pick).
On a phone the views become tabs and the controls stick to the bottom.
