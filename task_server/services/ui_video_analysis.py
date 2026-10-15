"""Bound visual proposals to actual chronological assets, never model authority."""
import base64
import copy
import hashlib
import json
import math
import re
import uuid

from .ui_case_draft_contract import ACTIONS, normalize_steps


def _text(value, limit=2000):
    if not isinstance(value, str) or len(value)>limit: raise ValueError('video_model_invalid')
    return value.strip()


def _scroll_object(value):
    """Normalize only a bounded JSON-object encoding, preserving strict semantics."""
    if isinstance(value,str):
        if not value or len(value)>4000:raise ValueError('video_action_invalid')
        def unique_object(pairs):
            result={}
            for key,item in pairs:
                if key in result:raise ValueError('video_action_invalid')
                result[key]=item
            return result
        def reject_constant(_):raise ValueError('video_action_invalid')
        try:
            value=json.loads(value,object_pairs_hook=unique_object,parse_constant=reject_constant)
        except (ValueError,RecursionError):
            raise ValueError('video_action_invalid') from None
    if not isinstance(value,dict):raise ValueError('video_action_invalid')
    return value


def validate_analysis(result, frames, duration, prior_secrets=()):
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
            from .yaml_static_validator import validate_midscene_action_parameters
            value=_scroll_object(event['value'])
            event['value']=value
            if not isinstance(value,dict) or set(value)-{'direction','distance','scrollType'}:raise ValueError('video_action_invalid')
            distance=value.get('distance')
            if isinstance(distance,(int,float)) and not math.isfinite(distance):raise ValueError('video_action_invalid')
            if validate_midscene_action_parameters('aiScroll',{'aiScroll':event['target'],**value}):raise ValueError('video_action_invalid')
        elif event['value']!='': raise ValueError('video_action_invalid')
        last=start
        clean.append(event)
    secrets=list(prior_secrets)+[e['value'] for e in events if isinstance(e,dict) and e.get('action')=='aiInput' and isinstance(e.get('value'),str) and e['value']]
    def redact(value):
        if isinstance(value,str):
            for secret in secrets:value=value.replace(secret,'[输入内容未保存]')
            return value
        if isinstance(value,list):return [redact(v) for v in value]
        if isinstance(value,dict):return {k:redact(v) for k,v in value.items()}
        return value
    return redact({'events':clean,'uncertainties':questions,'multiple_scenarios':result['multiple_scenarios']})


def _consolidation_images(events,frames,limit):
    """Prioritize ambiguous/destructive evidence; otherwise cover event chronology."""
    counts={}
    for event in events:
        key=(event['action'],event['target'])
        counts[key]=counts.get(key,0)+1
    priority=set();secondary=set()
    for index,event in enumerate(events):
        destructive=re.search(r'删除|移除|清空|支付|付款|delete|remove|pay',event['target'],re.I)
        ambiguous=counts[(event['action'],event['target'])]>1 or re.search(r'打印|下单',event['target'])
        if destructive or ambiguous:
            refs=priority if destructive else secondary
            refs.update(event['frame_refs'])
            if index+1<len(events):refs.update(events[index+1]['frame_refs'])
    all_refs={ref for event in events for ref in event['frame_refs']}
    def spread(items,count):
        if len(items)<=count:return items
        return [items[round(i*(len(items)-1)/(count-1))] for i in range(count)] if count>1 else [items[len(items)//2]]
    chosen=spread([f for f in frames if f['frame_id'] in priority],limit)
    selected={f['frame_id'] for f in chosen}
    if len(chosen)<limit:
        chosen+=spread([f for f in frames if f['frame_id'] in secondary-selected],limit-len(chosen))
        selected={f['frame_id'] for f in chosen}
    remaining=[f for f in frames if f['frame_id'] in all_refs-selected]
    chosen+=spread(remaining,limit-len(chosen)) if len(chosen)<limit else []
    return sorted(chosen,key=lambda frame:frame['timestamp_ms'])


def analyze_frames(document, root, cancelled):
    from . import ai_skill_service as ai
    frames=document['media']['frames']
    # The existing Gateway imagePartsFromBody accepts at most six images.
    limit=max(2,min(6,int(ai.AI_VISION_IMAGE_LIMIT)))
    if int(ai.AI_VISION_IMAGE_LIMIT)<2: raise ValueError('video_vision_limit')
    events=[];questions=[];multi=False;last_end=-1;audit_events=[];known_secrets=[]
    # Adjacent batches share one boundary frame, preserving the whole chronology.
    for offset in range(0,len(frames)-1,limit-1):
        if cancelled(): raise ValueError('video_cancelled')
        batch=frames[offset:offset+limit]
        assets=[{'name':f['frame_id'], 'mime':'image/jpeg','base64':base64.b64encode((root/'frames'/(f['frame_id']+'.jpg')).read_bytes()).decode()} for f in batch]
        if cancelled():raise ValueError('video_cancelled')
        result=ai.run_ai_skill('ui_video_steps', {'app_id':document['app_id'],'goal':document['goal'],
                              'duration_ms':document['media']['duration_ms'],'phase':'batch','frames':batch,'image_frames':batch,
                              'previous_events':[{k:e[k] for k in ('start_ms','end_ms','action','target')} for e in events[-3:]],
                              'baseline_examples':document.get('baseline_examples',[])}, image_assets=assets,
                              timeout=120,retry_count=0,max_tokens=10000,repair_invalid_json=False)
        if cancelled(): raise ValueError('video_cancelled')
        checked=validate_analysis(result,batch,document['media']['duration_ms'],known_secrets)
        known_secrets.extend(e['value'] for e in result['events'] if e['action']=='aiInput' and isinstance(e['value'],str) and e['value'])
        audit_events.extend(copy.deepcopy(checked['events']))
        for event in checked['events']:
            # Overlap may report a prior event; only exact same temporal identity
            # is deduplicated. Repeated same controls at different times survive.
            identity=(event['start_ms'],event['end_ms'],event['action'],event['target'])
            if any((e['start_ms'],e['end_ms'],e['action'],e['target'])==identity for e in events): continue
            if event['start_ms']<last_end: raise ValueError('video_model_timeline')
            events.append(event);last_end=event['start_ms']
        questions.extend(checked['uncertainties']);multi|=checked['multiple_scenarios']
        if len(events)>50: raise ValueError('video_split_required')
    raw_events=copy.deepcopy(audit_events)
    candidates=copy.deepcopy(events)
    raw_questions=list(dict.fromkeys(questions))
    if events:
        if cancelled():raise ValueError('video_cancelled')
        image_frames=_consolidation_images(events,frames,limit)
        assets=[{'name':f['frame_id'],'mime':'image/jpeg','base64':base64.b64encode((root/'frames'/(f['frame_id']+'.jpg')).read_bytes()).decode()} for f in image_frames]
        if cancelled():raise ValueError('video_cancelled')
        result=ai.run_ai_skill('ui_video_steps',{'phase':'consolidate','app_id':document['app_id'],
                    'goal':document['goal'],'duration_ms':document['media']['duration_ms'],
                    'frames':frames,'image_frames':image_frames,'candidate_events':candidates,
                    'raw_uncertainties':raw_questions,'multiple_scenarios':multi},image_assets=assets,
                    timeout=120,retry_count=0,max_tokens=10000,repair_invalid_json=False)
        if cancelled():raise ValueError('video_cancelled')
        checked=validate_analysis(result,frames,document['media']['duration_ms'],known_secrets)
        for event in checked['events']:
            matching=[raw for raw in raw_events if raw['action']==event['action'] and
                      raw['start_ms']<=event['end_ms'] and event['start_ms']<=raw['end_ms']]
            covered=event['start_ms']
            for raw in sorted(matching,key=lambda item:item['start_ms']):
                if raw['start_ms']>covered:break
                covered=max(covered,raw['end_ms'])
                if covered>=event['end_ms']:break
            supported_refs={ref for raw in matching for ref in raw['frame_refs']}
            supported_refs.update(frame['frame_id'] for frame in image_frames)
            if (not matching or min(raw['start_ms'] for raw in matching)>event['start_ms'] or
                covered<event['end_ms'] or not set(event['frame_refs'])<=supported_refs):
                raise ValueError('video_model_timeline')
            if all(raw['evidence_kind']=='inferred' for raw in matching):event['evidence_kind']='inferred'
        events=checked['events'];questions=checked['uncertainties']
        multi=multi or checked['multiple_scenarios']
    return {'events':events,'uncertainties':list(dict.fromkeys(questions)), 'multiple_scenarios':multi,
            'raw_events':raw_events,'raw_uncertainties':raw_questions}


def _expected_candidate(event, goal):
    """A sourced visible check suggestion, never an established business outcome."""
    observation=event['observation'].strip()
    concrete=(len(observation)>=6 and re.search(r'显示|出现|可见|打开|包含|弹出',observation)
              and not re.search(r'不明确|不确定|无法|可能|似乎|未知|跳帧|页面变化|界面变化',observation))
    candidate=observation if concrete else ''
    # Never extract a goal clause: doing so loses negation and scope. Only a
    # literal identical goal can share this observed candidate's attribution.
    basis=('user_goal' if candidate and candidate==goal.strip()
           else 'visible_candidate' if candidate else 'unresolved')
    return {'text':candidate,'basis':basis,'frame_refs':list(event['frame_refs']),'requires_confirmation':True}


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
        event['expected_candidate']=_expected_candidate(event,document['goal'])
        expected=event['expected_candidate']['text'] if event['action'] in {'aiTap','aiInput','aiScroll'} else ''
        target=event['target']
        steps.append({'step_id':step_id,'action':event['action'],'target':target,'value':event['value'],
                      'expected':expected,'source_ref':ref,'review_state':'needs_review'})
        questions.extend(f'片段 {event["start_ms"]/1000:g}–{event["end_ms"]/1000:g} 秒：{q}' for q in event['uncertainties'])
        if re.search('删除|支付|付款|打印|下单',event['target']): questions.append('含删除/支付/打印等有副作用动作，请核对测试数据与授权后再手工试跑')
    if steps and steps[-1]['action']!='aiAssert' and len(steps)<50:
        event=snapshot['events'][-1]
        if event['expected_candidate']['text']:
            check_id=str(uuid.uuid4());event['expected_check_step_id']=check_id
            steps.append({'step_id':check_id,'action':'aiAssert','target':event['expected_candidate']['text'],
                          'value':'','expected':'','source_ref':steps[-1]['source_ref'],'review_state':'needs_review'})
    if not steps or steps[-1]['action']!='aiAssert':
        questions.append('视频未给出明确结束检查，请补充业务预期；观察到的变化不能代替业务应有结果。')
    goal=document['goal'].strip()
    final_check=steps[-1]['target'] if steps and steps[-1]['action']=='aiAssert' else ''
    candidates=list(dict.fromkeys(event['expected_candidate']['text'] for event in snapshot['events'] if event['expected_candidate']['text']))
    assertion_mismatch=any(event['action']=='aiAssert' and event['expected_candidate']['text'] and event['target']!=event['expected_candidate']['text'] for event in snapshot['events'])
    needs_confirmation=bool(goal and goal!=final_check) or assertion_mismatch
    snapshot['goal_review']={'status':'needs_confirmation' if needs_confirmation else 'literal_match' if goal else 'not_provided',
                             'visible_candidates':candidates,'requires_confirmation':needs_confirmation}
    if needs_confirmation:
        # Central human confirmation preserves the entire original goal in the
        # immutable snapshot; no keyword-based success/negation interpretation.
        visible='；'.join(candidates)[:500] or '没有明确可见结果'
        questions.append(f'请明确确认目标与候选检查是否一致。用户目标原文：{goal[:600] or "未填写"}。录屏可见检查候选：{visible}。末尾检查候选：{final_check[:400] or "未明确"}。'
                         '平台未解释目标中的否定、范围或业务含义；如有冲突请修改预期/结束检查，再逐步确认。完整目标保留在来源详情。')
    normalized=normalize_steps({'title':'录屏步骤待核对','preconditions':[],'steps':steps,'questions':questions},document['app_id'])
    return normalized,snapshot
