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
from .load_monitoring_prometheus import build_query

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
             'name': item.get('name', '监控服务'), 'scope': 'host', 'metrics': [],
             'state': state, 'message': message} for item in snapshots]


def _identity(labels):
    return tuple(sorted((k, v) for k, v in labels.items() if k != '__name__'))


def _queries(metric, definition):
    query = build_query(metric, definition['deployment'], definition['labels'])
    selector = ','.join(k + '=' + json.dumps(v, ensure_ascii=False) for k, v in sorted(definition['labels'].items()))
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
        capacity = client.query_range(capacity_query, start, end, step)
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
                         and _finite(p['value']) and _finite(denominator) and denominator > 0)
                p['source_timestamp'] = source if _finite(source) else None
                p['denominator_value'] = denominator if _finite(denominator) and denominator > 0 else None
                if valid:
                    good += 1
                    values.append(p['value'])
                    timestamps.append(t)
                    source_times.add(source)
                    instance_times.append(t)
                    instance_sources.add(source)
                    p['used_value'] = p['value'] * denominator / 100
                else:
                    p['value'] = None
                    p['used_value'] = None
            boundaries = [start] + sorted(set(instance_times)) + [end]
            gaps.extend(b - a for a, b in zip(boundaries, boundaries[1:]))
            scrapes = sorted(instance_sources)
            scrape_gaps.extend(b - a for a, b in zip(scrapes, scrapes[1:]))
        # Coverage counts evaluation positions only; source cadence is separately visible.
        return {'key': key, 'label': '主机 CPU 使用率' if key == 'cpu_percent' else '主机内存使用率',
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
                'template_version': 'node-exporter-host-v1'}

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
                    'name': snapshot.get('name', '监控服务'), 'scope': 'host', 'metrics': [], 'state': 'failed'}
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
                            labels=copy.deepcopy(definition['labels']), step_seconds=definition['step_seconds'])
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
                item['instance_count'] = len({_identity(s['labels']) for m in item['metrics'] for s in m['series']})
                item['state'] = ('completed' if terminal else 'collecting') if valid else 'missing'
                item['message'] = ('主机维度监控；不能据此判断单个服务进程资源' if valid
                                   else '部分指标缺失、样本过期或覆盖不完整，不能按零值判断')
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
