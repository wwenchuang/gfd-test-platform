"""Run a bounded iPhone tracking session over the selected USB WDA and Proxyman."""
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import urllib.request
from urllib.parse import urlsplit

from mobile_tracking_runner import NATIVE_IDS, TEXT_LABELS, WITNESS_SECONDS, proxyman_events, scoped_events

PROXY_CLI = Path('/Applications/Proxyman.app/Contents/MacOS/proxyman-cli')
ALLOWED_LABELS = set(NATIVE_IDS) | TEXT_LABELS
ELEMENT_ID = 'element-6066-11e4-a52e-4f735466cecf'


def endpoint():
    value = urlsplit(os.getenv('TRACKING_EVENT_URL') or 'https://event.gongfudou.com/event/zxbEvent')
    if value.scheme != 'https' or not value.hostname or not value.path or value.username or value.password:
        raise ValueError('缺少有效的埋点 HTTPS 地址')
    return value


def wifi_address(status):
    if not isinstance(status, dict) or status.get('ready') is not True:
        raise ValueError('WDA 未就绪，请解锁指定 iPhone 并启动 WDA')
    details = status.get('ios')
    address = details.get('ip') if isinstance(details,dict) else None
    try:
        ip = ipaddress.IPv4Address(address)
    except (ipaddress.AddressValueError, TypeError):
        raise ValueError('WDA 未提供这部 iPhone 的 Wi-Fi IPv4 地址') from None
    if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified:
        raise ValueError('iPhone 的 Wi-Fi 地址不可用于核对代理记录')
    return str(ip)


def readiness(device, read, collect=proxyman_events):
    try:
        status = read(device, '/status')
        ip = wifi_address(status)
    except (RuntimeError, OSError, subprocess.SubprocessError):
        return {'ready':False,'stage':'wda','reason':'已连接 iPhone，WDA 无法访问，请解锁并检查 WDA'}
    except ValueError as exc:
        return {'ready':False,'stage':'wda','reason':str(exc)}
    if not PROXY_CLI.is_file():
        return {'ready':False,'stage':'proxy','reason':'Mac 未安装可导出回执的 Proxyman'}
    try:
        with tempfile.TemporaryDirectory() as folder:
            now = time.time()
            _, rows = collect(folder, endpoint(), ip, now - 120, now)
    except (OSError, ValueError, subprocess.SubprocessError):
        return {'ready':False,'stage':'proxy','reason':'无法读取这部 iPhone 的目标域名代理记录'}
    if not any(type(e.get('httpStatus')) is int and type(e.get('businessCode')) is int for e in rows):
        return {'ready':True,'stage':'first_capture','reason':'可开始，尚待核对本机首笔回执'}
    return {'ready':True,'stage':'ready','reason':'已核对 WDA 和该 iPhone 的埋点回执'}


def wda_call(port, method, path, payload=None):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(f'http://127.0.0.1:{port}'+path, method=method, data=data,
                                 headers={'Content-Type':'application/json'})
    with opener.open(req, timeout=8) as res:
        body = res.read(2_000_001)
    if len(body) > 2_000_000:
        raise ValueError('WDA 返回数据过大')
    reply = json.loads(body)
    if not isinstance(reply,dict) or isinstance(reply.get('value'),dict) and reply['value'].get('error'):
        raise ValueError('WDA 操作未成功')
    return reply


def require_token(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value):
        raise ValueError('WDA 未返回有效的操作标识')
    return value


def capture(device, bundle, path, ready, forward, call=wda_call, collect=proxyman_events):
    if not isinstance(path,list) or not 1<=len(path)<=5 or any(not isinstance(label,str) or label not in ALLOWED_LABELS for label in path):
        raise ValueError('iPhone 操作路径不在允许范围')
    target = endpoint()
    result = {'device':device,'endpoint':target._replace(query='',fragment='').geturl(),
              'performed':[],'frames':[],'events':[],'note':'待实测'}
    with forward(device) as port:
        def request(method, route, payload=None):
            return call(port, method, route, payload)
        def get(path):
            return request('GET',path).get('value')
        address = wifi_address(get('/status'))
        # Keep the device on its current screen if the target app is already open.
        reply = request('POST','/session',{'capabilities':{'alwaysMatch':{
            'bundleId':bundle,'forceAppLaunch':False,'shouldTerminateApp':False}}})
        value = reply.get('value')
        sid = require_token(reply.get('sessionId') or (value.get('sessionId') if isinstance(value,dict) else None))
        session = '/session/'+sid
        try:
            ready()
            for label in path:
                active = get('/wda/activeAppInfo') or {}
                if active.get('bundleId') != bundle:
                    result['note']='当前前台并非目标 iOS 应用，待实测'
                    return result
                # Labels come only from ALLOWED_LABELS, never free-form selectors.
                matches = request('POST',session+'/elements',{'using':'predicate string',
                    'value':f'(label == "{label}" OR name == "{label}") AND visible == 1 AND enabled == 1'}).get('value')
                if not isinstance(matches,list) or len(matches)!=1:
                    result['note']='在目标 iOS 应用未找到唯一可见的「'+label+'」，待实测'
                    return result
                identifier = require_token(matches[0].get(ELEMENT_ID) or matches[0].get('ELEMENT'))
                if (get('/wda/activeAppInfo') or {}).get('bundleId') != bundle:
                    result['note']='iPhone 前台已切换，待实测'
                    return result
                stamp = time.time()
                request('POST',session+'/element/'+identifier+'/click',{})
                if result['performed']:result['performed'][-1]['until']=stamp
                result['performed'].append({'label':label,'time':stamp,'source':'WDA 可见控件'})
                time.sleep(.7)
            time.sleep(WITNESS_SECONDS)
            until = time.time()
            result['performed'][-1]['until']=until
            if wifi_address(get('/status')) != address:
                result['note']='本次操作中 iPhone 的 Wi-Fi 地址发生变化，待实测'
                return result
            try:
                with tempfile.TemporaryDirectory() as folder:
                    matched, rows = collect(folder,target,address,result['performed'][0]['time'],until)
            except (OSError, ValueError, subprocess.SubprocessError):
                result['note']='已执行 iPhone 操作，Proxyman 未返回可解析的本轮记录，待实测'
                return result
            result['events']=scoped_events(rows,result['performed'],path)
            result['diagnostics']={'source':'Proxyman','matched_path':matched}
            result['note']='已取回该 iPhone 的本轮接口证据' if result['events'] else '本次 iPhone 操作窗口未取得对应事件，待实测'
            return result
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
            result['note']='iPhone 控件操作或 WDA 连接中断，已保留本次操作记录，待实测'
            return result
        finally:
            try:
                request('DELETE',session)
            except (OSError, RuntimeError, ValueError):
                result['diagnostics']={**result.get('diagnostics',{}),'session_cleanup':'WDA 会话关闭未确认'}
