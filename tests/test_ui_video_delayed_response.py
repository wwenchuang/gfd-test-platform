import pytest
from task_server.services.ui_video_delayed_response import review_refs


def event(action,refs,start,end):
    return {'action':action,'frame_refs':refs,'start_ms':start,'end_ms':end,'target':'任意控件','observation':'原图观察'}


def test_new_touch_before_existing_response_requests_both_without_merging():
    old=[event('aiAssert',['a','b'],100,200),event('aiTap',['c','d'],400,500)]
    checked=[event('aiTap',['a','b'],100,200),event('aiTap',['c'],400,400)]
    assert review_refs(old,checked)=={'a','b','c'}
    assert len(checked)==2 and all(x['action']=='aiTap' for x in checked)

@pytest.mark.parametrize('change', ['original_tap','long_gap','intervening_input','no_old_state','no_old_later_tap'])
def test_unrelated_operations_do_not_request_delayed_response_review(change):
    old=[event('aiAssert',['a','b'],100,200),event('aiTap',['c','d'],400,500)]
    checked=[event('aiTap',['a','b'],100,200),event('aiTap',['c'],400,400)]
    if change=='original_tap':old[0]['action']='aiTap'
    if change=='long_gap':checked[1]['start_ms']=1000
    if change=='intervening_input':checked.insert(1,event('aiInput',['x'],300,300))
    if change=='no_old_state':old.pop(0)
    if change=='no_old_later_tap':old[1]['action']='aiAssert'
    assert not review_refs(old,checked)


def test_new_touch_review_shares_existing_repair_budget(monkeypatch,tmp_path):
    from task_server.services import ui_video_reconcile as reconcile,ui_video_windows as windows
    old=[event('aiAssert',['a','b'],100,200),event('aiTap',['c','d'],400,500)]
    checked={'events':[event('aiTap',['a','b'],100,200),event('aiTap',['c'],400,400)],'uncertainties':[]}
    monkeypatch.setattr(windows,'observe_windows',lambda *a,**kw:(checked,[]))
    monkeypatch.setattr(reconcile,'check_coverage',lambda *a:None)
    for e in old+checked['events']:e.update(value='',uncertainties=[])
    calls=[]
    def repair(*args):calls.append(args[-1]);return checked
    monkeypatch.setattr(reconcile,'_repair_coverage',repair)
    reconcile.reconcile(old,[],1000,tmp_path,lambda:False,[])
    assert calls==[{'a','b','c'}]


def test_review_preserves_two_real_taps_when_pixels_confirm_retry(monkeypatch,tmp_path):
    from task_server.services import ui_video_reconcile as reconcile,ui_video_windows as windows,ai_skill_service as ai
    frames=[{'frame_id':chr(97+i),'timestamp_ms':i*100} for i in range(6)]
    old=[event('aiAssert',['b','c'],100,200),event('aiTap',['d','e'],300,400)]
    reviewed=[event('aiTap',['b','c'],100,200),event('aiTap',['d','e'],300,400)]
    for e in old+reviewed:e.update(value='',uncertainties=[],evidence_kind='inferred')
    monkeypatch.setattr(windows,'load_assets',lambda *a:[])
    def model(*args,**kw):
        assert 'delayed_response_question' in args[1]
        assert all('action' not in a for group in args[1]['coverage_checks'].values() for a in group)
        return {'events':[{'action':'aiTap','target':'按钮','value':'','frame_refs':refs,
                          'observation':note,'uncertainties':[]} for refs,note in [(['b','c'],'触点按下并松开，按钮无响应'),(['d','e'],'新的触点再次按下，重试点击后页面打开')]],
                'uncertainties':[],'multiple_scenarios':False,'coverage_resolutions':[]}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    result=reconcile._repair_window(old,{'events':reviewed,'uncertainties':[],'multiple_scenarios':False},frames,1000,tmp_path,lambda:False,[],{'b','c','d','e'})
    assert [e['action'] for e in result['events']]==['aiTap','aiTap']
    assert result['reconciliation_audit']['model_attempts']==1

@pytest.mark.parametrize('note',[
 '自动转场，未见新点击，但随后用户再次按下按钮重试。',
 '未见新点击，自动加载，触点没有消失且用户再次点击。',
])
def test_contradictory_automatic_state_cannot_remove_retry(note):
    from task_server.services.ui_video_reconcile import check_coverage
    old={**event('aiTap',['b','c'],100,200),'value':'','uncertainties':[]}
    new={**old,'action':'aiAssert','observation':note,'uncertainties':[]}
    with pytest.raises(ValueError,match='video_required_action_missing'):check_coverage([old],[new],1000)


def test_negated_release_and_press_does_not_hide_later_real_click():
    from task_server.services.ui_video_delayed_response import has_positive_tap
    assert not has_positive_tap('未见独立点击，未见松开后重新按下')
    assert has_positive_tap('未见松开后重新按下，但用户明确点击按钮没有响应')


def test_negated_coordinated_actions_are_not_positive_clicks():
    from task_server.services.ui_video_delayed_response import has_positive_tap,has_drag_conflict
    note='未见松开后重新按下或独立点击事件'
    assert not has_positive_tap(note)
    assert not has_drag_conflict(note)
    assert has_drag_conflict(note+'，但独立点击无响应')
