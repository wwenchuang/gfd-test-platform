"""Bounded iteration ownership policy, separate from frozen compiler admission.

No controller setup, VU-owned writes, retries, polling, or recovery after kill.
Only one response-derived resource and one DELETE cleanup per iteration.
"""
import math
import re


def valid_owned_id(value):
    return ((isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,256}', value) is not None) or
            (isinstance(value, (int, float)) and not isinstance(value, bool)
             and 0 <= value <= 9007199254740991 and math.isfinite(value) and int(value) == value))


def require_lifecycle(definition, *, compiler_version='k6-safe-v3'):
    steps = definition.get('steps') or []
    if any(s.get('scope') == 'setup_once' for s in steps):
        raise ValueError('全局 setup_once 尚无实际执行与清理保证，请改用只读 agent_setup 或迭代步骤')
    writes = [s for s in steps if s.get('side_effect') != 'readonly' or s.get('scope') == 'cleanup_once']
    if writes and compiler_version != 'k6-safe-v3':
        raise ValueError('旧版执行不会清理写入资源，已阻止启动；请创建新版执行')
    if not writes:
        return None
    creates = [s for s in writes if s.get('side_effect') == 'creates_owned_resource']
    cleanups = [s for s in writes if s.get('scope') == 'cleanup_once']
    if len(creates) != 1 or len(cleanups) != 1 or len(writes) != 2:
        raise ValueError('目前仅支持每轮创建一个临时资源并清理一次；更新已有资源、多资源补偿或独立清理尚不支持')
    create, cleanup = creates[0], cleanups[0]
    owner = (definition.get('risk') or {}).get('ownership_variable')
    if (create.get('scope') != 'iteration' or not owner or owner in {'__proto__', 'prototype', 'constructor'} or create.get('cleanup_step_id') != cleanup.get('id')
            or cleanup.get('side_effect') != 'cleanup_owned_resource' or steps.index(cleanup) < steps.index(create)):
        raise ValueError('写操作必须在 iteration 内创建，关联后置 cleanup_once，并声明当轮资源 ID')
    owner_extracts = [e for s in steps for e in s.get('extractions', []) if e.get('target') == owner]
    extraction = next((e for e in create.get('extractions', []) if e.get('target') == owner), None)
    if (len(owner_extracts) != 1 or extraction is None or extraction.get('type') != 'json_path'
            or not extraction.get('path') or not extraction.get('required', True) or 'default' in extraction):
        raise ValueError('资源 ID 必须由本轮创建响应 JSON 必需提取，不能设置默认值或被其他步骤覆盖')
    request = cleanup.get('request') or {}
    if (request.get('service') != (create.get('request') or {}).get('service') or request.get('method') != 'DELETE' or '{{' + owner + '}}' not in request.get('path', '')
            or owner in (request.get('path_params') or {})):
        raise ValueError('清理必须与创建使用同一服务，用 DELETE 路径直接引用本轮 ID，不能通过路径参数覆盖或仅在请求头声明归属')
    return {'owner': owner, 'create_id': create['id'], 'cleanup_id': cleanup['id']}
