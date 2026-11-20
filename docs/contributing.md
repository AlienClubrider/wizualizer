# Contributing

```sh
git clone https://github.com/AlienClubrider/wizualizer && cd wizualizer
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
```

The Java integration tests skip without a JDK and Maven; the Gradle one also
needs `gradle` on PATH (or a wrapper download). `tests/test_java_fixtures.py`
records Spring Boot tests in the realistic builds under `tests/fixtures/` (the
first run downloads Gradle and Spring; `WIZ_SKIP_SLOW=1` skips it).
`tests/test_browser.py` drives the viewer and the functions page in headless
Chromium and skips without Node and Playwright.

Demos: `./examples/demo.sh` (Python) and `./examples/java-demo.sh` (Maven)
build a throwaway git repo under `.wiz/` with a "PR" branch and record one
test. Then `cd .wiz/demo-shop && ../../.venv/bin/wiz` for the web app.

Screenshots (needs Playwright):

```sh
node scripts/screenshots.js                       # viewer, after ./examples/demo.sh
node scripts/screenshots.js java <viewer.html>    # java-*.png
node scripts/screenshots.js functions http://127.0.0.1:7357/   # functions page of a running wiz
```

Recorder internals (Java): `WIZ_RECORDER_STATS=<file>` appends one line per
recording with where the time went (events before and during the test,
breakpoints, unwatched framework calls); `WIZ_RECORDER_DEBUG=<file>` logs
every JDI event.

Keep it local: no network calls, no telemetry, no runtime dependencies. Pure
rules get unit tests; adapters get thin integration tests.
