"""One bounded video consumer per server process; restart requires explicit retry."""
import threading
import time

from task_server.services.operation_attribution import actor_context
from task_server.services.ui_execution_contract import AccessDenied, require_ui_actor
from task_server.services.ui_video_source_service import decode_video

_lock=threading.Lock()
_workers={}


def ensure_worker(store):
    key=str(store.root)
    with _lock:
        if key not in _workers:
            store.recover()
            wake=threading.Event()
            thread=threading.Thread(target=_loop,args=(store,wake),name='ui-video-worker',daemon=True)
            _workers[key]=wake
            thread.start()
        return _workers[key]


def _loop(store,wake):
    while True:
        wake.wait(10);wake.clear()
        try:
            with store.db() as db:
                row=db.execute("SELECT id FROM sources WHERE json_extract(document,'$.status')='queued' ORDER BY updated LIMIT 1").fetchone()
            if row:
                analyze_video_source(store,row[0])
                wake.set()
            else: store.cleanup()
        except Exception:
            # Errors are persisted by analyze; store outages back off naturally.
            time.sleep(1)


def analyze_video_source(store,identifier):
    document=store.transition(identifier,{'queued'},'decoding')
    if not document:return
    owner,app=document['owner_user_id'],document['app_id']
    version=document['analysis_version']
    def cancelled():
        latest=store.get(identifier,owner,app)
        return latest['status']=='cancelled' or latest['analysis_version']!=version
    try:
        from task_server.services.ui_case_draft_service import _application,_top3_examples
        from task_server.services.ui_video_analysis import analyze_frames
        with actor_context(document['actor']):
            require_ui_actor(document['actor'],permission='ui.edit',app_id=app);_application(app)
            media=decode_video(store.root/identifier/'source.mp4',store.root/identifier/'frames',cancelled)
            if media['black'] or media['static']:
                store.transition(identifier,{'decoding'},'review_needed',media=media,error='黑屏或静止视频没有可靠操作证据，请选择包含完整操作和结束结果的片段')
                return
            document=store.transition(identifier,{'decoding'},'analyzing',media=media)
            if not document:return
            document['baseline_examples']=_top3_examples(app,document['goal'] or '录屏操作步骤')
            result=analyze_frames(document,store.root/identifier,cancelled)
            if cancelled():raise ValueError('video_cancelled')
            require_ui_actor(document['actor'],permission='ui.edit',app_id=app);_application(app)
            status='review_needed' if result['multiple_scenarios'] or not result['events'] else 'ready'
            message='包含多个独立场景或缺少可靠动作，请分段后重新上传' if status=='review_needed' else ''
            segments=[{'source_id':identifier,'segment_id':str(i),'start_ms':e['start_ms'],'end_ms':e['end_ms'],'frame_refs':e['frame_refs']} for i,e in enumerate(result['events'])]
            store.transition(identifier,{'analyzing'},status,**result,segments=segments,candidate_steps=result['events'],error=message)
    except Exception as error:
        code=str(error) if isinstance(error,ValueError) and str(error).startswith('video_') else 'video_access_changed' if isinstance(error,AccessDenied) else 'video_analysis_failed'
        messages={'video_decoder_missing':'服务器缺少 ffmpeg/ffprobe，请管理员安装并配置 UI_VIDEO_FFMPEG/UI_VIDEO_FFPROBE 后重试',
                  'video_split_required':'转场/候选帧超过60张，请按连续场景分段上传',
                  'video_limits':'视频超过10分钟或画面尺寸限制，请压缩或分段上传',
                  'video_decode_invalid':'视频无法完整解码，请检查格式伪装、损坏或截断后重新导出',
                  'video_access_changed':'账号或应用权限已变化，分析停止',
                  'video_cancelled':'已取消',
                  'video_decode_timeout':'解码超时，请缩短视频后重试'}
        store.transition(identifier,{'decoding','analyzing'},'cancelled' if code=='video_cancelled' else 'failed',error=messages.get(code,'分析未能返回可靠步骤，请重试或缩短片段；原视频保留'),error_code=code)
