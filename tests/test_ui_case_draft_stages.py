import copy
import pytest
import yaml
from task_server.services.ui_case_draft_contract import build_draft_yaml, normalize_steps
from task_server.services.yaml_executable_scorer import score_midscene_yaml_executable

APP='com.example.test'


def staged_draft():
    steps=[]
    for stage in range(3):
        steps.extend({'action':'aiTap','target':f'入口{stage}-{i}','expected':f'页面{stage}-{i}内容可见','review_state':'confirmed'} for i in range(5))
        steps.append({'action':'aiAssert','target':f'页面{stage}-4内容可见','review_state':'confirmed','stage_end':True})
    return normalize_steps({'title':'完整业务流程','steps':steps},APP)


def test_checkpoint_tasks_preserve_every_action_and_source_mapping():
    draft=staged_draft();plain=copy.deepcopy(draft)
    for step in plain['steps']:step.pop('stage_end',None)
    old=build_draft_yaml(plain,APP);new=build_draft_yaml(draft,APP)
    tasks=yaml.safe_load(new['yaml_content'])['tasks']
    assert len(tasks)==3
    assert [a for i,t in enumerate(tasks) for a in t['flow'][int(i>0):]]==yaml.safe_load(old['yaml_content'])['tasks'][0]['flow']
    assert [v for i,ids in enumerate(new['step_ids']) for v in ids[int(i>0):]]==old['step_ids'][0]
    assert [r for i,roles in enumerate(new['compiler_roles']) for r in roles[int(i>0):]]==old['compiler_roles'][0]
    assert all('aiAssert' in t['flow'][-1] for t in tasks)
    assert all(set(t)=={'name','flow'} for t in tasks)
    assert not score_midscene_yaml_executable(old['yaml_content'],generated=True,compiler_roles=old['compiler_roles'])['ok']
    assert score_midscene_yaml_executable(new['yaml_content'],generated=True,compiler_roles=new['compiler_roles'])['ok']


@pytest.mark.parametrize('value',[1,'true',None,{},[]])
def test_stage_marker_must_be_boolean(value):
    with pytest.raises(ValueError,match='stage_end'):
        normalize_steps({'title':'检查','steps':[{'action':'aiAssert','target':'首页导航可见','stage_end':value}]},APP)


def test_stage_boundary_requires_explicit_assertion():
    with pytest.raises(ValueError,match='检查'):
        normalize_steps({'title':'检查','steps':[{'action':'aiTap','target':'下一步','expected':'预览可见','stage_end':True}]},APP)


def test_staging_does_not_approve_or_allow_missing_results():
    draft=staged_draft();draft['steps'][0]['review_state']='needs_review'
    assert not build_draft_yaml(draft,APP)['ready']
    draft['steps'][0]['review_state']='confirmed';draft['steps'][0]['expected']=''
    assert not build_draft_yaml(draft,APP)['ready']


def test_compiled_stages_round_trip_through_yaml_editor_without_losing_boundaries():
    from task_server.services.ui_yaml_candidate import parse_candidate
    built=build_draft_yaml(staged_draft(),APP)
    parsed=parse_candidate(built['yaml_content'],APP,[])
    rebuilt=build_draft_yaml(parsed,APP)
    assert yaml.safe_load(rebuilt['yaml_content'])==yaml.safe_load(built['yaml_content'])
    assert sum(bool(s.get('stage_end')) for s in parsed['steps'])==2
    assert not rebuilt['ready']


@pytest.mark.parametrize('change',['name','missing_assert','continue_on_error'])
def test_yaml_stage_import_rejects_changed_structure(change):
    from task_server.services.ui_yaml_candidate import parse_candidate
    data=yaml.safe_load(build_draft_yaml(staged_draft(),APP)['yaml_content'])
    if change=='name':data['tasks'][1]['name']='另一流程'
    elif change=='missing_assert':data['tasks'][0]['flow'].pop()
    else:data['tasks'][0]['continueOnError']=True
    with pytest.raises(ValueError):parse_candidate(yaml.safe_dump(data,allow_unicode=True),APP,[])


def test_stage_cannot_cut_runtime_object_binding_scope():
    steps=[{'action':'selectRecord','target':'当前测试账号的可删除记录'},
           {'action':'aiAssert','target':'记录列表可见','stage_end':True},
           {'action':'aiTap','target':'{{本次对象}}详情','expected':'详情可见'},
           {'action':'aiAssert','target':'{{本次对象}}详情与{{保留对象}}仍可见'}]
    with pytest.raises(ValueError):normalize_steps({'title':'记录检查','steps':steps},APP)


def test_stage_cannot_cut_print_submission_and_cancellation_scope():
    steps=[{'action':'submitPrint','target':'确认打印'},
           {'action':'aiAssert','target':'新任务绑定可见','stage_end':True},
           {'action':'cancelPrint','target':'取消本次任务','expected':'取消完成'},
           {'action':'aiAssert','target':'设备空闲'}]
    with pytest.raises(ValueError):normalize_steps({'title':'打印取消','steps':steps},APP)


def test_later_stage_waits_for_exact_previous_checkpoint_without_home_keywords():
    draft=normalize_steps({'title':'编辑模型','steps':[
        {'action':'aiInput','target':'请输入文本输入框','value':'UI自动化','expected':'UI自动化可见','review_state':'confirmed'},
        {'action':'aiAssert','target':'UI自动化可见','stage_end':True,'review_state':'confirmed'},
        {'action':'aiTap','target':'生成模型','expected':'去打印按钮可见','review_state':'confirmed'},
        {'action':'aiAssert','target':'去打印按钮可见','review_state':'confirmed'}]},APP)
    built=build_draft_yaml(draft,APP)
    document=yaml.safe_load(built['yaml_content'])
    assert document['tasks'][1]['flow'][0]=={'aiWaitFor':'UI自动化可见','timeout':12000}
    assert built['step_ids'][1][0]==draft['steps'][1]['step_id']
    assert built['compiler_roles'][1][0]=='guard'
    score=score_midscene_yaml_executable(built['yaml_content'],compiler_roles=built['compiler_roles'])
    assert score['ok'],score
    from task_server.services.ui_yaml_candidate import parse_candidate
    parsed=parse_candidate(built['yaml_content'],APP,[])
    assert yaml.safe_load(build_draft_yaml(parsed,APP)['yaml_content'])==document
    for change in ('mismatch','missing','continue'):
        bad=copy.deepcopy(document)
        if change=='mismatch':bad['tasks'][1]['flow'][0]['aiWaitFor']='其它内容可见'
        elif change=='missing':bad['tasks'][1]['flow'].pop(0)
        else:bad['tasks'][0]['continueOnError']=True
        result=score_midscene_yaml_executable(yaml.safe_dump(bad,allow_unicode=True),compiler_roles=built['compiler_roles'])
        assert not result['taskScores'][1]['startGuard'],change


def test_checkpoint_entry_cannot_carry_task_scoped_objects():
    steps=[{'action':'selectRecord','target':'当前测试账号的可删除记录'},
           {'action':'aiTap','target':'{{本次对象}}详情','expected':'详情可见'},
           {'action':'aiAssert','target':'{{本次对象}}详情与{{保留对象}}仍可见','stage_end':True},
           {'action':'aiTap','target':'设置','expected':'设置内容可见'},
           {'action':'aiAssert','target':'设置内容可见'}]
    with pytest.raises(ValueError,match='选取'):
        normalize_steps({'title':'记录检查后打开设置','steps':steps},APP)
