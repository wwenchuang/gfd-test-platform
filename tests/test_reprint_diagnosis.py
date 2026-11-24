from task_server.api_testing.reprint_diagnosis import reprint_hint

CASE = '重新打印判断 - 创建后取消并清理'


def step(stage, method, path, data=None, code=0, http=200, status='FAILED', assertion=None):
    return {'stage': stage, 'status': status,
            'request': {'method': method, 'url': 'https://test.invalid/app/print3d/api/v1/' + path},
            'response': {'status_code': http, 'body': {'code': code, 'data': data}},
            'assertions': [assertion] if assertion else []}


def test_reprint_blockers_and_positive_receipt():
    err = {'passed': False, 'type': 'json_path', 'expected': 'ready', 'actual': 'error',
           'message': '$.data.deviceStatus 期望 ready'}
    dev = step('setup', 'GET', 'devices/info', {'deviceStatus': 'error'}, assertion=err)
    own = {'stepType': 'slice', 'status': 'slicing'}
    queue = step('setup', 'GET', 'printJob/currentSliceJob', own, assertion={
        'passed': False, 'type': 'json_path', 'expected': None, 'actual': own, 'message': '$.data 期望 null'})
    main = step('main', 'POST', 'printJob/createReprint', code=1001)
    assert '设备异常' in reprint_hint(CASE, [dev])
    assert '已有切片' in reprint_hint(CASE, [queue])
    assert '业务码 1001' in reprint_hint(CASE, [main])
    assert 'HTTP 503' in reprint_hint(CASE, [step('main', 'POST', 'printJob/createReprint', http=503)])
    assert not reprint_hint('其他只读用例', [dev])
    assert not reprint_hint(CASE, [dict(dev, assertions=[])])
    assert not reprint_hint(CASE, [dict(queue, response={'status_code': 401})])
    assert not reprint_hint(CASE, [dict(main, stage='cleanup')])
    assert not reprint_hint(CASE, [dict(main, status='PASSED'), dict(dev, stage='cleanup')])
    assert not reprint_hint(CASE, [step('main', 'POST', 'printJob/createReprint', status='PASSED')])
    assert reprint_hint(CASE, [dev, main]) == reprint_hint(CASE, [main])
    assert '缺少有效回执' in reprint_hint(CASE, [dict(main, response=None)])
    assert '核对主体断言' in reprint_hint(CASE, [step('main', 'POST', 'printJob/createReprint')])
