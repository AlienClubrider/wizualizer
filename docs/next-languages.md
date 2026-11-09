# Adding Java and TypeScript

Everything after recording is language-neutral: the diff, change coverage, and
the viewer all work on [trace.json](trace-format.md). A language plugs in with
two pieces, defined in `src/wizualizer/ports.py`:

- a **Recorder**: runs one test and returns `steps`, `frames`, `functions`
  (without flowcharts), `truncated` and `test` (outcome);
- a **SourceModel**: for a source file, lists its statements (first line, line
  span, whether it runs inside a function) and builds the flowchart of a
  function starting at a given line.

`trace.make_trace()` combines them with the diff. The CLI picks the pair by
language (today only Python).

## Recorder over the Debug Adapter Protocol

For Java and TypeScript the plan is one generic `DapRecorder`, not one tracer
per language. DAP is what VS Code speaks to debuggers, so the same client works
with:

| language | adapter | how the test is started |
|---|---|---|
| Java | `java-debug` (Microsoft, runs inside jdt.ls) or plain JDWP via `jdb` | JUnit Platform Console Launcher with `--select-method`, launched with `-agentlib:jdwp=…` |
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

- **Java**: parse with a small vendored parser (or call `javaparser` through a
  helper JAR) to get statement spans and build the flowchart from `if`/`for`/
  `while`/`switch`/`try`, same node kinds as Python.
- **TypeScript**: the `typescript` compiler API (`ts.createSourceFile`) gives
  the AST; the flowchart builder mirrors `python/flowchart.py`.

Both must produce nodes with `lines` spans that match the line numbers the
debugger reports, and the "runs inside a function" flag for coverage.
