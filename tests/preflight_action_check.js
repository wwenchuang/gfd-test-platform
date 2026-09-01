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

test('a legacy Sonic warning provides the next action instead of a dead-end suggestion', t => {
  const source = fs.readFileSync('js/agent-status.js', 'utf8');
  const dom = new JSDOM('<body></body>', {runScripts: 'dangerously'});
  t.after(() => dom.window.close());
  loadFunction(dom.window, source, 'preflightActionHtml');
  const html = dom.window.preflightActionHtml({
    key: 'legacy',
    ok: false,
    action: '扫描并清理旧/重复脚本',
  });
  assert.match(html, /scanLegacySonicCases\('all'\)/);
  assert.match(html, /扫描并处理 37 条时将再次展示人工确认范围|扫描旧\/重复步骤/);
  assert.equal(dom.window.preflightActionHtml({key: 'legacy', ok: true, action: 'unused'}), '');
  assert.match(source, /preflightActionHtml\(item\)/);
});
