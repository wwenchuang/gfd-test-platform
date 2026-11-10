"""v5: consecutive, distinct taps can share one before/after state anchor."""
import copy

import pytest
from task_server.services import ai_skill_service as ai
from task_server.services import ui_video_reconcile as rec
from task_server.services import ui_video_windows as windows
from task_server.services.ui_video_analysis import draft_proposal
from task_server.services.ui_case_draft_contract import build_draft_yaml

FRAMES = [{'frame_id':f'frame-{i:03d}', 'timestamp_ms':t} for i,t in [
    (335,176833),(336,176933),(337,176967),(338,177900),(339,178900),(340,179900)]]

def event(target,refs,start,end):
    return {'action':'aiTap','target':target,'value':'','frame_refs':refs,
            'start_ms':start,'end_ms':end,'observation':'候选交互，需对照原视频',
            'uncertainties':[],'evidence_kind':'inferred'}

def test_shared_boundary_rechecks_both_distinct_actions_and_keeps_manual_gate(monkeypatch,tmp_path):
    first=event('选项一',['frame-335','frame-336','frame-337'],176833,176967)
    second=event('选项二',['frame-337','frame-338','frame-339'],176967,178900)
    original=[first,second]; before=copy.deepcopy(original); calls=[]
    monkeypatch.setattr(windows,'load_assets',lambda *a:[])
    def model(*args,**kwargs):
        refs={f['frame_id'] for f in args[1]['frames']}; calls.append(refs)
        assert 'frame-337' in args[1]['validation_feedback']
        assert '不得将同一触点算作两次独立触点' in args[1]['validation_feedback']
        rows=[{k:copy.deepcopy(v) for k,v in e.items() if k not in {'start_ms','end_ms','evidence_kind'}}
              for e in original if set(e['frame_refs'])<=refs]
        return {'events':rows,'uncertainties':[],'multiple_scenarios':False,'coverage_resolutions':[]}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    result=rec._repair_coverage(original,{'events':copy.deepcopy(original),'uncertainties':[],
                               'multiple_scenarios':False}, FRAMES,195698,tmp_path,lambda:False,[],{'frame-339'})
    assert original==before
    assert len(calls)==1 and {'frame-335','frame-336','frame-337','frame-338','frame-339'}<=calls[0]
    assert [(e['target'],e['frame_refs']) for e in result['events']]==[(e['target'],e['frame_refs']) for e in original]
    assert any('核对' in q and ('边界' in q or '触点' in q) for q in result['uncertainties'])
    document={'source_id':'uvs_'+'a'*32,'app_id':'com.kfb.model','analysis_version':5,'goal':'',
              'media':{'frames':FRAMES,'duration_ms':195698},**result}
    proposal,_=draft_proposal(document)
    for step in proposal['steps']:step['review_state']='confirmed'
    gate=build_draft_yaml(proposal,'com.kfb.model')
    assert not gate['ready'] and any('边界' in q or '触点' in q for q in gate['questions'])


def test_changed_target_even_with_bidirectional_coverage_still_blocks_trial(monkeypatch,tmp_path):
    first=event('选项一',['frame-335','frame-336','frame-337'],176833,176967)
    second=event('选项二',['frame-337','frame-338','frame-339'],176967,178900)
    old=[first,second]; calls=[]
    monkeypatch.setattr(windows,'load_assets',lambda *a:[])
    def model(*args,**kwargs):
        calls.append({f['frame_id'] for f in args[1]['frames']})
        rows=[{k:copy.deepcopy(v) for k,v in e.items() if k not in {'start_ms','end_ms','evidence_kind'}} for e in old]
        rows[1]['target']=rows[0]['target']
        return {'events':rows,'uncertainties':[],'multiple_scenarios':False,'coverage_resolutions':[]}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    result=rec._repair_coverage(old,{'events':copy.deepcopy(old),'uncertainties':[],
                                'multiple_scenarios':False},FRAMES,195698,tmp_path,lambda:False,[],{'frame-339'})
    assert len(calls)==1
    rec.check_coverage(old,copy.deepcopy(result['events']),195698)
    rec.check_coverage(result['events'],copy.deepcopy(old),195698)
    assert result['events'][0]['target']==result['events'][1]['target']
    assert any('边界' in q and '目标' in q and '核对' in q for q in result['uncertainties'])
    doc={'source_id':'uvs_'+'a'*32,'app_id':'com.kfb.model','analysis_version':5,'goal':'',
         'media':{'frames':FRAMES,'duration_ms':195698},**result}
    proposal,_=draft_proposal(doc)
    for step in proposal['steps']:step['review_state']='confirmed'
    gate=build_draft_yaml(proposal,'com.kfb.model')
    assert not gate['ready'] and any('边界' in q and '目标' in q for q in gate['questions'])


@pytest.mark.parametrize('seed',[{'frame-335','frame-336','frame-337','frame-338','frame-339'},{'frame-337'}], ids=['all_initial','affected_closure'])
def test_already_complete_pair_always_requires_review(monkeypatch,tmp_path,seed):
    first=event('选项一',['frame-335','frame-336','frame-337'],176833,176967)
    second=event('选项二',['frame-337','frame-338','frame-339'],176967,178900)
    old=[first,second]
    monkeypatch.setattr(windows,'load_assets',lambda *a:[])
    def model(*args,**kwargs):
        rows=[{k:copy.deepcopy(v) for k,v in e.items() if k not in {'start_ms','end_ms','evidence_kind'}} for e in old]
        return {'events':rows,'uncertainties':[],'multiple_scenarios':False,'coverage_resolutions':[]}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    result=rec._repair_coverage(old,{'events':copy.deepcopy(old),'uncertainties':[],
                                'multiple_scenarios':False},FRAMES,195698,tmp_path,lambda:False,[],
                                seed)
    assert any('相邻点击共享画面边界' in q for q in result['uncertainties'])


def test_boundary_expansion_fails_closed_at_40_frames(monkeypatch,tmp_path):
    frames=[{'frame_id':f'frame-{i:03d}','timestamp_ms':i*100} for i in range(1,44)]
    events=[event(f'选项{i}',[f'frame-{i:03d}',f'frame-{i+1:03d}'],i*100,(i+1)*100)
            for i in range(1,42)]
    calls=[]
    monkeypatch.setattr(ai,'run_ai_skill',lambda *args,**kwargs:calls.append(1))
    with pytest.raises(ValueError,match='video_required_action_missing'):
        rec._repair_coverage(events,{'events':copy.deepcopy(events),'uncertainties':[],
                             'multiple_scenarios':False},frames,5000,tmp_path,lambda:False,[],{'frame-042'})
    assert not calls


def test_boundary_expansion_honours_cancellation_before_model(monkeypatch,tmp_path):
    first=event('选项一',['frame-335','frame-336','frame-337'],176833,176967)
    second=event('选项二',['frame-337','frame-338','frame-339'],176967,178900)
    calls=[]
    monkeypatch.setattr(ai,'run_ai_skill',lambda *args,**kwargs:calls.append(1))
    with pytest.raises(ValueError,match='video_cancelled'):
        rec._repair_coverage([first,second],{'events':[first,second],'uncertainties':[],
                             'multiple_scenarios':False},FRAMES,195698,tmp_path,lambda:True,[],{'frame-339'})
    assert not calls


def test_boundary_pair_detection_includes_same_target_but_not_non_shared_endpoint():
    first=event('选项一',['frame-335','frame-336','frame-337'],176833,176967)
    same=event('选项一',['frame-337','frame-338','frame-339'],176967,178900)
    other=event('选项二',['frame-336','frame-338','frame-339'],176967,178900)
    assert len(list(rec._adjacent_tap_boundaries([first,same])))==1
    assert list(rec._adjacent_tap_boundaries([first,other]))==[]


@pytest.mark.parametrize('same_target',[False,True])
@pytest.mark.parametrize('seed',[{'frame-339'},{'frame-337'},{'frame-335','frame-336','frame-337','frame-338','frame-339'}],ids=['expansion','affected_closure','all_initial'])
def test_shared_state_misdeclared_as_touch_cannot_clear_review_gate(monkeypatch,tmp_path,same_target,seed):
    first=event('选项一',['frame-335','frame-336','frame-337'],176833,176967)
    second=event('选项一' if same_target else '选项二',['frame-337','frame-338','frame-339'],176967,178900)
    old=[first,second]
    refs=['frame-335','frame-336','frame-337','frame-338','frame-339']
    merged=event('选项一',refs,176833,178900)
    merged['observation']='模型声称一次点击'
    claim={'source_anchor_ids':['first_pass:0','first_pass:1','independent_review:0','independent_review:1'],
           'resolution':'same_interaction','reviewed_event_index':0,
           'evidence':{'before_frame':'frame-335','interaction_frame':'frame-337',
                       'after_frame':'frame-339','examined_frame_refs':refs,
                       'before_state':'选项页面可见','interaction':'模型声称一次点击',
                       'after_state':'页面发生变化','why_not_separate_actions':'模型声称为同一次动作'}}
    # Demonstrate the problematic claim is structurally accepted: this is not
    # proof of a touch or of a single action, only a regression counterexample.
    from task_server.services.ui_video_coverage_resolution import validate
    accepted=validate([claim],{'first_pass':old,'independent_review':old},[merged],FRAMES)
    assert accepted and accepted[0]['evidence']['interaction_frame']=='frame-337'
    monkeypatch.setattr(windows,'load_assets',lambda *a:[])
    def model(*args,**kwargs):
        row={k:copy.deepcopy(v) for k,v in merged.items() if k not in {'start_ms','end_ms','evidence_kind'}}
        return {'events':[row],'uncertainties':[],'multiple_scenarios':False,'coverage_resolutions':[copy.deepcopy(claim)]}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    result=rec._repair_coverage(old,{'events':copy.deepcopy(old),'uncertainties':[],
                                'multiple_scenarios':False},FRAMES,195698,tmp_path,lambda:False,[],seed)
    assert len(result['events'])==1
    assert any('边界' in q and '触点' in q and '核对' in q for q in result['uncertainties'])
    doc={'source_id':'uvs_'+'a'*32,'app_id':'com.kfb.model','analysis_version':5,'goal':'',
         'media':{'frames':FRAMES,'duration_ms':195698},**result}
    proposal,_=draft_proposal(doc)
    for step in proposal['steps']:step['review_state']='confirmed'
    gate=build_draft_yaml(proposal,'com.kfb.model')
    assert not gate['ready'] and any('边界' in q for q in gate['questions'])


@pytest.mark.parametrize("kind",["scroll","return"])
def test_mixed_tap_boundary_is_not_silently_absorbed(monkeypatch,tmp_path,kind):
    first=event('选项一',['frame-335','frame-336','frame-337'],176833,176967)
    second=event('列表',['frame-337','frame-338','frame-339'],176967,178900)
    if kind=='scroll':
        second.update(action='aiScroll',value={'direction':'up','scrollType':'singleAction'},observation='内容向上滚动')
    else:
        second.update(action='navigateBack',target='上一页面',observation='返回上一页面')
    old=[first,second];calls=[]
    assert list(rec._adjacent_tap_boundaries(old))==[]
    monkeypatch.setattr(windows,'load_assets',lambda *a:[])
    def model(*args,**kwargs):
        calls.append({f['frame_id'] for f in args[1]['frames']})
        row={k:copy.deepcopy(v) for k,v in second.items() if k not in {'start_ms','end_ms','evidence_kind'}}
        return {'events':[row],'uncertainties':[],'multiple_scenarios':False,'coverage_resolutions':[]}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    with pytest.raises(ValueError,match='video_reference_invalid'):
        rec._repair_coverage(old,{'events':copy.deepcopy(old),'uncertainties':[],
                             'multiple_scenarios':False},FRAMES,195698,tmp_path,lambda:False,[],{'frame-339'})
    assert calls and all('frame-335' not in refs for refs in calls)


def test_boundary_chain_over_160_frames_fails_closed_before_model(monkeypatch,tmp_path):
    frames=[{'frame_id':f'frame-{i:03d}','timestamp_ms':i*100} for i in range(1,164)]
    events=[event(f'选项{i%3}',[f'frame-{i:03d}',f'frame-{i+1:03d}'],i*100,(i+1)*100)
            for i in range(1,163)]
    calls=[]
    monkeypatch.setattr(ai,'run_ai_skill',lambda *args,**kwargs:calls.append(1))
    with pytest.raises(ValueError,match='video_required_action_missing'):
        rec._repair_coverage(events,{'events':copy.deepcopy(events),'uncertainties':[],
                             'multiple_scenarios':False},frames,20000,tmp_path,lambda:False,[],{'frame-163'})
    assert not calls


@pytest.mark.parametrize('targets',[('', '选项二'),('选项一',''),('','')],ids=['first_empty','second_empty','both_empty'])
def test_empty_old_target_does_not_bypass_shared_boundary_review(monkeypatch,tmp_path,targets):
    first=event(targets[0],['frame-335','frame-336','frame-337'],176833,176967)
    second=event(targets[1],['frame-337','frame-338','frame-339'],176967,178900)
    old=[first,second]
    refs=['frame-335','frame-336','frame-337','frame-338','frame-339']
    merged=event('选项一',refs,176833,178900)
    claim={'source_anchor_ids':['first_pass:0','first_pass:1','independent_review:0','independent_review:1'],
           'resolution':'same_interaction','reviewed_event_index':0,
           'evidence':{'before_frame':'frame-335','interaction_frame':'frame-337',
                       'after_frame':'frame-339','examined_frame_refs':refs,
                       'before_state':'选项页面可见','interaction':'模型声称一次点击',
                       'after_state':'页面发生变化','why_not_separate_actions':'模型声称为同一次动作'}}
    monkeypatch.setattr(windows,'load_assets',lambda *a:[])
    def model(*args,**kwargs):
        row={k:copy.deepcopy(v) for k,v in merged.items() if k not in {'start_ms','end_ms','evidence_kind'}}
        return {'events':[row],'uncertainties':[],'multiple_scenarios':False,'coverage_resolutions':[copy.deepcopy(claim)]}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    result=rec._repair_coverage(old,{'events':copy.deepcopy(old),'uncertainties':[],
                                'multiple_scenarios':False},FRAMES,195698,tmp_path,lambda:False,[],set(refs))
    assert len(result['events'])==1
    assert any('边界' in q and '触点' in q for q in result['uncertainties'])


@pytest.mark.parametrize('nested',[False,True],ids=['two_shared_frames','nested_time_single_shared_frame'])
def test_multi_frame_overlapping_adjacent_taps_cannot_silently_merge(monkeypatch,tmp_path,nested):
    if nested:
        first=event('选项一',['frame-335','frame-337','frame-339'],176833,178900)
        second=event('选项二',['frame-336','frame-337','frame-338'],176933,177900)
    else:
        first=event('选项一',['frame-335','frame-336','frame-337'],176833,176967)
        second=event('选项二',['frame-336','frame-337','frame-338','frame-339'],176933,178900)
    old=[first,second];refs=['frame-335','frame-336','frame-337','frame-338','frame-339']
    merged=event('选项一',refs,176833,178900)
    claim={'source_anchor_ids':['first_pass:0','first_pass:1','independent_review:0','independent_review:1'],
           'resolution':'same_interaction','reviewed_event_index':0,
           'evidence':{'before_frame':'frame-335','interaction_frame':'frame-337',
                       'after_frame':'frame-339','examined_frame_refs':refs,
                       'before_state':'选项页面可见','interaction':'模型声称一次点击',
                       'after_state':'页面发生变化','why_not_separate_actions':'模型声称为同一次动作'}}
    monkeypatch.setattr(windows,'load_assets',lambda *a:[])
    def model(*args,**kwargs):
        row={k:copy.deepcopy(v) for k,v in merged.items() if k not in {'start_ms','end_ms','evidence_kind'}}
        return {'events':[row],'uncertainties':[],'multiple_scenarios':False,'coverage_resolutions':[copy.deepcopy(claim)]}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    result=rec._repair_coverage(old,{'events':copy.deepcopy(old),'uncertainties':[],
                                'multiple_scenarios':False},FRAMES,195698,tmp_path,lambda:False,[],set(refs))
    assert len(result['events'])==1
    assert any('边界' in q and '触点' in q for q in result['uncertainties'])


def test_multi_frame_overlap_partly_selected_fails_before_model(monkeypatch,tmp_path):
    first=event('选项一',['frame-335','frame-336','frame-337'],176833,176967)
    second=event('选项二',['frame-336','frame-337','frame-338','frame-339'],176933,178900)
    old=[first,second];calls=[]
    monkeypatch.setattr(ai,'run_ai_skill',lambda *a,**kw:calls.append(1))
    with pytest.raises(ValueError,match='video_required_action_missing'):
        rec._repair_coverage(old,{'events':copy.deepcopy(old),'uncertainties':[],
                             'multiple_scenarios':False},FRAMES,195698,tmp_path,lambda:False,[],{'frame-339'})
    assert not calls
