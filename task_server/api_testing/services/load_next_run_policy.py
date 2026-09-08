"""Conservative, explainable experiment selection. AI cannot override this policy."""
import math

VERSION = 'next-run-policy.v1'

def number(value):
    try:
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None
    except OverflowError:
        return None

def resource_observations(report):
    rows = []
    for service in (report.get('monitoring') or {}).get('services', [])[:10]:
        for metric in service.get('metrics', [])[:30]:
            for series in metric.get('series', [])[:20]:
                points = series.get('points', [])
                valid = [p for p in points if number(p.get('value')) is not None]
                values = [p['value'] for p in valid]
                ratios = [p['utilization_percent'] for p in valid if number(p.get('utilization_percent')) is not None]
                rows.append({'evidence_id': 'monitoring.' + str(service.get('revision_id')), 'service': service.get('name'), 'revision_id': service.get('revision_id'),
                    'scope': service.get('scope'), 'state': service.get('state'), 'metric': metric.get('key'),
                    'unit': metric.get('unit'), 'denominator': metric.get('denominator'), 'semantics': metric.get('semantics'),
                    'labels': series.get('labels', {}), 'samples': len(valid),
                    'peak': max(values) if values else None,
                    'peak_observed_at': max(valid, key=lambda p: p['value']).get('timestamp') if valid else None,
                    'first_observed_at': valid[0].get('timestamp') if valid else None, 'minimum': min(values) if values else None,
                    'first': valid[0]['value'] if valid else None, 'last': points[-1].get('value') if points else None,
                    'last_observed_at': points[-1].get('timestamp') if points else None,
                    'utilization_percent_peak': max(ratios) if ratios else None,
                    'coverage': metric.get('coverage', {})})
    return rows

def build_next_run_policy(report):
    context = report.get('test_context') or {}
    workload = (report.get('evidence') or {}).get('workload_snapshot') or {}
    model = workload.get('executor')
    arrival = model == 'constant-arrival-rate'
    target = number(workload.get('rate' if arrival else 'vus'))
    if arrival and workload.get('time_unit') == '1m' and target is not None: target /= 60
    duration = workload.get('duration_seconds')
    original = {'load_model': model or 'constant-arrival-rate', 'target': target or 1,
                'duration_seconds': duration if isinstance(duration, int) else 60,
                'agent_suggestion': '重新检查当前节点容量；压力机资源不能替代被测服务资源。'}
    policy = {'version': VERSION, 'source': '平台证据策略（非AI自由估值）', 'action': 'review', 'can_prefill': False,
        'reason': '', 'objective': '', 'next_run': original, 'evidence_ids': ['load.goal', 'sampling.integrity'],
        'limitations': [], 'stop_conditions': ['沿用本轮全部验收阈值；阈值未通过时停止继续升压。',
            '服务告警、Pod不就绪或重启时人工停止并排查；这些监控信号尚未自动联动停止。'],
        'strategy_basis': '默认资源利用率85%仅是保守的停止升压提示线，不是业务验收标准；不自动修改原阈值。'}
    def decide(action, reason, objective, prefill=False):
        policy.update(action=action, reason=reason, objective=objective, can_prefill=prefill)
        return policy
    safety = report.get('scenario_safety') or {}
    if safety.get('readonly') is not True:
        return decide('review_side_effects', '场景含写操作或副作用未知，不能自动扩大执行次数或时长。', '先确认测试账号、每轮资源归属、清理和异步任务终态。')
    if model not in {'constant-arrival-rate', 'constant-vus'} or target is None or target <= 0 or isinstance(duration, bool) or not isinstance(duration, int) or not 10 <= duration <= 86400:
        return decide('review_profile', '原执行为阶梯/突发或负载快照不完整，不能用一个目标值代替原压力曲线。', '保留原阶段曲线，明确每段升压、稳态与恢复观察后人工配置。')
    if (report.get('evidence') or {}).get('complete') is not True or ((report.get('evidence') or {}).get('sample_integrity', {}).get('acceptable') is False or ((report.get('evidence') or {}).get('sample_integrity', {}).get('consistent') is False and (report.get('evidence') or {}).get('sample_integrity', {}).get('acceptable') is not True)) or (report.get('load_goal') or {}).get('reached') is not True:
        return decide('repair_evidence', '本轮未达压或执行/采样证据不完整，升压不能验证业务容量。', '修复节点、网络或采集问题后，以相同压力和时长复验。', True)
    if report.get('verdict') != 'passed' or any((number((report.get(k) or {}).get(f)) or 0) > 0 for k,f in [('transport','http_error_rate'),('business','failure_rate'),('workflow','failure_rate')]):
        return decide('repair_failures', '本轮已有失败或必选阈值未通过，不能继续升压。', '定位失败步骤并修复；以原场景、环境、压力和验收阈值复验。', True)
    for agent in report.get('agents') or []:
        samples = (agent.get('load_generator_resources') or {}).get('samples') or []
        if any((number(row.get(key)) or 0) >= 85 for row in samples for key in ('cpu_percent', 'memory_percent')):
            return decide('inspect_generator', '压力节点运行时资源利用率达到保守提示线，可能先受压力机限制。', '先核对节点配额和实际吞吐，调整发压资源后以相同压力复验；不能归因为业务瓶颈。')
    observations = resource_observations(report)
    policy['observations'] = observations
    services = (report.get('monitoring') or {}).get('services') or []
    policy['limitations'].append('仅覆盖本轮选中的服务与实例；未接入的数据库、网络、磁盘及其他依赖不能视为正常。')
    if any(s.get('scope') == 'host' for s in services):
        policy['limitations'].append('主机监控只说明整机资源，不能归属到某个业务服务进程。')
    complete = services and all(s.get('state') == 'completed' for s in services)
    for row in observations:
        peak = row['peak']; metric = row['metric']
        utilization = peak if metric in {'cpu_percent', 'memory_percent'} else row['utilization_percent_peak']
        abnormal = (utilization is not None and utilization >= 85) or (metric == 'pod_ready' and row['minimum'] is not None and row['minimum'] < 1) or (metric == 'pod_restarts_increase_2m' and peak is not None and peak > 0)
        if abnormal:
            policy['evidence_ids'].append('monitoring.' + str(row['revision_id']))
            return decide('inspect_resources', f"监控服务“{row['service']}”的 {metric} 存在资源压力或状态异常信号。", '先关联异常时间、慢步骤与服务日志；该信号不是已证明的根因，不建议升压。')
    # Require CPU/memory for resource monitors, not separate Pod-state/database monitors.
    # Quota-less container values cannot prove available headroom.
    resource_services = [s for s in services if s.get('scope') in {'host', 'container', 'pod'}]
    complete = bool(complete and resource_services)
    for service in resource_services:
        rows = [r for r in observations if r['revision_id'] == service.get('revision_id')]
        has_cpu = any(r['metric'] in {'cpu_percent','cpu_cores'} and r['samples'] >= 3 and (r['metric']=='cpu_percent' or r['utilization_percent_peak'] is not None) for r in rows)
        has_memory = any(r['metric'] in {'memory_percent','memory_working_set_bytes'} and r['samples'] >= 3 and (r['metric']=='memory_percent' or r['utilization_percent_peak'] is not None) for r in rows)
        if not has_cpu or not has_memory or any(r['samples'] < 3 or (number(r['coverage'].get('valid_ratio')) or 0) < .95 for r in rows): complete = False
    if not complete:
        policy['limitations'].append('需各被测服务CPU/内存至少3个有效样本、覆盖率至少95%，容器需已知资源配额；主机指标仅说明整机。')
        return decide('connect_monitoring', '缺少完整的被测服务资源监控，不能根据压力机空闲推断业务有余量。', '先在新建压测中接入并检查服务监控，再保持当前压力和时长复验。')
    purpose = context.get('purpose')
    if purpose == 'smoke':
        return decide('smoke_complete', '流程冒烟目的已完成；不自动转换为容量或稳定性测试。', '如需进一步压测，先选择日常负载、逐步加压或长时稳定性目的，并填写业务目标。')
    if purpose not in {'load', 'stress', 'soak'}:
        return decide('review_purpose', '测试目的未明确，或突发场景需要单独设计恢复阶段。', '明确测试目的与阶段，而不是自动改变压力模型。')
    goal = number(context.get('recommendation_goal'))
    observe = context.get('observation_seconds')
    step = number(context.get('max_step_percent'))
    if goal is None or goal <= 0 or isinstance(observe, bool) or not isinstance(observe, int) or not 60 <= observe <= 86400 or step is None or not 0 < step <= 50:
        return decide('set_goal', '没有完整的业务目标、观察时长和加压步长，平台不自由猜测下一轮数值。', '在测试条件填写目标（同负载单位）、单轮最大增长比例和观察时长。')
    if duration < observe:
        original['duration_seconds'] = max(duration, observe)
        return decide('observe_same_load', '按用户观察时长延长验证，保持压力不变以区分时间效应与负载效应。', '观察错误趋势、资源增长和Pod状态；本轮不能单独证明或排除内存泄漏。', True)
    if purpose == 'soak':
        return decide('observation_complete', '本轮已完成填写的稳定性观察时长，不自动反复延长或加压。', '对照资源时间趋势和错误证据形成结论；需要更长观察时请更新测试目标。')
    if target >= goal:
        return decide('goal_reached', '已达到填写的业务目标，且本轮证据满足继续判断的条件；不再自动升压。', '保留当前配置作为参考，后续以相同条件回归。')
    # Fixed VUs have no request-rate bound: explicit manual review remains necessary.
    if not arrival:
        return decide('review_vus', '固定并发不限制每秒请求数，不能仅凭VU比例推断业务流量。', '结合实际吞吐与业务目标人工设计下一轮，避免低VU造成高请求量。')
    proposed = math.floor(min(goal, target * (1 + step / 100)) * 60 + 1e-8) / 60
    if proposed <= target:
        return decide('review_resolution', '按填写步长计算后不足一个每分钟请求单位，不能向上取整扩大压力。', '人工核对步长和目标。')
    original.update(target=proposed, duration_seconds=observe)
    return decide('increase_bounded', f'本轮稳定观察和资源证据满足条件，按用户填写的最大增长{step:g}%递增，不超过业务目标。', '只改变压力，保持版本、数据条件、时长和阈值，定位性能变化；不是系统容量保证。', True)
