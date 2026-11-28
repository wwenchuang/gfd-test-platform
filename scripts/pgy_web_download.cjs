// Download the exact Android build through the public PGYER distribution page.
const { chromium } = require('playwright');
const { parseArgs } = require('node:util');
const fs = require('node:fs/promises');
const path = require('node:path');
const { createHash } = require('node:crypto');
const { createReadStream } = require('node:fs');

function officialPage(value) {
  const u = new URL(value);
  if (u.protocol !== 'https:' || !['www.pgyer.com', 'pgyer.com'].includes(u.hostname)
      || u.username || u.password || u.port || u.search || u.hash
      || !/^\/(?:app\/build\/)?[a-zA-Z0-9_-]+\/?$/.test(u.pathname)) {
    throw new Error('需要蒲公英 HTTPS 分发页');
  }
  return u.href;
}

async function main() {
  const { values: args } = parseArgs({ options: {
    url: { type: 'string' }, package: { type: 'string' }, version: { type: 'string' },
    environment: { type: 'string' }, build: { type: 'string' }, output: { type: 'string' },
  } });
  if (!args.url || !args.package || !args.version || !/^\d+$/.test(args.build || '') || !args.output) {
    throw new Error('需要 --url --package --version --build --output');
  }
  process.umask(0o077);
  const url = officialPage(args.url);
  const browser = await chromium.launch({ channel: 'chrome' });
  let tempDir;
  try {
    const page = await browser.newPage({ locale: 'zh-CN', acceptDownloads: true });
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
    officialPage(page.url());
    const detail = await page.locator('#down_load').getAttribute('href');
    if (detail && /^\/app\/build\/[a-f0-9]{32}$/.test(detail)) {
      await page.goto(officialPage(new URL(detail, page.url()).href), { waitUntil: 'domcontentloaded' });
      officialPage(page.url());
    }
    const info = page.locator('.version-info');
    for (const [label, expected] of Object.entries({
      '版本': args.version, 'Build': args.build, 'Bundle ID': args.package, '系统版本': 'Android',
    })) {
      const row = info.filter({ has: page.getByText(label, { exact: true }) });
      const value = (await row.locator('.gray-text').innerText()).trim();
      if (value !== expected) throw new Error('分发页的 ' + label + ' 与所选构建不一致');
    }
    if (args.environment === 'qa' && (await page.getByText(/上架包/).allTextContents()).some(t => /^\s*[“"]?上架包[”"]?\s*$/.test(t))) {
      throw new Error('分发页标注为上架包，与 QA 环境不一致');
    }
    const [download] = await Promise.all([
      page.waitForEvent('download', { timeout: 45000 }),
      page.locator('#down_load').click(),
    ]);
    const destination = path.resolve(args.output);
    tempDir = await fs.mkdtemp(path.join(path.dirname(destination), '.pgy-download-'));
    const file = path.join(tempDir, 'build.apk');
    await download.saveAs(file);
    const head = Buffer.alloc(4);
    const handle = await fs.open(file, 'r');
    try { await handle.read(head, 0, 4, 0); } finally { await handle.close(); }
    if (head.toString('hex') !== '504b0304') throw new Error('下载结果不是 APK 压缩包');
    const hash = createHash('sha256');
    for await (const chunk of createReadStream(file)) hash.update(chunk);
    await fs.link(file, destination); // Never overwrite another build or a prior receipt.
    console.log(JSON.stringify({ build: args.build, version: args.version, package: args.package,
      sha256: hash.digest('hex'), file: destination }));
  } finally {
    if (tempDir) await fs.rm(tempDir, { recursive: true, force: true });
    await browser.close();
  }
}

main().catch(e => {
  const reason = /^(需要|分发页|下载结果)/.test(e.message) ? e.message
    : '网页下载未完成，请检查分发页、安装按钮和浏览器下载状态（' + e.name + '）';
  console.error(reason);
  process.exitCode = 1;
});
