"""Bounded observation actions. AI selects evidence and priority, not arbitrary mutations."""
import copy
import math


METRIC_DOMAINS = {
    'cpu_percent': 'cpu', 'cpu_cores': 'cpu',
    'memory_percent': 'memory', 'memory_working_set_bytes': 'memory',
    'postgres_connections': 'database', 'postgres_commits_per_second': 'database', 'postgres_rollbacks_per_second': 'database',
    'disk_read_bytes_per_second': 'storage', 'disk_write_bytes_per_second': 'storage',
    'disk_read_iops': 'storage', 'disk_write_iops': 'storage', 'disk_read_latency_ms': 'storage', 'disk_write_latency_ms': 'storage',
    'network_receive_bytes_per_second': 'network', 'network_transmit_bytes_per_second': 'network',
}
LABELS = {'database': '数据库依赖', 'downstream': '下游调用', 'gc_runtime': '运行时 GC',
          'bounded_work_slots': '工作槽保护', 'bounded_connection_slots': '连接槽保护',
          'cpu_wall_time_work': '有界 CPU 工作', 'shared_ttl_allocation': '共享 TTL 内存分配'}


def recommendation_options(evidence):
    options = []

    def add(code, domain, action, verification, refs, *, fact=None, intent='inspect'):
        if not refs:
            return
        options.append({'action_code': code, 'domain': domain, 'service_key': fact.get('service_key') if fact else None,
                        'intent': intent, 'fact_ids': [fact['evidence_id']] if fact else [],
                        'evidence_ids': list(dict.fromkeys(refs)), 'action': action, 'verification': verification})

    add('review_execution', 'execution', '核对本轮达压、完整性与阈值，并按平台下一轮配置复验。',
        '对照节点终态、采样完整性、实际压力和失败阈值；证据未齐时保持当前配置，不据此确认根因。',
        ['load.goal', 'sampling.integrity'])
    step_ids = {row['evidence_id'] for row in evidence.get('steps', [])}
    add('inspect_step_assertions', 'test_data', '核对具体步骤的业务断言与失败位置。',
        '保持场景、数据和压力，比较该步骤 HTTP 与业务断言；步骤失败只能定位现象，不能独立证明服务内部根因。', sorted(step_ids))
    add('compare_request_path', 'network', '补采请求连接耗时与服务本地耗时，核对发压路径。',
        '保持请求和压力，记录连接、往返和服务本地耗时；端到端慢不能单独证明网络或服务瓶颈。',
        ['transport.summary', 'latency.summary'], intent='collect_evidence')
    for fact in evidence.get('service_facts', []):
        component, state = fact['component'], fact['state']
        refs = [f'step.{step}' for step in fact['step_ids'] if f'step.{step}' in step_ids]
        if state == 'unknown':
            add('confirm_' + component, fact['domain'], f'先确认{LABELS[component]}是否适用于本次服务。',
                '核对服务源码、部署记录或负责人确认，并以新环境版本记录来源；缺少记录不等于组件存在或不存在。',
                [fact['evidence_id']], fact=fact, intent='confirm_component')
        elif state == 'present':
            p = fact['parameters']
            if component == 'database':
                action, verify = '补采已记录数据库依赖的调用与等待证据。', '保持请求和数据，关联服务调用、查询耗时与等待；只有关联证据同步恶化才支持该候选，不直接修改查询或生产配置。'
                code = 'collect_database_observations'
            elif component == 'downstream':
                action, verify = '补采已记录下游调用的耗时、错误和关联信息。', '保持流量，关联本服务步骤与下游调用；未出现同步恶化时保留其他候选，不能由接口慢直接归因下游。'
                code = 'collect_downstream_observations'
            elif component == 'gc_runtime':
                action, verify = '补采已记录运行时的暂停与分配证据。', '保持流量并对齐暂停、分配和请求耗时窗口；内存占用高本身不证明 GC、泄漏或内存耗尽。'
                code = 'collect_gc_observations'
            elif component == 'bounded_work_slots':
                code, action = 'observe_work_slot_rejections', '核对工作槽保护是否在本轮触发。'
                verify = f'人工记录的工作槽上限为 {p["limit"]}；保持配置，补采活跃槽与拒绝响应并对齐步骤失败。未观察到拒绝或占满时不能判定保护已触发。'
            elif component == 'bounded_connection_slots':
                code, action = 'observe_connection_slot_rejections', '核对连接槽保护与连接失败是否关联。'
                verify = f'人工记录的连接槽上限为 {p["limit"]}；保持配置，补采活跃连接、关闭事件与连接失败。配置上限不等于本轮实测峰值。'
            elif component == 'cpu_wall_time_work':
                code, action = 'inspect_cpu_work', '对照有界 CPU 工作与请求耗时。'
                verify = f'人工记录的墙钟截止为 {p["duration_ms"]} ms，不代表消耗等量 CPU 时间；保持请求，补采服务本地耗时、实际配额与节流，确认端到端延迟是否同步变化。'
            else:
                code, action = 'observe_memory_recovery', '核对共享 TTL 分配与资源恢复。'
                refresh = '请求会续期' if p['refresh_on_request'] else '重复请求不续期'
                verify = f'人工记录为共享 {p["bytes"]} 字节、TTL {p["ttl_seconds"]} 秒、{refresh}；保持配置，对齐分配与过期释放。共享分配不按请求累加，未按期回落需补采对象与释放证据，不能直接判定泄漏。'
            add(code, fact['domain'], action, verify, refs, fact=fact, intent='collect_evidence')
    for domain in ('cpu', 'memory', 'storage', 'network'):
        refs = []
        for service in (evidence.get('resource_monitoring') or {}).get('services', []):
            for metric in service.get('metrics', []):
                peak = metric.get('peak')
                if METRIC_DOMAINS.get(metric.get('key')) == domain and type(peak) in (int, float) and math.isfinite(peak):
                    refs.append(metric['evidence_id'])
        add('inspect_' + domain + '_observation', domain, '核对环境级资源观测与采样口径。',
            '当前监控尚未绑定环境服务键，只能描述所选采集范围；先核对实例归属、采样窗口和分母，再补服务关联证据，不能替代服务根因证明。', refs)
    return options


def validate_recommendations(rows, evidence):
    if not isinstance(rows, list) or not 1 <= len(rows) <= 10:
        raise ValueError('AI诊断建议数量无效')
    options = recommendation_options(evidence)
    result = []
    fields = {'priority', 'domain', 'service_key', 'intent', 'action_code', 'fact_ids', 'evidence_ids'}
    for row in rows:
        if not isinstance(row, dict) or set(row) != fields or row['priority'] not in ('high', 'medium', 'low'):
            raise ValueError('AI诊断建议必须使用逐条结构契约，不能传自由动作文本')
        option = next((o for o in options if all(row[k] == o[k] for k in ('action_code', 'domain', 'service_key', 'intent', 'fact_ids'))), None)
        refs = row['evidence_ids']
        if not option or not isinstance(refs, list) or not 1 <= len(refs) <= 20 or any(not isinstance(ref, str) or ref not in option['evidence_ids'] for ref in refs):
            raise ValueError('AI诊断建议的组件状态、服务范围或证据领域不匹配')
        result.append({**copy.deepcopy(row), 'action': option['action'], 'verification': option['verification']})
    return result


def fallback_recommendations(evidence):
    option = recommendation_options(evidence)[0]
    row = {'priority': 'medium', **{k: option[k] for k in ('domain', 'service_key', 'intent', 'action_code', 'fact_ids', 'evidence_ids')}}
    return validate_recommendations([row], evidence)
