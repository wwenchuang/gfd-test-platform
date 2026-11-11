"""Project published replay and visual evidence into authenticated workbench views."""
import hashlib
import re

from task_server import identity
from task_server.services import requirement_design as design
from task_server.services import requirement_evaluation_view as files

_DIGEST = re.compile('[a-f0-9]{64}')
_REPORTS = {'replay': 'w8-report.json', 'visual': 'w9-report.json'}


def _rows(row, key):
    rows = row.get(key)
    if not isinstance(rows, list) or not all(isinstance(item, dict) for item in rows):
        raise files.Unavailable()
    return rows


def _in_scope(scope, key, values):
    allowed = scope.get(key)
    return allowed == '*' or (isinstance(allowed, list) and values and all(value in allowed for value in values))


def _authorized_profile(owner, username):
    profile = identity.get_access_profile(username)
    if not (profile and profile.get('user_id') == owner and profile.get('status') == 'active'
            and not profile.get('must_change_password') and
            (profile.get('is_superuser') or 'api.view' in (profile.get('permissions') or []))):
        raise design.Missing()
    return profile


def _replay(doc, profile):
    project = doc.get('project_id')
    environments = doc.get('environment_ids', [doc.get('environment_id')])
    if not isinstance(project, str) or not project or not isinstance(environments, list) or not environments or not all(
            isinstance(env, str) and env for env in environments):
        raise files.Unavailable()
    scope = profile.get('scope') or {}
    if not profile.get('is_superuser') and not (
            _in_scope(scope, 'api_projects', [project]) and _in_scope(scope, 'api_environments', environments)):
        raise design.Missing()
    visible = files._fields(doc, 'version checked_at total fully_replayed historical_passed manual_pending technical_gap_count generated replayed_assertions negative_controls finished_at')
    visible['cases'] = []
    for case in _rows(doc, 'cases'):
        row = files._fields(case, 'id key title version_number case_version_id execution_id run_id execution_case_id status review '
                                  'main_assertions setup_assertions cleanup_assertions replayed_assertions negative_controls')
        row['checks'] = [files._fields(c, 'stage title status evidence') for c in _rows(case, 'checks')]
        row['gaps'] = files._strings(case, 'gaps')
        visible['cases'].append(row)
    return visible


def _visual(doc):
    visible = files._fields(doc, 'version generated_at batch_id model completed started_at fingerprint')
    for key, allow in (
            ('usage', 'calls reported_tokens unreported_calls'),
            ('limits', 'rounds model_calls_per_run model_calls_total output_tokens_per_call model_timeout_ms run_timeout_ms'),
            ('versions', 'midscene playwright browser node')):
        if key in doc:
            if not isinstance(doc[key], dict):
                raise files.Unavailable()
            visible[key] = files._fields(doc[key], allow)
    visible['scenarios'] = [files._fields(s, 'id title assertion') for s in _rows(doc, 'scenarios')]
    visible['runs'] = []
    for run in _rows(doc, 'runs'):
        row = files._fields(run, 'scenario_id arm round status elapsed_ms reason assertion actions model_calls tokens condition_sha256')
        row['screenshots'] = []
        for image in _rows(run, 'screenshots'):
            digest = image.get('sha256')
            if not isinstance(digest, str) or not _DIGEST.fullmatch(digest) or image.get('file') != digest+'.png':
                raise files.Unavailable()
            row['screenshots'].append(files._fields(image, 'sha256 label'))
        visible['runs'].append(row)
    return visible


def report(kind, owner, username):
    profile = _authorized_profile(owner, username)
    if kind == 'visual' and not profile.get('is_superuser') and not all(
            (profile.get('scope') or {}).get(key) == '*' for key in ('api_projects','api_environments')):
        raise design.Missing()
    doc = files._published_file(_REPORTS[kind])
    if not isinstance(doc, dict) or doc.get('version') != 1:
        raise files.Unavailable()
    return _replay(doc, profile) if kind == 'replay' else _visual(doc)


def screenshot(owner, username, digest):
    if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
        raise ValueError('截图标识无效')
    visible = report('visual', owner, username)
    if not any(pic['sha256'] == digest for run in visible['runs'] for pic in run['screenshots']):
        raise design.Missing()
    data = files._published_bytes(digest+'.png', subdirectories=('visual-artifacts',), limit=8 * 1024 * 1024)
    if not data.startswith(b'\x89PNG\r\n\x1a\n') or hashlib.sha256(data).hexdigest() != digest:
        raise files.Unavailable()
    return data


def mobile_observations():
    """Expose only the app-scoped, cropped Android evidence prepared for colleagues."""
    doc = files._published_file('android-observations.json')
    if not isinstance(doc, dict) or doc.get('version') != 1 or doc.get('app_id') != 'com.kfb.model':
        raise files.Unavailable()
    visible = files._fields(doc, 'app_id build device measured_at')
    visible['notes'] = files._strings(doc, 'notes')
    visible['runs'] = []
    for item in _rows(doc, 'runs'):
        row = files._fields(item, 'flow round status duration_s')
        if (row.get('flow') not in ('收藏分类切换', '指定设备打印记录', '创作记录查询') or
                type(row.get('round')) is not int or row['round'] < 1 or
                row.get('status') not in ('passed', 'failed', 'blocked')):
            raise files.Unavailable()
        row['checks'] = files._strings(item, 'checks')
        row['screenshots'] = []
        for frame in _rows(item, 'screenshots'):
            digest = frame.get('sha256')
            if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
                raise files.Unavailable()
            row['screenshots'].append(files._fields(frame, 'sha256 label'))
        visible['runs'].append(row)
    return visible


def mobile_frame(digest):
    if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
        raise ValueError('截图标识无效')
    if not any(pic['sha256'] == digest for row in mobile_observations()['runs'] for pic in row['screenshots']):
        raise design.Missing()
    data = files._published_bytes(digest + '.png', subdirectories=('android-observations',), limit=1024 * 1024)
    if not data.startswith(b'\x89PNG\r\n\x1a\n') or hashlib.sha256(data).hexdigest() != digest:
        raise files.Unavailable()
    return data
