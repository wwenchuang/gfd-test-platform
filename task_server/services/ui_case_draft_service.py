"""Private human-reviewed drafts; model generation never dispatches a Runner job."""
import copy
import re
import threading

import yaml

from . import ai_skill_service, business_line_service, yaml_baseline_cache
from .operation_attribution import actor_context, current_actor
from .ui_case_compiler import compile_ui_case
from .ui_case_draft_contract import build_draft_yaml, normalize_steps
from .ui_case_draft_store import DraftConflict, DraftStore
from .ui_execution_contract import AccessDenied, require_ui_actor, resolve_ui_target
from .ui_execution_messages import blocker_message
from .yaml_executable_scorer import score_midscene_yaml_executable
from .yaml_static_validator import validate_yaml_static_executable

_generation_lock = threading.Lock()
_initialization_lock = threading.Lock()
_initialized_paths = set()
_DRAFT_ID = re.compile(r'^uid_[0-9a-f]{32}$')
_EDIT_KEYS = {'title', 'preconditions', 'steps', 'questions'}


def initialize_draft_service():
    """Recover prior-process work once, before this process can admit any worker.

    Safe for the server startup hook and lazy first use in the single server process.
    Repeated reads cannot recover or terminate a currently running worker.
    """
    store = DraftStore()
    key = str(store.path)
    with _initialization_lock:
        if key not in _initialized_paths:
            store.recover_interrupted_generation()
            _initialized_paths.add(key)
    return store


def _store():
    return initialize_draft_service()


def _start_worker(work):
    threading.Thread(target=work, name='ui-draft-normalizer', daemon=True).start()


def _body(body, allowed, required):
    if not isinstance(body, dict) or set(body) - allowed or not required.issubset(body):
        raise ValueError('draft_request_invalid')
    return body


def _revision(body):
    revision = body.get('revision')
    if type(revision) is not int or revision < 1:
        raise ValueError('draft_revision_invalid')
    return revision


def _application(app_id):
    if not isinstance(app_id, str) or not app_id or len(app_id) > 255:
        raise ValueError('draft_app_invalid')
    app = business_line_service.configured_test_application(app_id)
    if not app or not app.get('enabled') or app.get('historical_only'):
        raise ValueError('draft_app_unavailable')
    return app


def _owned(actor, draft_id, permission):
    profile = require_ui_actor(actor, permission=permission)
    if not isinstance(draft_id, str) or not _DRAFT_ID.fullmatch(draft_id):
        raise ValueError('draft_id_invalid')
    document = _store().get(draft_id, profile['user_id'])
    require_ui_actor(actor, permission=permission, app_id=document['app_id'])
    _application(document['app_id'])
    return profile, document


def _top3_examples(app_id, text):
    """Filter app provenance and genuine success before relevance ranking/Top3."""
    from .sonic_service import sonic_notify_known_apps
    module_owners = {}
    for app in sonic_notify_known_apps():
        package = app.get('package')
        if not isinstance(package, str) or not package:
            continue
        for module in app.get('modules') or []:
            if isinstance(module, str) and module:
                module_owners.setdefault(module, set()).add(package)
    rows = []
    terms = yaml_baseline_cache._terms(text, limit=120)
    for item in yaml_baseline_cache.get_yaml_baseline_cache(force=False).get('items') or []:
        if not isinstance(item, dict) or item.get('trusted') is not True or item.get('baselineUsable') is not True or item.get('lastRunStatus') != 'success':
            continue
        # Scope comes from trusted cache metadata or unambiguous server module
        # ownership, never from a launch action in the teaching text.
        owners = module_owners.get(item.get('module'), set())
        explicit_app = item.get('app_package')
        if explicit_app:
            if explicit_app != app_id or (owners and owners != {app_id}):
                continue
        elif owners != {app_id}:
            continue
        snippet = item.get('snippet')
        if not isinstance(snippet, str) or not snippet or len(snippet) > 30000:
            continue
        try:
            parsed = yaml.safe_load(snippet)
            if isinstance(parsed, list):
                # Existing baseline cache stores individual '- name/flow' blocks.
                tasks = parsed
            elif isinstance(parsed, dict) and 'android' in parsed and 'ios' not in parsed:
                tasks = parsed.get('tasks')
            else:
                continue
            if not isinstance(tasks, list):
                continue
            launches = [step['launch'] for task in tasks if isinstance(task, dict)
                        for step in (task.get('flow') or []) if isinstance(step, dict) and 'launch' in step]
            if not launches or any(package != app_id for package in launches):
                continue
        except (yaml.YAMLError, TypeError):
            continue
        score, _ = yaml_baseline_cache._score_item(terms, '', item)
        rows.append((score, str(item.get('title') or ''), snippet))
    rows.sort(key=lambda row: (row[0], row[1]), reverse=True)
    return [{'title': title[:200], 'yaml_content': snippet} for _, title, snippet in rows[:3]]


def _model_proposal(result, app_id):
    # The gateway's minimal schema validator does not enforce enum/additionalProperties.
    if not isinstance(result, dict) or set(result) != {'title', 'preconditions', 'steps', 'questions'}:
        raise ValueError('draft_model_structure_invalid')
    raw_steps = result['steps']
    fields = {'action', 'target', 'value', 'expected', 'review_state', 'source_ref'}
    if not isinstance(raw_steps, list) or not 1 <= len(raw_steps) <= 50:
        raise ValueError('draft_model_steps_invalid')
    for step in raw_steps:
        if not isinstance(step, dict) or set(step) != fields or step['review_state'] != 'needs_review' or step['source_ref'] != 'text':
            raise ValueError('draft_model_provenance_invalid')
    return normalize_steps(result, app_id)


def _generate(draft_id, trusted_actor, lock):
    owner_id = trusted_actor['user_id']
    try:
        with actor_context(trusted_actor):
            store = _store()
            document = store.get(draft_id, owner_id)
            require_ui_actor(trusted_actor, permission='ui.edit', app_id=document['app_id'])
            _application(document['app_id'])
            store.set_generation(draft_id, owner_id, 'generating')
            examples = _top3_examples(document['app_id'], document['source_text'])
            result = ai_skill_service.run_ai_skill('ui_step_normalizer', {
                'app_id': document['app_id'], 'source_text': document['source_text'],
                'baseline_examples': examples,
            }, timeout=180, retry_count=0, max_tokens=16000, repair_invalid_json=False)
            # Recheck live identity/scope after the long model call, before persistence.
            require_ui_actor(trusted_actor, permission='ui.edit', app_id=document['app_id'])
            _application(document['app_id'])
            normalized = _model_proposal(result, document['app_id'])
            compiled = build_draft_yaml(normalized, document['app_id'])
            store.set_generation(draft_id, owner_id, 'ready', {
                **normalized, 'yaml_content': compiled['yaml_content'], 'ready': False,
            })
    except DraftConflict:
        # Human edits and previous workers win; stale work cannot overwrite them.
        pass
    except Exception as error:
        message = ('AI 整理超时，请稍后重新创建草稿' if isinstance(error, TimeoutError)
                   else '账号或应用权限已变化，草稿整理已停止' if isinstance(error, AccessDenied)
                   else 'AI 未能返回有效步骤，原始描述已保留，请编辑或重新创建草稿')
        try:
            _store().set_generation(draft_id, owner_id, 'failed', error=message)
        except (DraftConflict, KeyError):
            pass
    finally:
        lock.release()


def create_draft(actor, body):
    body = _body(body, {'app_id', 'text'}, {'app_id', 'text'})
    app_id = body['app_id']
    profile = require_ui_actor(actor, permission='ui.edit', app_id=app_id)
    _application(app_id)
    text = body['text']
    if not isinstance(text, str) or not text.strip() or len(text) > 20000:
        raise ValueError('draft_text_invalid')
    lock = _generation_lock
    if not lock.acquire(blocking=False):
        raise ValueError('draft_generation_busy')
    document = None
    try:
        # Only copy the verified context. Never derive worker identity from request fields.
        trusted_actor = dict(current_actor())
        document = _store().create(profile, app_id, text.strip())
        _start_worker(lambda: _generate(document['draft_id'], trusted_actor, lock))
    except Exception:
        if document:
            try:
                _store().set_generation(document['draft_id'], profile['user_id'], 'failed', error='草稿整理暂不可用，请稍后重试')
            except (DraftConflict, KeyError):
                pass
        lock.release()
        raise
    return document


def import_recording_draft(actor, body):
    from .device_recording_service import get_recording_session
    from .recording_draft_import import proposal
    body = _body(body, {'app_id', 'session_id'}, {'app_id', 'session_id'})
    profile = require_ui_actor(actor, permission='ui.edit', app_id=body['app_id'])
    app = _application(body['app_id'])
    if not isinstance(body['session_id'], str) or not re.fullmatch(r'[0-9a-f]{32}', body['session_id']):
        raise ValueError('recording_import_invalid')
    session = get_recording_session(body['session_id'])
    if session.get('created_by') != profile['username'] or session.get('app_package') != body['app_id']:
        raise AccessDenied('recording_import_scope')
    if session.get('status') != 'finished':
        raise ValueError('recording_import_unfinished')
    snapshot, content = proposal(session, app)
    require_ui_actor(actor, permission='ui.edit', app_id=body['app_id'])
    return _store().create(profile, body['app_id'], '来源：已结束的操作录制；请逐步核对目标与预期。',
                           recording_snapshot=snapshot, recording_content=content)


def list_drafts(actor, app_id):
    profile = require_ui_actor(actor, permission='ui.view', app_id=app_id)
    _application(app_id)
    return {'items': _store().list(profile['user_id'], app_id, limit=20)}


def get_draft(actor, draft_id):
    return _owned(actor, draft_id, 'ui.view')[1]


def update_draft(actor, draft_id, body):
    body = _body(body, _EDIT_KEYS | {'revision'}, _EDIT_KEYS | {'revision'})
    profile, document = _owned(actor, draft_id, 'ui.edit')
    revision = _revision(body)
    if document['revision'] != revision:
        raise DraftConflict('draft_revision_changed')
    source = document.get('source_snapshot', {})
    provenance = {item['draft_step_id']: f"recording:{source['snapshot_id']}:{item['id']}"
                  for item in source.get('actions', []) if item.get('draft_step_id')}
    for step in body.get('steps', []) if isinstance(body.get('steps'), list) else []:
        if isinstance(step, dict) and step.get('source_ref', 'text') != provenance.get(step.get('step_id'), 'text'):
            raise ValueError('draft_source_invalid')
    normalized = normalize_steps({key: body[key] for key in _EDIT_KEYS}, document['app_id'])
    compiled = build_draft_yaml(normalized, document['app_id'])
    require_ui_actor(actor, permission='ui.edit', app_id=document['app_id'])
    return _store().revise(draft_id, profile['user_id'], revision,
                           {**normalized, 'yaml_content': compiled['yaml_content'], 'ready': False})


def restore_draft(actor, draft_id, body):
    """CAS against current revision, restore previous content as a new revision."""
    body = _body(body, {'revision'}, {'revision'})
    profile, document = _owned(actor, draft_id, 'ui.edit')
    revision = _revision(body)
    if document['revision'] != revision:
        raise DraftConflict('draft_revision_changed')
    if revision <= 1:
        raise ValueError('draft_previous_revision_missing')
    prior = _store().get_revision(draft_id, profile['user_id'], revision - 1)
    if not prior['steps']:
        raise ValueError('draft_previous_revision_empty')
    return update_draft(actor, draft_id, {'revision': revision, **{key: prior[key] for key in _EDIT_KEYS}})


def validate_draft(actor, draft_id, body):
    """Return a candidate gate only; trial dispatch adds its own admission/ownership gates."""
    body = _body(body, {'revision', 'target'}, {'revision', 'target'})
    _, document = _owned(actor, draft_id, 'ui.execute')
    revision = _revision(body)
    if document['revision'] != revision:
        raise DraftConflict('draft_revision_changed')
    target = body['target']
    if not isinstance(target, dict) or set(target) != {'runner_id', 'device_id', 'platform'}:
        raise ValueError('draft_target_invalid')
    gate = {'ready': False, 'blockers': [], 'compiled_case': {}, 'target_resolution': {},
            'draft': copy.deepcopy(document), 'static_validation': {}, 'executable_score': {}}
    if document['generation_status'] in {'queued', 'generating', 'failed'} or not document['steps']:
        gate['blockers'].append({'code': 'draft_generation_incomplete', 'message': '请先完成步骤整理或人工编辑'})
        return gate
    candidate = build_draft_yaml({key: document[key] for key in _EDIT_KEYS}, document['app_id'])
    gate['blockers'].extend(candidate['questions'])
    static = validate_yaml_static_executable(candidate['yaml_content'], strict=True)
    score = score_midscene_yaml_executable(candidate['yaml_content'], generated=True)
    gate.update(static_validation=static, executable_score=score)
    if not static.get('ok') or static.get('warnings'):
        gate['blockers'].extend(static.get('errors') or [])
        gate['blockers'].extend(static.get('warnings') or [])
        if not gate['blockers']:
            gate['blockers'].append('YAML 静态校验未通过')
    if not score.get('ok'):
        gate['blockers'].extend(score.get('errors') or score.get('reasons') or ['生成用例尚未达到可执行评分'])
    resolved = resolve_ui_target(document['app_id'], target, actor)
    gate['target_resolution'] = resolved
    if not resolved.get('ok'):
        gate['blockers'].append(resolved.get('block_code') or '设备目标不可用')
    else:
        compiled = compile_ui_case({'yaml_content': candidate['yaml_content'], 'step_ids': candidate['step_ids']}, resolved)
        gate['compiled_case'] = compiled
        if not compiled.get('ok'):
            gate['blockers'].append(compiled.get('block_code') or '目标平台无法编译这些步骤')
    require_ui_actor(actor, permission='ui.execute', app_id=document['app_id'])
    current = _store().get(draft_id, document['owner_user_id'])
    if current['revision'] != revision:
        raise DraftConflict('draft_revision_changed')
    gate['blockers'] = [
        {'code': value if re.fullmatch(r'[a-z][a-z0-9_]*', value) else 'draft_review_required',
         'message': blocker_message(value) if re.fullmatch(r'[a-z][a-z0-9_]*', value) else value}
        for value in dict.fromkeys(gate['blockers'])
    ]
    gate['ready'] = not gate['blockers'] and candidate['ready'] and gate['compiled_case'].get('ok') is True
    return gate
