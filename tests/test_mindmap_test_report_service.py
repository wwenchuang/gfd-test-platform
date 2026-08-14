import json
from pathlib import Path

import pytest


def _write_summary(
    root: Path,
    case_set_id: str = "case-a",
    *,
    title: str = "共享打印V1.2.2",
    module: str = "共享打印",
    cases=None,
    manual_cases=None,
) -> Path:
    case_dir = root / "cases" / case_set_id
    case_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "case_set_id": case_set_id,
        "title": title,
        "module": module,
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
        "cases": cases if cases is not None else [
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
        "manual_cases": manual_cases if manual_cases is not None else [
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
                {
                    "case_id": row.get("case_id"),
                    "file": row.get("yaml_file") or f"{case_set_id}-{row.get('case_id')}.yaml",
                    "target_task_name": row.get("title"),
                }
                for row in (cases if cases is not None else [
                    {"case_id": "TC-001", "title": "经销商与管理员菜单权限隔离", "yaml_file": "share-permission.yaml"},
                    {"case_id": "TC-002", "title": "管理员分成设置入口展示", "yaml_file": "share-admin.yaml"},
                    {"case_id": "TC-003", "title": "管理员分成比例保存", "yaml_file": "share-admin.yaml"},
                    {"case_id": "TC-004", "title": "商户分润规则展示", "yaml_file": "share-merchant.yaml"},
                ])
                if row.get("case_id")
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


def test_load_reportable_cases_accepts_multiple_case_sets(report_workspace):
    from task_server.services import test_report_service

    _write_summary(
        report_workspace,
        "case-b",
        title="押丝珑琅AI生成",
        module="3D共享",
        cases=[
            {
                "case_id": "TC-001",
                "title": "AI生成入口展示",
                "priority": "P1",
                "smoke": True,
                "feature": "3D生成",
                "scenario": "入口与权限",
                "expected_result": "页面展示 AI 生成入口。",
                "steps": ["进入 3D 共享页"],
                "assertions": ["AI 生成入口可见"],
            }
        ],
        manual_cases=[],
    )

    result = test_report_service.load_reportable_cases("", case_set_ids=["case-a", "case-b"])

    assert result["case_set_ids"] == ["case-a", "case-b"]
    assert result["source_count"] == 2
    assert [source["title"] for source in result["sources"]] == ["共享打印V1.2.2", "押丝珑琅AI生成"]
    assert result["counts"]["automation_case_count"] == 5
    by_selection_id = {case["selection_id"]: case for case in result["cases"]}
    assert by_selection_id["case-a::TC-001"]["case_id"] == "TC-001"
    assert by_selection_id["case-b::TC-001"]["source_title"] == "押丝珑琅AI生成"
    assert by_selection_id["case-b::TC-001"]["default_selected"] is True


def test_preview_uses_full_automation_statistics_and_passed_quality(report_workspace):
    from task_server.services import test_report_service

    result = test_report_service.preview_test_report({
        "case_set_id": "case-a",
        "selected_case_ids": ["TC-001"],
        "meta": {
            "report_title": "共享打印V1.2.2-测试报告",
            "tester": "王文闯",
            "client_side": "mini",
            "test_start": "2026-02-05",
            "test_end": "2026-02-06",
        },
    })

    assert result["statistics"]["total"] == 4
    assert result["statistics"]["passed"] == 4
    assert result["statistics"]["not_executed"] == 0
    assert result["statistics"]["pass_rate"] == 100
    assert result["quality"]["result"] == "通过"
    assert "核心测试范围" in result["quality"]["text"]
    assert "发布准入" in result["quality"]["text"]
    assert result["release"]["suggestion"] == "建议发布"
    assert "按既定发布流程推进上线" in result["release"]["text"]
    assert "测试范围" not in result["scope_markdown"]
    assert "模块与权限" in result["scope_markdown"]
    assert result["scope_markdown"].count("\n") <= 10


def test_preview_uses_manual_defect_severity_statistics(report_workspace):
    from task_server.services import test_report_service

    result = test_report_service.preview_test_report({
        "case_set_id": "case-a",
        "selected_case_ids": ["TC-001"],
        "defects": {
            "fatal": 1,
            "serious": 2,
            "normal": 3,
            "minor": 4,
        },
        "meta": {"report_title": "共享打印V1.2.2-测试报告"},
    })

    assert result["statistics"]["total"] == 4
    assert result["statistics"]["passed"] == 4
    assert result["statistics"]["defect_total"] == 10
    assert result["defects"] == {
        "fatal": 1,
        "serious": 2,
        "normal": 3,
        "minor": 4,
        "total": 10,
    }
    assert "| 致命 | 严重 | 一般 | 轻微 | 总计 |" in result["defect_table"]
    assert "| 1 | 2 | 3 | 4 | 10 |" in result["defect_table"]
    assert "测试结果： ✅ 通过" in result["markdown"]
    assert "✅ 建议发布：本轮测试结论为通过" in result["markdown"]
    assert "按既定发布流程推进上线" in result["markdown"]


def test_preview_merges_multiple_mindmaps_without_case_id_collision(report_workspace):
    from task_server.services import test_report_service

    _write_summary(
        report_workspace,
        "case-b",
        title="押丝珑琅AI生成",
        module="3D共享",
        cases=[
            {
                "case_id": "TC-001",
                "title": "AI生成入口展示",
                "priority": "P1",
                "smoke": True,
                "feature": "3D生成",
                "scenario": "入口与权限",
                "expected_result": "页面展示 AI 生成入口。",
                "steps": ["进入 3D 共享页"],
                "assertions": ["AI 生成入口可见"],
            },
            {
                "case_id": "TC-002",
                "title": "AI生成任务提交",
                "priority": "P2",
                "smoke": False,
                "feature": "3D生成",
                "scenario": "任务提交",
                "expected_result": "生成任务可正常提交。",
                "steps": ["填写提示词", "点击生成"],
                "assertions": ["任务进入生成中"],
            },
        ],
        manual_cases=[],
    )

    result = test_report_service.preview_test_report({
        "case_set_ids": ["case-a", "case-b"],
        "selected_case_ids": ["case-a::TC-001", "case-b::TC-001"],
        "meta": {
            "report_title": "多需求合并测试报告",
            "tester": "王文闯",
        },
    })

    assert result["case_set_ids"] == ["case-a", "case-b"]
    assert result["source_count"] == 2
    assert result["statistics"]["total"] == 6
    assert result["statistics"]["passed"] == 6
    assert result["statistics"]["not_executed"] == 0
    assert [case["selection_id"] for case in result["cases"]] == ["case-a::TC-001", "case-b::TC-001"]
    assert result["cases"][0]["source_title"] == "共享打印V1.2.2"
    assert result["cases"][1]["source_title"] == "押丝珑琅AI生成"
    assert "共享打印V1.2.2" in result["scope_markdown"]
    assert "押丝珑琅AI生成" in result["scope_markdown"]
    assert "共享打印V1.2.2 / TC-001" in result["case_table"]
    assert "押丝珑琅AI生成 / TC-001" in result["case_table"]
    assert result["scope_markdown"].count("\n") <= 8


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
    assert "## 1. 报告结论" not in markdown
    assert "## 1. 基本信息" in markdown
    assert "## 2. 测试概要" in markdown
    assert "测试范围" in markdown
    assert "版本质量满足发布要求" in markdown
    assert "测试结果： ✅ 通过" in markdown
    assert "✅ 建议发布：" in markdown
    assert "| 4 | 4 | 0 | 0 | 0 | 100% | 0 |" in markdown
    assert "共享打印V1.2.2-测试报告" in html
    assert "report-summary" not in html
    assert test_report_service.read_test_report(result["report_id"])["report_id"] == result["report_id"]
    indexed = test_report_service.list_test_reports(case_set_id="case-a")
    assert [item["report_id"] for item in indexed] == [result["report_id"]]


def test_default_report_body_uses_test_points_without_case_details(report_workspace):
    from task_server.services import test_report_service

    result = test_report_service.preview_test_report({
        "case_set_id": "case-a",
        "selected_case_ids": ["TC-001", "TC-002", "TC-003"],
        "meta": {"report_title": "共享打印V1.2.2-测试报告"},
    })

    markdown = result["markdown"]
    assert "## 3. 主要测试点" in markdown
    assert "1. " in result["test_points_markdown"]
    assert "2. " in result["test_points_markdown"]
    assert "选中用例明细" not in markdown
    assert "失败用例明细" not in markdown
    assert "| 用例编号 |" not in markdown
    assert "TC-001" not in markdown
    assert "经销商与管理员菜单权限隔离" not in markdown


def test_create_report_persists_word_export(report_workspace):
    from task_server.services import test_report_service

    result = test_report_service.create_test_report({
        "case_set_id": "case-a",
        "selected_case_ids": ["TC-001", "TC-002"],
        "meta": {"report_title": "共享打印V1.2.2-测试报告"},
    })

    assert Path(result["files"]["word"]).exists()
    word = Path(result["files"]["word"]).read_text(encoding="utf-8")
    assert "共享打印V1.2.2-测试报告" in word
    assert "report-cover" in word
    assert result["download"]["word"].endswith("&format=doc")


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
    assert "## 1. 报告结论" not in result["markdown"]
    assert "## 2. 测试概要" in result["markdown"]
    assert "## 3. 主要测试点" in result["markdown"]
    assert "## 6. 发布建议" in result["markdown"]
    assert "✅ 建议发布：" in result["markdown"]


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
    assert by_id["TC-002"]["status"] == "passed"
    assert result["statistics"]["total"] == 4
    assert result["statistics"]["passed"] == 4
    assert result["statistics"]["not_executed"] == 0
