// @ts-check
const { defineConfig } = require('@playwright/test');

module.exports = defineConfig({
  testDir: './tests',
  timeout: 30_000,
  fullyParallel: false, // one server, one demo-data state — run in order
  retries: 0,
  use: {
    baseURL: 'http://127.0.0.1:8501',
    viewport: { width: 1200, height: 1400 },
    colorScheme: 'dark',
  },
  expect: {
    toHaveScreenshot: { maxDiffPixelRatio: 0.01 },
  },
});
