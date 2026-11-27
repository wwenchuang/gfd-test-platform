import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from test_identity import activate, create_member, identity_db
from test_ui_execution_service import ACTORS, APP, TARGET, REVISION, environment
from test_ui_batch_dispatch import dispatch_env
from task_server.services.operation_attribution import actor_context
from task_server.services import requirement_design


def event(**changes):
    return dict(dict(source='business', event_id='E-1', app_id=APP, requirement_id='PRD-5',
                     revision='v1', title='导航可见', acceptance_criteria=['打开我的后显示个人页']), **changes)


@pytest.fixture
def inputs(dispatch_env, tmp_path, monkeypatch):
    monkeypatch.setattr(requirement_design, '_root', lambda: tmp_path / 'requirements')
    path = tmp_path / 'mapping.json'
    monkeypatch.setenv('MIDSCENE_REQUIREMENT_BINDINGS', str(path))
    path.write_text(json.dumps([dict(source='business', app_id=APP, requirement_id='PRD-5',
        revision='v1', runs=[dict(target=TARGET, cases=[dict(case_id='case-1', revision=REVISION)],
                                execution_policy='reviewed_non_printing')])]))
    return path, dispatch_env


def post(**changes):
    from task_server.services import requirement_intake as intake
    with actor_context(ACTORS['a']):
        return intake.submit(event(**changes))


def test_delivery_replay_and_changed_envelope(inputs):
    from task_server.services import requirement_intake as intake, ui_batch_dispatch
    from task_server.services.ui_execution_store import UiExecutionStore
    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = list(pool.map(lambda _: post(), range(2)))
    assert a['status'] == b['status'] == 'submitted'
    batch_id = a['batches'][0]['ui_batch_id']
    assert b['batches'][0]['ui_batch_id'] == batch_id
    assert post(event_id='E-2')['batches'][0]['ui_batch_id'] == batch_id
    with pytest.raises(intake.Conflict):
        post(title='变更内容')
    with pytest.raises(intake.Conflict):
        post(requirement_id='PRD-6')
    batch = ui_batch_dispatch.dispatch_ui_batch(batch_id)
    assert batch['case_attempts'][0]['job_id']
    assert intake.requirement_for_batch(UiExecutionStore().get_batch(batch_id))['requirement_id'] == 'PRD-5'


def test_unmapped_and_unready_do_not_dispatch_and_can_resume(inputs):
    from task_server.services import requirement_intake as intake
    from task_server.services.ui_execution_service import list_ui_batches
    path, (_runners, sonic) = inputs
    binding = path.read_text()
    path.write_text('[]')
    assert post()['status'] == 'awaiting_mapping'
    path.write_text(binding)
    sonic['iphone']['usage_status'] = 'debugging'
    blocked = post()
    assert blocked['status'] == 'blocked'
    assert blocked['blockers'] == [{'platform':'ios','code':'device_busy'}]
    with actor_context(ACTORS['a']):
        assert list_ui_batches(ACTORS['a'])['items'] == []
    sonic['iphone']['usage_status'] = 'idle'
    assert post()['status'] == 'submitted'
    with actor_context(ACTORS['b']):
        assert intake.read('business', 'E-1') is None


def test_invalid_body_and_revoked_permissions(inputs, monkeypatch):
    from task_server import identity
    from task_server.services import requirement_intake as intake
    with pytest.raises(ValueError):
        post(target=TARGET)
    with pytest.raises(PermissionError):
        post(app_id='unauthorized')
    profile = identity.get_access_profile('a')
    monkeypatch.setattr(identity, 'get_access_profile', lambda _: {**profile, 'permissions':['ui.view']})
    with pytest.raises(PermissionError):
        post()
    with actor_context(ACTORS['a']):
        assert intake.read('business','E-1') is None


def test_native_feishu_pull_paginates_with_project_field_and_uses_approved_scenario(inputs):
    from task_server.services import feishu_requirement_sync as sync
    path, _ = inputs
    mapping = json.loads(path.read_text())[0]
    del mapping['requirement_id'], mapping['revision']
    mapping.update(source='feishu-project:P', scenario='navigation')
    path.write_text(json.dumps([mapping]))
    cfg = dict(project_key='P', work_item_type_key='story', app_id=APP, username='a', user_id='u-a',
               acceptance_field='field_ac', scenario_field='field_feature',
               search_group={'conjunction':'AND','search_params':[{'param_key':'field_ready','operator':'=','value':True}]})
    item = dict(project_key='P', id=812, name='导航可见', work_item_type_key='story',
                updated_at=10, fields=[dict(field_key='field_ac', field_value='打开我的后显示个人页'),
                                      dict(field_key='field_feature',field_value='navigation')])
    calls = []
    def project_api(path, body):
        calls.append((path, body))
        rows = [item] if body['page_num'] == 1 else []
        return {'data':rows, 'err_code':0}
    first = sync.sync(cfg, post=project_api, page_size=1)
    item['updated_at'] = 20
    second = sync.sync(cfg, post=project_api, page_size=1)
    assert first['counts'] == second['counts'] == {'submitted':1}
    assert calls[0][0] == '/open_api/P/work_item/story/search/params'
    assert [x['page_num'] for _,x in calls] == [1,2,1,2]
    with actor_context(ACTORS['a']):
        from task_server.services.ui_execution_service import list_ui_batches
        rows = list_ui_batches(ACTORS['a'])['items']
        assert len(rows) == 1


def test_native_poll_fails_closed_on_actor_mismatch_and_wrong_item_scope(inputs):
    from task_server.services import feishu_requirement_sync as sync
    config = dict(project_key='P', work_item_type_key='story', app_id=APP, username='a', user_id='wrong',
                  acceptance_field='field_ac', search_group={'conjunction':'AND','search_params':[{'param_key':'x'}]})
    calls = []
    def post(path, body):
        calls.append(path)
        return {'data':[{'id':7, 'project_key':'other', 'work_item_type_key':'story'}]}
    with pytest.raises(PermissionError):
        sync.sync(config, post=post)
    assert not calls
    config['user_id'] = 'u-a'
    result = sync.sync(config, post=post)
    assert result['errors'] == [{'item_id':'7','code':'item_scope_mismatch'}]


def test_recent_listing_shares_only_app_scoped_integration_intakes(inputs, monkeypatch):
    from task_server import identity
    from task_server.services import requirement_intake as intake
    with actor_context(ACTORS['a']):
        intake.submit(event(), origin='integration')
        intake.submit(event(event_id='private', requirement_id='PRD-6'))
    with actor_context(ACTORS['b']):
        receipts = intake.recent(10)
        assert len(receipts) == 1 and receipts[0]['requirement_id'] == 'PRD-5'
        assert 'ui_batch_id' not in json.dumps(receipts)
    with actor_context(ACTORS['admin']):
        receipts = intake.recent(10)
        assert len(receipts) == 1
        assert receipts[0]['batches'][0]['ui_batch_id'].startswith('uib_')
    profile = identity.get_access_profile('b')
    monkeypatch.setattr(identity, 'get_access_profile', lambda _: {**profile, 'scope':{'ui_apps':[]}})
    with actor_context(ACTORS['b']):
        assert intake.recent(10) == []


def test_intake_routes_with_real_sessions_and_app_scope(identity_db, tmp_path, monkeypatch):
    import threading
    from task_server import app, auth
    from test_platform_mcp_http import request

    _, store = identity_db
    monkeypatch.setenv('TASK_OPERATION_DB', str(tmp_path / 'audit.sqlite3'))
    monkeypatch.delenv('MIDSCENE_REQUIREMENT_BINDINGS', raising=False)
    store.create_role('admin',{'id':'intake-executor','name':'需求测试','permissions':['ui.view','ui.execute']})
    store.create_role('admin',{'id':'intake-reader','name':'需求只读','permissions':['ui.view']})
    for username, role in [('executor','intake-executor'), ('reader','intake-reader')]:
        create_member(store, username=username, role_ids=[role], scope={'ui_apps':[APP]})
        activate(store,username)
    executor, reader = (auth.create_session_token(name) for name in ('executor','reader'))
    server = app.ThreadingHTTPServer(('127.0.0.1',0), app.TaskHTTPHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = '/api/platform/requirement-design/intake'
    try:
        assert request(server, 'POST', url, payload=event())[0] == 401
        assert request(server, 'POST', url, reader, event())[0] == 403
        assert request(server, 'POST', url, executor, event(app_id='other.app'))[0] == 403
        status, data, _ = request(server, 'POST', url, executor, event())
        assert status == 200 and data['intake']['status'] == 'awaiting_mapping'
        assert request(server, 'GET', url+'?source=business&event_id=E-1', reader)[0] == 404
        assert request(server, 'GET', url+'/recent?limit=10', executor)[1]['items'][0]['title'] == '导航可见'
        assert request(server, 'POST', url, executor, event(title='changed'))[0] == 409
        body = {'source':'business', 'event_id':'E-1', 'app_id':APP}
        assert request(server, 'POST', url+'/design-source', executor, body)[0] == 403
        admin = auth.create_session_token('admin')
        assert request(server, 'POST', url+'/design-source', admin, body)[0] == 404
        assert request(server, 'POST', url, admin, event())[0] == 200
        status, data, _ = request(server, 'POST', url+'/design-source', admin, body)
        assert status == 200 and len(data['source_id']) == 64
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_only_project_sync_can_assert_project_provenance(inputs):
    from task_server.services import requirement_intake as intake
    with actor_context(ACTORS['a']):
        with pytest.raises(ValueError):
            intake.submit(event(source='feishu-project:P'))
        assert intake.read('feishu-project:P','E-1') is None


def test_stale_pins_and_unreviewed_execution_are_blocked_before_dispatch(inputs):
    from task_server.services import requirement_intake as intake
    from task_server.services.ui_execution_service import list_ui_batches
    path, _ = inputs
    bindings = json.loads(path.read_text())
    run = bindings[0]['runs'][0]
    run['execution_policy'] = 'print'
    path.write_text(json.dumps(bindings))
    with pytest.raises(intake.MappingUnavailable):
        post()
    run.update(execution_policy='reviewed_non_printing',
               cases=[dict(case_id='case-' + str(i), revision=REVISION) for i in range(4)])
    path.write_text(json.dumps(bindings))
    with pytest.raises(intake.MappingUnavailable):
        post()
    run['cases'] = [dict(case_id='case-1', revision='0' * 64)]
    path.write_text(json.dumps(bindings))
    assert post()['blockers'] == [{'platform':'ios', 'code':'case_revision_changed'}]
    with actor_context(ACTORS['a']):
        assert list_ui_batches(ACTORS['a'])['items'] == []


def test_unmapped_receipt_becomes_private_text_only_source_without_dispatch(inputs, tmp_path):
    from task_server.services import requirement_design_model, requirement_intake as intake
    path, _ = inputs
    path.write_text('[]')
    with actor_context(ACTORS['a']):
        intake.submit(event(), origin='integration')
        with pytest.raises(PermissionError):
            intake.design_source('business', 'E-1', APP)
        intake.submit(event(requirement_id='PRD-6',event_id='private'))
    with actor_context(ACTORS['admin']):
        source_id = intake.design_source('business','E-1', APP)
        assert source_id == intake.design_source('business','E-1', APP)
        assert intake.design_source('business','private', APP) is None
        with requirement_design._db() as db:
            source_owner, source = requirement_design._source(db, 'u-admin', source_id)
            assert source_owner == 'u-admin'
            assert source['title'] == '导航可见' and source['clauses'] == [{'id':'P1','text':'打开我的后显示个人页'}]
            assert source['prototypes'] == []
            assert db.execute('SELECT count(*) FROM designs').fetchone()[0] == 0
        content, manifest, _ = requirement_design_model._input(source,tmp_path)
        assert len(content) == 1 and set(manifest) == {'P1'}


def test_development_sync_creates_versioned_design_without_dispatch(inputs, monkeypatch):
    from task_server.services import feishu_requirement_sync as sync
    from task_server.services import ui_execution_service
    cfg = dict(project_key='P', work_item_type_key='story', app_id=APP, username='a', user_id='u-a',
               acceptance_field='ac', mode='development_design',
               search_group={'search_params':[{'param_key':'node','value':'dev'}]})
    row = dict(project_key='P', work_item_type_key='story', name='套件专区', id=19,
               fields=[dict(field_key='ac', field_value='可打开套件详情')])
    seen = []
    monkeypatch.setattr(requirement_design,'authorized',lambda *_: True)
    def generate(source,directory):
        seen.append(source)
        return {'cases':[{'id':'C1'}]}
    from task_server.services import requirement_design_model
    monkeypatch.setattr(requirement_design_model,'generate',generate)
    def poll():
        return sync.sync(cfg,post=lambda *args: {'data':[row]})
    assert poll()['counts'] == {'design_completed':1}
    assert poll()['counts'] == {'design_completed':1} and len(seen)==1
    row['fields'][0]['field_value'] += '及说明'
    assert poll()['counts'] == {'design_completed':1} and len(seen)==2
    assert seen[0]['requirement_ref']['requirement_id']=='19'
    with actor_context(ACTORS['a']):
        assert ui_execution_service.list_ui_batches(ACTORS['a'])['items']==[]


def test_qa_build_dispatch_checks_material_and_installed_build(inputs,monkeypatch):
    from task_server.services import feishu_build_plan as builds, feishu_development_design as design
    from task_server.services import requirement_design_model, ui_execution_service as execution
    path, _ = inputs
    material=event(source='feishu-project:P',requirement_id='19')
    cfg = dict(user_id='u-a',username='a',project_key='P',app_id=APP,qa_packages={'ios':'com.kfb.3d'})
    monkeypatch.setattr(requirement_design,'authorized',lambda *_:True)
    monkeypatch.setattr(requirement_design_model,'generate',lambda *_:{'cases':[{'id':'C1'}]})
    with actor_context(ACTORS['a']):
        sid = design.create_design(cfg,material,None)['source_id']
        receipt=dict(project_key='P',requirement_id='19',revision='v1',source_id=sid,platform='ios',
                     environment='qa',build_id='CI-8',package='com.kfb.3d',version_name='1',version_code='2')
        assert builds.submit_build(cfg,material,None,None)['status']=='awaiting_qa_build'
        with pytest.raises(ValueError,match='build_scope_mismatch'):
            builds.submit_build(cfg,material,None,{**receipt,'environment':'production'})
        assert builds.submit_build(cfg,material,None,{**receipt,'source_id':'stale'})['status']=='design_changed_since_build'
        assert builds.submit_build(cfg,material,None,receipt)['status']=='awaiting_reviewed_baselines'
        mapping=json.loads(path.read_text())[0]
        mapping.update(source=material['source'],requirement_id='19',revision=sid)
        path.write_text(json.dumps([mapping]))
        wrong=builds.submit_build(cfg,material,None,{**receipt,'version_code':'3'})
        assert wrong['status']=='blocked' and wrong['blockers'][0]['code']=='app_version_changed'
        first=builds.submit_build(cfg,material,None,receipt)
        again=builds.submit_build(cfg,material,None,receipt)
        assert first['batches'][0]['ui_batch_id']==again['batches'][0]['ui_batch_id']
        assert len(execution.list_ui_batches(ACTORS['a'])['items'])==1


def test_bound_figma_node_uses_immutable_render_version(inputs,monkeypatch):
    from task_server.services import feishu_development_design as d
    from task_server.services import week1_probe
    calls=[]
    def api(path,query):
        calls.append((path,query))
        if path.endswith('/nodes'):
            return {'nodes':{'1:2':{'document':{'id':'1:2'}}},'version':'v18'}
        return {'images':{'1:2':'https://example.com/render'}}
    monkeypatch.setattr(d.knowledge_service,'figma_api_json',api)
    monkeypatch.setattr(d.knowledge_service,'_urlopen_bytes',lambda *a,**kw:week1_probe.FIXTURE.read_bytes())
    cfg={'figma_files':['File123']}
    with pytest.raises(ValueError):
        d._prototype(cfg,'https://www.figma.com/design/Other/a?node-id=1-2')
    assert not calls
    ref,image=d._prototype(cfg,'https://www.figma.com/design/File123/a?node-id=1-2')
    assert ref=={'node_id':'1:2','version':'v18','file_key':'File123'}
    assert image.startswith(b'\x89PNG') and calls[-1][1]['version']=='v18'


def test_signed_scope_wakeup_coalesces_and_ignores_callback_instructions(tmp_path,monkeypatch):
    from task_server.services.requirement_change_hook import receive
    cfg=[dict(mode='development_design',project_key='P',figma_files=['F']),dict(mode='qa_build_smoke',project_key='P')]
    marker=tmp_path/'pending'
    monkeypatch.setenv('MIDSCENE_FEISHU_HOOK_TOKEN','hook')
    body={'header':{'token':'hook','event_type':'WorkFlowNodeStatusEvent'},'payload':{'project_key':'other'}}
    with pytest.raises(PermissionError):
        receive('/hooks/qa/feishu-project',body,cfg,marker)
    assert not marker.exists()
    body['payload']={'project_key':'P','device_id':'ignored'}
    for _ in range(2):
        receive('/hooks/qa/feishu-project',body,cfg,marker)
    assert marker.read_bytes()==b''
    marker.unlink()
    monkeypatch.setenv('MIDSCENE_FIGMA_HOOK_PASSCODE','frame-secret')
    fig={'event_type':'FILE_VERSION_UPDATE','file_key':'F','passcode':'bad'}
    with pytest.raises(PermissionError):
        receive('/hooks/qa/figma',fig,cfg,marker)
    assert not marker.exists()
    receive('/hooks/qa/figma',{**fig,'passcode':'frame-secret'},cfg,marker)
    assert marker.exists()


def test_packaging_notification_before_transfer_is_durable_and_idempotent(inputs,tmp_path,monkeypatch):
    from task_server.services.requirement_change_hook import receive
    from task_server.services.feishu_build_plan import current_builds
    from task_server.services.requirement_intake import record_stage
    cfg=dict(user_id='u-a',app_id=APP,project_key='P')
    ready=dict(cfg,mode='qa_build_smoke',build_source='packaging_hook',qa_packages={'ios':'com.kfb.3d'})
    pair=[dict(cfg,mode='development_design'),ready]
    item=event(source='feishu-project:P',requirement_id='19')
    marker=tmp_path/'pending'
    monkeypatch.setenv('MIDSCENE_QA_PACKAGING_TOKEN','ci')
    with actor_context(ACTORS['a']):
        record_stage(item,'development_design',{'status':'design_completed','source_id':'a'*64})
    context=receive('/hooks/qa/build-context',dict(token='ci',project_key='P',requirement_id='19'),pair,marker)
    assert context['source_id']=='a'*64 and not marker.exists()
    notice=dict(context,platform='ios',environment='qa',build_id='B8',package='com.kfb.3d',version_name='1',version_code='2')
    for _ in range(2):
        receive('/hooks/qa/build',{'token':'ci','build':{**notice,'build_sequence':8}},pair,marker)
    receive('/hooks/qa/build',{'token':'ci','build':{**notice,'build_id':'old','build_sequence':7}},pair,marker)
    assert marker.exists() and current_builds(ready,item)==[notice]
    with pytest.raises(ValueError,match='build_receipt_conflict'):
        receive('/hooks/qa/build',{'token':'ci','build':{**notice,'version_code':'3','build_sequence':8}},pair,marker)


def test_only_bound_design_changes_replace_the_snapshot(inputs,monkeypatch):
    from task_server.services import feishu_development_design as d
    cfg=dict(user_id='u-a',figma_field='design',figma_files=['F'])
    ref={'file_key':'F','node_id':'1:2','version':'old'}
    artifact=[b'first']
    monkeypatch.setattr(d,'_prototype',lambda cfg,link:(dict(ref),artifact[0]))
    source=event()
    first=d.store_source(cfg,source,'frame')
    ref['version']='file-revised'
    assert d.store_source(cfg,source,'frame')==first
    artifact[0]=b'bound-frame-changed'
    assert d.store_source(cfg,source,'frame')!=first


@pytest.mark.parametrize('states,expected',[(['passed','failed'],'continue'),(['failed','failed'],'smoke_blocked'),
                                         (['passed','interrupted'],'smoke_blocked'),(['passed'],'smoke_blocked')])
def test_release_plan_requires_terminal_smoke_evidence(states,expected):
    from task_server.services.feishu_build_plan import _smoke_gate
    request={'cases':[{'case_id':str(i),'revision':'r'} for i in range(2)]}
    batch={'status':'failed','execution_settled':True,'case_attempts':[
        dict(case_id=str(i),case_revision='r',attempt_no=1,status=s) for i,s in enumerate(states)]}
    assert _smoke_gate(batch,request)==expected


def test_release_baselines_reused_only_for_exact_approved_business_coverage(inputs):
    from task_server.services import requirement_intake as service
    path,_=inputs
    binding=json.loads(path.read_text())[0]
    for field in ['requirement_id','revision']:
        binding.pop(field)
    coverage=dict(title='导航可见',clauses=['进入个人页'],frames=[dict(file_key='A',node_id='1:2',sha256='a'*64)])
    binding['coverage']=coverage
    path.write_text(json.dumps([binding]))
    assert len(dict(service._binding(event(),None,release_plan=True,coverage=coverage)))==1
    assert not dict(service._binding(event(),None,release_plan=True,coverage={**coverage,'frames':[]}))
    assert not dict(service._binding(event(),None,release_plan=True,coverage={**coverage,'clauses':['不同的验收条件']}))


def test_generation_outage_backoff_preserves_failed_run(inputs,monkeypatch):
    from task_server.services import feishu_development_design as design, requirement_design_model
    cfg={'user_id':'u-a','username':'a'}
    monkeypatch.setattr(requirement_design,'authorized',lambda *_:True)
    def offline(*_):
        raise ValueError('model_request_failed')
    monkeypatch.setattr(requirement_design_model,'generate',offline)
    first=design.create_design(cfg,event(),None)
    assert first['status']=='design_failed'
    assert design.create_design(cfg,event(),None)['design_id']==first['design_id']
    with requirement_design._db() as db:
        db.execute('UPDATE designs SET finished=0,created=1 WHERE id=?',(first['design_id'],))
    monkeypatch.setattr(requirement_design_model,'generate',lambda *_:{'cases':[{'id':'C1'}]})
    second=design.create_design(cfg,event(),None)
    assert second['status']=='design_completed' and second['source_id']==first['source_id']
    assert second['design_id']!=first['design_id']


def test_transfer_gate_reconciles_frozen_remaining_after_runner_receipt(inputs,monkeypatch):
    from task_server.services import case_service, feishu_requirement_sync as sync, feishu_build_plan as plans
    from task_server.services import job_service, requirement_design_model, ui_batch_dispatch as dispatch
    from task_server.services import ui_execution_service
    path,_=inputs
    base=case_service.list_task_case_assets()[0]
    monkeypatch.setattr(case_service,'list_task_case_assets',lambda:[base,{**base,'case_id':'case-2'}])
    monkeypatch.setattr(requirement_design,'authorized',lambda *_:True)
    monkeypatch.setattr(requirement_design_model,'generate',lambda *_:{'cases':[{'id':'C1'}]})
    cfg=dict(user_id='u-a',username='a',project_key='P',app_id=APP,acceptance_field='ac',
             work_item_type_key='story',search_group={'search_params':[{'param_key':'status','value':'test'}]},
             mode='qa_build_smoke',qa_packages={'ios':'com.kfb.3d'},build_source='packaging_hook')
    row=dict(project_key='P',work_item_type_key='story',id=19,name='套件',fields=[dict(field_key='ac',field_value='打开套件')])
    def poll():
        return sync.sync(cfg,post=lambda *args:{'data':[row]})['counts']
    dev={**cfg,'mode':'development_design'}
    dev.pop('build_source')
    dev.pop('qa_packages')
    assert sync.sync(dev,post=lambda *args:{'data':[row]})['counts']=={'design_completed':1}
    context=plans.build_context(cfg,'19')
    plans.record_build(cfg,dict(context,environment='qa',platform='ios',package='com.kfb.3d',
                                build_id='b1',build_sequence=1,version_name='1',version_code='2'))
    # The package receipt arrives while the story still has no transfer event.
    with actor_context(ACTORS['a']):
        assert ui_execution_service.list_ui_batches(ACTORS['a'])['items']==[]
    binding=json.loads(path.read_text())[0]
    binding.update(source='feishu-project:P',requirement_id='19',revision=context['source_id'])
    binding['runs'][0]['remaining_cases']=[{'case_id':'case-2','revision':REVISION}]
    path.write_text(json.dumps([binding]))
    assert poll()=={'smoke_running':1}
    with actor_context(ACTORS['a']):
        smoke=ui_execution_service.list_ui_batches(ACTORS['a'])['items'][0]
    attempt=dispatch.dispatch_ui_batch(smoke['ui_batch_id'])['case_attempts'][0]
    job=job_service.get_job(attempt['job_id'])
    claim=dispatch.claim_ui_job(job)
    receipt=dict(type='result',status='passed',execution_stopped=True,runner_id='mac',device_id='iphone',
                 ui_claim_token=claim['ui_claim_token'],execution_progress={'version':1,'tasks':[
                     dict(status='passed',current_step=2,total_steps=3,steps=[])]})
    assert dispatch.record_ui_attempt_event(job['job_id'],1,receipt)['status']=='passed'
    path.write_text('[]')
    assert poll()==poll()=={'submitted':1}
    with actor_context(ACTORS['a']):
        items=ui_execution_service.list_ui_batches(ACTORS['a'])['items']
        assert len(items)==2
        main=next(b for b in items if b['ui_batch_id']!=smoke['ui_batch_id'])
        saved=ui_execution_service.get_ui_batch(ACTORS['a'],main['ui_batch_id'])
        assert saved['case_attempts'][0]['case_id']=='case-2'
