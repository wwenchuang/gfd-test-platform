import json
from urllib.parse import urlparse, parse_qs


class _FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


def test_search_case_platform_cases_normalizes_agiletc_results(monkeypatch):
    from task_server.services import case_platform_service

    requests = []

    def fake_urlopen(req, timeout):
        requests.append((req.full_url, timeout))
        query = parse_qs(urlparse(req.full_url).query)
        assert query["productLineId"] == ["1"]
        assert query["caseType"] == ["0"]
        assert query["channel"] == ["1"]
        assert query["bizId"] == ["root"]
        return _FakeResponse({
            "code": 200,
            "msg": "服务运行成功",
            "data": {
                "total": 1,
                "dataSources": [
                    {
                        "id": 3088,
                        "title": "3D共享打印V1.2.2",
                        "description": "",
                        "creator": "wangwc",
                        "modifier": "wangwc",
                        "gmtCreated": "2026-02-06T07:00:00.000+0000",
                        "productLineId": 1,
                        "requirementId": "https://project.feishu.cn/y99fwz/story/detail/6876737017",
                        "recordNum": 0,
                    }
                ],
            },
        })

    monkeypatch.setenv("CASE_PLATFORM_BASE_URL", "http://qa-agiletc.gongfudou.com")
    monkeypatch.setattr(case_platform_service.urllib.request, "urlopen", fake_urlopen)

    result = case_platform_service.search_case_platform_cases("3D共享", limit=5)

    assert result["ok"] is True
    assert result["source"] == "agiletc"
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["id"] == "3088"
    assert item["title"] == "3D共享打印V1.2.2"
    assert item["version"] == "V1.2.2"
    assert item["requirement_link"] == "https://project.feishu.cn/y99fwz/story/detail/6876737017"
    assert item["case_link"] == "http://qa-agiletc.gongfudou.com/caseManager/1/3088/undefined/0"
    assert item["label"] == "3D共享打印V1.2.2 · V1.2.2 · #3088"
    assert any("title=3D" in url for url, _timeout in requests)


def test_search_case_platform_cases_uses_full_requirement_link(monkeypatch):
    from task_server.services import case_platform_service

    requested_queries = []

    def fake_urlopen(req, timeout):
        requested_queries.append(parse_qs(urlparse(req.full_url).query))
        return _FakeResponse({"code": 200, "data": {"total": 0, "dataSources": []}})

    monkeypatch.setenv("CASE_PLATFORM_BASE_URL", "http://qa-agiletc.gongfudou.com/")
    monkeypatch.setattr(case_platform_service.urllib.request, "urlopen", fake_urlopen)

    requirement = "https://project.feishu.cn/xbprint/story/detail/7075534465"
    result = case_platform_service.search_case_platform_cases("", requirement_link=requirement, limit=3)

    assert result["ok"] is True
    assert result["items"] == []
    assert requested_queries[0]["requirementId"] == [requirement]
    assert requested_queries[0]["pageSize"] == ["3"]
