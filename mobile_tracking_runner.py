"""Capture a short telemetry window on the explicitly selected Android handset."""
import base64
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile
import time
from datetime import datetime
from urllib.parse import parse_qs, urlsplit
from mobile_tracking_observer import decode

DEVICE = 'ecbfd645'
APP = 'com.kfb.model'

def proxyman_events(folder, target, device_ip, since, until):
    cli='/Applications/Proxyman.app/Contents/MacOS/proxyman-cli'
    destination=Path(folder)/'qa-events.har'
    subprocess.run([cli,'export-log','-m','domains','-d',target.hostname,'-o',str(destination),'-f','har'],
                   check=True,timeout=15,stdout=subprocess.DEVNULL)
    deadline=time.monotonic()+8
    while True:
        try:
            entries=json.loads(destination.read_text())['log']['entries']
            break
        except (FileNotFoundError,json.JSONDecodeError):
            if time.monotonic()>=deadline:raise ValueError('未取得 Proxyman 导出结果')
            time.sleep(.25)
    events=[]
    matched=0
    for entry in entries:
        if entry.get('_remoteDeviceIP')!=device_ip:continue
        req=entry['request']
        url=urlsplit(req['url'])
        if req['method']!='POST' or (url.scheme,url.netloc,url.path)!=(target.scheme,target.netloc,target.path):continue
        sent=datetime.fromisoformat(entry['startedDateTime'].replace('Z','+00:00')).timestamp()
        if not since<=sent<=until:continue
        matched+=1
        try:body=json.loads(req.get('postData',{}).get('text',''))
        except ValueError:continue
        response=entry['response']
        content=response.get('content',{})
        try:
            raw=content.get('text','')
            reply=json.loads(base64.b64decode(raw) if content.get('encoding')=='base64' else raw)
        except ValueError:reply=None
        ack='回执未识别'
        if isinstance(reply,dict) and type(reply.get('code')) is int:
            ack='已接收' if 200<=response['status']<300 and reply['code']==0 else '上报失败'
        for row in decode(body,sent):
            events.append(dict(row,ack=ack,httpStatus=response.get('status'),businessCode=reply.get('code') if isinstance(reply,dict) and type(reply.get('code')) is int else None,requestType=parse_qs(url.query).get('type',[''])[0]))
    return matched,events

def capture(adb, on_ready=lambda: None, path=None, device=DEVICE):
    if device not in (DEVICE,"A9TU024622000978"): raise ValueError("未知手机")
    def command(*args, timeout=6):
        return subprocess.check_output([adb,'-s',device,*args],timeout=timeout,stderr=subprocess.DEVNULL)
    def screenshot():
        args = []
        if device == "A9TU024622000978":
            displays = command('shell','dumpsys','display').decode(errors='replace')
            active = re.findall(r'DisplayDeviceInfo\{[^\n]*?uniqueId="local:(\d+)"[^\n]*?type INTERNAL,[^\n]*? state ON,', displays)
            if len(set(active)) != 1: raise ValueError('无法确认荣耀当前使用的屏幕')
            args = ['-d', active[0]]
        data = command('exec-out','screencap',*args,'-p')
        if not data.startswith(b'\x89PNG'): raise ValueError('未取得有效截图')
        return data
    def frame(folder):
        focus=command('shell','dumpsys','window').decode(errors='replace')
        if not any(APP+'/' in line for line in focus.splitlines() if 'mCurrentFocus=' in line):
            return None
        stamp=time.time()
        image=Path(folder)/'screen.png'
        image.write_bytes(screenshot())
        subprocess.run(['sips','-Z','420',str(image)],stdout=subprocess.DEVNULL,check=True,timeout=5)
        return {'time':stamp,'png':base64.b64encode(image.read_bytes()).decode()}
    events=[]
    frames=[]
    result={'events':events,'frames':frames,'note':'待实测'}
    with tempfile.TemporaryDirectory() as folder:
        if path:
            launcher=command('shell','cmd','package','resolve-activity','--brief','-a','android.intent.action.MAIN','-c','android.intent.category.LAUNCHER',APP).decode().strip().splitlines()[-1]
            if not re.fullmatch(re.escape(APP)+r'/[A-Za-z0-9_.$]+',launcher):
                result['note']='无法确认 App 启动入口，待实测'
                return result
            command('shell','am','start','-n',launcher)
            time.sleep(1)
        initial=frame(folder)
        if initial: frames.append(initial)
        else:
            result['note']='请先打开智小白 3D，待实测'
            return result
        endpoint=os.getenv('TRACKING_EVENT_URL') or 'https://event.gongfudou.com/event/zxbEvent'
        url=urlsplit(endpoint)
        result["endpoint"]=url.hostname+url.path
        def observe(is_running):
            on_ready()
            performed=[]
            result['performed']=performed
            for label in path or []:
                # Only tap a unique, visible control belonging to the configured App.
                if frame(folder) is None:break
                image=Path(folder)/'label.png'
                image.write_bytes(screenshot())
                helper=Path(__file__).with_name('mobile_tracking_label.swift')
                try:
                    point=subprocess.check_output(['swift',str(helper),str(image),label],timeout=15,stderr=subprocess.DEVNULL).decode().strip()
                except (OSError,subprocess.SubprocessError):break
                if not re.fullmatch(r'\d+ \d+',point) or frame(folder) is None:break
                stamp=time.time()
                command('shell','input','tap',*point.split())
                performed.append({'label':label,'time':stamp})
                time.sleep(.5)
                shot=frame(folder)
                if shot:frames.append(shot)
            until=time.monotonic()+30
            while time.monotonic()<until and is_running():
                shot=frame(folder)
                if shot: frames.append(shot)
                time.sleep(2)
        binary=os.getenv('TRACKING_MITMDUMP') or shutil.which('mitmdump')
        if url.scheme not in ('http','https') or not url.hostname:
            result['note']='Runner 缺少埋点上报地址或 mitmdump，待实测'
            return result
        old=command('shell','settings','get','global','http_proxy').decode().strip()
        networks=command('shell','dumpsys','connectivity').decode(errors='replace')
        wifi=re.search(r'HttpProxy: \[([0-9.]+)\] (\d+)',networks)
        if wifi and old in ('',':0','null',wifi[1]+':'+wifi[2]):
            host,proxy_port=wifi.groups()
            interfaces=subprocess.check_output(['/sbin/ifconfig'],text=True)
            listener=subprocess.run(['/usr/sbin/lsof','-nP','-iTCP:'+proxy_port,'-sTCP:LISTEN'],capture_output=True,text=True)
            if not re.search(r'\binet '+re.escape(host)+r'\s',interfaces) or 'Proxyman' not in listener.stdout:
                result['note']='设备已配置其他 Wi-Fi 代理，待实测'
                return result
            ip=re.search(r'\binet ([0-9.]+)/',command('shell','ip','-4','addr','show','wlan0').decode())
            if not ip:
                result['note']='无法核对手机 Wi-Fi 地址，待实测'
                return result
            since=time.time()
            observe(lambda:True)
            try:
                matched,found=proxyman_events(folder,url,ip[1],since,time.time())
            except (OSError,ValueError,subprocess.SubprocessError):
                result['note']='Proxyman 记录读取失败，待实测'
                return result
            events.extend(found)
            result['diagnostics']={'source':'Proxyman','matched_path':matched}
            result['note']='已读取本机 Proxyman 的本轮事件' if found else '本轮未取得这台手机的可解析埋点，待实测'
            return result
        if not binary:
            result['note']='Runner 缺少 mitmdump，待实测'
            return result
        if old not in ('',':0','null'):
            result['note']='手机已有代理正在使用，待实测'
            return result
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        env=dict(os.environ,TRACKING_EVENT_URL=endpoint,TRACKING_DIAGNOSTICS=str(Path(folder)/'diag'),TRACKING_ROWS=str(Path(folder)/'events'))
        proxy=subprocess.Popen([binary,'--listen-host','127.0.0.1','-p',str(port),'--allow-hosts',
            '^'+re.escape(url.hostname)+r'(?::\d+)?$','-q','-s',str(Path(__file__).with_name('mobile_tracking_observer.py'))],
            env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        remote=None
        try:
            for _ in range(50):
                if proxy.poll() is not None: raise RuntimeError('采集组件启动失败')
                try:
                    with socket.create_connection(('127.0.0.1',port),timeout=.2):break
                except OSError:time.sleep(.1)
            else:raise RuntimeError('采集组件启动超时')
            remote=command('reverse','tcp:0',f'tcp:{port}').decode().strip()
            if not remote.isdigit():raise RuntimeError('无法绑定手机采集端口')
            assigned='127.0.0.1:'+remote
            command('shell','settings','put','global','http_proxy',assigned)
            observe(lambda:proxy.poll() is None)
            path=Path(env['TRACKING_ROWS'])
            if path.exists():
                events.extend(json.loads(line) for line in path.read_text().splitlines()[:256])
            diag=Path(env['TRACKING_DIAGNOSTICS'])
            markers=diag.read_text().splitlines() if diag.exists() else []
            result['diagnostics']={key:markers.count(key) for key in ('matched_path','matched_query','tls_failed_client')}
            if events: result['note']='采集结束'
            elif 'matched_query' in markers: result['note']='收到目标上报请求，但未解析到事件，待实测'
            elif 'tls_failed_client' in markers: result['note']='上报域名的客户端 TLS 握手失败，待实测'
            else: result['note']='观察时段内未读到匹配的上报请求，待实测'
        finally:
            try:
                if remote and command('shell','settings','get','global','http_proxy').decode().strip()=='127.0.0.1:'+remote:
                    command('shell','settings','put','global','http_proxy',':0')
                    if old=='null':command('shell','settings','delete','global','http_proxy')
            finally:
                proxy.terminate()
                try:proxy.wait(timeout=3)
                except subprocess.TimeoutExpired:proxy.kill();proxy.wait()
                if remote and remote.isdigit():command('reverse','--remove','tcp:'+remote)
    return result
