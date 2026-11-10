# wizualizer

See what your code does when its tests run. `wiz` lists the **functions under
test** in your project, each with its runs (one per test, labelled by its
inputs). Open a run to step through the function as a flowchart, with
variables, a call sequence and the source, and step into the functions it
calls. On a branch, show only the functions whose runs reach changed lines.

![A flow mid-play: flowchart, variables, calls, source](docs/screenshots/flowchart.png)

## Prerequisites

- **Python 3.12+** and **git**.
- Python projects: pytest in the project's environment (its `.venv` is found
  automatically).
- Java projects: a **JDK 17+** and the project's **Maven or Gradle** (wrappers
  are used when present). JUnit 4 or 5.

## Install

```sh
pipx install git+https://github.com/AlienClubrider/wizualizer
# or
uv tool install git+https://github.com/AlienClubrider/wizualizer
```

No runtime dependencies.

## Update

```sh
uv tool upgrade wizualizer   # or: pipx upgrade wizualizer
```

Both reinstall from the latest commit, even when the version number did not
change (pipx may still say "unchanged").

## Quick start

```sh
cd my-project
wiz
```

`wiz` detects pytest, Maven or Gradle (and says what is missing if it finds
neither), finds the tests and serves a page on `127.0.0.1` (it prints the URL).
Pick a run: its test is recorded once, cached in `.wiz/`, and the viewer opens
on that function. If a recording fails, wiz says why in one line, with a fix
and the full build output. Pre-record everything with `wiz record --all`.

For one test without the web app (CI, sharing a file):

```sh
wiz run tests/test_billing.py::test_refund --base main   # or com.acme.BankTest#movesMoney
```

It writes one self-contained `.wiz/<test>.html`.

## Only changed functions

With a git diff (against the merge-base with `main`/`master`, or `--base REF`,
plus uncommitted work), switch on **Only changed functions**. wiz runs the
tests once, watching only the changed lines. In the viewer, **PR lines** shows
which changed lines ran and which never did. `wiz list --changed` does the same
in a terminal.

## Privacy

Fully local. No network calls, accounts or telemetry. The server binds
127.0.0.1 only, and the viewer's Content-Security-Policy blocks all network
access.

## Docs

- [CLI reference](docs/cli.md): commands, options, the viewer
- [Only changed functions](docs/only-changes.md): how it works, limits
- [Java](docs/java.md): Maven/Gradle, big builds, why a recording fails
- [Limits](docs/limits.md)
- [Architecture](docs/architecture.md) and [trace format](docs/trace-format.md)
- [Contributing](docs/contributing.md)

Light theme by default, dark with the ☾ button. Works on a phone.

## License

[MIT](LICENSE) © Bryson Bailey
