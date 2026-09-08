"""Explicit multi-run comparisons never promote unknown conditions to improvements."""
import copy
from datetime import timedelta

import pytest
from task_server.api_testing.services.load_run_comparison_service import compare_run_evidence


def evidence(run_id='a'):
    return {'run_id':run_id, 'conditions':{'environment':'env-v1','scenario':'hash','purpose':'load','release':'v1','data_profile':'100 accounts','cache_state':'warm','workload':{'rate':1},'thresholds':{},'dataset':{},'agents':[{'id':'node','cpu':2}],'monitoring':[{'id':'host'}],'target_resources':[{'cores':2}],'stop_policy':None},
            'evidence':{'complete':True,'reasons':[]},'metrics':{'p95_ms':50,'requests_per_second':10,'http_error_percent':0,'business_failure_percent':0}}


def test_different_release_is_visible_variable_but_unknown_cache_blocks_delta():
    a,b=evidence(),evidence('b');b['conditions']['release']='v2';b['metrics']['p95_ms']=40
    compared=compare_run_evidence(a,b)
    assert compared['eligible'] and compared['metrics'][0]['delta'] == -10
    assert any(x['key']=='release' and x['kind']=='variable' for x in compared['differences'])
    b['conditions']['cache_state']=None
    compared=compare_run_evidence(a,b)
    assert not compared['eligible'] and all(x['delta'] is None for x in compared['metrics'])


def test_incomplete_evidence_and_changed_hardware_never_claim_improvement():
    a,b=evidence(),evidence('b');b['evidence']={'complete':False,'reasons':['采样不完整']};b['metrics']['p95_ms']=1
    assert not compare_run_evidence(a,b)['eligible']
    b=evidence('b');b['conditions']['target_resources']=[{'cores':8}]
    assert not compare_run_evidence(a,b)['eligible']


def test_zero_reference_keeps_percentage_unknown_without_infinity():
    a,b=evidence(),evidence('b');b['metrics']['http_error_percent']=1
    compared=compare_run_evidence(a,b)
    metric=next(x for x in compared['metrics'] if x['key']=='http_error_percent')
    assert metric['delta']==1 and metric['change_percent'] is None

from task_server.api_testing import access
from task_server.api_testing.models.load_testing import ApiLoadRun, ApiLoadRunShard, ApiLoadMetricBucket, ApiLoadScenarioVersion
from task_server.api_testing.models.source import ApiSource, ApiSourceRevision, ApiSourceEndpoint
from task_server.api_testing.models.case import ApiCase, ApiCaseVersion
from task_server.api_testing.services.load_metric_service import LoadMetricService
from task_server.api_testing.services.load_scenario_service import LoadScenarioService
from task_server.api_testing.services.load_run_comparison_service import LoadRunComparisonService, LoadRunComparisonError
from task_server.api_testing.load_run_comparison_http import handle_load_run_comparison_request
from task_server.api_testing.http import ApiHttpError
from tests.api_testing.test_load_testing_repository import load_factory, load_records, load_run_with_shard
from tests.api_testing.test_load_report_service import _prepare, _metric_payload, _finish, START
from sqlalchemy import select


def audit():return {'owner_id':'load-owner','created_by':'load-owner','updated_by':'load-owner'}


@pytest.fixture
def comparison_runs(load_factory,load_run_with_shard,monkeypatch):
    monkeypatch.setattr(access,'get_access_profile',lambda actor:None)
    run,shard,_=_prepare(load_factory,load_run_with_shard,target_rate=1)
    LoadMetricService(load_factory).ingest(shard.agent_id,shard.id,_metric_payload('comparison-1',requests=10,iterations=10))
    _finish(load_factory,run,shard)
    with load_factory.begin() as session:
        first=session.get(ApiLoadRun,run.id)
        source=ApiSource(project_id=first.project_id,name='comparison source '+run.id,source_type='openapi',status='active',**audit());session.add(source);session.flush()
        revision=ApiSourceRevision(source_id=source.id,revision_number=1,status='active',document_hash='a'*64,normalized_document={},**audit());session.add(revision);session.flush();source.active_revision_id=revision.id
        endpoint=ApiSourceEndpoint(revision_id=revision.id,stable_key='a'*64,method='GET',path='/search',normalized_path='/search',operation={},**audit())
        uncovered=ApiSourceEndpoint(revision_id=revision.id,stable_key='b'*64,method='GET',path='/unused',normalized_path='/unused',operation={},**audit());session.add_all([endpoint,uncovered]);session.flush()
        case=ApiCase(project_id=first.project_id,endpoint_id=endpoint.id,name='search',origin='manual',**audit());session.add(case);session.flush()
        case_version=ApiCaseVersion(case_id=case.id,endpoint_id=endpoint.id,version_number=1,purpose='query',request_template={'request':{'method':'GET','path':'/search'}},**audit());session.add(case_version);session.flush()
        item={'id':case_version.id,'request':{'method':'GET','path':'/search'}}
        step_id=LoadScenarioService._unique_step_id(case_version.id,0,[])
        version=session.get(ApiLoadScenarioVersion,first.scenario_version_id)
        version.definition={'steps':[{'id':step_id,'scope':'iteration','action':'http_request','request':item['request']}],'source_snapshot':{'type':'case_version','version_ids':[case_version.id],'items':[item]}}
        bucket=session.scalar(select(ApiLoadMetricBucket).where(ApiLoadMetricBucket.run_id==first.id));bucket.scenario_step_id=step_id
        configuration=copy.deepcopy(first.configuration)
        configuration.update(test_context={'purpose':'load','release':'v1','data_profile':'100 accounts','cache_state':'warm'},dataset={},agents=[{'id':shard.agent_id,'agent_version':'0.1.4','k6_version':'0.52.0','hard_limits':{'cpu_cores':2}}],monitoring={'services':[{'revision_id':'monitor-v1','name':'host','required':True,'credential_ref':'NEVER-EXPOSE'}]})
        first.configuration=configuration
        first.summary={'monitoring':{'terminal':True,'state':'completed','services':[{'revision_id':'monitor-v1','state':'completed','metrics':[{'key':'cpu_percent','denominator':'host_cpu_cores','series':[{'labels':{'instance':'host'},'points':[{'denominator_value':2}]}]}]}]}}
        second=ApiLoadRun(project_id=first.project_id,scenario_version_id=first.scenario_version_id,environment_revision_id=first.environment_revision_id,load_model=first.load_model,configuration=copy.deepcopy(configuration),state='finished',started_at=START+timedelta(hours=1),finished_at=START+timedelta(hours=1,seconds=10),summary=copy.deepcopy(first.summary),**audit());session.add(second);session.flush()
        second_shard=ApiLoadRunShard(run_id=second.id,agent_id=shard.agent_id,sequence=1,global_sequence=1,allocation={},state='finished',**audit());session.add(second_shard);session.flush()
        session.add(ApiLoadMetricBucket(run_id=second.id,shard_id=second_shard.id,scenario_step_id=step_id,bucket_started_at=START+timedelta(hours=1),bucket_seconds=5,metrics=copy.deepcopy(bucket.metrics),**audit()))
        return {'run_ids':[first.id,second.id],'project_id':first.project_id,'endpoint_id':endpoint.id,'source_id':source.id,'step_id':step_id}


def test_real_reports_compare_and_count_endpoint_once_across_two_runs(load_factory,comparison_runs):
    result=LoadRunComparisonService(load_factory).compare(comparison_runs['run_ids'],'load-owner')
    assert result['comparisons'][0]['eligible']
    assert result['coverage']['selected_run_count']==2
    assert result['coverage']['observed_endpoint_count']==1
    assert result['coverage']['asset_endpoint_count']==2
    assert result['coverage']['coverage_ratio']==.5
    assert result['coverage']['endpoints'][0]['requests']==20
    assert len(result['coverage']['endpoints'][0]['run_ids'])==2
    assert 'NEVER-EXPOSE' not in str(result)


def test_manual_changed_request_is_not_guessed_from_endpoint_path(load_factory,comparison_runs):
    with load_factory.begin() as session:
        run=session.get(ApiLoadRun,comparison_runs['run_ids'][0]);version=session.get(ApiLoadScenarioVersion,run.scenario_version_id)
        definition=copy.deepcopy(version.definition);definition['steps'][0]['request']['path']='/different';version.definition=definition
    result=LoadRunComparisonService(load_factory).compare(comparison_runs['run_ids'],'load-owner')
    assert result['coverage']['observed_endpoint_count']==0
    assert result['coverage']['unmapped_step_run_count']==2
    assert result['coverage']['coverage_ratio'] is None


def test_historical_asset_revision_is_explicitly_unmapped(load_factory,comparison_runs):
    with load_factory.begin() as session:session.get(ApiSource,comparison_runs['source_id']).status='disabled'
    result=LoadRunComparisonService(load_factory).compare(comparison_runs['run_ids'],'load-owner')
    assert result['coverage']['historical_endpoint_step_run_count']==2
    assert result['coverage']['coverage_ratio'] is None


def test_readonly_api_and_run_scope_are_required_before_reports(load_factory,comparison_runs):
    result,status=handle_load_run_comparison_request('GET',{'run_ids':','.join(comparison_runs['run_ids'])},'load-owner',load_factory)
    assert status==200 and result['comparison']['coverage']['selected_run_count']==2
    with pytest.raises(access.AccessDeniedError):handle_load_run_comparison_request('GET',{'run_ids':','.join(comparison_runs['run_ids'])},'other-owner',load_factory)
    with pytest.raises(ApiHttpError):handle_load_run_comparison_request('POST',{},'load-owner',load_factory)
    with pytest.raises(ApiHttpError):handle_load_run_comparison_request('GET',{'run_ids':','.join(comparison_runs['run_ids']),'arbitrary':1},'load-owner',load_factory)
    with pytest.raises(LoadRunComparisonError):LoadRunComparisonService(load_factory).compare([comparison_runs['run_ids'][0]]*2,'load-owner')


def test_running_or_unfinished_monitoring_never_yields_improvement(load_factory,comparison_runs):
    with load_factory.begin() as session:
        run=session.get(ApiLoadRun,comparison_runs['run_ids'][1]);run.summary={'monitoring':{'terminal':False,'services':[]}}
    result=LoadRunComparisonService(load_factory).compare(comparison_runs['run_ids'],'load-owner')
    assert not result['comparisons'][0]['eligible']
    with load_factory.begin() as session:session.get(ApiLoadRun,comparison_runs['run_ids'][1]).state='running'
    with pytest.raises(LoadRunComparisonError):LoadRunComparisonService(load_factory).compare(comparison_runs['run_ids'],'load-owner')


def test_actual_scoped_viewer_can_compare_but_environment_restriction_blocks(load_factory,comparison_runs,monkeypatch):
    profile={'status':'active','permissions':['api.view','api.loadtest.view'],'scope':{'api_projects':[comparison_runs['project_id']],'api_environments':'*'}}
    monkeypatch.setattr(access,'get_access_profile',lambda _:profile)
    result,_=handle_load_run_comparison_request('GET',{'run_ids':','.join(comparison_runs['run_ids'])},'viewer',load_factory)
    assert result['comparison']['coverage']['coverage_ratio']==.5
    profile['scope']['api_environments']=[]
    with pytest.raises(access.AccessDeniedError):handle_load_run_comparison_request('GET',{'run_ids':','.join(comparison_runs['run_ids'])},'viewer',load_factory)


def test_all_runs_are_authorized_before_any_report_build(load_factory,comparison_runs,monkeypatch):
    from task_server.api_testing.services.load_run_comparison_service import _ComparisonReportService
    with load_factory.begin() as session:session.get(ApiLoadRun,comparison_runs['run_ids'][1]).owner_id='foreign-owner'
    monkeypatch.setattr(_ComparisonReportService,'build',lambda *args:pytest.fail('must authorize all selected runs before report retrieval'))
    with pytest.raises(access.AccessDeniedError):LoadRunComparisonService(load_factory).compare(comparison_runs['run_ids'],'load-owner')


def test_evidence_budget_is_enforced_before_report_loading(load_factory,comparison_runs,monkeypatch):
    from task_server.api_testing.services import load_run_comparison_service as module
    monkeypatch.setattr(module,'MAX_BUCKETS',1)
    with pytest.raises(LoadRunComparisonError,match='超过'):LoadRunComparisonService(load_factory).compare(comparison_runs['run_ids'],'load-owner')


def test_empty_or_unverified_resources_block_causal_comparison(load_factory,comparison_runs):
    with load_factory.begin() as session:
        run=session.get(ApiLoadRun,comparison_runs['run_ids'][1]);summary=copy.deepcopy(run.summary)
        summary['monitoring']['services'][0]['metrics'][0]['series'][0]['points'].append({'denominator_value':4})
        run.summary=summary
    result=LoadRunComparisonService(load_factory).compare(comparison_runs['run_ids'],'load-owner')
    assert not result['comparisons'][0]['eligible']
    assert any('资源分母' in reason for reason in result['comparisons'][0]['reasons'])
