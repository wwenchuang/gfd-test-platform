"""System Context Service - Agent启动时收集上下文"""

import os
import json
import glob
from datetime import datetime, timedelta


def build_agent_context(target, app_name='智小白3D APP', module=''):
    """为Agent PLAN阶段构建系统上下文"""
    context = {
        'appName': app_name,
        'target': target,
        'module': module,
        'recentFailures': _get_recent_failures(),
        'relatedCases': _get_related_cases(target, module),
        'recentYaml': _get_recent_yaml(module),
        'sonicStatus': _get_sonic_status(),
        'runnerStatus': _get_runner_status(),
        'modelStrategy': _get_model_strategy(),
        'knowledgeSnippets': _get_knowledge_snippets(target),
        'riskPolicy': _get_risk_policy_summary()
    }
    return context


def _get_recent_failures(limit=5):
    """获取最近的失败任务"""
    jobs_dir = '/opt/midscene-task-data/jobs'
    failures = []
    if not os.path.isdir(jobs_dir):
        return failures
    try:
        files = sorted(glob.glob(os.path.join(jobs_dir, '*.json')), key=os.path.getmtime, reverse=True)
        for f in files[:50]:
            with open(f, 'r') as fp:
                job = json.load(fp)
            if job.get('status') == 'FAILED':
                failures.append({
                    'jobId': job.get('jobId', ''),
                    'taskName': job.get('taskName', ''),
                    'error': (job.get('error', '') or '')[:200],
                    'createdAt': job.get('createdAt', '')
                })
            if len(failures) >= limit:
                break
    except Exception:
        pass
    return failures


def _get_related_cases(target, module):
    """从case-index获取相关Case"""
    try:
        from task_server.services.case_service import list_cases_by_module
        cases = list_cases_by_module(module)
        # 简单关键词匹配
        if target:
            keywords = target.split()
            related = [c for c in cases if any(kw in c.get('taskName', '') for kw in keywords)]
            return related[:10]
        return cases[:10]
    except Exception:
        return []


def _get_recent_yaml(module, limit=10):
    """获取最近的YAML文件"""
    base_dir = os.environ.get('MIDSCENE_BASE_DIR', '/opt/midscene-task-platform')
    tasks_dir = os.path.join(base_dir, 'server-tasks')
    yamls = []
    if not os.path.isdir(tasks_dir):
        return yamls
    try:
        for root, dirs, files in os.walk(tasks_dir):
            for f in files:
                if f.endswith('.yaml'):
                    full = os.path.join(root, f)
                    if module and module not in root:
                        continue
                    yamls.append({'path': full, 'name': f, 'module': os.path.basename(root)})
        yamls.sort(key=lambda x: x.get('name', ''))
        return yamls[:limit]
    except Exception:
        return []


def _get_sonic_status():
    """获取Sonic连接状态"""
    try:
        from task_server.sonic_service import get_sonic_status
        return get_sonic_status()
    except Exception:
        return {'connected': False, 'error': '无法获取Sonic状态'}


def _get_runner_status():
    """获取Runner状态"""
    runners_dir = '/opt/midscene-task-data/runners'
    if not os.path.isdir(runners_dir):
        return {'online': 0, 'runners': []}
    try:
        runners = []
        for f in os.listdir(runners_dir):
            if f.endswith('.json'):
                with open(os.path.join(runners_dir, f), 'r') as fp:
                    runners.append(json.load(fp))
        online = [r for r in runners if r.get('status') == 'online']
        return {'online': len(online), 'total': len(runners), 'runners': runners[:5]}
    except Exception:
        return {'online': 0, 'runners': []}


def _get_model_strategy():
    """获取当前模型策略摘要"""
    try:
        router_path = os.path.join(
            os.environ.get('MIDSCENE_BASE_DIR', '/opt/midscene-task-platform'),
            'ai-gateway/config/model-router.json'
        )
        if os.path.exists(router_path):
            with open(router_path, 'r') as f:
                router = json.load(f)
            return {'name': '稳定省钱版', 'tasks': list(router.keys())[:6]}
    except Exception:
        pass
    return {'name': '默认', 'tasks': []}


def _get_knowledge_snippets(target, limit=3):
    """从知识库获取相关片段"""
    try:
        from task_server.services.knowledge_service import match_failure_pattern
        if target:
            matches = match_failure_pattern(target)
            return matches[:limit]
    except Exception:
        pass
    return []


def _get_risk_policy_summary():
    """风险策略摘要"""
    return {
        'highRiskKeywords': [
            "确认打印", "开始打印", "支付", "删除", "覆盖基线",
            "格式化", "清空", "解绑", "重置", "批量同步", "批量执行"
        ],
        'rules': [
            'LOW在AUTO_SAFE下可自动执行',
            'MEDIUM需审计',
            'HIGH任何模式必须WAIT_CONFIRM',
            'PRODUCT_BUG不修YAML',
            'ENV_ISSUE不修YAML',
            'UNKNOWN进WAIT_CONFIRM'
        ]
    }
