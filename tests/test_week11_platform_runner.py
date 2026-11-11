import json
import http.server
import tempfile
import threading
from pathlib import Path
from contextlib import ExitStack
from types import SimpleNamespace
import signal
import sys
import unittest
from unittest.mock import patch

from task_server.services import week5_web_service as web

ACTOR = ('owner', 'tester', 'token')


class PlatformRunnerTests(unittest.TestCase):
    def runner(self):
        from task_server.services import platform_runner
        return platform_runner

    def test_python_checks_never_treat_boolean_code_as_zero(self):
        from task_server.fixtures.week11_runner.feedback import summarize
        summary = summarize(200, {'code':False, 'data':[]}, 'request')
        self.assertIsNone(summary['actual_code'])
        self.assertEqual(summary['checks'], {'http_200': True, 'code_zero': False, 'data_list': True})
        self.assertNotIn('data', summary)

    def test_python_adapter_runs_real_subprocess_with_redacted_http_results(self):
        runner = self.runner()
        from task_server.fixtures.week11_runner import feedback
        class Endpoint(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.server.auth_observed = self.headers.get('Authorization') == 'isolated-fixture'
                self.server.request_id = self.headers.get('X-Request-Id')
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps(self.server.body).encode())

        server = http.server.HTTPServer(('127.0.0.1', 0), Endpoint)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with tempfile.TemporaryDirectory() as tmp:
            wrapper = Path(tmp) / 'feedback-fixture.py'
            wrapper.write_text(
                'import runpy,sys\n'
                + 'worker=runpy.run_path(' + repr(feedback.__file__) + ')\n'
                + 'worker["main"].__globals__["URL"]=' + repr('http://127.0.0.1:%s/feedback' % server.server_port) + '\n'
                + 'sys.exit(worker["main"]())\n')
            with patch.object(runner, '_FEEDBACK', wrapper), patch.object(runner, '_feedback_credentials', return_value='isolated-fixture'), \
                    patch.object(runner, 'feedback_authorized', return_value=True):
                for body, status, rc, case_exit in [
                        ({'code':0, 'data':[{'private':'never archived'}]}, 'passed', 0, 0),
                        ({'code':999, 'data':[]}, 'failed', 1, 1),
                        ({'code':4009, 'data':None}, 'blocked', 2, None)]:
                    with self.subTest(status=status):
                        server.body = body
                        result = runner.execute('feedback-categories', ACTOR, lambda:False)
                        self.assertEqual((result['status'], result['worker_exit_code'], result['exit_code']), (status, rc, case_exit))
                        self.assertEqual(result['evidence']['request_id'], server.request_id)
                        self.assertTrue(server.auth_observed)
                        self.assertNotIn('never archived', json.dumps(result))
                        self.assertNotIn('isolated-fixture', json.dumps(result))

    def test_python_request_credentials_never_follow_redirects(self):
        from task_server.fixtures.week11_runner import feedback
        self.assertIsNone(feedback.NoRedirect().redirect_request(None, None, 302, 'redirect', {}, 'http://unused'))

    def test_process_cancel_kills_own_child_and_records_observed_code(self):
        runner = self.runner()
        control = runner.ProcessControl(lambda: True)
        with self.assertRaises(runner.Cancelled):
            web._communicate([sys.executable, '-c', 'import time;time.sleep(20)'], {}, b'', 5, control=control)
        self.assertEqual(control.worker_exit_code, -signal.SIGKILL)

    def test_worker_timeout_preserves_signal_and_classification(self):
        runner = self.runner()
        control = runner.ProcessControl(lambda: False)
        with self.assertRaises(TimeoutError):
            web._communicate([sys.executable, '-c', 'import time;time.sleep(20)'], {}, b'', .03, control=control)
        self.assertEqual(control.worker_exit_code, -signal.SIGKILL)
        self.assertEqual(control.stop_reason, 'timeout')

    def test_real_node_case_failure_is_distinct_from_worker_crash(self):
        runner = self.runner()
        observed = {'scenario':'ui_catalog_failure_drill', 'status':'failed', 'version':1, 'phase':'drill',
                    'error_kind':'drill_assertion', 'total':17, 'expected_total':0, 'node_version':'v22.0.0'}
        def run_web(*_, scenario, control):
            data = web._bounded_process([sys.executable, '-c', 'print('+repr(json.dumps(observed))+')'],
                                        {}, 'token', 'owner', scenario=scenario, control=control)
            return dict(data, id='a'*32, artifacts=['screenshot','trace'])
        with patch.object(web, 'run', side_effect=run_web):
            result = runner.execute('catalog-failure-drill', ACTOR, lambda:False)
        self.assertEqual((result['status'], result['exit_code'], result['worker_exit_code'],
                          result['runner_version']), ('failed', 1, 0, 'v22.0.0'))

    def test_unexpected_worker_exit_does_not_become_an_assertion(self):
        runner = self.runner()
        def crash(*_, scenario, control):
            try:
                web._bounded_process([sys.executable, '-c', 'import sys;sys.exit(8)'], {},
                                     'token', 'owner', scenario=scenario, control=control)
            except ValueError:
                return {'status':'blocked', 'error_kind':'environment'}
        with patch.object(web, 'run', side_effect=crash):
            result = runner.execute('published-catalog', ACTOR, lambda:False)
        self.assertEqual((result['status'], result['error_kind'], result['exit_code'], result['worker_exit_code']),
                         ('interrupted','worker_crash',None,8))

    def test_feedback_requires_permission_on_active_environment_and_pinned_get(self):
        try:
            from task_server.api_testing import http, access
            from task_server.api_testing.config import ApiTestingSettings
        except ModuleNotFoundError:
            self.skipTest('API testing project dependencies required')
        runner = self.runner()
        template = {'method':'GET', 'path':runner._FEEDBACK_PATH, 'service':'business'}
        child = SimpleNamespace(execution_id='execution', environment_revision_id='old', endpoint_id='endpoint', case_version_id='version')
        with ExitStack() as patches:
            def swap(target, name, value):
                return patches.enter_context(patch.object(target, name, return_value=value))
            swap(runner.auth,'verify_session_token',{'user_id':'owner','user':'tester'})
            swap(runner.identity,'get_access_profile',{'user_id':'owner','status':'active'})
            swap(ApiTestingSettings, 'from_env', SimpleNamespace(enabled=True))
            swap(access, 'require_permission', None)
            scope = swap(access, 'require_execution_environment', None)
            factory = patches.enter_context(patch.object(http, '_factory')).return_value
            swap(http, '_scope_execution', SimpleNamespace(id='execution',project_id='project',source_revision_id='source',environment_revision_id='old'))
            swap(http, '_scope_execution_case', child)
            swap(http, '_scope_endpoint', SimpleNamespace(id='endpoint',method='GET',path=runner._FEEDBACK_PATH,revision_id='source'))
            swap(http, '_scope_case_version', SimpleNamespace(endpoint_id='endpoint',case_id='case',request_template={'request':template}))
            swap(http, '_scope_case', SimpleNamespace(project_id='project'))
            swap(http, '_scope_environment_revision', SimpleNamespace(environment_id='env'))
            swap(http, '_scope_environment', SimpleNamespace(status='active',project_id='project',active_revision_id='active'))
            self.assertEqual(runner._feedback_context(ACTOR), (factory, 'active', template))
            scope.assert_called_once_with(factory.return_value.__enter__.return_value, 'active', 'tester', project_id='project')
            scope.side_effect = PermissionError()
            self.assertFalse(runner.feedback_authorized(ACTOR))
            scope.side_effect = None
            child.environment_revision_id = 'another'
            self.assertFalse(runner.feedback_authorized(ACTOR))
            child.environment_revision_id = 'old'
            template['path'] = '/other'
            self.assertFalse(runner.feedback_authorized(ACTOR))

    def test_existing_browser_busy_and_timeout_keep_their_reason(self):
        runner = self.runner()
        with patch.object(web, 'run', side_effect=web.week1_smoke.SmokeBusy):
            busy = runner.execute('published-catalog', ACTOR, lambda:False)
        self.assertEqual((busy['status'], busy['error_kind']), ('blocked', 'check_busy'))
        with patch.object(web, 'run', return_value={'status':'blocked', 'error_kind':'environment', 'detail':'browser_timeout'}):
            timeout = runner.execute('published-catalog', ACTOR, lambda:False)
        self.assertEqual((timeout['status'], timeout['error_kind'], timeout['worker_exit_code']), ('timed_out', 'timeout', None))

    def test_unknown_suite_is_rejected(self):
        with self.assertRaises(ValueError):
            self.runner().execute('arbitrary', ACTOR, lambda:False)


if __name__ == '__main__':
    unittest.main()
