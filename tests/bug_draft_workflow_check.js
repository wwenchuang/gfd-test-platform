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

test('an unlinked or unknown-app defect draft cannot be sent to a fallback group', t => {
  const source = fs.readFileSync('js/agent-status.js', 'utf8');
  const dom = new JSDOM('<body></body>', {runScripts: 'dangerously'});
  t.after(() => dom.window.close());
  const win = dom.window;
  Object.assign(win, {
    taskApps: [{package: 'com.kfb.model', name: '智小白3D'}],
    taskAppFeishuReady: () => true,
    taskAppFeishuLabel: () => '飞书：默认群',
  });
  loadFunction(win, source, 'feishuDraftSubmitReadiness');

  assert.deepEqual(
    JSON.parse(JSON.stringify(win.feishuDraftSubmitReadiness({title: '未关联草稿'}))),
    {enabled: false, reason: '未关联平台应用，不能确定通知目标', target: ''},
  );
  assert.match(win.feishuDraftSubmitReadiness({appPackage: 'com.unknown'}).reason, /不在平台应用配置/);
  assert.deepEqual(
    JSON.parse(JSON.stringify(win.feishuDraftSubmitReadiness({appPackage: 'com.kfb.model'}))),
    {enabled: true, reason: '', target: '智小白3D · 默认群'},
  );
  assert.match(source, /确认并发送飞书[^<]*<\/button>/);
  assert.match(source, /submitState\.enabled \? '' : 'disabled'/);
  assert.match(source, /暂不可发送/);
});

test('defect generation passes only observed environment facts and marks missing values', t => {
  const source = fs.readFileSync('js/ai-repair.js', 'utf8');
  const dom = new JSDOM('<body></body>', {runScripts: 'dangerously'});
  t.after(() => dom.window.close());
  const win = dom.window;
  loadFunction(win, source, 'bugDraftEnvironmentFacts');
  const facts = win.bugDraftEnvironmentFacts({
    app_name: '智小白3D',
    app_package: 'com.kfb.model',
    device_name: 'HUAWEI P40 Pro',
    runner_id: 'win-runner-01',
    midscene_version: '1.10.7',
    report_url: 'http://platform.test/reports/real.html',
  });
  assert.match(facts, /应用：智小白3D（com\.kfb\.model）/);
  assert.match(facts, /设备：HUAWEI P40 Pro/);
  assert.match(facts, /Runner：win-runner-01/);
  assert.match(facts, /Midscene 版本：1\.10\.7/);
  assert.match(facts, /Sonic 版本：未提供/);
  assert.doesNotMatch(facts, /2\.8\.3|3\.5\.1|iPhone 14|test\.gongfudou/);
});

test('defect prompt forbids invented environment versions, devices, and addresses', () => {
  const prompt = fs.readFileSync('ai-gateway/prompts/generate-bug-v1.txt', 'utf8');
  assert.match(prompt, /只使用输入中明确提供的事实/);
  assert.match(prompt, /缺失信息写“未提供”/);
  assert.match(prompt, /禁止编造版本号、设备型号、账号权限、URL/);
});
