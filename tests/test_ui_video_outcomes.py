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


@pytest.mark.parametrize('action',['aiTap','aiWaitFor'])
@pytest.mark.parametrize('change',['action','value','unknown','duplicate','early','unknown_frame','future','system','empty','toast','uncertain','questions','malformed'])
def test_focus_rejects_invalid_result_without_retry(monkeypatch,tmp_path,change,action):
    from task_server.services.ui_video_outcomes import repair_outcomes
    from task_server.services import ai_skill_service as ai
    frames,candidates,plan=fixture(tmp_path);r=result();e=r['events'][0]
    candidates[0]['action']=plan['events'][0]['action']=action
    if change=='action':e['action']='aiTap'
    if change=='value':e['value']='secret'
    if change=='unknown':e['target']='candidate:9'
    if change=='duplicate':r['events'].append(copy.deepcopy(e))
    if change=='early':e['frame_refs']=['frame-001']
    if change=='unknown_frame':e['frame_refs']=['frame-999']
    if change=='future':e['frame_refs']=['frame-006']
    if change=='system':
        candidates.pop(1)
        e['frame_refs']=['frame-007']
    if change=='questions':r['uncertainties']=['无法确认结果']
    if change=='malformed':e['observation']=None
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


def test_visual_repair_cannot_erase_settings_to_evade_quality_gate(monkeypatch,tmp_path):
    from task_server.services.ui_video_outcomes import repair_outcomes
    from task_server.services import ai_skill_service as ai
    frames,candidates,plan=fixture(tmp_path)
    plan['events'][0]['outcome']={'observation':'打印设置面板显示彩色、单面、A4','frame_refs':['frame-004']}
    r=result();r['events'][0]['observation']='打印设置面板可见，确认打印按钮可见'
    monkeypatch.setattr(ai,'run_ai_skill',lambda *a,**kw:r)
    with pytest.raises(ValueError,match='video_expected_missing'):
        repair_outcomes(plan,candidates,frames,800,tmp_path,[0],lambda:False,[])


@pytest.mark.parametrize('guard',['template','selection','unsupported'])
def test_wait_runtime_objects_and_unsupported_actions_refuse_dispatch(tmp_path,guard):
    from task_server.services.ui_video_outcomes import repair_outcomes
    frames,candidates,plan=fixture(tmp_path)
    candidates[0]['action']=plan['events'][0]['action']='aiWaitFor'
    if guard=='template':plan['events'][0]['target']='{{本次对象}}完成'
    if guard=='selection':plan['events'][0]['selection']={'kind':'runtime-object'}
    if guard=='unsupported':plan['events'][0]['action']='aiInput'
    def forbidden(*a,**kw):pytest.fail('protected outcome dispatch')
    with pytest.raises(ValueError,match='video_expected_missing') as caught:
        repair_outcomes(plan,candidates,frames,800,tmp_path,[0],lambda:False,[],skill_runner=forbidden)
    assert caught.value.outcome_failure_evidence=={'candidate_index':0,'reason':guard}


def test_early_optional_wait_outcome_gets_one_original_frame_repair(tmp_path):
    from task_server.services.ui_video_analysis import consolidate_observed_frames
    frames,candidates,plan=fixture(tmp_path)
    candidates[0].update(action='aiWaitFor',target='模型预览显示',end_ms=500,
                         frame_refs=['frame-001','frame-005'])
    for e in candidates:
        e.update(value='',observation='原始页面状态',uncertainties=[],evidence_kind='inferred')
    candidates[0]['uncertainties']=['原始证据需复核']
    plan['events'][0].update(action='aiWaitFor',target='模型预览显示',
        outcome={'observation':'模型预览已显示','frame_refs':['frame-004']})
    plan['events'][1].update(outcome={'observation':'首页显示列表入口','frame_refs':['frame-006']})
    original=copy.deepcopy((frames,candidates,plan));calls=[]
    observed={'events':candidates,'uncertainties':['原始全局问题'],'multiple_scenarios':False}
    def model(skill,payload,**kw):
        calls.append((skill,kw['version']))
        if kw['version']=='consolidate':return copy.deepcopy(plan)
        assert skill=='ui_video_observe' and kw['version']=='outcome'
        assert kw['retry_count']==0
        assert payload['checks'][0]['outcome_frame_ids']==['frame-005']
        assert [a['name'] for a in kw['image_assets']]==['frame-001','frame-005']
        return {'events':[{'action':'aiAssert','target':'candidate:0','value':'',
            'frame_refs':['frame-005'],'observation':'模型预览已显示','uncertainties':[]}],
            'uncertainties':[],'multiple_scenarios':False}
    actual=consolidate_observed_frames({'app_id':'synthetic.app','goal':'查看预览',
        'media':{'frames':frames,'duration_ms':800}},tmp_path,lambda:False,observed,candidates,[],skill_runner=model)
    assert calls==[('ui_video_steps','consolidate'),('ui_video_observe','outcome')]
    assert (frames,candidates,plan)==original
    first=actual['events'][0]
    assert first['action']=='aiWaitFor' and first['target']=='模型预览显示' and first['value']==''
    assert (first['start_ms'],first['end_ms'],first['frame_refs'])==(100,500,['frame-001','frame-005'])
    assert first['outcome']=={'observation':'模型预览已显示','frame_refs':['frame-005']}
    assert first['reusable_expected']=='模型预览已显示'
    assert first['evidence_kind']=='inferred' and first['observation']==plan['events'][0]['observation']
    assert actual['raw_events']==candidates
    assert actual['raw_uncertainties']==['原始全局问题']


@pytest.mark.parametrize('expected',[
    '颜色确认弹窗关闭；若存在处理中任务则任务冲突弹窗可见',
    '正在处理中，随后进入预览页面',
    '任务冲突弹窗可见，搜索框推荐内容与当前页面相关',
])
def test_early_wait_and_independent_result_quality_share_one_repair(tmp_path,expected):
    from task_server.services.ui_video_analysis import consolidate_observed_frames
    frames,candidates,plan=fixture(tmp_path)
    candidates[0].update(action='aiWaitFor',target='模型预览显示',end_ms=500,
                         frame_refs=['frame-001','frame-005'])
    for e in candidates:
        e.update(value='',observation='原始页面状态',uncertainties=[],evidence_kind='inferred')
    candidates[0]['uncertainties']=['等待证据需复核']
    candidates[1]['uncertainties']=['原始任务状态需复核']
    plan['events'][0].update(action='aiWaitFor',target='模型预览显示',
        outcome={'observation':'模型预览已显示','frame_refs':['frame-004']})
    plan['events'][1].update(
        outcome={'observation':'任务冲突弹窗可见','frame_refs':['frame-006']},
        reusable_expected=expected)
    original=copy.deepcopy((frames,candidates,plan));calls=[];repaired=[]
    observed={'events':candidates,'uncertainties':['原始全局问题'],'multiple_scenarios':False,
              'trajectory_audit':{'checks':[]}}
    visible={0:('frame-005','模型预览已显示'),1:('frame-006','任务冲突弹窗可见')}
    def model(skill,payload,**kw):
        calls.append((skill,kw['version']))
        if kw['version']=='consolidate':return copy.deepcopy(plan)
        assert skill=='ui_video_observe' and kw['version']=='outcome'
        assert kw['retry_count']==0
        events=[]
        for check in payload['checks']:
            index=int(check['target'].split(':')[1]);ref,text=visible[index]
            assert ref in check['outcome_frame_ids']
            repaired.append(index)
            events.append({'action':'aiAssert','target':check['target'],'value':'',
                           'frame_refs':[ref],'observation':text,'uncertainties':[]})
        return {'events':events,'uncertainties':[],'multiple_scenarios':False}
    actual=consolidate_observed_frames({'app_id':'synthetic.app','goal':'查看预览',
        'media':{'frames':frames,'duration_ms':800}},tmp_path,lambda:False,observed,candidates,[],skill_runner=model)
    assert calls==[('ui_video_steps','consolidate'),('ui_video_observe','outcome')]
    assert repaired==[0,1]
    assert (frames,candidates,plan)==original
    for index,event in enumerate(actual['events']):
        for key in ('action','target','value','observation'):
            assert event[key]==plan['events'][index][key]
        for key in ('start_ms','end_ms','frame_refs','evidence_kind'):
            assert event[key]==candidates[index][key]
        assert event['outcome']=={'observation':visible[index][1],'frame_refs':[visible[index][0]]}
        assert event['reusable_expected']==visible[index][1]
        assert event['uncertainties']==candidates[index]['uncertainties']
    assert actual['raw_events']==candidates
    assert actual['raw_uncertainties']==['原始全局问题']
    assert actual['uncertainties']==['原始全局问题','等待证据需复核','原始任务状态需复核']
    assert actual['trajectory_audit']==observed['trajectory_audit']


@pytest.mark.parametrize('count',[8,9])
def test_result_repair_union_keeps_eight_check_limit_without_truncation(tmp_path,count):
    from task_server.services.ui_video_analysis import consolidate_observed_frames
    frames=[{'frame_id':f'frame-{i:03d}','timestamp_ms':i*100} for i in range(1,count*3+1)]
    (tmp_path/'frames').mkdir()
    for frame in frames:(tmp_path/'frames'/(frame['frame_id']+'.jpg')).write_bytes(b'image')
    candidates=[];events=[];visible={};calls=[]
    for index in range(count):
        start=index*300+100;end=start+100;ref=f'frame-{end//100:03d}'
        action='aiWaitFor' if index==0 else 'aiTap'
        candidates.append({'action':action,'target':'预览显示' if index==0 else '确认选项',
            'value':'','observation':'原始操作','start_ms':start,'end_ms':end,
            'frame_refs':[f'frame-{start//100:03d}',ref],'evidence_kind':'observed','uncertainties':[]})
        events.append({'candidate_index':index,'action':action,'target':candidates[-1]['target'],
            'value':'','observation':'原始操作','uncertainties':[],
            'outcome':{'observation':'预览页面可见','frame_refs':['frame-001' if index==0 else ref]},
            'reusable_expected':'预览页面可见' if index==0 else '若存在任务则弹窗可见'})
        visible[f'candidate:{index}']=ref
    plan={'events':events,'uncertainties':[],'multiple_scenarios':False}
    before=copy.deepcopy((frames,candidates,plan))
    def model(skill,payload,**kw):
        calls.append(kw['version'])
        if kw['version']=='consolidate':return copy.deepcopy(plan)
        assert len(payload['checks'])==8
        return {'events':[{'action':'aiAssert','target':check['target'],'value':'',
            'frame_refs':[visible[check['target']]],'observation':'预览页面可见','uncertainties':[]}
            for check in payload['checks']], 'uncertainties':[],'multiple_scenarios':False}
    def consolidate():
        return consolidate_observed_frames({'app_id':'synthetic.app','goal':'查看预览',
            'media':{'frames':frames,'duration_ms':count*300}},tmp_path,lambda:False,
            {'events':candidates,'uncertainties':[],'multiple_scenarios':False},candidates,[],skill_runner=model)
    if count==9:
        with pytest.raises(ValueError,match='^video_expected_missing$'):consolidate()
        assert calls==['consolidate']
    else:
        actual=consolidate()
        assert len(actual['events'])==8
        assert calls==['consolidate','outcome']
        assert all(event['reusable_expected']=='预览页面可见' for event in actual['events'])
    assert (frames,candidates,plan)==before


@pytest.mark.parametrize('concern',['conditional','sequential','incidental','missing'])
@pytest.mark.parametrize('corrected_retry',[False,True])
def test_legacy_unindexed_result_concern_keeps_quality_gate_and_planning_retry(tmp_path,concern,corrected_retry):
    from task_server.services.ui_video_analysis import consolidate_observed_frames
    frames,candidates,indexed=fixture(tmp_path)
    for event in candidates:
        event.update(value='',observation='原始页面状态',uncertainties=[],evidence_kind='observed')
    plan=copy.deepcopy(indexed)
    for event in plan['events']:
        candidate=candidates[event.pop('candidate_index')]
        event.update({key:copy.deepcopy(candidate[key]) for key in
                      ('start_ms','end_ms','frame_refs','evidence_kind')})
    plan['events'][0].update(outcome={'observation':'反馈页面可见','frame_refs':['frame-004']},
        reusable_expected={'conditional':'若存在任务则任务弹窗可见',
            'sequential':'正在处理中，随后进入预览页面',
            'incidental':'反馈页面可见，搜索框推荐内容与当前页面相关',
            'missing':'反馈页面可见'}[concern])
    if concern=='missing':
        plan['events'][0].pop('outcome');plan['events'][0].pop('reusable_expected')
        plan['events'][0]['observation']='加载中'
    plan['events'][1]['outcome']={'observation':'首页显示列表入口','frame_refs':['frame-006']}
    before=copy.deepcopy((candidates,plan));calls=[]
    def model(skill,payload,**kw):
        calls.append((skill,kw['version']))
        assert skill=='ui_video_steps' and kw['version']=='consolidate'
        proposal=copy.deepcopy(plan)
        if len(calls)==2:
            assert payload['validation_feedback']
            assert payload['result_check_candidate_indexes']==[]
            if corrected_retry:
                proposal['events'][0].update(outcome={'observation':'反馈页面可见','frame_refs':['frame-004']},
                                             reusable_expected='反馈页面可见')
        return proposal
    def consolidate():
        return consolidate_observed_frames({'app_id':'synthetic.app','goal':'查看反馈',
            'media':{'frames':frames,'duration_ms':800}},tmp_path,lambda:False,
            {'events':candidates,'uncertainties':[],'multiple_scenarios':False},candidates,[],skill_runner=model)
    if corrected_retry:
        actual=consolidate()
        assert actual['events'][0]['reusable_expected']=='反馈页面可见'
    else:
        with pytest.raises(ValueError,match='^video_expected_missing$'):consolidate()
    assert calls==[('ui_video_steps','consolidate')]*2
    assert (candidates,plan)==before
