"""Test design may be manual-only; Runner conversion must remain strict."""
import copy

import pytest

from task_server.services import yaml_service


def manual_payload():
    return {
        "title": "首页布局人工设计", "cases": [],
        "scenarios": [{"feature": "首页", "scenario": "核对首页布局", "steps": ["打开首页", "对照页面"]}],
        "manual_cases": [{"case_id": "MANUAL-1", "title": "核对首页布局设计稿", "steps": ["对照设计稿"],
                          "executionLevel": "manual", "reason": "需人工提供当前设计稿"}],
        "analysis": {"readiness_level": "review", "requirement_points": ["核对首页布局"]}, "review": {},
    }


@pytest.mark.parametrize("automatic_candidate", [False, True])
def test_mindmap_saves_manual_design_and_reasons_without_generating_yaml(monkeypatch, automatic_candidate):
    payload = manual_payload()
    if automatic_candidate:
        payload["cases"] = payload.pop("manual_cases")
        payload["cases"][0].pop("reason")
        payload["cases"][0]["executionLevel"] = "automatic"
        payload["cases"][0]["steps"] = ["打开首页", "对照Figma设计稿检查首页布局"]
    writes = []
    monkeypatch.setattr(yaml_service, "save_asset_files", lambda *a: {})
    monkeypatch.setattr(yaml_service, "update_asset_request_context", lambda *a: {})
    monkeypatch.setattr(yaml_service, "load_asset_contents", lambda *a: (["仅检查首页"], []))
    monkeypatch.setattr(yaml_service, "load_figma_generation_context", lambda *a, **k: ([] if automatic_candidate else ["设计稿对照资料"], [], [], [], []))
    monkeypatch.setattr(yaml_service, "call_dashscope_refine_cases", lambda *a, **k: pytest.fail("manual-only visual refinement must be explicitly skipped"))
    monkeypatch.setattr(yaml_service, "_mindmap_generate_structure_payload", lambda *a, **k: copy.deepcopy(payload))
    monkeypatch.setattr(yaml_service, "write_json_file", lambda path, data: writes.append(copy.deepcopy(data)))
    monkeypatch.setattr(yaml_service, "build_generation_summary", lambda *a, **k: {})
    monkeypatch.setattr(yaml_service, "filtered_case_ui_design_assets_for_summary", lambda *a: {})
    monkeypatch.setattr(yaml_service, "write_generation_summary", lambda *a: [])
    result = yaml_service.generate_mindmap_from_request({
        "title": "仅设计", "module": "audit", "case_set_id": "audit-manual-only",
        "files": [{"name": "input.txt"}], "app_package": "com.kfb.model",
    })
    assert result["ok"]
    assert result["caseCount"] == 0
    assert result["manualCaseCount"] == 1
    assert "设计稿" in result["cases"]["manual_cases"][0]["reason"]
    assert result["scenarioCount"] == 1
    assert result["coverageAudit"]["ok"] is True
    assert not result["coverageAudit"]["missing_case_points"]
    if not automatic_candidate:
        assert "全人工" in result["cases"]["review"]["visual_refine_skipped"]
    assert result["file"] == ""
    assert writes[-1]["manual_cases"] == result["cases"]["manual_cases"]
    with pytest.raises(ValueError, match="没有可转换"):
        yaml_service.cases_to_separate_midscene_yamls(result["cases"])


def test_normal_yaml_split_still_rejects_manual_only():
    with pytest.raises(ValueError, match="没有可转换|非空 cases"):
        yaml_service.split_automation_ready_cases(manual_payload())
    with pytest.raises(ValueError, match="非空 cases"):
        yaml_service.audit_case_coverage(manual_payload())


def test_mindmap_skill_pipeline_keeps_ai_manual_reasons_without_local_automatic_fallback(monkeypatch):
    from task_server.services import ai_skill_service as skills
    payload = manual_payload()
    monkeypatch.setattr(skills, "call_skill_requirement_analyzer", lambda *a, **k: payload["analysis"])
    monkeypatch.setattr(skills, "call_skill_scenario_designer", lambda *a, **k: payload["scenarios"])
    monkeypatch.setattr(skills, "run_ai_skill", lambda *a, **k: {
        "cases": [], "manual_cases": copy.deepcopy(payload["manual_cases"]), "review": {},
    })
    monkeypatch.setattr(skills, "_fallback_automation_filter_from_scenarios", lambda *a, **k: pytest.fail("valid manual design must not become automatic fallback"))
    monkeypatch.setattr(skills, "call_skill_smoke_selector", lambda *a, **k: pytest.fail("manual-only design has no smoke candidates"))
    result = skills.build_cases_payload_from_skills(
        "首页设计", "audit", ["需要设计稿对照"], mode="mindmap", allow_entry_visibility_fast_path=False,
    )
    assert result["cases"] == []
    assert result["manual_cases"][0]["reason"] == "需人工提供当前设计稿"
    assert result["review"]["smoke_case_ids"] == []
    assert result["review"]["smoke_eligible_case_count"] == 0
    with pytest.raises(ValueError, match="非空 cases"):
        skills.select_smoke_cases_for_payload("首页设计", "audit", result, mode="full")
