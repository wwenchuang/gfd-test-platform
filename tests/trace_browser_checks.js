const assert = require('node:assert/strict');

module.exports = async function checkTraceFeedback(page) {
  let failTraces = true;
  let failSnapshots = false;
  let holdTraces = false;
  let releaseTrace;
  let traceStarted;
  const started = new Promise(resolve => { traceStarted = resolve; });
  await page.route('**/api/debug/traces?*', async route => {
    if (holdTraces) {
      traceStarted();
      await new Promise(resolve => { releaseTrace = resolve; });
    }
    await route.fulfill({json: failTraces ? {ok: false, error: '点检夹具：Trace 服务暂不可用'} : {
      ok: true, traces: [{traceId: 'agent:audit-trace', title: '点检 Trace 记录', status: 'failed', sourceType: 'agent', summary: {totalNodes: 2}}],
    }});
  });
  await page.route('**/api/debug/snapshots?*', route => route.fulfill({json: failSnapshots
    ? {ok: false, error: '点检夹具：快照加载失败'} : {ok: true, snapshots: []}}));
  await page.click('.workflow-step[data-workflow="execute"]');
  await page.getByRole('button', {name: 'Trace 回放', exact: true}).click();
  await page.locator('.execution-tab-body [role="alert"]').waitFor();
  assert.match(await page.locator('.execution-tab-body').innerText(), /Trace.*暂不可用/);
  assert.doesNotMatch(await page.locator('.execution-tab-body').innerText(), /暂无 Trace/);
  failTraces = false;
  await page.locator('.execution-tab-body').getByRole('button', {name: '刷新 Trace', exact: true}).click();
  await page.getByText('点检 Trace 记录', {exact: true}).waitFor();
  failSnapshots = true;
  await page.locator('.execution-tab-body').getByRole('button', {name: '刷新 Trace', exact: true}).click();
  await page.locator('.execution-tab-body [role="alert"]').waitFor();
  assert.match(await page.locator('.execution-tab-body').innerText(), /快照加载失败/);
  assert.equal(await page.getByText('点检 Trace 记录', {exact: true}).isVisible(), true, 'Snapshot failure must not discard loaded traces');
  holdTraces = true;
  await page.locator('.execution-tab-body').getByRole('button', {name: '刷新 Trace', exact: true}).click();
  await started;
  await page.click('.workflow-step[data-workflow="assets"]');
  const response = page.waitForResponse(r => r.url().includes('/debug/traces?'));
  releaseTrace();
  await response;
  await page.waitForTimeout(100);
  assert.equal(await page.locator('.assets-table').isVisible(), true, 'Late Trace response must not replace the page the user navigated to');
  await page.unroute('**/api/debug/traces?*');
  await page.unroute('**/api/debug/snapshots?*');
  await page.evaluate(() => { executionActiveTab = 'debug'; });
};
