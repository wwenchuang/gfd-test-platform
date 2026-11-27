"""Associate evidence with a recorded action only when the image bytes match."""
import base64
import binascii
import hashlib
import json
import re

from .ui_report_replay import ReportData

# Only action targets are displayed. Text entered by the test stays in the private replay.
ACTIONS = {'Tap': '点击', 'DoubleClick': '双击', 'Scroll': '滚动', 'Launch': '启动应用',
           'Input': '输入', 'Swipe': '滑动', 'Assert': '断言', 'WaitFor': '等待'}


def action_labels(dump):
    for execution in dump.get('executions', []):
        for task in execution.get('tasks', []):
            action = ACTIONS.get(task.get('subType'))
            if not action:
                continue
            params = task.get('param') or {}
            target = params.get('locate') or {}
            description = target.get('description', '') if isinstance(target, dict) else ''
            if not isinstance(description, str):
                description = ''
            description = ' '.join(description.split())[:55]
            title = action + ('「' + description + '」' if description else '')
            before = (task.get('uiContext') or {}).get('screenshot') or {}
            if isinstance(before, dict) and before.get('id'):
                yield before['id'], 1, title + '前'
            for record in task.get('recorder') or []:
                shot = record.get('screenshot') or {}
                if isinstance(shot, dict) and shot.get('id') and record.get('timing') == 'after-calling':
                    yield shot['id'], 2, title + '后'


def replay_labels(content):
    parser = ReportData()
    try:
        parser.feed(content.decode('utf-8', errors='replace'))
        parser.close()
    except (ValueError, AssertionError, RecursionError):
        return {}
    labels = {}
    for attrs, raw in parser.blocks:
        if attrs['type'] != 'midscene_web_dump':
            continue
        try:
            for key, rank, name in action_labels(json.loads(raw)):
                if rank > labels.get(key, (0, ''))[0]:
                    labels[key] = (rank, name)
                elif labels[key] != (rank, name) and rank == labels[key][0]:
                    labels[key] = (rank, '')
        except (AttributeError, ValueError, TypeError, RecursionError):
            continue
    result = {}
    for attrs, raw in parser.blocks:
        name = labels.get(attrs.get('data-id'), (0, ''))[1]
        if attrs['type'] != 'midscene-image' or not name:
            continue
        match = re.fullmatch(r'data:image/(jpeg|png);base64,(.*)', raw.strip(), re.DOTALL)
        if not match:
            continue
        try:
            content = base64.b64decode(''.join(match[2].split()), validate=True)
        except (ValueError, binascii.Error):
            continue
        kind = b'jpg' if match[1] == 'jpeg' else b'png'
        key = hashlib.sha256(kind + b'\0' + content).hexdigest()
        if key not in result:
            result[key] = name
        elif result[key] != name:
            result[key] = ''
    return {key: name for key, name in result.items() if name}


def index_recent_replays(db, batch_id):
    # Bound legacy enrichment per page read and remember processed reports, including unmatched ones.
    rows = db.execute("""SELECT e.attempt_id,e.artifact_id,length(a.content) AS bytes
        FROM ui_report_evidence e JOIN ui_report_artifacts a USING(ui_batch_id,artifact_id)
        WHERE e.ui_batch_id=? AND a.kind='html' AND length(a.content)<=33554432
        AND NOT EXISTS(SELECT 1 FROM ui_screenshot_indexes i WHERE i.attempt_id=e.attempt_id AND i.artifact_id=e.artifact_id)
        AND EXISTS(SELECT 1 FROM ui_report_evidence p JOIN ui_report_artifacts b USING(ui_batch_id,artifact_id)
        LEFT JOIN ui_screenshot_names n ON n.attempt_id=p.attempt_id AND n.artifact_id=p.artifact_id
        WHERE p.attempt_id=e.attempt_id AND b.kind IN ('jpg','png') AND COALESCE(n.caption,'')='')
        ORDER BY e.created_ns LIMIT 4""", (batch_id,)).fetchall()
    budget = 32 * 1024 * 1024
    for row in rows:
        if row['bytes'] > budget:
            break
        budget -= row['bytes']
        blob = db.execute('SELECT content FROM ui_report_artifacts WHERE ui_batch_id=? AND artifact_id=?',
                          (batch_id, row['artifact_id'])).fetchone()
        if not blob:
            continue
        labels = replay_labels(blob['content'])
        db.execute('BEGIN IMMEDIATE')
        try:
            db.executemany("""INSERT INTO ui_screenshot_names(attempt_id,artifact_id,caption) VALUES(?,?,?)
                ON CONFLICT(attempt_id,artifact_id) DO UPDATE SET caption=excluded.caption
                WHERE ui_screenshot_names.caption=''""",
                ((row['attempt_id'], aid, title) for aid, title in labels.items()))
            db.execute('INSERT OR IGNORE INTO ui_screenshot_indexes VALUES(?,?)',(row['attempt_id'],row['artifact_id']))
            db.commit()
        except Exception:
            db.rollback()
            raise
