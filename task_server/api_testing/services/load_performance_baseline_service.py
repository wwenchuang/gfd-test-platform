"""Manual performance reference lifecycle with frozen, secret-free comparison evidence."""
import copy
import hashlib
import json
import math
from sqlalchemy import func, select
from .. import access
from ..models.environment import ApiEnvironmentRevision
from ..models.project import ApiProject
from ..models.load_testing import ApiLoadRun, ApiLoadScenario, ApiLoadScenarioVersion, ApiLoadMetricBucket, ApiLoadSample
from ..models.load_performance_baseline import ApiLoadPerformanceBaseline
from .load_run_comparison_service import _ComparisonReportService, _run_evidence, compare_run_evidence, MAX_BUCKETS, MAX_SAMPLES

POLICIES = {
    'p95_increase_percent':('p95_ms','P95 增幅','%',1,'change_percent',1000),
    'rps_decrease_percent':('requests_per_second','请求吞吐降幅','%',-1,'change_percent',100),
    'http_error_increase_points':('http_error_percent','HTTP 失败率增幅','百分点',1,'delta',100),
    'business_failure_increase_points':('business_failure_percent','业务断言失败率增幅','百分点',1,'delta',100),
}


class LoadPerformanceBaselineError(ValueError):
    def __init__(self,message,status=422): super().__init__(message);self.status=status


def _hash(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()


def _policy(value):
    if not isinstance(value,dict) or not value or set(value)-set(POLICIES): raise LoadPerformanceBaselineError('请选择支持的退化预警指标')
    for key,limit in value.items():
        if isinstance(limit,bool) or not isinstance(limit,(int,float)) or not math.isfinite(limit) or not 0<=limit<=POLICIES[key][5]:
            raise LoadPerformanceBaselineError('退化预警阈值无效')
    return copy.deepcopy(value)


def evaluate_regression(reference,current,policy):
    comparison=compare_run_evidence(reference,current)
    if any(reference.get(key)!=current.get(key) for key in ('statistics_schema_version','statistical_methods')):
        comparison['eligible']=False;comparison['reasons'].append('冻结基线与当前执行的统计口径版本不同')
        for metric in comparison['metrics']:
            metric.update(delta=None,change_percent=None,direction='unknown')
    rows={item['key']:item for item in comparison['metrics']};checks=[]
    for key,limit in _policy(policy).items():
        metric,label,unit,direction,field,_=POLICIES[key]
        raw=rows[metric].get(field)
        actual=round(direction*raw,6) if comparison['eligible'] and raw is not None else None
        checks.append({'key':key,'label':label,'unit':unit,'limit':limit,'actual':actual,'triggered':actual>limit if actual is not None else None})
    incomplete=not comparison['eligible'] or any(row['actual'] is None for row in checks)
    state='inconclusive' if incomplete else 'regression_warning' if any(row['triggered'] for row in checks) else 'within_reference'
    return {'state':state,'checks':checks,'comparison':comparison,
            'message':{'inconclusive':'条件或指标证据不足，不能给出完整退化判断。','regression_warning':'同条件下有已选指标超过人工设定的退化预警阈值，请检查并复验。','within_reference':'所选指标未超过该参考基线的预警阈值；这不代表整体性能达标。'}[state]}


def _view(record,detail=False):
    snapshot=record.evidence_snapshot
    if _hash(snapshot)!=record.evidence_hash:raise LoadPerformanceBaselineError('性能参考基线证据校验失败',409)
    result={'id':record.id,'project_id':record.project_id,'scenario_id':record.scenario_id,'environment_id':record.environment_id,
            'environment_revision_id':record.environment_revision_id,'source_run_id':record.source_run_id,'name':record.name,
            'adoption_reason':record.adoption_reason,'status':record.status,'evidence_hash':record.evidence_hash,
            'regression_policy':copy.deepcopy(record.regression_policy),'created_at':record.created_at.isoformat(),
            'created_by':record.created_by,'scenario_name':snapshot.get('scenario_name'),'environment_name':snapshot.get('environment_name'),
            'release':snapshot.get('conditions',{}).get('release'),'metrics':copy.deepcopy(snapshot.get('metrics',{}))}
    if detail:result['evidence_snapshot']=copy.deepcopy(snapshot)
    return result


class LoadPerformanceBaselineService:
    def __init__(self,session_factory):self.session_factory=session_factory

    def _candidate(self,run_id,actor):
        access.require_permission(actor,'api.loadtest.view')
        if not isinstance(run_id,str) or not run_id or len(run_id)>36: raise LoadPerformanceBaselineError('执行编号无效')
        with self.session_factory() as session:
            run=session.get(ApiLoadRun,run_id)
            if run is None:raise LoadPerformanceBaselineError('执行不存在或不可访问',404)
            access.require_resource(session,run,actor,'api.loadtest.view')
            if run.state!='finished' or run.finished_at is None:raise LoadPerformanceBaselineError('只支持已正常完成的执行',409)
            version=session.get(ApiLoadScenarioVersion,run.scenario_version_id)
            revision=session.get(ApiEnvironmentRevision,run.environment_revision_id)
            for model,limit in [(ApiLoadMetricBucket,MAX_BUCKETS),(ApiLoadSample,MAX_SAMPLES)]:
                if session.scalar(select(func.count()).select_from(model).where(model.run_id==run.id))>limit:raise LoadPerformanceBaselineError('执行证据超过本次处理上限')
            scope={'project_id':run.project_id,'scenario_id':version.scenario_id,'environment_id':revision.environment_id,'environment_revision_id':revision.id}
        report=_ComparisonReportService(self.session_factory).build(run.id,actor)
        snapshot=_run_evidence(run,report)
        snapshot['statistics_schema_version']=report.get('statistics_schema_version')
        snapshot['statistical_methods']={key:(report.get('statistical_basis') or {}).get(key) for key in ('percentile_method','rate_duration_basis')}
        return scope,snapshot

    def list(self,project_id,actor):
        access.require_permission(actor,'api.loadtest.view')
        with self.session_factory() as session:
            project=session.get(ApiProject,project_id);access.require_resource(session,project,actor,'api.loadtest.view')
            rows=session.scalars(select(ApiLoadPerformanceBaseline).where(ApiLoadPerformanceBaseline.project_id==project_id,access.resource_predicate(actor,ApiLoadPerformanceBaseline)).order_by((ApiLoadPerformanceBaseline.status=='active').desc(),ApiLoadPerformanceBaseline.created_at.desc()).limit(201)).all()
            return {'baselines':[_view(row) for row in rows[:200]],'truncated':len(rows)>200}

    def get(self,baseline_id,actor):
        with self.session_factory() as session:
            row=self._record(session,baseline_id,actor)
            return _view(row,True)

    @staticmethod
    def _record(session,baseline_id,actor,manage=False):
        access.require_permission(actor,'api.loadtest.view')
        if manage:access.require_permission(actor,'api.baseline')
        row=session.get(ApiLoadPerformanceBaseline,baseline_id)
        if row is None:raise LoadPerformanceBaselineError('性能参考基线不存在或不可访问',404)
        access.require_resource(session,row,actor,'api.loadtest.view')
        if _hash(row.evidence_snapshot)!=row.evidence_hash:raise LoadPerformanceBaselineError('性能参考基线证据校验失败',409)
        return row

    def adopt(self,payload,actor):
        access.require_permission(actor,'api.loadtest.view');access.require_permission(actor,'api.baseline')
        if not isinstance(payload,dict) or set(payload)!={'run_id','name','adoption_reason','regression_policy'}:raise LoadPerformanceBaselineError('采纳参数无效')
        for key,limit in [('name',160),('adoption_reason',2000)]:
            if not isinstance(payload[key],str) or not payload[key].strip() or len(payload[key])>limit:raise LoadPerformanceBaselineError('请填写有效的基线名称和采纳理由')
        policy=_policy(payload['regression_policy']);scope,snapshot=self._candidate(payload['run_id'],actor)
        if not snapshot['evidence']['complete']:raise LoadPerformanceBaselineError('证据不完整或实际负载未达到目标，不能采纳为性能参考基线',409)
        conditions=compare_run_evidence(snapshot,snapshot)
        if not conditions['eligible']:raise LoadPerformanceBaselineError('条件未完整核验，不能采纳：'+'；'.join(conditions['reasons']),409)
        if any(snapshot['metrics'].get(POLICIES[key][0]) is None for key in policy):raise LoadPerformanceBaselineError('所选预警指标在源执行中没有样本，不能采纳',409)
        evidence_hash=_hash(snapshot)
        adoption_key=_hash({'scope':scope,'run_id':payload['run_id'],'name':payload['name'].strip(),'reason':payload['adoption_reason'].strip(),'policy':policy,'evidence_hash':evidence_hash})
        with self.session_factory.begin() as session:
            # Serialize active-slot replacement across concurrent adoption requests.
            session.scalar(select(ApiLoadScenario).where(ApiLoadScenario.id==scope['scenario_id']).with_for_update())
            existing=session.scalar(select(ApiLoadPerformanceBaseline).where(ApiLoadPerformanceBaseline.adoption_key==adoption_key))
            if existing:return _view(self._record(session,existing.id,actor),True)
            active=session.scalars(select(ApiLoadPerformanceBaseline).where(ApiLoadPerformanceBaseline.project_id==scope['project_id'],ApiLoadPerformanceBaseline.scenario_id==scope['scenario_id'],ApiLoadPerformanceBaseline.environment_id==scope['environment_id'],ApiLoadPerformanceBaseline.status=='active')).all()
            for previous in active:previous.status='retired';previous.updated_by=actor
            session.flush()
            row=ApiLoadPerformanceBaseline(**scope,source_run_id=payload['run_id'],name=payload['name'].strip(),adoption_reason=payload['adoption_reason'].strip(),status='active',evidence_snapshot=copy.deepcopy(snapshot),evidence_hash=evidence_hash,regression_policy=policy,adoption_key=adoption_key,owner_id=actor,created_by=actor,updated_by=actor)
            session.add(row);session.flush();return _view(row,True)

    def retire(self,baseline_id,actor):
        with self.session_factory.begin() as session:
            row=self._record(session,baseline_id,actor,True);row.status='retired';row.updated_by=actor;session.flush();return _view(row)

    def compare(self,baseline_id,run_id,actor):
        with self.session_factory() as session:
            row=self._record(session,baseline_id,actor);reference=copy.deepcopy(row.evidence_snapshot);policy=copy.deepcopy(row.regression_policy)
            scope=(row.project_id,row.scenario_id,row.environment_id);baseline=_view(row)
        current_scope,current=self._candidate(run_id,actor)
        if tuple(current_scope[key] for key in ('project_id','scenario_id','environment_id'))!=scope:raise LoadPerformanceBaselineError('请选择同一项目、场景和环境的执行；环境版本差异会在比较中说明')
        return {'baseline':baseline,'current_run_id':run_id,**evaluate_regression(reference,current,policy),
                'notice':'手动只读核对，不修改执行结论，不发送通知、不触发发压。参考证据取自采纳时的固定快照。'}
