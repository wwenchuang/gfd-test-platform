const { test } = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { spawn } = require('node:child_process');

test('anonymous text check reports exit status and preserves screenshots and traces', async () => {
  let posts = 0;
  const server = http.createServer((req, res) => {
    if (req.method !== 'GET') posts++;
    res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
    res.end('<div id="login-screen"><h1>功夫豆测试平台</h1></div>');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'week1-ui-smoke-'));
  try {
    for (const [mode, expectedExit] of [['positive', 0], ['intentional-failure', 1]]) {
      const child = spawn(process.execPath, [path.join(__dirname, 'ui_smoke.js'), mode], {
        env: { ...process.env, W01_UI_URL: `http://127.0.0.1:${server.address().port}/task-manager.html`, W01_UI_ARTIFACTS: dir },
        stdio: ['ignore', 'pipe', 'pipe'],
      });
      let output = '';
      child.stdout.on('data', chunk => { output += chunk; });
      child.stderr.on('data', chunk => { output += chunk; });
      const code = await new Promise(resolve => child.on('close', resolve));
      assert.equal(code, expectedExit, output);
      const result = JSON.parse(output.trim());
      assert.equal(result.actual, '功夫豆测试平台');
      assert.equal(result.passed, expectedExit === 0);
      assert.ok((await fs.stat(path.join(dir, mode, 'screenshot.png'))).size > 0);
      assert.ok((await fs.stat(path.join(dir, mode, 'trace.zip'))).size > 0);
    }
    assert.equal(posts, 0);
  } finally {
    await new Promise(resolve => server.close(resolve));
    await fs.rm(dir, { recursive: true, force: true });
  }
});
