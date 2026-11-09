# Adding languages (Java done, TypeScript next)

Everything after recording is language-neutral: the diff, change coverage, and
the viewer all work on [trace.json](trace-format.md). A language plugs in with
two pieces, defined in `src/wizualizer/ports.py`:

- a **Recorder**: runs one test and returns `steps`, `frames`, `functions`
  (without flowcharts), `truncated` and `test` (outcome);
- a **SourceModel**: for a source file, lists its statements (first line, line
  span, whether it runs inside a function) and builds the flowchart of a
  function starting at a given line.

`trace.make_trace()` combines them with the diff. The CLI picks the pair by
language: `JavaRecorder` + `JavaSource` for `Class#method` ids, else Python.

## Java: JDI, not DAP (done)

The Java recorder is a small Java program (`src/wizualizer/java/helper/`)
using JDI (`com.sun.jdi`), the debugger API every JDK ships. It listens on a
local port; Maven or Gradle starts the one test with
`-agentlib:jdwp=…,server=n,suspend=y` and the test JVM connects to it.

Why JDI over the Debug Adapter Protocol with Microsoft's java-debug:

- **Nothing to install.** java-debug runs as a plug-in inside jdt.ls (the
  Eclipse JDT language server, a large OSGi app that has to import and
  build the project itself). JDI is in every JDK, as is javac's parser, which
  the helper also uses for statements and flowcharts. No new dependency.
- **Return values.** JDI's `MethodExitEvent.returnValue()` gives every return
  value. DAP has no portable way to get them (see below).
- **Speed and precision.** DAP would mean a `stepIn` round trip per line and
  stepping out of library code. JDI sets a breakpoint on each line of
  project methods only, and method entry/exit and exception requests limited
  to project packages and the test's thread, switched on only while the test
  method runs. The RuneStone-Bank transfer test records in about 7 s,
  most of it Gradle.
- **Control.** Mockito inline mocks, Lombok and other generated code are
  recognised and skipped (no source method matches them), lambdas folded
  into their enclosing method, values rendered without running project code.

It stays behind the same `Recorder` port: `JavaRecorder.record()` returns the
same `steps / frames / functions / truncated / test` as the Python recorder.
TypeScript can still use the DAP plan below, or the Chrome DevTools Protocol
directly, for the same reasons Java went to its native debugger API.

## TypeScript: recorder over the Debug Adapter Protocol (planned)

For TypeScript the plan is a generic `DapRecorder`. DAP is what VS Code speaks to debuggers, so the same client works
with:

| language | adapter | how the test is started |
|---|---|---|
| TypeScript / JS | `vscode-js-debug` (`dapDebugServer.js`) | `node --test` / Jest / Vitest with one test name filter, `--inspect-brk` |
| Python (alternative) | `debugpy` | `python -m pytest <node id>` |

The loop:

1. Launch the test under the adapter, paused on entry.
2. `setFunctionBreakpoints` or a breakpoint on the test's first line; `continue`.
3. Repeat `stepIn` until the test frame returns. After each `stopped` event:
   `stackTrace` (frames → `frames`, call/return by comparing stack depth with
   the previous stop), `scopes` + `variables` for the top frame (→ `locals`,
   rendered with the same depth/length limits), and the stop line (→ `line`).
4. Use `stepOut` whenever the top frame's source is outside the project root
   (JDK, `node_modules`) so library code is skipped instead of stepped.
5. `exceptionBreakpointFilters` (caught + uncaught) give `exception` steps.

Return values: DAP has no portable "return value" event. Some adapters show
the last return value as a synthetic variable after a step out; check each
adapter before relying on it (not verified yet). Fallback: show the caller's
assignment on the next line.

Cost (estimate, not measured): a DAP round-trip per step is milliseconds, not
microseconds, so a 5,000-step test records in seconds. Keep `--max-steps`.

## SourceModels

- **Java** (done): the helper parses with javac's own Tree API and returns
  each method's statement tree and the file's statement spans;
  `java/flowchart.py` builds the flowchart from `if`/`for`/`while`/`do`/
  `switch`/`try`/labeled `break`/`continue`, same node kinds as Python.
- **TypeScript**: the `typescript` compiler API (`ts.createSourceFile`) gives
  the AST; the flowchart builder mirrors `python/flowchart.py`.

Both must produce nodes with `lines` spans that match the line numbers the
debugger reports, and the "runs inside a function" flag for coverage.
