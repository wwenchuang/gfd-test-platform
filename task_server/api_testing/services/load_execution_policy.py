"""Validated, immutable test intent and safety policy; no arbitrary script options."""
import math

PURPOSES = {'smoke': '流程冒烟', 'load': '日常负载', 'stress': '逐步加压', 'spike': '突发流量', 'soak': '长时稳定性'}


def parse_test_context(value):
    if value is None:
        return None
    allowed = {'purpose', 'release', 'data_profile', 'cache_state', 'notes'}
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError('测试条件包含不支持字段')
    if value.get('purpose') not in PURPOSES:
        raise ValueError('请选择有效的测试目的')
    result = {'purpose': value['purpose']}
    for key in allowed - {'purpose'}:
        text = value.get(key, '')
        if not isinstance(text, str) or len(text) > 1000 or any(ord(c) < 32 and c not in '\n\t' for c in text):
            raise ValueError('测试条件必须为不超过1000字的说明')
        result[key] = text.strip()
    return result


def parse_stop_policy(value):
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {'http_error_rate', 'grace_seconds'}:
        raise ValueError('自动停止策略需要HTTP错误率及观察宽限时间')
    rate, grace = value['http_error_rate'], value['grace_seconds']
    if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not math.isfinite(rate) or not 0 < rate <= 1:
        raise ValueError('自动停止HTTP错误率必须大于0且不超过100%')
    if isinstance(grace, bool) or not isinstance(grace, int) or not 10 <= grace <= 600:
        raise ValueError('自动停止观察宽限时间必须为10至600秒')
    return {'http_error_rate': rate, 'grace_seconds': grace}


def safety_thresholds(policy):
    parsed = parse_stop_policy(policy)
    if not parsed:
        return {}
    return {'http_req_failed': [{'threshold': f"rate<={parsed['http_error_rate']}", 'abortOnFail': True, 'delayAbortEval': f"{parsed['grace_seconds']}s"}]}


def stop_peers_after_failure(run, shards, failed_shard_id):
    """Caller holds the run lock; do not orphan unclaimed shards on guard abort."""
    if not (run.configuration or {}).get('stop_policy') or run.state not in {'starting', 'running', 'stopping'}:
        return
    run.state = 'stopping'
    run.stop_reason = '错误保护：一个压测节点失败，已通知其他节点停止；服务端在途任务需另行确认'
    run.summary = {**(run.summary or {}), 'automatic_stop': {'trigger_shard_id': failed_shard_id, 'reason': 'node_failed_with_guard_enabled'}}
    for shard in shards:
        if shard.state == 'assigned':
            shard.state = 'cancelled'
        elif shard.state in {'ready', 'running'}:
            shard.state = 'stopping'
