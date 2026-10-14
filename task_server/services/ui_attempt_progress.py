"""Fold authenticated source-flow snapshots, never stdout guesses, into stable UI steps."""
import yaml


_ACTION_LABELS = {'launch': '启动应用', 'terminate': '关闭应用', 'aiTap': '点击',
                  'aiWaitFor': '等待', 'aiAssert': '检查', 'ai': '操作', 'aiAction': '操作',
                  'aiInput': '输入内容', 'aiScroll': '滚动', 'sleep': '等待',
                  'runAdbShell': '设备操作', 'aiQuery': '提取信息'}


def stable_step_ids(case_id, revision, source):
    import hashlib
    try:
        data = yaml.safe_load(source)
        if not isinstance(data, dict) or not isinstance(data.get('tasks'), list) or any(not isinstance(t,dict) for t in data['tasks']):
            return None
    except yaml.YAMLError:
        return None
    return [["step_" + hashlib.sha256(f'{case_id}:{revision}:{ti}:{fi}'.encode()).hexdigest()[:24]
             for fi, _ in enumerate(task.get('flow') or [], 1)]
            for ti, task in enumerate(data.get('tasks') or [], 1)]


def describe_steps(attempt):
    compiled = attempt['compiled_case']
    data = yaml.safe_load(compiled.get('yaml_content') or '{}') or {}
    tasks = data.get('tasks') or []
    snapshot = attempt.get('execution_progress') or {}
    reported = snapshot.get('tasks') or []
    steps = []
    known = bool(snapshot.get('version') == 1 and reported)
    # JSON persistence sorts stable hashed IDs; only compiled positions describe execution order.
    position_key = lambda p: (p['task_index'], p['flow_index'])
    mapped_steps = [(step_id, sorted(positions, key=position_key))
                    for step_id, positions in (compiled.get('step_map') or {}).items()]
    mapped_steps.sort(key=lambda item: position_key(item[1][0]) if item[1] else (float('inf'), float('inf')))
    for step_id, positions in mapped_steps:
        states, labels = [], []
        for position in positions:
            ti, fi = position['task_index'] - 1, position['flow_index'] - 1
            flow = tasks[ti]['flow'][fi]
            action = next(iter(flow), '操作')
            action_label = _ACTION_LABELS.get(action, action)
            label = action_label if action in {'aiInput','runAdbShell'} else f'{action_label} · {str(flow[action])[:140]}'
            state = 'pending'
            if known and ti < len(reported):
                task = reported[ti]
                current = task.get('current_step')
                # Positional mapping is valid only when the observer reports this source flow.
                if task.get('total_steps') == len(tasks[ti]['flow']):
                    reported_steps = task.get('steps') or []
                    if fi < len(reported_steps) and action not in {'aiInput','runAdbShell'}:
                        label = reported_steps[fi].get('label') or label
                    if task['status'] == 'passed':
                        state = 'passed'
                    elif type(current) is int:
                        if fi < current:
                            state = 'passed'
                        elif fi == current:
                            state = 'failed' if task['status'] == 'failed' else 'running' if task['status'] == 'running' else 'pending'
            labels.append(label)
            states.append(state)
        state = next((s for s in ('failed','running','pending') if s in states), 'passed' if states else 'pending')
        if state == 'running' and attempt['status'] in {'cancelled','interrupted','cancelling'}:
            state = 'running' if attempt['status'] == 'cancelling' else attempt['status']
        steps.append({'step_id':step_id,'label':' / '.join(labels) or '操作','status':state})
    return {'steps':steps,'step_progress_known':known,'step_progress_truncated':bool(snapshot.get('truncated'))}
