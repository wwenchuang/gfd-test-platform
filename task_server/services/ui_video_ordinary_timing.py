"""Optional bounded timing metadata; never accepts prompts, values or errors."""
from contextlib import contextmanager
from contextvars import ContextVar
import json
import logging
import os
import re
import time
import uuid

_LOG=logging.getLogger(__name__)
_STATE=ContextVar('ordinary_video_timing',default=None)
_KINDS=frozenset({'context','assets','detail','touch_model','observe_model','window_total'})


@contextmanager
def session(source_id,version):
    state=None
    try:
        if (os.getenv('TASK_UI_VIDEO_TIMING_LOG')=='1' and type(source_id) is str and
            re.fullmatch(r'uvs_[0-9a-f]{32}',source_id) and type(version) is int and 1<=version<=1000000000):
            state={'source_id':source_id,'version':version,'attempt_id':uuid.uuid4().hex,'window':None}
    except Exception:
        state=None
    token=_STATE.set(state)
    try:yield
    finally:_STATE.reset(token)


def window(phase,index,total):
    state=_STATE.get()
    if state is None:return
    if (type(phase) is not str or phase not in ('observe','review') or type(index) is not int or type(total) is not int or
        not 1<=index<=total<=1000):
        state['window']=None
        return
    state['window']={'phase':phase,'window':index,'windows':total}


def _emit(data):
    try:_LOG.warning('ui_video_timing %s',json.dumps(data,ensure_ascii=True,separators=(',',':')))
    except Exception:pass


@contextmanager
def span(kind,detail=0,attempt=1):
    """Bound one operation without inspecting its inputs or results."""
    state=_STATE.get()
    base=None
    try:
        if (state is not None and state['window'] is not None and type(kind) is str and kind in _KINDS and
            type(detail) is int and 0<=detail<=6 and type(attempt) is int and 1<=attempt<=8):
            base={k:state[k] for k in ('source_id','version','attempt_id')}
            base.update(state['window']);base.update(kind=kind,detail=detail,call_attempt=attempt,call_id=uuid.uuid4().hex)
            started=time.monotonic_ns()
    except Exception:
        base=None
    if base is None:
        yield
        return
    _emit({**base,'event':'start'})
    outcome='returned'
    try:
        yield
    except BaseException as error:
        outcome='error'
        try:
            from .ai_skill_service import TouchTargetProviderTimeoutError,VideoObservationProviderTimeoutError
            if isinstance(error,(TimeoutError,TouchTargetProviderTimeoutError,VideoObservationProviderTimeoutError)):outcome='timeout_observed'
            elif isinstance(error,ValueError) and error.args==('video_cancelled',):outcome='cancelled'
        except Exception:pass
        raise
    finally:
        try:
            elapsed=max(0,min(86400000,(time.monotonic_ns()-started)//1000000))
            _emit({**base,'event':'end','elapsed_ms':elapsed,'outcome':outcome})
        except Exception:pass


def measure(kind,fn,detail=0,attempt=1):
    """Call fn exactly once and emit fixed-schema metadata without its arguments."""
    with span(kind,detail=detail,attempt=attempt):
        return fn()
