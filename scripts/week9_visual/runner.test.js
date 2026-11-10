'use strict';

const test=require('node:test');
const assert=require('node:assert/strict');
const {schedule,projectUsage,failure}=require('./run');
const {modelEndpoint, LIMITS}=require('./model');

test('all five workflows receive ten rounds per arm, alternating order',()=>{
  const slots=schedule('');
  assert.equal(slots.length,100);
  assert.equal(new Set(slots.map(row=>JSON.stringify(row))).size,100);
  assert.deepEqual(slots.slice(0,2).map(i=>i.arm),['control','ai']);
  assert.deepEqual(slots.slice(10,12).map(i=>i.arm),['ai','control']);
  for (const arm of ['ai','control']) {
    assert.equal(schedule(arm).length,50);
    const ids=new Set(schedule(arm).map(i=>i.scenario_id));
    assert.equal(ids.size,5);
    for (const id of ids) assert.equal(schedule(arm).filter(i=>i.scenario_id===id).length,10);
  }
});
test('model writes only to an exact supported provider endpoint',()=>{
  assert.equal(modelEndpoint('https://dashscope.aliyuncs.com/compatible-mode/v1/'),'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions');
  for (const base of ['http://dashscope.aliyuncs.com/compatible-mode/v1','https://dashscope.aliyuncs.com.evil.test/compatible-mode/v1',
    'https://dashscope.aliyuncs.com:9443/compatible-mode/v1','https://dashscope.aliyuncs.com/compatible-mode/v1?key=2',
    'https://user@dashscope.aliyuncs.com/compatible-mode/v1','https://localhost/v1']) assert.throws(()=>modelEndpoint(base));
});
test('unknown billing usage stays unknown and no raw provider errors are published',()=>{
  const row={steps:[{calls:[{tokens:23,status:'received'},{tokens:null,status:'provider_http_429'}]}]};
  projectUsage(row);assert.equal(row.tokens,null);
  assert.deepEqual(failure(new Error('leaked input'),row),{status:'blocked',reason:'provider_http_429'});
  row.steps[0].calls.pop();projectUsage(row);assert.equal(row.tokens,23);
  assert.equal(failure(new Error('contains a business title'),row).reason,'execution_error');
  assert.equal(failure(new Error('model_mislocated'),row).status,'failed');
});
test('maximum paid calls equal bounded actions in the fixed schedule',()=>{
  const actions={ui_case_selection:2,ui_case_revision:2,ui_start_handoff:2,ui_start_empty_guard:3,ui_catalog_paging:2};
  assert.equal(schedule('ai').reduce((a,i)=>a+actions[i.scenario_id],0),LIMITS.model_calls_total);
});
