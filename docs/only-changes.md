# Only changed flows

With a git diff (against the base, see `--base` in [cli.md](cli.md), plus
uncommitted and untracked files), the flows page offers **Only changed flows**.
It shows just the tests whose run reaches a changed line, with the count
(`Δ 3 lines`) and a `test changed` tag when the test's own file changed. Open
one and the **PR lines** view shows which changed lines that test ran and which
it never ran. Without changes the switch is disabled and says why.

## How it knows

The first time you turn it on, wiz runs the test suite **once** with a cheap
probe that watches only the changed statements:

- **Python**: a pytest plugin using `sys.monitoring`. Each watched location
  reports once per test and is then disabled, so the run is close to normal
  speed. Fixtures count (setup and teardown are watched too).
- **Java**: the build runs each module's test classes in one JVM with a JDI
  breakpoint on each changed statement only. On a hit, the stack says which
  test method is running.

The result is cached in `.wiz/` against the source hash and the diff base, so
it reruns only when code changes. A changed line counts as run when the
statement that owns it ran, the same rule as the coverage view.

Times: the demo shops take under 3 s; RuneStone-Bank's 37 Java tests (Spring
Boot, Gradle) about 50 s.

## Limits

- A test that fails before reaching changed code is not listed.
- Java: a change to a method signature alone is not detected (no statement
  runs on that line); test methods inherited from a base class are not
  attributed; a build that forks a new JVM per test class (`forkEvery`,
  `reuseForks=false`) is not supported.
