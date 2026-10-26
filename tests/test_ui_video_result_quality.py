import pytest
from task_server.services.ui_video_result_quality import needs_result_review


def event(observation):
    return {'action':'aiTap','target':'立即打印按钮','outcome':{'observation':observation}}


def test_missing_observed_duplex_setting_requests_visual_review():
    assert needs_result_review(event('打印设置面板显示份数1、彩色、范围1至16页、单面、A4、逆序'),
                               '打印设置面板显示份数1、彩色、范围1-16页、A4、逆序')


def test_equivalent_setting_labels_preserve_categories():
    assert not needs_result_review(event('打印设置面板显示打印份数1、彩色、范围1至16页、单面、A4、逆序'),
                                   '打印设置面板：1份、彩色、1-16页、单双面=单面、A4、逆序')


def test_only_observed_settings_are_required():
    assert not needs_result_review(event('打印设置面板显示A4及确认打印按钮'),'打印设置面板显示A4及确认打印按钮')


def test_non_print_settings_are_not_interpreted_as_print_configuration():
    assert not needs_result_review(event('记录详情展示A4、单面与历史打印份数'),'记录详情可见')


@pytest.mark.parametrize('expected',[
    '进入任务传输中页面，可见取消打印与继续打印按钮，以及任务完成状态提示',
    '任务传输中，同时打印完成',
])
def test_transmission_must_not_be_combined_with_completion(expected):
    assert needs_result_review(event('任务传输中，右下角任务完成礼盒'),expected)


def test_real_completion_without_transmission_is_not_rejected():
    assert not needs_result_review(event('打印完成页面显示完成状态'),'打印完成页面显示完成状态')


def test_retaining_transmission_only_is_valid():
    assert not needs_result_review(event('任务传输中，右下角任务完成礼盒'),'进入任务传输中页面，可见取消打印按钮')


def test_review_requirements_contain_only_category_names():
    from task_server.services.ui_video_result_quality import review_requirements
    result=review_requirements(event('打印设置面板：私有设备123，份数1，彩色，1至16页，单面，A4，逆序'))
    assert result['configuration_categories_to_check']==['份数','色彩','页码范围','单双面','纸张大小','打印顺序']
    assert '123' not in str(result)


def test_repair_cannot_drop_transmission_and_keep_only_completion():
    assert needs_result_review(event('任务传输中，右下角任务完成礼盒'),'打印完成页面显示完成状态')


@pytest.mark.parametrize('observed,expected',[
    ('单面','单双面选项'),('份数1','打印份数选项'),
    ('范围1至16页','打印范围选项'),('A4','纸张大小选项'),('逆序','打印顺序选项'),
])
def test_category_without_observed_value_is_not_sufficient(observed,expected):
    assert needs_result_review(event('打印设置面板显示'+observed),'打印设置面板显示'+expected)


@pytest.mark.parametrize('text',[
    '筛选面板展开，地区选中上海，排序为最新',
    '导出配置页面显示格式PDF，质量高清',
    '预约表单可见，服务类型选择到店',
    '通知设置面板显示提醒方式为邮件',
    '打印设置面板显示A4、单面',
])
def test_generic_configuration_review_does_not_depend_on_app_or_values(text):
    from task_server.services.ui_video_result_quality import needs_configuration_review
    assert needs_configuration_review(event(text))


@pytest.mark.parametrize('text',[
    '首页可见设置入口', '记录详情展示历史打印参数',
    '模型详情页显示去打印按钮', '点击设置按钮后首页仍可见',
])
def test_navigation_or_history_does_not_force_configuration_review(text):
    from task_server.services.ui_video_result_quality import needs_configuration_review
    assert not needs_configuration_review(event(text))


def test_dynamic_object_keeps_dedicated_result_path():
    from task_server.services.ui_video_result_quality import needs_configuration_review
    e=event('筛选面板可见');e['target']='{{本次对象}}'
    assert not needs_configuration_review(e)


@pytest.mark.parametrize('phrase',['份数显示为1','份数显示1','份数为1','份数是1','份数：1'])
def test_numeric_configuration_display_wording_is_not_value_loss(phrase):
    assert not needs_result_review(event('打印设置面板显示份数1'), '打印设置面板'+phrase)


def test_unrelated_rotating_advert_is_reviewed_not_fixed_as_browse_result():
    e={'action':'aiScroll','target':'资料列表','outcome':{'observation':'资料列表可见，顶部轮播广告自动切换'}}
    assert needs_result_review(e,'资料列表可见，轮播图切换为某促销广告')


def test_explicit_advert_interaction_keeps_its_own_result():
    e={'action':'aiTap','target':'轮播广告入口','outcome':{'observation':'广告详情页可见'}}
    assert not needs_result_review(e,'广告详情页可见')


@pytest.mark.parametrize('target,expected',[
    ('关闭按钮','广告弹窗消失，首页可见'),
    ('跳过','广告结束，首页可见'),
    ('轮播区域','轮播图切换为促销广告'),
])
def test_advert_business_results_are_not_incidental_banner_noise(target,expected):
    e={'action':'aiTap','target':target,'outcome':{'observation':expected}}
    assert not needs_result_review(e,expected)


@pytest.mark.parametrize('expected', [
    '首页顶部年级显示为七年级，搜索框推荐内容与七年级相关。',
    '筛选已应用，推荐位内容与地区相匹配。',
    '页面展示搜索热词和个性化推荐内容。',
])
def test_incidental_recommendation_requires_review(expected):
    candidate={'action':'aiTap','target':'确定按钮','outcome':{'observation':'已返回筛选结果页面'}}
    assert needs_result_review(candidate,expected)


@pytest.mark.parametrize('target', ['推荐商品入口','搜索框','搜索热词'])
def test_explicit_dynamic_content_operation_is_not_rejected(target):
    candidate={'action':'aiTap','target':target,'outcome':{'observation':'推荐内容可见'}}
    assert not needs_result_review(candidate,'搜索热词和个性化推荐内容可见')


@pytest.mark.parametrize('target,expected', [
    ('关闭按钮','搜索热词弹窗已关闭，搜索页面可见'),
    ('开关','个性化推荐内容已隐藏'),
    ('清空按钮','搜索热词已清空'),
])
def test_explicit_dynamic_content_state_change_is_valid(target,expected):
    candidate={'action':'aiTap','target':target,'outcome':{'observation':expected}}
    assert not needs_result_review(candidate,expected)


@pytest.mark.parametrize('quantity',['份数选中2','份数已选中 3','份数当前值为4','份数设置为5'])
def test_explicit_selected_quantity_is_not_a_missing_setting(quantity):
    event={'action':'aiTap','target':'打印入口','outcome':{'observation':'打印设置面板显示份数'+quantity[-1]}}
    assert not needs_result_review(event,'打印设置面板可见，'+quantity)


@pytest.mark.parametrize('quantity',['份数可设置','份数未选中','份数选中','份数设置为'])
def test_quantity_still_requires_a_number(quantity):
    event={'action':'aiTap','target':'打印入口','outcome':{'observation':'打印设置面板显示份数2'}}
    assert needs_result_review(event,'打印设置面板可见，'+quantity)


@pytest.mark.parametrize('expected',[
    '若存在任务则弹出处理提示，否则直接进入预览流程',
    '如果列表为空则显示空状态，否则显示记录',
])
def test_conditional_expected_requests_original_result_review(expected):
    assert needs_result_review(event('当前有一个正在处理的任务提示框可见'),expected)


def test_concrete_result_does_not_request_conditional_review():
    assert not needs_result_review(event('正在处理的任务提示框可见'),'正在处理的任务提示框可见，包含去处理按钮')
