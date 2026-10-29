"""Trusted private draft admission into the existing UI batch execution protocol."""
import hashlib
import json

from .operation_attribution import current_actor
from .ui_case_compiler import compile_ui_case
from .ui_case_draft_contract import build_draft_yaml
from .ui_video_execution_contract import compiler_options
from .ui_case_draft_store import DraftStore, DraftConflict
from .ui_execution_contract import require_ui_actor
from .ui_execution_service import _identifier, _request_hash, _require_ui_admission, BatchValidationError
from .ui_execution_store import UiExecutionStore
from .yaml_static_validator import validate_yaml_static_executable
from .yaml_executable_scorer import score_midscene_yaml_executable

TRIAL_CONTRACT = 'private-draft-trial-v1'


def _source(draft):
    if draft.get('yaml_candidate', {}).get('status') == 'invalid':
        raise BatchValidationError('draft_yaml_candidate_unresolved')
    proposal = {key:draft[key] for key in ('title','preconditions','steps','questions')}
    built = build_draft_yaml(proposal, draft['app_id'],
        **compiler_options(draft.get('source_snapshot', {}), draft['app_id'], proposal['steps']))
    static = validate_yaml_static_executable(built['yaml_content'], strict=True)
    score = score_midscene_yaml_executable(built['yaml_content'], generated=True, compiler_roles=built['compiler_roles'])
    if not built['ready'] or not static['ok'] or static.get('warnings') or not score['ok']:
        raise BatchValidationError('draft_gate_required')
    return built


def _hash(source):
    return hashlib.sha256(source.encode('utf-8')).hexdigest()


def validate_trial_snapshot(batch, attempt):
    """Recheck persisted provenance and pure gates, with no model or client flags."""
    try:
        snapshots = batch['snapshots']
        if len(snapshots) != 1:
            return False
        snapshot = snapshots[0]
        metadata = snapshot['metadata']
        if metadata.get('trial_contract') != TRIAL_CONTRACT or metadata.get('status') != 'draft':
            return False
        draft = DraftStore().get_revision(metadata['draft_id'], batch['owner_user_id'], metadata['draft_revision'])
        if draft['app_id'] != batch['app_id'] or draft['owner_username'] != batch['owner_username']:
            return False
        built = _source(draft)
        source_hash = _hash(built['yaml_content'])
        if (snapshot['source_yaml'] != built['yaml_content'] or snapshot['source_hash'] != source_hash
                or snapshot['case_revision'] != source_hash or snapshot['case_id'] != draft['draft_id']
                or attempt['case_id'] != draft['draft_id'] or attempt['case_revision'] != source_hash):
            return False
        compiled = compile_ui_case(built, attempt['target_resolution'])
        return compiled['ok'] and compiled == attempt['compiled_case']
    except (KeyError, ValueError, TypeError, OSError):
        return False


def create_draft_trial(actor, draft_id, body, key):
    _require_ui_admission()
    if not isinstance(body, dict) or set(body) != {'revision','target'} or type(body.get('revision')) is not int:
        raise BatchValidationError('draft_trial_request_invalid')
    _identifier(key, 'idempotency_key_invalid')
    profile = require_ui_actor(actor)
    draft = DraftStore().get(draft_id, profile['user_id'])
    require_ui_actor(actor, app_id=draft['app_id'])
    digest = _request_hash({'type':TRIAL_CONTRACT,'draft_id':draft_id,**body})
    prior = UiExecutionStore().find_idempotent(profile['user_id'], key)
    if prior:
        if prior['request_hash'] != digest:
            raise DraftConflict('idempotency_conflict')
        from .ui_execution_service import get_ui_batch
        return get_ui_batch(actor, prior['ui_batch_id'])
    if draft['revision'] != body['revision']:
        raise DraftConflict('draft_revision_changed')
    # The editor service resolves the exact current target and reports human blockers.
    from .ui_case_draft_service import validate_draft
    gate = validate_draft(actor, draft_id, body)
    if not gate['ready']:
        raise BatchValidationError('draft_gate_required')
    if gate['draft']['revision'] != body['revision']:
        raise DraftConflict('draft_revision_changed')
    draft = gate['draft']
    built = _source(draft)
    compiled = compile_ui_case(built, gate['target_resolution'])
    if not compiled['ok'] or compiled != gate['compiled_case']:
        raise BatchValidationError('draft_gate_required')
    source_hash = _hash(built['yaml_content'])
    resolution = dict(gate['target_resolution'])
    resolution['mapping_revision'] = _request_hash({'app_id':draft['app_id'],
        'platform_mappings':resolution['platform_mappings'],'target_package':resolution['target_package']})
    from .sonic_service import sonic_notify_known_apps
    app_name = next((str(app.get('name')) for app in sonic_notify_known_apps()
                     if app.get('package') == draft['app_id'] and app.get('name')), '测试应用')
    prepared = {'owner_user_id':profile['user_id'],'owner_username':profile['username'],
        'owner_display_name':current_actor()['display_name'],'app_id':draft['app_id'],
        'idempotency_key':key,'request_hash':digest,
        'selection':{'app_id':draft['app_id'],'cases':[{'case_id':draft_id,'revision':source_hash}],
            'targets':[body['target']],'test_data_refs':[],'notifications':{'enabled':False},
            'release_report':{'enabled':False}},
        'snapshots':[{'case_id':draft_id,'case_revision':source_hash,'source_hash':source_hash,
            'source_yaml':built['yaml_content'],'metadata':{'status':'draft','trial_contract':TRIAL_CONTRACT,
                'draft_id':draft_id,'draft_revision':draft['revision'],'task_name':draft['title'],
                'module':'_private_ui_drafts','file':draft_id + '_r' + str(draft['revision']) + '.yaml',
                'app_package':draft['app_id'],'app_name':app_name,'goal':draft['title'],
                'preconditions':draft['preconditions'],'preconditions_verified':False}}],
        'attempts':[{'case_id':draft_id,'case_revision':source_hash,'target':body['target'],
            'target_resolution':resolution,'compiled_case':compiled}]}
    # Reuse all dispatch preflight checks before persisting intent (no phone actions).
    from .ui_batch_dispatch import _target_check
    snapshot = prepared['snapshots'][0]
    snapshot['snapshot_id'] = 'draft-preflight'
    attempt = {**prepared['attempts'][0], **body['target'], 'snapshot_id':snapshot['snapshot_id']}
    blocker = _target_check(prepared, attempt)
    if blocker:
        raise BatchValidationError(blocker)
    return UiExecutionStore().create_batch(prepared)


def _verified_publish_batch(actor, draft, batch_id):
    from .ui_execution_service import get_ui_batch
    batch = get_ui_batch(actor, batch_id)
    if (not batch or batch['owner_user_id'] != draft['owner_user_id'] or batch['app_id'] != draft['app_id']
            or len(batch['case_attempts']) != 1 or not batch['execution_settled'] or batch['status'] != 'passed'):
        raise BatchValidationError('draft_trial_pass_required')
    attempt = batch['case_attempts'][0]
    if (not validate_trial_snapshot(batch, attempt)
            or batch['snapshots'][0]['metadata']['draft_id'] != draft['draft_id']
            or batch['snapshots'][0]['metadata']['draft_revision'] != draft['revision']
            or attempt['status'] != 'passed' or not attempt['execution_settled']
            or not attempt.get('stop_confirmed') or not attempt.get('job_id')
            or not attempt.get('event_sequence') or not attempt.get('finished_ns') or not attempt.get('last_event_ns')):
        raise BatchValidationError('draft_trial_pass_required')
    if (not attempt.get('step_progress_known') or attempt.get('step_progress_truncated')
            or not attempt.get('steps') or any(step['status'] != 'passed' for step in attempt['steps'])):
        raise BatchValidationError('draft_trial_steps_required')
    # Describe_steps folds task totals: additionally require exact complete task evidence.
    import yaml
    source_tasks = yaml.safe_load(attempt['compiled_case']['yaml_content'])['tasks']
    tasks = (attempt.get('execution_progress') or {}).get('tasks') or []
    if len(tasks) != len(source_tasks):
        raise BatchValidationError('draft_trial_steps_required')
    for task, source in zip(tasks, source_tasks):
        total = len(source['flow'])
        rows = task.get('steps')
        current = task.get('current_step')
        # The actual observer reports one label per source flow and a zero-based
        # ScriptPlayer cursor (some versions advance it after the final action).
        # Per-step status is not in this protocol: authenticated stopped/passed
        # result remains authoritative, and display-derived green rows are not.
        if (task.get('status') != 'passed' or task.get('total_steps') != total
                or type(current) is not int or current not in {total - 1, total}
                or not isinstance(rows, list) or len(rows) != total
                or any(not isinstance(row, dict) or not isinstance(row.get('label'), str)
                       or not row['label'].strip() for row in rows)):
            raise BatchValidationError('draft_trial_steps_required')
    return batch


def publish_draft(actor, draft_id, body):
    """Create a unique public asset only from this owner's proven current revision.

    A private durable intent survives file/metadata partial writes. Retrying the
    same intent may reconcile its exact bytes, and never overwrites another file.
    """
    import re
    from pathlib import Path
    from task_server import config
    from task_server.storage import file_mutation_lock, write_text_file
    from . import business_line_service, job_service, asset_lineage
    if (not isinstance(body, dict) or set(body) != {'revision','batch_id','module'}
            or type(body.get('revision')) is not int):
        raise BatchValidationError('draft_publish_request_invalid')
    profile = require_ui_actor(actor, permission='ui.baseline')
    store = DraftStore()
    draft = store.get(draft_id, profile['user_id'])
    require_ui_actor(actor, permission='ui.baseline', app_id=draft['app_id'])
    if draft['revision'] != body['revision']:
        raise DraftConflict('draft_revision_changed')
    if draft.get('yaml_candidate', {}).get('status') == 'invalid':
        raise BatchValidationError('draft_yaml_candidate_unresolved')
    module = body['module']
    app = business_line_service.configured_test_application(draft['app_id']) or {}
    from .sonic_service import sonic_notify_known_apps
    module_apps = {item.get('package') for item in sonic_notify_known_apps()
                   if isinstance(item, dict) and module in (item.get('modules') or [])}
    if (module_apps != {draft['app_id']} or not app.get('enabled') or app.get('historical_only') or not isinstance(module, str)
            or module in {'.','..'}
            or '/' in module or '\\' in module or not module.strip()):
        raise BatchValidationError('draft_module_invalid')
    if not re.fullmatch(r'uid_[0-9a-f]{32}', draft_id):
        raise BatchValidationError('draft_publish_request_invalid')
    batch = _verified_publish_batch(actor, draft, body['batch_id'])
    snapshot = batch['snapshots'][0]
    source = snapshot['source_yaml']
    source_hash = snapshot['source_hash']
    filename = draft_id + '_r' + str(draft['revision']) + '.yaml'
    root = Path(config.TASK_DIR).absolute()
    directory = root / module
    target = directory / filename
    if directory.is_symlink() or target.is_symlink() or directory.resolve().parent != root.resolve():
        raise BatchValidationError('draft_module_invalid')
    # Persist intent independently of file writes. No automatic recovery publishes assets.
    with store._db(write=True) as db:
        db.execute('''CREATE TABLE IF NOT EXISTS draft_publications (
            draft_id TEXT NOT NULL, revision INTEGER NOT NULL, module TEXT NOT NULL,
            batch_id TEXT NOT NULL, source_hash TEXT NOT NULL, status TEXT NOT NULL,
            result_json TEXT NOT NULL DEFAULT '', PRIMARY KEY(draft_id,revision))''')
        prior = db.execute('SELECT * FROM draft_publications WHERE draft_id=? AND revision=?',
                           (draft_id,draft['revision'])).fetchone()
        if prior and (prior['module'] != module or prior['batch_id'] != body['batch_id'] or prior['source_hash'] != source_hash):
            raise DraftConflict('draft_publish_conflict')
        if prior and prior['status'] == 'published':
            if target.is_symlink() or not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != source_hash:
                raise DraftConflict('draft_publish_asset_changed')
            from .ui_execution_catalog import clear_catalog_cache
            clear_catalog_cache()
            return json.loads(prior['result_json'])
        if not prior:
            if target.exists():
                raise DraftConflict('draft_publish_path_exists')
            db.execute('INSERT INTO draft_publications(draft_id,revision,module,batch_id,source_hash,status) VALUES(?,?,?,?,?,?)',
                       (draft_id,draft['revision'],module,body['batch_id'],source_hash,'pending'))
    directory.mkdir(mode=0o755, parents=True, exist_ok=True)
    with file_mutation_lock(str(target)):
        # Keep revision CAS locked for the short local file publication only.
        with store._db(write=True) as db:
            current = store._read(db, draft_id, profile['user_id'])
            if current['revision'] != body['revision']:
                raise DraftConflict('draft_revision_changed')
            require_ui_actor(actor, permission='ui.baseline', app_id=draft['app_id'])
            if directory.is_symlink() or target.is_symlink():
                raise DraftConflict('draft_publish_path_exists')
            if target.exists():
                if target.read_bytes() != source.encode('utf-8'):
                    raise DraftConflict('draft_publish_path_exists')
                attribution = asset_lineage.snapshot(str(target))[0]
            else:
                # Existing cross-process asset/file lock plus lineage's staged
                # atomic replace leaves no empty public placeholder on failure.
                attribution = write_text_file(str(target), source)
            # Metadata is published last: partial file writes remain outside the catalog.
            job_service.update_task_meta(module, filename, {'status':'baseline','app_package':draft['app_id'],
                'status_updated_by':profile['username'],'source_draft_id':draft_id,
                'source_draft_revision':draft['revision'],'source_snapshot':draft.get('source_snapshot',{}),'verified_ui_batch_id':batch['ui_batch_id'],
                'verified_source_hash':source_hash,
                'verified_platform':batch['case_attempts'][0]['platform'],
                'verified_app_versions':batch['case_attempts'][0]['target_resolution']['device_snapshot'].get('app_versions'),
                'preconditions':draft['preconditions'],'preconditions_verified':False})
            from .yaml_service import stable_case_id
            result = {'case_id':stable_case_id(draft['app_id'],module,filename,draft['title']),
                      'draft_id':draft_id,'revision':draft['revision'],'module':module,'file':filename,
                      'status':'baseline','source_hash':source_hash,'batch_id':batch['ui_batch_id'],
                      'attribution':attribution}
            db.execute("UPDATE draft_publications SET status='published',result_json=? WHERE draft_id=? AND revision=?",
                       (json.dumps(result,ensure_ascii=False),draft_id,draft['revision']))
            from .ui_execution_catalog import clear_catalog_cache
            clear_catalog_cache()
            return result
