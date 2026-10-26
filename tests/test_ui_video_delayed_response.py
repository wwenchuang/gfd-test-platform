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


@pytest.mark.parametrize('change',['delayed','real_retry','too_late','input_between','not_confirmation'])
def test_confirmation_followed_by_page_response_requires_original_pixel_review(change):
    from task_server.services.ui_video_delayed_response import review_refs
    frames=[{'frame_id':f'f{i}','timestamp_ms':i*200} for i in range(20)]
    first={**event('aiTap',['f1','f3'],200,600),'target':'耗材确认弹窗确认按钮','observation':'点击弹窗确认，弹窗关闭，返回编辑页'}
    second={**event('aiTap',['f11','f12'],2200,2400),'target':'下一步按钮','observation':'页面跳转预览'}
    opener={**event('aiTap',['f0'],0,0),'target':'下一步按钮','observation':'点击下一步后弹出确认对话框'}
    reviewed=[opener,first,second]
    if change=='real_retry':second['observation']='用户再次按下下一步，真实重试'
    if change=='too_late':second.update(start_ms=3800,end_ms=3900,frame_refs=['f19'])
    if change=='input_between':reviewed.insert(2,{**event('aiInput',['f4'],800,800),'target':'标题'})
    if change=='not_confirmation':first.update(target='返回按钮',observation='返回上一级')
    from task_server.services.ui_video_delayed_response import pending_response_checks
    checks=pending_response_checks(reviewed,frames)
    refs={r for check in checks for r in check['context_frame_ids']}
    if change in {'delayed','real_retry'}:
        # Both hypotheses get visual review; wording never silently erases retry.
        assert refs=={f'f{i}' for i in range(13)}
    else:assert not refs


def test_cancel_completed_result_followed_by_return_is_not_pending_confirmation():
    first={**event('aiTap',['f1','f3'],200,600),'target':'确定按钮',
           'observation':'点击取消确认弹窗确定，弹窗消失，页面跳转至显示已取消模型处理的结果页'}
    second={**event('aiTap',['f5','f6'],1000,1200),'target':'返回按钮'}
    from task_server.services.ui_video_delayed_response import pending_response_checks
    assert not pending_response_checks([first,second],[])


@pytest.mark.parametrize('actual',['automatic','real_click','contradictory','foreign'])
def test_response_review_preserves_real_click_or_requires_explicit_automatic_evidence(monkeypatch,tmp_path,actual):
    from task_server.services.ui_video_delayed_response import review_pending_responses
    from task_server.services import ui_video_windows,ai_skill_service as ai
    fs=[{'frame_id':f'f{i}','timestamp_ms':i*200} for i in range(14)]
    es=[{**event('aiTap',['f0'],0,0),'target':'提交按钮','observation':'点击提交后显示确认弹窗'},
        {**event('aiTap',['f1','f3'],200,600),'target':'确认按钮','observation':'确认弹窗消失，返回表单'},
        {**event('aiTap',['f11','f12'],2200,2400),'target':'提交按钮','observation':'表单发生转场'}]
    for e in es:e.update(value='',uncertainties=[],evidence_kind='inferred')
    observed={'events':es,'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(ui_video_windows,'load_assets',lambda *a:[])
    note='原提交确认后继续处理，未见新点击，页面自动跳转'
    action='aiAssert'
    if actual=='real_click':action='aiTap';note='新的独立触点按下并松开，按钮被再次点击'
    if actual=='contradictory':note+='，但用户再次点击按钮'
    refs=['f11','f12'] if actual!='foreign' else ['f0']
    monkeypatch.setattr(ai,'run_ai_skill',lambda *a,**k:{'events':[{'action':action,'target':'candidate:2','value':'',
        'frame_refs':refs,'observation':note,'uncertainties':[]}],'uncertainties':[],'multiple_scenarios':False})
    import copy
    before=copy.deepcopy(observed)
    if actual in {'contradictory','foreign'}:
        with pytest.raises(ValueError):review_pending_responses(observed,{'media':{'frames':fs,'duration_ms':2800}},tmp_path,lambda:False)
    else:
        out=review_pending_responses(observed,{'media':{'frames':fs,'duration_ms':2800}},tmp_path,lambda:False)
        assert out['events'][2]['action']==action
        assert out['events'][2]['target']=='提交按钮'
        assert out['events'][:2]==es[:2]
    assert observed==before


def test_response_observation_cannot_reintroduce_known_input(monkeypatch,tmp_path):
    from task_server.services.ui_video_delayed_response import review_pending_responses
    from task_server.services import ui_video_windows,ai_skill_service as ai
    fs=[{'frame_id':f'f{i}','timestamp_ms':i*200} for i in range(14)]
    es=[{**event('aiTap',['f0'],0,0),'target':'提交按钮','observation':'打开确认弹窗'},
        {**event('aiTap',['f1','f3'],200,600),'target':'确认按钮','observation':'确认弹窗消失'},
        {**event('aiTap',['f11','f12'],2200,2400),'target':'提交按钮'}]
    for e in es:e.update(value='',uncertainties=[],evidence_kind='inferred')
    monkeypatch.setattr(ui_video_windows,'load_assets',lambda *a:[])
    monkeypatch.setattr(ai,'run_ai_skill',lambda *a,**k:{'events':[{'action':'aiAssert','target':'candidate:2','value':'',
        'frame_refs':['f11','f12'],'observation':'未见新点击，页面自动加载private-input-value','uncertainties':[]}],
        'uncertainties':[],'multiple_scenarios':False})
    out=review_pending_responses({'events':es,'uncertainties':[],'multiple_scenarios':False},
        {'media':{'frames':fs,'duration_ms':2800}},tmp_path,lambda:False,['private-input-value'])
    assert 'private-input-value' not in str(out)
    assert '[输入内容未保存]' in out['events'][2]['observation']


@pytest.mark.parametrize('note',[
 '未见针对“提交”按钮的新独立触点按下或松开操作，页面自动跳转。',
 '按钮区域无新的独立触点按下或高亮反馈，页面自动加载。',
])
def test_targeted_negation_does_not_invent_positive_touch(note):
    from task_server.services.ui_video_reconcile import check_coverage
    from task_server.services.ui_video_delayed_response import has_positive_tap
    assert not has_positive_tap(note)
    old={**event('aiTap',['b','c'],100,200),'value':'','uncertainties':[]}
    # Coverage still requires explicit negative evidence plus an automatic cause.
    new={**old,'action':'aiAssert','observation':'未见新点击。'+note,'uncertainties':[]}
    check_coverage([old],[new],1000)
    for suffix in ['但用户再次按下按钮重试。','随后独立触点按下按钮。','用户点击按钮没有响应。']:
        assert has_positive_tap(note+suffix)
        with pytest.raises(ValueError,match='video_required_action_missing'):
            check_coverage([old],[{**new,'observation':note+suffix}],1000)


def test_target_negation_cannot_swallow_feedback_clause_or_other_control():
    from task_server.services.ui_video_delayed_response import has_positive_tap
    assert has_positive_tap('未见针对提交按钮的响应，用户再次点击按钮。')
    assert has_positive_tap('未见针对“提交”按钮的新点击，但用户点击“取消”按钮。')
