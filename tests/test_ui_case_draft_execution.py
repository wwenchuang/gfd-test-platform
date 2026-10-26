import copy
import json
from pathlib import Path
from uuid import uuid4

import pytest

from test_ui_batch_dispatch import dispatch_env
from test_ui_execution_service import environment, ACTORS, APP, TARGET
from task_server.services.operation_attribution import actor_context
from task_server.services.ui_case_draft_store import DraftStore, DraftConflict
from task_server.services.business_line_service import configured_test_application as real_app_lookup
from task_server.services import ui_case_draft_execution as execution


@pytest.fixture
def draft_env(dispatch_env, tmp_path, monkeypatch):
    monkeypatch.setenv('TASK_UI_DRAFT_DB', str(tmp_path / 'private' / 'draft.db'))
    store = DraftStore()
    draft = store.create({'user_id':'u-a','username':'a','display_name':'作者A'}, APP, '点我的检查页面')
    steps = [{'step_id':str(uuid4()),'action':action,'target':target,'expected':expected,
              'value':'','review_state':'confirmed','source_ref':'text'}
             for action,target,expected in [('aiTap','我的','显示我的页面'),('aiAssert','我的页面显示个人资料','')]]
    draft = store.revise(draft['draft_id'], 'u-a', 1, {'title':'检查我的页面','steps':steps})
    return draft, dispatch_env


def trial(draft, key='one'):
    with actor_context(ACTORS['a']):
        return execution.create_draft_trial(ACTORS['a'], draft['draft_id'], {'revision':draft['revision'],'target':TARGET}, key)


def test_trial_freezes_private_source_and_does_not_publish(draft_env):
    draft, _ = draft_env
    batch = trial(draft)
    assert batch['report_status'] == 'not_requested'
    assert batch['delivery_status'] == 'not_requested'
    assert batch['snapshots'][0]['metadata']['status'] == 'draft'
    assert batch['snapshots'][0]['metadata']['draft_revision'] == draft['revision']
    assert batch['owner_user_id'] == 'u-a'
    assert trial(draft)['ui_batch_id'] == batch['ui_batch_id']
    from task_server.services import ui_batch_dispatch as dispatch
    assert dispatch._target_check(batch, batch['case_attempts'][0]) == ''
    DraftStore().revise(draft['draft_id'], 'u-a', draft['revision'], {'title':'下一版本'})
    assert dispatch._target_check(batch, batch['case_attempts'][0]) == ''


def test_trial_rejects_injection_owner_and_unconfirmed(draft_env):
    draft, _ = draft_env
    with actor_context(ACTORS['a']):
        with pytest.raises(ValueError):
            execution.create_draft_trial(ACTORS['a'], draft['draft_id'], {'revision':2,'target':TARGET,'yaml':'evil'}, 'bad')
    with actor_context(ACTORS['b']):
        with pytest.raises(KeyError):
            execution.create_draft_trial(ACTORS['b'], draft['draft_id'], {'revision':2,'target':TARGET}, 'bad')
    edited = DraftStore().revise(draft['draft_id'], 'u-a', 2, {'questions':['还没确认']})
    with pytest.raises(ValueError):
        trial(edited)


def test_dispatch_rejects_forged_trial_or_changed_hash(draft_env):
    draft, _ = draft_env
    batch = trial(draft)
    from task_server.services import ui_batch_dispatch as dispatch
    bad = copy.deepcopy(batch)
    bad['snapshots'][0]['source_yaml'] += '\n# changed'
    assert dispatch._target_check(bad, bad['case_attempts'][0]) == 'draft_trial_gate_invalid'
    bad = copy.deepcopy(batch)
    bad['snapshots'][0]['metadata']['draft_revision'] += 1
    assert dispatch._target_check(bad, bad['case_attempts'][0]) == 'draft_trial_gate_invalid'


def passed_trial(draft, include_steps=True, progress_override=None):
    from task_server.services import ui_batch_dispatch as dispatch, job_service
    from task_server.services.ui_execution_store import UiExecutionStore
    batch = trial(draft)
    dispatch.dispatch_ui_batch(batch['ui_batch_id'])
    job_id = batch['case_attempts'][0]['dispatch_key']
    delivery = dispatch.claim_ui_job(job_service.get_job(job_id))
    assert delivery is not None
    event = {'type':'result','status':'passed','execution_stopped':True,
             'runner_id':TARGET['runner_id'],'device_id':TARGET['device_id'],
             'ui_claim_token':delivery['ui_claim_token']}
    if include_steps:
        import yaml
        tasks = yaml.safe_load(delivery['yaml_content'])['tasks']
        event['execution_progress'] = {'version':1,'tasks':[
            {'status':'passed','current_step':len(t['flow']),'total_steps':len(t['flow']),
             'steps':[{'label':'完成'} for _ in t['flow']]} for t in tasks]}
    if progress_override is not None:
        event['execution_progress']['tasks'][0].update(progress_override)
    UiExecutionStore().record_attempt_event(job_id, 1, event)
    return UiExecutionStore().get_batch(batch['ui_batch_id'])


@pytest.fixture
def publish_env(draft_env, tmp_path, monkeypatch):
    from task_server import config, identity
    from task_server.services import job_service, business_line_service
    monkeypatch.setattr(config, 'TASK_DIR', str(tmp_path / 'tasks'))
    monkeypatch.setattr(job_service, 'TASK_META_FILE', str(tmp_path / 'meta.json'))
    monkeypatch.setenv('TASK_ASSET_DB', str(tmp_path / 'private' / 'assets.db'))
    task_apps_path = tmp_path / 'task-apps.json'
    task_apps_path.write_text(json.dumps({'apps':[{'package':APP,'name':'测试应用','enabled':True,
        'platform_mappings':{'android':APP,'ios':'com.kfb.3d'},'modules':['navigation']}]}))
    monkeypatch.setattr(config, 'TASK_APPS_FILE', str(task_apps_path))
    monkeypatch.setattr(business_line_service, 'TASK_APPS_FILE', str(task_apps_path))
    monkeypatch.setattr(business_line_service, 'configured_test_application', real_app_lookup)
    assert 'modules' not in business_line_service.configured_test_application(APP)
    profile = identity.get_access_profile
    monkeypatch.setattr(identity, 'get_access_profile', lambda name:
                        {**profile(name),'permissions':profile(name)['permissions'] + ['ui.baseline']})
    return draft_env, tmp_path


def publish(draft, batch_id, module='navigation'):
    with actor_context(ACTORS['a']):
        return execution.publish_draft(ACTORS['a'], draft['draft_id'],
            {'revision':draft['revision'],'batch_id':batch_id,'module':module})


def test_publish_requires_real_completed_steps_and_current_revision(publish_env):
    (draft, _), path = publish_env
    batch = passed_trial(draft, include_steps=False)
    with pytest.raises(ValueError, match='draft_trial_steps_required'):
        publish(draft, batch['ui_batch_id'])
    assert not (path / 'tasks').exists()
    DraftStore().revise(draft['draft_id'], 'u-a', 2, {'title':'变化'})
    with pytest.raises(DraftConflict):
        publish(draft, batch['ui_batch_id'])


def test_publish_is_idempotent_and_recovers_metadata_failure(publish_env, monkeypatch):
    (draft, _), path = publish_env
    batch = passed_trial(draft)
    from task_server.services import job_service
    original = job_service.update_task_meta
    monkeypatch.setattr(job_service, 'update_task_meta', lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('disk full')))
    with pytest.raises(RuntimeError, match='disk full'):
        publish(draft, batch['ui_batch_id'])
    files = list((path / 'tasks' / 'navigation').glob('*.yaml'))
    assert len(files) == 1
    original_bytes = files[0].read_bytes()
    monkeypatch.setattr(job_service, 'update_task_meta', original)
    result = publish(draft, batch['ui_batch_id'])
    assert result['status'] == 'baseline'
    assert publish(draft, batch['ui_batch_id']) == result
    assert files[0].read_bytes() == original_bytes
    assert len(list((path / 'tasks' / 'navigation').glob('*.yaml'))) == 1


def test_publish_never_overwrites_or_crosses_module_scope(publish_env):
    (draft, _), path = publish_env
    batch = passed_trial(draft)
    with pytest.raises(ValueError, match='draft_module_invalid'):
        publish(draft, batch['ui_batch_id'], '../outside')
    target = path / 'tasks' / 'navigation' / (draft['draft_id'] + '_r2.yaml')
    target.parent.mkdir(parents=True)
    target.write_text('unrelated')
    with pytest.raises(DraftConflict, match='draft_publish_path_exists'):
        publish(draft, batch['ui_batch_id'])
    assert target.read_text() == 'unrelated'


def test_trial_target_request_has_exact_fields(draft_env):
    draft, _ = draft_env
    with actor_context(ACTORS['a']):
        with pytest.raises(ValueError, match='draft_target_invalid'):
            execution.create_draft_trial(ACTORS['a'], draft['draft_id'],
                {'revision':draft['revision'],'target':{**TARGET,'yaml_content':'unsafe'}}, 'bad-target')


def test_draft_dispatch_rechecks_permissions_and_version(draft_env, monkeypatch):
    draft, (runners, _) = draft_env
    batch = trial(draft)
    from task_server.services import ui_batch_dispatch as dispatch
    runners['mac']['devices'][0]['installed_apps'][0]['version_code'] = 'changed'
    assert dispatch._target_check(batch, batch['case_attempts'][0]) == 'app_version_changed'
    from task_server import identity
    profile = identity.get_access_profile
    monkeypatch.setattr(identity, 'get_access_profile', lambda name: {**profile(name),'scope':{'ui_apps':[]}})
    assert dispatch._target_check(batch, batch['case_attempts'][0]) == 'app_forbidden'


def test_publish_permission_revocation_prevents_writes(publish_env, monkeypatch):
    (draft, _), path = publish_env
    batch = passed_trial(draft)
    from task_server import identity
    profile = identity.get_access_profile
    monkeypatch.setattr(identity, 'get_access_profile', lambda name:
                        {**profile(name),'permissions':['ui.view','ui.execute']})
    with pytest.raises(PermissionError):
        publish(draft, batch['ui_batch_id'])
    assert not (path / 'tasks').exists()


def test_publish_rejects_shared_module_and_invalidates_catalog(publish_env, monkeypatch):
    (draft, _), path = publish_env
    batch = passed_trial(draft)
    from task_server.services import business_line_service, ui_execution_catalog
    from task_server import config
    apps = json.loads(Path(config.TASK_APPS_FILE).read_text())
    apps['apps'].append({'package':'com.other.app','name':'其他应用','modules':['navigation'],'enabled':True})
    Path(config.TASK_APPS_FILE).write_text(json.dumps(apps))
    with pytest.raises(ValueError, match='draft_module_invalid'):
        publish(draft, batch['ui_batch_id'])
    assert not (path / 'tasks').exists()
    apps['apps'].pop()
    Path(config.TASK_APPS_FILE).write_text(json.dumps(apps))
    cleared = []
    monkeypatch.setattr(ui_execution_catalog, 'clear_catalog_cache', lambda: cleared.append(True))
    result = publish(draft, batch['ui_batch_id'])
    assert cleared == [True]
    assert result['case_id']


def test_pending_publication_before_atomic_write_has_no_empty_placeholder(publish_env, monkeypatch):
    (draft, _), path = publish_env
    batch = passed_trial(draft)
    from task_server import storage
    original = storage.write_text_file
    monkeypatch.setattr(storage, 'write_text_file', lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('crash before atomic write')))
    with pytest.raises(RuntimeError, match='crash before atomic write'):
        publish(draft, batch['ui_batch_id'])
    target = path / 'tasks' / 'navigation' / (draft['draft_id'] + '_r2.yaml')
    assert not target.exists()
    monkeypatch.setattr(storage, 'write_text_file', original)
    assert publish(draft, batch['ui_batch_id'])['status'] == 'baseline'
    assert target.read_text()


def test_pending_publication_does_not_overwrite_third_party_file(publish_env, monkeypatch):
    (draft, _), path = publish_env
    batch = passed_trial(draft)
    from task_server import storage
    original = storage.write_text_file
    monkeypatch.setattr(storage, 'write_text_file', lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('write failed')))
    with pytest.raises(RuntimeError):
        publish(draft, batch['ui_batch_id'])
    target = path / 'tasks' / 'navigation' / (draft['draft_id'] + '_r2.yaml')
    target.write_text('第三方内容')
    monkeypatch.setattr(storage, 'write_text_file', original)
    with pytest.raises(DraftConflict, match='draft_publish_path_exists'):
        publish(draft, batch['ui_batch_id'])
    assert target.read_text() == '第三方内容'


@pytest.mark.parametrize('progress', [
    {'steps':[]}, {'current_step':None}, {'current_step':0},
    {'steps':[{'label':'只报告首步'}]},
])
def test_publish_rejects_incomplete_raw_authenticated_step_evidence(publish_env, progress):
    (draft, _), path = publish_env
    batch = passed_trial(draft, progress_override=progress)
    assert batch['status'] == 'passed'  # Overall result alone is insufficient.
    with pytest.raises(ValueError, match='draft_trial_steps_required'):
        publish(draft, batch['ui_batch_id'])
    assert not (path / 'tasks').exists()


def test_publish_accepts_actual_observer_final_zero_based_cursor(publish_env):
    (draft, _), _ = publish_env
    batch = passed_trial(draft, progress_override={'current_step':5})
    assert batch['case_attempts'][0]['execution_progress']['tasks'][0]['total_steps'] == 6
    assert publish(draft, batch['ui_batch_id'])['status'] == 'baseline'


def test_published_asset_retry_checks_exact_bytes(publish_env):
    (draft, _), path = publish_env
    batch = passed_trial(draft)
    result = publish(draft, batch['ui_batch_id'])
    target = path / 'tasks' / result['module'] / result['file']
    target.write_bytes(target.read_bytes().replace(b'\n', b'\r\n'))
    with pytest.raises(DraftConflict, match='draft_publish_asset_changed'):
        publish(draft, batch['ui_batch_id'])


def test_staged_trial_freezes_all_tasks_and_rejects_partial_stage_evidence(draft_env):
    import yaml
    draft,_=draft_env
    steps=copy.deepcopy(draft['steps'])
    steps[-1]['stage_end']=True
    second=copy.deepcopy(steps)
    for step in second:step['step_id']=str(uuid4());step.pop('stage_end',None)
    draft=DraftStore().revise(draft['draft_id'],'u-a',draft['revision'],{'steps':steps+second})
    batch=passed_trial(draft)
    attempt=batch['case_attempts'][0]
    assert len(yaml.safe_load(attempt['compiled_case']['yaml_content'])['tasks'])==2
    assert execution.validate_trial_snapshot(batch,attempt)
    assert len(attempt['execution_progress']['tasks'])==2
    # Publication must not turn a passed summary into proof of missing stages.
    partial=copy.deepcopy(batch);partial['case_attempts'][0]['execution_progress']['tasks'].pop()
    from unittest.mock import patch
    with actor_context(ACTORS['a']), patch.object(execution.UiExecutionStore,'get_batch',return_value=partial):
        with pytest.raises(execution.BatchValidationError,match='draft_trial_steps_required'):
            execution._verified_publish_batch(ACTORS['a'],draft,batch['ui_batch_id'])
