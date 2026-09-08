"""Bounded background collection of immutable host monitoring evidence.

No business requests, zero filling, raw credentials or service-CPU inference.
PromQL evaluation timestamps and exporter sample timestamps remain distinct.
"""
import copy
import json
import math
import time
from datetime import datetime, timezone

from sqlalchemy import select
from ..models.load_testing import ApiLoadRun
from .load_monitoring_prometheus import build_query, metric_selector, HOST_RATES, POSTGRES_METRICS, DISK_LATENCIES, template_version

MAX_SERVICES = 10
MAX_OUTPUT_POINTS = 40000
MAX_COLLECTION_SECONDS = 90
MAX_WINDOW_SECONDS = 86400
INGESTION_GRACE_SECONDS = 30
START_TIMEOUT_SECONDS = 900


def _epoch(value):
    if value is None:
        return None
    return (value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value).timestamp()


def _iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _unavailable_services(snapshots, message, state='failed'):
    return [{'revision_id': item.get('revision_id'), 'required': bool(item.get('required')),
             'name': item.get('name', '监控服务'), 'scope': item.get('metric_scope', item.get('deployment', 'host')), 'metrics': [],
             'state': state, 'message': message} for item in snapshots]


def _identity(labels):
    return tuple(sorted((k, v) for k, v in labels.items() if k != '__name__'))


def _queries(metric, definition):
    query = build_query(metric, definition['deployment'], definition['labels'])
    selector = metric_selector(definition['deployment'], definition['labels'])
    max_age = max(60, definition['step_seconds'] * 3)
    if metric in ('pod_ready', 'pod_restarts_increase_2m'):
        raw = ('kube_pod_status_ready{' + selector + ',condition="true"}' if metric == 'pod_ready' else
               'kube_pod_container_status_restarts_total{' + selector + '}')
        freshness = 'timestamp(' + raw + ')'
        if metric == 'pod_restarts_increase_2m':
            freshness += ' and (timestamp(' + raw + ' offset 2m) >= time() - 120 - ' + str(max_age) + ')'
        return query, freshness, None
    if metric.startswith('filesystem_used_') or metric in DISK_LATENCIES:
        if metric in DISK_LATENCIES:
            left, right = (name + '{' + selector + '}' for name in DISK_LATENCIES[metric])
            capacity = 'rate(' + right + '[2m])'
        else:
            left, right = ('node_filesystem_' + name + '_bytes{' + selector + '}' for name in ('size', 'free'))
            capacity = left
        a, b = 'timestamp(' + left + ')', 'timestamp(' + right + ')'
        freshness = '(' + a + ' + ' + b + ' - abs(' + a + ' - ' + b + ')) / 2'
        if metric in DISK_LATENCIES:
            for raw in (left, right):
                freshness += ' and (timestamp(' + raw + ' offset 2m) >= time() - 120 - ' + str(max_age) + ')'
        return query, freshness, capacity
    if metric in POSTGRES_METRICS:
        raw = POSTGRES_METRICS[metric][0] + '{' + selector + '}'
        freshness = 'timestamp(' + raw + ')'
        if metric != 'postgres_connections':
            freshness += ' and (timestamp(' + raw + ' offset 2m) >= time() - 120 - ' + str(max_age) + ')'
        return query, freshness, None
    if metric in HOST_RATES or metric in ('cpu_cores', 'memory_working_set_bytes'):
        raw = ((HOST_RATES[metric][0] if metric in HOST_RATES else
                'container_cpu_usage_seconds_total' if metric == 'cpu_cores' else
                'container_memory_working_set_bytes') + '{' + selector + '}')
        freshness = 'timestamp(' + raw + ')'
        if metric != 'memory_working_set_bytes':
            history = '(timestamp(' + raw + ' offset 2m) >= time() - 120 - ' + str(max_age) + ')'
            fresh_raw = raw + ' and ' + history
            freshness = 'timestamp(' + raw + ') and ' + history
            if metric == 'cpu_cores':
                # Every current CPU series must have history; partial cores cannot qualify.
                freshness = ('min without (cpu) (timestamp(' + raw + ')) and '
                             '(count without (cpu) (' + fresh_raw + ') == count without (cpu) (' + raw + '))')
        capacity = None
        if metric == 'cpu_cores':
            quota = 'container_spec_cpu_quota{' + selector + '}'
            period = 'container_spec_cpu_period{' + selector + '}'
            capacity = ('(' + quota + ' > 0) / (' + period + ' > 0) and '
                        '(timestamp(' + quota + ') >= time() - ' + str(max_age) + ') and '
                        '(timestamp(' + period + ') >= time() - ' + str(max_age) + ')')
        if metric == 'memory_working_set_bytes':
            limit = 'container_spec_memory_limit_bytes{' + selector + '}'
            # cAdvisor unlimited sentinel is near 2**63; never call it a real quota.
            capacity = ('((' + limit + ' > 0) < 1e18) and (timestamp(' + limit + ') >= time() - ' + str(max_age) + ')')
        return query, freshness, capacity
    if metric == 'cpu_percent':
        raw = 'node_cpu_seconds_total{' + selector + ',mode="idle"}'
        freshness = 'min without (cpu, mode) (timestamp(' + raw + '))'
        cores = 'count without (cpu, mode) (' + raw + ')'
        rate_cores = 'count without (cpu, mode) (rate(' + raw + '[2m]))'
        # A newly appearing core can lack two samples for rate; do not average
        # the remaining cores and scale that partial average by the full count.
        max_age = max(60, definition['step_seconds'] * 3)
        # Cold counters can produce a rate extrapolated across almost the entire
        # two-minute window. Require evidence for each CURRENT core at its start.
        # Intersect core labels before counting so a replaced core cannot qualify.
        history = '(timestamp(' + raw + ' offset 2m) >= time() - 120 - ' + str(max_age) + ')'
        historical_cores = 'count without (cpu, mode) (' + raw + ' and ' + history + ')'
        capacity = (cores + ' and (' + rate_cores + ' == ' + cores + ')'
                    + ' and (' + historical_cores + ' == ' + cores + ')')
    else:
        available = 'timestamp(node_memory_MemAvailable_bytes{' + selector + '})'
        total = 'timestamp(node_memory_MemTotal_bytes{' + selector + '})'
        # timestamp drops metric names; evaluating both raw metrics in one vector
        # creates duplicate label sets. Binary arithmetic also requires both metrics.
        freshness = '(' + available + ' + ' + total + ' - abs(' + available + ' - ' + total + ')) / 2'
        capacity = 'node_memory_MemTotal_bytes{' + selector + '}'
    return query, freshness, capacity


def _metadata(key, deployment):
    if key in ('cpu_percent', 'memory_percent'):
        return {}
    if key in ('pod_ready', 'pod_restarts_increase_2m'):
        ready = key == 'pod_ready'
        return {'label': 'Pod 就绪状态' if ready else '容器重启增量（前2分钟）',
                'unit': 'state' if ready else '次', 'scope': 'pod_state',
                'denominator': 'not_applicable', 'denominator_unit': '', 'used_unit': 'state' if ready else '次',
                'window_seconds': None if ready else 120,
                'semantics': ('kube-state-metrics 的 Pod Ready 条件：1 已就绪，0 未确认就绪（含 false / unknown）；缺失为未知，不是就绪率或可用率' if ready else
                              '各普通容器前2分钟 increase 估计增量，自动处理计数器重置，可能为小数；窗口含执行前历史且相邻窗口重叠，不可相加，不是本轮重启总数；保留 Pod UID，不跨 Pod 聚合，不含 init 容器')}
    if key in HOST_RATES:
        return {'label': HOST_RATES[key][1], 'unit': HOST_RATES[key][2], 'scope': 'host',
                'denominator': 'not_applicable', 'denominator_unit': '', 'used_unit': HOST_RATES[key][2],
                'semantics': '主机逐设备两分钟平均速率；保留设备标签，不相加物理盘/分区或虚拟网卡；不是空间占用或单服务资源'}
    if key in POSTGRES_METRICS:
        return {'label': POSTGRES_METRICS[key][1], 'unit': POSTGRES_METRICS[key][2], 'scope': 'postgres',
                'denominator': 'not_applicable', 'denominator_unit': '', 'used_unit': POSTGRES_METRICS[key][2],
                'semantics': 'postgres_exporter pg_stat_database，精确实例和数据库；连接包含空闲连接，提交/回滚使用两分钟 rate，不等于业务链路成功/失败'}
    if key in DISK_LATENCIES:
        return {'label': '磁盘平均读取延迟' if key == 'disk_read_latency_ms' else '磁盘平均写入延迟',
                'unit': 'ms', 'scope': 'host', 'denominator': 'disk_operations_per_second',
                'denominator_unit': 'IOPS', 'used_unit': 'ms',
                'semantics': '逐设备两分钟 rate(累计 I/O 耗时) / rate(完成操作数)；平均延迟不是 P95，零操作时延迟未知，不填零'}
    if key.startswith('filesystem_used_'):
        return {'label': '文件系统空间使用率' if key.endswith('percent') else '文件系统已用空间',
                'unit': '%' if key.endswith('percent') else 'bytes', 'scope': 'host',
                'denominator': 'filesystem_size_bytes', 'denominator_unit': 'bytes', 'used_unit': 'bytes',
                'semantics': '逐文件系统 (size - free) / size，保留设备、挂载点和类型；含保留空间口径差异，空间占用不是 I/O 压力，不合并重复挂载'}
    cpu = key == 'cpu_cores'
    return {'label': '容器 CPU 使用核数' if cpu else '容器内存 working set',
            'unit': 'cores' if cpu else 'bytes', 'scope': deployment,
            'denominator': 'container_cpu_quota_cores' if cpu else 'container_memory_limit_bytes',
            'denominator_unit': 'cores' if cpu else 'bytes', 'used_unit': 'cores' if cpu else 'bytes',
            'semantics': ('两分钟 rate；CPU 占比只使用同一容器新鲜的正 quota / period，无额度时仍显示实际核数' if cpu else
                          '容器 working set，不是 RSS；占比只使用同一容器新鲜有限的memory limit，未知或无限额时仅显示字节数')}


class LoadMonitoringCollectionService:
    def __init__(self, session_factory, *, monitoring_service, now=None):
        self.session_factory = session_factory
        self.monitoring_service = monitoring_service
        self.now = now or (lambda: datetime.now(timezone.utc))

    def _metric(self, client, definition, key, start, end):
        step = definition['step_seconds']
        query, freshness_query, capacity_query = _queries(key, definition)
        series = client.query_range(query, start, end, step)
        freshness = client.query_range(freshness_query, start, end, step)
        capacity = client.query_range(capacity_query, start, end, step) if capacity_query else []
        percentage = key in ('cpu_percent', 'memory_percent', 'filesystem_used_percent')
        fresh_map = {_identity(s['labels']): {p['timestamp']: p['value'] for p in s['points']} for s in freshness}
        cap_map = {_identity(s['labels']): {p['timestamp']: p['value'] for p in s['points']} for s in capacity}
        max_age = max(60, step * 3)
        values, timestamps, source_times = [], [], set()
        good, expected = 0, 0
        gaps, scrape_gaps = [], []
        for item in series:
            identity = _identity(item['labels'])
            expected += int((end - start) // step) + 1
            instance_times, instance_sources = [], set()
            for p in item['points']:
                t = p['timestamp']
                source = fresh_map.get(identity, {}).get(t)
                denominator = cap_map.get(identity, {}).get(t)
                valid = (_finite(source) and 0 <= t - source <= max_age
                         and _finite(p['value'])
                         and (key != 'pod_ready' or p['value'] in (0, 1))
                         and (key != 'pod_restarts_increase_2m' or p['value'] >= 0)
                         and (not percentage or (_finite(denominator) and denominator > 0)))
                p['source_timestamp'] = source if _finite(source) else None
                p['denominator_value'] = denominator if _finite(denominator) and denominator > 0 else None
                p['missing_reason'] = ('no_operations' if key in DISK_LATENCIES and denominator == 0 and _finite(source) and 0 <= t - source <= max_age else None)
                if valid:
                    good += 1
                    values.append(p['value'])
                    timestamps.append(t)
                    source_times.add(source)
                    instance_times.append(t)
                    instance_sources.add(source)
                    p['used_value'] = p['value'] * denominator / 100 if percentage else p['value']
                    p['utilization_percent'] = (100 * p['value'] / denominator if key in ('cpu_cores', 'memory_working_set_bytes') and _finite(denominator) and denominator > 0 else None)
                else:
                    p['value'] = None
                    p['used_value'] = None
                    p['utilization_percent'] = None
            boundaries = [start] + sorted(set(instance_times)) + [end]
            gaps.extend(b - a for a, b in zip(boundaries, boundaries[1:]))
            scrapes = sorted(instance_sources)
            scrape_gaps.extend(b - a for a, b in zip(scrapes, scrapes[1:]))
        # Coverage counts evaluation positions only; source cadence is separately visible.
        metadata = _metadata(key, definition['deployment'])
        return dict({'key': key, 'label': '主机 CPU 使用率' if key == 'cpu_percent' else '主机内存使用率',
                'unit': '%', 'scope': 'host', 'denominator': 'host_cpu_cores' if key == 'cpu_percent' else 'host_memory_total_bytes',
                'denominator_unit': 'cores' if key == 'cpu_percent' else 'bytes',
                'used_unit': 'cores' if key == 'cpu_percent' else 'bytes',
                'semantics': '两分钟 CPU rate，idle 以外均计忙；要求各当前核心具备两分钟前的新鲜历史采样' if key == 'cpu_percent' else 'MemTotal - MemAvailable',
                'series': series, 'peak': max(values) if values else None,
                'average': sum(values) / len(values) if values else None,
                'coverage': {'valid_evaluations': good, 'expected_evaluations': expected,
                             'valid_ratio': good / expected if expected else 0,
                             'start': min(timestamps) if timestamps else None,
                             'end': max(timestamps) if timestamps else None,
                             'max_gap_seconds': max(gaps) if gaps else None,
                             'distinct_source_timestamps': len(source_times),
                             'max_observed_scrape_gap_seconds': max(scrape_gaps) if scrape_gaps else None,
                             'freshness_limit_seconds': max_age},
                'query_step_seconds': step, 'timestamp_kind': 'promql_evaluation',
                'template_version': template_version(definition['deployment'])}, **metadata)

    def collect_window(self, snapshots, *, start, end, terminal, now):
        # Prometheus stores millisecond timestamps; a whole-second grid is stable
        # across retries and avoids a rounded response falling outside a probe.
        start, end = math.floor(start), math.floor(end)
        result = {'state': 'collecting', 'services': [], 'window_start': _iso(start),
                  'window_end': _iso(end), 'updated_at': _iso(now), 'terminal': terminal, 'complete': False}
        if not snapshots:
            return dict(result, state='not_selected', terminal=True, complete=True)
        if len(snapshots) > MAX_SERVICES or end < start or end - start > MAX_WINDOW_SECONDS:
            return dict(result, state='failed', terminal=True, message='监控范围超过后台采集上限',
                        services=_unavailable_services(snapshots, '监控范围超过后台采集上限'))
        deadline = time.monotonic() + MAX_COLLECTION_SECONDS
        output_points = 0
        point_budget_exhausted = False
        for snapshot in snapshots:
            item = {'revision_id': snapshot.get('revision_id'), 'required': bool(snapshot.get('required')),
                    'name': snapshot.get('name', '监控服务'), 'scope': snapshot.get('metric_scope', snapshot.get('deployment', 'host')), 'metrics': [], 'state': 'failed'}
            result['services'].append(item)
            try:
                if point_budget_exhausted:
                    item['message'] = '监控采集总点数超过上限'
                    continue
                if time.monotonic() >= deadline:
                    item['message'] = '本次采集时间预算已用尽'
                    continue
                definition = self.monitoring_service._definition_for_revision(snapshot['revision_id'])
                item.update(name=definition['name'], source_url=definition['source_url'],
                            labels=copy.deepcopy(definition['labels']), step_seconds=definition['step_seconds'], scope=definition['deployment'])
                client = self.monitoring_service._client_for_revision(snapshot['revision_id'])
                for key in definition['metrics']:
                    if time.monotonic() >= deadline:
                        raise TimeoutError()
                    metric = self._metric(client, definition, key, start, end)
                    points = sum(len(series['points']) for series in metric['series'])
                    if output_points + points > MAX_OUTPUT_POINTS:
                        point_budget_exhausted = True
                        raise OverflowError()
                    output_points += points
                    item['metrics'].append(metric)
                valid = bool(item['metrics']) and all(m['coverage']['valid_ratio'] >= 1 for m in item['metrics'])
                item['instance_count'] = len({(s['labels'].get('instance'), s['labels'].get('namespace'), s['labels'].get('pod'), s['labels'].get('id'), s['labels'].get('container'), s['labels'].get('datname')) for m in item['metrics'] for s in m['series']})
                if definition['deployment'] == 'pod_state':
                    item['instance_count'] = len({tuple(s['labels'].get(k) for k in ('instance', 'namespace', 'pod', 'uid')) for m in item['metrics'] for s in m['series']})
                item['state'] = ('completed' if terminal else 'collecting') if valid else 'missing'
                item['message'] = (('主机维度监控；不能据此判断单个服务进程资源' if definition['deployment'] == 'host' else 'PostgreSQL 指定数据库指标；事务速率不等于业务链路吞吐' if definition['deployment'] == 'postgres' else 'kube-state-metrics 指定 Pod 状态；就绪为0/1，重启仅为前2分钟滚动估计增量，不是本轮总数' if definition['deployment'] == 'pod_state' else '容器实际资源；Pod 按容器分列，未聚合为整个服务；额度未知时不计算百分比') if valid
                                   else '部分指标缺失、无活动样本、样本过期或覆盖不完整，不能按零值判断')
            except OverflowError:
                item.update(state='failed', message='监控采集总点数超过上限，未保存超限数据')
            except Exception:
                # Never propagate source exception messages, URLs with tokens or response bodies.
                item.update(state='failed', message='监控采集失败，请检查授权、只读连接和指标匹配')
        result['complete'] = terminal and all(s['state'] == 'completed' for s in result['services'])
        if terminal:
            result['state'] = 'completed' if result['complete'] else ('partial' if any(s['metrics'] for s in result['services']) else 'failed')
        return result

    def probe(self, snapshots):
        """Freshness-aware bounded prestart check; no load is dispatched."""
        now = _epoch(self.now())
        return self.collect_window(snapshots, start=now, end=now, terminal=True, now=now)

    def collect(self, run_id):
        with self.session_factory.begin() as session:
            run = session.get(ApiLoadRun, run_id)
            if run is None:
                return {'state': 'failed', 'terminal': True, 'complete': False, 'services': [], 'message': '执行不存在'}
            previous = copy.deepcopy((run.summary or {}).get('monitoring') or {})
            if previous.get('terminal'):
                return previous
            config = copy.deepcopy((run.configuration or {}).get('monitoring') or {})
            started, finished = _epoch(run.started_at), _epoch(run.finished_at)
            state = run.state
            created = _epoch(getattr(run, 'created_at', None))
        now = _epoch(self.now())
        if started is None:
            pending = (state in {'starting', 'queued'} and created is not None
                       and 0 <= now - created < START_TIMEOUT_SECONDS)
            message = '等待执行实际开始；最长等待15分钟' if pending else '执行尚无开始时间或启动等待已超时'
            result = {'state': 'collecting' if pending else 'failed', 'terminal': not pending,
                      'complete': False, 'services': _unavailable_services(config.get('services') or [], message,
                                                                          'collecting' if pending else 'failed'),
                      'message': message, 'updated_at': _iso(now)}
        else:
            before = min(3600, max(0, int(config.get('before_seconds', 60))))
            after = min(3600, max(0, int(config.get('after_seconds', 60))))
            # A broken run state cannot leave monitoring collecting forever.
            exhausted = now - started > MAX_WINDOW_SECONDS - before
            terminal = exhausted or (finished is not None and now >= finished + after + INGESTION_GRACE_SECONDS)
            if state in {'finished', 'failed', 'cancelled'} and finished is None:
                terminal = True
            end = min(now, finished + after) if finished is not None else now
            result = self.collect_window(config.get('services') or [], start=max(0, started - before), end=end,
                                         terminal=terminal, now=now)
            result['started_at'] = _iso(started)
            result['ingestion_grace_seconds'] = INGESTION_GRACE_SECONDS
        with self.session_factory.begin() as session:
            run = session.scalar(select(ApiLoadRun).where(ApiLoadRun.id == run_id).with_for_update())
            if run is None:
                return result
            existing = (run.summary or {}).get('monitoring') or {}
            # Do not let overlapping workers replace a final or newer snapshot.
            if existing.get('terminal') or existing.get('updated_at', '') > result.get('updated_at', ''):
                return copy.deepcopy(existing)
            run.summary = dict(run.summary or {}, monitoring=copy.deepcopy(result))
        return result
