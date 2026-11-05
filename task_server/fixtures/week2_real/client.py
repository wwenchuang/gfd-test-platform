"""Read only API-testing platform client; never logs payloads or credentials."""
import json
import time
import uuid
from urllib import error, request
from urllib.parse import urlsplit


class PlatformError(Exception):
    def __init__(self, status_code=None):
        self.status_code = status_code
        super().__init__('platform_request_failed' if status_code is None else 'platform_http_%s' % status_code)


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class PlatformClient:
    def __init__(self, origin, token, timeout=10):
        parts = urlsplit(origin)
        if (parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password
                or parts.path not in ('', '/') or parts.query or parts.fragment or not token or not 1 <= timeout <= 30):
            raise ValueError('invalid_platform_configuration')
        if parts.scheme == 'http' and (parts.hostname != '127.0.0.1' or parts.port is None):
            raise ValueError('invalid_platform_configuration')
        self.origin = origin.rstrip('/')
        self._token = token
        self.timeout = timeout
        self.opener = request.build_opener(request.ProxyHandler({}), _NoRedirect())
        self.events = []

    def _get(self, path):
        request_id = str(uuid.uuid4())
        req = request.Request(self.origin + path, headers={'Authorization': 'Bearer ' + self._token,
                              'X-Request-Id': request_id, 'Accept': 'application/json'}, method='GET')
        start = time.monotonic()
        status = None
        try:
            with self.opener.open(req, timeout=self.timeout) as response:
                status = response.status
                data = response.read(2097153)
                if len(data) > 2097152:
                    raise PlatformError(status)
                payload = json.loads(data)
                if status != 200 or not isinstance(payload, dict) or payload.get('ok') is not True or not isinstance(payload.get('data'), dict):
                    raise PlatformError(status)
                return payload['data']
        except error.HTTPError as exc:
            status = exc.code
            raise PlatformError(status) from None
        except (error.URLError, TimeoutError, OSError, ValueError, TypeError):
            raise PlatformError(status) from None
        finally:
            self.events.append({'method': 'GET', 'request_id': request_id, 'http_status': status,
                                'elapsed_ms': int((time.monotonic() - start) * 1000)})

    def get_case(self, execution_id, case_id):
        execution_id, case_id = str(uuid.UUID(execution_id)), str(uuid.UUID(case_id))
        base = '/api/api-testing/v1/executions/' + execution_id
        execution = self._get(base)
        case = self._get(base + '/cases/' + case_id)
        return {'execution': execution.get('execution'), 'case_result': case.get('case_result')}
