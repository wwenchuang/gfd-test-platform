"""Deliberately narrow YAML-to-Chinese adapter. Never executes or calls a model."""
import yaml
import json
from .ui_case_draft_contract import ACTIONS, SHORT_TIMEOUT_MS, normalize_steps, build_draft_yaml, click_target_condition
from .ui_dynamic_object import PREFIX
from . import ui_print_recovery as print_recovery

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
            if depth > 12 or count > 3000:
                raise ValueError('YAML 结构过深或内容过多，请精简')
        document = yaml.load(raw, Loader=_UniqueLoader)
    except yaml.YAMLError as error:
        mark = getattr(error, 'problem_mark', None)
        raise ValueError('YAML 格式错误' + (f'，请检查第 {mark.line + 1} 行' if mark else '')) from None
    if not isinstance(document, dict) or set(document) != {'android', 'tasks'} or document['android'] != {}:
        raise ValueError('仅支持 android: {} 和检查点分段的 tasks；设备、平台及其他配置不能从 YAML 导入')
    tasks = document['tasks']
    if (not isinstance(tasks, list) or not 1 <= len(tasks) <= 50
            or any(not isinstance(t, dict) or set(t) != {'name', 'flow'} for t in tasks)):
        raise ValueError('仅支持包含 name 和 flow 的任务；不支持额外任务配置')
    if any(not isinstance(t['flow'], list) or not t['flow'] for t in tasks):
        raise ValueError('阶段动作不能为空')
    title = tasks[0]['name']
    boundaries = set()
    if len(tasks) > 1:
        suffix = ' · 第1阶段'
        if not isinstance(title, str) or not title.endswith(suffix):
            raise ValueError('多个任务只支持由检查点生成的连续阶段名称')
        title = title[:-len(suffix)]
        total = 0
        for number, task in enumerate(tasks, 1):
            if task['name'] != f'{title} · 第{number}阶段':
                raise ValueError('阶段名称或顺序不一致')
            total += len(task['flow'])
            if number < len(tasks):
                if not isinstance(task['flow'][-1], dict) or set(task['flow'][-1]) != {'aiAssert'}:
                    raise ValueError('阶段只能在明确检查步骤后结束')
                boundaries.add(total - 1)
    flow = [item for task in tasks for item in task['flow']]
    if not 1 <= len(flow) <= 201:
        raise ValueError('候选 YAML 必须包含1至201个动作（转换后最多50个中文步骤）')
    print_recovery.validate_flow(document)
    steps = []
    for index, item in enumerate(flow):
        if isinstance(item,dict) and set(item)=={'aiQuery'} and isinstance(item['aiQuery'],str) and item['aiQuery'].startswith(PREFIX):
            item={'selectRecord':item['aiQuery'][len(PREFIX):]}
        if isinstance(item,dict) and set(item)=={'aiQuery'} and isinstance(item['aiQuery'],str) and item['aiQuery'].startswith(print_recovery.PREFIX):
            body=json.loads(item['aiQuery'][len(print_recovery.PREFIX):])
            action='submitPrint' if body['operation']=='submit' else 'cancelPrint'
            item={action:body['target'],'value':{key:body[key] for key in print_recovery.DEFAULTS}}
        if not isinstance(item, dict) or len(set(item) & ACTIONS) != 1:
            raise ValueError(f'第 {index + 1} 个动作不支持；不能转换坐标、ADB 或系统指令')
        action = next(iter(set(item) & ACTIONS))
        allowed = {action} | ({'timeout'} if action == 'aiWaitFor' else {'value'} if action in ('aiInput','submitPrint','cancelPrint') else {'direction', 'distance', 'scrollType'} if action == 'aiScroll' else set())
        if set(item) - allowed:
            raise ValueError(f'第 {index + 1} 个动作含不支持的选项：' + '、'.join(sorted(set(item) - allowed)))
        if action == 'aiWaitFor' and (type(item.get('timeout')) is not int or item['timeout'] != SHORT_TIMEOUT_MS):
            raise ValueError('等待步骤当前仅支持明确的 timeout: 12000；其他超时不能静默转换')
        for value in item.values():
            if isinstance(value, str) and value != value.strip():
                raise ValueError('动作文字首尾空格不能无损转换，请核对后去除')
        target = item[action]
        following = flow[index + 1] if index + 1 < len(flow) else {}
        expected = target if action == 'aiAssert' else ''
        steps.append({'action':action, 'target':target, 'value': {k:v for k,v in item.items() if k != action} if action == 'aiScroll' else item.get('value', ''),
                      'expected':expected, 'source_ref':'manual_yaml', 'review_state':'needs_review'})
        if index in boundaries:
            steps[-1]['stage_end'] = True
    # Fold only patterns which the compiler reconstructs byte-for-byte. Direct
    # assertions in older manual scripts stay explicit (no invented waits).
    compact = []
    consumed = set()
    for index, step in enumerate(steps):
        if index in consumed:
            continue
        following = steps[index + 1] if index + 1 < len(steps) else None
        check = steps[index + 2] if index + 2 < len(steps) else None
        if step['action'] == 'launch':
            if index != 0 or step['target'] != app_id:
                raise ValueError('禁止跨应用 launch，启动应用只能在首步声明一次')
            if following:
                continue
        if step['action'] == 'aiWaitFor' and following and following['action'] == 'aiTap' and click_target_condition(following['target']) == step['target']:
            continue
        if (step['action'] in {'aiTap','aiInput','aiScroll','cancelPrint'} and following and check
                and following['action']=='aiWaitFor' and check['action']=='aiAssert'
                and following['target']==check['target']):
            step['expected']=following['target']
            consumed.add(index+1)
            if index+2 < len(steps)-1 and not check.get('stage_end'):
                consumed.add(index+2)
        compact.append(step)
    if len(compact) > 50:
        raise ValueError('转换后超过50个中文步骤，请拆分用例；原始 YAML 已保留')
    normalized = normalize_steps({'title':title, 'preconditions':preconditions, 'steps':compact, 'questions':[]}, app_id)
    rebuilt = yaml.safe_load(build_draft_yaml(normalized, app_id)['yaml_content'])
    if rebuilt != document:
        raise ValueError('当前脚本不能无损转换为中文步骤；请保留自动启动、点击前等待及完整检查点，或恢复上一有效版')
    return normalized
