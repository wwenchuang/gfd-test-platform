"""Private revisioned UI drafts. Authorization and execution belong to services."""
import json
import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from task_server import identity

_SCHEMA_LOCK = threading.Lock()
_CONTENT_KEYS = {'title', 'preconditions', 'steps', 'questions', 'yaml_content', 'ready'}


class DraftConflict(ValueError):
    pass


def default_db_path():
    configured = os.getenv('TASK_UI_DRAFT_DB')
    return Path(configured).expanduser().absolute() if configured else identity.default_db_path().with_name('ui-case-drafts.sqlite3')


class DraftStore:
    def __init__(self, path=None):
        self.path = Path(path).expanduser().absolute() if path else default_db_path()

    @contextmanager
    def _db(self, write=False):
        with _SCHEMA_LOCK:
            directory = self.path.parent
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            info = directory.lstat()
            if directory.is_symlink() or (hasattr(os, 'geteuid') and info.st_uid != os.geteuid()):
                raise PermissionError('Draft directory must be owned by service account')
            os.chmod(directory, 0o700)
            fd = os.open(self.path, os.O_CREAT | os.O_WRONLY | getattr(os, 'O_NOFOLLOW', 0), 0o600)
            try:
                if hasattr(os, 'geteuid') and os.fstat(fd).st_uid != os.geteuid():
                    raise PermissionError('Draft database must be owned by service account')
                os.fchmod(fd, 0o600)
            finally:
                os.close(fd)
            db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
            db.row_factory = sqlite3.Row
            try:
                version = db.execute('PRAGMA user_version').fetchone()[0]
                if version not in (0, 1):
                    raise RuntimeError('Unsupported draft schema version')
                db.execute('PRAGMA foreign_keys=ON')
                if version == 0:
                    db.executescript('''
                        CREATE TABLE IF NOT EXISTS drafts (
                            draft_id TEXT PRIMARY KEY, owner_user_id TEXT NOT NULL,
                            app_id TEXT NOT NULL, revision INTEGER NOT NULL,
                            updated_ns INTEGER NOT NULL, document TEXT NOT NULL);
                        CREATE INDEX IF NOT EXISTS drafts_owner_app_time
                            ON drafts(owner_user_id, app_id, updated_ns DESC);
                        CREATE TABLE IF NOT EXISTS draft_revisions (
                            draft_id TEXT NOT NULL REFERENCES drafts(draft_id),
                            revision INTEGER NOT NULL, document TEXT NOT NULL,
                            PRIMARY KEY(draft_id, revision));
                        PRAGMA user_version=1;
                    ''')
            except Exception:
                db.close()
                raise
        try:
            if write:
                db.execute('BEGIN IMMEDIATE')
            yield db
            if write:
                db.execute('COMMIT')
        except Exception:
            if write and db.in_transaction:
                db.execute('ROLLBACK')
            raise
        finally:
            db.close()

    @staticmethod
    def _content(content):
        if not isinstance(content, dict) or set(content) - _CONTENT_KEYS:
            raise ValueError('draft_content_invalid')
        for key, value in content.items():
            expected = bool if key == 'ready' else list if key in {'preconditions', 'steps', 'questions'} else str
            if type(value) is not expected:
                raise ValueError('draft_content_invalid')
        # Round trip prevents callers retaining mutable store state.
        return json.loads(json.dumps(content, ensure_ascii=False))

    @staticmethod
    def _read(db, draft_id, owner_id):
        row = db.execute('SELECT document FROM drafts WHERE draft_id=? AND owner_user_id=?', (draft_id, owner_id)).fetchone()
        if not row:
            raise KeyError(draft_id)
        return json.loads(row['document'])

    @staticmethod
    def _save(db, document):
        encoded = json.dumps(document, ensure_ascii=False, sort_keys=True)
        db.execute('UPDATE drafts SET revision=?,updated_ns=?,document=? WHERE draft_id=?',
                   (document['revision'], document['updated_ns'], encoded, document['draft_id']))
        db.execute('INSERT INTO draft_revisions VALUES (?,?,?)', (document['draft_id'], document['revision'], encoded))

    def create(self, owner, app_id, text):
        fields = {key: owner.get(key) for key in ('user_id', 'username', 'display_name')}
        if any(not isinstance(value, str) or not value.strip() for value in fields.values()) or not isinstance(app_id, str) or not app_id.strip() or not isinstance(text, str) or not text.strip():
            raise ValueError('draft_input_invalid')
        now = time.time_ns()
        document = {'draft_id': 'uid_' + uuid.uuid4().hex, 'revision': 1, 'app_id': app_id,
                    'owner_user_id': fields['user_id'], 'owner_username': fields['username'],
                    'owner_display_name': fields['display_name'], 'source_text': text,
                    'title': '', 'preconditions': [], 'steps': [], 'questions': [],
                    'generation_status': 'queued', 'error': '', 'yaml_content': '', 'ready': False,
                    'created_ns': now, 'updated_ns': now}
        with self._db(write=True) as db:
            encoded = json.dumps(document, ensure_ascii=False, sort_keys=True)
            db.execute('INSERT INTO drafts VALUES (?,?,?,?,?,?)', (document['draft_id'], fields['user_id'], app_id, 1, now, encoded))
            db.execute('INSERT INTO draft_revisions VALUES (?,?,?)', (document['draft_id'], 1, encoded))
        return document

    def get(self, draft_id, owner_id):
        with self._db() as db:
            return self._read(db, draft_id, owner_id)

    def get_revision(self, draft_id, owner_id, revision):
        with self._db() as db:
            self._read(db, draft_id, owner_id)
            row = db.execute('SELECT document FROM draft_revisions WHERE draft_id=? AND revision=?', (draft_id, revision)).fetchone()
            if not row:
                raise KeyError((draft_id, revision))
            return json.loads(row['document'])

    def list(self, owner_id, app_id='', limit=20):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('draft_limit_invalid')
        with self._db() as db:
            query = 'SELECT document FROM drafts WHERE owner_user_id=?'
            params = [owner_id]
            if app_id:
                query += ' AND app_id=?'
                params.append(app_id)
            query += ' ORDER BY updated_ns DESC,draft_id DESC LIMIT ?'
            return [json.loads(row['document']) for row in db.execute(query, params + [limit])]

    def revise(self, draft_id, owner_id, expected_revision, content):
        content = self._content(content)
        with self._db(write=True) as db:
            document = self._read(db, draft_id, owner_id)
            if type(expected_revision) is not int or document['revision'] != expected_revision:
                raise DraftConflict('draft_revision_changed')
            document.update(content)
            document.update(revision=document['revision'] + 1, updated_ns=time.time_ns(), generation_status='edited', error='', ready=False)
            self._save(db, document)
            return document

    def set_generation(self, draft_id, owner_id, status, content=None, error=''):
        if status not in {'generating', 'ready', 'failed'} or not isinstance(error, str):
            raise ValueError('draft_generation_invalid')
        content = self._content({} if content is None else content)
        with self._db(write=True) as db:
            document = self._read(db, draft_id, owner_id)
            if document['revision'] != 1 or document['generation_status'] not in {'queued', 'generating'}:
                raise DraftConflict('draft_generation_stale')
            if status == 'generating':
                if document['generation_status'] != 'queued' or content:
                    raise DraftConflict('draft_generation_claimed')
                document.update(generation_status=status, updated_ns=time.time_ns())
                # Claim is operational state; do not rewrite immutable revision history.
                db.execute('UPDATE drafts SET updated_ns=?,document=? WHERE draft_id=?', (document['updated_ns'], json.dumps(document, ensure_ascii=False), draft_id))
            else:
                document.update(content)
                document.update(revision=2, generation_status=status, error=error, updated_ns=time.time_ns())
                if status == 'failed':
                    document['ready'] = False
                self._save(db, document)
            return document

    def recover_interrupted_generation(self):
        """Explicit startup recovery; never invoked by constructing this store."""
        recovered = 0
        with self._db(write=True) as db:
            for row in db.execute('SELECT document FROM drafts').fetchall():
                document = json.loads(row['document'])
                if document['revision'] == 1 and document['generation_status'] in {'queued', 'generating'}:
                    document.update(revision=2, generation_status='failed', error='服务重启导致整理中断，原始描述已保留，请重新创建草稿', ready=False, updated_ns=time.time_ns())
                    self._save(db, document)
                    recovered += 1
        return recovered
