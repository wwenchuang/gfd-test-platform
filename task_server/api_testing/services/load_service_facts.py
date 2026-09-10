"""Explicit operator-recorded service facts; never discover topology from prose."""
import copy
import hashlib
from ..executor import redact


COMPONENTS = {'database': 'database', 'downstream': 'downstream', 'gc_runtime': 'memory'}
BEHAVIORS = {'bounded_work_slots': 'application_pool', 'bounded_connection_slots': 'application_pool',
             'cpu_wall_time_work': 'cpu', 'shared_ttl_allocation': 'memory'}
STATES = {'present', 'absent', 'unknown'}
PARAMETERS = {
    'bounded_work_slots': {'limit'}, 'bounded_connection_slots': {'limit'},
    'cpu_wall_time_work': {'duration_ms', 'endpoints'},
    'shared_ttl_allocation': {'bytes', 'ttl_seconds', 'refresh_on_request', 'endpoints'},
}


def _require(condition, reason):
    if not condition:
        raise ValueError('服务能力事实：' + reason)


def normalize_service_facts(value):
    _require(isinstance(value, dict) and not set(value) - {'version', 'components', 'behaviors'}, '结构无效')
    _require(type(value.get('version')) is int and value['version'] == 1, '仅支持版本 1')
    result = {'version': 1, 'components': [], 'behaviors': []}
    for field, known, key in [('components', COMPONENTS, 'key'), ('behaviors', BEHAVIORS, 'kind')]:
        rows = value.get(field, [])
        _require(isinstance(rows, list) and len(rows) <= len(known), '条目数量无效')
        seen = set()
        for row in rows:
            _require(isinstance(row, dict), '条目必须为对象')
            name, state = row.get(key), row.get('state')
            _require(isinstance(name, str) and name in known and name not in seen, '类型无效或重复')
            _require(isinstance(state, str) and state in STATES, '状态无效')
            seen.add(name)
            params = PARAMETERS[name] if field == 'behaviors' and state == 'present' else set()
            _require(not set(row) - ({key, 'state', 'source'} | params) and params <= set(row), '行为参数与状态不匹配')
            item = {key: name, 'state': state}
            source = row.get('source')
            _require(state == 'unknown' or isinstance(source, dict), '已确认状态必须填写人工来源')
            if source is not None:
                _require(isinstance(source, dict) and not set(source) - {'kind', 'reference', 'recorded_by_operator'}, '来源结构无效')
                _require(source.get('kind') in ('operator_declaration', 'source_review', 'runtime_observation'), '来源类型无效')
                ref = source.get('reference')
                _require(isinstance(ref, str) and 0 < len(ref.strip()) <= 500, '来源引用必须填写且不超过 500 字符')
                item['source'] = {'kind': source['kind'], 'reference': redact(ref.strip()), 'recorded_by_operator': True}
            for param in params:
                v = row[param]
                if param == 'endpoints':
                    _require(isinstance(v, list) and 1 <= len(v) <= 20 and all(
                        isinstance(p, str) and 1 <= len(p) <= 500 and p.startswith('/') and not p.startswith('//')
                        and not any(c in p for c in ('?', '#', '\n', '\r')) for p in v), '接口范围必须为 1 至 20 条不带查询的相对路径')
                    item[param] = list(dict.fromkeys(v))
                elif param == 'refresh_on_request':
                    _require(type(v) is bool, '续期行为必须明确为是或否')
                    item[param] = v
                else:
                    maximum = {'limit': 1000000, 'duration_ms': 3600000, 'bytes': 1099511627776, 'ttl_seconds': 86400}[param]
                    _require(type(v) is int and 1 <= v <= maximum, '数值参数超出范围')
                    item[param] = v
            result[field].append(item)
    return result


def snapshot_service_facts(service_metadata, definition):
    """Freeze only declared facts for services/endpoints in this admitted scenario."""
    services = {}
    for step in definition.get('steps', []):
        request = step.get('request') or {}
        if not request:
            continue
        key = request.get('service', 'default')
        item = services.setdefault(key, {'step_ids': [], 'paths': []})
        item['step_ids'].append(step['id'])
        item['paths'].append(request.get('path'))
    for key, item in services.items():
        raw = (service_metadata.get(key) or {}).get('load_service_facts')
        normalized = normalize_service_facts(raw) if raw is not None else {'components': [], 'behaviors': []}
        item['components'] = copy.deepcopy(normalized['components'])
        item['behaviors'] = []
        for row in normalized['behaviors']:
            matching_steps = [step_id for step_id, path in zip(item['step_ids'], item['paths'])
                              if 'endpoints' not in row or path in row['endpoints']]
            if matching_steps:
                item['behaviors'].append({**copy.deepcopy(row), 'step_ids': matching_steps})
        del item['paths']
    return {'version': 1, 'services': services}


def fact_catalog(report):
    """A missing historical snapshot stays unknown; never look up live settings."""
    snapshot = ((report.get('evidence') or {}).get('environment_snapshot') or {}).get('service_facts') or {}
    services = snapshot.get('services') if snapshot.get('version') == 1 else None
    services = services if isinstance(services, dict) and services else {None: {}}
    result = []
    selected = list(services.items())[:30]
    if len(services) > 30:
        selected.append((None, {}))  # Truncated coverage cannot establish universal absence.
    for key, service in selected:
        suffix = hashlib.sha256(str(key).encode()).hexdigest()[:16]
        for field, known, name_key in [('components', COMPONENTS, 'key'), ('behaviors', BEHAVIORS, 'kind')]:
            rows = {r[name_key]: r for r in service.get(field, []) if isinstance(r, dict) and r.get(name_key) in known}
            for component, domain in known.items():
                row = rows.get(component, {})
                result.append({'evidence_id': f'fact.{suffix}.{component}', 'domain': domain, 'component': component,
                               'service_key': key, 'step_ids': list(row.get('step_ids', []) if field == 'behaviors' and 'endpoints' in row else service.get('step_ids', [])),
                               'state': row.get('state', 'unknown'), 'source': copy.deepcopy(row.get('source')),
                               'parameters': {k: copy.deepcopy(v) for k, v in row.items() if k not in {name_key, 'state', 'source', 'step_ids'}}})
    return result
