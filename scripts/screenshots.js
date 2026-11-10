// Render the viewer and the functions page headless (light theme) and save docs/screenshots/*.png.
// Needs Playwright (require('playwright')).
//   node scripts/screenshots.js                         viewer shots of the Python demo (run ./examples/demo.sh first)
//   node scripts/screenshots.js java <viewer.html>      the RuneStone-Bank transfer test (see docs/java.md) as java-*.png
//   node scripts/screenshots.js functions <url>         the functions page of a running `wiz` as functions*.png
const path = require('path');
const { chromium } = require('playwright');

const root = path.resolve(__dirname, '..');
const mode = process.argv[2] || 'python';
const out = (name) => path.join(root, 'docs', 'screenshots', name);
const target = {
  python: `file://${path.join(root, '.wiz', 'tests_test_orders.py__test_order_over_50_ships_free_with_coupon.html')}`,
  java: `file://${path.resolve(process.argv[3] || '')}`,
  functions: process.argv[3],
}[mode];

const shots = {
  python: [
    // Flowchart mid-play: stepped into shipping_cents from place_order (call path above the
    // flowchart), showing the path taken and the PR lines that never ran.
    { name: 'flowchart.png', viewport: { width: 1440, height: 900 }, setup: 'wiz.go(71)' },
    { name: 'coverage.png', viewport: { width: 1440, height: 1100 }, setup: "wiz.go(63); wiz.setSide('cov')" },
    { name: 'phone.png', viewport: { width: 390, height: 844 }, setup: 'wiz.go(71)' },
  ],
  java: [
    // Mid-play in TransactionServiceImpl.transfer, on the line the PR changed.
    { name: 'java-flowchart.png', viewport: { width: 1440, height: 900 }, setup: 'wiz.go(66)' },
    { name: 'java-phone.png', viewport: { width: 390, height: 844 }, setup: 'wiz.go(66)' },
  ],
  functions: [
    { name: 'functions.png', viewport: { width: 1200, height: 900 }, setup: 'window.scrollTo(0, 0)', wait: '.run' },
    { name: 'functions-phone.png', viewport: { width: 390, height: 844 }, setup: 'window.scrollTo(0, 0)', wait: '.run' },
  ],
}[mode];

(async () => {
  const browser = await chromium.launch();
  for (const s of shots) {
    const page = await browser.newPage({ viewport: s.viewport, deviceScaleFactor: 2, colorScheme: 'light' });
    const problems = [];
    page.on('pageerror', (e) => problems.push(e.message));
    page.on('request', (r) => {
      const u = r.url();
      if (!u.startsWith('file:') && !(mode === 'functions' && u.startsWith(target))) problems.push(`network request: ${u}`);
    });
    await page.goto(target);
    await page.evaluate(s.setup).catch(() => {}); // a reload ends the evaluate early
    if (s.wait) await page.waitForSelector(s.wait);
    await page.waitForTimeout(300);
    await page.screenshot({ path: out(s.name) });
    if (problems.length) { console.error(s.name, problems); process.exitCode = 1; }
    console.log(out(s.name));
    await page.close();
  }
  await browser.close();
})();
