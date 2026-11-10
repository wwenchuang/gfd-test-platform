// Isolated fixture: validates plumbing only; never a production business result.
const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const {spawn, spawnSync} = require('node:child_process');
const root = path.resolve(__dirname, '..');

test('browser runs three independent fixed catalog checks without write requests', {timeout: 120000}, async () => {
  const secret = 'EXAMPLE_TOKEN_0123456789abcdefghijklmnopqrs';
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'week5-browser-test-'));
  let html=fs.readFileSync(path.join(root,'task-manager.html'),'utf8');const aliases=new Map();
  for(const name of ['ui-test-home','ui-simple-entry']){
    const source=path.join(root,'js',name+'.js'),digest=crypto.createHash('sha256').update(fs.readFileSync(source)).digest('hex').slice(0,16);
    const hashed=`/js/${name}.${digest}.js`;aliases.set(hashed,source);html=html.replace(`js/${name}.js?`,hashed.slice(1)+'?');
  }
  const observed=[];
  const server = http.createServer((req, res) => {
    const u = new URL(req.url, 'http://127.0.0.1:8088');
    observed.push([req.method,u.pathname,u.search]);
    if (u.pathname.startsWith('/api/')) {
      assert.equal(req.headers.authorization, `Bearer ${secret}`);
      res.setHeader('Content-Type','application/json');
      if (u.pathname === '/api/auth/me') res.end(JSON.stringify({ok:true,user:'fixture',profile:{user_id:'fixture-id',username:'fixture',status:'active',permissions:['ui.view'],scope:{ui_apps:[]}}}));
      else if (u.pathname === '/api/ui-executions/availability') res.end(JSON.stringify({ok:true,enabled:true,evidence_available:true}));
      else if (u.pathname === '/api/ui-executions/catalog') {
        const app='com.example.app', item={case_id:'fixture-1',task_name:'浏览作品',app_id:app,module:'作品',execution_eligible:true,revision:'a'.repeat(64)};
        const query=u.searchParams.get('query')||'', selected=u.searchParams.get('app_id')||'', empty=query.startsWith('__web_check_missing_');
        const items=empty?[]:[item];res.end(JSON.stringify({ok:true,total:items.length,groups:selected?[]:[{app_id:app,name:'示例应用',items}],items:selected?items:[],apps:[{app_id:app,name:'示例应用'}],modules:['作品'],next_cursor:null}));
      }
      else {res.statusCode=404; res.end('{}');}
      return;
    }
    if(u.pathname==='/task-manager.html'){res.setHeader('Content-Type','text/html');res.end(html);return;}
    const file = aliases.get(u.pathname)||path.join(root, u.pathname);
    if (!file.startsWith(root + path.sep) || !fs.existsSync(file)) {res.statusCode=404;res.end();return;}
    const type = file.endsWith('.js')?'application/javascript':file.endsWith('.css')?'text/css':file.endsWith('.html')?'text/html':'image/png';
    res.setHeader('Content-Type',type);fs.createReadStream(file).pipe(res);
  });
  await new Promise((resolve, reject) => server.once('error', reject).listen(8088, '127.0.0.1', resolve));
  try {
    for(const scenario of ['ui_catalog_count','ui_catalog_filter','ui_catalog_empty']){
    const child = spawn(process.execPath, [path.join(root,'task_server/fixtures/week5_web/worker.js')], {env:{...process.env,WEEK5_ARTIFACTS:dir},stdio:['pipe','pipe','pipe']});
    let stdout='', stderr='';child.stdout.on('data', x=>stdout+=x);child.stderr.on('data', x=>stderr+=x);
    child.stdin.end(JSON.stringify({token:secret,userId:'fixture-id',scenario}));
    const code = await new Promise(resolve=>child.on('close', resolve));
    assert.equal(code,0,stderr);
    assert.equal(JSON.parse(stdout).scenario,scenario);assert.equal(JSON.parse(stdout).status,'passed',stdout);
    if(scenario==='ui_catalog_filter')assert.deepEqual(JSON.parse(stdout).asset,{app_id:'com.example.app',case_id:'fixture-1',revision:'a'.repeat(64)});
    assert.equal(fs.existsSync(path.join(dir,'screenshot.png')),true);
    const trace=path.join(dir,'trace.zip');assert.equal(fs.existsSync(trace),true);
    const unpacked=spawnSync('/usr/bin/unzip',['-p',trace],{maxBuffer:80*1024*1024});
    assert.equal(unpacked.status,0);
    assert.equal(unpacked.stdout.includes(Buffer.from(secret)),false);
    assert.equal(unpacked.stdout.includes(Buffer.from(secret).toString('base64')),false);
    assert.equal((stdout+stderr).includes(secret),false);
    }
    assert.ok(observed.some(([m,p,q])=>p==='/api/ui-executions/catalog'&&q.includes('app_id=com.example.app')&&q.includes('query=%E6%B5%8F%E8%A7%88%E4%BD%9C%E5%93%81')));
    assert.ok(observed.some(([m,p,q])=>p==='/api/ui-executions/catalog'&&q.includes('__web_check_missing_')));
    for(const asset of aliases.keys())assert.ok(observed.some(([m,p])=>p===asset),asset);
    assert.ok(observed.every(([method])=>method==='GET'));
  } finally {await new Promise(resolve=>server.close(resolve));fs.rmSync(dir,{recursive:true,force:true});}
});
