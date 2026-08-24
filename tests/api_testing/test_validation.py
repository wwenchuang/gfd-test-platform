from types import SimpleNamespace

from task_server.api_testing.validation import validate_case


def _request(path, *, method="GET", query=None, body=None):
    return {
        "method": method,
        "path": path,
        "service": "default",
        "path_params": {},
        "query": query or {},
        "headers": {},
        "cookies": {},
        "body": body,
    }


def _step(name, request, *, extractions=None, required_variables=None):
    return {
        "name": name,
        "enabled": True,
        "request": request,
        "assertions": [],
        "extractions": extractions or [],
        "required_variables": required_variables or [],
    }


def _extraction(target, path):
    return {"target": target, "type": "json_path", "path": path, "required": True}


def _case(endpoint, *, request=None, processing=None, extractions=None):
    return SimpleNamespace(
        id="case-version-1",
        endpoint_id=endpoint.id,
        request=request or _request(endpoint.path, method=endpoint.method),
        data_rows=(),
        assertions=(),
        extractions=tuple(extractions or ()),
        dependencies=(),
        processing=processing
        or {"pre": [], "post": [], "setup_steps": [], "cleanup_steps": []},
    )


def _endpoint(path="/resource/detail", *, method="GET", summary="资源详情"):
    return SimpleNamespace(
        id="endpoint-1",
        method=method,
        path=path,
        summary=summary,
        operation={},
    )


def test_validation_tracks_setup_exports_in_order_and_into_main_request():
    endpoint = _endpoint()
    case = _case(
        endpoint,
        request=_request(endpoint.path, query={"sn": "{{detailSn}}"}),
        processing={
            "pre": [],
            "post": [],
            "setup_steps": [
                _step(
                    "查询资源列表",
                    _request("/resource/page"),
                    extractions=[_extraction("resourceSn", "$.data.list[0].sn")],
                ),
                _step(
                    "查询资源详情",
                    _request("/resource/detail", query={"sn": "{{resourceSn}}"}),
                    extractions=[_extraction("detailSn", "$.data.sn")],
                    required_variables=["resourceSn"],
                ),
            ],
            "cleanup_steps": [],
        },
    )

    result = validate_case(case, endpoint, {"variables": {}, "services": {}})

    assert not any(item.code == "undefined_variable" for item in result.errors)


def test_validation_reports_undefined_setup_and_cleanup_variables_at_the_step():
    endpoint = _endpoint()
    case = _case(
        endpoint,
        processing={
            "pre": [],
            "post": [],
            "setup_steps": [
                _step(
                    "查询详情",
                    _request("/resource/detail", query={"sn": "{{missingSn}}"}),
                    required_variables=["missingSn"],
                )
            ],
            "cleanup_steps": [
                _step(
                    "删除本次资源",
                    _request("/resource/delete", method="POST", body={"sn": "{{createdSn}}"}),
                    required_variables=["createdSn"],
                )
            ],
        },
    )

    result = validate_case(case, endpoint, {"variables": {}, "services": {}})

    fields = {item.field for item in result.errors if item.code == "undefined_variable"}
    assert "processing.setup_steps[0].request" in fields
    assert "processing.cleanup_steps[0].request" in fields


def test_print_dispatch_requires_task_extraction_and_cancel_cleanup():
    endpoint = _endpoint(
        "/print3d/api/v1/printJob/print",
        method="POST",
        summary="下发打印",
    )
    case = _case(endpoint)

    result = validate_case(case, endpoint, {"variables": {}, "services": {}})

    assert {item.code for item in result.errors} >= {
        "print_task_extraction_required",
        "print_cleanup_required",
    }


def test_print_dispatch_with_dynamic_task_id_and_cancel_step_is_baseline_ready():
    endpoint = _endpoint(
        "/print3d/api/v1/printJob/print",
        method="POST",
        summary="下发打印",
    )
    case = _case(
        endpoint,
        extractions=[
            SimpleNamespace(
                target="printTaskSn",
                type="json_path",
                path="$.data.printTaskSn",
                name=None,
            )
        ],
        processing={
            "pre": [],
            "post": [],
            "setup_steps": [],
            "cleanup_steps": [
                _step(
                    "取消本次打印",
                    _request(
                        "/print3d/api/v1/printJob/cancel",
                        method="POST",
                        body={"printTaskSn": "{{printTaskSn}}"},
                    ),
                    required_variables=["printTaskSn"],
                )
            ],
        },
    )

    result = validate_case(case, endpoint, {"variables": {}, "services": {}})

    assert not {
        "print_task_extraction_required",
        "print_cleanup_required",
        "undefined_variable",
    } & {item.code for item in result.errors}
