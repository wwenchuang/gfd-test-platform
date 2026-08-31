const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');
const { JSDOM } = require('../api-testing-ui/node_modules/jsdom');

function fixture(t, apps) {
  const dom = new JSDOM(fs.readFileSync('task-manager.html', 'utf8'));
  t.after(() => dom.window.close());
  const document = dom.window.document;
  document.getElementById('new-task-module').add(new dom.window.Option('校园模块', '校园模块'));
  document.getElementById('new-task-module').value = '校园模块';
  document.getElementById('new-task-name').value = '只读首页点检';
  const writes = [], opened = [], toasts = [];
  const context = vm.createContext({
    document, taskApps: apps, modules: { '校园模块': [] },
    requireUiEditPermission: () => true,
    moduleApp: mod => apps.find(app => app.modules.includes(mod)),
    apiRequest: async (url, options) => {
      assert.equal(url, '/file');
      writes.push(JSON.parse(options.body));
      return {ok: true};
    },
    renderModules() {}, closeModal() {},
    openFile: (mod, file) => opened.push([mod, file]),
    showToast: message => toasts.push(message),
  });
  const source = fs.readFileSync('js/utils.js', 'utf8');
  vm.runInContext(source.slice(source.indexOf('async function addTask()'), source.indexOf('// ===== UPLOAD =====')), context);
  return {run: () => vm.runInContext('addTask()', context), writes, opened, toasts};
}

test('manual YAML uses the selected module application from configuration in a Runner flow step', async t => {
  const f = fixture(t, [{package: 'com.fixture.school', enabled: true, modules: ['校园模块']}]);
  await f.run();
  assert.equal(f.writes.length, 1);
  assert.match(f.writes[0].content, /flow:\n      - launch: "com\.fixture\.school"\n/);
  assert.doesNotMatch(f.writes[0].content, /com\.kfb\.model/);
  assert.match(f.writes[0].content, /请描述需要验证的页面结果/);
  assert.deepEqual(f.opened, [['校园模块', '只读首页点检.yaml']]);
});

for (const [label, apps] of [
  ['unassigned', []],
  ['disabled', [{package: 'com.fixture.school', enabled: false, modules: ['校园模块']}]],
  ['historical', [{package: 'com.fixture.school', historical_only: true, modules: ['校园模块']}]],
]) test(`manual YAML cannot invent an application for an ${label} module`, async t => {
  const f = fixture(t, apps);
  await f.run();
  assert.deepEqual(f.writes, []);
  assert.deepEqual(f.opened, []);
  assert.match(f.toasts.join('\n'), /应用配置.*启用/);
});
