const test=require('node:test'), assert=require('node:assert/strict'),fs=require('node:fs');
const {JSDOM}=require('../api-testing-ui/node_modules/jsdom');
function setup(){
 const dom=new JSDOM('<button id="import">整理为可执行用例</button>',{runScripts:'dangerously'}),w=dom.window;
 const source=fs.readFileSync('js/app.js','utf8');
 for(const name of ['generationDraftAction','importGenerationDraft']){
  const start=source.search(new RegExp(`^(?:async )?function ${name}\\(`,'m'));assert.notEqual(start,-1);
  const rest=source.slice(start),next=rest.slice(1).search(/^(?:async )?function [\w$]+\(/m);w.eval(next<0?rest:rest.slice(0,next+1));
 }
 Object.assign(w,{uiSimpleEnabled:true,uiSimpleEpoch:1,activeWorkflow:'generate',currentAccessProfile:{user_id:'u'},hasPermission:()=>true,currentModuleAppPackage:()=> 'com.app',showToast:()=>{},openImportedUiDraft:d=>w.opened=d,jsArg:JSON.stringify,escapeHtml:String});
 return w;
}
test('requires trustworthy owner, selected source and permission; manual rows have no button',()=>{
 const w=setup(),run={case_set_id:'case-1',app_package:'com.app',generation_job_id:'gen_1',draft_import_cases:[{case_index:0,case_hash:'h'}]};
 assert.match(w.generationDraftAction(run,0),/整理为可执行用例/);
 assert.doesNotMatch(w.generationDraftAction(run,-1),/<button/);
 w.hasPermission=()=>false;assert.doesNotMatch(w.generationDraftAction(run,0),/<button/);
});
test('submits selector only, adopts matching result and unlocks retry',async()=>{
 const w=setup();w.apiRequest=async(url,options)=>{w.body=JSON.parse(options.body);return {ok:true,draft:{draft_id:'d',app_id:'com.app'}}};
 await w.importGenerationDraft(w.document.querySelector('button'),'agent-1-a',0,'h','com.app');
 assert.deepEqual(Object.keys(w.body).sort(),['app_id','case_hash','case_index','case_set_id']);assert.equal(w.opened.draft_id,'d');
 assert.equal(w.document.querySelector('button').disabled,false);
});
for(const change of ['app','view','epoch','detached','identity'])test(`late ${change} response cannot open editor`,async()=>{
 const w=setup();let done;w.apiRequest=()=>new Promise(r=>done=r);const b=w.document.querySelector('button');
 const pending=w.importGenerationDraft(b,'agent-1-a',0,'h','com.app');
 if(change==='app')w.currentModuleAppPackage=()=> 'com.other';if(change==='view')w.activeWorkflow='home';if(change==='epoch')w.uiSimpleEpoch++;
 if(change==='detached')b.remove();if(change==='identity')w.currentAccessProfile.user_id='other';
 done({ok:true,draft:{draft_id:'d',app_id:'com.app'}});await pending;assert.equal(w.opened,undefined);
});
test('actual generation review places import only beside automated cases',()=>{
 const w=setup(),source=fs.readFileSync('js/app.js','utf8');
 const start=source.indexOf('function generationReviewHtml('),end=source.indexOf('\nfunction showGenerationReview(',start);w.eval(source.slice(start,end));
 Object.assign(w,{currentModule:'AI测试',currentFile:'',generatedSmokeRefs:()=>[],generatedSmokeRerunLimit:()=>0,reviewYamlExecutabilityHtml:()=>'',reviewReadinessHtml:()=>'',generationSmokeAdjustmentHtml:()=>'',reviewSupplementHtml:()=>'',reviewUiDesignsHtml:()=>'',reviewListHtml:()=>'',reviewMatrixHtml:()=>'',reviewCaseCard:row=>`<article>${row.title}</article>`});
 const html=w.generationReviewHtml({case_set_id:'case-1',app_package:'com.app',generation_job_id:'gen_1',draft_import_cases:[{case_index:0,case_hash:'hash'}],cases:[{title:'自动步骤'}],manual_cases:[{title:'人工检查'}]});
 w.document.body.innerHTML=html;
 assert.equal(w.document.querySelectorAll('[data-generation-draft]').length,1);
 assert.match(w.document.querySelector('[data-generation-draft]').getAttribute('onclick'),/importGenerationDraft/);
 assert.match(html,/人工检查/);
});
test('late summary response after navigation does not replace the view',async()=>{
 const w=setup(),source=fs.readFileSync('js/app.js','utf8');
 const start=source.indexOf('async function showGenerationReviewByCaseSet('),end=source.indexOf('\nasync function showCurrentGenerationReview(',start);w.eval(source.slice(start,end));
 w.document.body.innerHTML='<main id="editor-area"><p>source</p></main>';
 let done;w.apiRequest=()=>new Promise(r=>done=r);w.showGenerationReview=()=>w.opened=true;
 const pending=w.showGenerationReviewByCaseSet('case-1');w.activeWorkflow='home';done({summary:{}});await pending;assert.equal(w.opened,undefined);
});
