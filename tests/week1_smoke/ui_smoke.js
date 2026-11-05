#!/usr/bin/env node
// An anonymous login-page text check.  No credentials or form submissions.
const { chromium } = require('playwright');
const fs = require('node:fs/promises');
const path = require('node:path');

async function main() {
  const mode = process.argv[2];
  if (!['positive', 'intentional-failure'].includes(mode)) throw new Error('mode must be positive or intentional-failure');
  const target = new URL(process.env.W01_UI_URL || 'http://127.0.0.1:8088/task-manager.html');
  if (target.protocol !== 'http:' || target.hostname !== '127.0.0.1' || target.pathname !== '/task-manager.html' || target.username || target.password || target.search || target.hash) {
    throw new Error('only anonymous local task-manager.html is allowed');
  }
  const out = path.resolve(process.env.W01_UI_ARTIFACTS || 'artifacts', mode);
  await fs.mkdir(out, { recursive: true });
  const browser = await chromium.launch({ headless: true, ...(process.env.W01_BROWSER_EXECUTABLE ? { executablePath: process.env.W01_BROWSER_EXECUTABLE } : {}) });
  const context = await browser.newContext();
  await context.tracing.start({ screenshots: true, snapshots: true });
  const page = await context.newPage();
  let result = 2;
  try {
    await page.goto(target.href, { waitUntil: 'domcontentloaded', timeout: 15000 });
    const title = page.locator('#login-screen h1');
    await title.waitFor({ state: 'visible', timeout: 10000 });
    const actual = (await title.innerText()).trim();
    const expected = mode === 'positive' ? '功夫豆测试平台' : '故意设置不存在的标题';
    const passed = actual === expected;
    console.log(JSON.stringify({ mode, expected, actual, passed }));
    result = passed ? 0 : 1;
  } catch (_) {
    console.error('ERROR: page or browser unavailable; inspect trace privately');
  } finally {
    await page.screenshot({ path: path.join(out, 'screenshot.png') }).catch(() => {});
    await context.tracing.stop({ path: path.join(out, 'trace.zip') });
    await browser.close();
  }
  return result;
}
main().then(code => { process.exitCode = code; }).catch(() => { console.error('ERROR: invalid configuration or browser unavailable'); process.exitCode = 2; });
