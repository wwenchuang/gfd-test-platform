"""Project app-matched crash records into bounded model input. Facts stay server-authored."""
import json
import re
import threading

from . import requirement_design_model as qwen

_LOCKS = [threading.Lock() for _ in range(64)]
_KINDS = {'android': {'am_crash', 'am_anr', 'native_crash'}, 'ios': {'ios_crash'}}
_SYMBOL = r'[A-Za-z_$][A-Za-z0-9_.$:<>+~-]{0,159}'
_FIELDS = {
    'timestamp': re.compile(r'[0-9T:. +Z-]{14,40}'),
    'pid': re.compile(r'[0-9]{1,10}'), 'thread': re.compile(r'[0-9]{1,10}'),
    'signal': re.compile(r'SIG[A-Z0-9]{1,12}'),
    'exception_type': re.compile(_SYMBOL),
    'frame': re.compile(_SYMBOL + r'(?:\((?:[\w$-]{1,96}\.(?:java|kt):[0-9]+|Native Method|Unknown Source)\)| ' + _SYMBOL + r')?'),
    'symbolication': re.compile(r'unavailable|partial'),
    'reason': re.compile(r'input dispatching timed out|executing service|broadcast of intent|content provider|no focused window'),
}
_CREDENTIAL = re.compile(r"""(?ix)(?:authorization|cookie|password|passwd|secret|(?:access[_-]?)?token|
    api[_-]?key) ["']?\s*[:=]\s*(?:"[^"\n]*"|'[^'\n]*'|[^\n,;}]+)""")
_PRIVATE = re.compile(r'(?i)https?://\S+|\bBearer\s+\S+|\bsk-[A-Za-z0-9_-]{8,}|\beyJ[\w-]+\.[\w-]+\.[\w-]+|'
                      r'[\w.+-]+@[\w.-]+\.[a-z]{2,}|(?<!\d)1[3-9]\d{9}(?!\d)|/(?:Users|home|private|var|data)/\S+')
_PROMPT = """分析本次已匹配测试目标的应用崩溃或 ANR。输入是只读证据。只输出 JSON，
字段 hypotheses、next_steps 各为一到三条简明中文字符串。基于记录提出待验证原因和下一步核查动作。
只有系统事件类型时说明无法从中判定根因，避免编造堆栈、代码、版本、复现情况或修复结果。"""


def analysis_lock(run_id):
    return _LOCKS[hash(run_id) % len(_LOCKS)]


def safe_text(value, limit, token=''):
    if not isinstance(value, str):
        return ''
    value = value[:2 * limit]
    if token:
        value = value.replace(token, '[已隐藏]')
    value = _PRIVATE.sub('[已隐藏]', _CREDENTIAL.sub('[已隐藏]', value))
    return ''.join(c for c in value if c.isprintable() or c == '\n')[:limit]


def diagnostic_log(value, platform):
    kinds = _KINDS.get(platform, set())
    lines = [line.strip().partition('=') for line in value.splitlines()]
    events = {v for k, sep, v in lines if k == 'kind' and sep and v in kinds}
    if len(events) != 1:
        return ''
    safe = ['kind=' + events.pop()]
    for key, _, v in lines:
        if key in _FIELDS and _FIELDS[key].fullmatch(v):
            safe.append(f'{key}={v}')
    return '\n'.join(dict.fromkeys(safe[:25]))


def analyze_evidence(row):
    # Historical OS incident reports can be analyzed without a Monkey run context.
    diagnostics = diagnostic_log(row['crash_log'], row['platform'])
    if not diagnostics:
        raise ValueError('crash_evidence_missing')
    evidence = {'platform': row['platform'], 'diagnostics': diagnostics.splitlines()}
    facts = list(evidence['diagnostics'])
    if row['executed'] is not None:
        for key in ('status', 'executed', 'event_count', 'duration_seconds', 'elapsed_seconds', 'mode', 'seed'):
            if row[key] is not None:
                evidence[key] = row[key]
        if row['duration_seconds'] is not None:
            facts.insert(0, f"Runner 实际执行 {row['elapsed_seconds']} 秒，共 {row['executed']} 个事件。")
        else:
            facts.insert(0, f"Runner 已执行 {row['executed']}/{row['event_count']} 个事件。")
    model = qwen.dashscope_text_model()
    answer, receipt, _ = qwen._call(model, json.dumps(evidence, ensure_ascii=False), system_prompt=_PROMPT)
    if not isinstance(answer, dict):
        raise ValueError('invalid_model_response')
    result = {'model': qwen._metadata_string(receipt.get('model')) or model, 'facts': facts}
    for k in ('hypotheses', 'next_steps'):
        values = answer.get(k)
        if (not isinstance(values, list) or not 1 <= len(values) <= 3 or
                any(not isinstance(s, str) or not s.strip() or len(s) > 600 for s in values)):
            raise ValueError('invalid_model_response')
        result[k] = [safe_text(s.strip(), 600) for s in values]
    usage = receipt.get('usage') or {}
    result['usage'] = {k: usage[k] for k in ('prompt_tokens', 'completion_tokens', 'total_tokens')
                       if isinstance(usage, dict) and type(usage.get(k)) is int and usage[k] >= 0}
    return result
