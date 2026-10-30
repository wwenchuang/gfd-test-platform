import copy
import pytest
from test_ui_video_consolidation_entry import consolidation


def invoke(consolidation,runner,**options):
    from task_server.services.ui_video_analysis import consolidate_observed_frames
    document,root,observed=consolidation
    # Real JPEGs are required for atomic bounded preflight.
    from PIL import Image
    for frame in document['media']['frames']:
        Image.new('RGB',(8,8)).save(root/'frames'/(frame['frame_id']+'.jpg'))
    return consolidate_observed_frames(document,root,lambda:False,observed,copy.deepcopy(observed['events']),[],
                                       skill_runner=runner,bounded_business_planning=True,**options)


def answer(payload):
    return {'events':[{'candidate_index':i,'action':'aiInput','target':payload['candidate_events'][i]['target'],
                      'value':'','observation':'visible','uncertainties':[]}
                     for i in payload['planning_scope']['owned_candidate_indexes']],
            'uncertainties':[], 'multiple_scenarios':False}


def test_bounded_real_global_gate_and_one_safe_whole_pass_correction(consolidation):
    calls=[]; preflight=[]
    def runner(skill,payload,**kwargs):
        calls.append(copy.deepcopy(payload)); result=answer(payload)
        if len(calls)==1:result['events'][0]['candidate_index']=True
        return result
    result=invoke(consolidation,runner,planning_preflight=lambda requests:preflight.append(copy.deepcopy(requests)))
    assert len(calls)==2 and len(result['events'])==2
    assert calls[1]['validation_feedback'] and len(preflight)==2
    assert calls[0]['planning_scope']==calls[1]['planning_scope']


def test_provider_valueerror_propagates_without_schema_correction(consolidation):
    calls=[]; original=ValueError('provider failure')
    def runner(*args,**kwargs):calls.append(1);raise original
    with pytest.raises(ValueError) as error:invoke(consolidation,runner)
    assert error.value is original and len(calls)==1


def test_missing_required_uses_global_gate_and_no_third_pass(consolidation):
    calls=[]
    def runner(skill,payload,**kwargs):
        calls.append(payload); result=answer(payload); result['events']=[];return result
    with pytest.raises(ValueError,match='video_required_action_missing'):invoke(consolidation,runner)
    assert len(calls)==2


def test_conditional_prompt_preserves_absent_scope_exact_wire():
    from task_server.services.ai_skill_service import render_ai_skill_prompt,load_ai_skill_prompt
    import json
    payload={'phase':'whole-consolidate'}
    old=load_ai_skill_prompt('ui_video_steps','consolidate').replace('{{payload}}',json.dumps(payload,ensure_ascii=False,indent=2))
    assert render_ai_skill_prompt('ui_video_steps',payload,version='consolidate')==old
    payload['planning_scope']={'owned_candidate_indexes':[0]}
    assert '【规划输出归属】' in render_ai_skill_prompt('ui_video_steps',payload,version='consolidate')


def test_budget_full_pass_preflight_uses_remaining_original_clock_and_reservations():
    from task_server.services.ui_video_runtime import RuntimeBudget
    now=[0.0];budget=RuntimeBudget(0,clock=lambda:now[0],business=True)
    budget.freeze_packets(9,business=True)
    assert budget.limit==38
    budget.reserved=37
    with pytest.raises(ValueError,match='video_vision_limit'):budget.preflight_planning(2)
    assert budget.reserved==37
    budget.reserved=35;now[0]=4260
    with pytest.raises(TimeoutError,match='video_runtime_deadline'):budget.preflight_planning(2)
    now[0]=4259.5;budget.preflight_planning(2)
    assert budget.deadline==4500 and budget.reserved==35


@pytest.fixture
def split_consolidation(tmp_path):
    from PIL import Image
    frames=[{'frame_id':f'frame-{i:03d}','timestamp_ms':i*100} for i in range(60)]
    (tmp_path/'frames').mkdir()
    for frame in frames:Image.new('RGB',(8,8)).save(tmp_path/'frames'/(frame['frame_id']+'.jpg'))
    events=[{'action':'aiInput','target':f'field {i}','value':'','start_ms':i*100,'end_ms':i*100,
             'frame_refs':[frames[i]['frame_id']],'evidence_kind':'inferred',
             'observation':'full context','uncertainties':[]} for i in range(38)]
    document={'app_id':'com.example.app','goal':'input','media':{'frames':frames,'duration_ms':6000},
              'baseline_examples':[{'title':'actual baseline'}]}
    return document,tmp_path,{'events':events,'uncertainties':[],'multiple_scenarios':False}


def test_split_real_consolidator_full_rows_and_raw_immutability(split_consolidation):
    calls=[];raw=[];before=copy.deepcopy(split_consolidation[2])
    def runner(skill,payload,**kwargs):
        calls.append(copy.deepcopy(payload));r=answer(payload);raw.append(copy.deepcopy(r));return r
    result=invoke(split_consolidation,runner)
    assert len(calls)==2 and len(result['events'])==38
    assert all(len(p['candidate_events'])==38 and len(p['frames'])==60 for p in calls)
    assert split_consolidation[2]==before
    result['events'][0]['value']='changed'
    assert raw[0]['events'][0]['value']==''


def test_second_group_invalid_wire_admission_means_zero_calls(split_consolidation):
    # The last candidate is always group1; a huge unverified context field is
    # repeated in both requests, and shared IPC admission rejects it atomically.
    calls=[]
    split_consolidation[2]['events'][-1]['observation']='x'*(17*1024*1024)
    from task_server.services.ui_video_model_jobs import ModelJobError
    with pytest.raises(ModelJobError,match='video_job_message_limit'):
        invoke(split_consolidation,lambda *a,**k:calls.append(1))
    assert calls==[]


def test_second_group_corrupt_jpeg_means_zero_calls(split_consolidation):
    from task_server.services.ui_video_analysis import consolidate_observed_frames
    document,root,observed=split_consolidation;calls=[]
    (root/'frames'/'frame-059.jpg').write_bytes(b'corrupt')
    with pytest.raises(ValueError,match='video_frames_invalid'):
        consolidate_observed_frames(document,root,lambda:False,observed,observed['events'],[],
            skill_runner=lambda *a,**k:calls.append(1),bounded_business_planning=True)
    assert calls==[]


def test_cancellation_after_first_group_never_returns_partial_case(split_consolidation):
    from task_server.services.ui_video_analysis import consolidate_observed_frames
    document,root,observed=split_consolidation;calls=[]
    def runner(skill,payload,**kwargs):calls.append(payload);return answer(payload)
    with pytest.raises(ValueError,match='video_cancelled'):
        consolidate_observed_frames(document,root,lambda:bool(calls),observed,observed['events'],[],
            skill_runner=runner,bounded_business_planning=True)
    assert len(calls)==1


def test_full_original_questions_survive_intermediate_and_final_gates(consolidation):
    document,root,observed=consolidation
    document['video_runtime_selection']={'mode':'video-business-first-v1'}
    observed['uncertainties']=[f'question {i}' for i in range(45)]
    observed['trajectory_audit']={'checks':[]}
    def runner(skill,payload,**kwargs):
        assert payload['raw_uncertainties']==observed['uncertainties']
        return answer(payload)
    result=invoke(consolidation,runner)
    assert result['uncertainties']==observed['uncertainties']


def test_two_distinct_thirty_question_replies_keep_global_derived_ledger(split_consolidation):
    document,root,observed=split_consolidation
    document['video_runtime_selection']={'mode':'video-business-first-v1'}
    observed['trajectory_audit']={'checks':[]}
    observed['events']=[observed['events'][0],observed['events'][30]]
    observed['uncertainties']=['original root']
    def runner(skill,payload,**kwargs):
        result=answer(payload);g=payload['planning_scope']['group_index']
        result['uncertainties']=[f'group {g} unresolved question {i}' for i in range(30)]
        return result
    result=invoke(split_consolidation,runner)
    assert len(result['uncertainties'])==61
    from task_server.services.ui_video_analysis import draft_proposal
    proposal,snapshot=draft_proposal({**document,**result,'source_id':'uvs_'+'0'*32,'analysis_version':1})
    assert snapshot['raw_root_questions']==result['uncertainties']
    assert len(proposal['questions'])<=100
    for q in result['uncertainties']:assert any(q in text for text in proposal['questions'])


def test_merged_intermediate_overflow_fails_without_question_truncation(split_consolidation):
    split_consolidation[0]['video_runtime_selection']={'mode':'video-business-first-v1'}
    split_consolidation[2]['trajectory_audit']={'checks':[]}
    split_consolidation[2]['uncertainties']=[f'original root {i}' for i in range(129)]
    with pytest.raises(ValueError,match='video_model_invalid') as error:
        invoke(split_consolidation,lambda skill,payload,**kwargs:answer(payload))
    assert error.value.analysis_reason=='root_question_limit'
    assert error.value.question_count==129 and error.value.question_limit==128


def test_capacity_during_correction_retains_original_local_gate(consolidation):
    attempts=[];checks=[];failure=ValueError('video_vision_limit')
    def preflight(requests):
        checks.append(1)
        if len(checks)>1:raise failure
    def runner(skill,payload,**kwargs):
        attempts.append(payload);r=answer(payload);r['events'][0]['candidate_index']=True;return r
    with pytest.raises(ValueError) as error:invoke(consolidation,runner,planning_preflight=preflight)
    assert error.value is failure and len(attempts)==1
    assert error.value.planning_primary_gate=='video_planning_reply_ownership'


@pytest.mark.parametrize('invalid_both',[False,True])
def test_second_group_failure_reruns_whole_pass_and_never_third(split_consolidation,invalid_both):
    calls=[]
    def runner(skill,payload,**kwargs):
        calls.append(payload);r=answer(payload)
        if len(calls)==2 or invalid_both and len(calls)==4:r['events'][0]['candidate_index']=True
        return r
    if invalid_both:
        with pytest.raises(ValueError,match='video_planning_reply_ownership'):invoke(split_consolidation,runner)
    else:assert len(invoke(split_consolidation,runner)['events'])==38
    assert len(calls)==4
    assert [p['planning_scope']['group_index'] for p in calls]==[0,1,0,1]
    assert calls[0]['planning_scope']==calls[2]['planning_scope']


def test_attached_late_outcome_uses_one_global_repair_and_pending_plan(split_consolidation):
    document,root,observed=split_consolidation
    observed['events'][0].update(action='aiTap',target='打开信息页面')
    calls=[]
    def runner(skill,payload,**kwargs):
        calls.append((kwargs['version'],copy.deepcopy(payload)))
        if kwargs['version']=='outcome':
            assert [c['target'] for c in payload['checks']]==['candidate:0']
            return {'events':[{'action':'aiAssert','target':'candidate:0','value':'',
                'frame_refs':['frame-001'],'observation':'信息页面显示姓名输入框','uncertainties':[]}],
                'uncertainties':[],'multiple_scenarios':False}
        result=answer(payload)
        for event in result['events']:
            if event['candidate_index']==0:
                event.update(action='aiTap',outcome={'observation':'信息页面显示姓名输入框','frame_refs':['frame-005']},
                             reusable_expected='信息页面显示姓名输入框')
        return result
    result=invoke(split_consolidation,runner)
    assert [v for v,p in calls]==['consolidate','consolidate','outcome']
    assert len(result['events'])==38 and result['events'][0]['outcome']['frame_refs']==['frame-001']


@pytest.mark.parametrize('duplicate',[False,True])
def test_cross_group_dynamic_selection_remains_under_real_global_gate(split_consolidation,duplicate):
    _,_,observed=split_consolidation
    observed['events'][0].update(action='aiTap',target='对象详情入口')
    observed['events'][30].update(action='aiTap',target='对象详情入口')
    def runner(skill,payload,**kwargs):
        result=answer(payload)
        for e in result['events']:
            if e['candidate_index'] in (0,30):
                e.update(action='aiTap',target='{{本次对象}}详情入口',
                    outcome={'observation':'对象详情页面显示相关信息','frame_refs':[f"frame-{e['candidate_index']+1:03d}"]},
                    reusable_expected='页面显示{{本次对象}}与{{保留对象}}均保留')
                if e['candidate_index']==0 or duplicate:e['selection']='仅本次创建的测试对象'
        return result
    if duplicate:
        with pytest.raises(ValueError):invoke(split_consolidation,runner)
    else:
        result=invoke(split_consolidation,runner)
        assert result['events'][0]['selection']=='仅本次创建的测试对象'
        assert result['events'][30]['target']=='{{本次对象}}详情入口'
