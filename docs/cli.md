# CLI reference

Run every command in the project root (where `pyproject.toml`, `pytest.ini`,
`pom.xml` or `build.gradle(.kts)` is, or the repo above a `server/`-style
build folder; build files are looked for up to 3 levels down).

## `wiz` / `wiz serve`

Starts the web app on `127.0.0.1` and prints its URL. Port 7357 if free, else
any free port. It opens a browser only on a desktop (macOS, Windows, or Linux
with `$DISPLAY`/`$WAYLAND_DISPLAY`); on a server, open the printed URL.

The page lists the **functions under test** (`Class.method` or
`module.function`, with a short signature and, for a web request, the route),
20 to a page, with search (press `/`) over names, inputs and files. Each
function shows its **runs**: one per test that calls it, labelled by the inputs
that differ between them (`amount=20.00, to="USD"`); the test's name is the
small "from" line. A recording tells runs with the same inputs apart by what
they returned.

Which function a test is about is read from the test's source before anything
runs: the first project function it calls (on an object it built, a field, a
fixture, `@InjectMocks`/`@Autowired`, a static call), and for MockMvc or
WebTestClient the controller method whose `@*Mapping` matches the request.
After a recording, what actually ran confirms it or corrects it. Tests where
nothing is found sit under "Not placed yet" until opened.

Open a run: its test is recorded (the build's progress shows while it runs)
and the viewer opens on that function. Recordings are cached in `.wiz/`, so
reopening is instant until a source file or the diff base changes. If a
recording fails, the row says why in one line, with a fix and the whole build
output (also in `.wiz/logs/`). "← functions" in the viewer goes back.

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
wiz record --all                     # every test that is not recorded yet
wiz record 'tests/test_x.py::test_y' # or name them (ids as `wiz list` prints them)
```

Same options as `serve` (minus port/browser). Exit code 1 if any test could not
be recorded (a failing test still records fine).

## `wiz list`

Prints every test id. `wiz list --changed` prints only the tests that run
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

Opens on the **function under test**: the one picked on the functions page,
else the first function the change touches that the test reaches, else the
first one the test calls. The test's own
steps are recorded but hidden; "show test steps" under the flowchart brings
them back. Test code is `test_*.py`, `*_test.py`, `conftest.py`,
`*Test.java`, `*Tests.java`, `*IT.java`, and anything under `tests/` or
`test/`. Changed lines in test files are listed as "test changes", outside the
coverage number.

**Follow calls.** A flowchart node that calls project code has a chip per
function it calls (`↘ quote`, `×3` if it called it several times): tap it to
jump into that call, the one made from the frame on screen. Tapping a call
arrow in the sequence diagram or a frame in the call stack does the same. The
bar above the flowchart is the call path: tap a caller to go back to the line
that made the call. **↘ into** steps to the next step (into a call if the line
makes one), **↷ over** to the next line of the same function, **↗ out** back
to the caller.

A call that ran no project code has a chip too: dashed for a mock or stub
(tap it to open the real code it stood for, marked "not run"), grey for the
JDK or a library (tap it for its arguments and result). See
[mocked-calls.md](mocked-calls.md).

Keys: space play/pause, ← → step, `i`/`o`/`u` (or F11, F10, Shift+F11) into,
over and out, Home/End, `n` next changed line. The ☾/☀
button switches dark and light (remembered; follows the system until you pick).
On a phone the views become tabs and the controls stick to the bottom.

## Updating

```sh
uv tool upgrade wizualizer     # or: pipx upgrade wizualizer
```

For a git install, both fetch the repository again and reinstall from the
newest commit, even when the version number is the same (checked with uv 0.13
and pipx 1.18; pipx can still print "unchanged at version …"). `wiz
--version` shows the version. To start clean, reinstall over the old one:

```sh
uv tool install --force git+https://github.com/AlienClubrider/wizualizer
pipx install --force git+https://github.com/AlienClubrider/wizualizer
```

Recordings in `.wiz/` from an older wiz are kept and still open; delete the
folder to re-record everything.
