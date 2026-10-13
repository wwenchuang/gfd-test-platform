import copy
import hashlib

import pytest
import yaml

SOURCE = '''android: {}
agent:
  screenshotShrinkFactor: 2
tasks:
- name: 导航
  flow:
  - launch: com.kfb.model
  - aiTap: 我的
  - aiWaitFor: 我的页面可见
    timeout: 12000
  - aiAssert: 我的页面显示正确
'''
TARGET = {'ok': True, 'app_id': 'com.kfb.model', 'platform': 'ios', 'runner_id': 'mac', 'device_id': 'iphone',
          'source_package': 'com.kfb.model', 'target_package': 'com.kfb.3d',
          'platform_mappings': {'android': 'com.kfb.model', 'ios': 'com.kfb.3d'},
          'capabilities': {'ui_execution_contract': 'ui-v1', 'ui_actions': ['launch', 'aiTap', 'aiWaitFor', 'aiAssert']}}


def compile_case(text=SOURCE, target=None, **extra):
    from task_server.services.ui_case_compiler import compile_ui_case
    return compile_ui_case({'yaml_content': text, **extra}, target or TARGET)


def test_compilation_is_pure_deterministic_and_keeps_source_hash():
    case = {'yaml_content': SOURCE, 'step_ids': [['start', 'my', 'my', 'my']]}
    before = copy.deepcopy((case, TARGET))
    from task_server.services.ui_case_compiler import compile_ui_case
    result = compile_ui_case(case, TARGET)
    assert result['ok'] and result['unsupported_steps'] == []
    content = yaml.safe_load(result['yaml_content'])
    assert content['ios'] == {} and 'android' not in content
    assert content['tasks'][0]['flow'][0] == {'launch': 'com.kfb.3d'}
    assert content['agent'] == {'screenshotShrinkFactor': 2}
    assert result['step_map']['my'] == [{'task_index': 1, 'flow_index': 2}, {'task_index': 1, 'flow_index': 3}, {'task_index': 1, 'flow_index': 4}]
    assert result['source_hash'] == hashlib.sha256(SOURCE.encode()).hexdigest()
    assert result['compiled_hash'] == hashlib.sha256(result['yaml_content'].encode()).hexdigest()
    assert result['source_hash'] != result['compiled_hash']
    assert (case, TARGET) == before
    assert compile_ui_case(case, TARGET) == result
    assert 'wdaPort' not in result['yaml_content']


@pytest.mark.parametrize('step', ['back: true', 'adbShell: input keyevent 4', 'aiInput: text',
                                 'aiScroll: 下滑', 'aiTap: {x: 1, y: 2}', 'launch: com.other.app',
                                 'aiTap: 我的\n    locate: {x: 1}', 'aiWaitFor: 页面\n    timeout: nope'])
def test_cross_platform_unsupported_steps_block_entire_compilation(step):
    result = compile_case(SOURCE + '  - ' + step + '\n')
    assert not result['ok'] and result['unsupported_steps']
    assert result['yaml_content'] == '' and result['compiled_hash'] == ''
    assert result['unsupported_steps'][0]['flow_index'] == 5


@pytest.mark.parametrize('text', [SOURCE.replace('android: {}', 'android: {keyboardDismissStrategy: back}'),
                                 SOURCE.replace('android: {}', 'ios: {wdaHost: evil-host}'),
                                 SOURCE + 'unsupported: true\n', 'android: {}\ntasks: []\n'])
def test_unknown_structure_and_connection_settings_fail_closed(text):
    assert compile_case(text)['ok'] is False


def test_source_connection_device_is_removed_from_cross_platform_copy():
    result = compile_case(SOURCE.replace('android: {}', 'android: {deviceId: old-phone}'))
    assert result['ok'] and yaml.safe_load(result['yaml_content'])['ios'] == {}


def test_android_only_baseline_preserves_existing_actions_and_options():
    text = SOURCE.replace('android: {}', 'android: {keyboardDismissStrategy: back}') + '  - adbShell: input keyevent 4\n'
    target = {**TARGET, 'platform': 'android', 'target_package': 'com.kfb.model',
              'capabilities': {**TARGET['capabilities'], 'android_baseline': True}}
    result = compile_case(text, target)
    assert result['ok'] and yaml.safe_load(result['yaml_content']) == yaml.safe_load(text)
    assert result['required_actions'] == ['launch', 'aiTap', 'aiWaitFor', 'aiAssert', 'adbShell']


def test_android_baseline_device_id_cannot_override_selected_phone():
    target = {**TARGET, 'platform': 'android', 'target_package': 'com.kfb.model',
              'capabilities': {**TARGET['capabilities'], 'android_baseline': True}}
    result = compile_case(SOURCE.replace('android: {}', 'android: {deviceId: old-phone}'), target)
    assert result['ok'] and 'deviceId' not in yaml.safe_load(result['yaml_content'])['android']


def test_ios_to_android_uses_explicit_reverse_mapping():
    text = SOURCE.replace('android: {}', 'ios: {}').replace('com.kfb.model', 'com.kfb.3d')
    result = compile_case(text, {**TARGET, 'platform': 'android', 'target_package': 'com.kfb.model'})
    assert result['ok'] and yaml.safe_load(result['yaml_content'])['tasks'][0]['flow'][0]['launch'] == 'com.kfb.model'


def test_capability_loss_cannot_compile_even_supported_action():
    target = copy.deepcopy(TARGET)
    target['capabilities']['ui_actions'].remove('aiAssert')
    assert not compile_case(target=target)['ok']


def test_blocked_resolution_cannot_compile():
    assert not compile_case(target={'ok': False, 'block_code': 'device_not_found'})['ok']


def test_step_id_shape_mismatch_does_not_invent_provenance():
    result = compile_case(step_ids=[['only-one']])
    assert not result['ok'] and result['block_code'] == 'step_map_invalid'


def test_duplicate_yaml_key_cannot_silently_replace_unsupported_field():
    duplicated = SOURCE.replace('  - aiTap: 我的', '  - aiTap: {x: 1}\n    aiTap: 我的')
    assert compile_case(duplicated)['ok'] is False
