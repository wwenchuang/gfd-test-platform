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
