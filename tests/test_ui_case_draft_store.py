import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from task_server.services.ui_case_draft_store import DraftStore, DraftConflict

OWNER = {'user_id': 'u1', 'username': 'alice', 'display_name': '甲'}


def test_private_persistence_history_and_cas(tmp_path):
    path = tmp_path / 'private' / 'draft.sqlite3'
    store = DraftStore(path)
    first = store.create(OWNER, 'app', '点击首页')
    assert first['owner_user_id'] == 'u1' and first['revision'] == 1
    assert first['generation_status'] == 'queued'
    with pytest.raises(KeyError):
        store.get(first['draft_id'], 'u2')
    changed = store.revise(first['draft_id'], 'u1', 1, {'title': '首页', 'steps': [{'step_id': 's1'}]})
    assert changed['revision'] == 2
    with pytest.raises(DraftConflict):
        store.revise(first['draft_id'], 'u1', 1, {'title': 'stale'})
    reopened = DraftStore(path)
    assert reopened.get_revision(first['draft_id'], 'u1', 1)['title'] == ''
    assert reopened.get(first['draft_id'], 'u1')['title'] == '首页'
    assert reopened.list('u2') == []
    assert len(reopened.list('u1', 'app')) == 1
    assert reopened.list('u1', 'other') == []
    assert os.stat(path).st_mode & 0o777 == 0o600
    assert os.stat(path.parent).st_mode & 0o777 == 0o700


def test_generation_does_not_overwrite_edits_or_finished_result(tmp_path):
    store = DraftStore(tmp_path / 'd.db')
    draft = store.create(OWNER, 'app', '描述')
    key = draft['draft_id']
    store.set_generation(key, 'u1', 'generating')
    store.revise(key, 'u1', 1, {'title': '人工修改'})
    with pytest.raises(DraftConflict):
        store.set_generation(key, 'u1', 'ready', {'title': '迟到'})
    second = store.create(OWNER, 'app', '描述')
    result = store.set_generation(second['draft_id'], 'u1', 'ready', {'title': 'AI', 'ready': True})
    assert result['revision'] == 2 and result['ready'] is True
    with pytest.raises(DraftConflict):
        store.set_generation(second['draft_id'], 'u1', 'failed', error='迟到')


def test_whitelist_recovery_and_symlink(tmp_path):
    store = DraftStore(tmp_path / 'd.db')
    draft = store.create(OWNER, 'app', '描述')
    with pytest.raises(ValueError):
        store.revise(draft['draft_id'], 'u1', 1, {'owner_user_id': 'u2'})
    assert DraftStore(store.path).get(draft['draft_id'], 'u1')['generation_status'] == 'queued'
    assert store.recover_interrupted_generation() == 1
    assert store.get(draft['draft_id'], 'u1')['generation_status'] == 'failed'
    link = tmp_path / 'link.db'
    link.symlink_to(store.path)
    with pytest.raises((OSError, PermissionError)):
        DraftStore(link).list('u1')


def test_concurrent_revision_has_one_winner(tmp_path):
    store = DraftStore(tmp_path / 'd.db')
    draft = store.create(OWNER, 'app', '描述')
    def change(title):
        try:
            return store.revise(draft['draft_id'], 'u1', 1, {'title': title})['revision']
        except DraftConflict:
            return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(change, ['A', 'B']))
    assert sorted(map(str, results)) == ['2', 'conflict']


def test_content_types_and_environment_path(tmp_path, monkeypatch):
    monkeypatch.setenv('TASK_UI_DRAFT_DB', str(tmp_path / 'configured.db'))
    store = DraftStore()
    draft = store.create(OWNER, 'app', '描述')
    assert store.path == tmp_path / 'configured.db'
    for content in ([], {'steps': 'bad'}, {'ready': 1}, {'title': None}):
        with pytest.raises(ValueError):
            store.set_generation(draft['draft_id'], 'u1', 'ready', content)
    assert store.get(draft['draft_id'], 'u1')['revision'] == 1


def test_reads_do_not_rewrite_database_schema(tmp_path, monkeypatch):
    import sqlite3
    store = DraftStore(tmp_path / 'private' / 'd.db')
    draft = store.create(OWNER, 'app', '描述')
    original = sqlite3.connect
    statements = []
    def connect(*args, **kwargs):
        db = original(*args, **kwargs)
        db.set_trace_callback(statements.append)
        return db
    monkeypatch.setattr(sqlite3, 'connect', connect)
    store.get(draft['draft_id'], 'u1')
    store.list('u1', 'app')
    assert not any('CREATE ' in sql.upper() or 'USER_VERSION=' in sql.upper() for sql in statements)
