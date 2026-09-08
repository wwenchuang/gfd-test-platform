"""Performance schedules freeze reviewed runs and never catch up expired pressure."""
from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy import select
from tests.api_testing.test_load_testing_repository import load_factory, load_records, load_run_with_shard
from task_server.api_testing.models.load_testing import ApiLoadRun, ApiLoadScenarioVersion
from task_server.api_testing import access

@pytest.fixture(autouse=True)
def no_live_finalization(monkeypatch):
    from task_server.api_testing.services.load_schedule_service import LoadScheduleService
    monkeypatch.setattr(LoadScheduleService,'_enqueue_finalize',lambda self,run_id:None)

@pytest.fixture()
def source(load_factory, load_run_with_shard, monkeypatch):
    monkeypatch.setattr(access, 'get_access_profile', lambda actor: {'status':'active','must_change_password':False,'is_superuser':True,'permissions':[], 'scope':{}})
    _, run, shard = load_run_with_shard
    with load_factory.begin() as s:
        row=s.get(ApiLoadRun,run.id)
        version=s.get(ApiLoadScenarioVersion,row.scenario_version_id)
        version.definition={'steps':[{'side_effect':'readonly'}]}
        row.configuration={**row.configuration,'workload':{'executor':'constant-vus','vus':1,'duration_seconds':10},'thresholds':{},'agents':[{'id':shard.agent_id}],'preflight':{'passed':True}}
    return run

def service(factory, now=None):
    from task_server.api_testing.services.load_schedule_service import LoadScheduleService
    return LoadScheduleService(factory, now=lambda: now or datetime(2026,9,8,0,tzinfo=timezone.utc))

def test_default_disabled_frozen_snapshot(load_factory, source):
    svc=service(load_factory)
    row=svc.create({'source_run_id':source.id,'name':'每日只读验证','daily_time':'09:00'}, 'load-owner')
    assert row['enabled'] is False
    assert row['notification_enabled'] is False
    assert row['snapshot']['workload']['vus']==1
    assert row['snapshot']['allocation_policy']['agent_ids']
    with load_factory.begin() as s:
        run=s.get(ApiLoadRun,source.id);run.configuration={'workload':{'vus':999}}
    assert svc.list('load-owner')['schedules'][0]['snapshot']['workload']['vus']==1

def test_expired_schedule_skips_without_dispatch(load_factory, source):
    from task_server.api_testing.models.load_schedule import ApiLoadSchedule
    svc=service(load_factory)
    row=svc.create({'source_run_id':source.id,'name':'逾期','daily_time':'09:00'}, 'load-owner')
    with load_factory.begin() as s:
        rec=s.get(ApiLoadSchedule,row['id']);rec.enabled=True;rec.next_run_at=datetime(2026,9,7,1,tzinfo=timezone.utc)
    assert svc.dispatch_due()==[]
    with load_factory() as s:
        rec=s.get(ApiLoadSchedule,row['id']);assert rec.last_status=='skipped_expired';assert rec.next_run_at>svc.now()

def test_enable_requires_explicit_confirmation_and_permission(load_factory, source, monkeypatch):
    svc=service(load_factory);row=svc.create({'source_run_id':source.id,'name':'门禁','daily_time':'09:00'}, 'load-owner')
    with pytest.raises(ValueError):svc.update(row['id'],{'enabled':True},'load-owner')
    monkeypatch.setattr(access,'get_access_profile',lambda actor: None)
    with pytest.raises(access.AccessDeniedError):svc.update(row['id'],{'enabled':True,'confirmed':True},'load-owner')

def test_claim_once_and_overlap_is_skipped(load_factory, source):
    from task_server.api_testing.models.load_schedule import ApiLoadSchedule
    svc=service(load_factory);row=svc.create({'source_run_id':source.id,'name':'防重','daily_time':'09:00'},'load-owner')
    with load_factory.begin() as s:
        rec=s.get(ApiLoadSchedule,row['id']);rec.enabled=True;rec.next_run_at=svc.now()
    assert svc.dispatch_due()==[row['id']]
    assert svc.dispatch_due()==[row['id']] # same persisted occurrence advanced, never new pressure
    with load_factory() as s: assert s.get(ApiLoadSchedule,row['id']).last_status=='claimed'

def test_schedule_notification_opt_in_at_most_once(load_factory, source, monkeypatch):
    from task_server.api_testing.models.load_schedule import ApiLoadScheduleOccurrence
    from task_server.api_testing.services.load_schedule_service import notify_schedule_run
    from task_server.api_testing.services.notification_service import NotificationService
    row=service(load_factory).create({'source_run_id':source.id,'name':'通知','daily_time':'09:00'},'load-owner')
    with load_factory.begin() as s:
        s.add(ApiLoadScheduleOccurrence(schedule_id=row['id'],run_id=source.id,notification_enabled=True,notification_status='pending',owner_id='load-owner',created_by='load-owner',updated_by='load-owner'))
    sent=[]
    monkeypatch.setattr(NotificationService,'send_load_test_report',lambda *args,**kwargs:sent.append(args))
    assert notify_schedule_run(load_factory,source.id)
    assert notify_schedule_run(load_factory,source.id)
    assert len(sent)==1

def test_write_scenario_is_rejected(load_factory, source):
    with load_factory.begin() as s:
        version=s.get(ApiLoadScenarioVersion,source.scenario_version_id);version.definition={'steps':[{'side_effect':'write'}]}
    with pytest.raises(ValueError,match='只读'):service(load_factory).create({'source_run_id':source.id,'name':'写入','daily_time':'09:00'},'load-owner')

from tests.api_testing.test_load_run_service import run_records, _payload, _Preflight

@pytest.mark.parametrize('stale_command',[False,True])
def test_real_orchestration_waits_for_own_connectivity_then_preflights_once(load_factory, run_records, monkeypatch, stale_command):
    from task_server.api_testing.services.load_run_service import LoadRunService
    from task_server.api_testing.services.load_schedule_service import LoadScheduleService
    from task_server.api_testing.models.load_schedule import ApiLoadSchedule
    from task_server.api_testing.models.load_testing import ApiLoadAgent
    from task_server.api_testing.models.environment import ApiEnvironmentService
    from task_server.api_testing import load_testing_http
    now=datetime.now(timezone.utc)
    monkeypatch.setattr(access,'get_access_profile',lambda actor:{'status':'active','is_superuser':True})
    with load_factory.begin() as s:
        for agent in run_records['agents']:s.get(ApiLoadAgent,agent.id).last_heartbeat_at=now
        s.add(ApiEnvironmentService(revision_id=run_records['revision'].id,service_name='default',base_url='https://example.com',owner_id='owner',created_by='owner',updated_by='owner'))
    preflight=_Preflight()
    runner=LoadRunService(load_factory,preflight_service=preflight,now=lambda:now)
    source=runner.create(_payload(run_records,workload={'executor':'constant-vus','vus':1,'duration_seconds':10}),'owner')
    runner.preflight(source.id,'owner')
    runner.stop(source.id,'已审查配置','owner')
    svc=LoadScheduleService(load_factory,now=lambda:now)
    row=svc.create({'source_run_id':source.id,'name':'真实调度','daily_time':'09:00'},'owner')
    monkeypatch.setattr(load_testing_http,'_run_service',lambda factory:runner)
    with load_factory.begin() as s:
        plan=s.get(ApiLoadSchedule,row['id']);plan.enabled=True;plan.next_run_at=now
    if stale_command:
        with load_factory.begin() as s:
            for agent in s.scalars(select(ApiLoadAgent).where(ApiLoadAgent.id.in_(row['snapshot']['allocation_policy']['agent_ids']))):
                agent.health={**agent.health,'pending_command':{'type':'target_connectivity','id':'stale-command','environment_revision_id':run_records['revision'].id,'requested_at':(now-timedelta(hours=1)).isoformat()},'target_connectivity':{run_records['revision'].id:{'command_id':'stale-command','reachable':True}}}
    svc.dispatch_due();svc.advance(row['id']);svc.advance(row['id'])
    if stale_command:
        with load_factory() as s:
            plan=s.get(ApiLoadSchedule,row['id'])
            assert plan.last_status in {'blocked','cancelled'}
            assert s.get(ApiLoadRun,plan.last_run_id).state=='cancelled'
        assert len(preflight.calls)==1
        return
    with load_factory() as s:
        plan=s.get(ApiLoadSchedule,row['id']);run_id=plan.active_run_id
        assert plan.last_status=='connectivity'
        assert s.get(ApiLoadRun,run_id).state=='draft'
    with load_factory.begin() as s:
        for agent in s.scalars(select(ApiLoadAgent).where(ApiLoadAgent.id.in_(row['snapshot']['allocation_policy']['agent_ids']))):
            command=agent.health['pending_command']
            agent.health={**agent.health,'target_connectivity':{run_records['revision'].id:{'command_id':command['id'],'reachable':True}}}
    svc.advance(row['id']);svc.advance(row['id'])
    with load_factory() as s:
        assert s.get(ApiLoadRun,run_id).state=='starting'
        assert s.get(ApiLoadSchedule,row['id']).last_status=='running'
    assert len(preflight.calls)==2 # source review + one scheduled preflight

def test_http_scope_and_validation(load_factory, source, monkeypatch):
    from task_server.api_testing.load_testing_http import handle_load_testing_request
    data,status=handle_load_testing_request('POST',('load-schedules',),{}, {'source_run_id':source.id,'name':'接口计划','daily_time':'09:00'},'load-owner',load_factory)
    assert status==201 and data['schedule']['enabled'] is False
    monkeypatch.setattr(access,'get_access_profile',lambda actor:{'status':'active','permissions':['api.view','api.loadtest.view'],'scope':{'api_projects':[],'api_environments':[]}})
    result,status=handle_load_testing_request('GET',('load-schedules',),{}, {},'outsider',load_factory)
    assert result['schedules']==[]
    with pytest.raises(access.AccessDeniedError):handle_load_testing_request('PUT',('load-schedules',data['schedule']['id']),{},{'enabled':True,'confirmed':True},'outsider',load_factory)

def test_notification_failure_does_not_change_run_and_is_not_retried(load_factory, source, monkeypatch):
    from task_server.api_testing.models.load_schedule import ApiLoadScheduleOccurrence
    from task_server.api_testing.services.load_schedule_service import notify_schedule_run
    from task_server.api_testing.services.notification_service import NotificationService
    row=service(load_factory).create({'source_run_id':source.id,'name':'失败通知','daily_time':'09:00'},'load-owner')
    with load_factory.begin() as s:
        s.add(ApiLoadScheduleOccurrence(schedule_id=row['id'],run_id=source.id,notification_enabled=True,notification_status='pending',owner_id='load-owner',created_by='load-owner',updated_by='load-owner'))
    calls=[]
    def fail(*args,**kwargs):
        calls.append(1)
        raise RuntimeError('webhook unavailable')
    monkeypatch.setattr(NotificationService,'send_load_test_report',fail)
    with load_factory() as s: state=s.get(ApiLoadRun,source.id).state
    notify_schedule_run(load_factory,source.id);notify_schedule_run(load_factory,source.id)
    with load_factory() as s:
        assert s.get(ApiLoadRun,source.id).state==state
        assert s.scalar(select(ApiLoadScheduleOccurrence).where(ApiLoadScheduleOccurrence.run_id==source.id)).notification_status=='failed'
    assert len(calls)==1

def test_monitors_only_freeze_references_and_windows(load_factory, source):
    with load_factory.begin() as s:
        run=s.get(ApiLoadRun,source.id)
        run.configuration={**run.configuration,'monitoring':{'before_seconds':42,'after_seconds':84,'token':'must-not-copy','services':[{'revision_id':'monitor-v1','required':True,'token':'must-not-copy'}]}}
    row=service(load_factory).create({'source_run_id':source.id,'name':'监控冻结','daily_time':'09:00'},'load-owner')
    assert row['snapshot']['monitoring']=={'before_seconds':42,'after_seconds':84,'services':[{'revision_id':'monitor-v1','required':True}]}

def test_worker_lease_expires_without_repeating_creation(load_factory, source, monkeypatch):
    from task_server.api_testing.models.load_schedule import ApiLoadSchedule
    from task_server.api_testing import load_testing_http
    svc=service(load_factory);row=svc.create({'source_run_id':source.id,'name':'worker恢复','daily_time':'09:00'},'load-owner')
    with load_factory.begin() as s:
        rec=s.get(ApiLoadSchedule,row['id']);rec.enabled=True;rec.last_status='creating';rec.claimed_at=svc.now()-timedelta(seconds=181)
    class Runner:
        def create(self,*args):pytest.fail('ambiguous creation must not be retried')
    monkeypatch.setattr(load_testing_http,'_run_service',lambda factory:Runner())
    svc.advance(row['id'])
    with load_factory() as s:assert s.get(ApiLoadSchedule,row['id']).last_status=='blocked'

def test_missing_actor_blocks_background_creation(load_factory, source, monkeypatch):
    from task_server.api_testing.models.load_schedule import ApiLoadSchedule
    svc=service(load_factory);row=svc.create({'source_run_id':source.id,'name':'权限撤销','daily_time':'09:00'},'load-owner')
    with load_factory.begin() as s:
        rec=s.get(ApiLoadSchedule,row['id']);rec.enabled=True;rec.next_run_at=svc.now()
    svc.dispatch_due()
    monkeypatch.setattr(access,'get_access_profile',lambda actor:None)
    svc.advance(row['id'])
    with load_factory() as s:
        rec=s.get(ApiLoadSchedule,row['id']);assert rec.last_status=='blocked';assert rec.active_run_id is None

def test_missing_notification_actor_never_sends(load_factory, source, monkeypatch):
    from task_server.api_testing.models.load_schedule import ApiLoadScheduleOccurrence
    from task_server.api_testing.services.load_schedule_service import notify_schedule_run
    from task_server.api_testing.services.notification_service import NotificationService
    row=service(load_factory).create({'source_run_id':source.id,'name':'通知权限撤销','daily_time':'09:00'},'load-owner')
    with load_factory.begin() as s:
        s.add(ApiLoadScheduleOccurrence(schedule_id=row['id'],run_id=source.id,notification_enabled=True,notification_status='pending',owner_id='load-owner',created_by='load-owner',updated_by='load-owner'))
    calls=[]
    monkeypatch.setattr(access,'get_access_profile',lambda actor:None)
    monkeypatch.setattr(NotificationService,'send_load_test_report',lambda *args,**kwargs:calls.append(1))
    notify_schedule_run(load_factory,source.id)
    assert calls==[]

def test_edit_disabled_metadata_preserves_snapshot_and_archive_keeps_history(load_factory, source):
    from task_server.api_testing.models.load_schedule import ApiLoadSchedule, ApiLoadScheduleOccurrence
    svc=service(load_factory);row=svc.create({'source_run_id':source.id,'name':'待调整','daily_time':'09:00'},'load-owner')
    changed=svc.update(row['id'],{'name':'午间检查','daily_time':'12:30'},'load-owner')
    assert changed['name']=='午间检查' and changed['daily_time']=='12:30'
    assert changed['snapshot']==row['snapshot']
    with load_factory.begin() as s:
        s.add(ApiLoadScheduleOccurrence(schedule_id=row['id'],run_id=source.id,notification_enabled=False,notification_status='disabled',owner_id='load-owner',created_by='load-owner',updated_by='load-owner'))
    assert svc.update(row['id'],{'archived':True},'load-owner')['archived'] is True
    with load_factory() as s:assert s.scalar(select(ApiLoadScheduleOccurrence).where(ApiLoadScheduleOccurrence.schedule_id==row['id'])) is not None
    with pytest.raises(ValueError):svc.update(row['id'],{'enabled':True,'confirmed':True},'load-owner')

@pytest.mark.parametrize('state',['enabled','claimed','active_run'])
def test_edit_and_archive_refuse_active_plan(load_factory, source, state):
    from task_server.api_testing.models.load_schedule import ApiLoadSchedule
    svc=service(load_factory);row=svc.create({'source_run_id':source.id,'name':'保护活动轮次','daily_time':'09:00'},'load-owner')
    with load_factory.begin() as s:
        rec=s.get(ApiLoadSchedule,row['id'])
        if state=='enabled':rec.enabled=True
        elif state=='claimed':rec.last_status='claimed'
        else:rec.active_run_id=source.id
    with pytest.raises(ValueError):svc.update(row['id'],{'daily_time':'11:00'},'load-owner')
    with pytest.raises(ValueError):svc.update(row['id'],{'archived':True},'load-owner')

def test_terminal_schedule_enqueues_report_once(load_factory, source, monkeypatch):
    from task_server.api_testing.models.load_schedule import ApiLoadSchedule, ApiLoadScheduleOccurrence
    svc=service(load_factory);row=svc.create({'source_run_id':source.id,'name':'终态收尾','daily_time':'09:00'},'load-owner')
    with load_factory.begin() as s:
        run=s.get(ApiLoadRun,source.id);run.state='cancelled'
        rec=s.get(ApiLoadSchedule,row['id']);rec.active_run_id=source.id;rec.last_status='running';rec.claimed_at=svc.now()
        s.add(ApiLoadScheduleOccurrence(schedule_id=row['id'],run_id=source.id,notification_enabled=False,notification_status='disabled',owner_id='load-owner',created_by='load-owner',updated_by='load-owner'))
    calls=[]
    monkeypatch.setattr(svc,'_enqueue_finalize',lambda run_id:calls.append(run_id),raising=False)
    svc.advance(row['id']);svc.advance(row['id'])
    assert calls==[source.id]

@pytest.mark.parametrize('run_state',['starting','running'])
def test_starting_and_overdue_running_use_watchdog(load_factory, source, monkeypatch, run_state):
    from task_server.api_testing.models.load_schedule import ApiLoadSchedule, ApiLoadScheduleOccurrence
    from task_server.api_testing.models.load_testing import ApiLoadRunShard
    svc=service(load_factory);row=svc.create({'source_run_id':source.id,'name':'启动超时','daily_time':'09:00'},'load-owner')
    with load_factory.begin() as s:
        run=s.get(ApiLoadRun,source.id);run.state=run_state;run.updated_at=svc.now()-timedelta(seconds=200);run.started_at=svc.now()-timedelta(seconds=400)
        for shard in s.scalars(select(ApiLoadRunShard).where(ApiLoadRunShard.run_id==source.id)):shard.last_heartbeat_at=svc.now()
        rec=s.get(ApiLoadSchedule,row['id']);rec.enabled=True;rec.active_run_id=source.id;rec.last_status='running';rec.claimed_at=svc.now()-timedelta(seconds=200)
        s.add(ApiLoadScheduleOccurrence(schedule_id=row['id'],run_id=source.id,notification_enabled=False,notification_status='disabled',owner_id='load-owner',created_by='load-owner',updated_by='load-owner'))
    monkeypatch.setattr(svc,'_enqueue_finalize',lambda run_id:None,raising=False)
    svc.advance(row['id'])
    with load_factory() as s:
        assert s.get(ApiLoadRun,source.id).state=='failed'
        assert s.get(ApiLoadSchedule,row['id']).active_run_id is None

def test_finalization_task_records_completion(load_factory, source, monkeypatch):
    from task_server.api_testing.models.load_schedule import ApiLoadScheduleOccurrence
    from task_server.api_testing import tasks
    svc=service(load_factory);row=svc.create({'source_run_id':source.id,'name':'终态任务','daily_time':'09:00'},'load-owner')
    with load_factory.begin() as s:s.add(ApiLoadScheduleOccurrence(schedule_id=row['id'],run_id=source.id,notification_enabled=False,notification_status='disabled',finalize_state='queued',owner_id='load-owner',created_by='load-owner',updated_by='load-owner'))
    calls=[]
    monkeypatch.setattr(tasks,'_session_factory',lambda:load_factory)
    monkeypatch.setattr(tasks.finalize_load_run,'run',lambda run_id:calls.append(run_id) or 'report_completed')
    tasks.finalize_scheduled_load_run.run(source.id)
    with load_factory() as s:assert s.scalar(select(ApiLoadScheduleOccurrence).where(ApiLoadScheduleOccurrence.run_id==source.id)).finalize_state=='done'
    assert calls==[source.id]

def test_cancelled_preparation_builds_report_and_explicit_notification(load_factory, source, monkeypatch):
    from task_server.api_testing.models.load_schedule import ApiLoadScheduleOccurrence
    from task_server.api_testing import tasks
    from task_server.api_testing.services.load_ai_analysis_service import LoadAiAnalysisService
    from task_server.api_testing.services.notification_service import NotificationService
    svc=service(load_factory);row=svc.create({'source_run_id':source.id,'name':'取消收尾报告','daily_time':'09:00'},'load-owner')
    with load_factory.begin() as s:
        run=s.get(ApiLoadRun,source.id);run.state='cancelled';run.finished_at=svc.now()
        s.add(ApiLoadScheduleOccurrence(schedule_id=row['id'],run_id=source.id,notification_enabled=True,notification_status='pending',finalize_state='queued',owner_id='load-owner',created_by='load-owner',updated_by='load-owner'))
    monkeypatch.setattr(tasks,'_session_factory',lambda:load_factory)
    def unavailable(*args):raise RuntimeError('AI intentionally unavailable')
    monkeypatch.setattr(LoadAiAnalysisService,'request',unavailable)
    sent=[]
    monkeypatch.setattr(NotificationService,'send_load_test_report',lambda *args,**kwargs:sent.append(1))
    tasks.finalize_scheduled_load_run.run(source.id)
    tasks.finalize_scheduled_load_run.run(source.id)
    with load_factory() as s:
        assert 'deterministic_report' in s.get(ApiLoadRun,source.id).summary
        occurrence=s.scalar(select(ApiLoadScheduleOccurrence).where(ApiLoadScheduleOccurrence.run_id==source.id))
        assert occurrence.finalize_state=='done' and occurrence.notification_status=='sent'
    assert sent==[1]

def test_schedule_migration_roundtrip_in_isolated_schema(load_factory):
    from alembic import command
    from sqlalchemy import inspect
    from tests.api_testing.test_migrations import _alembic_config, _without_database_environment
    engine=load_factory.kw['bind']
    assert 'test_api_testing_' in str(engine.url.query.get('options',''))
    config=_alembic_config(engine.url.render_as_string(hide_password=False))
    with _without_database_environment():
        command.downgrade(config,'0012')
        assert 'api_load_schedules' not in inspect(engine).get_table_names()
        command.upgrade(config,'0013')
    assert {'archived','environment_revision_id','source_labels'} <= {item['name'] for item in inspect(engine).get_columns('api_load_schedules')}
    assert {'finalize_state','finalize_claimed_at'} <= {item['name'] for item in inspect(engine).get_columns('api_load_schedule_occurrences')}
