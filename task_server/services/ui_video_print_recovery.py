"""Evidence-indexed print recovery proposal; execution still needs review and gates."""
import copy
import re
from .ui_print_recovery import DEFAULTS,SUBMIT_EXPECTED
from .ui_video_recovery import invalid_retry


def eligible_terminal(terminal):
    # An explicit absence of user input is not a negated business result.
    # Remove only complete, narrowly recognized clauses; keep task negation and
    # uncertainty intact, and preserve the original candidate for audit.
    observation='，'.join(clause for clause in re.split(r'[，。；,;\n]',terminal['observation'])
        if not re.fullmatch(r'(?:期间)?未(?:见|观察到)用户(?:手动)?(?:点击(?:(?:导航)?入口|操作)?|操作)',clause.strip()))
    # Unselected feedback options describe form state, not an unfinished task.
    observation=re.sub(r'(?<=为)未选中状态', '待选择状态', observation)
    return not (terminal['action'] not in ('aiAssert','aiWaitFor') or
        re.search(r'未|没有|无法|不能|可能|不确定',observation) or
        not re.search(r'取消原因|反馈.*(?:页|界面)|任务.*(?:移除|消失)',observation))


def prepare_group(plan,candidates,required):
    """Expand an explicit recovery region into independently checked omissions.

    The model marks boundaries; it cannot omit arbitrary clicks in the region.
    Raw observations and failures remain in the immutable source snapshot.
    """
    if not isinstance(plan,dict):return plan
    result=copy.deepcopy(plan);group=result.get('print_recovery')
    if not isinstance(group,dict) or 'end' not in group:return result
    if set(group)!={'submit','cancel','confirm','end'}:raise ValueError('video_retry_invalid')
    start,confirm,end=[group[k] for k in ('cancel','confirm','end')]
    if any(type(i) is not int for i in (start,confirm,end)) or not 0<=start<confirm<end<len(candidates):
        raise invalid_retry('print_recovery索引必须使用原始candidate_events的整数candidate_index，不是精简后events序号、时间或字符串；满足0 <= cancel < confirm < end < 候选总数。请从原候选重新核对首次取消、紧接确认和终态观察，禁止调整业务顺序凑索引。')
    if not any(e.get('action')=='aiTap' and '确认打印' in e.get('target','') and '取消' not in e.get('target','') for e in candidates[:start]):
        raise invalid_retry('没有最终确认打印的候选证据，不能使用打印下发后的恢复规则；移除print_recovery，保留模型处理取消、耗材确认、已有任务处理及后续新任务的每个独立操作。不同任务的两次取消不是失败重试，不生成omitted_retries。')
    if not eligible_terminal(candidates[end]):
        eligible=[i for i in range(confirm+1,len(candidates)) if eligible_terminal(candidates[i])]
        raise invalid_retry('打印恢复end需要明确任务消失或取消原因页的稳定观察，不能是处理中、失败或仅设备空闲。请改用通过终态校验的原始候选索引：'+str(eligible)+'；仍须满足区间内无其他业务交互。')
    representatives={candidates[i]['target'].strip():i for i in (start,confirm)}
    if len(representatives)!=2:raise ValueError('video_retry_invalid')
    omissions=[]
    for i in range(confirm+1,end):
        event=candidates[i]
        if event['action'] in ('aiAssert','aiWaitFor'):continue
        if event['action']!='aiTap' or event['target'].strip() not in representatives or i not in required:
            raise invalid_retry(f'恢复区间候选{i}不是取消/确认的重复点击，必须重新核对end边界；不能吞掉原因选择、反馈和返回。')
        omissions.append({'candidate_index':i,'repeated_by':representatives[event['target'].strip()],'reason':'同一取消恢复区间内重复控件；平台仍校验失败证据'})
    old=result.get('omitted_retries',[])
    if not isinstance(old,list) or any(not isinstance(x,dict) for x in old):raise ValueError('video_retry_invalid')
    # Reject conflicting declarations instead of silently discarding model intent.
    if any(start<x.get('candidate_index',-1)<end and x.get('candidate_index') not in {o['candidate_index'] for o in omissions} for x in old):raise ValueError('video_retry_invalid')
    result['omitted_retries']=[x for x in old if not start<x.get('candidate_index',-1)<end]+omissions
    result['events']=[e for e in result.get('events',[]) if not confirm<e.get('candidate_index',-1)<end]
    group.pop('end')
    return result


def bind_group(plan,candidates):
    group=plan.pop('print_recovery',None)
    if group is None:return None
    if not isinstance(group,dict) or set(group)!={'submit','cancel','confirm'}:raise ValueError('video_retry_invalid')
    indexes=[group[k] for k in ('submit','cancel','confirm')]
    if any(type(i) is not int or not 0<=i<len(candidates) for i in indexes) or not indexes[0]<indexes[1]<indexes[2]:raise ValueError('video_retry_invalid')
    events=plan.get('events',[]);positions={e.get('candidate_index'):i for i,e in enumerate(events)}
    if any(i not in positions for i in indexes):raise ValueError('video_retry_invalid')
    submit,cancel,confirm=[candidates[i] for i in indexes]
    if any(e['action']!='aiTap' for e in (submit,cancel,confirm)):raise ValueError('video_retry_invalid')
    if (not re.search('确认打印',submit['target']) or '取消' in submit['target'] or
        any(e['action']=='aiTap' and '确认打印' in e['target'] and '取消' not in e['target']
            for e in candidates[indexes[0]+1:indexes[1]])):
        raise invalid_retry('print_recovery.submit必须指向最终确认打印的原始候选，不是打开确认弹窗的确认按钮；请核对检查无误后真正下发本次任务的点击，保留此前打开弹窗的步骤。')
    if not re.search('取消.*打印',cancel['target']) or not re.search('确认|确定',confirm['target']):raise ValueError('video_retry_invalid')
    if any(e['action'] in ('aiTap','aiInput','aiScroll') for e in candidates[indexes[1]+1:indexes[2]]):raise ValueError('video_retry_invalid')
    return {key:positions[group[key]] for key in group}


def apply_group(steps,group):
    if not group:return steps
    result=copy.deepcopy(steps)
    submit,cancel,confirm=[group[k] for k in ('submit','cancel','confirm')]
    if not 0<=submit<cancel<confirm<len(result) or any(result[i]['action']!='aiTap' for i in (submit,cancel,confirm)):raise ValueError('video_retry_invalid')
    result[submit].update(action='submitPrint',value=dict(DEFAULTS),expected=SUBMIT_EXPECTED)
    result[cancel].update(action='cancelPrint',target='取消本次打印并等待结束',value=dict(DEFAULTS),expected='本次打印任务已结束：显示取消原因页，或设备已空闲且活动任务区域内本次任务的进度、暂停及取消按钮均已消失；最近打印中的历史记录允许保留，不属于活动任务')
    result.pop(confirm)
    return result
