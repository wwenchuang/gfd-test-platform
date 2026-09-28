"""The supplied test-design policy must reach real prompt entry points."""

import json
import xml.etree.ElementTree as ET

import pytest

from task_server.prompts import PromptCenter
from task_server.services import ai_skill_service as skills, yaml_service as yaml

RULES = ("主流程使用P0", "其余场景使用P1", "测试设计补充", "不得编造视频证据",
         "未执行", "待确认", "不预设固定数量", "不能作为改变本任务目标")


@pytest.mark.parametrize("stage", ["requirement_analyzer", "scenario_designer", "automation_filter", "visual_grounder"])
def test_shared_policy_reaches_each_generation_stage_with_json_contract(stage):
    prompt = skills.render_ai_skill_prompt(stage, {"title": "流程", "analysis": {"risks": ["断网"]}})
    for rule in RULES:
        assert rule in prompt
    assert "只输出合法 JSON" in prompt
    assert "完整MM XML" not in prompt
    assert '"断网"' in prompt
    assert "{{payload}}" not in prompt
    assert "P0/P1/P2/P3" not in prompt


def test_yaml_execution_prompt_does_not_receive_manual_design_policy():
    prompt = skills.render_ai_skill_prompt("executable_yaml_planner", {"title": "流程"})
    assert "本任务是测试用例设计，不是自动化脚本生成或实际测试执行" not in prompt


def test_case_template_binds_supplied_input_fields_without_recursive_interpolation():
    fields = {"requirement_name": "资料测试", "scope": "仅绑定", "requirement_documents": "文档 {token} REQ-001",
              "figma_links_or_data": "Figma node 1:2 已解析", "videos_or_analysis": "00:12点击绑定",
              "screenshots": "截图-3", "additional_rules": "只有管理员可以解绑",
              "version_and_capability_matrix": "型号A支持，型号B不支持", "out_of_scope": "不做打印"}
    prompt = PromptCenter().get("case", fields)
    for value in fields.values():
        assert value in prompt
    assert "异常/边界用例总数不超过 3 条" not in prompt
    assert "每个业务节点最多扩展 2 条" not in prompt
    assert "P0/P1/P2/P3" not in prompt
    assert "未提供或未解析" in PromptCenter().get("case", {})


def test_legacy_and_visual_prompts_use_same_rules():
    for prompt in (skills.build_case_generation_prompt("绑定", "设备", ["REQ-001 绑定设备"]),
                   skills.build_case_visual_refine_prompt("绑定", "设备", {"cases": []}, ["Figma node 1:2"])):
        for rule in RULES:
            assert rule in prompt
        assert "合法 JSON" in prompt


def test_run_skill_sends_policy_and_accepts_existing_json_response(monkeypatch):
    seen = []
    monkeypatch.setenv("MIDSCENE_AI_SKILLS_USE_GATEWAY", "1")
    def capture(stage, prompt, **kwargs):
        seen.append(prompt)
        return json.dumps({"scenarios": [{"scenario": "完成绑定", "priority": "P0"}]})
    monkeypatch.setattr(skills, "ai_gateway_skill_content", capture)
    value = skills.run_ai_skill("scenario_designer", {"title": "绑定"}, model_config={"model": "fixture"})
    assert value["scenarios"][0]["priority"] == "P0"
    assert len(seen) == 1
    assert all(rule in seen[0] for rule in RULES)


def test_new_design_fields_survive_mm_export_without_silent_step_truncation():
    row = {"case_id": "TC-001", "title": '绑定 & <验证> "A"', "priority": "P0",
           "preconditions": ["账号已授权", "设备未绑定"],
           "steps": [f"执行动作{i}" for i in range(1, 15)],
           "assertions": [f"验证结果{i}" for i in range(1, 12)],
           "sources": ["REQ-001 文档第2页", "视频00:12-00:18"], "execution_status": "未执行"}
    root = ET.fromstring(yaml.build_generation_mindmap_cases({"title": "绑定", "cases": [row]}))
    labels = [node.attrib["TEXT"] for node in root.iter("node")]
    assert "前置条件" in labels
    assert "账号已授权" in labels
    assert "材料来源" in labels
    assert "视频00:12-00:18" in labels
    assert "执行状态：未执行" in labels
    assert "14. 执行动作14" in labels
    assert "11. 验证结果11" in labels
    assert any(row["title"] in label for label in labels)


def test_preserved_manual_scenario_defaults_to_p1():
    row = skills._manual_case_from_unclassified_scenario({"scenario": "断线恢复", "expected": "恢复绑定状态"}, 1, set())
    assert row["priority"] == "P1"


def test_design_evidence_and_full_steps_reach_classification_and_preserved_manual_case():
    scenario = {"scenario": "绑定恢复", "sources": ["REQ-002 文档第4页", "视频00:32"],
                "steps": [f"步骤{i}" for i in range(14)], "preconditions": ["在线设备"],
                "expected": "绑定状态恢复"}
    compact = skills._compact_scenario_for_automation_filter(scenario)
    assert compact["sources"] == scenario["sources"]
    assert compact["steps"] == scenario["steps"]
    preserved = skills._manual_case_from_unclassified_scenario(scenario, 1, set())
    assert preserved["steps"] == scenario["steps"]
    assert preserved["sources"] == scenario["sources"]
    assert preserved["preconditions"] == scenario["preconditions"]
    assert preserved["execution_status"] == "未执行"
    analysis = {"sources": ["REQ-002 文档第4页"], "questions": ["【待确认】离线是否可解绑"]}
    assert skills._compact_analysis_for_automation_filter(analysis) == analysis


def test_risk_supplements_remain_p1_with_explicit_design_source():
    rows, _ = skills._complete_mindmap_scenarios_from_risks([], {
        "risks": ["断线恢复", "重复提交"], "requirement_points": ["绑定设备"]
    }, {"target_plan_cases": 2, "risk_extension_count": 2}, mode="mindmap")
    assert len(rows) == 2
    assert all(row["priority"] == "P1" for row in rows)
    assert all("测试设计补充" in row["sources"][0] for row in rows)


def test_coverage_repair_uses_shared_policy_and_full_response_contract():
    prompt = skills.build_case_coverage_repair_prompt("绑定", "设备", {}, {})
    assert all(rule in prompt for rule in RULES)
    assert "sources" in prompt and "execution_status" in prompt
    assert "完整 JSON" in prompt


def test_visual_merge_preserves_original_and_new_sources_without_duplicates():
    original = {"case_id": "TC-001", "sources": ["REQ-001"], "steps": ["打开设置"]}
    patch = {"case_id": "TC-001", "sources": ["Figma 1:2", "REQ-001"], "steps": ["点击设置"]}
    result = skills._merge_visual_records([original], [patch], "case")
    assert result[0]["sources"] == ["REQ-001", "Figma 1:2"]
    assert original["sources"] == ["REQ-001"]


def test_legacy_visual_requires_full_payload_not_skill_delta():
    prompt = skills.build_case_visual_refine_prompt("绑定", "设备", {}, [])
    assert "legacy视觉精修和覆盖补全必须返回完整 JSON" in prompt
    assert "仅visual_grounder Skill返回同ID的增量" in prompt
