import base64
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from task_server.services import requirement_design_model as design
from task_server.services.week1_probe import FIXTURE, NoRedirect


class RequirementDesignModelTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name)
        self.png = FIXTURE.read_bytes()
        (self.path / 'prototype.png').write_bytes(self.png)
        self.digest = hashlib.sha256(self.png).hexdigest()
        self.source = {'title': '首页', 'app': 'A', 'version': 'v1',
                       'clauses': [{'id': 'P1', 'text': '显示入口'}, {'id': 'P2', 'text': '待确认'}],
                       'prototypes': [{'id': 'V1', 'caption': '首页布局', 'file': 'prototype.png', 'sha256': self.digest}]}
        self.draft = {
            'points': [{'id': 'T1', 'title': '入口可见', 'expected': '入口显示', 'refs': ['P1', 'V1']},
                       {'id': 'T2', 'title': '核对布局', 'expected': '入口居左', 'refs': ['V1']}],
            'cases': [{'id': 'C1', 'title': '进入首页', 'precondition': '已登录', 'steps': ['打开首页'],
                       'expected': '可见入口', 'point_ids': ['T1']}], 'questions': ['入口权限需确认']}
        self.usage = {'prompt_tokens': 123, 'completion_tokens': 75, 'total_tokens': 198}
        self.response = {'id': 'chatcmpl-123', 'model': 'returned-model', 'usage': self.usage,
                         'choices': [{'finish_reason': 'stop', 'message': {}}]}
        self.build = self.enter_patch(design.urllib.request, 'build_opener')
        self.enter_patch(design, 'dashscope_api_key', return_value='sk-test')
        self.enter_patch(design, 'dashscope_base_url', return_value='https://dashscope.aliyuncs.com/compatible-mode/v1')
        self.enter_patch(design, 'dashscope_vl_model', return_value='qwen3.8-max')

    def enter_patch(self, obj, name, **kwargs):
        context = patch.object(obj, name, **kwargs)
        self.addCleanup(context.stop)
        return context.start()

    def generate(self):
        self.response['choices'][0]['message']['content'] = json.dumps(self.draft)
        stream = io.BytesIO(json.dumps(self.response).encode())
        stream.headers = {'x-request-id': 'req-xyz'}
        self.build.return_value.open.return_value = stream
        return design.generate(self.source, self.path)

    def test_evidence_matches_submitted_pixels_and_provider_receipt(self):
        result = self.generate()
        request = self.build.return_value.open.call_args.args[0]
        payload = json.loads(request.data)
        self.assertIsInstance(self.build.call_args.args[0], NoRedirect)
        self.assertEqual(request.full_url, 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions')
        self.assertEqual(payload['model'], 'qwen3.8-max')
        content = payload['messages'][-1]['content']
        url = next(block['image_url']['url'] for block in content if block['type'] == 'image_url')
        self.assertEqual(base64.b64decode(url.removeprefix('data:image/png;base64,')), self.png)
        self.assertIn('显示入口', json.dumps(content, ensure_ascii=False))
        evidence = result['evidence']
        self.assertEqual(evidence['requested_model'], payload['model'])
        self.assertEqual(evidence['model'], self.response['model'])
        self.assertEqual(evidence['request_id'], 'req-xyz')
        self.assertEqual(evidence['response_id'], 'chatcmpl-123')
        self.assertEqual(evidence['usage'], self.usage)
        self.assertEqual(evidence['prototype_sha256'], {'V1': self.digest})
        for field in ('started_at', 'completed_at', 'prompt_version'):
            self.assertTrue(evidence[field])
        self.assertGreaterEqual(evidence['elapsed_ms'], 0)
        self.assertRegex(evidence['input_sha256'], r'^[0-9a-f]{64}$')
        self.assertEqual(result['checks']['unreferenced_source_ids'], ['P2'])
        self.assertEqual(result['checks']['uncovered_point_ids'], ['T2'])
        self.assertTrue(result['checks']['review_required'])
        self.assertEqual(result['cases'], self.draft['cases'])
        self.assertIs(result['checks']['draft_complete'], True)
        self.assertIs(result['checks']['partial'], False)

    def test_provider_omissions_stay_explicit(self):
        del self.response['model'], self.response['usage']
        evidence = self.generate()['evidence']
        self.assertIsNone(evidence['model'])
        self.assertEqual(evidence['usage'], {})

    def test_changed_inputs_change_hash(self):
        first = self.generate()['evidence']['input_sha256']
        self.source['clauses'][0]['text'] += '按钮'
        self.assertNotEqual(self.generate()['evidence']['input_sha256'], first)

    def test_invalid_model_output_cannot_be_saved_as_a_draft(self):
        original = self.draft
        for mutate in (
            lambda d: d['points'][0].update(refs=['P7']),
            lambda d: d['points'][0].update(refs=[]),
            lambda d: d['cases'][0].update(point_ids=['T3']),
            lambda d: d['cases'][0].update(steps='点击入口'),
            lambda d: d['points'][1].update(id='T1'),
            lambda d: d['cases'].extend(dict(d['cases'][0], id=f'X{i}') for i in range(5)),
            lambda d: d.pop('questions'),
        ):
            self.draft = copy.deepcopy(original)
            mutate(self.draft)
            with self.subTest(draft=self.draft), self.assertRaises(ValueError):
                self.generate()

    def test_substituted_or_outside_images_fail_before_any_request(self):
        (self.path / 'link.png').symlink_to(FIXTURE)
        for change in ({'file': str(FIXTURE)}, {'file': '../bad.png'}, {'file': 'link.png'}, {'sha256': 'a' * 64}):
            with self.subTest(change=change):
                self.source['prototypes'][0].update(file='prototype.png', sha256=self.digest)
                self.source['prototypes'][0].update(change)
                with self.assertRaises(ValueError):
                    self.generate()
                self.build.assert_not_called()

    def test_questions_preserve_disagreement_without_inventing_cases(self):
        self.draft = {'points': [], 'cases': [], 'questions': ['P1 写“显示入口”，V1 未见入口，以哪项为准？']}
        result = self.generate()
        self.assertEqual(result['questions'], self.draft['questions'])
        self.assertEqual(result['cases'], [])
        self.assertIs(result['checks']['draft_complete'], False)
        self.assertIs(result['checks']['partial'], True)
        self.assertEqual(result['checks']['unreferenced_source_ids'], ['P1', 'P2', 'V1'])

    def test_untrusted_model_endpoint_cannot_receive_private_material(self):
        self.enter_patch(design, 'dashscope_base_url', return_value='https://bad.test/compatible-mode/v1')
        with self.assertRaisesRegex(ValueError, '^unsupported_endpoint$'):
            self.generate()
        self.build.assert_not_called()

    def test_upstream_errors_do_not_expose_private_details(self):
        self.build.return_value.open.side_effect = design.urllib.error.URLError('private token and PRD text')
        with self.assertRaisesRegex(ValueError, '^model_request_failed$'):
            self.generate()

    def test_mismatched_png_signature_cannot_leave_server(self):
        data = b'not an image'
        (self.path / 'prototype.png').write_bytes(data)
        self.source['prototypes'][0]['sha256'] = hashlib.sha256(data).hexdigest()
        with self.assertRaisesRegex(ValueError, '^invalid_prototype_format$'):
            self.generate()
        self.build.assert_not_called()

    def test_truncated_output_is_rejected_even_if_json_parses(self):
        self.response['choices'][0]['finish_reason'] = 'length'
        with self.assertRaises(ValueError):
            self.generate()
