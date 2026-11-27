"""Poll test-ready requirements from Feishu Project's typed work-item search API."""
from collections import Counter
from functools import partial
from hashlib import sha256
import json
import os
from pathlib import Path
import re

from . import requirement_intake
from .operation_attribution import actor_context, actor_from_trusted_username
from .ui_execution_contract import require_ui_actor

KEY = re.compile(r'[A-Za-z0-9_-]{1,80}')


def _config(cfg):
    required = {'project_key','work_item_type_key','app_id','username','user_id','acceptance_field','search_group'}
    optional = {'scenario_field','revision_field'}
    if not isinstance(cfg, dict) or not required <= set(cfg) <= required | optional:
        raise ValueError('invalid_config')
    for name in ('project_key','work_item_type_key','acceptance_field','scenario_field','revision_field'):
        if name in cfg and (not isinstance(cfg[name], str) or not KEY.fullmatch(cfg[name])):
            raise ValueError('invalid_config')
    group = cfg['search_group']
    if not isinstance(group, dict) or not (group.get('search_params') or group.get('search_groups')):
        raise ValueError('ready_filter_required')
    for name in ('app_id','username','user_id'):
        if not isinstance(cfg[name], str) or not cfg[name].strip():
            raise ValueError('invalid_config')


def _value(fields, key):
    values = [field['field_value'] for field in fields if isinstance(field, dict)
              and field.get('field_key') == key and 'field_value' in field]
    return values[0] if len(values) == 1 else None


def _item(cfg, row):
    if not isinstance(row, dict) or (row.get('project_key'),row.get('work_item_type_key')) != (
            cfg['project_key'],cfg['work_item_type_key']):
        raise ValueError('item_scope_mismatch')
    item_id = row.get('id')
    if type(item_id) not in (str, int) or not re.fullmatch(r'[1-9][0-9]{0,19}',str(item_id)):
        raise ValueError('invalid_item_id')
    fields = row.get('fields')
    if not isinstance(fields, list):
        raise ValueError('missing_fields')
    raw = _value(fields, cfg['acceptance_field'])
    clauses = raw.splitlines() if isinstance(raw, str) else raw
    if not isinstance(clauses, list) or any(not isinstance(s, str) for s in clauses):
        raise ValueError('acceptance_text_required')
    clauses = [s.strip() for s in clauses if s.strip()]
    scenario = _value(fields, cfg['scenario_field']) if 'scenario_field' in cfg else None
    if isinstance(scenario, dict):
        scenario = scenario.get('value')  # Feishu Project single-select option key.
    if scenario is not None and not isinstance(scenario, str):
        raise ValueError('invalid_scenario_field')
    material = [row.get('name'), clauses, scenario]
    if 'revision_field' in cfg:
        version = _value(fields, cfg['revision_field'])
        if type(version) not in (str, int):
            raise ValueError('invalid_revision_field')
        revision = requirement_intake._identifier(str(version))
    else:
        revision = sha256(requirement_intake._json(material).encode()).hexdigest()
    # Digest the delivery ID independently so long upstream version strings still fit the API contract.
    event_id = str(item_id) + '.' + sha256(revision.encode()).hexdigest()
    return dict(source='feishu-project:' + cfg['project_key'], app_id=cfg['app_id'], event_id=event_id,
                requirement_id=str(item_id), revision=revision, title=row.get('name'), acceptance_criteria=clauses), scenario


def sync(cfg, *, post=None, page_size=50):
    _config(cfg)
    if not 1 <= page_size <= 50:
        raise ValueError('invalid_page_size')
    actor = actor_from_trusted_username(cfg['username'])
    if actor['kind'] != 'user' or actor['user_id'] != cfg['user_id']:
        raise PermissionError('service_identity_mismatch')
    fields = [cfg[key] for key in ('acceptance_field','scenario_field','revision_field') if key in cfg]
    path = '/open_api/{}/work_item/{}/search/params'.format(cfg['project_key'], cfg['work_item_type_key'])
    counts, errors = Counter(), []
    with actor_context(actor):
        for permission in ('ui.view','ui.execute'):
            require_ui_actor(actor, permission=permission, app_id=cfg['app_id'])
        if post is None:
            from .feishu_project_client import project_headers, project_post
            post = partial(project_post, headers=project_headers())
        for page in range(1, (5000 // page_size) + 2):
            response = post(path, dict(search_group=cfg['search_group'], fields=['name', *fields],
                                       page_num=page, page_size=page_size))
            rows = response.get('data')
            if not isinstance(rows, list) or len(rows) > page_size:
                raise ValueError('invalid_project_page')
            for row in rows:
                try:
                    event, scenario = _item(cfg, row)
                    receipt = requirement_intake.submit(event, origin='integration', scenario=scenario)
                except ValueError as exc:
                    item_id = str(row.get('id', '')) if isinstance(row, dict) else ''
                    code = str(exc)
                    errors.append({'item_id':item_id if item_id.isdecimal() else '',
                                   'code':code if re.fullmatch(r'[a-z_]{1,64}',code) else 'requirement_intake_failed'})
                else:
                    counts[receipt['status']] += 1
            pagination = response.get('pagination')
            total = pagination.get('total') if isinstance(pagination, dict) else None
            if len(rows) < page_size or type(total) is int and page * page_size >= total:
                break
        else:
            raise ValueError('filter_exceeds_project_limit')
    return {'counts':dict(counts), 'errors':errors}


def main():
    try:
        config_path = os.environ['MIDSCENE_FEISHU_REQUIREMENT_CONFIG']
        result = sync(json.loads(Path(config_path).read_text(encoding='utf-8')))
    except Exception as exc:
        # Configuration, upstream errors, and PRD fields can include private data.
        print(json.dumps({'ok':False, 'error':type(exc).__name__}))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return int(bool(result['errors']))


if __name__ == '__main__':
    raise SystemExit(main())
