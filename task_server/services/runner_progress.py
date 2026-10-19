"""Bounded, display-only snapshots from a Runner; never execution authority."""


def normalize_execution_progress(value):
    if not isinstance(value, dict) or value.get('version') != 1 or not isinstance(value.get('tasks'), list):
        return None
    tasks, budget = [], 1000
    truncated = bool(value.get('truncated')) or len(value['tasks']) > 100
    for raw in value['tasks'][:100]:
        if not isinstance(raw, dict):
            continue
        rows = raw.get('steps')
        rows = rows if isinstance(rows, list) else []
        limit = min(200, budget)
        steps = [{'label': str(row.get('label') or '操作')[:180]} for row in rows[:limit] if isinstance(row, dict)]
        budget -= len(steps)
        truncated |= len(rows) > len(steps)
        index = raw.get('current_step')
        index = index if type(index) is int and 0 <= index < 10000 else None
        total = raw.get('total_steps')
        total = total if type(total) is int and 0 <= total <= 10000 else len(steps)
        tasks.append({'name': str(raw.get('name') or '未命名用例')[:160],
                      'status': raw.get('status') if raw.get('status') in ('pending','running','passed','failed') else 'pending',
                      'current_step': index, 'total_steps': total, 'steps': steps})
        recovery=raw.get('print_recovery')
        if isinstance(recovery,dict) and recovery.get('status') in ('bound','running','passed','failed') and type(recovery.get('attempts')) is int and 0<=recovery['attempts']<=8 and type(recovery.get('elapsed_ms')) is int and 0<=recovery['elapsed_ms']<=210000:
            history=recovery.get('history')
            history=history if isinstance(history,list) else []
            tasks[-1]['print_recovery']={'status':recovery['status'],'attempts':recovery['attempts'],'elapsed_ms':recovery['elapsed_ms'],'recovered':recovery['attempts']>1,'history':[{'state':str(v.get('state',''))[:120],'attempts':v['attempts'],'elapsed_ms':v['elapsed_ms']} for v in history[-24:] if isinstance(v,dict) and type(v.get('attempts')) is int and 0<=v['attempts']<=8 and type(v.get('elapsed_ms')) is int and 0<=v['elapsed_ms']<=210000]}
    return {'version': 1, 'tasks': tasks, 'truncated': truncated}
