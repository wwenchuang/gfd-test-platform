import copy
import pytest


def fixture(tmp_path):
    frames=[{'frame_id':f'frame-{i:03d}','timestamp_ms':i*100} for i in range(1,8)]
    (tmp_path/'frames').mkdir()
    for f in frames:(tmp_path/'frames'/(f['frame_id']+'.jpg')).write_bytes(b'image')
    candidates=[{'action':'aiTap','target':'提交反馈','start_ms':100,'end_ms':200,'frame_refs':['frame-001','frame-002']},
                {'action':'aiTap','target':'返回','start_ms':500,'end_ms':600,'frame_refs':['frame-005','frame-006']},
                {'action':'aiAssert','target':'系统桌面','observation':'系统桌面可见','start_ms':700,'end_ms':700,'frame_refs':['frame-007']}]
    plan={'title':'提交反馈后返回','events':[{'candidate_index':0,'action':'aiTap','target':'提交反馈','value':'','observation':'历史toast','uncertainties':[]},
                                          {'candidate_index':1,'action':'aiTap','target':'返回','value':'','observation':'历史导航','uncertainties':[]}],
          'uncertainties':[],'multiple_scenarios':False}
    return frames,candidates,plan


def result():
    return {'events':[{'action':'aiAssert','target':'candidate:0','value':'','frame_refs':['frame-004'],
                       'observation':'提交反馈按钮已禁用，原因选项仍保持选中','uncertainties':[]}],
            'uncertainties':[],'multiple_scenarios':False}


def test_focus_changes_only_missing_outcome_and_keeps_original_plan(monkeypatch,tmp_path):
    from task_server.services.ui_video_outcomes import repair_outcomes
    from task_server.services import ai_skill_service as ai
    frames,candidates,plan=fixture(tmp_path);before=copy.deepcopy(plan);calls=[]
    def model(skill,payload,**kw):
        calls.append(payload)
        assert skill=='ui_video_observe' and kw['version']=='outcome'
        assert payload['checks'][0]['outcome_frame_ids']==[f'frame-{i:03d}' for i in range(2,6)]
        assert 'frame-007' not in [a['name'] for a in kw['image_assets']]
        return result()
    monkeypatch.setattr(ai,'run_ai_skill',model)
    actual,images=repair_outcomes(plan,candidates,frames,800,tmp_path,[0],lambda:False,[])
    assert len(calls)==1 and plan==before and actual['events'][1]==before['events'][1]
    assert actual['events'][0]['observation']=='历史toast'
    assert actual['events'][0]['outcome']['frame_refs']==['frame-004']
    assert actual['events'][0]['reusable_expected']=='提交反馈按钮已禁用，原因选项仍保持选中'


@pytest.mark.parametrize('change',['action','value','unknown','duplicate','future','empty','toast','uncertain'])
def test_focus_rejects_invalid_result_without_retry(monkeypatch,tmp_path,change):
    from task_server.services.ui_video_outcomes import repair_outcomes
    from task_server.services import ai_skill_service as ai
    frames,candidates,plan=fixture(tmp_path);r=result();e=r['events'][0]
    if change=='action':e['action']='aiTap'
    if change=='value':e['value']='secret'
    if change=='unknown':e['target']='candidate:9'
    if change=='duplicate':r['events'].append(copy.deepcopy(e))
    if change=='future':e['frame_refs']=['frame-007']
    if change=='empty':r['events']=[]
    if change=='toast':e['observation']='显示反馈成功Toast提示'
    if change=='uncertain':e['uncertainties']=['无法确认']
    calls=[]
    def model(*a,**kw):calls.append(1);return r
    monkeypatch.setattr(ai,'run_ai_skill',model)
    with pytest.raises(ValueError):repair_outcomes(plan,candidates,frames,800,tmp_path,[0],lambda:False,[])
    assert len(calls)==1


def test_focus_cancellation_and_dynamic_objects_do_not_call_model(monkeypatch,tmp_path):
    from task_server.services.ui_video_outcomes import repair_outcomes
    from task_server.services import ai_skill_service as ai
    frames,candidates,plan=fixture(tmp_path)
    monkeypatch.setattr(ai,'run_ai_skill',lambda *a,**kw:pytest.fail('unexpected AI call'))
    with pytest.raises(ValueError,match='video_cancelled'):repair_outcomes(plan,candidates,frames,800,tmp_path,[0],lambda:True,[])
    plan['events'][0]['target']='{{本次对象}}消失'
    with pytest.raises(ValueError,match='video_expected_missing'):repair_outcomes(plan,candidates,frames,800,tmp_path,[0],lambda:False,[])


def test_focus_returns_exact_sent_evidence_and_redacts_request(monkeypatch,tmp_path):
    from task_server.services.ui_video_outcomes import repair_outcomes
    from task_server.services import ai_skill_service as ai
    frames,candidates,plan=fixture(tmp_path)
    plan['events'][0]['target']='提交 TOPSECRET'
    def model(skill,payload,**kw):
        assert 'TOPSECRET' not in str(payload)
        return result()
    monkeypatch.setattr(ai,'run_ai_skill',model)
    repaired,images=repair_outcomes(plan,candidates,frames,800,tmp_path,[0],lambda:False,['TOPSECRET'])
    assert [f['frame_id'] for f in images]==[f'frame-{i:03d}' for i in range(1,6)]
    assert repaired['events'][0]['outcome']['frame_refs']==['frame-004']
