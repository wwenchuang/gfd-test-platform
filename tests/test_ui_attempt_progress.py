"""Display order follows compiled source positions after durable JSON serialization."""
import copy
import json

import pytest
import yaml

from task_server.services.ui_attempt_progress import describe_steps, stable_step_ids
from task_server.services.ui_execution_store import _json


FLOW = [{'launch': 'com.kfb.3d'}, {'aiWaitFor': '首页可见'}, {'aiTap': '我的'},
        {'aiAssert': '我的页面可见'}, {'aiTap': '首页'}, {'aiAssert': '首页可见'}]
LABELS = ['启动应用 · com.kfb.3d', '等待 · 首页可见', '点击 · 我的',
          '检查 · 我的页面可见', '点击 · 首页', '检查 · 首页可见']


def attempt_for(flows=None, ids=None):
    flows = flows or [FLOW]
    source = yaml.safe_dump({'ios': {}, 'tasks': [{'name': f'导航{i}', 'flow': flow}
                                                for i, flow in enumerate(flows)]}, allow_unicode=True)
    ids = ids or stable_step_ids('navigation', 'frozen-revision', source)
    mapping = {}
    for ti, row in enumerate(ids, 1):
        for fi, step_id in enumerate(row, 1):
            mapping.setdefault(step_id, []).append({'task_index': ti, 'flow_index': fi})
    compiled = json.loads(_json({'yaml_content': source, 'step_map': mapping}))
    return {'status': 'running', 'compiled_case': compiled}, ids


def progress(status='running', current=2, total=6, labels=None):
    return {'status': status, 'current_step': current, 'total_steps': total,
            'steps': [{'label': label} for label in (LABELS if labels is None else labels)]}


def test_stable_hashed_steps_use_execution_order_and_chinese_observer_labels():
    attempt, ids = attempt_for()
    assert list(attempt['compiled_case']['step_map']) != ids[0]
    observed_labels = [*LABELS]
    observed_labels[2] = '点击 · 底部导航“我的”入口'
    attempt['execution_progress'] = {'version': 1, 'tasks': [progress(labels=observed_labels)]}
    before = copy.deepcopy(attempt)
    result = describe_steps(attempt)
    assert [row['step_id'] for row in result['steps']] == ids[0]
    assert [row['label'] for row in result['steps']] == observed_labels
    assert [row['status'] for row in result['steps']] == ['passed', 'passed', 'running', 'pending', 'pending', 'pending']
    assert result['step_progress_known'] is True
    assert attempt == before


def test_multitask_grouped_steps_order_by_first_source_position_and_fold_all_positions():
    attempt, _ = attempt_for([FLOW[:2], FLOW[2:4]], [['z-start', 'y-navigation'], ['y-navigation', 'a-check']])
    attempt['compiled_case']['step_map']['y-navigation'].reverse()
    attempt['execution_progress'] = {'version': 1, 'tasks': [progress('passed', 1, 2, LABELS[:2]), progress('failed', 1, 2, LABELS[2:4])]}
    result = describe_steps(attempt)
    assert [row['step_id'] for row in result['steps']] == ['z-start', 'y-navigation', 'a-check']
    assert result['steps'][1] == {'step_id': 'y-navigation', 'label': '等待 · 首页可见 / 点击 · 我的', 'status': 'passed'}
    assert [row['status'] for row in result['steps']] == ['passed', 'passed', 'failed']


def test_missing_progress_remains_unknown_and_pending_in_source_order():
    attempt, ids = attempt_for()
    attempt['status'] = 'passed'
    result = describe_steps(attempt)
    assert result['step_progress_known'] is False
    assert [row['step_id'] for row in result['steps']] == ids[0]
    assert {row['status'] for row in result['steps']} == {'pending'}


@pytest.mark.parametrize('version,total', [(1, 3), (2, 6)])
def test_unmatched_progress_never_supplies_labels_or_pass_states(version, total):
    attempt, _ = attempt_for()
    attempt['execution_progress'] = {'version': version, 'tasks': [progress('passed', 5, total, ['错误映射'] * 6)]}
    result = describe_steps(attempt)
    assert {row['status'] for row in result['steps']} == {'pending'}
    assert all('错误映射' not in row['label'] for row in result['steps'])


def test_truncated_labels_and_missing_tasks_preserve_source_steps_without_guessing_success():
    attempt, ids = attempt_for([FLOW, [FLOW[-1]]])
    attempt['execution_progress'] = {'version': 1, 'truncated': True, 'tasks': [progress(labels=LABELS[:2])]}
    result = describe_steps(attempt)
    assert result['step_progress_known'] and result['step_progress_truncated']
    assert [row['step_id'] for row in result['steps']] == ids[0] + ids[1]
    assert result['steps'][0]['label'] == LABELS[0]
    assert result['steps'][2]['label'] == '点击 · 我的'
    assert result['steps'][-1]['status'] == 'pending'


def test_missing_step_map_does_not_invent_observer_steps():
    attempt, _ = attempt_for()
    attempt['compiled_case']['step_map'] = {}
    attempt['execution_progress'] = {'version': 1, 'tasks': [progress('passed')]}
    assert describe_steps(attempt)['steps'] == []


def test_input_and_shell_values_stay_hidden_even_in_observer_labels():
    attempt, _ = attempt_for([[{'aiInput': 'private input'}, {'runAdbShell': 'private command'}]])
    attempt['execution_progress'] = {'version': 1, 'tasks': [progress(total=2, current=1, labels=['private input', 'private command'])]}
    assert [row['label'] for row in describe_steps(attempt)['steps']] == ['输入内容', '设备操作']
