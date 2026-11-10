"""Only bounded source-frame metadata survives a state-overlap failure."""
import copy
import json
import logging
import pytest

from task_server.services import ui_video_reconcile as reconcile
from task_server.workers import ui_video_worker as worker
from test_ui_case_draft_service import env


def fixture(shared):
    frames=[{'frame_id':f'frame-{i:03d}','timestamp_ms':i*100} for i in range(1,100)]
    events=[{'action':'aiTap','target':'PRIVATE_TARGET','value':'','frame_refs':[f'frame-{i:03d}'],
             'start_ms':i*100,'end_ms':i*100,'observation':'PRIVATE_OBSERVATION',
             'uncertainties':[],'evidence_kind':'inferred'} for i in range(5,76,10)]
    refs=['frame-004','frame-005' if shared else 'frame-006']
    old={'action':'aiAssert','target':'PRIVATE_OLD','value':'','frame_refs':refs,'start_ms':400,
         'end_ms':500 if shared else 600,'observation':'PRIVATE_OLD_TEXT','uncertainties':[], 'evidence_kind':'inferred'}
    new={'action':'aiWaitFor','target':'PRIVATE_NEW','value':'','frame_refs':['frame-005'],
         'start_ms':500,'end_ms':500,'observation':'PRIVATE_NEW_TEXT','uncertainties':[], 'evidence_kind':'inferred'}
    return frames,events,old,new


@pytest.mark.parametrize('shared',[True,False])
def test_repair_failure_records_only_first_bounded_state_pair_without_mutating(monkeypatch,tmp_path,shared):
    frames,events,old,new=fixture(shared);reviewed=[old]+copy.deepcopy(events)
    checked={'events':reviewed,'uncertainties':[],'multiple_scenarios':False};before=copy.deepcopy(checked);calls=[]
    def fake(original,local,group,*args,**kwargs):
        calls.append(1)
        return {'events':copy.deepcopy(local['events'])+[copy.deepcopy(new)],'uncertainties':[],
                'multiple_scenarios':False,'reconciliation_audit':{'coverage_resolutions':[],'model_attempts':1}}
    monkeypatch.setattr(reconcile,'_repair_window',fake)
    with pytest.raises(ValueError,match='video_required_action_missing') as caught:
        reconcile._repair_coverage(events,checked,frames,11000,tmp_path,lambda:False,[],{e['frame_refs'][0] for e in events})
    # One bounded re-observation is now permitted; this deliberately invalid
    # stub then fails in a later group. The original evidence must survive.
    assert calls==[1,1,1] and checked==before
    evidence=caught.value.state_overlap_evidence
    assert evidence['kind']==('shared_frame' if shared else 'time_overlap')
    assert evidence['group_index']==1 and evidence['group_count']==8
    assert evidence['group_frame_ids']==['frame-005']
    assert evidence['repaired']=={'event_index':1,'action':'aiWaitFor','frame_refs':['frame-005'],'start_ms':500,'end_ms':500}
    assert evidence['preserved']=={'event_index':0,'action':'aiAssert','frame_refs':old['frame_refs'],'start_ms':400,'end_ms':old['end_ms']}
    assert 'PRIVATE' not in json.dumps(evidence)
    result=worker._private_target_failure(caught.value,worker._failure_code(caught.value),{'media':{'frames':frames,'duration_ms':11000}})
    assert result['state_overlap_evidence']==evidence
    assert 'overlap_expanded_window_failure_kind' not in result


def sample_evidence():
    return {'kind':'time_overlap','group_index':1,'group_count':8,'group_frame_ids':['frame-005'],
            'repaired':{'event_index':1,'action':'aiWaitFor','frame_refs':['frame-005'],'start_ms':500,'end_ms':500},
            'preserved':{'event_index':0,'action':'aiAssert','frame_refs':['frame-004','frame-006'],'start_ms':400,'end_ms':600}}


@pytest.mark.parametrize('mutation',[
    'extra_root','extra_event','bad_kind','false_shared','unknown_frame','duplicate_frame','bad_action',
    'repaired_outside_group','preserved_inside_group','bad_time','times_do_not_overlap','too_many_frames',
    'bad_event_index','bool_index','bad_group_count','group_not_contiguous','wrong_code','missing_duration'])
def test_invalid_or_forged_state_overlap_never_persisted(mutation):
    frames,_,_,_=fixture(False);doc={'media':{'frames':frames,'duration_ms':11000}}
    error=ValueError('video_required_action_missing');e=error.state_overlap_evidence=sample_evidence()
    code='video_required_action_missing'
    if mutation=='extra_root':e['text']='PRIVATE'
    if mutation=='extra_event':e['repaired']['target']='PRIVATE'
    if mutation=='bad_kind':e['kind']='PRIVATE'
    if mutation=='false_shared':e['kind']='shared_frame'
    if mutation=='unknown_frame':e['repaired']['frame_refs']=['frame-999']
    if mutation=='duplicate_frame':e['preserved']['frame_refs']=['frame-004','frame-004']
    if mutation=='bad_action':e['preserved']['action']='aiTap'
    if mutation=='repaired_outside_group':e['repaired']['frame_refs']=['frame-006'];e['repaired']['start_ms']=600;e['repaired']['end_ms']=600
    if mutation=='preserved_inside_group':e['preserved']['frame_refs']=['frame-005'];e['preserved']['start_ms']=500;e['preserved']['end_ms']=500
    if mutation=='bad_time':e['repaired']['start_ms']=-1
    if mutation=='times_do_not_overlap':e['preserved']['end_ms']=450;e['preserved']['frame_refs']=['frame-004']
    if mutation=='too_many_frames':e['group_frame_ids']=[f'frame-{i:03d}' for i in range(1,42)]
    if mutation=='bad_event_index':e['preserved']['event_index']=120
    if mutation=='bool_index':e['group_index']=True
    if mutation=='bad_group_count':e['group_count']=9
    if mutation=='group_not_contiguous':e['group_frame_ids']=['frame-004','frame-006']
    if mutation=='wrong_code':code='video_model_invalid'
    if mutation=='missing_duration':doc['media'].pop('duration_ms')
    assert worker._private_target_failure(error,code,doc)=={}


def test_log_omits_error_evidence_even_when_target_is_private(caplog):
    error=ValueError('video_required_action_missing');error.state_overlap_evidence=sample_evidence()
    error.state_overlap_evidence['repaired']['target']='PRIVATE_MARKER'
    with caplog.at_level(logging.WARNING):worker._log_failure('uvs_'+'a'*32,'analysis',error,'video_required_action_missing')
    assert 'PRIVATE_MARKER' not in caplog.text and 'state_overlap_evidence' not in caplog.text


def test_owner_app_version_guard_and_retry_clears_state_overlap(env,monkeypatch,tmp_path):
    import io
    from test_ui_case_draft_service import A,B,APP
    from task_server.services import ui_video_source_service as source,ui_video_analysis as analysis,ui_video_runtime as runtime
    from task_server.services.operation_attribution import actor_context
    frames,_,_,_=fixture(False)
    store=source.VideoStore(tmp_path/'videos');monkeypatch.setattr(source,'service_store',lambda:store)
    doc=store.upload(A,APP,'clip.mp4',io.BytesIO(b'video'),5,actor=A)
    monkeypatch.setattr(worker,'decode_video',lambda *a:{'black':False,'static':False,'frames':frames,'duration_ms':11000})
    monkeypatch.setattr(runtime,'selected',lambda *a:False)
    def fail(*args):
        error=ValueError('video_required_action_missing');error.state_overlap_evidence=sample_evidence();raise error
    monkeypatch.setattr(analysis,'analyze_whole_frames',fail)
    worker.analyze_video_source(store,doc['source_id'])
    with actor_context(A):
        _,_,saved=source.owned_source(A,doc['source_id'],APP)
        assert saved['status']=='failed'
        assert source.public_source(saved)['failure_diagnostic']=={'state_overlap_evidence':sample_evidence()}
        with pytest.raises(ValueError,match='video_not_ready'):source.import_video_draft(A,doc['source_id'],APP)
    with actor_context(B):
        with pytest.raises(KeyError):source.owned_source(B,doc['source_id'],APP)
    with pytest.raises(KeyError):store.get(doc['source_id'],A['user_id'],'com.other')
    retried=store.retry(doc['source_id'],A['user_id'],APP)
    assert retried['analysis_version']==2 and 'failure_diagnostic' not in retried
    assert store.transition(doc['source_id'],{'queued'},'failed',expected_version=1,
        expected_owner=A['user_id'],expected_app=APP,failure_diagnostic={'state_overlap_evidence':sample_evidence()}) is None


def test_actual_overlap_exception_survives_worker_boundary(env,monkeypatch,tmp_path):
    import io
    from test_ui_case_draft_service import A,APP
    from task_server.services import ui_video_source_service as source,ui_video_analysis as analysis,ui_video_runtime as runtime
    frames,events,old,new=fixture(False)
    checked={'events':[old]+copy.deepcopy(events),'uncertainties':[],'multiple_scenarios':False}
    store=source.VideoStore(tmp_path/'videos');doc=store.upload(A,APP,'clip.mp4',io.BytesIO(b'video'),5,actor=A)
    monkeypatch.setattr(worker,'decode_video',lambda *a:{'black':False,'static':False,'frames':frames,'duration_ms':11000})
    monkeypatch.setattr(runtime,'selected',lambda *a:False)
    def repair(original,local,group,*args,**kwargs):
        return {'events':copy.deepcopy(local['events'])+[copy.deepcopy(new)],'uncertainties':[],
                'multiple_scenarios':False,'reconciliation_audit':{'coverage_resolutions':[],'model_attempts':1}}
    monkeypatch.setattr(reconcile,'_repair_window',repair)
    monkeypatch.setattr(analysis,'analyze_whole_frames',lambda *args:reconcile._repair_coverage(
        events,checked,frames,11000,tmp_path,lambda:False,[],{e['frame_refs'][0] for e in events}))
    worker.analyze_video_source(store,doc['source_id'])
    saved=store.get(doc['source_id'],A['user_id'],APP)
    assert saved['status']=='failed' and saved['error_code']=='video_required_action_missing'
    evidence=saved['failure_diagnostic']['state_overlap_evidence']
    assert evidence['kind']=='time_overlap' and evidence['repaired']['frame_refs']==['frame-005']
    assert evidence['preserved']['frame_refs']==['frame-004','frame-006']
    assert 'PRIVATE' not in json.dumps(evidence)
