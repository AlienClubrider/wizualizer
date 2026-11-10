"""Java project layout: source roots, packages, the test to run, and how to build it.

Pure file-system reads, no JVM. Build tool detection prefers wrappers (mvnw,
gradlew) and finds the module that holds the test, so multi-module builds run
only that module's tests.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

SKIP_DIRS = {".git", "build", "target", "out", "bin", ".gradle", ".idea", ".wiz", ".venv", "venv", "node_modules", ".mvn"}
_PACKAGE = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.MULTILINE)
GRADLE_FILES = ("build.gradle", "build.gradle.kts")
GRADLE_SETTINGS = ("settings.gradle", "settings.gradle.kts")


class ProjectError(ValueError):
    pass


@dataclass
class SourceIndex:
    """Every .java file under the root that sits in a proper package directory."""

    roots: list[Path] = field(default_factory=list)  # package roots, e.g. .../src/main/java
    packages: list[str] = field(default_factory=list)
    classes: dict[str, Path] = field(default_factory=dict)  # top-level class name -> file

    @classmethod
    def scan(cls, root: Path) -> "SourceIndex":
        index = cls()
        roots: set[Path] = set()
        packages: set[str] = set()
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
            for name in filenames:
                if not name.endswith(".java") or name in ("module-info.java", "package-info.java"):
                    continue
                path = Path(dirpath) / name
                pkg = _package_of(path)
                parts = pkg.split(".") if pkg else []
                parent = path.parent
                if parts and list(parent.parts[-len(parts):]) != parts:
                    continue  # file not in its package directory: javac would not find it either
                src_root = parent.parents[len(parts) - 1] if parts else parent
                roots.add(src_root)
                if pkg:
                    packages.add(pkg)
                index.classes[f"{pkg}.{path.stem}" if pkg else path.stem] = path
        index.roots = sorted(roots)
        index.packages = sorted(packages)
        return index


def _package_of(path: Path) -> str:
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:20_000]
    except OSError:
        return ""
    m = _PACKAGE.search(head)
    return m.group(1) if m else ""


@dataclass(frozen=True)
class JavaTest:
    cls: str  # binary name, e.g. com.example.FooTest or com.example.FooTest$Nested
    method: str
    file: Path

    @property
    def id(self) -> str:
        return f"{self.cls}#{self.method}"


def resolve_test(test_id: str, index: SourceIndex) -> JavaTest:
    """`com.example.FooTest#bar`, `FooTest#bar` or `path/to/FooTest.java#bar`."""
    if "#" not in test_id:
        raise ProjectError(f"Java tests are given as Class#method, e.g. com.example.FooTest#bar (got {test_id!r})")
    cls, method = test_id.rsplit("#", 1)
    method = method.removesuffix("()")
    if cls.endswith(".java"):
        path = Path(cls).resolve()
        for name, file in index.classes.items():
            if file.resolve() == path:
                return JavaTest(name, method, file)
        raise ProjectError(f"{cls} is not a Java source file in this project")
    outer = cls.split("$", 1)[0]
    if outer in index.classes:
        return JavaTest(cls, method, index.classes[outer])
    matches = [name for name in index.classes if name.rsplit(".", 1)[-1] == outer]
    if len(matches) == 1:
        nested = cls[len(outer):]
        return JavaTest(matches[0] + nested, method, index.classes[matches[0]])
    if matches:
        raise ProjectError(f"{outer} is ambiguous, use the full name: {', '.join(sorted(matches))}")
    raise ProjectError(f"no Java class {outer} found under the project root")


@dataclass(frozen=True)
class Build:
    tool: str  # "maven" | "gradle"
    root: Path  # where the build runs
    module: Path  # the module that holds the test (== root for single-module builds)
    executable: str

    @property
    def module_path(self) -> str:
        return self.module.relative_to(self.root).as_posix() if self.module != self.root else ""

    def command(self, test: JavaTest, agent: str, init_script: Path | None = None,
                task: str | None = None) -> list[str]:
        """The build command that runs only `test`, with `agent` added to the test JVM."""
        return self.command_for(
            [f"{test.cls.replace('$', '*')}#{test.method}"], [f"{test.cls}.{test.method}"], agent, init_script,
            task or gradle_task(test.file))

    def command_for_classes(self, classes: list[str], agent: str, init_script: Path | None = None,
                            task: str = "test") -> list[str]:
        """The build command that runs every test in these (top-level) classes, in one test JVM."""
        return self.command_for([",".join(classes)], classes, agent, init_script, task)

    def command_for(self, maven_filters: list[str], gradle_filters: list[str], agent: str,
                    init_script: Path | None, task: str = "test") -> list[str]:
        if self.tool == "maven":
            cmd = [self.executable, "-B", "--no-transfer-progress", "test",
                   *(f"-Dtest={f}" for f in maven_filters),
                   "-Dsurefire.failIfNoSpecifiedTests=false", "-DfailIfNoTests=false",
                   f"-Dmaven.surefire.debug={agent}", "-DforkCount=1", "-DtrimStackTrace=false"]
            if self.module_path:
                cmd += ["-pl", self.module_path, "-am"]
            return cmd
        path = f":{self.module_path.replace('/', ':')}:{task}" if self.module_path else task
        cmd = [self.executable, "--console=plain", "--no-scan"]
        if init_script:
            # The init script reads the agent from this property when the test JVM starts, so the
            # script never changes and Gradle's configuration cache stays valid between recordings.
            cmd += ["--init-script", str(init_script), f"-D{AGENT_PROPERTY}={agent}"]
        cmd.append(path)
        for f in gradle_filters:
            cmd += ["--tests", f]
        return cmd

    def report(self, test: JavaTest, task: str | None = None) -> Path:
        """Where the JUnit XML report for the test class ends up."""
        outer = test.cls.split("$", 1)[0]
        if self.tool == "maven":
            return self.module / "target" / "surefire-reports" / f"TEST-{outer}.xml"
        return self.module / "build" / "test-results" / (task or gradle_task(test.file)) / f"TEST-{test.cls}.xml"


def gradle_task(test_file: Path) -> str:
    """The Test task for a test file's source set: src/test/java -> test,
    src/integrationTest/java -> integrationTest (Gradle's JVM test suite convention)."""
    parts = test_file.parts
    for i in range(len(parts) - 2, 0, -1):
        if parts[i - 1] == "src" and parts[i + 1] in ("java", "kotlin", "groovy"):
            return "test" if parts[i] == "main" else parts[i]
    return "test"


AGENT_PROPERTY = "wiz.jdwp"

# Adds the debug agent to the test JVM of every Test task in the build wiz runs (not buildSrc or
# included builds). It is a jvmArgumentProvider, so a build that sets `jvmArgs = [...]` cannot drop
# it, and it is configured after the build's own scripts, so their maxParallelForks, forkEvery and
# caching settings cannot undo it. The test task always runs: never up to date, never from the cache.
GRADLE_INIT_SCRIPT = """\
// wizualizer: lets wiz record one test. Safe to delete; wiz writes it again when needed.
class WizJdwpAgent implements CommandLineArgumentProvider {
  Iterable<String> asArguments() {
    String agent = System.getProperty('wiz.jdwp')
    return agent ? [agent] : []
  }
}

if (gradle.parent == null) {
  allprojects {
    afterEvaluate {
      tasks.withType(Test).configureEach {
        jvmArgumentProviders.add(new WizJdwpAgent())
        outputs.upToDateWhen { false }
        outputs.cacheIf { false }
        maxParallelForks = 1
        forkEvery = 0
      }
    }
  }
}
"""


def gradle_init_script() -> Path:
    """The init script (GRADLE_INIT_SCRIPT), written once to a stable path in the user's cache."""
    digest = hashlib.sha256(GRADLE_INIT_SCRIPT.encode()).hexdigest()[:12]
    path = cache_dir() / "gradle" / f"wiz-init-{digest}.gradle"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".tmp{os.getpid()}")
        tmp.write_text(GRADLE_INIT_SCRIPT, encoding="utf-8")
        tmp.replace(path)
    return path


def cache_dir() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "wizualizer"


def detect_build(test_file: Path, stop: Path) -> Build:
    """Find the module holding test_file and the build root above it (no higher than stop)."""
    stop = stop.resolve()
    module = None
    for d in [test_file.resolve().parent, *test_file.resolve().parents]:
        if _has(d, ("pom.xml", *GRADLE_FILES, *GRADLE_SETTINGS)):
            module = d
            break
        if d == stop:
            break
    if module is None:
        raise ProjectError(f"no pom.xml or build.gradle(.kts) found above {test_file}")

    is_gradle = _has(module, GRADLE_FILES + GRADLE_SETTINGS)
    if is_gradle and (module / "pom.xml").exists():
        # Both builds present: follow the wrapper; Gradle if both or neither have one.
        is_gradle = (module / "gradlew").exists() or not (module / "mvnw").exists()

    root = module
    for d in module.parents:
        if not d.is_relative_to(stop):
            break
        if is_gradle and _has(d, GRADLE_SETTINGS):
            root = d
        if not is_gradle and (d / "pom.xml").exists() and module in _maven_modules(d):
            root = d  # an aggregator that builds the module, even with pom-less folders between
        if d == stop:
            break
    if is_gradle and not _has(root, GRADLE_SETTINGS) and _has(module, GRADLE_SETTINGS):
        root = module

    tool = "gradle" if is_gradle else "maven"
    wrapper = root / ("gradlew" if is_gradle else "mvnw")
    if wrapper.exists() and os.access(wrapper, os.X_OK):
        exe = str(wrapper)
    else:
        exe = shutil.which("gradle" if is_gradle else "mvn") or ""
        if not exe:
            raise ProjectError(f"{tool} project without a wrapper, and `{'gradle' if is_gradle else 'mvn'}` is not on PATH")
    return Build(tool, root, module, exe)


_MODULE = re.compile(r"<module>\s*([^<]+?)\s*</module>")


def _maven_modules(pom_dir: Path, depth: int = 0) -> set[Path]:
    """Every module directory a pom.xml aggregates, transitively."""
    try:
        text = (pom_dir / "pom.xml").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    found: set[Path] = set()
    for name in _MODULE.findall(text):
        sub = (pom_dir / name).resolve()
        if sub.name == "pom.xml" or sub.suffix == ".xml":
            sub = sub.parent
        if sub not in found and depth < 8:
            found.add(sub)
            found |= _maven_modules(sub, depth + 1)
    return found


def _has(d: Path, names: tuple[str, ...]) -> bool:
    return any((d / n).exists() for n in names)
