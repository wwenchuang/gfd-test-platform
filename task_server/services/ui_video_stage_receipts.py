"""Versioned stage proof verification. Hashes are canonical options, not wire bytes."""
import copy
import hashlib
import json
import re
from .ui_video_stage_policy import POLICY_VERSION, STAGES, video_stage_image_limit

SYSTEM_PROMPT = '你是移动端测试平台 AI Skill 执行器。必须严格遵守用户输入的 Skill Prompt，只输出合法 JSON。'


def canonical_sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def expected_provider_options(body):
    """Independent reconstruction of the closed video-stage Gateway wire policy."""
    stage = body['videoStage'];payload = body['payload'];assets = body['imageAssets']
    content = [{'type':'text','text':body['prompt']}]
    for asset in assets:
        if stage == 'timeline':
            content.append({'type':'video_url','video_url':{'url':'data:video/mp4;base64,'+asset['base64']},
                            **{k:payload['video'][k] for k in ('fps','max_pixels','total_pixels')}})
            continue
        name = asset['name'];role = ''
        if stage == 'perception':
            role = ' [context]' if name in payload['context_frame_ids'] else ' [owned]'
        elif body['skillName'] in {'ui_video_observe', 'ui_video_trajectory'} and isinstance(payload.get('context_frame_ids'), list):
            role = (' [可引用为前后态或结果证据；不得作为本包新动作触发]' if body['skillName']=='ui_video_trajectory'
                    else ' [仅供上下文参考，禁止引用或输出该帧操作]') if name in payload['context_frame_ids'] else ' [当前窗口证据，可引用]'
        content.append({'type':'text','text':f"{name} {asset['timestamp_ms']}ms{role}"})
        part = {'type':'image_url','image_url':{'url':'data:image/jpeg;base64,'+asset['base64']}}
        if stage=='perception' or name in payload.get('perception_frame_ids', []):part['min_pixels']=1658880
        content.append(part)
    options = {'model':STAGES[stage][3], 'temperature':0.1, 'max_tokens':STAGES[stage][4],
        'enable_thinking':stage!='perception', 'response_format':{'type':'json_object'},
        'messages':[{'role':'system','content':SYSTEM_PROMPT},
                    {'role':'user','content':content if assets else body['prompt']}]}
    if stage!='perception':options['thinking_budget']=1024
    if stage in {'semantic','grounding'} or (stage == 'audit' and 'outputSchema' in body):options['response_format']={'type':'json_schema','json_schema':{
        'name':'video_audit' if stage == 'audit' else 'video_semantic','strict':True,'schema':body['outputSchema']}}
    return options


def make_expected_attestation(body):
    options = expected_provider_options(body)
    effective = {k:copy.deepcopy(v) for k,v in options.items() if k!='messages'}
    effective['image_count']=0 if body['videoStage']=='timeline' else len(body['imageAssets'])
    if body['videoStage']=='timeline':effective['video_count']=1
    effective['fine_frame_ids']=[a['name'] for a in body['imageAssets'] if body['videoStage']=='perception' or a['name'] in body['payload'].get('perception_frame_ids',[])]
    # Schema remains private request data; export only its digest.
    effective['response_format']=options['response_format']['type']
    return {'version':POLICY_VERSION,'stage':body['videoStage'],'skill':body['skillName'],
        'skill_version':body['skillVersion'],'correlation':body['videoCorrelation'],
        'model':options['model'],'effective_options':effective,
        'provider_options_sha256':canonical_sha(options),
        'output_schema_sha256':canonical_sha(body['outputSchema']) if 'outputSchema' in body else None}


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
                             ('thinking_budget', {1024}), ('video_count', {1}), ('image_count', {0, *range(2, video_stage_image_limit(value.get('stage')) + 1)})):
            item = effective.get(key)
            if type(item) is int and item in allowed:
                safe[key] = item
        item = effective.get('enable_thinking')
        if type(item) is bool:
            safe['enable_thinking'] = item
        refs = effective.get('fine_frame_ids')
        if (isinstance(refs, list) and len(refs) <= 12 and
                all(type(ref) is str and re.fullmatch(r'frame-(?:[0-9]{3}|end)', ref) for ref in refs) and
                len(set(refs)) == len(refs)):
            safe['fine_frame_ids'] = list(refs)
        out['effective_options'] = safe
    return out


def validate_attestation(value, body):
    # Python dict equality conflates booleans/integers and integers/equal floats.
    # Canonical JSON preserves their wire types, as in the existing options proof.
    try:
        matches = canonical_sha(value) == canonical_sha(make_expected_attestation(body))
    except (TypeError, ValueError, OverflowError, RecursionError):
        raise RuntimeError('video_stage_attestation_invalid') from None
    if not matches:
        raise RuntimeError('video_stage_attestation_invalid')
