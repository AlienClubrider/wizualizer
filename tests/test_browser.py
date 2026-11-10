"""The viewer and the functions page in a real (headless) browser: following calls, opening on a
function, paging. Needs Node and Playwright (as scripts/screenshots.js does); skipped without them."""

import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from wizualizer.discovery import Flow
from wizualizer.functions import EntryPoint
from wizualizer.ports import RecordRequest
from wizualizer.python.recorder import PythonRecorder
from wizualizer.python.source import PythonSource
from wizualizer.trace import make_trace
from wizualizer.viewer.render import render_html
from wizualizer.web.server import App, serve
from wizualizer.workspace import Workspace

ROOT = Path(__file__).parent.parent
SHOP = ROOT / "examples" / "shop"


def _node_path() -> str | None:
    if not shutil.which("node"):
        return None
    paths = [str(ROOT / "node_modules"), str(Path.home() / "node_modules")]
    try:
        paths.append(subprocess.run(["npm", "root", "-g"], capture_output=True, text=True, timeout=20).stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        pass
    env = {**os.environ, "NODE_PATH": os.pathsep.join(paths)}
    ok = subprocess.run(["node", "-e", "require('playwright')"], env=env, capture_output=True).returncode == 0
    return env["NODE_PATH"] if ok else None


NODE_PATH = _node_path()
pytestmark = pytest.mark.skipif(NODE_PATH is None, reason="needs Node and Playwright")


def browse(tmp_path, url: str, script: str) -> dict:
    """Open url in headless Chromium, run `script` (async JS with `page`, returns JSON), fail on page errors."""
    js = tmp_path / "drive.js"
    js.write_text(f"""
const {{ chromium }} = require('playwright');
(async () => {{
  const browser = await chromium.launch();
  const out = {{}};
  for (const vp of [{{ width: 1280, height: 860, name: 'desk' }}, {{ width: 390, height: 844, name: 'phone' }}]) {{
    const page = await browser.newPage({{ viewport: vp, colorScheme: 'light' }});
    const errors = [];
    page.on('pageerror', (e) => errors.push(e.message));
    await page.goto({json.dumps(url)});
    out[vp.name] = await (async (page, phone) => {{ {script} }})(page, vp.name === 'phone');
    out[vp.name].errors = errors;
  }}
  await browser.close();
  console.log(JSON.stringify(out));
}})().catch((e) => {{ console.error(e); process.exit(1); }});
""")
    proc = subprocess.run(["node", str(js)], env={**os.environ, "NODE_PATH": NODE_PATH},
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@pytest.fixture(scope="module")
def shop_trace():
    request = RecordRequest("tests/test_orders.py::test_order_over_50_ships_free_with_coupon", SHOP, SHOP,
                            interpreter=sys.executable)
    return make_trace(PythonRecorder(), PythonSource(), request)


def test_viewer_steps_into_over_and_out_of_calls(shop_trace, tmp_path):
    page = tmp_path / "v.html"
    page.write_text(render_html(shop_trace))
    got = browse(tmp_path, page.as_uri(), """
      const at = () => page.evaluate(() => ({ fn: wiz.fn.split(':')[0] + ':' + wiz.fn.split(':')[1], depth: wiz.depth,
        crumbs: [...document.querySelectorAll('#crumbs button')].map((b) => b.textContent),
        title: document.getElementById('test-id').textContent }));
      const r = { start: await at() };
      if (phone) await page.click('#tabs button[data-view="flow"]');
      const chip = page.locator('.callchip.real').first();
      r.chip = await chip.locator('text').textContent();
      await chip.click();
      r.into = await at();
      await page.click('#b-out');
      r.out = await at();
      await page.click('#b-into');
      await page.click('#b-into');
      r.deeper = await at();
      await page.locator('#crumbs button:not(.here)').first().click();
      r.crumb = await at();
      if (phone) await page.click('#tabs button[data-view="vars"]');
      await page.locator('#b-over').click();
      await page.locator('#stack button.frame').last().click();
      r.stack = await at();
      return r;
    """)
    for vp in ("desk", "phone"):
        r = got[vp]
        assert r["errors"] == []
        assert r["start"]["title"] == "OrderService.place_order" and r["start"]["depth"] == 1
        assert r["chip"].startswith("↘ ") and "…" not in r["chip"]
        assert r["into"]["depth"] == 2 and r["into"]["crumbs"][0] == "OrderService.place_order"
        assert r["out"]["depth"] == 1
        assert r["deeper"]["depth"] >= 2
        assert r["crumb"]["depth"] == 1 and r["crumb"]["crumbs"] == ["OrderService.place_order"]
        assert r["stack"]["depth"] == 1


def test_viewer_opens_the_real_code_of_a_mocked_call(mocked_trace, tmp_path):
    page = tmp_path / "v.html"
    page.write_text(render_html(mocked_trace))
    got = browse(tmp_path, page.as_uri() + "#fn=app/checkout.py::Checkout.pay", """
      const at = () => page.evaluate(() => ({ stand: wiz.stand, step: wiz.step, note: wiz.note,
        crumbs: [...document.querySelectorAll('#crumbs button')].map((b) => b.textContent),
        banner: document.getElementById('callnote').hidden ? '' : document.getElementById('callnote').innerText,
        vars: document.getElementById('vars').textContent }));
      if (phone) await page.click('#tabs button[data-view="flow"]');
      const r = { start: await at() };
      r.chips = await page.$$eval('#flow .callchip', (els) => els.map((e) => e.getAttribute('class') + ':' + e.dataset.name));
      await page.click('#flow .callchip.stand[data-name="charge"]');
      r.open = await at();
      await page.locator('#crumbs button:not(.here)').last().click();
      r.back = await at();
      await page.click('#flow .callchip.stand[data-name="send_receipt"]');
      r.stub = await at();
      await page.click('#b-out');
      r.out = await at();
      await page.locator('#flow .callchip.lib, #flow .callchip.more').first().click();
      r.lib = await at();
      // into: from the line that calls the mock, into its real code, then on to the next line
      await page.click('#flow .callchip.real[data-name="total"]');
      await page.click('#b-out');
      for (let k = 0; k < 12 && !(await at()).stand; k++) await page.click('#b-into');
      r.into = await at();
      await page.click('#b-into');
      r.after = await at();
      return r;
    """)
    for vp in ("desk", "phone"):
        r = got[vp]
        assert r["errors"] == []
        assert "callchip stand:charge" in r["chips"] and "callchip stand:send_receipt" in r["chips"]
        assert "callchip real:total" in r["chips"]
        o = r["open"]
        assert o["stand"] == "app/gateway.py:11" and o["crumbs"][-1] == "CardGateway.charge not run"
        assert "Not run in this flow: replaced by a mock in the test" in o["banner"]
        assert "account = 'ann', cents = 3" in o["banner"] and "the mock returned 'ok-1'" in o["banner"]
        assert "account'ann'cents3the mock returned'ok-1'" in o["vars"]
        assert r["back"]["stand"] is None and r["back"]["crumbs"][-1] == "Checkout.pay"
        assert r["stub"]["stand"] == "app/gateway.py:17" and "replaced by a stub" in r["stub"]["banner"]
        assert r["out"]["stand"] is None
        assert r["lib"]["note"] and r["lib"]["banner"].startswith("×")
        assert r["into"]["stand"] == "app/gateway.py:11"
        assert r["after"]["stand"] is None and r["after"]["step"] == r["into"]["step"] + 1


def test_viewer_opens_on_the_function_asked_for(shop_trace, tmp_path):
    page = tmp_path / "v.html"
    page.write_text(render_html(shop_trace))
    got = browse(tmp_path, page.as_uri() + "#fn=shop/pricing.py::shipping_cents", """
      return await page.evaluate(() => ({ fn: wiz.fn, title: document.getElementById('test-id').textContent,
        source: document.getElementById('source').textContent }));
    """)
    r = got["desk"]
    assert r["errors"] == [] and r["fn"].startswith("shop/pricing.py:")
    assert r["title"] == "shipping_cents" and r["source"] == "from test_orders.py › test_order_over_50_ships_free_with_coupon"


class ManyFunctions:
    """25 tests, each about its own function: two pages."""

    name = "python"

    def __init__(self, root):
        self.root = root

    def discover(self):
        return [Flow(f"tests/test_m.py::test_{i:02}", "python", "tests/test_m.py", "", f"test_{i:02}") for i in range(25)]

    def entry_points(self, flows):
        return {f.id: EntryPoint("shop/m.py", f"f{f.name[-2:]}", f"f{f.name[-2:]}(x)", 1, (("x", "1"),)) for f in flows}

    def interpreter(self):
        return None

    def source(self):
        return PythonSource()


def test_functions_page_pages_and_searches(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    app = App(Workspace(tmp_path, {"python": ManyFunctions(tmp_path)}), {"name": "m", "kind": "pytest", "problems": []})
    srv = serve(app, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        got = browse(tmp_path, f"http://127.0.0.1:{srv.server_address[1]}/", """
          await page.waitForSelector('.fn');
          const names = () => page.$$eval('.fn-name', (e) => e.map((x) => x.textContent));
          const r = { first: await names(), pager: await page.textContent('#pager') };
          await page.click('#pager button:last-child');
          r.second = await names();
          await page.fill('#search', 'f24');
          r.search = await names();
          r.pagerHidden = await page.$eval('#pager', (e) => e.hidden);
          r.run = await page.$eval('.run', (a) => a.getAttribute('href'));
          return r;
        """)
    finally:
        srv.shutdown()
        srv.server_close()
    for vp in ("desk", "phone"):
        r = got[vp]
        assert r["errors"] == []
        assert len(r["first"]) == 20 and r["first"][0] == "m.f00" and "Page 1 of 2 · 25 functions" in r["pager"]
        assert r["second"] == [f"m.f{i}" for i in range(20, 25)]
        assert r["search"] == ["m.f24"] and r["pagerHidden"]
        assert r["run"] == "/flow?id=tests%2Ftest_m.py%3A%3Atest_24#fn=shop%2Fm.py%3A%3Af24"
