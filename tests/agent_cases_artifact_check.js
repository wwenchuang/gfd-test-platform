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

test('generated Agent cases open as a readable workflow summary instead of raw JSON', () => {
  const source = fs.readFileSync('js/agent-workbench.js', 'utf8');
  const dom = new JSDOM('<body></body>', {runScripts: 'dangerously'});
  const win = dom.window;
  win.escapeHtml = value => String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;');
  for (const name of [
    'agentInfoGrid',
    'agentReadableList',
    'agentGeneratedCaseGroups',
    'agentCaseTextList',
    'agentCaseIdentity',
    'agentCaseExecutionLabel',
    'renderAgentCasesArtifact',
  ]) loadFunction(win, source, name);

  const run = {
    artifacts: {
      generatedCases: {
        title: '基础打印新增百度网盘入口',
        module: 'AI_Agent_草稿',
        analysis: {
          business_goals: ['三个基础打印入口都展示百度网盘'],
          entry_points: ['首页 -> 文档打印', '首页 -> 照片打印'],
        },
        cases: [
          {
            case_id: 'TC-001',
            title: '文档打印入口展示',
            priority: 'P1',
            smoke: true,
            requirementRefs: ['REQ-001'],
            preconditions: ['已登录'],
            steps: ['进入文档打印', '查看导入入口'],
            assertions: ['百度网盘入口可见'],
          },
          {
            case_id: 'TC-002',
            title: '照片打印入口展示',
            priority: 'P1',
            steps: ['进入照片打印'],
            expected_result: '百度网盘入口可见',
          },
        ],
        manual_cases: [{case_id: 'MC-001', title: '真实账号授权确认', reason: '需要人工登录第三方账号'}],
      },
      generatedCaseGroups: {
        executable_cases: [
          {case_id: 'TC-001'},
          {module: 'AI_Agent_草稿', file: '01-文档打印入口展示.yaml', executionLevel: 'executable'},
        ],
        needs_review_cases: [{case_id: 'TC-002', reason: '路径证据不足'}],
        draft_cases: [],
        manual_cases: [{case_id: 'MC-001'}],
      },
      matchedCases: [{id: 'BASE-001'}],
    },
  };

  const html = win.renderAgentCasesArtifact(run);
  const container = win.document.createElement('div');
  container.innerHTML = html;
  const visibleText = container.textContent;
  assert.match(html, /用例总览/);
  assert.match(html, /自动化设计<\/span>\s*<strong>2<\/strong>/);
  assert.match(html, /可执行 YAML<\/span>\s*<strong>2<\/strong>/);
  assert.match(html, /需确认 YAML<\/span>\s*<strong>1<\/strong>/);
  assert.match(html, /人工\/一次性设计<\/span>\s*<strong>1<\/strong>/);
  assert.match(html, /三个基础打印入口都展示百度网盘/);
  assert.match(html, /文档打印入口展示/);
  assert.match(html, /照片打印入口展示/);
  assert.match(html, /真实账号授权确认/);
  assert.equal(container.querySelectorAll('.agent-case-card').length, 3);
  assert.doesNotMatch(container.querySelector('.agent-case-list').textContent, /01-文档打印入口展示\.yaml/);
  const reviewCard = [...container.querySelectorAll('.agent-case-card')]
    .find(item => item.textContent.includes('照片打印入口展示'));
  assert.match(reviewCard.textContent, /路径证据不足/);
  assert.match(visibleText, /第 1 步：进入文档打印/);
  assert.match(visibleText, /预期：百度网盘入口可见/);
  assert.match(html, /冒烟/);
  assert.match(html, /<details class="agent-readable-panel agent-raw-json">\s*<summary>查看原始 JSON<\/summary>/);
  assert.equal(container.querySelector('.agent-raw-json').hasAttribute('open'), false);
  assert.doesNotMatch(html, /^<pre class="agent-artifact-pre">/);
  assert.match(source, /if \(tab === 'cases'\) return renderAgentCasesArtifact\(run\);/);
  assert.match(source, /\['plan', 'cases', 'quality'/);
  dom.window.close();
});
