"""One bounded video consumer per server process; restart requires explicit retry."""
import json
import logging
import re
import traceback
from pathlib import Path
import threading
import time

from task_server.services.operation_attribution import actor_context
from task_server.services.ui_execution_contract import AccessDenied, require_ui_actor
from task_server.services.ui_video_source_service import decode_video

_LOG=logging.getLogger(__name__)
_FAILURE_CODES=frozenset({
    'video_decoder_missing','video_split_required','video_limits','video_decode_invalid',
    'video_metadata_invalid','video_frames_invalid','video_decode_timeout','video_memory_limit',
    'video_cancelled','video_vision_limit','video_action_invalid','video_model_invalid',
    'video_model_timeline','video_required_action_missing','video_observation_missing','video_reference_invalid',
})


def _failure_code(error):
    if isinstance(error,AccessDenied):return 'video_access_changed'
    # Never stringify arbitrary model/provider exceptions, even for public state.
    argument=error.args[0] if error.args else None
    return argument if isinstance(error,ValueError) and type(argument) is str and argument in _FAILURE_CODES else 'video_analysis_failed'


def _log_failure(identifier,stage,error,code):
    if code=='video_cancelled':category='cancelled'
    elif isinstance(error,AccessDenied):category='access_changed'
    elif isinstance(error,TimeoutError):category='timeout'
    elif isinstance(error,json.JSONDecodeError):category='model_json'
    elif stage=='analysis':
        category='model_contract' if code in _FAILURE_CODES else 'model_schema' if isinstance(error,ValueError) else 'model_service'
    elif stage=='draft_validation':category='draft_contract'
    elif stage=='decode':category='decoder'
    elif stage in {'queue','persist'}:category='storage'
    elif stage=='baseline':category='baseline'
    else:category='internal'
    locations=[{'file':Path(frame.f_code.co_filename).name,'function':frame.f_code.co_name,'line':line}
               for frame,line in traceback.walk_tb(error.__traceback__)]
    detail={'source_id':identifier if re.fullmatch(r'uvs_[0-9a-f]{32}',identifier or '') else 'unassigned',
            'stage':stage,'exception_class':type(error).__name__,'category':category,'traceback':locations[-12:]}
    # No exc_info, arbitrary error str, source lines, payload, prompt, identity,
    # media path, provider trace or model output enters this diagnostic.
    _LOG.warning('ui_video_failure %s',json.dumps(detail,ensure_ascii=True,sort_keys=True))


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
        identifier=''
        try:
            with store.db() as db:
                row=db.execute("SELECT id FROM sources WHERE json_extract(document,'$.status')='queued' ORDER BY updated LIMIT 1").fetchone()
            if row:
                identifier=row[0]
                analyze_video_source(store,identifier)
                wake.set()
            else: store.cleanup()
        except Exception as error:
            _log_failure(identifier,'queue',error,_failure_code(error))
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
    stage='authorize'
    try:
        from task_server.services.ui_case_draft_service import _application,_top3_examples
        from task_server.services.ui_video_analysis import analyze_whole_frames,draft_proposal
        with actor_context(document['actor']):
            require_ui_actor(document['actor'],permission='ui.edit',app_id=app);_application(app)
            stage='decode'
            media=decode_video(store.root/identifier/'source.mp4',store.root/identifier/'frames',cancelled)
            if media['black'] or media['static']:
                store.transition(identifier,{'decoding'},'review_needed',media=media,error='黑屏或静止视频没有可靠操作证据，请选择包含完整操作和结束结果的片段')
                return
            stage='persist'
            document=store.transition(identifier,{'decoding'},'analyzing',media=media)
            if not document:return
            stage='baseline'
            document['baseline_examples']=_top3_examples(app,document['goal'] or '录屏操作步骤')
            stage='analysis'
            result=analyze_whole_frames(document,store.root/identifier,cancelled)
            if cancelled():raise ValueError('video_cancelled')
            stage='reauthorize'
            require_ui_actor(document['actor'],permission='ui.edit',app_id=app);_application(app)
            status='review_needed' if result['multiple_scenarios'] or not result['events'] else 'ready'
            if status=='ready':
                # Validate the entire existing draft contract before advertising importability.
                stage='draft_validation'
                draft_proposal({**document,**result})
            message='包含多个独立场景或缺少可靠动作，请分段后重新上传' if status=='review_needed' else ''
            segments=[{'source_id':identifier,'segment_id':str(i),'start_ms':e['start_ms'],'end_ms':e['end_ms'],'frame_refs':e['frame_refs']} for i,e in enumerate(result['events'])]
            stage='persist'
            store.transition(identifier,{'analyzing'},status,**result,segments=segments,candidate_steps=result['events'],error=message)
    except Exception as error:
        code=_failure_code(error)
        _log_failure(identifier,stage,error,code)
        messages={'video_required_action_missing':'用例整理遗漏必要业务操作，请重试分析',
                  'video_decoder_missing':'服务器缺少 ffmpeg/ffprobe，请管理员安装并配置 UI_VIDEO_FFMPEG/UI_VIDEO_FFPROBE 后重试',
                  'video_split_required':'转场/候选帧超过60张，请按连续场景分段上传',
                  'video_limits':'视频超过10分钟或画面尺寸限制，请压缩或分段上传',
                  'video_decode_invalid':'视频无法完整解码，请检查格式伪装、损坏或截断后重新导出',
                  'video_access_changed':'账号或应用权限已变化，分析停止',
                  'video_cancelled':'已取消',
                  'video_decode_timeout':'解码超时，请缩短视频后重试'}
        store.transition(identifier,{'decoding','analyzing'},'cancelled' if code=='video_cancelled' else 'failed',error=messages.get(code,'分析未能返回可靠步骤，请重试或缩短片段；原视频保留'),error_code=code)
