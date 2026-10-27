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
