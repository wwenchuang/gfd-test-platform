from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest


@pytest.mark.parametrize('name', [
    '_post_agent_runs_start', '_post_ui_generate_yaml',
    '_post_ui_generate_yaml_async', '_post_ui_regenerate_yaml_async',
])
def test_generation_missing_yaml_dependency_fails_before_work(monkeypatch, name):
    from task_server import router
    from task_server.services import yaml_service

    monkeypatch.setattr(yaml_service, '_pyyaml', None)
    responses = []
    handler = SimpleNamespace(
        _body=lambda: {},
        _json=lambda payload, code=200: responses.append((code, payload)),
    )
    getattr(router, name)(handler, {})
    assert responses[-1][0] == 503
    assert responses[-1][1]['code'] == 'YAML_DEPENDENCY_UNAVAILABLE'
    assert '虚拟环境' in responses[-1][1]['error']


@pytest.fixture
def repair_store(tmp_path, monkeypatch):
    from task_server.services import repair_service as service

    monkeypatch.setattr(service, 'TASK_DIR', str(tmp_path / 'tasks'))
    monkeypatch.setattr(service, 'VERSION_DIR', str(tmp_path / 'versions'))
    monkeypatch.setattr(service, 'REPAIR_DRAFTS_FILE', str(tmp_path / 'drafts.json'))
    target = tmp_path / 'tasks' / 'audit' / 'sample.yaml'
    target.parent.mkdir(parents=True)
    target.write_text('original yaml\n')
    service.upsert_repair_draft({
        'draftId': 'audit-draft', 'status': 'WAIT_CONFIRM',
        'module': 'audit', 'file': 'sample.yaml',
        'originalYaml': 'original yaml\n', 'fixedYaml': 'fixed yaml\n',
    })
    return service, target


def test_old_draft_cannot_overwrite_new_yaml(repair_store):
    service, target = repair_store
    target.write_text('new manual edit\n')
    with pytest.raises(service.RepairApplyError, match='已变化'):
        service.apply_repair_draft('audit-draft', confirm=True)
    assert target.read_text() == 'new manual edit\n'
    assert service.get_repair_draft('audit-draft')['status'] == 'WAIT_CONFIRM'


def test_backup_failure_blocks_replacement(repair_store, monkeypatch):
    service, target = repair_store
    monkeypatch.setattr(service, 'backup_before_repair', lambda *a, **kw: None)
    with pytest.raises(service.RepairApplyError, match='备份'):
        service.apply_repair_draft('audit-draft', confirm=True)
    assert target.read_text() == 'original yaml\n'


def test_repair_backup_uses_history_directory_convention(repair_store):
    from task_server.storage import clean_id
    service, _ = repair_store
    from pathlib import Path
    filename = '中文 file (test).yaml'
    expected = Path(service.VERSION_DIR) / clean_id('audit', 'module') / clean_id(filename, 'file')
    assert Path(service._version_dir_for('audit', filename)) == expected


def test_concurrent_apply_only_applies_once_and_keeps_original_backup(repair_store):
    service, target = repair_store

    def apply():
        try:
            return service.apply_repair_draft('audit-draft', confirm=True)['applied']
        except service.RepairApplyError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: apply(), range(2))) == [False, True]
    assert target.read_text() == 'fixed yaml\n'
    backups = [p for p in target.parents[2].glob('versions/**/*.yaml') if p.is_file()]
    assert len(backups) == 1
    assert backups[0].read_text() == 'original yaml\n'


def test_reject_route_does_not_change_already_applied_draft(repair_store):
    from task_server import router
    service, _ = repair_store
    service.apply_repair_draft('audit-draft', confirm=True)
    responses = []
    handler = SimpleNamespace(
        _body=lambda: {'draftId': 'audit-draft'},
        _json=lambda payload, code=200: responses.append((code, payload)),
    )
    router._post_repair_drafts_reject(handler, {})
    assert responses[-1][0] == 409
    assert service.get_repair_draft('audit-draft')['status'] == 'APPLIED'


def test_file_save_waits_for_repair_snapshot_and_backup(repair_store, monkeypatch):
    from task_server import router
    service, target = repair_store
    monkeypatch.setattr(router, 'TASK_DIR', service.TASK_DIR)
    monkeypatch.setattr(router, 'save_file_version', lambda *a, **kw: {})
    entered, release, saved = Event(), Event(), Event()
    original_backup = service.backup_before_repair

    def slow_backup(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return original_backup(*args, **kwargs)

    monkeypatch.setattr(service, 'backup_before_repair', slow_backup)

    def manual_save():
        handler = SimpleNamespace(_body=lambda: {'module': 'audit', 'file': 'sample.yaml', 'content': 'manual edit'}, _json=lambda *args: None)
        router._post_file_save(handler, {})
        saved.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        repair = pool.submit(service.apply_repair_draft, 'audit-draft', confirm=True)
        assert entered.wait(3)
        manual = pool.submit(manual_save)
        try:
            assert not saved.wait(0.1), 'Manual writes must not enter between snapshot comparison and replacement'
        finally:
            release.set()
        assert repair.result()['applied']
        manual.result()
    assert target.read_text() == 'manual edit'


def test_file_save_skips_identical_content_without_new_history(repair_store, monkeypatch):
    from task_server import router
    service, target = repair_store
    monkeypatch.setattr(router, 'TASK_DIR', service.TASK_DIR)
    backups = []
    writes = []
    monkeypatch.setattr(router, 'save_file_version', lambda *a, **kw: backups.append((a, kw)))
    monkeypatch.setattr(router, 'write_text_file', lambda *a, **kw: writes.append((a, kw)))
    responses = []
    handler = SimpleNamespace(
        _body=lambda: {'module': 'audit', 'file': 'sample.yaml', 'content': target.read_text()},
        _json=lambda payload, code=200: responses.append((code, payload)),
    )

    router._post_file_save(handler, {})

    assert responses == [(200, {'ok': True, 'unchanged': True})]
    assert backups == []
    assert writes == []


def test_agent_save_does_not_reopen_a_rejected_draft(repair_store):
    from task_server.services import agent_service
    service, _ = repair_store
    service.reject_repair_draft('audit-draft')
    result = agent_service.tool_save_repair_draft({}, {'draftId': 'audit-draft', 'analysis': 'late AI update'})
    assert result.get('ok') is False
    assert service.get_repair_draft('audit-draft')['status'] == 'REJECTED'


def test_agent_apply_cannot_report_applied_without_valid_yaml(repair_store):
    from task_server.services import agent_service
    service, target = repair_store
    result = agent_service.tool_apply_repair_after_confirm({}, {'draftId': 'audit-draft'})
    assert result['ok'] is False
    assert service.get_repair_draft('audit-draft')['status'] == 'WAIT_CONFIRM'
    assert target.read_text() == 'original yaml\n'


def test_ai_tool_cannot_supply_its_own_manual_confirmation(repair_store, monkeypatch):
    from task_server.services import agent_service, yaml_service
    service, target = repair_store
    monkeypatch.setattr(yaml_service, 'validate_midscene_yaml_executability', lambda text: {'ok': True})
    result = agent_service.tool_apply_repair_after_confirm({}, {'draftId': 'audit-draft', 'confirmApply': True, 'confirmRisk': True})
    assert result['ok'] is False
    assert '待我处理' in result['error']
    assert service.get_repair_draft('audit-draft')['status'] == 'WAIT_CONFIRM'
    assert target.read_text() == 'original yaml\n'


@pytest.mark.parametrize('status', ['APPLIED', 'REJECTED', 'EXPIRED'])
def test_agent_cannot_save_a_forged_new_terminal_draft(repair_store, status):
    from task_server.services import agent_service
    service, target = repair_store
    result = agent_service.tool_save_repair_draft({}, {
        'draftId': 'forged-new', 'status': status, 'fixedYaml': 'invalid yaml',
        'appliedAt': 'forged', 'backup': {'id': 'forged-backup'},
    })
    assert result['ok'] is False
    assert service.get_repair_draft('forged-new') is None
    assert target.read_text() == 'original yaml\n'


def test_pending_draft_save_drops_forged_outcome_metadata(repair_store):
    service, _ = repair_store
    saved = service.upsert_repair_draft({
        'draftId': 'audit-draft', 'status': 'WAIT_CONFIRM',
        'appliedAt': 'forged', 'applied_at': 'forged',
        'rejectedAt': 'forged', 'rejected_at': 'forged',
        'rejectReason': 'forged', 'backup': {'id': 'forged-backup'},
    })
    assert saved['status'] == 'WAIT_CONFIRM'
    for field in ('appliedAt', 'applied_at', 'rejectedAt', 'rejected_at', 'rejectReason', 'backup'):
        assert field not in saved


def test_platform_rejected_diagnosis_is_persisted_in_shared_store(repair_store, monkeypatch):
    from task_server.services import agent_service
    service, _ = repair_store
    monkeypatch.setattr(agent_service, 'TASK_DIR', service.TASK_DIR)
    monkeypatch.setattr(agent_service, '_ai_gateway_available', lambda: False)
    monkeypatch.setattr(agent_service, '_agent_repair_eligibility', lambda *a, **kw: {
        'eligible': False, 'failureType': 'ENV_ISSUE', 'code': 'device_offline', 'reason': '设备离线',
    })
    monkeypatch.setattr(agent_service, '_log_tool_call', lambda *a, **kw: None)
    run = {'runId': 'audit-diagnosis', 'artifacts': {}}
    call = agent_service._tool_generate_repair(run, failed_jobs_override=[{
        'jobId': 'audit-offline', 'module': 'audit', 'file': 'sample.yaml', 'taskName': '设备离线', 'error': '设备离线',
    }])
    assert call['status'] == 'SKIPPED', call
    ids = call['repairDraftIds']
    assert len(ids) == 1
    persisted = service.get_repair_draft(ids[0])
    assert persisted is not None, 'Every diagnostic ID returned to the UI must be discoverable in the draft store'
    assert persisted['status'] == 'REJECTED'
    assert persisted['repairSource'] == 'diagnosis_only'
