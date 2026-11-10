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

test('browser exercises read-only workflows and isolates deliberate failures and missing prerequisites', {timeout: 120000}, async () => {
  const secret = 'EXAMPLE_TOKEN_0123456789abcdefghijklmnopqrs';
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'week5-browser-test-'));
  let html=fs.readFileSync(path.join(root,'task-manager.html'),'utf8');const aliases=new Map();
  for(const name of ['ui-test-home','ui-simple-entry']){
    const source=path.join(root,'js',name+'.js'),digest=crypto.createHash('sha256').update(fs.readFileSync(source)).digest('hex').slice(0,16);
    const hashed=`/js/${name}.${digest}.js`;aliases.set(hashed,source);html=html.replace(`js/${name}.js?`,hashed.slice(1)+'?');
  }
  const flows=['ui_case_selection','ui_start_handoff','ui_start_switch','ui_start_unready','ui_case_revision','ui_catalog_paging','ui_catalog_module','ui_case_hidden_selection','ui_start_empty_guard'];
  const observed=[];
  let fault = null,versionReads=0;
  const server = http.createServer((req, res) => {
    const u = new URL(req.url, 'http://127.0.0.1:8088');
    observed.push([req.method,u.pathname,u.search]);
    if (u.pathname.startsWith('/api/')) {
      assert.equal(req.headers.authorization, `Bearer ${secret}`);
      res.setHeader('Content-Type','application/json');
      if(fault==='api_error'&&u.pathname==='/api/ui-executions/catalog'){res.statusCode=500;res.end(JSON.stringify({detail:'PRIVATE-W05-RAW-ERROR'}));return;}
      if (u.pathname === '/api/auth/me') res.end(JSON.stringify({ok:true,user:'fixture',profile:{user_id:'fixture-id',username:'fixture',status:'active',permissions:fault==='view_only'?['ui.view']:['ui.view','ui.execute'],display_name:'PRIVATE-W05-ACCOUNT',scope:{ui_apps:[]}}}));
      else if (u.pathname === '/api/ui-executions/availability') res.end(JSON.stringify({ok:true,enabled:true,evidence_available:true,debug:'PRIVATE-W05-AVAILABILITY'}));
      else if(u.pathname==='/api/ui-executions/devices'){
        assert.ok(['com.example.app','com.example.second'].includes(u.searchParams.get('app_id')));
        res.end(JSON.stringify({ok:true,devices:[{name:'PRIVATE-W05-PHONE',target:{platform:'android',device_id:'PRIVATE-W05-SERIAL',runner_id:'PRIVATE-W05-RUNNER'},app_id:u.searchParams.get('app_id'),connection_status:fault==='ready'?'ready':'offline',snapshot_url:'/PRIVATE-W05-PICTURE'}]}));
      }
      else if (u.pathname === '/api/ui-executions/catalog') {
        const app='com.example.app', item={case_id:'fixture-1',task_name:'浏览作品',private_notes:'PRIVATE-W05-CASE-NOTES',app_id:app,module:'作品',execution_eligible:true,revision:'a'.repeat(64)};
        const query=u.searchParams.get('query')||'', selected=u.searchParams.get('app_id')||'', empty=query.startsWith('__web_check_missing_');
        if(fault==='revised'&&selected&&++versionReads>1)item.revision='b'.repeat(64);
        let all=[item];
        if(fault==='two_pages')all=Array.from({length:23},(_,i)=>({...item,case_id:'fixture-'+(i+1),task_name:'作品 '+(i+1)}));
        if(empty||selected==='com.example.second')all=[];
        const cursor=Buffer.from(JSON.stringify(['0123456789abcdef',-100,'fixture-20'])).toString('base64url');
        const after=u.searchParams.get('cursor');
        if(after)assert.equal(after,cursor);
        const items=after?all.slice(20):all.slice(0,20);
        res.end(JSON.stringify({ok:true,total:all.length,groups:selected?[]:[{app_id:app,name:'示例应用',total:all.length,items:all.slice(0,3)}],items:selected?items:[],apps:[{app_id:app,name:'示例应用',notification_channel:{target_label:'PRIVATE-W05-CHANNEL'}},{app_id:'com.example.second',name:'另一应用'}],modules:['作品'],next_cursor:selected&&!after&&all.length>20?cursor:null}));
      }
      else {res.statusCode=404; res.end('{}');}
      return;
    }
    if(u.pathname==='/task-manager.html'){res.setHeader('Content-Type','text/html');res.end(html);return;}
    const file = aliases.get(u.pathname)||path.join(root, u.pathname);
    if (!file.startsWith(root + path.sep) || !fs.existsSync(file)) {res.statusCode=404;res.end();return;}
    const type = file.endsWith('.js')?'application/javascript':file.endsWith('.css')?'text/css':file.endsWith('.html')?'text/html':'image/png';
    res.setHeader('Content-Type',type);
    if(fault==='stale_render'&&file.endsWith('/ui-test-home.js')){
      res.end(fs.readFileSync(file,'utf8').replace('function renderCases(s,data){','function renderCases(s,data){if(s.selected.size && s.options.mode==="library")return;'));return;
    }
    if(fault==='broken_remove'&&file.endsWith('/ui-test-home.js')){
      res.end(fs.readFileSync(file,'utf8').replace('s.selected.delete(id);s.node.querySelectorAll','s.selected.has(id);s.node.querySelectorAll'));return;
    }
    if(fault==='changed_count'&&file.endsWith('/ui-test-home.js')){
      res.end(fs.readFileSync(file,'utf8').replace('data.total||0','999'));return;
    }
    if(fault==='broken_required_case'&&file.endsWith('/ui-test-home.js')){
      res.end(fs.readFileSync(file,'utf8').replace('勾选至少一条已发布基线。','已准备好'));return;
    }
    fs.createReadStream(file).pipe(res);
  });
  await new Promise((resolve, reject) => server.once('error', reject).listen(8088, '127.0.0.1', resolve));
  try {
    const drill='ui_catalog_failure_drill';
    for(const [scenario,fixtureFault] of [['ui_catalog_count'],['ui_catalog_filter'],['ui_catalog_empty'],[drill],[drill,'changed_count'],[drill,'wrong_owner'],...flows.map(key=>[key]),['ui_catalog_paging','two_pages'],['ui_start_empty_guard','broken_required_case'],['ui_case_selection','view_only'],['ui_start_unready','ready'],['ui_case_selection','broken_remove'],['ui_case_revision','revised'],['ui_case_revision','stale_render'],['ui_catalog_count','api_error']]){
    fault=fixtureFault;versionReads=0;
    const startIndex=observed.length;
    for (const name of ['screenshot.png','trace.zip']) fs.rmSync(path.join(dir,name),{force:true});
    const child = spawn(process.execPath, [path.join(root,'task_server/fixtures/week5_web/worker.js')], {env:{...process.env,WEEK5_ARTIFACTS:dir},stdio:['pipe','pipe','pipe']});
    let stdout='', stderr='';child.stdout.on('data', x=>stdout+=x);child.stderr.on('data', x=>stderr+=x);
    child.stdin.end(JSON.stringify({token:secret,userId:fault==='wrong_owner'?'other-id':'fixture-id',scenario}));
    const code = await new Promise(resolve=>child.on('close', resolve));
    assert.equal(code,0,stderr);
    const result=JSON.parse(stdout);
    assert.equal(result.scenario,scenario);
    if(fault==='api_error'){
      assert.equal(result.status,'blocked');assert.equal(result.error_kind,'environment');
      for(const name of ['screenshot.png','trace.zip'])assert.equal(fs.existsSync(path.join(dir,name)),false);
      continue;
    }
    if(['view_only','ready','revised'].includes(fault)){
      assert.equal(result.status,'blocked',stdout);
      assert.equal(result.detail,({ready:'ready_device_present',view_only:'execute_permission_required',revised:'case_version_changed'})[fault]);
      continue;
    }
    if(scenario==='ui_catalog_paging'&&(!fault||fault==='two_pages')){
      assert.equal(result.pages_checked,fault==='two_pages'?2:1,stdout);
      if(fault==='two_pages'){
        const reads=observed.slice(startIndex).filter(([,p,q])=>p.endsWith('/catalog')&&q.includes('app_id=com.example.app'));
        assert.equal(reads.length,3);
        assert.ok(reads[1][2].includes('&cursor='));
      }
    }
    if(flows.includes(scenario)&&(!fault||fault==='two_pages')){
      assert.equal(result.status,'passed',stdout);
      if(scenario==='ui_start_unready')assert.equal(result.device_count,1);
      if(scenario==='ui_start_switch')assert.equal(result.switched_app_id,'com.example.second');
      if(scenario==='ui_case_revision')assert.equal(observed.slice(startIndex).filter(([,p,q])=>p==='/api/ui-executions/catalog'&&q.includes('app_id=com.example.app')).length,2);
      assert.deepEqual(result.asset,{app_id:'com.example.app',case_id:'fixture-1',revision:'a'.repeat(64)});
    }
    if(fault&&fault!=='two_pages'){
      assert.equal(result.status,fault==='wrong_owner'?'blocked':'failed',stdout);
      assert.equal(result.error_kind,fault==='wrong_owner'?'authentication':'business',stdout);
      assert.equal(result.expected_total,undefined);
    }else if(scenario===drill){
      assert.equal(result.status,'failed',stdout);assert.equal(result.error_kind,'drill_assertion');
      assert.equal(result.expected_total,0);assert.equal(result.total,1);
    }else assert.equal(result.status,'passed',stdout);
    if(scenario==='ui_catalog_filter')assert.deepEqual(JSON.parse(stdout).asset,{app_id:'com.example.app',case_id:'fixture-1',revision:'a'.repeat(64)});
    if(fault==='wrong_owner'){
      assert.equal(fs.existsSync(path.join(dir,'screenshot.png')),false);
      assert.equal(fs.existsSync(path.join(dir,'trace.zip')),false);
      continue;
    }
    const screenshot=fs.readFileSync(path.join(dir,'screenshot.png'));
    assert.equal(screenshot.subarray(1,4).toString(),'PNG');assert.ok(screenshot.readUInt32BE(16)>0);
    const trace=path.join(dir,'trace.zip');assert.equal(fs.existsSync(trace),true);
    const unpacked=spawnSync('/usr/bin/unzip',['-p',trace],{maxBuffer:80*1024*1024});
    assert.equal(unpacked.status,0);
    if(scenario===drill&&!fault){
      const events=spawnSync('/usr/bin/unzip',['-p',trace,'trace.trace'],{encoding:'utf8',maxBuffer:80*1024*1024});
      assert.equal(events.status,0,events.error?.message||events.stderr);
      const failedActions=events.stdout.trim().split('\n').map(line=>JSON.parse(line)).filter(event=>event.type==='after'&&event.error);
      assert.ok(failedActions.some(event=>event.error?.message?.includes('catalog_drill_mismatch')),JSON.stringify(failedActions).slice(0,2800));
    }
    const traced=unpackTraceEvents(trace);
    for(const marker of ['PRIVATE-W05-AVAILABILITY','PRIVATE-W05-CASE-NOTES','PRIVATE-W05-RAW-ERROR','PRIVATE-W05-ACCOUNT','PRIVATE-W05-SERIAL','PRIVATE-W05-CHANNEL','PRIVATE-W05-PICTURE']){
      assert.equal(unpacked.stdout.includes(Buffer.from(marker)),false,marker);
      for(const event of traced)if(event.params?.body)assert.equal(Buffer.from(event.params.body,'base64').includes(Buffer.from(marker)),false,marker);
    }
    assert.equal(unpacked.stdout.includes(Buffer.from(secret)),false);
    assert.equal(unpacked.stdout.includes(Buffer.from(secret).toString('base64')),false);
    assert.equal((stdout+stderr).includes(secret),false);
    assert.ok(traced.some(event=>event.type==='before'&&event.title==='Screenshot'&&event.params?.selector==='[data-ui-mode="library"]:visible,[data-ui-mode="start"]:visible'),'screenshot must be restricted to the workflow pane');
    assert.equal(traced.some(event=>event.type==='screencast-frame'),false,'trace must not capture other page regions');

    }
    assert.ok(observed.some(([m,p,q])=>p==='/api/ui-executions/catalog'&&q.includes('app_id=com.example.app')&&q.includes('query=%E6%B5%8F%E8%A7%88%E4%BD%9C%E5%93%81')));
    assert.ok(observed.some(([m,p,q])=>p==='/api/ui-executions/catalog'&&q.includes('__web_check_missing_')));
    for(const asset of aliases.keys())assert.ok(observed.some(([m,p])=>p===asset),asset);
    assert.ok(observed.every(([method])=>method==='GET'));
  } finally {await new Promise(resolve=>server.close(resolve));fs.rmSync(dir,{recursive:true,force:true});}
});

function unpackTraceEvents(trace){
  const content=spawnSync('/usr/bin/unzip',['-p',trace,'trace.trace'],{encoding:'utf8',maxBuffer:80*1024*1024});
  return content.stdout.trim().split('\n').map(line=>JSON.parse(line));
}
