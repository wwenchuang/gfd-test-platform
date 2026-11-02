"""Versioned stage proof verification. Hashes are canonical options, not wire bytes."""
import base64
import copy
import hashlib
import json
import re
import time
from .ui_video_native import NATIVE_FILE_TRANSPORT, MAX_NATIVE_BYTES, validate_native_asset
from .ui_video_stage_policy import POLICY_VERSION, STAGES, video_stage_image_limit

SYSTEM_PROMPT = '你是移动端测试平台 AI Skill 执行器。必须严格遵守用户输入的 Skill Prompt，只输出合法 JSON。'


def canonical_sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


_NATIVE_RESOURCE_KEYS = {'version', 'uri', 'source_sha256', 'source_bytes', 'source_md5',
    'model', 'correlation', 'resource_reference_sha256', 'uploaded_at_ms',
    'documented_max_lifetime_seconds', 'private_acl', 'forbid_overwrite', 'resolve_header'}
_RESOLVE_HEADERS = {'X-DashScope-OssResourceResolve': 'enable'}


def _safe_native_transport(value):
    """Closed metadata only, including for rejected/untrusted replies."""
    if type(value) is not dict:
        return {}
    out = {}
    for key, allowed in (('version', {NATIVE_FILE_TRANSPORT}), ('model', {'qwen3.8-max'}),
                         ('resolve_header', {'enable'})):
        item = value.get(key)
        if type(item) is str and item in allowed:
            out[key] = item
    for key, size in (('source_sha256', 64), ('source_md5', 32), ('correlation', 32),
                      ('resource_reference_sha256', 64), ('provider_headers_sha256', 64)):
        item = value.get(key)
        if type(item) is str and re.fullmatch('[0-9a-f]{' + str(size) + '}', item):
            out[key] = item
    for key, lower, upper in (('source_bytes', 12, MAX_NATIVE_BYTES),
            ('uploaded_at_ms', 1, 2**53-1), ('documented_max_lifetime_seconds', 172800, 172800)):
        item = value.get(key)
        if type(item) is int and lower <= item <= upper:
            out[key] = item
    for key in ('private_acl', 'forbid_overwrite'):
        if value.get(key) is True:
            out[key] = True
    return out


def _validated_native_resource(body, native_resource):
    """Live capability check; sanitized archives cannot reconstruct the opaque URI."""
    def fail():
        raise ValueError('video_native_resource_invalid')
    if body.get('videoStage') != 'timeline':
        if native_resource is not None:
            fail()
        return None
    meta = body.get('payload', {}).get('video', {})
    if type(meta) is not dict:
        fail()
    mode = meta.get('transport')
    if 'transport' not in meta and native_resource is None:
        return None
    if (body.get('videoStage') != 'timeline' or mode != NATIVE_FILE_TRANSPORT or
            type(native_resource) is not dict or set(native_resource) != _NATIVE_RESOURCE_KEYS):
        fail()
    validate_native_asset(body['payload'], body['imageAssets'])
    resource = native_resource
    safe = _safe_native_transport(resource)
    if set(safe) != _NATIVE_RESOURCE_KEYS - {'uri'}:
        fail()
    uri = resource['uri'];correlation = body.get('videoCorrelation')
    if (type(correlation) is not str or not re.fullmatch('[0-9a-f]{32}', correlation) or
            type(uri) is not str or len(uri) > 1024 or
            not re.fullmatch(r'oss://dashscope-instant/(?:[A-Za-z0-9_-]+/)+[0-9a-f]{32}\.mp4', uri) or
            not uri.endswith('/' + correlation + '.mp4')):
        fail()
    meta = body['payload']['video'];deadline = body.get('videoDeadlineMs')
    timeout_ms = body.get('timeoutMs');now_ms = int(time.time()*1000)
    raw = base64.b64decode(body['imageAssets'][0]['base64'], validate=True)
    if (resource['source_sha256'] != meta['source_sha256'] or
            resource['source_bytes'] != meta['source_bytes'] or
            resource['source_md5'] != hashlib.md5(raw).hexdigest() or
            resource['correlation'] != correlation or
            resource['resource_reference_sha256'] != hashlib.sha256(uri.encode()).hexdigest() or
            type(deadline) is not int or not 0 < deadline <= 2**53-1 or
            type(timeout_ms) is not int or not 1 <= timeout_ms <= 120000 or
            not max(deadline-timeout_ms-1000, now_ms-121000) <= resource['uploaded_at_ms'] <= min(deadline, now_ms+1000)):
        fail()
    return resource


def expected_provider_options(body, *, native_resource=None):
    """Independent reconstruction of the closed video-stage Gateway wire policy."""
    resource = _validated_native_resource(body, native_resource)
    stage = body['videoStage'];payload = body['payload'];assets = body['imageAssets']
    content = []
    for asset in assets:
        if stage == 'timeline':
            content.append({'type':'video_url','video_url':{'url':resource['uri'] if resource is not None else 'data:video/mp4;base64,'+asset['base64']},
                            **{k:payload['video'][k] for k in ('fps','max_pixels','total_pixels')}})
            continue
        name = asset['name'];role = ''
        if stage == 'perception':
            detail = next((d for d in payload.get('detail_frames', []) if d['frame_id'] == name), None)
            parent = detail['source_frame_id'] if detail else name
            parent_role = 'context' if parent in payload['context_frame_ids'] else 'owned'
            role = (f" [source_frame_id={parent} box={json.dumps(detail['box'], separators=(',', ':'))} role={parent_role}; same-frame visual assistance; no separate time/event/action or ownership]"
                    if detail else f' [{parent_role}]')
        elif body['skillName'] in {'ui_video_observe', 'ui_video_trajectory'} and isinstance(payload.get('context_frame_ids'), list):
            role = (' [可引用为前后态或结果证据；不得作为本包新动作触发]' if body['skillName']=='ui_video_trajectory'
                    else ' [仅供上下文参考，禁止引用或输出该帧操作]') if name in payload['context_frame_ids'] else ' [当前窗口证据，可引用]'
        content.append({'type':'text','text':f"{name} {asset['timestamp_ms']}ms{role}"})
        part = {'type':'image_url','image_url':{'url':'data:image/jpeg;base64,'+asset['base64']}}
        if stage=='perception' or name in payload.get('perception_frame_ids', []):part['min_pixels']=1658880
        content.append(part)
    if assets:content.append({'type':'text','text':body['prompt']})
    options = {'model':STAGES[stage][3], 'temperature':0.1, 'max_tokens':STAGES[stage][4],
        'enable_thinking':stage!='perception', 'response_format':{'type':'json_object'},
        'messages':[{'role':'system','content':SYSTEM_PROMPT},
                    {'role':'user','content':content if assets else body['prompt']}]}
    if stage!='perception':options['thinking_budget']=2048 if stage in {'grounding','audit'} else 1024
    if stage in {'semantic','grounding'} or (stage == 'audit' and 'outputSchema' in body):options['response_format']={'type':'json_schema','json_schema':{
        'name':'video_audit' if stage == 'audit' else 'video_semantic','strict':True,'schema':body['outputSchema']}}
    return options


def make_expected_attestation(body, *, native_resource=None):
    options = expected_provider_options(body, native_resource=native_resource)
    effective = {k:copy.deepcopy(v) for k,v in options.items() if k!='messages'}
    effective['image_count']=0 if body['videoStage']=='timeline' else len(body['imageAssets'])
    if body['videoStage']=='timeline':effective['video_count']=1
    effective['fine_frame_ids']=[a['name'] for a in body['imageAssets'] if body['videoStage']=='perception' or a['name'] in body['payload'].get('perception_frame_ids',[])]
    # Schema remains private request data; export only its digest.
    effective['response_format']=options['response_format']['type']
    policy = {'version':POLICY_VERSION,'stage':body['videoStage'],'skill':body['skillName'],
        'skill_version':body['skillVersion'],'correlation':body['videoCorrelation'],
        'model':options['model'],'effective_options':effective,
        'provider_options_sha256':canonical_sha(options),
        'output_schema_sha256':canonical_sha(body['outputSchema']) if 'outputSchema' in body else None}
    if native_resource is not None:
        policy['native_transport'] = {**{k:v for k,v in native_resource.items() if k != 'uri'},
            'provider_headers_sha256':canonical_sha(_RESOLVE_HEADERS)}
    return policy


def safe_attestation(value):
    """Export only closed policy metadata, even before full attestation rejection."""
    if not isinstance(value, dict):
        return None
    out = {}
    enums = {
        'version': {POLICY_VERSION}, 'stage': set(STAGES),
        'skill': {config[0] for config in STAGES.values()},
        'skill_version': {config[1] for config in STAGES.values()},
        'model': {config[3] for config in STAGES.values()},
    }
    for key, allowed in enums.items():
        item = value.get(key)
        if type(item) is str and item in allowed:
            out[key] = item
    for key, length in (('correlation', 32), ('provider_options_sha256', 64),
                        ('output_schema_sha256', 64)):
        item = value.get(key)
        if type(item) is str and re.fullmatch(r'[0-9a-f]{' + str(length) + '}', item):
            out[key] = item
        elif key == 'output_schema_sha256' and key in value and item is None:
            out[key] = None
    if type(value.get('native_transport')) is dict:
        out['native_transport'] = _safe_native_transport(value['native_transport'])
    effective = value.get('effective_options')
    if isinstance(effective, dict):
        safe = {}
        for key, allowed in (('model', enums['model']),
                             ('response_format', {'json_object', 'json_schema'})):
            item = effective.get(key)
            if type(item) is str and item in allowed:
                safe[key] = item
        item = effective.get('temperature')
        if type(item) in (int, float) and item == 0.1:
            safe['temperature'] = item
        for key, allowed in (('max_tokens', {config[4] for config in STAGES.values()}),
                             ('thinking_budget', {1024, 2048}), ('video_count', {1}), ('image_count', {0, *range(2, video_stage_image_limit(value.get('stage')) + 1)})):
            item = effective.get(key)
            if type(item) is int and item in allowed:
                safe[key] = item
        item = effective.get('enable_thinking')
        if type(item) is bool:
            safe['enable_thinking'] = item
        refs = effective.get('fine_frame_ids')
        if (isinstance(refs, list) and len(refs) <= 12 and
                all(type(ref) is str and re.fullmatch(r'frame-(?:[0-9]{3}|end)' + (r'(?:-detail-[0-2])?' if value.get('stage') == 'perception' else ''), ref) for ref in refs) and
                len(set(refs)) == len(refs)):
            safe['fine_frame_ids'] = list(refs)
        out['effective_options'] = safe
    return out


def validate_attestation(value, body, *, native_resource=None):
    # Python dict equality conflates booleans/integers and integers/equal floats.
    # Canonical JSON preserves their wire types, as in the existing options proof.
    try:
        matches = canonical_sha(value) == canonical_sha(make_expected_attestation(body, native_resource=native_resource))
    except (TypeError, ValueError, OverflowError, RecursionError):
        raise RuntimeError('video_stage_attestation_invalid') from None
    if not matches:
        raise RuntimeError('video_stage_attestation_invalid')


def cached_token_usage(usage):
    """Optional measured cache count only; base token legacy types stay intact."""
    if not isinstance(usage, dict):
        return {}
    prompt, cached = usage.get('promptTokens'), usage.get('cachedTokens')
    if type(prompt) is int and 0 <= prompt <= 2**53 - 1 and type(cached) is int and 0 <= cached <= prompt:
        return {'cachedTokens': cached}
    return {}
