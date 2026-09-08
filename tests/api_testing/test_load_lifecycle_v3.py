"""Lifecycle admission and real generated JS behavior; fixtures never target business."""
import copy
import json
import subprocess
from pathlib import Path
import pytest
from task_server.api_testing.services.load_scenario_compiler import compile_scenario, LoadScenarioCompileError
from tests.api_testing.test_load_scenario_compiler import SEARCH_CHAIN, FIXED_RATE


def owned_definition():
    result = copy.deepcopy(SEARCH_CHAIN)
    template = result['steps'][2]
    create = dict(copy.deepcopy(template), id='create', name='创建临时资源', side_effect='creates_owned_resource', cleanup_step_id='cleanup', extractions=[{'target':'owned_id','type':'json_path','path':'$.id','required':True}])
    create['request'].update(method='POST',path='/resources',headers={})
    cleanup = dict(copy.deepcopy(template),id='cleanup',name='清理本轮资源',scope='cleanup_once',side_effect='cleanup_owned_resource')
    cleanup['request'].update(method='DELETE',path='/resources/{{owned_id}}',headers={})
    result.update(steps=[create,cleanup],dataset_contract={'dataset_id':None,'usage_mode':'cycle','variables':[]},risk={'level':'medium','ownership_variable':'owned_id','notes':'仅本轮'})
    return result


def test_frozen_v2_script_bytes_remain_unchanged():
    golden = json.loads(Path(__file__).with_name('fixtures').joinpath('load_compiler_v2.json').read_text())
    assert compile_scenario(SEARCH_CHAIN,FIXED_RATE,compiler_version='k6-safe-v2').script == golden


@pytest.mark.parametrize('change', ['setup_once','agent_setup','vu_once','default_id','unbound_cleanup','override_id','other_extraction','cross_service','mutate'])
def test_v3_rejects_unsafe_lifecycle(change):
    definition=owned_definition()
    if change in ('setup_once','agent_setup','vu_once'): definition['steps'][0]['scope']=change
    elif change=='default_id': definition['steps'][0]['extractions'][0]['default']='historical'
    elif change=='unbound_cleanup': definition['steps'][0]['side_effect']='readonly'
    elif change=='override_id': definition['steps'][1]['request']['path_params']={'owned_id':'historical'}
    elif change=='cross_service': definition['steps'][1]['request']['service']='other'
    elif change=='other_extraction':
        other=copy.deepcopy(definition['steps'][0]);other.update(id='other',side_effect='readonly');definition['steps'].insert(1,other)
    else: definition['steps'][0]['side_effect']='mutates_owned_resource'
    with pytest.raises(LoadScenarioCompileError): compile_scenario(definition,FIXED_RATE)


def run_js(definition, responses, iterations=1):
    compiled=compile_scenario(definition,FIXED_RATE)
    script='\n'.join(line for line in compiled.script.splitlines() if not line.startswith('import ')).replace('export const ','const ').replace('export function ','function ').replace('export default function(data)','function iteration(data)')
    harness='''
const calls=[],results=[];
const responses=RESPONSES;
const __ENV={LOAD_DATASET_FILE:'fixture',BASE_URL:'http://fixture'};
const __VU=1;
const exec={scenario:{iterationInTest:0,startTime:Date.now()}};
const open=()=> '[]'; const sleep=()=>{};
class Counter { add(){} }
class Rate { constructor(name){this.name=name} add(v){if(this.name==='workflow_iteration_success')results.push(v)} }
const check=(response,checks)=>Object.values(checks).every(fn=>fn(response));
const http={request(method,url){calls.push({method,url});const next=responses.shift();if(next==='timeout')throw Error('transport timeout');return {status:next.status,json:()=>next.body,headers:{},cookies:{},timings:{duration:1}}}};
SCRIPT
const data=setup();
for(let i=0;i<ITERATIONS;i++){try{iteration(data)}catch(e){}exec.scenario.iterationInTest++}
console.log(JSON.stringify({calls,results}));
'''.replace('RESPONSES',json.dumps(responses)).replace('SCRIPT',script).replace('ITERATIONS',str(iterations))
    return json.loads(subprocess.check_output(['node','--input-type=commonjs','-e',harness],text=True))


def test_v3_cleans_per_iteration_and_never_reuses_previous_id_after_timeout():
    result=run_js(owned_definition(),[{'status':200,'body':{'id':'first'}},{'status':200,'body':{}},'timeout'],2)
    assert result['calls']==[{'method':'POST','url':'http://fixture/resources'},{'method':'DELETE','url':'http://fixture/resources/first'},{'method':'POST','url':'http://fixture/resources'}]
    assert result['results']==[True,False]


def test_cleanup_failure_fails_workflow():
    result=run_js(owned_definition(),[{'status':200,'body':{'id':'first'}},{'status':500,'body':{}}])
    assert len(result['calls'])==2
    assert result['results']==[False]


def test_assertion_failure_still_cleans_extracted_id_and_stops_later_business():
    definition=owned_definition()
    definition['steps'][0]['assertions'].append({'type':'json_path','path':'$.ok','operator':'equals','expected':True})
    later=copy.deepcopy(definition['steps'][1]);later.update(id='later',scope='iteration',side_effect='readonly');later['request'].update(method='GET',path='/later');definition['steps'].insert(1,later)
    result=run_js(definition,[{'status':200,'body':{'id':'first','ok':False}},{'status':200,'body':{}}])
    assert [call['method'] for call in result['calls']]==['POST','DELETE']
    assert result['results']==[False]


def test_resource_path_preserves_safe_response_id_and_writes_do_not_follow_redirects():
    definition=owned_definition()
    result=run_js(definition,[{'status':200,'body':{'id':'owned_A-b_012'}},{'status':200,'body':{}}])
    assert result['calls'][-1]['url']=='http://fixture/resources/owned_A-b_012'
    script=compile_scenario(definition,FIXED_RATE).script
    assert 'redirects: 0' in script


@pytest.mark.parametrize('resource_id', ['', {}, [], True, '..', '../old', 'old/other', 'historical?x=1', 'historical#new', 'historical%3Fx=1', '%252e%252e', 'id with space', 'ümlaut', 'a.b', 'a' * 257, -1, 1.5, 9007199254740992])
def test_invalid_resource_id_never_reaches_cleanup(resource_id):
    result=run_js(owned_definition(),[{'status':200,'body':{'id':resource_id}}])
    assert [call['method'] for call in result['calls']]==['POST']
    assert result['results']==[False]


@pytest.mark.parametrize('mode', ['second_create_disconnect','cleanup_failure'])
def test_real_k6_iteration_cleanup(tmp_path, mode):
    import os
    import shutil
    import socket
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    k6=os.environ.get('K6_BINARY') or shutil.which('k6')
    if not k6: pytest.skip('Set K6_BINARY to run real local k6 acceptance')
    requests=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_POST(self):
            requests.append(('POST',self.path))
            n=sum(method=='POST' for method,_ in requests)
            if mode=='second_create_disconnect' and n>1:
                self.connection.shutdown(socket.SHUT_RDWR); self.connection.close(); return
            self.send_response(200); self.end_headers(); self.wfile.write(json.dumps({'id':f'owned-{n}'}).encode())
        def do_DELETE(self):
            requests.append(('DELETE',self.path))
            self.send_response(500 if mode=='cleanup_failure' else 200);self.end_headers();self.wfile.write(b'{}')
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    workload={'executor':'constant-arrival-rate','rate':1,'time_unit':'1s','duration_seconds':2,'pre_allocated_vus':1,'max_vus':1}
    compiled=compile_scenario(owned_definition(),workload)
    script=tmp_path/'scenario.js';script.write_text(compiled.script)
    dataset=tmp_path/'dataset.json';dataset.write_text('[]')
    try:
        result=subprocess.run([k6,'run','--quiet','--no-summary','--out','json=-',str(script)],env={**os.environ,'BASE_URL':f'http://127.0.0.1:{server.server_port}','LOAD_DATASET_FILE':str(dataset)},capture_output=True,text=True,timeout=20)
    finally:
        server.shutdown();server.server_close();thread.join(timeout=2)
    assert result.returncode==0,result.stderr
    points=[json.loads(line) for line in result.stdout.splitlines() if line.startswith('{')]
    workflow=[p['data']['value'] for p in points if p.get('type')=='Point' and p.get('metric')=='workflow_iteration_success']
    creates=[p for m,p in requests if m=='POST'];deletes=[p for m,p in requests if m=='DELETE']
    assert 2<=len(creates)<=3
    assert len(workflow)==len(creates)
    if mode=='second_create_disconnect':
        assert deletes==['/resources/owned-1']
        assert workflow[0]==1 and all(value==0 for value in workflow[1:])
    else:
        assert len(deletes)==len(creates)
        assert all(value==0 for value in workflow)


def test_private_agent_payload_rejects_legacy_write_before_compilation_or_dispatch():
    from types import SimpleNamespace
    from task_server.api_testing import load_agent_http
    run=SimpleNamespace(configuration={'compiler':{'version':'k6-safe-v2'}})
    with pytest.raises(load_agent_http.ApiHttpError) as error:
        load_agent_http._execution_payload(None,run,None,SimpleNamespace(definition=owned_definition()))
    assert error.value.code=='unsupported_lifecycle'


def test_saved_scenario_admission_reports_unsupported_lifecycle_and_polling():
    from task_server.api_testing.services.load_scenario_service import LoadScenarioService
    definition=owned_definition();definition['steps'][0]['scope']='agent_setup'
    assert not LoadScenarioService.validate_executable_definition(definition).accepted
    definition=owned_definition();definition['steps'][0]['polling']={'max_attempts':3}
    assert not LoadScenarioService.validate_executable_definition(definition).accepted


@pytest.mark.parametrize('resource_id,allowed', [('abc_12-XY',True),(0,True),(42.0,True),(9007199254740991,True),(9007199254740992,False),(10**400,False),('x?y',False),('x#y',False),('x%252f',False),(True,False),('x\n',False)])
def test_python_and_generated_js_ownership_id_contract_agree(resource_id,allowed):
    from task_server.api_testing.services.load_lifecycle_policy import valid_owned_id
    assert valid_owned_id(resource_id) is allowed
    responses=[{'status':200,'body':{'id':resource_id}}]
    if allowed: responses.append({'status':200,'body':{}})
    result=run_js(owned_definition(),responses)
    assert len(result['calls'])==(2 if allowed else 1)
