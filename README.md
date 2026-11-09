# wizualizer

See what your tests actually run. `wiz` lists every test in your project as a
**flow**; open one to step through it as a flowchart, with variables, a call
sequence and the source. On a branch with changes, show only the flows that
run changed lines, and which changed lines no test reaches.

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

## Quick start

```sh
cd my-project
wiz
```

`wiz` detects pytest, Maven or Gradle (and says what is missing if it finds
neither), finds the tests and serves a page on `127.0.0.1` (it prints the URL).
Click a flow: it is recorded once, cached in `.wiz/`, and opens in the viewer.
Pre-record everything with `wiz record --all`.

For one test without the web app (CI, sharing a file):

```sh
wiz run tests/test_billing.py::test_refund --base main   # or com.acme.BankTest#movesMoney
```

It writes one self-contained `.wiz/<test>.html`.

## Only changed flows

With a git diff (against the merge-base with `main`/`master` by default, or
`--base REF`, plus uncommitted work), switch on **Only changed flows**. wiz runs
the tests once, watching only the changed lines, and lists the tests that run
them with a count per flow. Inside a flow, **PR lines** shows which changed
lines ran and which never did. With no changes the switch is off and says why.
`wiz list --changed` does the same in a terminal.

## Privacy

Fully local. No network calls, accounts or telemetry. The server binds
127.0.0.1 only, and the viewer's Content-Security-Policy blocks all network
access.

## Docs

- [CLI reference](docs/cli.md): commands, options, the viewer
- [Only changed flows](docs/only-changes.md): how it works, limits
- [Java](docs/java.md): Maven/Gradle details, what is recorded
- [Limits](docs/limits.md)
- [Architecture](docs/architecture.md) and [trace format](docs/trace-format.md)
- [Contributing](docs/contributing.md)

Light theme by default, dark with the ☾ button. Works on a phone.

## License

[MIT](LICENSE) © Bryson Bailey
