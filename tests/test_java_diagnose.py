from wizualizer.java import containers
from wizualizer.java.diagnose import explain, milestone

T = "com.harbor.QuoteTest#quotes"

GRADLE_HEAD = """$ ./gradlew --console=plain :services:payments:test --tests com.harbor.QuoteTest.quotes
> Task :buildSrc:compileKotlin UP-TO-DATE
> Task :buildSrc:jar UP-TO-DATE
"""


def test_gradle_test_ran_without_the_agent():
    out = GRADLE_HEAD + "> Task :services:payments:compileJava\n> Task :services:payments:test\n\nBUILD SUCCESSFUL in 9s\n"
    d = explain("gradle", out, 0, T)
    assert "started without wiz's debug agent" in d.reason


def test_gradle_test_task_skipped_or_from_cache():
    for state in ("UP-TO-DATE", "FROM-CACHE", "SKIPPED"):
        d = explain("gradle", GRADLE_HEAD + f"> Task :services:payments:test {state}\nBUILD SUCCESSFUL\n", 0, T)
        assert d.reason == f"Gradle did not run the test task (it was {state})"


def test_gradle_compile_error():
    out = GRADLE_HEAD + ("> Task :services:payments:compileJava FAILED\nQuote.java:3: error: ';' expected\n"
                         "* What went wrong:\nExecution failed for task ':services:payments:compileJava'.\n")
    d = explain("gradle", out, 1, T)
    assert d.reason == "the build failed to compile before the test could run"


def test_gradle_no_matching_test_names_the_task():
    out = GRADLE_HEAD + "> Task :app:integrationTest FAILED\n* What went wrong:\nExecution failed for task " \
                        "':app:integrationTest'.\n> No tests found for given includes: [x](--tests filter)\n"
    d = explain("gradle", out, 1, T, "integrationTest")
    assert d.reason == f"Gradle found no test {T} in the integrationTest task"
    assert "WIZ_GRADLE_TEST_TASK" in d.hint


def test_gradle_stops_after_build_src():
    # What the report from a big multi-project build looked like: nothing after buildSrc.
    d = explain("gradle", GRADLE_HEAD + "\n* What went wrong:\nA problem occurred configuring project ':common:auth'.\n", 1, T)
    assert d.reason == "the build failed before the test ran: A problem occurred configuring project ':common:auth'"
    d = explain("gradle", GRADLE_HEAD, 143, T)
    assert d.reason == "Gradle stopped (exit code 143) before running the test task"


def test_docker_is_named_whatever_the_build():
    out = "> Task :app:test\nIllegalStateException: Could not find a valid Docker environment\n"
    for tool in ("gradle", "maven"):
        assert "Docker" in explain(tool, out, 1, T).reason


def test_maven_reasons():
    assert "compile" in explain("maven", "[ERROR] COMPILATION ERROR :\n", 1, T).reason
    assert explain("maven", "[ERROR] No tests matching pattern \"x\" were executed!\n", 1, T).reason == \
        f"Maven found no test {T}"
    assert "forkCount=0" in explain("maven", "[INFO] Tests run: 1, Failures: 0\n[INFO] BUILD SUCCESS\n", 0, T).hint
    d = explain("maven", "[ERROR] Failed to execute goal on project app: Could not resolve dependencies\n[ERROR] \n", 1, T)
    assert d.reason.startswith("the build failed before the test ran: Failed to execute goal")


def test_milestones_for_progress():
    assert milestone("gradle", "> Task :services:payments:compileJava\n") == "Gradle: :services:payments:compileJava"
    assert milestone("gradle", "Calculating task graph as no cached configuration is available for tasks: x") == \
        "Gradle: Calculating task graph"
    assert milestone("gradle", "BUILD SUCCESSFUL") is None
    assert milestone("maven", "[INFO] Building payments-service 1.0 [2/3]") == "Maven: Building payments-service 1.0"
    assert milestone("maven", "[INFO] Running com.harbor.QuoteTest") == "Maven: Running com.harbor.QuoteTest"


def test_testcontainers_found_in_the_test_or_its_superclass(tmp_path):
    base = tmp_path / "AbstractIT.java"
    base.write_text("package a;\nimport org.testcontainers.containers.PostgreSQLContainer;\nabstract class AbstractIT {}\n")
    test = tmp_path / "OrderIT.java"
    test.write_text("package a;\nclass OrderIT extends AbstractIT {}\n")
    plain = tmp_path / "QuoteTest.java"
    plain.write_text("package a;\n@SpringBootTest class QuoteTest extends Base<String> {}\n")
    classes = {"a.AbstractIT": base, "a.OrderIT": test, "a.QuoteTest": plain}
    assert containers.uses_testcontainers(test, classes)
    assert not containers.uses_testcontainers(plain, classes)


def test_docker_reachable(tmp_path):
    assert containers.docker_reachable({"DOCKER_HOST": "tcp://x:2375"}, tmp_path)
    (tmp_path / ".docker/run").mkdir(parents=True)
    assert not containers.docker_reachable({}, tmp_path) or __import__("os").path.exists("/var/run/docker.sock")
    (tmp_path / ".docker/run/docker.sock").write_text("")
    assert containers.docker_reachable({}, tmp_path)
