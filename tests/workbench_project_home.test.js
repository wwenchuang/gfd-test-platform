const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {JSDOM} = require('../api-testing-ui/node_modules/jsdom');
const tick = () => new Promise(resolve => setTimeout(resolve,0));
function mount(t,permissions,request,search='') {
  const dom = new JSDOM('<main data-practice-workbench><details data-requirement-design></details></main>', {runScripts:'outside-only',url:'http://workbench.test/'+search});
  t.after(()=>dom.window.close());
  const w = dom.window;
  w.HTMLElement.prototype.scrollIntoView = ()=>{};
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
const visualId='c'.repeat(64);
function openVisual(t,supported,previous,investigate) {
  return mount(t,['ui.view','platform.configure'],async (path,options)=>{
    if (path.endsWith('/investigate')) {
      assert.equal(options.method,'POST');
      assert.equal(options.body.id,visualId);
      assert.deepEqual(Object.keys(options.body),['id']);
      return investigate();
    }
    if (path.includes('/visual/result')) return {
      id:visualId,title:'拼图',environment:'正式',step:{},board:{id:'1:1',name:'入口'},
      size:[1,1],findings:{},api_investigation_supported:supported,api_investigation:previous
    };
    return {sources:[],devices:[],items:[]};
  },'?visual_compare='+visualId);
}
const getButton=(page,text)=>[...page.querySelectorAll('button')].find(b=>b.textContent===text);
const confirmed={
  status:'confirmed',conclusion:'真机与接口返回名称一致',design_name:'太空冒险',device_name:'太空探险',
  api_name:'太空探险',image_match:{matched:true,board:{status:'matched',score:.97},shot:{status:'matched',score:.95}},issue_url:'https://project.feishu.cn/example/issue/detail/7184762776',
  request:{method:'GET',url:'https://api.example/puzzle/presets',http:200,business_code:0,count:6,
    request_id:'trace-puzzle',queried_at:'2026-11-28T03:00:00Z'},
  evidence_image:'data:image/png;base64,iVBORw0KGgo='
};
test('saved visual report investigates on click and exposes the captured request and downloadable evidence',async t=>{
  let submitted=0,finish;
  const {page}=openVisual(t,true,null,()=>{
    submitted++;
    return new Promise(resolve=>{finish=()=>resolve({investigation:confirmed});});
  });
  await tick();
  assert.equal(submitted,0);
  const button=getButton(page,'一键核对接口');
  assert.ok(button);
  button.click();button.click();
  assert.equal(button.disabled,true);
  assert.equal(submitted,1);
  finish();await tick();
  assert.ok(getButton(page,'重新核对接口'));
  for (const item of ['真机与接口返回名称一致','太空冒险','太空探险','trace-puzzle','设计稿已匹配','真机已匹配','HTTP 200','业务码 0','2026-11-28','/puzzle/presets']) {
    assert.ok(page.textContent.includes(item),item);
  }
  assert.ok(page.querySelector('a[href="'+confirmed.issue_url+'"]'));
  assert.equal(page.querySelector('a[download]').href,confirmed.evidence_image);
  assert.ok(getButton(page,'放大证据图'));
});
test('inconclusive historical investigations remain visible without submitting a request',async t=>{
  const previous={status:'inconclusive',conclusion:'无法读取接口响应',request:{},issue_url:'javascript:alert(1)'};
  const {page}=openVisual(t,true,previous,()=>assert.fail('unexpected query'));
  await tick();
  assert.match(page.textContent,/待核实/);
  assert.match(page.textContent,/无法读取接口响应/);
  assert.ok(getButton(page,'重新核对接口'));
  assert.equal(page.querySelector('a[href^="javascript:"]'),null);
});
test('reports outside the configured scope have no investigation action',async t=>{
  const {page}=openVisual(t,false,null,()=>assert.fail('unexpected query'));
  await tick();
  assert.match(page.textContent,/AI 对照意见/);
  assert.equal(getButton(page,'一键核对接口'),undefined);
});
