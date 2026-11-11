from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from task_server import practice_knowledge_http as knowledge
from task_server import requirement_design_http as http
from task_server.access_control import AccessDenied, MainAccess
from task_server.services import requirement_design, ui_execution_contract as contract


BASE = http.BASE + '/practice-results/kit-regression'
APP = 'com.kfb.model'


class KitRegressionReportTests(unittest.TestCase):
    def setUp(self):
        self.routes = {}
        def register(path):
            def add(handler):
                self.routes[path] = handler
                return handler
            return add
        http.register_routes(register, lambda path: lambda f: f)
        self.profile = {'user_id': 'u', 'status': 'active', 'permissions': ['ui.view'], 'scope': {'ui_apps': [APP]}}
        self.addCleanup(patch.stopall)
        patch.object(contract, 'current_actor', return_value={'kind': 'user', 'user_id': 'u', 'username': 'user'}).start()
        patch.object(contract.identity, 'get_access_profile', return_value=self.profile).start()
        patch.object(knowledge, 'configured_test_applications', return_value=[{'name': '智小白3D', 'package': APP}]).start()
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patch.object(requirement_design, '_root', return_value=Path(tmp.name)).start()
        self.file = Path(tmp.name) / 'requirement-design-materials/evaluation/kit-regression.json'
        self.file.parent.mkdir(parents=True)
        self.doc = dict(schema_version=1, scope={'app_ids': [APP]}, run_id='kit-e2e-20261111-r1',
                        created_at='2026-11-11T09:00:00+08:00', requirement='测试来源', summary='仅测试投影',
                        generation={'status': '已生成', 'observed': '生成一条', 'evidence': '生成回执'},
                        cases=[dict(id='C1', title='示例', source='R1', expected='显示列表', raw='PRIVATE')],
                        checks=[dict(id='K1', case_id='C1', channel='ui', title='展示', status='blocked',
                                     observed='缺少数据', evidence='检查记录')], defects=[], knowledge=[],
                        limitations=['待人工确认'], raw='PRIVATE')

    def call(self, suffix='', **params):
        self.assertIn(BASE + suffix, self.routes, 'missing kit regression route')
        handler = Mock()
        self.routes[BASE + suffix](handler, params)
        return handler

    def publish(self):
        self.file.write_text(json.dumps(self.doc))

    def test_viewer_only_sees_app_scoped_published_evidence(self):
        self.assertEqual(self.call()._json.call_args.args[1], 404)
        self.assertEqual(self.call(scope='*')._json.call_args.args[1], 400)
        self.publish()
        for method in ('GET', 'HEAD'):
            MainAccess(self.profile).check(method, BASE)
        with self.assertRaises(AccessDenied):
            MainAccess(self.profile).check('POST', BASE)
        result, code = self.call()._json.call_args.args
        self.assertEqual(code, 200)
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.assertEqual(result['report']['checks'][0]['status'], 'blocked')
        self.assertEqual(result['report']['defects'], [])
        self.profile['scope']['ui_apps'] = ['other']
        self.assertEqual(self.call()._json.call_args.args[1], 403)
        self.profile['scope']['ui_apps'] = [APP]
        self.doc['scope']['app_ids'].append('another')
        self.publish()
        self.assertEqual(self.call()._json.call_args.args[1], 403)

    def test_rejects_invalid_links_and_duplicate_counts(self):
        check = self.doc['checks'][0]
        for key, value in [('case_id', 'missing'), ('status', 'complete'), ('channel', 'draft')]:
            original = check[key]
            check[key] = value
            self.publish()
            self.assertEqual(self.call()._json.call_args.args[1], 503)
            check[key] = original
        self.doc['checks'].append(check)
        self.publish()
        self.assertEqual(self.call()._json.call_args.args[1], 503)

    def test_download_is_bound_to_current_release_and_app_scope(self):
        blob = b'example archive'
        digest = hashlib.sha256(blob).hexdigest()
        self.doc['evidence'] = {'bytes': len(blob), 'sha256': digest}
        self.publish()
        archive = self.file.with_name('kit-regression-evidence.zip')
        archive.write_bytes(blob)
        handler = self.call('-evidence', id=digest)
        handler.wfile.write.assert_called_once_with(blob)
        handler.send_header.assert_any_call('Cache-Control', 'private, no-store')
        self.assertEqual(self.call('-evidence', id='a'*64)._json.call_args.args[1], 404)
        self.assertEqual(self.call('-evidence', id='../file')._json.call_args.args[1], 400)
        archive.write_bytes(b'x'*len(blob))
        self.assertEqual(self.call('-evidence', id=digest)._json.call_args.args[1], 503)
        self.profile['permissions'] = []
        self.assertEqual(self.call('-evidence', id=digest)._json.call_args.args[1], 403)

    def test_independent_immutable_ticket_and_live_report_reference(self):
        input_path = self.file.parents[2] / 'input.json'
        data = dict(run_id=self.doc['run_id'], observation_id='field-01', scope={'app_ids': [APP]},
                    title='构建差异', observed='现场未见入口', evidence='实机记录一')
        input_path.write_text(json.dumps(data))
        command = [sys.executable, str(Path(__file__).resolve().parents[1] / 'scripts/publish_kit_ticket.py'),
                   '--identity-root', str(self.file.parents[2]), '--input', str(input_path)]
        def publish():
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)
        with ThreadPoolExecutor(max_workers=3) as pool:
            receipts = list(pool.map(lambda _: publish(), range(3)))
        self.assertEqual(sum(receipt['created'] for receipt in receipts), 1)
        self.assertEqual(len({receipt['id'] for receipt in receipts}), 1)
        ticket_id = receipts[0]['id']
        self.assertRegex(ticket_id, r'^KIT-[0-9a-f]{24}$')
        self.assertEqual(publish(), {'created': False, 'id': ticket_id})
        input_path.write_text(json.dumps(dict(data, observed='修改事实')))
        self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)
        result, code = self.call('-ticket', id=ticket_id)._json.call_args.args
        self.assertEqual(code, 200)
        self.assertEqual(result['ticket']['observed'], data['observed'])
        self.assertEqual(result['ticket']['type'], '构建或配置待查')
        self.assertEqual(result['ticket']['status'], '已登记 · 待核对')
        self.doc['defects'] = [{'ticket_id': ticket_id, 'status': '伪造'}]
        self.publish()
        self.assertEqual(self.call()._json.call_args.args[0]['report']['defects'], [result['ticket']])
        self.assertEqual(self.call('-ticket', id='../x')._json.call_args.args[1], 400)
        MainAccess(self.profile).check('GET', BASE+'-ticket')
        with self.assertRaises(AccessDenied):
            MainAccess(self.profile).check('POST', BASE+'-ticket')
        self.profile['scope']['ui_apps'] = ['other']
        self.assertEqual(self.call('-ticket', id=ticket_id)._json.call_args.args[1], 403)
        self.profile['scope']['ui_apps'] = [APP]
        self.doc['run_id'] = 'kit-e2e-20261112-r1'
        self.publish()
        self.assertEqual(self.call()._json.call_args.args[1], 503)
