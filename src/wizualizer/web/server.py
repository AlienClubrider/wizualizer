"""`wiz` web app: a local page listing every flow; opening one records it (once) and plays it back.

Standard library only (http.server). Binds 127.0.0.1 only. Requests whose Host
is not this server are refused (DNS rebinding), and every POST needs an
`X-Wiz: 1` header, which a page from another origin cannot send without a CORS
preflight this server never answers.
"""

from __future__ import annotations

import json
import queue
import threading
import time
import traceback
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from urllib.parse import parse_qs, quote, urlsplit

from ..ports import RecordingFailed
from ..viewer.render import asset as viewer_asset
from ..viewer.render import escape, render_html
from ..workspace import Workspace

PREFERRED_PORT = 7357
CSP = ("default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
       "connect-src 'self'; img-src data:; base-uri 'none'; form-action 'none'")


class Jobs:
    """One background worker: recordings and change scans run one at a time (builds do not share well)."""

    def __init__(self, workspace: Workspace):
        self.ws = workspace
        self.state: dict[str, dict] = {}  # "record:<id>" | "scan" -> {status, error, started, finished}
        self._queue: queue.Queue[str] = queue.Queue()
        self._lock = threading.Lock()
        threading.Thread(target=self._work, daemon=True, name="wiz-jobs").start()

    def submit(self, key: str) -> dict:
        with self._lock:
            job = self.state.get(key)
            if job and job["status"] in ("queued", "running"):
                return job
            job = {"status": "queued", "error": "", "queued": time.time()}
            self.state[key] = job
        self._queue.put(key)
        return job

    def snapshot(self) -> dict[str, dict]:
        with self._lock:
            return {k: dict(v) for k, v in self.state.items()}

    def _work(self) -> None:
        while True:
            key = self._queue.get()
            job = self.state[key]
            job.update(status="running", started=time.time())
            try:
                if key == "scan":
                    self.ws.scan()
                else:
                    self.ws.trace(key.split(":", 1)[1])
                job.update(status="done", finished=time.time())
            except (RecordingFailed, KeyError) as exc:
                job.update(status="failed", error=str(exc)[-6000:], finished=time.time())
            except Exception:  # keep the worker alive; show what broke
                job.update(status="failed", error=traceback.format_exc()[-6000:], finished=time.time())


class App:
    def __init__(self, workspace: Workspace, project_info: dict):
        self.ws = workspace
        self.info = project_info
        self.jobs = Jobs(workspace)

    def state(self) -> dict:
        source_hash = self.ws.store.source_hash()
        diff = self.ws.diff()
        jobs = self.jobs.snapshot()
        error = ""
        try:
            flows = self.ws.flows(source_hash)
        except RecordingFailed as exc:
            flows, error = [], str(exc)
        scan = self.ws.cached_scan(source_hash, diff)
        out = []
        for f in flows:
            item = f.to_json()
            item["cached"] = self.ws.cached(f, source_hash, diff)
            job = jobs.get(f"record:{f.id}")
            if job:
                item["job"] = job
            if scan is not None:
                item["changed"] = scan["tests"].get(f.id)
            out.append(item)
        return {
            "project": {**self.info, "error": error},
            "flows": out,
            "diff": diff.to_json(),
            "scan": {"ready": scan is not None, "job": jobs.get("scan")},
        }


def flows_page() -> str:
    pkg = resources.files("wizualizer.web")
    page = (pkg / "flows.html").read_text(encoding="utf-8")
    return (page.replace("/*__THEME_CSS__*/", viewer_asset("theme.css"))
            .replace("/*__CSS__*/", (pkg / "flows.css").read_text(encoding="utf-8"))
            .replace("/*__THEME_JS__*/", viewer_asset("theme.js"))
            .replace("/*__JS__*/", (pkg / "flows.js").read_text(encoding="utf-8"))
            .replace("__CSP__", CSP))


def make_handler(app: App):
    page = flows_page()

    class Handler(BaseHTTPRequestHandler):
        server_version = "wiz"
        sys_version = ""

        def log_message(self, fmt, *args):  # quiet: the terminal shows only the URL
            pass

        def _host_ok(self) -> bool:
            port = self.server.server_address[1]
            return self.headers.get("Host", "") in (f"127.0.0.1:{port}", f"localhost:{port}")

        def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data, status: int = 200) -> None:
            self._send(status, json.dumps(data).encode(), "application/json")

        def _html(self, html: str, status: int = 200) -> None:
            self._send(status, html.encode("utf-8"), "text/html; charset=utf-8")

        def do_GET(self):
            if not self._host_ok():
                return self._send(HTTPStatus.MISDIRECTED_REQUEST, b"wrong host", "text/plain")
            url = urlsplit(self.path)
            if url.path == "/":
                return self._html(page, 200)
            if url.path == "/api/state":
                return self._json(app.state())
            if url.path == "/flow":
                flow_id = parse_qs(url.query).get("id", [""])[0]
                try:
                    trace = app.ws.trace(flow_id, record=False)
                except KeyError:
                    return self._html(f"<p>No test {escape(flow_id)} in this project. <a href='/'>All flows</a>",
                                      404)
                if trace is None:  # not recorded yet: the flows page records it and comes back
                    return self._send(HTTPStatus.SEE_OTHER, b"", "text/plain",
                                      {"Location": "/?open=" + quote(flow_id, safe="")})
                return self._html(render_html(trace, back_href="/"))
            self._send(404, b"not found", "text/plain")

        def do_POST(self):
            if not self._host_ok() or self.headers.get("X-Wiz") != "1":
                return self._send(HTTPStatus.FORBIDDEN, b"forbidden", "text/plain")
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}") if length <= 65536 else {}
            except ValueError:
                body = {}
            url = urlsplit(self.path)
            if url.path == "/api/record":
                flow_id = str(body.get("id", ""))
                try:
                    app.ws.flow(flow_id)
                except KeyError:
                    return self._json({"error": f"no test {flow_id}"}, 404)
                return self._json(app.jobs.submit(f"record:{flow_id}"))
            if url.path == "/api/scan":
                return self._json(app.jobs.submit("scan"))
            self._send(404, b"not found", "text/plain")

    return Handler


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        import sys

        if not isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError)):  # a closed tab is fine
            super().handle_error(request, client_address)


def serve(app: App, port: int | None = None) -> ThreadingHTTPServer:
    """A server on 127.0.0.1: the given port, else 7357 if free, else any free port."""
    handler = make_handler(app)
    for candidate in ([port] if port is not None else [PREFERRED_PORT, 0]):
        try:
            return Server(("127.0.0.1", candidate), handler)
        except OSError:
            if port is not None:
                raise
    raise OSError("no free port")
