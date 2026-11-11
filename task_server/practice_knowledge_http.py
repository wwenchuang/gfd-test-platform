"""Viewing the curated app knowledge corpus and the published retrieval evaluation."""
from task_server.services import requirement_evaluation_view as evaluation
from task_server.services.business_line_service import PRIMARY_APP_PACKAGE, configured_test_applications
from task_server.services.ui_execution_contract import AccessDenied, require_ui_actor

BASE = '/api/platform/requirement-design/knowledge'
PERFORMANCE = '/api/platform/requirement-design/practice-results/performance'
METHODS = ('keyword', 'vector')
METRICS = ('recall_at_5', 'citation_support', 'unknown_refusal', 'access_refusal', 'zero_leak')


def is_read_route(method, path):
    return (method in ('GET', 'HEAD') and path in (BASE, BASE+'/evaluation', PERFORMANCE, PERFORMANCE+'-evidence') or
            method == 'POST' and path == BASE+'/search')


def _apps(handler):
    try:
        profile = require_ui_actor(permission='ui.view')
    except AccessDenied:
        profile = {}
    scope = (profile.get('scope') or {}).get('ui_apps')
    if scope == '*' or isinstance(scope, list) and PRIMARY_APP_PACKAGE in scope:
        for app in configured_test_applications():
            if app['package'] == PRIMARY_APP_PACKAGE:
                return ([{'id': app['package'], 'name': app['name']}],)
    handler._json({'ok': False, 'error': '当前账号没有可检索的应用'}, 403)
    return None


def _search(question, *, allowed_apps, method):
    from task_server.services.practice_knowledge_search import search
    return search(question, allowed_apps=allowed_apps, method=method)


def _references(rows):
    return [evaluation._fields(row, 'document_id paragraph_id') for row in rows]


def _report(allowed_apps):
    try:
        doc = evaluation._published_file('knowledge-holdout.json')
        scope = doc['scope']['app_ids']
        if not isinstance(scope, list) or not scope or not all(app in allowed_apps for app in scope):
            raise AccessDenied('report_forbidden')
        if doc['schema_version'] != 1 or doc['split'] != 'holdout':
            raise evaluation.Unavailable()
        methods = {}
        for method, item in doc['methods'].items():
            if method in METHODS:
                methods[method] = dict(evaluation._fields(item, 'engine elapsed_ms'), metrics={
                    key: evaluation._fields(value, 'count total') for key, value in item['metrics'].items() if key in METRICS})
        questions = []
        for item in doc['questions']:
            results = {}
            for method, row in item['results'].items():
                if method in METHODS:
                    results[method] = dict(evaluation._fields(row, 'status recall_hits citation_supported refused leaked elapsed_ms'),
                                           top5=_references(row.get('top5', [])))
            questions.append(dict(evaluation._fields(item, 'id kind question'), gold=_references(item['gold']), results=results))
        return {**evaluation._fields(doc, 'created_at'), 'methods': methods, 'questions': questions,
                'human_review': evaluation._fields(doc['human_review'], 'reviewed total status')}
    except AccessDenied:
        raise
    except (OSError, ValueError, KeyError, AttributeError, TypeError, RecursionError):
        raise evaluation.Unavailable() from None


def register_routes(route_get, route_post):
    from task_server.requirement_design_http import _params, _serve
    from task_server.services import practice_performance_view

    @route_get(BASE)
    def available(handler, qs):
        def read(apps):
            _params(qs, ())
            return {'apps': apps}
        _serve(handler, read, authorize=_apps)

    @route_post(BASE+'/search')
    def search(handler, qs):
        def query(apps):
            _params(qs, ())
            body = handler._body()
            _params(body, ('app_id', 'question', 'method'))
            if body['app_id'] not in [app['id'] for app in apps]:
                raise AccessDenied('app_forbidden')
            question = body['question']
            if not isinstance(question, str) or not 1 <= len(question.strip()) <= 500 or body['method'] not in METHODS:
                raise ValueError('请输入 1 至 500 字的问题并选择检索方式')
            return {'result': _search(question.strip(), allowed_apps=[body['app_id']], method=body['method'])}
        _serve(handler, query, authorize=_apps)

    @route_get(BASE+'/evaluation')
    def published(handler, qs):
        def read(apps):
            _params(qs, ())
            return {'report': _report([app['id'] for app in apps])}
        _serve(handler, read, authorize=_apps)

    @route_get(PERFORMANCE)
    def performance(handler, qs):
        def read(apps):
            _params(qs, ())
            return {'report': practice_performance_view.published([app['id'] for app in apps])}
        _serve(handler, read, authorize=_apps)

    @route_get(PERFORMANCE+'-evidence')
    def performance_evidence(handler, qs):
        def read(apps):
            _params(qs, ('id',))
            return practice_performance_view.evidence([app['id'] for app in apps], qs['id'])
        _serve(handler, read, authorize=_apps, download=True)
