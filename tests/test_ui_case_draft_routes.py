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
                         ('POST', '/api/ui-case-drafts/draft-1/yaml-candidate'),
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
    for action in ('update', 'restore', 'yaml-candidate', 'validate', 'try', 'publish'):
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

def test_recording_import_route_permission_and_service(monkeypatch):
    from task_server import ui_case_draft_http
    from task_server.services import ui_case_draft_service
    path = '/api/ui-case-drafts/import-recording'
    MainAccess({'permissions': ['ui.edit'], 'scope': {'ui_apps': '*'}}, []).check('POST', path)
    with pytest.raises(AccessDenied):
        MainAccess({'permissions': ['ui.view'], 'scope': {'ui_apps': '*'}}, []).check('POST', path)
    routes = {}
    def register(key):
        def decorator(fn): routes[key] = fn; return fn
        return decorator
    ui_case_draft_http.register_routes(register, register, register, register)
    monkeypatch.setenv('UI_SIMPLE_FLOW_ENABLED', '1')
    monkeypatch.setattr(ui_case_draft_service, 'import_recording_draft', lambda actor, body: {'draft_id':'uid_x','app_id':body['app_id']})
    class Handler:
        def _body(self): return {'app_id':'com.kfb.model','session_id':'a'*32}
        def _json(self, body, status=200): self.response=body
    handler = Handler()
    routes[path](handler,{})
    assert handler.response['draft']['app_id'] == 'com.kfb.model'


def test_touch_decision_requires_edit_and_dispatches_to_service(monkeypatch):
    from task_server import ui_case_draft_http
    from task_server.services import ui_case_draft_service
    path='/api/ui-case-drafts/draft-1/touch-decision'
    MainAccess({'permissions':['ui.edit'],'scope':{'ui_apps':'*'}},[]).check('POST',path)
    with pytest.raises(AccessDenied,match='ui.edit'):
        MainAccess({'permissions':['ui.view'],'scope':{'ui_apps':'*'}},[]).check('POST',path)
    routes={}
    def reg(pattern):
        def bind(fn):routes[pattern]=fn;return fn
        return bind
    ui_case_draft_http.register_routes(reg,reg,reg,reg)
    payload={'revision':2,'index':0,'frame_id':'frame-001','disposition':'not_action','step_id':''}
    calls=[]
    monkeypatch.setenv('UI_SIMPLE_FLOW_ENABLED','1')
    monkeypatch.setattr(ui_case_draft_service,'resolve_touch_ambiguity',lambda actor,identifier,body:(calls.append((actor,identifier,body)) or {'draft_id':identifier,'revision':3}))
    class Handler:
        headers={}
        def _body(self):return payload
        def _json(self,body,status=200):self.response,self.status=body,status
    import re
    matches=[(fn,re.fullmatch(pattern,path)) for pattern,fn in routes.items() if re.fullmatch(pattern,path)]
    assert len(matches)==1
    handler=Handler();matches[0][0](handler,{},matches[0][1])
    assert handler.status==200 and handler.response['draft']['revision']==3
    assert calls==[(None,'draft-1',payload)]


def test_touch_decision_audit_is_scoped_and_omits_private_body():
    from task_server import operation_http
    path='/api/ui-case-drafts/private-id/touch-decision'
    assert operation_http.route_key('POST',path)=='POST.post_ui_case_draft_action.touch-decision'
    assert operation_http._resource(path,'POST',{'observation':'PRIVATE'}, {},200)==('ui_draft','','ui_draft.touch-decision')


def test_touch_frame_route_uses_view_permission_and_sends_checked_bytes(monkeypatch):
    import io,re
    from task_server import ui_case_draft_http,operation_http
    from task_server.services import ui_case_draft_service
    path='/api/ui-case-drafts/draft-1/touch-frame'
    MainAccess({'permissions':['ui.view'],'scope':{'ui_apps':'*'}},[]).check('GET',path)
    with pytest.raises(AccessDenied,match='ui.view'):
        MainAccess({'permissions':['ui.edit'],'scope':{'ui_apps':'*'}},[]).check('GET',path)
    assert operation_http.route_key('GET',path)=='GET.get_ui_case_draft_touch_frame'
    assert operation_http._resource(path,'GET',{}, {},200)==('ui_draft','','ui_draft.touch_frame')
    routes={}
    def reg(pattern):
        def bind(fn):routes[pattern]=fn;return fn
        return bind
    ui_case_draft_http.register_routes(reg,reg,reg,reg)
    calls=[]
    monkeypatch.setattr(ui_case_draft_service,'get_touch_frame',lambda actor,identifier,revision,index:(calls.append((actor,identifier,revision,index)) or b'verified-jpeg'))
    class Handler:
        def __init__(self):self.wfile=io.BytesIO();self.headers={}
        def send_response(self,status):self.status=status
        def send_header(self,key,value):self.headers[key]=value
        def end_headers(self):pass
        def _json(self,body,status=200):self.response,self.status=body,status
    matches=[(fn,re.fullmatch(pattern,path)) for pattern,fn in routes.items() if re.fullmatch(pattern,path)]
    assert len(matches)==1
    handler=Handler();matches[0][0](handler,{'revision':'3','index':'0'},matches[0][1])
    assert handler.status==200 and handler.wfile.getvalue()==b'verified-jpeg'
    assert handler.headers['Content-Type']=='image/jpeg' and handler.headers['Content-Length']=='13'
    assert handler.headers['Cache-Control']=='private, no-store' and handler.headers['X-Content-Type-Options']=='nosniff'
    assert calls==[(None,'draft-1',3,0)]
    bad=Handler();matches[0][0](bad,{'revision':'0','index':'-1'},matches[0][1])
    assert bad.status==400 and len(calls)==1


@pytest.mark.parametrize('code,expected',[
    ('video_touch_decision_invalid','打印下发及恢复过程'),
    ('video_touch_evidence_invalid','录屏证据'),
])
def test_touch_review_errors_have_specific_safe_explanation(monkeypatch,code,expected):
    from task_server import ui_case_draft_http
    class Handler:
        def _json(self,body,status=200):self.response,self.status=body,status
    handler=Handler()
    def fail():raise ValueError(code)
    ui_case_draft_http._run(handler,fail)
    assert handler.status==400 and handler.response['code']==code and expected in handler.response['error']
