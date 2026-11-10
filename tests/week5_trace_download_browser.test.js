// Browser download contract using the actual catalog UI; fixtures are local only.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const {spawnSync} = require('node:child_process');
const {chromium} = require('playwright');

const source = fs.readFileSync(path.join(__dirname, '..', 'js', 'ui-simple-entry.js'));
const zip = Buffer.from('UEsDBBQAAAAAACRxal0XyuPAGwAAABsAAAALAAAAdHJhY2UudHJhY2V7InR5cGUiOiJjb250ZXh0LW9wdGlvbnMifQpQSwECFAMUAAAAAAAkcWpdF8rjwBsAAAAbAAAACwAAAAAAAAAAAAAAgAEAAAAAdHJhY2UudHJhY2VQSwUGAAAAAAEAAQA5AAAARAAAAAAA', 'base64');
const id = 'a'.repeat(32);
const token = 'fixture-token';
const html = `<!doctype html><meta charset="utf-8"><main id="page"></main>
<script>
window.currentAccessProfile={user_id:'fixture',status:'active',permissions:['ui.view'],scope:{ui_apps:[]}};
window.sessionToken=()=>sessionStorage.getItem('sessionToken');
window.hasPermission=p=>p==='ui.view';
window.authHeaders=(extra={})=>new Headers({...extra,Authorization:'Bearer '+sessionToken()});
window.nativeFetch=window.fetch.bind(window);
sessionStorage.setItem('sessionToken','${token}');
</script><script src="/js/ui-simple-entry.js"></script>
<script>attachCatalogWebCheck(document.getElementById('page'),()=>true);</script>`;

test('catalog trace actual browser save and authorization guards', {timeout: 60000}, async t => {
  let responseStatus = 200, delayResponse = false, pending = null;
  let requested = 0, authorization = [];
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://127.0.0.1');
    if (url.pathname === '/') {res.writeHead(200, {'Content-Type':'text/html; charset=utf-8'}); res.end(html); return;}
    if (url.pathname === '/js/ui-simple-entry.js') {res.writeHead(200, {'Content-Type':'application/javascript'}); res.end(source); return;}
    if (url.pathname === '/api/ui-executions/web-check') {
      assert.equal(req.headers.authorization, 'Bearer ' + token);
      res.writeHead(200, {'Content-Type':'application/json'});
      res.end(JSON.stringify({ok:true,results:[{id,scenario:'ui_catalog_count',status:'passed',total:1,artifacts:['trace']}]})); return;
    }
    if (url.pathname === '/api/ui-executions/web-check/artifact') {
      requested++; authorization.push(req.headers.authorization);
      assert.equal(url.searchParams.get('id'), id); assert.equal(url.searchParams.get('item'), 'trace');
      const send = () => {if (res.destroyed) return; res.writeHead(responseStatus, {'Content-Type':responseStatus === 200 ? 'application/zip' : 'application/json', 'Content-Length':responseStatus === 200 ? zip.length : 2}); res.end(responseStatus === 200 ? zip : '{}');};
      if (delayResponse) pending = send; else send();
      return;
    }
    res.writeHead(404); res.end();
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({headless:true});
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'week5-download-'));
  t.after(async () => {if (pending) pending(); await browser.close(); await new Promise(resolve => server.close(resolve)); fs.rmSync(temp,{recursive:true,force:true});});
  const page = await browser.newPage({acceptDownloads:true});
  const downloads = []; page.on('download', item => downloads.push(item));
  await page.goto(`http://127.0.0.1:${server.address().port}/`);
  await page.locator('[data-catalog-check] summary').click();
  await page.locator('[data-catalog-artifact="trace"]').waitFor();

  const [download] = await Promise.all([
    page.waitForEvent('download', {timeout:10000}),
    page.locator('[data-catalog-artifact="trace"]').click(),
  ]);
  assert.equal(download.suggestedFilename(), '用例库页面检查-trace.zip');
  const saved = path.join(temp, 'saved.zip'); await download.saveAs(saved);
  assert.deepEqual(fs.readFileSync(saved), zip);
  const inspect = spawnSync('python3', ['-c', 'import sys,zipfile; z=zipfile.ZipFile(sys.argv[1]); assert z.testzip() is None; assert "trace.trace" in z.namelist()', saved]);
  assert.equal(inspect.status, 0, inspect.stderr.toString());
  assert.equal(authorization[0], 'Bearer ' + token);

  for (const status of [401,403]) {
    responseStatus = status;
    // A denied artifact clears history; load it afresh for the next attempt.
    if (!await page.locator('[data-catalog-artifact="trace"]').count()) {
      await page.locator('[data-catalog-refresh]').click();
      await page.locator('[data-catalog-artifact="trace"]').waitFor();
    }
    const before = downloads.length;
    await page.locator('[data-catalog-artifact="trace"]').click();
    await page.locator('[data-catalog-notice]').getByText(status === 401 ? '登录已失效，请重新登录。' : '证据不存在或当前账号无权查看。').waitFor();
    await page.waitForTimeout(250);
    assert.equal(downloads.length, before);
  }

  responseStatus = 200; delayResponse = true;
  await page.locator('[data-catalog-refresh]').click();
  await page.locator('[data-catalog-artifact="trace"]').waitFor();
  const before = downloads.length, requestTarget = requested + 1;
  await page.locator('[data-catalog-artifact="trace"]').click();
  await expectCondition(() => requested === requestTarget);
  await page.evaluate(() => sessionStorage.setItem('sessionToken','different-user-token'));
  pending(); pending = null;
  await page.getByText('账号或权限已变化，请重新打开此页面。').waitFor();
  await page.waitForTimeout(250);
  assert.equal(downloads.length, before);
  assert.ok(authorization.every(value => value === 'Bearer ' + token));
});

async function expectCondition(check) {
  for (let i=0;i<100;i++) {if (check()) return; await new Promise(resolve=>setTimeout(resolve,20));}
  throw new Error('fixture request was not received');
}
