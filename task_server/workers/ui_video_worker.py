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
    'video_expected_missing','video_retry_invalid','video_coverage_resolution_invalid',
})


def _failure_code(error):
    if isinstance(error,AccessDenied):return 'video_access_changed'
    # Never stringify arbitrary model/provider exceptions, even for public state.
    argument=error.args[0] if error.args else None
    return argument if isinstance(error,ValueError) and type(argument) is str and argument in _FAILURE_CODES else 'video_analysis_failed'


def _private_target_failure(error,code,document):
    """Bounded evidence saved under the existing owner/app source permissions."""
    pair=getattr(error,'target_failure_evidence',None)
    if code!='video_model_invalid' or type(pair) is not dict or set(pair)!={'frame_id','observed_target','probe_target'}:return {}
    if type(pair['frame_id']) is not str or pair['frame_id'] not in {f.get('frame_id') for f in document.get('media',{}).get('frames',[])}:return {}
    if any(type(pair[k]) is not str or not 1<=len(pair[k])<=400 for k in ('observed_target','probe_target')):return {}
    return dict(pair)


def _log_failure(identifier,stage,error,code):
    if code=='video_cancelled':category='cancelled'
    elif isinstance(error,AccessDenied):category='access_changed'
    elif isinstance(error,TimeoutError):category='timeout'
    elif isinstance(error,json.JSONDecodeError):category='model_json'
    elif stage in {'analysis','input_recovery'}:
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
    # Gateway wrappers preserve __cause__; retain locations and types only.
    # Never stringify nested provider/schema exceptions (they may contain media).
    causes=[];seen={id(error)};cause=error.__cause__
    while cause is not None and id(cause) not in seen and len(causes)<4:
        seen.add(id(cause))
        causes.append({'exception_class':type(cause).__name__,
            'traceback':[{'file':Path(frame.f_code.co_filename).name,
                          'function':frame.f_code.co_name,'line':line}
                         for frame,line in traceback.walk_tb(cause.__traceback__)][-8:]})
        cause=cause.__cause__
    if causes:detail['causes']=causes
    from task_server.services.ai_skill_service import SkillSchemaError
    if isinstance(error, SkillSchemaError):
        schema_path = getattr(error, 'schema_path', None)
        if type(schema_path) is str and re.fullmatch(r'\$(?:\.[a-zA-Z_][a-zA-Z0-9_]*){0,8}', schema_path):
            detail['schema_path'] = schema_path
    target=getattr(error,'target_diagnostic',None)
    if code=='video_model_invalid' and type(target) is dict:
        if (type(target.get('frame_id')) is str and re.fullmatch(r'frame-[0-9]{3,6}',target['frame_id']) and
            type(target.get('binding')) is str and target['binding'] in {'explicit','nearby'} and target.get('comparison')=='mismatch'):
            detail['target_diagnostic']={k:target[k] for k in ('frame_id','binding','comparison')}
    if code=='video_reference_invalid':
        reason=getattr(error,'reference_reason',None)
        shape=getattr(error,'reference_shape',None)
        if type(reason) is str and reason in {'count','outside_window','duplicate','order','event_order'}:
            detail['reference_reason']=reason
        if reason=='count' and type(shape) is str and shape in {'not_array','empty','too_many'}:
            detail['reference_shape']=shape
    if code=='video_coverage_resolution_invalid':
        reason=getattr(error,'coverage_reason',None)
        if type(reason) is str and reason in {
            'return_target_not_supported','return_anchors_not_overlapping',
            'return_touch_not_in_anchor','return_control_not_visible',
            'return_single_gesture_not_explicit','return_gesture_not_explicit',
            'return_second_press_not_excluded',
        }:
            detail['coverage_reason']=reason
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
    from task_server.services.ui_video_runtime import selected
    if selected(identifier):
        return _analyze_runtime_source(store,identifier)
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
                store.transition(identifier,{'decoding'},'review_needed',expected_version=version,media=media,error='黑屏或静止视频没有可靠操作证据，请选择包含完整操作和结束结果的片段')
                return
            stage='persist'
            document=store.transition(identifier,{'decoding'},'analyzing',expected_version=version,media=media)
            if not document:return
            stage='baseline'
            document['baseline_examples']=_top3_examples(app,document['goal'] or '录屏操作步骤')
            stage='analysis'
            def progress(phase,completed,total,events):
                if cancelled():raise ValueError('video_cancelled')
                store.transition(identifier,{'analyzing'},'analyzing',expected_version=version,
                    analysis_progress={'phase':phase,'completed':completed,'total':total},raw_events=events,
                    **({'first_pass_events':events} if phase=='reconciling' else {}))
            result=analyze_whole_frames(document,store.root/identifier,cancelled,progress)
            if cancelled():raise ValueError('video_cancelled')
            from task_server.services.ui_video_business_inputs import recover_business_inputs
            stage='input_recovery'
            result=recover_business_inputs(result,document,store.root/identifier,cancelled)
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
            store.transition(identifier,{'analyzing'},status,expected_version=version,**result,segments=segments,candidate_steps=result['events'],error=message)
    except Exception as error:
        code=_failure_code(error)
        _log_failure(identifier,stage,error,code)
        messages={'video_coverage_resolution_invalid':'重复操作的前后画面仍有歧义，尚未生成可执行用例；原视频已保留',
                  'video_required_action_missing':'用例整理遗漏必要业务操作，请重试分析',
                  'video_expected_missing':'未能确认点击或滚动后的稳定结果，请保留操作后画面再重试；原视频保留',
                  'video_decoder_missing':'服务器缺少 ffmpeg/ffprobe，请管理员安装并配置 UI_VIDEO_FFMPEG/UI_VIDEO_FFPROBE 后重试',
                  'video_split_required':'视频操作变化过于密集，超出完整证据容量，请缩短连续场景后重试',
                  'video_retry_invalid':'失败重试与后续业务无法可靠区分，请核对录屏；原视频保留',
                  'video_limits':'视频超过10分钟或画面尺寸限制，请压缩或分段上传',
                  'video_decode_invalid':'视频无法完整解码，请检查格式伪装、损坏或截断后重新导出',
                  'video_access_changed':'账号或应用权限已变化，分析停止',
                  'video_cancelled':'已取消',
                  'video_decode_timeout':'解码超时，请缩短视频后重试'}
        store.transition(identifier,{'decoding','analyzing'},'cancelled' if code=='video_cancelled' else 'failed',expected_version=version,error=messages.get(code,'分析未能返回可靠步骤，请重试或缩短片段；原视频保留'),error_code=code,failure_diagnostic=_private_target_failure(error,code,document))


def _analyze_runtime_source(store,identifier):
    """One server-enrolled version; all publication stays in the parent worker."""
    from task_server.services.ui_video_runtime import MODE, RuntimeBudget, run_runtime
    from task_server.services.ui_video_workspace import MARKER, create_analysis_workspace
    from task_server.services.ui_case_draft_service import _application, _top3_examples
    from task_server.services.ui_video_analysis import draft_proposal
    with store.db() as db:
        row=db.execute('SELECT document FROM sources WHERE id=?',(identifier,)).fetchone()
    if not row:return
    original=json.loads(row[0])
    owner,app,version=original['owner_user_id'],original['app_id'],original['analysis_version']
    actor=original['actor']
    selection={'mode':MODE,'source_id':identifier,'owner_user_id':owner,'app_id':app,'analysis_version':version}
    stage='authorize'
    document=original
    def current(allowed):
        latest=store.get(identifier,owner,app)
        if (latest['source_id']!=identifier or latest['owner_user_id']!=owner or latest['app_id']!=app or
            latest['analysis_version']!=version or latest['status'] not in allowed):
            raise ValueError('video_cancelled')
        return latest
    def authorize():
        profile=require_ui_actor(actor,permission='ui.edit',app_id=app)
        if profile['user_id']!=owner:raise AccessDenied('video_access_changed')
        try:_application(app)
        except ValueError:raise AccessDenied('video_access_changed') from None
    def check():
        latest=current({'decoding','analyzing'})
        if latest.get('video_runtime_selection')!=selection:raise ValueError('video_cancelled')
        authorize()
    def cancelled():
        check()
        return False
    def transition(allowed,status,**fields):
        check()
        value=store.transition(identifier,allowed,status,expected_version=version,
            expected_owner=owner,expected_app=app,**fields)
        if value is None:raise ValueError('video_cancelled')
        return value
    def publish(**fields):
        return transition({'analyzing'},'analyzing',**fields)
    with actor_context(actor):
        try:
            current({'queued'})
            authorize()
            document=store.transition(identifier,{'queued'},'decoding',expected_version=version,
                expected_owner=owner,expected_app=app,video_runtime_selection=selection)
            if document is None:return
            check()
            workspace=create_analysis_workspace(store.root,document,version)
            document=transition({'decoding'},'decoding',**{MARKER:version})
            stage='decode'
            media=decode_video(store.root/identifier/'source.mp4',workspace/'frames',cancelled)
            budget=RuntimeBudget((len(media['frames'])+7)//8)
            budget.check()
            if media['black'] or media['static']:
                transition({'decoding'},'review_needed',media=media,
                    error='黑屏或静止视频没有可靠操作证据，请选择包含完整操作和结束结果的片段')
                return
            stage='persist'
            document=transition({'decoding'},'analyzing',media=media)
            stage='baseline'
            check();budget.check()
            document['baseline_examples']=_top3_examples(app,document['goal'] or '录屏操作步骤')
            check();budget.check()
            stage='analysis'
            result=run_runtime(document,workspace,store.root/identifier/'source.mp4',check,publish,budget=budget)
            check();budget.check()
            status='review_needed' if result['multiple_scenarios'] or not result['events'] else 'ready'
            if status=='ready':
                stage='draft_validation'
                draft_proposal({**document,**result})
            check();budget.check()
            segments=[{'source_id':identifier,'segment_id':str(i),'start_ms':e['start_ms'],
                'end_ms':e['end_ms'],'frame_refs':e['frame_refs']} for i,e in enumerate(result['events'])]
            stage='persist'
            transition({'analyzing'},status,**result,segments=segments,candidate_steps=result['events'],
                error='包含多个独立场景或缺少可靠动作，请分段后重新上传' if status=='review_needed' else '')
        except Exception as error:
            code=_failure_code(error)
            _log_failure(identifier,stage,error,code)
            try:
                # Terminal status only may settle after revoked access. It never
                # adds AI content, and cannot overwrite cancelled/new/foreign work.
                current({'queued','decoding','analyzing'})
                store.transition(identifier,{'queued','decoding','analyzing'},
                    'cancelled' if code=='video_cancelled' else 'failed',expected_version=version,
                    expected_owner=owner,expected_app=app,error_code=code,
                    error='账号或应用权限已变化，分析停止' if code=='video_access_changed' else
                    '分析未能返回可靠步骤，请重试或缩短片段；原视频保留')
            except (KeyError,ValueError):
                pass
