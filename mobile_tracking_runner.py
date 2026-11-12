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
from urllib.parse import urlsplit

DEVICE = 'ecbfd645'
APP = 'com.kfb.model'

def capture(adb, on_ready=lambda: None):
    def command(*args, timeout=6):
        return subprocess.check_output([adb,'-s',DEVICE,*args],timeout=timeout,stderr=subprocess.DEVNULL)
    def frame(folder):
        focus=command('shell','dumpsys','window').decode(errors='replace')
        if not any(APP+'/' in line for line in focus.splitlines() if 'mCurrentFocus=' in line):
            return None
        stamp=time.time()
        image=Path(folder)/'screen.png'
        image.write_bytes(command('exec-out','screencap','-p'))
        subprocess.run(['sips','-Z','420',str(image)],stdout=subprocess.DEVNULL,check=True,timeout=5)
        return {'time':stamp,'png':base64.b64encode(image.read_bytes()).decode()}
    events=[]
    frames=[]
    result={'events':events,'frames':frames,'note':'待实测'}
    with tempfile.TemporaryDirectory() as folder:
        initial=frame(folder)
        if initial: frames.append(initial)
        else:
            result['note']='请先打开智小白 3D，待实测'
            return result
        endpoint=os.getenv('TRACKING_EVENT_URL') or 'https://qa-event.gongfudou.com/event/zxbEvent?type=CS'
        url=urlsplit(endpoint)
        binary=os.getenv('TRACKING_MITMDUMP') or shutil.which('mitmdump')
        if url.scheme not in ('http','https') or not url.hostname or not binary:
            result['note']='Runner 缺少埋点上报地址或 mitmdump，待实测'
            return result
        old=command('shell','settings','get','global','http_proxy').decode().strip()
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
            on_ready()
            until=time.monotonic()+30
            while time.monotonic()<until and proxy.poll() is None:
                shot=frame(folder)
                if shot: frames.append(shot)
                time.sleep(2)
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
