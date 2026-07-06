// Visual regression baseline (ROADMAP Item 11).
//
// Run against a live `python -m screener.webapp` with SCREENER_FORCE_DEMO=1
// set (so screenshots compare against deterministic synthetic data, not a
// real store that changes every trading day — see webapp._demo_forced()):
//
//   SCREENER_FORCE_DEMO=1 python -m screener.webapp &
//   cd web/visual && npm install && npx playwright install chromium
//   npm test              # compares against the committed baseline
//   npm run update        # regenerates the baseline after an intentional UI change
//
// Six named views per the roadmap: define, results, modal, dashboard,
// allocate, watchlist.
const { test, expect } = require('@playwright/test');
const fs = require('fs');
const path = require('path');

// The watchlist test below adds a symbol via the real API (there's no
// "run in a sandbox" mode) — reset the on-disk file first so repeated
// runs stay idempotent instead of accumulating duplicate rows that
// would show up as a spurious visual diff.
const WATCHLIST_FILE = path.join(__dirname, '..', '..', '..', 'data', 'watchlist.jsonl');
function resetWatchlistFile() {
  try { fs.unlinkSync(WATCHLIST_FILE); } catch (e) { /* fine if absent */ }
}

async function waitLoaded(page) {
  await page.goto('/');
  await page.waitForFunction(() =>
    document.getElementById('status').textContent.includes('data as of'));
}

async function runTrendScreen(page) {
  await page.click('#tabJs');
  await page.fill('#qJs', JSON.stringify({conditions: [{type: 'trend', direction: 'up'}]}));
  await page.click('#btnRun');
  await page.waitForSelector('.stats', { state: 'visible', timeout: 15000 });
  await page.waitForFunction(
    () => document.getElementById('matchesSection').children.length > 0,
    { timeout: 15000 });
}

test('define — initial screen-definition view', async ({ page }) => {
  await waitLoaded(page);
  await expect(page).toHaveScreenshot('define.png');
});

test('results — after running a screen', async ({ page }) => {
  await waitLoaded(page);
  await runTrendScreen(page);
  await expect(page).toHaveScreenshot('results.png', { fullPage: true });
});

test('modal — full chart', async ({ page }) => {
  await waitLoaded(page);
  await runTrendScreen(page);
  await page.click('#matchesSection .match:first-child .mhead');
  await page.click('#matchesSection .match:first-child button:has-text("full chart")');
  await page.waitForSelector('#chartModalBody svg', { timeout: 15000 });
  await expect(page.locator('#chartModal')).toHaveScreenshot('modal.png');
});

test('dashboard — multi-screen picker + results', async ({ page }) => {
  await waitLoaded(page);
  await page.click('#btnDashboard');
  await page.waitForTimeout(200);
  await page.check('#dashboardPanel input[value="support_50ema_uptrend"]');
  await page.check('#dashboardPanel input[value="golden_cross"]');
  await page.click('#dashboardPanel button:has-text("run selected")');
  await page.waitForSelector('#dashboardResults table', { timeout: 15000 });
  await expect(page.locator('#dashboardPanel')).toHaveScreenshot('dashboard.png');
});

test('allocate — sizing results', async ({ page }) => {
  await waitLoaded(page);
  await runTrendScreen(page);
  await page.click('#btnAllocate');
  await page.waitForTimeout(200);
  await page.click('#allocatePanel button.primary');
  await page.waitForFunction(
    () => document.getElementById('allocateResults').innerHTML.includes('Deployed'),
    { timeout: 15000 });
  await expect(page.locator('#allocatePanel')).toHaveScreenshot('allocate.png');
});

test('watchlist — tagged match with decay status', async ({ page }) => {
  resetWatchlistFile();
  await waitLoaded(page);
  await runTrendScreen(page);
  await page.click('#matchesSection .match:first-child .mhead');
  await page.click('#matchesSection .match:first-child button:has-text("watch")');
  await page.waitForTimeout(300);
  await page.click('#btnWatchlist');
  await page.waitForFunction(
    () => document.getElementById('watchlistPanel').children.length > 0,
    { timeout: 15000 });
  await expect(page.locator('#watchlistPanel')).toHaveScreenshot('watchlist.png');
});
