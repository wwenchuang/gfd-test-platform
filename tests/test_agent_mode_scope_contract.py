"""Agent mode and module scope are hard execution boundaries."""

from task_server.services import agent_service


def test_analysis_only_mode_allows_read_analysis_steps_only():
    allowed = {"PREPARE_SOURCE", "PLAN", "IMPACT_ANALYSIS", "CASE_RETRIEVAL", "MATCH_CASES"}
    for step in agent_service._STEP_ORDER:
        assert agent_service._agent_step_allowed_for_mode("ANALYZE_ONLY", step) is (step in allowed)
    assert all(agent_service._agent_step_allowed_for_mode("AUTO_SAFE", step) for step in agent_service._STEP_ORDER)


def test_module_scope_matches_the_exact_configured_directory(monkeypatch, tmp_path):
    current = tmp_path / "家用基线"
    legacy = tmp_path / "家用基线旧"
    current.mkdir()
    legacy.mkdir()
    (current / "current.yaml").write_text("tasks: []", encoding="utf-8")
    (legacy / "legacy.yaml").write_text("tasks: []", encoding="utf-8")
    monkeypatch.setattr(agent_service, "_get_search_dirs_for_app", lambda *_: [str(current), str(legacy)])

    cases = agent_service._collect_candidate_yamls({
        "target": "家用基线回归", "appName": "智小白3D", "module": "家用基线",
    })

    assert [item["file_name"] for item in cases] == ["current.yaml"]
