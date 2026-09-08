#!/usr/bin/env python3
"""Create local credentials once; never print them or replace existing tokens."""
import os
from pathlib import Path
import secrets
root=Path(__file__).resolve().parent
folder=root/'secrets'
folder.mkdir(mode=0o700,exist_ok=True)
if folder.is_symlink():raise SystemExit('拒绝符号链接secrets目录')
folder.chmod(0o700)
for key in ('api','metrics','admin'):
    file=folder/(key+'_token')
    try:
        fd=os.open(file,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o644)
    except FileExistsError:
        if file.is_symlink() or len(file.read_text().strip())<32:raise SystemExit('已有令牌文件无效，请管理员检查')
    else:
        with os.fdopen(fd,'w') as out:out.write(secrets.token_hex(32)+'\n')
    # Parent 0700 protects host files; individual Compose-secret mounts must be
    # readable by the unprivileged container user. No secret directory mount.
    file.chmod(0o644)
print('令牌已准备，已有令牌保留。文件位于权限700的secrets目录；不会输出令牌。')
