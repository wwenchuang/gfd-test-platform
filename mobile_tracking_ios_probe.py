"""Explicit iPhone proxy diagnosis. This is not a business run or a heartbeat."""
import base64
from datetime import datetime
import ipaddress
import json
from pathlib import Path
import re
import secrets
import subprocess
import tempfile
import time
from urllib.parse import urlsplit


IPHONE = '00008110-000150CC21C2801E'
APP = 'com.kfb.3d'
PROXYMAN = '/Applications/Proxyman.app/Contents/MacOS/proxyman-cli'
TARGETS = ('event.gongfudou.com', 'qa-event.gongfudou.com')
WINDOW = 12
COUNTS = ('connectCount', 'postCount', 'receiptCount', 'acceptedCount', 'eventCount')


REASONS = {
    'device': '请在指定 iPhone 13 上验证智小白 3D。',
    'local': '未能核对 Mac 本机接口地址，请重试。',
    'timeout': '验证已超时，请检查手机与 Mac 连接后重试。',
    'large': '目标域名的代理导出过大，请缩短当前 Proxyman 会话后重试。',
    'incomplete': '未取得完整的代理导出，请检查 Proxyman 后重试。',
    'proxy': '无法从 Proxyman 读取指定域名，请确认 Mac 上的代理已启动。',
    'safari': '无法在指定 iPhone 打开 Safari，请确认手机已解锁并与 Mac 配对。',
    'ambiguous': '同一次探针出现多个代理来源，无法确认手机归属。',
    'address': '代理记录缺少可区分于 Mac 的手机 IPv4 来源。',
    'absent': '未见该 iPhone 的专属探针，请检查手机 Wi-Fi 的代理地址和端口。',
    'changed': '两次探针来源不一致，请确认 Wi-Fi 连接稳定后重试。',
    'restore': '未能切回智小白 3D，请确认此机已安装测试 App。',
    'receipt': '已验证手机代理来源，并取得目标接口的业务回执。',
    'post': '已看到目标接口 POST，尚未取得可识别的业务码。',
    'tunnel': '已看到目标域名的隧道连接，尚未取得目标接口请求和业务回执。',
    'no_event': '已验证手机代理来源，切回 App 后的观察窗口内未见目标域名上报。',
}
PUBLIC_REASONS = frozenset(REASONS.values())


class DiagnosisError(Exception):
    """Only fixed, colleague-facing explanations may be returned."""


def _launch(device, bundle, url=None, timeout=11):
    cmd = ['xcrun', 'devicectl', '--timeout', '9', 'device', 'process', 'launch', '--device', device]
    if url:
        cmd.extend(['--payload-url', url])
    cmd.extend([bundle, '--json-output', '-'])
    try:
        output = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, timeout=timeout)
        return json.loads(output).get('info', {}).get('outcome') == 'success'
    except (ValueError, OSError, subprocess.SubprocessError):
        return False


def _remaining(deadline, limit):
    left = deadline - time.monotonic()
    if left <= 0:
        raise DiagnosisError(REASONS['timeout'])
    return min(left, limit)


def _entries(domains, deadline):
    with tempfile.TemporaryDirectory(prefix='ios-proxy-probe-') as directory:
        path = Path(directory) / 'scoped.har'
        cmd = [PROXYMAN, 'export-log', '-m', 'domains']
        for domain in domains:
            cmd.extend(['-d', domain])
        cmd.extend(['-f', 'har', '-o', str(path)])
        try:
            subprocess.run(cmd, check=True, timeout=_remaining(deadline, 12), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            read_until = min(deadline, time.monotonic() + 4)
            while True:
                try:
                    if path.stat().st_size > 96 * 1024 * 1024:
                        raise DiagnosisError(REASONS['large'])
                    rows = json.loads(path.read_bytes())['log']['entries']
                    if not isinstance(rows, list):
                        raise ValueError()
                    return rows
                except (FileNotFoundError, ValueError, KeyError):
                    if time.monotonic() >= read_until:
                        raise DiagnosisError(REASONS['incomplete']) from None
                    time.sleep(.25)
        except (OSError, subprocess.SubprocessError):
            raise DiagnosisError(REASONS['proxy']) from None


def _mac_addresses():
    try:
        output = subprocess.check_output(['/sbin/ifconfig', '-a'], text=True, stderr=subprocess.DEVNULL, timeout=3)
    except (OSError, subprocess.SubprocessError):
        raise DiagnosisError(REASONS['local']) from None
    ips = set(re.findall(r'^\s*inet (\d+(?:\.\d+){3})\s', output, re.M))
    if not ips:
        raise DiagnosisError(REASONS['local'])
    return ips


def _peer(device, local, deadline):
    host = 'iphone-source-' + secrets.token_hex(12) + '.invalid'
    url = 'http://' + host + '/source/' + secrets.token_hex(12)
    if not _launch(device, 'com.apple.mobilesafari', url, timeout=_remaining(deadline, 11)):
        raise DiagnosisError(REASONS['safari'])
    time.sleep(_remaining(deadline, 3))
    for attempt in range(2):
        rows = _entries([host], deadline)
        peers = {r.get('_remoteDeviceIP') for r in rows if (r.get('request') or {}).get('url') == url}
        if peers:
            if len(peers) != 1:
                raise DiagnosisError(REASONS['ambiguous'])
            candidate = peers.pop()
            try:
                ip = ipaddress.IPv4Address(candidate)
                if candidate not in local and not any((ip.is_loopback, ip.is_unspecified, ip.is_multicast,
                                                      ip.is_link_local, ip.is_reserved)) and ip not in ipaddress.ip_network('198.18.0.0/15'):
                    return str(ip)
            except (ValueError, TypeError):
                pass
            raise DiagnosisError(REASONS['address'])
        if attempt == 0:
            time.sleep(_remaining(deadline, 2))
    raise DiagnosisError(REASONS['absent'])


def _body(node):
    value = node.get('text')
    if not isinstance(value, str):
        return {}
    try:
        if node.get('encoding') == 'base64':
            value = base64.b64decode(value, validate=True)
        data = json.loads(value)
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


def _counts(entries, peer, start, end):
    counts = dict.fromkeys(COUNTS, 0)
    for row in entries:
        if row.get('_remoteDeviceIP') != peer:
            continue
        req = row.get('request') or {}
        address = str(req.get('url') or '')
        try:
            ts = datetime.fromisoformat(row['startedDateTime'].replace('Z', '+00:00')).timestamp()
            url = urlsplit(address if '://' in address else '//' + address)
            if not start <= ts <= end or url.hostname not in TARGETS:
                continue
        except (ValueError, KeyError, AttributeError):
            continue
        if req.get('method') == 'CONNECT':
            counts['connectCount'] += 1
        if (req.get('method'), url.scheme, url.path) != ('POST', 'https', '/event/zxbEvent'):
            continue
        counts['postCount'] += 1
        events = _body(req.get('postData') or {}).get('evts')
        if isinstance(events, list):
            counts['eventCount'] += sum(isinstance(e, dict) and isinstance(e.get('type'), str) and bool(e['type']) for e in events)
        response = row.get('response') or {}
        code = _body(response.get('content') or {}).get('code')
        if type(code) is int:
            counts['receiptCount'] += 1
            if type(response.get('status')) is int and 200 <= response['status'] < 300 and code == 0:
                counts['acceptedCount'] += 1
    return counts


def probe(device, bundle):
    """Identify the exact handset twice, restore the test app, and report counts."""
    result = dict(kind='ios_proxy_probe', stage='preflight', sourceVerified=False, probeMatches=0,
                  appRestored=False, observationComplete=False, windowSeconds=0, reason='',
                  **dict.fromkeys(COUNTS))
    if device != IPHONE or bundle != APP:
        result['reason'] = REASONS['device']
        return result
    try:
        local = _mac_addresses()
    except DiagnosisError as exc:
        result['reason'] = str(exc)
        return result
    deadline = time.monotonic() + 85
    result['stage'] = 'source'
    try:
        first = _peer(device, local, deadline)
        result['probeMatches'] = 1
        if _peer(device, local, deadline) != first:
            raise DiagnosisError(REASONS['changed'])
        result.update(probeMatches=2, sourceVerified=True)
    except DiagnosisError as exc:
        result['reason'] = str(exc)
    finally:
        start = time.time()
        result['appRestored'] = _launch(device, bundle)
    if not result['appRestored']:
        result.update(stage='restore', reason=REASONS['restore'])
        return result
    if not result['sourceVerified']:
        return result
    result['stage'] = 'observe'
    try:
        time.sleep(_remaining(deadline, WINDOW))
        end = time.time()
        result['windowSeconds'] = max(0, round(end - start))
        result.update(_counts(_entries(TARGETS, deadline), first, start, end))
    except DiagnosisError as exc:
        result['reason'] = str(exc)
        return result
    result.update(observationComplete=True, stage='complete')
    if result['receiptCount']:
        result['reason'] = REASONS['receipt']
    elif result['postCount']:
        result['reason'] = REASONS['post']
    elif result['connectCount']:
        result['reason'] = REASONS['tunnel']
    else:
        result['reason'] = REASONS['no_event']
    return result
