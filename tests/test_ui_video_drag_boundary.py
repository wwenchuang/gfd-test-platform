import copy
import pytest
from task_server.services import ui_video_reconcile as rec
from task_server.services import ui_video_coverage_resolution as cr


def example():
    frames=[{'frame_id':f'f{i}','timestamp_ms':100*i} for i in range(1,5)]
    old=[{'action':'aiTap','target':'卡片换一换','value':'','frame_refs':['f1','f2'],'start_ms':100,'end_ms':200,'observation':'圆圈出现在按钮旁','uncertainties':[]},
         {'action':'aiScroll','target':'列表内容','value':{'direction':'down','scrollType':'singleAction'},'frame_refs':['f2','f3'],'start_ms':200,'end_ms':300,'observation':'内容上移','uncertainties':[]}]
    for event in old:event['evidence_kind']='inferred'
    new=[{**old[1],'start_ms':100,'frame_refs':['f1','f2','f3']}]
    claim={'source_anchor_ids':['first_pass:0','first_pass:1'],'resolution':'same_scroll_gesture','reviewed_event_index':0,'evidence':{
        'before_frame':'f1','interaction_frame':'f2','after_frame':'f3','examined_frame_refs':['f1','f2','f3'],
        'before_state':'列表与按钮可见','interaction':'同一次连续拖动，触点持续向上移动，内容随触点上移','after_state':'下方内容显露','why_not_separate_actions':'未见独立点击，未见松开后重新按下','gesture_facts':gesture_facts()}}
    return frames,old,new,claim


def test_adjacent_tap_scroll_only_flags_for_review_without_mutation():
    _,old,_,_=example();before=copy.deepcopy(old)
    assert rec._tap_scroll_refs(old)=={'f1','f2','f3'}
    assert old==before


@pytest.mark.parametrize('change',['gap','no_shared','reversed','intervening'])
def test_unrelated_actions_do_not_trigger(change):
    _,old,_,_=example()
    if change=='gap':old[1]['start_ms']=600
    if change=='no_shared':old[1]['frame_refs']=['f3','f4']
    if change=='reversed':old.reverse()
    if change=='intervening':old.insert(1,{**old[0],'target':'另一按钮','frame_refs':['f4']})
    assert not rec._tap_scroll_refs(old)


def test_continuous_drag_claim_keeps_audit_and_satisfies_coverage():
    frames,old,new,claim=example()
    result=cr.validate([claim],{'first_pass':old},new,frames)
    assert result[0]['resolution']=='same_scroll_gesture'
    assert result[0]['source_anchors']['first_pass:0']['action']=='aiTap'
    rec.check_coverage(old,new,400,result,'first_pass')


@pytest.mark.parametrize('change',['click_without_response','new_press','direction','missing_frame','not_shared','uncertain','no_motion','only_taps','intervening'])
def test_drag_mapping_rejects_incomplete_or_separate_interactions(change):
    frames,old,new,claim=example();ev=claim['evidence']
    if change=='click_without_response':ev['why_not_separate_actions']='独立点击没有响应，随后进行拖动'
    if change=='new_press':ev['why_not_separate_actions']='松开后重新按下，再次滑动'
    if change=='direction':new[0]['value']={'direction':'up','scrollType':'singleAction'}
    if change=='missing_frame':ev['examined_frame_refs']=['f2','f3']
    if change=='not_shared':old[0]['frame_refs']=['f1']
    if change=='uncertain':ev['interaction']='可能是同一次拖动'
    if change=='no_motion':ev['interaction']='同一次操作，按钮可见'
    if change=='only_taps':old[1]['action']='aiTap'
    if change=='intervening':old.insert(1,{**old[0],'target':'其他按钮','start_ms':150,'end_ms':150});claim['source_anchor_ids']=['first_pass:0','first_pass:2']
    with pytest.raises(ValueError):cr.validate([claim],{'first_pass':old},new,frames)


def test_actual_independent_click_then_scroll_can_stay_two_actions():
    frames,old,_,_=example()
    assert cr.validate([],{'first_pass':old},old,frames)==[]
    rec.check_coverage(old,old,400)


@pytest.mark.parametrize('merge',[True,False])
def test_bounded_pixel_review_can_correct_drag_or_keep_independent_click(monkeypatch,tmp_path,merge):
    from task_server.services import ai_skill_service as ai
    frames,old,new,claim=example()
    (tmp_path/'frames').mkdir()
    for frame in frames:(tmp_path/'frames'/(frame['frame_id']+'.jpg')).write_bytes(b'image')
    claim['source_anchor_ids']+=['independent_review:0','independent_review:1']
    calls=[]
    def model(skill,payload,**kwargs):
        calls.append(payload)
        assert kwargs['version']=='coverage'
        assert 'same_scroll_gesture' in payload['coverage_resolution_contract']['schema']['resolution']
        source=new if merge else old
        events=[{k:e[k] for k in ('action','target','value','frame_refs','observation','uncertainties')} for e in source]
        return {'events':events,'uncertainties':[],'multiple_scenarios':False,'coverage_resolutions':[claim] if merge else []}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    out=rec._repair_coverage(old,{'events':copy.deepcopy(old),'uncertainties':[],'multiple_scenarios':False},frames,400,tmp_path,lambda:False,[],rec._tap_scroll_refs(old))
    assert len(calls)==1
    assert [e['action'] for e in out['events']]==(['aiScroll'] if merge else ['aiTap','aiScroll'])
    assert bool(out['reconciliation_audit']['coverage_resolutions'])==merge


def test_false_tap_can_be_first_of_two_real_drags_without_merging():
    _,old,_,_=example()
    revised=copy.deepcopy(old)
    revised[0].update(action='aiScroll',target='列表',value={'direction':'down','scrollType':'singleAction'},observation='未见独立点击，实际为同一次连续拖动；触点持续移动，内容随触点上移')
    rec.check_coverage(old,revised,400)
    assert len(revised)==2 and revised[1]['frame_refs']==old[1]['frame_refs']
    assert not revised[0]['uncertainties']


@pytest.mark.parametrize('note',[
    '独立点击无响应，随后滚动',
    '未见独立点击，页面保持不变',
    '可能为同一次连续拖动；触点移动，内容上移，未见独立点击',
    '未见独立点击，实际为同一次连续拖动；触点没有移动，内容上移',
])
def test_one_to_one_correction_requires_affirmative_motion(note):
    _,old,_,_=example();revised=copy.deepcopy(old)
    revised[0].update(action='aiScroll',value={'direction':'down','scrollType':'singleAction'},observation=note)
    with pytest.raises(ValueError):rec.check_coverage(old,revised,400)


@pytest.mark.parametrize('change',['return','missing_frame','uncertain','not_adjacent'])
def test_one_to_one_drag_correction_does_not_expand_scope(change):
    _,old,_,_=example();revised=copy.deepcopy(old)
    revised[0].update(action='aiScroll',value={'direction':'down','scrollType':'singleAction'},observation='未见独立点击，实际为同一次连续拖动；触点持续移动，内容随触点上移')
    if change=='return':old[0]['target']='左上角返回箭头'
    if change=='missing_frame':revised[0]['frame_refs']=['f2']
    if change=='uncertain':revised[0]['uncertainties']=['触点不清楚']
    if change=='not_adjacent':old[1]['start_ms']=350
    with pytest.raises(ValueError):rec.check_coverage(old,revised,400)


def test_one_to_one_drag_survives_repeated_coverage_checks():
    _,old,_,_=example();revised=copy.deepcopy(old)
    revised[0].update(action='aiScroll',value={'direction':'down','scrollType':'singleAction'},observation='未见独立点击，实际为同一次连续拖动；触点持续移动，内容随触点上移')
    rec.check_coverage(old,revised,400)
    rec.check_coverage(old,revised,400)


@pytest.mark.parametrize('note',['同一次连续拖动，触点并未移动，内容并未位移','同一次连续拖动，触点持续移动，内容不发生位移','同一次连续拖动，触点并非移动，内容随触点上移'])
def test_drag_merge_rejects_negated_motion(note):
    frames,old,new,claim=example();claim['evidence']['interaction']=note
    with pytest.raises(ValueError):cr.validate([claim],{'first_pass':old},new,frames)


def test_full_repair_keeps_two_drags_and_original_click_in_audit(monkeypatch,tmp_path):
    from task_server.services import ai_skill_service as ai
    frames,old,_,_=example();revised=copy.deepcopy(old)
    revised[0].update(action='aiScroll',value={'direction':'down','scrollType':'singleAction'},observation='未见独立点击，实际为同一次连续拖动；触点持续移动，内容随触点上移')
    (tmp_path/'frames').mkdir()
    for frame in frames:(tmp_path/'frames'/(frame['frame_id']+'.jpg')).write_bytes(b'image')
    def model(*args,**kwargs):
        return {'events':[{k:e[k] for k in ('action','target','value','frame_refs','observation','uncertainties')} for e in revised],'uncertainties':[],'multiple_scenarios':False,'coverage_resolutions':[]}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    out=rec._repair_coverage(old,{'events':copy.deepcopy(old),'uncertainties':[],'multiple_scenarios':False},frames,400,tmp_path,lambda:False,[],rec._tap_scroll_refs(old))
    assert [e['action'] for e in out['events']]==['aiScroll','aiScroll']
    assert out['reconciliation_audit']['before_events'][0]['action']=='aiTap'
    assert out['reconciliation_audit']['coverage_resolutions']==[]


def test_drag_merge_cannot_remove_return_navigation():
    frames,old,new,claim=example()
    old[0].update(target='左上角返回箭头',observation='用户执行返回手势，返回上一级后开始滚动')
    with pytest.raises(ValueError):cr.validate([claim],{'first_pass':old},new,frames)


def test_drag_diagnostic_identifies_original_evidence_omitted_from_result():
    frames,old,new,claim=example()
    new[0]['frame_refs']=['f2','f3']
    result=cr.diagnose([claim],{'first_pass':old},new,frames)
    assert result['result_evidence_to_recheck'][0]['missing_result_state_frames']==['f1']


@pytest.mark.parametrize('path',['one_to_one','merge'])
def test_real_unresponsive_click_cannot_be_erased_by_contradictory_drag_claim(path):
    frames,old,new,claim=example()
    contradiction='用户明确点击按钮但没有响应，然后开始拖动'
    if path=='one_to_one':
        revised=copy.deepcopy(old)
        revised[0].update(action='aiScroll',value=old[1]['value'],observation='未见独立点击，实际为同一次连续拖动；触点持续移动，内容上移。'+contradiction)
        with pytest.raises(ValueError):rec.check_coverage(old,revised,400)
    else:
        claim['evidence']['interaction']+='。'+contradiction
        with pytest.raises(ValueError):cr.validate([claim],{'first_pass':old},new,frames)


def gesture_facts():
    return {'content_motion_direction':'up','interaction_count':1,'independent_click_observed':False,
            'continuous_drag_observed':True,'release_then_press_observed':False}


@pytest.mark.parametrize('reason',['全段指针持续接触，未发生抬手再按，只有这一段连续拖动','中间无停顿或重新按压迹象，属于同一次滑动手势'])
def test_structured_drag_facts_do_not_depend_on_fixed_negative_phrase(reason):
    frames,old,new,claim=example()
    claim['evidence']['gesture_facts']=gesture_facts()
    claim['evidence']['why_not_separate_actions']=reason
    result=cr.validate([claim],{'first_pass':old},new,frames)
    assert result[0]['resolution']=='same_scroll_gesture'


@pytest.mark.parametrize('change',['missing','two','click','release','uncertain','string_false','extra'])
def test_structured_drag_facts_reject_missing_conflicting_or_untyped_evidence(change):
    frames,old,new,claim=example();facts=gesture_facts()
    if change=='two':facts['interaction_count']=2
    if change=='click':facts['independent_click_observed']=True
    if change=='release':facts['release_then_press_observed']=True
    if change=='uncertain':facts['continuous_drag_observed']=None
    if change=='string_false':facts['independent_click_observed']='false'
    if change=='extra':facts['ignore_validation']=True
    if change=='missing':claim['evidence'].pop('gesture_facts')
    else:claim['evidence']['gesture_facts']=facts
    with pytest.raises(ValueError):cr.validate([claim],{'first_pass':old},new,frames)


def test_drag_audits_full_context_without_replaying_context_as_action():
    frames,old,new,claim=example()
    frames.insert(0,{'frame_id':'f0','timestamp_ms':0})
    old[0]['frame_refs'].insert(0,'f0');old[0]['start_ms']=0
    claim['evidence']['examined_frame_refs'].insert(0,'f0')
    result=cr.validate([claim],{'first_pass':old},new,frames)
    assert result[0]['source_anchors']['first_pass:0']['frame_refs']==['f0','f1','f2']
    assert result[0]['reviewed_event']['frame_refs']==['f1','f2','f3']


def test_drag_can_correct_disputed_old_direction_from_explicit_motion_evidence():
    frames,old,new,claim=example()
    old[1]['value']={'direction':'up','scrollType':'singleAction'}
    new[0]['value']={'direction':'down','scrollType':'singleAction'}
    claim['evidence']['gesture_facts']['content_motion_direction']='up'
    assert cr.validate([claim],{'first_pass':old},new,frames)


@pytest.mark.parametrize('text',[
    '页面没有响应后再次滑动', '实际共三次拖动',
    '独立点击无响应，随后拖动', '独立点击没有响应，随后拖动',
    '用户独立点击按钮没有响应，随后再次拖动',
])
@pytest.mark.parametrize('location',['interaction','observation','single'])
def test_drag_rejects_action_contradictions_despite_unrelated_negation(text,location):
    frames,old,new,claim=example()
    if location=='single':
        revised=copy.deepcopy(old)
        revised[0]['action']='aiScroll'
        revised[0]['observation']='未见独立点击，实际为同一次连续拖动；触点持续移动，内容上移。'+text
        with pytest.raises(ValueError):rec.check_coverage(old,revised,400)
    else:
        if location=='observation':new[0]['observation']+='。'+text
        else:claim['evidence']['interaction']+='。'+text
        with pytest.raises(ValueError):cr.validate([claim],{'first_pass':old},new,frames)


@pytest.mark.parametrize('motion',['内容下移','内容随触点下移','内容向下移动'])
def test_explicit_content_motion_must_agree_with_structured_direction(motion):
    frames,old,new,claim=example()
    claim['evidence']['interaction']='同一次连续拖动，触点持续移动，'+motion
    new[0]['observation']=motion
    with pytest.raises(ValueError):cr.validate([claim],{'first_pass':old},new,frames)
