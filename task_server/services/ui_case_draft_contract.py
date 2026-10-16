"""Bounded, pure validation and source-YAML compilation for human-reviewed UI drafts.

Confirmation authorizes a candidate for subsequent platform gates; it never means
that a device has executed or verified the candidate.
"""
import copy
import math
import re
from uuid import UUID, uuid4

import yaml

from .yaml_static_validator import validate_midscene_action_parameters
from .ui_dynamic_object import PREFIX, validate_references

ACTIONS = frozenset({'launch', 'aiTap', 'aiWaitFor', 'aiAssert', 'aiInput', 'aiScroll', 'selectRecord'})
STEP_FIELDS = frozenset({'step_id', 'action', 'target', 'value', 'expected', 'review_state', 'source_ref'})
AMBIGUOUS_TARGETS = frozenset({'按钮', '这里', '那里', '它', '点击这里', '输入框', '列表', '页面', '正常', '功能正常', '可见', '显示', '成功', '通过', '显示正常', '操作成功'})
SHORT_TIMEOUT_MS = 12000


def _text(value, name, limit=2000):
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError(f'{name} 必须为不超过 {limit} 字的字符串')
    return value.strip()


def _app_id(app_id):
    app_id = _text(app_id, 'app_id', 255)
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+', app_id):
        raise ValueError('app_id 必须为当前应用包名')
    return app_id


def normalize_steps(payload, app_id):
    """Validate a text proposal/editor payload; allocate IDs once and retain valid IDs.

    Model callers must validate the stricter normalizer JSON schema first: only
    the human editing service may supply existing IDs or confirmed states.
    Provenance for future recording/video imports must be assigned by that service.
    """
    app_id = _app_id(app_id)
    if not isinstance(payload, dict) or set(payload) - {'title', 'preconditions', 'steps', 'questions'}:
        raise ValueError('草稿结构无效')
    title = _text(payload.get('title', '未命名用例'), 'title', 200)
    if not title:
        raise ValueError('title 不能为空')
    preconditions = payload.get('preconditions', [])
    if not isinstance(preconditions, list) or len(preconditions) > 20:
        raise ValueError('preconditions 必须为最多20项的列表')
    preconditions = [_text(item, 'preconditions', 1000) for item in preconditions]
    raw_steps = payload.get('steps')
    if not isinstance(raw_steps, list) or not 1 <= len(raw_steps) <= 50:
        raise ValueError('steps 必须包含1至50步')
    # Source questions remain blocking until the editor explicitly resolves them.
    supplied_questions = payload.get('questions', [])
    if not isinstance(supplied_questions, list) or len(supplied_questions) > 100:
        raise ValueError('questions 必须为最多100项的列表')
    for question in supplied_questions:
        _text(question, 'questions', 2000)
    steps, questions, ids = [], list(supplied_questions), set()
    launch_seen = False
    for index, raw in enumerate(raw_steps, 1):
        if not isinstance(raw, dict) or set(raw) - STEP_FIELDS:
            raise ValueError(f'步骤{index} 包含不支持字段')
        action = raw.get('action')
        if not isinstance(action, str) or action not in ACTIONS:
            raise ValueError(f'步骤{index} action 不支持')
        step_id = raw.get('step_id')
        if step_id is None:
            step_id = str(uuid4())
        try:
            if not isinstance(step_id, str) or str(UUID(step_id)) != step_id:
                raise ValueError()
        except (ValueError, AttributeError):
            raise ValueError(f'步骤{index} step_id 必须为规范UUID') from None
        if step_id in ids:
            raise ValueError('step_id 不得重复')
        ids.add(step_id)
        target = _text(raw.get('target', ''), 'target')
        expected = _text(raw.get('expected', ''), 'expected')
        review_state = raw.get('review_state', 'needs_review')
        if review_state not in ('needs_review', 'confirmed'):
            raise ValueError('review_state 仅支持 needs_review/confirmed')
        source_ref = raw.get('source_ref', 'text')
        if not isinstance(source_ref, str) or (source_ref not in {'text', 'manual_yaml'} and not re.fullmatch(r'(?:recording:[0-9a-f]{64}:[0-9a-f]{32}|video:uvs_[0-9a-f]{32}:[1-9][0-9]*:[0-9]+)', source_ref)):
            raise ValueError('source_ref 必须为文字或规范录制来源')
        value = copy.deepcopy(raw.get('value', ''))
        if action == 'aiScroll':
            if value == '':
                value = {}
            if not isinstance(value, dict) or set(value) - {'direction', 'distance', 'scrollType'}:
                raise ValueError('aiScroll value 参数不支持')
            distance = value.get('distance')
            if isinstance(distance, (int, float)) and not math.isfinite(distance):
                raise ValueError('aiScroll distance 必须为有限正数')
            issues = validate_midscene_action_parameters(action, {'aiScroll': target, **value})
            if issues:
                raise ValueError('; '.join(issues))
        else:
            value = _text(value, 'value', 4000)
            if action != 'aiInput' and value:
                raise ValueError(f'{action} 不支持 value')
        unresolved = []
        if action == 'launch':
            if target != app_id:
                raise ValueError('禁止跨应用 launch')
            if launch_seen or index != 1:
                raise ValueError('launch 只能作为首步声明一次')
            launch_seen = True
        else:
            if not target or target in AMBIGUOUS_TARGETS:
                unresolved.append('请明确目标控件或具体页面状态')
            if action not in ('aiWaitFor', 'aiAssert', 'selectRecord') and not expected:
                unresolved.append('请补充明确的预期结果')
            if action == 'aiInput' and not value:
                unresolved.append('请补充输入内容')
        if expected in AMBIGUOUS_TARGETS:
            unresolved.append('请在预期中写明检查对象及具体状态，例如：我的页面显示打印记录入口')
        if action == 'aiAssert' and expected and expected != target:
            unresolved.append('检查条件与预期不一致，请重新填写检查结果')
        if unresolved:
            review_state = 'needs_review'
            questions.append(f'步骤{index}：' + '；'.join(unresolved))
        steps.append({'step_id': step_id, 'action': action, 'target': target,
                      'value': value, 'expected': expected, 'review_state': review_state,
                      'source_ref': source_ref})
    validate_references(steps)
    if steps[-1]['action'] != 'aiAssert' or not steps[-1]['target'] or steps[-1]['target'] in AMBIGUOUS_TARGETS:
        questions.append('请在最后添加明确的 aiAssert 检查点')
    return {'title': title, 'preconditions': preconditions, 'steps': steps, 'questions': list(dict.fromkeys(questions))}


def build_draft_yaml(draft, app_id):
    """Build an Android source candidate; target compiler retains capability gates.

    Every generated flow entry maps to its source step. The automatic launch is
    attributed to the first source step rather than a synthetic editable step.
    """
    app_id = _app_id(app_id)
    normalized = normalize_steps(draft, app_id)
    steps = normalized['steps']
    flow, step_ids, roles = [], [], []
    deferred_checks = {}
    if steps[0]['action'] != 'launch':
        flow.append({'launch': app_id})
        step_ids.append(steps[0]['step_id'])
        roles.append('launch')
    for index, step in enumerate(steps):
        action, target = step['action'], step['target']
        if action == 'aiTap':
            flow.append({'aiWaitFor': target, 'timeout': SHORT_TIMEOUT_MS})
            step_ids.append(step['step_id'])
            roles.append('guard')
        item = {'aiQuery': PREFIX + target} if action == 'selectRecord' else {action: target}
        if action == 'aiInput':
            item['value'] = step['value']
        elif action == 'aiScroll':
            item.update(step['value'])
        elif action == 'aiWaitFor':
            item['timeout'] = SHORT_TIMEOUT_MS
        flow.append(item)
        step_ids.append(step['step_id'])
        roles.append('business')
        # A visible post-action state may arrive asynchronously. Wait briefly
        # before asserting it; keep explicit assertions and their source mapping.
        following = steps[index + 1] if index + 1 < len(steps) else None
        checks = []
        if index in deferred_checks:
            checks.append(deferred_checks[index])
        if action != 'aiAssert' and step['expected'] and not (action == 'aiWaitFor' and target == step['expected']):
            checks.append((step['expected'], step['step_id']))
        for expected, owner in dict.fromkeys(checks):
            if following and following['action'] == 'aiWaitFor' and following['target'] == expected:
                deferred_checks[index + 1] = (expected, owner)
                continue
            if not (action == 'aiWaitFor' and target == expected):
                flow.append({'aiWaitFor': expected, 'timeout': SHORT_TIMEOUT_MS})
                step_ids.append(owner)
                roles.append('guard')
            if not (following and following['action'] == 'aiAssert' and following['target'] == expected):
                flow.append({'aiAssert': expected})
                step_ids.append(owner)
                roles.append('expected')
    questions = list(normalized['questions'])
    unconfirmed = [str(index) for index, step in enumerate(steps, 1) if step['review_state'] != 'confirmed']
    if unconfirmed:
        questions.append('请确认建议步骤：' + '、'.join(unconfirmed))
    content = yaml.safe_dump({'android': {}, 'tasks': [{'name': normalized['title'], 'flow': flow}]},
                             allow_unicode=True, sort_keys=False)
    return {'yaml_content': content, 'step_ids': [step_ids], 'compiler_roles': [roles], 'questions': questions, 'ready': not questions}
