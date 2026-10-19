"""Platform print recovery envelope: never a new Midscene YAML action."""
import json

PREFIX='平台打印取消恢复v1：'
DEFAULTS={'timeout_ms':180000,'max_attempts':8,'retry_delay_ms':10000}
SUBMIT_EXPECTED='本次新建打印任务已绑定'


def policy(value):
    if value=='':value={}
    if not isinstance(value,dict) or set(value)-set(DEFAULTS):raise ValueError('打印恢复参数无效')
    result={**DEFAULTS,**value}
    for key,low,high in [('timeout_ms',1000,180000),('max_attempts',1,8),('retry_delay_ms',10000,60000)]:
        if type(result[key]) is not int or not low<=result[key]<=high:raise ValueError('打印恢复参数超出安全上限')
    return result


def encode(action,target,value):
    return PREFIX+json.dumps({'operation':{'submitPrint':'submit','cancelPrint':'cancel'}[action],
                             'target':target,**policy(value)},ensure_ascii=False,separators=(',',':'))


def validate_flow(data):
    """Validate every envelope before any application or print action starts."""
    found=False
    if not isinstance(data,dict) or not isinstance(data.get('tasks',[]),list):raise ValueError('YAML任务结构无效')
    for task in data.get('tasks',[]):
        if not isinstance(task,dict) or not isinstance(task.get('flow',[]),list):raise ValueError('YAML动作结构无效')
        submitted=cancelled=False
        for step in task.get('flow',[]):
            if not isinstance(step,dict):raise ValueError('YAML动作必须是对象')
            prompt=step.get('aiQuery','')
            if not isinstance(prompt,str) or not prompt.startswith(PREFIX):continue
            if set(step)!={'aiQuery'}:raise ValueError('打印恢复不支持额外YAML参数')
            try:body=json.loads(prompt[len(PREFIX):])
            except (ValueError,TypeError):raise ValueError('打印恢复参数无效') from None
            if not isinstance(body,dict) or set(body)!={'operation','target',*DEFAULTS}:raise ValueError('打印恢复参数无效')
            policy({key:body[key] for key in DEFAULTS})
            if not isinstance(body['target'],str) or not 1<=len(body['target'].strip())<=500:raise ValueError('打印恢复目标无效')
            if body['operation']=='submit' and not submitted:submitted=True
            elif body['operation']=='cancel' and submitted and not cancelled:cancelled=True
            else:raise ValueError('打印恢复必须先绑定新任务且不能重复')
            found=True
        if submitted!=cancelled:raise ValueError('打印恢复必须同时包含确认打印和取消')
    return found


def validate_steps(steps):
    flow=[{'aiQuery':encode(s['action'],s['target'],s['value'])} for s in steps if s['action'] in ('submitPrint','cancelPrint')]
    validate_flow({'tasks':[{'flow':flow}]})
