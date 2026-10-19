import copy
import pytest
from task_server.services import ui_video_reconcile as vr


def event(i,action='aiTap'):
    return {'action':action,'target':'取消打印' if i==0 else '返回上一级','value':'',
            'frame_refs':[f'f{i}'], 'start_ms':i*100,'end_ms':i*100,
            'observation':'取消未完成' if i==0 else '页面变化','evidence_kind':'inferred','uncertainties':[]}


def test_review_rejects_silent_removal_but_allows_auto_transition_correction():
    original=[event(1),event(2)]
    corrected=copy.deepcopy(original);corrected[1]['action']='aiAssert'
    vr.check_coverage(original,corrected)
    with pytest.raises(ValueError,match='video_required_action_missing'):
        vr.check_coverage(original,corrected[:1])


def test_reconcile_uses_supplied_pixels_redacts_and_keeps_input_untouched(monkeypatch,tmp_path):
    from task_server.services import ai_skill_service as ai
    fs=[{'frame_id':f'f{i}','timestamp_ms':i*100} for i in range(3)]
    (tmp_path/'frames').mkdir()
    for f in fs:(tmp_path/'frames'/(f['frame_id']+'.jpg')).write_bytes(b'image')
    original=[event(0),event(1)];before=copy.deepcopy(original)
    def model(skill,payload,**kw):
        assert payload['review_mode']=='full_flow_after_recovery'
        assert [x['name'] for x in kw['image_assets']]==[f['frame_id'] for f in payload['frames']]
        return {'events':[{'action':e['action'],'target':e['target'],'value':'',
            'observation':'页面变化 secret','frame_refs':e['frame_refs']} for e in original],
            'multiple_scenarios':False,'uncertainties':[]}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    result=vr.reconcile(original,fs,300,tmp_path,lambda:False,['secret'])
    assert 'secret' not in str(result) and original==before
    with pytest.raises(ValueError,match='video_cancelled'):
        vr.reconcile(original,fs,300,tmp_path,lambda:True,[])


def test_reconciliation_is_limited_to_long_cancel_recovery():
    assert vr.needs_reconciliation([event(0)],[{}]*61)
    assert not vr.needs_reconciliation([event(0)],[{}]*60)
    assert not vr.needs_reconciliation([event(1)],[{}]*61)

@pytest.mark.parametrize('action',['aiInput','aiScroll'])
def test_review_cannot_replace_prerequisite_with_observation(action):
    old=event(1,action);new=event(1,'aiAssert')
    with pytest.raises(ValueError,match='video_required_action_missing'):
        vr.check_coverage([old],[new])


def test_one_review_event_cannot_cover_multiple_original_actions():
    new=event(1,'aiAssert');new['frame_refs']=['f1','f2']
    with pytest.raises(ValueError,match='video_required_action_missing'):
        vr.check_coverage([event(1),event(2)],[new])


def test_review_all_frames_and_shared_secrets(monkeypatch,tmp_path):
    from task_server.services import ai_skill_service as ai
    fs=[{'frame_id':f'f{i}','timestamp_ms':i*100} for i in range(90)]
    (tmp_path/'frames').mkdir()
    for f in fs:(tmp_path/'frames'/(f['frame_id']+'.jpg')).write_bytes(b'image')
    original=[event(0,'aiInput')];seen=set();secrets=[]
    def model(skill,payload,**kw):
        seen.update(f['frame_id'] for f in payload['frames'])
        assert len(kw['image_assets'])<=60
        first=payload['window']['index']==1
        e={'action':'aiInput' if first else 'aiAssert','target':'文本' if first else '页面',
           'value':'NEW_SECRET' if first else '', 'observation':'页面 NEW_SECRET',
           'frame_refs':[payload['frames'][0]['frame_id']]}
        return {'events':[e],'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    result=vr.reconcile(original,fs,9000,tmp_path,lambda:False,secrets)
    assert len(seen)==90 and 'NEW_SECRET' in secrets and 'NEW_SECRET' not in str(result)


def test_long_observation_budget_is_separate_from_final_case_budget(monkeypatch,tmp_path):
    from task_server.services import ui_video_windows as vw,ai_skill_service as ai
    from task_server.services.ui_video_analysis import validate_analysis
    fs=[{'frame_id':f'frame-{i:03d}','timestamp_ms':i*100} for i in range(90)]
    (tmp_path/'frames').mkdir()
    for f in fs:(tmp_path/'frames'/(f['frame_id']+'.jpg')).write_bytes(b'image')
    def model(skill,payload,**kw):
        return {'events':[{'action':'aiAssert','target':'当前任务','value':'','observation':'任务仍在',
                          'frame_refs':[f['frame_id']]} for f in payload['frames']],
                'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    observed,_=vw.observe_windows(fs,9000,tmp_path,lambda:False)
    assert len(observed['events'])==90
    with pytest.raises(ValueError,match='video_model_invalid'):
        validate_analysis(observed,fs,9000)


def test_window_retries_malformed_output_once_without_copying_platform_fields(monkeypatch,tmp_path):
    from task_server.services import ui_video_windows as vw,ai_skill_service as ai
    fs=[{'frame_id':f'frame-{i:03d}','timestamp_ms':i*100} for i in range(3)]
    (tmp_path/'frames').mkdir()
    for f in fs:(tmp_path/'frames'/(f['frame_id']+'.jpg')).write_bytes(b'image')
    calls=[]
    def model(skill,payload,**kw):
        calls.append(copy.deepcopy(payload))
        e={'action':'aiAssert','target':'当前页面','value':'','observation':'首页可见','frame_refs':['frame-001']}
        if len(calls)==1:e['start_ms']=100
        return {'events':[e],'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    result,_=vw.observe_windows(fs,300,tmp_path,lambda:False)
    assert len(calls)==2 and calls[1]['validation_feedback']
    assert result['events'][0]['start_ms']==100


def test_invalid_window_stops_after_two_model_calls(monkeypatch,tmp_path):
    from task_server.services import ui_video_windows as vw,ai_skill_service as ai
    fs=[{'frame_id':f'frame-{i:03d}','timestamp_ms':i*100} for i in range(3)]
    (tmp_path/'frames').mkdir()
    for f in fs:(tmp_path/'frames'/(f['frame_id']+'.jpg')).write_bytes(b'image')
    calls=[]
    def model(*args,**kw):
        calls.append(1)
        return {'events':[{'action':'aiTap','target':'页面','value':'','observation':'点击','frame_refs':['forged']}],
                'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    with pytest.raises(ValueError,match='video_reference_invalid'):
        vw.observe_windows(fs,300,tmp_path,lambda:False)
    assert len(calls)==2


def test_review_candidates_are_local_and_do_not_offer_foreign_frame_refs(monkeypatch,tmp_path):
    from task_server.services import ui_video_windows as vw,ai_skill_service as ai
    fs=[{'frame_id':f'frame-{i:03d}','timestamp_ms':i*100} for i in range(90)]
    (tmp_path/'frames').mkdir()
    for f in fs:(tmp_path/'frames'/(f['frame_id']+'.jpg')).write_bytes(b'image')
    candidates=[{**event(i),'frame_refs':[fs[i]['frame_id']]} for i in (0,30,60,89)]
    def model(skill,payload,**kw):
        ids={f['frame_id'] for f in payload['frames']}
        assert all(set(e['frame_refs'])<=ids and 'start_ms' not in e for e in payload['candidate_events'])
        return {'events':[],'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    vw.observe_windows(fs,9000,tmp_path,lambda:False,review_events=candidates)
