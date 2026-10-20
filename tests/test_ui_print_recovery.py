import copy
from uuid import uuid4
import pytest
import yaml
from task_server.services.ui_case_draft_contract import normalize_steps,build_draft_yaml
from task_server.services.ui_case_compiler import compile_ui_case


def draft():
    return {'title':'姓名牌打印取消恢复验证','steps':[
      {'action':'submitPrint','target':'检查无误，确认打印','expected':'本次新建打印任务已绑定','review_state':'confirmed'},
      {'action':'cancelPrint','target':'取消本次打印并等待结束','expected':'本次任务结束','review_state':'confirmed'},
      {'action':'aiAssert','target':'取消原因页显示','review_state':'confirmed'}]}


def target(platform='ios',cap=True):
    return {'ok':True,'platform':platform,'target_package':'com.test.ios' if platform=='ios' else 'com.test.app','app_id':'com.test.app',
      'platform_mappings':{'android':'com.test.app','ios':'com.test.ios'},
      'capabilities':{'android_baseline':True,'ui_print_recovery_v1':cap,'ui_actions':['launch','aiQuery','aiAssert','aiWaitFor','aiTap']}}


def test_print_steps_compile_to_existing_action_and_require_capability():
    d=normalize_steps(draft(),'com.test.app');compiled=build_draft_yaml(d,'com.test.app')
    data=yaml.safe_load(compiled['yaml_content']);flow=data['tasks'][0]['flow']
    assert sum('aiQuery' in step for step in flow)==2
    assert {'aiAssert':'本次任务结束'} in flow
    assert not any('submitPrint' in step or 'cancelPrint' in step for step in flow)
    for platform in ['ios','android']:
        assert compile_ui_case(compiled,target(platform))['ok']
        assert compile_ui_case(compiled,target(platform,False))['block_code']=='print_recovery_unsupported'


@pytest.mark.parametrize('kind',['orphan','duplicate','too_many','too_long','too_fast'])
def test_invalid_print_recovery_contract_is_rejected(kind):
    d=draft()
    if kind=='orphan':d['steps'].pop(0)
    if kind=='duplicate':d['steps'].insert(1,copy.deepcopy(d['steps'][0]))
    if kind=='too_many':d['steps'][1]['value']={'max_attempts':9}
    if kind=='too_long':d['steps'][1]['value']={'timeout_ms':180001}
    if kind=='too_fast':d['steps'][1]['value']={'retry_delay_ms':0}
    with pytest.raises(ValueError):normalize_steps(d,'com.test.app')


def test_video_print_group_preserves_evidence_and_folds_only_cancel_confirmation():
    from task_server.services.ui_video_print_recovery import bind_group,apply_group
    targets=['检查无误，确认打印','取消打印','确认','模型脱落','提交反馈','返回']
    candidates=[{'action':'aiTap','target':t} for t in targets]
    planned={'events':[dict(candidate_index=i,**e) for i,e in enumerate(candidates)],'print_recovery':{'submit':0,'cancel':1,'confirm':2}}
    group=bind_group(planned,candidates)
    steps=[{'step_id':str(uuid4()),'action':'aiTap','target':t,'value':'','expected':'稳定结果'} for t in targets]
    changed=apply_group(steps,group)
    assert [s['action'] for s in changed]==['submitPrint','cancelPrint','aiTap','aiTap','aiTap']
    assert changed[-1]['target']=='返回' and len(steps)==6
    planned['print_recovery']={'submit':0,'cancel':1,'confirm':4}
    with pytest.raises(ValueError):bind_group(planned,candidates)


def test_print_recovery_progress_is_bounded_and_not_execution_authority():
    from task_server.services.runner_progress import normalize_execution_progress
    raw={'version':1,'tasks':[{'steps':[],'print_recovery':{'status':'passed','attempts':3,'elapsed_ms':48000,'recovered':True,'history':[{'state':'暂时失败','attempts':2,'elapsed_ms':10000}]}}]}
    clean=normalize_execution_progress(raw)['tasks'][0]['print_recovery']
    assert clean['attempts']==3 and clean['recovered'] is True
    raw['tasks'][0]['print_recovery']['attempts']=999
    assert 'print_recovery' not in normalize_execution_progress(raw)['tasks'][0]


def test_recovery_region_collects_repeated_buttons_but_preserves_feedback():
    from task_server.services.ui_video_print_recovery import prepare_group
    from task_server.services.ui_video_recovery import review_retries
    targets=['检查无误，确认打印','取消打印','确认','取消失败','取消打印','确认','任务已结束','模型脱落','提交反馈','返回']
    raw=[{'action':'aiAssert' if i in (3,6) else 'aiTap','target':t,'value':'','observation':('本次任务卡片已移除' if i==6 else t),'frame_refs':[f'f{i}']} for i,t in enumerate(targets)]
    plan={'events':[dict(candidate_index=i,**e) for i,e in enumerate(raw)],'print_recovery':{'submit':0,'cancel':1,'confirm':2,'end':6}}
    required=[i for i,e in enumerate(raw) if e['action']=='aiTap']
    cleaned=prepare_group(plan,raw,required)
    final,needed,audit=review_retries(cleaned,raw,required)
    assert [e['candidate_index'] for e in final['events']]==[0,1,2,6,7,8,9]
    assert needed==[0,1,2,7,8,9] and len(audit)==2
    assert plan['print_recovery']['end']==6
    bad=copy.deepcopy(raw);bad[4]['target']='确认打印'
    with pytest.raises(ValueError):prepare_group(plan,bad,required)
    bad=copy.deepcopy(raw);bad[6]['observation']='设备空闲'
    with pytest.raises(ValueError):prepare_group(plan,bad,required)


def test_print_yaml_roundtrip_preserves_cancel_postcondition():
    from task_server.services.ui_yaml_candidate import parse_candidate
    compiled=build_draft_yaml(normalize_steps(draft(),'com.test.app'),'com.test.app')
    parsed=parse_candidate(compiled['yaml_content'],'com.test.app',[])
    assert parsed['steps'][0]['action']=='submitPrint'
    assert parsed['steps'][1]['action']=='cancelPrint'
    assert parsed['steps'][1]['expected']=='本次任务结束'
    assert yaml.safe_load(build_draft_yaml(parsed,'com.test.app')['yaml_content'])==yaml.safe_load(compiled['yaml_content'])


@pytest.mark.parametrize('step',[None,42,'hello'])
def test_invalid_yaml_row_remains_validation_error(step):
    from task_server.services.ui_yaml_candidate import parse_candidate
    raw=yaml.safe_dump({'android':{},'tasks':[{'name':'测试','flow':[step]}]})
    with pytest.raises(ValueError):parse_candidate(raw,'com.test.app',[])


def test_video_cancel_step_never_uses_pending_confirmation_as_success():
    from task_server.services.ui_video_print_recovery import apply_group
    steps=[{'action':'aiTap','target':t,'value':'','expected':e} for t,e in [
        ('检查无误，确认打印','进入设备页'),('取消打印','弹出确认框'),('确认','触发取消流程，显示等待提示'),('不想打了','原因被选中')]]
    result=apply_group(steps,{'submit':0,'cancel':1,'confirm':2})
    assert result[1]['action']=='cancelPrint'
    assert '本次打印任务已结束' in result[1]['expected']
    assert '等待提示' not in result[1]['expected']
    assert steps[2]['expected']=='触发取消流程，显示等待提示'
