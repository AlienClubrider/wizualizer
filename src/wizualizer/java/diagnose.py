"""Why a Java recording failed, in one line, with a hint: read from the build's own output.

Pure rules over text, no I/O. The recorder calls `explain` when the build ended
without the test JVM ever connecting to the recorder.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

DOCKER_MARKERS = (
    "Could not find a valid Docker environment",
    "DockerClientProviderStrategy",
    "Cannot connect to the Docker daemon",
    "docker.sock",
)
GRADLE_SKIPPED = ("UP-TO-DATE", "FROM-CACHE", "SKIPPED", "NO-SOURCE")


@dataclass(frozen=True)
class Diagnosis:
    reason: str  # one line, plain words
    hint: str  # what to do about it


def explain(tool: str, output: str, exit_code: int | None, test: str, task: str = "test") -> Diagnosis:
    """The likeliest reason the test JVM never connected, from the build output."""
    if any(m in output for m in DOCKER_MARKERS):
        return docker_needed()
    if tool == "gradle":
        return _gradle(output, exit_code, test, task)
    return _maven(output, exit_code, test)


def docker_needed() -> Diagnosis:
    return Diagnosis(
        "this test starts Docker containers (Testcontainers) and Docker is not available",
        "wiz records tests that run inside the JVM with external services mocked. Start Docker, "
        "or pick a test that mocks its external services (MockMvc, @MockitoBean).")


def _gradle(output: str, exit_code: int | None, test: str, task: str) -> Diagnosis:
    rerun = "Run the build command shown below in a terminal: it fails the same way without wiz."
    if re.search(r"Configuration cache problems found|configuration cache state could not be cached", output):
        return Diagnosis("Gradle's configuration cache rejected the build",
                         "Record again with the configuration cache off for this run: "
                         "set WIZ_GRADLE_ARGS=--no-configuration-cache.")
    if "No tests found for given includes" in output:
        return Diagnosis(f"Gradle found no test {test} in the {task} task",
                         f"Check the class and method name. If the test belongs to another Test task "
                         f"than :{task}, tell wiz with WIZ_GRADLE_TEST_TASK=<task name>.")
    if re.search(r"Compilation failed|compile\w*Java FAILED|compile\w*Kotlin FAILED|^e: ", output, re.M):
        return Diagnosis("the build failed to compile before the test could run",
                         f"Fix the compile errors in the output. {rerun}")
    ran = _gradle_task_state(output, task)
    if ran in GRADLE_SKIPPED:
        return Diagnosis(f"Gradle did not run the {task} task (it was {ran})",
                         "Something in the build disables or skips the task (onlyIf, enabled = false, a "
                         "property). Check how the task is configured for local runs.")
    if ran == "FAILED" or ran == "ran":
        if re.search(r"transport error|JDWP|ERROR: Cannot load this JVM TI agent", output):
            return Diagnosis("the test JVM could not reach the recorder",
                             "A firewall or a JVM option blocks localhost debugging; see the JDWP error "
                             "in the output.")
        return Diagnosis(f"the {task} task ran, but its test JVM started without wiz's debug agent",
                         "The build launches tests in a way wiz cannot reach (a custom JavaExec task, "
                         "remote test distribution, or a plugin that rewrites the test JVM's arguments).")
    failure = _what_went_wrong(output)
    if failure:
        return Diagnosis(f"the build failed before the test ran: {failure}", rerun)
    if exit_code not in (0, None):
        return Diagnosis(f"Gradle stopped (exit code {exit_code}) before running the {task} task", rerun)
    return Diagnosis(f"Gradle finished without running the {task} task",
                     "Check that the module has a Test task by that name, then run the build command below in "
                     "a terminal.")


def _gradle_task_state(output: str, task: str) -> str | None:
    """'ran', 'FAILED', 'UP-TO-DATE', ... for the test task in Gradle's plain output, or None."""
    state = None
    for m in re.finditer(rf"^> Task (\S*:{re.escape(task)}|{re.escape(task)})(?: (\S+))?\s*$", output, re.M):
        state = m.group(2) or "ran"
    return state


def _what_went_wrong(output: str) -> str:
    m = re.search(r"\* What went wrong:\n(.+)", output)
    return m.group(1).strip().rstrip(".:")[:200] if m else ""


def _maven(output: str, exit_code: int | None, test: str) -> Diagnosis:
    rerun = "Run the build command shown below in a terminal: it fails the same way without wiz."
    if "COMPILATION ERROR" in output:
        return Diagnosis("the build failed to compile before the test could run",
                         f"Fix the compile errors in the output. {rerun}")
    if re.search(r"No tests (matching pattern|were executed)", output):
        return Diagnosis(f"Maven found no test {test}", "Check the class and method name.")
    if re.search(r"Tests run: \d+", output):
        return Diagnosis("the test ran, but its JVM started without wiz's debug agent",
                         "The pom likely runs tests inside Maven's own JVM (forkCount=0) or replaces "
                         "Surefire's debug option; wiz needs a forked test JVM.")
    m = re.search(r"^\[ERROR\] (?!\s*$)(.+)", output, re.M)
    if m:
        return Diagnosis(f"the build failed before the test ran: {m.group(1).strip()[:200]}", rerun)
    if exit_code not in (0, None):
        return Diagnosis(f"Maven stopped (exit code {exit_code}) before running the test", rerun)
    return Diagnosis("Maven finished without running the test",
                     "Check that the module's Surefire runs it, then run the build command below in a terminal.")


def milestone(tool: str, line: str) -> str | None:
    """A short progress note for a line of build output worth showing while the build runs."""
    line = line.rstrip()
    if tool == "gradle":
        if line.startswith("> Task "):
            return f"Gradle: {line[7:]}"
        if line.startswith(("Calculating task graph", "Reusing configuration cache", "Starting a Gradle Daemon")):
            return f"Gradle: {line.split(' as ', 1)[0].rstrip('.')}"
        return None
    m = re.match(r"\[INFO\] (Building .+?)(?:\s+\[\d+/\d+\])?$", line)
    if m:
        return f"Maven: {m.group(1)}"
    if line.startswith("[INFO] --- ") and ("surefire" in line or "compiler" in line):
        return f"Maven: {line[11:].split(' (', 1)[0].strip(' -')}"
    if line.startswith("[INFO] Running "):
        return f"Maven: {line[7:]}"
    return None
