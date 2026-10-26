import io
import json
import time
from unittest.mock import patch

import pytest
from task_server.services.ui_video_source_service import VideoStore


def seed(store, count):
    with store.db() as db:
        for i in range(count):
            doc={'source_id':f'uvs_{i:032x}','owner_user_id':'other','app_id':'other.app',
                 'status':'ready','updated_at':time.time(),'retained':True}
            store._save(db,doc)


def upload(store):
    return store.upload({'user_id':'one'},'com.example.app','x.mp4',io.BytesIO(b'video'),5)


def test_configured_count_admits_new_video_without_removing_history(tmp_path, monkeypatch):
    monkeypatch.setenv('TASK_UI_VIDEO_MAX_SOURCES','200')
    store=VideoStore(tmp_path);seed(store,100)
    assert upload(store)['status']=='queued'
    with store.db() as db:assert db.execute('SELECT COUNT(*) FROM sources').fetchone()[0]==101
    assert store.get('uvs_'+32*'0','other','other.app')['retained']


@pytest.mark.parametrize('value',[None,'','bad','0','-1','1001','1.5'])
def test_default_and_invalid_count_keep_100_limit(tmp_path,monkeypatch,value):
    if value is None:monkeypatch.delenv('TASK_UI_VIDEO_MAX_SOURCES',raising=False)
    else:monkeypatch.setenv('TASK_UI_VIDEO_MAX_SOURCES',value)
    store=VideoStore(tmp_path);seed(store,100)
    with pytest.raises(ValueError,match='video_capacity'):upload(store)
    assert not list(tmp_path.glob('uvs_*'))


def test_configured_limit_is_still_enforced(tmp_path,monkeypatch):
    monkeypatch.setenv('TASK_UI_VIDEO_MAX_SOURCES','200')
    store=VideoStore(tmp_path);seed(store,200)
    with pytest.raises(ValueError,match='video_capacity'):upload(store)


@pytest.mark.parametrize('guard',['disk','concurrency'])
def test_larger_count_keeps_other_admission_guards(tmp_path,monkeypatch,guard):
    monkeypatch.setenv('TASK_UI_VIDEO_MAX_SOURCES','200')
    store=VideoStore(tmp_path)
    if guard=='disk':monkeypatch.setattr(store,'_disk_bytes',lambda:2*1024**3-1)
    else:
        for _ in range(4):upload(store)
    with pytest.raises(ValueError,match='video_capacity'):upload(store)
