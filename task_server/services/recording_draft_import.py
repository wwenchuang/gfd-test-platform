"""Deterministic, non-executing recording adapter. No legacy generated YAML is used."""
import hashlib
import json
import re
from uuid import UUID

from .device_recording_service import confirmed_recording_description, _position_only_target_context
from .ui_case_draft_contract import normalize_steps, build_draft_yaml, AMBIGUOUS_TARGETS


def proposal(session, app):
    raw = session.get('steps')
    if not isinstance(raw, list) or not 1 <= len(raw) <= 50:
        raise ValueError('recording_import_size')
    # Whitelist immutable semantic/evidence metadata; never copy paths or bridge secrets.
    fields = {'id', 'type', 'sequence', 'recorded_at', 'semantic_description', 'semantic_source',
              'semantic_recognition_status', 'semantic_confidence', 'evidence_status',
              'screen_change_status', 'text', 'key', 'package', 'description', 'checkpoint_kind'}
    actions = []
    for step in raw:
        if not isinstance(step, dict) or not re.fullmatch(r'[0-9a-f]{32}', str(step.get('id', ''))):
            raise ValueError('recording_import_invalid')
        item = {k: v for k, v in step.items() if k in fields}
        for key in ('screenshot_sha256', 'after_screenshot_sha256'):
            value = step.get(key)
            if isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value):
                item[key] = value
        # Keep coordinates only in source evidence, never in executable parameters.
        for key in ('point', 'start', 'end'):
            value = step.get(key)
            if isinstance(value, dict) and all(type(value.get(k)) is int and 0 <= value[k] <= 100000 for k in ('x', 'y')):
                item[key] = {k: value[k] for k in ('x', 'y')}
        item['evidence_warning_present'] = bool(step.get('evidence_warning'))
        actions.append(item)
    snapshot = {'session_id': session['id'], 'app_id': app['package'], 'actions': actions,
                'evidence_note': '仅保存录制时证据标识与校验摘要；原截图可能已变更或不可用，未核对原图内容。'}
    encoded = json.dumps(snapshot, sort_keys=True, ensure_ascii=False, allow_nan=False)
    if len(encoded.encode()) > 200000:
        raise ValueError('recording_import_size')
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    snapshot['snapshot_id'] = digest
    steps, questions, missing_results, missing_frames = [], [], [], []
    for index, item in enumerate(actions, 1):
        kind = item.get('type')
        action = {'tap': 'aiTap', 'text': 'aiInput', 'swipe': 'aiScroll', 'launch': 'launch',
                  'checkpoint': 'aiAssert' if item.get('checkpoint_kind') == 'assert' else 'aiWaitFor'}.get(kind)
        if not action:
            questions.append(f'录制第{index}步：不支持的系统按键或操作，请人工补充等效步骤或确认移除')
            continue
        target, value = '', ''
        if kind == 'launch':
            if item.get('package') != app['package']:
                raise ValueError('recording_import_cross_app')
            if steps:
                questions.append(f'录制第{index}步：中途启动应用需人工核对，未转换为执行步骤')
                continue
            target = app['package']
        elif kind == 'checkpoint':
            target = item.get('description') or ''
        elif kind in {'tap', 'text'}:
            if item.get('evidence_status') == 'captured' and not item['evidence_warning_present']:
                target = confirmed_recording_description(item)
                if target in AMBIGUOUS_TARGETS | {'待识别', '未知'} or _position_only_target_context(re.sub(r'^点击', '', target)):
                    target = ''
            if kind == 'text':
                value = item.get('text') or ''
        else:
            # Gesture direction alone cannot identify the intended scroll container.
            value = {}
            start, end = item.get('start'), item.get('end')
            if start and end:
                dx, dy = end['x'] - start['x'], end['y'] - start['y']
                if dx or dy:
                    value = {'direction': ('down' if dy < 0 else 'up') if abs(dy) >= abs(dx) else ('right' if dx < 0 else 'left')}
        if kind != 'checkpoint':
            missing_results.append(str(index))
        if item['evidence_warning_present'] or item.get('evidence_status') != 'captured':
            missing_frames.append(str(index))
        sid = str(UUID(item['id']))
        item['draft_step_id'] = sid
        ref = f'recording:{digest}:{item["id"]}'
        steps.append({'step_id': sid, 'action': action, 'target': target, 'value': value,
                      'expected': '', 'source_ref': ref, 'review_state': 'needs_review'})
    if missing_results:
        questions.append('录制第' + '、'.join(missing_results) + '步：缺少已核对的操作后结果证据，请补充具体预期并人工确认；动作送达不代表页面成功跳转')
    if missing_frames:
        questions.append('录制第' + '、'.join(missing_frames) + '步：截图缺失、过期或状态待核对，请人工检查来源')
    if not steps:
        steps = [{'action': 'aiAssert', 'target': '', 'expected': '', 'value': '', 'source_ref': 'text'}]
    normalized = normalize_steps({'title': '录制整理用例', 'preconditions': [], 'steps': steps, 'questions': questions}, app['package'])
    return snapshot, {**normalized, 'yaml_content': build_draft_yaml(normalized, app['package'])['yaml_content'], 'ready': False}
