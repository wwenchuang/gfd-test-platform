"""Read pointer targets in a small context before observing the full window."""
import copy
import re
from . import ai_skill_service as ai


def probe(frames, details, assets, duration, cancelled, timeout_retry_budget=None):
    from .ui_video_analysis import _bind_whole_evidence, validate_analysis,_text,_check_question_count,_redact_question
    from .ui_video_automatic_motion import explicit_no_action_assertion
    timeout_retry_budget = timeout_retry_budget if isinstance(timeout_retry_budget, dict) else {'used': False}
    by_name = {asset['name']: asset for asset in assets}
    hints = []
    probe_secrets=[]
    schema_correction_used = False
    format_review='局部复核首轮输出未通过格式校验，重答可能遗漏相关操作，请核对原视频。'
    for detail in details[:6]:
        if cancelled():
            raise ValueError('video_cancelled')
        parent = next((f for f in frames if f['frame_id'] == detail['source_frame_id']),None)
        if parent is None:raise ValueError('video_reference_invalid')
        index = frames.index(parent)
        selected = frames[max(0, index-1):index+2]
        for offset in (800, 1600):
            later = next((f for f in frames[index+1:] if f['timestamp_ms'] >= parent['timestamp_ms']+offset), None)
            if later and later not in selected:
                selected.append(later)
        if len(selected) < 2:
            continue
        payload = {'phase':'whole-observe', 'frames':selected, 'image_frames':selected,
                   'detail_frames':[detail], 'duration_ms':duration}
        pending_questions=[];pending_multiple=False
        # One extra schema correction for the entire probe, not per detail.
        # Never turn malformed output into an empty successful observation.
        while True:
            if cancelled():raise ValueError('video_cancelled')
            try:
                result = ai.run_ai_skill('ui_video_observe', payload, version='touch-target',
                    image_assets=[by_name[f['frame_id']] for f in selected]+[by_name[detail['frame_id']]],
                    timeout=120, retry_count=0, max_tokens=5000, repair_invalid_json=False)
                if cancelled():raise ValueError('video_cancelled')
                if isinstance(result,dict) and isinstance(result.get('events'),list):
                    for event in result['events']:
                        if isinstance(event,dict) and event.get('action') in {'aiInput','navigateBack'} and isinstance(event.get('value'),str) and event['value']:
                            probe_secrets.append(_text(event['value'],4000))
                checked = validate_analysis(_bind_whole_evidence(result, selected), selected, duration,probe_secrets)
                break
            except ai.TouchTargetProviderTimeoutError:
                if cancelled():raise ValueError('video_cancelled') from None
                if timeout_retry_budget.get('used'):
                    raise
                timeout_retry_budget['used'] = True
                continue
            except ai.SkillSchemaError as error:
                if cancelled():raise ValueError('video_cancelled') from None
                if schema_correction_used:raise
                schema_correction_used = True
                pending_questions.append(format_review)
                payload['validation_feedback'] = error.model_feedback + ' 重新核对同一组原帧，按规定JSON结构输出；不补造操作，不省略events字段。'
            except ValueError as error:
                if cancelled():raise ValueError('video_cancelled') from None
                if getattr(error,'action_reason',None)!='navigate_back_contract':raise
                # Check every other event and field before any retry or
                # downgrade. This copy exists only for validation and is never
                # used as an observation, hint, or executable action.
                validation_copy=copy.deepcopy(result)
                for event in validation_copy['events']:
                    if event.get('action')=='navigateBack':
                        _text(event['target']); _text(event['value'],4000)
                        event.update(target='上一页面',value='')
                validate_analysis(_bind_whole_evidence(validation_copy,selected),selected,duration,probe_secrets)
                # Root and event questions are unverified, but must not vanish
                # when a malformed action receives its single format retry.
                roots=result.get('uncertainties')
                events=result.get('events')
                _check_question_count(roots,30)
                if len(events)>50:raise ValueError('video_model_invalid')
                pending_questions.extend(_text(q) for q in roots if q)
                for event in events:
                    if not isinstance(event,dict) or not isinstance(event.get('uncertainties',[]),list):
                        raise ValueError('video_model_invalid')
                    _check_question_count(event.get('uncertainties',[]),10)
                    pending_questions.extend('局部复核待核对：'+_text(q) for q in event.get('uncertainties',[]) if q)
                # A corrected reply may omit other candidates from the invalid
                # reply. Do not repeat unverified actions or their content;
                # require a human to check the recording before execution.
                pending_questions.append(format_review)
                pending_questions=list(dict.fromkeys(_redact_question(q,probe_secrets) for q in pending_questions))
                _check_question_count(pending_questions,30)
                pending_multiple|=result['multiple_scenarios']
                if schema_correction_used:
                    # No candidate from an invalid auxiliary response is a
                    # verified action. Continue only with a mandatory review.
                    checked={'events':[],'uncertainties':[],'multiple_scenarios':False}
                    break
                schema_correction_used = True
                payload['validation_feedback']='上次返回动作格式不符合合同。仅在原帧支持主动返回时使用navigateBack，target必须为上一页面、value必须为空字符串；不能确认时保留疑问，不编造动作。重新核对同一组原帧。'
        if cancelled():
            raise ValueError('video_cancelled')
        root_questions=list(dict.fromkeys(checked['uncertainties']+[_redact_question(q,probe_secrets) for q in pending_questions]))
        _check_question_count(root_questions,30)
        checked['multiple_scenarios']|=pending_multiple
        # A question about a non-parent event is still grounded only in that
        # event's referenced frame, never in the parent's detail image.
        for event in checked['events']:
            bound=parent['frame_id'] in event['frame_refs']
            handled=bound and (event['action'] in {'aiTap','navigateBack'} or
                               (event['action']=='aiAssert' and explicit_no_action_assertion(event['observation'])))
            if not handled and event['uncertainties']:
                hints.append({'frame_id':event['frame_refs'][0],'frame_refs':list(event['frame_refs']),
                              'scope':'event','kind':'unresolved_observation',
                              'questions':list(event['uncertainties']),'multiple_scenarios':False})
        if root_questions or checked['multiple_scenarios']:
            hints.append({'frame_id':parent['frame_id'],'frame_refs':[f['frame_id'] for f in selected],
                          'scope':'probe','kind':'unresolved_observation','questions':root_questions,
                          'multiple_scenarios':checked['multiple_scenarios']})
        bound_action_hints=[event for event in checked['events']
            if parent['frame_id'] in event['frame_refs'] and
            (event['action']=='navigateBack' or
             event['action']=='aiAssert' and explicit_no_action_assertion(event['observation']))]
        uncertain_taps=[event for event in checked['events'] if event['action']=='aiTap' and
            parent['frame_id'] in event['frame_refs'] and
            (event['uncertainties'] or root_questions or checked['multiple_scenarios'])]
        if bound_action_hints:
            coframed=[event for event in checked['events'] if parent['frame_id'] in event['frame_refs']]
            if uncertain_taps or len(bound_action_hints)>1 or len(coframed)>len(bound_action_hints):
                # A second plausible action in the same probe response cannot
                # be discarded just because a return/no-action hint exists.
                raise ValueError('video_model_invalid')
            for event in bound_action_hints:
                hints.append({'frame_id':parent['frame_id'],'action':event['action'],'target':event['target'],
                              'observation':event['observation'],
                              'requires_review':bool(event['uncertainties'] or root_questions or checked['multiple_scenarios']),
                              'questions':list(event['uncertainties']),
                              'multiple_scenarios':checked['multiple_scenarios']})
            continue
        if uncertain_taps:
            # Preserve an uncertain click as a review blocker, never as a
            # silently trusted target. Ambiguous multi-action probes fail closed.
            if len(checked['events'])!=1 or len(uncertain_taps)!=1:raise ValueError('video_model_invalid')
            event=uncertain_taps[0]
            hints.append({'frame_id':parent['frame_id'],'target':event['target'],
                          'observation':event['observation'],'requires_review':True,
                          'questions':list(event['uncertainties']),
                          'multiple_scenarios':checked['multiple_scenarios']})
            continue
        if checked['uncertainties'] or checked['multiple_scenarios'] or len(checked['events']) != 1:
            continue
        event = checked['events'][0]
        if (event['action'] == 'aiTap' and not event['uncertainties'] and
                parent['frame_id'] in event['frame_refs'] and event['target']):
            hints.append({'frame_id':parent['frame_id'], 'target':event['target'],'observation':event['observation']})
    # Later details can identify input strings previously mentioned in hints.
    for hint in hints:
        for key in ('target','observation'):
            if key in hint and not (key=='target' and hint.get('action')=='navigateBack' and hint[key]=='上一页面'):
                hint[key]=_redact_question(hint[key],probe_secrets)
        if 'questions' in hint:hint['questions']=[_redact_question(q,probe_secrets) for q in hint['questions']]
    return hints


def bind_uncertain_hints(events,hints,analysis=None):
    """Attach blockers only to events sharing the evidenced source frame."""
    question='触点目标或页面变化存在疑问，请对照原视频核对'
    root_questions=[]
    for hint in hints:
        if hint.get('kind')=='unresolved_observation':
            if hint.get('multiple_scenarios') and isinstance(analysis,dict):analysis['multiple_scenarios']=True
            root_questions.extend('局部复核待核对：'+q for q in hint.get('questions',[]) if q)
            if hint.get('multiple_scenarios'):
                root_questions.append('局部复核识别到多个可能场景，请核对原视频。')
            continue
        if hint.get('multiple_scenarios'):
            if isinstance(analysis,dict):analysis['multiple_scenarios']=True
            root_questions.append('局部复核识别到多个可能场景，请保留该不确定性并核对原帧。')
        if hint.get('action') in ('navigateBack','aiAssert'):
            root_questions.extend(hint.get('questions',[]))
            if hint.get('multiple_scenarios'):
                scenario_question='局部复核识别到多个可能场景，请保留该不确定性并核对原帧。'
                root_questions.append(scenario_question)
            matches=[e for e in events if hint['frame_id'] in e.get('frame_refs',[])]
            taps=[event for event in matches if event.get('action')=='aiTap']
            if taps:
                if any(len(event.get('uncertainties',[]))>=10 for event in taps):raise ValueError('video_model_invalid')
                conflict_question='同帧局部复核与主观察动作判断冲突，请核对完整原帧；无法确认时保留疑问，不删除或替换动作。'
                for event in taps:
                    if conflict_question not in event['uncertainties']:event['uncertainties'].append(conflict_question)
                root_questions.append(conflict_question)
                continue
            same_action=[event for event in matches if event.get('action')==hint['action']]
            if same_action:
                if hint.get('requires_review'):
                    if any(len(event.get('uncertainties',[]))>=10 for event in same_action):raise ValueError('video_model_invalid')
                    q=(scenario_question if hint.get('multiple_scenarios') else
                       '局部原帧复核对同一动作仍有疑问，请核对原帧；不确定时保留问题。')
                    for event in same_action:
                        if q not in event['uncertainties']:event['uncertainties'].append(q)
                    root_questions.append(q)
                continue
            if matches:
                conflict_question='同帧局部复核与主观察动作判断冲突，请核对完整原帧；无法确认时保留疑问，不删除或替换动作。'
                root_questions.append(conflict_question)
            else:
                root_questions.append('局部复核提示存在动作归因疑问，请核对原视频；不据此新增、删除或替换动作。')
            continue
        if not hint.get('requires_review'):continue
        root_questions.extend(hint.get('questions',[]))
        matches=[e for e in events if e.get('action')=='aiTap' and hint['frame_id'] in e.get('frame_refs',[])]
        if len(matches)!=1 or len(matches[0].get('uncertainties',[]))>=10:raise ValueError('video_model_invalid')
        if question not in matches[0]['uncertainties']:matches[0]['uncertainties'].append(question)
        if not hint.get('questions'):
            root_questions.append(question)
    return list(dict.fromkeys(root_questions))


def _unquote(value):
    text=value.strip()
    if len(text)>1 and (text[0],text[-1]) in {('“','”'),('「','」'),('"','"'),("'","'")}:
        text=text[1:-1].strip()
    return text


def _label(value):
    text=_unquote(value)
    for suffix in ('按钮','入口'):
        if text.endswith(suffix):
            text=text[:-len(suffix)].strip()
            break
    return text


def _qualified_label(value):
    # Only an explicit region+side prefix is decoration. Never use substring
    # matching, remove arbitrary context, or equate two conflicting locations.
    match=re.fullmatch(r'([\w\u4e00-\u9fff]{1,20}(?:区域|面板)(?:左侧|右侧|上方|下方|顶部|底部|[左右][上下][方角]))的(.{2,60})', value.strip())
    if not match:
        return None
    label=match[2]
    for suffix in ('文字链接','文字按钮','文字入口','链接'):
        if label.endswith(suffix):
            label=label[:-len(suffix)]
            break
    return _label(label)


def _same_explicit_label(left,right):
    a,b=_qualified_label(left),_qualified_label(right)
    if (a is None)==(b is None):
        return False
    return bool(a and a==_label(right) or b and b==_label(left))



def _same_explicit_input_label(left, right):
    # A bare visible value and that exact value plus an input type describe
    # the same explicit touch. Never erase a conflicting control type or
    # location, and never apply this to a merely nearby frame.
    left, right = _unquote(left), _unquote(right)
    for typed, bare in ((left, right), (right, left)):
        if bare.endswith(('输入框', '按钮', '入口', '链接')):
            continue
        match = re.fullmatch(r'(.+?)(?:文本输入框|输入框)', typed)
        if match and bare and _unquote(match[1]) == bare:
            return True
    return False


def _dialog_and_bare_label(left, right):
    """Recognize a shape to *review*, not proof of a shared control."""
    for qualified, bare in ((_unquote(left), _unquote(right)), (_unquote(right), _unquote(left))):
        match=re.fullmatch(r'(.{1,60}(?:弹窗|对话框))的(.{1,60})按钮', qualified)
        if not match or not bare or bare.endswith(('按钮','入口','输入框','链接')):continue
        if match[2]==bare:return True
    return False


def dialog_target_review(events,hints,frames,diagnostic):
    """Return an untrusted same-frame candidate for explicit human review.

    The helper never proves targets equivalent, discards actions, or replaces
    either name. Multi-action and multi-probe ambiguity remains a hard error.
    """
    frame=diagnostic.get('frame_id')
    if diagnostic.get('binding')!='explicit' or diagnostic.get('comparison')!='mismatch':return None
    if frame not in {item['frame_id'] for item in frames}:return None
    matches=[event for event in events if event.get('action')=='aiTap' and frame in event.get('frame_refs',[])]
    local_hints=[hint for hint in hints if hint.get('frame_id')==frame and
                 hint.get('kind')!='unresolved_observation' and hint.get('action') not in ('navigateBack','aiAssert')]
    if len(matches)!=1 or len(local_hints)!=1:return None
    event,hint=matches[0],local_hints[0]
    if hint.get('action') not in (None,'aiTap'):return None
    left,right=event.get('target'),hint.get('target')
    if not isinstance(left,str) or not isinstance(right,str) or not _dialog_and_bare_label(left,right):return None
    # Do not let the first reviewable disagreement hide a second hard conflict.
    if conflict(events,[other for other in hints if other is not hint],frames):return None
    return event,frame,left,right


def _dialog_review_failure():
    error=ValueError('video_model_invalid')
    error.dialog_review_blocked=True
    return error


def guard_ambiguous_dialog_hints(events,hints,frames):
    """Do not retry away multi-action, multi-probe or other conflicts."""
    times={item['frame_id']:item['timestamp_ms'] for item in frames}
    for hint in hints:
        frame=hint.get('frame_id');target=hint.get('target')
        if not isinstance(target,str) or hint.get('kind')=='unresolved_observation' or hint.get('action') not in (None,'aiTap'):continue
        taps=[event for event in events if event.get('action')=='aiTap' and frame in event.get('frame_refs',[])]
        if not taps and frame in times:
            nearby=[event for event in events if event.get('action')=='aiTap' and event.get('frame_refs') and
                    abs(times[event['frame_refs'][0]]-times[frame])<=350]
            if any(isinstance(event.get('target'),str) and _dialog_and_bare_label(event['target'],target) for event in nearby):
                raise _dialog_review_failure()
        if not any(isinstance(event.get('target'),str) and _dialog_and_bare_label(event['target'],target) for event in taps):continue
        if len(taps)!=1 or sum(other.get('frame_id')==frame for other in hints)!=1:
            raise _dialog_review_failure()
        others=[other for other in hints if other is not hint]
        used={id(taps[0])}
        for other in others:
            if other.get('kind')=='unresolved_observation' or other.get('action') in ('navigateBack','aiAssert'):continue
            fid=other.get('frame_id')
            if fid not in times:raise _dialog_review_failure()
            selected=[event for event in events if event.get('action')=='aiTap' and fid in event.get('frame_refs',[])]
            # Another concrete probe requires its own explicit evidence: even
            # the same label nearby could be a separate rapid second tap.
            if len(selected)!=1:raise _dialog_review_failure()
            if id(selected[0]) in used:raise _dialog_review_failure()
            used.add(id(selected[0]))
        if conflict(events,others,frames):
            raise _dialog_review_failure()


def conflict(events, hints, frames, diagnostic=None, evidence=None):
    times = {f['frame_id']:f['timestamp_ms'] for f in frames}
    for hint in hints:
        if hint.get('kind')=='unresolved_observation':
            continue
        if hint.get('action') in ('navigateBack','aiAssert'):
            continue
        explicit = [e for e in events if e.get('action') == 'aiTap' and hint['frame_id'] in e.get('frame_refs',[])]
        nearby = explicit or [e for e in events if e.get('action') == 'aiTap' and e.get('frame_refs')
                  and abs(times[e['frame_refs'][0]]-times[hint['frame_id']]) <= 350]
        if len(nearby)==1:
            left,right=nearby[0]['target'].strip(),hint['target'].strip()
            comparison='exact_match' if left==right else 'normalized_match' if _label(left) and _label(left)==_label(right) else 'mismatch'
            if comparison=='mismatch' and explicit and (_same_explicit_label(left,right) or _same_explicit_input_label(left,right)):
                comparison='normalized_match'
            if diagnostic is not None:
                diagnostic.update(frame_id=hint['frame_id'],binding='explicit' if explicit else 'nearby',comparison=comparison)
            if comparison=='mismatch':
                record=re.fullmatch(r'.*第[一二三四五六七八九十0-9]+条[^的]{0,8}记录的(.+)',_unquote(left))
                if (diagnostic is not None and explicit and record and
                    _label(record[1]) and _label(record[1])==_label(right)):
                    diagnostic['ambiguous_bare_probe']=True
                if evidence is not None:
                    evidence.update(frame_id=hint['frame_id'],observed_target=left,probe_target=right)
                return '触点附近的点击目标与同帧局部复核不一致：'+hint['frame_id']+' 应使用目标 '+hint['target']+'。局部交互证据：'+hint.get('observation','')[:500]+'。按该原帧触点核对，不用转场中经过触点的底部控件替代。'
    return ''
