"""Separate test process deliberately dies mid-write; contains no real data."""
import io
import os
import sys
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from task_server.services import week5_web_service as service

profile={'user_id':'user1','username':'user','status':'active','must_change_password':False,
         'permissions':['ui.view'],'is_superuser':False,'scope':{'ui_apps':['app1']}}
worker={'scenario':'ui_catalog_count','version':1,'status':'passed','error_kind':None,'total':0}
buffer=io.BytesIO()
with zipfile.ZipFile(buffer,'w') as archive:
    archive.writestr('trace.trace','{"event":"ready"}')

def interrupted(root, run_id, image, _trace):
    directory=root/run_id/'evidence'
    directory.mkdir(parents=True)
    (directory/'screenshot.png').write_bytes(image)
    os._exit(72)

with patch.object(service,'_profile',return_value=profile), \
     patch.object(service,'_execute',return_value=(worker,b'\x89PNG\r\n\x1a\nfixture',buffer.getvalue())), \
     patch.object(service,'_save_files',side_effect=interrupted):
    service.run('user1','user','x'*43,db_path=Path(sys.argv[1]),root=Path(sys.argv[2]))
os._exit(0)
