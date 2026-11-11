import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from task_server import requirement_design_http as http
from task_server.access_control import AccessDenied, MainAccess
from task_server.services import ui_execution_contract as contract

BASE = http.BASE + '/knowledge'
APP = 'com.kfb.model'


class KnowledgeRouteTests(unittest.TestCase):
    def setUp(self):
        from task_server import practice_knowledge_http
        self.module = practice_knowledge_http
        self.routes = {}
        def register(path):
            def add(function):
                self.routes[path] = function
                return function
            return add
        http.register_routes(register, register)
        self.actor = {'kind': 'user', 'user_id': 'u', 'username': 'user'}
        self.profile = {'user_id': 'u', 'status': 'active', 'permissions': ['ui.view'], 'scope': {'ui_apps': [APP]}}
        self.addCleanup(patch.stopall)
        patch.object(contract, 'current_actor', return_value=self.actor).start()
        patch.object(contract.identity, 'get_access_profile', return_value=self.profile).start()
        self.catalog = patch.object(self.module, 'configured_test_applications', return_value=[
            {'package': APP, 'name': '智小白3D'}, {'package': 'other.app', 'name': '其他'}]).start()

    def call(self, path='', body=None, qs=None):
        handler = Mock()
        handler._body.return_value = body
        self.routes[BASE+path](handler, qs or {})
        return handler._json.call_args.args

    def test_view_only_scoped_user_can_reach_exact_routes(self):
        for method, suffix in [('GET', ''), ('POST','/search'), ('GET','/evaluation')]:
            MainAccess(self.profile).check(method, BASE+suffix)
        with self.assertRaises(AccessDenied):
            MainAccess(self.profile).check('GET', BASE+'/other')
        self.assertEqual(self.call()[0]['apps'], [{'id': APP, 'name':'智小白3D'}])
        self.profile['permissions'] = []
        for method, suffix in [('GET', ''), ('POST','/search'), ('GET','/evaluation')]:
            with self.assertRaises(AccessDenied):
                MainAccess(self.profile).check(method, BASE+suffix)
            self.assertEqual(self.call(suffix)[1], 403)

    def test_scope_rechecked_and_spoofed_params_rejected_before_search(self):
        body = {'question':'已发布的用例如何核对？', 'app_id':APP, 'method':'keyword'}
        with patch.object(self.module, '_search', return_value={'status':'unknown'}) as search:
            self.assertEqual(self.call('/search', body)[1], 200)
            search.assert_called_once_with(body['question'], allowed_apps=[APP], method='keyword')
            search.reset_mock()
            self.assertEqual(self.call('/search', dict(body,allowed_apps=['*']))[1],400)
            self.assertEqual(self.call('/search', dict(body,app_id='other.app'))[1],403)
            self.assertEqual(self.call('/search', dict(body,question=' '))[1],400)
            self.assertEqual(self.call('/search', dict(body,method='other'))[1],400)
            for scope in ([], ['other.app'], 'com.kfb.model.extra'):
                self.profile['scope']['ui_apps'] = scope
                for path in ('','/search','/evaluation'):
                    self.assertEqual(self.call(path, body)[1], 403)
            search.assert_not_called()
        self.profile['scope']['ui_apps'] = '*'
        self.catalog.return_value = []
        self.assertEqual(self.call()[1], 403)

    def test_report_requires_exact_scope_and_projects_only_display_fields(self):
        from task_server.services import requirement_design as design
        with TemporaryDirectory() as temp, patch.object(design, '_root', return_value=Path(temp)):
            folder = Path(temp) / 'requirement-design-materials/evaluation'
            folder.mkdir(parents=True)
            report_path = folder / 'knowledge-holdout.json'
            self.assertEqual(self.call('/evaluation')[1], 404)
            report = {'schema_version':1,'split':'holdout','scope':{'app_ids':[APP]},'methods':{},
                      'questions':[], 'human_review':{'reviewed':0,'total':30,'status':'pending'},'raw_output':'private'}
            report_path.write_text(json.dumps(report))
            data, code = self.call('/evaluation')
            self.assertEqual(code, 200)
            self.assertNotIn('raw_output',data['report'])
            self.assertEqual(data['report']['human_review']['reviewed'],0)
            for scope in ([], [APP, 'other.app']):
                report['scope']['app_ids'] = scope
                report_path.write_text(json.dumps(report))
                self.assertEqual(self.call('/evaluation')[1],403)
            del report['scope']
            report_path.write_text(json.dumps(report))
            self.assertNotEqual(self.call('/evaluation')[1],200)

if __name__ == '__main__':
    unittest.main()
