"""Explicit, immutable evidence gates for unmatched video touches."""
import copy
import pytest
from task_server.services import ui_case_draft_service as service
from task_server.services import ui_case_draft_execution as execution
from task_server.services.operation_attribution import actor_context
from task_server.services.ui_execution_service import BatchValidationError
from test_ui_case_draft_service import env, create, confirm, A, APP, TARGET


def video_draft(env):
    base = create(env)
    snapshot = {'kind': 'video', 'snapshot_id': 'snapshot-v1', 'source_id': 'uvs_' + 'a'*32,
                'app_id': APP, 'analysis_version': 1,
                'frames': [{'frame_id': 'frame-001', 'timestamp_ms': 100}], 'events': [], 'touch_source_projection': [],
                'touch_ambiguities': [{'id': 'touch-001', 'frame_id': 'frame-001',
                    'frame_sha256': 'a'*64, 'reason': 'no_match', 'questions': ['触点目标不确定']} ]}
    content = {k: copy.deepcopy(base[k]) for k in ('title', 'preconditions', 'steps', 'questions', 'yaml_content', 'ready')}
    for step in content['steps']:
        step['review_state'] = 'confirmed'
    content['questions'] = []
    return service._store().create(A, APP, '来源：录屏', recording_snapshot=snapshot, recording_content=content)


def test_deleting_questions_does_not_release_unresolved_touch_validate(env):
    draft = video_draft(env)
    with actor_context(A):
        gate = service.validate_draft(A, draft['draft_id'], {'revision': draft['revision'], 'target': TARGET})
    assert not gate['ready']
    assert any(item['code'] == 'video_touch_review_required' for item in gate['blockers'])


def test_trial_source_rechecks_unresolved_touch_even_after_questions_removed(env):
    draft = video_draft(env)
    with pytest.raises(BatchValidationError, match='video_touch_review_required'):
        execution._source(draft)


def test_unmatched_probe_keeps_exact_sent_frame_as_pending_evidence(monkeypatch, tmp_path):
    import base64, hashlib
    from task_server.services import ui_video_windows as windows, ui_video_touch_detail as detail
    from task_server.services import ui_video_touch_targets as targets, ai_skill_service as ai
    frames = [{'frame_id':'frame-001','timestamp_ms':0}, {'frame_id':'frame-002','timestamp_ms':100}]
    pixel_bytes=b'actual-original-frame'
    monkeypatch.setattr(windows, 'load_assets', lambda fs, *_: [
        {'name': f['frame_id'], 'timestamp_ms': f['timestamp_ms'], 'mime': 'image/jpeg',
         'base64':base64.b64encode(pixel_bytes).decode()} for f in fs])
    monkeypatch.setattr(detail, 'append_details', lambda *a: [])
    monkeypatch.setattr(targets, 'probe', lambda *a: [{'frame_id':'frame-001', 'target':'图标',
        'observation':'触点可能位于空白区域', 'questions':['是否触发操作'], 'requires_review':True}])
    monkeypatch.setattr(ai, 'run_ai_skill', lambda *a,**kw: {'events':[], 'uncertainties':[], 'multiple_scenarios':False})
    result, _ = windows.observe_windows(frames, 200, tmp_path, lambda:False)
    assert result['events']==[]
    pending=result['touch_ambiguities']
    assert len(pending)==1 and pending[0]['frame_id']=='frame-001'
    assert pending[0]['frame_sha256']==hashlib.sha256(pixel_bytes).hexdigest()
    assert pending[0]['questions']==['是否触发操作']
    assert pending[0]['phase']=='observe'


def test_analysis_preserves_first_and_review_pending_through_planning(monkeypatch, tmp_path):
    from task_server.services import ui_video_analysis as analysis, ui_video_windows as windows
    from task_server.services import ui_video_reconcile as reconcile, ui_video_delayed_response as delayed
    first={'frame_id':'frame-001', 'frame_sha256':'a'*64, 'reason':'no_match', 'phase':'observe'}
    second={'frame_id':'frame-002', 'frame_sha256':'b'*64, 'reason':'no_match', 'phase':'review'}
    monkeypatch.setattr(windows, 'observe_windows', lambda *a,**k: ({'events':[], 'uncertainties':[], 'multiple_scenarios':False, 'touch_ambiguities':[first]}, []))
    monkeypatch.setattr(reconcile, 'needs_reconciliation', lambda *a: True)
    monkeypatch.setattr(reconcile, 'reconcile', lambda *a: {'events':[], 'uncertainties':[], 'multiple_scenarios':False, 'touch_ambiguities':[second]})
    monkeypatch.setattr(delayed, 'review_pending_responses', lambda observed,*a: observed)
    monkeypatch.setattr(analysis, 'consolidate_observed_frames', lambda *a,**k: {'events':[], 'uncertainties':[], 'multiple_scenarios':False})
    doc={'media':{'frames':[{'frame_id':'frame-001','timestamp_ms':0},{'frame_id':'frame-002','timestamp_ms':100}], 'duration_ms':200}}
    result=analysis.analyze_whole_frames(doc,tmp_path,lambda:False)
    assert result['touch_ambiguities']==[first,second]


def test_source_snapshot_retains_unmatched_frame_evidence():
    from task_server.services.ui_video_analysis import draft_proposal
    from test_ui_video_executor_contract import source_document
    doc=source_document()
    evidence={'frame_id':doc['media']['frames'][0]['frame_id'], 'frame_sha256':'a'*64,
              'reason':'no_match', 'phase':'observe','target':'目标有疑问',
              'observation':'待核对','questions':['是否点击']}
    doc['touch_ambiguities']=[evidence]
    _,snapshot=draft_proposal(doc)
    assert snapshot['touch_ambiguities']==[evidence]


def real_video_draft(env, tmp_path, monkeypatch):
    import hashlib, io
    from task_server.services.ui_video_source_service import VideoStore
    root=tmp_path/'video-root';monkeypatch.setenv('TASK_UI_VIDEO_ROOT',str(root))
    store=VideoStore();source=store.upload(A,APP,'clip.mp4',io.BytesIO(b'mp4'),3,actor=A)
    source_id=source['source_id'];frame='frame-001';data=b'exact original bytes'
    folder=root/source_id/'frames';folder.mkdir();(folder/(frame+'.jpg')).write_bytes(data)
    ambiguity={'frame_id':frame,'frame_sha256':hashlib.sha256(data).hexdigest(),
               'reason':'no_match','phase':'observe','target':'可能的入口',
               'observation':'触点可能位于空白','questions':['是否有动作']}
    frames=[{'frame_id':frame,'timestamp_ms':100}]
    store.transition(source_id,{'queued'},'ready',media={'frames':frames},touch_ambiguities=[ambiguity],retained=True)
    base=create(env)
    snapshot={'kind':'video','snapshot_id':'real-v1','source_id':source_id,'app_id':APP,'analysis_version':1,
              'frames':frames,'events':[],'touch_source_projection':[],'touch_ambiguities':[ambiguity]}
    content={k:copy.deepcopy(base[k]) for k in ('title','preconditions','steps','questions','yaml_content','ready')}
    for step in content['steps']:step['review_state']='confirmed'
    content['questions']=[]
    return service._store().create(A,APP,'来源：录屏',recording_snapshot=snapshot,recording_content=content),store,folder


def test_explicit_decision_is_bound_to_exact_frame_and_invalidated_by_edit(env,tmp_path,monkeypatch):
    draft,store,folder=real_video_draft(env,tmp_path,monkeypatch)
    body={'revision':1,'index':0,'frame_id':'frame-001','disposition':'not_action','step_id':''}
    with actor_context(A):
        reviewed=service.resolve_touch_ambiguity(A,draft['draft_id'],body)
        assert reviewed['revision']==2
        gate=service.validate_draft(A,draft['draft_id'],{'revision':2,'target':TARGET})
        assert not any(b['code']=='video_touch_review_required' for b in gate['blockers'])
        updated=service.update_draft(A,draft['draft_id'],{**confirm(reviewed),'title':'变更'})
        gate=service.validate_draft(A,draft['draft_id'],{'revision':updated['revision'],'target':TARGET})
        assert any(b['code']=='video_touch_review_required' for b in gate['blockers'])


def test_explicit_decision_rejects_wrong_frame_and_changed_pixels(env,tmp_path,monkeypatch):
    draft,store,folder=real_video_draft(env,tmp_path,monkeypatch)
    body={'revision':1,'index':0,'frame_id':'frame-002','disposition':'not_action','step_id':''}
    with actor_context(A):
        with pytest.raises(ValueError):service.resolve_touch_ambiguity(A,draft['draft_id'],body)
        body['frame_id']='frame-001'
        (folder/'frame-001.jpg').write_bytes(b'different bytes')
        with pytest.raises(ValueError):service.resolve_touch_ambiguity(A,draft['draft_id'],body)
    assert service._store().get(draft['draft_id'],A['user_id'])['revision']==1


def test_decision_rechecks_source_version_and_pixel_bytes_before_execution(env,tmp_path,monkeypatch):
    draft,store,folder=real_video_draft(env,tmp_path,monkeypatch)
    body={'revision':1,'index':0,'frame_id':'frame-001','disposition':'not_action','step_id':''}
    with actor_context(A):
        reviewed=service.resolve_touch_ambiguity(A,draft['draft_id'],body)
        execution._source(reviewed)
        (folder/'frame-001.jpg').write_bytes(b'tampered')
        with pytest.raises(BatchValidationError,match='video_touch_review_required'):execution._source(reviewed)
        gate=service.validate_draft(A,draft['draft_id'],{'revision':2,'target':TARGET})
        assert any(b['code']=='video_touch_review_required' for b in gate['blockers'])
        (folder/'frame-001.jpg').write_bytes(b'exact original bytes')
        doc=store.get(reviewed['source_snapshot']['source_id'],A['user_id'],APP)
        doc['analysis_version']=2
        with store.db() as db:store._save(db,doc)
        with pytest.raises(BatchValidationError,match='video_touch_review_required'):execution._source(reviewed)


def test_decision_is_owner_scoped_and_compare_and_swap(env,tmp_path,monkeypatch):
    from test_ui_case_draft_service import B
    from task_server.services.ui_case_draft_store import DraftConflict
    draft,store,folder=real_video_draft(env,tmp_path,monkeypatch)
    body={'revision':1,'index':0,'frame_id':'frame-001','disposition':'not_action','step_id':''}
    with actor_context(B):
        with pytest.raises(KeyError):service.resolve_touch_ambiguity(B,draft['draft_id'],body)
    with actor_context(A):
        service.resolve_touch_ambiguity(A,draft['draft_id'],body)
        with pytest.raises(DraftConflict):service.resolve_touch_ambiguity(A,draft['draft_id'],body)


@pytest.mark.parametrize('bad',[None,{},'bad',[None]])
def test_invalid_touch_evidence_never_releases_gate(bad):
    from task_server.services.ui_video_touch_review import touch_review_blocked
    assert touch_review_blocked({'source_snapshot':{'touch_ambiguities':bad}})


def test_probe_evidence_redacts_secrets_discovered_after_probe(monkeypatch,tmp_path):
    import base64
    from task_server.services import ui_video_windows as windows, ui_video_touch_detail as detail
    from task_server.services import ui_video_touch_targets as targets, ai_skill_service as ai
    frames=[{'frame_id':'frame-001','timestamp_ms':0},{'frame_id':'frame-002','timestamp_ms':100}]
    monkeypatch.setattr(windows,'load_assets',lambda fs,*a:[{'name':f['frame_id'],'timestamp_ms':f['timestamp_ms'],'mime':'image/jpeg','base64':base64.b64encode(b'x').decode()} for f in fs])
    monkeypatch.setattr(detail,'append_details',lambda *a:[])
    monkeypatch.setattr(targets,'probe',lambda *a:[{'frame_id':'frame-001','target':'secret-123','observation':'secret-123','questions':['secret-123'],'requires_review':True}])
    monkeypatch.setattr(ai,'run_ai_skill',lambda *a,**k:{'events':[{'action':'aiInput','target':'输入框','value':'secret-123','frame_refs':['frame-002'],'observation':'输入文字','uncertainties':[]}], 'uncertainties':[],'multiple_scenarios':False})
    result,_=windows.observe_windows(frames,200,tmp_path,lambda:False)
    assert 'secret-123' not in str(result['touch_ambiguities'])


def test_earlier_probe_is_redacted_again_when_later_window_discovers_input(monkeypatch,tmp_path):
    import base64
    from task_server.services import ui_video_windows as windows, ui_video_touch_detail as detail
    from task_server.services import ui_video_touch_targets as targets, ai_skill_service as ai
    frames=[{'frame_id':f'frame-{i:03d}','timestamp_ms':i*100} for i in range(1,63)]
    monkeypatch.setattr(windows,'load_assets',lambda fs,*a:[{'name':f['frame_id'],'timestamp_ms':f['timestamp_ms'],'mime':'image/jpeg','base64':base64.b64encode(b'x').decode()} for f in fs])
    monkeypatch.setattr(detail,'append_details',lambda *a:[])
    count={'probe':0,'observe':0}
    def probe(*args):
        count['probe']+=1
        return [{'frame_id':'frame-001','target':'secret-123','observation':'secret-123','questions':['secret-123'],'requires_review':True}] if count['probe']==1 else []
    def model(*args,**kw):
        count['observe']+=1
        event={'action':'aiInput','target':'输入框','value':'secret-123','frame_refs':['frame-027'],'observation':'输入文字','uncertainties':[]}
        return {'events':[event] if count['observe']==2 else [],'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(targets,'probe',probe);monkeypatch.setattr(ai,'run_ai_skill',model)
    result,_=windows.observe_windows(frames,7000,tmp_path,lambda:False)
    assert 'secret-123' not in str(result['touch_ambiguities'])


def test_planning_secrets_redact_first_pass_touch_evidence(monkeypatch,tmp_path):
    from task_server.services import ui_video_analysis as analysis, ui_video_windows as windows
    from task_server.services import ui_video_reconcile as reconcile, ui_video_delayed_response as delayed
    first={'frame_id':'frame-001','frame_sha256':'a'*64,'reason':'no_match','phase':'observe','target':'secret-123'}
    monkeypatch.setattr(windows,'observe_windows',lambda *a,**k:({'events':[],'uncertainties':[],'multiple_scenarios':False,'touch_ambiguities':[first]}, []))
    monkeypatch.setattr(reconcile,'needs_reconciliation',lambda *a:False)
    monkeypatch.setattr(delayed,'review_pending_responses',lambda observed,*a:observed)
    def consolidate(*args,**kwargs):
        kwargs['touch_secret_sink'].append('secret-123')
        return {'events':[],'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(analysis,'consolidate_observed_frames',consolidate)
    doc={'media':{'frames':[{'frame_id':'frame-001','timestamp_ms':0},{'frame_id':'frame-002','timestamp_ms':100}],'duration_ms':200}}
    result=analysis.analyze_whole_frames(doc,tmp_path,lambda:False)
    assert 'secret-123' not in str(result['touch_ambiguities'])


@pytest.mark.parametrize('mutation',[
    lambda d: d['source_snapshot'].update(app_id='com.other'),
    lambda d: d['source_snapshot'].update(source_id='uvs_'+'b'*32),
    lambda d: d['source_snapshot'].update(analysis_version=2),
    lambda d: d['source_snapshot']['touch_ambiguities'][0].update(frame_id='frame-002'),
    lambda d: d['source_snapshot']['touch_ambiguities'][0].update(frame_sha256='b'*64),
    lambda d: d['touch_decisions']['0'].update(actor_user_id='u-b'),
    lambda d: d['steps'][0].update(target='已篡改'),
])
def test_decision_cannot_be_reused_for_other_identity_frame_or_contents(env,tmp_path,monkeypatch,mutation):
    draft,store,folder=real_video_draft(env,tmp_path,monkeypatch)
    with actor_context(A):
        reviewed=service.resolve_touch_ambiguity(A,draft['draft_id'],
            {'revision':1,'index':0,'frame_id':'frame-001','disposition':'not_action','step_id':''})
    altered=copy.deepcopy(reviewed);mutation(altered)
    with pytest.raises(BatchValidationError,match='video_touch_review_required'):execution._source(altered)


def test_restore_cannot_restore_old_touch_decision(env,tmp_path,monkeypatch):
    draft,store,folder=real_video_draft(env,tmp_path,monkeypatch)
    with actor_context(A):
        reviewed=service.resolve_touch_ambiguity(A,draft['draft_id'],
            {'revision':1,'index':0,'frame_id':'frame-001','disposition':'not_action','step_id':''})
        edited=service.update_draft(A,draft['draft_id'],{**confirm(reviewed),'title':'变更'})
        restored=service.restore_draft(A,draft['draft_id'],{'revision':edited['revision']})
        assert 'touch_decisions' not in restored
        with pytest.raises(BatchValidationError,match='video_touch_review_required'):execution._source(restored)


def test_missing_tap_cannot_be_resolved_using_a_manual_assertion(env,tmp_path,monkeypatch):
    draft,store,folder=real_video_draft(env,tmp_path,monkeypatch)
    assertion=next(step for step in draft['steps'] if step['action']=='aiAssert')
    body={'revision':1,'index':0,'frame_id':'frame-001','disposition':'manual_step','step_id':assertion['step_id']}
    with actor_context(A):
        with pytest.raises(ValueError,match='video_touch_decision_invalid'):
            service.resolve_touch_ambiguity(A,draft['draft_id'],body)
    assert service._store().get(draft['draft_id'],A['user_id'])['revision']==1


def test_one_manual_tap_cannot_dispose_two_distinct_ambiguous_touches(env,tmp_path,monkeypatch):
    first,store,folder=real_video_draft(env,tmp_path,monkeypatch)
    source_id=first['source_snapshot']['source_id']
    source=store.get(source_id,A['user_id'],APP)
    second=copy.deepcopy(source['touch_ambiguities'][0]);second['questions']=['是否另一次点击']
    source['touch_ambiguities'].append(second)
    with store.db() as db:store._save(db,source)
    snapshot=copy.deepcopy(first['source_snapshot']);snapshot['snapshot_id']='two-evidence';snapshot['touch_ambiguities'].append(second)
    content={k:copy.deepcopy(first[k]) for k in ('title','preconditions','steps','questions','yaml_content','ready')}
    draft=service._store().create(A,APP,'来源：录屏',recording_snapshot=snapshot,recording_content=content)
    step=next(s for s in draft['steps'] if s['action']=='aiTap')
    with actor_context(A):
        with pytest.raises(ValueError,match='video_touch_same_frame_ambiguous'):
            service.resolve_touch_ambiguity(A,draft['draft_id'],
                {'revision':1,'index':0,'frame_id':'frame-001','disposition':'manual_step','step_id':step['step_id']})


def test_same_frame_reviews_cannot_be_given_opposing_decisions(env,tmp_path,monkeypatch):
    first,store,folder=real_video_draft(env,tmp_path,monkeypatch)
    source=store.get(first['source_snapshot']['source_id'],A['user_id'],APP)
    second=copy.deepcopy(source['touch_ambiguities'][0]);second['phase']='review'
    source['touch_ambiguities'].append(second)
    with store.db() as db:store._save(db,source)
    snapshot=copy.deepcopy(first['source_snapshot']);snapshot['snapshot_id']='same-frame';snapshot['touch_ambiguities'].append(second)
    content={k:copy.deepcopy(first[k]) for k in ('title','preconditions','steps','questions','yaml_content','ready')}
    draft=service._store().create(A,APP,'来源：录屏',recording_snapshot=snapshot,recording_content=content)
    step=next(s for s in draft['steps'] if s['action']=='aiTap')
    with actor_context(A):
        reviewed=service.resolve_touch_ambiguity(A,draft['draft_id'],
            {'revision':1,'index':0,'frame_id':'frame-001','disposition':'not_action','step_id':''})
        with pytest.raises(ValueError,match='video_touch_decision_invalid'):
            service.resolve_touch_ambiguity(A,draft['draft_id'],
                {'revision':reviewed['revision'],'index':1,'frame_id':'frame-001','disposition':'manual_step','step_id':step['step_id']})


def test_pending_hint_then_later_binding_failure_never_retries_away_first_events(monkeypatch,tmp_path):
    import base64
    from task_server.services import ui_video_windows as windows, ui_video_touch_detail as detail
    from task_server.services import ui_video_touch_targets as targets, ai_skill_service as ai
    frames=[{'frame_id':'frame-001','timestamp_ms':0},{'frame_id':'frame-002','timestamp_ms':100}]
    monkeypatch.setattr(windows,'load_assets',lambda fs,*a:[{'name':f['frame_id'],'timestamp_ms':f['timestamp_ms'],'mime':'image/jpeg','base64':base64.b64encode(b'x').decode()} for f in fs])
    monkeypatch.setattr(detail,'append_details',lambda *a:[])
    monkeypatch.setattr(targets,'probe',lambda *a:[{'frame_id':'frame-001','target':'疑问','questions':[],'requires_review':True}, {'frame_id':'frame-002','target':'按钮','questions':[],'requires_review':True}])
    calls=[]
    def model(*a,**k):
        calls.append(1)
        return {'events':[{'action':'aiTap','target':'按钮','value':'','frame_refs':['frame-002'],'observation':'页面','uncertainties':[f'问题{i}' for i in range(10)]}], 'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    with pytest.raises(ValueError,match='video_model_invalid'):windows.observe_windows(frames,200,tmp_path,lambda:False)
    assert len(calls)==1


def test_decision_rejects_retyping_original_tap_despite_unchanged_source_id(env,tmp_path,monkeypatch):
    first,store,folder=real_video_draft(env,tmp_path,monkeypatch)
    snapshot=copy.deepcopy(first['source_snapshot']);snapshot['snapshot_id']='projection-test'
    content={k:copy.deepcopy(first[k]) for k in ('title','preconditions','steps','questions','yaml_content','ready')}
    tap=content['steps'][0];tap['source_ref']=f"video:{snapshot['source_id']}:1:0"
    snapshot['events']=[{'draft_step_id':tap['step_id']}]
    snapshot['touch_source_projection']=[{k:copy.deepcopy(tap[k]) for k in ('step_id','source_ref','action','target','value')}]
    draft=service._store().create(A,APP,'来源：录屏',recording_snapshot=snapshot,recording_content=content)
    edited_body=confirm(draft);edited_body['steps'][0]['action']='aiAssert'
    with actor_context(A):
        edited=service.update_draft(A,draft['draft_id'],edited_body)
        with pytest.raises(ValueError,match='video_touch_decision_invalid'):
            service.resolve_touch_ambiguity(A,draft['draft_id'],
                {'revision':edited['revision'],'index':0,'frame_id':'frame-001','disposition':'not_action','step_id':''})


def test_versioned_frame_directory_is_used_for_decision(env,tmp_path,monkeypatch):
    import shutil
    draft,store,folder=real_video_draft(env,tmp_path,monkeypatch)
    source=store.get(draft['source_snapshot']['source_id'],A['user_id'],APP)
    workspace=folder.parent/'analysis-v1';workspace.mkdir()
    shutil.move(str(folder),str(workspace/'frames'))
    source['analysis_workspace_version']=1
    with store.db() as db:store._save(db,source)
    with actor_context(A):
        reviewed=service.resolve_touch_ambiguity(A,draft['draft_id'],
            {'revision':1,'index':0,'frame_id':'frame-001','disposition':'not_action','step_id':''})
    assert not execution._source(reviewed)['questions']


def test_print_recovery_projection_uses_final_steps_without_removed_confirmation():
    from task_server.services.ui_video_analysis import draft_proposal
    from test_ui_video_question_length_contract import shaped
    doc=shaped(True);doc['print_recovery']={'submit':0,'cancel':1,'confirm':2}
    doc['touch_ambiguities']=[{'frame_id':doc['media']['frames'][0]['frame_id'],'frame_sha256':'a'*64,
                               'reason':'no_match','phase':'observe','questions':['需核对']}]
    proposal,snapshot=draft_proposal(doc)
    from task_server.services.ui_video_touch_review import source_projection_matches
    assert any(s['action']=='submitPrint' for s in snapshot['touch_source_projection'])
    assert any(s['action']=='cancelPrint' for s in snapshot['touch_source_projection'])
    assert snapshot['events'][2]['draft_step_id'] not in [s['step_id'] for s in snapshot['touch_source_projection']]
    assert source_projection_matches({'source_snapshot':snapshot,'steps':proposal['steps']})


def test_print_recovery_confirmation_fold_does_not_block_touch_decision(env,tmp_path,monkeypatch):
    from task_server.services.ui_video_analysis import draft_proposal
    from test_ui_video_question_length_contract import shaped
    initial,store,folder=real_video_draft(env,tmp_path,monkeypatch)
    source=store.get(initial['source_snapshot']['source_id'],A['user_id'],APP)
    doc=shaped(True);doc.update(source_id=source['source_id'],analysis_version=1,app_id=APP)
    doc['print_recovery']={'submit':0,'cancel':1,'confirm':2}
    doc['touch_ambiguities']=source['touch_ambiguities'];source['media']=doc['media']
    with store.db() as db:store._save(db,source)
    proposal,snapshot=draft_proposal(doc)
    draft=service._store().create(A,APP,'来源：录屏',recording_snapshot=snapshot,
        recording_content={**proposal,'yaml_content':'','ready':False})
    with actor_context(A):
        reviewed=service.resolve_touch_ambiguity(A,draft['draft_id'],
            {'revision':1,'index':0,'frame_id':'frame-001','disposition':'not_action','step_id':''})
    assert reviewed['revision']==2


def test_manual_tap_must_follow_and_precede_source_events_by_frame_time(env,tmp_path,monkeypatch):
    first,store,folder=real_video_draft(env,tmp_path,monkeypatch)
    snapshot=copy.deepcopy(first['source_snapshot']);snapshot['snapshot_id']='ordered-video'
    content={k:copy.deepcopy(first[k]) for k in ('title','preconditions','steps','questions','yaml_content','ready')}
    manual=content['steps'][0];before=copy.deepcopy(content['steps'][1]);after=copy.deepcopy(content['steps'][1])
    before.update(step_id='11111111-1111-4111-8111-111111111111',source_ref=f"video:{snapshot['source_id']}:1:0")
    after.update(step_id='22222222-2222-4222-8222-222222222222',source_ref=f"video:{snapshot['source_id']}:1:1")
    content['steps']=[manual,before,after]
    snapshot['events']=[{'start_ms':10,'end_ms':50},{'start_ms':150,'end_ms':200}]
    from task_server.services.ui_video_touch_review import source_projection
    snapshot['touch_source_projection']=source_projection(content['steps'])
    draft=service._store().create(A,APP,'来源：录屏',recording_snapshot=snapshot,recording_content=content)
    with actor_context(A):
        with pytest.raises(ValueError,match='video_touch_decision_invalid'):
            service.resolve_touch_ambiguity(A,draft['draft_id'],
                {'revision':1,'index':0,'frame_id':'frame-001','disposition':'manual_step','step_id':manual['step_id']})


def test_update_moving_manual_tap_before_recorded_action_still_blocks_decision(env,tmp_path,monkeypatch):
    first,store,folder=real_video_draft(env,tmp_path,monkeypatch)
    snapshot=copy.deepcopy(first['source_snapshot']);snapshot['snapshot_id']='ordered-update'
    content={k:copy.deepcopy(first[k]) for k in ('title','preconditions','steps','questions','yaml_content','ready')}
    manual=content['steps'][0];before=copy.deepcopy(content['steps'][1]);after=copy.deepcopy(content['steps'][1])
    before.update(step_id='11111111-1111-4111-8111-111111111111',source_ref=f"video:{snapshot['source_id']}:1:0")
    after.update(step_id='22222222-2222-4222-8222-222222222222',source_ref=f"video:{snapshot['source_id']}:1:1")
    content['steps']=[before,manual,after]
    snapshot['events']=[{'start_ms':10,'end_ms':50,'draft_step_id':before['step_id']},
                        {'start_ms':150,'end_ms':200,'draft_step_id':after['step_id']}]
    from task_server.services.ui_video_touch_review import source_projection
    snapshot['touch_source_projection']=source_projection(content['steps'])
    draft=service._store().create(A,APP,'来源：录屏',recording_snapshot=snapshot,recording_content=content)
    with actor_context(A):
        body=confirm(draft);body['steps'][0],body['steps'][1]=body['steps'][1],body['steps'][0]
        edited=service.update_draft(A,draft['draft_id'],body)
        with pytest.raises(ValueError,match='video_touch_decision_invalid'):
            service.resolve_touch_ambiguity(A,draft['draft_id'],
                {'revision':edited['revision'],'index':0,'frame_id':'frame-001','disposition':'manual_step','step_id':manual['step_id']})
        gate=service.validate_draft(A,draft['draft_id'],{'revision':edited['revision'],'target':TARGET})
        assert not gate['ready'] and any(b['code']=='video_touch_review_required' for b in gate['blockers'])


def test_two_manual_taps_cannot_reverse_source_frame_order(env,tmp_path,monkeypatch):
    import hashlib
    first,store,folder=real_video_draft(env,tmp_path,monkeypatch)
    source=store.get(first['source_snapshot']['source_id'],A['user_id'],APP)
    second=copy.deepcopy(source['touch_ambiguities'][0]);second['frame_id']='frame-002'
    second['frame_sha256']=hashlib.sha256(b'frame two').hexdigest()
    (folder/'frame-002.jpg').write_bytes(b'frame two')
    source['touch_ambiguities'].append(second);source['media']['frames'].append({'frame_id':'frame-002','timestamp_ms':200})
    with store.db() as db:store._save(db,source)
    snapshot=copy.deepcopy(first['source_snapshot']);snapshot.update(snapshot_id='two-times',touch_ambiguities=source['touch_ambiguities'],frames=source['media']['frames'])
    content={k:copy.deepcopy(first[k]) for k in ('title','preconditions','steps','questions','yaml_content','ready')}
    first_tap=content['steps'][0];second_tap=copy.deepcopy(first_tap);second_tap['step_id']='33333333-3333-4333-8333-333333333333'
    content['steps']=[first_tap,second_tap,content['steps'][1]]
    draft=service._store().create(A,APP,'来源：录屏',recording_snapshot=snapshot,recording_content=content)
    with actor_context(A):
        reviewed=service.resolve_touch_ambiguity(A,draft['draft_id'],
            {'revision':1,'index':1,'frame_id':'frame-002','disposition':'manual_step','step_id':first_tap['step_id']})
        with pytest.raises(ValueError,match='video_touch_decision_invalid'):
            service.resolve_touch_ambiguity(A,draft['draft_id'],
                {'revision':reviewed['revision'],'index':0,'frame_id':'frame-001','disposition':'manual_step','step_id':second_tap['step_id']})


def test_manual_tap_inside_compound_cancel_wait_is_not_reordered():
    from task_server.services.ui_video_touch_review import manual_position_valid
    source='uvs_'+'a'*32
    doc={'source_snapshot':{'source_id':source,'analysis_version':1,
         'frames':[{'frame_id':'frame-001','timestamp_ms':20}],
         'events':[{'action':'aiTap','start_ms':0,'end_ms':1},
                   {'action':'aiTap','start_ms':10,'end_ms':10},
                   {'action':'aiTap','start_ms':30,'end_ms':30}],
         'print_recovery':{'submit':0,'cancel':1,'confirm':2}},
         'steps':[{'step_id':'cancel','source_ref':f'video:{source}:1:1','action':'cancelPrint'},
                  {'step_id':'manual','source_ref':'text','action':'aiTap'}]}
    assert not manual_position_valid(doc,'frame-001','manual')


def test_manual_decisions_require_strict_time_and_step_order():
    from task_server.services.ui_video_touch_review import manual_decisions_order_valid
    doc={'source_snapshot':{'frames':[{'frame_id':'frame-001','timestamp_ms':10},
                                     {'frame_id':'frame-002','timestamp_ms':20}]},
         'steps':[{'step_id':'a'},{'step_id':'b'}]}
    decisions={'0':{'frame_id':'frame-001','step_id':'a','disposition':'manual_step'},
               '1':{'frame_id':'frame-002','step_id':'b','disposition':'manual_step'}}
    assert manual_decisions_order_valid(doc,decisions)
    decisions['0']['step_id'],decisions['1']['step_id']='b','a'
    assert not manual_decisions_order_valid(doc,decisions)
    decisions['0']['step_id'],decisions['1']['step_id']='a','b'
    doc['source_snapshot']['frames'][1]['timestamp_ms']=10
    assert not manual_decisions_order_valid(doc,decisions)


def test_review_frame_returns_exact_hashed_bytes_and_rejects_tampering(env,tmp_path,monkeypatch):
    draft,store,folder=real_video_draft(env,tmp_path,monkeypatch)
    with actor_context(A):
        assert service.get_touch_frame(A,draft['draft_id'],1,0)==b'exact original bytes'
        (folder/'frame-001.jpg').write_bytes(b'tampered')
        with pytest.raises(ValueError):service.get_touch_frame(A,draft['draft_id'],1,0)


def test_review_frame_rechecks_owner_revision_and_index(env,tmp_path,monkeypatch):
    from test_ui_case_draft_service import B
    from task_server.services.ui_case_draft_store import DraftConflict
    draft,store,folder=real_video_draft(env,tmp_path,monkeypatch)
    with actor_context(B):
        with pytest.raises(KeyError):service.get_touch_frame(B,draft['draft_id'],1,0)
    with actor_context(A):
        with pytest.raises(DraftConflict):service.get_touch_frame(A,draft['draft_id'],2,0)
        with pytest.raises(ValueError):service.get_touch_frame(A,draft['draft_id'],1,1)


def test_source_projection_allows_input_and_expected_but_not_semantic_changes():
    from task_server.services.ui_video_touch_review import source_projection,source_projection_matches
    source_step={'step_id':'a','source_ref':'video:source:1:0','action':'aiInput','target':'搜索框',
                 'value':'','expected':'','review_state':'needs_review'}
    snapshot={'touch_source_projection':source_projection([source_step])}
    draft={'source_snapshot':snapshot,'steps':[copy.deepcopy(source_step)]}
    draft['steps'][0].update(value='测试数据',expected='搜索结果显示',review_state='confirmed')
    assert source_projection_matches(draft)
    draft['steps'][0]['target']='别的输入框'
    assert not source_projection_matches(draft)


def test_manual_tap_during_compound_submit_binding_is_not_reordered():
    from task_server.services.ui_video_touch_review import manual_position_valid
    source='uvs_'+'a'*32
    doc={'source_snapshot':{'source_id':source,'analysis_version':1,
         'frames':[{'frame_id':'frame-001','timestamp_ms':5}],
         'events':[{'action':'aiTap','start_ms':0,'end_ms':1},
                   {'action':'aiTap','start_ms':10,'end_ms':10},
                   {'action':'aiTap','start_ms':30,'end_ms':30}],
         'print_recovery':{'submit':0,'cancel':1,'confirm':2}},
         'steps':[{'step_id':'submit','source_ref':f'video:{source}:1:0','action':'submitPrint'},
                  {'step_id':'manual','source_ref':'text','action':'aiTap'},
                  {'step_id':'cancel','source_ref':f'video:{source}:1:1','action':'cancelPrint'}]}
    assert not manual_position_valid(doc,'frame-001','manual')
