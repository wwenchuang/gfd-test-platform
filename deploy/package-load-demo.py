#!/usr/bin/env python3
"""Explicit source-only bundle; local secrets and environment are never included."""
import argparse,gzip,hashlib,io,tarfile
from pathlib import Path
FILES=('server.py','Dockerfile','compose.yml','prometheus.yml','prepare.py','check.py','admin.py','README.md','openapi.json','.dockerignore','.gitignore')
def build(destination):
    root=Path(__file__).resolve().parent/'load-demo'
    out=Path(destination);out.mkdir(parents=True,exist_ok=True)
    buffer=io.BytesIO()
    with tarfile.open(fileobj=buffer,mode='w') as archive:
        for name in FILES:
            p=root/name
            if p.is_symlink() or not p.is_file():raise ValueError('非法包文件:'+name)
            data=p.read_bytes();item=tarfile.TarInfo(name);item.mode=0o644;item.size=len(data);item.mtime=0
            archive.addfile(item,io.BytesIO(data))
    target=out/'midscene-load-demo-1.0.0.tar.gz'
    target.write_bytes(gzip.compress(buffer.getvalue(),mtime=0))
    target.with_suffix('.gz.sha256').write_text(hashlib.sha256(target.read_bytes()).hexdigest()+'  '+target.name+'\n')
    return target
if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('destination');print(build(parser.parse_args().destination))
