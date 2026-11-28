from task_server.services import requirement_scripts as scripts


def example():
    return {'id':'C1','title':'查看活动规则','precondition':'已登录','expected':'活动规则页显示积分获取条件',
            'plan':{'kind':'ui','platforms':['android','ios']},'point_ids':['T1'],
            'navigation':{'android':[{'label':'活动规则','visible':'活动规则页显示积分获取条件'}]}}


def test_compile_unbound_requirement_and_guard_effect():
    case = example()
    built = scripts.prepare(case, 'com.kfb.model', 'android')
    assert built['ready'] and 'aiAssert' in built['yaml_content']
    assert '活动规则页显示积分获取条件' in built['yaml_content']
    assert not scripts.prepare(case, 'com.kfb.model', 'ios')['ready']
    case['navigation']['android'][0]['label']='开始打印'
    assert not scripts.prepare(case, 'com.kfb.model', 'android')['ready']


def test_manual_design_is_not_dispatched():
    case = example()
    case['plan']['kind'] = 'manual'
    assert not scripts.prepare(case,'com.kfb.model','android')['ready']


def test_navigation_cannot_replace_the_business_action():
    case = example()
    case['steps'] = ['点击预设图中的“星际小宇航员”']
    assert '星际小宇航员' in scripts.prepare(case,'com.kfb.model','android')['reason']


def test_long_visual_assertion_survives_runner_guard_normalization():
    case = example()
    case['expected'] = '； '.join(['活动规则页显示积分获取条件和最新规则文案'] * 7)
    assert scripts.prepare(case,'com.kfb.model','android')['ready']


def test_visible_upload_entry_needs_no_photo_fixture():
    case = example()
    case['expected'] = '页面显示选择图片标题和拍照或上传自选照片入口'
    assert scripts.prepare(case,'com.kfb.model','android')['ready']
    case['navigation']['android'][0]['label'] = '从相册选择'
    assert not scripts.prepare(case,'com.kfb.model','android')['ready']


def test_before_and_after_do_not_become_one_terminal_assertion():
    case = example()
    case['expected'] = '初始状态按钮置灰，点击图案后按钮可用'
    assert not scripts.prepare(case,'com.kfb.model','android')['ready']
