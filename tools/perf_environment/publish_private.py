"""Install a first W15 report + checked archive, without overwriting an existing batch."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import stat
import tempfile
import zipfile

def main():
    ps=argparse.ArgumentParser()
    ps.add_argument("source",type=Path)
    ps.add_argument("--directory",required=True,type=Path)
    ps.add_argument("--reader",default="midscene")
    args=ps.parse_args()
    folder=args.directory.absolute()
    if not folder.is_dir() or any(p.is_symlink() for p in (folder,*folder.parents)):
        raise SystemExit('invalid private publication directory')
    account=pwd.getpwnam(args.reader);names=('jmeter-readonly-evidence.zip','jmeter-readonly.json')
    archive, report=[(args.source/n).read_bytes() for n in names]
    obj=json.loads(report)
    if not (obj.get('schema_version')==1 and obj['scope']['app_ids']==['com.kfb.model'] and
            len(archive)<=8*1024**2 and len(archive)==obj['evidence']['bytes'] and
            hashlib.sha256(archive).hexdigest()==obj['evidence']['sha256']):
        raise SystemExit('private data failed verification')
    with zipfile.ZipFile(args.source/names[0]) as z:
        if z.testzip() or len(z.namelist())!=len(set(z.namelist())):raise SystemExit('invalid ZIP')
    # A concurrent or previous publication wins. Never silently replace it.
    for name, data in zip(names,[archive,report]):
        path=folder/name
        if path.exists() or path.is_symlink():
            st=path.lstat()
            if not stat.S_ISREG(st.st_mode) or st.st_nlink!=1 or path.read_bytes()!=data:
                raise SystemExit('different previous publication: '+name)
            if st.st_uid!=account.pw_uid or st.st_mode & 0o077:raise SystemExit('publication ownership mismatch')
    for name,data in zip(names,[archive,report]):
        path=folder/name
        if path.exists():continue
        with tempfile.NamedTemporaryFile(dir=folder,delete=False) as tmp:
            tmp.write(data);tmp.flush();os.fsync(tmp.fileno());os.fchown(tmp.fileno(),account.pw_uid,account.pw_gid)
            os.fchmod(tmp.fileno(),0o600)
        try:os.link(tmp.name,path)
        finally:os.unlink(tmp.name)
    print('private artifacts verified:',len(obj['stages']),'stages;',len(archive),'ZIP bytes')
if __name__=='__main__':main()
