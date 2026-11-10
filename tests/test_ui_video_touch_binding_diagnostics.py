"""Private bounded metadata for unbound uncertain touch hints."""
import copy
import json
import pytest
from task_server.services import ui_video_touch_targets as targets
from task_server.workers import ui_video_worker as worker
from test_ui_case_draft_service import env

Q='触点目标或页面变化存在疑问，请对照原视频核对'
FRAME='frame-001'

def event(questions=(),action='aiTap'):
    return {'action':action,'target':'PRIVATE_TARGET','frame_refs':[FRAME],
            'uncertainties':list(questions)}

def hint(action=None):
    return {'frame_id':FRAME,'target':'PRIVATE_HINT','observation':'PRIVATE_OBSERVATION',
            'questions':['PRIVATE_QUESTION'],'requires_review':True,**({'action':action} if action else {})}

@pytest.mark.parametrize('events,reason,counts',[
    ([], 'no_match', []),
    ([event(),event()], 'multiple_matches', [0,0]),
    ([event([str(i) for i in range(10)])], 'question_capacity', [10]),
])
def test_binding_failures_report_only_bounded_metadata(events,reason,counts):
    before=copy.deepcopy(events)
    with pytest.raises(ValueError,match='video_model_invalid') as caught:
        targets.bind_uncertain_hints(events,[hint()])
    assert events==before
    assert caught.value.touch_binding_evidence=={'reason':reason,'frame_id':FRAME,
        'match_count':len(counts),'event_question_counts':counts}
    assert 'PRIVATE' not in json.dumps(caught.value.touch_binding_evidence)

@pytest.mark.parametrize('case',['tap','conflicting_back','same_back'])
def test_full_question_list_is_not_waived_for_preexisting_generic_text(case):
    if case=='tap':
        item=event([Q]+[str(i) for i in range(9)]);probe=hint()
    elif case=='conflicting_back':
        question='同帧局部复核与主观察动作判断冲突，请核对完整原帧；无法确认时保留疑问，不删除或替换动作。'
        item=event([question]+[str(i) for i in range(9)]);probe=hint('navigateBack')
    else:
        question='局部原帧复核对同一动作仍有疑问，请核对原帧；不确定时保留问题。'
        item=event([question]+[str(i) for i in range(9)],action='navigateBack');probe=hint('navigateBack')
    before=copy.deepcopy(item)
    with pytest.raises(ValueError,match='video_model_invalid'):targets.bind_uncertain_hints([item],[probe])
    assert item==before


def test_last_window_metadata_is_retained_only_in_source_diagnostic(monkeypatch,tmp_path,caplog):
    from task_server.services import ui_video_windows as windows, ui_video_touch_detail as detail, ai_skill_service as ai
    frames=[{'frame_id':FRAME,'timestamp_ms':0},{'frame_id':'frame-002','timestamp_ms':100}]
    monkeypatch.setattr(windows,'load_assets',lambda *a:[])
    monkeypatch.setattr(detail,'append_details',lambda *a:[])
    monkeypatch.setattr(targets,'probe',lambda *a:[hint()])
    monkeypatch.setattr(ai,'run_ai_skill',lambda *a,**kw:{'events':[],'uncertainties':[], 'multiple_scenarios':False})
    with pytest.raises(ValueError) as caught:
        windows.observe_windows(frames,200,tmp_path,lambda:False,review_events=[])
    doc={'media':{'frames':frames}}
    expected={'reason':'no_match','frame_id':FRAME,'match_count':0,'event_question_counts':[],
              'phase':'review','window_index':1,'window_count':1,'attempt':2}
    assert worker._private_target_failure(caught.value,'video_model_invalid',doc)=={'touch_binding_evidence':expected}
    worker._log_failure('uvs_'+'a'*32,'analysis',caught.value,'video_model_invalid')
    assert 'PRIVATE' not in caplog.text and 'touch_binding_evidence' not in caplog.text


@pytest.mark.parametrize('patch',[
    {'frame_id':'frame-999'}, {'reason':'PRIVATE'}, {'event_question_counts':['PRIVATE']},
    {'match_count':True}, {'phase':'PRIVATE'}, {'window_index':2}, {'attempt':3},
    {'event_question_counts':[0]}, {'reason':'multiple_matches'}, {'extra':'PRIVATE'},
])
def test_private_diagnostic_rejects_unverified_or_malformed_data(patch):
    e=ValueError('video_model_invalid');e.touch_binding_evidence={'reason':'no_match','frame_id':FRAME,
        'match_count':0,'event_question_counts':[],'phase':'observe','window_index':1,'window_count':1,'attempt':1}
    e.touch_binding_evidence.update(patch)
    assert worker._private_target_failure(e,'video_model_invalid',{'media':{'frames':[{'frame_id':FRAME,'timestamp_ms':0},{'frame_id':'frame-002','timestamp_ms':100}]}})=={}


def test_multiple_back_candidates_do_not_partially_mutate_on_capacity_error():
    question='同帧局部复核与主观察动作判断冲突，请核对完整原帧；无法确认时保留疑问，不删除或替换动作。'
    items=[event([question]+[str(i) for i in range(9)]),event([str(i) for i in range(10)])]
    before=copy.deepcopy(items)
    with pytest.raises(ValueError,match='video_model_invalid'):targets.bind_uncertain_hints(items,[hint('navigateBack')])
    assert items==before


def test_failure_stays_owner_app_version_scoped_and_retry_clears(env,tmp_path,monkeypatch):
    import io
    from test_ui_case_draft_service import A,B,APP
    from task_server.services import ui_video_source_service as video, ui_video_analysis as analysis, ui_video_runtime as runtime
    from task_server.services.operation_attribution import actor_context
    store=video.VideoStore(tmp_path/'videos');monkeypatch.setattr(video,'service_store',lambda:store)
    doc=store.upload(A,APP,'clip.mp4',io.BytesIO(b'mp4'),3,actor=A)
    frames=[{'frame_id':FRAME,'timestamp_ms':0},{'frame_id':'frame-002','timestamp_ms':100}]
    monkeypatch.setattr(worker,'decode_video',lambda *a:{'frames':frames,'duration_ms':200,'black':False,'static':False})
    monkeypatch.setattr(runtime,'selected',lambda *a:False)
    expected={'reason':'multiple_matches','frame_id':FRAME,'match_count':2,'event_question_counts':[0,10],
              'phase':'observe','window_index':1,'window_count':1,'attempt':2}
    def fail(*a):
        error=ValueError('video_model_invalid');error.touch_binding_evidence=expected;raise error
    monkeypatch.setattr(analysis,'analyze_whole_frames',fail)
    worker.analyze_video_source(store,doc['source_id'])
    with actor_context(A):
        _,_,saved=video.owned_source(A,doc['source_id'],APP)
        assert video.public_source(saved)['failure_diagnostic']=={'touch_binding_evidence':expected}
        with pytest.raises(ValueError,match='video_not_ready'):video.import_video_draft(A,doc['source_id'],APP)
    with actor_context(B):
        with pytest.raises(KeyError):video.owned_source(B,doc['source_id'],APP)
    with pytest.raises(KeyError):store.get(doc['source_id'],A['user_id'],'com.foreign.app')
    retried=store.retry(doc['source_id'],A['user_id'],APP)
    assert retried['analysis_version']==2 and 'failure_diagnostic' not in retried
    assert store.transition(doc['source_id'],{'queued'},'failed',expected_version=1,
        expected_owner=A['user_id'],expected_app=APP,failure_diagnostic={'touch_binding_evidence':expected}) is None


def test_fifteenth_window_reports_current_window_not_completed_count(monkeypatch,tmp_path):
    from task_server.services import ui_video_windows as windows, ui_video_touch_detail as detail, ai_skill_service as ai
    frames=[{'frame_id':f'frame-{i:03d}','timestamp_ms':i*100} for i in range(1,389)]
    monkeypatch.setattr(windows,'load_assets',lambda *a:[])
    monkeypatch.setattr(detail,'append_details',lambda *a:[])
    progress=[]
    monkeypatch.setattr(targets,'probe',lambda fs,*a:[{**hint(),'frame_id':'frame-388'}] if progress[-1][1]==14 else [])
    monkeypatch.setattr(ai,'run_ai_skill',lambda *a,**kw:{'events':[],'uncertainties':[], 'multiple_scenarios':False})
    with pytest.raises(ValueError) as caught:
        windows.observe_windows(frames,39000,tmp_path,lambda:False,lambda phase,done,total,events:progress.append((phase,done,total)))
    assert hasattr(caught.value,'touch_binding_evidence'), (repr(caught.value),progress[-1])
    private=worker._private_target_failure(caught.value,'video_model_invalid',{'media':{'frames':frames}})
    assert private, (caught.value.touch_binding_evidence,progress[-1])
    evidence=private['touch_binding_evidence']
    assert progress[-1]==('observing',14,15)
    assert (evidence['frame_id'],evidence['phase'],evidence['window_index'],evidence['window_count'],evidence['attempt'])==('frame-388','observe',15,15,2)


def test_same_generic_text_does_not_resolve_a_different_source_frame():
    item=event([Q]+[str(i) for i in range(9)])
    item['frame_refs']=[FRAME,'frame-002']
    before=copy.deepcopy(item)
    with pytest.raises(ValueError,match='video_model_invalid'):
        targets.bind_uncertain_hints([item],[{**hint(),'frame_id':'frame-002'}])
    assert item==before
