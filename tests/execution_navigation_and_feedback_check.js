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

test('the main platform defers every text search handler until Chinese IME composition finishes', t => {
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
    input.value = 'shoucang';
    input.dispatchEvent(new win.InputEvent('input', {
      bubbles: true,
      data: 'g',
      inputType: 'insertCompositionText',
      isComposing: true,
    }));
    assert.equal(inputCount, 0, `${id} must not filter or rerender during IME composition`);

    input.value = '收藏';
    input.dispatchEvent(new win.InputEvent('input', {bubbles: true, data: '收藏'}));
    assert.equal(inputCount, 1, `${id} must handle the committed Chinese value once`);
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
  assert.match(source, /clearAppInstallFeedback\(\); setExecutionTab\('debug'\)/);
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
