const assert = require('node:assert/strict');
const fs = require('node:fs');
const test = require('node:test');
const {JSDOM} = require('../api-testing-ui/node_modules/jsdom');
const source = fs.readFileSync('js/app.js', 'utf8');
function setup(t, rows) {
  const dom = new JSDOM('<body><main></main></body>', {runScripts:'dangerously'});
  t.after(() => dom.window.close());
  const w = dom.window;
  Object.assign(w, {mindmapCenterFileRows:rows, mindmapReportSelectedCaseSetIds:new Set(), mindmapFileView:{query:'',app:'com.test',module:'',status:'',sort:'module',page:1,selectedOnly:false}, escapeHtml:s=>String(s??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'), jsArg:s=>JSON.stringify(s).replace(/"/g,'&quot;'),formatBytes:n=>`${n} B`});
  for (const name of ['parseMindmapTimeValue','mindmapRecordTimeValue','sortMindmapRecordsByTime','mindmapModuleName','mindmapFileApp','mindmapFilePage','mindmapFileResultsHtml','renderMindmapFileResults','setMindmapFileFilter','changeMindmapFilePage','resetMindmapFileFilters','mindmapFilesSectionHtml','syncMindmapReportSelectedCaseSetIds','updateMindmapReportSourceSelectionText','toggleMindmapReportCaseSet','selectVisibleMindmapReportCaseSets','clearMindmapReportCaseSets','mindmapStatusText','mindmapStatusClass','mindmapRecordCard']) {
    const start = source.search(new RegExp(`^function ${name}\\(`,'m'));
    assert.notEqual(start,-1,`${name} must exist`);
    const rest=source.slice(start), next=rest.slice(1).search(/^(?:async )?function [\w$]+\(/m);
    w.eval(next<0?rest:rest.slice(0,next+1));
  }
  w.document.querySelector('main').innerHTML=w.mindmapFilesSectionHtml(rows);
  return w;
}
const rows = Array.from({length:27},(_,i)=>({app_package:'com.test',case_set_id:`case-${i}`,title:`打印记录 ${i}`,module:i<15?'智小白':'共享',mindmap_exists:i!==26,mindmap_downloadable:i!==26,generated_at:`2026-09-${String(i+1).padStart(2,'0')} 12:00:00`,yaml_file:`case-${i}.yaml`}));
test('groups by module and renders only twelve compact rows per page',t=>{
  const w=setup(t,rows); assert.equal(w.document.querySelectorAll('.mindmap-record-check').length,12);
  assert.ok(w.document.querySelector('.mindmap-library-group h4'));
  assert.match(w.document.querySelector('#mindmap-file-results').textContent,/27/);
  assert.equal(w.document.querySelectorAll('.mindmap-file-more').length,12);
});
test('search covers module filename and ID across pages with multiple keywords',t=>{
  const w=setup(t,rows); w.changeMindmapFilePage(1);
  w.setMindmapFileFilter('query','智小白 CASE-14.YAML');
  assert.equal(w.mindmapFileView.page,1);
  assert.equal(w.document.querySelector('.mindmap-record-check').value,'case-14');
  assert.equal(w.document.querySelectorAll('.mindmap-record-check').length,1);
  w.setMindmapFileFilter('status','pending');
  assert.equal(w.document.querySelectorAll('.mindmap-record-check').length,0);
  assert.match(w.document.querySelector('#mindmap-file-results').textContent,/没有匹配/);
});
test('current page selection never selects hidden rows and survives filters and paging',t=>{
  const w=setup(t,rows); w.selectVisibleMindmapReportCaseSets();
  assert.equal(w.mindmapReportSelectedCaseSetIds.size,12);
  w.changeMindmapFilePage(1);
  assert.match(w.document.querySelector('#mindmap-report-source-count').textContent,/12.*不在当前页/);
  w.selectVisibleMindmapReportCaseSets(); assert.equal(w.mindmapReportSelectedCaseSetIds.size,24);
  w.setMindmapFileFilter('query','not-found');
  assert.equal(w.mindmapReportSelectedCaseSetIds.size,24);
  w.clearMindmapReportCaseSets(); assert.equal(w.mindmapReportSelectedCaseSetIds.size,0);
});
test('selected-only view and refresh prune only vanished records',t=>{
  const w=setup(t,rows); w.mindmapReportSelectedCaseSetIds.add('case-1');
  w.setMindmapFileFilter('selectedOnly',true);
  assert.equal(w.document.querySelectorAll('.mindmap-record-check').length,1);
  w.mindmapCenterFileRows=rows.filter(r=>r.case_set_id!=='case-1');
  w.document.querySelector('main').innerHTML=w.mindmapFilesSectionHtml(w.mindmapCenterFileRows);
  assert.equal(w.mindmapReportSelectedCaseSetIds.size,0);
  assert.equal(w.document.querySelectorAll('.mindmap-record-check').length,0);
});
test('typing keeps search node and focus, escaping prevents markup injection',t=>{
  const w=setup(t,[{...rows[0],title:'<img src=x onerror=alert(1)>',module:'<script>x</script>'}]);
  const input=w.document.querySelector('#mindmap-file-search'); input.focus();
  input.value='打印'; input.dispatchEvent(new w.Event('input',{bubbles:true}));
  assert.equal(w.document.activeElement,input); assert.equal(w.document.querySelector('#mindmap-file-search'),input);
  w.resetMindmapFileFilters();
  assert.equal(w.document.querySelector('img, script'),null);
});
test('status filters distinguish deleted files from missing files; reset restores results',t=>{
  const w=setup(t,[rows[0],{...rows[1],mindmap_deleted:true},rows[26]]);
  w.setMindmapFileFilter('status','deleted'); assert.equal(w.document.querySelector('.mindmap-record-check').value,'case-1');
  w.setMindmapFileFilter('status','pending'); assert.equal(w.document.querySelector('.mindmap-record-check').value,'case-26');
  w.resetMindmapFileFilters(); assert.equal(w.document.querySelectorAll('.mindmap-record-check').length,3);
});
test('latest sort and title sort use records rather than page order',t=>{
  const w=setup(t,rows); w.setMindmapFileFilter('sort','recent');
  assert.equal(w.document.querySelector('.mindmap-record-check').value,'case-26');
  w.setMindmapFileFilter('sort','title'); assert.equal(w.document.querySelector('.mindmap-record-check').value,'case-0');
});

test('all apps show latest three each, drilling into app shows full paginated list',t=>{
 const w=setup(t,[...rows,...rows.map(r=>({...r,app_package:'com.other',case_set_id:'other-'+r.case_set_id}))]);
 w.resetMindmapFileFilters();
 assert.equal(w.document.querySelectorAll('.mindmap-record-check').length,6);
 assert.match(w.document.querySelector('#mindmap-file-results').textContent,/每个应用先展示最近 3 条/);
 const groups=w.document.querySelectorAll('.mindmap-library-group');
 assert.equal(groups.length,2);
 assert.match(groups[0].textContent,/查看全部 27 条/);
 w.setMindmapFileFilter('app','com.other');
 assert.equal(w.document.querySelectorAll('.mindmap-record-check').length,12);
 assert.ok([...w.document.querySelectorAll('.mindmap-record-check')].every(i=>i.value.startsWith('other-')));
});
test('unassigned apps stay separate and same named apps retain package identity',t=>{
 const w=setup(t,[{...rows[0],app_package:''},{...rows[1],app_package:'com.one'},{...rows[2],app_package:'com.two'}]);
 w.appInfoByPackage=()=>({name:'同名应用'}); w.resetMindmapFileFilters();
 assert.equal(w.document.querySelectorAll('.mindmap-library-group').length,3);
 assert.match(w.document.querySelector('#mindmap-file-results').textContent,/未关联应用/);
 w.setMindmapFileFilter('app','com.two');
 assert.equal(w.document.querySelector('.mindmap-record-check').value,'case-2');
});

test('overview paginates applications and selects only their visible previews',t=>{
 const data=Array.from({length:5},(_,app)=>rows.slice(0,5).map(r=>({...r,app_package:`com.app${app}`,case_set_id:`${app}-${r.case_set_id}`}))).flat();
 const w=setup(t,data); w.resetMindmapFileFilters();
 assert.equal(w.document.querySelectorAll('.mindmap-library-group').length,4);
 assert.equal(w.document.querySelectorAll('.mindmap-record-check').length,12);
 w.selectVisibleMindmapReportCaseSets();
 assert.equal(w.mindmapReportSelectedCaseSetIds.size,12);
 w.changeMindmapFilePage(1);
 assert.equal(w.document.querySelectorAll('.mindmap-library-group').length,1);
 assert.equal(w.document.querySelectorAll('.mindmap-record-check').length,3);
 assert.match(w.document.querySelector('#mindmap-report-source-count').textContent,/12.*不在当前页/);
 w.selectVisibleMindmapReportCaseSets();
 assert.equal(w.mindmapReportSelectedCaseSetIds.size,15);
});
test('refresh removes stale application and module filters instead of hiding valid records',t=>{
 const w=setup(t,rows); w.setMindmapFileFilter('module','智小白');
 w.mindmapCenterFileRows=[{...rows[0],app_package:'com.new',module:'新模块'}];
 w.document.querySelector('main').innerHTML=w.mindmapFilesSectionHtml(w.mindmapCenterFileRows);
 assert.equal(w.mindmapFileView.app,''); assert.equal(w.mindmapFileView.module,'');
 assert.equal(w.document.querySelector('[aria-label="筛选应用"]').value,'');
 assert.equal(w.document.querySelectorAll('.mindmap-record-check').length,1);
});
