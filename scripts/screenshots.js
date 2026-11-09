// Render the demo viewer headless and save docs/screenshots/*.png.
// Run ./examples/demo.sh first. Needs Playwright (require('playwright')).
const path = require('path');
const { chromium } = require('playwright');

const root = path.resolve(__dirname, '..');
const html = path.join(root, '.wiz', 'tests_test_orders.py__test_order_over_50_ships_free_with_coupon.html');
const out = (name) => path.join(root, 'docs', 'screenshots', name);

const shots = [
  // Flowchart mid-play: inside shipping_cents, showing the path taken and the PR lines that never ran.
  { name: 'flowchart.png', viewport: { width: 1440, height: 900 }, setup: 'wiz.go(76)' },
  { name: 'coverage.png', viewport: { width: 1440, height: 1100 }, setup: "wiz.go(68); wiz.setSide('cov')" },
  { name: 'phone.png', viewport: { width: 390, height: 844 }, setup: 'wiz.go(76)' },
];

(async () => {
  const browser = await chromium.launch();
  for (const s of shots) {
    const page = await browser.newPage({ viewport: s.viewport, deviceScaleFactor: 2 });
    const problems = [];
    page.on('pageerror', (e) => problems.push(e.message));
    page.on('request', (r) => { if (!r.url().startsWith('file:')) problems.push(`network request: ${r.url()}`); });
    await page.goto(`file://${html}`);
    await page.evaluate(s.setup);
    await page.waitForTimeout(250);
    await page.screenshot({ path: out(s.name) });
    if (problems.length) { console.error(s.name, problems); process.exitCode = 1; }
    console.log(out(s.name));
    await page.close();
  }
  await browser.close();
})();
