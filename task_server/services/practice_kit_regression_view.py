"""Read the operator-published, app-scoped suite regression report."""
from datetime import datetime
import hashlib
import json
import re

from task_server.services import requirement_design as design
from task_server.services import requirement_evaluation_view as evaluation
from task_server.services.ui_execution_contract import AccessDenied


def _text(value):
    if (not isinstance(value, str) or not 1 <= len(value.strip()) <= 4000 or
            re.search(r'[\x00-\x08\x0b-\x0c\x0e-\x1f\x7f]|https?://|\bBearer\s+\S+|'
                      r'\b(?:Authorization|Cookie|password|access_token|api_key)\s*[:=]\s*\S+', value, re.I)):
        raise evaluation.Unavailable()
    return value.strip()


def _fields(row, names):
    return {key: _text(row[key]) for key in names.split()}


def _items(doc, name):
    rows = doc[name]
    if not isinstance(rows, list) or len(rows) > 500:
        raise evaluation.Unavailable()
    return rows


def _records(doc, group, fields):
    rows = [_fields(row, 'id ' + fields) for row in _items(doc, group)]
    if len({row['id'] for row in rows}) != len(rows):
        raise evaluation.Unavailable()
    return rows


TICKET_TYPE = '构建或配置待查'
TICKET_STATUS = '已登记 · 待核对'


def ticket_input(doc):
    fields = _fields(doc, 'run_id observation_id title observed evidence')
    scope = doc['scope']['app_ids']
    if (scope != ['com.kfb.model'] or not re.fullmatch(r'kit-e2e-[0-9]{8}-r[1-9][0-9]*', fields['run_id']) or
            not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', fields['observation_id'])):
        raise evaluation.Unavailable()
    key = (fields['run_id'] + '/' + fields['observation_id']).encode()
    return dict(fields, id='KIT-' + hashlib.sha256(key).hexdigest()[:24], scope={'app_ids': scope})


def ticket(allowed_apps, ticket_id):
    if not isinstance(ticket_id, str) or not re.fullmatch(r'KIT-[0-9a-f]{24}', ticket_id):
        raise ValueError('工单编号无效')
    raw = evaluation._published_bytes(ticket_id + '.json', subdirectories=('kit-tickets',), limit=128 * 1024)
    try:
        doc = json.loads(raw)
        fields = ticket_input(doc)
        scope = fields.pop('scope')['app_ids']
        if any(app not in allowed_apps for app in scope):
            raise AccessDenied('ticket_forbidden')
        if (doc['id'] != fields['id'] or fields['id'] != ticket_id or doc['type'] != TICKET_TYPE or
                doc['status'] != TICKET_STATUS or type(doc['schema_version']) is not int or doc['schema_version'] != 1):
            raise evaluation.Unavailable()
        created = datetime.fromisoformat(doc['created_at'].replace('Z', '+00:00'))
        if created.tzinfo is None:
            raise evaluation.Unavailable()
        return dict(fields, type=doc['type'], status=doc['status'], created_at=created.isoformat())
    except (KeyError, AttributeError, ValueError, TypeError, OverflowError, RecursionError):
        raise evaluation.Unavailable() from None


def _defects(doc, allowed_apps):
    rows = []
    for item in _items(doc, 'defects'):
        if 'ticket_id' in item:
            try:
                row = ticket(allowed_apps, item['ticket_id'])
            except (ValueError, design.Missing):
                raise evaluation.Unavailable() from None
            if row['run_id'] != doc['run_id']:
                raise evaluation.Unavailable()
        else:
            row = _fields(item, 'id title status observed evidence')
        rows.append(row)
    if len({row['id'] for row in rows}) != len(rows):
        raise evaluation.Unavailable()
    return rows


def published(allowed_apps):
    doc = evaluation._published_file('kit-regression.json')
    try:
        scope = doc['scope']['app_ids']
        if not isinstance(scope, list) or not scope or any(app not in allowed_apps for app in scope):
            raise AccessDenied('report_forbidden')
        if (type(doc['schema_version']) is not int or doc['schema_version'] != 1 or
                not re.fullmatch(r'kit-e2e-[0-9]{8}-r[1-9][0-9]*', doc['run_id'])):
            raise evaluation.Unavailable()
        timestamp = datetime.fromisoformat(doc['created_at'].replace('Z', '+00:00'))
        if timestamp.tzinfo is None:
            raise evaluation.Unavailable()
        cases = _records(doc, 'cases', 'title expected source')
        case_ids = {row['id'] for row in cases}
        checks = _records(doc, 'checks', 'case_id channel title status observed evidence')
        for row in checks:
            if (row['case_id'] not in case_ids or row['channel'] not in ('ui', 'api') or
                    row['status'] not in ('passed', 'failed', 'blocked', 'not_run')):
                raise evaluation.Unavailable()
        report = dict(_fields(doc, 'requirement summary run_id'), created_at=timestamp.isoformat(),
                      generation=_fields(doc['generation'], 'status observed evidence'), cases=cases, checks=checks,
                      defects=_defects(doc, allowed_apps),
                      knowledge=_records(doc, 'knowledge', 'title status observed evidence'),
                      limitations=[_text(item) for item in _items(doc, 'limitations')])
        if 'evidence' in doc:
            item = doc['evidence']
            size = item['bytes']
            if (type(size) is not int or not 1 <= size <= 8 * 1024 * 1024 or
                    not re.fullmatch('[0-9a-f]{64}', item['sha256'])):
                raise evaluation.Unavailable()
            report['evidence'] = {'bytes': size, 'sha256': item['sha256']}
        return report
    except (KeyError, AttributeError, ValueError, TypeError, OverflowError, RecursionError):
        raise evaluation.Unavailable() from None


def evidence(allowed_apps, digest):
    if not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
        raise ValueError('证据编号无效')
    metadata = published(allowed_apps).get('evidence')
    if not metadata or metadata['sha256'] != digest:
        raise design.Missing()
    blob = evaluation._published_bytes('kit-regression-evidence.zip', limit=metadata['bytes'])
    if len(blob) != metadata['bytes'] or hashlib.sha256(blob).hexdigest() != digest:
        raise evaluation.Unavailable()
    return 'application/zip', 'kit-regression-evidence-' + digest[:12] + '.zip', blob
