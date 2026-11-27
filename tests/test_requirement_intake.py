import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from test_identity import activate, create_member, identity_db
from test_ui_execution_service import ACTORS, APP, TARGET, REVISION, environment
from test_ui_batch_dispatch import dispatch_env
from task_server.services.operation_attribution import actor_context
from task_server.services import requirement_design


def event(**changes):
    return dict(dict(source='business', event_id='E-1', app_id=APP, requirement_id='PRD-5',
                     revision='v1', title='导航可见', acceptance_criteria=['打开我的后显示个人页']), **changes)


@pytest.fixture
def inputs(dispatch_env, tmp_path, monkeypatch):
    monkeypatch.setattr(requirement_design, '_root', lambda: tmp_path / 'requirements')
    path = tmp_path / 'mapping.json'
    monkeypatch.setenv('MIDSCENE_REQUIREMENT_BINDINGS', str(path))
    path.write_text(json.dumps([dict(source='business', app_id=APP, requirement_id='PRD-5',
        revision='v1', runs=[dict(target=TARGET, cases=[dict(case_id='case-1', revision=REVISION)],
                                execution_policy='reviewed_non_printing')])]))
    return path, dispatch_env


def post(**changes):
    from task_server.services import requirement_intake as intake
    with actor_context(ACTORS['a']):
        return intake.submit(event(**changes))


def test_delivery_replay_and_changed_envelope(inputs):
    from task_server.services import requirement_intake as intake, ui_batch_dispatch
    from task_server.services.ui_execution_store import UiExecutionStore
    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = list(pool.map(lambda _: post(), range(2)))
    assert a['status'] == b['status'] == 'submitted'
    batch_id = a['batches'][0]['ui_batch_id']
    assert b['batches'][0]['ui_batch_id'] == batch_id
    assert post(event_id='E-2')['batches'][0]['ui_batch_id'] == batch_id
    with pytest.raises(intake.Conflict):
        post(title='变更内容')
    with pytest.raises(intake.Conflict):
        post(requirement_id='PRD-6')
    batch = ui_batch_dispatch.dispatch_ui_batch(batch_id)
    assert batch['case_attempts'][0]['job_id']
    assert intake.requirement_for_batch(UiExecutionStore().get_batch(batch_id))['requirement_id'] == 'PRD-5'


def test_unmapped_and_unready_do_not_dispatch_and_can_resume(inputs):
    from task_server.services import requirement_intake as intake
    from task_server.services.ui_execution_service import list_ui_batches
    path, (_runners, sonic) = inputs
    binding = path.read_text()
    path.write_text('[]')
    assert post()['status'] == 'awaiting_mapping'
    path.write_text(binding)
    sonic['iphone']['usage_status'] = 'debugging'
    blocked = post()
    assert blocked['status'] == 'blocked'
    assert blocked['blockers'] == [{'platform':'ios','code':'device_busy'}]
    with actor_context(ACTORS['a']):
        assert list_ui_batches(ACTORS['a'])['items'] == []
    sonic['iphone']['usage_status'] = 'idle'
    assert post()['status'] == 'submitted'
    with actor_context(ACTORS['b']):
        assert intake.read('business', 'E-1') is None


def test_invalid_body_and_revoked_permissions(inputs, monkeypatch):
    from task_server import identity
    from task_server.services import requirement_intake as intake
    with pytest.raises(ValueError):
        post(target=TARGET)
    with pytest.raises(PermissionError):
        post(app_id='unauthorized')
    profile = identity.get_access_profile('a')
    monkeypatch.setattr(identity, 'get_access_profile', lambda _: {**profile, 'permissions':['ui.view']})
    with pytest.raises(PermissionError):
        post()
    with actor_context(ACTORS['a']):
        assert intake.read('business','E-1') is None


def test_native_feishu_pull_paginates_with_project_field_and_uses_approved_scenario(inputs):
    from task_server.services import feishu_requirement_sync as sync
    path, _ = inputs
    mapping = json.loads(path.read_text())[0]
    del mapping['requirement_id'], mapping['revision']
    mapping.update(source='feishu-project:P', scenario='navigation')
    path.write_text(json.dumps([mapping]))
    cfg = dict(project_key='P', work_item_type_key='story', app_id=APP, username='a', user_id='u-a',
               acceptance_field='field_ac', scenario_field='field_feature',
               search_group={'conjunction':'AND','search_params':[{'param_key':'field_ready','operator':'=','value':True}]})
    item = dict(project_key='P', id=812, name='导航可见', work_item_type_key='story',
                updated_at=10, fields=[dict(field_key='field_ac', field_value='打开我的后显示个人页'),
                                      dict(field_key='field_feature',field_value='navigation')])
    calls = []
    def project_api(path, body):
        calls.append((path, body))
        rows = [item] if body['page_num'] == 1 else []
        return {'data':rows, 'err_code':0}
    first = sync.sync(cfg, post=project_api, page_size=1)
    item['updated_at'] = 20
    second = sync.sync(cfg, post=project_api, page_size=1)
    assert first['counts'] == second['counts'] == {'submitted':1}
    assert calls[0][0] == '/open_api/P/work_item/story/search/params'
    assert [x['page_num'] for _,x in calls] == [1,2,1,2]
    with actor_context(ACTORS['a']):
        from task_server.services.ui_execution_service import list_ui_batches
        rows = list_ui_batches(ACTORS['a'])['items']
        assert len(rows) == 1


def test_native_poll_fails_closed_on_actor_mismatch_and_wrong_item_scope(inputs):
    from task_server.services import feishu_requirement_sync as sync
    config = dict(project_key='P', work_item_type_key='story', app_id=APP, username='a', user_id='wrong',
                  acceptance_field='field_ac', search_group={'conjunction':'AND','search_params':[{'param_key':'x'}]})
    calls = []
    def post(path, body):
        calls.append(path)
        return {'data':[{'id':7, 'project_key':'other', 'work_item_type_key':'story'}]}
    with pytest.raises(PermissionError):
        sync.sync(config, post=post)
    assert not calls
    config['user_id'] = 'u-a'
    result = sync.sync(config, post=post)
    assert result['errors'] == [{'item_id':'7','code':'item_scope_mismatch'}]


def test_recent_listing_shares_only_app_scoped_integration_intakes(inputs, monkeypatch):
    from task_server import identity
    from task_server.services import requirement_intake as intake
    with actor_context(ACTORS['a']):
        intake.submit(event(), origin='integration')
        intake.submit(event(event_id='private', requirement_id='PRD-6'))
    with actor_context(ACTORS['b']):
        receipts = intake.recent(10)
        assert len(receipts) == 1 and receipts[0]['requirement_id'] == 'PRD-5'
        assert 'ui_batch_id' not in json.dumps(receipts)
    with actor_context(ACTORS['admin']):
        receipts = intake.recent(10)
        assert len(receipts) == 1
        assert receipts[0]['batches'][0]['ui_batch_id'].startswith('uib_')
    profile = identity.get_access_profile('b')
    monkeypatch.setattr(identity, 'get_access_profile', lambda _: {**profile, 'scope':{'ui_apps':[]}})
    with actor_context(ACTORS['b']):
        assert intake.recent(10) == []


def test_intake_routes_with_real_sessions_and_app_scope(identity_db, tmp_path, monkeypatch):
    import threading
    from task_server import app, auth
    from test_platform_mcp_http import request

    _, store = identity_db
    monkeypatch.setenv('TASK_OPERATION_DB', str(tmp_path / 'audit.sqlite3'))
    monkeypatch.delenv('MIDSCENE_REQUIREMENT_BINDINGS', raising=False)
    store.create_role('admin',{'id':'intake-executor','name':'需求测试','permissions':['ui.view','ui.execute']})
    store.create_role('admin',{'id':'intake-reader','name':'需求只读','permissions':['ui.view']})
    for username, role in [('executor','intake-executor'), ('reader','intake-reader')]:
        create_member(store, username=username, role_ids=[role], scope={'ui_apps':[APP]})
        activate(store,username)
    executor, reader = (auth.create_session_token(name) for name in ('executor','reader'))
    server = app.ThreadingHTTPServer(('127.0.0.1',0), app.TaskHTTPHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = '/api/platform/requirement-design/intake'
    try:
        assert request(server, 'POST', url, payload=event())[0] == 401
        assert request(server, 'POST', url, reader, event())[0] == 403
        assert request(server, 'POST', url, executor, event(app_id='other.app'))[0] == 403
        status, data, _ = request(server, 'POST', url, executor, event())
        assert status == 200 and data['intake']['status'] == 'awaiting_mapping'
        assert request(server, 'GET', url+'?source=business&event_id=E-1', reader)[0] == 404
        assert request(server, 'GET', url+'/recent?limit=10', executor)[1]['items'][0]['title'] == '导航可见'
        assert request(server, 'POST', url, executor, event(title='changed'))[0] == 409
        body = {'source':'business', 'event_id':'E-1', 'app_id':APP}
        assert request(server, 'POST', url+'/design-source', executor, body)[0] == 403
        admin = auth.create_session_token('admin')
        assert request(server, 'POST', url+'/design-source', admin, body)[0] == 404
        assert request(server, 'POST', url, admin, event())[0] == 200
        status, data, _ = request(server, 'POST', url+'/design-source', admin, body)
        assert status == 200 and len(data['source_id']) == 64
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_only_project_sync_can_assert_project_provenance(inputs):
    from task_server.services import requirement_intake as intake
    with actor_context(ACTORS['a']):
        with pytest.raises(ValueError):
            intake.submit(event(source='feishu-project:P'))
        assert intake.read('feishu-project:P','E-1') is None


def test_stale_pins_and_unreviewed_execution_are_blocked_before_dispatch(inputs):
    from task_server.services import requirement_intake as intake
    from task_server.services.ui_execution_service import list_ui_batches
    path, _ = inputs
    bindings = json.loads(path.read_text())
    run = bindings[0]['runs'][0]
    run['execution_policy'] = 'print'
    path.write_text(json.dumps(bindings))
    with pytest.raises(intake.MappingUnavailable):
        post()
    run.update(execution_policy='reviewed_non_printing',
               cases=[dict(case_id='case-' + str(i), revision=REVISION) for i in range(4)])
    path.write_text(json.dumps(bindings))
    with pytest.raises(intake.MappingUnavailable):
        post()
    run['cases'] = [dict(case_id='case-1', revision='0' * 64)]
    path.write_text(json.dumps(bindings))
    assert post()['blockers'] == [{'platform':'ios', 'code':'case_revision_changed'}]
    with actor_context(ACTORS['a']):
        assert list_ui_batches(ACTORS['a'])['items'] == []


def test_unmapped_receipt_becomes_private_text_only_source_without_dispatch(inputs, tmp_path):
    from task_server.services import requirement_design_model, requirement_intake as intake
    path, _ = inputs
    path.write_text('[]')
    with actor_context(ACTORS['a']):
        intake.submit(event(), origin='integration')
        with pytest.raises(PermissionError):
            intake.design_source('business', 'E-1', APP)
        intake.submit(event(requirement_id='PRD-6',event_id='private'))
    with actor_context(ACTORS['admin']):
        source_id = intake.design_source('business','E-1', APP)
        assert source_id == intake.design_source('business','E-1', APP)
        assert intake.design_source('business','private', APP) is None
        with requirement_design._db() as db:
            source_owner, source = requirement_design._source(db, 'u-admin', source_id)
            assert source_owner == 'u-admin'
            assert source['title'] == '导航可见' and source['clauses'] == [{'id':'P1','text':'打开我的后显示个人页'}]
            assert source['prototypes'] == []
            assert db.execute('SELECT count(*) FROM designs').fetchone()[0] == 0
        content, manifest, _ = requirement_design_model._input(source,tmp_path)
        assert len(content) == 1 and set(manifest) == {'P1'}
