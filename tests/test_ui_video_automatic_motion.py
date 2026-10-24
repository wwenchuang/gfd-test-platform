"""Suspicious refresh motion requests pixels, never deletes operations."""
import pytest
from task_server.services.ui_video_automatic_motion import review_refs


@pytest.mark.parametrize('note,expected',[
 ('页面向上滚动，顶部刷新完成提示区域移出屏幕，功能图标整体上移',True),
 ('刷新结束后头部自动回弹，内容上移',True),
 ('页面发生自动布局变化',True),
 ('刷新完成，用户再次按下拖动页面向上滚动',False),
 ('页面上移显示下一张卡片',False),
 ('手指松开后惯性滚动继续',False),
 ('页面没有自动回弹，用户拖动列表',False),
])
def test_motion_review_is_narrow_and_never_changes_event(note,expected):
    event={'action':'aiScroll','observation':note,'frame_refs':['f1','f2']}
    assert review_refs([event])==({'f1','f2'} if expected else set())
    assert event['action']=='aiScroll' and event['observation']==note


def test_assertions_and_clicks_do_not_trigger_scroll_review():
    assert not review_refs([{'action':action,'observation':'刷新提示自动收起','frame_refs':['f1']} for action in ['aiAssert','aiTap','aiInput']])


def test_reconciliation_combines_motion_refs_in_one_repair_budget(monkeypatch,tmp_path):
    from task_server.services import ui_video_reconcile as reconcile,ui_video_windows as windows
    event={'action':'aiScroll','target':'页面','value':{'direction':'down','scrollType':'singleAction'},
           'frame_refs':['f1','f2'],'start_ms':100,'end_ms':200,'observation':'刷新完成提示区域移出屏幕，内容上移','uncertainties':[]}
    checked={'events':[event],'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(windows,'observe_windows',lambda *a,**kw:(checked,[]))
    monkeypatch.setattr(reconcile,'_overlapping_gesture_refs',lambda events:{'f3'})
    calls=[]
    def repair(*args):calls.append(args[-1]);return checked
    monkeypatch.setattr(reconcile,'_repair_coverage',repair)
    reconcile.reconcile([event],[{'frame_id':f'f{i}','timestamp_ms':i*100} for i in range(1,4)],1000,tmp_path,lambda:False,[])
    assert calls==[{'f1','f2','f3'}]


def test_motion_review_receives_prior_refresh_context_as_read_only(monkeypatch,tmp_path):
    from task_server.services import ui_video_reconcile as reconcile,ui_video_windows as windows,ai_skill_service as ai
    frames=[{'frame_id':f'f{i}','timestamp_ms':i*100} for i in range(41)]
    event={'action':'aiScroll','target':'页面','value':{'direction':'down','scrollType':'singleAction'},
           'frame_refs':['f20','f21'],'start_ms':2000,'end_ms':2100,
           'observation':'刷新完成提示区域移出屏幕，内容上移','uncertainties':[]}
    monkeypatch.setattr(windows,'load_assets',lambda *a:[])
    class StopProbe(Exception):pass
    def inspect(*args,**kw):
        payload=args[1]
        early=[ref for ref in payload['context_frame_ids'] if int(ref[1:])<=5]
        assert early
        assert not set(early)&{f['frame_id'] for f in payload['frames']}
        assert len(payload['context_frame_ids'])<=16
        assert 'motion_review_question' in payload
        assert all('action' not in anchor for anchors in payload['coverage_checks'].values() for anchor in anchors)
        assert payload['gesture_hypotheses']==[]
        assert '不能仅以内容' in payload['validation_feedback']
        assert kw['version']=='coverage-observe'
        raise StopProbe()
    monkeypatch.setattr(ai,'run_ai_skill',inspect)
    with pytest.raises(StopProbe):reconcile._repair_window([event],{'events':[event]},frames,5000,tmp_path,lambda:False,[],{'f20','f21'})

@pytest.mark.parametrize('observation,accepted',[
 ('刷新头部自动收起，而非用户发起的独立滚动操作。',True),
 ('刷新结束后的自动布局恢复，而非新的用户滚动操作。',True),
 ('自动布局变化，未见独立用户滚动。',True),
 ('自动布局重排，并非用户发起了新的独立滚动操作。',True),
 ('页面自动回弹，未见独立的用户手指拖动或新的滚动操作证据。',True),
 ('自动布局变化，未见触点，但用户发起了滚动。',False),
 ('无法确认是否为自动布局变化或用户滚动。',False),
])
def test_full_evidence_scroll_correction_accepts_explicit_negative_only(observation,accepted):
    from task_server.services.ui_video_reconcile import check_coverage
    old={'action':'aiScroll','target':'列表','value':{'direction':'down','scrollType':'singleAction'},'frame_refs':['f1','f2'],
         'start_ms':100,'end_ms':200,'observation':'内容上移','uncertainties':[]}
    new={**old,'action':'aiAssert','value':'','observation':observation,'uncertainties':[]}
    if accepted:check_coverage([old],[new],1000)
    else:
        with pytest.raises(ValueError,match='video_required_action_missing'):check_coverage([old],[new],1000)

@pytest.mark.parametrize('note',[
 '自动布局变化，未见独立用户滚动，但随后用户重新按下并拖动列表。',
 '刷新头部自动收起，而非用户滚动；随后又进行了拖动。',
 '未见用户滚动，自动回弹后用户发起滚动。',
 '自动布局变化，未见独立用户滚动。随后触点没有消失且用户继续拖动列表。',
])
def test_automatic_correction_rejects_later_positive_drag(note):
    from task_server.services.ui_video_reconcile import check_coverage
    old={'action':'aiScroll','target':'列表','value':{'direction':'down','scrollType':'singleAction'},'frame_refs':['f1','f2'],
         'start_ms':100,'end_ms':200,'observation':'内容上移','uncertainties':[]}
    new={**old,'action':'aiAssert','value':'','observation':note,'uncertainties':[]}
    with pytest.raises(ValueError,match='video_required_action_missing'):check_coverage([old],[new],1000)


def test_single_window_still_reviews_automatic_motion(monkeypatch,tmp_path):
    from task_server.services import ui_video_analysis as analysis,ui_video_reconcile as reconcile,ui_video_windows as windows
    event={'action':'aiScroll','target':'列表','value':{'direction':'down','scrollType':'singleAction'},'frame_refs':['f1','f2'],
           'start_ms':100,'end_ms':200,'observation':'刷新完成提示区域消失，内容上移','uncertainties':[]}
    observed={'events':[event],'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(windows,'observe_windows',lambda *a,**kw:(observed,[]))
    monkeypatch.setattr(reconcile,'needs_reconciliation',lambda *a:False)
    calls=[]
    class StopProbe(Exception):pass
    def repair(*args):
        calls.append(args[-1]);raise StopProbe()
    monkeypatch.setattr(reconcile,'_repair_coverage',repair)
    doc={'media':{'frames':[{'frame_id':'f1','timestamp_ms':100},{'frame_id':'f2','timestamp_ms':200}],'duration_ms':1000}}
    with pytest.raises(StopProbe):analysis.analyze_whole_frames(doc,tmp_path,lambda:False)
    assert calls==[{'f1','f2'}]


def test_negative_movement_without_conflict_is_not_positive():
    from task_server.services.ui_video_automatic_motion import has_positive_drag
    assert not has_positive_drag('用户没有拖动列表；刷新头部自动收起。')
