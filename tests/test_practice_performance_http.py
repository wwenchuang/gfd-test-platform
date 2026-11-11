import hashlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from task_server import practice_knowledge_http as knowledge
from task_server import requirement_design_http as http
from task_server.access_control import AccessDenied, MainAccess
from task_server.services import requirement_design, ui_execution_contract as contract

BASE = http.BASE + '/practice-results/performance'
APP = 'com.kfb.model'


class PerformanceReportTests(unittest.TestCase):
    def setUp(self):
        routes = {}
        def register(path):
            def add(handler):
                routes[path] = handler
                return handler
            return add
        http.register_routes(register, lambda path: lambda f: f)
        self.route = routes.get(BASE)
        self.download = routes.get(BASE + '-evidence')
        self.profile = {'user_id': 'u', 'status': 'active', 'permissions': ['ui.view'], 'scope': {'ui_apps': [APP]}}
        self.addCleanup(patch.stopall)
        patch.object(contract, 'current_actor', return_value={'kind': 'user', 'user_id': 'u', 'username': 'user'}).start()
        patch.object(contract.identity, 'get_access_profile', return_value=self.profile).start()
        patch.object(knowledge, 'configured_test_applications', return_value=[{'name': '智小白3D', 'package': APP}]).start()
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patch.object(requirement_design, '_root', return_value=Path(tmp.name)).start()
        self.file = Path(tmp.name) / 'requirement-design-materials/evaluation/jmeter-readonly.json'
        self.file.parent.mkdir(parents=True)
        self.stage = dict(phase='baseline', threads=5, ramp_s=0, warmup_s=3, steady_s=10, elapsed_s=13, samples=100,
                          http_successes=99, business_successes=97, errors=3, tps=10, p50_ms=10,
                          p95_ms=25, p99_ms=75, jtl_sha256='a'*64, monitor=[
                              dict(source='generator', metric='CPU', unit='%', samples=5, min=2, max=6, mean=4, logs='PRIVATE')])
        self.doc = dict(schema_version=1, scope={'app_ids': [APP]}, created_at='2026-11-11T09:00:00+08:00',
                        environment='isolated', scenario='只读目录', conditions='固定间隔，请求只读目录。',
                        thresholds={'p95_ms': 20, 'error_pct': 1}, stages=[self.stage],
                        resources={'generator': '已采集', 'server': '未采集', 'database': '未采集'},
                        limitations=['仅适用于单机'], retest='待复测', stop_reason='已按计划结束',
                        raw_jtl='PRIVATE')

    def call(self, params=None):
        self.assertIsNotNone(self.route, 'missing read-only performance route')
        handler = Mock()
        self.route(handler, params or {})
        return handler._json.call_args.args

    def publish(self):
        self.file.write_text(json.dumps(self.doc))

    def test_only_exact_read_route_in_viewer_scope(self):
        for method in ('GET', 'HEAD'):
            MainAccess(self.profile).check(method, BASE)
        for method, url in [('POST', BASE), ('GET', BASE+'/raw'), ('GET', BASE+'/')]:
            with self.assertRaises(AccessDenied):
                MainAccess(self.profile).check(method, url)
        self.assertEqual(self.call()[1], 404)
        self.assertEqual(self.call({'app_ids': ['*']})[1], 400)
        for scope in ([], ['other'], APP+'.extra'):
            self.profile['scope']['ui_apps'] = scope
            self.assertEqual(self.call()[1], 403)
        self.profile['scope']['ui_apps'] = '*'
        self.profile['permissions'] = []
        self.assertEqual(self.call()[1], 403)

    def test_download_requires_matching_report_scope_size_and_digest(self):
        self.assertIsNotNone(self.download)
        blob = b'public fixture archive'
        digest = hashlib.sha256(blob).hexdigest()
        self.doc['evidence'] = {'sha256': digest, 'bytes': len(blob), 'private_path': 'PRIVATE'}
        self.publish()
        self.assertEqual(self.call()[0]['report']['evidence'], {'sha256': digest, 'bytes': len(blob)})
        archive = self.file.with_name('jmeter-readonly-evidence.zip')
        archive.write_bytes(blob)
        def read(value):
            handler = Mock()
            self.download(handler, {'id': value})
            return handler
        handler = read(digest)
        handler.send_response.assert_called_once_with(200)
        handler.wfile.write.assert_called_once_with(blob)
        handler.send_header.assert_any_call('Cache-Control', 'private, no-store')
        self.assertEqual(read('b'*64)._json.call_args.args[1], 404)
        self.assertEqual(read('../path')._json.call_args.args[1], 400)
        for tampered in (b'x'*len(blob), blob+b'x'):
            archive.write_bytes(tampered)
            self.assertEqual(read(digest)._json.call_args.args[1], 503)
        self.profile['scope']['ui_apps'] = ['other']
        self.assertEqual(read(digest)._json.call_args.args[1], 403)
        self.profile['scope']['ui_apps'] = [APP]
        self.doc['scope']['app_ids'] = [APP, 'other']
        self.publish()
        self.assertEqual(read(digest)._json.call_args.args[1], 403)

    def test_projects_only_measured_display_values(self):
        self.publish()
        data, status = self.call()
        self.assertEqual(status, 200)
        report = data['report']
        self.assertNotIn('PRIVATE', json.dumps(report))
        self.assertEqual(report['stages'][0]['http_successes'], 99)
        self.assertEqual(report['stages'][0]['business_successes'], 97)
        self.assertEqual(report['stages'][0]['monitor'][0]['samples'], 5)
        self.assertEqual(report['stages'][0]['warmup_s'], 3)
        self.doc['stages'] = []
        self.publish()
        self.assertEqual(self.call()[0]['report']['stages'], [])

    def test_fails_closed_for_mixed_scope_invalid_counts_and_sensitive_text(self):
        self.doc['scope']['app_ids'].append('other.app')
        self.publish()
        self.assertEqual(self.call()[1], 403)
        self.doc['scope']['app_ids'] = [APP]
        for key, value in [('http_successes', 101), ('p50_ms', 'raw'), ('samples', True)]:
            original = self.stage[key]
            self.stage[key] = value
            self.publish()
            self.assertEqual(self.call()[1], 503)
            self.stage[key] = original
        for text in ('https://private.example/a?token=abc', 'Authorization: Bearer abc', '访问 10.0.0.1', '/var/tmp/log'):
            self.doc['conditions'] = text
            self.publish()
            data, code = self.call()
            self.assertEqual(code, 503)
            self.assertNotIn(text, str(data))


if __name__ == '__main__':
    unittest.main()
