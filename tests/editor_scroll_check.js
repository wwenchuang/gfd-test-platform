const assert = require('node:assert/strict');
const fs = require('node:fs');
const test = require('node:test');
const { JSDOM } = require('../api-testing-ui/node_modules/jsdom');

function loadFunction(window, name) {
  const source = fs.readFileSync('js/execution.js', 'utf8');
  const start = source.search(new RegExp(`^function ${name}\\(`, 'm'));
  assert.notEqual(start, -1, name);
  const rest = source.slice(start);
  const next = rest.slice(1).search(/^function [\w$]+\(/m);
  window.eval(next < 0 ? rest : rest.slice(0, next + 1));
}

test('wheel over the YAML panel scrolls the editor and line numbers together', () => {
  const dom = new JSDOM('<div id="wrap"><div id="line-nums"></div><textarea id="editor"></textarea></div>', {runScripts: 'dangerously'});
  const { window } = dom;
  loadFunction(window, 'enableEditorPanelScrolling');
  const wrap = window.document.getElementById('wrap');
  const editor = window.document.getElementById('editor');
  const lines = window.document.getElementById('line-nums');
  Object.defineProperty(editor, 'scrollHeight', {value: 1000});
  Object.defineProperty(editor, 'clientHeight', {value: 200});
  window.enableEditorPanelScrolling(wrap, editor, lines);
  wrap.dispatchEvent(new window.WheelEvent('wheel', {deltaY: 120, bubbles: true, cancelable: true}));
  assert.equal(editor.scrollTop, 120);
  assert.equal(lines.scrollTop, 120);
  editor.scrollTop = 240;
  editor.dispatchEvent(new window.Event('scroll'));
  assert.equal(lines.scrollTop, 240);
  dom.window.close();
});
