"""Read previously recorded practice evidence only after per-execution scope checks."""
import copy
import json
import sys

from task_server import identity
from task_server.services import week2_real

# Historical snapshots, kept out of the publicly served static bundle.  A source
# must still exist and pass current project/environment authorization before any
# entry (including its identifier) is returned.
_RECORDS = (
    {'key':'cleanup', 'execution_id':'0157b386-1475-4946-aae8-1664498ecc69', 'cases':1,
     'summary':'2026-11-06 生产环境单次创建会话后按本次返回的编号标记删除，随后接口查询不可见。关联资源、物理删除与异常恢复未证实。'},
    {'key':'baseline', 'execution_id':'1eb0c640-d78a-41f9-b441-02785fd87e1c', 'cases':1,
     'summary':'2026-11-06 既有接口基线只读执行通过，HTTP 200、业务码 0、三条断言通过。'},
    {'key':'failure', 'execution_id':'43b09e5e-d879-47a2-bde5-2d462d48e988', 'cases':1,
     'summary':'2026-11-06 该执行因缺少临时变量在发请求前中断，未收到业务响应。'},
    {'key':'trace', 'execution_id':'6e5ae109-ba21-4434-9835-5d1e58d228ff', 'cases':1,
     'summary':'2026-11-05 只读反馈请求携带请求号，HTTP 200、业务码 0、三条断言通过；业务响应未回显该请求头，业务日志关联未验证。'},
    {'key':'feedback', 'execution_id':'de803f8b-e18b-4e67-986d-b14ce737b8aa', 'cases':1,
     'summary':'2026-11-05 反馈分类只读请求 1/1 通过，HTTP 200、业务码 0、三项断言通过。服务器文件模式重跑 17 项数据处理检查通过，但文件来源未独立认证，本次处理未重新发起业务请求。',
     'checks':['HTTP 状态检查','业务码为整数','业务码为观察到的 0','data 为列表','每项为对象','包含所需字段',
               'sn 为非空字符串','name 为非空字符串','children 为列表','order_related 为布尔值','提取数量与原数据一致',
               '提取不修改原输入','拒绝无效 sn','拒绝空 name','拒绝无效 children','拒绝无效 order_related','拒绝缺少必需字段']},
    {'key':'timeout', 'execution_id':'0dfab6ed-b597-4c56-81bf-cc8d7dcb6342', 'cases':325,
     'summary':'历史回归中一次文字建模聊天请求超时，未收到响应；该记录不是反馈分类接口超时，也不是新的业务调用。请在原执行记录中核对具体子用例。'},
)


def _verify_execution(username, execution_id, expected_cases):
    from sqlalchemy import select
    from task_server.api_testing import access, http
    from task_server.api_testing.models.execution import ApiExecutionCase

    access.authorize_http(username, 'GET', ('executions', execution_id))
    factory = http._factory()
    execution = http._scope_execution(factory, execution_id, username)
    with factory() as session:
        # Query all children rather than just those visible through a predicate:
        # otherwise a partial scope could make an incomplete result look complete.
        children = session.execute(select(ApiExecutionCase.id, ApiExecutionCase.environment_revision_id)
                                   .where(ApiExecutionCase.execution_id == execution.id)).all()
        if len(children) != expected_cases or not children:
            return False
        allowed = set(session.scalars(select(ApiExecutionCase.id).where(
            ApiExecutionCase.execution_id == execution.id,
            access.resource_predicate(username, ApiExecutionCase))))
        return (len(allowed) == len(children)
                and all(child.id in allowed and child.environment_revision_id == execution.environment_revision_id
                        for child in children))


def _verify_sources(owner, username, records):
    results = []
    for record in records:
        try:
            if not week2_real.authorized_actor(owner, username):
                raise ValueError('unauthorized')
            results.append(_verify_execution(username, record['execution_id'], record['cases']))
        except Exception:
            results.append(False)
    return results


def _invoke_authorization(owner, username, records, timeout, slot_fd):
    body = json.dumps({'owner':owner, 'username':username, 'records':records})
    raw = week2_real._run_isolated([sys.executable, '-m', 'task_server.services.practice_legacy_evidence'],
                                   body, timeout, slot_fd=slot_fd)
    values = json.loads(raw)
    if not isinstance(values,list) or len(values)!=len(records) or any(type(x) is not bool for x in values):
        raise ValueError('invalid authorization')
    return values


def get_records(owner, username):
    try:
        if not week2_real.authorized_actor(owner, username):
            return []
        profile = copy.deepcopy(identity.get_access_profile(username))
        sources = [{'execution_id':record['execution_id'], 'cases':record.get('cases', 1)} for record in _RECORDS]
        with week2_real._history_slot(None) as slot:
            allowed = _invoke_authorization(owner, username, sources, week2_real._HISTORY_TIMEOUT_SECONDS, slot)
        if (not week2_real.authorized_actor(owner, username)
                or profile != identity.get_access_profile(username)
                or len(allowed) != len(_RECORDS) or any(type(value) is not bool for value in allowed)):
            return []
        return [{**{'key':record['key'], 'execution_id':record['execution_id'], 'summary':record['summary']},
                 **({'checks':record['checks']} if 'checks' in record else {})}
                for record, permitted in zip(_RECORDS, allowed) if permitted]
    except Exception:
        return []


if __name__ == '__main__':
    try:
        request = json.load(sys.stdin)
        print(json.dumps(_verify_sources(request['owner'], request['username'], request['records'])))
    except Exception:
        print('[]')
