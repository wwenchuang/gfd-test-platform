"""Validate explicitly declared recovery omissions without inventing Runner retries."""
import copy
import re


def invalid_retry(feedback):
    error=ValueError('video_retry_invalid')
    error.model_feedback=feedback  # platform-authored text only, never model output
    return error


def review_retries(plan,candidates,required):
    if not isinstance(plan,dict):return plan,required,[]
    cleaned=copy.deepcopy(plan);omitted=cleaned.pop('omitted_retries',[])
    if not isinstance(omitted,list) or len(omitted)>20:raise ValueError('video_retry_invalid')
    if not omitted:return cleaned,required,[]
    kept={e.get('candidate_index') for e in cleaned.get('events',[]) if isinstance(e,dict)}
    dropped=set();pairs=[]
    for item in omitted:
        if not isinstance(item,dict) or set(item)!={'candidate_index','repeated_by','reason'}:raise ValueError('video_retry_invalid')
        index,later=item['candidate_index'],item['repeated_by'];reason=item['reason']
        if (type(index) is not int or type(later) is not int or not (0<=index<len(candidates) and 0<=later<len(candidates) and index!=later) or
            index in dropped or index in kept or later not in kept or index not in required or
            not isinstance(reason,str) or not 1<=len(reason.strip())<=500):raise ValueError('video_retry_invalid')
        before,after=candidates[index],candidates[later]
        if before['action']!='aiTap' or after['action']!='aiTap' or before['target'].strip()!=after['target'].strip():
            raise invalid_retry(f'重试声明{index}与代表候选{later}不是同一点击控件；不能合并不同业务。')
        dropped.add(index);pairs.append((index,later,reason))
    targets={candidates[index]['target'].strip() for index in dropped}
    audit=[]
    for index,later,reason in pairs:
        span=candidates[min(index,later):max(index,later)]
        if len(targets)>2 or any(e['action'] in {'aiInput','aiScroll'} or
               (e['action']=='aiTap' and e['target'].strip() not in targets) for e in span):
            raise invalid_retry(f'重试候选{index}到{later}跨越了其他交互；请保留后续业务，或使用完整打印取消恢复区间。')
        failures=[e for e in span if re.search(r'失败|未完成|超时|未成功|稍后重试',re.sub(r'(?:没有|未发生|无|并非)失败','',e['observation']))]
        if not failures:raise invalid_retry(f'候选{index}到{later}之间没有明确失败证据，不能声明为失败重试。')
        audit.append({'candidate_index':index,'repeated_by':later,'reason':'录屏中的失败重试已合并，保留同控件操作及业务结果检查',
                      'frame_refs':list(candidates[index]['frame_refs']),
                      'failure_frame_refs':list(dict.fromkeys(ref for e in failures for ref in e['frame_refs']))})
    return cleaned,[i for i in required if i not in dropped],audit
