"""Monitoring execution choices are validated and never trust source snapshots."""
import pytest
from task_server.api_testing.services.load_run_service import LoadRunError, _parse_monitoring

@pytest.mark.parametrize('payload',[{'services':'oops'},{'services':[{'revision_id':'a','required':'yes'}]}, {'services':[{'revision_id':'a','required':True},{'revision_id':'a','required':False}]}, {'services':[], 'before_seconds':601}, {'services':[], 'source_url':'http://metadata'}])
def test_invalid_monitoring_choices_rejected(payload):
    with pytest.raises(LoadRunError):
        _parse_monitoring(payload)

def test_monitoring_optional_and_bounds():
    assert _parse_monitoring(None) is None
    assert _parse_monitoring({'services':[{'revision_id':'a','required':True}], 'after_seconds':0}) == {'services':[{'revision_id':'a','required':True}], 'before_seconds':60, 'after_seconds':0}


def test_required_monitor_missing_cannot_disappear_from_report_gate():
    from task_server.api_testing.services.load_report_service import _monitoring_evidence
    from types import SimpleNamespace
    run = SimpleNamespace(configuration={'monitoring':{'services':[{'revision_id':'a','name':'主机','required':True}]}}, summary={'monitoring':{'terminal':True,'state':'failed','services':[]}})
    result, acceptable = _monitoring_evidence(run)
    assert acceptable is False
    assert result['state'] == 'failed'
    run.configuration['monitoring']['services'][0]['required'] = False
    assert _monitoring_evidence(run)[1] is True

from tests.api_testing.test_load_run_service import load_factory, run_records, _service, _payload
from task_server.api_testing.models.load_testing import ApiLoadRun
from task_server.api_testing.models.load_monitoring import ApiLoadMonitoringService
from task_server.api_testing.services.load_monitoring_config_service import LoadMonitoringConfigService
from task_server.api_testing import access
from datetime import datetime, timezone


def test_run_freezes_monitor_and_requires_fresh_check(load_factory, run_records, monkeypatch):
    monkeypatch.setattr(access, 'get_access_profile', lambda _: {'status':'active','is_superuser':True})
    config = LoadMonitoringConfigService(load_factory)
    monitor = config.create(run_records['revision'].id, {'name':'主机', 'source_url':'https://metrics.example', 'labels':{'instance':'node:9100'}, 'authorize_host':True}, actor='owner')
    svc = _service(load_factory)
    run = svc.create(_payload(run_records,monitoring={'services':[{'revision_id':monitor['revision_id'],'required':True}]}), 'owner')
    assert run.configuration['monitoring']['services'][0]['name'] == '主机'
    assert 'token' not in run.configuration['monitoring']['services'][0]
    with load_factory.begin() as session:
        session.get(ApiLoadRun,run.id).state='queued'
    with pytest.raises(LoadRunError,match='重新检查'):
        svc.start(run.id,'owner')
    with load_factory.begin() as session:
        session.get(ApiLoadMonitoringService,monitor['id']).last_check={'state':'ready','freshness':'verified','revision_id':monitor['revision_id'],'checked_at':datetime.now(timezone.utc).isoformat()}
    assert svc.start(run.id,'owner').state == 'starting'
    config.update(monitor['id'],{'name':'修改后主机'},actor='owner')
    with load_factory() as session:
        assert session.get(ApiLoadRun,run.id).configuration['monitoring']['services'][0]['name']=='主机'


def test_monitor_http_lifecycle_uses_environment_scope(load_factory, run_records, monkeypatch):
    from task_server.api_testing.load_testing_http import handle_load_testing_request
    monkeypatch.setattr(access, 'get_access_profile', lambda _: {'status':'active','is_superuser':True})
    payload={'environment_revision_id':run_records['revision'].id,'name':'接口配置监控','source_url':'https://metrics.example','labels':{'instance':'node:9100'},'authorize_host':True}
    data,status=handle_load_testing_request('POST',('load-monitoring-services',),{},payload,'owner',load_factory)
    assert status==201
    data,status=handle_load_testing_request('GET',('load-monitoring-services',),{'environment_revision_id':run_records['revision'].id},{},'owner',load_factory)
    assert data['items'][0]['name']=='接口配置监控'


def test_recovery_filters_completed_runs_before_page_limit(load_factory,run_records,monkeypatch):
    from task_server.api_testing import tasks
    from datetime import timedelta
    svc=_service(load_factory)
    run=svc.create(_payload(run_records), 'owner')
    with load_factory.begin() as session:
        row=session.get(ApiLoadRun,run.id)
        row.state='finished';row.configuration={**row.configuration,'monitoring':{'services':[{'revision_id':'a','required':True}]}}
        row.summary={}
        for i in range(101):
            session.add(ApiLoadRun(project_id=row.project_id,scenario_version_id=row.scenario_version_id,environment_revision_id=row.environment_revision_id,load_model=row.load_model,state='finished',configuration=row.configuration,summary={'monitoring':{'terminal':True}},owner_id='owner',created_by='owner',updated_by='owner',created_at=datetime.now(timezone.utc)+timedelta(seconds=i)))
    queued=[]
    monkeypatch.setattr(tasks,'_session_factory',lambda:load_factory)
    monkeypatch.setattr(tasks,'_monitoring_resume_at',0)
    monkeypatch.setattr(tasks.collect_load_monitoring,'delay',lambda rid:queued.append(rid))
    tasks._resume_load_monitoring()
    assert run.id in queued
    with load_factory() as session:
        assert all(not ((session.get(ApiLoadRun,rid).summary or {}).get("monitoring") or {}).get("terminal") for rid in queued)


def test_mandatory_monitoring_failure_overrides_otherwise_passing_report(load_factory, run_records):
    from task_server.api_testing.services.load_report_service import _monitoring_evidence
    from types import SimpleNamespace
    run=SimpleNamespace(configuration={"monitoring":{"services":[{"revision_id":"a","name":"主机","required":True}]}},summary={"monitoring":{"terminal":True,"services":[{"revision_id":"a","state":"missing"}]}})
    assert _monitoring_evidence(run)[1] is False
