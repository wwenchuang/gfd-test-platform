"""统一任务中心 - 管理所有异步任务的状态"""

import os
import json
import uuid
from datetime import datetime

TASKS_PATH = '/opt/midscene-task-data/task-center.json'

# 任务类型
TASK_TYPES = [
    'AGENT_RUN', 'YAML_GENERATION', 'SONIC_SYNC', 'RUNNER_JOB',
    'FAILURE_ANALYSIS', 'REPAIR_DRAFT', 'BUG_DRAFT', 'REPORT_UPLOAD'
]


def _load_tasks():
    """加载任务中心数据"""
    if os.path.exists(TASKS_PATH):
        with open(TASKS_PATH, 'r') as f:
            return json.load(f)
    return {'tasks': [], 'updatedAt': None}


def _save_tasks(data):
    """保存任务中心数据"""
    data['updatedAt'] = datetime.now().isoformat()
    os.makedirs(os.path.dirname(TASKS_PATH), exist_ok=True)
    with open(TASKS_PATH, 'w') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def create_task(task_type, title, case_id=None, agent_run_id=None, job_id=None, report_id=None):
    """创建任务"""
    if task_type not in TASK_TYPES:
        raise ValueError(f'无效的任务类型: {task_type}, 有效值: {TASK_TYPES}')
    data = _load_tasks()
    task = {
        'taskId': str(uuid.uuid4())[:12],
        'type': task_type,
        'title': title,
        'status': 'PENDING',
        'progress': 0,
        'caseId': case_id,
        'agentRunId': agent_run_id,
        'jobId': job_id,
        'reportId': report_id,
        'createdAt': datetime.now().isoformat(),
        'updatedAt': datetime.now().isoformat(),
        'error': None
    }
    data['tasks'].append(task)
    _save_tasks(data)
    return task


def update_task_status(task_id, status, progress=None, error=None):
    """更新任务状态"""
    data = _load_tasks()
    for task in data['tasks']:
        if task.get('taskId') == task_id:
            task['status'] = status
            if progress is not None:
                task['progress'] = progress
            if error is not None:
                task['error'] = error
            task['updatedAt'] = datetime.now().isoformat()
            _save_tasks(data)
            return task
    return None


def get_task(task_id):
    """根据taskId查找任务"""
    data = _load_tasks()
    for task in data['tasks']:
        if task.get('taskId') == task_id:
            return task
    return None


def list_tasks(task_type=None, status=None, limit=50):
    """列出任务，支持按类型和状态过滤"""
    data = _load_tasks()
    tasks = data['tasks']
    if task_type:
        tasks = [t for t in tasks if t.get('type') == task_type]
    if status:
        tasks = [t for t in tasks if t.get('status') == status]
    # 按时间倒序
    tasks.sort(key=lambda t: t.get('createdAt', ''), reverse=True)
    return tasks[:limit]
