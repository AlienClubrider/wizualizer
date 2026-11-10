"""Java change probe: run each module's tests once with breakpoints only on the watched lines."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from ..ports import RecordingFailed
from .helper import JdkMissing, find_jdk, helper_command
from .project import JavaTest, ProjectError, SourceIndex, detect_build
from .recorder import attach_and_build, failure, test_task


def probe(root: Path, tests: list[tuple[str, Path]], watch: dict[str, list[int]],
          java_home: str | None = None, progress=None) -> dict[str, dict]:
    """tests: (Class#method, test file). Returns test id -> {"lines": {file: [lines]}}."""
    if not watch or not tests:
        return {}
    try:
        jdk = find_jdk(java_home)
        helper = helper_command(jdk)
        index = SourceIndex.scan(root)
        builds: dict[tuple[Path, Path, str], tuple] = {}
        for test_id, file in tests:
            build = detect_build(file, root)
            task = test_task(JavaTest(test_id.split("#", 1)[0], "", file))
            entry = builds.setdefault((build.root, build.module, task), (build, task, set(), set()))
            entry[2].add(test_id.split("#", 1)[0].split("$", 1)[0])
            entry[3].add(test_id)
    except (ProjectError, JdkMissing) as exc:
        raise RecordingFailed(str(exc)) from exc

    hits: dict[str, dict] = {}
    for build, task, classes, ids in builds.values():
        with tempfile.TemporaryDirectory(prefix="wiz-") as tmp:
            tmp = Path(tmp)
            out, watch_file, tests_file = tmp / "hits.json", tmp / "watch.tsv", tmp / "tests.txt"
            watch_file.write_text("".join(f"{rel}\t{line}\n" for rel, lines in watch.items() for line in lines),
                                  encoding="utf-8")
            tests_file.write_text("\n".join(sorted(ids)) + "\n", encoding="utf-8")
            args = ["probe", "--root", str(root), "--out", str(out), "--watch", str(watch_file),
                    "--tests", str(tests_file), "--timeout", "0"]
            for r in index.roots:
                args += ["--source-root", str(r)]
            for p in index.packages:
                args += ["--package", p]
            run = attach_and_build(
                helper + args, build,
                lambda agent, init: build.command_for_classes(sorted(classes), agent, init, task), jdk, progress)
            if not out.exists():
                raise failure(build, run, ", ".join(sorted(classes)), task)
            for test, files in json.loads(out.read_text(encoding="utf-8"))["hits"].items():
                hits[test] = {"lines": files}
    return hits
