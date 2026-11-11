const {test} = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const {chromium} = require('playwright');

const first='80000000-0000-4000-b000-000000000001',other='80000000-0000-4000-b000-000000000002';
const suite={suite_id:'catalog-failure-drill',environment:'platform',title:'目录故障演练',runner:'node',drill:true};
const rows=[first,other].map(id=>({...suite,run_id:id,request_id:id,status:'failed',error_kind:'drill_assertion',created_at:1800000000,cases:[],evidence:{}}));
const html=`<main id="app"><div id="editor-area"></div></main><script>
  window.currentAccessProfile={user_id:'colleague',scope:{ui_apps:'*'}};
  window.activeWorkflow='ai_practice';window.sessionToken=()=>'fixture';
  window.authHeaders=(extra)=>({...extra,Authorization:'Bearer fixture'});
  window.hasPermission=p=>p==='ui.view';window.canAccessWorkflow=()=>false;
  window.toggleLibrary=()=>{};window.nativeFetch=fetch.bind(window);
  </script><script src="/js/ui-practice-results.js"></script><script src="/js/ui-simple-entry.js"></script><script>showAiPractice()</script>`;
const insufficient={value:null,numerator:0,denominator:0,note:'没有关联的重试样本'};
const report={window_limit:80,history_count:6,active_runs:0,drill_runs:2,metrics:{
  first_pass_rate:{value:75,numerator:3,denominator:4,note:'首轮业务执行'},final_pass_rate:{value:75,numerator:3,denominator:4,note:'业务执行终态'},
  retry_recovery_rate:insufficient,flake_rate:insufficient,average_recovery_ms:null,counts:{passed:3,failed:1,skipped:0,errors:0,total:4},duration:{sample_count:4,total_ms:80,mean_ms:20}
},gold_candidates:[],methodology:['仅计算已结束的正式用例。'],incidents:[],runs:rows.map(row=>({...row,is_drill:true}))};
function diagnosis(id){
  const facts={'run.status':'failed','observed.total':'36','observed.expected_total':'0'};
  const bundle={run_id:id,suite_id:suite.suite_id,is_drill:true,facts,started_at:1800000000,finished_at:1800000001,
    coverage:{source:'structured_case_result',attempts_observed:2,attempts_included:1,truncated:true,raw_logs_included:false,source_logs_truncated:null}};
  return {run_id:id,bundle,draft:{run_id:id,is_drill:true,title:'目录数量核对差异',facts:['页面与目录核对已执行'],expected:['预期 0 条'],
    actual:['实测 36 条'],to_confirm:['确认过滤条件'],refs:['observed.total']},
    review:id===first?{status:'ready',run_id:id,model:'测试模型',requested_model:'测试模型',duration_ms:500,provider_calls:1,billing_status:'unverified',usage:{total_tokens:120},classification:'candidate',category:'script',
      observations:[{text:'页面显示 36 条',evidence_ids:['observed.total']}],
      candidate_cause:{text:'预期数量可能来自演练设置',evidence_ids:['observed.expected_total']},
      verification_action:'回看演练断言'}:null,
    evaluation:{status:'pending_human',human_labeled:0,gold_target:20,accuracy:null,fixtures:[]}};
}

async function setup(t,diagnose=async id=>({json:{ok:true,diagnosis:diagnosis(id)}}), generate=null){
  const browser=await chromium.launch();t.after(()=>browser.close());
  const page=await browser.newPage();page.setDefaultTimeout(3000);
  const reads=[], writes=[];let authorized=true;
  await page.route('http://127.0.0.1/**',async route=>{
    const req=route.request(),url=new URL(req.url()),api=url.pathname.split('/').pop();
    if(url.pathname==='/') return route.fulfill({contentType:'text/html; charset=utf-8',body:generate?html.replace("p==='ui.view'","['ui.view','ui.execute'].includes(p)"):html});
    if(url.pathname.startsWith('/js/')) return route.fulfill({path:path.join(__dirname,'..',url.pathname)});
    assert.equal(req.headers().authorization,'Bearer fixture');
    let json={ok:true};
    if(!authorized) return route.fulfill({status:403,json:{ok:false,code:'unauthorized'}});
    if(api==='call'){
      const {name,arguments:args}=req.postDataJSON();writes.push(name);
      assert.ok(['list_suites','get_run'].includes(name));
      json.result=name==='list_suites'?{suites:[suite]}:rows.find(r=>r.run_id===args.run_id);
    }else if(api==='runs') json.runs=rows;
    else if(api==='acceptance') json.report=null;
    else if(api==='report'){assert.equal(req.method(),'GET');reads.push(api);json.report=report;}
    else if(api==='diagnosis'){
      const id=url.searchParams.get('run_id');assert.equal(req.method(),'GET');reads.push(id);
      return route.fulfill(await diagnose(id));
    }else if(api==='diagnose'&&generate){
      assert.equal(req.method(),'POST');
      return route.fulfill(await generate(req.postDataJSON()));
    }else throw Error(url.pathname);
    return route.fulfill({json});
  });
  await page.goto('http://127.0.0.1/');await page.getByText('工具执行',{exact:true}).click();
  await page.getByText('运行编号 '+first,{exact:true}).waitFor();
  return {page,reads,writes,revoke:()=>{authorized=false;}};
}

test('report and diagnosis are on-demand, present the samples, evidence and draft, and clear when access changes',async t=>{
  const {page,reads,writes,revoke}=await setup(t);
  assert.deepEqual(reads,[]);
  await page.getByText('运行报告',{exact:true}).click();
  await page.getByText(/首次通过率.*75%.*3 \/ 4/).waitFor();
  await page.getByText(/重试恢复率.*样本不足/).waitFor();
  await page.getByText('失败分析',{exact:true}).click();
  await page.getByText('预期数量可能来自演练设置',{exact:true}).waitFor();
  await page.getByText(/人工已标注 0 \/ 20/).waitFor();
  await page.getByText(/执行事实（/).click();
  await page.getByText(/本摘要展示 1 \/ 2 次执行/).waitFor();
  await page.getByText(/原始日志未采集，源日志截断情况未知/).waitFor();
  await page.getByText('模型调用记录',{exact:true}).click();
  await page.getByText(/供应商调用 1 次.*500 ms/).waitFor();
  await page.getByText('缺陷草稿',{exact:true}).click();
  await page.getByText('预期 0 条',{exact:true}).waitFor();
  assert.deepEqual(reads,['report',first]);
  assert.ok(writes.every(name=>['list_suites','get_run'].includes(name)));
  revoke();await page.getByRole('button',{name:'刷新记录'}).click();
  await page.getByText(/权限已变化/).waitFor();
  assert.equal(await page.getByText('预期数量可能来自演练设置',{exact:true}).count(),0);
  assert.equal(await page.getByText(/首次通过率.*75%/).count(),0);
});

test('a delayed diagnosis cannot overwrite the next selection and missing model evidence remains pending',async t=>{
  let release,entered;
  const pending=new Promise(resolve=>{entered=resolve;});
  const blocked=new Promise(resolve=>{release=resolve;});
  t.after(()=>release());
  const {page}=await setup(t,async id=>{
    if(id===first){entered();await blocked;}
    return {json:{ok:true,diagnosis:diagnosis(id)}};
  });
  await page.getByText('失败分析',{exact:true}).click();
  await pending;
  await page.getByText('我的运行记录',{exact:true}).click();
  await page.getByRole('button',{name:new RegExp(other)}).click();
  await page.getByText('运行编号 '+other,{exact:true}).waitFor();
  await page.getByText('失败分析',{exact:true}).click();
  await page.getByText('尚无本次运行的模型建议。',{exact:true}).waitFor();
  assert.equal(await page.getByRole('button',{name:'用千问分析本次失败'}).count(),0);
  release();
  assert.equal(await page.getByText('预期数量可能来自演练设置',{exact:true}).count(),0);
});

test('report links open the exact drill; unknown classification keeps its observations and does not invent a candidate',async t=>{
  const {page}=await setup(t,async id=>{
    const record=diagnosis(id);
    record.review={...diagnosis(first).review,run_id:id,classification:'unknown',category:'unknown',
      candidate_cause:{text:'证据不足，根因待核对。',evidence_ids:[]}};
    return {json:{ok:true,diagnosis:record}};
  });
  await page.getByText('运行报告',{exact:true}).click();
  await page.getByText(/待分析运行（2）/).click();
  await page.getByRole('button',{name:new RegExp('演练.*'+other)}).click();
  await page.getByText('运行编号 '+other,{exact:true}).waitFor();
  await page.getByText(/归因待确认/).waitFor();
  await page.getByText('页面显示 36 条',{exact:true}).waitFor();
  assert.equal(await page.getByText('候选原因',{exact:true}).count(),0);
});

test('unknown evidence IDs cannot display a proposed cause while the protected facts and draft remain available',async t=>{
  const {page}=await setup(t,async id=>{
    const record=diagnosis(id);
    record.review.candidate_cause.evidence_ids=['unrelated.run'];
    return {json:{ok:true,diagnosis:record}};
  });
  await page.getByText('失败分析',{exact:true}).click();
  await page.getByText('模型建议的证据引用无法核对。',{exact:true}).waitFor();
  assert.equal(await page.getByText('预期数量可能来自演练设置',{exact:true}).count(),0);
  await page.getByText('缺陷草稿',{exact:true}).click();
  await page.getByText('实测 36 条',{exact:true}).waitFor();
});

test('authorized generation requires one explicit click and re-reads the selected run after the provider finishes',async t=>{
  let entered,release;
  const active=new Promise(resolve=>{entered=resolve;});
  const blocked=new Promise(resolve=>{release=resolve;});
  t.after(()=>release());
  const sent=[];let ready=false;
  const {page,reads}=await setup(t,async id=>{
    const data=diagnosis(id);if(!ready)data.review=null;
    return {json:{ok:true,diagnosis:data}};
  },async body=>{sent.push(body);entered();await blocked;ready=true;return {json:{ok:true,review:diagnosis(first).review}};});
  await page.getByText('失败分析',{exact:true}).click();
  const button=page.getByRole('button',{name:'用千问分析本次失败'});
  await button.waitFor();assert.equal(sent.length,0);
  await button.evaluate(el=>{el.click();el.click();});
  await active;
  assert.equal(await button.isDisabled(),true);
  await page.getByText('失败分析',{exact:true}).click();
  await page.getByText('失败分析',{exact:true}).click();
  await button.waitFor();assert.equal(await button.isDisabled(),true);
  release();
  await page.getByText('预期数量可能来自演练设置',{exact:true}).waitFor();
  assert.deepEqual(sent,[{run_id:first}]);assert.equal(await button.count(),0);
  assert.ok(reads.filter(r=>r===first).length>=2);
});

test('a finished generation never inserts a prior identity result',async t=>{
  let entered,release;
  const active=new Promise(resolve=>{entered=resolve;});
  const blocked=new Promise(resolve=>{release=resolve;});
  t.after(()=>release());
  const {page}=await setup(t,async id=>({json:{ok:true,diagnosis:{...diagnosis(id),review:null}}}),async ()=>{
    entered();await blocked;return {json:{ok:true,review:diagnosis(first).review}};
  });
  await page.getByText('失败分析',{exact:true}).click();
  await page.getByRole('button',{name:'用千问分析本次失败'}).click();
  await active;await page.evaluate(()=>{window.currentAccessProfile={user_id:'other'};});
  await page.getByText('工具执行',{exact:true}).waitFor({state:'detached'});release();
  assert.equal(await page.getByText('预期数量可能来自演练设置',{exact:true}).count(),0);
});

test('a busy provider is re-read without an automatic retry and a recorded unavailable result hides the paid action',async t=>{
  let posts=0,state=null;
  const {page,reads}=await setup(t,async id=>({json:{ok:true,diagnosis:{...diagnosis(id),review:state}}}),async ()=>{
    posts++;return {status:429,json:{ok:false,code:'model_busy'}};
  });
  await page.getByText('失败分析',{exact:true}).click();
  await page.getByRole('button',{name:'用千问分析本次失败'}).click();
  await page.getByText('当前调用额度已用尽或服务繁忙。',{exact:true}).waitFor();
  assert.deepEqual(reads,[first,first]);assert.equal(posts,1);
  state={status:'unavailable',run_id:first,requested_model:'test',model:null,provider_calls:1,duration_ms:180,
    usage:{},billing_status:'unverified'};
  await page.getByText('失败分析',{exact:true}).click();
  await page.getByText('失败分析',{exact:true}).click();
  await page.getByText('本次模型调用已记录，未取得可用建议。',{exact:true}).waitFor();
  assert.equal(await page.getByRole('button',{name:'用千问分析本次失败'}).count(),0);
  assert.equal(posts,1);
});
