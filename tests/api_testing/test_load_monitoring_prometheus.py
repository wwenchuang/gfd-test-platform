import json
import socket

import pytest

from task_server.api_testing.services import load_monitoring_prometheus as m


def payload(values=None):
    return json.dumps({'status': 'success', 'data': {'resultType': 'matrix', 'result': [
        {'metric': {'instance': 'host-a'}, 'values': values or [[10, '1.5'], [15, 'NaN'], [20, '+Inf']]}
    ]}}).encode()


@pytest.fixture
def resolver(monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('10.0.0.8', 9090))])


def client(transport):
    return m.PrometheusMonitoringClient('http://metrics.internal:9090/prometheus', allowed_hosts={'metrics.internal'}, transport=transport)


def test_normalizes_missing_without_zero(resolver):
    calls = []
    def transport(**kw):
        calls.append(kw)
        return 200, payload()
    assert client(transport).query_range('up', 10, 20, 5) == [{'labels': {'instance': 'host-a'}, 'points': [
        {'timestamp': 10.0, 'value': 1.5}, {'timestamp': 15.0, 'value': None}, {'timestamp': 20.0, 'value': None}]}]
    assert calls[0]['address'] == '10.0.0.8'
    assert calls[0]['path'].startswith('/prometheus/api/v1/query_range?')


@pytest.mark.parametrize('url', ['file:///tmp/x', 'http://u:p@metrics.internal', 'http://metrics.internal?q=x', 'http://metrics.internal#x', 'http://other.internal'])
def test_rejects_unapproved_url(url):
    with pytest.raises(m.MonitoringQueryError):
        m.PrometheusMonitoringClient(url, allowed_hosts={'metrics.internal'})


def test_no_default_authorization():
    with pytest.raises(m.MonitoringQueryError):
        m.PrometheusMonitoringClient('http://metrics.internal', allowed_hosts=set())


@pytest.mark.parametrize('address', ['127.0.0.1', '169.254.169.254', '0.0.0.0', '::1', '::ffff:127.0.0.1', '224.0.0.1'])
def test_denies_sensitive_addresses_even_allowlisted(monkeypatch, address):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (address, 9090))])
    with pytest.raises(m.MonitoringQueryError):
        client(lambda **kw: pytest.fail('must not connect')).query_range('up', 10, 20, 5)


@pytest.mark.parametrize('args', [('up', 20, 10, 5), ('up', 0, 90000, 5), ('up', 0, 20, 0), ('x' * 8193, 0, 20, 5), ('up', float('nan'), 20, 5)])
def test_bounds_input(args):
    with pytest.raises(m.MonitoringQueryError):
        client(lambda **kw: pytest.fail('must not connect')).query_range(*args)


@pytest.mark.parametrize('response', [(302, b''), (500, b'secret-token'), (200, b'x' * (2 * 1024 * 1024 + 1)), (200, b'not json'), (200, b'{"status":"error","error":"secret-token"}')])
def test_sanitizes_errors(resolver, response):
    with pytest.raises(m.MonitoringQueryError) as error:
        client(lambda **kw: response).query_range('secret-query', 10, 20, 5)
    assert 'secret' not in str(error.value)


def test_rejects_bad_points(resolver):
    with pytest.raises(m.MonitoringQueryError):
        client(lambda **kw: (200, payload([[10, 'nonsense']]))).query_range('up', 10, 20, 5)


def test_template_scope_and_escaping():
    query = m.build_query('cpu_percent', 'host', {'instance': 'host"a', 'job': 'node'})
    assert 'mode="idle"' in query and 'rate(node_cpu_seconds_total' in query
    assert 'host\\"a' in query
    assert 'MemAvailable' in m.build_query('memory_percent', 'host', {'instance': 'a'})
    with pytest.raises(m.MonitoringQueryError):
        m.build_query('cpu_percent', 'container', {'instance': 'a'})
    with pytest.raises(m.MonitoringQueryError):
        m.build_query('cpu_percent', 'host', {})


def test_rejects_metadata_carrier_address(monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('100.100.100.200', 9090))])
    with pytest.raises(m.MonitoringQueryError):
        client(lambda **kw: pytest.fail('metadata must not connect')).query_range('up', 10, 20, 5)


def test_default_transport_pins_connection_and_never_follows_redirect(resolver, monkeypatch):
    from task_server.api_testing import executor
    addresses = []
    class Connection:
        def __init__(self, hostname, port, address, timeout):
            addresses.append((hostname, address))
            self.sock = self
        def connect(self): pass
        def settimeout(self, timeout): pass
        def request(self, *args, **kwargs): pass
        def getresponse(self): return type('Response', (), {'status': 302})()
        def close(self): pass
    monkeypatch.setattr(executor, '_PinnedHttpConnection', Connection)
    with pytest.raises(m.MonitoringQueryError):
        m.PrometheusMonitoringClient('http://metrics.internal', allowed_hosts={'metrics.internal'}).query_range('up', 10, 20, 5)
    assert addresses == [('metrics.internal', '10.0.0.8')]


@pytest.mark.parametrize('values', [[[10, True]], [[10, '1'], [10, '2']], [[9, '1']], [[21, '1']]])
def test_rejects_invalid_sample_types_or_times(resolver, values):
    with pytest.raises(m.MonitoringQueryError):
        client(lambda **kw: (200, payload(values))).query_range('up', 10, 20, 5)


def test_series_limit(resolver):
    body = json.dumps({'status': 'success', 'data': {'resultType': 'matrix', 'result': [{'metric': {}, 'values': []}] * 101}}).encode()
    with pytest.raises(m.MonitoringQueryError):
        client(lambda **kw: (200, body)).query_range('up', 10, 20, 5)


@pytest.mark.parametrize('hosts', ['not-metrics.internal.example', b'metrics.internal', {'metrics.internal': True}, [None]])
def test_allowlist_requires_explicit_host_collection(hosts):
    with pytest.raises(m.MonitoringQueryError):
        m.PrometheusMonitoringClient('http://metrics.internal', allowed_hosts=hosts)


def test_bearer_credentials_require_tls():
    with pytest.raises(m.MonitoringQueryError):
        m.PrometheusMonitoringClient('http://metrics.internal', token='private-token', allowed_hosts={'metrics.internal'})


def test_tls_token_sent_only_in_header(resolver):
    calls = []
    def transport(**kw):
        calls.append(kw)
        return 200, payload()
    m.PrometheusMonitoringClient('https://metrics.internal', token='private-token', allowed_hosts={'metrics.internal'}, transport=transport).query_range('up', 10, 20, 5)
    assert calls[0]['headers']['Authorization'] == 'Bearer private-token'
    assert 'private-token' not in calls[0]['path']


@pytest.mark.parametrize('result', [[], [{'metric': {'instance': 'a'}, 'values': []}]])
def test_empty_result_is_not_fabricated(resolver, result):
    body = json.dumps({'status': 'success', 'data': {'resultType': 'matrix', 'result': result}}).encode()
    actual = client(lambda **kw: (200, body)).query_range('up', 10, 20, 5)
    assert actual == ([] if not result else [{'labels': {'instance': 'a'}, 'points': []}])


@pytest.mark.parametrize('notice', ['warnings', 'infos'])
def test_partial_or_annotated_success_is_not_silently_accepted(resolver, notice):
    response = json.loads(payload())
    response[notice] = ['remote details containing secret-token']
    with pytest.raises(m.MonitoringQueryError) as error:
        client(lambda **kw: (200, json.dumps(response).encode())).query_range('up', 10, 20, 5)
    assert 'secret-token' not in str(error.value)


def test_fractional_query_is_normalized_to_prometheus_milliseconds(resolver):
    from urllib.parse import urlsplit, parse_qs
    def transport(**kw):
        query = parse_qs(urlsplit(kw['path']).query)
        assert float(query['start'][0]) == 1000.123
        return 200, payload([[1000.123, '2']])
    result = client(transport).query_range('up', 1000.123456, 1000.123456, 5)
    assert result[0]['points'][0]['value'] == 2
