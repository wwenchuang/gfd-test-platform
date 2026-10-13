"""Fold authenticated source-flow snapshots, never stdout guesses, into stable UI steps."""
import yaml


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
    for step_id, positions in (compiled.get('step_map') or {}).items():
        states, labels = [], []
        for position in positions:
            ti, fi = position['task_index'] - 1, position['flow_index'] - 1
            flow = tasks[ti]['flow'][fi]
            action = next(iter(flow), '操作')
            labels.append(action if action in {'aiInput','runAdbShell'} else f'{action} · {str(flow[action])[:140]}')
            state = 'pending'
            if ti < len(reported):
                task = reported[ti]
                current = task.get('current_step')
                # Positional mapping is valid only when the observer reports this source flow.
                if task.get('total_steps') == len(tasks[ti]['flow']):
                    if task['status'] == 'passed':
                        state = 'passed'
                    elif type(current) is int:
                        if fi < current:
                            state = 'passed'
                        elif fi == current:
                            state = 'failed' if task['status'] == 'failed' else 'running' if task['status'] == 'running' else 'pending'
            states.append(state)
        state = next((s for s in ('failed','running','pending') if s in states), 'passed')
        if state == 'running' and attempt['status'] in {'cancelled','interrupted','cancelling'}:
            state = 'running' if attempt['status'] == 'cancelling' else attempt['status']
        steps.append({'step_id':step_id,'label':' / '.join(labels),'status':state})
    return {'steps':steps,'step_progress_known':known,'step_progress_truncated':bool(snapshot.get('truncated'))}
