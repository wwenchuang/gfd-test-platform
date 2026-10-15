"""Bounded data-only snapshot of one stored requirement case. Never reads files."""
import hashlib
import json

from .ui_case_draft_contract import ACTIONS


def _text(value, limit=2000):
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError('requirement_import_size')
    return ''.join(c for c in value.strip() if c.isprintable() or c in '\n\t')


def _lines(value, required=False):
    if isinstance(value, str):
        value = [value] if value.strip() else []
    if not isinstance(value, list) or len(value) > 50 or (required and not value):
        raise ValueError('requirement_import_size')
    rows = []
    for row in value:
        if isinstance(row, dict):
            # Preserve all supported structured step fields; never silently drop an action.
            if set(row) - {'action', 'target', 'value', 'expected', 'description', 'step', 'expected_result'}:
                raise ValueError('requirement_import_unsupported')
            if 'action' in row and (not isinstance(row['action'], str) or row['action'] not in ACTIONS):
                raise ValueError('requirement_import_unsupported')
            row = {key: _text(val) for key, val in row.items()}
            if not row: raise ValueError('requirement_import_size')
            rows.append(row)
        else:
            line = _text(row)
            if not line: raise ValueError('requirement_import_size')
            rows.append(line)
    return rows


def _first(item, keys, default):
    return next((item[key] for key in keys if item.get(key) not in (None, '', [])), default)


def case_snapshot(run, item, index):
    if not isinstance(item, dict): raise ValueError('requirement_import_size')
    artifacts = run.get('artifacts') or {}
    if not isinstance(artifacts, dict): raise ValueError('requirement_import_size')
    generated = artifacts.get('generatedCases') or {}
    validation = artifacts.get('yamlValidation') or {}
    if not isinstance(validation, dict): raise ValueError('requirement_import_size')
    # Same per-key priority as agentGeneratedCaseGroups, not whole-dict fallback.
    # An explicit empty list is authoritative in JavaScript; malformed values
    # fail closed instead of hiding a manual classification from another layer.
    layers = (artifacts.get('generatedCaseGroups', {}), validation.get('executionGroups', {}), generated)
    manual = []
    for layer in layers:
        if not isinstance(layer, dict): raise ValueError('requirement_import_size')
        if 'manual_cases' in layer:
            manual = layer['manual_cases']
            if not isinstance(manual, list): raise ValueError('requirement_import_size')
            break
    identity = lambda row: next((row.get(key) for key in ('case_id', 'caseId', 'id', 'file', 'path', 'title', 'name') if row.get(key)), None)
    if any(isinstance(row, dict) and identity(row) and identity(row) == identity(item) for row in manual):
        raise ValueError('requirement_import_manual')
    level = str(item.get('executionLevel') or item.get('execution_level') or item.get('level') or item.get('type') or '').lower()
    if item.get('manual') is True or any(token in level for token in ('manual', 'one_time', 'one-time', '一次性', '人工')):
        raise ValueError('requirement_import_manual')
    snapshot = {'kind': 'requirement', 'run_id': run['runId'], 'case_index': index,
                'case_id': _text(item.get('case_id') or item.get('id') or '', 200),
                'title': _text(_first(item, ('title', 'name', 'scenario'), ''), 200),
                'preconditions': _lines(_first(item, ('preconditions', 'precondition', 'data_requirements', 'dataRequirements'), [])),
                'steps': _lines(_first(item, ('steps', 'flow', 'actions'), []), required=True),
                'expected': _lines(_first(item, ('assertions', 'expects', 'expected_result', 'expectedResult', 'expected'), []))}
    if not snapshot['title']: raise ValueError('requirement_import_size')
    encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
    if len(encoded) > 18000: raise ValueError('requirement_import_size')
    snapshot['snapshot_id'] = hashlib.sha256(encoded.encode()).hexdigest()
    return snapshot


def import_refs(run):
    """Selectors for the displayed persisted rows, including rows with no legacy ID."""
    if run.get('sourceType') != 'requirement' or not run.get('ownerUserId'): return []
    artifacts = run.get('artifacts') or {}
    if not isinstance(artifacts, dict): return []
    generated = artifacts.get('generatedCases') or {}
    cases = generated.get('cases', []) if isinstance(generated, dict) else []
    if not isinstance(cases, list): return []
    refs = []
    for index, item in enumerate(cases[:100]):
        try:
            snap = case_snapshot(run, item, index)
            refs.append({'case_index': index, 'case_hash': snap['snapshot_id']})
        except (ValueError, TypeError, KeyError):
            pass
    return refs
