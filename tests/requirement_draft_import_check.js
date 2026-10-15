const test=require('node:test'), assert=require('node:assert/strict'),fs=require('node:fs');
const {JSDOM}=require('../api-testing-ui/node_modules/jsdom');
function setup(){
 const dom=new JSDOM('<button id="import">整理为可执行用例</button>',{runScripts:'dangerously'}),w=dom.window;
 const source=fs.readFileSync('js/agent-workbench.js','utf8');
 for(const name of ['agentRequirementDraftAction','importAgentRequirementDraft']){
  const start=source.search(new RegExp(`^(?:async )?function ${name}\\(`,'m'));assert.notEqual(start,-1);
  const rest=source.slice(start),next=rest.slice(1).search(/^(?:async )?function [\w$]+\(/m);w.eval(next<0?rest:rest.slice(0,next+1));
 }
 Object.assign(w,{uiSimpleEnabled:true,uiSimpleEpoch:1,activeWorkflow:'agent',currentAccessProfile:{user_id:'u'},hasPermission:()=>true,selectedAgentAppPackage:()=> 'com.app',showToast:()=>{},openImportedUiDraft:d=>w.opened=d,jsArg:JSON.stringify,escapeHtml:String});
 return w;
}
test('requires trustworthy owner, selected source and permission; manual rows have no button',()=>{
 const w=setup(),run={runId:'agent-1-a',appPackage:'com.app',ownerUserId:'u',sourceType:'requirement',draftImportCases:[{case_index:0,case_hash:'h'}]};
 assert.match(w.agentRequirementDraftAction(run,0),/整理为可执行用例/);
 assert.doesNotMatch(w.agentRequirementDraftAction(run,-1),/<button/);
 w.hasPermission=()=>false;assert.doesNotMatch(w.agentRequirementDraftAction(run,0),/<button/);
});
test('submits selector only, adopts matching result and unlocks retry',async()=>{
 const w=setup();w.apiRequest=async(url,options)=>{w.body=JSON.parse(options.body);return {ok:true,draft:{draft_id:'d',app_id:'com.app'}}};
 await w.importAgentRequirementDraft(w.document.querySelector('button'),'agent-1-a',0,'h','com.app');
 assert.deepEqual(Object.keys(w.body).sort(),['app_id','case_hash','case_index','run_id']);assert.equal(w.opened.draft_id,'d');
 assert.equal(w.document.querySelector('button').disabled,false);
});
for(const change of ['app','view','epoch','detached','identity'])test(`late ${change} response cannot open editor`,async()=>{
 const w=setup();let done;w.apiRequest=()=>new Promise(r=>done=r);const b=w.document.querySelector('button');
 const pending=w.importAgentRequirementDraft(b,'agent-1-a',0,'h','com.app');
 if(change==='app')w.selectedAgentAppPackage=()=> 'com.other';if(change==='view')w.activeWorkflow='home';if(change==='epoch')w.uiSimpleEpoch++;
 if(change==='detached')b.remove();if(change==='identity')w.currentAccessProfile.user_id='other';
 done({ok:true,draft:{draft_id:'d',app_id:'com.app'}});await pending;assert.equal(w.opened,undefined);
});
