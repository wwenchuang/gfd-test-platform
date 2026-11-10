const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {allowRequest, responseContainsSecret, classifyIncomplete, recordRouteError} = require('../task_server/fixtures/week5_web/policy');

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

test('the 35 observed HTML assets and the candidate hashed entry are on the fixed list', () => {
  const origin='http://127.0.0.1:8088';
  const assets=fs.readFileSync(path.join(__dirname,'fixtures/week5_html_assets_20261110.txt'),'utf8').trim().split('\n');
  assert.equal(assets.length,35);
  for (const asset of assets) assert.equal(allowRequest(origin,'GET',`${origin}/${asset}`),'static',asset);
  const next=assets.find(x=>x.startsWith('js/ui-simple-entry.')).replace('1514fe455649371c','d6214cd9458c31ef');
  assert.equal(allowRequest(origin,'GET',`${origin}/${next}`),'static');
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
