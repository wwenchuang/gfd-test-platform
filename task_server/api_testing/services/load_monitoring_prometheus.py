"""Bounded read-only Prometheus range queries. No inferred or zero-filled samples.

Allowed hosts must come from an administrator-approved source, never the query
request itself. Injected transports are trusted code and receive a pinned IP.
Bearer credentials require HTTPS; unauthenticated HTTP is for explicitly trusted
networks only and offers no confidentiality or integrity protection.

Range timestamps are PromQL EVALUATION times, not underlying scrape timestamps.
A range step does not prove exporter freshness or coverage. Empty results mean no
available evidence, never healthy/zero. This series-only interface rejects source
warnings/infos rather than silently discarding potential partial-result notices.
"""
import ipaddress
import json
import math
import queue
import re
import socket
import threading
import time
from urllib.parse import urlencode, urlsplit


class MonitoringQueryError(ValueError):
    """Safe public error; remote error bodies and credentials are never echoed."""


MAX_BYTES = 2 * 1024 * 1024
MAX_SERIES = 100
MAX_POINTS = 20000
TIMEOUT_SECONDS = 10
_DNS_SLOTS = threading.BoundedSemaphore(4)


def _resolve(host, port, deadline):
    # Bound callers even when the OS resolver stalls; at most four daemon resolver
    # workers can exist. Connection uses the returned numeric IP, never DNS again.
    if not _DNS_SLOTS.acquire(blocking=False):
        raise MonitoringQueryError('监控域名解析繁忙，请稍后重试')
    result = queue.Queue(maxsize=1)
    def work():
        try:
            result.put(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))
        except Exception:
            result.put(None)
        finally:
            _DNS_SLOTS.release()
    threading.Thread(target=work, daemon=True).start()
    try:
        records = result.get(timeout=max(.001, deadline - time.monotonic()))
    except queue.Empty:
        raise MonitoringQueryError('监控查询超时') from None
    if not records:
        raise MonitoringQueryError('无法解析监控主机')
    addresses = []
    for record in records:
        ip = ipaddress.ip_address(record[4][0])
        ip = ip.ipv4_mapped if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped else ip
        if ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast or ip.is_reserved or str(ip) == '100.100.100.200':
            raise MonitoringQueryError('监控地址属于禁止访问的网络范围')
        addresses.append(str(ip))
    return addresses[0]


def _transport(*, scheme, hostname, port, address, path, headers, deadline):
    # Reuse the executor's certificate-validating/SNI-preserving pinned sockets.
    from ..executor import _PinnedHttpConnection, _PinnedHttpsConnection
    def remaining():
        left = deadline - time.monotonic()
        if left <= 0:
            raise MonitoringQueryError('监控查询超时')
        return left
    cls = _PinnedHttpsConnection if scheme == 'https' else _PinnedHttpConnection
    conn = cls(hostname, port, address, remaining())
    try:
        conn.connect()
        sock = conn.sock
        sock.settimeout(remaining())
        conn.request('GET', path, headers=headers)
        sock.settimeout(remaining())
        response = conn.getresponse()
        # No redirects, proxy env, compressed responses, retries or remote error text.
        if response.status != 200:
            raise MonitoringQueryError('监控源查询失败，请检查连接和只读权限')
        if response.getheader('Content-Encoding', 'identity') != 'identity':
            raise MonitoringQueryError('监控响应编码不受支持')
        data = bytearray()
        while True:
            sock.settimeout(remaining())
            chunk = response.read1(min(65536, MAX_BYTES + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > MAX_BYTES:
                raise MonitoringQueryError('监控响应超过大小限制')
        remaining()
        return response.status, bytes(data)
    finally:
        conn.close()


class PrometheusMonitoringClient:
    def __init__(self, base_url, token='', *, allowed_hosts, transport=None):
        try:
            if (not isinstance(allowed_hosts, (set, frozenset, list, tuple))
                    or not allowed_hosts or len(allowed_hosts) > 100
                    or any(not isinstance(h, str) or not h or len(h) > 253 for h in allowed_hosts)):
                raise ValueError()
            parsed = urlsplit(base_url)
            host = parsed.hostname
            if (parsed.scheme not in {'http', 'https'} or not host
                    or parsed.username is not None or parsed.password is not None
                    or parsed.query or parsed.fragment or '?' in base_url or '#' in base_url
                    or any(ord(c) < 33 for c in base_url) or '\\' in base_url
                    or host not in allowed_hosts or '%' in base_url
                    or any(p in {'.', '..'} for p in parsed.path.split('/'))):
                raise ValueError()
            if token and parsed.scheme != 'https':
                raise MonitoringQueryError('携带监控令牌时必须使用 HTTPS')
            self._port = parsed.port or (443 if parsed.scheme == 'https' else 80)
            if not 1 <= self._port <= 65535 or not isinstance(token, str) or len(token) > 8192 or any(ord(c) < 32 or ord(c) == 127 for c in token):
                raise ValueError()
        except (TypeError, ValueError):
            raise MonitoringQueryError('监控地址未授权或格式无效') from None
        self._parsed = parsed
        self._token = token
        self._transport = transport or _transport

    def query_range(self, query, start, end, step_seconds):
        try:
            if (not isinstance(query, str) or not query.strip() or len(query.encode()) > 8192
                    or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
                           for v in (start, end, step_seconds))
                    or start < 0 or end < start or end - start > 86400 or not 1 <= step_seconds <= 3600
                    or (end - start) / step_seconds + 1 > MAX_POINTS):
                raise MonitoringQueryError('监控查询范围、步长或查询内容无效')
            # Match Prometheus millisecond precision in both request and bounds.
            start, end = math.floor(start * 1000) / 1000, math.floor(end * 1000) / 1000
            deadline = time.monotonic() + TIMEOUT_SECONDS
            p = self._parsed
            address = _resolve(p.hostname, self._port, deadline)
            path = p.path.rstrip('/') + '/api/v1/query_range?' + urlencode({
                'query': query, 'start': start, 'end': end, 'step': step_seconds, 'timeout': '8s'})
            headers = {'Accept': 'application/json', 'Accept-Encoding': 'identity'}
            if self._token:
                headers['Authorization'] = 'Bearer ' + self._token
            status, body = self._transport(scheme=p.scheme, hostname=p.hostname, port=self._port,
                address=address, path=path, headers=headers, deadline=deadline)
            if time.monotonic() > deadline:
                raise MonitoringQueryError('监控查询超时')
            if status != 200 or not isinstance(body, bytes) or len(body) > MAX_BYTES:
                raise MonitoringQueryError('监控源查询失败或响应超过限制')
            data = json.loads(body)
            if data.get('status') != 'success' or data.get('warnings') or data.get('infos'):
                raise MonitoringQueryError('监控源未返回完整成功结果')
            data = data['data']
            if data.get('resultType') != 'matrix' or not isinstance(data.get('result'), list) or len(data['result']) > MAX_SERIES:
                raise MonitoringQueryError('监控响应结构无效或序列过多')
            output, count = [], 0
            for series in data['result']:
                labels, values = series['metric'], series['values']
                if not isinstance(labels, dict) or len(labels) > 40 or any(not isinstance(k, str) or not isinstance(v, str) or len(k) > 128 or len(v) > 1024 for k, v in labels.items()) or not isinstance(values, list):
                    raise MonitoringQueryError('监控响应结构无效')
                points, previous = [], None
                count += len(values)
                if count > MAX_POINTS:
                    raise MonitoringQueryError('监控评估点超过限制')
                for pair in values:
                    if not isinstance(pair, list) or len(pair) != 2 or isinstance(pair[0], bool) or isinstance(pair[1], bool):
                        raise MonitoringQueryError('监控评估点格式无效')
                    timestamp = float(pair[0])
                    if not math.isfinite(timestamp) or not start <= timestamp <= end or previous is not None and timestamp <= previous:
                        raise MonitoringQueryError('监控评估时间无效')
                    value = None if pair[1] is None else float(pair[1])
                    points.append({'timestamp': timestamp, 'value': value if value is None or math.isfinite(value) else None})
                    previous = timestamp
                output.append({'labels': dict(labels), 'points': points})
            return output
        except MonitoringQueryError:
            raise
        except Exception:
            raise MonitoringQueryError('监控查询失败，请检查连接、权限与数据格式') from None


def build_query(metric, deployment, labels):
    """Host-only node_exporter templates: CPU busy%, memory used/total%.

    CPU excludes idle (iowait remains busy); memory uses MemAvailable, not MemFree.
    CPU is a trailing two-minute rate, not an instantaneous reading. It includes
    pre-run history at the start of a test and smooths short tests; ensure exporter
    samples exist before the run. Increasing query step does not change this rate
    window. Neither template proves scrape freshness: report it separately using
    source health/timestamp evidence, not query evaluation timestamps.
    Container/service metrics require a separate explicit denominator definition.
    """
    if deployment != 'host' or not isinstance(labels, dict) or not labels.get('instance') or len(labels) > 10:
        raise MonitoringQueryError('请选择主机维度并指定实例，不能替代容器指标')
    if any(not isinstance(k, str) or not re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_]*', k) or k in {'mode', 'cpu', '__name__'} or not isinstance(v, str) or len(v) > 512 for k, v in labels.items()):
        raise MonitoringQueryError('监控标签无效')
    selector = ','.join(k + '=' + json.dumps(v, ensure_ascii=False) for k, v in sorted(labels.items()))
    if metric == 'cpu_percent':
        return '100 * (1 - avg without (cpu, mode) (rate(node_cpu_seconds_total{' + selector + ',mode="idle"}[2m])))'
    if metric == 'memory_percent':
        return '100 * (1 - node_memory_MemAvailable_bytes{' + selector + '} / node_memory_MemTotal_bytes{' + selector + '})'
    raise MonitoringQueryError('监控指标模板不受支持')
