const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs');
const {JSDOM}=require('../api-testing-ui/node_modules/jsdom');
function setup(){
 const ids=['module','title','file','content','figma-url','figma-mode','figma-limit','create-job','auto-optimize','run-mode','knowledge-tier'];
 const w=new JSDOM(`<main id="editor-area"><p>source</p></main><div id="modal-generate" class="show"></div><input id="app-filter" value="com.app">`+ids.map(id=>`<input id="generate-${id}" value="${id==='module'?'AI测试':id==='content'?'检查我的':''}">`).join('')+'<input name="generate-business" type="radio" checked value="b">',{runScripts:'dangerously'}).window;
 const source=fs.readFileSync('js/app.js','utf8');
 for(const name of ['captureGenerationView','generateYaml','showGenerateJobsCenter']){
 const start=source.search(new RegExp(`^(?:async )?function ${name}\\(`,'m'));if(start<0)continue;
 let end=source.slice(start+1).search(/^(?:async )?function [\w$]+\(/m);if(name==='generateYaml')end=source.indexOf('\n\n// 快捷键',start)-start-1;
 w.eval(end<0?source.slice(start):source.slice(start,start+1+end));}
 Object.assign(w,{generateBusyOwner:null,activeWorkflow:'generate',uiSimpleEpoch:1,currentAccessProfile:{user_id:'u'},currentModule:'old',currentFile:'old.yaml',generateAssetFiles:[],modules:{},taskApps:[{package:'com.app',modules:['old']}],GENERATION_RECORD_PAGE_SIZE:20,
 selectedGenerateApplication:()=>({package:'com.app'}),currentModuleAppPackage:()=>w.app||'com.app',selectedRunnerDevice:()=>({}),selectedGenerateKnowledgePageIds:()=>[],rememberAppPackage:()=>{},setGenerateBusy:()=>{},setGenerateProgress:()=>{},setGenerateStatus:()=>{},showToast:()=>{},closeModal:()=>w.document.querySelector('#modal-generate').classList.remove('show'),loadJobs:async()=>{},renderGenerateJobsCenterLoading:()=>{w.centers=(w.centers||0)+1;w.activeWorkflow='generate';w.document.querySelector('#editor-area').innerHTML='<p>loading</p>';},renderGenerateJobsCenter:()=>{w.document.querySelector('#editor-area').innerHTML='<p>jobs</p>';},loadModules:async()=>{},renderModules:()=>{},buildGenerateResultNote:()=>'',showGenerationReviewByCaseSet:async()=>w.opened=true,stopGenerateProgress:()=>{},setGenerateProgressError:()=>{},apiRequest:async()=>({job_id:'gen_1'}),pollGenerateJob:async()=>({module:'new',file:'new.yaml',case_set_id:'case1'})});return w;
}
function change(w,kind){if(kind==='view')w.activeWorkflow='home';if(kind==='app')w.document.querySelector('#app-filter').value='other';if(kind==='account')w.currentAccessProfile.user_id='other';if(kind==='epoch')w.uiSimpleEpoch++;if(kind==='dom')w.document.querySelector('#editor-area').innerHTML='<p>other</p>';}
for(const stage of ['submit','poll','modules','center'])for(const kind of ['view','app','account','epoch','dom'])test(`stale ${stage}/${kind} preserves current page and selected file`,async()=>{
 const w=setup();let release,entered;const reached=new Promise(r=>entered=r),wait=()=>{entered();return new Promise(r=>release=r)};
 if(stage==='submit')w.apiRequest=wait;if(stage==='poll')w.pollGenerateJob=wait;if(stage==='modules')w.loadModules=wait;if(stage==='center')w.loadJobs=wait;
 const pending=w.generateYaml();await reached;change(w,kind);release(stage==='submit'?{job_id:'gen_1'}:{module:'new',file:'new.yaml',case_set_id:'case1'});await pending;
 assert.equal(w.opened,undefined);assert.equal(w.currentFile,'old.yaml');if(stage==='submit')assert.equal(w.centers,undefined);
 if(kind==='view')assert.equal(w.activeWorkflow,'home');if(kind==='app')assert.equal(w.document.querySelector('#app-filter').value,'other');
});
test('unchanged context adopts completed result',async()=>{const w=setup();await w.generateYaml();assert.equal(w.currentFile,'new.yaml');assert.equal(w.opened,true);});
test('polling stops adopting progress after context expires',async()=>{
 const w=setup(),source=fs.readFileSync('js/app.js','utf8');const a=source.indexOf('async function pollGenerateJob('),b=source.indexOf('\nasync function pollGenericJob(',a);w.eval(source.slice(a,b));
 let release;w.apiRequest=()=>new Promise(r=>release=r);let valid=true,updates=0;w.setGenerateProgress=()=>updates++;w.setGenerateStatus=()=>updates++;
 const pending=w.pollGenerateJob('gen_1',()=>valid);valid=false;release({job:{status:'success',result:{}}});assert.equal(await pending,null);assert.equal(updates,0);
});
function useRealBusy(w){
 const source=fs.readFileSync('js/app.js','utf8');
 for(const name of ['setGenerateBusy','updateGenerateSubmitState','showGenerateYaml']){const a=source.indexOf(`function ${name}(`),b=source.slice(a+1).search(/^(?:async )?function [\w$]+\(/m);w.eval(source.slice(a,a+b+1));}
 w.document.body.insertAdjacentHTML('beforeend','<button id="btn-generate-yaml"></button><button id="btn-close-generate"></button><select id="generate-application"></select>');
 Object.assign(w,{generateBusy:false,generateBusyOwner:null,enabledGenerateApplications:()=>[{package:'com.app'}],taskAppCatalogLoading:()=>false,taskAppCatalogError:()=>null,setActiveWorkflow:v=>w.activeWorkflow=v,renderModuleSelects:()=>{},resetGenerateModal:()=>w.updateGenerateSubmitState(),syncGenerateAppFromModule:()=>{},updateGenerateAppHint:()=>{},ensureModulesLoaded:async()=>{},loadKnowledgeApps:async()=>{},renderGenerateApplicationOptions:()=>{},handleGenerateApplicationChange:()=>{},moduleAppPackage:()=> 'com.app',loadRunnerDevices:()=>{}});
}
for(const stage of ['submit','poll','modules','center'])test(`stale ${stage} releases actual busy state and generator reopens enabled`,async()=>{
 const w=setup();useRealBusy(w);let release,entered;const reached=new Promise(r=>entered=r),wait=()=>{entered();return new Promise(r=>release=r)};
 if(stage==='submit')w.apiRequest=wait;if(stage==='poll')w.pollGenerateJob=wait;if(stage==='modules')w.loadModules=wait;if(stage==='center')w.loadJobs=wait;
 const pending=w.generateYaml();await reached;assert.equal(w.generateBusy,true);change(w,'view');release(stage==='submit'?{job_id:'gen_1'}:{module:'new',file:'new.yaml',case_set_id:'case1'});await pending;
 assert.equal(w.generateBusy,false);w.showGenerateYaml();assert.equal(w.document.querySelector('#btn-generate-yaml').disabled,false);assert.equal(w.document.querySelector('#btn-close-generate').disabled,false);
});
test('older stale attempt cannot release a newer attempt busy state',async()=>{
 const w=setup();useRealBusy(w);let first,second;w.apiRequest=()=>new Promise(r=>{if(!first)first=r;else second=r});
 const old=w.generateYaml();change(w,'view');const next=w.generateYaml();first({job_id:'gen_old'});await old;assert.equal(w.generateBusy,true);assert.equal(w.document.querySelector('#btn-generate-yaml').disabled,true);
 change(w,'account');second({job_id:'gen_new'});await next;assert.equal(w.generateBusy,false);
});
