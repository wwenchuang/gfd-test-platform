"""Encrypted durable monitoring revisions, authorization and environment isolation."""
import json
import secrets
import pytest
from task_server.api_testing import access
from task_server.api_testing.crypto import decrypt_secret
from task_server.api_testing.models.environment import ApiSecretValue
from task_server.api_testing.models.load_monitoring import ApiLoadMonitoringRevision
from task_server.api_testing.services.load_monitoring_config_service import LoadMonitoringConfigService
from tests.api_testing.test_load_testing_repository import load_factory, load_records


@pytest.fixture
def catalog(load_factory, load_records, monkeypatch):
    monkeypatch.setenv('API_TESTING_SECRET_KEY', secrets.token_urlsafe(48))
    profile = {'status': 'active', 'is_superuser': True}
    monkeypatch.setattr(access, 'get_access_profile', lambda actor: profile if actor == 'load-owner' else {'status': 'active', 'permissions': ['api.loadtest.view'], 'scope': {}})
    return LoadMonitoringConfigService(load_factory), load_records, profile


def payload(**changes):
    return dict(name='主机指标', source_url='https://monitor.example.com', labels={'instance': 'node:9100'}, authorize_host=True, token='sensitive-bearer-123', **changes)


def env_id(records):
    return records['environment_revision'].id


def test_secret_rotation_and_frozen_revision(catalog, load_factory):
    svc, records, _ = catalog
    original = svc.create(env_id(records), payload(), actor='load-owner')
    assert original['has_token'] is True
    assert 'sensitive-bearer' not in json.dumps(original)
    snapshot = svc.get_snapshot(original['revision_id'], actor='load-owner', environment_revision_id=env_id(records))
    svc.update(original['id'], {'name': '新名称', 'token': 'rotated-bearer'}, actor='load-owner')
    assert snapshot == svc.get_snapshot(original['revision_id'], actor='load-owner', environment_revision_id=env_id(records))
    with load_factory() as session:
        old = session.get(ApiLoadMonitoringRevision, original['revision_id'])
        secret = session.get(ApiSecretValue, old.secret_value_id)
        assert 'sensitive-bearer' not in secret.ciphertext
        assert decrypt_secret(secret.ciphertext) == 'sensitive-bearer-123'
    assert svc.list(env_id(records), actor='load-owner')['items'][-1]['name'] == '新名称'


def test_host_approval_and_scopes(catalog):
    svc, records, profile = catalog
    data = payload()
    data.pop('authorize_host')
    with pytest.raises(access.AccessDeniedError):
        svc.create(env_id(records), data, actor='load-owner')
    created = svc.create(env_id(records), payload(), actor='load-owner')
    with pytest.raises(access.AccessDeniedError):
        svc.list(env_id(records), actor='stranger')
    with pytest.raises(access.AccessDeniedError):
        svc.get_snapshot(created['revision_id'], actor='load-owner', environment_revision_id=env_id(records), project_id='wrong-project')
    profile.update(is_superuser=False, permissions=['api.environment', 'api.loadtest.view'], scope={'api_projects': '*', 'api_environments': '*'})
    svc.update(created['id'], {'name': '编辑允许'}, actor='load-owner')
    with pytest.raises(access.AccessDeniedError):
        svc.update(created['id'], {'source_url': 'https://evil.example', 'authorize_host': True}, actor='load-owner')


def test_disable_redacts_connection_failure(catalog, monkeypatch):
    svc, records, _ = catalog
    created = svc.create(env_id(records), payload(), actor='load-owner')
    def fail(_):
        raise RuntimeError('sensitive-bearer-123')
    monkeypatch.setattr(svc, '_client_for_revision', fail)
    result = svc.test_connection(created['id'], actor='load-owner')
    assert result['state'] == 'failed'
    assert 'sensitive-bearer' not in json.dumps(result)
    with pytest.raises(ValueError):
        svc.require_ready(created['revision_id'], actor='load-owner')
    svc.disable(created['id'], actor='load-owner')
    with pytest.raises(ValueError):
        svc.get_snapshot(created['revision_id'], actor='load-owner', environment_revision_id=env_id(records))


@pytest.mark.parametrize('change', [{'deployment': 'docker'}, {'metrics': []}, {'labels': {}}, {'step_seconds': True}, {'allowed_hosts': ['evil']}])
def test_invalid_contract(catalog, change):
    svc, records, _ = catalog
    data = payload()
    data.update(change)
    with pytest.raises(ValueError):
        svc.create(env_id(records), data, actor='load-owner')


def test_ready_requires_verified_freshness_and_current_revision(catalog, monkeypatch):
    from task_server.api_testing.services.load_monitoring_collection_service import LoadMonitoringCollectionService
    svc, records, _ = catalog
    created = svc.create(env_id(records), payload(), actor='load-owner')
    monkeypatch.setattr(LoadMonitoringCollectionService, 'probe', lambda self, snapshots: {'services': [{'state': 'completed', 'message': '主机样本已核验'}]})
    checked = svc.test_connection(created['id'], actor='load-owner')
    assert checked['freshness'] == 'verified'
    assert svc.require_ready(created['revision_id'], actor='load-owner')['revision_id'] == created['revision_id']
    newer = svc.update(created['id'], {'name': 'changed'}, actor='load-owner')
    with pytest.raises(ValueError):
        svc.require_ready(created['revision_id'], actor='load-owner')
    with pytest.raises(ValueError):
        svc.require_ready(newer['revision_id'], actor='load-owner')


def test_unknown_actor_cannot_authorize_host(catalog, monkeypatch):
    svc, records, _ = catalog
    monkeypatch.setattr(access, 'get_access_profile', lambda actor: None)
    with pytest.raises(access.AccessDeniedError):
        svc.create(env_id(records), payload(), actor='load-owner')


def test_old_frozen_revision_can_be_rechecked_without_rewriting_definition(catalog, monkeypatch):
    from task_server.api_testing.services.load_monitoring_collection_service import LoadMonitoringCollectionService
    svc, records, _ = catalog
    created = svc.create(env_id(records), payload(), actor='load-owner')
    old_definition = svc._definition_for_revision(created['revision_id'])
    newer = svc.update(created['id'], {'name': '新的环境配置'}, actor='load-owner')
    queried = []
    def probe(self, snapshots):
        queried.append(snapshots[0]['revision_id'])
        return {'services': [{'state': 'completed', 'message': '主机样本已核验'}]}
    monkeypatch.setattr(LoadMonitoringCollectionService, 'probe', probe)
    svc.test_connection(created['id'], actor='load-owner', revision_id=created['revision_id'])
    assert queried == [created['revision_id']]
    assert svc.require_ready(created['revision_id'], actor='load-owner')['freshness'] == 'verified'
    assert svc._definition_for_revision(created['revision_id']) == old_definition
    with pytest.raises(ValueError):
        svc.require_ready(newer['revision_id'], actor='load-owner')
    unrelated = svc.create(env_id(records), payload(), actor='load-owner')
    with pytest.raises(ValueError):
        svc.test_connection(created['id'], actor='load-owner', revision_id=unrelated['revision_id'])
    svc.disable(created['id'], actor='load-owner')
    with pytest.raises(ValueError):
        svc.test_connection(created['id'], actor='load-owner', revision_id=created['revision_id'])


def test_edit_during_old_revision_check_does_not_restore_stale_readiness(catalog, monkeypatch):
    from task_server.api_testing.services.load_monitoring_collection_service import LoadMonitoringCollectionService
    svc, records, _ = catalog
    created = svc.create(env_id(records), payload(), actor='load-owner')
    svc.update(created['id'], {'name': '第二版'}, actor='load-owner')
    def probe(self, snapshots):
        svc.update(created['id'], {'name': '检查期间修改'}, actor='load-owner')
        return {'services': [{'state': 'completed', 'message': '主机样本已核验'}]}
    monkeypatch.setattr(LoadMonitoringCollectionService, 'probe', probe)
    svc.test_connection(created['id'], actor='load-owner', revision_id=created['revision_id'])
    with pytest.raises(ValueError):
        svc.require_ready(created['revision_id'], actor='load-owner')

@pytest.mark.parametrize('deployment,labels', [('container', {'instance':'cadvisor:8080','id':'/docker/abc'}), ('pod', {'instance':'node:10250','namespace':'qa','pod':'api-abc'})])
def test_container_definition_freezes_scope_and_exact_queries(catalog, deployment, labels):
    svc, records, _ = catalog
    data = payload(deployment=deployment, metrics=['cpu_cores','memory_working_set_bytes'])
    data['labels'] = labels
    created = svc.create(env_id(records), data, actor='load-owner')
    snapshot = svc.get_snapshot(created['revision_id'], actor='load-owner', environment_revision_id=env_id(records))
    assert snapshot['metric_scope'] == deployment
    assert snapshot['template_version'] == 'cadvisor-container-v1'
    assert 'container_cpu_usage_seconds_total' in snapshot['queries']['cpu_cores']
    svc.update(created['id'], {'deployment':'host','labels':{'instance':'host:9100'},'metrics':['network_receive_bytes_per_second','disk_read_iops']}, actor='load-owner')
    assert snapshot == svc.get_snapshot(created['revision_id'], actor='load-owner', environment_revision_id=env_id(records))


def test_postgres_config_freezes_database_and_only_supported_metrics(catalog):
    svc, records, _ = catalog
    data = payload(deployment='postgres', metrics=['postgres_connections','postgres_commits_per_second','postgres_rollbacks_per_second'])
    data['labels'] = {'instance':'pg:9187','datname':'app'}
    created = svc.create(env_id(records), data, actor='load-owner')
    snapshot = svc.get_snapshot(created['revision_id'], actor='load-owner', environment_revision_id=env_id(records))
    assert snapshot['metric_scope'] == 'postgres'
    assert snapshot['template_version'] == 'postgres-exporter-database-v1'
    assert 'datname="app"' in snapshot['queries']['postgres_connections']
    with pytest.raises(ValueError): svc.update(created['id'], {'metrics':['memory_percent']}, actor='load-owner')
