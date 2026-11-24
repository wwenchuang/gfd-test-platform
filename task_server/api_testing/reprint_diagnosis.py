"""Classify a reprint using the recorded workflow step, never a new device request."""
import json
from urllib.parse import urlsplit

REPRINT_CASES = frozenset(('核心链路-真实打印创建查询取消（4F2A）', '重新打印判断 - 创建后取消并清理'))


def _operation(step):
    req = step.get('request')
    if not isinstance(req, dict):
        return None, None
    try:
        path = urlsplit(req.get('url') or req.get('path') or '').path
    except (TypeError, ValueError):
        return None, None
    return req.get('method'), path.removeprefix('/app')


def reprint_hint(name, trace):
    if name not in REPRINT_CASES or not isinstance(trace, list):
        return ''
    steps = [s for s in trace if isinstance(s, dict)]
    main_seen = any(s.get('stage') == 'main' and s.get('request') for s in steps)
    for s in steps:
        if s.get('status') not in ('FAILED', 'BROKEN'):
            continue
        method, path = _operation(s)
        response = s.get('response')
        if not isinstance(response, dict):
            response = {}
        status = response.get('status_code')
        body = response.get('body')
        if isinstance(body, str) and len(body) <= 65536:
            try:
                body = json.loads(body)
            except (ValueError, RecursionError):
                body = None
        body = body if isinstance(body, dict) else {}
        code, data = body.get('code'), body.get('data')
        if s.get('stage') == 'main' and (method, path) == ('POST', '/print3d/api/v1/printJob/createReprint'):
            if type(status) is not int or not 100 <= status <= 599:
                return '重打请求缺少有效回执。请按请求标识核对任务是否创建。'
            if not 200 <= status < 300:
                return f'重打接口失败。POST createReprint 返回 HTTP {status}。'
            if type(code) is int and -2147483648 <= code <= 2147483647 and code != 0:
                return f'重打接口失败。POST createReprint 返回 HTTP {status}、业务码 {code}。'
            return '重打步骤未通过。已收到接口响应，请核对主体断言和任务编号。'
        if (main_seen or s.get('stage') != 'setup' or method != 'GET' or status != 200
                or type(code) is not int or code != 0 or not isinstance(data, dict)):
            continue
        for a in s.get('assertions') or ():
            if not isinstance(a, dict) or a.get('passed') is not False or a.get('type') != 'json_path':
                continue
            # Older receipts store the evaluated JSON path at the start of the assertion message.
            field = a.get('path') or str(a.get('message') or '').partition(' ')[0]
            if (path == '/print3d/api/v1/devices/info' and field in ('$.data.deviceStatus', '$.data.printStatus')
                    and a.get('actual') == 'error' and data.get(field.split('.')[-1]) == 'error'):
                return f'设备异常。GET devices/info 返回 {field.split(".")[-1]}=error，前置检查停止，未发出重打请求。'
            if (path == '/print3d/api/v1/printJob/currentSliceJob' and field == '$.data'
                    and 'expected' in a and a['expected'] is None
                    and data.get('stepType') == 'slice' and data.get('status') == 'slicing'):
                return '已有切片阻塞。GET currentSliceJob 返回切片，预期队列为空，未发出重打请求。'
    return ''
