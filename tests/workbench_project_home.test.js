const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {JSDOM} = require('../api-testing-ui/node_modules/jsdom');
const tick = () => new Promise(resolve => setTimeout(resolve,0));
function mount(t,permissions,request) {
  const dom = new JSDOM('<main data-practice-workbench><details data-requirement-design></details></main>', {runScripts:'outside-only',url:'http://workbench.test'});
  t.after(()=>dom.window.close());
  const w = dom.window;
  w.hasPermission = key => permissions.includes(key);
  w.currentAccessProfile = {is_superuser:true};
  w.apiRequest = request;
  w.eval(fs.readFileSync('js/workbench-project.js','utf8'));
  const page = w.document.querySelector('main');
  w.WorkbenchProject.attach(page,()=>true);
  return {page,w};
}
test('a receipt shows the real issue ID and a scoped report link, and renders source titles as text', async t=>{
  const id='uib_'+'a'.repeat(32);
  const {page}=mount(t,['ui.view'], async () => ({items:[{title:'<img/onerror=alert(1)>',source:'s',requirement_id:'r',revision:'v',origin:'integration',status:'submitted',batches:[{platform:'iOS',ui_batch_id:id,defect_review:{status:'submitted',attempts:[{status:'submitted',issue_id:712},{status:'receipt_unknown',issue_id:999}]}}]}]}));
  await tick();
  assert.equal(page.querySelector('a').getAttribute('href'),'?ui_batch='+id);
  assert.match(page.textContent,/飞书项目缺陷 #712/);assert.doesNotMatch(page.textContent,/#999|undefined/);
  assert.equal(page.querySelector('img'),null);
  assert.equal(page.querySelector('form'),null);
});
test('read-only views do not submit or generate on load, administrators can hand an unmapped requirement to design',async t=>{
  const calls=[];
  const {page}=mount(t,['ui.view','ui.execute','platform.configure'],async(path,opts)=>{
    calls.push([path,opts]);return opts?.method==='POST'?{source_id:'f'.repeat(64)}:{items:[{source:'workbench',event_id:'e',app_id:'app',title:'套件专区',requirement_id:'k',revision:'1',status:'awaiting_mapping'}]};
  });
  await tick();
  assert.equal(calls.length,1);
  const panel=page.querySelector('[data-requirement-design]');panel.scrollIntoView=()=>{};
  let selected;
  panel.addEventListener('use-intake-source',e=>selected=e.detail);
  Array.from(page.querySelectorAll('button')).find(b=>b.textContent.includes('准备测试设计')).click();
  await tick();
  assert.equal(selected,'f'.repeat(64));assert.equal(calls[1][1].body.event_id,'e');
});
test('application viewers can read the generated development cases without operating a device',async t=>{
  const {page}=mount(t,['ui.view'],async()=>({items:[{title:'套件专区',origin:'integration',requirement_id:'31',
    revision:'v1',status:'design_completed',design_cases:[{title:'套件详情',precondition:'已登录',steps:['打开套件'],
    expected:'展示详情',plan:{evidence:'套件详情原图'}}]}]}));
  await tick();
  assert.match(page.textContent,/查看测试用例（1 条）/);
  assert.match(page.textContent,/套件详情原图/);
  assert.doesNotMatch(page.textContent,/打开需求设计详情/);
});
