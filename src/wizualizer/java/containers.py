"""Does a test need Docker (Testcontainers), and is Docker there? Checked before a recording starts,
so a test that cannot run says so at once instead of waiting on a container."""

from __future__ import annotations

import os
import re
from pathlib import Path

_MARKERS = re.compile(r"\borg\.testcontainers\.|@Testcontainers\b|@Container\b|@ServiceConnection\b")
_EXTENDS = re.compile(r"\bclass\s+\w+(?:<[^{]*?>)?\s+extends\s+([\w.]+)")


def uses_testcontainers(test_file: Path, classes: dict[str, Path], depth: int = 3) -> bool:
    """True if the test class, or a project superclass of it, uses Testcontainers."""
    seen: set[Path] = set()
    path: Path | None = test_file
    while path and path not in seen and len(seen) <= depth:
        seen.add(path)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        if _MARKERS.search(text):
            return True
        m = _EXTENDS.search(text)
        path = _find_class(m.group(1), classes) if m else None
    return False


def _find_class(name: str, classes: dict[str, Path]) -> Path | None:
    if name in classes:
        return classes[name]
    simple = name.rsplit(".", 1)[-1]
    found = [p for full, p in classes.items() if full.rsplit(".", 1)[-1] == simple]
    return found[0] if len(found) == 1 else None


def docker_reachable(env: dict[str, str] | None = None, home: Path | None = None) -> bool:
    """A Docker endpoint is configured or its socket exists (Docker Desktop, Colima, Podman, Rancher)."""
    env = os.environ if env is None else env
    if env.get("DOCKER_HOST") or env.get("TESTCONTAINERS_HOST_OVERRIDE"):
        return True
    home = home or Path.home()
    if (home / ".testcontainers.properties").exists():
        return True
    candidates = [Path("/var/run/docker.sock"), home / ".docker/run/docker.sock",
                  home / ".colima/default/docker.sock", home / ".rd/docker.sock",
                  home / ".local/share/containers/podman/machine/podman.sock"]
    runtime = env.get("XDG_RUNTIME_DIR")
    if runtime:
        candidates += [Path(runtime) / "docker.sock", Path(runtime) / "podman/podman.sock"]
    return any(p.exists() for p in candidates)
