const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const {allowRequest, responseContainsSecret, classifyIncomplete, recordRouteError, catalogTarget} = require('../task_server/fixtures/week5_web/policy');

test('filter target comes from an authorized visible group and a real named case', () => {
 const item={app_id:'real.app',case_id:'case1',task_name:'真实用例',execution_eligible:true,revision:'a'.repeat(64)};
 const data={apps:[{app_id:'real.app'}],groups:[{app_id:'real.app',items:[item]}]};
 assert.deepEqual(catalogTarget(data),{app:'real.app',caseId:'case1',name:'真实用例',revision:'a'.repeat(64)});
 assert.equal(catalogTarget({...data,apps:[]}),null);
 assert.equal(catalogTarget({...data,groups:[{app_id:'other',items:[item]}]}),null);
 assert.equal(catalogTarget({...data,groups:[{app_id:'real.app',items:[{...item,task_name:'a'.repeat(201)}]}]}),null);
 assert.equal(catalogTarget({...data,groups:[{app_id:'real.app',items:[{...item,revision:''}]}]}),null);
 assert.equal(catalogTarget({apps:[],groups:[]}),null);
});

test('only the fixed same-origin static page and approved read-only APIs pass', () => {
  const origin = 'http://127.0.0.1:8088';
  assert.equal(allowRequest(origin, 'GET', `${origin}/task-manager.html`), 'static');
  assert.equal(allowRequest(origin, 'GET', `${origin}/js/auth.js?v=abc-123`), 'static');
  assert.equal(allowRequest(origin, 'GET', `${origin}/js/app.js?v=${'a'.repeat(300)}`), 'static');
  assert.equal(allowRequest(origin, 'GET', `${origin}/js/ui-simple-entry.1514fe455649371c.js?v=20261110-contract-root-v1`), 'static');
  assert.equal(allowRequest(origin, 'GET', `${origin}/js/ui-simple-entry.d6214cd9458c31ef.js?v=20261110-contract-root-v1`), 'static');
  assert.equal(allowRequest(origin, 'GET', `${origin}/css/ui-case-draft.98c46b5734c85379.css?v=20261109-workbench-style-v1`), 'static');
  assert.equal(allowRequest(origin, 'GET', `${origin}/api/auth/me`), 'api');
  assert.equal(allowRequest(origin, 'GET', `${origin}/api/ui-executions/availability`), 'api');
  assert.equal(allowRequest(origin, 'GET', `${origin}/api/ui-executions/catalog?app_id=&module=&query=&scope=published&limit=20`), 'api');
  for (const [method, url] of [
    ['POST', `${origin}/api/auth/me`], ['GET', `${origin}/api/auth/logout`],
    ['GET', `${origin}/api/auth/me?x=1`], ['GET', `${origin}/js/unknown.js`],
    ['GET', 'http://localhost:8088/api/auth/me'], ['GET', `${origin}/task-manager.html?x=1`],
    ['GET', `${origin}/js/%61uth.js`], ['GET', 'ws://127.0.0.1:8088/api/auth/me'],
    ['GET', `${origin}/js/ui-simple-entry.0000000000000000.js?v=20261110-contract-root-v1`],
    ['GET', `${origin}/css/ui-case-draft.98c46b5734c85370.css?v=20261109-workbench-style-v1`],
    ['GET', `${origin}/api/ui-executions/catalog?app_id=other&module=&query=&scope=published&limit=20`],
  ]) assert.equal(allowRequest(origin, method, url), false, `${method} ${url}`);
});

test('catalog filter only allows exact server-discovered queries and never writes', () => {
  const origin='http://127.0.0.1:8088';
  const allowed=new Set(['?app_id=com.example.app&module=&query=&scope=published&limit=20','?app_id=com.example.app&module=&query=%E7%94%A8%E4%BE%8B&scope=published&limit=20']);
  for(const query of allowed)assert.equal(allowRequest(origin,'GET',`${origin}/api/ui-executions/catalog${query}`,allowed),'api');
  assert.equal(allowRequest(origin,'POST',`${origin}/api/ui-executions/catalog${[...allowed][0]}`,allowed),false);
  assert.equal(allowRequest(origin,'GET',`${origin}/api/ui-executions/catalog?app_id=other&module=&query=&scope=published&limit=20`,allowed),false);
  assert.equal(allowRequest(origin,'GET',`${origin}/api/ui-executions/catalog${[...allowed][0]}`,new Set()),false);
});

test('the 35 observed HTML assets and the candidate hashed entry are on the fixed list', () => {
  const origin='http://127.0.0.1:8088';
  const assets=fs.readFileSync(path.join(__dirname,'fixtures/week5_html_assets_20261110.txt'),'utf8').trim().split('\n');
  assert.equal(assets.length,35);
  for (const asset of assets) assert.equal(allowRequest(origin,'GET',`${origin}/${asset}`),'static',asset);
  const next=assets.find(x=>x.startsWith('js/ui-simple-entry.')).replace('1514fe455649371c','d6214cd9458c31ef');
  assert.equal(allowRequest(origin,'GET',`${origin}/${next}`),'static');
});

test('all actual candidate HTML assets including both exact content-hashed scripts are allowed', () => {
  const origin='http://127.0.0.1:8088',root=path.resolve(__dirname,'..');let html=fs.readFileSync(path.join(root,'task-manager.html'),'utf8');
  for(const name of ['ui-simple-entry','ui-test-home']){
    const digest=crypto.createHash('sha256').update(fs.readFileSync(path.join(root,'js',name+'.js'))).digest('hex').slice(0,16);
    const raw=`js/${name}.js?`, hashed=`js/${name}.${digest}.js?`;assert.ok(html.includes(raw));html=html.replace(raw,hashed);
  }
  const urls=[...html.matchAll(/(?:src|href)="((?:js|css|assets)\/[^"#]+)"/g)].map(match=>match[1]);
  assert.ok(urls.length>=35);
  for(const url of urls)assert.equal(allowRequest(origin,'GET',`${origin}/${url}`),'static',url);
});

test('authentication failure wins over network failure regardless of callback order', () => {
  for (const order of [['authentication','environment'],['environment','authentication']]) {
    const state={routeError:false};
    for (const kind of order) recordRouteError(state,kind);
    assert.equal(state.routeError,'authentication');
  }
});

test('a missing UI after successful identity and catalog reads is a UI assertion failure', () => {
  const state={auth:{ok:true,profile:{user_id:'u'}}, availability:{ok:true,enabled:true},catalog:{ok:true,total:3},routeError:false};
  assert.deepEqual(classifyIncomplete(state,'u'), {status:'failed',error_kind:'business'});
  assert.deepEqual(classifyIncomplete({...state,routeError:'authentication'},'u'), {status:'blocked',error_kind:'authentication'});
  assert.deepEqual(classifyIncomplete({...state,catalog:null},'u'), {status:'blocked',error_kind:'environment'});
});

test('response gate detects the token and standard encoded variants before browser delivery', () => {
  const secret = 'EXAMPLE_TOKEN_1234567890';
  for (const value of [secret, encodeURIComponent(secret), Buffer.from(secret).toString('base64')]) {
    assert.equal(responseContainsSecret(Buffer.from(`prefix ${value} suffix`), secret), true);
  }
  assert.equal(responseContainsSecret(Buffer.from('{"ok":true}'), secret), false);
});

test('device GET requires an exact separately admitted application query',()=>{
  const origin='http://127.0.0.1:8088', path=origin+'/api/ui-executions/devices';
  const apps=new Set(['?app_id=com.example.app']);
  assert.equal(allowRequest(origin,'GET',path+'?app_id=com.example.app',new Set(),apps),'api');
  for(const [method,suffix] of [['POST','?app_id=com.example.app'],['GET',''],['GET','?app_id=com.example.app&all=true'],['GET','?app_id=com.other']]){
    assert.equal(allowRequest(origin,method,path+suffix,new Set(),apps),false);
  }
  assert.equal(allowRequest(origin,'GET',path+'?app_id=com.example.app',apps),false);
});

test('paging exposes only a cursor bound to the last returned case and never changes W05 projection', () => {
  const {browserData}=require('../task_server/fixtures/week5_web/workflows');
  const api='/api/ui-executions/catalog';
  const data={ok:true,items:[{case_id:'one'}],apps:[],groups:[]};
  const cursor=Buffer.from('["0123456789abcdef",-1712345678901234567,"one"]').toString('base64url');
  assert.equal(browserData(api,{...data,next_cursor:cursor},'app','ui_catalog_paging').next_cursor,cursor);
  assert.equal(browserData(api,{...data,next_cursor:cursor},'app','ui_case_selection').next_cursor,undefined);
  for (const raw of ['private', '["0123456789abcdef",-1,"another"]','["0123456789abcdef",-1,"one","private"]']){
    assert.throws(()=>browserData(api,{...data,next_cursor:Buffer.from(raw).toString('base64url')},'app','ui_catalog_paging'),/invalid_cursor|JSON|Unexpected/);
  }
});
