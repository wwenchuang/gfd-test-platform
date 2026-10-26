"""Read pointer targets in a small context before observing the full window."""
import re
from . import ai_skill_service as ai


def probe(frames, details, assets, duration, cancelled):
    from .ui_video_analysis import _bind_whole_evidence, validate_analysis
    by_name = {asset['name']: asset for asset in assets}
    hints = []
    schema_correction_used = False
    for detail in details[:6]:
        if cancelled():
            raise ValueError('video_cancelled')
        parent = next(f for f in frames if f['frame_id'] == detail['source_frame_id'])
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
        # One extra schema correction for the entire probe, not per detail.
        # Never turn malformed output into an empty successful observation.
        for attempt in range(2):
            if cancelled():raise ValueError('video_cancelled')
            try:
                result = ai.run_ai_skill('ui_video_observe', payload, version='touch-target',
                    image_assets=[by_name[f['frame_id']] for f in selected]+[by_name[detail['frame_id']]],
                    timeout=120, retry_count=0, max_tokens=5000, repair_invalid_json=False)
                break
            except ai.SkillSchemaError as error:
                if cancelled():raise ValueError('video_cancelled') from None
                if schema_correction_used or attempt:raise
                schema_correction_used = True
                payload['validation_feedback'] = error.model_feedback + ' 重新核对同一组原帧，按规定JSON结构输出；不补造操作，不省略events字段。'
        if cancelled():
            raise ValueError('video_cancelled')
        checked = validate_analysis(_bind_whole_evidence(result, selected), selected, duration)
        if checked['uncertainties'] or checked['multiple_scenarios'] or len(checked['events']) != 1:
            continue
        event = checked['events'][0]
        if (event['action'] == 'aiTap' and not event['uncertainties'] and
                parent['frame_id'] in event['frame_refs'] and event['target']):
            hints.append({'frame_id':parent['frame_id'], 'target':event['target'],'observation':event['observation']})
    return hints


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
    match=re.fullmatch(r'([\w\u4e00-\u9fff]{1,20}区域(?:左侧|右侧|上方|下方|[左右][上下][方角]))的(.{2,60})', value.strip())
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

def conflict(events, hints, frames, diagnostic=None, evidence=None):
    times = {f['frame_id']:f['timestamp_ms'] for f in frames}
    for hint in hints:
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
                if evidence is not None:
                    evidence.update(frame_id=hint['frame_id'],observed_target=left,probe_target=right)
                return '触点附近的点击目标与同帧局部复核不一致：'+hint['frame_id']+' 应使用目标 '+hint['target']+'。局部交互证据：'+hint.get('observation','')[:500]+'。按该原帧触点核对，不用转场中经过触点的底部控件替代。'
    return ''
