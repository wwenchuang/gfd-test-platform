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
        self.assertEqual([s['suite_id'] for s in response['result']['suites']], ['published-catalog'])
        access = MainAccess(self.profile, catalog=[])
        for method,path in self.routes:
            if '/mcp/' in path:
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
        self.runner.assert_called_once_with('owner','user','token',scenario='ui_catalog_count')
        self.assertLessEqual(row['started_at'],row['finished_at'])
        self.assertNotIn(b'"token"', (self.directory/'platform-mcp.sqlite3').read_bytes())

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
        self.runner.return_value={'scenario':'ui_catalog_count','version':1,'status':'blocked','error_kind':'environment','detail':'browser_timeout'}
        self.start()
        self.work()
        self.assertEqual((self.start()['status'],self.start()['error_kind']),('timed_out','browser_timeout'))

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
