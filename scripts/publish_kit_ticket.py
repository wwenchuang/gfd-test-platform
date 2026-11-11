"""Register one immutable, app-scoped workbench investigation on the server."""
import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import secrets
import stat
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from task_server.services import practice_kit_regression_view as view
from task_server.services import requirement_evaluation_view as evaluation


def register(identity_root, source):
    fields = view.ticket_input(source)
    ticket_id = fields['id']
    filename = ticket_id + '.json'
    doc = dict(fields, schema_version=1, type=view.TICKET_TYPE, status=view.TICKET_STATUS,
               created_at=datetime.now(timezone.utc).isoformat())
    payload = (json.dumps(doc, ensure_ascii=False, indent=2) + '\n').encode()
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    with ExitStack() as stack:
        directory = os.open(identity_root, directory_flags)
        stack.callback(os.close, directory)
        for part in ('requirement-design-materials', 'evaluation', 'kit-tickets'):
            try:
                os.mkdir(part, mode=0o700, dir_fd=directory)
            except FileExistsError:
                pass
            directory = os.open(part, directory_flags, dir_fd=directory)
            stack.callback(os.close, directory)
        # Serialize cooperating CLI processes; readers only see complete files.
        fcntl.flock(directory, fcntl.LOCK_EX)
        try:
            fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        except FileNotFoundError:
            fd = None
        if fd is not None:
            with os.fdopen(fd, 'rb') as stream:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 128 * 1024:
                    raise ValueError('现有工单不可读取')
                existing = json.load(stream)
            expected = dict(doc, created_at=existing['created_at'])
            if existing != expected:
                raise ValueError('同一运行和观察编号已有不同工单，原件已保留')
            return {'created': False, 'id': ticket_id}

        temporary = '.' + secrets.token_hex(16) + '.tmp'
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=directory)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.rename(temporary, filename, src_dir_fd=directory, dst_dir_fd=directory)
        except BaseException:
            os.unlink(temporary, dir_fd=directory)
            raise
        os.fsync(directory)
    return {'created': True, 'id': ticket_id}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='登记一份内部排查工单并输出稳定编号')
    parser.add_argument('--identity-root', required=True, type=Path, help='身份数据库所在目录')
    parser.add_argument('--input', required=True, type=Path, help='私有输入 JSON')
    args = parser.parse_args()
    try:
        receipt = register(args.identity_root, json.loads(args.input.read_bytes()))
    except (OSError, KeyError, TypeError, ValueError, evaluation.Unavailable) as exc:
        parser.exit(1, f'工单未登记：{exc or "输入无效"}\n')
    print(json.dumps(receipt, ensure_ascii=False))
