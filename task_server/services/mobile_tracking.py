"""One bounded capture per physical phone, readable only by its initiating user."""
import json
import secrets
import sqlite3
import time
from pathlib import Path
from task_server.config import LEARNING_DIR

DEVICE = 'ecbfd645'
HONOR = 'A9TU024622000978'
DEVICES = (DEVICE, HONOR)
APP = 'com.kfb.model'
def db():
    path = Path(LEARNING_DIR) / 'private-mobile-tracking'
    path.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path/'sessions.sqlite', timeout=5)
    con.execute('CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, owner TEXT, runner TEXT, created REAL, state TEXT, result TEXT)')
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
        con.execute('DELETE FROM runs WHERE created < ?', (now-86400,))
        if con.execute("SELECT 1 FROM runs WHERE state IN ('queued','running','ready') AND created>?", (now-150,)).fetchone():
            raise ValueError('指定手机已有采集任务')
        key = secrets.token_hex(16)
        con.execute('INSERT INTO runs VALUES(?,?,?,?,?,?)',(key,owner,runner,now,'queued',json.dumps({'path':path,'device':device})))
        return key

def claim(runner, devices=None):
    reported = [DEVICE] if devices is None else devices
    devices = [d for d in DEVICES if isinstance(reported,list) and d in reported]
    if not devices: return None
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
    encoded = json.dumps(result)
    if len(encoded)>6_000_000: raise ValueError('证据超过大小限制')
    with db() as con:
        con.execute("UPDATE runs SET state='done',result=? WHERE id=? AND runner=? AND state IN ('running','ready')",(encoded,key,runner))

def read(owner,key):
    with db() as con:
        row = con.execute('SELECT created,state,result FROM runs WHERE id=? AND owner=?',(key,owner)).fetchone()
    if not row: raise ValueError('找不到本次测试')
    created,state,result=row
    if state!='done' and time.time()-created>(30 if state=='queued' else 150):
        return {'state':'done','note':'采集超时，待实测'}
    return dict(json.loads(result),state=state)
