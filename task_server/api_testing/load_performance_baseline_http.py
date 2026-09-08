"""Narrow authenticated HTTP boundary for manually managed performance references."""
from . import access
from .db import _session_factory
from .http import API_PREFIX, ApiHttpError, _domain_error, _failure, _read_json_body, _request_id, _success
from .services.load_performance_baseline_service import LoadPerformanceBaselineError, LoadPerformanceBaselineService


def handle_load_performance_baseline_request(method,segments,query,payload,actor,factory):
    access.require_permission(actor,'api.view');access.require_permission(actor,'api.loadtest.view')
    if method!='GET':access.require_permission(actor,'api.baseline')
    service=LoadPerformanceBaselineService(factory)
    try:
        if segments==('load-performance-baselines',):
            if method=='GET':
                if not isinstance(query.get('project_id'),str) or set(query)!={'project_id'}:raise LoadPerformanceBaselineError('请选择基线所属项目')
                return service.list(query['project_id'],actor),200
            if method=='POST':return {'baseline':service.adopt(payload,actor)},201
        if len(segments)==2 and segments[0]=='load-performance-baselines' and method=='GET':return {'baseline':service.get(segments[1],actor)},200
        if len(segments)==3 and segments[0]=='load-performance-baselines':
            if segments[2]=='retire' and method=='POST':
                if payload not in (None,{}):raise LoadPerformanceBaselineError('停用不接受额外参数')
                return {'baseline':service.retire(segments[1],actor)},200
            if segments[2]=='compare' and method=='GET':
                if set(query)!={'run_id'}:raise LoadPerformanceBaselineError('请选择要核对的执行')
                return {'regression':service.compare(segments[1],query['run_id'],actor)},200
    except LoadPerformanceBaselineError as error:
        raise ApiHttpError(error.status,'performance_baseline_invalid',str(error)) from None
    raise ApiHttpError(404,'not_found','性能参考基线路由不存在')


def dispatch_load_performance_baseline_request(handler,method,path,query,actor):
    if not path.startswith(API_PREFIX):return False
    segments=tuple(part for part in path[len(API_PREFIX):].strip('/').split('/') if part)
    if not segments or segments[0]!='load-performance-baselines':return False
    request_id=_request_id(handler)
    try:
        payload=_read_json_body(handler) if method=='POST' else {}
        result,status=handle_load_performance_baseline_request(method,segments,query,payload,actor,_session_factory())
        _success(handler,result,request_id,status)
    except ApiHttpError as error:_failure(handler,error,request_id)
    except Exception as error:_failure(handler,_domain_error(error),request_id)
    return True
