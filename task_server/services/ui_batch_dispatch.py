"""UI batches adapt to the existing Job/Runner queue; no alternate execution queue."""
import os
import re
import threading
import time
from functools import wraps

from . import job_service, runner_service, sonic_service
from .operation_attribution import actor_context
from .ui_execution_contract import require_ui_actor, resolve_ui_target
from .ui_execution_lifecycle import TERMINAL, ACK_LOST_NS
from .ui_execution_store import UiExecutionStore, default_db_path
from .ui_execution_messages import blocker_message

# Always acquired before JOB_LOCK. No SQLite transaction spans file or network I/O.
DISPATCH_LOCK = threading.RLock()
_WORKER = None


def enabled():
    return os.getenv('UI_SIMPLE_FLOW_ENABLED', '').lower().strip() in {'1','true','yes'}


def serialized(function):
    @wraps(function)
    def run(*args, **kwargs):
        with DISPATCH_LOCK:
            return function(*args, **kwargs)
    return run


def _owner(batch):
    return {'kind':'user','username':batch['owner_username'],'user_id':batch['owner_user_id'],
            'display_name':batch['owner_display_name']}


def _unsupported_selection(selection):
    if selection.get('suite_id'):
        return 'suite_execution_unavailable'
    if selection.get('test_data_refs'):
        return 'test_data_unavailable'
    return ''


def _target_check(batch, attempt, occupancy=None, fresh=False):
    code = _unsupported_selection(batch['selection'])
    if code:
        return code
    metadata = next(s['metadata'] for s in batch['snapshots'] if s['snapshot_id'] == attempt['snapshot_id'])
    if metadata.get('status') not in {'active','baseline'}:
        # Generated drafts must go through Agent static/scoring/smoke gates first.
        return 'generated_case_gate_required'
    with actor_context(_owner(batch)):
        target = {k:attempt[k] for k in ('runner_id','device_id','platform')}
        resolved = resolve_ui_target(batch['app_id'], target)
    if not resolved['ok']:
        return resolved['block_code']
    runner = runner_service.list_runners().get(attempt['runner_id'], {})
    if ((runner.get('capabilities') or {}).get('ui_attempt_events') != 1 or
            (runner.get('capabilities') or {}).get('ui_private_reports') != 1 or
            (runner.get('capabilities') or {}).get('ui_process_stop_proof') != 1):
        return 'runner_upgrade_required'
    frozen = attempt['target_resolution']
    if resolved['target_package'] != frozen['target_package'] or resolved['platform_mappings'] != frozen['platform_mappings']:
        return 'app_mapping_changed'
    versions = resolved['device_snapshot'].get('app_versions') or []
    frozen_versions = frozen['device_snapshot'].get('app_versions') or []
    for evidence in (versions, frozen_versions):
        if (len(evidence) != 1 or evidence[0].get('installed') is not True or
                any(type(evidence[0].get(key)) not in (str, int) or
                    not str(evidence[0][key]).strip() for key in ('version_name','version_code'))):
            return 'app_version_unavailable'
    if versions != frozen_versions:
        return 'app_version_changed'
    if not set(attempt['compiled_case']['required_actions']) <= set(resolved['capabilities'].get('ui_actions') or []):
        return 'runner_capability_missing'
    try:
        statuses = occupancy if occupancy is not None else sonic_service.sonic_list_device_statuses(force=fresh)
    except Exception:
        return 'occupancy_unavailable'
    if not (statuses.get('__source__') or {}).get('available') or attempt['device_id'] not in statuses:
        return 'occupancy_unavailable'
    if statuses[attempt['device_id']].get('usage_status') != 'idle':
        return 'device_busy'
    return ''


def preflight_ui_batch(actor, request):
    from .ui_execution_service import _normalize_request, _prepare_ui_batch, _authorize_notifications, _require_ui_admission, BatchValidationError
    _require_ui_admission()
    normalized = _normalize_request(request)
    profile = require_ui_actor(actor, app_id=normalized['app_id'])
    _authorize_notifications(actor, normalized)
    blockers, compatibility = [], []
    code = _unsupported_selection(normalized)
    if code:
        blockers.append({'code':code,'message':code})
    else:
        try:
            prepared = _prepare_ui_batch(actor, normalized, profile, 'preflight', '')
            # Use the same target checks as dispatch without writing a batch.
            for i, snapshot in enumerate(prepared['snapshots']):
                snapshot['snapshot_id'] = str(i)
            for attempt in prepared['attempts']:
                attempt.update(attempt['target'])
                attempt['snapshot_id'] = next(s['snapshot_id'] for s in prepared['snapshots'] if s['case_id'] == attempt['case_id'])
                code = _target_check(prepared,attempt)
                compatibility.append({**attempt['target'],'case_id':attempt['case_id'],'ready':not code,'code':code})
                if code:
                    blockers.append({'code':code, **attempt['target']})
        except BatchValidationError as error:
            blockers.append({'code':error.code})
    for blocker in blockers:
        blocker['message'] = blocker_message(blocker['code'])
    return {'ready':not blockers,'blockers':blockers,'compatibility':compatibility,'required_inputs':[],
            'dry_run':{'status':'not_performed','reason':'native_ios_dry_run_unavailable' if any(t['platform'] == 'ios' for t in normalized['targets']) else 'baseline_execution'}}


def _read_occupancy(fresh=False):
    # External I/O must never hold DISPATCH_LOCK: stop acknowledgements need it.
    try:
        return sonic_service.sonic_list_device_statuses(force=fresh)
    except Exception:
        return {'__source__': {'available': False}}


def dispatch_ui_batch(batch_id):
    if not enabled() and not default_db_path().exists():
        return None
    batch = UiExecutionStore().get_batch(batch_id)
    if not batch or not enabled() or not any(a['status'] in {'queued','blocked'} for a in batch['case_attempts']):
        return batch
    occupancy = _read_occupancy()
    return _dispatch_ui_batch(batch_id, occupancy)


@serialized
def _dispatch_ui_batch(batch_id, occupancy):
    # Re-read mutable state after the unlocked network wait. SQLite reservation
    # and claim compare-and-update still arbitrate other processes.
    store = UiExecutionStore()
    batch = store.get_batch(batch_id)
    if not batch or not enabled() or batch['status'] in TERMINAL:
        return batch
    for attempt in batch['case_attempts']:
        if attempt['status'] not in {'queued','blocked'}:
            continue
        code = _target_check(batch, attempt, occupancy=occupancy)
        if code:
            store.block_attempt(attempt['attempt_id'], code)
            continue
        # Other legacy Jobs on this phone are respected too. A reserved UI job
        # never permits legacy auto-selection to steal its device (assign hook).
        job_id = attempt['dispatch_key']
        with job_service.JOB_LOCK:
            # Refresh after target checks, and keep legacy claims from racing the
            # occupancy decision/reservation. SQLite still validates the current
            # attempt and lease. No network/file write occurs in this lock span.
            jobs_by_id, running = job_service.load_ui_dispatch_jobs([job_id])
            if running.get(attempt['device_id'], set()) - {job_id}:
                store.block_attempt(attempt['attempt_id'],'device_busy')
                continue
            if not store.reserve_attempt(attempt['attempt_id']):
                store.block_attempt(attempt['attempt_id'],'device_queued')
                continue
            existing = jobs_by_id.get(job_id)
        snapshot = next(s for s in batch['snapshots'] if s['snapshot_id'] == attempt['snapshot_id'])
        if not existing:
            try:
                with actor_context(_owner(batch)):
                    existing = job_service.create_job({'job_id':job_id,'module':snapshot['metadata']['module'],
                        'file':snapshot['metadata']['file'],'target_task_name':snapshot['metadata'].get('task_name',''),
                        'device_id':attempt['device_id'],'target_runner_id':attempt['runner_id'],
                        'platform':attempt['platform'],'device_strategy':'fixed','run_mode':'baseline',
                        'auto_optimize':False,'max_attempt':1,'ui_batch_id':batch_id,
                        'ui_execution_contract':'ui-v1','ui_required_actions':attempt['compiled_case']['required_actions'],
                        'app_id':batch['app_id'],'app_package':batch['app_id'],
                        'target_package':attempt['target_resolution']['target_package']})
            except ValueError:
                with job_service.JOB_LOCK:
                    jobs_by_id, _ = job_service.load_ui_dispatch_jobs([job_id])
                    existing = jobs_by_id.get(job_id)
                if not existing:
                    raise
        if existing.get('ui_batch_id') != batch_id or existing.get('device_id') != attempt['device_id']:
            raise ValueError('dispatch_job_conflict')
        store.link_job(job_id, job_id)
    return store.get_batch(batch_id)


def prepare_ui_claim(job, occupancy=None):
    evidence = occupancy if occupancy is not None else _read_occupancy(fresh=True)
    return _prepare_ui_claim(job, evidence)


@serialized
def _prepare_ui_claim(job, occupancy):
    """Called inside the ordinary Job claim path, once; returns frozen delivery."""
    if not enabled():
        return None
    store = UiExecutionStore()
    attempt = store.attempt_for_job(job['job_id'])
    if not attempt or attempt['status'] not in {'queued','blocked'}:
        return None
    batch = store.get_batch(attempt['ui_batch_id'])
    code = _target_check(batch, attempt, occupancy=occupancy, fresh=True)
    if code:
        store.block_attempt(attempt['attempt_id'],code)
        return None
    if attempt['status'] == 'blocked' and not store.reserve_attempt(attempt['attempt_id']):
        return None
    return {'ui_batch_id':batch['ui_batch_id'],'ui_execution_contract':'ui-v1','ui_attempt_events':1,
            'ui_required_actions':attempt['compiled_case']['required_actions'],
            'ui_step_map':attempt['compiled_case']['step_map'],
            'yaml_content':attempt['compiled_case']['yaml_content'], 'app_id':batch['app_id'],
            'target_package':attempt['target_resolution']['target_package'],'platform':attempt['platform']}


def claim_ui_job(job, prepared=None):
    prepared = prepared if prepared is not None else prepare_ui_claim(job)
    if prepared is None:
        return None
    with DISPATCH_LOCK:
        if not enabled():
            return None
        token = UiExecutionStore().claim_attempt(job['job_id'])
        return {**prepared, 'ui_claim_token':token} if token else None


@serialized
def record_ui_attempt_event(job_id, sequence, event):
    store = UiExecutionStore()
    from .ui_batch_report_upload import private_callback_event
    event = private_callback_event(job_id,sequence,event)
    result = store.record_attempt_event(job_id,sequence,event)
    attempt = store.attempt_for_job(job_id)
    _sync_job(store, attempt)
    return result


def _sync_job(store, attempt):
    # SQLite is authoritative; this idempotent write is replayed after a crash.
    status = {'passed':'success','interrupted':'failed' if attempt['stop_confirmed'] else 'running',
              'cancelling':'cancelled','queued':'pending','blocked':'pending'}.get(attempt['status'],attempt['status'])
    patch = {'status':status, 'execution_progress':{}}
    if attempt['finished_ns']:
        patch['finished_at'] = time.strftime('%Y-%m-%d %H:%M:%S',time.localtime(attempt['finished_ns'] / 10**9))
    # Raw UI evidence is private; legacy Job JSON is shared historical storage.
    patch.update(stdout_tail='', stderr_tail='')
    if job_service.update_job(attempt['job_id'],patch):
        store.mark_job_synced(attempt)


@serialized
def cancel_ui_batch(actor, batch_id):
    from .ui_execution_service import get_ui_batch
    batch = get_ui_batch(actor,batch_id)
    if not batch:
        return None
    require_ui_actor(actor, app_id=batch['app_id'])
    store = UiExecutionStore()
    store.cancel_batch(batch_id)
    for attempt in store.get_batch(batch_id)['case_attempts']:
        if attempt['job_id'] and attempt['status'] in {'cancelled','cancelling'}:
            _sync_job(store, attempt)
    return store.get_batch(batch_id)


@serialized
def confirm_ui_attempt_stopped(actor, batch_id, request):
    from .ui_execution_service import get_ui_batch, BatchValidationError
    from .operation_attribution import current_actor
    keys = {'attempt_id','expected_status','expected_event_sequence','expected_recovery_version','confirmed_stopped','reason'}
    if (not isinstance(request,dict) or set(request) != keys or request.get('confirmed_stopped') is not True
            or not isinstance(request.get('attempt_id'),str)
            or request.get('expected_status') not in {'interrupted','cancelling'}
            or type(request.get('expected_event_sequence')) is not int or not 0 <= request['expected_event_sequence'] < 2**63
            or not isinstance(request.get('expected_recovery_version'),str) or not re.fullmatch(r'[0-9a-f]{64}',request['expected_recovery_version'])
            or not isinstance(request.get('reason'),str) or not 1 <= len(request['reason'].strip()) <= 500):
        raise BatchValidationError('recovery_confirmation_required')
    batch = get_ui_batch(actor,batch_id)
    if not batch:
        return None
    require_ui_actor(actor,app_id=batch['app_id'])
    store = UiExecutionStore()
    store.confirm_attempt_stopped(batch_id,request,current_actor())
    # Reconciler replays job synchronization after commit; recovery needs no file I/O.
    return store.get_batch(batch_id)


def rerun_ui_batch(actor, batch_id, scope, idempotency_key):
    from .ui_execution_service import get_ui_batch, _identifier, _request_hash, _require_ui_admission
    _require_ui_admission()
    batch = get_ui_batch(actor,batch_id)
    if not batch:
        return None
    profile = require_ui_actor(actor,app_id=batch['app_id'])
    if scope not in {'all','failed'}:
        raise ValueError('rerun_scope_invalid')
    selected = [a for a in batch['case_attempts'] if scope == 'all' or a['status'] in {'failed','cancelled','interrupted'}]
    if not selected:
        raise ValueError('rerun_empty')
    snapshots = [s for s in batch['snapshots'] if any(a['snapshot_id'] == s['snapshot_id'] for a in selected)]
    from .operation_attribution import current_actor
    # A rerun is a new test, not a new release request or notification consent.
    notifications = {**(batch['selection'].get('notifications') or {}),'enabled':False}
    prepared = {'owner_user_id':profile['user_id'],'owner_username':profile['username'],
        'owner_display_name':current_actor()['display_name'],'app_id':batch['app_id'],
        'suite_id':batch['suite_id'],'suite_revision':batch['suite_revision'],
        'idempotency_key':_identifier(idempotency_key,'idempotency_key_invalid'),
        'request_hash':_request_hash({'rerun_of':batch_id,'scope':scope}),
        'selection':{**batch['selection'],'notifications':notifications,'release_report':{'enabled':False},'rerun_of':batch_id,'scope':scope},'snapshots':snapshots,
        'attempts':[{'case_id':a['case_id'],'case_revision':a['case_revision'],
            'target':{k:a[k] for k in ('runner_id','device_id','platform')},
            'target_resolution':a['target_resolution'],'compiled_case':a['compiled_case']} for a in selected]}
    return UiExecutionStore().create_batch(prepared)


def reconcile_ui_batches(limit=50):
    if not enabled() and not default_db_path().exists():
        return
    store = UiExecutionStore()
    for batch_id in store.recoverable_batch_ids(limit):
        _reconcile_ui_batch(batch_id)
        if enabled():
            dispatch_ui_batch(batch_id)  # May wait on Sonic, outside the global lock.


@serialized
def _reconcile_ui_batch(batch_id):
    store = UiExecutionStore()
    store.mark_reconciled(batch_id)
    runners = runner_service.list_runners()
    batch = store.get_batch(batch_id)
    for attempt in batch['case_attempts']:
        if attempt['job_sync_pending'] and attempt['job_id']:
            _sync_job(store,attempt)
        if attempt['status'] != 'running':
            continue
        runner = runners.get(attempt['runner_id']) or {}
        device = next((d for d in runner.get('devices',[]) if d.get('device_id') == attempt['device_id']), {})
        if not runner.get('online') or device.get('status') not in {'online','device'}:
            store.interrupt_attempt(attempt['attempt_id'],'execution_connection_lost')
        elif time.time_ns() - (attempt['last_event_ns'] or 0) > ACK_LOST_NS:
            store.interrupt_attempt(attempt['attempt_id'],'execution_ack_lost')


def start_ui_batch_reconciler():
    global _WORKER
    if (not enabled() and not default_db_path().exists()) or (_WORKER and _WORKER.is_alive()):
        return
    def loop():
        import logging
        while enabled() or default_db_path().exists():
            try:
                reconcile_ui_batches()
                from .ui_batch_report_service import tick_ui_report_worker
                tick_ui_report_worker()
                from .ui_batch_notification_service import notifications_enabled, tick_batch_notification_worker
                if notifications_enabled():
                    tick_batch_notification_worker()
            except Exception:
                logging.getLogger(__name__).exception('UI batch reconciliation failed')
            time.sleep(5)
    _WORKER = threading.Thread(target=loop,name='ui-batch-reconciler',daemon=True)
    _WORKER.start()
