#!/usr/bin/env python3
"""只读反馈分类接口验证；只输出 HTTP 和业务码，不保存响应体。"""
import argparse
import json
import os
import sys
import urllib.request

URL = 'https://print.wisebeginner3d.com/app/print3d/api/v1/feedback/category/first?source=3D&bizCode=ZXB'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch_result(authorization):
    headers = {'Biz': 'ZXB', 'Accept': 'application/json'}
    if authorization:
        headers['Authorization'] = authorization
    request = urllib.request.Request(URL, headers=headers, method='GET')
    opener = urllib.request.build_opener(NoRedirect())
    with opener.open(request, timeout=15) as response:
        raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError('response too large')
        return response.status, json.loads(raw)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--scenario', required=True, choices=('anonymous', 'authenticated'))
    parser.add_argument('--expected-code', type=int)
    args = parser.parse_args(argv)
    auth = os.environ.get('W01_API_AUTHORIZATION', '') if args.scenario == 'authenticated' else ''
    if args.scenario == 'authenticated' and not auth:
        print('BLOCKED: authenticated scenario requires W01_API_AUTHORIZATION', file=sys.stderr)
        return 2
    expected = args.expected_code if args.expected_code is not None else (4009 if args.scenario == 'anonymous' else 0)
    try:
        status, body = fetch_result(auth)
        if not isinstance(body, dict) or type(body.get('code')) is not int:
            raise ValueError('invalid response')
        actual = body['code']
        data_is_list = isinstance(body.get('data'), list)
        passed = status == 200 and actual == expected and (args.scenario != 'authenticated' or data_is_list)
        print(json.dumps({'scenario': args.scenario, 'http_status': status, 'expected_code': expected,
                          'actual_code': actual, 'data_is_list': data_is_list, 'passed': passed}))
        return 0 if passed else 1
    except Exception:
        # Exceptions can contain response bodies, URLs, or headers; never echo them.
        print('ERROR: request failed or response is invalid; inspect server/network privately', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
