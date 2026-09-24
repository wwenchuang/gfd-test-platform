"""Stable YAML identity, exact-byte revisions, and recoverable filesystem mutations.

All callers must authorize the module/file BEFORE calling. This is provenance,
not an access-control store. The protected journal contains hashes and references,
never scripts. Files remain in TASK_DIR; existing version backups remain unchanged.
Lock order is global asset lock -> per-file lock -> SQLite. A durable intent is
committed before filesystem mutation; recovery publishes it only if ALL expected
filesystem results match. A crash before mutation discards the unmatched intent;
external edits are reconciled as unknown, never attributed to the next reader.
"""
import copy
import hashlib
import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from task_server import config
from .operation_attribution import current_actor, default_db_path, _private_dir

_SOURCE = ContextVar("asset_source", default=None)
UNKNOWN = {"kind": "unknown", "user_id": "", "username": "", "display_name": ""}


def db_path():
    return Path(os.getenv("TASK_ASSET_DB") or default_db_path().with_name("assets.sqlite3")).expanduser().absolute()


def is_asset_path(path):
    root, target = Path(config.TASK_DIR).absolute(), Path(path).absolute()
    try:
        relative = target.relative_to(root)
    except ValueError:
        return False
    return (bool(relative.parts) and target.suffix in {".yaml", ".yml"}
            and all(not part.startswith(".") for part in relative.parts))


def _key(path):
    if not is_asset_path(path):
        raise ValueError("not a task YAML asset")
    target = Path(path).absolute()
    # Do not follow symlinks into another scope, even another module in TASK_DIR.
    if target.resolve() != target:
        raise ValueError("symlink assets are not supported")
    return str(target.relative_to(Path(config.TASK_DIR).resolve()))


def _path(key):
    path = Path(config.TASK_DIR).resolve() / key
    _key(path)
    return path


@contextmanager
def mutation_lock():
    from task_server.storage import _path_mutation_lock
    database = db_path()
    _private_dir(database.parent)
    with _path_mutation_lock(str(database) + ".mutation"):
        yield


@contextmanager
def source_context(*, source_recording_id=None, restored_from_version_id=None):
    """Internal, validated source IDs only; never populate from arbitrary bodies."""
    import re
    values = {k: v for k, v in {
        "source_recording_id": source_recording_id,
        "restored_from_version_id": restored_from_version_id,
    }.items() if isinstance(v, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", v)}
    token = _SOURCE.set(values)
    try:
        yield
    finally:
        _SOURCE.reset(token)


@contextmanager
def _database():
    path = db_path()
    if path.is_symlink():
        raise PermissionError("asset database must not be a symlink")
    conn = sqlite3.connect(path)
    os.chmod(path, 0o600)
    try:
        conn.executescript('''
            CREATE TABLE IF NOT EXISTS assets (id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS bindings (path TEXT PRIMARY KEY, asset_id TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS revisions (id TEXT PRIMARY KEY, asset_id TEXT NOT NULL, data TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS revisions_asset ON revisions(asset_id);
            CREATE TABLE IF NOT EXISTS pending (id TEXT PRIMARY KEY, plan TEXT NOT NULL);
        ''')
        _recover(conn)
        yield conn
    finally:
        conn.close()


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _file_identity(path):
    stat = Path(path).stat()
    return [stat.st_dev, stat.st_ino]


@contextmanager
def _staged(path, write):
    """Prepare bytes before journaling, so identical old bytes aren't proof of commit."""
    target = Path(path)
    staged = target.with_name(f".asset-{uuid.uuid4().hex}.tmp")
    try:
        write(staged)
        yield staged
    finally:
        staged.unlink(missing_ok=True)


def _expected_matches(plan):
    for key, expected in plan["expected"].items():
        path = _path(key)
        actual = _hash(path.read_bytes()) if path.is_file() else None
        if actual != expected:
            return False
        identity = plan.get("identities", {}).get(key)
        if identity and _file_identity(path) != identity:
            return False
    return True


def _publish(conn, plan):
    for row in plan["assets"]:
        conn.execute("INSERT OR REPLACE INTO assets VALUES (?,?)", (row["asset_id"], json.dumps(row)))
        version = row["version"]
        conn.execute("INSERT OR IGNORE INTO revisions VALUES (?,?,?)", (version["version_id"], row["asset_id"], json.dumps(version)))
    for key, asset_id in plan["bindings"].items():
        conn.execute("DELETE FROM bindings WHERE path=?", (key,))
        if asset_id:
            conn.execute("INSERT INTO bindings VALUES (?,?)", (key, asset_id))


def _recover(conn):
    for intent_id, data in conn.execute("SELECT id,plan FROM pending").fetchall():
        plan = json.loads(data)
        with conn:
            if _expected_matches(plan):
                _publish(conn, plan)
            conn.execute("DELETE FROM pending WHERE id=?", (intent_id,))


def _finish(conn, intent_id, plan):
    if not _expected_matches(plan):
        raise OSError("asset contents changed during mutation")
    with conn:
        _publish(conn, plan)
        conn.execute("DELETE FROM pending WHERE id=?", (intent_id,))


def _mutate(conn, plan, action):
    intent_id = uuid.uuid4().hex
    with conn:
        conn.execute("INSERT INTO pending VALUES (?,?)", (intent_id, json.dumps(plan)))
    action()
    _finish(conn, intent_id, plan)


def _row(conn, key):
    result = conn.execute("SELECT a.data FROM assets a JOIN bindings b ON a.id=b.asset_id WHERE b.path=?", (key,)).fetchone()
    return json.loads(result[0]) if result else None


def _revision(asset_id, data, actor, source):
    return {"version_id": uuid.uuid4().hex, "asset_id": asset_id,
            "content_sha256": _hash(data), "size": len(data), "author": dict(actor),
            "created_at": time.time(), "source": source}


def _new(data, actor, source):
    asset_id = uuid.uuid4().hex
    return {"asset_id": asset_id, "creator": dict(actor), "created_at": time.time(),
            "deleted_at": None, "version": _revision(asset_id, data, actor, source)}


def _sync(conn, key):
    path, row = _path(key), _row(conn, key)
    if not path.is_file():
        if row:
            row["deleted_at"] = time.time()
            with conn:
                _publish(conn, {"assets": [row], "bindings": {key: None}})
        return None
    data = path.read_bytes()
    if row is None:
        row = _new(data, UNKNOWN, "historical")
    elif row["version"]["content_sha256"] != _hash(data):
        row["version"] = _revision(row["asset_id"], data, UNKNOWN, "external")
    else:
        return row
    with conn:
        _publish(conn, {"assets": [row], "bindings": {key: row["asset_id"]}})
    return row


def snapshot(path):
    """Authorized caller receives (metadata, exact bytes) in ONE critical section.

    Task3B must freeze returned bytes, not read the mutable path a second time.
    External writes are unknown revisions. No scripts are stored in this DB.
    """
    key = _key(path)
    with mutation_lock(), _database() as conn:
        row = _sync(conn, key)
        if not row:
            raise FileNotFoundError(path)
        data = _path(key).read_bytes()
        if _hash(data) != row["version"]["content_sha256"]:
            raise OSError("external asset changed during snapshot; retry")
        return row, data


def versions(path):
    key = _key(path)
    with mutation_lock(), _database() as conn:
        row = _sync(conn, key)
        if not row:
            raise FileNotFoundError(path)
        result = [json.loads(x[0]) for x in conn.execute("SELECT data FROM revisions WHERE asset_id=?", (row["asset_id"],))]
        return sorted(result, key=lambda x: x["created_at"], reverse=True)


def write_text(path, text):
    from task_server.storage import _write_text_file
    key, data = _key(path), (text or "").encode("utf-8")
    with mutation_lock(), _database() as conn:
        row = _sync(conn, key)
        if row and row["version"]["content_sha256"] == _hash(data) and not _SOURCE.get():
            return row
        if row:
            row["version"] = _revision(row["asset_id"], data, current_actor(), "write")
        else:
            row = _new(data, current_actor(), "write")
        row["version"].update(_SOURCE.get() or {})
        if (_SOURCE.get() or {}).get("source_recording_id"):
            row["source_recording_id"] = _SOURCE.get()["source_recording_id"]
        plan = {"assets": [row], "bindings": {key: row["asset_id"]}, "expected": {key: _hash(data)}}
        with _staged(path, lambda temp: _write_text_file(temp, text)) as staged:
            plan["identities"] = {key: _file_identity(staged)}
            _mutate(conn, plan, lambda: os.replace(staged, path))
        return row


def copy_or_move(source, target, *, move=False, overwrite=False, before=None):
    from task_server.storage import _write_bytes_file
    skey, tkey = _key(source), _key(target)
    with mutation_lock(), _database() as conn:
        src = _sync(conn, skey)
        if not src:
            raise FileNotFoundError(source)
        if skey == tkey:
            return src
        dst = _sync(conn, tkey)
        if dst and not overwrite:
            raise FileExistsError("目标文件已存在，如需覆盖请勾选覆盖")
        data = _path(skey).read_bytes()
        if not move and dst and dst["version"]["content_sha256"] == _hash(data):
            return dst  # no filesystem transition to attest, so no new authored revision
        if _hash(data) != src["version"]["content_sha256"]:
            raise OSError("source changed during copy")
        if before:
            before(bool(dst))
        actor = current_actor()
        ref = {"asset_id": src["asset_id"], "version_id": src["version"]["version_id"]}
        rows, bindings, expected = [], {}, {tkey: _hash(data)}
        if move:
            row = copy.deepcopy(src)
            if dst:
                dst["deleted_at"] = time.time()
                dst["replaced_by_asset_id"] = src["asset_id"]
                rows.append(dst)
                row["replaced_asset_id"] = dst["asset_id"]
            bindings[skey], expected[skey] = None, None
            row["last_moved_by"] = dict(actor)
        else:
            row = dst or _new(data, actor, "copy")
            row["version"] = _revision(row["asset_id"], data, actor, "copy")
            row["copied_from"] = ref
            row["version"]["copied_from"] = ref
            row.pop("source_recording_id", None)
            if src.get("source_recording_id"):
                row["source_recording_id"] = src["source_recording_id"]
                row["version"]["source_recording_id"] = src["source_recording_id"]
        rows.append(row)
        bindings[tkey] = row["asset_id"]
        plan = {"assets": rows, "bindings": bindings, "expected": expected}
        _path(tkey).parent.mkdir(parents=True, exist_ok=True)
        if move:
            plan["identities"] = {tkey: _file_identity(_path(skey))}
            _mutate(conn, plan, lambda: os.replace(_path(skey), _path(tkey)))
        else:
            with _staged(_path(tkey), lambda temp: _write_bytes_file(temp, data)) as staged:
                plan["identities"] = {tkey: _file_identity(staged)}
                _mutate(conn, plan, lambda: os.replace(staged, _path(tkey)))
        return row


def delete_file(path):
    key = _key(path)
    with mutation_lock(), _database() as conn:
        row = _sync(conn, key)
        if not row:
            return None
        row["deleted_at"], row["deleted_by"] = time.time(), dict(current_actor())
        plan = {"assets": [row], "bindings": {key: None}, "expected": {key: None}}
        _mutate(conn, plan, lambda: _path(key).unlink())
        return row


def delete_module(path):
    import shutil
    root, target = Path(config.TASK_DIR).resolve(), Path(path).absolute()
    if target == root or root not in target.parents or target.resolve() != target:
        raise ValueError("cannot delete task root or unsafe module")
    with mutation_lock():
        if not target.exists():
            return
        for item in sorted(target.rglob("*")):
            if is_asset_path(item):
                delete_file(item)
        shutil.rmtree(target)


def backup_attribution(path, data):
    """A backup describes its saved bytes, never the user requesting a backup."""
    if is_asset_path(path):
        try:
            row, actual = snapshot(path)
            if actual == data:
                return row["version"]
        except FileNotFoundError:
            pass
    return {"author": dict(UNKNOWN), "source": "historical", "content_sha256": _hash(data), "size": len(data)}


def verified_backup_attribution(meta, data):
    saved = meta.get("attribution") or {}
    if saved.get("content_sha256") == _hash(data):
        return saved
    return {"author": dict(UNKNOWN), "source": "external" if saved else "historical",
            "content_sha256": _hash(data), "size": len(data)}


def migrate(*, apply=False):
    """Read-only inventory by default. No reliable legacy stable IDs exist here.

    Thus historical usernames/comments are never interpreted as creator evidence.
    Explicit apply backs up metadata first, then imports unknown revisions using
    the same lock/reconciliation protocol. YAML files are never modified.
    """
    root = Path(config.TASK_DIR).resolve()
    paths = sorted(p for p in root.rglob("*") if is_asset_path(p))
    report = {"total": len(paths), "unknown": 0, "conflicts": 0, "tracked": 0, "applied": 0}
    rows = {}
    database = db_path()
    if database.exists():
        with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as conn:
            for key, data in conn.execute("SELECT b.path,a.data FROM bindings b JOIN assets a ON a.id=b.asset_id"):
                rows[key] = json.loads(data)
    valid = []
    for path in paths:
        try:
            key = _key(path)
            data = path.read_bytes()
            row = rows.get(key)
            if row:
                report["tracked"] += 1
                if row["version"]["content_sha256"] != _hash(data):
                    report["conflicts"] += 1
            if not row or row["creator"]["kind"] == "unknown":
                report["unknown"] += 1
            valid.append(path)
        except (OSError, ValueError):
            report["conflicts"] += 1
    if not apply:
        return report
    with mutation_lock():
        backup = database.with_name(f"assets.before-migration.{time.time_ns()}.{uuid.uuid4().hex[:8]}.sqlite3")
        source = sqlite3.connect(database) if database.exists() else sqlite3.connect(":memory:")
        try:
            with sqlite3.connect(backup) as target:
                source.backup(target)
            os.chmod(backup, 0o600)
        finally:
            source.close()
        report["backup"] = str(backup)
        with _database() as conn:
            for path in valid:
                key = _key(path)
                before = _row(conn, key)
                after = _sync(conn, key)
                report["applied"] += int(before != after)
    return report


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Inventory YAML lineage; historical creators stay unknown.")
    parser.add_argument("--apply", action="store_true", help="back up metadata and import; default is read-only")
    args = parser.parse_args()
    print(json.dumps(migrate(apply=args.apply), ensure_ascii=False, indent=2))
