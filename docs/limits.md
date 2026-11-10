# Limits

- Languages: Python (pytest) and Java (JUnit via Maven or Gradle).
  TypeScript is planned: [next-languages.md](next-languages.md).
- Python 3.12+ in the project (the recorder uses `sys.monitoring`). Only the
  test call phase is recorded, not fixtures. Code under the project root is
  recorded, minus virtualenvs and site-packages.
- Main thread only. Threads, subprocesses and async tasks on other threads are
  not recorded.
- Values are rendered strings, bounded (depth 3, 8 items, 240 chars). Python
  objects with a custom `__repr__` are shown with it, so a slow or
  side-effecting `__repr__` runs during recording.
- Comprehensions, generator expressions and lambdas are folded into their
  enclosing function.
- Flowcharts are approximate around `try`/`finally` (a `return` inside `try`
  is drawn straight to the end) and treat `match` cases as a chain of checks.
- Coverage judges statements, not branches: `x = a if c else b` counts as run
  either way. Module- and class-level lines are "import-time" and not judged.
- One recording at a time in the web app (builds do not share well).
- Java specifics: [java.md](java.md#limits). Only-changes specifics:
  [only-changes.md](only-changes.md#limits).
- Mocked and library calls: [mocked-calls.md](mocked-calls.md#limits).
- Memory (heap) view is not built yet: [memory-view.md](memory-view.md).
