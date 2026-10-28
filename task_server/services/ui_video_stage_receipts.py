"""Versioned stage proof verification. Hashes are canonical options, not wire bytes."""
import copy
import hashlib
import json
import math
from .ui_video_stage_policy import POLICY_VERSION, STAGES

SYSTEM_PROMPT = '你是移动端测试平台 AI Skill 执行器。必须严格遵守用户输入的 Skill Prompt，只输出合法 JSON。'


def canonical_sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def expected_provider_options(body):
    """Independent reconstruction of the closed seven-stage Gateway wire policy."""
    stage = body['videoStage'];payload = body['payload'];assets = body['imageAssets']
    content = [{'type':'text','text':body['prompt']}]
    for asset in assets:
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
    if stage=='semantic':options['response_format']={'type':'json_schema','json_schema':{
        'name':'video_semantic','strict':True,'schema':body['outputSchema']}}
    return options


def make_expected_attestation(body):
    options = expected_provider_options(body)
    effective = {k:copy.deepcopy(v) for k,v in options.items() if k!='messages'}
    effective['image_count']=len(body['imageAssets'])
    effective['fine_frame_ids']=[a['name'] for a in body['imageAssets'] if body['videoStage']=='perception' or a['name'] in body['payload'].get('perception_frame_ids',[])]
    # Schema remains private request data; export only its digest.
    effective['response_format']=options['response_format']['type']
    return {'version':POLICY_VERSION,'stage':body['videoStage'],'skill':body['skillName'],
        'skill_version':body['skillVersion'],'correlation':body['videoCorrelation'],
        'model':options['model'],'effective_options':effective,
        'provider_options_sha256':canonical_sha(options),
        'output_schema_sha256':canonical_sha(body['outputSchema']) if 'outputSchema' in body else None}


def safe_attestation(value):
    """Bounded positive projection even on malformed/untrusted Gateway replies."""
    if not isinstance(value,dict):return None
    out={k:v for k in ('version','stage','skill','skill_version','correlation','model','provider_options_sha256','output_schema_sha256')
         if (v:=value.get(k)) is None or (type(v) is str and len(v)<=128)}
    effective=value.get('effective_options')
    if isinstance(effective,dict):
        allowed={'model','temperature','max_tokens','enable_thinking','thinking_budget','response_format','image_count'}
        out['effective_options']={k:v for k,v in effective.items() if k in allowed and
            (type(v) is bool or (type(v) in (int,float) and -2**53 <= v <= 2**53 and math.isfinite(v)) or (type(v) is str and len(v)<=128))}
        refs=effective.get('fine_frame_ids')
        if isinstance(refs,list) and len(refs)<=60 and all(type(r) is str and len(r)<=32 for r in refs):
            out['effective_options']['fine_frame_ids']=list(refs)
    return out


def validate_attestation(value, body):
    if value != make_expected_attestation(body):raise RuntimeError('video_stage_attestation_invalid')
