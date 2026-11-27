"""Display names belong to a screenshot's use in one attempt, independently of its bytes."""
from collections import Counter
import json
import re
from pathlib import Path

from .ui_execution_contract import AccessDenied, require_ui_actor


def clean_name(value):
    if (not isinstance(value, str) or len(value) > 100 or
            re.search(r'[\x00-\x1f\x7f/\\]', value) or any(not c.isprintable() for c in value)):
        raise ValueError('截图名称最多 100 个字符，不能包含路径或控制字符')
    return value.strip()


def source_caption(shot):
    # Newer runners can send an explicit capture label. Older ones send only the filename.
    name = shot.get('step_label') or shot.get('caption')
    if not name:
        filename = str(shot.get('name') or '').replace('\\','/').split('/')[-1]
        stem = Path(filename).stem
        if re.search(r'[\u3400-\u9fff]', stem) and not re.fullmatch(r'(执行)?截图[\d _-]*', stem):
            name = stem
    try:
        return clean_name(name or '')
    except ValueError:
        return ''


def named_evidence(db, batch_id):
    from .ui_batch_report_service import _artifact_metadata
    from .ui_screenshot_replay_names import index_recent_replays
    index_recent_replays(db, batch_id)
    rows = db.execute("""SELECT e.attempt_id,e.artifact_id,a.kind,c.platform,c.attempt_no,s.metadata_json,
        n.caption,n.custom_name FROM ui_report_evidence e JOIN ui_report_artifacts a
        ON a.ui_batch_id=e.ui_batch_id AND a.artifact_id=e.artifact_id
        JOIN case_attempts c ON c.attempt_id=e.attempt_id AND c.ui_batch_id=e.ui_batch_id
        JOIN snapshots s ON s.snapshot_id=c.snapshot_id
        LEFT JOIN ui_screenshot_names n ON n.attempt_id=e.attempt_id AND n.artifact_id=e.artifact_id
        WHERE e.ui_batch_id=? ORDER BY e.created_ns,e.attempt_id,e.artifact_id""",(batch_id,))
    rows = rows.fetchall()
    totals = Counter((row['attempt_id'], row['caption']) for row in rows if row['caption'])
    occurrences = Counter()
    counts = Counter()
    result = []
    for row in rows:
        item = _artifact_metadata(batch_id,row['artifact_id'],row['kind'],row['attempt_id'])
        if row['kind'] != 'html':
            meta = json.loads(row['metadata_json'])
            case = meta.get('task_name') or Path(meta.get('file') or '').stem or '用例执行'
            platform = {'android':'Android','ios':'iOS'}.get(row['platform'],'')
            counts[row['attempt_id']] += 1
            context = ' · '.join(filter(None, (case,platform,f"第 {row['attempt_no']} 次执行")))
            label = row['caption'] or f"图片 {counts[row['attempt_id']]:02}"
            key = (row['attempt_id'], row['caption'])
            if totals[key] > 1:
                occurrences[key] += 1
                label += f' · 图 {occurrences[key]}'
            automatic = f'{case} · {label}'
            item.update(display_name=row['custom_name'] or automatic, auto_name=automatic,
                        custom_name=row['custom_name'] or '', capture_context=context)
        result.append(item)
    return result


def can_rename(actor, batch):
    try:
        require_ui_actor(actor,permission='ui.edit',app_id=batch['app_id'])
        return True
    except AccessDenied:
        return False


def rename(actor, batch_id, body):
    from .ui_batch_report_service import _batch, _connect
    batch = _batch(actor,batch_id)
    require_ui_actor(actor,permission='ui.edit',app_id=batch['app_id'])
    if not isinstance(body,dict):
        raise ValueError('截图名称无效')
    name = clean_name(body.get('name'))
    previous = clean_name(body.get('previous_name'))
    aid, attempt = body.get('artifact_id'), body.get('attempt_id')
    if not isinstance(aid,str) or not re.fullmatch('[a-f0-9]{64}',aid) or not isinstance(attempt,str):
        raise FileNotFoundError('screenshot_not_found')
    db = _connect()
    try:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute("""SELECT n.custom_name FROM ui_report_evidence e JOIN ui_report_artifacts a
            ON a.ui_batch_id=e.ui_batch_id AND a.artifact_id=e.artifact_id
            LEFT JOIN ui_screenshot_names n ON n.attempt_id=e.attempt_id AND n.artifact_id=e.artifact_id
            WHERE e.ui_batch_id=? AND e.attempt_id=? AND e.artifact_id=? AND a.kind IN ('jpg','png')""",
            (batch_id,attempt,aid)).fetchone()
        if not row:
            raise FileNotFoundError('screenshot_not_found')
        current = row['custom_name'] or ''
        if current not in (previous,name):
            raise ValueError('名称已被修改，请刷新报告后重试')
        db.execute("""INSERT INTO ui_screenshot_names(attempt_id,artifact_id,custom_name) VALUES(?,?,?)
            ON CONFLICT(attempt_id,artifact_id) DO UPDATE SET custom_name=excluded.custom_name""",(attempt,aid,name))
        db.commit()
        return {'custom_name':name}
    finally:
        db.close()
