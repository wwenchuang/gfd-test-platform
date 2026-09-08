"""Authenticated read-only HTTP boundary for explicit performance comparisons."""
from . import access
from .db import _session_factory
from .http import API_PREFIX, ApiHttpError, _domain_error, _failure, _request_id, _success
from .services.load_run_comparison_service import LoadRunComparisonError, LoadRunComparisonService


def handle_load_run_comparison_request(method, query, actor, factory):
    access.require_permission(actor,'api.view')
    access.require_permission(actor,'api.loadtest.view')
    if method!='GET': raise ApiHttpError(405,'method_not_allowed','对比接口只接受只读查询')
    if not isinstance(query,dict) or set(query)-{'run_ids'}:
        raise ApiHttpError(422,'comparison_invalid','对比查询参数无效')
    raw=query.get('run_ids')
    if not isinstance(raw,str) or len(raw)>184: raise ApiHttpError(422,'comparison_invalid','请选择 2 至 5 个执行')
    try:
        return {'comparison':LoadRunComparisonService(factory).compare(raw.split(','),actor)},200
    except LoadRunComparisonError as error:
        raise ApiHttpError(error.status,'comparison_invalid',str(error)) from None


def dispatch_load_run_comparison_request(handler, method, path, query, actor):
    if path.rstrip('/')!=API_PREFIX.rstrip('/')+'/load-run-comparisons': return False
    request_id=_request_id(handler)
    try:
        result,status=handle_load_run_comparison_request(method,query,actor,_session_factory())
        _success(handler,result,request_id,status)
    except ApiHttpError as error:
        _failure(handler,error,request_id)
    except Exception as error:
        _failure(handler,_domain_error(error),request_id)
    return True
