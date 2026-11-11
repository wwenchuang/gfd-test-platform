"""Run one fixed authenticated feedback category GET. stdout contains assertion evidence only."""
import json
import sys
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

URL = 'https://print.wisebeginner3d.com/app/print3d/api/v1/feedback/category/first?source=3D&bizCode=ZXB'
BODY_LIMIT = 1024 * 1024


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def summarize(status, body, request_id):
    code = body.get('code') if isinstance(body, dict) else None
    actual_code = code if type(code) is int else None
    data_list = isinstance(body, dict) and isinstance(body.get('data'), list)
    return {'http_status': status, 'actual_code': actual_code, 'expected_code': 0,
            'data_is_list': data_list, 'request_id': request_id,
            'checks': {'http_200': status == 200, 'code_zero': actual_code == 0, 'data_list': data_list}}


def main():
    request_id = str(uuid.uuid4())
    try:
        authorization = json.load(sys.stdin)['authorization']
        if not isinstance(authorization, str) or not authorization:
            raise ValueError()
        request = Request(URL, method='GET', headers={'Biz':'ZXB', 'Accept':'application/json',
                          'Authorization':authorization, 'X-Request-Id':request_id})
        opener = build_opener(ProxyHandler({}), NoRedirect())
        try:
            response = opener.open(request, timeout=15)
        except HTTPError as error:
            response = error
        with response:
            raw = response.read(BODY_LIMIT + 1)
            if len(raw) > BODY_LIMIT:
                raise ValueError()
            try:
                body = json.loads(raw)
            except (ValueError, UnicodeError):
                body = None
            evidence = summarize(response.status, body, request_id)
        status, reason = ('passed', None) if all(evidence['checks'].values()) else ('failed', 'business')
        if evidence['http_status'] in (401, 403) or evidence['actual_code'] == 4009:
            status, reason = 'blocked', 'authentication'
    except (TimeoutError, URLError) as exc:
        timed_out = isinstance(exc, TimeoutError) or isinstance(getattr(exc, 'reason', None), TimeoutError)
        status, reason, evidence = ('timed_out', 'timeout', {}) if timed_out else ('blocked', 'environment', {})
    except Exception:
        status, reason, evidence = 'blocked', 'environment', {}
    print(json.dumps({'status':status, 'error_kind':reason, 'evidence':evidence}))
    return {'passed':0, 'failed':1}.get(status, 2)


if __name__ == '__main__':
    sys.exit(main())
