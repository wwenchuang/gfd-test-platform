const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const runId = 'uib_' + 'a'.repeat(32);
const saved = { id: '00000000-0000-4000-8000-000000000001', source: 'ui', run_id: runId, status: 'completed', elapsed_ms: 2120,
  result: { summary: '页面等待超时。请求回执说明本次服务返回异常，还需业务日志确认原因。', facts: [{ text: '当前步骤未等到预期结果；请求收到 HTTP 504。', evidence_ids: ['e1'] }], hypotheses: [{ text: '可能是上游服务处理超时，尚不能归为脚本问题。', evidence_ids: ['e1'] }], recommendations: [{ text: '按当前请求时间核对网关与业务日志，再决定是否复测。', evidence_ids: ['e1'] }], missing_evidence: ['未找到同版本、同环境的历史成功记录。'] },
  evidence: [{ id: 'e1', title: '本次请求与回执', kind: 'request_trace', href: '/task-manager.html?ui_batch=' + runId, data: { step: '加载列表', status: 504, duration_ms: 30000 } }],
  trace: [{ round: 1, tool: 'get_failed_steps', arguments: {}, status: 'completed', elapsed_ms: 12, summary: '定位到「加载列表」等待超时。' }, { round: 2, tool: 'get_request_trace', arguments: { case_id: 'case-a' }, status: 'completed', elapsed_ms: 17, summary: '读取同次运行的 HTTP 504 回执。' }], usage: { prompt_tokens: 1200, completion_tokens: 400, total_tokens: 1600 } };

test('real browser opens the existing ordinary report, investigates, restores, preserves transport errors and clears denied evidence', async t => {
  const browser = await chromium.launch(); t.after(() => browser.close());
  const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
  const errors = []; page.on('pageerror', error => errors.push(error.message));
  let started = false, deny = 0; const posts = [];
  await page.route('http://127.0.0.1/**', async route => {
    const req = route.request(), url = new URL(req.url());
    if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: `<meta charset="UTF-8"><link rel="stylesheet" href="/css/failure-analysis.css"><style>body{margin:0;background:#10171d;color:#edf5f4;font:14px system-ui}main{max-width:1120px;margin:auto;padding:20px;box-sizing:border-box}.ui-report-actions{display:flex;gap:8px;flex-wrap:wrap}</style><main></main><script src="/js/failure-analysis.js"></script><script src="/js/ui-batch-report.js"></script><script>sessionStorage.setItem('sessionToken','fixture');window.request=async(path,options={})=>{const r=await fetch('/api'+path,{...options,body:options.body?JSON.stringify(options.body):undefined,headers:{Authorization:'Bearer fixture','Content-Type':'application/json'}});const value=await r.json();if(!r.ok)throw Object.assign(Error(value.error),{status:r.status});return value};window.report=UiBatchReport.mount(document.querySelector('main'),{batchId:'${runId}',identity:()=> 'fixture-owner',request});</script>` });
    if (url.pathname.startsWith('/js/') || url.pathname.startsWith('/css/')) return route.fulfill({ path: path.join(__dirname, '..', url.pathname) });
    assert.equal(req.headers().authorization, 'Bearer fixture');
    if (url.pathname.endsWith('/report')) return route.fulfill({ json: { ok: true, report: { status: 'not_requested', snapshot: null } } });
    if (url.pathname.endsWith('/evidence')) return route.fulfill({ json: { ok: true, artifacts: [] } });
    if (url.pathname === '/api/failure-analysis/tasks') {
      if (deny) return route.fulfill({ status: deny, json: { ok: false, error: deny === 403 ? '无权访问' : '服务暂时不可用' } });
      if (req.method() === 'POST') { posts.push(req.postDataJSON()); started = true; return route.fulfill({ json: { ok: true, task: { ...saved, status: 'running', result: null, trace: [], evidence: [] } } }); }
      return route.fulfill({ json: { ok: true, task: started ? saved : null } });
    }
    throw Error('Unexpected request: ' + url.pathname);
  });
  await page.goto('http://127.0.0.1/');
  await page.getByText('本次为普通测试，未申请发版报告。执行结果与截图已保留。').waitFor();
  assert.equal(posts.length, 0);
  await page.getByRole('button', { name: 'AI 分析失败', exact: true }).click();
  await page.getByText('正在调查', { exact: true }).waitFor();
  await page.getByText(saved.result.summary, { exact: true }).waitFor();
  assert.equal(posts.length, 1); assert.equal(posts[0].source, 'ui'); assert.equal(posts[0].run_id, runId);
  await page.locator('[data-analysis-ref]').first().click();
  assert.equal(await page.locator('[data-analysis-evidence]').evaluate(node => node.open), true);
  fs.mkdirSync(path.join(__dirname, '../output/playwright/failure-analysis'), { recursive: true });
  await page.screenshot({ path: path.join(__dirname, '../output/playwright/failure-analysis/report-desktop.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  await page.screenshot({ path: path.join(__dirname, '../output/playwright/failure-analysis/report-mobile.png'), fullPage: true });
  await page.reload(); await page.getByText(saved.result.summary, { exact: true }).waitFor(); assert.equal(posts.length, 1);
  deny = 503; await page.getByRole('button', { name: '刷新调查', exact: true }).click(); await page.getByText('服务暂时不可用', { exact: true }).waitFor();
  assert.equal(await page.getByText(saved.result.summary, { exact: true }).count(), 1);
  deny = 0; await page.getByRole('button', { name: '重试', exact: true }).click(); await page.waitForFunction(() => document.querySelector('[data-analysis-error]').hidden);
  deny = 403; await page.getByRole('button', { name: '刷新调查', exact: true }).click(); await page.getByText('当前无法访问这份调查，请核对账号和报告权限', { exact: true }).waitFor();
  assert.equal(await page.getByText(saved.result.summary, { exact: true }).count(), 0); assert.deepEqual(errors, []);
});
