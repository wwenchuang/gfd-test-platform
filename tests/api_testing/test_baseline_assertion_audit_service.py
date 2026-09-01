import json

from task_server.api_testing.services.baseline_assertion_audit_service import (
    BaselineAssertionAuditService,
    _is_one_time,
    analyze_baseline_assertions,
)


def assertion(assertion_type, operator, *, expected=None, path=None, enabled=True):
    item = {
        "type": assertion_type,
        "operator": operator,
        "enabled": enabled,
    }
    if expected is not None:
        item["expected"] = expected
    if path is not None:
        item["path"] = path
    return item


def response(status_code, body):
    return {
        "status_code": status_code,
        "body": json.dumps(body, ensure_ascii=False) if not isinstance(body, str) else body,
    }


READ_ONLY = {
    "kind": "read_only",
    "label": "只读查询",
    "risk": "low",
    "requires_setup": False,
    "requires_cleanup": False,
    "baseline_policy": "direct",
    "reason": "可直接执行",
}


def test_exact_business_success_assertion_matches_actual_response():
    result = analyze_baseline_assertions(
        [
            assertion("status_code", "equals", expected=200),
            assertion("json_path", "equals", expected=0, path="$.code"),
            assertion(
                "schema",
                "matches",
                expected={"type": "object", "required": ["code", "data"]},
            ),
        ],
        response(200, {"code": 0, "data": []}),
        READ_ONLY,
        {},
    )

    assert result["status"] == "verified"
    assert result["business_path"] == "$.code"
    assert result["business_value"] == 0
    assert result["suggested_assertions"] == []
    assert result["execution"]["selectable"] is True


def test_read_only_business_success_with_only_data_exists_needs_domain_assertion():
    result = analyze_baseline_assertions(
        [
            assertion("status_code", "equals", expected=200),
            assertion("json_path", "equals", expected=0, path="$.code"),
            assertion("json_path", "exists", path="$.data"),
        ],
        response(200, {"code": 0, "data": []}),
        READ_ONLY,
        {},
    )

    assert result["status"] == "domain_assertion_required"
    assert "业务成功" in result["reason"]
    assert "领域" in result["reason"]


def test_read_only_business_success_with_precise_domain_assertion_is_verified():
    result = analyze_baseline_assertions(
        [
            assertion("status_code", "equals", expected=200),
            assertion("json_path", "equals", expected=0, path="$.code"),
            assertion("json_path", "equals", expected=10, path="$.data.size"),
        ],
        response(200, {"code": 0, "data": {"size": 10, "records": []}}),
        READ_ONLY,
        {},
    )

    assert result["status"] == "verified"


def test_status_only_success_response_receives_exact_business_suggestion():
    result = analyze_baseline_assertions(
        [assertion("status_code", "equals", expected=200)],
        response(200, {"code": 200, "message": "返回成功"}),
        READ_ONLY,
        {},
    )

    assert result["status"] == "upgrade_available"
    assert result["suggested_assertions"] == [
        assertion("json_path", "equals", expected=200, path="$.code")
    ]


def test_unexpected_business_failure_is_not_converted_into_expected_failure():
    result = analyze_baseline_assertions(
        [assertion("status_code", "equals", expected=200)],
        response(200, {"code": 1001, "msg": "设备不存在"}),
        READ_ONLY,
        {},
    )

    assert result["status"] == "business_failure"
    assert result["business_value"] == 1001
    assert result["suggested_assertions"] == []
    assert "不能自动采纳" in result["reason"]


def test_exact_negative_business_case_remains_valid():
    result = analyze_baseline_assertions(
        [
            assertion("status_code", "equals", expected=200),
            assertion("json_path", "equals", expected=1001, path="$.code"),
        ],
        response(200, {"code": 1001, "msg": "参数缺失"}),
        READ_ONLY,
        {},
    )

    assert result["status"] == "verified"
    assert result["reason"] == "精确业务断言与实际响应一致"


def test_success_boolean_receives_boolean_suggestion():
    result = analyze_baseline_assertions(
        [assertion("status_code", "equals", expected=200)],
        response(200, {"success": True, "result": {"id": 1}}),
        READ_ONLY,
        {},
    )

    assert result["status"] == "upgrade_available"
    assert result["suggested_assertions"] == [
        assertion("json_path", "equals", expected=True, path="$.success")
    ]


def test_boolean_code_is_not_misclassified_as_numeric_success():
    result = analyze_baseline_assertions(
        [assertion("status_code", "equals", expected=200)],
        response(200, {"code": False, "message": "invalid response contract"}),
        READ_ONLY,
        {},
    )

    assert result["status"] == "business_failure"
    assert result["suggested_assertions"] == []


def test_numeric_assertion_does_not_match_a_boolean_business_code():
    result = analyze_baseline_assertions(
        [assertion("json_path", "equals", expected=0, path="$.code")],
        response(200, {"code": False, "message": "invalid response contract"}),
        READ_ONLY,
        {},
    )

    assert result["status"] == "business_failure"
    assert result["business_value"] is False


def test_conflicting_success_fields_are_not_hidden_by_an_exact_code_assertion():
    result = analyze_baseline_assertions(
        [assertion("json_path", "equals", expected=0, path="$.code")],
        response(200, {"code": 0, "success": False, "message": "failed"}),
        READ_ONLY,
        {},
    )

    assert result["status"] == "business_failure"
    assert result["business_path"] == "$.success"
    assert result["business_value"] is False


def test_status_only_response_without_business_envelope_needs_domain_assertion():
    result = analyze_baseline_assertions(
        [assertion("status_code", "equals", expected=200)],
        response(200, {"items": [{"id": 1}]}),
        READ_ONLY,
        {},
    )

    assert result["status"] == "domain_assertion_required"
    assert result["business_path"] == ""


def test_transport_headers_do_not_count_as_domain_assertions():
    result = analyze_baseline_assertions(
        [
            assertion("status_code", "equals", expected=200),
            assertion("header", "equals", expected="application/json", path="Content-Type"),
        ],
        response(200, {"items": [{"id": 1}]}),
        READ_ONLY,
        {},
    )

    assert result["status"] == "domain_assertion_required"


def test_unexpected_http_failure_is_not_treated_as_valid_domain_evidence():
    result = analyze_baseline_assertions(
        [assertion("schema", "matches", expected={"type": "object"})],
        response(500, {"message": "server error"}),
        READ_ONLY,
        {},
    )

    assert result["status"] == "http_failure"
    assert result["actual_http_status"] == 500


def test_exact_negative_http_case_remains_valid():
    result = analyze_baseline_assertions(
        [assertion("status_code", "equals", expected=404)],
        response(404, {"message": "not found"}),
        READ_ONLY,
        {},
    )

    assert result["status"] == "verified"
    assert "负向" in result["reason"]


def test_missing_or_unparseable_evidence_is_explicit():
    missing = analyze_baseline_assertions([], None, READ_ONLY, {})
    invalid = analyze_baseline_assertions([], response(200, "not-json"), READ_ONLY, {})

    assert missing["status"] == "evidence_missing"
    assert invalid["status"] == "domain_assertion_required"


def test_guarded_write_remains_manual_when_step_semantics_cannot_be_proven():
    workflow = {
        **READ_ONLY,
        "kind": "device_control",
        "label": "设备控制",
        "risk": "high",
        "requires_setup": True,
        "requires_cleanup": True,
        "baseline_policy": "guarded",
        "reason": "执行后恢复原设置",
    }
    result = analyze_baseline_assertions(
        [assertion("json_path", "equals", expected=0, path="$.code")],
        response(200, {"code": 0}),
        workflow,
        {"setup_steps": [{"enabled": True}], "cleanup_steps": [{"enabled": True}]},
    )

    assert result["execution"]["selectable"] is False
    assert result["execution"]["level"] == "manual"
    assert "逐条确认" in result["execution"]["reason"]


def test_print_review_requires_this_job_extraction_and_cancel_cleanup():
    workflow = {
        **READ_ONLY,
        "kind": "print_lifecycle",
        "label": "打印生命周期",
        "risk": "high",
        "requires_setup": True,
        "requires_cleanup": True,
        "baseline_policy": "guarded",
        "reason": "打印成功后必须取消本次打印",
    }
    arbitrary = analyze_baseline_assertions(
        [assertion("json_path", "equals", expected=0, path="$.code")],
        response(200, {"code": 0}),
        workflow,
        {
            "setup_steps": [{"enabled": True, "name": "任意准备"}],
            "cleanup_steps": [{"enabled": True, "name": "任意清理"}],
        },
        [],
    )
    protected = analyze_baseline_assertions(
        [assertion("json_path", "equals", expected=0, path="$.code")],
        response(200, {"code": 0}),
        workflow,
        {
            "setup_steps": [{"enabled": True, "name": "准备设备"}],
            "cleanup_steps": [{
                "enabled": True,
                "name": "取消本次打印",
                "request": {"method": "POST", "path": "/print3d/api/v1/printJob/cancel"},
                "required_variables": ["printTaskSn"],
            }],
        },
        [{"target": "printTaskSn", "type": "json_path", "path": "$.data.printTaskSn"}],
    )

    assert arbitrary["execution"]["selectable"] is False
    assert "打印任务标识" in arbitrary["execution"]["reason"]
    assert protected["execution"]["selectable"] is True
    assert protected["execution"]["level"] == "guarded"


def test_normal_api_test_name_is_not_misclassified_as_one_time():
    assert _is_one_time("API test 设备列表", "基础回归", ("api",)) is False
    assert _is_one_time("一次性数据初始化", "人工验证", ()) is True


def test_one_time_audit_is_counted_as_manual_only_not_automated_review():
    items = [
        {
            "status": "domain_assertion_required",
            "manual_only": True,
            "execution": {"selectable": False},
        },
        {
            "status": "business_failure",
            "manual_only": False,
            "execution": {"selectable": False},
        },
        {
            "status": "verified",
            "manual_only": False,
            "execution": {"selectable": True},
        },
    ]

    summary = BaselineAssertionAuditService._summary(items)

    assert summary["total"] == 3
    assert summary["verified"] == 1
    assert summary["needs_review"] == 1
    assert summary["manual_only"] == 1
