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

def start(owner, runner, path=None, device=DEVICE):
    if device not in DEVICES: raise ValueError("未知设备")
    from .mobile_tracking_plan import LABELS
    if not isinstance(path,list) or not 1<=len(path)<=5 or any(a not in LABELS for a in path):
        raise ValueError('手机操作路径未通过校验')
    with db() as con:
        con.execute('BEGIN IMMEDIATE')
        now = time.time()
        con.execute("DELETE FROM runs WHERE created < ? AND id NOT IN (SELECT j.value FROM reports, json_each(reports.summary, '$.runIds') AS j)", (now-86400,))
        if con.execute("SELECT 1 FROM runs WHERE state IN ('queued','running','ready') AND created>? AND COALESCE(json_extract(result,'$.device'),?)=?", (now-150,DEVICE,device)).fetchone():
            raise ValueError('指定手机已有采集任务')
        key = secrets.token_hex(16)
        con.execute('INSERT INTO runs VALUES(?,?,?,?,?,?)',(key,owner,runner,now,'queued',json.dumps({'path':path,'device':device})))
        return key

def claim(runner, devices=None, directory_lookup=None):
    reported = [DEVICE] if devices is None else devices
    devices = [d for d in DEVICES if isinstance(reported,list) and d in reported]
    if not devices: return None
    if directory_lookup:
        with db() as con:
            queued = con.execute("SELECT 1 FROM runs WHERE runner=? AND state='queued' AND created>? LIMIT 1",
                                 (runner,time.time()-30)).fetchone()
        if not queued:
            return None
        directory = directory_lookup()
        devices = [serial for serial in devices if not sonic_busy(directory,serial)]
        if not devices:
            return None
    with db() as con:
        con.execute('BEGIN IMMEDIATE')
        row = con.execute("SELECT id FROM runs WHERE runner=? AND state='queued' AND created>? AND COALESCE(json_extract(result,'$.device'),?) IN (" + ','.join('?' for _ in devices) + ") ORDER BY created LIMIT 1",(runner,time.time()-30,DEVICE,*devices)).fetchone()
        if row:
            con.execute("UPDATE runs SET state='running' WHERE id=?",row)
            return row[0]

def ready(key, runner):
    with db() as con:
        con.execute("UPDATE runs SET state='ready' WHERE id=? AND runner=? AND state='running'", (key,runner))

def finish(key, runner, result):
    if not isinstance(result, dict):
        raise ValueError('采集结果无效')
    with db() as con:
        con.execute('BEGIN IMMEDIATE')
        row = con.execute("SELECT result FROM runs WHERE id=? AND runner=? AND state IN ('running','ready')", (key,runner)).fetchone()
        if not row:
            return
        assignment = json.loads(row[0])
        device = assignment.get('device', DEVICE)
        if result.get('device', device) != device:
            result = {'note': '采集设备与指定手机不一致，待实测'}
        encoded = json.dumps(dict(result, device=device, path=assignment.get('path', [])))
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
