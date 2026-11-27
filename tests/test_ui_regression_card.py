import json

from task_server.services.ui_regression_card import capture_phone, regression_card


def test_three_notices_communicate_coverage_and_hide_identifiers(monkeypatch):
    from task_server.services import runner_service
    rows = [{'case_id':'1','name':'打印记录查看'}]
    target = {'device_id':'ecbfd645','runner_id':'android-runner','platform':'android'}
    monkeypatch.setattr(runner_service,'list_runners',lambda: {'android-runner':{'devices':[
        {**target,'label':'OPPO Reno9','android_version':'15'}]}})
    record = {'status':'passed','outcomes':{'smoke':{'1':'passed'}},'target':target,'batches':{'smoke':'uib_'+'a'*32}}
    capture_phone(record,target)
    android = regression_card(record,rows,'android','全部通过','https://qa.example')['card']
    assert android['header']['template']=='green' and 'OPPO Reno9 · Android 15' in str(android)
    assert all(raw not in str(android) for raw in ('ecbfd645','android-runner'))
    assert android['elements'][-1]['actions'][0]['url'].endswith('ui_batch=uib_'+'a'*32)
    record.update(status='已执行用例全部通过',report_phone={'platform':'ios','name':'iPhone 13','os':'27.0.1'})
    rows.extend({'case_id':str(i),'name':f'暂缓项{i}','deferred':True} for i in range(2,5))
    ios=regression_card(record,rows,'ios',record['status'],'')['card']
    assert ios['header']['template']=='blue' and '1 条通过 · 3 条暂缓' in str(ios['header'])
    record.update(status='冒烟未达继续条件',outcomes={'冒烟':{'1':'failed'}})
    rows.append({'case_id':'5','name':'后续用例'})
    failed=regression_card(record,rows,'ios','首批验证未通过继续条件，回归已停止','')['card']
    assert failed['header']['template']=='red'
    assert all(x in json.dumps(failed,ensure_ascii=False) for x in ('iPhone 13','失败用例　打印记录查看','暂缓 3','未完成 1'))


def test_unreported_name_never_falls_back_to_serial_or_another_device(monkeypatch):
    from task_server.services import runner_service
    target={'device_id':'ecbfd645','runner_id':'runner','platform':'android'}
    monkeypatch.setattr(runner_service,'list_runners',lambda:{'runner':{'devices':[{'device_id':'other','label':'Other'}]}})
    record={'target':target}
    capture_phone(record,target)
    card=regression_card(record,[],'android','执行时间窗口已结束','')
    assert '机型未记录' in str(card) and not any(x in str(card) for x in ('ecbfd645','Other'))
