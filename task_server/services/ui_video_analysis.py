"""Bound visual proposals to actual chronological assets, never model authority."""
import base64
import copy
import hashlib
import json
import re
import uuid

from .ui_case_draft_contract import ACTIONS, normalize_steps


def _text(value, limit=2000):
    if not isinstance(value, str) or len(value)>limit: raise ValueError('video_model_invalid')
    return value.strip()


def validate_analysis(result, frames, duration):
    if not isinstance(result, dict) or set(result)!={'events','uncertainties','multiple_scenarios'}: raise ValueError('video_model_invalid')
    if type(result['multiple_scenarios']) is not bool: raise ValueError('video_model_invalid')
    events=result['events']; questions=result['uncertainties']
    if not isinstance(events,list) or len(events)>50 or not isinstance(questions,list) or len(questions)>30: raise ValueError('video_model_invalid')
    questions=[_text(q) for q in questions]
    by_id={f['frame_id']:f['timestamp_ms'] for f in frames}
    last=-1
    clean=[]
    for raw in events:
        if not isinstance(raw,dict) or set(raw)!={'action','target','value','start_ms','end_ms','frame_refs','observation','evidence_kind','uncertainties'}: raise ValueError('video_model_invalid')
        event=copy.deepcopy(raw)
        if event['action'] not in ACTIONS-{'launch'} or event['evidence_kind'] not in {'observed','inferred'}: raise ValueError('video_action_invalid')
        start,end=event['start_ms'],event['end_ms'];refs=event['frame_refs']
        if type(start) is not int or type(end) is not int or not last<=start<=end<=duration: raise ValueError('video_reference_invalid')
        if not isinstance(refs,list) or not 1<=len(refs)<=8 or any(not isinstance(r,str) or r not in by_id or not start<=by_id[r]<=end for r in refs): raise ValueError('video_reference_invalid')
        if [by_id[r] for r in refs]!=sorted(by_id[r] for r in refs): raise ValueError('video_reference_invalid')
        event['target']=_text(event['target']);event['observation']=_text(event['observation'])
        if not event['observation']: raise ValueError('video_observation_missing')
        if not isinstance(event['uncertainties'],list) or len(event['uncertainties'])>10: raise ValueError('video_model_invalid')
        event['uncertainties']=[_text(q) for q in event['uncertainties']]
        if event['action']=='aiInput':
            _text(event['value'],4000)
            # Video transcription cannot establish whether text is a credential.
            # Require explicit user configuration for all input values, avoiding
            # unknown tokens, passwords and OTPs ever entering saved proposals.
            event['value']=''
            event['uncertainties'].append('请手工填写非敏感测试输入；密码、验证码或令牌请使用人工前置条件')
        elif event['action']=='aiScroll':
            if not isinstance(event['value'],dict): raise ValueError('video_action_invalid')
        elif event['value']!='': raise ValueError('video_action_invalid')
        last=start
        clean.append(event)
    secrets=[e['value'] for e in events if isinstance(e,dict) and e.get('action')=='aiInput' and isinstance(e.get('value'),str) and e['value']]
    def redact(value):
        if isinstance(value,str):
            for secret in secrets:value=value.replace(secret,'[输入内容未保存]')
            return value
        if isinstance(value,list):return [redact(v) for v in value]
        if isinstance(value,dict):return {k:redact(v) for k,v in value.items()}
        return value
    return redact({'events':clean,'uncertainties':questions,'multiple_scenarios':result['multiple_scenarios']})


def analyze_frames(document, root, cancelled):
    from . import ai_skill_service as ai
    frames=document['media']['frames']
    limit=max(2,min(8,int(ai.AI_VISION_IMAGE_LIMIT)))
    if int(ai.AI_VISION_IMAGE_LIMIT)<2: raise ValueError('video_vision_limit')
    events=[];questions=[];multi=False;last_end=-1
    # Adjacent batches share one boundary frame, preserving the whole chronology.
    for offset in range(0,len(frames)-1,limit-1):
        if cancelled(): raise ValueError('video_cancelled')
        batch=frames[offset:offset+limit]
        assets=[{'name':f['frame_id'], 'mime':'image/jpeg','base64':base64.b64encode((root/'frames'/(f['frame_id']+'.jpg')).read_bytes()).decode()} for f in batch]
        result=ai.run_ai_skill('ui_video_steps', {'app_id':document['app_id'],'goal':document['goal'],
                              'duration_ms':document['media']['duration_ms'],'frames':batch,
                              'previous_events':[{k:e[k] for k in ('start_ms','end_ms','action','target')} for e in events[-3:]],
                              'baseline_examples':document.get('baseline_examples',[])}, image_assets=assets,
                              timeout=120,retry_count=0,max_tokens=10000,repair_invalid_json=False)
        if cancelled(): raise ValueError('video_cancelled')
        checked=validate_analysis(result,batch,document['media']['duration_ms'])
        for event in checked['events']:
            # Overlap may report a prior event; only exact same temporal identity
            # is deduplicated. Repeated same controls at different times survive.
            identity=(event['start_ms'],event['end_ms'],event['action'],event['target'])
            if any((e['start_ms'],e['end_ms'],e['action'],e['target'])==identity for e in events): continue
            if event['start_ms']<last_end: raise ValueError('video_model_timeline')
            events.append(event);last_end=event['start_ms']
        questions.extend(checked['uncertainties']);multi|=checked['multiple_scenarios']
        if len(events)>50: raise ValueError('video_split_required')
    return {'events':events,'uncertainties':list(dict.fromkeys(questions)), 'multiple_scenarios':multi}


def draft_proposal(document):
    identifier=document['source_id'];version=document['analysis_version']
    snapshot={'kind':'video','source_id':identifier,'app_id':document['app_id'],'analysis_version':version,
              'duration_ms':document['media']['duration_ms'],'frames':document['media']['frames'],
              'events':copy.deepcopy(document['events']),'goal':document['goal']}
    snapshot['snapshot_id']=hashlib.sha256(json.dumps([identifier,version]).encode()).hexdigest()
    steps=[];questions=list(document['uncertainties'])
    for index,event in enumerate(snapshot['events']):
        step_id=str(uuid.uuid4());event['draft_step_id']=step_id
        ref=f"video:{identifier}:{version}:{index}"
        steps.append({'step_id':step_id,'action':event['action'],'target':event['target'],'value':event['value'],
                      'expected':'','source_ref':ref,'review_state':'needs_review'})
        questions.extend(f'片段 {event["start_ms"]/1000:g}–{event["end_ms"]/1000:g} 秒：{q}' for q in event['uncertainties'])
        if re.search('删除|支付|付款|打印|下单',event['target']): questions.append('含删除/支付/打印等有副作用动作，请核对测试数据与授权后再手工试跑')
    questions.append('请区分看到的结果与业务预期，核对可见检查候选；缺少结束检查时补充明确检查点。'+('用户测试目标：'+document['goal'] if document['goal'] else ''))
    normalized=normalize_steps({'title':'录屏步骤待核对','preconditions':[],'steps':steps,'questions':questions},document['app_id'])
    return normalized,snapshot
