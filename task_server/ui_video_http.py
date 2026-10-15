"""Streaming upload and private media; never buffer a video JSON/base64 body."""
import mimetypes
import re
import urllib.parse

from .services.ui_video_source_service import MAX_BYTES


def _error(handler,error):
    from .services.ui_execution_contract import AccessDenied
    code=str(error) if isinstance(error,ValueError) else 'video_access_denied' if isinstance(error,AccessDenied) else 'video_not_found' if isinstance(error,KeyError) else 'video_unavailable'
    status=403 if isinstance(error,AccessDenied) else 404 if isinstance(error,KeyError) else 429 if code=='video_capacity' else 400 if isinstance(error,ValueError) else 503
    messages={'video_size':'视频必须为1字节至200MB；请压缩或分段上传','video_capacity':'视频队列或私有存储已满，请稍后重试或联系管理员归档',
              'video_format':'请选择 MP4/MOV 文件','video_not_ready':'视频尚未形成可靠候选步骤，请先核对状态',
              'video_retry_state':'当前状态不能重试；已有草稿请继续编辑，勿覆盖修改',
              'video_access_denied':'当前账号无权访问该应用或视频','video_not_found':'未找到当前应用下属于你的录屏',
              'video_truncated_upload':'上传中断，请重新选择文件上传'}
    handler._json({'ok':False,'code':code,'error':messages.get(code,'视频请求未完成，请检查文件与状态后重试')},status)


def _mark(handler,identifier):
    from .operation_http import mark_resource
    mark_resource(handler,'ui_video',identifier)


def register_routes(route_get,route_post_prefix_before_body,route_get_regex,route_post_regex):
    @route_post_prefix_before_body('/api/ui-video-sources/upload')
    def upload(handler,qs,path):
        if path!='/api/ui-video-sources/upload':return handler._json({'ok':False,'error':'Not found'},404)
        from .services.ui_video_source_service import create_video_source
        from .services.ui_batch_dispatch import enabled
        # Unread or rejected binary bytes must never become another HTTP request.
        handler.close_connection=True
        try:
            if not enabled():return handler._json({'ok':False,'code':'feature_disabled','error':'测试服务暂未开放'},503)
            if handler.headers.get('Transfer-Encoding'):raise ValueError('video_size')
            length=int(handler.headers.get('Content-Length','0'))
            if not 1<=length<=MAX_BYTES:raise ValueError('video_size')
            if handler.headers.get('Content-Type','').split(';')[0] not in {'video/mp4','video/quicktime','application/octet-stream'}:raise ValueError('video_format')
            previous_timeout=handler.connection.gettimeout()
            handler.connection.settimeout(30)
            try:
                source=create_video_source(None,{'app_id':qs.get('app_id',''),'filename':qs.get('filename',''),
                    'goal':qs.get('goal',''),'stream':handler.rfile,'length':length})
            finally:handler.connection.settimeout(previous_timeout)
            _mark(handler,source['source_id']);handler._json({'ok':True,'source':source},202)
        except Exception as error:_error(handler,error)

    @route_get('/api/ui-video-sources')
    def listing(handler,qs):
        from .services.ui_video_source_service import service_store,public_source
        from .services.ui_execution_contract import require_ui_actor
        from .services.ui_case_draft_service import _application
        try:
            app=qs.get('app_id','');profile=require_ui_actor(None,permission='ui.view',app_id=app);_application(app)
            handler._json({'ok':True,'sources':[public_source(d) for d in service_store().list(profile['user_id'],app)]})
        except Exception as error:_error(handler,error)

    @route_get_regex(r'^/api/ui-video-sources/(uvs_[0-9a-f]{32})(?:/(video|frames/[^/]+))?$')
    def get(handler,qs,match):
        from .services.ui_video_source_service import owned_source,public_source
        try:
            identifier,asset=match.groups();store,_,doc=owned_source(None,identifier,qs.get('app_id',''))
            _mark(handler,identifier)
            if not asset:return handler._json({'ok':True,'source':public_source(doc)})
            if asset=='video':path=store.root/identifier/'source.mp4';mime='video/mp4'
            else:
                frame_id=asset.split('/')[1]
                if frame_id not in {f['frame_id'] for f in doc.get('media',{}).get('frames',[])}:raise KeyError(frame_id)
                path=store.root/identifier/'frames'/(frame_id+'.jpg');mime='image/jpeg'
            # Stream bounded chunks; do not allocate a 200MB response in HTTP memory.
            length=path.stat().st_size
            handler.send_response(200)
            for key,value in {'Content-Type':mime,'Content-Length':str(length),'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff','Content-Security-Policy':"sandbox; default-src 'none'",'Referrer-Policy':'no-referrer'}.items():handler.send_header(key,value)
            handler.end_headers()
            with path.open('rb') as source:
                while True:
                    chunk=source.read(65536)
                    if not chunk:break
                    handler.wfile.write(chunk)
        except (BrokenPipeError,ConnectionResetError):pass
        except Exception as error:_error(handler,error)

    @route_post_regex(r'^/api/ui-video-sources/(uvs_[0-9a-f]{32})/(cancel|retry|import)$')
    def action(handler,qs,match):
        from .services.ui_video_source_service import owned_source,public_source,import_video_draft
        from .services.ui_batch_dispatch import enabled
        try:
            if not enabled():return handler._json({'ok':False,'code':'feature_disabled','error':'测试服务暂未开放'},503)
            body=handler._body()
            if not isinstance(body,dict) or set(body)!={'app_id'}:raise ValueError('video_request_invalid')
            identifier,verb=match.groups();store,_,doc=owned_source(None,identifier,body['app_id'],'ui.edit')
            if verb=='import':result={'draft':import_video_draft(None,identifier,body['app_id'])}
            elif verb=='cancel':
                store.cancel(identifier)
                result={'source':public_source(store.get(identifier,doc['owner_user_id'],doc['app_id']))}
            else:
                updated=store.retry(identifier,doc['owner_user_id'],doc['app_id'])
                from task_server.workers.ui_video_worker import ensure_worker
                ensure_worker(store).set();result={'source':public_source(updated)}
            _mark(handler,identifier);handler._json({'ok':True,**result})
        except Exception as error:_error(handler,error)
