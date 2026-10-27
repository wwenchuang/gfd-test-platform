"""Version-local private evidence paths preserve legacy source compatibility."""
import io
import pytest
from task_server.services.ui_video_source_service import VideoStore,public_source
from task_server.services import ui_video_workspace as ws


@pytest.fixture
def source(tmp_path):
    store=VideoStore(tmp_path/'store')
    doc=store.upload({'user_id':'owner'},'app','x.mp4',io.BytesIO(b'original'),8)
    return store,doc


def test_version_workspaces_isolate_old_and_new_files_and_legacy(source):
    store,doc=source
    legacy=store.root/doc['source_id']/'frames';legacy.mkdir();(legacy/'old.jpg').write_bytes(b'legacy')
    assert ws.resolve_frame_directory(store.root,doc)==legacy
    one=ws.create_analysis_workspace(store.root,doc,1)
    assert one==store.root/doc['source_id']/'analysis-v1'
    assert one.stat().st_mode&0o777==0o700
    assert (one/'frames').stat().st_mode&0o777==0o700
    (one/'frames'/'frame-001.jpg').write_bytes(b'one')
    newer={**doc,'analysis_version':2}
    two=ws.create_analysis_workspace(store.root,newer,2)
    (two/'frames'/'frame-001.jpg').write_bytes(b'two')
    assert (one/'frames'/'frame-001.jpg').read_bytes()==b'one'
    assert (legacy/'old.jpg').read_bytes()==b'legacy'
    assert ws.resolve_frame_directory(store.root,{**newer,'analysis_workspace_version':2})==two/'frames'


@pytest.mark.parametrize('marker',[None,True,0,-1,1.0,'1','../../frames',2])
def test_present_bad_or_stale_marker_never_falls_back(source,marker):
    store,doc=source
    (store.root/doc['source_id']/'frames').mkdir()
    with pytest.raises(ValueError):ws.resolve_frame_directory(store.root,{**doc,'analysis_workspace_version':marker})


@pytest.mark.parametrize('version',[True,0,-1,'1',2])
def test_creation_requires_exact_trusted_document_version(source,version):
    store,doc=source
    with pytest.raises(ValueError):ws.create_analysis_workspace(store.root,doc,version)


def test_source_and_workspace_symlinks_and_path_traversal_rejected(source,tmp_path):
    store,doc=source
    for bad in ['../escape','uvs_'+'a'*32+'/../escape']:
        with pytest.raises(ValueError):ws.create_analysis_workspace(store.root,{**doc,'source_id':bad},1)
    outside=tmp_path/'outside';outside.mkdir()
    (store.root/doc['source_id']/'analysis-v1').symlink_to(outside,target_is_directory=True)
    with pytest.raises(ValueError):ws.create_analysis_workspace(store.root,doc,1)


def test_retry_clears_only_new_private_evidence_and_preserves_old_version_directory(source):
    store,doc=source
    path=ws.create_analysis_workspace(store.root,doc,1);(path/'frames'/'evidence.jpg').write_bytes(b'old')
    private=dict(trajectory_audit={'secret':'pixels'},trajectory_record_index=['private'],trajectory_records=['private'],
                 analysis_workspace_version=1,analysis_workspace_path='/private/root',trajectory_record_root='/private/root')
    store.transition(doc['source_id'],{'queued'},'failed',**private)
    updated=store.retry(doc['source_id'],'owner','app')
    assert updated['analysis_version']==2
    assert all(k not in updated for k in private)
    assert (path/'frames'/'evidence.jpg').read_bytes()==b'old'
    assert store.transition(doc['source_id'],{'queued'},'ready',expected_version=1,trajectory_audit={'stale':True}) is None


def test_public_source_drops_new_private_fields_without_removing_existing_public_fields(source):
    _,doc=source
    fields=dict(trajectory_audit={'secret':1},trajectory_records=['secret'],trajectory_record_index=['secret'],
                analysis_workspace_version=1,analysis_workspace_path='/private/root',trajectory_record_root='/private/root')
    public=public_source({**doc,**fields,'events':[],'analysis_progress':{'phase':'observing'}})
    assert not set(fields)&set(public)
    assert public['events']==[] and public['analysis_progress']=={'phase':'observing'}


def test_http_version_workspace_privacy_and_legacy_guards(source,monkeypatch):
    """Route wiring retains auth lookup and membership before resolving file paths."""
    import re
    from types import SimpleNamespace
    from task_server import ui_video_http
    from task_server.services import ui_video_source_service as service
    store,doc=source;doc['media']={'frames':[dict(frame_id='frame-001',timestamp_ms=0),dict(frame_id='frame-end',timestamp_ms=1000)]}
    legacy=store.root/doc['source_id']/'frames';legacy.mkdir()
    (legacy/'frame-001.jpg').write_bytes(b'legacy');(legacy/'frame-end.jpg').write_bytes(b'tail')
    private=dict(trajectory_audit={'secret':1},trajectory_record_index=['secret'],analysis_workspace_version=1)
    regex_handlers=[];get_handlers={}
    def route_get(path):
        def bind(f):get_handlers[path]=f;return f
        return bind
    def route_regex(pattern):
        def bind(f):regex_handlers.append((pattern,f));return f
        return bind
    def unused(*a):return lambda f:f
    ui_video_http.register_routes(route_get,unused,route_regex,unused)
    monkeypatch.setattr(ui_video_http,'_mark',lambda *a:None)
    lookups=[]
    def owned(actor,identifier,app):
        lookups.append((identifier,app))
        if app!='app':raise KeyError(identifier)
        return store,{},doc
    monkeypatch.setattr(service,'owned_source',owned)
    class Handler:
        def __init__(self):self.wfile=io.BytesIO();self.status=None;self.json=None
        def _json(self,value,status=200):self.status=status;self.json=value
        def send_response(self,status):self.status=status
        def send_header(self,*a):pass
        def end_headers(self):pass
    pattern,get=regex_handlers[0]
    def fetch(asset='frames/frame-001',**qs):
        h=Handler();match=re.match(pattern,'/api/ui-video-sources/'+doc['source_id']+('/'+asset if asset else ''))
        get(h,{'app_id':'app',**qs},match);return h
    assert fetch().wfile.getvalue()==b'legacy'
    assert fetch('frames/frame-end').wfile.getvalue()==b'tail'
    assert fetch(analysis_version='2').status==404
    assert fetch(app_id='other').status==404
    assert fetch('frames/frame-002').status==404
    workspace=ws.create_analysis_workspace(store.root,doc,1)
    (workspace/'frames'/'frame-001.jpg').write_bytes(b'version1')
    doc.update(private)
    assert fetch(analysis_version='1').wfile.getvalue()==b'version1'
    assert not set(private)&set(fetch('').json['source'])
    for bad in [None,'../frames',2,True]:
        doc['analysis_workspace_version']=bad
        h=fetch();assert h.status==400 and h.wfile.getvalue()==b''
    assert lookups
    from task_server.services import ui_execution_contract,ui_case_draft_service
    monkeypatch.setattr(service,'service_store',lambda:store)
    monkeypatch.setattr(ui_execution_contract,'require_ui_actor',lambda *a,**k:{'user_id':'owner'})
    monkeypatch.setattr(ui_case_draft_service,'_application',lambda app:None)
    store.transition(doc['source_id'],{'queued'},'failed',**private)
    listed=Handler();get_handlers['/api/ui-video-sources'](listed,{'app_id':'app'})
    assert listed.status==200 and len(listed.json['sources'])==1
    assert not set(private)&set(listed.json['sources'][0])
