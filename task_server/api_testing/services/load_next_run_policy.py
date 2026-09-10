"""Evidence-based experiment guardrails and auditable AI proposal validation."""
import math
from copy import deepcopy
from .load_scenario_compiler import _parse_workload, LoadScenarioCompileError

VERSION = 'next-run-policy.v3'

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
    arrival = 'arrival-rate' in str(model)
    ramping = str(model).startswith('ramping-')
    stages = workload.get('stages') if isinstance(workload.get('stages'), list) else []
    start_key = 'start_rate' if arrival else 'start_vus'
    profile_error = None
    try:
        _parse_workload(workload)
    except (LoadScenarioCompileError, TypeError, ValueError, KeyError, OverflowError) as error:
        profile_error = error
    ramp_targets = ([number(workload.get(start_key))] + [number(s.get('target')) for s in stages if isinstance(s, dict)]) if ramping else []
    target = max(ramp_targets) if ramping and ramp_targets and all(value is not None for value in ramp_targets) else number(workload.get('rate' if arrival else 'vus')) if not ramping else None
    if arrival and workload.get('time_unit') == '1m' and target is not None: target /= 60
    stage_durations = [s.get('duration_seconds') for s in stages if isinstance(s, dict)]
    duration = sum(stage_durations) if ramping and stage_durations and all(isinstance(value, int) and not isinstance(value, bool) for value in stage_durations) else workload.get('duration_seconds') if not ramping else None
    original = {'load_model': model or 'constant-arrival-rate', 'target': target or 1,
                'duration_seconds': duration if isinstance(duration, int) else 60,
                'workload': deepcopy(workload),
                'agent_suggestion': '重新检查当前节点容量；压力机资源不能替代被测服务资源。'}
    policy = {'version': VERSION, 'source': '规则备用建议', 'validation_status': 'rule_fallback', 'original_workload': deepcopy(workload), 'test_context': deepcopy(context), 'action': 'review', 'can_prefill': False,
        'reason': '', 'objective': '', 'next_run': original, 'evidence_ids': ['load.goal', 'sampling.integrity'],
        'limitations': [], 'stop_conditions': ['沿用本轮全部验收阈值；阈值未通过时停止继续升压。',
            '服务告警、Pod不就绪或重启时人工停止并排查；这些监控信号尚未自动联动停止。'],
        'strategy_basis': '默认资源利用率85%仅是保守的停止升压提示线，不是业务验收标准；不自动修改原阈值。'}
    policy['observations'] = resource_observations(report)
    policy['evidence_ids'] += ['latency.summary', 'business.summary', 'workflow.summary']
    policy['evidence_ids'] += list(dict.fromkeys(r['evidence_id'] for r in policy['observations']))
    policy['stage_evidence'] = deepcopy((report.get('load_goal') or {}).get('stages') or [])
    def decide(action, reason, objective, prefill=False):
        policy.update(action=action, reason=reason, objective=objective, can_prefill=prefill)
        # Keep the full executable curve in sync with fixed-model fallback fields.
        if not ramping and workload:
            updated = original['workload']
            updated['duration_seconds'] = original['duration_seconds']
            if arrival:
                value = original['target']
                updated['time_unit'] = '1s' if float(value).is_integer() else '1m'
                updated['rate'] = round(value if updated['time_unit'] == '1s' else value * 60)
            else:
                updated['vus'] = original['target']
        if action in {'smoke_complete', 'observation_complete', 'goal_reached'}:
            policy['continuation_status'] = '已完成本轮目标'
        else:
            policy['continuation_status'] = '暂不升压但需要继续验证' if action not in {'increase_bounded', 'review_ramp_growth'} else '可在校验范围内设计下一轮'
        return policy
    safety = report.get('scenario_safety') or {}
    if safety.get('readonly') is not True:
        return decide('review_side_effects', '场景含写操作或副作用未知，不能自动扩大执行次数或时长。', '先确认测试账号、每轮资源归属、清理和异步任务终态。')
    if profile_error is not None or model not in {'constant-arrival-rate', 'constant-vus', 'ramping-arrival-rate', 'ramping-vus'} or target is None or target <= 0 or isinstance(duration, bool) or not isinstance(duration, int) or not 10 <= duration <= 86400:
        return decide('review_profile', '原负载快照不完整，不能据此生成可执行配置。', '保留原阶段曲线，明确每段升压、稳态与恢复观察后人工配置。')
    observations = policy['observations']
    services = (report.get('monitoring') or {}).get('services') or []
    policy['limitations'].append('仅覆盖本轮选中的服务与实例；未接入的数据库、网络、磁盘及其他依赖不能视为正常。')
    if any(s.get('scope') == 'host' for s in services):
        policy['limitations'].append('主机监控只说明整机资源，不能归属到某个业务服务进程。')
    resource_services = [s for s in services if s.get('scope') in {'host', 'container', 'pod'}]
    monitoring_complete = bool(services and resource_services and all(s.get('state') == 'completed' for s in services))
    for service in resource_services:
        rows = [r for r in observations if r['revision_id'] == service.get('revision_id')]
        resource_rows = [r for r in rows if r['metric'] in {'cpu_percent','cpu_cores','memory_percent','memory_working_set_bytes'}]
        by_labels = {}
        for row in resource_rows:
            label_key = tuple(sorted((str(key), str(value)) for key, value in (row.get('labels') or {}).items() if key != '__name__'))
            by_labels.setdefault(label_key, []).append(row)
        if not by_labels:
            monitoring_complete = False
        for instance_rows in by_labels.values():
            has_cpu = any(r['metric'] in {'cpu_percent','cpu_cores'} and r['samples'] >= 3 and (r['metric']=='cpu_percent' or r['utilization_percent_peak'] is not None) for r in instance_rows)
            has_memory = any(r['metric'] in {'memory_percent','memory_working_set_bytes'} and r['samples'] >= 3 and (r['metric']=='memory_percent' or r['utilization_percent_peak'] is not None) for r in instance_rows)
            if not has_cpu or not has_memory or any(r['samples'] < 3 or (number(r['coverage'].get('valid_ratio')) or 0) < .95 for r in instance_rows):
                monitoring_complete = False
    if not monitoring_complete:
        policy['limitations'].append('需各被测服务每个实例的CPU/内存至少3个有效样本、覆盖率至少95%，容器需已知资源配额；主机指标仅说明整机。')
    abnormal_resources = []
    for row in observations:
        utilization = row['peak'] if row['metric'] in {'cpu_percent', 'memory_percent'} else row['utilization_percent_peak']
        abnormal = (utilization is not None and utilization >= 85) or (row['metric'] == 'pod_ready' and row['minimum'] is not None and row['minimum'] < 1) or (row['metric'] == 'pod_restarts_increase_2m' and row['peak'] is not None and row['peak'] > 0)
        if abnormal:
            abnormal_resources.append(row)
    if abnormal_resources:
        policy['limitations'].append('本轮同时存在被测服务资源异常或Pod状态信号；需与失败阶段和慢步骤对齐，不能被主要失败分支隐藏。')
    error_fields = [('transport','http_error_rate','requests'),('business','failure_rate','assertions'),('workflow','failure_rate','iterations')]
    unknown_errors = [f'{section}.{field}' for section, field, denominator in error_fields if number((report.get(section) or {}).get(field)) is None or number((report.get(section) or {}).get(denominator)) is None or number((report.get(section) or {}).get(denominator)) <= 0]
    known_failure = report.get('verdict') == 'failed' or any((number((report.get(k) or {}).get(f)) or 0) > 0 for k,f,_ in error_fields) or any(item.get('required') is True and item.get('passed') is False and item.get('actual') is not None for item in (report.get('thresholds') or []) if isinstance(item, dict))
    if known_failure:
        return decide('repair_failures', '本轮已有失败或必选阈值未通过，不能继续升压。', '定位失败步骤并核对负载目标缺口和服务监控，必要时拆开接口低负载验证；修复后以原完整曲线复验并检查降压后恢复。', True)
    if unknown_errors:
        return decide('repair_evidence', 'HTTP、业务或完整链路失败率存在未知值，不能按零失败继续升压。', '先补齐失败统计分母和结构化证据，再按原配置复验；同时核对服务监控完整性。', True)
    if (report.get('evidence') or {}).get('complete') is not True or ((report.get('evidence') or {}).get('sample_integrity', {}).get('acceptable') is False or ((report.get('evidence') or {}).get('sample_integrity', {}).get('consistent') is False and (report.get('evidence') or {}).get('sample_integrity', {}).get('acceptable') is not True)) or (report.get('load_goal') or {}).get('reached') is not True:
        return decide('repair_evidence', '本轮未达压或执行/采样证据不完整，升压不能验证业务容量。', '先核对各阶段实际压力、采样覆盖和目标达成计算；同时补齐服务监控缺口，确认原因后按原曲线复验。', True)
    for agent in report.get('agents') or []:
        samples = (agent.get('load_generator_resources') or {}).get('samples') or []
        if any((number(row.get(key)) or 0) >= 85 for row in samples for key in ('cpu_percent', 'memory_percent')):
            return decide('inspect_generator', '压力节点运行时资源利用率达到保守提示线，可能先受压力机限制。', '先核对节点配额和实际吞吐，调整发压资源后以相同压力复验；不能归因为业务瓶颈。')
    if abnormal_resources:
        row = abnormal_resources[0]
        policy['evidence_ids'].append('monitoring.' + str(row['revision_id']))
        return decide('inspect_resources', f"监控服务“{row['service']}”的 {row['metric']} 存在资源压力或状态异常信号。", '先关联异常时间、慢步骤与服务日志；该信号不是已证明的根因，不建议升压。')
    # Require CPU/memory for resource monitors, not separate Pod-state/database monitors.
    # Quota-less container values cannot prove available headroom.
    if not monitoring_complete:
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
        return decide('set_goal', '加压目标、增长上限或观察时长尚未完整填写；可以保持原配置复验，调整压力或延长执行前需补齐边界。', '保留原曲线、原时长、阈值和监控条件复验，核对慢步骤与恢复表现；需要调整时再填写业务目标、单轮增长上限和观察时长。', True)
    if duration < observe:
        if ramping:
            policy['can_adjust_curve'] = True
            return decide('observe_full_curve', '完整阶段曲线尚未覆盖用户填写的观察时长；先延长阶段观察，不能同时升压。', '保持原起点、阶段压力、恢复含义和VU预算，仅设计有界的完整曲线观察时长。', True)
        original['duration_seconds'] = observe
        return decide('observe_same_load', '按用户观察时长延长验证，保持压力不变以区分时间效应与负载效应。', '观察错误趋势、资源增长和Pod状态；本轮不能单独证明或排除内存泄漏。', True)
    if purpose == 'soak':
        return decide('observation_complete', '本轮已完成填写的稳定性观察时长，不自动反复延长或加压。', '对照资源时间趋势和错误证据形成结论；需要更长观察时请更新测试目标。')
    if target >= goal:
        return decide('goal_reached', '已达到填写的业务目标，且本轮证据满足继续判断的条件；不再自动升压。', '保留当前配置作为参考，后续以相同条件回归。')
    if ramping:
        policy['can_adjust_curve'] = True
        return decide('review_ramp_growth', '阶段负载、阈值和资源证据可用于设计下一轮；建议仍需逐阶段校验业务目标和增长范围。', '保留升压、稳态与降压恢复顺序；结合阶段证据选择保持压力复验、延长稳态或有界调整。', True)
    # Fixed VUs have no request-rate bound: explicit manual review remains necessary.
    if not arrival:
        return decide('review_vus', '固定并发不限制每秒请求数，不能仅凭VU比例推断业务流量。', '结合实际吞吐与业务目标人工设计下一轮，避免低VU造成高请求量。')
    proposed = math.floor(min(goal, target * (1 + step / 100)) * 60 + 1e-8) / 60
    if proposed <= target:
        return decide('review_resolution', '按填写步长计算后不足一个每分钟请求单位，不能向上取整扩大压力。', '人工核对步长和目标。')
    original.update(target=proposed, duration_seconds=observe)
    return decide('increase_bounded', f'本轮稳定观察和资源证据满足条件，按用户填写的最大增长{step:g}%递增，不超过业务目标。', '只改变压力，保持版本、数据条件、时长和阈值，定位性能变化；不是系统容量保证。', True)


def validate_next_run_advice(policy, advice, *, fallback=False):
    """Retain the model proposal; constrain executable parameters, never erase provenance."""
    result = deepcopy(policy)
    result['ai_proposal'] = deepcopy(advice) if not fallback else None
    result['adjustment_reasons'] = []
    original = policy.get('original_workload') or {}
    def repeat_original(reason, source):
        result.update(action='repeat_original', can_prefill=True, validation_status='rule_fallback' if fallback else 'ai_adjusted', source=source,
                      reason=reason, objective='保持原配置、原阈值与监控条件复验；先解决证据或建议校验问题，不自动升压。')
        result['next_run'] = deepcopy(policy.get('next_run') or {})
        result['next_run']['workload'] = deepcopy(original)
        if str(original.get('executor')).startswith('ramping-'):
            targets = [original.get('start_rate', original.get('start_vus', 0))] + [stage.get('target', 0) for stage in original.get('stages', [])]
            result['next_run']['duration_seconds'] = sum(stage.get('duration_seconds', 0) for stage in original.get('stages', []))
            result['next_run']['target'] = max(targets) / (60 if original.get('time_unit') == '1m' else 1)
        else:
            result['next_run']['duration_seconds'] = original.get('duration_seconds')
            result['next_run']['target'] = original.get('rate', original.get('vus'))
            if 'arrival-rate' in str(original.get('executor')) and original.get('time_unit') == '1m' and number(result['next_run']['target']) is not None:
                result['next_run']['target'] /= 60
        return result
    if fallback:
        if policy.get('action') in {'increase_bounded', 'observe_same_load', 'observe_full_curve', 'review_ramp_growth'}:
            return repeat_original('AI 分析不可用，规则备用计划保留原配置复验。', '规则备用建议')
        return result
    proposed = deepcopy(advice.get('workload'))
    try:
        if not proposed:
            if str(original.get('executor')).startswith('ramping-'):
                raise ValueError('AI 未提供完整阶段曲线，保留原曲线复验。')
            proposed = deepcopy(original)
            proposed['duration_seconds'] = advice.get('duration_seconds')
            if 'arrival-rate' in str(original.get('executor')):
                target = advice.get('target')
                numeric_target = number(target)
                if numeric_target is None or numeric_target <= 0:
                    raise ValueError('AI 建议的吞吐必须是有限正数。')
                per_minute = numeric_target * 60
                if numeric_target.is_integer():
                    proposed['time_unit'], proposed['rate'] = '1s', int(numeric_target)
                elif math.isfinite(per_minute) and math.isclose(per_minute, round(per_minute), rel_tol=0, abs_tol=1e-9):
                    proposed['time_unit'], proposed['rate'] = '1m', int(round(per_minute))
                else:
                    raise ValueError('AI 建议吞吐无法精确表示为整次每秒或每分钟，不能取整放大或隐去压力。')
            else:
                proposed['vus'] = advice.get('target')
        _parse_workload(proposed)
        if proposed['executor'] != original.get('executor') or advice.get('load_model') != original.get('executor'):
            raise ValueError('本次建议需保留原负载模型，切换模型应单独设计。')
        if policy.get('action') not in {'increase_bounded', 'observe_same_load', 'observe_full_curve', 'review_ramp_growth'}:
            if proposed != original:
                raise ValueError('本轮尚不具备调整压力或延长执行的证据；先完成排查，再按原配置复验。')
        else:
            arrival = 'arrival-rate' in proposed['executor']
            divisor = 60 if proposed.get('time_unit') == '1m' else 1
            previous_divisor = 60 if original.get('time_unit') == '1m' else 1
            context = policy.get('test_context') or {}
            factor = 1 + (number(context.get('max_step_percent')) or 0) / 100
            goal = number(context.get('recommendation_goal')) or 0
            if arrival and any(proposed.get(k) != original.get(k) for k in ('max_vus', 'pre_allocated_vus')):
                raise ValueError('AI 不得扩大原发压 VU 预算；请在节点容量核验后单独调整。')
            if proposed['executor'].startswith('ramping-'):
                if proposed.get('time_unit') != original.get('time_unit'):
                    raise ValueError('阶段吞吐必须保留原时间单位，避免换算改变曲线含义。')
                key = 'start_rate' if arrival else 'start_vus'
                before = [original[key]] + [s['target'] for s in original['stages']]
                after = [proposed[key]] + [s['target'] for s in proposed['stages']]
                if len(before) != len(after):
                    raise ValueError('保留原阶段顺序与数量，以便逐阶段比较；新增阶段请在向导明确配置。')
                before_directions = [(right > left) - (right < left) for left, right in zip(before, before[1:])]
                after_directions = [(right > left) - (right < left) for left, right in zip(after, after[1:])]
                if after_directions != before_directions:
                    raise ValueError('各阶段必须保留原升压、稳态和降压恢复方向，不能改变恢复含义。')
                if after[0] != before[0]:
                    raise ValueError('下一轮必须保留原曲线起点，避免同时改变基线与阶段压力。')
                recovery_started = False
                for index, direction in enumerate(before_directions):
                    recovery_started = recovery_started or direction < 0
                    if recovery_started and after[index + 1] > before[index + 1]:
                        raise ValueError('降压恢复及后续低稳态目标不能高于原恢复目标。')
                if any(a / divisor > max(b / previous_divisor, min(goal, b / previous_divisor * factor)) + 1e-8 for a, b in zip(after, before)):
                    raise ValueError('阶段压力超过业务目标或单轮增长上限，或扩大了原降压恢复阶段。')
                total = sum(s['duration_seconds'] for s in proposed['stages'])
                old_total = sum(s['duration_seconds'] for s in original['stages'])
                pressure_changed = after != before
                duration_changed = any(new['duration_seconds'] != old['duration_seconds'] for new, old in zip(proposed['stages'], original['stages']))
                if policy.get('action') == 'observe_full_curve' and pressure_changed:
                    raise ValueError('完整曲线观察只能调整阶段时长，必须保持原起点和全部阶段压力。')
                if pressure_changed and duration_changed:
                    raise ValueError('单轮实验只能改变阶段压力或观察时长之一，避免混淆瓶颈定位。')
                # Recovery must remain present and must not be shortened by model advice.
                if any(new['duration_seconds'] < old['duration_seconds'] for new, old in zip(proposed['stages'], original['stages'])):
                    raise ValueError('不能缩短原阶段观察时间；低负载步骤排查应单独配置。')
                if total > max(old_total, context.get('observation_seconds', old_total)):
                    raise ValueError('总观察时长超过本轮或用户填写的观察上限。')
            else:
                total = proposed['duration_seconds']
                bound = policy['next_run']['target']
                target = proposed['rate'] / divisor if arrival else proposed['vus']
                original_target = original.get('rate', original.get('vus')) / previous_divisor
                original_duration = original.get('duration_seconds')
                if policy.get('action') == 'observe_same_load' and abs(target - original_target) > 1e-8:
                    raise ValueError('延长观察只能保持原压力，不能同时改变压力定位。')
                if target < original_target - 1e-8 or total < original_duration:
                    raise ValueError('固定模型建议不能降低原压力或缩短原观察时间。')
                if target > bound + 1e-8 or total > policy['next_run']['duration_seconds']:
                    raise ValueError('AI 建议超过本轮证据允许的压力或观察时长。')
        total = sum(s['duration_seconds'] for s in proposed['stages']) if 'stages' in proposed else proposed['duration_seconds']
        if not 10 <= total <= 86400:
            raise ValueError('总观察时长必须在 10 到 86400 秒之间。')
        result['next_run'] = deepcopy(advice)
        result['next_run']['workload'] = proposed
        result['next_run']['duration_seconds'] = total
        targets = [s['target'] for s in proposed['stages']] + [proposed.get('start_rate', proposed.get('start_vus', 0))] if 'stages' in proposed else [proposed.get('rate', proposed.get('vus'))]
        result['next_run']['target'] = max(targets) / (60 if proposed.get('time_unit') == '1m' else 1)
        result.update(validation_status='ai_validated', source='AI 建议已通过校验')
    except (LoadScenarioCompileError, ValueError, TypeError, KeyError, OverflowError) as error:
        result['adjustment_reasons'] = [str(error)]
        if policy.get('action') in {'increase_bounded', 'observe_same_load', 'observe_full_curve', 'review_ramp_growth'}:
            repeat_original('AI 建议越过平台约束，已调整为原配置复验。', 'AI 建议被调整')
        else:
            result.update(validation_status='ai_adjusted', source='AI 建议被平台门禁拒绝')
    return result
