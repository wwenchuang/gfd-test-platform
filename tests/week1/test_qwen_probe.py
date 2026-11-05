import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from task_server.services import week1_probe as probe

class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / 'probe.sqlite3'
        self.addCleanup(self.tmp.cleanup)

    def test_history_is_owner_scoped_and_manual_review_is_independent(self):
        response = {'choices': [{'message': {'content': '左侧红色矩形，右侧蓝色圆'}}], 'usage': {'total_tokens': 9}}
        with patch.object(probe, '_request_model', return_value=response):
            record = probe.run_probe('u1', 'vision', self.db)
        self.assertEqual(record['status'], 'success')
        self.assertEqual(record['manual_check'], 'pending')
        self.assertEqual(probe.list_results('u2', self.db), [])
        with self.assertRaises(ValueError):
            probe.confirm('u2', record['id'], True, self.db)
        done = probe.confirm('u1', record['id'], False, self.db)
        self.assertEqual(done['manual_check'], 'failed')
        self.assertEqual(done['confirmed_by'], 'u1')
        self.assertTrue(done['confirmed_at'])

    def test_redact_answer_and_unsafe_endpoint_rejected(self):
        self.assertNotIn('sk-secret', probe.redact('sk-secret answer', 'sk-secret'))
        for url in ['https://evil.test/compatible-mode/v1', 'http://dashscope.aliyuncs.com/compatible-mode/v1', 'https://dashscope.aliyuncs.com.evil.test/compatible-mode/v1']:
            with self.assertRaises(ValueError):
                probe.validate_base_url(url)

    def test_failure_is_sanitized_and_persisted(self):
        with patch.object(probe, '_request_model', side_effect=RuntimeError('sk-private secret')):
            result = probe.run_probe('u1', 'text', self.db)
        self.assertEqual(result['status'], 'failed')
        self.assertNotIn('private', str(result))
        self.assertEqual(probe.list_results('u1', self.db)[0]['id'], result['id'])

    def test_fixed_request_uses_trusted_origin_and_disables_redirects(self):
        import json
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, limit):
                return json.dumps({'choices':[{'message':{'content':'ok'}}]}).encode()
        class Opener:
            def open(self, req, timeout):
                self.request = req
                return Response()
        opener = Opener()
        with patch.object(probe, 'get_dashscope_api_key', return_value='sk-test'), \
             patch.object(probe, 'get_dashscope_base_url', return_value='https://dashscope.aliyuncs.com/compatible-mode/v1'), \
             patch.object(probe, 'get_vl_model', return_value='qwen-vision'), \
             patch.object(probe.urllib.request, 'build_opener', return_value=opener) as build:
            probe._request_model('vision')
        self.assertIsInstance(build.call_args.args[0], probe.NoRedirect)
        self.assertIsNone(probe.NoRedirect().redirect_request(None, None, 302, None, None, 'https://evil.test'))
        body = json.loads(opener.request.data)
        self.assertEqual(body['model'], 'qwen-vision')
        self.assertTrue(body['messages'][0]['content'][0]['image_url']['url'].startswith('data:image/png;base64,'))
        self.assertEqual(opener.request.full_url, 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions')

    def test_text_answer_is_not_confused_with_successful_http(self):
        with patch.object(probe, '_request_model', return_value={'choices':[{'message':{'content':'无法确认'}}]}):
            result = probe.run_probe('u1', 'text', self.db)
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['text_check'], 'failed')

    def test_missing_key_is_classified_without_exposing_error(self):
        with patch.object(probe, 'get_dashscope_api_key', side_effect=ValueError('未配置 secret')):
            result = probe.run_probe('u1', 'text', self.db)
        self.assertEqual(result['error_code'], 'missing_key')
        self.assertNotIn('secret', str(result))

    def test_duplicate_requests_are_throttled(self):
        with patch.object(probe, '_request_model', return_value={'choices':[{'message':{'content':'CONNECTED'}}]}):
            probe.run_probe('u1', 'text', self.db)
            with self.assertRaises(probe.ProbeBusy):
                probe.run_probe('u1', 'text', self.db)

if __name__ == '__main__': unittest.main()
