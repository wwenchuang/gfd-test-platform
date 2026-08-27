import json

import pytest

from task_server.api_testing.contracts.case import CasePayloadError, parse_case_payload
from task_server.services import business_line_service


def _request(path):
    return {
        "method": "GET",
        "path": path,
        "service": "default",
        "path_params": {},
        "query": {},
        "headers": {},
        "cookies": {},
        "body": None,
    }


def _step(name, path, *, required_variables=None, polling=None):
    step = {
        "name": name,
        "enabled": True,
        "request": _request(path),
        "assertions": [
            {
                "type": "status_code",
                "operator": "equals",
                "expected": 200,
                "timeout_ms": 0,
                "enabled": True,
            }
        ],
        "extractions": [
            {
                "target": "resourceSn",
                "type": "json_path",
                "path": "$.data.sn",
                "required": True,
            }
        ],
        "required_variables": required_variables or [],
    }
    if polling is not None:
        step["polling"] = polling
    return step


def _payload(processing):
    return {
        "name": "用例内编排",
        "purpose": "验证前置、主体和清理步骤的数据传递",
        "priority": "P1",
        "app_package": "com.kfb.model",
        "app_name": "智小白3D",
        "business": "home",
        "request": _request("/resource/{{resourceSn}}"),
        "data_rows": [],
        "assertions": [],
        "extractions": [],
        "dependencies": [],
        "processing": processing,
    }


@pytest.mark.parametrize("business", ["home", "shared"])
def test_case_contract_accepts_explicit_business(business):
    payload = _payload({"pre": [], "post": []})
    payload["business"] = business

    assert parse_case_payload(payload)["business"] == business


def test_case_contract_rejects_unknown_business():
    payload = _payload({"pre": [], "post": []})
    payload["business"] = "enterprise"

    with pytest.raises(CasePayloadError, match="business is not supported"):
        parse_case_payload(payload)


def test_case_contract_accepts_configured_business_internal_id(tmp_path, monkeypatch):
    path = tmp_path / "task-apps.json"
    path.write_text(json.dumps({"apps": [{
        "package": "com.kfb.model",
        "business_lines": [{"id": "biz_school", "name": "校园版", "enabled": True}],
    }]}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(business_line_service, "TASK_APPS_FILE", str(path))
    payload = _payload({"pre": [], "post": []})
    payload["business"] = "biz_school"

    assert parse_case_payload(payload)["business"] == "biz_school"


def test_case_contract_requires_application_identity_and_validates_its_business(tmp_path, monkeypatch):
    path = tmp_path / "task-apps.json"
    path.write_text(json.dumps({"apps": [
        {
            "package": "com.kfb.model",
            "name": "智小白3D",
            "business_lines": [{"id": "home", "name": "家用", "enabled": True}],
        },
        {
            "package": "com.example.school",
            "name": "校园版",
            "business_lines": [{"id": "school", "name": "校园业务", "enabled": True}],
        },
    ]}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(business_line_service, "TASK_APPS_FILE", str(path))
    payload = _payload({"pre": [], "post": []})
    payload.update({
        "app_package": "com.example.school",
        "app_name": "校园版",
        "business": "校园业务",
    })

    parsed = parse_case_payload(payload)

    assert parsed["app_package"] == "com.example.school"
    assert parsed["app_name"] == "校园版"
    assert parsed["business"] == "school"

    payload["business"] = "home"
    with pytest.raises(CasePayloadError, match="business is not supported"):
        parse_case_payload(payload)


def test_case_contract_rejects_missing_application_identity():
    payload = _payload({"pre": [], "post": []})
    del payload["app_package"]
    with pytest.raises(CasePayloadError, match="app_package"):
        parse_case_payload(payload)


def test_case_contract_accepts_strict_setup_and_cleanup_steps():
    parsed = parse_case_payload(
        _payload(
            {
                "pre": [],
                "post": [],
                "setup_steps": [_step("查询可用资源", "/resources")],
                "cleanup_steps": [
                    _step(
                        "删除本次资源",
                        "/resource/{{resourceSn}}/delete",
                        required_variables=["resourceSn"],
                    )
                ],
            }
        )
    )

    assert parsed["processing"]["setup_steps"][0]["name"] == "查询可用资源"
    assert parsed["processing"]["cleanup_steps"][0]["required_variables"] == [
        "resourceSn"
    ]


def test_case_contract_keeps_old_processing_shape_compatible():
    parsed = parse_case_payload(_payload({"pre": [], "post": []}))

    assert parsed["processing"]["setup_steps"] == []
    assert parsed["processing"]["cleanup_steps"] == []


def test_case_contract_rejects_duplicate_step_names_within_a_phase():
    with pytest.raises(CasePayloadError, match="names must be unique"):
        parse_case_payload(
            _payload(
                {
                    "pre": [],
                    "post": [],
                    "setup_steps": [
                        _step("查询资源", "/resources"),
                        _step("查询资源", "/resources/next"),
                    ],
                    "cleanup_steps": [],
                }
            )
        )


def test_case_contract_limits_each_inline_phase_to_twenty_steps():
    with pytest.raises(CasePayloadError, match="at most 20 entries"):
        parse_case_payload(
            _payload(
                {
                    "pre": [],
                    "post": [],
                    "setup_steps": [
                        _step(f"步骤 {index}", f"/resources/{index}")
                        for index in range(21)
                    ],
                    "cleanup_steps": [],
                }
            )
        )


def test_case_contract_rejects_invalid_required_variable_names():
    with pytest.raises(CasePayloadError, match="valid variable name"):
        parse_case_payload(
            _payload(
                {
                    "pre": [],
                    "post": [],
                    "setup_steps": [],
                    "cleanup_steps": [
                        _step(
                            "清理资源",
                            "/resource/delete",
                            required_variables=["resource sn"],
                        )
                    ],
                }
            )
        )


def test_case_contract_rejects_unknown_inline_step_fields():
    step = _step("查询资源", "/resources")
    step["dependencies"] = []
    with pytest.raises(CasePayloadError, match="contains unknown field"):
        parse_case_payload(
            _payload(
                {
                    "pre": [],
                    "post": [],
                    "setup_steps": [step],
                    "cleanup_steps": [],
                }
            )
        )


def test_case_contract_accepts_bounded_polling_for_query_steps():
    parsed = parse_case_payload(
        _payload(
            {
                "pre": [],
                "post": [],
                "setup_steps": [
                    _step(
                        "等待图片生成",
                        "/text-model/query",
                        polling={"max_attempts": 10, "interval_ms": 2000},
                    )
                ],
                "cleanup_steps": [],
            }
        )
    )

    assert parsed["processing"]["setup_steps"][0]["polling"] == {
        "max_attempts": 10,
        "interval_ms": 2000,
    }


@pytest.mark.parametrize(
    ("polling", "message"),
    [
        ({"max_attempts": 1, "interval_ms": 2000}, "max_attempts"),
        ({"max_attempts": 10, "interval_ms": 50}, "interval_ms"),
        ({"max_attempts": 30, "interval_ms": 30_000}, "total wait"),
    ],
)
def test_case_contract_rejects_unbounded_polling(polling, message):
    with pytest.raises(CasePayloadError, match=message):
        parse_case_payload(
            _payload(
                {
                    "pre": [],
                    "post": [],
                    "setup_steps": [
                        _step("等待图片生成", "/text-model/query", polling=polling)
                    ],
                    "cleanup_steps": [],
                }
            )
        )


def test_case_contract_rejects_polling_for_mutating_steps():
    step = _step(
        "错误重复创建",
        "/text-model/create",
        polling={"max_attempts": 3, "interval_ms": 1000},
    )
    step["request"]["method"] = "POST"
    with pytest.raises(CasePayloadError, match="GET or HEAD"):
        parse_case_payload(
            _payload(
                {
                    "pre": [],
                    "post": [],
                    "setup_steps": [step],
                    "cleanup_steps": [],
                }
            )
        )
