import importlib.util
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SPEC = importlib.util.spec_from_file_location("midscene_upload", ROOT / "midscene-upload.py")
midscene = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(midscene)


def test_protected_env_file_supplies_runtime_env_without_overriding_process_values():
    keys = [
        "DASHSCOPE_API_KEY",
        "DASHSCOPE_BASE_URL",
        "DASHSCOPE_MODEL",
        "DASHSCOPE_VL_MODEL",
    ]
    original = {key: os.environ.get(key) for key in keys}
    path = ""
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as env_file:
            path = env_file.name
            env_file.write("export DASHSCOPE_API_KEY='file-test-key'\n")
            env_file.write("export DASHSCOPE_BASE_URL='https://example.invalid/v1'\n")
            env_file.write("export DASHSCOPE_MODEL='file-text-model'\n")
            env_file.write("export DASHSCOPE_VL_MODEL='file-vl-model'\n")
        os.chmod(path, 0o600)
        for key in keys:
            os.environ.pop(key, None)
        status = midscene.load_startup_env(path)
        runtime = midscene.midscene_runtime_env()
        assert status["loaded"] is True
        assert status["valid"] is True
        assert runtime["DASHSCOPE_API_KEY"] == "file-test-key"
        assert runtime["MIDSCENE_MODEL_NAME"] == "file-vl-model"
        os.environ["DASHSCOPE_API_KEY"] = "process-test-key"
        midscene.load_startup_env(path)
        assert midscene.midscene_runtime_env()["DASHSCOPE_API_KEY"] == "process-test-key"
    finally:
        if path and os.path.exists(path):
            os.unlink(path)
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_runtime_env_file_rejects_overly_open_secret_permissions():
    path = ""
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as env_file:
            path = env_file.name
            env_file.write("export DASHSCOPE_API_KEY='must-not-load'\n")
        os.chmod(path, 0o644)
        status = midscene.load_startup_env(path)
        assert status["loaded"] is False
        assert status["valid"] is False
        assert "chmod 600" in status["error"]
    finally:
        if path and os.path.exists(path):
            os.unlink(path)


def test_runtime_env_file_rejects_smart_quotes_and_unclosed_values():
    keys = ["DASHSCOPE_API_KEY", "SONIC_PASSWORD"]
    original = {key: os.environ.get(key) for key in keys}
    path = ""
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as env_file:
            path = env_file.name
            env_file.write("export DASHSCOPE_API_KEY='bad-key’\n")
            env_file.write("export SONIC_PASSWORD='unfinished\n")
        os.chmod(path, 0o600)
        for key in keys:
            os.environ.pop(key, None)
        status = midscene.load_startup_env(path)
        issues = {item["key"]: item["message"] for item in status["issues"]}
        assert status["loaded"] is True
        assert status["valid"] is False
        assert "中文引号" in issues["DASHSCOPE_API_KEY"]
        assert "未闭合" in issues["SONIC_PASSWORD"]
        assert os.environ.get("DASHSCOPE_API_KEY") is None
        assert os.environ.get("SONIC_PASSWORD") is None
    finally:
        if path and os.path.exists(path):
            os.unlink(path)
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_task_server_runtime_env_service_has_required_imports():
    keys = ["DASHSCOPE_API_KEY", "OPENAI_API_KEY"]
    original = {key: os.environ.get(key) for key in keys}
    try:
        os.environ["DASHSCOPE_API_KEY"] = "service-test-key"
        os.environ.pop("OPENAI_API_KEY", None)
        from task_server.services.runner_service import midscene_runtime_env, runtime_env_preview

        env = midscene_runtime_env()
        preview = runtime_env_preview(env)
        assert env["DASHSCOPE_API_KEY"] == "service-test-key"
        assert env["OPENAI_API_KEY"] == "service-test-key"
        assert preview["DASHSCOPE_API_KEY"].startswith("servic")
    finally:
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_sonic_service_migrated_helpers_do_not_reference_legacy_globals():
    source = (ROOT / "task_server" / "services" / "sonic_service.py").read_text(encoding="utf-8")
    assert not re.search(r"(?<!_)parse_time\(", source)
    assert not re.search(r"(?<!_)safe_int\(", source)
    assert not re.search(r"(?<!_)extract_page_items\(", source)
    assert "read_json_file(SONIC_SYNC_FILE" not in source
    assert "write_json_file(SONIC_SYNC_FILE" not in source
    assert "os.makedirs(LEARNING_DIR" not in source
    assert "def append_sonic_notify_log" in source and "cfg.LEARNING_DIR" in source
    assert "from .job_service import find_job, save_jobs, update_task_meta" in source


def test_bridge_groovy_auth_failure_reason_accepts_token_or_session():
    from task_server import router

    class Handler:
        def __init__(self, headers=None, authorized=False):
            self.headers = headers or {}
            self.client_address = ("127.0.0.1", 12345)
            self.authorized = authorized

        def _authorized(self):
            return self.authorized

    original_token = router.TOKEN
    original_callback_token = router.SONIC_CALLBACK_TOKEN
    original_append_log = router.append_sonic_notify_log
    logged = []
    try:
        router.TOKEN = "runner-token"
        router.SONIC_CALLBACK_TOKEN = "callback-token"
        router.append_sonic_notify_log = lambda event, payload, **extra: logged.append((event, payload, extra))

        assert router._bridge_groovy_auth_failure_reason(Handler({"x-token": "runner-token"}), {"case_id": "case-1"}) == ""
        assert router._bridge_groovy_auth_failure_reason(Handler({"x-token": "callback-token"}), {"case_id": "case-1"}) == ""
        assert router._bridge_groovy_auth_failure_reason(Handler(authorized=True), {"case_id": "case-1"}) == ""
        assert logged == []

        assert router._bridge_groovy_auth_failure_reason(Handler(), {"case_id": "case-1"}) == "missing x-token or session unauthorized"
        assert router._bridge_groovy_auth_failure_reason(Handler({"x-token": "bad"}), {"case_id": "case-2"}) == "invalid x-token"
        assert [row[0] for row in logged] == ["bridge_groovy_unauthorized", "bridge_groovy_unauthorized"]
        assert logged[0][1]["case_id"] == "case-1"
        assert logged[0][1]["has_x_token"] is False
        assert logged[1][1]["case_id"] == "case-2"
        assert logged[1][1]["has_x_token"] is True
    finally:
        router.TOKEN = original_token
        router.SONIC_CALLBACK_TOKEN = original_callback_token
        router.append_sonic_notify_log = original_append_log


def test_dashscope_chat_body_requests_json_object_output():
    body = midscene.build_dashscope_chat_body(
        "只输出 JSON",
        image_assets=None,
        temperature=0.2,
        json_response=True,
    )
    assert body["response_format"] == {"type": "json_object"}
    assert body["temperature"] == 0.2
    assert body["messages"][0]["content"] == "你只输出合法 JSON。"


def test_ai_skill_schema_validation_rejects_missing_required_fields():
    valid = {
        "title": "示例",
        "module": "验收",
        "analysis": {"requirement_points": ["入口可达"]},
        "scenarios": [],
        "cases": [],
        "manual_cases": [],
        "review": {},
    }
    assert midscene.validate_ai_skill_output("cases_payload", valid) == valid
    invalid = {
        "title": "示例",
        "module": "验收",
        "analysis": {},
        "scenarios": [],
        "cases": [],
        "manual_cases": [],
        "review": {},
    }
    try:
        midscene.validate_ai_skill_output("cases_payload", invalid)
    except ValueError as exc:
        assert "requirement_points" in str(exc)
    else:
        raise AssertionError("schema validation should reject missing requirement_points")


def test_build_cases_payload_from_skills_composes_three_stage_pipeline():
    original = midscene.run_ai_skill
    calls = []

    def fake_run_ai_skill(skill_name, payload=None, **kwargs):
        calls.append(skill_name)
        if skill_name == "requirement_analyzer":
            return {
                "business_goals": ["用户进入目标页"],
                "roles": ["已登录用户"],
                "entry_points": ["首页"],
                "state_assumptions": ["已登录"],
                "data_assumptions": ["数据可为空"],
                "risks": ["入口不可达"],
                "requirement_points": ["用户可以进入目标页"],
                "questions": [],
            }
        if skill_name == "scenario_designer":
            return {
                "scenarios": [{
                    "feature": "目标功能",
                    "scenario": "进入目标页",
                    "type": "正常流程",
                    "design_method": ["等价类"],
                    "business_path": "首页 -> 目标页",
                    "expected": "目标页展示",
                    "automation_suitable": True,
                    "reason": "UI 可见",
                }]
            }
        if skill_name == "automation_filter":
            return {
                "cases": [{
                    "case_id": "TC-001",
                    "title": "进入目标页",
                    "priority": "P1",
                    "smoke": True,
                    "steps": ["点击「目标」入口"],
                    "assertions": ["页面展示「目标」标题或核心区域"],
                    "tags": ["冒烟"],
                }],
                "manual_cases": [],
                "review": {"automation_check": "可自动化"},
            }
        raise AssertionError(skill_name)

    try:
        midscene.run_ai_skill = fake_run_ai_skill
        payload = midscene.build_cases_payload_from_skills("标题", "模块", ["需求文本"])
        assert calls == ["requirement_analyzer", "scenario_designer", "automation_filter"]
        assert payload["analysis"]["requirement_points"] == ["用户可以进入目标页"]
        assert payload["cases"][0]["case_id"] == "TC-001"
        assert "requirement_analyzer" in payload["review"]["skill_pipeline"]
    finally:
        midscene.run_ai_skill = original


def test_call_dashscope_cases_falls_back_to_legacy_when_skill_pipeline_fails():
    original_build = midscene.build_cases_payload_from_skills
    original_legacy = midscene.call_dashscope_cases_legacy

    def fake_build(*args, **kwargs):
        raise ValueError("skill failed")

    def fake_legacy(title, module, text_assets, image_assets):
        return {
            "title": title,
            "module": module,
            "analysis": {"requirement_points": ["兜底需求"]},
            "scenarios": [],
            "cases": [{
                "case_id": "TC-001",
                "title": "兜底用例",
                "steps": ["点击入口"],
                "assertions": ["页面展示核心区域"],
            }],
            "manual_cases": [],
            "review": {},
        }

    try:
        midscene.build_cases_payload_from_skills = fake_build
        midscene.call_dashscope_cases_legacy = fake_legacy
        payload = midscene.call_dashscope_cases("标题", "模块", ["需求文本"], [])
        assert payload["cases"][0]["title"] == "兜底用例"
        assert payload["review"]["skill_pipeline"] == "fallback_legacy_prompt"
        assert "skill failed" in payload["review"]["skill_pipeline_error"]
    finally:
        midscene.build_cases_payload_from_skills = original_build
        midscene.call_dashscope_cases_legacy = original_legacy


def test_visual_grounder_skill_preserves_base_requirement_points():
    original = midscene.run_ai_skill
    calls = []
    base_payload = {
        "title": "标题",
        "module": "模块",
        "analysis": {"requirement_points": ["进入目标页"]},
        "scenarios": [{"scenario": "进入目标页"}],
        "cases": [{
            "case_id": "TC-001",
            "title": "进入目标页",
            "steps": ["点击入口"],
            "assertions": ["页面展示目标标题"],
        }],
        "manual_cases": [],
        "review": {},
    }

    def fake_run_ai_skill(skill_name, payload=None, **kwargs):
        calls.append((skill_name, payload))
        assert skill_name == "visual_grounder"
        return {
            "title": "标题",
            "module": "模块",
            "analysis": {},
            "scenarios": base_payload["scenarios"],
            "cases": [{
                "case_id": "TC-001",
                "title": "进入目标页",
                "steps": ["点击底部 Tab「目标」"],
                "assertions": ["页面展示「目标」标题或核心区域"],
            }],
            "manual_cases": [],
            "review": {"visual_grounding_check": "已校准入口"},
        }

    try:
        midscene.run_ai_skill = fake_run_ai_skill
        payload = midscene.call_visual_grounder_skill("标题", "模块", base_payload, ["页面知识"], [])
        assert calls[0][0] == "visual_grounder"
        assert payload["analysis"]["requirement_points"] == ["进入目标页"]
        assert payload["review"]["visual_grounder_skill"] == "visual_grounder.v1"
        assert "底部 Tab" in payload["cases"][0]["steps"][0]
    finally:
        midscene.run_ai_skill = original


def test_refine_cases_falls_back_to_legacy_when_visual_grounder_fails():
    original_skill = midscene.call_visual_grounder_skill
    original_legacy = midscene.call_dashscope_refine_cases_legacy
    base_payload = {
        "title": "标题",
        "module": "模块",
        "analysis": {"requirement_points": ["进入目标页"]},
        "scenarios": [],
        "cases": [{
            "case_id": "TC-001",
            "title": "旧用例",
            "steps": ["点击入口"],
            "assertions": ["页面展示核心区域"],
        }],
        "manual_cases": [],
        "review": {},
    }

    def fake_skill(*args, **kwargs):
        raise ValueError("visual failed")

    def fake_legacy(title, module, payload, visual_text_assets, image_assets):
        result = dict(payload)
        result["cases"] = [dict(payload["cases"][0], title="兜底视觉用例")]
        result["review"] = {}
        return result

    try:
        midscene.call_visual_grounder_skill = fake_skill
        midscene.call_dashscope_refine_cases_legacy = fake_legacy
        payload = midscene.call_dashscope_refine_cases("标题", "模块", base_payload, ["页面知识"], [])
        assert payload["cases"][0]["title"] == "兜底视觉用例"
        assert payload["review"]["visual_grounder_skill"] == "fallback_legacy_refine_prompt"
        assert "visual failed" in payload["review"]["visual_grounder_error"]
    finally:
        midscene.call_visual_grounder_skill = original_skill
        midscene.call_dashscope_refine_cases_legacy = original_legacy


def test_coverage_auditor_skill_can_mark_payload_ok():
    original = midscene.run_ai_skill
    calls = []
    payload = {
        "title": "标题",
        "module": "模块",
        "analysis": {"requirement_points": ["进入目标页"]},
        "scenarios": [{"scenario": "进入目标页"}],
        "cases": [{
            "case_id": "TC-001",
            "title": "进入目标页",
            "steps": ["点击入口"],
            "assertions": ["页面展示目标标题"],
        }],
        "manual_cases": [],
        "review": {},
    }

    def fake_run_ai_skill(skill_name, payload=None, **kwargs):
        calls.append(skill_name)
        assert skill_name == "coverage_auditor"
        return {
            "coverage_check": "覆盖完整",
            "missing_requirement_points": [],
            "missing_case_points": [],
            "missing_scenario_points": [],
            "generic_assertion_cases": [],
            "duplicate_cases": [],
            "questions": [],
            "ok": True,
        }

    try:
        midscene.run_ai_skill = fake_run_ai_skill
        current, audit = midscene.improve_case_coverage("标题", "模块", payload, max_rounds=1)
        assert calls == ["coverage_auditor"]
        assert audit["coverage_auditor_skill"] == "coverage_auditor.v1"
        assert current["review"]["coverage_audit"]["ok"] is True
    finally:
        midscene.run_ai_skill = original


def test_coverage_auditor_falls_back_to_local_audit_when_skill_fails():
    original = midscene.run_ai_skill
    payload = {
        "title": "标题",
        "module": "模块",
        "analysis": {"requirement_points": ["进入目标页"]},
        "scenarios": [{"scenario": "进入目标页"}],
        "cases": [{
            "case_id": "TC-001",
            "title": "进入目标页",
            "steps": ["点击入口"],
            "assertions": ["页面展示目标标题"],
        }],
        "manual_cases": [],
        "review": {},
    }

    def fake_run_ai_skill(*args, **kwargs):
        raise ValueError("audit failed")

    try:
        midscene.run_ai_skill = fake_run_ai_skill
        current, audit = midscene.improve_case_coverage("标题", "模块", payload, max_rounds=1)
        assert current["review"]["coverage_auditor_skill"] == "fallback_local_audit"
        assert "audit failed" in current["review"]["coverage_auditor_error"]
        assert "ok" in audit
    finally:
        midscene.run_ai_skill = original


def test_ai_skill_fixture_terms_keep_prompts_grounded_to_platform_flow():
    fixture_path = ROOT / "ai_skills" / "evals" / "fixtures" / "mobile_print_record_generation.json"
    with fixture_path.open("r", encoding="utf-8") as fixture_file:
        fixture = json.load(fixture_file)
    expected_contract = fixture["expected_contract"]
    for skill_name, contract in expected_contract.items():
        prompt = midscene.load_ai_skill_prompt(skill_name)
        assert "{{payload}}" in prompt
        for term in contract.get("must_include_prompt_terms", []):
            assert term in prompt
    for field in expected_contract["requirement_analyzer"]["output_fields"]:
        assert field in midscene.load_ai_skill_schema("requirement_analyzer").get("required", [])


def test_repair_patch_planner_skill_is_used_before_legacy_prompt():
    original_run = midscene.run_ai_skill
    original_context = midscene.repair_knowledge_context
    calls = []

    def fake_run_ai_skill(skill_name, payload=None, **kwargs):
        calls.append((skill_name, payload, kwargs))
        assert skill_name == "repair_patch_planner"
        assert payload["framework"]["task"]
        assert payload["framework"]["midscene"]
        assert payload["framework"]["sonic"]
        return {
            "analysis": "点击后目标按钮尚未渲染",
            "changes": ["在完成后等待 PNG 选项"],
            "patches": [{
                "op": "insert_after",
                "anchor": "aiTap: 完成",
                "lines": ["aiWaitFor: 页面出现 PNG 选项"],
                "reason": "目标按钮延迟渲染",
            }],
        }

    try:
        midscene.run_ai_skill = fake_run_ai_skill
        midscene.repair_knowledge_context = lambda *args, **kwargs: ("页面知识：完成后出现 PNG", [], ["page-1"])
        result = midscene.call_dashscope_repair_yaml_task_patch(
            "模块",
            "case.yaml",
            "导出 PNG",
            "android:\n  packageName: com.demo.app\ntasks:\n  - name: 导出 PNG\n    flow:\n      - aiTap: 完成\n",
            "- name: 导出 PNG\n  # baseline.goal: 导出 PNG\n  # baseline.path: 首页 -> 完成 -> PNG\n  flow:\n    - aiTap: 完成\n",
            "找不到 PNG",
            "",
            {"error": "PNG not found"},
            [],
        )
        assert calls and calls[0][0] == "repair_patch_planner"
        assert result["repair_patch_skill"] == "repair_patch_planner.v1"
        assert result["patches"][0]["op"] == "insert_after"
        assert result["used_knowledge_pages"] == ["page-1"]
    finally:
        midscene.run_ai_skill = original_run
        midscene.repair_knowledge_context = original_context


def test_sonic_suite_case_count_from_dto_shapes():
    assert midscene.sonic_count_suite_cases({"testCases": [{"id": 1}, {"id": 2}]}) == 2
    assert midscene.sonic_count_suite_cases({"caseIds": [1, 2, 3]}) == 3
    assert midscene.sonic_count_suite_cases({"caseIds": "1,2; 3 4"}) == 4
    assert midscene.sonic_count_suite_cases({"caseCount": 5}) == 5


def test_sonic_suite_expected_total_uses_definition_and_result_meta():
    suite = {
        "results": [{"status": "success", "total_task_count": 1}],
        "sonic_suite_definition": {"expected_total_count": 3},
        "sonic_result_meta": {"send_msg_count": 5},
    }
    stats = midscene.sonic_suite_display_stats(suite)
    assert stats["total"] == 5
    assert stats["actual_total"] == 1
    assert stats["pending"] == 4


def test_sonic_result_matching_prefers_exact_suite_id():
    suite = {
        "sonic_suite_id": "46",
        "sonic_suite_name": "3DUI基线测试",
        "created_ts": midscene.parse_time("2026-05-25 09:59:00"),
        "last_update_ts": midscene.parse_time("2026-05-25 10:01:00"),
    }
    right = {
        "id": 505,
        "projectId": 3,
        "suiteId": 46,
        "suiteName": "3DUI基线测试",
        "sendMsgCount": 3,
        "receiveMsgCount": 3,
        "createTime": "2026-05-25 10:00:00",
        "status": 1,
    }
    wrong = dict(right, id=506, suiteId=47, suiteName="其他套件")
    assert midscene.sonic_score_result_meta_for_suite(right, suite, 3) > 3000
    assert midscene.sonic_score_result_meta_for_suite(wrong, suite, 3) == -1
    assert midscene.sonic_score_result_for_suite(right, suite, 3) > 3000
    assert midscene.sonic_score_result_for_suite(wrong, suite, 3) == -1


def test_sonic_failed_status_does_not_mean_suite_finished():
    still_running_after_failure = {
        "status": 3,
        "sendMsgCount": 2,
        "receiveMsgCount": 1,
    }
    all_results_received = {
        "status": 3,
        "sendMsgCount": 2,
        "receiveMsgCount": 2,
    }
    assert not midscene.sonic_result_is_finished(still_running_after_failure)
    assert midscene.sonic_result_is_finished(all_results_received)


def test_sonic_suite_summary_card_marks_pending_as_warning():
    suite = {
        "app_package": "com.kfb.model",
        "run_mode": "baseline",
        "results": [{"status": "success", "total_task_count": 1}],
        "sonic_suite_definition": {"expected_total_count": 2},
    }
    card = midscene.build_sonic_suite_summary_card(suite)
    text = str(card)
    assert "告警" in text
    assert "待回传" in text


def test_sonic_completed_suite_reports_missing_task_callbacks():
    suite = {
        "app_package": "com.kfb.model",
        "run_mode": "baseline",
        "results": [{"status": "success", "total_task_count": 1}],
        "sonic_result_meta": {
            "finished": True,
            "send_msg_count": 2,
            "receive_msg_count": 2,
            "expected_total_count": 2,
            "status": 1,
            "status_text": "通过",
        },
    }
    stats = midscene.sonic_suite_display_stats(suite)
    assert stats["total"] == 2
    assert stats["passed"] == 2
    assert stats["warning"] == 0
    assert stats["pending"] == 0
    assert stats["missing_task_callbacks"] == 1
    text = str(midscene.build_sonic_suite_summary_card(suite))
    assert "基线回归通过" in text
    assert "待回传" not in text


def test_suite_summary_report_renders_missing_task_callbacks_as_rows():
    old_report_dir = midscene.REPORT_DIR
    try:
        with tempfile.TemporaryDirectory() as tmp:
            midscene.REPORT_DIR = tmp
            suite = {
                "suite_key": "sonic_result_3_647",
                "app_package": "com.kfb.model",
                "run_mode": "baseline",
                "device_id": "device-1",
                "sonic_report_url": "http://sonic/Home/3/ResultDetail/647",
                "results": [
                    {"status": "success", "module": "3D打印基线", "target_task_name": "普通印章打印"},
                    {"status": "success", "module": "3D打印基线", "target_task_name": "文字建模"},
                ],
                "sonic_result_meta": {
                    "finished": True,
                    "send_msg_count": 4,
                    "receive_msg_count": 4,
                    "expected_total_count": 4,
                    "status": 1,
                    "status_text": "通过",
                },
            }
            midscene.write_sonic_suite_summary_report(suite)
            html = Path(tmp, "sonic_result_3_647-summary.html").read_text(encoding="utf-8")
            assert html.count("<tr>") == 5  # header + 2 real rows + 2 missing callback rows
            assert "未回传用例 1" in html
            assert "Sonic 原始报告已结束，但 Task 平台未收到该用例的桥接回传" in html
    finally:
        midscene.REPORT_DIR = old_report_dir


def test_generation_summary_includes_requirement_analysis_markdown():
    old_case_dir = midscene.CASE_DIR
    try:
        with tempfile.TemporaryDirectory() as tmp:
            midscene.CASE_DIR = tmp
            payload = {
                "analysis": {
                    "business_goals": ["用户可以查看收藏内容"],
                    "roles": ["已登录用户"],
                    "entry_points": ["首页 -> 我的"],
                    "requirement_points": ["从我的页进入我的收藏"],
                    "coverage_matrix": [
                        {
                            "feature": "我的收藏",
                            "requirement_point": "从我的页进入我的收藏",
                            "auto_cases": ["TC-001"],
                            "manual_cases": ["未登录态提示"],
                        }
                    ],
                },
                "scenarios": [{"scenario": "进入我的收藏"}],
                "cases": [
                    {
                        "case_id": "TC-001",
                        "title": "进入我的收藏列表",
                        "priority": "P1",
                        "smoke": True,
                        "scenario": "进入我的收藏",
                        "steps": ["点击底部 Tab「我的」", "点击「我的收藏」入口"],
                        "assertions": ["页面展示「我的收藏」标题或收藏列表/空态"],
                    }
                ],
                "manual_cases": [{"title": "未登录态提示", "reason": "需要切换登录态"}],
            }
            summary = midscene.build_generation_summary("cs-test", "收藏需求", "我的", "task.yaml", payload)
            assert summary["requirement_analysis"]["requirement_points"] == ["从我的页进入我的收藏"]
            paths = midscene.write_generation_summary("cs-test", summary)
            md = Path(paths["markdown"]).read_text(encoding="utf-8")
            assert "## 需求分析" in md
            assert "业务目标：用户可以查看收藏内容" in md
            assert "| 我的收藏 | 从我的页进入我的收藏 | TC-001 | 未登录态提示 | - |" in md
    finally:
        midscene.CASE_DIR = old_case_dir


def test_sonic_result_id_derives_fixed_report_url_without_lookup():
    old_report_dir = midscene.REPORT_DIR
    suite = {
        "suite_key": "sonic_result_3_647",
        "app_package": "com.kfb.model",
        "run_mode": "baseline",
        "sonic_result_id": 647,
        "sonic_project_id": 3,
        "results": [
            {"status": "failed", "module": "3D打印基线", "target_task_name": "模型导入-本地导入"},
        ],
        "sonic_completion": {
            "finished": True,
            "status": "failed",
            "total": 1,
            "passed": 0,
            "failed": 1,
            "warning": 0,
        },
        "sonic_report_lookup": {"attempt": 6, "max_attempt": 6, "error": "未匹配到 Sonic 测试结果"},
        "sonic_report_lookup_error": "未匹配到 Sonic 测试结果",
    }
    url = midscene.ensure_sonic_suite_report_url(suite)
    assert url.endswith("/Home/3/ResultDetail/647")
    assert midscene.sonic_suite_report_lookup_message(suite) == ""
    card_text = str(midscene.build_sonic_suite_summary_card(suite))
    assert "查看 Sonic 报告" in card_text
    assert "未匹配到 Sonic 测试结果" not in card_text
    try:
        with tempfile.TemporaryDirectory() as tmp:
            midscene.REPORT_DIR = tmp
            midscene.write_sonic_suite_summary_report(dict(suite, sonic_report_url=""))
            html = Path(tmp, "sonic_result_3_647-summary.html").read_text(encoding="utf-8")
            assert "/Home/3/ResultDetail/647" in html
            assert "未匹配到 Sonic 测试结果" not in html
    finally:
        midscene.REPORT_DIR = old_report_dir


def test_feishu_reason_does_not_emit_irrecoverable_mojibake():
    original = "当前页面弹出更新提示框，遮挡了主界面；未显示搜索输入框。"
    broken = original.encode("utf-8").decode("gbk", errors="replace")
    assert "�" in broken
    assert midscene.sonic_notify_clean_text(broken) == "日志编码异常，请查看报告"
    assert midscene.sonic_notify_clean_text("Assertion failed: 小锟斤拷商品列表锟斤拷") == "日志编码异常，请查看报告"


def test_feishu_reason_recovers_reversible_utf8_as_gbk_text():
    original = "当前页面"
    broken = original.encode("utf-8").decode("gbk")
    assert midscene.sonic_notify_clean_text(broken) == original


def test_feishu_webhook_rejects_multiline_export_pollution():
    polluted = (
        "https://open.feishu.cn/open-apis/bot/v2/hook/example”\n"
        "export FIGMA_TOKEN=not-a-webhook"
    )
    try:
        midscene.validate_feishu_webhook(polluted)
        assert False, "polluted Webhook should be rejected"
    except ValueError as error:
        assert "单行机器人地址" in str(error)


def test_feishu_webhook_rejects_smart_quotes():
    try:
        midscene.validate_feishu_webhook("“https://open.feishu.cn/open-apis/bot/v2/hook/example”")
        assert False, "quoted Webhook should be rejected"
    except ValueError as error:
        assert "中文引号" in str(error)


def test_task_app_save_rejects_polluted_feishu_webhook():
    try:
        midscene.normalize_task_app({
            "name": "3D 打印",
            "package": "com.kfb.model",
            "feishu_webhook": "https://open.feishu.cn/open-apis/bot/v2/hook/example\nexport APP_PACKAGE=com.kfb.model",
        })
        assert False, "invalid application Webhook should not be saved"
    except ValueError as error:
        assert "单行机器人地址" in str(error)


def test_legacy_sonic_custom_robot_path_enters_suite_completion_flow():
    assert "/api/sonic/custom-robot" in midscene.SONIC_SUITE_COMPLETION_PATHS
    assert "/api/sonic/suite-complete" in midscene.SONIC_SUITE_COMPLETION_PATHS


def test_parse_manual_sonic_suite_completion_text_with_result_url():
    raw = """测试套件：3D测试自动 运行完毕！
通过数：1
异常数：0
失败数：0
测试报告：http://101.34.197.12:3000/Home/3/ResultDetail/603
""".encode("utf-8")
    event = midscene.parse_sonic_suite_completion_payload(raw, "text/plain;charset=UTF-8")
    assert event["suite_name"] == "3D测试自动"
    assert event["passed"] == 1
    assert event["failed"] == 0
    assert event["total"] == 1
    assert event["status"] == "success"
    assert event["project_id"] == 3
    assert event["result_id"] == 603


def test_parse_interrupted_sonic_suite_completion_text():
    raw = """测试套件：3D测试自动
运行状态：中断
总数：3
通过数：1
失败数：0
异常数：0
测试报告：http://101.34.197.12:3000/Home/3/ResultDetail/604
""".encode("utf-8")
    event = midscene.parse_sonic_suite_completion_payload(raw, "text/plain;charset=UTF-8")
    assert event["status"] == "interrupted"
    assert event["total"] == 3
    assert event["passed"] == 1
    assert event["project_id"] == 3
    assert event["result_id"] == 604


def test_manual_sonic_completion_stats_work_without_bridge_results():
    suite = {
        "app_package": "com.kfb.model",
        "run_mode": "baseline",
        "results": [],
        "sonic_completion": {
            "finished": True,
            "status": "success",
            "passed": 3,
            "failed": 0,
            "warning": 0,
            "total": 3,
            "duration": "9分59秒",
        },
    }
    stats = midscene.sonic_suite_display_stats(suite)
    assert stats["total"] == 3
    assert stats["passed"] == 3
    assert stats["pending"] == 0
    text = str(midscene.build_sonic_suite_summary_card(suite))
    assert "3 条用例" in text
    assert "通过 3" in text


def test_suite_duration_prefers_sonic_result_create_and_end_time():
    suite = {
        "app_package": "com.kfb.model",
        "run_mode": "baseline",
        "results": [
            {
                "status": "success",
                "started_at": "2026-06-01 16:55:00",
                "finished_at": "2026-06-01 16:58:00",
            }
        ],
        "sonic_completion": {
            "finished": True,
            "status": "success",
            "passed": 4,
            "failed": 0,
            "warning": 0,
            "total": 4,
            "duration": "6分59秒",
        },
        "sonic_result_meta": {
            "finished": True,
            "send_msg_count": 4,
            "receive_msg_count": 4,
            "expected_total_count": 4,
            "status": 1,
            "status_text": "通过",
            "createTime": "2026-06-01 16:48:51",
            "endTime": "2026-06-01 16:59:55",
        },
    }
    assert midscene.sonic_suite_duration_text(suite) == "11分4秒"
    text = str(midscene.build_sonic_suite_summary_card(suite))
    assert "耗时：11分4秒" in text
    assert "6分59秒" not in text


def test_interrupted_suite_card_is_labeled_interrupted():
    suite = {
        "app_package": "com.kfb.model",
        "run_mode": "baseline",
        "results": [],
        "sonic_completion": {
            "finished": True,
            "status": "interrupted",
            "passed": 1,
            "failed": 0,
            "warning": 0,
            "total": 3,
        },
    }
    stats = midscene.sonic_suite_display_stats(suite)
    assert stats["total"] == 3
    assert stats["warning"] == 2
    assert midscene.sonic_suite_effective_status(suite) == "interrupted"
    text = str(midscene.build_sonic_suite_summary_card(suite))
    assert "中断" in text
    assert "orange" in text


def test_manual_sonic_completion_creates_summary_trigger_without_case_callback():
    state = {"active": {}, "suites": {}}
    scheduled = []
    original_load = midscene.load_sonic_suite_results
    original_save = midscene.save_sonic_suite_results
    original_schedule = midscene.schedule_sonic_suite_summary
    original_log = midscene.append_sonic_notify_log
    original_app = midscene.sonic_suite_app_for_completion
    try:
        midscene.load_sonic_suite_results = lambda: state
        midscene.save_sonic_suite_results = lambda payload: state.update(payload)
        midscene.schedule_sonic_suite_summary = lambda suite_key, delay=None: scheduled.append((suite_key, delay))
        midscene.append_sonic_notify_log = lambda *args, **kwargs: None
        midscene.sonic_suite_app_for_completion = lambda event: {"package": "com.kfb.model", "name": "3D 打印"}
        result = midscene.register_sonic_suite_completion({
            "project_id": 3,
            "result_id": 603,
            "suite_name": "3D测试自动",
            "status": "success",
            "passed": 1,
            "failed": 0,
            "warning": 0,
            "total": 1,
            "report_url": "http://sonic/Home/3/ResultDetail/603",
        })
    finally:
        midscene.load_sonic_suite_results = original_load
        midscene.save_sonic_suite_results = original_save
        midscene.schedule_sonic_suite_summary = original_schedule
        midscene.append_sonic_notify_log = original_log
        midscene.sonic_suite_app_for_completion = original_app

    suite = state["suites"][result["suite_key"]]
    assert suite["completion_received"] is True
    assert suite["sonic_report_url"].endswith("/Home/3/ResultDetail/603")
    assert scheduled == [(result["suite_key"], 5)]


def test_failed_suite_notification_can_be_retried_for_same_sonic_result():
    state = {
        "active": {},
        "suites": {
            "sonic_result_3_603": {
                "suite_key": "sonic_result_3_603",
                "results": [],
                "sonic_result_id": 603,
                "sent_at": "2026-05-26 10:00:00",
                "send_error": "未配置应用飞书机器人 Webhook",
            }
        },
    }
    scheduled = []
    original_load = midscene.load_sonic_suite_results
    original_save = midscene.save_sonic_suite_results
    original_schedule = midscene.schedule_sonic_suite_summary
    original_log = midscene.append_sonic_notify_log
    original_app = midscene.sonic_suite_app_for_completion
    try:
        midscene.load_sonic_suite_results = lambda: state
        midscene.save_sonic_suite_results = lambda payload: state.update(payload)
        midscene.schedule_sonic_suite_summary = lambda suite_key, delay=None: scheduled.append((suite_key, delay))
        midscene.append_sonic_notify_log = lambda *args, **kwargs: None
        midscene.sonic_suite_app_for_completion = lambda event: {"package": "com.kfb.model", "name": "3D 打印"}
        result = midscene.register_sonic_suite_completion({
            "project_id": 3,
            "result_id": 603,
            "suite_name": "3D测试自动",
            "status": "success",
            "passed": 1,
            "total": 1,
        })
    finally:
        midscene.load_sonic_suite_results = original_load
        midscene.save_sonic_suite_results = original_save
        midscene.schedule_sonic_suite_summary = original_schedule
        midscene.append_sonic_notify_log = original_log
        midscene.sonic_suite_app_for_completion = original_app

    assert result["duplicate"] is False
    assert scheduled == [("sonic_result_3_603", 5)]


def test_baseline_sonic_case_results_wait_for_authoritative_suite_completion():
    state = {"active": {}, "suites": {}}
    scheduled = []
    original_flag = midscene.SONIC_NOTIFY_ON_SUITE_COMPLETION_ONLY
    original_load = midscene.load_sonic_suite_results
    original_save = midscene.save_sonic_suite_results
    original_schedule = midscene.schedule_sonic_suite_summary
    original_log = midscene.append_sonic_notify_log
    original_app = midscene.sonic_suite_app_info
    try:
        midscene.SONIC_NOTIFY_ON_SUITE_COMPLETION_ONLY = True
        midscene.load_sonic_suite_results = lambda: state
        midscene.save_sonic_suite_results = lambda payload: state.update(payload)
        midscene.schedule_sonic_suite_summary = lambda suite_key, delay=None: scheduled.append((suite_key, delay))
        midscene.append_sonic_notify_log = lambda *args, **kwargs: None
        midscene.sonic_suite_app_info = lambda package="", module="": {"package": "com.kfb.model", "name": "3D 打印"}
        suite_key = midscene.register_sonic_suite_result({
            "source": "sonic",
            "status": "success",
            "run_mode": "baseline",
            "job_id": "sonic_job_1",
            "module": "3D打印基线",
            "file": "case.yaml",
            "app_package": "com.kfb.model",
            "device_id": "device-1",
            "runner_id": "sonic",
        })
    finally:
        midscene.SONIC_NOTIFY_ON_SUITE_COMPLETION_ONLY = original_flag
        midscene.load_sonic_suite_results = original_load
        midscene.save_sonic_suite_results = original_save
        midscene.schedule_sonic_suite_summary = original_schedule
        midscene.append_sonic_notify_log = original_log
        midscene.sonic_suite_app_info = original_app

    assert scheduled and scheduled[0][0] == suite_key
    assert state["suites"][suite_key]["notification_mode"] == "suite_completion"
    assert len(state["suites"][suite_key]["results"]) == 1


def test_suite_completion_mode_never_sends_partial_fallback():
    now = int(time.time())
    state = {
        "active": {},
        "suites": {
            "suite-timeout": {
                "suite_key": "suite-timeout",
                "source": "sonic",
                "run_mode": "baseline",
                "app_package": "com.kfb.model",
                "app": {"package": "com.kfb.model", "name": "3D 打印"},
                "created_ts": now - midscene.sonic_suite_max_wait_seconds() - 5,
                "last_update_ts": now - midscene.sonic_suite_quiet_seconds() - 5,
                "results": [{
                    "job_id": "sonic_job_1",
                    "status": "success",
                    "module": "3D打印基线",
                    "target_task_name": "用例A",
                }],
                "notification_mode": "suite_completion",
            }
        },
    }
    logged = []
    scheduled = []
    original_flag = midscene.SONIC_NOTIFY_ON_SUITE_COMPLETION_ONLY
    original_load = midscene.load_sonic_suite_results
    original_save = midscene.save_sonic_suite_results
    original_log = midscene.append_sonic_notify_log
    original_timer = midscene.threading.Timer
    original_attach_definition = midscene.attach_sonic_suite_definition_from_api
    original_attach_meta = midscene.attach_sonic_result_meta_from_api
    original_attach_report = midscene.attach_sonic_report_from_api
    original_webhook = midscene.task_app_feishu_webhook

    class DummyTimer:
        def __init__(self, delay, fn, args=()):
            scheduled.append(delay)
            self.daemon = False

        def start(self):
            pass

    try:
        midscene.SONIC_NOTIFY_ON_SUITE_COMPLETION_ONLY = True
        midscene.load_sonic_suite_results = lambda: state
        midscene.save_sonic_suite_results = lambda payload: state.update(payload)
        midscene.append_sonic_notify_log = lambda event, *args, **kwargs: logged.append(event)
        midscene.threading.Timer = DummyTimer
        midscene.attach_sonic_suite_definition_from_api = lambda suite_key, suite: suite
        midscene.attach_sonic_result_meta_from_api = lambda suite_key, suite: suite
        midscene.attach_sonic_report_from_api = lambda suite_key, suite: suite
        midscene.task_app_feishu_webhook = lambda app: "https://feishu.test/webhook"
        midscene.send_sonic_suite_summary_if_quiet("suite-timeout")
    finally:
        midscene.SONIC_NOTIFY_ON_SUITE_COMPLETION_ONLY = original_flag
        midscene.load_sonic_suite_results = original_load
        midscene.save_sonic_suite_results = original_save
        midscene.append_sonic_notify_log = original_log
        midscene.threading.Timer = original_timer
        midscene.attach_sonic_suite_definition_from_api = original_attach_definition
        midscene.attach_sonic_result_meta_from_api = original_attach_meta
        midscene.attach_sonic_report_from_api = original_attach_report
        midscene.task_app_feishu_webhook = original_webhook
        midscene.SONIC_SUITE_TIMERS.pop("suite-timeout", None)

    suite = state["suites"]["suite-timeout"]
    assert not suite.get("sent_at")
    assert suite["send_in_progress"] is False
    assert logged == ["suite_summary_wait_sonic_result_finished"]
    assert scheduled == [midscene.sonic_suite_running_check_delay_seconds()]


def test_authoritative_completion_does_not_reuse_legacy_partial_summary():
    suite = {
        "suite_key": "suite-timeout-sent",
        "notification_mode": "suite_completion",
        "completion_timeout": True,
        "completion_received": False,
        "sent_at": "2026-05-27 07:11:00",
        "app_package": "com.kfb.model",
        "sonic_suite_name": "3D测试自动",
    }
    event = {
        "app_package": "com.kfb.model",
        "suite_name": "3D测试自动",
        "result_id": 603,
    }
    assert midscene.sonic_suite_matches_completion(suite, event) is False


def test_authoritative_result_id_does_not_attach_historical_case_bucket():
    state = {
        "active": {},
        "suites": {
            "old-case-bucket": {
                "suite_key": "old-case-bucket",
                "app_package": "com.kfb.model",
                "sonic_suite_name": "3D测试自动",
                "notification_mode": "suite_completion",
                "results": [{"job_id": "old-job", "status": "success", "target_task_name": "历史用例"}],
            },
        },
    }
    scheduled = []
    original_load = midscene.load_sonic_suite_results
    original_save = midscene.save_sonic_suite_results
    original_schedule = midscene.schedule_sonic_suite_summary
    original_log = midscene.append_sonic_notify_log
    original_app = midscene.sonic_suite_app_for_completion
    try:
        midscene.load_sonic_suite_results = lambda: state
        midscene.save_sonic_suite_results = lambda payload: state.update(payload)
        midscene.schedule_sonic_suite_summary = lambda suite_key, delay=None: scheduled.append((suite_key, delay))
        midscene.append_sonic_notify_log = lambda *args, **kwargs: None
        midscene.sonic_suite_app_for_completion = lambda event: {"package": "com.kfb.model", "name": "3D 打印"}
        result = midscene.register_sonic_suite_completion({
            "project_id": 3,
            "result_id": 632,
            "suite_name": "3D测试自动",
            "status": "success",
            "passed": 1,
            "failed": 0,
            "warning": 0,
            "total": 1,
            "report_url": "http://sonic/Home/3/ResultDetail/632",
        })
    finally:
        midscene.load_sonic_suite_results = original_load
        midscene.save_sonic_suite_results = original_save
        midscene.schedule_sonic_suite_summary = original_schedule
        midscene.append_sonic_notify_log = original_log
        midscene.sonic_suite_app_for_completion = original_app

    assert result["suite_key"] == "sonic_result_3_632"
    assert state["suites"]["old-case-bucket"]["results"][0]["target_task_name"] == "历史用例"
    assert state["suites"]["sonic_result_3_632"]["results"] == []
    assert state["suites"]["sonic_result_3_632"]["completion_source"] == "sonic_callback"
    assert scheduled == [("sonic_result_3_632", 5)]


def test_completion_without_result_id_does_not_attach_historical_case_bucket():
    state = {
        "active": {},
        "suites": {
            "old-case-bucket": {
                "suite_key": "old-case-bucket",
                "app_package": "com.kfb.model",
                "sonic_suite_name": "3D测试自动",
                "results": [{"job_id": "old-job", "status": "success", "target_task_name": "历史用例"}],
            },
        },
    }
    scheduled = []
    original_load = midscene.load_sonic_suite_results
    original_save = midscene.save_sonic_suite_results
    original_schedule = midscene.schedule_sonic_suite_summary
    original_log = midscene.append_sonic_notify_log
    original_app = midscene.sonic_suite_app_for_completion
    original_unique = midscene.unique_millis_id
    try:
        midscene.load_sonic_suite_results = lambda: state
        midscene.save_sonic_suite_results = lambda payload: state.update(payload)
        midscene.schedule_sonic_suite_summary = lambda suite_key, delay=None: scheduled.append((suite_key, delay))
        midscene.append_sonic_notify_log = lambda *args, **kwargs: None
        midscene.sonic_suite_app_for_completion = lambda event: {"package": "com.kfb.model", "name": "3D 打印"}
        midscene.unique_millis_id = lambda prefix: f"{prefix}_isolated"
        result = midscene.register_sonic_suite_completion({
            "suite_name": "3D测试自动",
            "status": "success",
            "passed": 1,
            "failed": 0,
            "warning": 0,
            "total": 1,
        })
    finally:
        midscene.load_sonic_suite_results = original_load
        midscene.save_sonic_suite_results = original_save
        midscene.schedule_sonic_suite_summary = original_schedule
        midscene.append_sonic_notify_log = original_log
        midscene.sonic_suite_app_for_completion = original_app
        midscene.unique_millis_id = original_unique

    assert result["suite_key"] == "sonic_suite_isolated"
    assert state["suites"]["old-case-bucket"]["results"][0]["target_task_name"] == "历史用例"
    assert state["suites"]["sonic_suite_isolated"]["results"] == []
    assert scheduled == [("sonic_suite_isolated", 5)]


def test_completion_without_result_id_attaches_active_current_suite():
    now = int(time.time())
    state = {
        "active": {"com.kfb.model|||3D测试自动||sonic|UQG": "active-suite"},
        "suites": {
            "active-suite": {
                "suite_key": "active-suite",
                "app_package": "com.kfb.model",
                "sonic_suite_name": "3D测试自动",
                "last_update_ts": now,
                "created_ts": now - 30,
                "results": [{"job_id": "sonic-job-1", "status": "success", "target_task_name": "用例A"}],
            },
        },
    }
    scheduled = []
    original_load = midscene.load_sonic_suite_results
    original_save = midscene.save_sonic_suite_results
    original_schedule = midscene.schedule_sonic_suite_summary
    original_log = midscene.append_sonic_notify_log
    original_app = midscene.sonic_suite_app_for_completion
    try:
        midscene.load_sonic_suite_results = lambda: state
        midscene.save_sonic_suite_results = lambda payload: state.update(payload)
        midscene.schedule_sonic_suite_summary = lambda suite_key, delay=None: scheduled.append((suite_key, delay))
        midscene.append_sonic_notify_log = lambda *args, **kwargs: None
        midscene.sonic_suite_app_for_completion = lambda event: {"package": "com.kfb.model", "name": "3D 打印"}
        result = midscene.register_sonic_suite_completion({
            "app_package": "com.kfb.model",
            "suite_name": "3D测试自动",
            "status": "success",
            "passed": 1,
            "failed": 0,
            "warning": 0,
            "total": 1,
        })
    finally:
        midscene.load_sonic_suite_results = original_load
        midscene.save_sonic_suite_results = original_save
        midscene.schedule_sonic_suite_summary = original_schedule
        midscene.append_sonic_notify_log = original_log
        midscene.sonic_suite_app_for_completion = original_app

    assert result["suite_key"] == "active-suite"
    assert state["suites"]["active-suite"]["completion_received"] is True
    assert len(state["suites"]["active-suite"]["results"]) == 1
    assert scheduled == [("active-suite", 5)]


def test_suite_completion_fallback_does_not_resend_for_late_case_results():
    now = int(time.time())
    state = {
        "active": {},
        "suites": {
            "suite-timeout-sent": {
                "suite_key": "suite-timeout-sent",
                "source": "sonic",
                "run_mode": "baseline",
                "app_package": "com.kfb.model",
                "app": {"package": "com.kfb.model", "name": "3D 打印"},
                "created_ts": now - midscene.sonic_suite_max_wait_seconds() - 60,
                "last_update_ts": now - 5,
                "sent_at": "2026-05-27 07:11:00",
                "sent_count": 2,
                "send_error": "",
                "completion_final_sent": True,
                "results": [
                    {"job_id": "sonic_job_1", "status": "success"},
                    {"job_id": "sonic_job_2", "status": "success"},
                    {"job_id": "sonic_job_3", "status": "success"},
                ],
                "notification_mode": "suite_completion",
            }
        },
    }
    sent = []
    logged = []
    original_flag = midscene.SONIC_NOTIFY_ON_SUITE_COMPLETION_ONLY
    original_load = midscene.load_sonic_suite_results
    original_post = midscene.post_feishu_card
    original_log = midscene.append_sonic_notify_log
    try:
        midscene.SONIC_NOTIFY_ON_SUITE_COMPLETION_ONLY = True
        midscene.load_sonic_suite_results = lambda: state
        midscene.post_feishu_card = lambda webhook, card: sent.append((webhook, card)) or {"ok": True}
        midscene.append_sonic_notify_log = lambda event, *args, **kwargs: logged.append(event)
        midscene.send_sonic_suite_summary_if_quiet("suite-timeout-sent")
    finally:
        midscene.SONIC_NOTIFY_ON_SUITE_COMPLETION_ONLY = original_flag
        midscene.load_sonic_suite_results = original_load
        midscene.post_feishu_card = original_post
        midscene.append_sonic_notify_log = original_log
        midscene.SONIC_SUITE_TIMERS.pop("suite-timeout-sent", None)

    assert sent == []
    assert logged == ["suite_summary_already_sent_final_refresh_only"]


def test_finished_result_api_completion_sends_once():
    state = {
        "active": {},
        "suites": {
            "suite-old-inference": {
                "suite_key": "suite-old-inference",
                "source": "sonic",
                "run_mode": "baseline",
                "app_package": "com.kfb.model",
                "app": {"package": "com.kfb.model", "name": "3D 打印"},
                "results": [{"job_id": "sonic_job_1", "status": "success"}],
            }
        },
    }
    sent = []
    logged = []
    original_load = midscene.load_sonic_suite_results
    original_save = midscene.save_sonic_suite_results
    original_webhook = midscene.task_app_feishu_webhook
    original_post = midscene.post_feishu_card
    original_report = midscene.write_sonic_suite_summary_report
    original_attach_definition = midscene.attach_sonic_suite_definition_from_api
    original_attach_meta = midscene.attach_sonic_result_meta_from_api
    original_attach_report = midscene.attach_sonic_report_from_api
    original_log = midscene.append_sonic_notify_log
    try:
        midscene.load_sonic_suite_results = lambda: state
        midscene.save_sonic_suite_results = lambda payload: state.update(payload)
        midscene.task_app_feishu_webhook = lambda app: "https://open.feishu.cn/open-apis/bot/v2/hook/example"
        midscene.post_feishu_card = lambda webhook, card: sent.append((webhook, card)) or {"ok": True}
        midscene.write_sonic_suite_summary_report = lambda suite: "http://task/reports/sonic_result_3_631-summary.html"
        midscene.attach_sonic_suite_definition_from_api = lambda suite_key, suite: suite

        def attach_meta(suite_key, suite):
            suite["sonic_result_meta"] = {
                "project_id": 3,
                "result_id": 631,
                "send_msg_count": 2,
                "receive_msg_count": 2,
                "expected_total_count": 2,
                "sonic_report_url": "http://sonic/Home/3/ResultDetail/631",
                "status": 1,
                "status_text": "success",
                "finished": True,
            }
            suite["expected_total_count"] = 2
            return suite

        midscene.attach_sonic_result_meta_from_api = attach_meta
        midscene.attach_sonic_report_from_api = lambda suite_key, suite: suite
        midscene.append_sonic_notify_log = lambda event, *args, **kwargs: logged.append(event)
        midscene.send_sonic_suite_summary_if_quiet("suite-old-inference")
        midscene.send_sonic_suite_summary_if_quiet("sonic_result_3_631")
    finally:
        midscene.load_sonic_suite_results = original_load
        midscene.save_sonic_suite_results = original_save
        midscene.task_app_feishu_webhook = original_webhook
        midscene.post_feishu_card = original_post
        midscene.write_sonic_suite_summary_report = original_report
        midscene.attach_sonic_suite_definition_from_api = original_attach_definition
        midscene.attach_sonic_result_meta_from_api = original_attach_meta
        midscene.attach_sonic_report_from_api = original_attach_report
        midscene.append_sonic_notify_log = original_log
        midscene.SONIC_SUITE_TIMERS.pop("suite-old-inference", None)
        midscene.SONIC_SUITE_TIMERS.pop("sonic_result_3_631", None)

    assert len(sent) == 1
    assert "suite-old-inference" not in state["suites"]
    suite = state["suites"]["sonic_result_3_631"]
    assert suite["completion_source"] == "sonic_results_api"
    assert suite["completion_final_sent"] is True
    assert suite["sent_count"] == 1
    assert logged.count("suite_summary_sent") == 1
    assert logged[-1] == "suite_summary_already_sent_final_refresh_only"


def test_legacy_mixed_completion_is_not_sent_after_upgrade():
    state = {
        "active": {},
        "suites": {
            "historical-case-bucket": {
                "suite_key": "historical-case-bucket",
                "completion_received": True,
                "completion_source": "sonic_callback",
                "sonic_project_id": 3,
                "sonic_result_id": 632,
                "app_package": "com.kfb.model",
                "results": [{"job_id": "old-job", "status": "success"}],
            },
        },
    }
    sent = []
    logged = []
    original_load = midscene.load_sonic_suite_results
    original_save = midscene.save_sonic_suite_results
    original_post = midscene.post_feishu_card
    original_log = midscene.append_sonic_notify_log
    try:
        midscene.load_sonic_suite_results = lambda: state
        midscene.save_sonic_suite_results = lambda payload: state.update(payload)
        midscene.post_feishu_card = lambda webhook, card: sent.append(card) or {"ok": True}
        midscene.append_sonic_notify_log = lambda event, *args, **kwargs: logged.append(event)
        midscene.send_sonic_suite_summary_if_quiet("historical-case-bucket")
    finally:
        midscene.load_sonic_suite_results = original_load
        midscene.save_sonic_suite_results = original_save
        midscene.post_feishu_card = original_post
        midscene.append_sonic_notify_log = original_log
        midscene.SONIC_SUITE_TIMERS.pop("historical-case-bucket", None)

    assert sent == []
    assert logged == ["suite_summary_legacy_mixed_completion_suppressed"]
    assert state["suites"]["historical-case-bucket"]["notification_suppressed_reason"]


def test_suite_summary_send_claim_blocks_concurrent_duplicate_notification():
    state = {
        "active": {},
        "suites": {
            "suite-final": {
                "suite_key": "suite-final",
                "source": "sonic",
                "run_mode": "baseline",
                "completion_received": True,
                "app_package": "com.kfb.model",
                "app": {"package": "com.kfb.model", "name": "3D 打印"},
                "results": [{"job_id": "sonic_job_1", "status": "success"}],
            }
        },
    }
    sent = []
    logged = []
    original_load = midscene.load_sonic_suite_results
    original_save = midscene.save_sonic_suite_results
    original_webhook = midscene.task_app_feishu_webhook
    original_definition = midscene.attach_sonic_suite_definition_from_api
    original_result_meta = midscene.attach_sonic_result_meta_from_api
    original_report_meta = midscene.attach_sonic_report_from_api
    original_write = midscene.write_sonic_suite_summary_report
    original_post = midscene.post_feishu_card
    original_log = midscene.append_sonic_notify_log
    try:
        midscene.load_sonic_suite_results = lambda: state
        midscene.save_sonic_suite_results = lambda payload: state.update(payload)
        midscene.task_app_feishu_webhook = lambda app: "https://open.feishu.cn/open-apis/bot/v2/hook/example"
        midscene.attach_sonic_suite_definition_from_api = lambda suite_key, suite: suite
        midscene.attach_sonic_result_meta_from_api = lambda suite_key, suite: suite
        midscene.attach_sonic_report_from_api = lambda suite_key, suite: suite
        midscene.write_sonic_suite_summary_report = lambda suite: "http://task/reports/suite-final.html"

        def post_once(webhook, card):
            sent.append((webhook, card))
            midscene.send_sonic_suite_summary_if_quiet("suite-final")
            return {"ok": True}

        midscene.post_feishu_card = post_once
        midscene.append_sonic_notify_log = lambda event, *args, **kwargs: logged.append(event)
        midscene.send_sonic_suite_summary_if_quiet("suite-final")
    finally:
        midscene.load_sonic_suite_results = original_load
        midscene.save_sonic_suite_results = original_save
        midscene.task_app_feishu_webhook = original_webhook
        midscene.attach_sonic_suite_definition_from_api = original_definition
        midscene.attach_sonic_result_meta_from_api = original_result_meta
        midscene.attach_sonic_report_from_api = original_report_meta
        midscene.write_sonic_suite_summary_report = original_write
        midscene.post_feishu_card = original_post
        midscene.append_sonic_notify_log = original_log
        midscene.SONIC_SUITE_TIMERS.pop("suite-final", None)

    assert len(sent) == 1
    assert "suite_summary_send_already_in_progress" in logged
    assert state["suites"]["suite-final"]["send_in_progress"] is False
    assert state["suites"]["suite-final"]["sent_at"]


def test_suite_summary_waiting_for_expected_results_releases_send_claim():
    state = {
        "active": {},
        "suites": {
            "suite-wait-more": {
                "suite_key": "suite-wait-more",
                "source": "sonic",
                "run_mode": "baseline",
                "completion_received": True,
                "created_ts": int(time.time()),
                "app_package": "com.kfb.model",
                "app": {"package": "com.kfb.model", "name": "3D 打印"},
                "results": [{"job_id": "sonic_job_1", "status": "success"}],
            }
        },
    }
    scheduled = []
    sent = []
    original_load = midscene.load_sonic_suite_results
    original_save = midscene.save_sonic_suite_results
    original_webhook = midscene.task_app_feishu_webhook
    original_definition = midscene.attach_sonic_suite_definition_from_api
    original_result_meta = midscene.attach_sonic_result_meta_from_api
    original_schedule = midscene.schedule_sonic_suite_summary
    original_post = midscene.post_feishu_card
    original_log = midscene.append_sonic_notify_log
    try:
        midscene.load_sonic_suite_results = lambda: state
        midscene.save_sonic_suite_results = lambda payload: state.update(payload)
        midscene.task_app_feishu_webhook = lambda app: "https://open.feishu.cn/open-apis/bot/v2/hook/example"

        def attach_definition(suite_key, suite):
            suite["expected_total_count"] = 2
            return suite

        midscene.attach_sonic_suite_definition_from_api = attach_definition
        midscene.attach_sonic_result_meta_from_api = lambda suite_key, suite: suite
        midscene.schedule_sonic_suite_summary = lambda suite_key, delay=None: scheduled.append(suite_key)
        midscene.post_feishu_card = lambda webhook, card: sent.append(card) or {"ok": True}
        midscene.append_sonic_notify_log = lambda *args, **kwargs: None
        midscene.send_sonic_suite_summary_if_quiet("suite-wait-more")
    finally:
        midscene.load_sonic_suite_results = original_load
        midscene.save_sonic_suite_results = original_save
        midscene.task_app_feishu_webhook = original_webhook
        midscene.attach_sonic_suite_definition_from_api = original_definition
        midscene.attach_sonic_result_meta_from_api = original_result_meta
        midscene.schedule_sonic_suite_summary = original_schedule
        midscene.post_feishu_card = original_post
        midscene.append_sonic_notify_log = original_log
        midscene.SONIC_SUITE_TIMERS.pop("suite-wait-more", None)

    suite = state["suites"]["suite-wait-more"]
    assert sent == []
    assert scheduled == ["suite-wait-more"]
    assert suite["send_in_progress"] is False
    assert suite["send_started_ts"] == 0


def test_pending_suite_summary_timers_restore_on_server_startup():
    state = {
        "active": {},
        "suites": {
            "waiting-suite": {
                "suite_key": "waiting-suite",
                "source": "sonic",
                "run_mode": "baseline",
                "results": [{"job_id": "sonic_job_1", "status": "success"}],
                "notification_mode": "suite_completion",
                "send_in_progress": True,
                "send_started_ts": int(time.time()),
            },
            "already-sent": {
                "suite_key": "already-sent",
                "results": [{"job_id": "sonic_job_2", "status": "success"}],
                "sent_at": "2026-05-26 10:00:00",
                "send_error": "",
            },
            "empty-suite": {
                "suite_key": "empty-suite",
                "results": [],
            },
        },
    }
    scheduled = []
    logged = []
    original_load = midscene.load_sonic_suite_results
    original_save = midscene.save_sonic_suite_results
    original_schedule = midscene.schedule_sonic_suite_summary
    original_log = midscene.append_sonic_notify_log
    try:
        midscene.load_sonic_suite_results = lambda: state
        midscene.save_sonic_suite_results = lambda payload: state.update(payload)
        midscene.schedule_sonic_suite_summary = lambda suite_key, delay=None: scheduled.append((suite_key, delay))
        midscene.append_sonic_notify_log = lambda event, payload=None, **kwargs: logged.append((event, payload))
        midscene.restore_pending_sonic_suite_summary_timers()
    finally:
        midscene.load_sonic_suite_results = original_load
        midscene.save_sonic_suite_results = original_save
        midscene.schedule_sonic_suite_summary = original_schedule
        midscene.append_sonic_notify_log = original_log

    assert scheduled == [("waiting-suite", 5)]
    assert state["suites"]["waiting-suite"]["send_in_progress"] is False
    assert state["suites"]["waiting-suite"]["send_started_ts"] == 0
    assert logged and logged[0][0] == "suite_summary_timers_restored"


def test_late_case_result_after_final_summary_starts_new_suite_bucket():
    state = {
        "active": {
            "com.kfb.model||||sonic|device-1|baseline": "suite-refresh"
        },
        "suites": {
            "suite-refresh": {
                "suite_key": "suite-refresh",
                "source": "sonic",
                "run_mode": "baseline",
                "app_package": "com.kfb.model",
                "runner_id": "sonic",
                "device_id": "device-1",
                "suite_report_url": "http://task/reports/suite-refresh-summary.html",
                "sent_at": "2026-05-27 07:11:00",
                "send_error": "",
                "completion_timeout": True,
                "results": [{"job_id": "sonic_job_1", "status": "success"}],
            }
        },
    }
    scheduled = []
    summary_refreshes = []
    original_flag = midscene.SONIC_NOTIFY_ON_SUITE_COMPLETION_ONLY
    original_load = midscene.load_sonic_suite_results
    original_save = midscene.save_sonic_suite_results
    original_schedule = midscene.schedule_sonic_suite_summary
    original_write_summary = midscene.write_sonic_suite_summary_report
    original_log = midscene.append_sonic_notify_log
    original_app = midscene.sonic_suite_app_info
    try:
        midscene.SONIC_NOTIFY_ON_SUITE_COMPLETION_ONLY = True
        midscene.load_sonic_suite_results = lambda: state
        midscene.save_sonic_suite_results = lambda payload: state.update(payload)
        midscene.schedule_sonic_suite_summary = lambda suite_key, delay=None: scheduled.append((suite_key, delay))
        midscene.write_sonic_suite_summary_report = lambda suite: summary_refreshes.append((suite["suite_key"], len(suite.get("results") or []))) or suite.get("suite_report_url", "")
        midscene.append_sonic_notify_log = lambda *args, **kwargs: None
        midscene.sonic_suite_app_info = lambda package="", module="": {"package": "com.kfb.model", "name": "3D 打印"}
        suite_key = midscene.register_sonic_suite_result({
            "source": "sonic",
            "status": "success",
            "run_mode": "baseline",
            "job_id": "sonic_job_2",
            "module": "3D打印基线",
            "file": "case2.yaml",
            "app_package": "com.kfb.model",
            "device_id": "device-1",
            "runner_id": "sonic",
        })
    finally:
        midscene.SONIC_NOTIFY_ON_SUITE_COMPLETION_ONLY = original_flag
        midscene.load_sonic_suite_results = original_load
        midscene.save_sonic_suite_results = original_save
        midscene.schedule_sonic_suite_summary = original_schedule
        midscene.write_sonic_suite_summary_report = original_write_summary
        midscene.append_sonic_notify_log = original_log
        midscene.sonic_suite_app_info = original_app

    assert suite_key != "suite-refresh"
    assert summary_refreshes == []
    assert len(state["suites"]["suite-refresh"]["results"]) == 1
    assert len(state["suites"][suite_key]["results"]) == 1
    assert scheduled and scheduled[0][0] == suite_key


def test_sent_suite_completion_summary_is_never_resent_for_late_results():
    state = {
        "active": {},
        "suites": {
            "sonic_result_3_631": {
                "suite_key": "sonic_result_3_631",
                "source": "sonic",
                "run_mode": "baseline",
                "notification_mode": "suite_completion",
                "completion_received": True,
                "sent_at": "2026-05-27 17:10:00",
                "sent_count": 3,
                "send_error": "",
                "app_package": "com.kfb.model",
                "app": {"package": "com.kfb.model", "name": "智小白3D"},
                "results": [
                    {"job_id": "sonic_job_1", "status": "success"},
                    {"job_id": "sonic_job_2", "status": "success"},
                    {"job_id": "sonic_job_3", "status": "success"},
                    {"job_id": "sonic_job_4", "status": "success"},
                ],
            }
        },
    }
    sent = []
    logged = []
    original_load = midscene.load_sonic_suite_results
    original_post = midscene.post_feishu_card
    original_log = midscene.append_sonic_notify_log
    try:
        midscene.load_sonic_suite_results = lambda: state
        midscene.post_feishu_card = lambda webhook, card: sent.append(card) or {"ok": True}
        midscene.append_sonic_notify_log = lambda event, *args, **kwargs: logged.append(event)
        midscene.send_sonic_suite_summary_if_quiet("sonic_result_3_631")
    finally:
        midscene.load_sonic_suite_results = original_load
        midscene.post_feishu_card = original_post
        midscene.append_sonic_notify_log = original_log
        midscene.SONIC_SUITE_TIMERS.pop("sonic_result_3_631", None)

    assert sent == []
    assert logged == ["suite_summary_already_sent_final_refresh_only"]


def test_background_midscene_report_attaches_without_creating_new_suite_result():
    jobs = [{
        "job_id": "sonic_job_1",
        "module": "3D打印基线",
        "file": "case.yaml",
        "sonic_suite_key": "suite-1",
        "report_upload_pending": True,
    }]
    suites = {
        "active": {},
        "suites": {
            "suite-1": {
                "suite_key": "suite-1",
                "suite_report_url": "http://task/reports/suite-1-summary.html",
                "results": [{"job_id": "sonic_job_1", "report_url": "", "report_upload_pending": True}],
            }
        },
    }
    summary_refreshes = []
    original_load_jobs = midscene.load_jobs
    original_save_jobs = midscene.save_jobs
    original_update_meta = midscene.update_task_meta
    original_load_suites = midscene.load_sonic_suite_results
    original_save_suites = midscene.save_sonic_suite_results
    original_write_summary = midscene.write_sonic_suite_summary_report
    original_log = midscene.append_sonic_notify_log
    try:
        midscene.load_jobs = lambda: jobs
        midscene.save_jobs = lambda payload: jobs.__setitem__(slice(None), payload)
        midscene.update_task_meta = lambda *args, **kwargs: None
        midscene.load_sonic_suite_results = lambda: suites
        midscene.save_sonic_suite_results = lambda payload: suites.update(payload)
        midscene.write_sonic_suite_summary_report = lambda suite: summary_refreshes.append(suite["suite_key"]) or "report"
        midscene.append_sonic_notify_log = lambda *args, **kwargs: None
        updated = midscene.attach_sonic_background_report(
            "sonic_job_1",
            "http://task/reports/case.html",
            r"D:\sonic\case.html",
            ""
        )
    finally:
        midscene.load_jobs = original_load_jobs
        midscene.save_jobs = original_save_jobs
        midscene.update_task_meta = original_update_meta
        midscene.load_sonic_suite_results = original_load_suites
        midscene.save_sonic_suite_results = original_save_suites
        midscene.write_sonic_suite_summary_report = original_write_summary
        midscene.append_sonic_notify_log = original_log

    assert updated["report_url"] == "http://task/reports/case.html"
    assert updated["report_upload_pending"] is False
    assert len(suites["suites"]["suite-1"]["results"]) == 1
    assert suites["suites"]["suite-1"]["results"][0]["report_url"] == "http://task/reports/case.html"
    assert summary_refreshes == ["suite-1"]


def test_suite_summary_waits_briefly_for_background_midscene_reports():
    state = {
        "active": {},
        "suites": {
            "suite-pending": {
                "suite_key": "suite-pending",
                "completion_received": True,
                "completion_ts": int(time.time()),
                "results": [{"job_id": "sonic_job_1", "report_upload_pending": True}],
            }
        },
    }
    scheduled = []
    logged = []
    original_load = midscene.load_sonic_suite_results
    original_timer = midscene.threading.Timer
    original_log = midscene.append_sonic_notify_log

    class DummyTimer:
        def __init__(self, delay, fn, args=()):
            scheduled.append(delay)
            self.daemon = False

        def start(self):
            pass

    try:
        midscene.load_sonic_suite_results = lambda: state
        midscene.threading.Timer = DummyTimer
        midscene.append_sonic_notify_log = lambda event, *args, **kwargs: logged.append(event)
        midscene.send_sonic_suite_summary_if_quiet("suite-pending")
    finally:
        midscene.load_sonic_suite_results = original_load
        midscene.threading.Timer = original_timer
        midscene.append_sonic_notify_log = original_log
        midscene.SONIC_SUITE_TIMERS.pop("suite-pending", None)

    assert scheduled == [midscene.sonic_midscene_report_check_delay_seconds()]
    assert logged == ["suite_summary_wait_midscene_reports"]
    card_text = str(midscene.build_sonic_suite_summary_card(state["suites"]["suite-pending"]))
    assert "后台上传" in card_text


def test_suite_report_keeps_reserved_midscene_link_visible_while_uploading():
    original_report_dir = midscene.REPORT_DIR
    try:
        with tempfile.TemporaryDirectory() as tempdir:
            midscene.REPORT_DIR = tempdir
            midscene.write_sonic_suite_summary_report({
                "suite_key": "suite-pending-link",
                "app_package": "com.kfb.model",
                "results": [{
                    "status": "success",
                    "module": "3D打印基线",
                    "file": "case.yaml",
                    "report_url": "http://task/reports/case.html",
                    "report_upload_pending": True,
                }],
            })
            content = (Path(tempdir) / "suite-pending-link-summary.html").read_text(encoding="utf-8")
    finally:
        midscene.REPORT_DIR = original_report_dir

    assert 'href="http://task/reports/case.html"' in content
    assert "Midscene 报告（上传中）" in content


def test_automatic_baseline_repair_is_off_by_default():
    original = midscene.ENABLE_AUTOMATIC_BASELINE_REPAIR
    try:
        midscene.ENABLE_AUTOMATIC_BASELINE_REPAIR = False
        assert midscene.automatic_baseline_repair_enabled(True) is False
    finally:
        midscene.ENABLE_AUTOMATIC_BASELINE_REPAIR = original


def test_completed_suite_is_not_reopened_by_later_run():
    job = {
        "source": "sonic",
        "app_package": "com.kfb.model",
        "sonic_suite_id": "46",
        "sonic_suite_name": "3DUI基线测试",
        "runner_id": "sonic",
        "device_id": "device-1",
        "run_mode": "baseline",
    }
    natural_key = midscene.sonic_suite_natural_key(job)
    state = {
        "active": {natural_key: "old-suite"},
        "suites": {
            "old-suite": {
                "sent_at": "2026-05-25 10:00:00",
                "created_ts": 1000,
                "last_update_ts": 1000,
            }
        }
    }
    assert midscene.sonic_suite_key_for_job(job, state, 1001) != "old-suite"


def test_sync_case_adds_managed_case_to_bound_suite():
    calls = []
    original_request = midscene.sonic_request

    def fake_request(method, path, params=None, body=None, timeout=0):
        calls.append((method, path, body))
        if method == "GET" and path == "/testSuites":
            return {
                "code": 2000,
                "data": {
                    "id": 46,
                    "name": "3DUI基线测试",
                    "projectId": 3,
                    "testCases": [{"id": 100, "name": "已有用例"}],
                    "devices": [{"id": 1}],
                },
            }
        if method == "PUT" and path == "/testSuites":
            return {"code": 2000, "data": body}
        raise AssertionError(f"unexpected Sonic request: {method} {path}")

    try:
        midscene.sonic_request = fake_request
        result = midscene.sonic_sync_case_to_configured_suite(
            {"sonic_suite_id": "46", "sonic_suite_name": "3DUI基线测试"},
            3,
            {"id": 101, "name": "新增用例"},
        )
    finally:
        midscene.sonic_request = original_request

    put_body = next(body for method, path, body in calls if method == "PUT" and path == "/testSuites")
    assert result["state"] == "linked"
    assert result["case_count"] == 2
    assert [row["id"] for row in put_body["testCases"]] == [100, 101]
    assert put_body["devices"] == [{"id": 1}]


def test_app_binding_resolves_suite_name_to_stable_ids():
    original_find_project_id = midscene.sonic_find_project_id
    original_list_projects = midscene.sonic_list_projects
    original_request = midscene.sonic_request

    try:
        midscene.sonic_find_project_id = lambda app: 3
        midscene.sonic_list_projects = lambda: [{"id": 3, "projectName": "3D 打印"}]
        midscene.sonic_request = lambda method, path, params=None, body=None, timeout=0: {
            "code": 2000,
            "data": [{"id": 46, "projectId": 3, "name": "每日基线", "testCases": [{"id": 100}, {"id": 101}]}],
        }
        bound = midscene.resolve_task_app_sonic_binding({
            "package": "com.kfb.model",
            "sonic_project_name": "3D 打印",
            "sonic_suite_name": "每日基线",
        })
    finally:
        midscene.sonic_find_project_id = original_find_project_id
        midscene.sonic_list_projects = original_list_projects
        midscene.sonic_request = original_request

    assert bound["sonic_project_id"] == "3"
    assert bound["sonic_suite_id"] == "46"
    assert bound["sonic_suite_case_count"] == 2


def test_app_binding_rejects_suite_from_another_project():
    original_find_project_id = midscene.sonic_find_project_id
    original_list_projects = midscene.sonic_list_projects
    original_request = midscene.sonic_request

    try:
        midscene.sonic_find_project_id = lambda app: 3
        midscene.sonic_list_projects = lambda: [{"id": 3, "projectName": "3D 打印"}]
        midscene.sonic_request = lambda method, path, params=None, body=None, timeout=0: {
            "code": 2000,
            "data": {"id": 46, "projectId": 7, "name": "错误项目的基线", "testCases": []},
        }
        try:
            midscene.resolve_task_app_sonic_binding({
                "package": "com.kfb.model",
                "sonic_project_id": "3",
                "sonic_suite_id": "46",
            })
            raised = False
        except ValueError:
            raised = True
    finally:
        midscene.sonic_find_project_id = original_find_project_id
        midscene.sonic_list_projects = original_list_projects
        midscene.sonic_request = original_request

    assert raised


def test_configured_login_is_preferred_over_legacy_static_token():
    original_login = midscene.sonic_login_token
    original_cache = midscene.sonic_cached_token
    original_env = {key: os.environ.get(key) for key in ("SONIC_USERNAME", "SONIC_PASSWORD", "SONIC_TOKEN")}
    try:
        os.environ["SONIC_USERNAME"] = "configured-user"
        os.environ["SONIC_PASSWORD"] = "configured-password"
        os.environ["SONIC_TOKEN"] = "legacy-static-token"
        midscene.sonic_cached_token = lambda expected_username="": ""
        midscene.sonic_login_token = lambda: "fresh-login-token"
        assert midscene.sonic_token() == "fresh-login-token"
        assert midscene.sonic_token_source() == "login"
    finally:
        midscene.sonic_login_token = original_login
        midscene.sonic_cached_token = original_cache
        for key, value in original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_failed_auto_login_can_fall_back_to_legacy_token_and_reports_error():
    original_login = midscene.sonic_login_token
    original_cache = midscene.sonic_cached_token
    original_notify_log = midscene.append_sonic_notify_log
    original_env = {key: os.environ.get(key) for key in ("SONIC_USERNAME", "SONIC_PASSWORD", "SONIC_TOKEN")}
    original_state = dict(midscene.SONIC_LOGIN_STATE)
    try:
        os.environ["SONIC_USERNAME"] = "configured-user"
        os.environ["SONIC_PASSWORD"] = "configured-password"
        os.environ["SONIC_TOKEN"] = "legacy-static-token"
        midscene.sonic_cached_token = lambda expected_username="": ""
        midscene.append_sonic_notify_log = lambda *args, **kwargs: None

        def fail_login():
            raise RuntimeError("login endpoint unavailable")

        midscene.sonic_login_token = fail_login
        assert midscene.sonic_token() == "legacy-static-token"
        preview = midscene.sonic_auth_preview()
        assert preview["preferred_source"] == "login"
        assert preview["active_source"] == "static_token_fallback"
        assert preview["login_ok"] is False
        assert "unavailable" in preview["login_error"]
    finally:
        midscene.sonic_login_token = original_login
        midscene.sonic_cached_token = original_cache
        midscene.append_sonic_notify_log = original_notify_log
        midscene.SONIC_LOGIN_STATE.update(original_state)
        for key, value in original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_preflight_does_not_treat_public_project_list_as_authenticated():
    original_token = midscene.sonic_token
    original_probe = midscene.sonic_probe_token
    try:
        midscene.sonic_token = lambda force_refresh=False: "some-token"
        midscene.sonic_probe_token = lambda: {
            "ok": False,
            "message": "unauthorized",
            "auth_status": "token_invalid",
        }
        dashboard = midscene.platform_preflight_dashboard()
    finally:
        midscene.sonic_token = original_token
        midscene.sonic_probe_token = original_probe
    sonic_check = next(item for item in dashboard["checks"] if item["key"] == "sonic")
    assert sonic_check["ok"] is False
    assert "unauthorized" in sonic_check["detail"]


def test_groovy_bridge_fetches_server_model_env_with_supported_string_syntax():
    bridge = (ROOT / "sonic-midscene-task-runner.groovy").read_text(encoding="utf-8")
    assert "/api/sonic/runtime-env" in bridge
    assert "模型配置来源" in bridge
    assert "replaceAll('/+$', '')" in bridge
    assert ".rstrip(" not in bridge


def test_groovy_baseline_result_log_does_not_mislabel_null_optimize_as_repair():
    bridge = (ROOT / "sonic-midscene-task-runner.groovy").read_text(encoding="utf-8")
    assert '"Task结果归档"' in bridge
    assert '"服务端修复状态"' not in bridge
    assert 'resp.body?.contains("\\\"optimize\\\"")' not in bridge
    assert "autoOptimize && optimize instanceof Map && optimize.ok == true && optimize.next_job" in bridge


def test_groovy_large_report_upload_skips_failed_full_upload_retry():
    bridge = (ROOT / "sonic-midscene-task-runner.groovy").read_text(encoding="utf-8")
    assert "directUploadLimit = 4 * 1024 * 1024" in bridge
    assert "reportFile.length() <= directUploadLimit" in bridge
    assert "chunkSize = 1024 * 1024" in bridge


def test_groovy_sonic_log_keeps_full_executed_yaml_and_compact_runtime_output():
    bridge = (ROOT / "sonic-midscene-task-runner.groovy").read_text(encoding="utf-8")
    assert '"YAML脚本（本次实际执行内容）", yamlContent' in bridge
    assert "yamlPreview" not in bridge
    assert "完整 YAML 请在 Task 平台查看" not in bridge
    assert bridge.index('yamlFile.setText(yamlContent, "UTF-8")') < bridge.index('"YAML脚本（本次实际执行内容）", yamlContent')
    assert "def outputSummary = compactLog(output" in bridge
    assert '"退出码：${exitCode} 状态：${statusText}\\n${outputSummary}"' in bridge


def test_groovy_bridge_report_times_are_explicitly_china_timezone():
    bridge = (ROOT / "sonic-midscene-task-runner.groovy").read_text(encoding="utf-8")
    assert 'java.util.TimeZone.getTimeZone("Asia/Shanghai")' in bridge
    assert 'formatBridgeTime("yyyy-MM-dd HH:mm:ss")' in bridge
    assert 'formatBridgeTime("yyyy-MM-dd_HH-mm-ss")' in bridge
    assert 'new java.text.SimpleDateFormat("yyyy-MM-dd HH:mm:ss").format(new Date())' not in bridge
    assert 'new java.text.SimpleDateFormat("yyyy-MM-dd_HH-mm-ss").format(new Date())' not in bridge


def test_groovy_runtime_progress_does_not_resend_growing_console_output():
    bridge = (ROOT / "sonic-midscene-task-runner.groovy").read_text(encoding="utf-8")
    assert 'def progressOutputTail = ""' in bridge
    assert "progressOutputTail = compactLog(progressOutputTail + line" in bridge
    assert "nowMs - lastProgressAt > 15000" in bridge
    assert "postProgressToTaskManager(progress, currentTaskName, currentTaskIndex, completedTaskCount, totalTaskCount, progressOutputTail" in bridge
    assert "postProgressToTaskManager(progress, currentTaskName, currentTaskIndex, completedTaskCount, totalTaskCount, outputBuffer.toString()" not in bridge


def test_sonic_bridge_scripts_can_be_refreshed_in_bulk():
    source = (ROOT / "task_server" / "services" / "sonic_service.py").read_text(encoding="utf-8")
    router = (ROOT / "task_server" / "router.py").read_text(encoding="utf-8")
    frontend = (ROOT / "js" / "agent-status.js").read_text(encoding="utf-8")
    assert "def sonic_refresh_bridge_scripts" in source
    assert "sonic_scan_midscene_cases(" in source
    assert "sonic_upsert_bridge_step(project_id, platform, sonic_case_id, case_id)" in source
    assert '"/api/sonic/refresh-bridges"' in router
    assert "refreshSonicBridgeScripts" in frontend
    assert "/sonic/refresh-bridges" in frontend


def test_groovy_network_callbacks_have_timeouts():
    bridge = (ROOT / "sonic-midscene-task-runner.groovy").read_text(encoding="utf-8")
    assert "curl --connect-timeout 3 --max-time 5" in bridge
    assert "curl --connect-timeout 5 --max-time 20" in bridge
    assert "curl --connect-timeout 5 --max-time 30 -L -f" in bridge
    assert "curl --connect-timeout 5 --max-time 45" in bridge
    assert "shell am force-stop io.appium.uiautomator2.server" in bridge
    assert "runCmd(adbCmd, 10)" in bridge
    assert "def runCmd = { String cmd, int timeoutSeconds = 0 ->" in bridge
    assert "command timeout after ${timeoutSeconds}s" in bridge


def test_groovy_case_handoff_avoids_duplicate_launch_and_fixed_waits():
    bridge = (ROOT / "sonic-midscene-task-runner.groovy").read_text(encoding="utf-8")
    reset_section = bridge[bridge.index("def resetForegroundApp"):bridge.index("def postResultToTaskManager")]
    restore_section = bridge[bridge.index("try {", bridge.index("postResultToTaskManager(statusValue")):]
    assert "shell am force-stop ${appPackage}" in reset_section
    assert "shell monkey -p ${appPackage}" not in reset_section
    assert "设备前置清理失败，命令未完成" in reset_section
    assert 'runCmd("${adbPath} -s ${deviceSerial} ${adbArg}", 8)' in reset_section
    assert "启动动作交由 YAML 执行" in bridge
    assert "APP前置重置失败" in bridge
    assert 'postResultToTaskManager("failed", 1, "", msg' in bridge
    assert "Thread.sleep(3000)" not in restore_section
    assert "Thread.sleep(2000)" not in restore_section
    assert "Thread.sleep(300)" in restore_section
    assert "衔接耗时：结果归档 ${archiveElapsedMs}ms，Driver 恢复 ${restoreElapsedMs}ms" in bridge
    assert "restoreSonicDriverWithTimeout(60)" in restore_section
    assert "Sonic Driver恢复中" in restore_section
    assert "Sonic Driver 恢复超过 ${timeoutSeconds} 秒未完成" in bridge
    assert "Sonic Driver恢复失败" in restore_section
    assert 'postResultToTaskManager("failed", 1, output, msg' in restore_section


def test_groovy_midscene_report_upload_runs_after_result_in_background():
    bridge = (ROOT / "sonic-midscene-task-runner.groovy").read_text(encoding="utf-8")
    result_pos = bridge.index("postResultToTaskManager(statusValue")
    background_pos = bridge.index('new Thread({', result_pos)
    upload_pos = bridge.index("uploadReportFile(asyncReportFile", background_pos)
    assert result_pos < background_pos < upload_pos
    assert "/api/sonic/report-ready" in bridge
    assert "不阻塞下一条用例" in bridge
    assert "uploadThread.setDaemon(true)" in bridge
    assert 'reportUrl = "${String.valueOf(taskServer).replaceAll(\'/+$\', \'\')}/reports/${encodeUrlPart(safeFileName)}"' in bridge
    assert '"report_upload_pending": ${localReportPath && !reportUploadError ? "true" : "false"}' in bridge
    assert "Midscene HTML 报告地址已预留，文件正在后台上传：${reportUrl}" in bridge
    assert "预留地址：${reportUrl}" in bridge


def test_groovy_default_does_not_force_kill_midscene_process():
    bridge = (ROOT / "sonic-midscene-task-runner.groovy").read_text(encoding="utf-8")
    main_section = bridge[bridge.index('def proc = pb.start()'):]
    assert "不设置外层 Midscene 进程超时" in bridge
    assert "midsceneTimeoutSeconds" not in bridge
    assert "MIDSCENE_RUN_TIMEOUT_SECONDS" not in bridge
    assert "MIDSCENE_TIMEOUT" not in bridge
    assert "proc.waitFor(midsceneTimeoutSeconds" not in main_section
    assert "proc.destroyForcibly()" not in main_section
    assert "Midscene超时" not in main_section
    assert "执行超过" not in main_section
    assert "proc.waitFor(600" not in main_section
    wait_pos = main_section.index("proc.waitFor()")
    exit_pos = main_section.index("def exitCode = proc.exitValue()", wait_pos)
    assert wait_pos < exit_pos
    assert "finished ? proc.exitValue()" not in main_section
    assert "def timeoutMessage" not in main_section


def test_desktop_runners_default_to_five_minute_midscene_timeout():
    for filename in ("mac-midscene-runner.py", "windows-midscene-runner.py"):
        runner = (ROOT / filename).read_text(encoding="utf-8")
        assert 'TIMEOUT_SECONDS = int(os.getenv("MIDSCENE_TIMEOUT", "300"))' in runner
        assert '"900"' not in runner


def test_desktop_runners_require_explicit_runner_token():
    for filename in ("mac-midscene-runner.py", "windows-midscene-runner.py"):
        runner = (ROOT / filename).read_text(encoding="utf-8")
        assert 'TOKEN = os.getenv("MIDSCENE_RUNNER_TOKEN", "").strip()' in runner
        assert 'validate_runner_config()' in runner
        assert "弱默认值" in runner
        assert 'os.getenv("MIDSCENE_RUNNER_TOKEN", "midscene2026")' not in runner


def test_sonic_groovy_bridge_requires_explicit_runner_token():
    bridge = (ROOT / "sonic-midscene-task-runner.groovy").read_text(encoding="utf-8")
    token_block = bridge[bridge.index("def runnerToken = firstValue(["):bridge.index("def runtimeEnvFetch")]
    assert '"midscene2026"' not in token_block
    assert "weakRunnerTokens" in token_block
    assert "重新同步 Sonic 桥接脚本" in token_block


def test_server_package_includes_desktop_runners():
    package_script = (ROOT / "deploy" / "package-server.sh").read_text(encoding="utf-8")
    install_script = (ROOT / "deploy" / "install-server.sh").read_text(encoding="utf-8")
    for filename in ("windows-midscene-runner.py", "mac-midscene-runner.py", "run-mac-midscene-runner.sh"):
        assert filename in package_script
        assert filename in install_script
    assert "sonic-midscene-task-runner.groovy" in package_script
    assert "sonic-midscene-task-runner.groovy" in install_script


def test_desktop_runners_queue_report_after_result_is_archived():
    for filename in ("mac-midscene-runner.py", "windows-midscene-runner.py"):
        runner = (ROOT / filename).read_text(encoding="utf-8")
        assert '"/api/sonic/runtime-env"' in runner
        assert "task_runtime_env()" in runner
        result_pos = runner.index('post_job_result(job["job_id"], result)')
        queue_pos = runner.index('enqueue_report_upload(job["job_id"], pending_report_path, pending_report_name)')
        assert result_pos < queue_pos
        assert 'f"/api/runner/jobs/{job_id}/report-ready"' in runner
        run_job_body = runner[runner.index("def run_job("):runner.index("def print_startup()")]
        assert "http_upload_report(report_path, report_name)" not in run_job_body
        assert '"report_upload_pending": bool(report_path)' in run_job_body
        assert 'report_url = SERVER.rstrip("/") + "/reports/" + urllib.parse.quote(report_name)' in run_job_body


def test_sonic_step_state_marks_bridge_and_legacy_steps_as_cleanup_required():
    state = midscene.sonic_step_state([
        {
            "id": 10,
            "stepType": "runScript",
            "sort": 1,
            "content": 'def taskServer = "http://task"; midscene "old.yaml"',
        },
        {
            "id": 11,
            "stepType": "runScript",
            "sort": 2,
            "content": "// Midscene Sonic Bridge - managed by Task Platform\n/api/sonic/bridge-groovy",
        },
    ])
    assert state["state"] == "mixed"
    assert state["step_count"] == 2
    assert state["bridge_count"] == 1
    assert state["legacy_count"] == 1
    assert "重新同步清理" in state["label"]


def test_sonic_step_state_marks_old_feishu_midscene_script_as_legacy():
    state = midscene.sonic_step_state([
        {
            "id": 12,
            "stepType": "runScript",
            "sort": 1,
            "content": (
                'def sendFeishu = { caseName -> "Midscene 自动化测试报告" }\n'
                'def feishuCmd = "curl -X POST https://open.feishu.cn/open-apis/bot/v2/hook/xxx"\n'
                'androidStepHandler.log.sendStepLog(2, "飞书通知", "已发送")'
            ),
        }
    ])
    assert state["state"] == "legacy"
    assert state["step_count"] == 1
    assert state["legacy_count"] == 1
    assert state["label"] == "旧模板脚本"


def test_syncing_sonic_case_keeps_one_bridge_and_deletes_old_midscene_steps():
    original_list_steps = midscene.sonic_list_steps
    original_request = midscene.sonic_request
    requests = []
    steps = [
        {
            "id": 20,
            "stepType": "runScript",
            "sort": 1,
            "content": 'def taskServer = "http://task"; midscene "old.yaml"',
        },
        {
            "id": 21,
            "stepType": "runScript",
            "sort": 2,
            "content": "// Midscene Sonic Bridge - managed by Task Platform\n/api/sonic/bridge-groovy",
        },
        {"id": 22, "stepType": "click", "sort": 3, "content": ""},
    ]
    try:
        midscene.sonic_list_steps = lambda sonic_case_id: list(steps)

        def fake_request(method, path, params=None, body=None, timeout=0):
            requests.append((method, path, params, body))
            if method == "DELETE" and path == "/steps":
                steps[:] = [item for item in steps if item.get("id") != params.get("id")]
            return {"code": 2000, "data": "ok"}

        midscene.sonic_request = fake_request
        result = midscene.sonic_upsert_bridge_step(3, 1, 100, "case-100")
    finally:
        midscene.sonic_list_steps = original_list_steps
        midscene.sonic_request = original_request

    put = next(item for item in requests if item[0] == "PUT" and item[1] == "/steps")
    deletes = [item for item in requests if item[0] == "DELETE" and item[1] == "/steps"]
    assert result["verified_state"] == "bridge"
    assert result["verified_step_count"] == 1
    assert put[3]["id"] == 21
    assert [item[2]["id"] for item in deletes] == [20]
    assert result["removed_step_ids"] == [20]
    assert result["cleaned_duplicate_steps"] == 1


def test_syncing_sonic_case_fails_if_legacy_step_remains_after_cleanup():
    original_list_steps = midscene.sonic_list_steps
    original_request = midscene.sonic_request
    try:
        midscene.sonic_list_steps = lambda sonic_case_id: [
            {
                "id": 20,
                "stepType": "runScript",
                "sort": 1,
                "content": 'def taskServer = "http://task"; midscene "old.yaml"',
            },
            {
                "id": 21,
                "stepType": "runScript",
                "sort": 2,
                "content": "// Midscene Sonic Bridge - managed by Task Platform\n/api/sonic/bridge-groovy",
            },
        ]
        midscene.sonic_request = lambda *args, **kwargs: {"code": 2000, "data": "ok"}
        try:
            midscene.sonic_upsert_bridge_step(3, 1, 100, "case-100")
            raise AssertionError("expected cleanup verification failure")
        except RuntimeError as exc:
            assert "同步后复检未通过" in str(exc)
    finally:
        midscene.sonic_list_steps = original_list_steps
        midscene.sonic_request = original_request


def test_generated_yaml_does_not_pause_after_every_business_action():
    _, yaml_text = midscene.cases_to_midscene_yaml({
        "_automation_ready": True,
        "title": "快速导航",
        "cases": [{
            "title": "快速导航",
            "app_package": "com.kfb.model",
            "steps": ["点击首页入口", "点击详情按钮"],
            "assertions": ["详情标题可见"],
            "preconditions": ["用户已登录"],
        }],
    })
    assert '- aiTap: "点击首页入口"\n      - aiTap: "点击详情按钮"' in yaml_text
    assert '- ai: "确认前置条件：用户已登录"\n      - sleep: 500' not in yaml_text
    assert '- aiAssert: "详情标题可见"\n      - sleep: 500' not in yaml_text
    assert midscene.validate_midscene_yaml(yaml_text)["ok"] is True


def test_search_entry_input_repair_keeps_search_icon_step():
    block = '''  - name: "搜索打印"
    flow:
      - aiAction: 右上角放大镜 搜索“关节龙”
      - aiTap: "关节龙"'''
    repaired, changes = midscene.normalize_input_actions_in_task_block(block)
    assert '- aiTap: "右上角放大镜搜索图标或搜索入口"' in repaired
    assert '- aiInput: "当前页面的搜索输入框或文本输入框"\n        value: "关节龙"' in repaired
    assert '- aiKeyboardPress: "当前页面的搜索输入框或文本输入框"\n        keyName: "Enter"' in repaired
    assert '- aiTap: "当前页面的搜索输入框或文本输入框"\n      - sleep: 200\n      - aiInput' not in repaired
    assert any("保留入口步骤" in item for item in changes)


def test_file_picker_search_input_repair_uses_precise_input_prompt():
    block = '''  - name: "模型导入-本地导入"
    flow:
      - aiTap: "本地导入"
      - aiTap: "右上角放大镜搜索图标"
      - aiInput: "当前页面的搜索输入框或文本输入框"
        value: ".stl"'''
    repaired, changes = midscene.normalize_search_input_submit_in_task_block(block)
    assert '- aiInput: "文件选择器顶部搜索输入框"\n        value: ".stl"' in repaired
    assert 'autoDismissKeyboard: false' in repaired
    assert 'mode: "replace"' in repaired
    assert '- aiKeyboardPress: "文件选择器顶部搜索输入框"\n        keyName: "Enter"' in repaired
    assert "当前页面的搜索输入框或文本输入框" not in repaired
    assert any("文件选择器搜索输入框" in item for item in changes)


def test_scroll_until_input_box_is_not_rewritten_as_input_action():
    block = '''  - name: "十二生肖印章打印"
    flow:
      - aiTap: 牛
      - sleep: 3000
      - aiAction: 在“底座样式”旁边的空白区域上滑（手指上划，让页面内容上移），直到出现“姓名”，能看到输入框；最多上滑5次
      - sleep: 1000
      - aiAction: 姓名输入框输入姓名：UI自动化'''
    repaired, changes = midscene.normalize_input_actions_in_task_block(block)
    assert 'aiAction: 在“底座样式”旁边的空白区域上滑' in repaired
    assert '- aiTap: "当前页面的搜索输入框或文本输入框"' not in repaired
    assert '- aiTap: "姓名输入框"' in repaired
    assert '- aiInput: "姓名输入框"\n        value: "UI自动化"' in repaired
    assert all("底座样式" not in item for item in changes)


def test_sonic_case_yaml_keeps_saved_steps_without_static_repair():
    yaml_text = '''android:

tasks:
  - name: 十二生肖印章打印
    flow:
      - aiTap: 牛
      - sleep: 3000
      - aiAction: 在“底座样式”旁边的空白区域上滑（手指上划，让页面内容上移），直到出现“姓名”，能看到输入框；最多上滑5次
      - sleep: 1000
      - aiAction: 姓名输入框输入姓名：UI自动化
      - sleep: 1000
  - name: 其他用例
    flow:
      - aiTap: 其他'''
    single = midscene.yaml_with_single_task(yaml_text, "十二生肖印章打印", app_package="com.kfb.model")
    assert "在“底座样式”旁边的空白区域上滑" in single
    assert "姓名输入框输入姓名：UI自动化" in single
    assert "当前页面的搜索输入框或文本输入框" not in single
    assert "其他用例" not in single


def test_groovy_bridge_does_not_inject_extra_yaml_steps_before_execution():
    bridge = (ROOT / "sonic-midscene-task-runner.groovy").read_text(encoding="utf-8")
    execution_area = bridge[bridge.index("if (yamlContent.contains(\"deviceId:\"))"):bridge.index("def appPackage = parseAppPackage")]
    assert "injectExternalPageEscape" not in execution_area


def test_static_speedup_removes_only_completed_short_waits():
    block = """  - name: "等待清理"
    flow:
      - aiTap: "进入详情"
      - sleep: 1000
      - aiWaitFor: "详情标题可见"
        timeout: 30000
      - sleep: 1000
      - aiAssert: "详情标题可见"
      - sleep: 500
      - ai: "确认前置条件：用户已登录"
      - sleep: 500
      - aiTap: "下一步"
      - sleep: 2000"""
    cleaned, changes = midscene.normalize_redundant_short_sleeps_in_task_block(block)
    assert '- aiTap: "进入详情"\n      - sleep: 1000' in cleaned
    assert '- aiWaitFor: "详情标题可见"\n        timeout: 30000\n      - sleep: 1000' not in cleaned
    assert '- aiAssert: "详情标题可见"\n      - sleep: 500' not in cleaned
    assert '- ai: "确认前置条件：用户已登录"\n      - sleep: 500' not in cleaned
    assert '- aiTap: "下一步"\n      - sleep: 2000' in cleaned
    assert len(changes) == 3


def test_waitfor_timeout_repair_is_capped_at_five_minutes():
    block = """  - name: "等待上限"
    flow:
      - aiWaitFor: "模型处理进度到 100%"
        timeout: 600000
      - aiTap: "确认打印"
      - sleep: 600000"""
    capped, cap_changes = midscene.normalize_waitfor_timeouts_in_task_block(block)
    assert "timeout: 300000" in capped
    assert "timeout: 600000" not in capped
    assert any("压到 300000ms" in item for item in cap_changes)

    converted, sleep_changes = midscene.normalize_long_sleep_waits_in_task_block(block)
    converted, _ = midscene.normalize_waitfor_timeouts_in_task_block(converted)
    assert "timeout: 300000" in converted
    assert "timeout: 600000" not in converted
    assert sleep_changes


if __name__ == "__main__":
    test_protected_env_file_supplies_runtime_env_without_overriding_process_values()
    test_runtime_env_file_rejects_overly_open_secret_permissions()
    test_runtime_env_file_rejects_smart_quotes_and_unclosed_values()
    test_task_server_runtime_env_service_has_required_imports()
    test_sonic_service_migrated_helpers_do_not_reference_legacy_globals()
    test_bridge_groovy_auth_failure_reason_accepts_token_or_session()
    test_sonic_suite_case_count_from_dto_shapes()
    test_sonic_suite_expected_total_uses_definition_and_result_meta()
    test_sonic_result_matching_prefers_exact_suite_id()
    test_sonic_failed_status_does_not_mean_suite_finished()
    test_sonic_suite_summary_card_marks_pending_as_warning()
    test_sonic_completed_suite_reports_missing_task_callbacks()
    test_suite_summary_report_renders_missing_task_callbacks_as_rows()
    test_sonic_result_id_derives_fixed_report_url_without_lookup()
    test_feishu_reason_does_not_emit_irrecoverable_mojibake()
    test_feishu_reason_recovers_reversible_utf8_as_gbk_text()
    test_feishu_webhook_rejects_multiline_export_pollution()
    test_feishu_webhook_rejects_smart_quotes()
    test_task_app_save_rejects_polluted_feishu_webhook()
    test_parse_manual_sonic_suite_completion_text_with_result_url()
    test_parse_interrupted_sonic_suite_completion_text()
    test_manual_sonic_completion_stats_work_without_bridge_results()
    test_interrupted_suite_card_is_labeled_interrupted()
    test_manual_sonic_completion_creates_summary_trigger_without_case_callback()
    test_failed_suite_notification_can_be_retried_for_same_sonic_result()
    test_baseline_sonic_case_results_wait_for_authoritative_suite_completion()
    test_suite_completion_mode_never_sends_partial_fallback()
    test_authoritative_completion_does_not_reuse_legacy_partial_summary()
    test_authoritative_result_id_does_not_attach_historical_case_bucket()
    test_completion_without_result_id_does_not_attach_historical_case_bucket()
    test_suite_completion_fallback_does_not_resend_for_late_case_results()
    test_finished_result_api_completion_sends_once()
    test_legacy_mixed_completion_is_not_sent_after_upgrade()
    test_suite_summary_send_claim_blocks_concurrent_duplicate_notification()
    test_suite_summary_waiting_for_expected_results_releases_send_claim()
    test_pending_suite_summary_timers_restore_on_server_startup()
    test_late_case_result_after_final_summary_starts_new_suite_bucket()
    test_background_midscene_report_attaches_without_creating_new_suite_result()
    test_suite_summary_waits_briefly_for_background_midscene_reports()
    test_suite_report_keeps_reserved_midscene_link_visible_while_uploading()
    test_automatic_baseline_repair_is_off_by_default()
    test_completed_suite_is_not_reopened_by_later_run()
    test_sync_case_adds_managed_case_to_bound_suite()
    test_app_binding_resolves_suite_name_to_stable_ids()
    test_app_binding_rejects_suite_from_another_project()
    test_configured_login_is_preferred_over_legacy_static_token()
    test_failed_auto_login_can_fall_back_to_legacy_token_and_reports_error()
    test_preflight_does_not_treat_public_project_list_as_authenticated()
    test_groovy_bridge_fetches_server_model_env_with_supported_string_syntax()
    test_groovy_baseline_result_log_does_not_mislabel_null_optimize_as_repair()
    test_groovy_large_report_upload_skips_failed_full_upload_retry()
    test_groovy_sonic_log_keeps_full_executed_yaml_and_compact_runtime_output()
    test_groovy_bridge_report_times_are_explicitly_china_timezone()
    test_groovy_runtime_progress_does_not_resend_growing_console_output()
    test_sonic_bridge_scripts_can_be_refreshed_in_bulk()
    test_groovy_network_callbacks_have_timeouts()
    test_groovy_case_handoff_avoids_duplicate_launch_and_fixed_waits()
    test_groovy_midscene_report_upload_runs_after_result_in_background()
    test_groovy_default_does_not_force_kill_midscene_process()
    test_desktop_runners_default_to_five_minute_midscene_timeout()
    test_desktop_runners_queue_report_after_result_is_archived()
    test_sonic_step_state_marks_bridge_and_legacy_steps_as_cleanup_required()
    test_sonic_step_state_marks_old_feishu_midscene_script_as_legacy()
    test_syncing_sonic_case_keeps_one_bridge_and_deletes_old_midscene_steps()
    test_syncing_sonic_case_fails_if_legacy_step_remains_after_cleanup()
    test_generated_yaml_does_not_pause_after_every_business_action()
    test_search_entry_input_repair_keeps_search_icon_step()
    test_file_picker_search_input_repair_uses_precise_input_prompt()
    test_scroll_until_input_box_is_not_rewritten_as_input_action()
    test_sonic_case_yaml_keeps_saved_steps_without_static_repair()
    test_groovy_bridge_does_not_inject_extra_yaml_steps_before_execution()
    test_static_speedup_removes_only_completed_short_waits()
    test_waitfor_timeout_repair_is_capped_at_five_minutes()
    print("sonic integration regression checks ok")
