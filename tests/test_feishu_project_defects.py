import copy
import json

import pytest

from task_server.services import feishu_project_defects as tickets


ACTOR = {'user_id': 'owner'}
BATCH = 'uib_' + 'a' * 32
REV = 'b' * 64
STEP = 'step_' + 'c' * 24
DIAGNOSTIC = 'AssertionError: order.total expected 10 actual 0'


@pytest.fixture
def flow(tmp_path, monkeypatch):
    monkeypatch.setenv('TASK_UI_EXECUTION_DB', str(tmp_path / 'ui.sqlite'))
    from task_server.services import failure_analysis_tasks
    monkeypatch.setattr(failure_analysis_tasks,'latest',lambda *_: {'id':'analysis','status':'completed'})
    config = tmp_path / 'tickets.json'
    config.write_text(json.dumps({'apps': {'app': {'project_key': 'qa', 'work_item_type_key': 'issue',
        'evidence_field': 'field_evidence', 'rules': [{'case_id': 'order', 'case_revision': REV, 'step_id': STEP,
            'diagnostic': DIAGNOSTIC, 'title': '订单合计错误', 'expected': '合计 10 元'}]}}}))
    monkeypatch.setenv('MIDSCENE_FEISHU_DEFECT_CONFIG', str(config))
    for key in ('PLUGIN_ID', 'PLUGIN_SECRET', 'USER_KEY'):
        monkeypatch.setenv('FEISHU_PROJECT_' + key, 'fixture-' + key)
    attempt = dict(attempt_id='d'*32, case_id='order', case_revision=REV, job_id=BATCH+'_1_1',
                   platform='android', device_id='device', snapshot_id='snapshot', attempt_no=1, status='failed',
                   stop_confirmed=1, step_progress_known=True, steps=[dict(step_id=STEP, status='failed')],
                   result={'stderr': DIAGNOSTIC + '\nBearer SECRET-never-exported'}, target_resolution={})
    batch = dict(ui_batch_id=BATCH, owner_user_id='owner', execution_settled=True, app_id='app', case_attempts=[attempt])
    monkeypatch.setattr(tickets, 'get_ui_batch', lambda *_: batch)
    monkeypatch.setattr(tickets, 'requirement_for_batch', lambda b: dict(source='feishu', requirement_id='71',revision='v2'))
    monkeypatch.setattr(tickets, 'require_ui_actor', lambda *a, **kw: ACTOR)
    calls = []
    def post(path, payload, headers=None):
        calls.append((path,payload))
        if path.endswith('/plugin_token'):
            return {'error': {'code': 0}, 'data': {'token':'fixture-token'}}
        return {'err_code':0, 'data':871}
    monkeypatch.setattr(tickets.project, '_post', post)
    return batch, attempt, calls


def test_creates_actual_work_item_and_replays_once(flow):
    batch, attempt, calls = flow
    once = tickets.process_ui_batch(ACTOR, BATCH)
    assert once['attempts'][0]['issue_id'] == 871
    assert once['attempts'][0]['status'] == 'submitted'
    assert tickets.process_ui_batch(ACTOR, BATCH)['attempts'][0]['issue_id'] == 871
    creates = [payload for path, payload in calls if path.endswith('/work_item/create')]
    assert len(creates) == 1
    content = creates[0]['field_value_pairs'][0]['field_value']
    assert all(x in content for x in [BATCH, REV, STEP, DIAGNOSTIC, '合计 10 元'])
    assert 'SECRET-never-exported' not in content


@pytest.mark.parametrize('change', [dict(status='interrupted'), dict(step_progress_known=False),
    dict(steps=[dict(step_id=STEP,status='passed')]), dict(result={'stderr':'model service timed out'}),
    dict(result={'stderr':'yaml source: '+DIAGNOSTIC}), dict(result={'stderr':DIAGNOSTIC,'stdout':'device offline'}), dict(stop_confirmed=0)])
def test_environment_unproven_and_partial_evidence_never_send(flow,change):
    _,attempt,calls = flow
    attempt.update(change)
    tickets.process_ui_batch(ACTOR, BATCH)
    assert not calls


def test_prior_failed_attempt_superseded_by_pass(flow):
    batch,attempt,calls = flow
    batch['case_attempts'].append(dict(copy.deepcopy(attempt), status='passed', attempt_no=2))
    assert not tickets.process_ui_batch(ACTOR, BATCH)['attempts']
    assert not calls


def test_missing_credentials_preserves_draft_then_retries(flow, monkeypatch):
    _,_,calls = flow
    monkeypatch.delenv('FEISHU_PROJECT_PLUGIN_SECRET')
    missing = tickets.process_ui_batch(ACTOR,BATCH)['attempts'][0]
    assert missing['status'] == 'configuration_required' and missing['tracking_key']
    assert not calls
    monkeypatch.setenv('FEISHU_PROJECT_PLUGIN_SECRET','fixture')
    assert tickets.process_ui_batch(ACTOR,BATCH)['attempts'][0]['issue_id'] == 871


def test_missing_issue_id_never_reposts_and_recovers_by_matching_receipt(flow,monkeypatch):
    _,_,calls = flow
    title = []
    def post(path,payload,headers=None):
        calls.append((path,payload))
        if path.endswith('/plugin_token'):
            return {'error':{'code':0},'data':{'token':'temporary'}}
        if path.endswith('/create'):
            title.append(payload['name'])
            return {'err_code':0,'data':None}
        assert path.endswith('/filter')
        return {'err_code':0,'data':[{'name':title[0], 'work_item_type_key':'issue', 'id':34}]}
    monkeypatch.setattr(tickets.project,'_post',post)
    one = tickets.process_ui_batch(ACTOR,BATCH)['attempts'][0]
    assert one['status'] == 'receipt_unknown' and not one.get('issue_id')
    assert tickets.process_ui_batch(ACTOR,BATCH)['attempts'][0]['issue_id'] == 34
    assert len(title) == 1


def test_unmapped_failure_starts_readonly_investigation(flow, monkeypatch):
    from task_server.services import failure_analysis_tasks
    _, attempt, calls = flow
    attempt['result'] = {'stderr':'waiting for model service timed out'}
    requests = []
    monkeypatch.setattr(failure_analysis_tasks,'latest',lambda *args:None)
    monkeypatch.setattr(failure_analysis_tasks,'start',lambda actor, request: requests.append(request) or {'id':'task','status':'queued'})
    result = tickets.process_ui_batch(ACTOR,BATCH)
    assert result['investigation'] == {'id':'task','status':'queued'}
    assert requests[0]['source']=='ui' and requests[0]['run_id']==BATCH
    assert result['attempts'][0]['status']=='needs_evidence' and not calls


# Use the existing intake and authenticated Runner fixtures to cover the production handoff.
from test_requirement_intake import inputs, post, dispatch_env, environment


def test_terminal_intake_batch_reconciles_authenticated_runner_evidence(inputs, tmp_path, monkeypatch):
    from task_server import identity
    from task_server.services import failure_analysis_tasks, job_service, ui_batch_dispatch as dispatch
    from task_server.services.ui_execution_store import UiExecutionStore
    from test_ui_execution_service import APP
    real_profile = identity.get_access_profile
    monkeypatch.setattr(identity,'get_access_profile',lambda name: {**real_profile(name), 'permissions':['ui.view','ui.execute','platform.notify']})
    monkeypatch.setattr(failure_analysis_tasks,'latest',lambda *_: {'id':'investigation','status':'completed'})
    intake = post()
    batch_id = intake['batches'][0]['ui_batch_id']
    attempt = dispatch.dispatch_ui_batch(batch_id)['case_attempts'][0]
    delivery = dispatch.claim_ui_job(job_service.get_job(attempt['job_id']))
    import yaml
    compiled = yaml.safe_load(delivery['yaml_content'])['tasks'][0]['flow']
    end = len(compiled)
    last = next(key for key, rows in delivery['ui_step_map'].items() if {'task_index':1, 'flow_index':end} in rows)
    config = tmp_path/'issues.json'
    config.write_text(json.dumps({'apps':{APP:{'project_key':'qa','work_item_type_key':'issue','evidence_field':'field_evidence',
          'rules':[{'case_id':'case-1','case_revision':attempt['case_revision'],'step_id':last,'title':'验收失败','expected':'个人页','diagnostic':DIAGNOSTIC}]}}}))
    monkeypatch.setenv('MIDSCENE_FEISHU_DEFECT_CONFIG',str(config))
    for var in tickets.project.CREDENTIALS:
        monkeypatch.setenv(var,'fixture')
    posted = []
    def upstream(path,body,headers=None):
        if path.endswith('/plugin_token'):
            return {'error':{'code':0},'data':{'token':'fixture'}}
        posted.append(body)
        return {'err_code':0,'data':733}
    monkeypatch.setattr(tickets.project,'_post',upstream)
    event = dict(type='result',status='failed',execution_stopped=True,runner_id=attempt['runner_id'],device_id=attempt['device_id'],
                 ui_claim_token=delivery['ui_claim_token'], stderr=DIAGNOSTIC, execution_progress={'version':1, 'tasks':[
                     dict(status='failed',current_step=end-1,total_steps=end,steps=[])]})
    assert dispatch.record_ui_attempt_event(attempt['job_id'],1,event)['status']=='failed'
    tickets._scan_one()
    tickets._scan_one()
    receipt = tickets.batch_review(UiExecutionStore().get_batch(batch_id))
    assert receipt['attempts'][0]['issue_id']==733 and len(posted)==1
    assert receipt['investigation']['id']=='investigation'


def test_destination_specific_required_fields_and_template(flow,monkeypatch):
    from pathlib import Path
    import os
    config = Path(os.environ['MIDSCENE_FEISHU_DEFECT_CONFIG'])
    data = json.loads(config.read_text())
    target = data['apps']['app']
    target.update(template_id=72, field_value_pairs=[{'field_key':'priority','field_value':'option_id'}])
    config.write_text(json.dumps(data))
    _,_,calls = flow
    tickets.process_ui_batch(ACTOR,BATCH)
    body = next(payload for path,payload in calls if path.endswith('/create'))
    assert body['template_id']==72
    assert {'field_key':'priority','field_value':'option_id'} in body['field_value_pairs']


def test_explicit_project_rejection_can_retry(flow, monkeypatch):
    requests = []
    def post(path,payload,headers=None):
        if path.endswith('/plugin_token'):
            return {'error':{'code':0},'data':{'token':'fixture'}}
        requests.append(path)
        return {'err_code':30301,'data':73} if len(requests)==1 else {'err_code':0,'data':73}
    monkeypatch.setattr(tickets.project,'_post',post)
    rejected = tickets.process_ui_batch(ACTOR,BATCH)['attempts'][0]
    assert rejected['status']=='rejected' and not rejected['issue_id']
    assert tickets.process_ui_batch(ACTOR,BATCH)['attempts'][0]['issue_id']==73
    assert len(requests)==2


def test_changed_case_revision_and_generic_network_failure_never_file(flow):
    _, attempt, calls = flow
    attempt['case_revision'] = '1'*64
    assert tickets.process_ui_batch(ACTOR,BATCH)['attempts'][0]['status']=='needs_evidence'
    assert not calls


def test_two_workers_and_lost_network_reply_do_not_duplicate_issue(flow, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    request_started, allow_timeout = Event(), Event()
    created = []
    def post(path, payload, headers=None):
        if path.endswith('/plugin_token'):
            return {'error':{'code':0}, 'data':{'token':'fixture'}}
        if path.endswith('/create'):
            created.append(payload['name'])
            request_started.set()
            assert allow_timeout.wait(3)
            raise tickets.project.ProjectUnavailable('feishu_response_unavailable')
        return {'err_code':0, 'data':[]}
    monkeypatch.setattr(tickets.project,'_post',post)
    with ThreadPoolExecutor(max_workers=2) as workers:
        first = workers.submit(tickets.process_ui_batch, ACTOR, BATCH)
        assert request_started.wait(3)
        second = workers.submit(tickets.process_ui_batch, ACTOR, BATCH)
        try:
            assert second.result(3)['attempts'][0]['status']=='receipt_unknown'
        finally:
            allow_timeout.set()
        assert first.result(3)['attempts'][0]['status']=='receipt_unknown'
    assert tickets.process_ui_batch(ACTOR,BATCH)['attempts'][0]['status']=='receipt_unknown'
    assert len(created)==1


@pytest.mark.parametrize('owner, expected', [('developer-qa','submitted'), ([], 'owner_unresolved')])
def test_route_bug_by_current_requirement_developer(flow,monkeypatch,owner,expected):
    from pathlib import Path
    import os
    cfg = Path(os.environ['MIDSCENE_FEISHU_DEFECT_CONFIG'])
    data = json.loads(cfg.read_text())
    data['apps']['app']['developer_assignment'] = dict(requirement_type='story', developer_field='dev',
                                                       assignee_field='owner')
    cfg.write_text(json.dumps(data))
    monkeypatch.setattr(tickets,'requirement_for_batch',lambda _:dict(source='feishu-project:qa',requirement_id='71',revision='v2'))
    route = []
    def api(path,body,headers=None):
        if path.endswith('/plugin_token'):
            return {'error':{'code':0},'data':{'token':'fixture'}}
        if path.endswith('/query'):
            assert body['work_item_ids']==[71]
            return {'err_code':0,'data':[dict(id=71,project_key='qa',work_item_type_key='story',
                     fields=[dict(field_key='dev',field_value=owner)])]}
        route.append(body)
        return {'err_code':0,'data':871}
    monkeypatch.setattr(tickets.project,'_post',api)
    assert tickets.process_ui_batch(ACTOR,BATCH)['attempts'][0]['status']==expected
    if owner:
        assert {'field_key':'owner','field_value':owner} in route[0]['field_value_pairs']
    else:
        assert not route


@pytest.mark.parametrize('entries,expected', [
    ([{'role':'qa','owners':['tester']},{'role':'mobile','owners':['developer']}], 'submitted'),
    ([{'role':'mobile','owners':['first','second']}], 'owner_unresolved'),
    ([{'role':'mobile','owners':['first']},{'role':'mobile','owners':['second']}], 'owner_unresolved'),
    ([{'role':'qa','owners':['tester']}], 'owner_unresolved'),
])
def test_owner_from_named_requirement_role(flow, monkeypatch, entries, expected):
    import os
    from pathlib import Path
    path = Path(os.environ['MIDSCENE_FEISHU_DEFECT_CONFIG'])
    config = json.loads(path.read_text())
    config['apps']['app']['developer_assignment'] = dict(requirement_type='story',developer_field='role_owners',
                                                         developer_role='mobile',assignee_field='owner')
    path.write_text(json.dumps(config))
    monkeypatch.setattr(tickets,'requirement_for_batch',lambda _:dict(source='feishu-project:qa',requirement_id='71',revision='v2'))
    writes = []
    def api(path, body,headers=None):
        if path.endswith('/plugin_token'):
            return {'error':{'code':0}, 'data':{'token':'fixture'}}
        if path.endswith('/query'):
            assert body == {'work_item_ids':[71],'fields':['role_owners']}
            return {'err_code':0,'data':[dict(id=71,project_key='qa',work_item_type_key='story',
                                             fields=[dict(field_key='role_owners',field_value=entries)])]}
        writes.append(body)
        return {'err_code':0,'data':871}
    monkeypatch.setattr(tickets.project,'_post',api)
    assert tickets.process_ui_batch(ACTOR,BATCH)['attempts'][0]['status'] == expected
    if expected == 'submitted':
        assert {'field_key':'owner','field_value':'developer'} in writes[0]['field_value_pairs']
    else:
        assert not writes


def test_routes_single_owner_to_multi_user_and_links_exact_story(flow,monkeypatch):
    import os
    from pathlib import Path
    path=Path(os.environ['MIDSCENE_FEISHU_DEFECT_CONFIG'])
    config=json.loads(path.read_text())
    target=config['apps']['app']
    target.update(requirement_link_field='_field_linked_story',developer_assignment=dict(
        requirement_type='story',developer_field='role_owners',developer_role='Server',
        assignee_field='issue_operator',assignee_field_type='multi_user'))
    path.write_text(json.dumps(config))
    req=dict(source='feishu-project:qa',requirement_id='7082224050',revision='v2')
    monkeypatch.setattr(tickets,'requirement_for_batch',lambda _:req)
    bodies=[]
    def api(path,body,headers=None):
        if path.endswith('/plugin_token'):
            return {'error':{'code':0},'data':{'token':'ok'}}
        if path.endswith('/query'):
            return {'err_code':0,'data':[dict(project_key='qa',work_item_type_key='story',id=7082224050,
                fields=[dict(field_key='role_owners',field_value=[dict(role='Server',owners=['dev'])])])]}
        bodies.append(body)
        return {'err_code':0,'data':91}
    monkeypatch.setattr(tickets.project,'_post',api)
    assert tickets.process_ui_batch(ACTOR,BATCH)['attempts'][0]['issue_id']==91
    assert {'field_key':'issue_operator','field_value':['dev']} in bodies[0]['field_value_pairs']
    assert {'field_key':'_field_linked_story','field_value':7082224050} in bodies[0]['field_value_pairs']
    for collision in ('issue_operator','field_evidence'):
        target['requirement_link_field']=collision
        path.write_text(json.dumps(config))
        with pytest.raises(ValueError,match='config_invalid'):
            tickets._config('app')


def test_configured_destination_without_accepted_failure_signatures_keeps_evidence_pending(flow, tmp_path):
    _, _, calls = flow
    path = tmp_path / 'tickets.json'
    config = json.loads(path.read_text())
    config['apps']['app']['rules'] = []
    path.write_text(json.dumps(config))
    review = tickets.process_ui_batch(ACTOR, BATCH)
    assert review['attempts'][0]['status'] == 'needs_evidence'
    assert not calls


def test_unknown_create_records_transport_error_without_reposting(flow, monkeypatch):
    _, _, calls = flow
    normal = tickets.project._post

    def fail_create(path, payload, headers=None):
        if path.endswith('/create'):
            calls.append((path, payload))
            raise tickets.project.ProjectUnavailable('feishu_http_504')
        if path.endswith('/filter'):
            return {'err_code': 0, 'data': []}
        return normal(path, payload, headers)

    monkeypatch.setattr(tickets.project, '_post', fail_create)
    for _ in range(2):
        receipt = tickets.process_ui_batch(ACTOR, BATCH)['attempts'][0]
        assert receipt['status'] == 'receipt_unknown'
        assert receipt['detail'] == 'feishu_http_504'
    assert sum(path.endswith('/create') for path, _ in calls) == 1


@pytest.mark.parametrize('status,code,error,detail', [
    (403, 12345, tickets.project.ProjectUnavailable, 'feishu_http_403_business_12345'),
    (400, 20038, tickets.project.ProjectRejected, 'feishu_business_error_20038_field_discovery_version'),
    (502, 20038, tickets.project.ProjectUnavailable, 'feishu_http_502_business_20038'),
])
def test_http_error_preserves_only_feishu_business_code(monkeypatch, status, code, error, detail):
    class Reply:
        headers = {}

        def __init__(self):
            self.status = status

        def read(self, size):
            return json.dumps({'err_code': code, 'err_msg': 'private upstream message',
                               'err': {'msg': '发现版本(discovery_version) required'}}).encode()

    class Connection:
        def request(self, *args):
            pass

        def getresponse(self):
            return Reply()

        def close(self):
            pass

    monkeypatch.setattr(tickets.project.http.client, 'HTTPSConnection', lambda *a, **kw: Connection())
    with pytest.raises(error, match='^' + detail + '$'):
        tickets.project._post('/open_api/project/work_item/create', {})
