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
    optional = {'scenario_field','revision_field','mode','figma_field','figma_files','build_field','qa_packages','build_source','generated_targets','current_node_ids','title_contains'}
    if not isinstance(cfg, dict) or not required <= set(cfg) <= required | optional:
        raise ValueError('invalid_config')
    for name in ('project_key','work_item_type_key','acceptance_field','scenario_field','revision_field'):
        if name in cfg and (not isinstance(cfg[name], str) or not KEY.fullmatch(cfg[name])):
            raise ValueError('invalid_config')
    if cfg.get('mode','ready_smoke') not in ('development_design','ready_smoke','qa_build_smoke'):
        raise ValueError('invalid_mode')
    if cfg.get('mode') == 'qa_build_smoke':
        packages = cfg.get('qa_packages')
        targets = cfg.get('generated_targets',{})
        if not isinstance(targets,dict) or not isinstance(packages,dict) or set(targets)-set(packages):
            raise ValueError('invalid_qa_target')
        for target in targets.values():
            if not isinstance(target,dict) or set(target)!={'runner_id','device_id'}:
                raise ValueError('invalid_qa_target')
            for value in target.values():
                requirement_intake._identifier(value)
        source = cfg.get('build_source','project_field')
        if source not in ('packaging_hook','project_field') or (source=='packaging_hook' and 'build_field' in cfg):
            raise ValueError('invalid_build_source')
        if ((source=='project_field' and (not isinstance(cfg.get('build_field'),str) or not KEY.fullmatch(cfg['build_field'])))
                or (not packages and source != 'packaging_hook') or set(packages)-{'android','ios'}
                or not all(isinstance(v,str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,128}',v) for v in packages.values())):
            raise ValueError('qa_build_scope_required')
    elif {'build_field','qa_packages','build_source','generated_targets'} & cfg.keys():
        raise ValueError('invalid_mode')
    if 'figma_field' in cfg or 'figma_files' in cfg:
        files = cfg.get('figma_files')
        if (cfg.get('mode') not in ('development_design','qa_build_smoke') or not isinstance(cfg.get('figma_field'),str) or not KEY.fullmatch(cfg['figma_field'])
                or not isinstance(files,list) or not files or not all(isinstance(x,str) and KEY.fullmatch(x) for x in files)):
            raise ValueError('invalid_figma_scope')
    if 'title_contains' in cfg:
        label = cfg['title_contains']
        if not isinstance(label,str) or not 1 <= len(label) <= 160 or label.strip() != label or not label.isprintable():
            raise ValueError('invalid_title_scope')
    if 'current_node_ids' in cfg:
        nodes = cfg['current_node_ids']
        if (not isinstance(nodes,list) or not 1 <= len(nodes) <= 50
                or not all(isinstance(n,str) and KEY.fullmatch(n) for n in nodes) or len(set(nodes)) != len(nodes)):
            raise ValueError('invalid_current_node_ids')
    group = cfg['search_group']
    if not isinstance(group, dict) or not (group.get('search_params') or group.get('search_groups')):
        raise ValueError('ready_filter_required')
    for name in ('app_id','username','user_id'):
        if not isinstance(cfg[name], str) or not cfg[name].strip():
            raise ValueError('invalid_config')


def _value(fields, key, *, rich_text=False):
    matches = [field for field in fields if isinstance(field, dict) and field.get('field_key') == key]
    if len(matches) != 1:
        return None
    field = matches[0]
    if rich_text:
        # SearchByParams and GetWorkItemsByIds expose different SDK names for the same expansion.
        rendered = field.get('field_value_markdown') or field.get('field_value_mark_down')
        if isinstance(rendered, dict) and isinstance(rendered.get('markdown'), str):
            return rendered['markdown']
    return field.get('field_value')


def _scoped_id(cfg, row, *, allow_missing=False):
    if not isinstance(row,dict) or any(row.get(k) != cfg[k] for k in ('project_key','work_item_type_key')
                                       if not allow_missing or k in row):
        raise ValueError('item_scope_mismatch')
    item_id = row.get('id')
    if type(item_id) not in (str, int) or not re.fullmatch(r'[1-9][0-9]{0,19}',str(item_id)):
        raise ValueError('invalid_item_id')
    return str(item_id)


def _item(cfg, row, item_id):
    fields = row.get('fields')
    if not isinstance(fields, list):
        raise ValueError('missing_fields')
    raw = _value(fields, cfg['acceptance_field'])
    link = (raw.get('link') or raw.get('url')) if isinstance(raw,dict) else raw
    if isinstance(link,str) and link.strip().lower().startswith(('https://','http://')):
        from .feishu_wiki_acceptance import read_wiki_clauses
        clauses = read_wiki_clauses(link.strip())
    else:
        raw = _value(fields, cfg['acceptance_field'], rich_text=True)
        clauses = raw.splitlines() if isinstance(raw, str) else raw
        if isinstance(clauses,list) and any(isinstance(c,str) and re.fullmatch(
                r'(?:https?://\S+|\[[^\]]*\]\(https?://[^)]*\))',c.strip(),re.I) for c in clauses):
            raise ValueError('acceptance_text_required')
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


def _details(cfg, rows, fields, post):
    """Read scoped details in one call per search page whenever workflow or missing fields require it."""
    ids = set()
    for row in rows:
        try:
            item_id = _scoped_id(cfg,row,allow_missing=True)
        except ValueError:
            continue
        returned = row.get('fields')
        available = {f['field_key'] for f in returned or [] if isinstance(f,dict) and isinstance(f.get('field_key'),str)} if isinstance(returned,list) else set()
        has_scope = 'project_key' in row and 'work_item_type_key' in row
        if cfg.get('current_node_ids') or cfg.get('title_contains') or not set(fields) <= available or not has_scope:
            ids.add(int(item_id))
    if not ids:
        return {}
    path = '/open_api/{}/work_item/{}/query'.format(cfg['project_key'],cfg['work_item_type_key'])
    selected = list(dict.fromkeys(['name','current_nodes',*fields]))
    result = post(path,dict(work_item_ids=sorted(ids),fields=selected,
                            expand={'need_workflow':True,'need_rich_text_mark_down':True})).get('data')
    if not isinstance(result,list):
        raise ValueError('invalid_project_detail')
    details = {str(i):[] for i in ids}
    for row in result:
        if isinstance(row,dict) and str(row.get('id')) in details:
            details[str(row['id'])].append(row)
    return details


def sync(cfg, *, post=None, page_size=50, excluded_nodes=()):
    _config(cfg)
    if not 1 <= page_size <= 50:
        raise ValueError('invalid_page_size')
    actor = actor_from_trusted_username(cfg['username'])
    if actor['kind'] != 'user' or actor['user_id'] != cfg['user_id']:
        raise PermissionError('service_identity_mismatch')
    fields = [cfg[key] for key in ('acceptance_field','scenario_field','revision_field') if key in cfg]
    path = '/open_api/{}/work_item/{}/search/params'.format(cfg['project_key'], cfg['work_item_type_key'])
    counts, errors = Counter(), []
    development = cfg.get('mode') == 'development_design'
    fields.extend(cfg[key] for key in ('figma_field','build_field') if key in cfg)
    with actor_context(actor):
        for permission in (('ui.view',) if development else ('ui.view','ui.execute')):
            require_ui_actor(actor, permission=permission, app_id=cfg['app_id'])
        if cfg.get('mode') in ('development_design','qa_build_smoke') and not requirement_intake.requirement_design.authorized(actor['user_id'], cfg['username']):
            raise PermissionError('design_forbidden')
        if post is None:
            from .feishu_project_client import project_headers, project_post
            post = partial(project_post, headers=project_headers())
        for page in range(1, (5000 // page_size) + 2):
            response = post(path, dict(search_group=cfg['search_group'], fields=['name', *fields],
                                       page_num=page, page_size=page_size, expand={'need_rich_text_mark_down':True}))
            rows = response.get('data')
            if not isinstance(rows, list) or len(rows) > page_size:
                raise ValueError('invalid_project_page')
            details = _details(cfg,rows,fields,post)
            for row in rows:
                item_id = None
                event = None
                failure_stage = 'development_design'
                try:
                    candidate = _scoped_id(cfg,row,allow_missing=True)
                    if candidate in details:
                        item_id = candidate
                        matches = details[candidate]
                        if len(matches) != 1:
                            raise ValueError('project_detail_missing_or_ambiguous')
                        row = matches[0]
                    item_id = _scoped_id(cfg,row)
                    if 'title_contains' in cfg:
                        title = row.get('name')
                        if not isinstance(title,str) or not title.strip():
                            raise ValueError('project_title_unavailable')
                        if cfg['title_contains'] not in title:
                            if cfg.get('mode') in ('development_design','qa_build_smoke'):
                                requirement_intake.invalidate_design(cfg['project_key'],cfg['app_id'],item_id)
                            counts['outside_title_scope'] += 1
                            continue
                    if 'current_node_ids' in cfg:
                        nodes = row.get('current_nodes')
                        if not isinstance(nodes,list) or any(not isinstance(n,dict) or not isinstance(n.get('id'),str) or not n['id'] for n in nodes):
                            raise ValueError('project_current_nodes_unavailable')
                        active = {node['id'] for node in nodes}
                        if active.isdisjoint(cfg['current_node_ids']):
                            counts['outside_current_node'] += 1
                            continue
                        if not active.isdisjoint(excluded_nodes):
                            raise ValueError('project_lifecycle_nodes_ambiguous')
                    if cfg.get('mode') == 'qa_build_smoke' and not cfg['qa_packages']:
                        title = requirement_intake.requirement_design._text(row.get('name'),160)
                        # The environment has no admissible QA package. Record only the observed handoff.
                        stage_event = dict(source='feishu-project:'+cfg['project_key'],app_id=cfg['app_id'],
                                           requirement_id=item_id,title=title,revision='')
                        requirement_intake.record_stage(stage_event,'qa_build_smoke',{'status':'qa_package_scope_required'})
                        counts['qa_package_scope_required'] += 1
                        continue
                    material, scenario = _item(cfg, row, item_id)
                    event = requirement_intake._event(material)
                    link = _value(row['fields'], cfg['figma_field']) if 'figma_field' in cfg else None
                    if development:
                        from .feishu_development_design import create_design
                        receipt = create_design(cfg, event, link)
                    elif cfg.get('mode') == 'qa_build_smoke':
                        from .feishu_build_plan import current_builds, submit_build
                        from .feishu_development_design import create_design
                        updated = create_design(cfg,event,link)
                        requirement_intake.record_stage(event,'development_design',updated)
                        if updated['status'] != 'design_completed':
                            counts[updated['status']] += 1
                            continue
                        failure_stage = 'qa_build_smoke'
                        builds = (current_builds(cfg,event) if cfg.get('build_source')=='packaging_hook'
                                  else [_value(row['fields'],cfg['build_field'])])
                        for build in builds or [None]:
                            receipt = submit_build(cfg,event,link,build,current_source=updated['source_id'])
                            stage = 'qa_build_smoke' + ('.'+build['platform'] if isinstance(build,dict) else '')
                            requirement_intake.record_stage(event,stage,receipt)
                            counts[receipt['status']] += 1
                        continue
                    else:
                        receipt = requirement_intake.submit(event, origin='integration', scenario=scenario)
                except ValueError as exc:
                    code = str(exc)
                    if event and cfg.get('mode') in ('development_design','qa_build_smoke'):
                        # Revoke a previously ready packaging context if today's material cannot be read.
                        requirement_intake.record_stage(event,failure_stage,{'status':(
                            'source_unavailable' if failure_stage=='development_design' else 'build_unavailable')})
                    elif item_id and cfg.get('mode') in ('development_design','qa_build_smoke'):
                        requirement_intake.invalidate_design(cfg['project_key'],cfg['app_id'],item_id)
                    raw_id = str(row.get('id', '')) if isinstance(row, dict) else ''
                    errors.append({'item_id':raw_id if raw_id.isdecimal() else '',
                                   'code':code if re.fullmatch(r'[a-z_]{1,64}',code) else 'requirement_intake_failed'})
                else:
                    if cfg.get('mode') in ('development_design','qa_build_smoke'):
                        requirement_intake.record_stage(event,cfg['mode'],receipt)
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
        configs = json.loads(Path(config_path).read_text(encoding='utf-8'))
        if isinstance(configs,list):
            if len(configs)!=2 or {c.get('mode') for c in configs} != {'development_design','qa_build_smoke'}:
                raise ValueError('invalid_lifecycle_modes')
            dev,ready = sorted(configs,key=lambda c:c['mode'])
            for cfg in configs:
                _config(cfg)
            shared = ('project_key','work_item_type_key','app_id','user_id','username','acceptance_field','revision_field',
                      'scenario_field','figma_field','figma_files','title_contains')
            nodes = set(dev.get('current_node_ids',[])), set(ready.get('current_node_ids',[]))
            if (any(dev.get(k)!=ready.get(k) for k in shared) or not nodes[0].isdisjoint(nodes[1])
                    or (dev['search_group']==ready['search_group'] and not all(nodes))):
                raise ValueError('inconsistent_lifecycle_scope')
            result = {'counts':{}, 'errors':[]}
            for cfg,excluded in ((dev,nodes[1]),(ready,nodes[0])):
                outcome = sync(cfg,excluded_nodes=excluded)
                result['counts'][cfg['mode']] = outcome['counts']
                result['errors'].extend(outcome['errors'])
        else:
            result = sync(configs)
    except Exception as exc:
        # Configuration, upstream errors, and PRD fields can include private data.
        print(json.dumps({'ok':False, 'error':type(exc).__name__}))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return int(bool(result['errors']))


if __name__ == '__main__':
    raise SystemExit(main())
