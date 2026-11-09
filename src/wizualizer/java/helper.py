"""The Java helper (helper/wiz/*.java): compiled once per JDK into a cache directory.

It needs a JDK (javac, jdk.jdi, jdk.compiler), nothing else.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import threading
from pathlib import Path

HELPER_SRC = Path(__file__).resolve().parent / "helper"


class JdkMissing(RuntimeError):
    pass


def find_jdk(java_home: str | None = None) -> Path:
    """The JDK home: --java-home, else $JAVA_HOME, else the one `javac` on PATH belongs to."""
    for candidate in (java_home, os.environ.get("JAVA_HOME")):
        if candidate and (Path(candidate) / "bin" / "javac").exists():
            return Path(candidate)
    javac = shutil.which("javac")
    if javac:
        return Path(javac).resolve().parent.parent
    raise JdkMissing("no JDK found: install one (javac and java) or pass --java-home")


def helper_classpath(jdk: Path) -> Path:
    """Compile the helper for this JDK if needed and return its class directory."""
    sources = sorted((HELPER_SRC / "wiz").glob("*.java"))
    h = hashlib.sha256(str(jdk.resolve()).encode())
    for src in sources:
        h.update(src.name.encode())
        h.update(src.read_bytes())
    cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "wizualizer" / "java-helper"
    out = cache / h.hexdigest()[:16]
    if (out / "wiz" / "Main.class").exists():
        return out
    tmp = out.with_name(out.name + f".tmp{os.getpid()}")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    proc = subprocess.run(
        [str(jdk / "bin" / "javac"), "-nowarn", "-d", str(tmp), *map(str, sources)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        shutil.rmtree(tmp, ignore_errors=True)
        raise JdkMissing(f"could not compile the Java helper with {jdk}:\n{proc.stderr[-2000:]}")
    shutil.rmtree(out, ignore_errors=True)
    tmp.rename(out)
    return out


def helper_command(jdk: Path, *args: str) -> list[str]:
    return [str(jdk / "bin" / "java"), "-Xshare:auto", "-XX:TieredStopAtLevel=1",
            "-cp", str(helper_classpath(jdk)), "wiz.Main", *args]


class OutlineServer:
    """A long-lived `wiz.Main serve` process: one JVM start for all files of a run."""

    def __init__(self, jdk: Path):
        self._jdk = jdk
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()

    def outline(self, source: str) -> str:
        data = source.encode("utf-8")
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                self._proc = subprocess.Popen(
                    helper_command(self._jdk, "serve"),
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                )
            assert self._proc.stdin and self._proc.stdout
            self._proc.stdin.write(f"{len(data)}\n".encode() + data)
            self._proc.stdin.flush()
            line = self._proc.stdout.readline()
        if not line:
            raise RuntimeError("the Java helper stopped")
        return line.decode("utf-8")

    def close(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.stdin.close()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None
