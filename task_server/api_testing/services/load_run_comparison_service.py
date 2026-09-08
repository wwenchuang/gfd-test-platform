"""Read-only, bounded comparisons and observed endpoint coverage. No baseline writes."""
import copy
import json
import math
from datetime import datetime, timezone

from sqlalchemy import func, select
from .. import access
from ..models.case import ApiCase, ApiCaseVersion
from ..models.load_testing import ApiLoadRun, ApiLoadScenarioVersion, ApiLoadMetricBucket, ApiLoadSample
from ..models.source import ApiSource, ApiSourceRevision, ApiSourceEndpoint
from .load_report_service import LoadReportService
from .load_scenario_service import LoadScenarioService

MAX_BUCKETS = 50000
MAX_SAMPLES = 10000
CONDITIONS = {'environment':'环境版本', 'scenario':'场景内容版本', 'purpose':'测试目的', 'release':'业务版本',
              'data_profile':'数据与账号规模', 'cache_state':'缓存状态', 'workload':'负载参数',
              'thresholds':'验收标准', 'dataset':'数据集快照', 'agents':'压力机与分配规格',
              'monitoring':'监控范围', 'target_resources':'实测资源分母', 'stop_policy':'自动停止策略'}
METRICS = [('p95_ms','P95 响应时间','ms'), ('requests_per_second','请求吞吐','RPS'),
           ('http_error_percent','HTTP 失败率','%'), ('business_failure_percent','业务断言失败率','%')]
REQUIRED = frozenset(CONDITIONS) - {'stop_policy'}


class LoadRunComparisonError(ValueError):
    def __init__(self, message, status=422):
        super().__init__(message)
        self.status = status


def _known(value):
    return value is not None and (not isinstance(value,str) or value.strip().lower() not in {'','unknown','未知','未记录','未核验'})


def _finite(value):
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)


def compare_run_evidence(reference, current):
    differences, reasons = [], []
    for key, label in CONDITIONS.items():
        left, right = reference['conditions'].get(key), current['conditions'].get(key)
        unknown = key in REQUIRED and (not _known(left) or not _known(right))
        if unknown or left != right:
            kind = 'unknown' if unknown else 'variable' if key == 'release' else 'blocking'
            differences.append({'key':key,'label':label,'reference':left,'current':right,'kind':kind})
            if kind != 'variable': reasons.append(label + ('未完整记录或核验' if unknown else '不同'))
    for title, item in [('参照执行',reference),('当前执行',current)]:
        if not item['evidence']['complete']:
            reasons.extend(title + '：' + reason for reason in (item['evidence'].get('reasons') or ['证据不足']))
    eligible = not reasons
    rows = []
    for key,label,unit in METRICS:
        left,right=reference['metrics'].get(key),current['metrics'].get(key)
        valid = eligible and _finite(left) and _finite(right)
        delta = round(right-left, 6) if valid else None
        rows.append({'key':key,'label':label,'unit':unit,'reference':left,'current':right,'delta':delta,
                     'change_percent':round((right-left)/abs(left)*100,6) if valid and left != 0 else None,
                     'direction':('increased' if delta > 0 else 'decreased' if delta < 0 else 'unchanged') if delta is not None else 'unknown'})
    return {'reference_run_id':reference['run_id'],'run_id':current['run_id'],'eligible':eligible,
            'reasons':reasons,'differences':differences,'metrics':rows,
            'scope':'只对记录与采样支持的相同条件给出数值差异；业务版本可作为比较变量。数值下降不证明优化因果或最大容量。'}


def _safe_agents(configuration):
    agents = configuration.get('agents')
    if not isinstance(agents,list) or not agents: return None
    result=[]
    for agent in agents:
        if not agent.get('agent_version') or not agent.get('k6_version') or not agent.get('hard_limits'): return None
        result.append({key:copy.deepcopy(agent.get(key)) for key in ('id','agent_version','k6_version','hard_limits','soft_limits','allocation')})
    return sorted(result,key=lambda item: str(item.get('id')))


def _monitoring_scope(configuration):
    config=configuration.get('monitoring') or {}
    return {'before_seconds':config.get('before_seconds',0),'after_seconds':config.get('after_seconds',0),
            'services':sorted([{key:copy.deepcopy(item.get(key)) for key in ('revision_id','deployment','labels','metrics','step_seconds','required')} for item in config.get('services',[])],key=lambda item:str(item.get('revision_id')))}


def _resource_basis(report):
    """Compare only real measured denominator series, never infer hardware from labels."""
    result=[]
    for service in (report.get('monitoring') or {}).get('services',[]):
        for metric in service.get('metrics',[]):
            if metric.get('denominator') not in {'host_cpu_cores','host_memory_total_bytes','container_cpu_quota_cores'}: continue
            for series in metric.get('series',[]):
                values=sorted({_p['denominator_value'] for _p in series.get('points',[]) if _finite(_p.get('denominator_value')) and _p['denominator_value'] > 0})
                if len(values)>1: return None
                if values: result.append({'revision_id':service.get('revision_id'),'key':metric.get('key'),'labels':copy.deepcopy(series.get('labels',{})),'values':values})
    return sorted(result,key=lambda item:json.dumps(item,sort_keys=True)) or None


def _run_evidence(run, report):
    config=run.configuration or {}; context=config.get('test_context') or {}
    reasons=[]
    if not (report.get('evidence') or {}).get('complete'): reasons.append('请求/耗时或节点完成证据不完整')
    if not (report.get('load_goal') or {}).get('reached'): reasons.append('实际负载未达目标')
    monitoring=report.get('monitoring') or {}
    selected={item.get('revision_id') for item in config.get('monitoring',{}).get('services',[])}
    collected={item.get('revision_id') for item in monitoring.get('services',[])}
    if selected and (not monitoring.get('terminal') or not selected.issubset(collected) or not (report.get('evidence') or {}).get('monitoring_required_complete') or any(s.get('state')!='completed' for s in monitoring.get('services',[]))): reasons.append('选定监控尚未收齐')
    latency=report.get('latency') or {};transport=report.get('transport') or {};business=report.get('business') or {}
    conditions={'environment':run.environment_revision_id,'scenario':(config.get('scenario') or {}).get('content_hash'),
                **{key:context.get(key) or None for key in ('purpose','release','data_profile','cache_state')},
                'workload':config.get('workload') or None,'thresholds':config.get('thresholds'),
                'dataset':{key:copy.deepcopy((config.get('dataset') or {}).get(key)) for key in ('id','content_hash','usage_mode','row_count')} if 'dataset' in config else None,
                'agents':_safe_agents(config),'monitoring':_monitoring_scope(config),
                'target_resources':_resource_basis(report),'stop_policy':copy.deepcopy(config.get('stop_policy'))}
    return {'run_id':run.id,'scenario_name':(config.get('scenario') or {}).get('name','未记录'),
            'environment_name':(config.get('environment') or {}).get('name','未记录'),
            'started_at':run.started_at.isoformat() if run.started_at else None,'finished_at':run.finished_at.isoformat(),
            'conditions':conditions,'evidence':{'complete':not reasons,'reasons':reasons},
            'metrics':{'p95_ms':latency.get('p95_ms') if latency.get('sample_count') else None,
                       'requests_per_second':transport.get('requests_per_second') if transport.get('requests') else None,
                       'http_error_percent':100*transport['http_error_rate'] if transport.get('requests') and _finite(transport.get('http_error_rate')) else None,
                       'business_failure_percent':100*business['failure_rate'] if business.get('assertions') and _finite(business.get('failure_rate')) else None}}


def _copied_steps(items):
    generated=[]; result={}
    for index,item in enumerate(items):
        processing=item.get('processing') or {}
        generated.extend({'id':f'case-{index+1}-setup-{n+1}'} for n,_ in enumerate(processing.get('setup_steps') or []))
        step_id=LoadScenarioService._unique_step_id(item.get('id'),index,generated)
        generated.append({'id':step_id});result[step_id]=item
        generated.extend({'id':f'{step_id}-cleanup-{n+1}'} for n,_ in enumerate(processing.get('cleanup_steps') or []))
    return result


def _request_key(request):
    request=request or {}
    return (str(request.get('method','GET')).upper(),request.get('path'),request.get('service','default'))


class _ComparisonReportService(LoadReportService):
    @staticmethod
    def _previous_run(session, run):
        # The requested set is explicit: do not load an unrelated historical run.
        return None, '仅对比显式选择的执行'


class LoadRunComparisonService:
    def __init__(self, session_factory): self.session_factory=session_factory

    def compare(self, run_ids, actor):
        access.require_permission(actor,'api.loadtest.view')
        if (not isinstance(run_ids,list) or not 2 <= len(run_ids) <= 5 or
                any(not isinstance(item,str) or not item or len(item)>36 for item in run_ids) or len(set(run_ids))!=len(run_ids)):
            raise LoadRunComparisonError('请选择 2 至 5 个不重复的已完成执行')
        with self.session_factory() as session:
            runs=[]
            # Authorize every run before report or source evidence is loaded.
            for run_id in run_ids:
                run=session.get(ApiLoadRun,run_id)
                if run is None: raise LoadRunComparisonError('执行不存在或不可访问',404)
                access.require_resource(session,run,actor,'api.loadtest.view')
                runs.append(run)
            if len({run.project_id for run in runs})!=1: raise LoadRunComparisonError('只能比较同一项目的执行')
            if any(run.state!='finished' or run.finished_at is None for run in runs): raise LoadRunComparisonError('只支持已正常完成的执行',409)
            for model,limit in [(ApiLoadMetricBucket,MAX_BUCKETS),(ApiLoadSample,MAX_SAMPLES)]:
                count=session.scalar(select(func.count()).select_from(model).where(model.run_id.in_(run_ids)))
                if count>limit: raise LoadRunComparisonError('所选执行证据量超过本次对比上限，请缩小范围')
            coverage=self._coverage(session,runs,actor)
        report_service=_ComparisonReportService(self.session_factory)
        public=[_run_evidence(run,report_service.build(run.id,actor)) for run in runs]
        return {'schema_version':1,'reference_run_id':run_ids[0],'runs':public,
                'comparisons':[compare_run_evidence(public[0],item) for item in public[1:]],
                'coverage':coverage,'generated_at':datetime.now(timezone.utc).isoformat(),
                'notice':'覆盖仅表示所选轮次实际尝试过的迭代请求，不表示接口业务通过或完整测试覆盖；本页不采纳基线、不自动重跑。'}

    @staticmethod
    def _coverage(session,runs,actor):
        project_id=runs[0].project_id
        asset_query=(select(ApiSourceEndpoint.id).join(ApiSourceRevision,ApiSourceEndpoint.revision_id==ApiSourceRevision.id)
                     .join(ApiSource,ApiSourceRevision.source_id==ApiSource.id)
                     .where(ApiSource.project_id==project_id,ApiSource.status=='active',ApiSource.active_revision_id==ApiSourceRevision.id,
                            access.resource_predicate(actor,ApiSourceEndpoint)))
        asset_ids=set(session.scalars(asset_query.limit(20001)))
        if len(asset_ids)>20000: raise LoadRunComparisonError('当前接口资产超过覆盖统计上限')
        asset_count=len(asset_ids)
        observed={};unmapped=[];historical=[];attempted=0
        for run in runs:
            version=session.get(ApiLoadScenarioVersion,run.scenario_version_id)
            definition=(version.definition if version else {}) or {}
            steps={item['id']:item for item in definition.get('steps',[]) if isinstance(item,dict) and item.get('id')}
            source=definition.get('source_snapshot') or (version.source_snapshot if version else {}) or {}
            copied=_copied_steps(source.get('items') or []) if source.get('type')=='case_version' else {}
            records=session.execute(select(ApiLoadMetricBucket.scenario_step_id,func.sum(ApiLoadMetricBucket.metrics['requests'].as_integer()))
                                    .where(ApiLoadMetricBucket.run_id==run.id).group_by(ApiLoadMetricBucket.scenario_step_id))
            for step_id,requests in records:
                if not requests or requests<=0: continue
                step=steps.get(step_id)
                if step and step.get('scope') not in (None,'iteration'): continue
                attempted+=1; entry={'run_id':run.id,'step_id':step_id,'requests':int(requests)}
                item=copied.get(step_id)
                if not step or step.get('scope')!='iteration' or not item or item.get('id') not in source.get('version_ids',[]):
                    unmapped.append(entry);continue
                case_version=session.get(ApiCaseVersion,item['id']);case=session.get(ApiCase,case_version.case_id) if case_version else None
                if not case or case.project_id!=project_id or not access.resource_allowed(session,case_version,actor):
                    unmapped.append(entry);continue
                stored=case_version.request_template or {};stored=stored.get('request',stored)
                if not (_request_key(step.get('request'))==_request_key(item.get('request'))==_request_key(stored)):
                    unmapped.append(entry);continue
                endpoint=session.get(ApiSourceEndpoint,case_version.endpoint_id)
                if endpoint is None or not access.resource_allowed(session,endpoint,actor): unmapped.append(entry);continue
                # Exact endpoint ID only. A superseded version never silently maps by path.
                if endpoint.id not in asset_ids:
                    historical.append(entry);continue
                row=observed.setdefault(endpoint.id,{'endpoint_id':endpoint.id,'method':endpoint.method,'path':endpoint.path,'run_ids':set(),'requests':0})
                row['run_ids'].add(run.id);row['requests']+=int(requests)
        exact=not unmapped and not historical and attempted>0
        return {'scope':'当前项目当前激活接口资产，按 endpoint ID 去重；只计有请求证据的 iteration 步骤',
                'selected_run_count':len(runs),'observed_step_run_count':attempted,'asset_endpoint_count':asset_count,
                'observed_endpoint_count':len(observed),'coverage_ratio':len(observed)/asset_count if exact and asset_count else None,
                'mapping_complete':exact,'unmapped_step_run_count':len(unmapped),'historical_endpoint_step_run_count':len(historical),
                'endpoint_details_truncated':len(observed)>200,
                'endpoints':[dict(item,run_ids=sorted(item['run_ids'])) for item in observed.values()][:200],
                'unmapped_steps':unmapped[:100],'historical_steps':historical[:100],
                'notice':'手写或修改后的未归属步骤、缺失来源与历史接口版本不按路径猜测归属；存在这些情况时覆盖率未知。资产分母是查询时的激活版本，非历史执行时的全部资产。'}
