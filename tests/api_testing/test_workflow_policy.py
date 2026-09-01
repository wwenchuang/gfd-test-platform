from types import SimpleNamespace

import pytest

from task_server.api_testing.services.workflow_policy import classify_endpoint_workflow


def endpoint(method, path, summary, tags=()):
    return SimpleNamespace(method=method, path=path, summary=summary, tags=list(tags))


def test_classifies_print_dispatch_as_guarded_lifecycle():
    result = classify_endpoint_workflow(
        endpoint("POST", "/print3d/api/v1/print/start", "下发打印")
    )

    assert result == {
        "kind": "print_lifecycle",
        "label": "打印生命周期",
        "risk": "high",
        "requires_setup": True,
        "requires_cleanup": True,
        "baseline_policy": "guarded",
        "reason": "动态获取设备和切片产物；打印成功后必须取消本次打印",
    }


def test_classifies_delete_as_disposable_resource_only():
    result = classify_endpoint_workflow(
        endpoint("DELETE", "/models/{modelSn}", "删除模型")
    )

    assert result["kind"] == "delete_resource"
    assert result["requires_setup"] is True
    assert result["requires_cleanup"] is False
    assert result["baseline_policy"] == "guarded"
    assert "临时资源" in result["reason"]


def test_classifies_reversible_state_and_read_only_endpoints():
    favorite = classify_endpoint_workflow(
        endpoint("POST", "/collection/add", "添加收藏", ("我的收藏",))
    )
    listing = classify_endpoint_workflow(
        endpoint("GET", "/collection/page", "我的收藏列表", ("我的收藏",))
    )

    assert favorite["kind"] == "reversible_state"
    assert favorite["requires_setup"] is True
    assert favorite["requires_cleanup"] is True
    assert listing["kind"] == "read_only"
    assert listing["baseline_policy"] == "direct"


def test_classifies_irreversible_business_action_as_excluded():
    result = classify_endpoint_workflow(
        endpoint("POST", "/points/exchange", "积分兑换商品")
    )

    assert result["kind"] == "irreversible"
    assert result["baseline_policy"] == "excluded"
    assert result["requires_setup"] is False
    assert result["requires_cleanup"] is False


def test_read_only_queries_do_not_inherit_mutation_risk_from_business_words():
    exchange_page = classify_endpoint_workflow(
        endpoint("GET", "/points/exchange/page", "积分兑换商品分页")
    )
    uploaded_models = classify_endpoint_workflow(
        endpoint("GET", "/models/upload/list", "上传模型查询")
    )
    slice_detail = classify_endpoint_workflow(
        endpoint("GET", "/slice/{id}", "切片详情")
    )

    assert exchange_page["baseline_policy"] == "direct"
    assert uploaded_models["baseline_policy"] == "direct"
    assert slice_detail["kind"] == "resource_query"
    assert slice_detail["baseline_policy"] == "direct"


@pytest.mark.parametrize(
    ("path", "summary"),
    (
        ("/pmc/api/v1/iot/topicMsg", "发送消息"),
        ("/print3dAI/api/v1/imageModel", "创建建模任务"),
        ("/print3d/api/v1/devices/clickMaterialDeplete", "点击正品耗材提示"),
        ("/print3d/api/v1/model/clickLatestModel", "点击最新模型接口"),
        ("/print3d/api/v1/devices/upgrade", "设备手动升级"),
        ("/print3d/api/v1/imageModel/cancel", "图片建模取消"),
        ("/print3d/api/v1/createTextModel/continue", "继续文字建模"),
        ("/print3d/api/v1/createTextModel/retry", "重试文字建模"),
    ),
)
def test_get_endpoints_with_action_semantics_are_not_treated_as_read_only(path, summary):
    result = classify_endpoint_workflow(endpoint("GET", path, summary))

    assert result["kind"] == "semantic_get_mutation"
    assert result["baseline_policy"] == "manual"
    assert result["requires_setup"] is True
    assert result["requires_cleanup"] is True


def test_get_query_with_create_in_the_resource_name_remains_read_only():
    result = classify_endpoint_workflow(
        endpoint("GET", "/print3d/api/v1/createTextModel/queryCurrentModel", "查询当前生成的任务")
    )

    assert result["kind"] == "read_only"
    assert result["baseline_policy"] == "direct"


@pytest.mark.parametrize(
    ("path", "summary"),
    (
        ("/print3d/api/v1/auth/generateCaptcha", "获取滑块信息接口"),
        ("/print3d/api/v1/auth/publicKey", "获取加密公钥"),
        ("/print3d/api/v1/qidi/token", "获取obstoken"),
        ("/print3d/api/v1/users/getNewToken", "刷新token接口(鉴权)"),
    ),
)
def test_credential_and_challenge_get_endpoints_are_manual_authentication_flows(path, summary):
    result = classify_endpoint_workflow(endpoint("GET", path, summary))

    assert result["kind"] == "authentication"
    assert result["baseline_policy"] == "manual"
    assert result["requires_setup"] is True
    assert result["requires_cleanup"] is True


def test_get_device_state_mark_is_treated_as_a_state_change():
    result = classify_endpoint_workflow(
        endpoint("GET", "/print3d/api/v1/devices/deviceStateMark", "设备进料标记")
    )

    assert result["kind"] == "semantic_get_mutation"
    assert result["baseline_policy"] == "manual"


def test_classifies_device_settings_as_restore_required():
    result = classify_endpoint_workflow(
        endpoint("POST", "/devices/settings/update", "修改设备参数", ("设备控制",))
    )

    assert result["kind"] == "device_control"
    assert result["requires_setup"] is True
    assert result["requires_cleanup"] is True
    assert "恢复" in result["reason"]
