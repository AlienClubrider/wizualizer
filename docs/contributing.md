# Contributing

```sh
git clone https://github.com/AlienClubrider/wizualizer && cd wizualizer
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
```

The Java integration tests skip without a JDK and Maven; the Gradle one also
needs `gradle` on PATH (or a wrapper download).

Demos: `./examples/demo.sh` (Python) and `./examples/java-demo.sh` (Maven)
build a throwaway git repo under `.wiz/` with a "PR" branch and record one
test. Then `cd .wiz/demo-shop && ../../.venv/bin/wiz` for the web app.

Screenshots (needs Playwright):

```sh
node scripts/screenshots.js                       # viewer, after ./examples/demo.sh
node scripts/screenshots.js java <viewer.html>    # java-*.png
node scripts/screenshots.js flows http://127.0.0.1:7357/   # flows page of the demo shop
```

Keep it local: no network calls, no telemetry, no runtime dependencies. Pure
rules get unit tests; adapters get thin integration tests.
