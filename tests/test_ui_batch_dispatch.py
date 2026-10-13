"""UI dispatch uses the existing durable Job queue, with exact-device reservations."""
import copy
import pytest
from test_ui_execution_service import environment, ACTORS, APP, TARGET, request, SOURCE
from task_server.services.operation_attribution import actor_context
from task_server.services.job_audit import record_created as record_job_created


@pytest.fixture
def dispatch_env(environment, tmp_path, monkeypatch):
    from task_server.services import runner_service, job_service, sonic_service, job_audit
    monkeypatch.setenv('UI_SIMPLE_FLOW_ENABLED', '1')
    jobs_file = str(tmp_path / 'jobs.json')
    monkeypatch.setattr(job_service, 'JOBS_FILE', jobs_file)
    monkeypatch.setattr(runner_service, 'JOBS_FILE', jobs_file)
    monkeypatch.setattr(job_audit, 'record_created', lambda *_: None)
    runners = copy.deepcopy(runner_service.list_runners())
    runners['mac']['capabilities']['ui_attempt_events'] = 1
    runners['mac']['capabilities']['ui_private_reports'] = 1
    runners['mac']['capabilities']['ui_process_stop_proof'] = 1
    runners['mac']['devices'][0]['installed_apps'] = [{'package': 'com.kfb.3d', 'installed': True, 'version_name': '1', 'version_code': '2'}]
    runners['mac']['devices'].append({**runners['mac']['devices'][0], 'device_id': 'phone2'})
    monkeypatch.setattr(runner_service, 'list_runners', lambda: runners)
    monkeypatch.setattr(runner_service, 'load_runners', lambda: runners)
    monkeypatch.setattr(runner_service, 'save_runners', lambda *_: None)
    sonic = {'__source__': {'available': True}, 'iphone': {'usage_status': 'idle'}, 'phone2': {'usage_status': 'idle'}}
    monkeypatch.setattr(sonic_service, 'sonic_list_device_statuses', lambda **_: sonic)
    return runners, sonic


def create(name='a', key='one', targets=None):
    from task_server.services.ui_execution_service import create_ui_batch
    selected = request()
    if targets:
        selected['targets'] = targets
    with actor_context(ACTORS[name]):
        return create_ui_batch(ACTORS[name], selected, key)


def test_dispatch_freezes_yaml_and_stable_step_ids(dispatch_env, monkeypatch):
    from task_server.services import ui_batch_dispatch as dispatch, case_service, job_service
    batch = create()
    attempt = batch['case_attempts'][0]
    assert len(attempt['compiled_case']['step_map']) == 3
    monkeypatch.setattr(case_service, 'task_case_yaml', lambda _: 'BROKEN changed source')
    dispatch.dispatch_ui_batch(batch['ui_batch_id'])
    job = job_service.get_job(attempt['dispatch_key'])
    assert job['target_runner_id'] == 'mac' and job['device_id'] == 'iphone'
    assert job['run_mode'] == 'baseline' and job['auto_optimize'] is False
    assert dispatch.claim_ui_job(job)['yaml_content'] == attempt['compiled_case']['yaml_content']
    assert dispatch.claim_ui_job(job) is None  # no second delivery, even same Runner


def test_claim_rechecks_stop_proof_after_job_was_queued(dispatch_env):
    from task_server.services import ui_batch_dispatch as dispatch, job_service
    batch = create()
    dispatch.dispatch_ui_batch(batch['ui_batch_id'])
    job = job_service.get_job(batch['case_attempts'][0]['dispatch_key'])
    dispatch_env[0]['mac']['capabilities'].pop('ui_process_stop_proof')
    assert dispatch.claim_ui_job(job) is None
    from task_server.services.ui_execution_store import UiExecutionStore
    assert UiExecutionStore().attempt_for_job(job['job_id'])['block_code'] == 'runner_upgrade_required'


def test_same_phone_serializes_but_different_phone_parallel(dispatch_env):
    from task_server.services import ui_batch_dispatch as dispatch, job_service
    a, b = create(), create('b')
    c = create('b', 'other', [{**TARGET, 'device_id': 'phone2'}])
    for batch in (a,b,c):
        dispatch.dispatch_ui_batch(batch['ui_batch_id'])
    assert job_service.get_job(a['case_attempts'][0]['dispatch_key'])
    assert job_service.get_job(b['case_attempts'][0]['dispatch_key']) is None
    assert job_service.get_job(c['case_attempts'][0]['dispatch_key'])


@pytest.mark.parametrize('mutation,code', [
    ('sonic_missing','occupancy_unavailable'),('remote_control','device_busy'),
    ('offline','runner_offline'),('old','runner_upgrade_required'),('unsafe_stop','runner_upgrade_required'),('version','app_version_changed'),
    ('revoked','app_forbidden')])
def test_dispatch_rechecks_current_evidence(dispatch_env, monkeypatch, mutation, code):
    from task_server.services import ui_batch_dispatch as dispatch, job_service
    from task_server import identity
    runners, sonic = dispatch_env
    batch = create()
    if mutation == 'sonic_missing': sonic['__source__']['available'] = False
    if mutation == 'remote_control': sonic['iphone']['usage_status'] = 'debugging'
    if mutation == 'offline': runners['mac']['online'] = False
    if mutation == 'old': runners['mac']['capabilities'].pop('ui_attempt_events')
    if mutation == 'unsafe_stop': runners['mac']['capabilities'].pop('ui_process_stop_proof')
    if mutation == 'version': runners['mac']['devices'][0]['installed_apps'][0]['version_code'] = '3'
    if mutation == 'revoked':
        original = identity.get_access_profile
        monkeypatch.setattr(identity, 'get_access_profile', lambda name: {**original(name), 'scope': {'ui_apps': []}})
    result = dispatch.dispatch_ui_batch(batch['ui_batch_id'])
    assert result['case_attempts'][0]['block_code'] == code
    assert job_service.get_job(batch['case_attempts'][0]['dispatch_key']) is None


def test_preflight_explicitly_blocks_unresolved_suite_and_data(dispatch_env):
    from task_server.services.ui_execution_service import preflight_ui_batch
    with actor_context(ACTORS['a']):
        for changed, code in [({'suite_id':'native','suite_revision':'1'}, 'suite_execution_unavailable'),
                              ({'test_data_refs':['fixture']}, 'test_data_unavailable')]:
            result = preflight_ui_batch(ACTORS['a'], {**request(), **changed})
            assert result['ready'] is False
            assert code in [b['code'] for b in result['blockers']]


def test_existing_job_create_crash_relinks_same_id(dispatch_env, monkeypatch):
    from task_server.services import ui_batch_dispatch as dispatch, job_service
    from task_server.services.ui_execution_store import UiExecutionStore
    batch = create()
    original = UiExecutionStore.link_job
    monkeypatch.setattr(UiExecutionStore, 'link_job', lambda *a: (_ for _ in ()).throw(RuntimeError('crash')))
    with pytest.raises(RuntimeError): dispatch.dispatch_ui_batch(batch['ui_batch_id'])
    job = job_service.get_job(batch['case_attempts'][0]['dispatch_key'])
    assert job
    monkeypatch.setattr(UiExecutionStore, 'link_job', original)
    assert dispatch.dispatch_ui_batch(batch['ui_batch_id'])['case_attempts'][0]['job_id'] == job['job_id']
    assert len(job_service.load_jobs(limit=None)) == 1


def test_existing_http_claim_delivers_frozen_yaml_without_current_file_or_meta(dispatch_env, monkeypatch, tmp_path):
    from task_server import router
    from task_server.services import ui_batch_dispatch as dispatch
    batch = create()
    dispatch.dispatch_ui_batch(batch['ui_batch_id'])
    monkeypatch.setattr(router, '_require_runner_auth', lambda _: False)
    monkeypatch.setattr(router, 'TASK_DIR', str(tmp_path / 'missing'))
    monkeypatch.setattr(router, 'update_task_meta', lambda *a: pytest.fail('current source meta mutated'))
    from task_server.services import job_audit
    monkeypatch.setattr(job_audit, 'record_dispatch', lambda *_: None)
    class Handler:
        def _json(self, payload, status=200): self.response = payload,status
    handler = Handler()
    router._get_runner_jobs_next(handler, {'runner_id':'mac','devices':'iphone'})
    body, status = handler.response
    assert status == 200 and body['job']['ui_claim_token']
    assert body['job']['yaml_content'] == batch['case_attempts'][0]['compiled_case']['yaml_content']
    assert body['job']['ui_step_map']
    router._get_runner_jobs_next(handler, {'runner_id':'mac','devices':'iphone'})
    assert handler.response[0]['job'] is None


def test_legacy_job_cannot_steal_reserved_phone(dispatch_env):
    from task_server.services import ui_batch_dispatch as dispatch, job_service, runner_service
    # Legacy is deliberately queued first.
    legacy = job_service.create_job({'device_id':'iphone','target_runner_id':'mac'})
    batch = create()
    dispatch.dispatch_ui_batch(batch['ui_batch_id'])
    result = runner_service.assign_job('mac', ['iphone'])
    assert result['job_id'] != legacy['job_id']
    assert result['job_id'] == batch['case_attempts'][0]['dispatch_key']


def test_preflight_rejects_stale_case_and_sonic_busy(dispatch_env):
    from task_server.services.ui_execution_service import preflight_ui_batch
    with actor_context(ACTORS['a']):
        bad = request()
        bad['cases'][0]['revision'] = '0' * 64
        result = preflight_ui_batch(ACTORS['a'], bad)
        assert result['ready'] is False
        assert result['blockers'][0]['code'] == 'case_revision_changed'
        dispatch_env[1]['iphone']['usage_status'] = 'debugging'
        result = preflight_ui_batch(ACTORS['a'], request())
        assert result['ready'] is False
        assert result['blockers'][0]['code'] == 'device_busy'


def test_flag_disable_still_protects_existing_claimed_device(dispatch_env, monkeypatch):
    from task_server.services import ui_batch_dispatch as dispatch, runner_service, job_service
    batch = create()
    dispatch.dispatch_ui_batch(batch['ui_batch_id'])
    first = runner_service.assign_job('mac',['iphone'])
    assert first
    monkeypatch.setenv('UI_SIMPLE_FLOW_ENABLED','0')
    job_service.create_job({'target_runner_id':'mac','device_id':'iphone'})
    assert runner_service.assign_job('mac',['iphone']) is None


def test_current_owner_disabled_and_device_disappeared_at_claim_fail_closed(dispatch_env, monkeypatch):
    from task_server.services import ui_batch_dispatch as dispatch, runner_service
    from task_server import identity
    batch = create()
    dispatch.dispatch_ui_batch(batch['ui_batch_id'])
    original = identity.get_access_profile
    monkeypatch.setattr(identity,'get_access_profile',lambda name:{**original(name),'status':'disabled'})
    assert runner_service.assign_job('mac',['iphone']) is None
    monkeypatch.setattr(identity,'get_access_profile',original)
    dispatch_env[0]['mac']['devices'] = []
    assert runner_service.assign_job('mac',['iphone']) is None


def test_physical_phone_reserved_across_duplicate_runner_registration(dispatch_env):
    from task_server.services import ui_batch_dispatch as dispatch, job_service
    runners, sonic = dispatch_env
    runners['duplicate'] = copy.deepcopy(runners['mac'])
    a = create()
    b = create('b','duplicate',[{**TARGET,'runner_id':'duplicate'}])
    dispatch.dispatch_ui_batch(a['ui_batch_id'])
    dispatch.dispatch_ui_batch(b['ui_batch_id'])
    assert job_service.get_job(b['case_attempts'][0]['dispatch_key']) is None


def test_claim_forces_fresh_sonic_occupancy_after_reservation(dispatch_env, monkeypatch):
    from task_server.services import ui_batch_dispatch as dispatch, job_service, sonic_service
    batch = create()
    dispatch.dispatch_ui_batch(batch['ui_batch_id'])
    calls = []
    def statuses(force=False):
        calls.append(force)
        return {'__source__':{'available':True},'iphone':{'usage_status':'debugging' if force else 'idle'}}
    monkeypatch.setattr(sonic_service,'sonic_list_device_statuses',statuses)
    job = job_service.get_job(batch['case_attempts'][0]['dispatch_key'])
    assert dispatch.claim_ui_job(job) is None
    assert calls == [True]


@pytest.mark.parametrize('missing', [('', ''), ('1.2', ''), (' ', '2'), (None, '2')])
@pytest.mark.parametrize('evidence', ['frozen', 'current', 'both'])
def test_incomplete_installed_version_evidence_blocks_dispatch(dispatch_env, missing, evidence):
    from task_server.services import ui_batch_dispatch as dispatch
    app = dispatch_env[0]['mac']['devices'][0]['installed_apps'][0]
    original = app.copy()
    if evidence in {'frozen','both'}:
        app.update(version_name=missing[0], version_code=missing[1])
    batch = create()
    if evidence == 'frozen': app.update(original)
    if evidence == 'current': app.update(version_name=missing[0], version_code=missing[1])
    result = dispatch.dispatch_ui_batch(batch['ui_batch_id'])
    assert result['case_attempts'][0]['block_code'] == 'app_version_unavailable'


@pytest.mark.parametrize('operation', ['claim','dispatch','reconcile'])
def test_slow_sonic_does_not_block_events_and_cancel_wins_before_mutation(dispatch_env, monkeypatch, operation):
    import concurrent.futures
    import threading
    from task_server.services import ui_batch_dispatch as dispatch, runner_service, job_service, sonic_service
    a = create()
    dispatch.dispatch_ui_batch(a['ui_batch_id'])
    claimed_job = runner_service.assign_job('mac',['iphone'])
    payload = claimed_job['_ui_delivery']
    b = create('b','other',[{**TARGET,'device_id':'phone2'}])
    if operation != 'dispatch': dispatch.dispatch_ui_batch(b['ui_batch_id'])
    entered, release = threading.Event(), threading.Event()
    def slow_statuses(**kwargs):
        entered.set()
        assert release.wait(5)
        return dispatch_env[1]
    monkeypatch.setattr(sonic_service,'sonic_list_device_statuses',slow_statuses)
    def cancel():
        with actor_context(ACTORS['b']):
            return dispatch.cancel_ui_batch(ACTORS['b'],b['ui_batch_id'])
    operations = {'claim':lambda:runner_service.assign_job('mac',['phone2']),
                  'dispatch':lambda:dispatch.dispatch_ui_batch(b['ui_batch_id']),
                  'reconcile':lambda:dispatch.reconcile_ui_batches()}
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        waiting = pool.submit(operations[operation])
        try:
            assert entered.wait(2)
            callback = pool.submit(dispatch.record_ui_attempt_event,claimed_job['job_id'],1,
                                  {'runner_id':'mac','device_id':'iphone','ui_claim_token':payload['ui_claim_token'],'type':'progress'})
            assert callback.result(timeout=1)['ok']
            assert pool.submit(cancel).result(timeout=1)['status'] == 'cancelled'
        finally:
            release.set()
        result = waiting.result(timeout=3)
    if operation == 'claim': assert result is None
    b_job = job_service.get_job(b['case_attempts'][0]['dispatch_key'])
    assert b_job is None or b_job['status'] == 'cancelled'


def test_runner_without_private_report_upload_is_blocked(dispatch_env):
    from task_server.services import ui_batch_dispatch as dispatch, job_service
    dispatch_env[0]['mac']['capabilities'].pop('ui_private_reports',None)
    batch=create()
    result=dispatch.dispatch_ui_batch(batch['ui_batch_id'])
    assert result['case_attempts'][0]['block_code'] == 'runner_upgrade_required'
    assert job_service.get_job(batch['case_attempts'][0]['dispatch_key']) is None


def test_background_job_creation_and_runner_dispatch_keep_frozen_owner(dispatch_env, tmp_path, monkeypatch):
    from task_server.services import ui_batch_dispatch as dispatch, job_service, job_audit
    from task_server.services.operation_attribution import OperationStore
    from task_server import identity
    operation_path = tmp_path / 'audit.sqlite3'
    monkeypatch.setenv('TASK_OPERATION_DB', str(operation_path))
    # dispatch_env suppresses old job audit; restore its real public entry point.
    monkeypatch.setattr(job_audit, 'record_created', record_job_created)
    batch = create()
    with actor_context(ACTORS['b']):
        dispatch.dispatch_ui_batch(batch['ui_batch_id'])
    job = job_service.get_job(batch['case_attempts'][0]['dispatch_key'])
    with actor_context({'kind':'runner','username':'runner','display_name':'Runner'}):
        job_audit.record_dispatch(job['job_id'], batch['case_attempts'][0]['compiled_case']['yaml_content'])
    rows = OperationStore(operation_path).list_events(identity.get_access_profile('admin'), all_actors=True)['events']
    creation = next(row for row in rows if row['action'] == 'job.created')
    delivery = next(row for row in rows if row['action'] == 'job.dispatch_prepared')
    assert creation['actor'] == ACTORS['a']
    assert delivery['actor']['kind'] == 'runner'
    assert delivery['initiator_user_id'] == ACTORS['a']['user_id']
    assert len(OperationStore(operation_path).list_events(identity.get_access_profile('a'))['events']) == 2
    assert not OperationStore(operation_path).list_events(identity.get_access_profile('b'))['events']


@pytest.mark.parametrize('profile_change', ['disabled', 'deleted', 'replaced'])
def test_unavailable_owner_cannot_create_attributed_background_job(dispatch_env, tmp_path, monkeypatch, profile_change):
    from task_server import identity
    from task_server.services import ui_batch_dispatch as dispatch, job_service, job_audit
    from task_server.services.operation_attribution import OperationStore
    operation_path = tmp_path / 'audit.sqlite3'
    monkeypatch.setenv('TASK_OPERATION_DB', str(operation_path))
    monkeypatch.setattr(job_audit, 'record_created', record_job_created)
    batch = create()
    original = identity.get_access_profile
    def changed(name):
        profile = original(name)
        if name != 'a': return profile
        if profile_change == 'deleted': return None
        return {**profile, **({'status':'disabled'} if profile_change == 'disabled' else {'user_id':'new-owner'})}
    monkeypatch.setattr(identity, 'get_access_profile', changed)
    with actor_context(ACTORS['b']):
        result = dispatch.dispatch_ui_batch(batch['ui_batch_id'])
    assert result['case_attempts'][0]['block_code'] == 'account_unavailable'
    assert job_service.get_job(batch['case_attempts'][0]['dispatch_key']) is None
    assert not OperationStore(operation_path).list_events(original('admin'), all_actors=True)['events']
