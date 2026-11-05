"""Inspect one existing platform execution; never executes a business API."""
import argparse
import json
import os
import sys
from pathlib import Path

from checks import run_checks
from client import PlatformClient, PlatformError
from evidence import parse_evidence, EvidenceError


def main():
    parser = argparse.ArgumentParser(description='基于真实平台执行证据的第 2 周数据处理练习')
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--evidence', help='手工提供的最小脱敏证据 JSON 文件')
    source.add_argument('--platform-origin', help='明确指定 API 测试平台地址，凭证取环境变量')
    parser.add_argument('--execution-id', required=True)
    parser.add_argument('--case-id', required=True)
    args = parser.parse_args()
    client = None
    try:
        if args.evidence:
            with Path(args.evidence).open('rb') as source_file:
                raw = source_file.read(2097153)
            if len(raw) > 2097152: raise EvidenceError('evidence_too_large')
            payload = json.loads(raw)
            mode = 'existing_evidence_file'
        else:
            token = os.environ.get('WEEK2_PLATFORM_TOKEN', '')
            if not token: raise EvidenceError('platform_token_missing')
            allowed = os.environ.get('WEEK2_ALLOWED_PLATFORM_ORIGIN', '')
            if not allowed or args.platform_origin.rstrip('/') != allowed.rstrip('/'):
                raise EvidenceError('platform_origin_not_allowed')
            client = PlatformClient(args.platform_origin, token)
            payload = client.get_case(args.execution_id, args.case_id)
            mode = 'platform_read_only'
        parsed = parse_evidence(payload, args.execution_id, args.case_id)
        checks = run_checks(parsed)
        allowed_types = {'status_code', 'json_path', 'schema'}
        assertion_results = [{'type': item.get('type') if item.get('type') in allowed_types else 'other', 'passed': item['passed']}
                             for item in parsed['assertions']]
        failed = parsed['source_status'] == 'FAILED' or any(not item['passed'] for item in assertion_results) or any(item['status'] == 'failed' for item in checks)
        blocked = any(item['status'] == 'blocked' for item in checks)
        status = 'failed' if failed else 'blocked' if blocked else 'passed'
        output = {'status': status, 'source': mode,
                  'source_verification': 'file_origin_not_independently_verified' if args.evidence else 'platform_api_response',
                  'execution_id': parsed['execution_id'], 'execution_case_id': parsed['case_id'],
                  'business_http_status': parsed['http_status'], 'original_assertions': assertion_results,
                  'checks': checks, 'total': len(checks), 'platform_get_requests': len(client.events) if client else 0,
                  'business_requests': 0, 'platform_requests': client.events if client else [],
                  'cleanup': 'not_applicable_no_business_data_created',
                  'scope': 'local_data_processing', 'business_contract': 'not_business_reviewed'}
        print(json.dumps(output, ensure_ascii=False))
        return 0 if status == 'passed' else 1 if status == 'failed' else 2
    except (OSError, ValueError, TypeError, EvidenceError, PlatformError) as exc:
        # Deliberately do not expose paths, raw payloads, URLs, credentials or exception strings.
        category = ('platform_http_error' if isinstance(exc, PlatformError) else
                    'evidence_or_configuration_invalid')
        status = 'failed' if isinstance(exc, EvidenceError) and exc.source_failed else 'blocked'
        output = {'status': status, 'error': category, 'checks': [],
                  'business_requests': 0, 'platform_requests': client.events if client else []}
        print(json.dumps(output, ensure_ascii=False))
        return 1 if status == 'failed' else 2


if __name__ == '__main__':
    sys.exit(main())
