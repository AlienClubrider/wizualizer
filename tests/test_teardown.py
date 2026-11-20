"""Stopping a recording: which processes count as its test JVM, and how long a test may run."""

import pytest

from wizualizer.cli import record_timeout
from wizualizer.java.teardown import parse_ps

PS = """\
  101 /jdk/bin/java -agentlib:jdwp=transport=dt_socket,server=n,suspend=y,address=127.0.0.1:4321 -jar surefire.jar
  102 /jdk/bin/java -Dmaven.surefire.debug=-agentlib:jdwp=transport=dt_socket,server=n,suspend=y,address=127.0.0.1:4321 org.codehaus.plexus.classworlds.launcher.Launcher test
  103 /jdk/bin/java -Dwiz.jdwp=-agentlib:jdwp=transport=dt_socket,server=n,suspend=y,address=127.0.0.1:4321 -jar gradle-wrapper.jar test
  104 /jdk/bin/java -agentlib:jdwp=transport=dt_socket,server=n,suspend=y,address=127.0.0.1:43210 -cp worker.jar Worker
  105 /jdk/bin/java -agentlib:jdwp=transport=dt_socket,server=n,suspend=y,address=127.0.0.1:1234 -cp worker.jar Worker
"""


def test_only_the_jvm_running_this_recordings_agent_is_its_test_jvm():
    # Not the Maven or Gradle client that carries the agent in a -D property, not another port.
    assert parse_ps(PS, "127.0.0.1:4321") == [101]
    assert parse_ps(PS, "127.0.0.1:9999") == []


def test_timeout_comes_from_the_flag_then_the_environment_then_the_default(monkeypatch):
    monkeypatch.delenv("WIZ_RECORD_TIMEOUT", raising=False)
    assert record_timeout(None) == 300
    assert record_timeout(42) == 42
    monkeypatch.setenv("WIZ_RECORD_TIMEOUT", "90")
    assert record_timeout(None) == 90
    assert record_timeout(30) == 30
    monkeypatch.setenv("WIZ_RECORD_TIMEOUT", "soon")
    assert record_timeout(None) == 300


@pytest.mark.parametrize("value", ["0", "-1"])
def test_a_timeout_of_zero_means_no_limit(monkeypatch, value):
    monkeypatch.setenv("WIZ_RECORD_TIMEOUT", value)
    assert record_timeout(None) is None
    assert record_timeout(0) is None
