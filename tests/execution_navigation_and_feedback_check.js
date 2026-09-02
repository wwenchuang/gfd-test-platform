const assert = require('node:assert/strict');
const fs = require('node:fs');
const test = require('node:test');
const {JSDOM} = require('../api-testing-ui/node_modules/jsdom');

function loadFunction(window, source, name) {
  const start = source.search(new RegExp(`^(?:async )?function ${name}\\(`, 'm'));
  assert.notEqual(start, -1, `${name} must exist`);
  const rest = source.slice(start);
  const next = rest.slice(1).search(/^(?:async )?function [\w$]+\(/m);
  window.eval(next < 0 ? rest : rest.slice(0, next + 1));
}

test('the main platform commits Chinese input when Safari omits the final input event', async t => {
  const source = fs.readFileSync('js/utils.js', 'utf8');
  const dom = new JSDOM('<body><input id="search" type="search"><input id="text" type="text"><textarea id="notes"></textarea></body>', {runScripts: 'dangerously'});
  t.after(() => dom.window.close());
  const win = dom.window;
  loadFunction(win, source, 'installImeCompositionGuard');
  win.installImeCompositionGuard(win.document);

  for (const id of ['search', 'text', 'notes']) {
    const input = win.document.getElementById(id);
    let inputCount = 0;
    input.addEventListener('input', () => { inputCount += 1; });
    input.dispatchEvent(new win.CompositionEvent('compositionstart', {bubbles: true, data: ''}));
    input.value = 'shoucang';
    input.dispatchEvent(new win.InputEvent('input', {
      bubbles: true,
      data: 'g',
      inputType: 'insertCompositionText',
      isComposing: true,
    }));
    assert.equal(inputCount, 0, `${id} must not filter or rerender during IME composition`);

    input.value = '收藏';
    input.dispatchEvent(new win.CompositionEvent('compositionend', {bubbles: true, data: '收藏'}));
    await new Promise(resolve => win.setTimeout(resolve, 0));
    assert.equal(inputCount, 1, `${id} must handle the committed Chinese value once`);
    assert.equal(input.value, '收藏');
  }
});

test('workflow guide action buttons keep quoted navigation calls clickable', t => {
  const source = fs.readFileSync('js/navigation.js', 'utf8');
  const dom = new JSDOM('<body></body>', {runScripts: 'dangerously'});
  t.after(() => dom.window.close());
  const win = dom.window;
  const calls = [];
  win.escapeHtml = value => String(value || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
  win.activateWorkflow = key => calls.push(key);
  win.eval(`
    var activeWorkflow = 'yaml_edit';
    var WORKFLOW_SECTIONS = {
      yaml_edit: {
        index: '1', title: 'YAML 编辑', subtitle: '选择 YAML', help: '先选择文件', checklist: [],
        cards: [{title: '选择', text: '从资产打开', actions: [
          {label: '去用例资产选择', cls: 'primary', fn: 'activateWorkflow("assets")'}
        ]}]
      }
    };
  `);
  loadFunction(win, source, 'workflowGuideHtml');
  win.document.body.innerHTML = win.workflowGuideHtml('yaml_edit');

  const button = win.document.querySelector('.workflow-card-actions button');
  assert.equal(button.textContent.trim(), '去用例资产选择');
  button.click();
  assert.deepEqual(calls, ['assets']);
});

test('returning from a repair draft restores a real execution tab instead of a blank body', async t => {
  const dom = new JSDOM('<body></body>', {runScripts: 'dangerously'});
  t.after(() => dom.window.close());
  const win = dom.window;
  const calls = [];
  Object.assign(win, {
    activateWorkflow: async key => calls.push(['workflow', key]),
    setExecutionTab: key => calls.push(['tab', key]),
  });
  loadFunction(win, fs.readFileSync('js/ai-repair.js', 'utf8'), 'returnToRunnerPendingActions');
  await win.returnToRunnerPendingActions();
  assert.deepEqual(calls, [['workflow', 'execute'], ['tab', 'debug']]);
});

test('cancelling repair prompts keeps the current workflow and editor actions visible', async t => {
  const source = fs.readFileSync('js/execution.js', 'utf8');
  const dom = new JSDOM('<body></body>', {runScripts: 'dangerously'});
  t.after(() => dom.window.close());
  const win = dom.window;
  const transitions = [];
  Object.assign(win, {
    showToast: () => {},
    confirm: () => false,
    prompt: () => '',
    setActiveWorkflow: key => transitions.push(key),
    detectSelectedTaskName: () => '当前用例',
  });
  win.eval("var currentModule = '业务基线'; var currentFile = '当前.yaml';");
  loadFunction(win, source, 'repairCurrentFile');
  loadFunction(win, source, 'repairSelectedTask');
  loadFunction(win, source, 'repairTaskByName');

  await win.repairCurrentFile();
  await win.repairSelectedTask();
  await win.repairTaskByName('当前用例');

  assert.deepEqual(transitions, []);
});

test('the optional Agent install step returns to the preserved Agent form', async t => {
  const executionSource = fs.readFileSync('js/execution.js', 'utf8');
  const agentSource = fs.readFileSync('js/agent-workbench.js', 'utf8');
  const dom = new JSDOM('<body></body>', {runScripts: 'dangerously'});
  t.after(() => dom.window.close());
  const win = dom.window;
  const calls = [];
  Object.assign(win, {
    appInstallReturnWorkflow: 'agent',
    activeWorkflow: 'agent',
    clearAppInstallFeedback: () => calls.push(['clear']),
    activateWorkflow: async key => calls.push(['workflow', key]),
    setExecutionTab: (key, options) => calls.push(['tab', key, options]),
    captureAgentFormDraft: () => calls.push(['capture']),
    showToast: () => {},
  });
  loadFunction(win, executionSource, 'returnFromAppInstall');
  await win.returnFromAppInstall();
  assert.deepEqual(calls, [['clear'], ['workflow', 'agent']]);
  assert.equal(win.appInstallReturnWorkflow, 'execute');

  calls.length = 0;
  loadFunction(win, agentSource, 'openAgentAppInstall');
  await win.openAgentAppInstall();
  assert.deepEqual(JSON.parse(JSON.stringify(calls)), [
    ['capture'],
    ['workflow', 'execute'],
    ['tab', 'install', {returnWorkflow: 'agent'}],
  ]);
});

test('editing the APK install form clears contradictory validation feedback immediately', t => {
  const source = fs.readFileSync('js/execution.js', 'utf8');
  const dom = new JSDOM(`
    <body>
      <div id="apk-install-status" class="generate-status show error">请先选择执行设备</div>
      <div id="toast" class="toast error show">请先选择执行设备</div>
    </body>
  `, {runScripts: 'dangerously'});
  t.after(() => dom.window.close());
  const win = dom.window;
  loadFunction(win, source, 'clearAppInstallFeedback');
  const toast = win.document.getElementById('toast');
  toast._toastTimer = win.setTimeout(() => {}, 1000);
  win.clearAppInstallFeedback();
  const status = win.document.getElementById('apk-install-status');
  assert.equal(status.textContent, '');
  assert.equal(status.className, 'generate-status');
  assert.equal(toast.className, 'toast');
  assert.equal(toast._toastTimer, null);
  assert.match(source, /id="apk-install-mode" onchange="clearAppInstallFeedback\(\); syncAppInstallMode\(\)"/);
  assert.match(source, /id="apk-install-source" onchange="clearAppInstallFeedback\(\); syncAppInstallMode\(\)"/);
  assert.match(source, /id="apk-install-file"[^>]+onchange="clearAppInstallFeedback\(\)"/);
  assert.match(source, /id="apk-install-url"[^>]+oninput="clearAppInstallFeedback\(\)"/);
  assert.match(source, /id="apk-install-device" onchange="clearAppInstallFeedback\(\)"/);
  assert.match(source, /onclick="returnFromAppInstall\(\)"/);
});

test('mindmap report validation feedback stays visible after an action is rejected', t => {
  const source = fs.readFileSync('js/app.js', 'utf8');
  const dom = new JSDOM('<body><div id="mindmap-report-status" class="generate-status show">ready</div></body>', {runScripts: 'dangerously'});
  t.after(() => dom.window.close());
  const win = dom.window;
  loadFunction(win, source, 'setMindmapReportStatus');

  win.setMindmapReportStatus('请至少选择一条用例。', 'error');

  const status = win.document.getElementById('mindmap-report-status');
  assert.equal(status.textContent, '请至少选择一条用例。');
  assert.equal(status.className, 'generate-status show error');
});

test('mindmap report preview distinguishes automation totals from pending manual checks', t => {
  const source = fs.readFileSync('js/app.js', 'utf8');
  const dom = new JSDOM('<body></body>', {runScripts: 'dangerously'});
  t.after(() => dom.window.close());
  const win = dom.window;
  win.escapeHtml = value => String(value ?? '');
  loadFunction(win, source, 'mindmapReportStatsHtml');

  const html = win.mindmapReportStatsHtml({
    statistics: {total: 2, passed: 2, manual_pending: 1, pass_rate: 100, defect_total: 0},
    quality: {result: '待人工确认'},
    release: {suggestion: '暂不建议发布'},
  });

  assert.match(html, /自动化总计/);
  assert.match(html, /<strong>1<\/strong><span>待人工确认<\/span>/);
});

test('mindmap report history controls explain and disable an empty history', t => {
  const source = fs.readFileSync('js/app.js', 'utf8');
  const dom = new JSDOM('<body></body>', {runScripts: 'dangerously'});
  t.after(() => dom.window.close());
  const win = dom.window;
  win.escapeHtml = value => String(value ?? '');
  loadFunction(win, source, 'mindmapReportMemoryRow');

  win.document.body.innerHTML = win.mindmapReportMemoryRow(
    '<input id="tester">',
    'tester-history',
    '<option value="">暂无历史记录</option>',
    "void 0",
    "void 0",
  );

  const select = win.document.getElementById('tester-history');
  const remove = win.document.querySelector('.mindmap-report-memory-row button');
  assert.equal(select.disabled, true);
  assert.equal(select.title, '暂无历史记录');
  assert.equal(remove.disabled, true);
  assert.equal(remove.title, '暂无可删除的历史记录');
});

test('generation supplement actions keep visible state labels in sync', t => {
  const source = fs.readFileSync('js/app.js', 'utf8');
  const dom = new JSDOM(`
    <body>
      <div data-supplement-card="1" data-state="pending">
        <span data-state-label>待处理</span>
        <button id="accept"></button>
      </div>
      <div data-supplement-card="1" data-state="pending">
        <span data-state-label>待处理</span>
      </div>
    </body>
  `, {runScripts: 'dangerously'});
  t.after(() => dom.window.close());
  const win = dom.window;
  loadFunction(win, source, 'setSupplementIssueState');
  loadFunction(win, source, 'acceptAllSupplementIssues');
  loadFunction(win, source, 'ignoreAllSupplementIssues');

  win.setSupplementIssueState(win.document.getElementById('accept'), 'accepted');
  assert.equal(win.document.querySelector('[data-state-label]').textContent, '已采纳');

  win.ignoreAllSupplementIssues();
  for (const card of win.document.querySelectorAll('[data-supplement-card]')) {
    assert.equal(card.dataset.state, 'ignored');
    assert.equal(card.querySelector('[data-state-label]').textContent, '本轮不考虑');
  }

  win.acceptAllSupplementIssues();
  for (const card of win.document.querySelectorAll('[data-supplement-card]')) {
    assert.equal(card.dataset.state, 'accepted');
    assert.equal(card.querySelector('[data-state-label]').textContent, '已采纳');
  }
});

test('refreshing the generic Trace Viewer keeps the full list after a trace is selected', async t => {
  const html = fs.readFileSync('trace-viewer.html', 'utf8');
  const script = html.match(/<script>([\s\S]*?)<\/script>\s*<\/body>/)[1]
    .replace(/\n\s*loadTraces\(\);\s*$/, '\n');
  const dom = new JSDOM(`
    <body>
      <div id="trace-list"></div>
      <section id="trace-detail"></section>
    </body>
  `, {runScripts: 'dangerously', url: 'http://platform.test/trace-viewer.html'});
  t.after(() => dom.window.close());
  const win = dom.window;
  const requests = [];
  const records = [
    {traceId: 'trace-1', title: '第一条', status: 'success', sourceType: 'job', nodes: []},
    {traceId: 'trace-2', title: '第二条', status: 'failed', sourceType: 'agent', nodes: []},
  ];
  Object.assign(win, {
    Headers: global.Headers,
    FormData: global.FormData,
    fetch: async url => {
      requests.push(String(url));
      const payload = String(url).includes('?id=') ? {ok: true, trace: records[0]} : {ok: true, traces: records};
      return {ok: true, status: 200, text: async () => JSON.stringify(payload)};
    },
  });
  win.eval(script);
  await win.loadTraces();
  assert.equal(win.document.querySelectorAll('.trace-item').length, 2);
  await win.loadTraces();
  assert.equal(win.document.querySelectorAll('.trace-item').length, 2);
  assert.deepEqual(requests, ['/api/debug/traces?limit=60', '/api/debug/traces?limit=60']);
});

test('the execution module picker keeps business modules clear and consolidates internal history', t => {
  const source = fs.readFileSync('js/execution.js', 'utf8');
  const dom = new JSDOM('<body></body>', {runScripts: 'dangerously'});
  t.after(() => dom.window.close());
  const win = dom.window;
  Object.assign(win, {
    modules: {
      '3D打印基线': ['printing.yaml'],
      '共享业务基线': ['shared.yml'],
      'AI_Agent_修复重跑_agent-123': ['repair.yaml'],
      'AI_Agent_草稿_agent-456': ['draft.yaml'],
      '空目录': [],
      '说明目录': ['README.md'],
    },
    executionModuleFilter: '',
    executionYamlSearch: '',
    escapeHtml: value => String(value),
  });
  loadFunction(win, source, 'isExecutionHistoryModule');
  loadFunction(win, source, 'executionYamlRows');
  loadFunction(win, source, 'executionModuleOptionsHtml');

  const options = win.executionModuleOptionsHtml();
  assert.match(options, /3D打印基线（1）/);
  assert.match(options, /共享业务基线（1）/);
  assert.match(options, /AI 生成\/修复历史（2 个模块 · 2 个 YAML）/);
  assert.doesNotMatch(options, /修复重跑_agent-123/);
  assert.doesNotMatch(options, /草稿_agent-456/);
  assert.doesNotMatch(options, /空目录|说明目录/);

  win.executionModuleFilter = '__AI_HISTORY__';
  assert.deepEqual(
    Array.from(win.executionYamlRows(), row => `${row.mod}/${row.file}`),
    ['AI_Agent_草稿_agent-456/draft.yaml', 'AI_Agent_修复重跑_agent-123/repair.yaml'],
  );
});

test('batch move requires an explicit target instead of defaulting to the source module', t => {
  const dom = new JSDOM(`
    <body>
      <div id="modal-batch-move"></div>
      <div id="batch-move-count"></div>
      <select id="batch-move-module"></select>
      <input id="batch-move-overwrite" type="checkbox" checked>
    </body>
  `, {runScripts: 'dangerously'});
  t.after(() => dom.window.close());
  const win = dom.window;
  Object.assign(win, {
    activeWorkflow: 'assets',
    currentModule: '3D打印基线',
    modules: {'3D打印基线': ['标牌打印.yaml'], '共享业务基线': ['分享.yaml']},
    selectedFiles: new Set(),
    selectedAssetRowsForCurrentFilters: () => [{mod: '3D打印基线', file: '标牌打印.yaml'}],
    escapeHtml: value => String(value),
    showToast: () => {},
  });
  const source = fs.readFileSync('js/execution.js', 'utf8');
  loadFunction(win, source, 'isExecutionHistoryModule');
  loadFunction(win, source, 'selectedFileItems');
  loadFunction(win, source, 'batchMoveModuleOptionsHtml');
  loadFunction(win, source, 'showBatchMove');

  win.showBatchMove();

  const target = win.document.getElementById('batch-move-module');
  assert.equal(target.value, '');
  assert.match(target.options[0].textContent, /请选择目标模块/);
  assert.equal(win.document.getElementById('batch-move-overwrite').checked, false);
});

test('batch move excludes its only source and keeps generated repair history out of targets', t => {
  const dom = new JSDOM(`
    <body>
      <div id="modal-batch-move"></div>
      <div id="batch-move-count"></div>
      <select id="batch-move-module"></select>
      <input id="batch-move-overwrite" type="checkbox">
    </body>
  `, {runScripts: 'dangerously'});
  t.after(() => dom.window.close());
  const win = dom.window;
  Object.assign(win, {
    activeWorkflow: 'assets',
    currentModule: 'AI测试',
    modules: {
      'AI测试': ['试卷夹.yaml'],
      '3D打印基线': ['打印.yaml'],
      '共享业务基线': ['分享.yaml'],
      '普通草稿': ['草稿.yaml'],
      'AI_Agent_修复重跑_agent-123': ['repair.yaml'],
      'cache': [],
    },
    selectedFiles: new Set(),
    selectedAssetRowsForCurrentFilters: () => [{mod: 'AI测试', file: '试卷夹.yaml'}],
    escapeHtml: value => String(value),
    showToast: () => {},
  });
  const source = fs.readFileSync('js/execution.js', 'utf8');
  loadFunction(win, source, 'isExecutionHistoryModule');
  loadFunction(win, source, 'selectedFileItems');
  loadFunction(win, source, 'batchMoveModuleOptionsHtml');
  loadFunction(win, source, 'showBatchMove');

  win.showBatchMove();

  const target = win.document.getElementById('batch-move-module');
  const labels = Array.from(target.options, option => option.textContent);
  assert.doesNotMatch(labels.join('\n'), /AI测试|AI_Agent_|cache/);
  assert.match(labels.join('\n'), /3D打印基线|共享业务基线|普通草稿/);
  assert.deepEqual(Array.from(target.querySelectorAll('optgroup'), group => group.label), [
    '业务与固定基线',
    '其他模块',
  ]);
});

test('destructive YAML actions identify their scope and explain that deletion is permanent', async t => {
  const dom = new JSDOM('<body></body>', {runScripts: 'dangerously'});
  t.after(() => dom.window.close());
  const win = dom.window;
  const confirmations = [];
  let requestCount = 0;
  Object.assign(win, {
    activeWorkflow: 'assets',
    currentModule: '3D打印基线',
    modules: {
      '3D打印基线': ['标牌打印.yaml'],
      '共享业务基线': ['分享.yaml'],
    },
    selectedFiles: new Set(),
    selectedAssetRowsForCurrentFilters: () => [
      {mod: '3D打印基线', file: '标牌打印.yaml'},
      {mod: '共享业务基线', file: '分享.yaml'},
    ],
    confirm: message => {
      confirmations.push(message);
      return false;
    },
    apiRequest: async () => {
      requestCount += 1;
    },
    showToast: () => {},
  });
  const source = fs.readFileSync('js/execution.js', 'utf8');
  loadFunction(win, source, 'deleteFile');
  loadFunction(win, source, 'deleteSelectedFiles');
  loadFunction(win, source, 'deleteCurrentModule');

  await win.deleteFile('3D打印基线', '标牌打印.yaml');
  await win.deleteSelectedFiles();
  await win.deleteCurrentModule();

  assert.equal(requestCount, 0);
  assert.equal(confirmations.length, 3);
  assert.match(confirmations[0], /永久删除/);
  assert.match(confirmations[0], /3D打印基线\/标牌打印\.yaml/);
  assert.match(confirmations[0], /无法.*恢复/);
  assert.match(confirmations[1], /永久删除 2 个 YAML 文件/);
  assert.match(confirmations[1], /3D打印基线、共享业务基线/);
  assert.match(confirmations[1], /无法.*恢复/);
  assert.match(confirmations[2], /永久删除模块「3D打印基线」/);
  assert.match(confirmations[2], /其中 1 个 YAML 文件/);
  assert.match(confirmations[2], /无法.*恢复/);
});

test('failed YAML deletion stays visible and batch deletion retains failed selections', async t => {
  const dom = new JSDOM('<body><div id="file-info"></div></body>', {runScripts: 'dangerously'});
  t.after(() => dom.window.close());
  const win = dom.window;
  const toasts = [];
  Object.assign(win, {
    activeWorkflow: 'assets',
    currentModule: '3D打印基线',
    currentFile: '仍在编辑.yaml',
    modules: {
      '3D打印基线': ['标牌打印.yaml'],
      '共享业务基线': ['分享.yaml'],
    },
    selectedFiles: new Set([
      '3D打印基线::标牌打印.yaml',
      '共享业务基线::分享.yaml',
    ]),
    selectedAssetRowsForCurrentFilters: () => [
      {mod: '3D打印基线', file: '标牌打印.yaml'},
      {mod: '共享业务基线', file: '分享.yaml'},
    ],
    confirm: () => true,
    apiRequest: async url => {
      if (url.includes(encodeURIComponent('标牌打印.yaml'))) throw new Error('服务暂不可用');
      return {ok: true};
    },
    fileKey: (mod, file) => `${mod}::${file}`,
    renderModules: () => {},
    showAssetsCenter: () => {},
    showToast: (message, type) => toasts.push({message, type}),
  });
  const source = fs.readFileSync('js/execution.js', 'utf8');
  loadFunction(win, source, 'deleteFile');
  loadFunction(win, source, 'deleteSelectedFiles');

  await win.deleteFile('3D打印基线', '标牌打印.yaml');
  assert.deepEqual(win.modules['3D打印基线'], ['标牌打印.yaml']);
  assert.equal(win.selectedFiles.has('3D打印基线::标牌打印.yaml'), true);
  assert.match(toasts.at(-1).message, /删除文件失败.*服务暂不可用/);

  await win.deleteSelectedFiles();
  assert.deepEqual(win.modules['3D打印基线'], ['标牌打印.yaml']);
  assert.deepEqual(win.modules['共享业务基线'], []);
  assert.deepEqual(Array.from(win.selectedFiles), ['3D打印基线::标牌打印.yaml']);
  assert.match(win.document.getElementById('file-info').textContent, /成功 1 个.*失败 1 个/);
  assert.deepEqual(toasts.at(-1), {message: '批量删除完成：成功 1 个，失败 1 个；失败项已保留', type: 'error'});
});

test('Trace actions use reliable viewer links and distinguish view from refresh', t => {
  const source = fs.readFileSync('js/execution.js', 'utf8');
  const dom = new JSDOM('<body></body>', {runScripts: 'dangerously'});
  t.after(() => dom.window.close());
  const win = dom.window;
  Object.assign(win, {
    executionActiveTab: 'trace',
    debugTraceLoading: false,
    debugTraceError: '',
    debugSnapshotError: '',
    debugTraceData: {traces: [{traceId: 'trace 1', title: '打印链路', status: 'success', sourceType: 'job'}]},
    debugSnapshotData: {snapshots: [{snapshotId: 'snapshot-1', sourceId: 'trace 1'}]},
    selectedTraceSnapshots: [],
    escapeHtml: value => String(value),
    jsArg: value => JSON.stringify(value),
    renderEmptyState: () => '',
  });
  loadFunction(win, source, 'renderExecutionTabTrace');
  win.document.body.innerHTML = win.renderExecutionTabTrace();

  const links = Array.from(win.document.querySelectorAll('a[target="_blank"]'));
  assert.deepEqual(links.map(link => link.textContent.trim()), ['打开 Viewer', '查看', '查看 Trace']);
  assert.equal(links[0].getAttribute('href'), '/trace-viewer.html');
  assert.equal(links[1].getAttribute('href'), '/trace-viewer.html?id=trace%201');
  assert.equal(links[2].getAttribute('rel'), 'noopener');
  assert.doesNotMatch(win.document.body.innerHTML, /window\.open/);

  loadFunction(win, source, 'executionTraceActionLabel');
  win.executionActiveTab = 'debug';
  assert.equal(win.executionTraceActionLabel(), '查看 Trace');
  win.executionActiveTab = 'trace';
  assert.equal(win.executionTraceActionLabel(), '刷新 Trace');
});

test('Sonic workspace keeps recent execution history concise and points batch users to assets', t => {
  const dom = new JSDOM('<body></body>', {runScripts: 'dangerously'});
  t.after(() => dom.window.close());
  const win = dom.window;
  Object.assign(win, {
    latestJobs: Array.from({length: 30}, (_, index) => ({
      job_id: `job-${index}`,
      module: '3D打印基线',
      file: `用例-${index}.yaml`,
      status: 'success',
      finished_at: '2026-09-02T12:00:00',
    })),
    currentFile: '',
    currentModule: '',
    isApkInstallJob: () => false,
    escapeHtml: value => String(value),
    jobStatusText: () => '成功',
    renderEmptyState: () => 'empty',
  });
  loadFunction(win, fs.readFileSync('js/execution.js', 'utf8'), 'renderExecutionTabSonic');

  win.document.body.innerHTML = win.renderExecutionTabSonic();

  assert.equal(win.document.querySelectorAll('tbody tr').length, 12);
  assert.match(win.document.body.textContent, /最近任务.*显示 12\/30 条/);
  assert.match(win.document.body.textContent, /批量同步请到.*用例资产.*勾选/);
});

test('closing an unsubmitted generate form restores the workflow that opened it', t => {
  const source = fs.readFileSync('js/app.js', 'utf8');
  const dom = new JSDOM('<body></body>', {runScripts: 'dangerously'});
  t.after(() => dom.window.close());
  const win = dom.window;
  const calls = [];
  Object.assign(win, {
    generateBusy: false,
    generateModalReturnWorkflow: 'system_config',
    activeWorkflow: 'generate',
    closeModal: id => calls.push(['close', id]),
    resetGenerateModal: () => calls.push(['reset']),
    activateWorkflow: key => calls.push(['workflow', key]),
    setGenerateStatus: () => {},
  });
  loadFunction(win, source, 'closeGenerateModal');
  win.closeGenerateModal();
  assert.deepEqual(calls, [
    ['close', 'modal-generate'],
    ['reset'],
    ['workflow', 'system_config'],
  ]);
  assert.equal(win.generateModalReturnWorkflow, '');
  assert.match(source, /generateModalReturnWorkflow = activeWorkflow !== 'generate' \? activeWorkflow : '';/);
});

test('rerun confirmation identifies the business task instead of only an internal id', t => {
  const source = fs.readFileSync('js/app.js', 'utf8');
  const dom = new JSDOM('<body></body>', {runScripts: 'dangerously'});
  t.after(() => dom.window.close());
  const win = dom.window;
  Object.assign(win, {
    latestJobs: [{
      job_id: 'sonic_123',
      target_task_name: '姓名牌打印',
      module: '3D打印基线',
      finished_at: '2026-09-01T09:27:08',
    }],
  });
  loadFunction(win, source, 'retryJobConfirmationText');
  const message = win.retryJobConfirmationText('sonic_123');
  assert.match(message, /姓名牌打印/);
  assert.match(message, /3D打印基线/);
  assert.match(message, /2026-09-01 09:27:08/);
  assert.match(message, /sonic_123/);
});

test('locating an older job keeps it in the short Runner history rail', t => {
  const source = fs.readFileSync('js/app.js', 'utf8');
  const dom = new JSDOM('<body></body>', {runScripts: 'dangerously'});
  t.after(() => dom.window.close());
  const win = dom.window;
  loadFunction(win, source, 'recentJobsWithFocus');
  const jobs = Array.from({length: 10}, (_, index) => ({job_id: `job-${index}`, status: 'success'}));
  const recent = jobs.slice(0, 6);
  const result = Array.from(win.recentJobsWithFocus(recent, jobs, new Set(), 'job-9', 6), job => job.job_id);
  assert.deepEqual(result, ['job-9', 'job-0', 'job-1', 'job-2', 'job-3', 'job-4']);
  assert.match(source, /executionActiveTab = 'debug'/);
  assert.match(source, /expandedJobs\.add\(jobId\)/);
});

test('Runner status distinguishes executable devices from historical binding rows', t => {
  const source = fs.readFileSync('js/execution.js', 'utf8');
  assert.match(source, />可执行设备</);
  assert.match(source, />设备绑定记录</);
  assert.match(source, /同一设备可能保留多个 Runner 绑定记录/);
});
