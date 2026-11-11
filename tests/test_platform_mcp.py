import json
import os
import tempfile
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from task_server import requirement_design_http
from task_server.access_control import MainAccess, AccessDenied
from task_server.response import request_body_limit


class Handler:
    def __init__(self, body, token='token'):
        self.headers = {'Authorization': 'Bearer ' + token}
        self.body = body

    def _body(self):
        return self.body

    def _json(self, value, code=200):
        self.response = value, code


class ToolApiTests(unittest.TestCase):
    def setUp(self):
        self.routes = {}
        def register(method):
            def route(path):
                def wrap(fn):
                    self.routes[method, path] = fn
                    return fn
                return wrap
            return route
        requirement_design_http.register_routes(register('GET'), register('POST'))
        self.assertIn(('POST', '/api/platform/mcp/call'), self.routes)
        from task_server.services import platform_mcp as tools
        self.tools = tools
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.bind('task_server.identity.default_db_path', return_value=self.directory / 'identity.sqlite3')
        self.actor = self.bind('task_server.platform_mcp_http.current_actor', return_value={'kind':'user','user_id':'owner'})
        self.session = self.bind('task_server.auth.verify_session_token', return_value={'user_id':'owner','user':'user'})
        self.profile = {'user_id':'owner','status':'active','permissions':['ui.view'],'scope':{'ui_apps':['one']}}
        self.access = self.bind('task_server.identity.get_access_profile', return_value=self.profile)
        self.launch = self.bind('task_server.services.platform_mcp._launch')
        self.claim = self.bind('task_server.services.platform_mcp.acquire_heavy_workload_slot', return_value=True)
        self.release = self.bind('task_server.services.platform_mcp.release_heavy_workload_slot')
        self.runner = self.bind('task_server.services.week5_web_service.run', return_value={
            'scenario':'ui_catalog_count','version':1,'status':'passed','error_kind':None,'total':7,
            'id':'a' * 32,'artifacts':['screenshot','trace']})
        def browser(*args, **kwargs):
            kwargs['control'].worker_exit_code = 0
            kwargs['control'].node_version = '22.0.0'
            return self.runner.return_value
        self.runner.side_effect = browser
        self.args = {'suite_id':'published-catalog','environment':'platform','request_id':str(uuid.uuid4())}

    def bind(self, path, **kwargs):
        obj = patch(path, **kwargs)
        self.addCleanup(obj.stop)
        return obj.start()

    def request(self, method, action, body=None, qs=None):
        handler = Handler(body)
        self.routes[method, '/api/platform/mcp/' + action](handler, qs or {})
        return handler.response

    def call(self, name, args):
        return self.request('POST','call',{'name':name,'arguments':args})

    def start(self):
        response, code = self.call('run_suite', self.args)
        self.assertEqual((response['ok'],code),(True,200))
        return response['result']

    def work(self):
        self.tools._worker(*self.launch.call_args.args)

    def test_discovery_and_scoped_policy(self):
        response, code = self.call('list_suites', {})
        self.assertEqual(code,200)
        self.assertEqual([s['suite_id'] for s in response['result']['suites']], ['published-catalog','catalog-failure-drill'])
        access = MainAccess(self.profile, catalog=[])
        for method,path in self.routes:
            if '/mcp/' in path:
                if path.endswith('/diagnose'):
                    with self.assertRaises(AccessDenied):
                        access.check(method,path)
                    MainAccess(dict(self.profile,permissions=['ui.view','ui.execute']),catalog=[]).check(method,path)
                else:
                    access.check(method,path)
                with self.assertRaises(AccessDenied):
                    MainAccess({},catalog=[]).check(method,path)
        self.assertEqual(request_body_limit('/api/platform/mcp/call'),1024)
        row = self.start()
        self.assertEqual(access.filter('/api/platform/mcp/runs', {'runs':[row]}),{'runs':[row]})

    def test_rejects_unknown_arguments_and_tool_before_dispatch(self):
        for name,args in [('get_run',{'run_id':'..'}),('run_suite',dict(self.args, url='x')),
                          ('run_suite',dict(self.args, suite_id='other')),('list_suites',{'limit':1}),
                          ('run_suite',dict(self.args, environment='prod')),('run_suite',dict(self.args, request_id=1)),
                          ('shell',{}),([],{}),('run_suite',[])]:
            with self.subTest(name=name,args=args):
                self.assertEqual(self.call(name,args)[1],400)
        self.assertEqual(self.request('POST','call',{'name':'list_suites','arguments':{}}, {'url':'x'})[1],400)
        self.launch.assert_not_called()

    def test_personal_bearer_required_and_disabled_account_rejected(self):
        self.session.return_value=None
        self.assertEqual(self.call('list_suites',{})[1],403)
        self.session.return_value={'user_id':'other','user':'user'}
        self.assertEqual(self.call('run_suite',self.args)[1],403)
        self.session.return_value={'user_id':'owner','user':'user'}
        self.profile['status']='disabled'
        self.assertEqual(self.request('GET','runs')[1],403)
        self.launch.assert_not_called()

    def test_disabled_execution_still_allows_reads_and_replay_of_known_request(self):
        row = self.start()
        with patch.dict(os.environ, TASK_MCP_RUN_ENABLED='0'):
            self.assertEqual(self.start()['run_id'],row['run_id'])
            reply, code = self.call('run_suite',dict(self.args,request_id=str(uuid.uuid4())))
            self.assertEqual((code,reply['code']),(503,'execution_disabled'))
            self.assertEqual(self.call('list_suites',{})[1],200)
        self.launch.assert_called_once()

    def test_concurrent_duplicate_is_durable_and_only_dispatches_once(self):
        with ThreadPoolExecutor(max_workers=4) as executor:
            rows = list(executor.map(lambda _: self.start(), range(8)))
        self.assertEqual(len({r['run_id'] for r in rows}),1)
        self.launch.assert_called_once()
        self.work()
        row = self.start()
        self.assertEqual((row['status'],row['total'],row['runner_id']),('passed',7,'a'*32))
        self.assertEqual(row['evidence']['trace'],'/api/ui-executions/web-check/artifact?id='+'a'*32+'&item=trace')
        self.assertEqual(self.runner.call_count,1)
        self.assertEqual(self.runner.call_args.kwargs['scenario'],'ui_catalog_count')
        self.assertLessEqual(row['started_at'],row['finished_at'])
        self.assertNotIn(b'"token"', (self.directory/'platform-mcp.sqlite3').read_bytes())

    def test_cancel_queued_job_never_starts_child(self):
        row = self.start()
        result, status = self.call('cancel_run', {'run_id':row['run_id']})
        self.assertEqual(status, 200)
        self.assertEqual(result['result']['status'], 'cancelled')
        self.work()
        self.runner.assert_not_called()
        case, = self.start()['cases']
        self.assertEqual((case['status'],case['worker_exit_code'],case['duration_ms']), ('cancelled',None,0))

    def test_active_cancel_waits_for_confirmed_stop_and_checks_ownership(self):
        from threading import Event, Thread
        entered, proceed = Event(), Event()
        def execute(suite, actor, cancel_requested):
            entered.set()
            self.assertTrue(proceed.wait(3))
            self.assertTrue(cancel_requested())
            return dict(runner='node',runner_version='22.0.0',status='cancelled',error_kind='cancelled',
                        exit_code=None,worker_exit_code=-15,evidence={})
        row = self.start()
        with patch.object(self.tools, '_execute', side_effect=execute):
            worker = Thread(target=self.work)
            worker.start()
            try:
                self.assertTrue(entered.wait(2))
                self.profile['scope']={'ui_apps':['two']}
                self.assertEqual(self.call('cancel_run',{'run_id':row['run_id']})[1], 404)
                self.profile['scope']={'ui_apps':['one']}
                result, code = self.call('cancel_run',{'run_id':row['run_id']})
                self.assertEqual((code,result['result']['status']), (200,'cancelling'))
                self.assertIsNone(result['result']['junit_url'])
            finally:
                proceed.set()
                worker.join(3)
        row = self.start()
        self.assertEqual((row['status'],row['cases'][0]['worker_exit_code']), ('cancelled',-15))

    def test_feedback_contract_conflict_scope_and_history(self):
        with patch.object(self.tools, '_feedback_authorized', return_value=True):
            run = self.start()
            feedback_args = dict(self.args, suite_id='feedback-categories')
            self.assertEqual(self.call('run_suite',feedback_args)[1],409)
            self.work()
            feedback_args['request_id'] = str(uuid.uuid4())
            result, status = self.call('run_suite',feedback_args)
            self.assertEqual(status,200)
            rid = result['result']['run_id']
            native = dict(runner='python', runner_version='3.11.7', status='passed', error_kind=None, exit_code=0,
                          worker_exit_code=0,evidence={'http_status':200,'actual_code':0,'expected_code':0,'data_is_list':True})
            with patch.object(self.tools,'_execute',return_value=native):
                self.work()
            row = self.call('get_run',{'run_id':rid})[0]['result']
            case, = row['cases']
            self.assertEqual((case['case_id'],case['runner'],case['version'],case['attempt']),('feedback_categories','python','3.11.7',1))
            self.assertEqual((case['evidence'],case['run_id'],case['exit_code']), (native['evidence'],rid,0))
            self.assertEqual(row['junit_url'],'/api/platform/mcp/junit?run_id='+rid)
        self.assertEqual(self.call('get_run',{'run_id':rid})[1],404)
        self.assertEqual([r['run_id'] for r in self.request('GET','runs')[0]['runs']], [run['run_id']])

    def test_preupgrade_active_run_with_expired_deadline_recovers(self):
        row = self.start()
        with self.tools._db() as db:
            db.execute('DELETE FROM run_cases WHERE run_id=?',(row['run_id'],))
            db.execute("UPDATE runs SET status='running',deadline=0,started_at=1 WHERE run_id=?",(row['run_id'],))
        self.assertEqual(self.start()['status'], 'timed_out')

    def test_legacy_row_keeps_evidence_without_fabricating_worker_exit_or_version(self):
        row = self.start()
        with self.tools._db() as db:
            db.execute("UPDATE runs SET status='passed',runner_id=?,total=36,started_at=10,finished_at=11 WHERE run_id=?",
                       ('a'*32,row['run_id']))
        state = self.start()
        case, = state['cases']
        self.assertIsNone(case['worker_exit_code'])
        self.assertIsNone(case['version'])
        self.assertEqual((case['run_id'],case['duration_ms'],case['evidence']['trace']), (row['run_id'],1000,state['evidence']['trace']))

    def test_other_user_and_changed_scope_cannot_read_result(self):
        row = self.start()
        self.work()
        self.profile['scope']={'ui_apps':['two']}
        self.assertEqual(self.call('get_run',{'run_id':row['run_id']})[1],404)
        self.assertEqual(self.request('GET','runs')[0]['runs'],[])
        self.assertEqual(self.call('run_suite', self.args)[1],404)
        self.session.return_value={'user_id':'other','user':'other'}
        self.actor.return_value={'kind':'user','user_id':'other'}
        self.profile['user_id']='other'
        self.assertEqual(self.call('get_run',{'run_id':row['run_id']})[1],404)
        self.assertNotEqual(self.start()['run_id'], row['run_id'])

    def test_worker_rechecks_token_and_scope_before_launch(self):
        row = self.start()
        self.session.return_value=None
        self.work()
        self.runner.assert_not_called()
        self.session.return_value={'user_id':'owner','user':'user'}
        result=self.call('get_run',{'run_id':row['run_id']})[0]['result']
        self.assertEqual((result['status'], result['error_kind']), ('blocked','authentication'))

    def test_result_read_rechecks_scope_before_returning_evidence(self):
        row = self.start()
        self.work()
        old_scope=self.tools.web._scope(self.profile)
        for method in ('get_run','history'):
            with self.subTest(method=method), patch.object(self.tools,'_scope', side_effect=[old_scope,'changed']):
                response, status = (self.call('get_run',{'run_id':row['run_id']}) if method=='get_run'
                                    else self.request('GET','runs'))
                self.assertEqual(status,403)
                self.assertFalse(response['ok'])

    def test_busy_slot_is_not_recorded_as_pass(self):
        self.claim.return_value=False
        self.start()
        self.work()
        self.assertEqual(self.start()['error_kind'],'heavy_workload_busy')
        self.runner.assert_not_called()
        self.release.assert_not_called()

    def test_expired_deadline_prevents_late_completion(self):
        row = self.start()
        def slow(*args,**kwargs):
            with self.tools._db() as db:
                db.execute('UPDATE runs SET deadline=0 WHERE run_id=?',(row['run_id'],))
            return {'scenario':'ui_catalog_count','version':1,'status':'passed','total':7,'id':'a'*32,
                    'error_kind':None,'artifacts':['screenshot','trace']}
        self.runner.side_effect=slow
        self.work()
        self.assertEqual(self.start()['status'],'timed_out')
        self.assertEqual(self.start()['evidence'],{})
        self.release.assert_called_once()

    def test_dead_worker_marks_interrupted_without_replaying(self):
        row = self.start()
        with self.tools._db() as db:
            db.execute('UPDATE runs SET worker_pid=? WHERE run_id=?',(2147483647,row['run_id']))
        self.assertEqual(self.start()['status'],'interrupted')
        self.work()
        self.runner.assert_not_called()
        self.launch.assert_called_once()

    def test_timeout_from_fixed_runner_has_distinct_terminal_status(self):
        def timeout(*args,control,**kwargs):
            control.stop_reason='timeout'
            control.worker_exit_code=-9
            raise TimeoutError()
        self.runner.side_effect=timeout
        self.start()
        self.work()
        self.assertEqual((self.start()['status'],self.start()['error_kind']),('timed_out','browser_timeout'))

    def test_isolated_recovery_receipt_rejects_private_data_and_forged_outcomes(self):
        self.assertEqual(self.request('GET','recovery-drill')[0]['report'],None)
        checks = []
        for kind,status,error,raw in [('cancel','cancelled','cancelled',-9),('worker_crash','interrupted','worker_crash',19),
                                      ('timeout','timed_out','timeout',-9),('service_restart','interrupted','service_restarted',None)]:
            checks.append(dict(kind=kind,run_id=str(uuid.uuid4()),before='running',status=status,error_kind=error,
                               worker_exit_code=raw,duration_ms=10,verified=True))
        report = dict(version=1,environment='isolated',checked_at=time.time(),checks=checks)
        path = self.directory/'mcp-recovery-drill.json'
        path.write_text(json.dumps(report))
        path.chmod(0o600)
        self.assertEqual(self.request('GET','recovery-drill')[0]['report'],report)
        for bad in [dict(report,environment='production'),dict(report,checks=checks[:3]+checks[:1]),
                    dict(report,checks=[dict(checks[0],status='passed')]+checks[1:]),
                    dict(report,checks=[dict(checks[0],worker_exit_code=0)]+checks[1:]),dict(report,session='secret'), None]:
            path.write_text(json.dumps(bad))
            self.assertEqual(self.request('GET','recovery-drill')[1],503)
        path.write_text(json.dumps(report))
        path.chmod(0o644)
        self.assertEqual(self.request('GET','recovery-drill')[1],503)
        path.rename(self.directory/'private.json')
        path.symlink_to(self.directory/'private.json')
        self.assertEqual(self.request('GET','recovery-drill')[1],503)
        self.session.return_value=None
        self.assertEqual(self.request('GET','recovery-drill')[1],403)

    def test_receipt_checks_durable_run_and_owner_scope_and_rejects_symlink(self):
        row=self.start()
        self.work()
        self.assertIsNone(self.request('GET','acceptance')[0]['report'])
        receipt={'version':1,'run_id':row['run_id'],'checked_at':time.time(),
                 'checks':[{'name':'host discovery','status':'passed','detail':'3 tools'}]}
        path=self.directory/'mcp-acceptance.json'
        path.write_text(json.dumps(receipt))
        path.chmod(0o600)
        self.assertEqual(self.request('GET','acceptance')[0]['report'],receipt)
        self.profile['scope']={'ui_apps':[]}
        self.assertIsNone(self.request('GET','acceptance')[0]['report'])
        self.profile['scope']={'ui_apps':['one']}
        path.rename(self.directory/'other')
        path.symlink_to(self.directory/'other')
        self.assertEqual(self.request('GET','acceptance')[1],503)


if __name__ == '__main__':
    unittest.main()
