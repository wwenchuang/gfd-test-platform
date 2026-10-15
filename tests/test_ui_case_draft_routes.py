import pytest

from task_server.access_control import AccessDenied, MainAccess
from test_operation_http import operation_server, request, events
from test_identity import identity_db


def test_scoped_draft_routes_have_distinct_permissions():
    access = MainAccess({'permissions': ['ui.view', 'ui.edit', 'ui.execute'],
                         'scope': {'ui_apps': ['app.a']}}, [])
    for method, path in [('GET', '/api/ui-case-drafts'),
                         ('GET', '/api/ui-case-drafts/draft-1'),
                         ('POST', '/api/ui-case-drafts'),
                         ('POST', '/api/ui-case-drafts/draft-1/update'),
                         ('POST', '/api/ui-case-drafts/draft-1/validate'),
                         ('POST', '/api/ui-case-drafts/draft-1/try')]:
        access.check(method, path)
    with pytest.raises(AccessDenied, match='ui.baseline'):
        access.check('POST', '/api/ui-case-drafts/draft-1/publish')
    with pytest.raises(AccessDenied):
        access.check('POST', '/api/ui-case-drafts/draft-1/arbitrary')
    viewer = MainAccess({'permissions': ['ui.view'], 'scope': {'ui_apps': '*'}}, [])
    with pytest.raises(AccessDenied, match='ui.edit'):
        viewer.check('POST', '/api/ui-case-drafts')


def test_registered_draft_routes_do_not_leak_internal_gate(monkeypatch):
    from task_server import ui_case_draft_http
    from task_server.services import ui_case_draft_service
    routes, regex = {}, {}
    def register(key):
        def decorator(fn):
            routes[key] = fn
            return fn
        return decorator
    def register_regex(key):
        def decorator(fn):
            regex[key] = fn
            return fn
        return decorator
    ui_case_draft_http.register_routes(register, register, register_regex, register_regex)
    monkeypatch.setenv('UI_SIMPLE_FLOW_ENABLED', '1')
    monkeypatch.setattr(ui_case_draft_service, 'validate_draft', lambda *args: {
        'ready': True, 'blockers': [], 'draft': {'source_text': 'PRIVATE'},
        'target_resolution': {'secret': 'PRIVATE'}, 'compiled_case': {'yaml_content': 'PRIVATE'}})
    class Handler:
        headers = {}
        def _body(self): return {'revision': 2, 'target': {}}
        def _json(self, body, status=200): self.response, self.status = body, status
    import re
    handler = Handler()
    for pattern, fn in regex.items():
        match = re.fullmatch(pattern, '/api/ui-case-drafts/draft-1/validate')
        if match:
            fn(handler, {}, match)
            break
    assert handler.response == {'ok': True, 'gate': {'ready': True, 'blockers': []}}
    assert handler.status == 200


def test_draft_audit_keys_are_stable_and_do_not_include_description():
    from task_server import operation_http
    for action in ('update', 'restore', 'validate', 'try', 'publish'):
        path = '/api/ui-case-drafts/private-id/' + action
        assert operation_http.route_key('POST', path) == 'POST.post_ui_case_draft_action.' + action
        assert operation_http._resource(path, 'POST', {'source_text': 'SECRET'}, {}, 200) == ('ui_draft', '', 'ui_draft.' + action)
    assert operation_http.route_key('POST', '/api/ui-case-drafts/id/not-real') == 'unknown'


def test_draft_list_response_uses_one_items_layer(monkeypatch):
    from task_server import ui_case_draft_http
    from task_server.services import ui_case_draft_service
    routes = {}
    def register(key):
        def decorator(fn):
            routes[key] = fn
            return fn
        return decorator
    ui_case_draft_http.register_routes(register, register, register, register)
    monkeypatch.setattr(ui_case_draft_service, 'list_drafts', lambda *args: {'items': [{'draft_id': 'owned'}]})
    class Handler:
        def _json(self, body, status=200): self.response = body
    handler = Handler()
    routes['/api/ui-case-drafts']  # POST and GET use distinct tables in production.
    # Locate GET registration separately to exercise its actual serializer.
    get = {}
    def get_register(key):
        def decorator(fn): get[key] = fn; return fn
        return decorator
    ui_case_draft_http.register_routes(get_register, register, register, register)
    get['/api/ui-case-drafts'](handler, {'app_id': 'app.a'})
    assert handler.response == {'ok': True, 'items': [{'draft_id': 'owned'}]}


def test_trial_http_audit_is_acceptance_not_execution_pass(operation_server, monkeypatch):
    from task_server import auth
    from task_server.services import ui_case_draft_execution
    server, store = operation_server
    monkeypatch.setenv('UI_SIMPLE_FLOW_ENABLED', '1')
    monkeypatch.setattr(ui_case_draft_execution, 'create_draft_trial', lambda *a: {
        'ui_batch_id': 'uib_' + 'a' * 32, 'status': 'queued', 'execution_settled': False})
    status, raw = request(server, 'POST', '/api/ui-case-drafts/uid_' + 'b' * 32 + '/try',
                          auth.create_session_token(), {'revision': 3, 'target': {}, 'source_text': 'PRIVATE_SENTINEL'})
    assert status == 200, raw
    event = events(store)[0]
    assert event['action'] == 'ui_draft.try'
    assert event['resource_type'] == 'ui_draft'
    assert event['result'] == 'accepted'
    assert event['resource_id'] == 'uid_' + 'b' * 32
    assert 'PRIVATE_SENTINEL' not in str(event)
