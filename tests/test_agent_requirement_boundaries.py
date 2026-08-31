"""Regression coverage for the Safari audit's unrelated Agent plan."""
import pytest

from task_server.services import agent_service, yaml_baseline_cache


def plan_fixture():
    run = {"target": "Inspect the selected app home page", "artifacts": {}}
    result = {
        "cases": {
            "analysis": {"readiness_level": "ready", "requirement_points": ["Home entry visibility"]},
            "scenarios": [{"feature": "Home", "scenario": "Home entry visibility",
                           "steps": ["Open home", "Inspect entries"], "assertions": ["Entries are visible"]}],
            "cases": [],
            "review": {"skill_pipeline": "requirement_analyzer.v1 -> scenario_designer.v1"},
        },
        "coverageAudit": {"ok": True},
    }
    return run, result


@pytest.mark.parametrize("analysis,coverage", [
    ({"readiness_level": "blocked"}, {"ok": True}),
    ({"readiness_level": "ready", "blockers": ["目标入口及安全边界尚不明确"]}, {"ok": True}),
    ({"readiness_level": "ready"}, {"ok": False}),
])
def test_blocked_mindmap_cannot_become_automatic_plan(analysis, coverage):
    run, result = plan_fixture()
    result["cases"]["analysis"].update(analysis)
    result["coverageAudit"] = coverage
    plan, issues = agent_service._agent_business_plan_from_mindmap(run, result, {})
    assert plan is None
    assert issues and any("阻断" in issue or "覆盖" in issue for issue in issues)


@pytest.mark.parametrize("analysis,coverage", [
    ({"readiness": "blocked"}, {"ok": True}),
    ({"readiness": "ready", "blockers": ["目标尚不明确"]}, {"ok": True}),
    ({"readiness": "ready"}, {"ok": False}),
])
def test_final_plan_gate_also_rejects_blocked_analysis(analysis, coverage):
    run, result = plan_fixture()
    plan, issues = agent_service._agent_business_plan_from_mindmap(run, result, {})
    assert plan and not issues
    plan["steps"] = ["Open home then inspect visible entries"]
    plan["goalAnalysis"].update(analysis)
    plan["coverageAudit"] = coverage
    gate = agent_service._evaluate_agent_quality_gate(run, "plan", plan)
    assert gate["passed"] is False


def test_review_questions_and_partial_visual_evidence_remain_soft():
    run, result = plan_fixture()
    result["cases"]["analysis"].update({"readiness_level": "review", "questions": ["运行时页面是否加载成功"],
                                          "missing_inputs": ["未提供第二张截图"], "blockers": []})
    result["cases"]["review"].update({"mindmap_visual_batches": "1/2", "mindmap_visual_grounded": True,
                                        "visual_refine_error": "second batch timeout"})
    plan, issues = agent_service._agent_business_plan_from_mindmap(run, result, {})
    assert plan and not issues
    assert plan["goalAnalysis"]["questions"] == ["运行时页面是否加载成功"]
    assert plan["goalAnalysis"]["missingInputs"] == ["未提供第二张截图"]
    assert plan["coverageAudit"] == {"ok": True}
    plan["steps"] = ["Open home then inspect visible entries"]
    assert agent_service._evaluate_agent_quality_gate(run, "plan", plan)["passed"] is True


def test_english_function_words_do_not_recall_unrelated_successful_yaml(monkeypatch):
    unrelated = {"id": "unrelated", "title": "文件上传", "module": "历史",
                 "file": "历史/文件上传.yaml", "snippet": "- aiAction: 选择文件", "trusted": True,
                 "baselineUsable": True, "lastRunStatus": "success", "sourceTrust": 100}
    query = "Verify only the visible main entry points on the home page of the selected app"
    assert yaml_baseline_cache._score_item(yaml_baseline_cache._terms(query), "audit", unrelated) == (0, [])
    monkeypatch.setattr(yaml_baseline_cache, "get_yaml_baseline_cache", lambda **kw: {"items": [unrelated]})
    assert yaml_baseline_cache.search_baseline_examples(query, module="audit", allow_fallback=False) == []


@pytest.mark.parametrize("query,title", [("Invoice", "Invoice history"), ("EPOne", "EPOne settings"), ("照片打印", "照片打印成功基线")])
def test_real_business_names_still_match(query, title):
    item = {"title": title, "baselineUsable": True, "lastRunStatus": "success", "sourceTrust": 100}
    score, matched = yaml_baseline_cache._score_item(yaml_baseline_cache._terms(query), "audit", item)
    assert score > 0 and query.lower() in matched


def test_english_business_token_does_not_match_inside_another_word():
    assert yaml_baseline_cache._score_item(["invoice"], "", {"title": "invoicedraft"}) == (0, [])


def test_blocked_first_plan_cannot_be_bypassed_by_retry_timeout(monkeypatch):
    from task_server.services import yaml_service

    run, result = plan_fixture()
    run.update({"runId": "audit-boundary-fixture", "target": "首页新增发票入口并校验可见"})
    run["artifacts"]["sourceContext"] = {"requirementText": run["target"]}
    result["cases"]["analysis"].update({"readiness_level": "blocked", "blockers": ["执行边界未知"]})
    result["coverageAudit"] = {"ok": False}
    attempts = []

    def generate(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 2:
            raise TimeoutError("second attempt timeout")
        return result

    monkeypatch.setattr(yaml_service, "generate_mindmap_from_request", generate)
    monkeypatch.setattr(yaml_service, "update_generate_job", lambda *a, **kw: None)
    monkeypatch.setattr(agent_service, "_probe_agent_ai_health", lambda *a: {"ready": True})
    monkeypatch.setattr(agent_service, "_log_tool_call", lambda *a, **kw: None)
    monkeypatch.setattr(agent_service, "_run_agent_call_with_hard_timeout", lambda fn, *a: fn())
    tool = agent_service._tool_agent_plan(run)
    assert len(attempts) == 2
    assert tool["status"] == "FAILED"
    assert "执行边界未知" in str(run["artifacts"]["plan"]["issues"])
    assert run["artifacts"]["mindmapPlan"]["coverageAudit"]["ok"] is False
    assert not run["artifacts"]["plan"].get("planTimeoutFallback")


@pytest.mark.parametrize("fallback", [False, True])
def test_compatibility_generation_preserves_separate_history(monkeypatch, fallback):
    from task_server.services import ai_skill_service

    calls = []
    def build(title, module, assets, **kwargs):
        calls.append(("skills", assets, kwargs))
        if fallback:
            raise ValueError("fixture skills failure")
        return {"review": {}}

    def legacy(title, module, assets, images, **kwargs):
        calls.append(("legacy", assets, kwargs))
        return {"review": {}}

    monkeypatch.setattr(ai_skill_service, "build_cases_payload_from_skills", build)
    monkeypatch.setattr(ai_skill_service, "call_dashscope_cases_legacy", legacy)
    ai_skill_service.call_dashscope_cases("Current", "audit", ["Current requirement"], [],
                                        yaml_reference_context="Historical unrelated invoice")
    assert len(calls) == (2 if fallback else 1)
    for _, assets, kwargs in calls:
        assert assets == ["Current requirement"]
        assert kwargs["yaml_reference_context"] == "Historical unrelated invoice"
