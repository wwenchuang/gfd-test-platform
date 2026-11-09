import copy
import json
from pathlib import Path
from uuid import uuid4

import pytest

from test_ui_batch_dispatch import dispatch_env
from test_ui_execution_service import environment, ACTORS, APP, TARGET
from task_server.services.operation_attribution import actor_context
from task_server.services.ui_case_draft_store import DraftStore, DraftConflict
from task_server.services.business_line_service import configured_test_application as real_app_lookup
from task_server.services import ui_case_draft_execution as execution


@pytest.fixture
def draft_env(dispatch_env, tmp_path, monkeypatch):
    monkeypatch.setenv('TASK_UI_DRAFT_DB', str(tmp_path / 'private' / 'draft.db'))
    store = DraftStore()
    draft = store.create({'user_id':'u-a','username':'a','display_name':'作者A'}, APP, '点我的检查页面')
    steps = [{'step_id':str(uuid4()),'action':action,'target':target,'expected':expected,
              'value':'','review_state':'confirmed','source_ref':'text'}
             for action,target,expected in [('aiTap','我的','显示我的页面'),('aiAssert','我的页面显示个人资料','')]]
    draft = store.revise(draft['draft_id'], 'u-a', 1, {'title':'检查我的页面','steps':steps})
    return draft, dispatch_env


def trial(draft, key='one'):
    with actor_context(ACTORS['a']):
        return execution.create_draft_trial(ACTORS['a'], draft['draft_id'], {'revision':draft['revision'],'target':TARGET}, key)


def test_trial_freezes_private_source_and_does_not_publish(draft_env):
    draft, _ = draft_env
    batch = trial(draft)
    assert batch['report_status'] == 'not_requested'
    assert batch['delivery_status'] == 'not_requested'
    assert batch['snapshots'][0]['metadata']['status'] == 'draft'
    assert batch['snapshots'][0]['metadata']['draft_revision'] == draft['revision']
    assert batch['owner_user_id'] == 'u-a'
    assert trial(draft)['ui_batch_id'] == batch['ui_batch_id']
    from task_server.services import ui_batch_dispatch as dispatch
    assert dispatch._target_check(batch, batch['case_attempts'][0]) == ''
    DraftStore().revise(draft['draft_id'], 'u-a', draft['revision'], {'title':'下一版本'})
    assert dispatch._target_check(batch, batch['case_attempts'][0]) == ''


def test_trial_rejects_injection_owner_and_unconfirmed(draft_env):
    draft, _ = draft_env
    with actor_context(ACTORS['a']):
        with pytest.raises(ValueError):
            execution.create_draft_trial(ACTORS['a'], draft['draft_id'], {'revision':2,'target':TARGET,'yaml':'evil'}, 'bad')
    with actor_context(ACTORS['b']):
        with pytest.raises(KeyError):
            execution.create_draft_trial(ACTORS['b'], draft['draft_id'], {'revision':2,'target':TARGET}, 'bad')
    edited = DraftStore().revise(draft['draft_id'], 'u-a', 2, {'questions':['还没确认']})
    with pytest.raises(ValueError):
        trial(edited)


def test_trial_rejects_unresolved_video_window_question(draft_env):
    draft, _ = draft_env
    warning='片段 177.9–190.267 秒（原帧 frame-313–frame-342）：该片段模型未报告不确定项，请对照视频核对。'
    edited=DraftStore().revise(draft['draft_id'],'u-a',draft['revision'],{'questions':[warning]})
    with pytest.raises(ValueError,match='draft_gate_required'):
        trial(edited,'unresolved-video-question')


def test_dispatch_rejects_forged_trial_or_changed_hash(draft_env):
    draft, _ = draft_env
    batch = trial(draft)
    from task_server.services import ui_batch_dispatch as dispatch
    bad = copy.deepcopy(batch)
    bad['snapshots'][0]['source_yaml'] += '\n# changed'
    assert dispatch._target_check(bad, bad['case_attempts'][0]) == 'draft_trial_gate_invalid'
    bad = copy.deepcopy(batch)
    bad['snapshots'][0]['metadata']['draft_revision'] += 1
    assert dispatch._target_check(bad, bad['case_attempts'][0]) == 'draft_trial_gate_invalid'


def passed_trial(draft, include_steps=True, progress_override=None):
    from task_server.services import ui_batch_dispatch as dispatch, job_service
    from task_server.services.ui_execution_store import UiExecutionStore
    batch = trial(draft)
    dispatch.dispatch_ui_batch(batch['ui_batch_id'])
    job_id = batch['case_attempts'][0]['dispatch_key']
    delivery = dispatch.claim_ui_job(job_service.get_job(job_id))
    assert delivery is not None
    event = {'type':'result','status':'passed','execution_stopped':True,
             'runner_id':TARGET['runner_id'],'device_id':TARGET['device_id'],
             'ui_claim_token':delivery['ui_claim_token']}
    if include_steps:
        import yaml
        tasks = yaml.safe_load(delivery['yaml_content'])['tasks']
        event['execution_progress'] = {'version':1,'tasks':[
            {'status':'passed','current_step':len(t['flow']),'total_steps':len(t['flow']),
             'steps':[{'label':'完成'} for _ in t['flow']]} for t in tasks]}
    if progress_override is not None:
        event['execution_progress']['tasks'][0].update(progress_override)
    UiExecutionStore().record_attempt_event(job_id, 1, event)
    return UiExecutionStore().get_batch(batch['ui_batch_id'])


@pytest.fixture
def publish_env(draft_env, tmp_path, monkeypatch):
    from task_server import config, identity
    from task_server.services import job_service, business_line_service
    monkeypatch.setattr(config, 'TASK_DIR', str(tmp_path / 'tasks'))
    monkeypatch.setattr(job_service, 'TASK_META_FILE', str(tmp_path / 'meta.json'))
    monkeypatch.setenv('TASK_ASSET_DB', str(tmp_path / 'private' / 'assets.db'))
    task_apps_path = tmp_path / 'task-apps.json'
    task_apps_path.write_text(json.dumps({'apps':[{'package':APP,'name':'测试应用','enabled':True,
        'platform_mappings':{'android':APP,'ios':'com.kfb.3d'},'modules':['navigation']}]}))
    monkeypatch.setattr(config, 'TASK_APPS_FILE', str(task_apps_path))
    monkeypatch.setattr(business_line_service, 'TASK_APPS_FILE', str(task_apps_path))
    monkeypatch.setattr(business_line_service, 'configured_test_application', real_app_lookup)
    assert 'modules' not in business_line_service.configured_test_application(APP)
    profile = identity.get_access_profile
    monkeypatch.setattr(identity, 'get_access_profile', lambda name:
                        {**profile(name),'permissions':profile(name)['permissions'] + ['ui.baseline']})
    return draft_env, tmp_path


def publish(draft, batch_id, module='navigation'):
    with actor_context(ACTORS['a']):
        return execution.publish_draft(ACTORS['a'], draft['draft_id'],
            {'revision':draft['revision'],'batch_id':batch_id,'module':module})


def test_publish_requires_real_completed_steps_and_current_revision(publish_env):
    (draft, _), path = publish_env
    batch = passed_trial(draft, include_steps=False)
    with pytest.raises(ValueError, match='draft_trial_steps_required'):
        publish(draft, batch['ui_batch_id'])
    assert not (path / 'tasks').exists()
    DraftStore().revise(draft['draft_id'], 'u-a', 2, {'title':'变化'})
    with pytest.raises(DraftConflict):
        publish(draft, batch['ui_batch_id'])


def test_publish_is_idempotent_and_recovers_metadata_failure(publish_env, monkeypatch):
    (draft, _), path = publish_env
    batch = passed_trial(draft)
    from task_server.services import job_service
    original = job_service.update_task_meta
    monkeypatch.setattr(job_service, 'update_task_meta', lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('disk full')))
    with pytest.raises(RuntimeError, match='disk full'):
        publish(draft, batch['ui_batch_id'])
    files = list((path / 'tasks' / 'navigation').glob('*.yaml'))
    assert len(files) == 1
    original_bytes = files[0].read_bytes()
    monkeypatch.setattr(job_service, 'update_task_meta', original)
    result = publish(draft, batch['ui_batch_id'])
    assert result['status'] == 'baseline'
    assert publish(draft, batch['ui_batch_id']) == result
    assert files[0].read_bytes() == original_bytes
    assert len(list((path / 'tasks' / 'navigation').glob('*.yaml'))) == 1


def test_publish_never_overwrites_or_crosses_module_scope(publish_env):
    (draft, _), path = publish_env
    batch = passed_trial(draft)
    with pytest.raises(ValueError, match='draft_module_invalid'):
        publish(draft, batch['ui_batch_id'], '../outside')
    target = path / 'tasks' / 'navigation' / (draft['draft_id'] + '_r2.yaml')
    target.parent.mkdir(parents=True)
    target.write_text('unrelated')
    with pytest.raises(DraftConflict, match='draft_publish_path_exists'):
        publish(draft, batch['ui_batch_id'])
    assert target.read_text() == 'unrelated'


def test_trial_target_request_has_exact_fields(draft_env):
    draft, _ = draft_env
    with actor_context(ACTORS['a']):
        with pytest.raises(ValueError, match='draft_target_invalid'):
            execution.create_draft_trial(ACTORS['a'], draft['draft_id'],
                {'revision':draft['revision'],'target':{**TARGET,'yaml_content':'unsafe'}}, 'bad-target')


def test_draft_dispatch_rechecks_permissions_and_version(draft_env, monkeypatch):
    draft, (runners, _) = draft_env
    batch = trial(draft)
    from task_server.services import ui_batch_dispatch as dispatch
    runners['mac']['devices'][0]['installed_apps'][0]['version_code'] = 'changed'
    assert dispatch._target_check(batch, batch['case_attempts'][0]) == 'app_version_changed'
    from task_server import identity
    profile = identity.get_access_profile
    monkeypatch.setattr(identity, 'get_access_profile', lambda name: {**profile(name),'scope':{'ui_apps':[]}})
    assert dispatch._target_check(batch, batch['case_attempts'][0]) == 'app_forbidden'


def test_publish_permission_revocation_prevents_writes(publish_env, monkeypatch):
    (draft, _), path = publish_env
    batch = passed_trial(draft)
    from task_server import identity
    profile = identity.get_access_profile
    monkeypatch.setattr(identity, 'get_access_profile', lambda name:
                        {**profile(name),'permissions':['ui.view','ui.execute']})
    with pytest.raises(PermissionError):
        publish(draft, batch['ui_batch_id'])
    assert not (path / 'tasks').exists()


def test_publish_rejects_shared_module_and_invalidates_catalog(publish_env, monkeypatch):
    (draft, _), path = publish_env
    batch = passed_trial(draft)
    from task_server.services import business_line_service, ui_execution_catalog
    from task_server import config
    apps = json.loads(Path(config.TASK_APPS_FILE).read_text())
    apps['apps'].append({'package':'com.other.app','name':'其他应用','modules':['navigation'],'enabled':True})
    Path(config.TASK_APPS_FILE).write_text(json.dumps(apps))
    with pytest.raises(ValueError, match='draft_module_invalid'):
        publish(draft, batch['ui_batch_id'])
    assert not (path / 'tasks').exists()
    apps['apps'].pop()
    Path(config.TASK_APPS_FILE).write_text(json.dumps(apps))
    cleared = []
    monkeypatch.setattr(ui_execution_catalog, 'clear_catalog_cache', lambda: cleared.append(True))
    result = publish(draft, batch['ui_batch_id'])
    assert cleared == [True]
    assert result['case_id']


def test_pending_publication_before_atomic_write_has_no_empty_placeholder(publish_env, monkeypatch):
    (draft, _), path = publish_env
    batch = passed_trial(draft)
    from task_server import storage
    original = storage.write_text_file
    monkeypatch.setattr(storage, 'write_text_file', lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('crash before atomic write')))
    with pytest.raises(RuntimeError, match='crash before atomic write'):
        publish(draft, batch['ui_batch_id'])
    target = path / 'tasks' / 'navigation' / (draft['draft_id'] + '_r2.yaml')
    assert not target.exists()
    monkeypatch.setattr(storage, 'write_text_file', original)
    assert publish(draft, batch['ui_batch_id'])['status'] == 'baseline'
    assert target.read_text()


def test_pending_publication_does_not_overwrite_third_party_file(publish_env, monkeypatch):
    (draft, _), path = publish_env
    batch = passed_trial(draft)
    from task_server import storage
    original = storage.write_text_file
    monkeypatch.setattr(storage, 'write_text_file', lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('write failed')))
    with pytest.raises(RuntimeError):
        publish(draft, batch['ui_batch_id'])
    target = path / 'tasks' / 'navigation' / (draft['draft_id'] + '_r2.yaml')
    target.write_text('第三方内容')
    monkeypatch.setattr(storage, 'write_text_file', original)
    with pytest.raises(DraftConflict, match='draft_publish_path_exists'):
        publish(draft, batch['ui_batch_id'])
    assert target.read_text() == '第三方内容'


@pytest.mark.parametrize('progress', [
    {'steps':[]}, {'current_step':None}, {'current_step':0},
    {'steps':[{'label':'只报告首步'}]},
])
def test_publish_rejects_incomplete_raw_authenticated_step_evidence(publish_env, progress):
    (draft, _), path = publish_env
    batch = passed_trial(draft, progress_override=progress)
    assert batch['status'] == 'passed'  # Overall result alone is insufficient.
    with pytest.raises(ValueError, match='draft_trial_steps_required'):
        publish(draft, batch['ui_batch_id'])
    assert not (path / 'tasks').exists()


def test_publish_accepts_actual_observer_final_zero_based_cursor(publish_env):
    (draft, _), _ = publish_env
    batch = passed_trial(draft, progress_override={'current_step':5})
    assert batch['case_attempts'][0]['execution_progress']['tasks'][0]['total_steps'] == 6
    assert publish(draft, batch['ui_batch_id'])['status'] == 'baseline'


def test_published_asset_retry_checks_exact_bytes(publish_env):
    (draft, _), path = publish_env
    batch = passed_trial(draft)
    result = publish(draft, batch['ui_batch_id'])
    target = path / 'tasks' / result['module'] / result['file']
    target.write_bytes(target.read_bytes().replace(b'\n', b'\r\n'))
    with pytest.raises(DraftConflict, match='draft_publish_asset_changed'):
        publish(draft, batch['ui_batch_id'])


def test_staged_trial_freezes_all_tasks_and_rejects_partial_stage_evidence(draft_env):
    import yaml
    draft,_=draft_env
    steps=copy.deepcopy(draft['steps'])
    steps[-1]['stage_end']=True
    second=copy.deepcopy(steps)
    for step in second:step['step_id']=str(uuid4());step.pop('stage_end',None)
    draft=DraftStore().revise(draft['draft_id'],'u-a',draft['revision'],{'steps':steps+second})
    batch=passed_trial(draft)
    attempt=batch['case_attempts'][0]
    assert len(yaml.safe_load(attempt['compiled_case']['yaml_content'])['tasks'])==2
    assert execution.validate_trial_snapshot(batch,attempt)
    assert len(attempt['execution_progress']['tasks'])==2
    # Publication must not turn a passed summary into proof of missing stages.
    partial=copy.deepcopy(batch);partial['case_attempts'][0]['execution_progress']['tasks'].pop()
    from unittest.mock import patch
    with actor_context(ACTORS['a']), patch.object(execution.UiExecutionStore,'get_batch',return_value=partial):
        with pytest.raises(execution.BatchValidationError,match='draft_trial_steps_required'):
            execution._verified_publish_batch(ACTORS['a'],draft,batch['ui_batch_id'])


@pytest.mark.parametrize('unbound_event',[False,True])
def test_unresolved_empty_probe_blocks_actual_draft_trial(draft_env,monkeypatch,unbound_event):
    from task_server.services import ui_video_touch_targets as targets
    frames=[{'frame_id':f'frame-{i:03d}','timestamp_ms':i*100} for i in range(1,4)]
    detail={'frame_id':'frame-002-detail','source_frame_id':'frame-002','timestamp_ms':200}
    assets=[{'name':f['frame_id'],'timestamp_ms':f['timestamp_ms']} for f in frames+[detail]]
    events=[{'action':'aiTap','target':'入口','value':'','frame_refs':['frame-003'],
             'observation':'页面显示','uncertainties':[]}] if unbound_event else []
    monkeypatch.setattr(targets.ai,'run_ai_skill',lambda *a,**kw:{'events':events,
        'uncertainties':['无法判断是点击还是返回'],'multiple_scenarios':False})
    hints=targets.probe(frames,[detail],assets,1000,lambda:False)
    questions=targets.bind_uncertain_hints([],hints)
    assert any('局部复核待核对' in q and '无法判断' in q for q in questions)
    draft,_=draft_env
    edited=DraftStore().revise(draft['draft_id'],'u-a',draft['revision'],{'questions':questions})
    with pytest.raises(ValueError,match='draft_gate_required'):
        trial(edited,'unresolved-empty-probe')


@pytest.mark.parametrize('outcome_retry',[False,True])
def test_legacy_analysis_questions_block_actual_trial(draft_env,monkeypatch,tmp_path,outcome_retry):
    from task_server.services import ui_video_analysis as analysis,ai_skill_service as ai
    frames=[{'frame_id':'frame-001','timestamp_ms':100},{'frame_id':'frame-002','timestamp_ms':200}]
    root=tmp_path/'legacy';(root/'frames').mkdir(parents=True)
    for frame in frames:(root/'frames'/(frame['frame_id']+'.jpg')).write_bytes(b'mock')
    event={'action':'aiTap','target':'我的','value':'','start_ms':100,'end_ms':200,
           'frame_refs':['frame-001','frame-002'],'observation':'我的页面','evidence_kind':'inferred','uncertainties':[]}
    def model(name,payload,**kwargs):
        if payload['phase']=='consolidate' or payload.get('validation_feedback'):
            return {'events':[event],'uncertainties':[],'multiple_scenarios':False}
        raw={**event,'uncertainties':['无法确认点击是否发生']}
        if outcome_retry:raw['outcome']={'observation':'我的页面','frame_refs':['unknown']}
        return {'events':[raw],'uncertainties':['页面状态尚待核对'],'multiple_scenarios':False}
    monkeypatch.setattr(ai,'run_ai_skill',model);monkeypatch.setattr(ai,'AI_VISION_IMAGE_LIMIT',6)
    doc={'app_id':APP,'goal':'','media':{'duration_ms':1000,'frames':frames}}
    result=analysis.analyze_frames(doc,root,lambda:False)
    assert any('无法确认点击是否发生' in q for q in result['uncertainties'])
    assert any('页面状态尚待核对' in q for q in result['uncertainties'])
    draft,_=draft_env
    edited=DraftStore().revise(draft['draft_id'],'u-a',draft['revision'],{'questions':result['uncertainties']})
    with pytest.raises(ValueError,match='draft_gate_required'):
        trial(edited,'legacy-unresolved-question')


def test_retry_repair_preserves_first_uncertainties_through_actual_trial(draft_env,monkeypatch,tmp_path):
    from task_server.services import ui_video_reconcile as vr,ui_video_windows as windows,ai_skill_service as ai
    from test_ui_video_coverage_resolution import tap_return_conflict_fixture
    frames,back,tap,claim=tap_return_conflict_fixture()
    monkeypatch.setattr(windows,'load_assets',lambda *a,**kw:[])
    calls=[]
    def model(skill,payload,**kw):
        calls.append(1); candidate=copy.deepcopy(claim)
        event={k:v for k,v in back.items() if k not in {'start_ms','end_ms','evidence_kind'}}
        if len(calls)==1:
            candidate['evidence']['interaction']='未见点击返回箭头，实际执行返回操作'
            event['uncertainties']=['触点归属不确定']
        return {'events':[event],'coverage_resolutions':[candidate],
                'uncertainties':['返回方式未确定'] if len(calls)==1 else [],
                'multiple_scenarios':len(calls)==1}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    result=vr._repair_window([back],{'events':[tap],'uncertainties':[],'multiple_scenarios':False},
                             frames,1000,tmp_path,lambda:False,[],{'f1','f2','f3'},attempt_limit=2)
    assert len(calls)==2 and result['multiple_scenarios']
    assert any('返回方式未确定' in q for q in result['uncertainties'])
    assert any('触点归属不确定' in q for q in result['uncertainties'])
    draft,_=draft_env
    edited=DraftStore().revise(draft['draft_id'],'u-a',draft['revision'],{'questions':result['uncertainties']})
    with pytest.raises(ValueError,match='draft_gate_required'):
        trial(edited,'unresolved-repair-retry')


@pytest.mark.parametrize('field',['target','expected'])
def test_redacted_executable_field_cannot_be_compiled_or_trialled(draft_env,field):
    from task_server.services.ui_case_draft_contract import build_draft_yaml
    draft,_=draft_env
    steps=copy.deepcopy(draft['steps']);steps[0][field]='测试[输入内容未保存]字段'
    edited=DraftStore().revise(draft['draft_id'],'u-a',draft['revision'],{'steps':steps})
    built=build_draft_yaml({key:edited[key] for key in ('title','preconditions','steps','questions')},APP)
    assert not built['ready'] and not built['yaml_content']
    with pytest.raises(ValueError,match='draft_gate_required'):
        trial(edited,'redacted-'+field)
    reset=DraftStore().revise(edited['draft_id'],'u-a',edited['revision'],{'steps':draft['steps'],'questions':[]})
    assert trial(reset,'redacted-reset-'+field)['ui_batch_id']


def test_redacted_field_validate_http_returns_blocker_objects(draft_env,monkeypatch):
    import re
    from task_server import ui_case_draft_http
    monkeypatch.setenv('UI_SIMPLE_FLOW_ENABLED','1')
    draft,_=draft_env
    steps=copy.deepcopy(draft['steps']);steps[0]['target']='按钮[输入内容未保存]'
    edited=DraftStore().revise(draft['draft_id'],'u-a',draft['revision'],{'steps':steps})
    routes={}
    def register(pattern):
        def bind(fn):routes[pattern]=fn;return fn
        return bind
    ui_case_draft_http.register_routes(register,register,register,register)
    class Handler:
        headers={}
        def _body(self):return {'revision':edited['revision'],'target':TARGET}
        def _json(self,body,status=200):self.response=body;self.status=status
    handler=Handler()
    with actor_context(ACTORS['a']):
        for pattern,fn in routes.items():
            match=re.fullmatch(pattern,f"/api/ui-case-drafts/{edited['draft_id']}/validate")
            if match:fn(handler,{},match);break
    assert handler.status==200 and handler.response['gate']['ready'] is False
    assert handler.response['gate']['blockers']
    assert all(set(item)=={'code','message'} for item in handler.response['gate']['blockers'])


def test_repeated_video_question_keeps_global_notice_and_blocks_trial(draft_env):
    from task_server.services.ui_video_analysis import _merge_legacy_questions,draft_proposal
    events=[{'action':'aiAssert','target':'列表可见','value':'','frame_refs':[f'frame-00{i}'],
             'start_ms':i*100,'end_ms':i*100,'evidence_kind':'inferred','observation':'列表可见',
             'uncertainties':['点击是否发生']} for i in (1,2)]
    questions=_merge_legacy_questions([],events,[],[])
    assert len(questions)==1
    doc={'app_id':APP,'source_id':'uvs_'+'0'*32,'analysis_version':1,'goal':'',
         'media':{'duration_ms':1000,'frames':[{'frame_id':f'frame-00{i}','timestamp_ms':i*100} for i in (1,2)]},
         'events':events,'uncertainties':questions}
    proposal,_=draft_proposal(doc)
    assert any('同一问题可能涉及视频多处，请核对完整视频中所有相关操作后再解除' in q for q in proposal['questions'])
    draft,_=draft_env
    edited=DraftStore().revise(draft['draft_id'],'u-a',draft['revision'],{'questions':proposal['questions']})
    with pytest.raises(ValueError,match='draft_gate_required'):
        trial(edited,'repeated-video-issue')


@pytest.mark.parametrize('field',['target','expected'])
def test_planner_cannot_clear_source_redaction_before_actual_trial(draft_env,field):
    from task_server.services.ui_video_analysis import _bind_candidate_evidence,validate_analysis,draft_proposal
    frames=[{'frame_id':'frame-001','timestamp_ms':100},{'frame_id':'frame-002','timestamp_ms':200}]
    candidate={'action':'aiTap','target':'按钮[输入内容未保存]' if field=='target' else '按钮',
        'value':'','start_ms':100,'end_ms':200,'frame_refs':['frame-001','frame-002'],
        'observation':'页面显示结果[输入内容未保存]' if field=='expected' else '页面显示',
        'evidence_kind':'inferred','uncertainties':[]}
    planned={'events':[{'candidate_index':0,'action':'aiTap','target':'按钮A','value':'',
                        'observation':'页面显示结果A','uncertainties':[]}],
             'uncertainties':[],'multiple_scenarios':False}
    result=validate_analysis(_bind_candidate_evidence(planned,[candidate],[0]),frames,1000)
    doc={'app_id':APP,'source_id':'uvs_'+'0'*32,'analysis_version':1,'goal':'',
         'media':{'duration_ms':1000,'frames':frames},**result}
    proposal,_=draft_proposal(doc)
    assert any('[输入内容未保存]' in s[field] for s in proposal['steps'])
    draft,_=draft_env
    edited=DraftStore().revise(draft['draft_id'],'u-a',draft['revision'],{'steps':proposal['steps'],'questions':[]})
    with pytest.raises(ValueError,match='draft_gate_required'):
        trial(edited,'planner-redaction-'+field)


@pytest.mark.parametrize('indexed',[False,True])
def test_masked_source_outcome_cannot_be_replaced_by_clean_planned_outcome(draft_env,indexed):
    from task_server.services.ui_video_analysis import _bind_candidate_evidence,validate_analysis,draft_proposal,_expected_candidate
    frames=[{'frame_id':f'frame-00{i}','timestamp_ms':i*100} for i in (1,2,3)]
    candidate={'action':'aiTap','target':'按钮','value':'','start_ms':100,'end_ms':200,
        'frame_refs':['frame-001','frame-002'],'observation':'按钮已点击',
        'outcome':{'observation':'页面显示结果[输入内容未保存]','frame_refs':['frame-003']},
        'evidence_kind':'inferred','uncertainties':[]}
    event={'action':'aiTap','target':'按钮','value':'','observation':'按钮已点击',
           'outcome':{'observation':'页面显示结果成功','frame_refs':['frame-003']},'uncertainties':[]}
    if indexed:event['candidate_index']=0
    else:event.update(start_ms=100,end_ms=200,frame_refs=['frame-001','frame-002'],evidence_kind='inferred')
    planned={'events':[event],'uncertainties':[],'multiple_scenarios':False}
    checked=validate_analysis(_bind_candidate_evidence(planned,[candidate],[0]),frames,1000)
    assert '[输入内容未保存]' in checked['events'][0]['outcome']['observation']
    assert '[输入内容未保存]' in _expected_candidate(checked['events'][0],'')['text']
    doc={'app_id':APP,'source_id':'uvs_'+'0'*32,'analysis_version':1,'goal':'',
         'media':{'duration_ms':1000,'frames':frames},**checked}
    proposal,_=draft_proposal(doc)
    assert any('[输入内容未保存]' in s['expected'] for s in proposal['steps'])
    draft,_=draft_env
    edited=DraftStore().revise(draft['draft_id'],'u-a',draft['revision'],{'steps':proposal['steps'],'questions':[]})
    with pytest.raises(ValueError,match='draft_gate_required'):
        trial(edited,'masked-outcome-'+str(indexed))


def test_each_video_question_keeps_notice_after_another_is_resolved(draft_env):
    from task_server.services.ui_video_analysis import draft_proposal
    events=[{'action':'aiAssert','target':'列表可见','value':'','frame_refs':['frame-001'],
             'start_ms':100,'end_ms':100,'evidence_kind':'inferred','observation':'列表可见','uncertainties':[]}]
    doc={'app_id':APP,'source_id':'uvs_'+'0'*32,'analysis_version':1,'goal':'',
         'media':{'duration_ms':1000,'frames':[{'frame_id':'frame-001','timestamp_ms':100}]},
         'events':events,'uncertainties':['问题甲','问题乙']}
    proposal,_=draft_proposal(doc)
    notice='同一问题可能涉及视频多处，请核对完整视频中所有相关操作后再解除。'
    assert all(notice in q for q in proposal['questions'])
    draft,_=draft_env
    edited=DraftStore().revise(draft['draft_id'],'u-a',draft['revision'],{'questions':proposal['questions'][1:]})
    with pytest.raises(ValueError,match='draft_gate_required'):
        trial(edited,'one-question-still-unresolved')


@pytest.mark.parametrize('indexed',[False,True])
@pytest.mark.parametrize('source_outcome',['页面显示结果[输入内容未保存]','页面显示成功，正在加载[输入内容未保存]'])
def test_masked_outcome_cannot_be_lost_by_loading_clause_filter(draft_env,indexed,source_outcome):
    from task_server.services.ui_video_analysis import _bind_candidate_evidence,validate_analysis,draft_proposal,_expected_candidate
    frames=[{'frame_id':f'frame-00{i}','timestamp_ms':i*100} for i in (1,2,3)]
    candidate={'action':'aiTap','target':'按钮','value':'','start_ms':100,'end_ms':200,
        'frame_refs':['frame-001','frame-002'],'observation':'按钮已点击',
        'outcome':{'observation':source_outcome,'frame_refs':['frame-003']},
        'evidence_kind':'inferred','uncertainties':[]}
    event={'action':'aiTap','target':'按钮','value':'','observation':'按钮已点击',
        'outcome':{'observation':'页面显示结果成功，正在加载','frame_refs':['frame-003']},'uncertainties':[]}
    if indexed:event['candidate_index']=0
    else:event.update(start_ms=100,end_ms=200,frame_refs=['frame-001','frame-002'],evidence_kind='inferred')
    planned={'events':[event],'uncertainties':[],'multiple_scenarios':False}
    checked=validate_analysis(_bind_candidate_evidence(planned,[candidate],[0]),frames,1000)
    assert '[输入内容未保存]' in _expected_candidate(checked['events'][0],'')['text']
    doc={'app_id':APP,'source_id':'uvs_'+'0'*32,'analysis_version':1,'goal':'',
         'media':{'duration_ms':1000,'frames':frames},**checked}
    proposal,_=draft_proposal(doc)
    assert any('[输入内容未保存]' in s['expected'] for s in proposal['steps'])
    draft,_=draft_env
    edited=DraftStore().revise(draft['draft_id'],'u-a',draft['revision'],{'steps':proposal['steps'],'questions':[]})
    with pytest.raises(ValueError,match='draft_gate_required'):
        trial(edited,'masked-loading-outcome-'+str(indexed))


@pytest.mark.parametrize('indexed',[False,True])
@pytest.mark.parametrize('field',['selection','reusable_expected'])
def test_planner_cannot_erase_masked_executable_source_field(draft_env,indexed,field):
    from task_server.services.ui_video_analysis import _bind_candidate_evidence,validate_analysis,draft_proposal
    from task_server.services.ui_case_draft_contract import build_draft_yaml
    frames=[{'frame_id':f'frame-00{i}','timestamp_ms':i*100} for i in (1,2)]
    target='{{本次对象}}删除按钮' if field=='selection' else '按钮'
    candidate={'action':'aiTap','target':target,'value':'','start_ms':100,'end_ms':200,
        'frame_refs':['frame-001','frame-002'],'observation':'页面显示结果成功',
        'evidence_kind':'inferred','uncertainties':[],field:'结果[输入内容未保存]'}
    event={'action':'aiTap','target':target,'value':'','observation':'页面显示结果成功','uncertainties':[],field:'安全结果'}
    if field=='selection':
        candidate['reusable_expected']=event['reusable_expected']='{{本次对象}}消失，{{保留对象}}仍显示'
    if indexed:event['candidate_index']=0
    else:event.update(start_ms=100,end_ms=200,frame_refs=['frame-001','frame-002'],evidence_kind='inferred')
    planned={'events':[event],'uncertainties':[],'multiple_scenarios':False}
    checked=validate_analysis(_bind_candidate_evidence(planned,[candidate],[0]),frames,1000)
    assert '[输入内容未保存]' in checked['events'][0][field]
    doc={'app_id':APP,'source_id':'uvs_'+'0'*32,'analysis_version':1,'goal':'',
         'media':{'duration_ms':1000,'frames':frames},**checked}
    proposal,_=draft_proposal(doc)
    built=build_draft_yaml(proposal,APP)
    assert not built['ready'] and not built['yaml_content']
    draft,_=draft_env
    edited=DraftStore().revise(draft['draft_id'],'u-a',draft['revision'],{'steps':proposal['steps'],'questions':[]})
    with pytest.raises(ValueError,match='draft_gate_required'):
        trial(edited,'masked-'+field+'-'+str(indexed))
