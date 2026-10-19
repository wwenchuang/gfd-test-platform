"""Re-read full-flow evidence after long recovery loops, before case planning."""
import re


def needs_reconciliation(events,frames):
    return len(frames)>60 and any('取消' in e['target'] for e in events) and any(
        re.search(r'取消.*(?:未完成|失败|未成功)',e['observation']) for e in events)


def check_coverage(original,reviewed,duration=None):
    from .ui_video_analysis import _system_boundary
    required=[event for event in original if event['action'] in ('aiTap','aiInput','aiScroll')
              and not (duration is not None and _system_boundary(event,duration))]
    owners={}
    def assign(index,seen):
        old=required[index]
        for position,new in enumerate(reviewed):
            if position in seen or not set(old['frame_refs']) & set(new['frame_refs']):continue
            if old['action'] in ('aiInput','aiScroll') and new['action']!=old['action']:continue
            if old['action']=='aiTap' and new['action'] not in ('aiTap','aiAssert','aiWaitFor'):continue
            seen.add(position)
            if position not in owners or assign(owners[position],seen):
                owners[position]=index
                return True
        return False
    if any(not assign(index,set()) for index in range(len(required))):
        raise ValueError('video_required_action_missing')
    for position,index in owners.items():
        if required[index]['action']=='aiTap' and reviewed[position]['action']!='aiTap':
            reviewed[position]['uncertainties'].append('首轮点击已复核为自动状态变化，请核对该段视频')


def reconcile(events,frames,duration,root,cancelled,secrets,progress=None):
    from .ui_video_windows import observe_windows
    # Revisit all decoded pixels. Resampling a long film back to sixty images
    # would lose the before/after states this pass is intended to recover.
    checked,new_secrets=observe_windows(frames,duration,root,cancelled,progress,
                                       review_events=events,prior_secrets=secrets)
    secrets.extend(new_secrets)
    check_coverage(events,checked['events'],duration)
    warnings=[q for e in checked['events'] for q in e['uncertainties'] if q.startswith('首轮点击')]
    checked['uncertainties']=list(dict.fromkeys(checked['uncertainties']+warnings))
    return checked
