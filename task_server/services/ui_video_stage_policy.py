"""Opt-in video invocation capabilities; no worker activation or visual admission."""
import base64
import binascii
import json
import math
import re

UNSET = object()
POLICY_VERSION = 'video-stage-policy-v1'
STAGES = {
    'timeline': ('ui_video_timeline', 'v1', 'whole-timeline', 'qwen3.8-max', 10000),
    'perception': ('ui_video_perception', 'v1', 'whole-perception', 'qwen3.8-max', 8192),
    'overview': ('ui_video_trajectory', 'overview', 'whole-trajectory', 'qwen3.8-flash', 10000),
    'semantic': ('ui_video_trajectory', 'observe', 'whole-trajectory', 'qwen3.8-flash', 10000),
    'audit': ('ui_video_trajectory', 'audit', 'whole-trajectory', 'qwen3.8-max', 10000),
    'consolidate': ('ui_video_steps', 'consolidate', 'whole-consolidate', 'qwen3.8-max', 10000),
    'outcome': ('ui_video_observe', 'outcome', 'whole-observe', 'qwen3.8-max', 4000),
    'inputs': ('ui_video_observe', 'inputs', 'whole-observe', 'qwen3.8-max', 3000),
    # Existing bank runner resolves observe by first match; semantic must stay first.
    'grounding': ('ui_video_trajectory', 'observe', 'whole-trajectory', 'qwen3.8-max', 10000),
}
_SEMANTIC_FIELDS = {'states', 'events', 'checks', 'uncertainties', 'multiple_scenarios'}


def video_stage_image_limit(stage=None):
    """Closed stage count policy; legacy callers retain the 60-image bound."""
    if stage in ('grounding', 'audit'):
        return 128
    if stage == 'perception':
        return 12
    if stage == 'semantic':
        return 0
    if stage == 'timeline':
        return 1
    return 60


def _provider_projection(node):
    """Project the canonical observer schema into the bounded provider subset."""
    if 'oneOf' in node:
        return {'anyOf': [_provider_projection(child) for child in node['oneOf']]}
    out = {k: node[k] for k in ('type', 'enum', 'additionalProperties') if k in node}
    if 'enum' in out:
        out.setdefault('type', 'string')
    if node.get('maxLength') == 0:
        out['enum'] = ['']
    if 'properties' in node:
        out['properties'] = {k: _provider_projection(v) for k, v in node['properties'].items()}
        out['required'] = list(out['properties'])
    if 'items' in node:
        out['items'] = _provider_projection(node['items'])
    return out


def validate_output_schema(schema):
    """Only the original observer scaffold, with bounded leaf-enum narrowing."""
    def fail():
        raise ValueError('video_output_schema_invalid')
    try:
        encoded = json.dumps(schema, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        fail()
    if len(encoded.encode('utf-8')) > 32768:
        fail()
    count = 0
    def normalize(node, depth=0):
        nonlocal count
        count += 1
        if count > 512 or depth > 16 or not isinstance(node, dict):
            fail()
        if set(node) in ({'anyOf'}, {'oneOf'}):
            variants = next(iter(node.values()))
            if not isinstance(variants, list) or not 2 <= len(variants) <= 8:
                fail()
            return {'anyOf': [normalize(child, depth + 1) for child in variants]}
        out = dict(node)
        if set(out) == {'enum'}:
            out['type'] = 'string'
        kind = out.get('type')
        if not isinstance(kind, str):
            fail()
        allowed = {'type', 'enum'}
        if kind == 'object':
            allowed |= {'properties', 'required', 'additionalProperties'}
            props, required = out.get('properties'), out.get('required')
            if (not isinstance(props, dict) or not isinstance(required, list) or
                    any(not isinstance(k, str) or not k or len(k) > 128 for k in props) or
                    any(not isinstance(k, str) for k in required) or
                    len(required) != len(set(required)) or set(required) != set(props) or
                    out.get('additionalProperties') is not False):
                fail()
            out['properties'] = {k: normalize(child, depth + 1) for k, child in props.items()}
        elif kind == 'array':
            allowed.add('items')
            out['items'] = normalize(out.get('items'), depth + 1)
        elif kind not in {'string', 'boolean', 'integer', 'number'}:
            fail()
        if set(out) - allowed:
            fail()
        if 'enum' in out:
            values = out['enum']
            if (kind in {'object', 'array'} or not isinstance(values, list) or not 1 <= len(values) <= 128 or
                    any(type(v) not in {'string': (str,), 'boolean': (bool,), 'integer': (int,),
                                         'number': (int, float)}[kind] for v in values)):
                fail()
            if len(set(values)) != len(values):
                fail()
        return out
    normalized = normalize(schema)
    # The source contract remains canonical. No alternate event/state scaffolds.
    from .ai_skill_service import load_ai_skill_schema
    expected = _provider_projection(load_ai_skill_schema('ui_video_trajectory')['oneOf'][0])
    props = normalized.get('properties', {})
    if set(props) == _SEMANTIC_FIELDS:
        del expected['properties']['revisit']
        expected['required'].remove('revisit')
    def match(actual, contract):
        if 'anyOf' in contract:
            if set(actual) != {'anyOf'} or len(actual['anyOf']) != len(contract['anyOf']):
                fail()
            for child, wanted in zip(actual['anyOf'], contract['anyOf']):
                match(child, wanted)
            return
        if actual.get('type') != contract['type']:
            fail()
        kind = contract['type']
        if kind == 'object':
            if set(actual['properties']) != set(contract['properties']):
                fail()
            for key, wanted in contract['properties'].items():
                match(actual['properties'][key], wanted)
        elif kind == 'array':
            match(actual['items'], contract['items'])
        if 'enum' in contract:
            if contract['enum'] == [''] and 'enum' not in actual:
                actual['enum'] = ['']  # Preserve original maxLength:0 after projection.
            if 'enum' not in actual or not set(actual['enum']) <= set(contract['enum']):
                fail()
    match(normalized, expected)
    return normalized


def _metadata(frames):
    if not isinstance(frames, list) or not 2 <= len(frames) <= 1000:
        raise ValueError('video_frames_invalid')
    previous, seen = -1, set()
    for frame in frames:
        if not isinstance(frame, dict):
            raise ValueError('video_frames_invalid')
        name, stamp = frame.get('frame_id'), frame.get('timestamp_ms')
        if (not isinstance(name, str) or not re.fullmatch(r'frame-(?:[0-9]{3}|end)', name) or
                name in seen or type(stamp) is not int or not previous < stamp <= 600000):
            raise ValueError('video_frames_invalid')
        seen.add(name)
        previous = stamp


def validate_video_stage(skill, payload, assets, stage=UNSET, schema=UNSET, *, version="v1"):
    """Validate before transport. Explicit null capabilities are invalid."""
    if stage is UNSET:
        if (skill in {'ui_video_perception', 'ui_video_timeline'} or
                isinstance(assets, list) and any(isinstance(a, dict) and
                    (a.get('mime') == 'video/mp4' or a.get('name') == 'source-video') for a in assets)):
            raise ValueError('video_stage_required')
        if schema is not UNSET:
            raise ValueError('video_stage_schema_without_stage')
        return None
    if not isinstance(stage, str) or stage not in STAGES:
        raise ValueError('video_stage_invalid')
    if not isinstance(payload, dict):
        raise ValueError('video_stage_payload_invalid')
    expected, expected_version, phase, _, _ = STAGES[stage]
    if skill != expected or version != expected_version or payload.get('phase') != phase:
        raise ValueError('video_stage_scope_invalid')
    if stage == 'timeline':
        from .ui_video_native import validate_native_asset
        if schema is not UNSET:
            raise ValueError('video_stage_schema_invalid')
        validate_native_asset(payload, assets)
        return stage
    if stage == 'grounding':
        if payload.get('reasoning_mode') != 'bounded' or 'perception_bank' in payload:
            raise ValueError('video_stage_grounding_invalid')
        validate_output_schema(schema)
        from .ui_video_perception_binding import _source_index
        try:
            _source_index(payload)
        except ValueError as exc:
            raise ValueError('video_frames_invalid') from exc
        if payload.get('frames') != payload.get('image_frames'):
            raise ValueError('video_frames_invalid')
    if stage == 'semantic':
        if assets not in (None, []) or payload.get('reasoning_mode') != 'bounded':
            raise ValueError('video_stage_semantic_invalid')
        if any(k in payload for k in ('image_frames', 'detail_frames', 'perception_mode', 'perception_frame_ids')):
            raise ValueError('video_stage_semantic_invalid')
        _metadata(payload.get('frames'))
        validate_output_schema(schema)
        return stage
    if schema is not UNSET and stage != 'grounding':
        raise ValueError('video_stage_schema_invalid')
    if stage == 'perception':
        from .ui_video_perception_binding import _source_index
        if (not isinstance(assets, list) or not 2 <= len(assets) <= 12 or
                payload.get('image_frames') != payload.get('frames') or
                any(k in payload for k in ('detail_frames', 'perception_mode', 'perception_frame_ids'))):
            raise ValueError('video_stage_perception_invalid')
        try:
            _source_index(payload)
        except ValueError as exc:
            raise ValueError('video_frames_invalid') from exc
    elif ('detail_frames' in payload or
          (stage in {'overview', 'audit'} and payload.get('reasoning_mode') != 'bounded')):
        raise ValueError('video_stage_audit_invalid')
    if stage not in {'audit', 'perception'} and any(k in payload for k in ('perception_mode', 'perception_frame_ids')):
        raise ValueError('video_perception_selection_invalid')
    frames = payload.get('image_frames')
    _metadata(frames)
    if not isinstance(assets, list) or len(assets) != len(frames) or not 2 <= len(assets) <= video_stage_image_limit(stage):
        raise ValueError('video_vision_limit')
    if stage == 'audit' and any(k in payload for k in ('perception_mode', 'perception_frame_ids')):
        if payload.get('perception_mode') != 'fine-detail':
            raise ValueError('video_perception_selection_invalid')
        selected = payload.get('perception_frame_ids')
        if (not isinstance(selected, list) or not 1 <= len(selected) <= 12 or
                any(not isinstance(ref, str) for ref in selected) or len(set(selected)) != len(selected) or
                not set(selected) <= {frame['frame_id'] for frame in frames}):
            raise ValueError('video_perception_selection_invalid')
    total = 0
    for frame, asset in zip(frames, assets):
        if not isinstance(asset, dict):
            raise ValueError('video_frames_invalid')
        data = asset.get('base64')
        if (asset.get('name') != frame['frame_id'] or asset.get('timestamp_ms') != frame['timestamp_ms'] or
                asset.get('mime') != 'image/jpeg' or not isinstance(data, str) or not data or asset.get('dataUrl')):
            raise ValueError('video_frames_invalid')
        total += len(data)
        if total > 10 * 1024 * 1024:
            raise ValueError('video_vision_limit')
        try:
            base64.b64decode(data, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError('video_frames_invalid') from None
    return stage


def validate_stage_output(value, schema, path='$'):
    """Validate the already checked strict subset without weakening legacy schemas."""
    if 'anyOf' in schema:
        for child in schema['anyOf']:
            try:
                return validate_stage_output(value, child, path)
            except ValueError:
                pass
        raise ValueError(f'video_semantic_output_invalid:{path}')
    kind = schema['type']
    valid = {'object': lambda: isinstance(value, dict), 'array': lambda: isinstance(value, list),
             'string': lambda: isinstance(value, str), 'boolean': lambda: type(value) is bool,
             'integer': lambda: type(value) is int, 'number': lambda: type(value) in (int, float)}[kind]()
    if kind == 'number' and valid and not math.isfinite(value):
        valid = False
    if not valid or ('enum' in schema and value not in schema['enum']):
        raise ValueError(f'video_semantic_output_invalid:{path}')
    if kind == 'object':
        if set(value) != set(schema['properties']):
            raise ValueError(f'video_semantic_output_invalid:{path}')
        for key, child in schema['properties'].items():
            validate_stage_output(value[key], child, f'{path}.{key}')
    elif kind == 'array':
        for index, child in enumerate(value):
            validate_stage_output(child, schema['items'], f'{path}[{index}]')
    return value
