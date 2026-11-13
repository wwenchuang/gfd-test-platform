"""Capture a short telemetry window on the explicitly selected Android handset."""
import base64
import json
import os
from pathlib import Path
import re
import shutil
import secrets
import xml.etree.ElementTree as ET
import socket
import subprocess
import tempfile
import time
from datetime import datetime
from urllib.parse import urlsplit
from mobile_tracking_observer import matching_request, request_records, receipt

DEVICE = 'ecbfd645'
APP = 'com.kfb.model'

def proxyman_events(folder, target, device_ip, since, until):
    cli='/Applications/Proxyman.app/Contents/MacOS/proxyman-cli'
    destination=Path(folder)/'tracking-events.har'
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
        if not matching_request(target,url,req.get('method')):continue
        sent=datetime.fromisoformat(entry['startedDateTime'].replace('Z','+00:00')).timestamp()
        if not since<=sent<=until:continue
        matched+=1
        post=req.get('postData',{})
        raw=post.get('text','')
        try:
            if post.get('encoding')=='base64':raw=base64.b64decode(raw,validate=True).decode('utf-8')
            body=json.loads(raw)
        except (ValueError,UnicodeError,TypeError):continue
        response=entry.get('response',{})
        content=response.get('content',{})
        try:
            payload=content.get('text','')
            reply=json.loads(base64.b64decode(payload) if content.get('encoding')=='base64' else payload)
        except (ValueError,TypeError):reply=None
        details=receipt(response.get('status'),reply)
        events.extend(dict(row,**details) for row in request_records(body,sent,url,raw))
    return matched,events

# Resource IDs were observed on the actual App. Duplicate/dynamic feed items and
# arbitrary AI text are never transformed into coordinate guesses.
NATIVE_IDS = {
    '首页': ('app_tab_home',), '成长': ('app_tab_growth',),
    '创作': ('iv_tab_modeling',), '广场': ('app_tab_square',),
    '我的': ('app_tab_personal',), '设备': ('iv_device_icon_bg',),
    '搜索': ('iv_search_icon','model_plaza_search'), '消息': ('cl_notification_icon',),
    '扫码': ('model_home_scan_button',), '勋章挑战': ('model_home_challenge',),
    '印章': ('layout_text_stamp',), '标牌': ('layout_placard_model',),
    '掐丝珐琅': ('layout_qiansi_enamel',), 'AI建模': ('layout_modeling_ai',),
    '导入打印': ('layout_model_import',), '查看全部': ('ivMoreModelSeries',),
    '推荐': ('model_plaza_recommend_tab',), '最新': ('model_plaza_latest_tab',),
}
TEXT_LABELS = set('主题专区 推荐 热门 最新 玩具 收藏 作品 导入 挂件 书签 吧唧 角色手办 动物萌宠 炫酷载具 模型导入 魔法涂鸦 魔法叠叠乐 魔法捏捏乐 魔法积木 微信导入 本地导入 开始创作'.split())
WITNESS_SECONDS = 10


def scoped_events(rows,performed,path):
    if not path or len(performed)!=len(path) or [x['label'] for x in performed]!=path:return []
    last=performed[-1]
    if 'until' not in last:return []
    return [dict(row,actionIndex=len(path)-1) for row in rows if
            row.get('event') not in ('AOP','AS','AE','AEP') and
            type(row.get('time')) in (int,float) and last['time']<=row['time']<=last['until'] and
            type(row.get('sent')) in (int,float) and last['time']<=row['sent']<=last['until']]


def focused_activity(command):
    windows=command('shell','dumpsys','window').decode(errors='replace')
    current=re.findall(r'mCurrentFocus=Window\{[^}\n]*?\bu\d+ ([^ }\n]+)',windows)
    return current[0] if len(current)==1 and re.fullmatch(re.escape(APP)+r'/[\w.$]+',current[0]) else None


def intersect(boxes):
    if not boxes:return None
    left,top=max(b[0] for b in boxes),max(b[1] for b in boxes)
    right,bottom=min(b[2] for b in boxes),min(b[3] for b in boxes)
    if left<right and top<bottom:return (left,top,right,bottom)


def native_bounds(dump,ids,activity):
    sections=re.split(r'(?m)^  ACTIVITY ',dump)
    shorthand=activity.replace(APP+'/'+APP+'.',APP+'/.',1)
    matches=[s for s in sections if s.startswith((activity+' ',shorthand+' ')) and 'View Hierarchy:' in s]
    if len(matches)!=1:return []
    stack=[];boxes=[]
    for line in matches[0].partition('View Hierarchy:')[2].splitlines():
        m=re.match(r'^( +)[\w.$]+\{[\da-f]+ ([VIG])\S* \S+ (-?\d+),(-?\d+)-(-?\d+),(-?\d+)',line)
        if not m:continue
        depth=len(m[1])
        while stack and stack[-1][0]>=depth:stack.pop()
        _,px,py,clip=stack[-1] if stack else (0,0,0,None)
        x,y,right,bottom=map(int,m.group(3,4,5,6))
        bounds=(px+x,py+y,px+right,py+bottom)
        visible=m[2]=='V' and (not stack or clip is not None)
        box=intersect([bounds,clip] if clip else [bounds]) if visible else None
        stack.append((depth,bounds[0],bounds[1],box))
        rid=re.search(r'\b(?:app|com\.kfb\.model):id/(\w+)\b',line)
        if box and rid and rid[1] in ids:boxes.append(box)
    return boxes


def xml_bounds(xml,label,resource_id=None):
    root=ET.fromstring(xml)
    boxes=[]
    for n in root.iter('node'):
        if n.get('package')!=APP or n.get('enabled')=='false' or n.get('visible-to-user')=='false':continue
        if resource_id and n.get('resource-id')!=resource_id:continue
        if not any(label in (n.get(key) or '').splitlines() for key in ('text','content-desc')):continue
        m=re.fullmatch(r'\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]',n.get('bounds',''))
        if m:boxes.append(tuple(map(int,m.groups())))
    return boxes


def android_hierarchy(device):
    # The stock shell dumper waits for an idle window. The animated home feed
    # never goes idle on Honor; UiAutomator2 can read the live accessibility tree.
    import uiautomator2
    phone=uiautomator2.connect(device)
    try:
        return phone.dump_hierarchy()
    finally:
        phone.stop_uiautomator(wait=False)


def find_control(command,label,device=None,previous_label=None):
    activity=focused_activity(command)
    if not activity:return None
    home_category=label=='热门' or (previous_label=='首页' and label in ('推荐','最新','玩具'))
    ids=() if home_category else NATIVE_IDS.get(label,())
    boxes=[]
    if ids:
        dump=command('shell','dumpsys','activity',activity).decode(errors='replace')
        boxes=native_bounds(dump,ids,activity)
    source='App 控件 ID'
    if not boxes and label in TEXT_LABELS:
        resource_id=APP+':id/tv_tab_title' if home_category else None
        hierarchy=None
        if device:
            try:
                hierarchy=android_hierarchy(device)
            except Exception:
                # Stock Android remains usable on runners without the optional
                # driver, or when another accessibility tool holds this device.
                pass
        if hierarchy:
            boxes=xml_bounds(hierarchy,label,resource_id)
            box=intersect(boxes)
            if box:return (activity,(box[0]+box[2])//2,(box[1]+box[3])//2,'可见文字')
            return None
        remote='/data/local/tmp/tracking-'+secrets.token_hex(10)+'.xml'
        try:
            command('shell','uiautomator','dump',remote,timeout=12)
            boxes=xml_bounds(command('shell','cat',remote),label,resource_id)
        except (subprocess.SubprocessError,ET.ParseError):return None
        finally:command('shell','rm','-f',remote)
        source='可见文字'
    # Duplicate parent/child nodes may represent one control. Only their shared
    # visible region is actionable. Two disjoint matches are ambiguous.
    box=intersect(boxes)
    if box:return (activity,(box[0]+box[2])//2,(box[1]+box[3])//2,source)


def capture(adb, on_ready=lambda: None, path=None, device=DEVICE):
    if device not in (DEVICE,"A9TU024622000978"): raise ValueError("未知手机")
    def command(*args, timeout=6):
        return subprocess.check_output([adb,'-s',device,*args],timeout=timeout,stderr=subprocess.DEVNULL)
    events=[]
    result={'device':device,'events':events,'frames':[],'performed':[],'note':'待实测'}
    with tempfile.TemporaryDirectory() as folder:
        if path:
            launcher=command('shell','cmd','package','resolve-activity','--brief','-a','android.intent.action.MAIN','-c','android.intent.category.LAUNCHER',APP).decode().strip().splitlines()[-1]
            if not re.fullmatch(re.escape(APP)+r'/[A-Za-z0-9_.$]+',launcher):
                result['note']='无法确认 App 启动入口，待实测'
                return result
            command('shell','am','start','-n',launcher)
            time.sleep(1)
        if not focused_activity(command):
            result['note']='请先打开智小白 3D，待实测'
            return result
        endpoint=os.getenv('TRACKING_EVENT_URL') or 'https://event.gongfudou.com/event/zxbEvent'
        url=urlsplit(endpoint)
        result['endpoint']=url._replace(query='',fragment='').geturl()
        def observe(is_running):
            on_ready()
            for label in path or []:
                if not is_running():break
                previous=result['performed'][-1]['label'] if result['performed'] else None
                location=find_control(command,label,device=device,previous_label=previous)
                if location is None:
                    result['note']='未唯一定位当前 App 的「'+label+'」，待实测'
                    return
                activity,x,y,source=location
                if focused_activity(command)!=activity:
                    result['note']='操作前页面已切换，待实测'
                    return
                stamp=time.time()
                command('shell','input','tap',str(x),str(y))
                if result['performed']:result['performed'][-1]['until']=stamp
                result['performed'].append({'label':label,'time':stamp,'source':source})
                time.sleep(.7)
            if not path or len(result['performed'])!=len(path):return
            deadline=time.monotonic()+WITNESS_SECONDS
            while time.monotonic()<deadline and is_running():
                time.sleep(.4)
            result['performed'][-1]['until']=time.time()
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
            events.extend(scoped_events(found,result['performed'],path))
            result['diagnostics']={'source':'Proxyman','matched_path':matched}
            if len(result['performed'])==len(path or []):
                result['note']='已读取本机 Proxyman 的本轮事件' if events else '本轮目标操作时间窗无可关联的埋点，待实测'
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
            rows_path=Path(env['TRACKING_ROWS'])
            if rows_path.exists():
                with rows_path.open() as rows:
                    events.extend(scoped_events((json.loads(line) for line in rows),result['performed'],path))
            diag=Path(env['TRACKING_DIAGNOSTICS'])
            markers=diag.read_text().splitlines() if diag.exists() else []
            result['diagnostics']={key:markers.count(key) for key in ('matched_path','matched_query','tls_failed_client')}
            if len(result['performed'])!=len(path or []):return result
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
