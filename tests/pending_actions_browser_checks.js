const assert = require('node:assert/strict');

module.exports = async function checkPendingActions(page, screenshotPath) {
  let drafts = Array.from({length: 10}, (_, i) => ({
    draftId: `batch-${i}`, status: 'WAIT_CONFIRM', module: 'AI测试',
    file: i < 2 ? '重复目标.yaml' : `批量-${i}.yaml`, taskName: `批量草稿 ${i}`,
    originalYaml: 'old', fixedYaml: 'new', riskHits: i === 2 ? ['支付'] : [],
  }));
  const requests = [];
  let inflight = 0, maxInflight = 0;
  await page.route('**/api/repair-drafts', route => route.fulfill({json: {ok: true, drafts}}));
  await page.route('**/api/repair-drafts/*', async route => {
    const body = route.request().postDataJSON();
    requests.push({url: route.request().url(), ...body});
    maxInflight = Math.max(maxInflight, ++inflight);
    await new Promise(resolve => setTimeout(resolve, 40));
    inflight--;
    if (body.draftId === 'batch-4') return route.fulfill({status: 409, headers: {'x-test-expected': 'batch-conflict'}, json: {ok: false, error: '原 YAML 已变化，请重新生成草稿'}});
    const saved = {...drafts.find(d => d.draftId === body.draftId), status: route.request().url().endsWith('/apply') ? 'APPLIED' : 'REJECTED'};
    drafts = drafts.filter(d => d.draftId !== body.draftId);
    await route.fulfill({json: {ok: true, applied: saved.status === 'APPLIED', draft: saved}});
  });
  await page.evaluate(async () => { await loadRepairDrafts(); renderJobs(); });
  assert.equal(await page.locator('[data-pending-id]').count(), 8, 'Pending cards need a bounded initial page');
  await page.getByRole('button', {name: /显示更多待处理/}).click();
  assert.equal(await page.locator('[data-pending-id]').count(), 11, 'All ten drafts and the failed job must be discoverable');
  await page.getByLabel('全选待处理').check();
  await page.evaluate(() => renderJobs());
  assert.equal(await page.locator('[data-pending-id]:checked').count(), 11, 'Polling must preserve selection');
  await page.evaluate(() => {
    window.__batchEditor = {module: currentModule, file: currentFile, content: document.getElementById('editor').value, initial: editorInitialContent};
    currentModule = 'AI测试'; currentFile = '批量-3.yaml'; editorInitialContent = 'old';
    document.getElementById('editor').value = 'unsaved manual changes'; editorDirty = true;
  });
  let dirtyConfirmations = 0;
  const dismissDirtyConfirmation = async dialog => { dirtyConfirmations++; await dialog.dismiss(); };
  page.on('dialog', dismissDirtyConfirmation);
  await page.locator('.pending-action-card').filter({has: page.locator('[data-pending-id="repair:batch-3"]')})
    .getByRole('button', {name: '人工确认替换', exact: true}).click();
  page.off('dialog', dismissDirtyConfirmation);
  assert.equal(dirtyConfirmations, 0, 'Single apply must also block before confirmation when the target editor has unsaved changes');
  assert.match(await page.locator('#toast').innerText(), /未保存修改/);
  await page.getByRole('button', {name: '批量确认替换', exact: true}).click();
  assert.match(await page.locator('#pending-batch-dialog').innerText(), /未保存修改/);
  await page.locator('#pending-batch-dialog').getByRole('button', {name: '取消', exact: true}).click();
  await page.evaluate(() => { document.getElementById('editor').value = 'old'; editorDirty = false; });
  await page.locator('.pending-action-card').filter({has: page.locator('[data-pending-id="repair:batch-3"]')})
    .getByRole('button', {name: '人工确认替换', exact: true}).click();
  assert.match(await page.locator('#pending-batch-dialog').innerText(), /已选 1 项，本次处理 1 项/);
  assert.equal(await page.evaluate(() => { document.getElementById('editor').focus(); return document.activeElement.id === 'editor'; }), false,
    'Single replacement must keep the editor inert through the shared confirmation and request flow');
  await page.locator('#pending-batch-dialog').getByRole('button', {name: '取消', exact: true}).click();
  assert.equal(await page.locator('[data-pending-id]:checked').count(), 11, 'Opening a single item must preserve the existing multi-selection');
  await page.evaluate(() => { window.__batchDetachedEditor = document.getElementById('editor'); window.__batchDetachedEditor.remove(); });
  await page.getByRole('button', {name: '批量确认替换', exact: true}).click();
  assert.doesNotMatch(await page.locator('#pending-batch-dialog').innerText(), /未保存修改/, 'Remembering a file without an open editor must not look dirty');
  await page.locator('#pending-batch-dialog').getByRole('button', {name: '取消', exact: true}).click();
  await page.evaluate(() => document.getElementById('editor-area').appendChild(window.__batchDetachedEditor));
  await page.getByRole('button', {name: '批量确认替换', exact: true}).click();
  const dialog = page.locator('#pending-batch-dialog');
  assert.equal(await dialog.locator('.pending-batch-details-primary > li').count(), 10, 'Large previews need a bounded first page');
  assert.match(await dialog.locator('.pending-batch-more summary').innerText(), /展开剩余 1 项/);
  assert.match(await dialog.locator('.pending-batch-skip-summary').innerText(), /重复选择|未保存修改/);
  async function checkDialogLayout(buttonName) {
    const viewport = page.viewportSize();
    const box = await dialog.boundingBox();
    assert.ok(box.x >= 8 && box.y >= 8, 'Dialog needs viewport margins and must not be clipped');
    for (const node of [dialog.locator('h3'), dialog.getByRole('button', {name: buttonName, exact: true})]) {
      const rect = await node.boundingBox();
      assert.ok(rect.y >= box.y && rect.y + rect.height <= viewport.height - 8, 'Title and action must stay visible while details scroll');
    }
  }
  await checkDialogLayout('确认处理');
  assert.match(await dialog.innerText(), /重复目标/);
  assert.match(await dialog.innerText(), /支付/);
  assert.equal(await dialog.getByRole('button', {name: '确认处理'}).isDisabled(), true, 'Risk acknowledgement must precede applying');
  const writesDuringApply = [];
  await page.route('**/api/file', route => {
    if (route.request().method() !== 'POST') return route.continue();
    writesDuringApply.push(route.request().postDataJSON());
    return route.fulfill({json: {ok: true}});
  });
  await dialog.getByLabel('已逐项检查所选草稿及风险，确认替换并备份').check();
  await dialog.getByRole('button', {name: '确认处理'}).click();
  await page.keyboard.press('ControlOrMeta+s');
  await dialog.getByRole('button', {name: '关闭'}).waitFor();
  assert.equal(writesDuringApply.length, 0, 'Keyboard save must not write a stale editor while batch replacement is in progress');
  await page.unroute('**/api/file');
  assert.equal(maxInflight, 1, 'Bulk operations must run serially');
  assert.equal(requests.length, 8, 'Duplicate targets and non-draft jobs must never be submitted for replacement');
  assert.ok(requests.every(r => !['batch-0', 'batch-1'].includes(r.draftId)));
  assert.match(await dialog.innerText(), /成功 7/);
  assert.match(await dialog.innerText(), /失败 1/);
  assert.match(await dialog.innerText(), /跳过 3/);
  assert.match(await dialog.innerText(), /原 YAML 已变化/);
  assert.equal(await page.locator('#editor').inputValue(), 'new', 'Successful replacement must refresh the open editor before another save');
  await checkDialogLayout('关闭');
  await page.screenshot({path: screenshotPath});
  const desktopViewport = page.viewportSize();
  await page.setViewportSize({width: 390, height: 844});
  await checkDialogLayout('关闭');
  await page.screenshot({path: screenshotPath.replace('.png', '-mobile.png')});
  await page.setViewportSize(desktopViewport);
  await dialog.getByRole('button', {name: '关闭'}).click();
  await page.getByRole('button', {name: '批量拒绝', exact: true}).click();
  await dialog.getByLabel('拒绝原因（可选）').fill('重复草稿，保留当前 YAML');
  await dialog.getByRole('button', {name: '确认处理'}).click();
  await dialog.getByRole('button', {name: '关闭'}).waitFor();
  assert.equal(requests.filter(r => r.url.endsWith('/reject')).length, 3);
  assert.match(await dialog.innerText(), /成功 2/);
  assert.match(await dialog.innerText(), /失败 1/);
  await dialog.getByRole('button', {name: '关闭'}).click();
  let reviewConfirmed = false;
  const reviewRequests = [];
  await page.route('**/api/jobs/*/review', route => {
    reviewRequests.push(route.request().postDataJSON());
    return route.fulfill({json: {ok: true, job: {job_id: 'job-baseline-failed', status: 'failed', failure_review: {manual_confirmed: reviewConfirmed}}}});
  });
  await page.getByRole('button', {name: '批量标记已处理', exact: true}).click();
  assert.match(await dialog.innerText(), /不改变原执行结果，也不自动重跑/);
  await dialog.getByRole('button', {name: '确认处理'}).click();
  await dialog.getByRole('button', {name: '关闭'}).waitFor();
  assert.match(await dialog.innerText(), /成功 0 · 失败 1 · 跳过 1/, 'HTTP success without confirmed review must stay pending');
  await dialog.getByRole('button', {name: '关闭'}).click();
  reviewConfirmed = true;
  await page.getByRole('button', {name: '批量标记已处理', exact: true}).click();
  await dialog.getByRole('button', {name: '确认处理'}).click();
  await dialog.getByRole('button', {name: '关闭'}).waitFor();
  assert.match(await dialog.innerText(), /成功 1 · 失败 0 · 跳过 1/);
  assert.equal(reviewRequests.length, 2);
  assert.ok(reviewRequests.every(r => r.suggested_action === 'manual_done'));
  await dialog.getByRole('button', {name: '关闭'}).click();
  await page.unroute('**/api/jobs/*/review');
  await page.evaluate(() => {
    window.__batchOriginalProfile = currentAccessProfile;
    currentAccessProfile = {permissions: ['ui.view']};
    renderJobs();
  });
  assert.equal(await page.locator('[data-pending-id]:enabled').count(), 0, 'Read-only users cannot select mutations');
  assert.equal(await page.getByRole('button', {name: '批量拒绝', exact: true}).isDisabled(), true);
  await page.evaluate(() => { currentAccessProfile = window.__batchOriginalProfile; });
  await page.unroute('**/api/repair-drafts');
  await page.unroute('**/api/repair-drafts/*');
  await page.evaluate(async () => {
    currentModule = window.__batchEditor.module; currentFile = window.__batchEditor.file;
    document.getElementById('editor').value = window.__batchEditor.content;
    editorInitialContent = window.__batchEditor.initial; editorDirty = false;
    await loadRepairDrafts(); renderJobs();
  });
};
