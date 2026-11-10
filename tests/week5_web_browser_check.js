// Isolated fixture: validates plumbing only; never a production business result.
const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawn, spawnSync} = require('node:child_process');
const root = path.resolve(__dirname, '..');

test('browser uses placeholder only and asserts the actual UI catalog rendering', {timeout: 45000}, async () => {
  const secret = 'EXAMPLE_TOKEN_0123456789abcdefghijklmnopqrs';
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'week5-browser-test-'));
  const server = http.createServer((req, res) => {
    const u = new URL(req.url, 'http://127.0.0.1:8088');
    if (u.pathname.startsWith('/api/')) {
      assert.equal(req.headers.authorization, `Bearer ${secret}`);
      res.setHeader('Content-Type','application/json');
      if (u.pathname === '/api/auth/me') res.end(JSON.stringify({ok:true,user:'fixture',profile:{user_id:'fixture-id',username:'fixture',status:'active',permissions:['ui.view'],scope:{ui_apps:[]}}}));
      else if (u.pathname === '/api/ui-executions/availability') res.end(JSON.stringify({ok:true,enabled:true,evidence_available:true}));
      else if (u.pathname === '/api/ui-executions/catalog') res.end(JSON.stringify({ok:true,total:0,groups:[],apps:[],modules:[]}));
      else {res.statusCode=404; res.end('{}');}
      return;
    }
    const file = path.join(root, u.pathname === '/task-manager.html' ? 'task-manager.html' : u.pathname);
    if (!file.startsWith(root + path.sep) || !fs.existsSync(file)) {res.statusCode=404;res.end();return;}
    const type = file.endsWith('.js')?'application/javascript':file.endsWith('.css')?'text/css':file.endsWith('.html')?'text/html':'image/png';
    res.setHeader('Content-Type',type);fs.createReadStream(file).pipe(res);
  });
  await new Promise((resolve, reject) => server.once('error', reject).listen(8088, '127.0.0.1', resolve));
  try {
    const child = spawn(process.execPath, [path.join(root,'task_server/fixtures/week5_web/worker.js')], {env:{...process.env,WEEK5_ARTIFACTS:dir},stdio:['pipe','pipe','pipe']});
    let stdout='', stderr='';child.stdout.on('data', x=>stdout+=x);child.stderr.on('data', x=>stderr+=x);
    child.stdin.end(JSON.stringify({token:secret,userId:'fixture-id'}));
    const code = await new Promise(resolve=>child.on('close', resolve));
    assert.equal(code,0,stderr);
    assert.equal(JSON.parse(stdout).status,'passed',stdout);
    assert.equal(fs.existsSync(path.join(dir,'screenshot.png')),true);
    const trace=path.join(dir,'trace.zip');assert.equal(fs.existsSync(trace),true);
    const unpacked=spawnSync('/usr/bin/unzip',['-p',trace],{maxBuffer:80*1024*1024});
    assert.equal(unpacked.status,0);
    assert.equal(unpacked.stdout.includes(Buffer.from(secret)),false);
    assert.equal(unpacked.stdout.includes(Buffer.from(secret).toString('base64')),false);
    assert.equal((stdout+stderr).includes(secret),false);
  } finally {await new Promise(resolve=>server.close(resolve));fs.rmSync(dir,{recursive:true,force:true});}
});
