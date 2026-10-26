"""Recover only clearly typed business inputs from their final original pixels.

Observation remains redacted. This bounded final pass never turns a credential
or an unknown field into replay data and never changes the observed actions.
"""
import copy
import re

_MANUAL='请手工填写非敏感测试输入；密码、验证码或令牌请使用人工前置条件'
_PROTECTED=re.compile(r'密码|口令|验证码|令牌|密钥|账号|帐号|账户|帐户|登录|登陆|认证|联系|收件|手机号码?|电话|身份证|银行卡|邮箱|地址|收货|password|passwd|passcode|otp|token|secret|api.?key|credential|login|username|account|bearer|\bpin\b',re.I)
_BUSINESS=re.compile(r'搜索|检索|查询|查找|文本|文字|标题|备注|说明|昵称|命名|名称|数量|份数|页码|页数')


def _business_field(event):
    return bool(_BUSINESS.search(event['target']) and not _PROTECTED.search(event['target']+' '+event['observation']))


def _safe_value(value):
    return (isinstance(value,str) and 0<len(value.strip())<=128 and
            not _PROTECTED.search(value) and not re.search(r'\d{6,}',re.sub(r'[\s()+.\-]','',value)) and not re.search(r'\d{6,}|@|https?://|\bsk-|[A-Za-z0-9_+/=-]{24,}',value,re.I) and
            not any(ord(c)<32 for c in value))


def recover_business_inputs(result,document,root,cancelled):
    from . import ai_skill_service as ai
    from .ui_video_windows import load_assets
    if cancelled():raise ValueError('video_cancelled')
    out=copy.deepcopy(result);audit=[];checks=[];allowed={};image_ids=set()
    frames=document['media']['frames'];times={f['frame_id']:f['timestamp_ms'] for f in frames}
    events=out['events']
    for index,event in enumerate(events):
        if event['action']!='aiInput':continue
        event['value']=''
        audit.append({'candidate_index':index,'status':'manual','frame_refs':[]})
        if not _business_field(event) or len(checks)>=8:continue
        boundary=next((e['start_ms'] for e in events[index+1:] if e['action'] in {'aiTap','aiScroll','aiInput'}),document['media']['duration_ms']+1)
        # Include the final input anchor plus subsequent pre-interaction pixels.
        # Never inspect another field's later input to fill this value.
        following=[f for f in frames if event['end_ms']<=f['timestamp_ms']<boundary]
        if not following:continue
        refs=list(dict.fromkeys(event['frame_refs'][-1:]+[f['frame_id'] for f in following[-3:]]))
        key=f'candidate:{index}';allowed[key]=set(refs);image_ids.update(refs)
        checks.append({'target':key,'field':event['target'],'final_input_frame_ids':refs})
    if not checks:return {**out,'input_recovery':audit}
    images=[f for f in frames if f['frame_id'] in image_ids]
    # Transport requires two labelled images; add an actual immediate anchor,
    # never a synthetic image or an unrelated later page.
    if len(images)==1:
        extra=next((f for f in reversed(frames) if f['timestamp_ms']<images[0]['timestamp_ms']),None)
        if extra:images.insert(0,extra)
    if not 2<=len(images)<=60:raise ValueError('video_vision_limit')
    raw=ai.run_ai_skill('ui_video_observe',{'phase':'whole-observe','frames':images,'image_frames':images,
        'duration_ms':document['media']['duration_ms'],'checks':checks},image_assets=load_assets(images,root,cancelled),
        version='inputs',timeout=120,retry_count=0,max_tokens=3000,repair_invalid_json=False)
    if cancelled():raise ValueError('video_cancelled')
    if (not isinstance(raw,dict) or set(raw)!={'events','uncertainties','multiple_scenarios'} or
        not isinstance(raw['events'],list) or not isinstance(raw['uncertainties'],list) or
        type(raw['multiple_scenarios']) is not bool):raise ValueError('video_model_invalid')
    seen=set();unsafe=[]
    for event in raw['events']:
        if not isinstance(event,dict) or set(event)-{'action','target','value','frame_refs','observation','uncertainties'}:
            raise ValueError('video_model_invalid')
        key=event.get('target');refs=event.get('frame_refs')
        if (event.get('action')!='aiInput' or key not in allowed or key in seen or
            not isinstance(refs,list) or not 1<=len(refs)<=4 or len(set(refs))!=len(refs) or
            any(not isinstance(r,str) or r not in allowed[key] for r in refs) or
            [times[r] for r in refs]!=sorted(times[r] for r in refs)):
            raise ValueError('video_reference_invalid')
        seen.add(key);index=int(key.split(':')[1]);value=event.get('value')
        accepted=(not raw['multiple_scenarios'] and not raw['uncertainties'] and
                  event.get('uncertainties',[])==[] and event.get('observation')=='普通业务输入' and _safe_value(value))
        if not accepted:
            if isinstance(value,str) and value:unsafe.append(value)
            continue
        events[index]['value']=value.strip()
        events[index]['uncertainties']=[q for q in events[index]['uncertainties'] if q!=_MANUAL]
        next(a for a in audit if a['candidate_index']==index).update(status='recovered',frame_refs=list(refs))
    # Reject partial model contracts; manual is explicit, not a missing entry.
    if seen!=set(allowed):raise ValueError('video_model_invalid')
    def redact(value):
        if isinstance(value,str):
            for secret in unsafe:value=value.replace(secret,'[输入内容未保存]')
        elif isinstance(value,list):value=[redact(v) for v in value]
        elif isinstance(value,dict):value={k:redact(v) for k,v in value.items()}
        return value
    return redact({**out,'input_recovery':audit})
