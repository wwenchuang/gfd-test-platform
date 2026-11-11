"""Serve the real TaskHTTPHandler on loopback with a disposable scoped account.

Set PYTHONPATH to the checkout whose revision you are measuring. No production
secrets, jobs, device connections or background scheduler are needed.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import subprocess
import threading
import urllib.request
from tempfile import TemporaryDirectory

def main():
    args = argparse.ArgumentParser()
    args.add_argument('--checkout', required=True, type=Path)
    args.add_argument('--private-dir', required=True, type=Path)
    opt = args.parse_args()
    os.umask(0o077)
    private = opt.private_dir.resolve()
    private.mkdir(mode=0o700, exist_ok=False)
    root = opt.checkout.resolve()
    expected_path = '/api/platform/requirement-design/knowledge/evaluation'
    rev = subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()
    stat = subprocess.check_output(['git','-C',str(root),'status','--porcelain','--untracked-files=no'],text=True)
    if stat.strip():raise SystemExit('measurement checkout has modified tracked files')
    with TemporaryDirectory(dir=private) as temp:
        tmp = Path(temp)
        for key in ('TASK_DIR','REPORT_DIR','LEARNING_DIR','ASSET_DIR','CASE_DIR','GENERATE_JOB_DIR','KNOWLEDGE_DIR'):
            path=tmp/key;path.mkdir();os.environ[key]=str(path)
        os.environ.update(TASK_APP_ENV='test', TASK_AUTH_DB=str(tmp/'auth'/'identity.sqlite3'),
                          TASK_ADMIN_PASSWORD=secrets.token_urlsafe(32))
        os.chdir(root)
        import sys
        sys.path.insert(0,str(root))
        from task_server import config, identity
        from task_server.app import ThreadingHTTPServer, TaskHTTPHandler
        store=identity.get_identity_store();username='perf-readonly'
        password=secrets.token_urlsafe(30)
        user=store.create_user(config.TASK_ADMIN_USER,dict(username=username,display_name='只读压测',
            role_ids=['viewer'],scope={'ui_apps':['com.kfb.model']}))
        store.change_password(username,user['temporary_password'],password)
        folder=Path(os.environ['TASK_AUTH_DB']).parent/'requirement-design-materials'/'evaluation'
        folder.mkdir(parents=True)
        source=root/'docs/audits/knowledge-eval/holdout-report.json'
        shutil.copyfile(source,folder/'knowledge-holdout.json')
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with ThreadingHTTPServer(('127.0.0.1',0),TaskHTTPHandler) as server:
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            base=f'http://127.0.0.1:{server.server_port}'
            req=urllib.request.Request(base+'/api/auth/login',json.dumps(dict(username=username,password=password)).encode(),
                                       {'Content-Type':'application/json'})
            with opener.open(req,timeout=10) as r:
                assert r.status==200; obj=json.load(r); assert obj['ok'] and obj['user']==username;token=obj['token']
            try:
                with opener.open(urllib.request.Request(base+expected_path,headers={'Authorization':'Bearer '+token}),timeout=10) as r:
                    data=json.load(r);assert r.status==200 and data['ok'] and len(data['report']['questions'])==30
                (private/'token').write_text(token); (private/'token').chmod(0o600)
                metadata=dict(revision=rev,pid=os.getpid(),port=server.server_port,sqlite_path=os.environ['TASK_AUTH_DB'],
                              source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),route=expected_path,
                              target='isolated platform, no reverse proxy',account_role='viewer',scope='com.kfb.model')
                (private/'target.json').write_text(json.dumps(metadata))
                print('READY',rev[:12],server.server_port,flush=True)
                done=threading.Event()
                for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,lambda *_:done.set())
                done.wait()
            finally:
                try:opener.open(urllib.request.Request(base+'/api/auth/logout',data=b'{}',headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'}),timeout=5).close()
                finally:(private/'token').unlink(missing_ok=True);server.shutdown();thread.join()
if __name__=='__main__':main()
