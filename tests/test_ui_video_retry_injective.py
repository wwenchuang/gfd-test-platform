"""Retries preserve every earlier observation without order-dependent matching."""
import copy
from task_server.services import ui_video_reconcile as reconcile
from task_server.workers.ui_video_worker import _private_target_failure


def event(refs,action='aiTap',target='PRIVATE_TARGET',value=''):
    return {'action':action,'target':target,'value':value,'frame_refs':refs}


def test_reordered_overlapping_hypotheses_have_complete_injective_matching():
    previous=[event(['frame-001']),event(['frame-001','frame-002'])]
    retry=[event(['frame-001','frame-002']),event(['frame-001'])]
    assert reconcile._retry_frame_unmatched(previous,retry) is None


def test_duplicates_cannot_reuse_one_retry_and_missing_frames_fail():
    previous=[event(['frame-001']),event(['frame-001'])]
    assert reconcile._retry_frame_unmatched(previous,[copy.deepcopy(previous[0])]) is not None
    assert reconcile._retry_frame_unmatched([event(['frame-001','frame-002'])],[event(['frame-001'])]) is not None


def test_action_target_and_value_must_remain_exact():
    previous=[event(['frame-001'])]
    for other in (event(['frame-001'],action='aiAssert'),event(['frame-001'],target='OTHER'),event(['frame-001'],value='OTHER')):
        assert reconcile._retry_frame_unmatched(previous,[other]) is not None


def test_private_match_diagnostic_reports_only_validated_safe_fields():
    error=ValueError('video_required_action_missing')
    error.missing_frame_refs={'frame-001'}
    error.retry_match_evidence={'action':'aiTap','frame_refs':['frame-001'],
        'previous_count':2,'candidate_count':1,'action_candidates':1,'target_candidates':1,'value_candidates':1,'frame_candidates':1}
    doc={'media':{'frames':[{'frame_id':'frame-001'}]}}
    result=_private_target_failure(error,'video_required_action_missing',doc)
    assert result['retry_match_evidence']==error.retry_match_evidence
    assert 'PRIVATE_TARGET' not in str(result)
    malformed=copy.deepcopy(error.retry_match_evidence);malformed['target']='PRIVATE_TARGET';error.retry_match_evidence=malformed
    assert 'retry_match_evidence' not in _private_target_failure(error,'video_required_action_missing',doc)


def test_repair_window_reordered_events_keeps_full_coverage(monkeypatch,tmp_path):
    from task_server.services import ui_video_windows as windows,ai_skill_service as ai
    from test_ui_video_coverage_resolution import fixture
    frames,old,new,claim=fixture()
    old[0].update(action='aiTap',target='确认',value='',frame_refs=['f1','f3'],end_ms=300,observation='原始假设',evidence_kind='inferred')
    old[1].update(target='确认',value='',frame_refs=['f2','f3','f4'],end_ms=400,observation='原始假设',evidence_kind='inferred')
    new[0].update(target='确认',value='',frame_refs=['f1','f2','f4'],end_ms=400,observation='点击确认',evidence_kind='inferred')
    claim.update(resolution='same_interaction',source_anchor_ids=['first_pass:0','first_pass:1','independent_review:0','independent_review:1'])
    claim['evidence'].update(interaction_frame='f2',after_frame='f4',examined_frame_refs=['f1','f2','f3','f4'])
    short={'action':'aiAssert','target':'PRIVATE_ASSERT','value':'','frame_refs':['f1'],'observation':'画面静止','uncertainties':[]}
    long={**copy.deepcopy(short),'frame_refs':['f1','f2']}
    monkeypatch.setattr(windows,'load_assets',lambda *a,**kw:[])
    calls=[]
    def model(skill,payload,**kwargs):
        calls.append(1)
        tap={k:v for k,v in new[0].items() if k not in {'start_ms','end_ms','evidence_kind'}}
        c=copy.deepcopy(claim)
        if len(calls)==2:
            tap['frame_refs']=['f1','f2','f3','f4'];c['evidence']['interaction_frame']='f3'
        return {'events':[tap,short,long] if len(calls)==1 else [tap,long,short],
                'coverage_resolutions':[c],'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    result=reconcile._repair_window(old,{'events':copy.deepcopy(old),'uncertainties':[],'multiple_scenarios':False},
        frames,1000,tmp_path,lambda:False,[],{'f1','f2','f3','f4'},attempt_limit=2,retry_return_gesture_only=True,motion_review_refs={'f2'})
    assert len(calls)==2 and len(result['events'])==3


def test_frame_end_is_a_bounded_private_diagnostic():
    error=ValueError('video_required_action_missing');error.missing_frame_refs={'frame-end'}
    error.retry_match_evidence={'action':'aiTap','frame_refs':['frame-end'],
        'previous_count':1,'candidate_count':0,'action_candidates':0,'target_candidates':0,'value_candidates':0,'frame_candidates':0}
    doc={'media':{'frames':[{'frame_id':'frame-end'}]}}
    result=_private_target_failure(error,'video_required_action_missing',doc)
    assert result=={'missing_frame_refs':['frame-end'],'retry_match_evidence':error.retry_match_evidence}
    error.retry_match_evidence['frame_refs']=['frame-secret']
    assert 'retry_match_evidence' not in _private_target_failure(error,'video_required_action_missing',doc)


def test_diagnostic_rejects_forged_fields_and_unbounded_values():
    import pytest
    original={'action':'aiTap','frame_refs':['frame-001'],'previous_count':2,'candidate_count':1,
              'action_candidates':1,'target_candidates':1,'value_candidates':1,'frame_candidates':1}
    doc={'media':{'frames':[{'frame_id':'frame-001'}]}}
    for bad in ({'action':'PRIVATE_ACTION'},{'frame_refs':['frame-999']},{'frame_refs':['frame-001','frame-001']},
                {'previous_count':True},{'candidate_count':121},{'frame_candidates':2},
                {'private':'SECRET'},{'target_candidates':0}):
        error=ValueError('video_required_action_missing');error.missing_frame_refs={'frame-001'}
        error.retry_match_evidence={**original,**bad}
        result=_private_target_failure(error,'video_required_action_missing',doc)
        assert 'retry_match_evidence' not in result and 'SECRET' not in str(result)


from test_ui_case_draft_service import env, A, APP

def test_retry_diagnostic_ordinary_worker_owner_scope_retry_and_version_cas(env,tmp_path,monkeypatch):
    import io,pytest
    from task_server.services import ui_video_source_service as video,ui_video_analysis as analysis
    from task_server.workers import ui_video_worker as worker
    store=video.VideoStore(tmp_path/'videos')
    doc=store.upload(A,APP,'private.mp4',io.BytesIO(b'video'),5,actor=A)
    monkeypatch.setattr(worker,'decode_video',lambda *a:{'black':False,'static':False,'frames':[{'frame_id':'frame-end','timestamp_ms':100}],'duration_ms':100})
    error=ValueError('video_required_action_missing');error.missing_frame_refs={'frame-end'}
    error.retry_match_evidence={'action':'aiTap','frame_refs':['frame-end'],'previous_count':2,'candidate_count':1,
        'action_candidates':1,'target_candidates':0,'value_candidates':0,'frame_candidates':0}
    def fail(*args):raise error
    monkeypatch.setattr(analysis,'analyze_whole_frames',fail)
    worker.analyze_video_source(store,doc['source_id'])
    saved=store.get(doc['source_id'],A['user_id'],APP)
    visible=video.public_source(saved)
    assert visible['failure_diagnostic']['retry_match_evidence']==error.retry_match_evidence
    with pytest.raises(KeyError):store.get(doc['source_id'],'foreign-user',APP)
    with pytest.raises(KeyError):store.get(doc['source_id'],A['user_id'],'foreign.app')
    retried=store.retry(doc['source_id'],A['user_id'],APP)
    assert 'failure_diagnostic' not in retried
    assert store.transition(doc['source_id'],{'queued'},'failed',expected_version=doc['analysis_version'],expected_owner=A['user_id'],expected_app=APP,failure_diagnostic={'private':'late'}) is None
    assert 'failure_diagnostic' not in store.get(doc['source_id'],A['user_id'],APP)
