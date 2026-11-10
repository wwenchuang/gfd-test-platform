import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from task_server import requirement_design_http as http
from task_server.services import requirement_design as store


class EvaluationHttpTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.folder = self.root / 'requirement-design-materials/evaluation'
        self.folder.mkdir(parents=True)
        self.routes = {}
        def register(path):
            def add(fn):
                self.routes[path] = fn
                return fn
            return add
        http.register_routes(register, register)
        for obj, attr, value in [(store, '_root', self.root), (http, 'current_actor', {'kind':'user','user_id':'u','username':'name'})]:
            ctx = patch.object(obj, attr, return_value=value)
            self.addCleanup(ctx.stop)
            ctx.start()
        ctx = patch.object(store, 'authorized', return_value=True)
        self.addCleanup(ctx.stop)
        self.access = ctx.start()

    def request(self, suffix='', params=None):
        h = Mock()
        self.routes[http.BASE + '/evaluation' + suffix](h, params or {})
        return h._json.call_args.args

    def test_authorization_is_checked_before_read_and_unknown_parameters_rejected(self):
        self.access.return_value = False
        for path in ('', '/blind'):
            self.assertEqual(self.request(path)[1], 403)
        self.access.return_value = True
        self.assertEqual(self.request(params={'filename':'blind-key.json'})[1], 400)
        self.assertEqual(self.request()[1], 404)

    def test_blind_export_drops_hidden_mapping_and_reviewer_scores(self):
        document = {'version':1, 'status':'pending', 'key':{'B01':'zero'}, 'items':[{
            'label':'B01', 'group':'g1', 'variant':'zero', 'expected':'预期', 'title':'场景', 'review':'9',
            'precondition':'材料齐全', 'steps':['操作'], 'refs':['P1']}]}
        (self.folder/'blind-review.json').write_text(json.dumps(document))
        payload, code = self.request('/blind')
        self.assertEqual(code, 200)
        public = payload['blind']
        self.assertNotIn('key', public)
        self.assertNotIn('variant', public['items'][0])
        self.assertIsNone(public['items'][0]['review'])
        self.assertNotIn('zero', str(public))

    def test_report_exports_reproducibility_metadata_but_no_blind_key(self):
        group = {'id':'g1', 'title':'关联模型', 'clauses':[{'id':'P1','text':'关联多个模型'}],
                 'prototypes':[{'id':'V1','caption':'选择弹窗','sha256':'abc','private_path':'/raw'}],
                 'variants':[{'variant':'few', 'status':'failed','error':'invalid_response','raw':'private raw'}],
                 'observations':[], 'questions':['上限？']}
        doc = {'version':1,'status':'partial','candidate_count':0,'candidate_target':20,
               'groups':[group], 'candidates':[], 'human_review':'pending','blind_review':{'candidate_count':0},
               'prompt_sha256':{'few':'sha'},'parameters':{'response_format':{'type':'json_object'},'max_tokens':5000},
               'deduplication':'按步骤和预期', 'blind_key':{'B01':'few'}}
        (self.folder/'report.json').write_text(json.dumps(doc))
        payload, code = self.request()
        report = payload['report']
        self.assertEqual(code, 200)
        self.assertEqual(report['parameters'],doc['parameters'])
        self.assertEqual(report['prompt_sha256'],doc['prompt_sha256'])
        self.assertEqual(report['deduplication'],doc['deduplication'])
        self.assertEqual(report['groups'][0]['variants'][0]['error'], 'invalid_response')
        self.assertNotIn('private_path',str(report))
        self.assertNotIn('private raw',str(report))
        self.assertNotIn('blind_key',report)

    def test_symlink_and_invalid_report_fail_without_disclosing_file_contents(self):
        secret = self.root/'secret.json'
        secret.write_text('{"key":"sensitive"}')
        path = self.folder/'report.json'
        path.symlink_to(secret)
        payload, status = self.request()
        self.assertNotEqual(status, 200)
        self.assertNotIn('sensitive', str(payload))
        path.unlink()
        path.write_text('{"broken":true}')
        self.assertEqual(self.request()[1], 503)
