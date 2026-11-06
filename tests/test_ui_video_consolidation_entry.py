"""The shared consolidation entry retains the existing planning gates."""
import copy

import pytest


@pytest.fixture
def consolidation(tmp_path):
    frames=[{'frame_id':f'frame-{i:03d}','timestamp_ms':i*100} for i in range(1,9)]
    (tmp_path/'frames').mkdir()
    for frame in frames:
        (tmp_path/'frames'/(frame['frame_id']+'.jpg')).write_bytes(b'image')
    events=[{'action':'aiInput','target':target,'value':'','frame_refs':[frames[i]['frame_id']],
             'start_ms':frames[i]['timestamp_ms'],'end_ms':frames[i]['timestamp_ms'],
             'evidence_kind':'inferred','observation':'输入框显示','uncertainties':[]}
            for i,target in ((0,'姓名输入框'),(3,'标题输入框'))]
    observed={'events':events,'uncertainties':[],'multiple_scenarios':False}
    document={'app_id':'com.example.app','goal':'','media':{'frames':frames,'duration_ms':1000},
              'baseline_examples':[{'title':'sample'}]}
    return document,tmp_path,observed


@pytest.mark.parametrize('audit',[None,{'method':'trajectory','windows':[{'refs':['frame-001'],'status':'verified'}]}])
def test_shared_consolidation_keeps_required_actions_and_optional_audit(monkeypatch,consolidation,audit):
    from task_server.services import ui_video_analysis as va,ai_skill_service as ai
    document,root,observed=consolidation
    if audit is not None:observed['trajectory_audit']=copy.deepcopy(audit)
    first_pass=copy.deepcopy(observed['events']);secrets=[]
    before=copy.deepcopy((document,observed,first_pass,secrets));calls=[]
    def model(skill,payload,**kwargs):
        calls.append(payload)
        assert skill=='ui_video_steps' and kwargs['version']=='consolidate'
        assert kwargs['timeout']==210 and kwargs['retry_count']==0
        assert payload['required_candidate_indexes']==[0,1]
        assert payload['baseline_examples']==document['baseline_examples']
        return {'title':'填写信息','events':[{'candidate_index':i,'action':'aiInput','target':e['target'],
                'value':'','observation':'输入框显示','uncertainties':[]} for i,e in enumerate(observed['events'])],
                'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    result=va.consolidate_observed_frames(document,root,lambda:False,observed,first_pass,secrets)
    assert len(calls)==1 and len(result['events'])==2
    assert (document,observed,first_pass,secrets)==before
    assert ('trajectory_audit' in result)==(audit is not None)
    if audit is not None:
        assert result['trajectory_audit']==audit
        result['trajectory_audit']['windows'][0]['status']='changed'
        assert observed['trajectory_audit']==audit


def test_shared_consolidation_rejects_missing_required_interaction(monkeypatch,consolidation):
    from task_server.services import ui_video_analysis as va,ai_skill_service as ai
    document,root,observed=consolidation;calls=[]
    def model(skill,payload,**kwargs):
        calls.append(payload)
        return {'title':'填写信息','events':[{'candidate_index':0,'action':'aiInput','target':'姓名输入框',
                'value':'','observation':'输入框显示','uncertainties':[]}],
                'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    with pytest.raises(ValueError,match='video_required_action_missing'):
        va.consolidate_observed_frames(document,root,lambda:False,observed,copy.deepcopy(observed['events']),[])
    assert len(calls)==2 and calls[1]['validation_feedback']


def test_controlled_runner_retains_existing_consolidation_timeout(consolidation):
    from task_server.services import ui_video_analysis as va
    document,root,observed=consolidation
    def runner(skill,payload,**kwargs):
        assert kwargs['timeout']==120 and kwargs['retry_count']==0
        return {'title':'填写信息','events':[{'candidate_index':i,'action':'aiInput','target':e['target'],
                'value':'','observation':'输入框显示','uncertainties':[]} for i,e in enumerate(observed['events'])],
                'uncertainties':[],'multiple_scenarios':False}
    result=va.consolidate_observed_frames(document,root,lambda:False,observed,copy.deepcopy(observed['events']),[],skill_runner=runner)
    assert len(result['events'])==2


def test_unexplained_exit_prompt_survives_planner_omission(monkeypatch,consolidation):
    from task_server.services import ui_video_analysis as va,ai_skill_service as ai
    document,root,observed=consolidation
    observed['events'].append({'action':'aiAssert','target':'首页退出提示','value':'','frame_refs':['frame-008'],
        'start_ms':800,'end_ms':800,'evidence_kind':'inferred',
        'observation':'页面显示“再试一次退出智小白3DAPP”的提示','uncertainties':[]})
    def model(skill,payload,**kwargs):
        return {'title':'填写信息','events':[{'candidate_index':i,'action':'aiInput','target':e['target'],
                'value':'','observation':'输入框显示','uncertainties':[]} for i,e in enumerate(observed['events'][:2])],
                'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    result=va.consolidate_observed_frames(document,root,lambda:False,observed,copy.deepcopy(observed['events']),[])
    assert any('遗漏触发提示的返回操作' in q for q in result['uncertainties'])


def test_first_pass_exit_prompt_is_not_lost_during_reconciliation(monkeypatch,consolidation):
    from task_server.services import ui_video_analysis as va,ai_skill_service as ai
    document,root,observed=consolidation
    first_pass=copy.deepcopy(observed['events'])+[{'action':'aiAssert','target':'首页退出提示','value':'','frame_refs':['frame-008'],
        'start_ms':800,'end_ms':800,'evidence_kind':'inferred',
        'observation':'页面显示“再试一次退出智小白3DAPP”的提示','uncertainties':[]}]
    def model(skill,payload,**kwargs):
        return {'title':'填写信息','events':[{'candidate_index':i,'action':'aiInput','target':e['target'],
                'value':'','observation':'输入框显示','uncertainties':[]} for i,e in enumerate(observed['events'])],
                'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    result=va.consolidate_observed_frames(document,root,lambda:False,observed,first_pass,[])
    assert any('遗漏触发提示的返回操作' in q for q in result['uncertainties'])


@pytest.mark.parametrize('top_count',[0,30])
def test_trajectory_independent_blockers_survive_planner_omission(monkeypatch,consolidation,top_count):
    from task_server.services import ui_video_analysis as va,ai_skill_service as ai
    document,root,observed=consolidation
    blockers=['独立证据复核：触点目标尚不能确认','独立证据复核：输入后的状态尚不能确认']
    observed['uncertainties']=[f'整体待核对问题{i}' for i in range(top_count)]
    for event,blocker in zip(observed['events'],blockers):event['uncertainties']=[blocker]
    observed['trajectory_audit']={'version':'trajectory-v1','checks':[
        {'event_id':f'event-{i}','status':'needs_review','question':blocker.split('：',1)[1]}
        for i,blocker in enumerate(blockers)]}
    before=copy.deepcopy(observed)
    def model(skill,payload,**kwargs):
        return {'title':'填写信息','events':[{'candidate_index':i,'action':'aiInput','target':e['target'],
                'value':'','observation':'输入框显示','uncertainties':[]} for i,e in enumerate(observed['events'])],
                'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    result=va.consolidate_observed_frames(document,root,lambda:False,observed,copy.deepcopy(observed['events']),[])
    assert observed==before
    assert set(observed['uncertainties']+blockers)<=set(result['uncertainties'])
    for event,blocker in zip(result['events'],blockers):assert blocker in event['uncertainties']
    proposal,snapshot=va.draft_proposal({**document,**result,'source_id':'uvs_'+'0'*32,'analysis_version':1})
    for blocker in blockers:
        assert any(blocker in q for q in proposal['questions'])
    assert all(any(blocker in e['uncertainties'] for e in snapshot['events']) for blocker in blockers)


def test_legacy_candidate_uncertainty_survives_planner_omission_and_blocks_trial(monkeypatch,consolidation):
    from task_server.services import ui_video_analysis as va,ai_skill_service as ai
    from task_server.services.ui_case_draft_contract import build_draft_yaml
    document,root,observed=consolidation
    blocker='触点目标仍需对照原画面核对'
    observed['events'][0]['uncertainties']=[blocker]
    def model(skill,payload,**kwargs):
        return {'title':'填写信息','events':[{'candidate_index':i,'action':'aiInput','target':e['target'],
                'value':'','observation':'输入框显示','uncertainties':[]} for i,e in enumerate(observed['events'])],
                'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    result=va.consolidate_observed_frames(document,root,lambda:False,observed,copy.deepcopy(observed['events']),[])
    assert blocker in result['events'][0]['uncertainties']
    proposal,_=va.draft_proposal({**document,**result,'source_id':'uvs_'+'0'*32,'analysis_version':1})
    assert any(blocker in q for q in proposal['questions'])
    assert not build_draft_yaml(proposal,document['app_id'])['ready']


def test_unindexed_legacy_plan_keeps_manual_input_review(monkeypatch,consolidation):
    from task_server.services import ui_video_analysis as va,ai_skill_service as ai
    from task_server.services.ui_video_business_inputs import _MANUAL
    document,root,observed=consolidation
    observed['events'][0]['uncertainties']=[_MANUAL]
    def model(skill,payload,**kwargs):
        planned=copy.deepcopy(observed['events'])
        for event in planned:event.update(value='example',uncertainties=[])
        return {'title':'填写信息','events':planned,'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    result=va.consolidate_observed_frames(document,root,lambda:False,observed,copy.deepcopy(observed['events']),[])
    assert _MANUAL in result['uncertainties']


def test_trajectory_manual_input_placeholder_can_be_resolved_after_consolidation(monkeypatch,consolidation):
    from task_server.services import ui_video_analysis as va,ai_skill_service as ai
    from task_server.services.ui_video_business_inputs import _MANUAL,recover_business_inputs
    document,root,observed=consolidation
    blocker='独立证据复核：输入前触点仍需核对'
    for event in observed['events']:
        event['target']='模型文字输入框'
        event['uncertainties']=[_MANUAL]
    observed['events'][0]['uncertainties'].append(blocker)
    observed['trajectory_audit']={'checks':[{'status':'needs_review','question':'输入前触点仍需核对'}]}
    before=copy.deepcopy(observed)
    def model(skill,payload,**kwargs):
        if kwargs['version']=='inputs':
            return {'events':[{'action':'aiInput','target':check['target'],'value':'自定义文字',
                    'frame_refs':check['final_input_frame_ids'][-1:],'observation':'普通业务输入','uncertainties':[]}
                    for check in payload['checks']], 'uncertainties':[],'multiple_scenarios':False}
        return {'title':'填写文字','events':[{'candidate_index':i,'action':'aiInput','target':e['target'],
                'value':'','observation':'输入框显示','uncertainties':[]} for i,e in enumerate(observed['events'])],
                'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    result=va.consolidate_observed_frames(document,root,lambda:False,observed,copy.deepcopy(observed['events']),[])
    assert all(_MANUAL in e['uncertainties'] for e in result['events'])
    recovered=recover_business_inputs(result,document,root,lambda:False)
    assert all(e['value']=='自定义文字' and _MANUAL not in e['uncertainties'] for e in recovered['events'])
    assert _MANUAL not in recovered['uncertainties']
    assert blocker in recovered['uncertainties'] and blocker in recovered['events'][0]['uncertainties']
    assert observed==before


def test_unconfirmed_fresh_dialog_motion_survives_planner_omission(monkeypatch,consolidation):
    from task_server.services import ui_video_analysis as va,ai_skill_service as ai
    document,root,observed=consolidation
    observed['events'][0].update(action='aiTap',target='确认',observation='点击后弹出确认打印对话框')
    observed['events'][1].update(action='aiScroll',target='确认打印弹窗内容',
        value={'direction':'down','scrollType':'singleAction'},observation='弹窗内容向上位移，判定滚动')
    def model(skill,payload,**kwargs):
        return {'title':'检查弹窗','events':[{'candidate_index':i,'action':e['action'],'target':e['target'],
                'value':e['value'],'observation':e['observation'],'uncertainties':[]} for i,e in enumerate(observed['events'])],
                'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    monkeypatch.setattr(va,'_result_review_needs',lambda *args:(set(),[]))
    result=va.consolidate_observed_frames(document,root,lambda:False,observed,copy.deepcopy(observed['events']),[])
    assert any('弹窗' in q and '滚动' in q for q in result['uncertainties'])
    proposal,_=va.draft_proposal({**document,**result,'source_id':'uvs_'+'0'*32,'analysis_version':1})
    from task_server.services.ui_case_draft_contract import build_draft_yaml
    assert not build_draft_yaml(proposal,document['app_id'])['ready']


def test_original_dialog_scroll_concern_survives_model_assert_correction(monkeypatch,consolidation):
    from task_server.services import ui_video_analysis as va,ai_skill_service as ai
    document,root,observed=consolidation
    observed['events'][0].update(action='aiTap',target='确认',observation='点击后弹出确认打印对话框')
    observed['events'][1].update(action='aiAssert',target='确认打印弹窗内容',value='',
        observation='图片加载完成，未见独立滚动')
    first_pass=copy.deepcopy(observed['events'])
    first_pass[1].update(action='aiScroll',value={'direction':'down','scrollType':'singleAction'},
        observation='弹窗内容向上位移，判定滚动')
    def model(skill,payload,**kwargs):
        return {'title':'检查弹窗','events':[{'candidate_index':0,'action':'aiTap','target':'确认',
                'value':'','observation':'点击后弹出确认打印对话框','uncertainties':[]}],
                'uncertainties':[],'multiple_scenarios':False}
    monkeypatch.setattr(ai,'run_ai_skill',model)
    monkeypatch.setattr(va,'_result_review_needs',lambda *args:(set(),[]))
    result=va.consolidate_observed_frames(document,root,lambda:False,observed,first_pass,[])
    assert any('弹窗' in q and '滚动' in q and 'frame-004' in q for q in result['uncertainties'])
    proposal,_=va.draft_proposal({**document,**result,'source_id':'uvs_'+'0'*32,'analysis_version':1})
    from task_server.services.ui_case_draft_contract import build_draft_yaml
    assert not build_draft_yaml(proposal,document['app_id'])['ready']
