const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const {readLoopback} = require('../task_server/fixtures/week5_web/proxy');

test('proxy sends private credential only to loopback and returns a bounded clean response', async () => {
  const secret = 'EXAMPLE_TOKEN_0123456789abcdefghijklmno';
  const seen = [];
  const server = http.createServer((req, res) => {
    seen.push(req.headers.authorization);
    res.setHeader('Content-Type','application/json');
    res.end('{"ok":true}');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  try {
    const result = await readLoopback('/api/auth/me', secret, server.address().port);
    assert.equal(result.status, 200);
    assert.equal(result.body.toString(), '{"ok":true}');
    assert.deepEqual(seen, [`Bearer ${secret}`]);
  } finally { await new Promise(resolve => server.close(resolve)); }
});

test('proxy rejects redirects, compression and reflected credentials', async () => {
  const secret = 'EXAMPLE_TOKEN_0123456789abcdefghijklmno';
  let mode = 'redirect';
  const server = http.createServer((req, res) => {
    if (mode === 'redirect') { res.writeHead(302, {Location: 'http://other.invalid/'}); res.end(); }
    else if (mode === 'gzip') { res.writeHead(200, {'Content-Encoding':'gzip'}); res.end('anything'); }
    else { res.writeHead(200); res.end(secret); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  try {
    for (mode of ['redirect', 'gzip', 'reflect']) {
      await assert.rejects(readLoopback('/api/auth/me', secret, server.address().port));
    }
    await assert.rejects(readLoopback('/api/anything-else', secret, server.address().port));
  } finally { await new Promise(resolve => server.close(resolve)); }
});
