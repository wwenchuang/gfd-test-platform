from contextlib import ExitStack
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from task_server import requirement_design_http as http
from task_server.services import requirement_design as design


class PracticeResultsTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.enterContext = stack.enter_context
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(design, '_root', return_value=self.root))
        self.folder = self.root / 'requirement-design-materials' / 'evaluation'
        self.folder.mkdir(parents=True)
        self.routes = {}
        def register(path):
            def add(fn):
                self.routes[path] = fn
                return fn
            return add
        http.register_routes(register, register)
        self.enterContext(patch.object(http, 'current_actor', return_value={'kind':'user','user_id':'u','username':'name'}))
        self.profile = {'user_id':'u','status':'active','scope':{'ui_apps':'*','api_projects':['p'],'api_environments':['e']},
                        'permissions':['ui.view','platform.configure','api.view']}
        self.enterContext(patch.object(design.identity, 'get_access_profile', return_value=self.profile))

    def call(self, kind, **params):
        handler = Mock()
        self.routes[http.BASE+'/practice-results/'+kind](handler, params)
        return handler

    def test_case_projection_checks_both_scopes_and_unknown_fields(self):
        doc = dict(version=1,project_id='p',environment_id='e',cases=[dict(id='E01',title='公开标题',status='partial',
                   internal='raw',checks=[],gaps=['原响应未留存'])])
        (self.folder/'w8-report.json').write_text(json.dumps(doc))
        result, status = self.call('replay')._json.call_args.args
        self.assertEqual(status, 200)
        self.assertNotIn('internal', str(result))
        self.assertEqual(result['report']['cases'][0]['gaps'], ['原响应未留存'])
        self.profile['scope']['api_projects'] = ['other']
        self.assertEqual(self.call('replay')._json.call_args.args[1],404)
        self.profile['scope']['api_projects'] = '*'
        self.profile['scope']['api_environments'] = []
        self.assertEqual(self.call('replay')._json.call_args.args[1],404)

    def test_outer_ui_scope_and_permissions_apply_to_all_published_artifacts(self):
        for key, bad in [('scope', {'ui_apps':['other'],'api_projects':'*','api_environments':'*'}),
                         ('permissions', ['platform.configure','api.view'])]:
            with patch.dict(self.profile, {key:bad}):
                for kind in ['replay','visual','screenshot']:
                    self.assertEqual(self.call(kind)._json.call_args.args[1],403)

    def test_only_current_report_png_hash_with_full_scope_can_be_downloaded(self):
        raw=b'\x89PNG\r\n\x1a\nactual image fixture'
        digest=hashlib.sha256(raw).hexdigest()
        assets=self.folder/'visual-artifacts'
        assets.mkdir()
        path=assets/(digest+'.png')
        path.write_bytes(raw)
        doc=dict(version=1,scenarios=[dict(id='f',title='场景')],runs=[dict(scenario_id='f',screenshots=[dict(file=path.name,sha256=digest,label='定位')])])
        (self.folder/'w9-report.json').write_text(json.dumps(doc))
        self.assertEqual(self.call('screenshot',id=digest)._json.call_args.args[1],404)
        self.profile['is_superuser']=True
        self.call('screenshot',id=digest).wfile.write.assert_called_once_with(raw)
        for bad in ('f'*64,'../w9-report.json'):
            self.assertNotEqual(self.call('screenshot',id=bad)._json.call_args.args[1],200)
        path.write_bytes(raw+b'changed')
        self.assertNotEqual(self.call('screenshot',id=digest)._json.call_args.args[1],200)
        path.unlink()
        path.symlink_to(self.folder/'w9-report.json')
        self.assertNotEqual(self.call('screenshot',id=digest)._json.call_args.args[1],200)

if __name__ == '__main__':
    unittest.main()
