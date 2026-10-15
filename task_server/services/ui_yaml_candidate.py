"""Deliberately narrow YAML-to-Chinese adapter. Never executes or calls a model."""
import yaml
from .ui_case_draft_contract import ACTIONS, SHORT_TIMEOUT_MS, normalize_steps, build_draft_yaml

MAX_PARSE_CHARS = 100000
MAX_RAW_CHARS = 200000


class _UniqueLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in result:
                raise ValueError('YAML 不允许重复键或非文字字段名')
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def parse_candidate(raw, app_id, preconditions):
    if len(raw) > MAX_PARSE_CHARS:
        raise ValueError('YAML 超过10万字解析上限；原文已保留，请精简或恢复上一有效版')
    try:
        depth = count = 0
        for event in yaml.parse(raw):
            count += 1
            if isinstance(event, yaml.events.AliasEvent) or getattr(event, 'anchor', None):
                raise ValueError('YAML 不支持锚点和别名，请直接写明每一步')
            if isinstance(event, (yaml.events.MappingStartEvent, yaml.events.SequenceStartEvent)):
                depth += 1
            if isinstance(event, (yaml.events.MappingEndEvent, yaml.events.SequenceEndEvent)):
                depth -= 1
            if depth > 12 or count > 2000:
                raise ValueError('YAML 结构过深或内容过多，请精简')
        document = yaml.load(raw, Loader=_UniqueLoader)
    except yaml.YAMLError as error:
        mark = getattr(error, 'problem_mark', None)
        raise ValueError('YAML 格式错误' + (f'，请检查第 {mark.line + 1} 行' if mark else '')) from None
    if not isinstance(document, dict) or set(document) != {'android', 'tasks'} or document['android'] != {}:
        raise ValueError('仅支持 android: {} 和单个 tasks；设备、平台及其他配置不能从 YAML 导入')
    tasks = document['tasks']
    if not isinstance(tasks, list) or len(tasks) != 1 or not isinstance(tasks[0], dict) or set(tasks[0]) != {'name', 'flow'}:
        raise ValueError('仅支持一个包含 name 和 flow 的任务；不支持额外任务配置')
    task = tasks[0]
    flow = task['flow']
    if not isinstance(flow, list) or not 1 <= len(flow) <= 151:
        raise ValueError('候选 YAML 必须包含1至151个动作（转换后最多50个中文步骤）')
    steps = []
    for index, item in enumerate(flow):
        if not isinstance(item, dict) or len(set(item) & ACTIONS) != 1:
            raise ValueError(f'第 {index + 1} 个动作不支持；不能转换坐标、ADB 或系统指令')
        action = next(iter(set(item) & ACTIONS))
        allowed = {action} | ({'timeout'} if action == 'aiWaitFor' else {'value'} if action == 'aiInput' else {'direction', 'distance', 'scrollType'} if action == 'aiScroll' else set())
        if set(item) - allowed:
            raise ValueError(f'第 {index + 1} 个动作含不支持的选项：' + '、'.join(sorted(set(item) - allowed)))
        if action == 'aiWaitFor' and (type(item.get('timeout')) is not int or item['timeout'] != SHORT_TIMEOUT_MS):
            raise ValueError('等待步骤当前仅支持明确的 timeout: 12000；其他超时不能静默转换')
        for value in item.values():
            if isinstance(value, str) and value != value.strip():
                raise ValueError('动作文字首尾空格不能无损转换，请核对后去除')
        target = item[action]
        following = flow[index + 1] if index + 1 < len(flow) else {}
        expected = target if action == 'aiAssert' else following.get('aiAssert', '') if isinstance(following, dict) else ''
        steps.append({'action':action, 'target':target, 'value': {k:v for k,v in item.items() if k != action} if action == 'aiScroll' else item.get('value', ''),
                      'expected':expected, 'source_ref':'manual_yaml', 'review_state':'needs_review'})
    # Fold only exact compiler-generated guards/checkpoints. The resulting flow
    # remains byte-value equivalent, while a 50-step draft stays within its bound.
    compact = []
    for index, step in enumerate(steps):
        following = steps[index + 1] if index + 1 < len(steps) else None
        if step['action'] == 'launch':
            if index != 0 or step['target'] != app_id:
                raise ValueError('禁止跨应用 launch，启动应用只能在首步声明一次')
            if following:
                continue  # build_draft_yaml supplies this same launch.
        if step['action'] == 'aiWaitFor' and not step['expected'] and following and following['action'] == 'aiTap' and following['target'] == step['target']:
            continue  # compiler restores the exact timeout:12000 guard.
        if step['action'] == 'aiAssert' and following and compact:
            previous = steps[index - 1]
            if not (following['action'] == 'aiAssert' and following['target'] == step['target']) and previous['action'] != 'aiAssert' and previous['expected'] == step['target'] and not (previous['action'] == 'aiWaitFor' and previous['target'] == step['target']):
                continue  # compiler restores this expected-result assertion.
        compact.append(step)
    if len(compact) > 50:
        raise ValueError('转换后超过50个中文步骤，请拆分用例；原始 YAML 已保留')
    normalized = normalize_steps({'title':task['name'], 'preconditions':preconditions, 'steps':compact, 'questions':[]}, app_id)
    rebuilt = yaml.safe_load(build_draft_yaml(normalized, app_id)['yaml_content'])
    if rebuilt != document:
        raise ValueError('当前脚本不能无损转换为中文步骤；请保留自动启动、点击前等待及完整检查点，或恢复上一有效版')
    return normalized
