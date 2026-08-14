import json
from pathlib import Path

import pytest


def _write_summary(root: Path, case_set_id: str = "case-a") -> Path:
    case_dir = root / "cases" / case_set_id
    case_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "case_set_id": case_set_id,
        "title": "共享打印V1.2.2",
        "module": "共享打印",
        "generated_at": "2026-02-04 11:20:00",
        "analysis": {
            "business_goals": ["验证运维、商户、经销商分润功能是否符合需求并确保核心业务流程不受影响。"],
            "risks": ["管理员权限配置错误会影响分润入口可见性。"],
        },
        "scenarios": [
            {"feature": "管理员分成与权限", "scenario": "模块与权限", "expected": "不同角色菜单符合权限配置"},
            {"feature": "管理员分成与权限", "scenario": "管理员分成设置页面", "expected": "基础配置可保存并展示"},
            {"feature": "商户分润", "scenario": "商户规则配置", "expected": "商户规则生效"},
        ],
        "cases": [
            {
                "case_id": "TC-001",
                "title": "经销商与管理员菜单权限隔离",
                "priority": "P1",
                "smoke": False,
                "feature": "管理员分成与权限",
                "scenario": "模块与权限",
                "expected_result": "经销商可见店铺分成设置，管理员仅可见店铺分成设置。",
                "steps": ["登录经销商账号", "进入设备管理页", "检查分成设置入口"],
                "assertions": ["经销商和管理员菜单符合权限配置"],
            },
            {
                "case_id": "TC-002",
                "title": "管理员分成设置入口展示",
                "priority": "P2",
                "smoke": True,
                "feature": "管理员分成与权限",
                "scenario": "管理员分成设置页面",
                "expected_result": "页面展示管理员分成设置入口。",
                "steps": ["登录商户账号", "进入分成设置"],
                "assertions": ["管理员分成设置入口可见"],
            },
            {
                "case_id": "TC-003",
                "title": "管理员分成比例保存",
                "priority": "P2",
                "smoke": False,
                "feature": "管理员分成与权限",
                "scenario": "管理员分成设置页面",
                "expected_result": "比例保存成功。",
                "steps": ["修改分成比例", "保存"],
                "assertions": ["出现保存成功反馈"],
            },
            {
                "case_id": "TC-004",
                "title": "商户分润规则展示",
                "priority": "P0",
                "smoke": False,
                "feature": "商户分润",
                "scenario": "商户规则配置",
                "expected_result": "规则列表展示正确。",
                "steps": ["进入商户分润", "查看规则列表"],
                "assertions": ["列表展示当前规则"],
            },
        ],
        "manual_cases": [
            {
                "case_id": "MT-001",
                "title": "财务后台实际结算金额复核",
                "priority": "P1",
                "feature": "财务结算",
                "scenario": "线下结算复核",
                "reason": "需要真实结算账期和后台权限。",
                "steps": ["准备结算账期", "核对账单"],
                "assertions": ["账单金额和分润规则一致"],
            }
        ],
        "generatedCaseGroups": {
            "executable_cases": [
                {"case_id": "TC-001", "file": "share-permission.yaml", "target_task_name": "经销商与管理员菜单权限隔离"},
                {"case_id": "TC-002", "file": "share-admin.yaml", "target_task_name": "管理员分成设置入口展示"},
                {"case_id": "TC-003", "file": "share-admin.yaml", "target_task_name": "管理员分成比例保存"},
                {"case_id": "TC-004", "file": "share-merchant.yaml", "target_task_name": "商户分润规则展示"},
            ]
        },
        "report_checkpoints": ["不同角色入口和权限展示正确", "分成设置保存后反馈明确"],
    }
    (case_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return case_dir


@pytest.fixture()
def report_workspace(tmp_path, monkeypatch):
    from task_server.services import test_report_service

    case_root = tmp_path / "cases"
    learning_root = tmp_path / "learning"
    case_root.mkdir()
    learning_root.mkdir()
    monkeypatch.setattr(test_report_service, "CASE_DIR", str(case_root))
    monkeypatch.setattr(test_report_service, "LEARNING_DIR", str(learning_root))
    monkeypatch.setattr(test_report_service, "TEST_REPORT_INDEX_FILE", str(learning_root / "test-report-index.json"))
    _write_summary(tmp_path)
    return tmp_path


def test_load_reportable_cases_defaults_to_p0_p1_and_smoke(report_workspace):
    from task_server.services import test_report_service

    result = test_report_service.load_reportable_cases("case-a")

    selected = [
        case["case_id"]
        for group in result["groups"]
        for scenario in group["scenarios"]
        for case in scenario["cases"]
        if case["default_selected"]
    ]
    assert selected == ["TC-001", "TC-002", "TC-004"]
    assert result["counts"]["manual_case_count"] == 1
    assert result["title"] == "共享打印V1.2.2"


def test_preview_uses_concise_scope_and_unexecuted_quality(report_workspace):
    from task_server.services import test_report_service

    result = test_report_service.preview_test_report({
        "case_set_id": "case-a",
        "selected_case_ids": ["TC-001", "TC-002", "TC-003", "TC-004"],
        "meta": {
            "report_title": "共享打印V1.2.2-测试报告",
            "tester": "王文闯",
            "client_side": "mini",
            "test_start": "2026-02-05",
            "test_end": "2026-02-06",
        },
    })

    assert result["statistics"]["total"] == 4
    assert result["statistics"]["not_executed"] == 4
    assert result["quality"]["result"] == "未执行"
    assert "测试范围" not in result["scope_markdown"]
    assert "模块与权限" in result["scope_markdown"]
    assert result["scope_markdown"].count("\n") <= 10
    assert "仅完成测试设计" in result["release"]["suggestion"]


def test_create_report_persists_markdown_html_and_index(report_workspace):
    from task_server.services import test_report_service

    result = test_report_service.create_test_report({
        "case_set_id": "case-a",
        "selected_case_ids": ["TC-001", "TC-002"],
        "meta": {
            "report_title": "共享打印V1.2.2-测试报告",
            "tester": "王文闯",
            "client_side": "mini",
            "requirement_link": "填写飞书链接",
            "case_link": "http://qa-agiletc.gongfudou.com/caseManager/1/3088/undefined/0",
        },
    })

    assert Path(result["files"]["markdown"]).exists()
    assert Path(result["files"]["html"]).exists()
    markdown = Path(result["files"]["markdown"]).read_text(encoding="utf-8")
    html = Path(result["files"]["html"]).read_text(encoding="utf-8")
    assert "## 1. 基本信息" in markdown
    assert "## 2. 测试概要" in markdown
    assert "测试范围" in markdown
    assert "共享打印V1.2.2-测试报告" in html
    assert test_report_service.read_test_report(result["report_id"])["report_id"] == result["report_id"]
    indexed = test_report_service.list_test_reports(case_set_id="case-a")
    assert [item["report_id"] for item in indexed] == [result["report_id"]]


def test_template_missing_sections_gets_default_fallback(report_workspace):
    from task_server.services import test_report_service

    template = test_report_service.save_test_report_template({
        "name": "极简模板",
        "filename": "simple.md",
        "content": "# {{report_title}}\n\n测试人员：{{tester}}\n",
    })
    result = test_report_service.preview_test_report({
        "case_set_id": "case-a",
        "selected_case_ids": ["TC-001"],
        "template_id": template["template_id"],
        "meta": {"report_title": "共享打印V1.2.2-测试报告", "tester": "王文闯"},
    })

    assert "# 共享打印V1.2.2-测试报告" in result["markdown"]
    assert "## 2. 测试概要" in result["markdown"]
    assert "## 5. 发布建议" in result["markdown"]


def test_preview_enriches_execution_result_from_midscene_report_index(report_workspace):
    from task_server.services import test_report_service

    report_index = Path(test_report_service.LEARNING_DIR) / "report-index.json"
    report_index.write_text(json.dumps({
        "reports": [{
            "reportId": "rpt-001",
            "jobId": "job-001",
            "module": "共享打印",
            "file": "share-permission.yaml",
            "status": "success",
            "reportUrl": "/reports/share-permission/index.html",
            "createdAt": "2026-02-06 10:00:00",
            "summary": "TC-001 执行通过",
        }]
    }, ensure_ascii=False), encoding="utf-8")

    result = test_report_service.preview_test_report({
        "case_set_id": "case-a",
        "selected_case_ids": ["TC-001", "TC-002"],
        "meta": {"report_title": "共享打印V1.2.2-测试报告"},
    })

    by_id = {case["case_id"]: case for case in result["cases"]}
    assert by_id["TC-001"]["status"] == "passed"
    assert by_id["TC-001"]["report_url"] == "/reports/share-permission/index.html"
    assert by_id["TC-002"]["status"] == "not_executed"
    assert result["statistics"]["passed"] == 1
    assert result["statistics"]["not_executed"] == 1
