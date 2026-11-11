import copy
import pytest
from task_server.services import ui_video_reconcile as reconcile


def fixture(count):
    frames=[{'frame_id':f'f{i}','timestamp_ms':i*100} for i in range(110)]
    events=[{'action':'aiTap','target':f'按钮{i}','value':'','frame_refs':[f'f{i}'],
             'start_ms':i*100,'end_ms':i*100,'observation':'点击后画面变化',
             'uncertainties':[],'evidence_kind':'inferred'} for i in range(5,5+count*10,10)]
    return frames,events


@pytest.mark.parametrize('count,limit',[(4,4),(5,6),(6,7),(8,9),(9,9)])
def test_groups_are_globally_bounded_and_fully_preserved(monkeypatch,tmp_path,count,limit):
    frames,events=fixture(count);calls=[];checked={'events':copy.deepcopy(events),'uncertainties':[],'multiple_scenarios':False}
    def fake(original,local,group,*args,**kw):
        calls.append(kw)
        attempts=2 if len(calls)==1 and kw['attempt_limit']==2 else 1
        return {'events':copy.deepcopy(local['events']),'uncertainties':[],'multiple_scenarios':False,
                'reconciliation_audit':{'coverage_resolutions':[],'model_attempts':attempts}}
    monkeypatch.setattr(reconcile,'_repair_window',fake)
    result=reconcile._repair_coverage(events,checked,frames,11000,tmp_path,lambda:False,[],{e['frame_refs'][0] for e in events})
    assert len(calls)==count
    assert sum(2 if i==0 and c['attempt_limit']==2 else 1 for i,c in enumerate(calls))<=limit
    assert all(c['retry_return_gesture_only']==(count>=5) for c in calls)
    assert result['events']==events and checked['events']==events


def test_ten_groups_rejected_before_call(monkeypatch,tmp_path):
    frames,events=fixture(10)
    monkeypatch.setattr(reconcile,'_repair_window',lambda *a,**k:pytest.fail('unexpected model call'))
    with pytest.raises(ValueError,match='video_required_action_missing'):
        reconcile._repair_coverage(events,{'events':events,'uncertainties':[],'multiple_scenarios':False},frames,11000,tmp_path,lambda:False,[],{e['frame_refs'][0] for e in events})


def test_multiple_group_ownership_rejects_without_truncating():
    groups=[[{'frame_id':'f1'}],[{'frame_id':'f3'}]]
    event={'action':'navigateBack','frame_refs':['f1','f3']}
    with pytest.raises(ValueError,match='video_required_action_missing'):
        reconcile._validate_repair_group_ownership([event],groups)


def test_failure_in_last_group_does_not_mutate_source(monkeypatch,tmp_path):
    frames,events=fixture(8);checked={'events':copy.deepcopy(events),'uncertainties':[],'multiple_scenarios':False};before=copy.deepcopy(checked);calls=[]
    def fake(original,local,group,*args,**kw):
        calls.append(1)
        if len(calls)==8:raise RuntimeError('injected')
        return {'events':copy.deepcopy(local['events']),'uncertainties':[],'multiple_scenarios':False,'reconciliation_audit':{'coverage_resolutions':[],'model_attempts':1}}
    monkeypatch.setattr(reconcile,'_repair_window',fake)
    with pytest.raises(RuntimeError,match='injected'):
        reconcile._repair_coverage(events,checked,frames,11000,tmp_path,lambda:False,[],{e['frame_refs'][0] for e in events})
    assert len(calls)==8 and checked==before


@pytest.mark.parametrize('count',[41,161])
def test_frame_limit_refuses_before_any_model(monkeypatch,tmp_path,count):
    frames=[{'frame_id':f'f{i}','timestamp_ms':i*100} for i in range(count+1)]
    events=[{'action':'aiTap','target':'按钮','value':'','frame_refs':[f'f{i}'],
             'start_ms':i*100,'end_ms':i*100,'observation':'画面变化',
             'uncertainties':[],'evidence_kind':'inferred'} for i in range(count)]
    monkeypatch.setattr(reconcile,'_repair_window',lambda *a,**kw:pytest.fail('over-budget model call'))
    with pytest.raises(ValueError,match='video_required_action_missing'):
        reconcile._repair_coverage(events,{'events':copy.deepcopy(events),'uncertainties':[],'multiple_scenarios':False},frames,(count+1)*100,tmp_path,lambda:False,[],{e['frame_refs'][0] for e in events})


def test_omitting_last_group_interaction_fails_global_coverage(monkeypatch,tmp_path):
    frames,events=fixture(8);calls=[]
    def fake(original,local,group,*args,**kw):
        calls.append(1)
        return {'events':copy.deepcopy(local['events']) if len(calls)<8 else [],
                'uncertainties':[],'multiple_scenarios':False,'reconciliation_audit':{'coverage_resolutions':[],'model_attempts':1}}
    monkeypatch.setattr(reconcile,'_repair_window',fake)
    with pytest.raises(ValueError,match='video_required_action_missing'):
        reconcile._repair_coverage(events,{'events':copy.deepcopy(events),'uncertainties':[],'multiple_scenarios':False},frames,11000,tmp_path,lambda:False,[],{e['frame_refs'][0] for e in events})
    assert len(calls)==8


def test_cancellation_between_groups_stops_dispatch(monkeypatch,tmp_path):
    frames,events=fixture(8);calls=[]
    def fake(original,local,group,*args,**kw):
        calls.append(1)
        return {'events':copy.deepcopy(local['events']),'uncertainties':[],'multiple_scenarios':False,
                'reconciliation_audit':{'coverage_resolutions':[],'model_attempts':1}}
    monkeypatch.setattr(reconcile,'_repair_window',fake)
    with pytest.raises(ValueError,match='video_cancelled'):
        reconcile._repair_coverage(events,{'events':copy.deepcopy(events),'uncertainties':[],'multiple_scenarios':False},frames,11000,tmp_path,lambda:len(calls)==3,[],{e['frame_refs'][0] for e in events})
    assert len(calls)==3


def test_separate_returns_remain_separate_and_questions_survive(monkeypatch,tmp_path):
    frames,events=fixture(8)
    for index in (6,7):events[index].update(action='navigateBack',target='上一页面',observation='返回上一页面')
    def fake(original,local,group,*args,**kw):
        return {'events':copy.deepcopy(local['events']),'uncertainties':['该处仍需核对'],'multiple_scenarios':True,
                'reconciliation_audit':{'coverage_resolutions':[],'model_attempts':1}}
    monkeypatch.setattr(reconcile,'_repair_window',fake)
    result=reconcile._repair_coverage(events,{'events':copy.deepcopy(events),'uncertainties':['原问题'],'multiple_scenarios':False},frames,11000,tmp_path,lambda:False,[],{e['frame_refs'][0] for e in events})
    returns=[e for e in result['events'] if e['action']=='navigateBack']
    assert len(returns)==2 and returns[0]['frame_refs']!=returns[1]['frame_refs']
    assert set(result['uncertainties'])=={'原问题','该处仍需核对'} and result['multiple_scenarios']


def test_cross_group_assertion_is_expanded_and_preserved(monkeypatch,tmp_path):
    frames,events=fixture(8)
    assertion={'action':'aiAssert','target':'状态','value':'','frame_refs':['f5','f15'],
               'start_ms':500,'end_ms':1500,'observation':'状态可见',
               'uncertainties':[],'evidence_kind':'inferred'}
    reviewed=copy.deepcopy(events);reviewed.insert(1,assertion);calls=[]
    def fake(original,local,group,*args,**kw):
        calls.append([f['frame_id'] for f in group])
        return {'events':copy.deepcopy(local['events']), 'uncertainties':[],
                'multiple_scenarios':False,'reconciliation_audit':{'coverage_resolutions':[],'model_attempts':1}}
    monkeypatch.setattr(reconcile,'_repair_window',fake)
    result=reconcile._repair_coverage(events,{'events':reviewed,'uncertainties':[],'multiple_scenarios':False},frames,11000,tmp_path,lambda:False,[],{e['frame_refs'][0] for e in events})
    assert any(g==[f'f{i}' for i in range(5,16)] for g in calls)
    assert sum(e==assertion for e in result['events'])==1


def test_expanded_cross_group_assertion_cannot_disappear(monkeypatch,tmp_path):
    frames,events=fixture(8)
    assertion={'action':'aiAssert','target':'状态','value':'','frame_refs':['f5','f15'],
               'start_ms':500,'end_ms':1500,'observation':'状态可见',
               'uncertainties':[],'evidence_kind':'inferred'}
    reviewed=copy.deepcopy(events);reviewed.insert(1,assertion)
    def fake(original,local,group,*args,**kw):
        kept=[e for e in local['events'] if e['action']!='aiAssert']
        return {'events':copy.deepcopy(kept),'uncertainties':[],'multiple_scenarios':False,
                'reconciliation_audit':{'coverage_resolutions':[],'model_attempts':1}}
    monkeypatch.setattr(reconcile,'_repair_window',fake)
    result=reconcile._repair_coverage(events,{'events':reviewed,'uncertainties':[],'multiple_scenarios':False},frames,11000,tmp_path,lambda:False,[],{e['frame_refs'][0] for e in events})
    assert any('状态证据未完整保留' in q for q in result['uncertainties'])


def test_partly_preserved_assertion_cannot_be_replayed(monkeypatch,tmp_path):
    frames,events=fixture(8)
    assertion={'action':'aiAssert','target':'状态','value':'','frame_refs':['f5','f6'],
               'start_ms':500,'end_ms':600,'observation':'状态可见',
               'uncertainties':[],'evidence_kind':'inferred'}
    reviewed=copy.deepcopy(events);reviewed.insert(1,assertion);calls=[]
    def fake(original,local,group,*args,**kw):
        calls.append(1)
        emitted={**assertion,'frame_refs':['f5'],'end_ms':500}
        return {'events':copy.deepcopy(local['events'])+[emitted], 'uncertainties':[],
                'multiple_scenarios':False,'reconciliation_audit':{'coverage_resolutions':[],'model_attempts':1}}
    monkeypatch.setattr(reconcile,'_repair_window',fake)
    with pytest.raises(ValueError,match='video_required_action_missing'):
        reconcile._repair_coverage(events,{'events':reviewed,'uncertainties':[],'multiple_scenarios':False},frames,11000,tmp_path,lambda:False,[],{e['frame_refs'][0] for e in events})
    assert len(calls)==2  # Bounded re-observation still refuses to lose state evidence.


def test_cross_group_assertion_expansion_over_40_fails_before_call(monkeypatch,tmp_path):
    frames,events=fixture(8)
    assertion={'action':'aiAssert','target':'状态','value':'','frame_refs':['f5','f55'],
               'start_ms':500,'end_ms':5500,'observation':'状态可见',
               'uncertainties':[],'evidence_kind':'inferred'}
    reviewed=copy.deepcopy(events);reviewed.insert(1,assertion)
    monkeypatch.setattr(reconcile,'_repair_window',lambda *a,**kw:pytest.fail('over-budget group'))
    with pytest.raises(ValueError,match='video_required_action_missing'):
        reconcile._repair_coverage(events,{'events':reviewed,'uncertainties':[],'multiple_scenarios':False},frames,11000,tmp_path,lambda:False,[],{e['frame_refs'][0] for e in events})


def test_preserved_state_time_overlap_refuses_distinct_frames(monkeypatch,tmp_path):
    frames,events=fixture(8)
    assertion={'action':'aiAssert','target':'长状态','value':'','frame_refs':['f4','f6'],
               'start_ms':400,'end_ms':600,'observation':'页面持续显示',
               'uncertainties':[],'evidence_kind':'inferred'}
    reviewed=copy.deepcopy(events);reviewed.insert(0,assertion);calls=[]
    def fake(original,local,group,*args,**kwargs):
        calls.append(1)
        state={'action':'aiAssert','target':'新状态','value':'','frame_refs':['f5'],
               'start_ms':500,'end_ms':500,'observation':'界面显示',
               'uncertainties':[],'evidence_kind':'inferred'}
        return {'events':copy.deepcopy(local['events'])+[state],'uncertainties':[],'multiple_scenarios':False,
                'reconciliation_audit':{'coverage_resolutions':[],'model_attempts':1}}
    monkeypatch.setattr(reconcile,'_repair_window',fake)
    with pytest.raises(ValueError,match='video_required_action_missing'):
        reconcile._repair_coverage(events,{'events':reviewed,'uncertainties':[],'multiple_scenarios':False},frames,11000,tmp_path,lambda:False,[],{e['frame_refs'][0] for e in events})
    assert len(calls)==3  # The next invalid stub result still fails after re-observation.


def test_state_bridges_and_interactions_reach_fixed_point(monkeypatch,tmp_path):
    frames,events=fixture(8)
    def state(a,b):return {'action':'aiAssert','target':f'状态{a}','value':'','frame_refs':[f'f{a}',f'f{b}'],
            'start_ms':a*100,'end_ms':b*100,'observation':'状态可见','uncertainties':[],'evidence_kind':'inferred'}
    reviewed=copy.deepcopy(events)+[state(5,15),state(15,25)]
    reviewed.sort(key=lambda e:(e['start_ms'],e['end_ms']))
    calls=[]
    def fake(original,local,group,*args,**kw):
        calls.append([f['frame_id'] for f in group])
        return {'events':copy.deepcopy(local['events']),'uncertainties':[],'multiple_scenarios':False,
                'reconciliation_audit':{'coverage_resolutions':[],'model_attempts':1}}
    monkeypatch.setattr(reconcile,'_repair_window',fake)
    result=reconcile._repair_coverage(events,{'events':reviewed,'uncertainties':[],'multiple_scenarios':False},frames,11000,tmp_path,lambda:False,[],{e['frame_refs'][0] for e in events})
    assert any(group==[f'f{i}' for i in range(5,26)] for group in calls)
    assert len([e for e in result['events'] if e['action']=='aiAssert'])==2


def test_bridge_closes_over_partially_touched_scroll(monkeypatch,tmp_path):
    frames,events=fixture(8)
    state={'action':'aiAssert','target':'状态','value':'','frame_refs':['f5','f15'],
           'start_ms':500,'end_ms':1500,'observation':'状态可见','uncertainties':[],'evidence_kind':'inferred'}
    scroll={'action':'aiScroll','target':'列表','value':{'direction':'down','scrollType':'singleAction'},
            'frame_refs':['f12','f17'],'start_ms':1200,'end_ms':1700,'observation':'列表向上滚动',
            'uncertainties':[],'evidence_kind':'inferred'}
    reviewed=copy.deepcopy(events)+[state,scroll];reviewed.sort(key=lambda e:(e['start_ms'],e['end_ms']))
    calls=[]
    def fake(original,local,group,*args,**kw):
        calls.append([f['frame_id'] for f in group])
        return {'events':copy.deepcopy(local['events']),'uncertainties':[],'multiple_scenarios':False,
                'reconciliation_audit':{'coverage_resolutions':[],'model_attempts':1}}
    monkeypatch.setattr(reconcile,'_repair_window',fake)
    result=reconcile._repair_coverage(events,{'events':reviewed,'uncertainties':[],'multiple_scenarios':False},frames,11000,tmp_path,lambda:False,[],{e['frame_refs'][0] for e in events})
    assert any(group==[f'f{i}' for i in range(5,18)] for group in calls)
    assert sum(e==scroll for e in result['events'])==1


def test_cancel_during_closure_never_reaches_model(monkeypatch,tmp_path):
    frames,events=fixture(8);checks=[]
    state={'action':'aiAssert','target':'状态','value':'','frame_refs':['f5','f15'],
           'start_ms':500,'end_ms':1500,'observation':'状态可见','uncertainties':[],'evidence_kind':'inferred'}
    reviewed=copy.deepcopy(events)+[state];reviewed.sort(key=lambda e:(e['start_ms'],e['end_ms']))
    monkeypatch.setattr(reconcile,'_repair_window',lambda *a,**kw:pytest.fail('cancelled work reached model'))
    def cancelled():checks.append(1);return len(checks)>=2
    with pytest.raises(ValueError,match='video_cancelled'):
        reconcile._repair_coverage(events,{'events':reviewed,'uncertainties':[],'multiple_scenarios':False},frames,11000,tmp_path,cancelled,[],{e['frame_refs'][0] for e in events})
    assert len(checks)==2


def test_bridge_closes_over_interaction_whose_endpoints_are_outside_bridge(monkeypatch,tmp_path):
    frames,events=fixture(8)
    state={'action':'aiAssert','target':'状态','value':'','frame_refs':['f5','f15'],
           'start_ms':500,'end_ms':1500,'observation':'状态可见','uncertainties':[],'evidence_kind':'inferred'}
    scroll={'action':'aiScroll','target':'列表','value':{'direction':'down','scrollType':'singleAction'},
            'frame_refs':['f3','f17'],'start_ms':300,'end_ms':1700,'observation':'列表滚动',
            'uncertainties':[],'evidence_kind':'inferred'}
    reviewed=copy.deepcopy(events)+[state,scroll];reviewed.sort(key=lambda e:(e['start_ms'],e['end_ms']))
    calls=[]
    def fake(original,local,group,*args,**kw):
        calls.append([f['frame_id'] for f in group])
        return {'events':copy.deepcopy(local['events']),'uncertainties':[],'multiple_scenarios':False,
                'reconciliation_audit':{'coverage_resolutions':[],'model_attempts':1}}
    monkeypatch.setattr(reconcile,'_repair_window',fake)
    result=reconcile._repair_coverage(events,{'events':reviewed,'uncertainties':[],'multiple_scenarios':False},frames,11000,tmp_path,lambda:False,[],{e['frame_refs'][0] for e in events})
    assert any(group==[f'f{i}' for i in range(3,18)] for group in calls)
    assert sum(e==scroll for e in result['events'])==1


def test_bridge_interaction_expansion_over_limit_is_refused(monkeypatch,tmp_path):
    frames,events=fixture(8)
    state={'action':'aiAssert','target':'状态','value':'','frame_refs':['f5','f15'],
           'start_ms':500,'end_ms':1500,'observation':'状态可见','uncertainties':[],'evidence_kind':'inferred'}
    scroll={'action':'aiScroll','target':'列表','value':{'direction':'down','scrollType':'singleAction'},
            'frame_refs':['f12','f60'],'start_ms':1200,'end_ms':6000,'observation':'列表滚动',
            'uncertainties':[],'evidence_kind':'inferred'}
    reviewed=copy.deepcopy(events)+[state,scroll];reviewed.sort(key=lambda e:(e['start_ms'],e['end_ms']))
    monkeypatch.setattr(reconcile,'_repair_window',lambda *a,**kw:pytest.fail('over-budget group'))
    with pytest.raises(ValueError,match='video_required_action_missing'):
        reconcile._repair_coverage(events,{'events':reviewed,'uncertainties':[],'multiple_scenarios':False},frames,11000,tmp_path,lambda:False,[],{e['frame_refs'][0] for e in events})


def test_bridge_interaction_expansion_propagates_until_stable(monkeypatch,tmp_path):
    frames,events=fixture(8)
    state={'action':'aiAssert','target':'状态','value':'','frame_refs':['f5','f15'],
           'start_ms':500,'end_ms':1500,'observation':'状态可见','uncertainties':[],'evidence_kind':'inferred'}
    def scroll(a,b):return {'action':'aiScroll','target':f'列表{a}','value':{'direction':'down','scrollType':'singleAction'},
            'frame_refs':[f'f{a}',f'f{b}'],'start_ms':a*100,'end_ms':b*100,'observation':'列表滚动','uncertainties':[],'evidence_kind':'inferred'}
    reviewed=copy.deepcopy(events)+[state,scroll(12,17),scroll(17,21)];reviewed.sort(key=lambda e:(e['start_ms'],e['end_ms']))
    calls=[]
    def fake(original,local,group,*args,**kw):
        calls.append([f['frame_id'] for f in group])
        return {'events':copy.deepcopy(local['events']),'uncertainties':[],'multiple_scenarios':False,
                'reconciliation_audit':{'coverage_resolutions':[],'model_attempts':1}}
    monkeypatch.setattr(reconcile,'_repair_window',fake)
    result=reconcile._repair_coverage(events,{'events':reviewed,'uncertainties':[],'multiple_scenarios':False},frames,11000,tmp_path,lambda:False,[],{e['frame_refs'][0] for e in events})
    assert any(group==[f'f{i}' for i in range(5,22)] for group in calls)
    assert len([e for e in result['events'] if e['action']=='aiScroll'])==2


def test_new_overlapping_scroll_requires_review_without_merging(monkeypatch,tmp_path):
    frames,events=fixture(8)
    state={'action':'aiAssert','target':'状态','value':'','frame_refs':['f5','f15'],
           'start_ms':500,'end_ms':1500,'observation':'状态可见','uncertainties':[],'evidence_kind':'inferred'}
    old={'action':'aiScroll','target':'列表','value':{'direction':'down','scrollType':'singleAction'},
         'frame_refs':['f12','f17'],'start_ms':1200,'end_ms':1700,'observation':'列表滚动',
         'uncertainties':[],'evidence_kind':'inferred'}
    extra={**copy.deepcopy(old),'frame_refs':['f14'],'start_ms':1400,'end_ms':1400}
    reviewed=copy.deepcopy(events)+[state,old];reviewed.sort(key=lambda e:(e['start_ms'],e['end_ms']))
    def fake(original,local,group,*args,**kw):
        result=copy.deepcopy(local['events'])
        if any(e['action']=='aiScroll' for e in result):result.append(copy.deepcopy(extra))
        result.sort(key=lambda e:(e['start_ms'],e['end_ms']))
        return {'events':result,'uncertainties':[],'multiple_scenarios':False,
                'reconciliation_audit':{'coverage_resolutions':[],'model_attempts':1}}
    monkeypatch.setattr(reconcile,'_repair_window',fake)
    result=reconcile._repair_coverage(events,{'events':reviewed,'uncertainties':[],'multiple_scenarios':False},frames,11000,tmp_path,lambda:False,[],{e['frame_refs'][0] for e in events})
    assert len([e for e in result['events'] if e['action']=='aiScroll'])==2
    assert any('交互时间重叠' in q for q in result['uncertainties'])


def test_real_repair_window_keeps_new_overlapping_scroll_unresolved(monkeypatch,tmp_path):
    from task_server.services import ui_video_windows as windows,ui_video_touch_detail as details,ai_skill_service as ai
    frames,events=fixture(6)
    state={'action':'aiAssert','target':'状态','value':'','frame_refs':['f5','f15'],
           'start_ms':500,'end_ms':1500,'observation':'状态可见','uncertainties':[],'evidence_kind':'inferred'}
    old={'action':'aiScroll','target':'列表','value':{'direction':'down','scrollType':'singleAction'},
         'frame_refs':['f12','f17'],'start_ms':1200,'end_ms':1700,'observation':'列表滚动',
         'uncertainties':[],'evidence_kind':'inferred'}
    extra={**copy.deepcopy(old),'frame_refs':['f14'],'start_ms':1400,'end_ms':1400}
    source=copy.deepcopy(events)+[state,old];source.sort(key=lambda e:(e['start_ms'],e['end_ms']))
    monkeypatch.setattr(windows,'load_assets',lambda frames,*a:[{'name':f['frame_id']} for f in frames])
    monkeypatch.setattr(details,'append_details',lambda *a,**kw:[])
    calls=[]
    def model(skill,payload,**kw):
        calls.append(1);ids={f['frame_id'] for f in payload['frames']}
        local=[copy.deepcopy(e) for e in source if set(e['frame_refs'])<=ids]
        if 'f14' in ids:local.append(copy.deepcopy(extra))
        local.sort(key=lambda e:(e['start_ms'],e['end_ms']))
        return {'events':[{k:v for k,v in e.items() if k not in {'start_ms','end_ms','evidence_kind'}} for e in local],
                'uncertainties':[],'multiple_scenarios':False,'coverage_resolutions':[]}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    result=reconcile._repair_coverage(source,{'events':copy.deepcopy(source),'uncertainties':[],'multiple_scenarios':False},frames,11000,tmp_path,lambda:False,[],{e['frame_refs'][0] for e in events})
    assert len([e for e in result['events'] if e['action']=='aiScroll'])==2
    assert any('交互时间重叠' in q for q in result['uncertainties'])


def test_parallel_related_expansion_joins_bridge_closure(monkeypatch,tmp_path):
    frames,events=fixture(6)
    anchor=events[-1];anchor['target']='相关入口'
    state={'action':'aiAssert','target':'状态','value':'','frame_refs':['f5','f15'],
           'start_ms':500,'end_ms':1500,'observation':'状态可见','uncertainties':[],'evidence_kind':'inferred'}
    related={**copy.deepcopy(anchor),'frame_refs':['f55','f60'],'end_ms':6000}
    downstream={'action':'aiScroll','target':'下游列表','value':{'direction':'down','scrollType':'singleAction'},
                'frame_refs':['f58','f62'],'start_ms':5800,'end_ms':6200,'observation':'列表滚动',
                'uncertainties':[],'evidence_kind':'inferred'}
    reviewed=copy.deepcopy(events)+[state,related,downstream];reviewed.sort(key=lambda e:(e['start_ms'],e['end_ms']));calls=[]
    def fake(original,local,group,*args,**kw):
        calls.append([f['frame_id'] for f in group])
        return {'events':copy.deepcopy(local['events']),'uncertainties':[],'multiple_scenarios':False,
                'reconciliation_audit':{'coverage_resolutions':[],'model_attempts':1}}
    monkeypatch.setattr(reconcile,'_repair_window',fake)
    result=reconcile._repair_coverage(events,{'events':reviewed,'uncertainties':[],'multiple_scenarios':False},frames,11000,tmp_path,lambda:False,[],{e['frame_refs'][0] for e in events})
    assert any(group==[f'f{i}' for i in range(55,63)] for group in calls)
    assert sum(e==downstream for e in result['events'])==1
