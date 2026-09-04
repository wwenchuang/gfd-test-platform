import json
from pathlib import Path

import pytest


def test_mindmap_generation_expands_risk_and_boundary_coverage_within_plan_limit():
    from task_server.services.case_service import generation_volume_targets

    analysis = {
        "requirement_points": [
            "自动合盘入口",
            "按颜色分盘显隐",
            "打印记录聚合",
            "设备详情聚合",
            "模型打印状态同步",
        ],
        "risks": [
            "多色与单色识别错误",
            "合盘任务中断",
            "打印记录与设备详情状态不一致",
            "状态同步延迟",
        ],
    }

    targets = generation_volume_targets(analysis, mode="mindmap")

    assert targets["requirement_unit_count"] == 5
    assert targets["risk_extension_count"] == 3
    assert targets["target_plan_cases"] == 8
    assert targets["max_plan_cases"] >= 8
    assert targets["target_automation_cases"] == 5


def test_full_generation_keeps_document_units_without_adding_risk_only_cases():
    from task_server.services.case_service import generation_volume_targets

    targets = generation_volume_targets({
        "requirement_points": ["入口", "列表", "详情", "状态", "通知"],
        "risks": ["网络", "权限", "数据"],
    }, mode="full")

    assert targets["risk_extension_count"] == 0
    assert targets["target_plan_cases"] == 5


def test_mindmap_scenario_designer_fills_document_risks_to_plan_target(monkeypatch):
    from task_server.services import ai_skill_service

    analysis = {
        "requirement_points": ["入口", "分盘", "打印记录", "设备详情", "状态同步"],
        "risks": ["单色模型误显示按颜色分盘", "合盘任务中断", "记录与设备状态不一致", "打印标签未同步"],
    }
    targets = ai_skill_service.generation_volume_targets(analysis, mode="mindmap")
    model_scenarios = [
        {
            "feature": point,
            "scenario": f"{point}正常流程",
            "requirement_point": point,
            "business_path": f"进入{point} -> 完成操作 -> 查看结果",
        }
        for point in analysis["requirement_points"]
    ]
    monkeypatch.setattr(
        ai_skill_service,
        "run_ai_skill",
        lambda *args, **kwargs: {"scenarios": model_scenarios},
    )

    runtime_trace = {}
    scenarios = ai_skill_service.call_skill_scenario_designer(
        "合盘打印流程方案",
        "合盘打印",
        analysis,
        mode="mindmap",
        targets=targets,
        runtime_trace=runtime_trace,
    )

    assert len(scenarios) == 8
    extensions = [row for row in scenarios if row.get("source") == "platform_risk_extension"]
    assert len(extensions) == 3
    assert {row["risk"] for row in extensions}.issubset(set(analysis["risks"]))
    assert all(row["type"] == "异常/边界/状态" for row in extensions)
    assert runtime_trace["mindmap_risk_extension"]["added_count"] == 3
    assert runtime_trace["mindmap_risk_extension"]["shortfall_count"] == 0


def test_compact_mindmap_uses_same_risk_coverage_target():
    from task_server.services.case_service import generation_volume_targets

    targets = generation_volume_targets({
        "requirement_points": ["入口", "列表", "详情", "状态"],
        "risks": ["空态", "状态延迟"],
    }, mode="compact_mindmap")

    assert targets["risk_extension_count"] == 2
    assert targets["target_plan_cases"] == 6


def test_mindmap_automation_filter_preserves_scenarios_the_model_did_not_classify(monkeypatch):
    from task_server.services import ai_skill_service

    scenarios = [
        {
            "feature": "合盘打印",
            "scenario": f"场景-{index}",
            "requirement_point": f"需求-{index}",
            "business_path": f"进入页面 -> 执行场景-{index} -> 查看结果",
            "expected": f"场景-{index}结果正确",
            "risk": f"风险-{index}" if index > 5 else "",
            "source": "platform_risk_extension" if index > 5 else "ai",
        }
        for index in range(1, 9)
    ]
    monkeypatch.setattr(
        ai_skill_service,
        "run_ai_skill",
        lambda *args, **kwargs: {
            "cases": [
                {"case_id": f"TC-{index:03d}", "title": f"场景-{index}", "scenario": f"场景-{index}", "coverage": f"需求-{index}"}
                for index in range(1, 6)
            ],
            "manual_cases": [],
            "review": {},
        },
    )

    result = ai_skill_service.call_skill_automation_filter(
        "合盘打印流程方案",
        "合盘打印",
        {"requirement_points": [f"需求-{index}" for index in range(1, 9)]},
        scenarios,
        mode="mindmap",
        targets={"target_plan_cases": 8, "target_automation_cases": 5, "max_cases": 5},
    )

    assert len(result["cases"]) == 5
    assert len(result["manual_cases"]) == 3
    assert {row["risk"] for row in result["manual_cases"]} == {"风险-6", "风险-7", "风险-8"}
    assert all(row["source"] == "platform_preserved_unclassified_scenario" for row in result["manual_cases"])
    assert result["review"]["scenario_classification_audit"]["preserved_count"] == 3
    ai_skill_service.validate_ai_skill_output("cases_payload", {
        "title": "合盘打印流程方案",
        "module": "合盘打印",
        "analysis": {"requirement_points": [f"需求-{index}" for index in range(1, 9)]},
        "scenarios": scenarios,
        **result,
    })


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


def test_preview_uses_full_automation_statistics_and_unexecuted_quality(report_workspace):
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
    assert result["statistics"]["passed"] == 0
    assert result["statistics"]["not_executed"] == 4
    assert result["statistics"]["pass_rate"] == 0
    assert result["quality"]["result"] == "缺少执行证据"
    assert "核心测试范围" in result["quality"]["text"]
    assert "不能形成发布结论" in result["quality"]["text"]
    assert result["release"]["suggestion"] == "暂不建议发布"
    assert "完成执行和人工确认" in result["release"]["text"]
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
    assert result["statistics"]["passed"] == 0
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
    assert "测试结果： ⚠️ 缺少执行证据" in result["markdown"]
    assert "⚠️ 暂不建议发布：" in result["markdown"]
    assert "完成执行和人工确认" in result["markdown"]


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
    assert result["statistics"]["passed"] == 0
    assert result["statistics"]["not_executed"] == 6
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
    assert "完成执行和人工确认" in markdown
    assert "测试结果： ⚠️ 缺少执行证据" in markdown
    assert "⚠️ 暂不建议发布：" in markdown
    assert "| 4 | 0 | 0 | 0 | 4 | 0 | 0% | 0 |" in markdown
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
    assert "⚠️ 暂不建议发布：" in result["markdown"]


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
    assert by_id["TC-002"]["status"] == "not_executed"
    assert result["statistics"]["total"] == 4
    assert result["statistics"]["passed"] == 1
    assert result["statistics"]["not_executed"] == 3


def test_mindmap_only_cases_explain_missing_script_instead_of_claiming_not_run(report_workspace):
    from task_server.services import test_report_service

    summary_path = report_workspace / "cases" / "case-a" / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary.pop("generatedCaseGroups", None)
    summary["yaml_file"] = ""
    summary["yaml_files"] = []
    summary["yaml_file_count"] = 0
    summary["yaml_check"] = {"ok": True, "mode": "mindmap_only", "message": "只生成脑图任务未生成 YAML"}
    summary.setdefault("review", {})["case_dedup"] = {
        "input_case_count": 4,
        "output_case_count": 4,
        "duplicate_case_count": 0,
        "trimmed_case_count": 0,
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")

    cases = test_report_service.load_reportable_cases("case-a")
    assert cases["execution_readiness"] == {
        "automation_total": 4,
        "evidence_matched": 0,
        "missing_script": 4,
        "missing_record": 0,
        "can_generate_execution_report": False,
        "message": "4 条尚未生成可执行 YAML，因此无法自动形成执行结论。",
    }
    assert cases["generation_audit"]["design_total"] == 5
    assert cases["generation_audit"]["deduplicated_count"] == 0
    assert cases["generation_audit"]["message"] == "本批共生成 5 条测试设计：4 条自动化、1 条人工；去重 0 条，没有因数量上限删除用例。"
    automation = [case for case in cases["cases"] if case["source_type"] == "automation"]
    assert {case["execution_evidence_state"] for case in automation} == {"missing_script"}
    assert {case["execution_evidence_label"] for case in automation} == {"未生成可执行 YAML"}

    preview = test_report_service.preview_test_report({
        "case_set_id": "case-a",
        "selected_case_ids": ["TC-001"],
        "meta": {"report_title": "共享打印V1.2.2-测试报告"},
    })
    assert preview["quality"]["result"] == "缺少执行证据"
    assert preview["statistics"]["missing_script"] == 4
    assert "未生成可执行 YAML" in preview["quality"]["text"]


def test_explicit_recorded_results_complete_execution_report_without_runner_yaml(report_workspace):
    from task_server.services import test_report_service

    summary_path = report_workspace / "cases" / "case-a" / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary.pop("generatedCaseGroups", None)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")

    result = test_report_service.preview_test_report({
        "case_set_id": "case-a",
        "selected_case_ids": ["TC-001"],
        "execution_results": {
            case_id: {
                "status": "passed",
                "source": "manual_record",
                "failure_reason": "2026-09-04 Safari 真机执行，记录见 AgileTC 3088",
            }
            for case_id in ("TC-001", "TC-002", "TC-003", "TC-004")
        },
        "execution_note": "2026-09-04 Safari 真机执行，记录见 AgileTC 3088",
        "meta": {"report_title": "共享打印V1.2.2-测试报告"},
    })

    assert result["statistics"]["passed"] == 4
    assert result["statistics"]["not_executed"] == 0
    assert result["statistics"]["manually_recorded"] == 4
    assert result["execution_readiness"]["can_generate_execution_report"] is True
    assert result["quality"]["result"] == "通过"
    assert result["report_cases"][0]["execution_evidence_state"] == "recorded"
    assert result["report_cases"][0]["execution_evidence_label"] == "人工记录 · 通过"


def test_formal_report_accepts_platform_manual_pass_without_extra_note(report_workspace):
    from task_server.services import test_report_service

    summary_path = report_workspace / "cases" / "case-a" / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary.pop("generatedCaseGroups", None)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")

    result = test_report_service.create_test_report({
        "case_set_id": "case-a",
        "selected_case_ids": ["TC-001"],
        "report_mode": "execution",
        "execution_results": {
            case_id: {"status": "passed", "source": "manual_record"}
            for case_id in ("TC-001", "TC-002", "TC-003", "TC-004")
        },
        "meta": {"report_title": "平台人工标记通过-测试报告"},
    })

    assert result["statistics"]["passed"] == 4
    assert result["quality"]["result"] == "通过"
    assert "平台人工标记" in result["execution_note"]


def test_formal_execution_report_rejects_unclosed_execution_evidence(report_workspace):
    from task_server.services import test_report_service

    with pytest.raises(test_report_service.TestReportError, match="不能生成正式执行报告"):
        test_report_service.create_test_report({
            "case_set_id": "case-a",
            "selected_case_ids": ["TC-001"],
            "report_mode": "execution",
            "meta": {"report_title": "共享打印V1.2.2-测试报告"},
        })


def test_preview_counts_selected_manual_cases_as_pending_and_blocks_release(report_workspace):
    from task_server.services import test_report_service

    result = test_report_service.preview_test_report({
        "case_set_id": "case-a",
        "selected_case_ids": ["TC-001", "MT-001"],
        "execution_results": {
            "TC-001": {"status": "passed"},
            "TC-002": {"status": "passed"},
            "TC-003": {"status": "passed"},
            "TC-004": {"status": "passed"},
        },
        "meta": {"report_title": "共享打印V1.2.2-测试报告"},
    })

    assert result["statistics"]["manual_total"] == 1
    assert result["statistics"]["manual_pending"] == 1
    assert result["quality"]["result"] == "待人工确认"
    assert result["release"]["suggestion"] == "暂不建议发布"
    assert "| 待人工确认 |" in result["summary_table"]
    assert "| 4 | 4 | 0 | 0 | 0 | 1 | 100% | 0 |" in result["summary_table"]
    assert "待人工确认" in result["manual_case_table"]


def test_recorded_manual_failure_is_a_failure_not_pending_confirmation(report_workspace):
    from task_server.services import test_report_service

    result = test_report_service.preview_test_report({
        "case_set_id": "case-a",
        "selected_case_ids": ["TC-001", "MT-001"],
        "execution_results": {
            **{case_id: {"status": "passed"} for case_id in ("TC-001", "TC-002", "TC-003", "TC-004")},
            "MT-001": {"status": "failed", "source": "manual_record", "failure_reason": "账单金额不一致"},
        },
        "execution_note": "2026-09-04 财务后台人工复核",
        "meta": {"report_title": "共享打印V1.2.2-测试报告"},
    })

    assert result["statistics"]["manual_pending"] == 0
    assert result["statistics"]["manual_failed"] == 1
    assert result["quality"]["result"] == "未通过"
    assert result["release"]["suggestion"] == "暂不建议发布"
    assert "| 失败 |" in result["manual_case_table"]


def test_preview_does_not_recommend_release_when_recorded_defects_remain(report_workspace):
    from task_server.services import test_report_service

    result = test_report_service.preview_test_report({
        "case_set_id": "case-a",
        "selected_case_ids": ["TC-001"],
        "execution_results": {
            case_id: {"status": "passed"}
            for case_id in ("TC-001", "TC-002", "TC-003", "TC-004")
        },
        "defects": {"normal": 1},
        "meta": {"report_title": "共享打印V1.2.2-测试报告"},
    })

    assert result["quality"]["result"] == "存在缺陷"
    assert result["release"]["suggestion"] == "暂不建议发布"
