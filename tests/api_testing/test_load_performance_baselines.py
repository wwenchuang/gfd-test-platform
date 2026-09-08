"""Manual immutable performance references and same-condition regression warnings."""
import copy
import pytest
from task_server.api_testing.services.load_performance_baseline_service import evaluate_regression, LoadPerformanceBaselineService
from tests.api_testing.test_load_run_comparison_service import evidence, comparison_runs, load_factory, load_run_with_shard, load_records


def test_regression_policy_warns_on_observed_degradation_only():
    a,b=evidence(),evidence('b');b['metrics']['p95_ms']=60
    result=evaluate_regression(a,b,{'p95_increase_percent':10})
    assert result['state']=='regression_warning'
    assert result['checks'][0]['actual']==20
    b['conditions']['cache_state']=None
    result=evaluate_regression(a,b,{'p95_increase_percent':10})
    assert result['state']=='inconclusive' and result['checks'][0]['actual'] is None


def test_error_percentage_uses_percentage_points_and_zero_reference_is_not_divided():
    a,b=evidence(),evidence('b');b['metrics']['http_error_percent']=1
    result=evaluate_regression(a,b,{'http_error_increase_points':.5})
    assert result['state']=='regression_warning' and result['checks'][0]['actual']==1


def test_changed_statistics_method_blocks_warning_and_delta():
    a,b=evidence(),evidence('b')
    a['statistics_schema_version']=2;b['statistics_schema_version']=3
    result=evaluate_regression(a,b,{'p95_increase_percent':10})
    assert result['state']=='inconclusive'
    assert result['checks'][0]['actual'] is None
    assert all(row['delta'] is None for row in result['comparison']['metrics'])
    assert any('统计口径' in reason for reason in result['comparison']['reasons'])

from task_server.api_testing import access
from task_server.api_testing.models.load_testing import ApiLoadRun
from task_server.api_testing.models.load_performance_baseline import ApiLoadPerformanceBaseline
from task_server.api_testing.services.load_performance_baseline_service import LoadPerformanceBaselineError
from task_server.api_testing.load_performance_baseline_http import handle_load_performance_baseline_request


def payload(run_id,**changes):return {'run_id':run_id,'name':'日常负载参考','adoption_reason':'已核对请求与资源证据，人工采纳作为后续参考','regression_policy':{'p95_increase_percent':10,'http_error_increase_points':.5},**changes}


def test_adoption_is_frozen_idempotent_and_new_reference_preserves_history(load_factory,comparison_runs):
    service=LoadPerformanceBaselineService(load_factory);first,second=comparison_runs['run_ids']
    baseline=service.adopt(payload(first),'load-owner')
    assert baseline['status']=='active'
    assert service.adopt(payload(first),'load-owner')['id']==baseline['id']
    frozen=copy.deepcopy(baseline['evidence_snapshot'])
    with load_factory.begin() as session:
        run=session.get(ApiLoadRun,first);config=copy.deepcopy(run.configuration);config['test_context']['cache_state']='changed after adoption';run.configuration=config
    assert service.get(baseline['id'],'load-owner')['evidence_snapshot']==frozen
    assert service.compare(baseline['id'],second,'load-owner')['state']=='within_reference'
    newer=service.adopt(payload(second),'load-owner')
    history=service.get(baseline['id'],'load-owner')
    assert history['status']=='retired' and history['evidence_snapshot']==frozen
    assert newer['status']=='active'
    assert len(service.list(comparison_runs['project_id'],'load-owner')['baselines'])==2
    assert service.retire(newer['id'],'load-owner')['status']=='retired'


def test_incomplete_or_unreached_run_cannot_be_adopted(load_factory,comparison_runs):
    first=comparison_runs['run_ids'][0]
    with load_factory.begin() as session:
        run=session.get(ApiLoadRun,first);config=copy.deepcopy(run.configuration);config['workload']['rate']=100;run.configuration=config
    with pytest.raises(LoadPerformanceBaselineError,match='不能采纳'):LoadPerformanceBaselineService(load_factory).adopt(payload(first),'load-owner')


def test_manual_baseline_permission_is_distinct_from_view_and_respects_environment_scope(load_factory,comparison_runs,monkeypatch):
    profile={'status':'active','permissions':['api.view','api.loadtest.view'],'scope':{'api_projects':[comparison_runs['project_id']],'api_environments':'*'}}
    monkeypatch.setattr(access,'get_access_profile',lambda _:profile)
    service=LoadPerformanceBaselineService(load_factory)
    with pytest.raises(access.AccessDeniedError):service.adopt(payload(comparison_runs['run_ids'][0]),'viewer')
    profile['permissions'].append('api.baseline')
    baseline=service.adopt(payload(comparison_runs['run_ids'][0]),'manager')
    profile['permissions'].remove('api.baseline')
    assert service.get(baseline['id'],'viewer')['id']==baseline['id']
    with pytest.raises(access.AccessDeniedError):service.retire(baseline['id'],'viewer')
    profile['scope']['api_environments']=[]
    with pytest.raises(access.AccessDeniedError):service.get(baseline['id'],'viewer')
    assert service.list(comparison_runs['project_id'],'viewer')['baselines']==[]


def test_http_lifecycle_and_snapshot_integrity(load_factory,comparison_runs):
    result,status=handle_load_performance_baseline_request('POST',('load-performance-baselines',),{},payload(comparison_runs['run_ids'][0]),'load-owner',load_factory)
    assert status==201;baseline_id=result['baseline']['id']
    compared,status=handle_load_performance_baseline_request('GET',('load-performance-baselines',baseline_id,'compare'),{'run_id':comparison_runs['run_ids'][1]},None,'load-owner',load_factory)
    assert status==200 and compared['regression']['state']=='within_reference'
    with load_factory.begin() as session:
        row=session.get(ApiLoadPerformanceBaseline,baseline_id);row.evidence_snapshot={'tampered':True}
    with pytest.raises(LoadPerformanceBaselineError,match='校验失败'):LoadPerformanceBaselineService(load_factory).get(baseline_id,'load-owner')


def test_source_run_deletion_does_not_erase_frozen_reference(load_factory,comparison_runs):
    service=LoadPerformanceBaselineService(load_factory);baseline=service.adopt(payload(comparison_runs['run_ids'][0]),'load-owner')
    with load_factory.begin() as session:session.delete(session.get(ApiLoadRun,comparison_runs['run_ids'][0]))
    assert service.get(baseline['id'],'load-owner')['source_run_id']==comparison_runs['run_ids'][0]
    assert service.compare(baseline['id'],comparison_runs['run_ids'][1],'load-owner')['state']=='within_reference'


def test_adoption_rejects_selected_warning_metric_without_samples(load_factory,comparison_runs,monkeypatch):
    service=LoadPerformanceBaselineService(load_factory)
    scope,snapshot=service._candidate(comparison_runs['run_ids'][0],'load-owner')
    snapshot['metrics']['p95_ms']=None
    monkeypatch.setattr(service,'_candidate',lambda *args:(scope,snapshot))
    with pytest.raises(LoadPerformanceBaselineError,match='没有样本'):
        service.adopt(payload(comparison_runs['run_ids'][0]),'load-owner')


def test_adoption_rejects_unknown_comparison_conditions(load_factory,comparison_runs,monkeypatch):
    service=LoadPerformanceBaselineService(load_factory)
    scope,snapshot=service._candidate(comparison_runs['run_ids'][0],'load-owner')
    snapshot['conditions']['cache_state']=None
    monkeypatch.setattr(service,'_candidate',lambda *args:(scope,snapshot))
    with pytest.raises(LoadPerformanceBaselineError,match='条件未完整核验'):
        service.adopt(payload(comparison_runs['run_ids'][0]),'load-owner')


def test_baseline_migration_upgrade_and_downgrade_in_isolated_schema():
    from alembic import command
    from sqlalchemy import create_engine, inspect
    from tests.api_testing.test_migrations import _database_url, _create_test_schema, _drop_test_schema, _alembic_config, _without_database_environment
    database_url=_database_url();created=set()
    schema_name,schema_url=_create_test_schema(database_url,created)
    engine=create_engine(schema_url)
    try:
        config=_alembic_config(schema_url)
        with _without_database_environment():
            command.upgrade(config,'0011')
            assert 'api_load_performance_baselines' not in inspect(engine).get_table_names()
            command.upgrade(config,'0012')
            indexes=inspect(engine).get_indexes('api_load_performance_baselines')
            active=next(row for row in indexes if row['name']=='uq_load_performance_baseline_active')
            assert active['unique'] is True
            assert 'active' in str(active['dialect_options']['postgresql_where'])
            command.downgrade(config,'0011')
            assert 'api_load_performance_baselines' not in inspect(engine).get_table_names()
    finally:
        engine.dispose()
        _drop_test_schema(database_url,schema_name,created)
