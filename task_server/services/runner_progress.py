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
    return {'version': 1, 'tasks': tasks, 'truncated': truncated}
