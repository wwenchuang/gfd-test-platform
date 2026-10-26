import copy

import pytest

from task_server.services import ui_video_reconcile as review


def interactions():
    return [
        dict(action='aiTap', target='文本框', value='', start_ms=100, end_ms=300,
             frame_refs=['f1', 'f2', 'f3'], observation='输入框获得焦点，键盘弹出', uncertainties=[], evidence_kind='inferred'),
        dict(action='aiInput', target='底部输入框', value='', start_ms=200, end_ms=600,
             frame_refs=['f2', 'f4', 'f6'], observation='删除原文字并输入新文字', uncertainties=[], evidence_kind='inferred'),
    ]


def test_overlapping_input_requests_pixels_without_rewriting_events():
    events=interactions(); before=copy.deepcopy(events)
    assert review._overlapping_input_refs(events)=={'f1','f2','f3','f4','f6'}
    assert events==before


@pytest.mark.parametrize('change', ['separate', 'boundary', 'other_action', 'no_shared_frame', 'intervening'])
def test_unrelated_input_does_not_request_overlap_review(change):
    events=interactions()
    if change=='separate': events[1]['start_ms']=400
    if change=='boundary': events[1]['start_ms']=300
    if change=='other_action': events[1]['action']='aiTap'
    if change=='no_shared_frame': events[1]['frame_refs']=['f4','f6']
    if change=='intervening': events.insert(1, {**events[0], 'action':'aiScroll', 'frame_refs':['f0']})
    assert not review._overlapping_input_refs(events)


def test_reconcile_uses_existing_bounded_pixel_repair_for_input_overlap(monkeypatch,tmp_path):
    from task_server.services import ui_video_windows as windows, ai_skill_service as ai
    events=interactions(); before=copy.deepcopy(events)
    frames=[dict(frame_id=f'f{i}',timestamp_ms=i*100) for i in range(1,8)]
    (tmp_path/'frames').mkdir()
    for frame in frames: (tmp_path/'frames'/(frame['frame_id']+'.jpg')).write_bytes(b'image')
    monkeypatch.setattr(windows,'observe_windows',lambda *a,**kw: (
        dict(events=copy.deepcopy(events),uncertainties=[],multiple_scenarios=False),[]))
    calls=[]
    def model(skill,payload,**kw):
        calls.append(payload)
        assert '输入证据从实际删改文字开始' in payload['validation_feedback']
        assert '一次完整文字替换' in payload['validation_feedback']
        assert '不能跨提交、换字段或其他独立业务操作合并' in payload['validation_feedback']
        fixed=copy.deepcopy(events)
        fixed[1]['frame_refs']=['f4','f6']
        return dict(events=[{k:v for k,v in e.items() if k not in {'start_ms','end_ms','evidence_kind'}} for e in fixed],
                    uncertainties=[],multiple_scenarios=False,coverage_resolutions=[])
    monkeypatch.setattr(ai,'run_ai_skill',model)
    result=review.reconcile(events,frames,800,tmp_path,lambda:False,[])
    assert len(calls)==1
    assert [e['action'] for e in result['events']]==['aiTap','aiInput']
    assert result['events'][0]['end_ms']<=result['events'][1]['start_ms']
    assert events==before
    for audited,original in zip(result['reconciliation_audit']['before_events'],before):
        assert {k:v for k,v in audited.items() if k!='uncertainties'}=={k:v for k,v in original.items() if k!='uncertainties'}


def test_short_flow_also_requests_overlap_repair(monkeypatch,tmp_path):
    from task_server.services import ui_video_windows as windows, ui_video_analysis as analysis
    events=interactions()
    frames=[dict(frame_id=f'f{i}',timestamp_ms=i*100) for i in range(1,8)]
    monkeypatch.setattr(windows,'observe_windows',lambda *a,**kw: (
        dict(events=events,uncertainties=[],multiple_scenarios=False),[]))
    def repair(old,checked,fs,duration,root,cancelled,secrets,refs):
        assert refs=={'f1','f2','f3','f4','f6'}
        raise RuntimeError('reached existing pixel repair')
    monkeypatch.setattr(review,'_repair_coverage',repair)
    with pytest.raises(RuntimeError,match='reached existing pixel repair'):
        analysis.analyze_whole_frames({'media':{'frames':frames,'duration_ms':800}},tmp_path,lambda:False)


def input_round():
    first={**interactions()[1],'target':'备注输入框','start_ms':100,'end_ms':300,'frame_refs':['f1','f3'],'observation':'清空旧文字'}
    second={**first,'start_ms':500,'end_ms':900,'frame_refs':['f5','f9'],'observation':'继续输入文字'}
    merged={**first,'end_ms':900,'frame_refs':['f1','f3','f5','f9'],'observation':'同一输入框的一轮连续编辑，清空后继续输入，最后文字可见'}
    return [first,second],merged


def test_continuous_input_requests_pixels_and_covers_each_edit_endpoint():
    old,new=input_round();before=copy.deepcopy(old)
    assert review._overlapping_input_refs(old)=={'f1','f3','f5','f9'}
    review.check_coverage(old,[new],1000)
    from task_server.services.ui_video_business_inputs import _MANUAL
    new['uncertainties']=[_MANUAL]
    review.check_coverage(old,[new],1000)
    assert old==before


@pytest.mark.parametrize('change',['different_field','intervening_tap','intervening_state','missing_anchor','truncated_time','uncertain','no_continuity','separate_round'])
def test_input_round_cannot_hide_independent_or_unproved_edits(change):
    old,new=input_round()
    if change=='different_field':old[1]['target']='标题输入框'
    elif change in {'intervening_tap','intervening_state'}:
        old.insert(1,{**old[0],'action':'aiTap' if change=='intervening_tap' else 'aiAssert','target':'预览','frame_refs':['f4'],'start_ms':400,'end_ms':400})
    elif change=='missing_anchor':new['frame_refs'].remove('f3')
    elif change=='truncated_time':new['start_ms']=200
    elif change=='uncertain':new['uncertainties']=['无法确认是否为同一轮输入']
    elif change=='no_continuity':new['observation']='文字可见'
    else:new['observation']='同一输入框连续编辑，但随后重新进入输入框开始第二轮输入'
    with pytest.raises(ValueError,match='video_required_action_missing'):review.check_coverage(old,[new],1000)


@pytest.mark.parametrize('note',[
 '并非同一输入框的一轮连续编辑，实际为分开输入。',
 '不是同一输入框连续编辑，两个独立输入操作。',
 '同一输入框连续编辑，但中途点击搜索提交后继续输入。',
 '同一输入框连续编辑，切换页面后继续输入。',
])
def test_input_round_rejects_negated_or_contradictory_continuity(note):
    old,new=input_round();new['observation']=note
    with pytest.raises(ValueError,match='video_required_action_missing'):review.check_coverage(old,[new],1000)


@pytest.mark.parametrize('length',[25,41])
def test_separate_input_repairs_keep_each_window_budget(monkeypatch,tmp_path,length):
    frames=[{'frame_id':f'f{i}','timestamp_ms':i*100} for i in range(120)]
    events=[]
    for start in (0,70):
        for first,last in ((start,start+10),(start+12,start+length-1)):
            events.append(dict(action='aiInput',target=f'输入框{start}',value='',start_ms=first*100,end_ms=last*100,
                frame_refs=[f'f{first}',f'f{last}'],observation='文字编辑',uncertainties=[],evidence_kind='inferred'))
    calls=[]
    def repair(old,checked,window,*args,**kw):
        calls.append(len(window));assert len(window)<=40
        return {**checked,'reconciliation_audit':{'coverage_resolutions':[],'model_attempts':1}}
    monkeypatch.setattr(review,'_repair_window',repair)
    checked={'events':events,'uncertainties':[],'multiple_scenarios':False}
    if length==41:
        with pytest.raises(ValueError):review._repair_coverage(events,checked,frames,12000,tmp_path,lambda:False,[],review._overlapping_input_refs(events))
        assert not calls
    else:
        result=review._repair_coverage(events,checked,frames,12000,tmp_path,lambda:False,[],review._overlapping_input_refs(events))
        assert calls==[25,25]
        assert [{k:v for k,v in e.items() if k!='uncertainties'} for e in result['events']]==[{k:v for k,v in e.items() if k!='uncertainties'} for e in events]


@pytest.mark.parametrize('note',[
 '同一输入框的一轮连续编辑，但中途点击提交没有响应后继续输入。',
 '同一输入框连续编辑，但实际为两轮编辑。',
 '同一输入框连续编辑，但用户点击预览后继续修改。',
 '同一输入框连续编辑，点击删除键后点击预览再修改。',
 '同一输入框连续编辑，但并非同一轮编辑。',
 '同一输入框连续编辑，点击键盘上方的预览按钮后继续修改。',
 '同一输入框连续编辑，点击输入法旁边的业务按钮后继续修改。',
 '同一输入框连续编辑，并非没有提交。',
 '同一输入框连续编辑，点击删除键旁的预览按钮后继续修改。',
])
def test_input_round_does_not_negate_action_when_response_is_missing(note):
    old,new=input_round();new['observation']=note
    with pytest.raises(ValueError,match='video_required_action_missing'):review.check_coverage(old,[new],1000)


@pytest.mark.parametrize('note',[
 '同一输入框的一轮连续编辑。全过程未离开当前页面或切换字段。',
 '同一输入框的一轮连续编辑。点击删除键后继续输入。全过程未离开当前字段或提交。',
 '同一输入框的一轮连续编辑。从开始点击删除键清空原有文字，随后切换键盘布局继续输入。全过程未离开当前字段或提交。',
])
def test_input_round_allows_explicit_absence_of_business_boundary(note):
    old,new=input_round();new['observation']=note
    review.check_coverage(old,[new],1000)
