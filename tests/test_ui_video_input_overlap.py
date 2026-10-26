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
