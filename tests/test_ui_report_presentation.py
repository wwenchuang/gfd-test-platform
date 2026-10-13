"""Release-approval reports use frozen business coverage, not action transcripts."""
from copy import deepcopy
from task_server.services import ui_report_presentation as presentation


def snapshot(status='passed'):
    return {'app_id':'com.kfb.model','app_name':'智小白3D','owner_display_name':'王文创',
        'created_ns':1791889000000000000,'started_ns':1791889020000000000,'finished_ns':1791889080000000000,
        'ui_batch_id':'uib_private_id','status':status,'selection':{},'artifacts':[],
        'attempts':[{'case_id':'case-internal','case_revision':'a'*64,'attempt_no':1,'job_id':'job-secret',
          'platform':'ios','device_id':'00008110-raw-udid','status':status,'started_ns':1791889020000000000,
          'finished_ns':1791889080000000000,'block_message':'','result':{},
          'metadata':{'task_name':'个人中心与首页导航','goal':'个人中心与首页之间切换','expected':'入口可打开，返回后首页正常显示'},
          'device_snapshot':{'label':'iPhone 13','ios_version':'26.6','app_versions':[{'version_name':'1.19.7','version_code':'70'}]},
          'steps':[{'label':'launch · com.kfb.3d','status':'passed'},{'label':'aiTap · 我的','status':'passed'}]}]}


def test_release_report_has_business_title_person_time_coverage_and_conclusion_without_steps():
    view=presentation.build_presentation(snapshot())
    text=presentation.render_markdown(view)
    for expected in ['智小白3D 发布测试报告','执行人：王文创','测试时间：','个人中心与首页之间切换','个人中心与首页导航','本次测试通过','1.19.7','70']:
        assert expected in text
    for internal in ['com.kfb','launch','aiTap','job-secret','case-internal','a'*64,'00008110','步骤','Bug','ID：']:
        assert internal not in text
    assert view['conclusion_status']=='passed'
    assert '1 条用例' in view['summary']


def test_not_started_does_not_claim_coverage_and_failure_is_not_passed():
    data=snapshot('cancelled');data['attempts'][0]['started_ns']=None
    view=presentation.build_presentation(data)
    assert view['conclusion_status']=='incomplete'
    assert view['coverage'][0]['result']=='未执行'
    assert view['executed_cases']==0
    assert '未完成' in presentation.render_markdown(view)
    failed=presentation.build_presentation(snapshot('failed'))
    assert failed['conclusion_status']=='failed'
    assert '未通过' in presentation.render_markdown(failed)


def test_repeated_failure_then_success_keeps_both_results_without_double_counting_cases():
    data=snapshot('passed');first=data['attempts'][0];first['status']='failed'
    second=deepcopy(first);second['status']='passed';second['attempt_no']=2;data['attempts'].append(second)
    view=presentation.build_presentation(data)
    assert view['case_count']==1 and view['attempt_count']==2
    assert view['conclusion_status']=='failed'
    assert [row['result'] for row in view['coverage']]==['未通过','通过']
    assert '第 2 次' in presentation.render_markdown(view)


def test_missing_metadata_is_explicit_not_fabricated_and_script_identifiers_are_not_title():
    data=snapshot();data['app_name']='com.kfb.model';data['attempts'][0]['metadata']={'task_name':'导航'}
    view=presentation.build_presentation(data)
    assert view['title']=='应用发布测试报告'
    assert view['coverage'][0]['scenario']=='导航（未填写独立场景说明）'
    assert view['coverage'][0]['expected']=='未填写'
    assert 'com.kfb.model' not in presentation.render_markdown(view)


def test_report_uses_literal_frozen_text_and_escapes_markdown_injection():
    data=snapshot();data['attempts'][0]['metadata']['goal']='<script>alert(1)</script> | [跳转](https://evil.test)\n# 假标题'
    text=presentation.render_markdown(presentation.build_presentation(data))
    assert '<script>' not in text and '[跳转]' not in text and '\n# 假标题' not in text


def test_running_or_unknown_outcome_is_not_release_pass():
    for status in ['running','interrupted','blocked','mystery']:
        assert presentation.build_presentation(snapshot(status))['conclusion_status']=='incomplete'


def test_test_versions_are_frozen_and_separate_for_each_platform():
    data=snapshot()
    android=deepcopy(data['attempts'][0]);android['platform']='android'
    android['device_snapshot']={'label':'OPPO Reno9','android_version':'15',
        'app_versions':[{'version_name':'2.0.0','version_code':'51'}]}
    data['attempts'].append(android)
    view=presentation.build_presentation(data)
    assert view['test_versions']==['苹果端 1.19.7（构建号 70）','安卓端 2.0.0（构建号 51）']
    text=presentation.render_markdown(view)
    assert '测试版本：苹果端 1.19.7（构建号 70）；安卓端 2.0.0（构建号 51）' in text
    assert text.index('测试版本：') < text.index('覆盖场景及结果')
    assert '1.19.7' in view['summary'] and '2.0.0' in view['summary']
    data['attempts'][0]['device_snapshot']['app_versions'][0]['version_name']='9.9.9'
    assert '9.9.9' not in presentation.render_markdown(view)


def test_unavailable_version_is_explicit_and_build_zero_is_preserved():
    data=snapshot();device=data['attempts'][0]['device_snapshot']
    device['app_versions']=[]
    assert presentation.build_presentation(data)['test_versions']==['苹果端 未获取']
    device['app_versions']=[{'version_code':0}]
    assert presentation.build_presentation(data)['test_versions']==['苹果端 未获取（构建号 0）']


def test_tested_app_versions_are_distinct_from_phone_os_versions():
    data=snapshot();android=deepcopy(data['attempts'][0]);android['platform']='android'
    android['device_snapshot']={'label':'OPPO Reno9','android_version':'15',
        'app_versions':[{'version_name':'2.0.0','version_code':'51'}]}
    data['attempts'].append(android)
    view=presentation.build_presentation(data);text=presentation.render_markdown(view)
    assert '测试版本：苹果端 1.19.7（构建号 70）；安卓端 2.0.0（构建号 51）' in text
    assert '苹果系统 26.6' in text and '安卓系统 15' in text
    assert [row['target_label'] for row in view['coverage']]==['苹果系统','安卓系统']
    assert '苹果端 1.19.7' in view['summary'] and '安卓端 2.0.0' in view['summary']
    assert '苹果系统 1.19.7' not in text and '安卓系统 2.0.0' not in text


def test_same_case_cross_platform_results_have_clear_target_in_all_export_rows():
    data=snapshot('failed')
    android=deepcopy(data['attempts'][0]);android['platform']='android';android['status']='passed'
    android['device_snapshot']={'label':'OPPO Reno9','android_version':'15',
        'app_versions':[{'version_name':'2.0.0','version_code':'51'}]}
    data['attempts'].append(android)
    view=presentation.build_presentation(data)
    rows=[line for line in presentation.render_markdown(view).splitlines() if line.startswith('| 个人中心')]
    assert len(rows)==2
    assert '苹果系统' in rows[0] and '未通过' in rows[0]
    assert '安卓系统' in rows[1] and '通过' in rows[1]
    assert all(line.count('|')==4 for line in rows)
    assert '苹果系统' in view['concerns'][0]


def test_same_platform_multiple_devices_or_versions_identify_the_frozen_target():
    data=snapshot();second=deepcopy(data['attempts'][0]);second['status']='failed'
    second['device_id']='another-private-udid'
    second['device_snapshot']={'label':'iPhone 15','ios_version':'26.6',
        'app_versions':[{'version_name':'1.20.0','version_code':'71'}]}
    data['attempts'].append(second)
    view=presentation.build_presentation(data)
    rows=[line for line in presentation.render_markdown(view).splitlines() if line.startswith('| 个人中心')]
    assert '苹果系统，设备 1' in rows[0] and '苹果系统，设备 2' in rows[1]
    assert all('应用版本' not in row and '构建号' not in row and '苹果 13' not in row and '苹果 15' not in row for row in rows)
    assert view['environments']==[
        '设备 1：苹果 13，苹果系统 26.6；应用版本 1.19.7（构建号 70）',
        '设备 2：苹果 15，苹果系统 26.6；应用版本 1.20.0（构建号 71）']
    assert '苹果系统，设备 2' in view['concerns'][0] and '1.20.0' not in view['concerns'][0]
    assert 'another-private-udid' not in '\n'.join(rows)
    second['device_snapshot']['app_versions'][0]['version_name']='9.9.9'
    assert '9.9.9' not in presentation.render_markdown(view)


def test_identical_phone_labels_use_frozen_target_order_and_keep_concerns_unambiguous():
    data=snapshot();first=data['attempts'][0];first['runner_id']='private-runner-a'
    second=deepcopy(first);second['runner_id']='private-runner-b';second['device_id']='private-phone-b';second['status']='failed'
    data['selection']['targets']=[{key:a[key] for key in ('platform','runner_id','device_id')} for a in (first,second)]
    # Attempt display order can differ from the frozen target selection order.
    retry=deepcopy(first);retry['attempt_no']=2
    data['attempts']=[second,first,retry]
    view=presentation.build_presentation(data)
    assert view['coverage'][0]['target_label']=='苹果系统，设备 2'
    assert view['coverage'][1]['target_label']=='苹果系统，设备 1'
    assert view['coverage'][1]['target_label']==view['coverage'][2]['target_label']
    assert '设备 2' in view['concerns'][0] and '未通过' in view['concerns'][0]
    assert set(view['environments'])=={
        '设备 1：苹果 13，苹果系统 26.6；应用版本 1.19.7（构建号 70）',
        '设备 2：苹果 13，苹果系统 26.6；应用版本 1.19.7（构建号 70）'}
    text=presentation.render_markdown(view)
    assert '设备 1' in text and '设备 2' in text
    for secret in ['private-runner-a','private-runner-b','private-phone-b','00008110-raw-udid']:
        assert secret not in text
    data['attempts']=[first,second,retry]
    assert presentation.build_presentation(data)['coverage'][1]['target_label']==view['coverage'][0]['target_label']


def test_repeated_attempt_on_one_frozen_phone_does_not_invent_another_device():
    data=snapshot();retry=deepcopy(data['attempts'][0]);retry['attempt_no']=2
    data['attempts'].append(retry)
    view=presentation.build_presentation(data)
    assert all(row['target_label']=='苹果系统' for row in view['coverage'])
    assert '设备 1' not in presentation.render_markdown(view)
