"""Bounded coverage inventory, not a bottleneck verdict or execution policy.

Only existing report contracts are recognized. In particular, absent dependency
metadata does not establish that a database or downstream is not applicable.
"""
import math
import re
from .load_service_facts import fact_catalog
from .load_recommendation_contract import recommendation_options


# Current collectors expose these signals, not SQL traces, GC or pool telemetry.
_DOMAINS = (
    ('cpu', 'CPU', {'cpu_percent', 'cpu_cores'},
     '尚未获得有效 CPU 采样。',
     'CPU 使用量不包含节流、运行队列或调用栈，不能确认计算瓶颈。',
     '补采服务 CPU 配额、节流与步骤耗时；保持配置，核对采样窗口及服务归属后比较性能变化。'),
    ('memory', '内存', {'memory_percent', 'memory_working_set_bytes'},
     '尚未获得有效内存采样。',
     '内存占用不包含 GC 暂停、分配速率或泄漏证据，缓存增长不能直接视为泄漏。',
     '先确认服务内存分配行为与运行时，再补采内存配额及资源恢复趋势；占用不能直接证明泄漏。'),
    ('application_pool', '应用排队与连接池', set(),
     '未配置线程池、连接池等待或应用队列采集。', '',
     '先确认是否存在池或队列及其类型，再采集活跃数、上限、等待时长与超时；保持配置核对关联。'),
    ('database', '数据库', {'postgres_connections', 'postgres_commits_per_second', 'postgres_rollbacks_per_second'},
     '未配置有效数据库监控，无法判断是否存在数据库依赖。',
     '连接数与事务速率不包含慢 SQL、锁等待或查询调用链，不能确认数据库根因。',
     '先确认数据库依赖并记录来源；已确认依赖后再补采调用与等待证据，不从接口耗时推断数据库根因。'),
    ('storage', '磁盘与存储', {'disk_read_bytes_per_second', 'disk_write_bytes_per_second', 'disk_read_iops', 'disk_write_iops', 'disk_read_latency_ms', 'disk_write_latency_ms'},
     '未配置有效存储采样。',
     '主机磁盘吞吐或平均延迟缺少服务归属、队列与等待证据，不能确认存储根因。',
     '补采磁盘队列、IO 等待与服务归属；保持配置，核对存储等待与业务耗时是否关联。'),
    ('network', '网络', {'network_receive_bytes_per_second', 'network_transmit_bytes_per_second'},
     '未配置有效网络采样。',
     '主机收发速率不包含重传、往返延迟或带宽上限，不能证明网络没有瓶颈。',
     '补采重传、往返延迟、带宽上限与请求连接耗时；保持服务和流量，核对本地及端到端耗时。'),
    ('downstream', '下游依赖', set(),
     '未配置下游调用链或依赖服务指标；接口耗时无法单独归因到下游。', '',
     '先确认下游依赖并记录来源，再采集调用耗时、错误和限流；保持配置核对关联。'),
    ('load_generator', '压力机', set(),
     '尚未获得有效压力机运行资源采样。', '',
     '核对实际压力、丢弃迭代、节点配额和运行资源；保持当前配置，先确认每个节点证据完整。'),
    ('test_data', '测试数据', set(),
     '尚未获得有效步骤业务断言证据。', '',
     '补充账号分配、数据分布与资源清理证据；保持当前数据和压力，核对业务失败位置。'),
)


def _object(value):
    return value if isinstance(value, dict) else {}


def _rows(value, limit):
    return value[:limit] if isinstance(value, list) else []


def _finite(value):
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def _reference(prefix, value):
    # Return only bounded identifiers, never names, labels, queries or error text.
    return prefix + value if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,120}', value) else None


def build_bottleneck_evidence(report):
    """Return at most nine deterministic coverage rows without mutating report."""
    report = _object(report)
    result = []
    by_domain = {}
    for domain, label, keys, missing, limitation, verification in _DOMAINS:
        row = {'domain': domain, 'label': label, 'status': 'missing',
               'evidence_ids': [], 'limitations': [missing], 'next_verification': verification}
        result.append(row)
        by_domain[domain] = row

    truncated = False

    def bounded(value, limit):
        nonlocal truncated
        if isinstance(value, list) and len(value) > limit:
            truncated = True
        return _rows(value, limit)

    services = bounded(_object(report.get('monitoring')).get('services'), 10)
    for service in services:
        service = _object(service)
        reference = _reference('monitoring.', service.get('revision_id'))
        for metric in bounded(service.get('metrics'), 30):
            metric = _object(metric)
            domain_spec = next((item for item in _DOMAINS if isinstance(metric.get('key'), str) and metric['key'] in item[2]), None)
            if not domain_spec:
                continue
            series = bounded(metric.get('series'), 20)
            counts = [sum(_finite(_object(p).get('value')) for p in bounded(_object(s).get('points'), 5000)) for s in series]
            if not any(counts):
                continue
            row = by_domain[domain_spec[0]]
            if row['status'] == 'missing':
                row.update(status='partial', limitations=[domain_spec[4]])
            if reference and reference not in row['evidence_ids']:
                row['evidence_ids'].append(reference)
            coverage = _object(metric.get('coverage')).get('valid_ratio')
            if service.get('state') != 'completed' or not all(n >= 3 for n in counts) or not _finite(coverage) or coverage < .95:
                row['limitations'].append('采样数量、覆盖率或采集终态不足，需先补齐观察窗口。')
            if service.get('scope') == 'host':
                row['limitations'].append('主机指标只能描述整机，不能直接归属到某个业务服务。')
            if domain_spec[0] in {'cpu', 'memory'}:
                row['limitations'].append('需逐实例核对配额与阶段采样；服务汇总覆盖率不能证明每个实例都完整。')

    agents = bounded(report.get('agents'), 20)
    generator = by_domain['load_generator']
    complete_agents = []
    any_runtime = False
    for agent in agents:
        agent = _object(agent)
        resources = _object(agent.get('load_generator_resources'))
        samples = [_object(s) for s in bounded(resources.get('samples'), 5000)]
        usable = [s for s in samples if all(_finite(s.get(k)) and 0 <= s[k] <= 100 for k in ('cpu_percent', 'memory_percent'))]
        if any(_finite(s.get(k)) and 0 <= s[k] <= 100 for s in samples for k in ('cpu_percent', 'memory_percent')):
            any_runtime = True
            ref = _reference('agent.', agent.get('id'))
            if ref:
                generator['evidence_ids'].append(ref)
        complete_agents.append(agent.get('state') == 'finished' and len(usable) >= 3 and len(usable) == len(samples) and not resources.get('dropped_samples'))
    if any_runtime:
        reached = _object(report.get('load_goal')).get('reached') is True
        complete = _object(report.get('evidence')).get('complete') is True
        dropped = _object(report.get('dropped_iterations')).get('count')
        all_agents_retained = len(_rows(report.get('agents'), 21)) <= 20
        generator['status'] = 'available' if all(complete_agents) and all_agents_retained and reached and complete and _finite(dropped) and dropped >= 0 else 'partial'
        generator['limitations'] = ['运行资源与达压数据可用于排查压力机；采样摘要不能独立证明瓶颈或排除节点网络限制。']
        if generator['status'] == 'partial':
            generator['limitations'].append('部分节点、实际压力、丢弃迭代或完整性证据不足，不能把缺失当作零占用。')

    data = by_domain['test_data']
    for step in bounded(report.get('steps'), 20):
        step = _object(step)
        rate = step.get('business_failure_rate')
        requests = step.get('requests')
        if _finite(requests) and requests > 0 and _finite(rate) and 0 <= rate <= 1:
            data['status'] = 'partial'
            ref = _reference('step.', step.get('id'))
            if ref:
                data['evidence_ids'].append(ref)
            data['limitations'] = ['步骤业务断言只能提示失败位置；缺少数据分布、账号隔离与热点关联，不能确认测试数据根因。']

    for row in result:
        if truncated:
            row['limitations'].append('清单按有界数量截取证据，未展示部分仍需核对，不能视为完整覆盖。')
            if row['status'] == 'available':
                row['status'] = 'partial'
        row['evidence_ids'] = list(dict.fromkeys(row['evidence_ids']))[:20]
        row['limitations'] = list(dict.fromkeys(row['limitations']))[:5]
    facts = fact_catalog(report)
    options = recommendation_options({'service_facts': facts, 'steps': [
        {'evidence_id': 'step.' + str(s.get('id'))} for s in _rows(report.get('steps'), 20) if isinstance(s, dict)]})
    for row in result:
        matching = [f for f in facts if f['domain'] == row['domain']]
        row['fact_ids'] = [f['evidence_id'] for f in matching]
        # A missing dependency is not an absent dependency. All selected services
        # must explicitly agree before a whole dependency domain is inapplicable.
        if row['domain'] in {'database', 'downstream'} and matching and all(f['state'] == 'absent' for f in matching):
            row.update(status='not_applicable', next_verification='本次已记录该组件不适用；架构更新后保存新环境版本再验证。',
                       limitations=['所选场景服务的人工来源记录均明确该组件不存在；此记录不是本轮自动探测结果。'])
            if row['evidence_ids']:
                row['limitations'].append('现有环境级监控未绑定这些服务，不能覆盖服务能力来源记录或证明服务依赖。')
        elif row['domain'] in {'database', 'downstream', 'application_pool', 'memory', 'cpu'}:
            choices = [o for o in options if o['domain'] == row['domain']]
            choices.sort(key=lambda o: o['intent'] == 'confirm_component')
            if choices:
                row['next_verification'] = choices[0]['action'] + ' ' + choices[0]['verification']
            elif row['domain'] == 'application_pool':
                row['next_verification'] = '本次已记录的工作槽与连接槽不适用；其他池或队列仍需独立确认，不能推断存在。'
            if row['domain'] == 'memory' and any(f['component'] == 'shared_ttl_allocation' and f['state'] == 'present' for f in matching):
                row['limitations'].insert(0, '共享 TTL 分配属于人工记录的有界行为，不按每请求累加；占用采样不能独立证明泄漏或内存耗尽。')
    return result
