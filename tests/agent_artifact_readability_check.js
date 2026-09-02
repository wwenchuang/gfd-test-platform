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

function makeWindow() {
  const dom = new JSDOM('<body></body>', {runScripts: 'dangerously'});
  const win = dom.window;
  win.escapeHtml = value => String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;');
  win.jsArg = value => JSON.stringify(String(value ?? ''));
  win.agentStepLabel = value => ({PREPARE_SOURCE: '资料准备', GENERATE_YAML: '生成 YAML'}[value] || value);
  win.agentStatusText = value => ({SUCCESS: '成功', FAILED: '失败', RUNNING: '执行中'}[String(value).toUpperCase()] || value);
  return {dom, win};
}

test('YAML artifact presents files and validation status before technical JSON', () => {
  const source = fs.readFileSync('js/agent-workbench.js', 'utf8');
  const {dom, win} = makeWindow();
  for (const name of ['agentInfoGrid', 'agentReadableList', 'agentCaseTextList', 'agentYamlDisplayRows', 'renderAgentYamlArtifact']) {
    loadFunction(win, source, name);
  }
  const artifacts = {
    yamlRefs: [
      {module: 'AI_Agent_草稿', file: '01-入口.yaml', executionLevel: 'executable', score: 100, smoke: true},
      {module: 'AI_Agent_草稿', file: '02-跳转.yaml', executionLevel: 'needs_review', issues: ['路径证据不足']},
    ],
    generationPipeline: {yamlFiles: ['01-入口.yaml']},
    yamlValidation: {passedCount: 1, failedCount: 1},
  };
  assert.equal(win.agentYamlDisplayRows(artifacts).length, 2);
  const html = win.renderAgentYamlArtifact({artifacts});
  assert.match(html, /YAML 文件/);
  assert.match(html, /01-入口\.yaml/);
  assert.match(html, /评分 100/);
  assert.match(html, /冒烟/);
  assert.match(html, /路径证据不足/);
  assert.match(html, /编辑 YAML/);
  assert.match(html, /<details class="agent-readable-panel agent-raw-json">/);
  assert.doesNotMatch(html, /^<pre class="agent-artifact-pre">/);
  assert.match(source, /if \(tab === 'yaml'\) return renderAgentYamlArtifact\(run\);/);
  dom.window.close();
});

test('Agent logs present a status timeline and keep tool payloads collapsed', () => {
  const source = fs.readFileSync('js/agent-workbench.js', 'utf8');
  const {dom, win} = makeWindow();
  for (const name of ['agentInfoGrid', 'agentCaseTextList', 'agentLogDurationText', 'renderAgentLogArtifact']) {
    loadFunction(win, source, name);
  }
  const html = win.renderAgentLogArtifact({steps: [
    {step: 'PREPARE_SOURCE', status: 'SUCCESS', durationMs: 1200, summary: '资料已整理', toolCalls: [{toolName: 'prepare_source'}]},
    {step: 'GENERATE_YAML', status: 'FAILED', startedAt: '2026-09-02T10:00:00Z', endedAt: '2026-09-02T10:00:04Z', summary: '模型返回为空'},
  ]});
  assert.match(html, /阶段轨迹/);
  assert.match(html, /已结束<\/span>\s*<strong>2<\/strong>/);
  assert.match(html, /失败<\/span>\s*<strong>1<\/strong>/);
  assert.match(html, /资料准备/);
  assert.match(html, /1\.2s/);
  assert.match(html, /模型返回为空/);
  assert.match(html, /工具调用 1/);
  assert.match(html, /查看完整阶段 JSON/);
  assert.match(source, /if \(tab === 'logs'\) return renderAgentLogArtifact\(run\);/);
  dom.window.close();
});

test('repair artifact separates current applicable output from duplicate attempts', () => {
  const source = fs.readFileSync('js/agent-workbench.js', 'utf8');
  const {dom, win} = makeWindow();
  for (const name of ['agentInfoGrid', 'agentReadableList', 'agentRepairAttemptGroups', 'renderRepairDraftDetail']) {
    loadFunction(win, source, name);
  }
  const artifacts = {
    repairSummary: {
      failedTaskCount: 1,
      repairTargetCount: 1,
      draftCount: 1,
      aiUsedCount: 0,
      items: [{targetTaskName: '照片打印入口', blockedReason: 'repair_patch_application_failed', failureReason: '路径不稳定'}],
    },
    repairDraft: {repairSource: 'diagnosis_only'},
    repairDrafts: [
      {targetTaskName: '照片打印入口', analysis: '第一次仅诊断'},
      {targetTaskName: '照片打印入口', fixedYaml: 'android: {}'},
    ],
  };
  const html = win.renderRepairDraftDetail({}, artifacts);
  const container = win.document.createElement('div');
  container.innerHTML = html;
  assert.equal(container.querySelectorAll('.agent-repair-attempt').length, 1);
  assert.match(container.textContent, /当前可应用 YAML\s*0/);
  assert.match(container.textContent, /2 次尝试/);
  assert.match(container.textContent, /生成过 1 份 YAML 候选，但未通过当前门禁/);
  assert.match(container.textContent, /当前没有通过门禁的可应用 YAML/);
  dom.window.close();
});

test('optional bug artifact explains why script failures do not create product defects', () => {
  const source = fs.readFileSync('js/agent-workbench.js', 'utf8');
  const {dom, win} = makeWindow();
  win.agentArtifactDefinition = () => ({title: '缺陷草稿'});
  win.agentArtifactStateLabel = () => '按需';
  loadFunction(win, source, 'renderAgentArtifactEmpty');
  const html = win.renderAgentArtifactEmpty('bug', 'optional', {
    artifacts: {failureAnalysis: {failureType: 'SCRIPT_ISSUE'}},
  });
  assert.match(html, /脚本问题/);
  assert.match(html, /未确认产品问题/);
  assert.match(source, /renderAgentArtifactEmpty\(tab, state, run\)/);
  dom.window.close();
});

test('HTML escaping preserves zero counts in result metrics', () => {
  const source = fs.readFileSync('js/app.js', 'utf8');
  const {dom, win} = makeWindow();
  loadFunction(win, source, 'escapeHtml');
  assert.equal(win.escapeHtml(0), '0');
  assert.equal(win.escapeHtml(false), 'false');
  assert.equal(win.escapeHtml(null), '');
  dom.window.close();
});

test('Agent stage statuses use Chinese labels in readable logs', () => {
  const source = fs.readFileSync('js/agent-status.js', 'utf8');
  const {dom, win} = makeWindow();
  loadFunction(win, source, 'agentStatusText');
  assert.equal(win.agentStatusText('SUCCESS'), '成功');
  assert.equal(win.agentStatusText('PARTIAL_FAILED'), '部分失败');
  assert.equal(win.agentStatusText('SKIPPED'), '跳过');
  dom.window.close();
});
