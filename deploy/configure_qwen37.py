#!/usr/bin/env python3
"""Preview or explicitly apply a precise Qwen 3.8 Max -> 3.7 Plus migration.

No service restart and no automatic installer hook. Both files must exist and
validate before any change. Output contains counts and backup paths, never keys
or configuration contents. Use the service owner (or root) to preserve ownership.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shlex
import stat
import tempfile


OLD = 'qwen3.8-max'
NEW = 'qwen3.7-plus'
ENV_KEYS = frozenset(('DASHSCOPE_MODEL', 'DASHSCOPE_VL_MODEL', 'MIDSCENE_MODEL_NAME'))
MAX_FILE_BYTES = 4 * 1024 * 1024


class MigrationError(Exception):
    """A fixed, non-sensitive error code only."""


def _read(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_FILE_BYTES:
                raise MigrationError('file_invalid')
            data = stream.read(MAX_FILE_BYTES + 1)
            after = os.fstat(stream.fileno())
            if len(data) > MAX_FILE_BYTES or _fingerprint(before) != _fingerprint(after):
                raise MigrationError('file_changed_during_read')
            return data, after
    except FileNotFoundError:
        raise MigrationError('file_missing') from None
    except OSError:
        raise MigrationError('file_unreadable_or_unsafe') from None


def _fingerprint(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode))


def _env_value(rhs):
    # Only a whitespace-delimited, unquoted # starts a comment. No shell is run.
    quote, escaped, end = '', False, len(rhs)
    for index, char in enumerate(rhs):
        if escaped:
            escaped = False
        elif char == '\\' and quote != "'":
            escaped = True
        elif quote:
            if char == quote:
                quote = ''
        elif char in ('"', "'"):
            quote = char
        elif char == '#' and (index == 0 or rhs[index - 1].isspace()):
            end = index
            break
    if quote or escaped or '\0' in rhs:
        raise MigrationError('invalid_env')
    content = rhs[:end].rstrip()
    try:
        tokens = shlex.split(content, comments=False, posix=True)
    except ValueError:
        raise MigrationError('invalid_env') from None
    if len(tokens) > 1:
        raise MigrationError('invalid_env')
    return tokens[0] if tokens else '', content, rhs[len(content):]


def _env(data):
    try:
        source = data.decode('utf-8')
    except UnicodeError:
        raise MigrationError('invalid_env') from None
    result, seen, count = [], set(), 0
    for raw in source.splitlines(keepends=True):
        ending = '\r\n' if raw.endswith('\r\n') else '\n' if raw.endswith('\n') else ''
        line = raw[:-len(ending)] if ending else raw
        if not line.strip() or line.lstrip().startswith('#'):
            result.append(raw)
            continue
        match = re.fullmatch(r'(\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*)(.*)', line)
        if not match:
            raise MigrationError('invalid_env')
        prefix, key, rhs = match.groups()
        _value, content, suffix = _env_value(rhs)
        if key in ENV_KEYS:
            if key in seen:
                raise MigrationError('duplicate_model_assignment')
            seen.add(key)
            if content in (OLD, '"' + OLD + '"', "'" + OLD + "'"):
                quote = content[0] if content[:1] in ('"', "'") and content[-1:] == content[:1] else ''
                raw = prefix + quote + NEW + quote + suffix + ending
                count += 1
        result.append(raw)
    return ''.join(result).encode('utf-8'), count


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise MigrationError('invalid_providers')
        result[key] = value
    return result


def _invalid_constant(_value):
    raise MigrationError('invalid_providers')


def _providers(data):
    try:
        document = json.loads(data.decode('utf-8'), object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise MigrationError('invalid_providers') from None
    if not isinstance(document, dict) or not isinstance(document.get('providers'), dict):
        raise MigrationError('invalid_providers')
    count = 0
    for provider in document['providers'].values():
        if (not isinstance(provider, dict) or not isinstance(provider.get('model'), str)
                or ('name' in provider and not isinstance(provider['name'], str))):
            raise MigrationError('invalid_providers')
        if provider['model'] != OLD:
            continue
        provider['model'] = NEW
        if 'name' in provider:
            name = re.sub(r'qwen\s*3\.8[\s_-]*max', 'Qwen 3.7 Plus', provider['name'], flags=re.I)
            provider['name'] = re.sub(r'3\.8[\s_-]*max', '3.7 Plus', name, flags=re.I)
        count += 1
    if not count:
        return data, 0
    return (json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8'), count


def _copy(record, data, *, backup=False):
    path, info = record['path'], record['stat']
    prefix = '.' + path.name + ('.qwen37-backup-' if backup else '.qwen37-stage-')
    fd, name = tempfile.mkstemp(prefix=prefix, dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'wb') as stream:
            current = os.fstat(stream.fileno())
            if (current.st_uid, current.st_gid) != (info.st_uid, info.st_gid):
                os.fchown(stream.fileno(), info.st_uid, info.st_gid)
            os.fchmod(stream.fileno(), 0o600 if backup else stat.S_IMODE(info.st_mode))
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        return temporary
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _sync_directory(path):
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _still_original(record):
    data, info = _read(record['path'])
    return data == record['original'] and _fingerprint(info) == _fingerprint(record['stat'])


def _apply(records, backups):
    staged, replaced = {}, []
    try:
        # Finish every backup/stage before replacing either configuration.
        for record in records:
            backups[record['role']] = str(_copy(record, record['original'], backup=True))
            staged[record['role']] = _copy(record, record['updated'])
            _sync_directory(record['path'].parent)
        if not all(_still_original(record) for record in records):
            raise MigrationError('concurrent_change')
        for record in records:
            if not _still_original(record):
                raise MigrationError('concurrent_change')
            os.replace(staged[record['role']], record['path'])
            staged.pop(record['role'])
            replaced.append(record)
            _sync_directory(record['path'].parent)
        return
    except (OSError, MigrationError):
        rollback_failed = False
        for record in reversed(replaced):
            try:
                # Do not overwrite an unrelated writer's later configuration.
                if _read(record['path'])[0] != record['updated']:
                    rollback_failed = True
                    continue
                restore = _copy(record, record['original'])
                try:
                    os.replace(restore, record['path'])
                    _sync_directory(record['path'].parent)
                finally:
                    restore.unlink(missing_ok=True)
            except (OSError, MigrationError):
                rollback_failed = True
        raise MigrationError('apply_failed_restore_from_backups' if rollback_failed else 'apply_failed_rolled_back') from None
    finally:
        for path in staged.values():
            path.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', type=Path, default=Path('/opt/midscene.env'))
    parser.add_argument('--providers-file', type=Path, default=Path('/opt/ai-gateway/config/providers.json'))
    parser.add_argument('--apply', action='store_true', help='Write validated replacements; default only previews counts')
    args = parser.parse_args(argv)
    mode, backups = 'apply' if args.apply else 'dry_run', {}
    try:
        records = []
        for role, path, transform in (('env', args.env_file, _env), ('providers', args.providers_file, _providers)):
            original, info = _read(path)
            updated, count = transform(original)
            records.append(dict(role=role, path=path.absolute(), stat=info, original=original, updated=updated, count=count))
        if records[0]['path'] == records[1]['path']:
            raise MigrationError('paths_must_differ')
        changes = {record['role']:record['count'] for record in records}
        changed = [record for record in records if record['count']]
        if args.apply and changed:
            _apply(changed, backups)
        status = 'unchanged' if not changed else 'applied' if args.apply else 'changes_available'
        print(json.dumps({'mode':mode, 'status':status, 'changes':changes, 'backups':backups}, ensure_ascii=False))
        return 0
    except MigrationError as error:
        code = str(error)
    except (OSError, ValueError, TypeError, RecursionError):
        code = 'migration_unavailable'
    print(json.dumps({'mode':mode, 'status':'error', 'code':code, 'backups':backups}, ensure_ascii=False))
    return 3 if code == 'apply_failed_restore_from_backups' else 2


if __name__ == '__main__':
    raise SystemExit(main())
