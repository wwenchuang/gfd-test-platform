import copy
import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import yaml

from task_server.services.ui_case_draft_contract import normalize_steps, build_draft_yaml

APP = 'com.kfb.model'


@pytest.mark.parametrize('count',[6,10,11])
def test_compiler_checks_do_not_double_penalize_business_complexity(count):
    from task_server.services.yaml_executable_scorer import score_midscene_yaml_executable
    draft={'title':'记录管理验证','steps':[
        {'action':'aiTap','target':f'入口{i}','expected':f'页面{i}显示内容','review_state':'confirmed'} for i in range(count)
    ]+[{'action':'aiAssert','target':'页面5显示内容','review_state':'confirmed'}]}
    built=build_draft_yaml(draft,APP)
    plain=score_midscene_yaml_executable(built['yaml_content'],generated=True)
    assert not plain['ok']
    scored=score_midscene_yaml_executable(built['yaml_content'],generated=True,compiler_roles=built['compiler_roles'])
    assert scored['ok']
    assert scored['taskScores'][0]['actionCount']==plain['taskScores'][0]['actionCount']
    assert scored['taskScores'][0]['businessActionCount']==count+1


@pytest.mark.parametrize('count,target',[ (14,'页面入口'), (6,'如果出现就点击入口') ])
def test_compiler_provenance_does_not_allow_long_or_conditional_business(count,target):
    from task_server.services.yaml_executable_scorer import score_midscene_yaml_executable
    draft={'title':'业务检查','steps':[
        {'action':'aiTap','target':target,'expected':'页面内容可见','review_state':'confirmed'} for _ in range(count)
    ]+[{'action':'aiAssert','target':'页面内容可见','review_state':'confirmed'}]}
    built=build_draft_yaml(draft,APP)
    scored=score_midscene_yaml_executable(built['yaml_content'],compiler_roles=built['compiler_roles'])
    assert not scored['ok']


def test_compiler_roles_are_not_read_from_yaml_or_client_steps():
    from task_server.services.yaml_executable_scorer import score_midscene_yaml_executable
    draft={'title':'业务检查','steps':[
        {'action':'aiTap','target':'页面入口','expected':'页面内容可见','review_state':'confirmed'} for _ in range(6)
    ]+[{'action':'aiAssert','target':'页面内容可见','review_state':'confirmed'}]}
    built=build_draft_yaml(draft,APP)
    data=yaml.safe_load(built['yaml_content']);data['compiler_roles']=built['compiler_roles']
    assert not score_midscene_yaml_executable(yaml.safe_dump(data,allow_unicode=True))['ok']
    draft['steps'][0]['compiler_role']='guard'
    with pytest.raises(ValueError):build_draft_yaml(draft,APP)


def payload():
    return {'title': '搜索模型', 'preconditions': ['已登录'], 'steps': [
        {'action': 'aiTap', 'target': '首页搜索按钮', 'expected': '搜索页可见'},
        {'action': 'aiInput', 'target': '搜索输入框', 'value': '机器人', 'expected': '输入框显示机器人'},
        {'action': 'aiAssert', 'target': '搜索结果显示机器人模型', 'expected': '搜索结果显示机器人模型'},
    ]}


def test_normalization_assigns_ids_is_pure_and_keeps_ids_on_edit():
    source = payload()
    before = copy.deepcopy(source)
    draft = normalize_steps(source, APP)
    assert source == before
    assert len({UUID(s['step_id']) for s in draft['steps']}) == 3
    assert all(s['review_state'] == 'needs_review' and s['source_ref'] == 'text' for s in draft['steps'])
    assert normalize_steps(draft, APP) == draft
    assert not build_draft_yaml(draft, APP)['ready']


@pytest.mark.parametrize('value', [None, [], '', {'steps': []}, {'steps': [{}] * 51}, {'steps': '点击'}])
def test_rejects_invalid_payload_types_and_count(value):
    with pytest.raises(ValueError):
        normalize_steps(value, APP)


@pytest.mark.parametrize('changes', [
    {'action': 'runAdbShell'}, {'action': 'exec'}, {'action': 'aiAction'},
    {'action': 'launch', 'target': 'com.other.app'}, {'target': 2},
    {'step_id': 'arbitrary'}, {'source_ref': 'recording:fake'},
    {'review_state': 'passed'}, {'timeout': 999999},
])
def test_rejects_unsupported_or_forged_step_fields(changes):
    source = payload()
    source['steps'][0].update(changes)
    with pytest.raises(ValueError):
        normalize_steps(source, APP)


def test_rejects_duplicate_step_ids():
    source = payload()
    step_id = str(uuid4())
    for step in source['steps']:
        step['step_id'] = step_id
    with pytest.raises(ValueError, match='step_id'):
        normalize_steps(source, APP)


@pytest.mark.parametrize('changes', [{'target': ''}, {'target': '按钮'}, {'expected': ''}])
def test_ambiguity_and_missing_expectation_block_even_if_confirmed(changes):
    source = payload()
    source['steps'][0].update(changes, review_state='confirmed')
    draft = normalize_steps(source, APP)
    assert draft['steps'][0]['review_state'] == 'needs_review'
    assert draft['questions']
    assert not build_draft_yaml(draft, APP)['ready']


def test_final_assertion_is_required_and_never_invented():
    source = payload()
    source['steps'].pop()
    for step in source['steps']:
        step['review_state'] = 'confirmed'
    result = build_draft_yaml(normalize_steps(source, APP), APP)
    assert not result['ready'] and result['questions']
    assert [item['aiAssert'] for item in yaml.safe_load(result['yaml_content'])['tasks'][0]['flow'] if 'aiAssert' in item] == ['搜索页可见', '输入框显示机器人']


@pytest.mark.parametrize('action', ['launch', 'aiTap', 'aiWaitFor'])
@pytest.mark.parametrize('expected', ['可见', '正常', '成功', '显示正常'])
def test_objectless_expectation_cannot_be_confirmed_for_execution(action, expected):
    source = payload()
    source['steps'][0].update(action=action, target=APP if action == 'launch' else '底部导航的我的入口', expected=expected)
    for step in source['steps']:
        step['review_state'] = 'confirmed'
    draft = normalize_steps(source, APP)
    assert draft['steps'][0]['review_state'] == 'needs_review'
    assert any('预期' in q for q in draft['questions'])
    assert not build_draft_yaml(draft, APP)['ready']


@pytest.mark.parametrize('action', ['aiWaitFor', 'aiAssert'])
def test_objectless_state_is_not_a_checkable_target(action):
    source = payload()
    source['steps'] = [{'action': action, 'target': '可见', 'expected': '', 'review_state': 'confirmed'}]
    assert not build_draft_yaml(source, APP)['ready']


def test_compiles_real_midscene_parameters_and_provenance():
    source = payload()
    source['steps'].insert(2, {'action': 'aiScroll', 'target': '搜索结果列表',
        'value': {'direction': 'down', 'distance': 300, 'scrollType': 'singleAction'},
        'expected': '更多结果可见'})
    for step in source['steps']:
        step['review_state'] = 'confirmed'
    draft = normalize_steps(source, APP)
    result = build_draft_yaml(draft, APP)
    assert result['ready'] and not result['questions']
    flow = yaml.safe_load(result['yaml_content'])['tasks'][0]['flow']
    assert yaml.safe_load(result['yaml_content'])['android'] == {}
    assert flow[0] == {'launch': APP}
    assert flow[1] == {'aiWaitFor': '页面中可见且可点击的目标：首页搜索按钮', 'timeout': 12000}
    assert next(step for step in flow if 'aiInput' in step) == {'aiInput': '搜索输入框', 'value': '机器人'}
    assert next(step for step in flow if 'aiScroll' in step) == {'aiScroll': '搜索结果列表', 'direction': 'down', 'distance': 300, 'scrollType': 'singleAction'}
    assert len(result['step_ids'][0]) == len(flow)
    assert result['step_ids'][0][1:3] == [draft['steps'][0]['step_id']] * 2
    from task_server.services.yaml_static_validator import validate_yaml_static_executable
    assert validate_yaml_static_executable(result['yaml_content'])['ok']


@pytest.mark.parametrize('value', [{'direction': 'diagonal'}, {'distance': -1}, {'distance': True}, {'command': 'adb'}, 'down'])
def test_rejects_invalid_scroll_parameters(value):
    source = payload()
    source['steps'][0].update(action='aiScroll', value=value)
    with pytest.raises(ValueError):
        normalize_steps(source, APP)


def test_explicit_launch_is_single_and_cross_application_build_is_rejected():
    source = payload()
    source['steps'].insert(0, {'action': 'launch', 'target': APP})
    draft = normalize_steps(source, APP)
    result = build_draft_yaml(draft, APP)
    assert result['yaml_content'].count('launch:') == 1
    draft['steps'][0]['target'] = 'com.other'
    with pytest.raises(ValueError):
        build_draft_yaml(draft, APP)


def test_schema_limits_ai_to_unconfirmed_text_suggestions():
    root = Path(__file__).resolve().parents[1]
    schema = json.loads((root / 'ai_skills/schemas/ui_step_normalizer.schema.json').read_text())
    steps = schema['properties']['steps']
    assert steps['maxItems'] == 50 and steps['minItems'] == 1
    fields = steps['items']['properties']
    assert 'step_id' not in fields
    assert fields['review_state']['enum'] == ['needs_review']
    assert fields['source_ref']['enum'] == ['text']


def test_model_questions_survive_and_block_until_explicitly_resolved():
    source = payload()
    source['questions'] = ['来源要求执行 shell，无法转换']
    for step in source['steps']:
        step['review_state'] = 'confirmed'
    draft = normalize_steps(source, APP)
    result = build_draft_yaml(draft, APP)
    assert not result['ready']
    assert source['questions'][0] in result['questions']
    draft['questions'] = []
    assert build_draft_yaml(draft, APP)['ready']


@pytest.mark.parametrize('distance', [float('nan'), float('inf')])
def test_scroll_distance_must_be_finite(distance):
    source = payload()
    source['steps'][0].update(action='aiScroll', value={'direction': 'down', 'distance': distance})
    with pytest.raises(ValueError):
        normalize_steps(source, APP)


def test_auto_launch_uses_validated_canonical_app_id():
    result = build_draft_yaml(payload(), ' ' + APP + ' ')
    assert yaml.safe_load(result['yaml_content'])['tasks'][0]['flow'][0] == {'launch': APP}


def test_every_operation_expectation_is_executed_and_exact_next_checkpoint_is_reused():
    source = payload()
    source['steps'].insert(2, {'action': 'aiTap', 'target': '搜索提交按钮', 'expected': '搜索结果显示机器人模型'})
    for step in source['steps']:
        step['review_state'] = 'confirmed'
    normalized = normalize_steps(source, APP)
    result = build_draft_yaml(normalized, APP)
    flow = yaml.safe_load(result['yaml_content'])['tasks'][0]['flow']
    checks = [step['aiAssert'] for step in flow if 'aiAssert' in step]
    assert checks == ['搜索页可见', '输入框显示机器人', '搜索结果显示机器人模型']
    assert result['step_ids'][0][3] == normalized['steps'][0]['step_id']


@pytest.mark.parametrize('action,target', [('aiWaitFor', '处理中提示出现'), ('launch', APP)])
def test_wait_and_launch_expectations_are_not_silently_dropped(action, target):
    result = build_draft_yaml({'title':'完成检查','steps':[
        {'action':action,'target':target,'expected':'处理结果为成功','review_state':'confirmed'},
        {'action':'aiAssert','target':'首页底栏可见','review_state':'confirmed'}]}, APP)
    assert result['ready']
    assert {'aiAssert':'处理结果为成功'} in yaml.safe_load(result['yaml_content'])['tasks'][0]['flow']


def test_assertion_conflicting_expectation_requires_human_resolution():
    result = build_draft_yaml({'title':'检查','steps':[
        {'action':'aiAssert','target':'结果成功','expected':'出现错误','review_state':'confirmed'}]}, APP)
    assert not result['ready']
    assert any('不一致' in question for question in result['questions'])

@pytest.mark.parametrize('explicit_next_wait', [False, True])
def test_navigation_result_waits_before_asserting_and_preserves_assertion(explicit_next_wait):
    steps=[{'action':'aiTap','target':'打印记录入口','expected':'打印记录列表可见','review_state':'confirmed'}]
    if explicit_next_wait:
        steps.append({'action':'aiWaitFor','target':'打印记录列表可见','review_state':'confirmed'})
    steps.append({'action':'aiAssert','target':'打印记录列表可见','review_state':'confirmed'})
    built=build_draft_yaml({'title':'打印记录导航','steps':steps}, APP)
    flow=yaml.safe_load(built['yaml_content'])['tasks'][0]['flow']
    click=next(i for i,x in enumerate(flow) if 'aiTap' in x)
    check=next(i for i,x in enumerate(flow) if x.get('aiAssert')=='打印记录列表可见')
    assert {'aiWaitFor':'打印记录列表可见','timeout':12000} in flow[click+1:check]
    assert len([x for x in flow if x.get('aiWaitFor')=='打印记录列表可见'])==1
    assert len(built['compiler_roles'][0])==len(flow)==len(built['step_ids'][0])


def test_tap_guard_checks_visible_control_not_current_destination():
    draft={'title':'返回首页','steps':[
        {'action':'aiTap','target':'首页','expected':'首页核心功能可见','review_state':'confirmed'},
        {'action':'aiAssert','target':'首页核心功能可见','review_state':'confirmed'}]}
    built=build_draft_yaml(draft,APP)
    flow=yaml.safe_load(built['yaml_content'])['tasks'][0]['flow']
    assert flow[1]=={'aiWaitFor':'页面中可见且可点击的目标：首页','timeout':12000}
    assert flow[2]=={'aiTap':'首页'}
    assert flow[3]=={'aiWaitFor':'首页核心功能可见','timeout':12000}

def test_explicit_click_wait_keeps_original_condition_without_duplicate_guard():
    draft={'title':'显式等待','steps':[
        {'action':'aiWaitFor','target':'首页','review_state':'confirmed'},
        {'action':'aiTap','target':'首页','expected':'首页核心功能可见','review_state':'confirmed'},
        {'action':'aiAssert','target':'首页核心功能可见','review_state':'confirmed'}]}
    flow=yaml.safe_load(build_draft_yaml(draft,APP)['yaml_content'])['tasks'][0]['flow']
    assert flow[1:3]==[{'aiWaitFor':'首页','timeout':12000},{'aiTap':'首页'}]
