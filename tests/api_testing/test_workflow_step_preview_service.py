import pytest

from task_server.api_testing.contracts import case as case_contract
from task_server.api_testing.contracts.case import CasePayloadError
from task_server.api_testing.services.workflow_step_preview_service import (
    WorkflowStepPreviewService,
    flatten_response_fields,
)


def test_response_fields_are_selectable_and_sensitive_values_remain_available():
    result = flatten_response_fields(
        {
            "status_code": 200,
            "headers": {"X-Request-Id": "request-1"},
            "cookies": {"sessionToken": "cookie-secret"},
            "body": {
                "code": 0,
                "data": {
                    "access_token": "body-secret",
                    "profile": {"userSn": "user-1"},
                },
            },
        }
    )

    by_key = {
        (field["source"], field.get("path") or field.get("name")): field
        for field in result["fields"]
    }
    token = by_key[("json_path", "$.data.access_token")]
    user_sn = by_key[("json_path", "$.data.profile.userSn")]
    cookie = by_key[("cookie", "sessionToken")]

    assert token == {
        "id": "json_path:$.data.access_token",
        "source": "json_path",
        "path": "$.data.access_token",
        "name": "access_token",
        "value": "body-secret",
        "value_type": "string",
        "sensitive": True,
        "suggested_target": "access_token",
    }
    assert user_sn["value"] == "user-1"
    assert user_sn["sensitive"] is False
    assert cookie["value"] == "cookie-secret"
    assert cookie["sensitive"] is True
    assert result["truncated"] is False


def test_response_field_limit_is_explicit():
    result = flatten_response_fields(
        {"status_code": 200, "headers": {}, "cookies": {}, "body": {str(index): index for index in range(600)}}
    )

    assert len(result["fields"]) == 500
    assert result["truncated"] is True


def _step(*, enabled=True, extraction_target="accessToken"):
    return {
        "name": "获取令牌",
        "enabled": enabled,
        "request": {
            "method": "GET",
            "path": "/token",
            "service": "default",
            "path_params": {},
            "query": {},
            "headers": {},
            "cookies": {},
            "body": None,
        },
        "assertions": [],
        "extractions": [
            {
                "target": extraction_target,
                "type": "json_path",
                "path": "$.data.access_token",
                "required": True,
            }
        ],
        "required_variables": [],
    }


def test_preview_payload_rejects_disabled_target_and_unknown_override():
    with pytest.raises(CasePayloadError, match="target setup step is disabled"):
        case_contract.parse_workflow_step_preview_payload(
            {
                "setup_steps": [_step(enabled=False)],
                "target_index": 0,
                "initial_variables": {},
                "processing_pre": [],
                "extraction_overrides": {},
            }
        )

    with pytest.raises(CasePayloadError, match="unknown extracted variable"):
        case_contract.parse_workflow_step_preview_payload(
            {
                "setup_steps": [_step()],
                "target_index": 0,
                "initial_variables": {},
                "processing_pre": [],
                "extraction_overrides": {"missingToken": "replacement"},
            }
        )


def test_preview_payload_accepts_prefix_extraction_override():
    parsed = case_contract.parse_workflow_step_preview_payload(
        {
            "setup_steps": [_step(), {**_step(extraction_target="resourceSn"), "name": "查询资源"}],
            "target_index": 1,
            "initial_variables": {"seed": "value"},
            "processing_pre": [],
            "extraction_overrides": {"accessToken": "replacement"},
        }
    )

    assert parsed["target_index"] == 1
    assert parsed["initial_variables"] == {"seed": "value"}
    assert parsed["extraction_overrides"] == {"accessToken": "replacement"}


def test_preview_service_does_not_offer_fields_from_a_failed_prefix_step():
    class Executor:
        def preview_setup_steps(self, *_args, **_kwargs):
            return {
                "status": "FAILED",
                "failure_category": "product_assertion",
                "error_message": "",
                "trace": [],
                "response": {"status_code": 200, "headers": {}, "cookies": {}, "body": {"token": "wrong-step"}},
                "target_index": 1,
                "executed_index": 0,
                "target_reached": False,
                "available_variables": [],
                "missing_variables": [],
            }

    service = WorkflowStepPreviewService(None, executor=Executor())
    preview = service.preview(
        {
            "environment_revision_id": "environment-1",
            "setup_steps": [_step(), {**_step(extraction_target="resourceSn"), "name": "目标步骤"}],
            "target_index": 1,
            "initial_variables": {},
            "processing_pre": [],
            "extraction_overrides": {},
        }
    )

    assert preview["fields"] == []
