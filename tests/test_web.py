"""The web app's HTTP API, on a real local server with a fake language."""

import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from test_workspace import FakeLanguage, repo  # noqa: F401  (fixture)
from wizualizer.web.server import App, serve
from wizualizer.workspace import Workspace

FLOW = "tests/test_pricing.py::test_free_shipping"


@pytest.fixture
def server(repo):  # noqa: F811
    lang = FakeLanguage(repo)
    app = App(Workspace(repo, {"python": lang}), {"name": "shop", "kind": "pytest", "problems": []})
    srv = serve(app, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", lang
    srv.shutdown()
    srv.server_close()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def call(url, body=None, headers=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={"X-Wiz": "1", **(headers or {})})
    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(req) as r:
            return r.status, r.headers, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read().decode()


def test_binds_loopback_only(server):
    url, _ = server
    assert url.startswith("http://127.0.0.1:")


def test_state_lists_functions_with_their_runs_and_diff(server):
    url, _ = server
    status, _, body = call(url + "/api/state")
    state = json.loads(body)
    assert status == 200
    [fn] = state["functions"]
    assert (fn["name"], fn["signature"], fn["file"], fn["known"]) == ("pricing.shipping", "shipping(subtotal)", "pricing.py", True)
    assert [(r["id"], r["label"], r["test"]) for r in fn["runs"]] == [(FLOW, "subtotal=6000", "test_free_shipping")]
    assert fn["runs"][0]["cached"] is None and state["tests"] == 1
    assert state["diff"]["available"] is False and "no code changes" in state["diff"]["reason"]
    assert state["project"]["name"] == "shop"


def test_page_has_a_strict_csp(server):
    url, _ = server
    status, headers, body = call(url + "/")
    assert status == 200 and "connect-src 'self'" in body and "Only changed functions" in body
    assert headers["X-Frame-Options"] == "DENY"


def test_refuses_other_hosts_and_unmarked_posts(server):
    url, _ = server
    assert call(url + "/api/state", headers={"Host": "evil.example"})[0] == 421
    req = urllib.request.Request(url + "/api/scan", data=b"{}")  # no X-Wiz header
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(req)
    assert err.value.code == 403


def test_open_unrecorded_flow_records_it_then_serves_the_viewer(server):
    url, lang = server
    status, headers, _ = call(url + "/flow?id=" + FLOW)
    assert status == 303 and headers["Location"].startswith("/?open=")

    status, _, body = call(url + "/api/record", {"id": FLOW})
    assert status == 200 and json.loads(body)["status"] in ("queued", "running", "done")
    for _ in range(100):
        run = json.loads(call(url + "/api/state")[2])["functions"][0]["runs"][0]
        if run["cached"]:
            break
        time.sleep(0.05)
    assert run["cached"]["outcome"] == "passed" and run["job"]["status"] == "done"

    status, _, html = call(url + "/flow?id=" + FLOW)
    assert status == 200 and 'class="back" href="/"' in html and FLOW in html
    call(url + "/flow?id=" + FLOW)
    assert lang.recorded == [FLOW]  # reopening is served from the cache


def test_unknown_flow(server):
    url, _ = server
    assert call(url + "/api/record", {"id": "nope"})[0] == 404
    assert call(url + "/flow?id=nope")[0] == 404


def test_failed_recording_reports_reason_hint_and_full_log(server):
    url, lang = server
    from wizualizer.ports import RecordingFailed

    log = "$ ./gradlew :app:test\n" + "x\n" * 50_000 + "BUILD FAILED\n"

    def fail(request):
        raise RecordingFailed("Recording failed: it broke.\nFix: mend it", "it broke", "mend it", log)

    lang.recorder = lambda: type("R", (), {"language": "python", "name": "fake", "record": staticmethod(fail)})()
    call(url + "/api/record", {"id": FLOW})
    for _ in range(100):
        run = json.loads(call(url + "/api/state")[2])["functions"][0]["runs"][0]
        if run.get("job", {}).get("status") == "failed":
            break
        time.sleep(0.05)
    job = run["job"]
    assert (job["reason"], job["hint"], job["has_log"]) == ("it broke", "mend it", True)
    assert "log" not in job  # the state stays small; the log comes on demand
    assert job["log_path"].endswith(".wiz/logs/tests_test_pricing.py__test_free_shipping.log")
    status, _, text = call(url + "/api/log?id=" + FLOW)
    assert status == 200 and text == log
