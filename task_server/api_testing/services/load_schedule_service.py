"""Opt-in daily performance schedules, with bounded persisted stage claims."""
import copy
import re
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from sqlalchemy import select
from .. import access
from ..models.load_schedule import ApiLoadSchedule, ApiLoadScheduleOccurrence
from ..models.load_testing import ApiLoadRun, ApiLoadScenarioVersion, ApiLoadAgent

TERMINAL = {'finished','failed','cancelled'}
ACTIVE = {'claimed','creating','connectivity','preflighting','running'}
TZ = ZoneInfo('Asia/Shanghai')

class LoadScheduleService:
    def __init__(self, factory, *, now=None):
        self.factory=factory
        self.now=now or (lambda:datetime.now(timezone.utc))

    @staticmethod
    def _require_live_actor(actor):
        if access.get_access_profile(actor) is None:
            raise access.AccessDeniedError('api.loadtest.execute')
        access.require_permission(actor, 'api.loadtest.execute')

    def _next(self, clock):
        now=self.now().astimezone(TZ)
        hour,minute=map(int,clock.split(':'))
        result=now.replace(hour=hour,minute=minute,second=0,microsecond=0)
        return (result if result>now else result+timedelta(days=1)).astimezone(timezone.utc)

    @staticmethod
    def _view(row):
        keys=('id','project_id','name','source_run_id','source_labels','snapshot','daily_time','archived','enabled','notification_enabled','active_run_id','last_run_id','last_status','last_message')
        return {**{k:copy.deepcopy(getattr(row,k)) for k in keys},'timezone':'Asia/Shanghai','next_run_at':row.next_run_at.isoformat()}

    def list(self, actor):
        access.require_permission(actor,'api.loadtest.view')
        with self.factory() as s:
            rows=s.scalars(select(ApiLoadSchedule).where(access.resource_predicate(actor,ApiLoadSchedule)).order_by(ApiLoadSchedule.created_at.desc()).limit(200))
            result=[]
            for row in rows:
                view=self._view(row)
                occurrence=s.scalar(select(ApiLoadScheduleOccurrence).where(ApiLoadScheduleOccurrence.run_id==row.last_run_id)) if row.last_run_id else None
                view['notification_status']=occurrence.notification_status if occurrence else ('pending' if row.notification_enabled else 'disabled')
                if row.last_status=='connectivity':view['last_message']='等待本次节点连通性结果'
                result.append(view)
            return {'schedules':result}

    def create(self,payload,actor):
        self._require_live_actor(actor)
        name=str(payload.get('name') or '').strip()
        clock=str(payload.get('daily_time') or '')
        if not name or len(name)>160 or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',clock):raise ValueError('请填写计划名称和有效的每日时间')
        notify=payload.get('notification_enabled',False)
        if not isinstance(notify,bool):raise ValueError('通知开关必须是布尔值')
        if notify:access.require_permission(actor,'platform.notify')
        with self.factory.begin() as s:
            run=s.get(ApiLoadRun,str(payload.get('source_run_id') or ''))
            access.require_resource(s,run,actor,'api.loadtest.execute')
            cfg=copy.deepcopy(run.configuration or {})
            if (cfg.get('preflight') or {}).get('passed') is not True:raise ValueError('请选择已通过功能预检的执行配置')
            version=s.get(ApiLoadScenarioVersion,run.scenario_version_id)
            steps=(version.definition or {}).get('steps',[]) if version else []
            if not steps or any(step.get('side_effect')!='readonly' for step in steps):raise ValueError('定时计划仅支持每个步骤均明确标记只读的已审查场景')
            snapshot={k:cfg[k] for k in ('workload','thresholds','priority','test_context','stop_policy') if k in cfg}
            snapshot.update(scenario_version_id=run.scenario_version_id,environment_revision_id=run.environment_revision_id)
            policy=copy.deepcopy(cfg.get('allocation_policy') or {})
            policy['agent_ids']=[a['id'] for a in cfg.get('agents',[])]
            policy.pop('node_group',None)
            policy['allow_fallback']=False
            policy['allow_run_anyway']=False
            if not policy['agent_ids']:raise ValueError('来源执行缺少已审查的固定节点')
            snapshot['allocation_policy']=policy
            if cfg.get('monitoring'):
                monitoring=cfg['monitoring']
                snapshot['monitoring']={k:copy.deepcopy(monitoring[k]) for k in ('before_seconds','after_seconds') if k in monitoring}
                snapshot['monitoring']['services']=[{'revision_id':v['revision_id'],'required':v.get('required',False)} for v in monitoring.get('services',[])]
            row=ApiLoadSchedule(project_id=run.project_id,environment_revision_id=run.environment_revision_id,name=name,source_run_id=run.id,snapshot=snapshot,source_labels={'scenario':str((cfg.get('scenario') or {}).get('name') or '性能场景'),'environment':str((cfg.get('environment') or {}).get('name') or '目标环境'),'scenario_version':(cfg.get('scenario') or {}).get('version_number')},daily_time=clock,enabled=False,notification_enabled=notify,next_run_at=self._next(clock),owner_id=actor,created_by=actor,updated_by=actor)
            s.add(row);s.flush();return self._view(row)

    def update(self,id,payload,actor):
        self._require_live_actor(actor)
        if set(payload)-{'enabled','confirmed','notification_enabled','name','daily_time','archived'}:raise ValueError('配置快照不可修改，请创建新计划')
        with self.factory.begin() as s:
            row=s.scalar(select(ApiLoadSchedule).where(ApiLoadSchedule.id==id).with_for_update())
            access.require_resource(s,row,actor,'api.loadtest.execute')
            if row.archived:raise ValueError('计划已归档，请创建新计划')
            metadata=set(payload) & {'name','daily_time','archived'}
            if metadata:
                if row.enabled or row.active_run_id or row.last_status in ACTIVE or 'enabled' in payload:
                    raise ValueError('请先停用计划并等待活动轮次结束，再调整时间或归档')
                if 'name' in payload:
                    name=str(payload['name'] or '').strip()
                    if not name or len(name)>160:raise ValueError('计划名称应为1至160个字符')
                    row.name=name
                if 'daily_time' in payload:
                    clock=str(payload['daily_time'] or '')
                    if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',clock):raise ValueError('每日时间无效')
                    row.daily_time=clock;row.next_run_at=self._next(clock)
                if 'archived' in payload:
                    if payload['archived'] is not True:raise ValueError('归档操作必须明确选择')
                    row.archived=True
            if 'enabled' in payload:
                if not isinstance(payload['enabled'],bool):raise ValueError('启用开关必须是布尔值')
                if payload['enabled'] and payload.get('confirmed') is not True:raise ValueError('启用前请明确确认固定压力、节点及执行时间')
                if payload['enabled']:
                    self._require_live_actor(row.created_by)
                    access.require_execution_environment(s,row.snapshot['environment_revision_id'],row.created_by,row.project_id)
                    row.next_run_at=self._next(row.daily_time)
                row.enabled=payload['enabled']
            if 'notification_enabled' in payload:
                if not isinstance(payload['notification_enabled'],bool):raise ValueError('通知开关必须是布尔值')
                if payload['notification_enabled']:access.require_permission(actor,'platform.notify');access.require_permission(row.created_by,'platform.notify')
                row.notification_enabled=payload['notification_enabled']
            row.updated_by=actor;s.flush();return self._view(row)

    def dispatch_due(self):
        now=self.now();ids=[]
        with self.factory.begin() as s:
            rows=s.scalars(select(ApiLoadSchedule).where(ApiLoadSchedule.archived.is_(False)).where((ApiLoadSchedule.enabled.is_(True)) | (ApiLoadSchedule.active_run_id.is_not(None)) | (ApiLoadSchedule.last_status.in_(ACTIVE))).with_for_update(skip_locked=True))
            for row in rows:
                if row.last_status in ACTIVE or row.active_run_id:
                    ids.append(row.id)
                    if row.next_run_at<=now:row.next_run_at=self._next(row.daily_time)
                    continue
                if not row.enabled or row.next_run_at>now:continue
                late=(now-row.next_run_at).total_seconds()
                row.next_run_at=self._next(row.daily_time)
                if late>90:row.last_status='skipped_expired';row.last_message='执行窗口已过期，本次跳过，不补发压力';continue
                row.last_status='claimed';row.claimed_at=now;row.last_message='等待重新检查权限和节点';ids.append(row.id)
        with self.factory() as s:
            terminal_schedules=s.scalars(select(ApiLoadScheduleOccurrence.schedule_id).join(ApiLoadRun,ApiLoadRun.id==ApiLoadScheduleOccurrence.run_id).where(ApiLoadRun.state.in_(TERMINAL),ApiLoadScheduleOccurrence.finalize_state!='done').limit(200))
            ids.extend(terminal_schedules)
        return list(dict.fromkeys(ids))

    def advance(self,id):
        from ..load_testing_http import _run_service
        with self.factory() as s:
            schedule=s.get(ApiLoadSchedule,id)
            run_id=schedule.active_run_id if schedule else None
        if run_id:
            runner=_run_service(self.factory)
            runner.now=self.now
            runner.recover_stale_runs(run_id=run_id)
        try:
            self._advance(id)
        finally:
            self._finalize_terminal(id)

    def _enqueue_finalize(self, run_id):
        from ..tasks import finalize_scheduled_load_run
        finalize_scheduled_load_run.delay(run_id)

    def _finalize_terminal(self, schedule_id):
        pending=[]
        with self.factory.begin() as s:
            rows=s.scalars(select(ApiLoadScheduleOccurrence).join(ApiLoadRun,ApiLoadRun.id==ApiLoadScheduleOccurrence.run_id).where(ApiLoadScheduleOccurrence.schedule_id==schedule_id,ApiLoadRun.state.in_(TERMINAL),ApiLoadScheduleOccurrence.finalize_state!='done').with_for_update(of=ApiLoadScheduleOccurrence,skip_locked=True))
            for row in rows:
                if row.finalize_state=='queued' and row.finalize_claimed_at and (self.now()-row.finalize_claimed_at).total_seconds()<120:continue
                row.finalize_state='queued';row.finalize_claimed_at=self.now();pending.append(row.run_id)
        for run_id in pending:
            try:self._enqueue_finalize(run_id)
            except Exception:
                with self.factory.begin() as s:
                    row=s.scalar(select(ApiLoadScheduleOccurrence).where(ApiLoadScheduleOccurrence.run_id==run_id))
                    row.finalize_state='pending'

    def _advance(self,id):
        from ..load_testing_http import _run_service, _prepare_run_connectivity
        service=_run_service(self.factory)
        now=self.now()
        with self.factory.begin() as s:
            row=s.scalar(select(ApiLoadSchedule).where(ApiLoadSchedule.id==id).with_for_update(skip_locked=True))
            if row is None:return
            state=row.last_status;run_id=row.active_run_id;actor=row.created_by
            if state not in ACTIVE and not run_id:return
            run=s.get(ApiLoadRun,run_id) if run_id else None
            if run_id and run is None:
                row.last_status='blocked';row.active_run_id=None;row.last_message='最近执行记录已删除，本次不重试';return
            if run and run.state in TERMINAL:
                row.last_status=run.state;row.active_run_id=None;row.last_message='执行已结束，请查看报告';return
            expired=row.claimed_at and (now-row.claimed_at).total_seconds()>180
            if not row.enabled or (expired and state!='running'):
                # Never retry an ambiguous in-flight creation or preflight after worker loss.
                row.last_status='blocked';row.last_message='计划已停用或准备超时；本次不重试'
                if not run_id:return
                state='cancel'
            elif state=='blocked':state='cancel'
            elif state in {'creating','preflighting'}:return
            elif state=='running':return
            elif state=='claimed':
                if (now-row.claimed_at).total_seconds()>90:row.last_status='skipped_expired';row.last_message='调度延迟，已跳过本次压力';return
                row.last_status='creating'
            elif state=='connectivity':
                ready=True
                for agent_id,command_id in (row.last_message and json.loads(row.last_message) or {}).items():
                    agent=s.get(ApiLoadAgent,agent_id)
                    result=((agent.health or {}).get('target_connectivity') or {}).get(row.snapshot['environment_revision_id'],{}) if agent else {}
                    if result.get('command_id')!=command_id or result.get('reachable') is not True:ready=False
                if not ready:return
                row.last_status='preflighting'
            snapshot=copy.deepcopy(row.snapshot);notify=row.notification_enabled
        try:
            if state=='cancel':
                self._cancel_preparation(run_id);return
            self._require_live_actor(actor)
            with self.factory() as s:
                record=s.get(ApiLoadSchedule,id);access.require_resource(s,record,actor,'api.loadtest.execute')
                access.require_execution_environment(s,snapshot['environment_revision_id'],actor,record.project_id)
            if state=='claimed':
                run=service.create(snapshot,actor);run_id=run.id
                with self.factory.begin() as s:
                    row=s.get(ApiLoadSchedule,id);row.active_run_id=run_id;row.last_run_id=run_id
                    s.add(ApiLoadScheduleOccurrence(schedule_id=id,run_id=run_id,notification_enabled=notify,notification_status='pending' if notify else 'disabled',owner_id=actor,created_by=actor,updated_by=actor))
                agents=_prepare_run_connectivity(self.factory,run_id,actor)
                commands={}
                for agent in agents:
                    command=agent.health['pending_command']
                    requested_at=datetime.fromisoformat(command['requested_at'])
                    if requested_at.tzinfo is None or requested_at < now:
                        raise ValueError('节点尚有旧连通性命令，本次不复用历史证据')
                    commands[agent.id]=command['id']
                if not commands:raise ValueError('未能下发节点连通性检查')
                with self.factory.begin() as s:
                    row=s.get(ApiLoadSchedule,id);row.last_status='connectivity';row.last_message=json.dumps(commands)
            elif state=='connectivity':
                from .load_monitoring_config_service import LoadMonitoringConfigService
                from ..models.load_monitoring import ApiLoadMonitoringRevision
                monitor=LoadMonitoringConfigService(self.factory)
                for item in (snapshot.get('monitoring') or {}).get('services',[]):
                    with self.factory() as s:
                        revision=s.get(ApiLoadMonitoringRevision,item['revision_id'])
                        service_id=revision.service_id if revision else None
                    try:monitor.test_connection(service_id,actor=actor,revision_id=item['revision_id'])
                    except Exception:
                        if item.get('required'):raise
                result=service.preflight(run_id,actor)
                if result.state!='queued':raise ValueError('功能预检未通过，本次不启动压力')
                with self.factory.begin() as s:
                    row=s.scalar(select(ApiLoadSchedule).where(ApiLoadSchedule.id==id).with_for_update())
                    if not row.enabled:raise ValueError('计划已停用，本次不启动压力')
                    service.start(run_id,actor)
                    row.last_status='running';row.last_message='预检通过，执行中'
        except Exception as error:
            with self.factory.begin() as s:
                row=s.get(ApiLoadSchedule,id);row.last_status='blocked';row.last_message=f'本次已阻断（{type(error).__name__}），请在执行详情检查权限、节点及预检结果'
            if run_id:
                self._cancel_preparation(run_id)

    def _cancel_preparation(self, run_id):
        # Internal cancellation may run after the creator loses permission.
        # It can only cancel an unstarted draft belonging to this schedule flow.
        from ..models.load_testing import ApiLoadRunShard
        with self.factory.begin() as s:
            occurrence=s.scalar(select(ApiLoadScheduleOccurrence).where(ApiLoadScheduleOccurrence.run_id==run_id))
            run=s.scalar(select(ApiLoadRun).where(ApiLoadRun.id==run_id).with_for_update())
            if occurrence is None or run is None or run.state not in {'draft','preflighting','queued'}:return
            run.state='cancelled';run.verdict='inconclusive';run.finished_at=self.now();run.stop_reason='定时执行已停用或门禁未通过，本次不重试'
            for shard in s.scalars(select(ApiLoadRunShard).where(ApiLoadRunShard.run_id==run_id).with_for_update()):
                shard.state='cancelled'


def notify_schedule_run(factory, run_id, *, report=None):
    """At-most-once attempt; ambiguous delivery is never automatically retried."""
    from .notification_service import NotificationService
    with factory.begin() as s:
        occurrence=s.scalar(select(ApiLoadScheduleOccurrence).where(ApiLoadScheduleOccurrence.run_id==run_id).with_for_update())
        if occurrence is None:return False
        if not occurrence.notification_enabled or occurrence.notification_status!='pending':return True
        occurrence.notification_status='sending'
        actor=occurrence.created_by
        occurrence_id=occurrence.id
    status='sent'
    try:
        if access.get_access_profile(actor) is None:
            raise access.AccessDeniedError('platform.notify')
        access.require_permission(actor,'platform.notify')
        NotificationService(factory).send_load_test_report(run_id,actor,report=report)
    except Exception:
        status='failed'
    with factory.begin() as s:
        occurrence=s.get(ApiLoadScheduleOccurrence,occurrence_id)
        occurrence.notification_status=status
    return True
