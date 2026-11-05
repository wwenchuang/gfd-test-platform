import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from client import PlatformClient, PlatformError
from evidence import parse_evidence, EvidenceError
from checks import run_checks


def example():
    # Synthetic unit-test input only. Actual practice requires platform evidence.
    return {'execution': {'id': 'a' * 32, 'state': 'DONE', 'case_results': [{'execution_case_id': 'b' * 32}]}, 'case_result': {'execution_case_id': 'b' * 32, 'status': 'PASSED', 'sanitized_result': {
        'status': 'PASSED',
        'request': {'method': 'GET', 'url': 'https://example.invalid/app/print3d/api/v1/feedback/category/first?bizCode=ZXB'},
        'response': {'status_code': 200, 'body': json.dumps({'code': 0, 'data': [
            {'sn':'sample', 'name':'example', 'children': [], 'order_related': False}]})},
        'assertions': [{'passed': True}]}}}


class EvidenceTests(unittest.TestCase):
    def test_platform_wrapper_and_real_shape(self):
        parsed = parse_evidence(example())
        self.assertEqual(parsed['body']['code'], 0)
        self.assertEqual(parsed['http_status'], 200)
        self.assertEqual(parsed['body']['data'][0]['sn'], 'sample')

    def test_execution_and_case_identity_must_match(self):
        with self.assertRaises(EvidenceError):
            parse_evidence(example(), 'c'*32, 'b'*32)
        with self.assertRaises(EvidenceError):
            parse_evidence(example(), 'a'*32, 'c'*32)
        payload = example()
        payload['execution']['case_results'] = [{'execution_case_id': 'c'*32}]
        with self.assertRaises(EvidenceError):
            parse_evidence(payload, 'a'*32, 'b'*32)

    def test_reject_wrong_endpoint_or_unfinished_execution(self):
        for field, value in [('method', 'POST'), ('url', 'https://example.invalid/other')]:
            payload = example()
            payload['case_result']['sanitized_result']['request'][field] = value
            with self.subTest(field=field), self.assertRaises(EvidenceError):
                parse_evidence(payload)
        payload = example()
        payload['execution']['state'] = 'RUNNING'
        with self.assertRaises(EvidenceError):
            parse_evidence(payload)

    def test_reject_non_json_and_empty_records(self):
        payload = example()
        payload['case_result']['sanitized_result']['response']['body'] = 'not json'
        with self.assertRaises(EvidenceError): parse_evidence(payload)
        payload = example()
        payload['case_result']['sanitized_result']['response']['body'] = '{"code":0,"data":[]}'
        checks = run_checks(parse_evidence(payload))
        self.assertTrue(any(item['status'] == 'blocked' for item in checks))

    def test_twelve_or_more_actual_data_processing_checks(self):
        checks = run_checks(parse_evidence(example()))
        self.assertGreaterEqual(len(checks), 12)
        self.assertTrue(all(item['status'] == 'passed' for item in checks), checks)
        self.assertEqual(len({item['id'] for item in checks}), len(checks))

    def test_bad_record_is_not_reported_as_pass(self):
        payload = example()
        payload['case_result']['sanitized_result']['response']['body'] = '{"code":0,"data":[{"sn":null,"name":"x","children":[],"order_related":false}]}'
        checks = run_checks(parse_evidence(payload))
        self.assertTrue(any(item['status'] == 'failed' for item in checks))

    def test_blocked_data_and_untrusted_assertion_type_in_cli(self):
        payload = example()
        payload['case_result']['sanitized_result']['response']['body'] = '{"code":0,"data":[]}'
        payload['case_result']['sanitized_result']['assertions'][0]['type'] = 'secret-sensitive-type'
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8') as source:
            json.dump(payload, source)
            source.flush()
            cmd = [sys.executable, str(Path(__file__).resolve().parents[1] / 'run.py'),
                   '--evidence', source.name, '--execution-id', 'a'*32, '--case-id', 'b'*32]
            result = subprocess.run(cmd, capture_output=True, text=True)
        report = json.loads(result.stdout)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(report['status'], 'blocked')
        self.assertNotIn('secret-sensitive-type', result.stdout)
        self.assertEqual(report['source_verification'], 'file_origin_not_independently_verified')

    def test_failed_source_is_failed_not_blocked(self):
        payload = example()
        payload['case_result']['status'] = 'FAILED'
        payload['case_result']['sanitized_result']['status'] = 'FAILED'
        payload['case_result']['sanitized_result']['assertions'][0]['passed'] = False
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8') as source:
            json.dump(payload, source)
            source.flush()
            cmd = [sys.executable, str(Path(__file__).resolve().parents[1] / 'run.py'),
                   '--evidence', source.name, '--execution-id', 'a'*32, '--case-id', 'b'*32]
            result = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)['status'], 'failed')


class ClientTests(unittest.TestCase):
    def test_requires_explicit_origin_and_rejects_sensitive_url(self):
        for url in ['', 'https://u:p@host.test', 'https://host.test/path', 'ftp://host.test', 'https://host.test?token=secret',
                    'http://example.invalid:8088', 'http://localhost:8088', 'http://127.0.0.1']:
            with self.subTest(url=url), self.assertRaises(ValueError): PlatformClient(url, 'token')
        self.assertEqual(PlatformClient('http://127.0.0.1:8088', 'token').origin, 'http://127.0.0.1:8088')

    def test_no_redirect_and_preserves_http_error_without_body(self):
        client = PlatformClient('https://example.invalid', 'secret-token')
        with patch.object(client.opener, 'open', side_effect=HTTPError('https://example.invalid',401,'bad',{},None)):
            with self.assertRaises(PlatformError) as error:
                client.get_case('a'*32, 'b'*32)
        self.assertEqual(error.exception.status_code, 401)
        self.assertNotIn('secret-token', str(error.exception))

    def test_rejects_ids_before_network(self):
        client = PlatformClient('https://example.invalid', 'secret-token')
        with patch.object(client.opener, 'open') as open_call:
            with self.assertRaises(ValueError): client.get_case('../bad', 'b'*32)
            open_call.assert_not_called()


if __name__ == '__main__': unittest.main()
