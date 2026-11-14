"""One bounded capture per physical phone, readable only by its initiating user."""
import hashlib
import json
import re
import secrets
import sqlite3
import time
from pathlib import Path
from task_server.config import LEARNING_DIR

DEVICE = 'ecbfd645'
HONOR = 'A9TU024622000978'
IPHONE = '00008110-000150CC21C2801E'
DEVICE_NAMES = {HONOR: '荣耀 Magic Vs3', DEVICE: 'OPPO Reno9', IPHONE: 'iPhone 13'}
DEVICES = tuple(DEVICE_NAMES)
APP = 'com.kfb.model'
ACTIVE_RUN_FILTER = "((state='queued' AND created>?) OR (state IN ('running','ready') AND created>?))"
def db():
    path = Path(LEARNING_DIR) / 'private-mobile-tracking'
    path.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path/'sessions.sqlite', timeout=5)
    con.execute('CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, owner TEXT, runner TEXT, created REAL, state TEXT, result TEXT)')
    con.execute('CREATE TABLE IF NOT EXISTS reports (id TEXT PRIMARY KEY, owner TEXT NOT NULL, created REAL NOT NULL, summary TEXT NOT NULL)')
    return con

def assignment_for(key,runner):
    with db() as con:
        row=con.execute('SELECT result FROM runs WHERE id=? AND runner=?',(key,runner)).fetchone()
    return json.loads(row[0]) if row else {}

def start(owner, runner, path=None, device=DEVICE, *, probe=False):
    if device not in DEVICES: raise ValueError("未知设备")
    from .mobile_tracking_plan import LABELS
    if probe and (device != IPHONE or path is not None):
        raise ValueError('连接诊断只支持指定 iPhone，不接受业务操作路径')
    if not probe and (not isinstance(path,list) or not 1<=len(path)<=5 or any(a not in LABELS for a in path)):
        raise ValueError('手机操作路径未通过校验')
    with db() as con:
        con.execute('BEGIN IMMEDIATE')
        now = time.time()
        con.execute("DELETE FROM runs WHERE created < ? AND id NOT IN (SELECT j.value FROM reports, json_each(reports.summary, '$.runIds') AS j)", (now-86400,))
        if con.execute(f"SELECT 1 FROM runs WHERE {ACTIVE_RUN_FILTER} AND COALESCE(json_extract(result,'$.device'),?)=?", (now-30,now-150,DEVICE,device)).fetchone():
            raise ValueError('指定手机已有采集任务')
        key = secrets.token_hex(16)
        con.execute('INSERT INTO runs VALUES(?,?,?,?,?,?)',(key,owner,runner,now,'queued',json.dumps({'kind':'ios_proxy_probe','device':device} if probe else {'path':path,'device':device})))
        return key

def claim(runner, devices=None, directory_lookup=None, *, probe_devices=None):
    reported = [DEVICE] if devices is None else devices
    capture = {d for d in DEVICES if isinstance(reported,list) and d in reported}
    probes = {IPHONE} if isinstance(probe_devices,list) and IPHONE in probe_devices else set()
    if not capture and not probes:
        return None
    if directory_lookup:
        with db() as con:
            queued = con.execute("SELECT 1 FROM runs WHERE runner=? AND state='queued' AND created>? LIMIT 1",
                                 (runner,time.time()-30)).fetchone()
        if not queued:
            return None
        directory = directory_lookup()
        busy = {d for d in capture | probes if sonic_busy(directory,d)}
        capture -= busy
        probes -= busy
    with db() as con:
        con.execute('BEGIN IMMEDIATE')
        # Legacy assignments have no kind. A diagnostic-only worker must never claim those.
        rows = con.execute("SELECT id,result FROM runs WHERE runner=? AND state='queued' AND created>? ORDER BY created",
                           (runner,time.time()-30))
        for key, payload in rows:
            assignment = json.loads(payload)
            allowed = probes if assignment.get('kind') == 'ios_proxy_probe' else capture
            if assignment.get('device',DEVICE) in allowed:
                con.execute("UPDATE runs SET state='running' WHERE id=?", (key,))
                return key


def _public_probe(result):
    from mobile_tracking_ios_probe import PUBLIC_REASONS
    data = result if isinstance(result,dict) else {}
    stage = data.get('stage')
    if stage not in ('preflight','source','restore','observe','complete'):
        stage = 'preflight'
    valid_source = data.get('sourceVerified') is True and type(data.get('probeMatches')) is int and data['probeMatches']==2
    restored = data.get('appRestored') is True
    complete = valid_source and restored and stage=='complete' and data.get('observationComplete') is True
    reason = data.get('reason')
    if not isinstance(reason,str) or reason not in PUBLIC_REASONS:
        reason = '未取得完整的连接诊断结果，请重试'
    probe = dict(kind='ios_proxy_probe',stage=stage,reason=reason,
                 sourceVerified=valid_source,appRestored=restored,observationComplete=complete)
    for key, maximum in [('probeMatches',2),('windowSeconds',150),('connectCount',1000000),('postCount',1000000),
                         ('receiptCount',1000000),('acceptedCount',1000000),('eventCount',1000000)]:
        value = data.get(key)
        valid = type(value) is int and 0 <= value <= maximum
        if key in ('probeMatches','windowSeconds'):
            probe[key] = value if valid else 0
        else:
            probe[key] = value if valid and complete else None
    return probe


def ready(key, runner):
    with db() as con:
        con.execute("UPDATE runs SET state='ready' WHERE id=? AND runner=? AND state='running'", (key,runner))

def finish(key, runner, result):
    if not isinstance(result, dict):
        raise ValueError('采集结果无效')
    with db() as con:
        con.execute('BEGIN IMMEDIATE')
        row = con.execute("SELECT result,created FROM runs WHERE id=? AND runner=? AND state IN ('running','ready')", (key,runner)).fetchone()
        if not row:
            return
        assignment = json.loads(row[0])
        device = assignment.get('device', DEVICE)
        if result.get('device', device) != device:
            result = {'note': '采集设备与指定手机不一致，待实测'}
        if assignment.get('kind') == 'ios_proxy_probe':
            probe = _public_probe(result.get('probeResult'))
            result = dict(kind='ios_proxy_probe',device=device,probeResult=probe,note=probe['reason'])
            if time.time()-row[1]>150:
                result = dict(kind='ios_proxy_probe',device=device,note='连接诊断超时，请重新发起诊断')
        else:
            result = dict(result, device=device, path=assignment.get('path', []))
        encoded = json.dumps(result)
        if len(encoded)>6_000_000: raise ValueError('证据超过大小限制')
        con.execute("UPDATE runs SET state='done',result=? WHERE id=?",(encoded,key))

def read(owner,key):
    if not isinstance(key, str):
        raise ValueError('运行编号无效')
    with db() as con:
        row = con.execute('SELECT created,state,result FROM runs WHERE id=? AND owner=?',(key,owner)).fetchone()
    if not row: raise ValueError('找不到本次测试')
    created,state,result=row
    if state!='done' and time.time()-created>(30 if state=='queued' else 150):
        assignment = json.loads(result)
        if assignment.get('kind') == 'ios_proxy_probe':
            return dict(state='done',kind='ios_proxy_probe',device=assignment['device'],note='连接诊断超时，请重新发起诊断')
        return {'state':'done','note':'采集超时，待实测'}
    return dict(json.loads(result),state=state)


def _sonic_connection(directory, serial):
    if not directory or (directory.get('__source__') or {}).get('available') is not True:
        return 'unknown', '暂时无法读取 Sonic 设备目录'
    device = directory.get(serial)
    if device is None:
        return 'not_listed', '当前 Sonic 目录中未查到这部手机'
    status = device.get('sonic_status')
    if status == 'ONLINE':
        return 'online', '已连接 Sonic，设备空闲'
    if status in ('DEBUGGING', 'TESTING'):
        return 'busy', '已连接 Sonic，设备正在使用'
    if status in ('OFFLINE', 'DISCONNECTED'):
        return 'offline', 'Sonic 显示设备已断开'
    return 'unknown', '已在 Sonic 目录中找到设备，连接状态待确认'


def sonic_busy(directory, serial):
    return _sonic_connection(directory, serial)[0] == 'busy'


def selectable_devices(online, sonic_directory=None):
    now = time.time()
    with db() as con:
        reserved = {row[0] for row in con.execute(
            f"SELECT DISTINCT COALESCE(json_extract(result,'$.device'),?) FROM runs WHERE {ACTIVE_RUN_FILTER}",
            (DEVICE, now-30, now-150))}
    result = []
    for serial, name in DEVICE_NAMES.items():
        registered = [d for d in online if d.get('device_id') == serial]
        live = [d for d in registered if d.get('runner_online')]
        candidates = [d for d in live if d.get('status') in ('online', 'device')]
        sonic_status, sonic_reason = _sonic_connection(sonic_directory, serial)
        if live:
            runner_status, reason = 'online', '执行机在线，手机尚未就绪'
        elif registered:
            runner_status, reason = 'offline', '执行机心跳已过期'
        else:
            runner_status, reason = 'not_listed', '尚未收到这部手机的执行机心跳'
        option = dict(id=serial, name=name, ready=False, reason=reason, sonicStatus=sonic_status,
                      sonicReason=sonic_reason, runnerStatus=runner_status)
        if len(candidates) > 1:
            option['reason'] = '多台 Runner 同时登记了这部手机'
        elif candidates:
            row = candidates[0]
            caps = row.get('runner_capabilities') or {}
            ios = (caps.get('tracking_ios_devices') or {}).get(serial, {})
            if serial == IPHONE and (row.get('platform') != 'ios' or ios.get('ready') is not True):
                option['reason'] = ios.get('reason') or (row.get('preflight') or {}).get('reason') or '已连接 iPhone，请检查 iOS Runner 和 WDA'
            elif not caps.get('tracking_http_receipts'):
                option['reason'] = '请更新 Mac Runner'
            elif row.get('usage_status') != 'idle':
                option['reason'] = row.get('usage_label') or '设备正在使用'
            else:
                option.update(ready=True, reason=ios.get('reason') or '已连接', runner=row['runner_id'])
        option['runnerReason'] = option['reason']
        if sonic_status == 'busy':
            option.update(ready=False,reason='Sonic 显示设备正被占用，空闲后可开始采集')
            option.pop('runner',None)
        if serial == IPHONE:
            option.update(probeReady=False,probeReason=option['reason'])
            if len(candidates)==1 and sonic_status!='busy':
                row=candidates[0]
                probe_state=(row.get('runner_capabilities',{}).get('tracking_ios_probe_devices') or {}).get(serial,{})
                if row.get('platform')!='ios' or not probe_state:
                    option['probeReason']='请更新指定 iPhone 的 Mac 执行机以启用连接诊断'
                elif row.get('usage_status')=='busy' or row.get('active_job_id'):
                    option['probeReason']='执行机正在使用这部 iPhone'
                elif row.get('usage_status')!='idle' and not (row.get('usage_status')=='unknown' and row.get('preflight_status')=='blocked'):
                    option['probeReason']='未确认执行机空闲，暂不能启动连接诊断'
                else:
                    option.update(probeReady=probe_state.get('ready') is True,
                                  probeReason=probe_state.get('reason') or '连接诊断尚未就绪')
                    if option['probeReady']:
                        option['probeRunner']=row['runner_id']
        if serial in reserved:
            reason='这部手机正在执行埋点测试或连接诊断，请等待本次任务结束'
            option.update(ready=False,reason=reason)
            option.pop('runner',None)
            if serial==IPHONE:
                option.update(probeReady=False,probeReason=reason)
                option.pop('probeRunner',None)
        result.append(option)
    return result


def save_report(owner, data):
    if not isinstance(data, dict) or data.get('device') not in DEVICES:
        raise ValueError('报告设备无效')
    rows, ids = data.get('rows'), data.get('runIds')
    if not isinstance(rows, list) or not 1 <= len(rows) <= 5000 or not isinstance(ids, list) or len(ids) > 1000:
        raise ValueError('报告行或运行编号无效')
    if any(not isinstance(i, str) or len(i) != 32 for i in ids):
        raise ValueError('运行编号无效')
    cleaned = []
    for row in rows:
        if not isinstance(row, dict) or row.get('status') not in ('通过','待实测','差异待确认','失败'):
            raise ValueError('报告结论无效')
        fields = {k: str(row.get(k) or '')[:1200] for k in ('origin','action','status','reason')}
        fields['event'] = str(row.get('event') or '')[:160]
        receipts = row.get('receiptIds') or []
        if not isinstance(receipts, list) or len(receipts) > 128 or any(
                not isinstance(i, str) or not re.fullmatch(r'[0-9a-f]{16}', i) for i in receipts):
            raise ValueError('接口请求编号无效')
        fields['receiptIds'] = list(dict.fromkeys(receipts))
        cleaned.append(fields)
    ids = list(dict.fromkeys(ids))
    summary = dict(title=str(data.get('title') or '埋点实测')[:160], device=data['device'], deviceName=DEVICE_NAMES[data['device']], rows=cleaned, runIds=ids)
    encoded = json.dumps(summary, ensure_ascii=False)
    if len(encoded.encode()) > 2500000:
        raise ValueError('报告超出保存大小')
    key = hashlib.sha256((owner + '\0' + encoded).encode()).hexdigest()
    with db() as con:
        con.execute('BEGIN IMMEDIATE')
        for rid in ids:
            if not con.execute("SELECT 1 FROM runs WHERE id=? AND owner=? AND COALESCE(json_extract(result,'$.device'),?)=?",
                               (rid, owner, DEVICE, data['device'])).fetchone():
                raise ValueError('运行不存在或无权查看')
        con.execute('INSERT OR IGNORE INTO reports VALUES(?,?,?,?)',(key,owner,time.time(),encoded))
    return key


def history(owner):
    with db() as con:
        saved = con.execute('SELECT id,created,summary FROM reports WHERE owner=? ORDER BY created DESC LIMIT 5',(owner,)).fetchall()
    return [dict(json.loads(payload),id=key,created=created) for key,created,payload in saved]

# Files are addressed by a fixed report key. Browser input never becomes a path.
PUBLISHED_FILES = {
    'honor': ('honor-tracking.xlsx', '荣耀MagicVs3-线上埋点逐项实测.xlsx'),
    'honor-nov14': ('honor-followup-20261114.xlsx', '荣耀-11月14日上午抓包复测.xlsx'),
    'honor-pending': ('honor-pending.xlsx', '荣耀MagicVs3-待实测清单.xlsx'),
    'iphone': ('iphone-tracking.xlsx', 'iPhone13-埋点实测对照.xlsx'),
}


def published_file(key):
    if not isinstance(key, str) or key not in PUBLISHED_FILES:
        raise ValueError('找不到该实测表')
    filename, display = PUBLISHED_FILES[key]
    source = Path(__file__).resolve().parents[1] / 'assets' / 'tracking' / filename
    if not source.is_file():
        raise ValueError('当前版本缺少该实测表，请检查发布包')
    return source.read_bytes(), display


def published_report():
    path = Path(__file__).resolve().parents[1] / 'assets' / 'tracking-acceptance.json'
    return json.loads(path.read_text(encoding='utf-8'))
