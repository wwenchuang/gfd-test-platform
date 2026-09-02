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

test('download YAML does not silently replace cleaned scripts with a JSON index', async () => {
  const source = fs.readFileSync('js/agent-status.js', 'utf8');
  const dom = new JSDOM('<body></body>', {runScripts: 'dangerously'});
  const win = dom.window;
  win.currentAgentRun = () => ({target: '历史任务', artifacts: {yamlRefs: [{module: '旧目录', file: '已清理.yaml'}]}});
  win.firstArtifactValue = () => '';
  win.agentYamlRefsFromArtifacts = artifacts => artifacts.yamlRefs;
  win.apiTextRequest = async () => { throw new Error('文件不存在'); };
  win.showToast = (message, type) => { win.lastToast = {message, type}; };
  loadFunction(win, source, 'downloadAgentYaml');

  const downloaded = await win.downloadAgentYaml();

  assert.equal(downloaded, false);
  assert.deepEqual(win.lastToast, {
    message: '历史 YAML 文件已被清理，无法下载真实脚本；仍可在 YAML 产物中查看文件索引和校验记录。',
    type: 'error',
  });
  dom.window.close();
});

test('Agent mindmap download uses the signed-in request path instead of opening an unauthenticated tab', async () => {
  const source = fs.readFileSync('js/agent-workbench.js', 'utf8');
  const dom = new JSDOM('<body></body>', {runScripts: 'dangerously'});
  const win = dom.window;
  win.currentAgentRun = () => ({target: '基础打印', artifacts: {caseSetId: 'case-set-1'}});
  win.agentMindmapInfo = () => ({caseSetId: 'case-set-1', url: '/api/cases/mindmap?case_set_id=case-set-1'});
  win.downloadMindmap = async (caseSetId, title) => {
    win.downloadArgs = {caseSetId, title};
    return true;
  };
  win.showToast = () => {};
  loadFunction(win, source, 'downloadAgentMindmap');

  const downloaded = await win.downloadAgentMindmap();

  assert.equal(downloaded, true);
  assert.deepEqual(win.downloadArgs, {caseSetId: 'case-set-1', title: '基础打印'});
  assert.doesNotMatch(win.downloadAgentMindmap.toString(), /window\.open/);
  dom.window.close();
});

test('all main-platform mindmap actions use the authenticated downloader', () => {
  const source = fs.readFileSync('js/app.js', 'utf8');
  assert.doesNotMatch(source, /href="\$\{mindmapDownloadUrl\(/);
  assert.match(source, /async function downloadMindmap\(/);
  assert.match(source, /downloadAuthenticatedFile\(/);
});

test('deleted mindmap guidance names the page that owns the refresh action', async () => {
  const source = fs.readFileSync('js/app.js', 'utf8');
  const dom = new JSDOM('<body></body>', {runScripts: 'dangerously'});
  const win = dom.window;
  win.downloadAuthenticatedFile = async () => { throw new Error('脑图文件已删除；请点击刷新脑图文件'); };
  win.showToast = (message, type) => { win.lastToast = {message, type}; };
  loadFunction(win, source, 'mindmapApiPath');
  loadFunction(win, source, 'mindmapDownloadErrorMessage');
  loadFunction(win, source, 'downloadMindmap');

  assert.equal(await win.downloadMindmap('case-set-1'), false);
  assert.deepEqual(win.lastToast, {
    message: '脑图文件已清理。请到“AI 生成用例 → 脑图中心”点击“刷新脑图文件”后再下载。',
    type: 'error',
  });
  dom.window.close();
});
