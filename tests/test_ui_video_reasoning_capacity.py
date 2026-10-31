"""Offline closed capacity policy; literal expectations independent of Gateway/Python tables."""
import copy
import hashlib
import io
import json

import pytest
from task_server.services import ai_skill_service as ai
from task_server.services.ui_video_stage_receipts import (
    expected_provider_options, make_expected_attestation, safe_attestation, validate_attestation,
)
from test_ui_video_stage_runtime import request
from test_ui_video_stage_wire import evidence, semantic_schema
from test_ui_video_business_transport import native

# Deliberately literal: agreement between implementations alone cannot prove this policy.
POLICIES = [
    ('timeline', 'ui_video_timeline', 'v1', 'qwen3.8-max', 10000, 1024),
    ('perception', 'ui_video_perception', 'v1', 'qwen3.8-max', 8192, None),
    ('overview', 'ui_video_trajectory', 'overview', 'qwen3.8-flash', 10000, 1024),
    ('semantic', 'ui_video_trajectory', 'observe', 'qwen3.8-flash', 10000, 1024),
    ('grounding', 'ui_video_trajectory', 'observe', 'qwen3.8-max', 10000, 2048),
    ('audit', 'ui_video_trajectory', 'audit', 'qwen3.8-max', 10000, 2048),
    ('consolidate', 'ui_video_steps', 'consolidate', 'qwen3.8-max', 10000, 1024),
    ('outcome', 'ui_video_observe', 'outcome', 'qwen3.8-max', 4000, 1024),
    ('inputs', 'ui_video_observe', 'inputs', 'qwen3.8-max', 3000, 1024),
]


def body_for(stage, skill, version):
    if stage == 'timeline':
        payload, assets = native()
    elif stage == 'grounding':
        payload, assets = evidence()
        payload.update(phase='whole-trajectory', reasoning_mode='bounded')
    else:
        _, kw = request(stage)
        payload, assets = kw['payload'], kw['image_assets']
    body = dict(videoStage=stage, skillName=skill, skillVersion=version, payload=payload,
                imageAssets=assets, prompt='像素 😀', videoCorrelation='a'*32)
    if stage in {'semantic', 'grounding'}:
        body['outputSchema'] = semantic_schema()
    return body


def literal_options(body, model, tokens, budget):
    content = []
    stage = body['videoStage']
    for asset in body['imageAssets']:
        if stage == 'timeline':
            content.append({'type': 'video_url', 'video_url': {'url': 'data:video/mp4;base64,'+asset['base64']},
                            'fps': 4, 'max_pixels': 655360, 'total_pixels': 268435456})
            continue
        name = asset['name']
        context = name in body['payload']['context_frame_ids']
        role = (' [context]' if context else ' [owned]') if stage == 'perception' else (
            ' [可引用为前后态或结果证据；不得作为本包新动作触发]' if context else ' [当前窗口证据，可引用]') if body['skillName'] == 'ui_video_trajectory' else (
            ' [仅供上下文参考，禁止引用或输出该帧操作]' if context else ' [当前窗口证据，可引用]') if body['skillName'] == 'ui_video_observe' else ''
        content.append({'type': 'text', 'text': f"{name} {asset['timestamp_ms']}ms{role}"})
        image = {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,'+asset['base64']}}
        if stage == 'perception':
            image['min_pixels'] = 1658880
        content.append(image)
    if content:
        content.append({'type': 'text', 'text': body['prompt']})
    options = {'model': model, 'temperature': .1, 'max_tokens': tokens, 'enable_thinking': budget is not None,
               'response_format': {'type': 'json_object'}, 'messages': [
                   {'role': 'system', 'content': '你是移动端测试平台 AI Skill 执行器。必须严格遵守用户输入的 Skill Prompt，只输出合法 JSON。'},
                   {'role': 'user', 'content': content or body['prompt']}]}
    if budget is not None:
        options['thinking_budget'] = budget
    if stage in {'semantic', 'grounding'}:
        options['response_format'] = {'type': 'json_schema', 'json_schema': {
            'name': 'video_semantic', 'strict': True, 'schema': body['outputSchema']}}
    return options


def digest(options):
    return hashlib.sha256(json.dumps(options, sort_keys=True, ensure_ascii=False,
                                   separators=(',', ':'), allow_nan=False).encode()).hexdigest()


@pytest.mark.parametrize('stage,skill,version,model,tokens,budget', POLICIES)
def test_literal_options_hash_and_wrong_stage_budget(stage, skill, version, model, tokens, budget):
    body = body_for(stage, skill, version)
    options = literal_options(body, model, tokens, budget)
    before = copy.deepcopy(body)
    assert expected_provider_options(body) == options
    attestation = make_expected_attestation(body)
    assert attestation['provider_options_sha256'] == digest(options)
    assert attestation['version'] == 'video-stage-policy-v1'
    assert safe_attestation(attestation) == attestation
    validate_attestation(attestation, body)
    assert body == before
    # Forge an internally consistent alternate-budget hash, not just metadata.
    for wrong in [1024, 2048, 4096, True, 1024.0, 2048.0, None]:
        if type(wrong) is int and wrong == budget:
            continue
        forged = copy.deepcopy(attestation)
        altered = copy.deepcopy(options)
        altered['thinking_budget'] = wrong
        forged['effective_options']['thinking_budget'] = wrong
        forged['provider_options_sha256'] = digest(altered)
        with pytest.raises(RuntimeError, match='^video_stage_attestation_invalid$'):
            validate_attestation(forged, body)


@pytest.mark.parametrize('budget', [1024, 2048])
def test_historical_and_current_safe_integer_metadata_is_detached(budget):
    original = {'stage': 'audit', 'effective_options': {'thinking_budget': budget}}
    safe = safe_attestation(original)
    assert safe == original
    safe['effective_options']['thinking_budget'] = 1
    assert original['effective_options']['thinking_budget'] == budget


@pytest.mark.parametrize('budget', [True, False, 1024.0, 2048.0, 4096, '2048', None])
def test_safe_metadata_rejects_coerced_and_unapproved_budgets(budget):
    assert safe_attestation({'effective_options': {'thinking_budget': budget}}) == {'effective_options': {}}


@pytest.mark.parametrize('stage,budget', [('audit', 1024), ('grounding', 1024), ('inputs', 2048)])
def test_wrong_stage_budget_receipted_before_admission(monkeypatch, stage, budget):
    if stage == 'grounding':
        skill = 'ui_video_trajectory'
        kw = dict(payload=body_for(stage, skill, 'observe')['payload'], image_assets=evidence()[1],
                  video_stage=stage, version='observe', output_schema=semantic_schema())
    else:
        skill, kw = request(stage)
    receipts = []
    def http(req, timeout):
        body = json.loads(req.data)
        options = expected_provider_options(body)
        options['thinking_budget'] = budget
        proof = make_expected_attestation(body)
        proof['effective_options']['thinking_budget'] = budget
        proof['provider_options_sha256'] = digest(options)
        return io.BytesIO(json.dumps(dict(success=True, content='untouched raw output', model='qwen3.8-max',
            fallbackUsed=False, finishReason='stop', videoPolicy=proof)).encode())
    monkeypatch.setattr(ai.urllib.request, 'urlopen', http)
    with pytest.raises(RuntimeError, match='^video_stage_attestation_invalid$'):
        ai.ai_gateway_skill_content(skill, 'pixels', **kw, raw_receipt=receipts.append)
    assert len(receipts) == 1 and receipts[0]['content'] == 'untouched raw output'
    assert receipts[0]['videoPolicy']['effective_options']['thinking_budget'] == budget
