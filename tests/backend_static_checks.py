#!/usr/bin/env python3
import importlib.util
import base64
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
ENTRY = ROOT / "midscene-upload.py"
MODULE = ROOT / "midscene_upload_compat.py"
NGINX_CONF = ROOT / "deploy" / "nginx-midscene-task.conf"
ENV_EXAMPLE = ROOT / "deploy" / "midscene.env.example"


def load_backend():
    spec = importlib.util.spec_from_file_location("midscene_upload_static_check", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check_runner_inline_android_device_injection():
    import yaml
    from task_server.services.yaml_service import midscene_cli_dispatch_yaml_text

    source = "android:\n  tasks:\n  - name: smoke\n    flow:\n    - terminate: com.xbxxhz.box\n    - launch: com.xbxxhz.box\n"
    dispatched = midscene_cli_dispatch_yaml_text(source, device_id="ecbfd645")
    for filename in ("windows-midscene-runner.py", "mac-midscene-runner.py"):
        module_name = filename.replace("-", "_").replace(".py", "")
        spec = importlib.util.spec_from_file_location(module_name, ROOT / filename)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        rendered = module.inject_external_page_escape(module.midscene_cli_yaml_text(dispatched))
        parsed = yaml.safe_load(rendered)
        require(parsed.get("android") == {"deviceId": "ecbfd645"}, f"{filename} must preserve the server-dispatched Android deviceId block")
        require(parsed.get("agent", {}).get("screenshotShrinkFactor") == 2, f"{filename} must preserve the mobile screenshot shrink factor")
        require(isinstance(parsed.get("tasks"), list) and len(parsed["tasks"]) == 1, f"{filename} must keep root tasks executable")
        require(rendered.count("android:") == 1, f"{filename} must keep one Android interface root")

        inline = module.ensure_android_device_id("android: {}\ntasks: []\n", "ecbfd645")
        require(yaml.safe_load(inline).get("android") == {"deviceId": "ecbfd645"}, f"{filename} must expand inline empty Android config before injecting deviceId")


def check_midscene_model_family_protocol():
    from task_server.services import runner_service

    require(runner_service.infer_midscene_model_family("qwen3.6-plus") == "qwen3.6", "Server must map qwen3.6-plus to the Midscene 1.7.10 normalized-coordinate family")
    require(runner_service.infer_midscene_model_family("qwen3.6-plus", "qwen2.5-vl") == "qwen3.6", "Known Qwen3.6 model names must override stale incompatible family settings")
    require(runner_service.infer_midscene_model_family("qwen2.5-vl-72b") == "qwen2.5-vl", "Server must keep true Qwen2.5-VL models on the pixel-coordinate family")

    env_keys = ("DASHSCOPE_API_KEY", "DASHSCOPE_VL_MODEL", "MIDSCENE_MODEL_FAMILY", "MIDSCENE_USE_QWEN_VL")
    old_env = {key: os.environ.get(key) for key in env_keys}
    try:
        os.environ["DASHSCOPE_API_KEY"] = "static-check-model-key"
        os.environ["DASHSCOPE_VL_MODEL"] = "qwen3.6-plus"
        os.environ.pop("MIDSCENE_MODEL_FAMILY", None)
        os.environ["MIDSCENE_USE_QWEN_VL"] = "1"
        runtime = runner_service.midscene_runtime_env()
        require(runtime.get("MIDSCENE_MODEL_NAME") == "qwen3.6-plus", "Server runtime env must expose the configured Midscene model name")
        require(runtime.get("MIDSCENE_MODEL_FAMILY") == "qwen3.6", "Server runtime env must explicitly expose the Qwen3.6 model family")
        require("MIDSCENE_USE_QWEN_VL" not in runtime, "Server must not declare Qwen3.6 as legacy qwen2.5-vl")
    finally:
        for key, value in old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    for filename in ("windows-midscene-runner.py", "mac-midscene-runner.py"):
        module_name = f"model_family_{filename.replace('-', '_').replace('.py', '')}"
        spec = importlib.util.spec_from_file_location(module_name, ROOT / filename)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        require(module.infer_midscene_model_family("qwen3.6-plus", "qwen2.5-vl") == "qwen3.6", f"{filename} must reject a stale qwen2.5-vl family for a known Qwen3.6 model")
        original_runtime = module.task_runtime_env
        old_legacy = os.environ.get("MIDSCENE_USE_QWEN_VL")
        try:
            os.environ["MIDSCENE_USE_QWEN_VL"] = "1"
            module.task_runtime_env = lambda force=False: {
                "MIDSCENE_MODEL_API_KEY": "static-check-model-key",
                "MIDSCENE_MODEL_BASE_URL": "https://example.invalid/v1",
                "MIDSCENE_MODEL_NAME": "qwen3.6-plus",
                "MIDSCENE_MODEL_FAMILY": "qwen3.6",
                "MIDSCENE_USE_QWEN_VL": "1",
            }
            runtime = module.midscene_env("ecbfd645")
            require(runtime.get("MIDSCENE_MODEL_FAMILY") == "qwen3.6", f"{filename} must pass the explicit Qwen3.6 family to Midscene")
            require("MIDSCENE_USE_QWEN_VL" not in runtime, f"{filename} must clear stale qwen2.5-vl switches when an explicit family is configured")
            require(runtime.get("ANDROID_SERIAL") == "ecbfd645", f"{filename} must preserve the selected Android device while applying model config")
        finally:
            module.task_runtime_env = original_runtime
            if old_legacy is None:
                os.environ.pop("MIDSCENE_USE_QWEN_VL", None)
            else:
                os.environ["MIDSCENE_USE_QWEN_VL"] = old_legacy


def check_agent_failure_ai_payload_has_primary_evidence():
    from task_server.services import agent_service

    old_task_dir = agent_service.TASK_DIR
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            agent_service.TASK_DIR = temp_dir
            module_dir = Path(temp_dir) / "AI_Agent_草稿"
            module_dir.mkdir()
            (module_dir / "case.yaml").write_text("android:\n  tasks: []\n", encoding="utf-8")
            payload = agent_service._agent_failure_ai_payload(
                {"target": "基础打印新增百度网盘入口"},
                "SCRIPT_ISSUE",
                "执行失败 1 个任务",
                [{
                    "jobId": "job-static-failure",
                    "module": "AI_Agent_草稿",
                    "file": "case.yaml",
                    "taskName": "文档打印入口验证",
                    "failureReason": "截图显示仍停留在首页，文档打印入口未被点击",
                    "stdoutTail": "waitFor timeout",
                    "stderrTail": "",
                    "summaryText": "页面仍为首页",
                    "failureReview": {"category": "env_issue", "confidence": 0.96, "reason": "模型服务请求被中止"},
                }],
            )
        require(payload.get("taskName") == "文档打印入口验证", "Agent failure analysis must send the primary task name expected by AI Gateway")
        require("android:" in payload.get("yaml", ""), "Agent failure analysis must send the failed YAML expected by AI Gateway")
        require("waitFor timeout" in payload.get("log", "") and "页面仍为首页" in payload.get("log", ""), "Agent failure analysis must send Runner log and summary evidence")
        require("仍停留在首页" in payload.get("screenshotDesc", ""), "Agent failure analysis must send screenshot-derived failure context")
        require(payload.get("failedJobs") and payload["failedJobs"][0].get("jobId") == "job-static-failure", "Agent failure analysis must preserve aggregate failed jobs")
        require(payload["failedJobs"][0].get("failureReview", {}).get("category") == "env_issue", "Agent failure analysis must preserve Runner failure review evidence")
    finally:
        agent_service.TASK_DIR = old_task_dir


def check_agent_failure_review_and_repair_guard():
    from task_server.services import agent_service
    from task_server.services import repair_service

    normalized = agent_service._normalize_failed_execution_item({
        "jobId": "job-static-model-timeout",
        "error": "waitFor timeout",
        "failureReview": {
            "category": "env_issue",
            "confidence": 0.96,
            "reason": "Midscene model request was aborted",
        },
    })
    require(normalized.get("failureType") == "ENV_ISSUE", "Runner env_issue review must override misleading script-like timeout text")
    require("model request" in normalized.get("failureReason", ""), "Normalized Agent failure must retain the Runner review reason")
    script_timeout = agent_service._normalize_failed_execution_item({
        "jobId": "job-static-page-timeout",
        "failureType": "等待目标超时",
        "summaryText": "waitFor timeout: 当前已经进入文档打印页面，但脚本仍在等待打印首页",
    })
    require(script_timeout.get("failureType") == "SCRIPT_ISSUE", "Display labels such as 等待目标超时 must normalize to SCRIPT_ISSUE")
    require(script_timeout.get("failureKind") == "等待目标超时", "Agent must retain the concrete failure kind for display and evidence")
    require(agent_service._agent_should_confirm_unknown_failure({}, "UNKNOWN"), "Unreviewed UNKNOWN failures must request confirmation")
    require(not agent_service._agent_should_confirm_unknown_failure({"unknownFailureConfirmed": True}, "UNKNOWN"), "Reviewed UNKNOWN failures must not enter a confirmation loop")

    original = """android:
  tasks:
    - name: smoke
      flow:
        - launch: com.xbxxhz.box
        - aiTap: 文档打印
        - aiAssert: 百度网盘入口可见
"""
    sleep_only = """android:
  tasks:
    - name: smoke repair
      flow:
        - launch: com.xbxxhz.box
        - sleep: 3000
        - aiTap: 文档打印
        - sleep: 2000
        - aiAssert: 百度网盘入口可见
"""
    semantic_fix = sleep_only.replace("aiTap: 文档打印", "aiTap: 首页中名称为文档打印的入口")
    require(not agent_service._agent_repair_has_semantic_change(original, sleep_only), "Agent must reject sleep-only or task-name-only repair YAML")
    require(agent_service._agent_repair_has_semantic_change(original, semantic_fix), "Agent must accept repair YAML that changes an executable action")

    old_task_dir = agent_service.TASK_DIR
    old_gateway_available = agent_service._ai_gateway_available
    old_gateway_post = agent_service._ai_gateway_post
    old_log_tool_call = agent_service._log_tool_call
    old_upsert = repair_service.upsert_repair_draft
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            agent_service.TASK_DIR = temp_dir
            module_dir = Path(temp_dir) / "AI_Agent_草稿"
            module_dir.mkdir()
            (module_dir / "case.yaml").write_text(original, encoding="utf-8")
            agent_service._ai_gateway_available = lambda: True
            agent_service._ai_gateway_post = lambda *args, **kwargs: {"fixedYaml": sleep_only}
            agent_service._log_tool_call = lambda *args, **kwargs: None
            repair_service.upsert_repair_draft = lambda draft: dict(draft)
            run = {
                "target": "基础打印新增百度网盘入口",
                "platform": "android",
                "artifacts": {
                    "failureAnalysis": {"failureType": "SCRIPT_ISSUE", "summary": "点击后仍停留首页"},
                    "report": {
                        "failedJobs": [{
                            "jobId": "job-static-noop-repair",
                            "module": "AI_Agent_草稿",
                            "file": "case.yaml",
                            "taskName": "smoke",
                            "status": "failed",
                            "error": "waitFor timeout",
                            "failureType": "SCRIPT_ISSUE",
                        }],
                    },
                },
            }
            call = agent_service._tool_generate_repair(run)
        draft = (run.get("artifacts", {}).get("repairDrafts") or [{}])[0]
        summary = run.get("artifacts", {}).get("repairSummary") or {}
        require(call.get("status") == "SKIPPED" and not call.get("aiUsed"), "Sleep-only AI repair must not be reported as usable")
        require(draft.get("status") == "REJECTED" and not draft.get("fixedYaml") and draft.get("rejectedYaml"), "Sleep-only AI repair must be retained only as rejected evidence")
        require(summary.get("blockedCount") == 1 and summary.get("items", [{}])[0].get("blockedReason") == "sleep_only_or_noop", "Repair summary must explain why a no-op candidate cannot rerun")
    finally:
        agent_service.TASK_DIR = old_task_dir
        agent_service._ai_gateway_available = old_gateway_available
        agent_service._ai_gateway_post = old_gateway_post
        agent_service._log_tool_call = old_log_tool_call
        repair_service.upsert_repair_draft = old_upsert


def check_agent_quality_report_uses_figma_visual_reference():
    from task_server.services import agent_service

    run = {
        "artifacts": {
            "sourceContext": {"figmaUrl": "https://www.figma.com/design/static/check"},
            "visualReferenceReport": {
                "figmaPageCount": 4,
                "figmaImageCount": 4,
                "ignoredFigmaCount": 0,
                "uploadedImageCount": 0,
            },
        },
    }
    result = {
        "case_set_id": "agent-static-figma-quality",
        "cases": {
            "analysis": {"requirement_points": ["文档打印页面展示百度网盘入口"]},
            "cases": [{"title": "百度网盘入口可见"}],
            "manual_cases": [],
        },
        "summary": {
            "counts": {"cases": 1, "manual_cases": 0, "yaml_files": 1},
            "ui_design_assets": [],
            "hidden_ui_design_assets": [],
        },
        "caseCount": 1,
        "manualCaseCount": 0,
        "scenarioCount": 1,
        "yamlFileCount": 1,
        "coverageAudit": {"ok": True, "requirement_point_count": 1},
    }
    report = agent_service._build_agent_quality_report(
        run,
        result,
        yaml_file_items=[{"file": "00-smoke.yaml"}],
        yaml_executability={"taskCount": 1},
    )
    figma_layer = next((item for item in report.get("layers", []) if item.get("name") == "Figma 解析图片"), {})
    require(report.get("figmaImageCount") == 4, "Quality report must reuse parsed Figma image count from visual reference evidence")
    require(figma_layer.get("count") == 4 and figma_layer.get("ready") is True, "Quality report Figma layer must reflect parsed visual references")

    visual_run = {
        "artifacts": {
            "sourceContext": {
                "figmaUrl": "https://www.figma.com/design/static/check",
                "figmaUsedPages": [{"page_name": "文档打印"}],
                "figmaImageCount": 4,
            }
        }
    }
    visual_report = agent_service._agent_visual_reference_report(
        visual_run,
        {"review": {"yaml_visual_grounded": True}},
    )
    require(visual_report.get("aiJudgementRequired") is True, "Parsed Figma pages must require AI visual judgement even without uploaded screenshots")
    require(visual_report.get("sentToAiForJudgement") is True and visual_report.get("aiJudgementStatus") == "completed", "Figma visual grounding completion must be traceable")

    old_draft_dir = agent_service.AGENT_DRAFT_DIR
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            agent_service.AGENT_DRAFT_DIR = temp_dir
            draft_run = {
                "runId": "agent-static-figma-draft",
                "artifacts": {
                    "sourceContext": visual_run["artifacts"]["sourceContext"],
                    "visualReferenceReport": {"figmaImageCount": 4, "ignoredFigmaCount": 0},
                },
                "pendingConfirmations": [],
            }
            agent_service._save_agent_yaml_draft(
                draft_run,
                draft_run["artifacts"],
                "android:\n  tasks:\n    - name: static\n      flow:\n        - launch: com.xbxxhz.box\n        - aiAssert: 百度网盘入口可见\n",
            )
            draft_quality = draft_run["artifacts"].get("qualityReport") or {}
            draft_figma_layer = next((item for item in draft_quality.get("layers", []) if item.get("name") == "Figma 解析图片"), {})
            require(draft_quality.get("figmaImageCount") == 4 and draft_figma_layer.get("ready") is True, "Draft quality report must not lose parsed Figma image counts")
    finally:
        agent_service.AGENT_DRAFT_DIR = old_draft_dir
    require(not any("没有可展示的解析图片" in item for item in report.get("warnings", [])), "Parsed Figma images must not produce a false missing-image warning")


def check_agent_regression_scope_preserves_new_requirement_generation():
    from task_server.services import agent_service

    source_context = {
        "sourceType": "requirement",
        "requirementText": "基础打印新增百度网盘入口",
        "figmaUrl": "https://www.figma.com/design/static/new-feature",
    }
    regression_run = {
        "target": "基础打印新增百度网盘入口",
        "scope": "regression",
        "sourceType": "requirement",
        "sourceRefs": {"figmaUrl": source_context["figmaUrl"]},
    }
    require(not agent_service._agent_explicit_reuse_requested(regression_run, "requirement"), "Regression execution scope alone must not mean reuse historical YAML")
    require(agent_service._agent_is_new_requirement_run(regression_run, source_context), "Regression scope with requirement/Figma input must enter the complete new-requirement pipeline")
    require(
        not agent_service._agent_is_new_requirement_run({**regression_run, "target": "回归已有百度网盘基线用例"}, source_context),
        "Explicit reuse/baseline wording must continue to select historical YAML",
    )
    require(
        not agent_service._agent_is_new_requirement_run({**regression_run, "sourceType": "failed_job", "sourceRefs": {"failedJobId": "job-static"}}, source_context),
        "Failed-job runs must continue to reuse the exact failed YAML",
    )


def check_generated_yaml_short_guards_and_execution_level_floor():
    from task_server.services import agent_service, yaml_service

    scan_case = {
        "title": "扫描复印页百度网盘入口相对位置校验",
        "app_package": "com.xbxxhz.box",
        "steps": [
            "启动 App，并等待小白学习首页加载完成",
            "如当前不在首页，返回或点击底部「首页」回到首页",
            "点击「扫描复印」入口，进入扫描复印页",
            "等待扫描复印页加载完成，页面展示功能入口区域",
            "横向滑动功能入口区域，等待「百度网盘」入口可见",
        ],
        "assertions": ["扫描复印页展示「百度网盘」入口"],
    }
    task_yaml = yaml_service.case_to_task_yaml(scan_case)
    normalized_once, _ = yaml_service.normalize_horizontal_icon_scrolls_in_task_block(task_yaml, evidence_text="android:")
    normalized_twice, _ = yaml_service.normalize_horizontal_icon_scrolls_in_task_block(normalized_once, evidence_text="android:")
    flow_text = normalized_once.split("flow:", 1)[-1]
    require(normalized_once == normalized_twice, "Horizontal aiScroll normalization must be idempotent")
    require(flow_text.count("- aiScroll:") == 1, "One generated horizontal step must remain one semantic aiScroll")
    require("input swipe 950 1080 150 1080 500" not in flow_text, "Generated horizontal scrolling must not add fixed-coordinate ADB fallbacks")
    require("如当前不在首页" not in flow_text, "Launch guard must absorb duplicate conditional home-recovery steps")
    require("点击后的目标页面、弹窗、列表、空态" not in flow_text, "Generic transition waits must not inject out-of-scope empty-state semantics")
    require(flow_text.count("- aiWaitFor:") <= 5, "Short generated entry checks must not accumulate duplicate waits")

    yaml_text = """android:
  tasks:
    - name: 百度网盘入口可见
      flow:
        - launch: com.xbxxhz.box
        - aiWaitFor: 小白学习首页入口可见
        - aiTap: 文档打印入口
        - aiAssert: 百度网盘入口可见
"""
    old_task_dir = agent_service.TASK_DIR
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            agent_service.TASK_DIR = temp_dir
            module_dir = Path(temp_dir) / "AI_Agent_草稿"
            module_dir.mkdir()
            review_path = module_dir / "review.yaml"
            executable_path = module_dir / "executable.yaml"
            review_path.write_text(yaml_text, encoding="utf-8")
            executable_path.write_text(yaml_text, encoding="utf-8")
            run = {"target": "普通新需求", "scope": "regression", "module": "AI_Agent_草稿", "artifacts": {}}
            artifacts = run["artifacts"]
            refs, err = agent_service._confirm_agent_yaml_files(run, artifacts, [
                {
                    "module": "AI_Agent_草稿",
                    "file": review_path.name,
                    "path": str(review_path),
                    "executionLevel": "needs_review",
                    "scopeReview": {"ok": False, "reasons": ["需求范围待确认"]},
                },
                {
                    "module": "AI_Agent_草稿",
                    "file": executable_path.name,
                    "path": str(executable_path),
                    "executionLevel": "executable",
                    "scopeReview": {"ok": True, "reasons": []},
                },
            ])
            require(not err and [ref.get("file") for ref in refs] == ["executable.yaml"], "Confirmation must not promote needs_review YAML into Runner refs")
            review_result = next(item for item in (artifacts.get("yamlValidation") or {}).get("results", []) if item.get("file") == "review.yaml")
            require(review_result.get("executionLevel") == "needs_review", "Confirmation must preserve the stricter generated execution level")

            blocked_run = {"target": "普通新需求", "scope": "regression", "module": "AI_Agent_草稿", "artifacts": {}}
            blocked_refs, blocked_err = agent_service._confirm_agent_yaml_files(blocked_run, blocked_run["artifacts"], [{
                "module": "AI_Agent_草稿",
                "file": review_path.name,
                "path": str(review_path),
                "executionLevel": "needs_review",
                "scopeReview": {"ok": False, "reasons": ["需求范围待确认"]},
            }])
            require(not blocked_refs and "完整回归生成结果未达到" in blocked_err, "Regression must not continue with only a synthetic smoke when all requirement YAML needs review")
    finally:
        agent_service.TASK_DIR = old_task_dir


def check_generated_yaml_semantic_scope_and_visual_trace():
    from task_server.services import agent_service, ai_skill_service, yaml_service

    analysis = {
        "requirement_points": [
            "文档打印页面新增百度网盘入口，入口位于本地文档之后并保持同级展示。",
        ],
    }
    concrete_case = {
        "title": "文档打印百度网盘入口可见性",
        "steps": [
            "点击首页或底部导航中名称为「文档打印」的入口",
            "等待文档打印页面展示「百度网盘」入口",
        ],
        "assertions": ["百度网盘入口位于本地文档之后"],
    }
    concrete_yaml = """android:
  tasks:
    - name: 文档打印百度网盘入口可见性
      flow:
        - launch: com.xbxxhz.box
        - aiTap: 文档打印入口
        - aiWaitFor:
            prompt: 文档打印页面展示百度网盘入口
            timeout: 12000
        - aiAssert: 百度网盘入口位于本地文档之后
"""
    concrete_review = yaml_service.generated_case_requirement_scope_review(
        concrete_case,
        analysis,
        concrete_yaml,
    )
    require(concrete_review.get("ok"), "Structural timeout fields must not be treated as an unrequested timeout scenario")

    abstract_case = {
        **concrete_case,
        "steps": ["进入「基础打印-入口一致性」相关页面或入口区域"],
    }
    abstract_yaml = concrete_yaml.replace(
        "aiTap: 文档打印入口",
        "aiTap: 进入「基础打印-入口一致性」相关页面或入口区域",
    )
    abstract_review = yaml_service.generated_case_requirement_scope_review(
        abstract_case,
        analysis,
        abstract_yaml,
    )
    require(
        not abstract_review.get("ok")
        and any("抽象模块名" in reason for reason in abstract_review.get("reasons", [])),
        "Generated YAML must reject test taxonomy and abstract page groups as aiTap targets",
    )

    for feature, scenario_name, point, expected_entry in (
        ("基础打印-入口一致性", "文档打印百度网盘入口可见性", "文档打印页面展示百度网盘入口", "文档打印"),
        ("普通照片打印", "普通照片打印百度网盘入口可见性", "普通照片打印导入方式展示百度网盘入口", "照片打印"),
        ("扫描复印", "扫描复印百度网盘入口可见性", "扫描复印页面展示百度网盘入口", "扫描复印"),
    ):
        steps, _ = ai_skill_service._fallback_steps_for_scenario({
            "feature": feature,
            "scenario": scenario_name,
            "requirement_point": point,
        }, app_context={"app_name": "小白学习打印", "home_hint": "文档打印、照片打印或扫描复印入口"})
        step_text = "\n".join(steps)
        require(expected_entry in step_text, f"Fallback must route through the concrete {expected_entry} entry")
        require("相关页面或入口区域" not in step_text and "目标入口区域" not in step_text, "Fallback must not invent abstract UI pages")

    base_payload = {
        "title": "基础打印新增百度网盘入口",
        "module": "基础打印",
        "analysis": analysis,
        "scenarios": [],
        "cases": [concrete_case],
        "manual_cases": [],
        "review": {"automation_filter_skill": "automation_filter.v1"},
    }
    original_chat = ai_skill_service.dashscope_chat_content
    try:
        ai_skill_service.dashscope_chat_content = lambda *args, **kwargs: json.dumps({
            "title": "基础打印新增百度网盘入口",
            "module": "基础打印",
            "cases": [concrete_case],
        }, ensure_ascii=False)
        grounded = ai_skill_service.call_visual_grounder_skill(
            base_payload["title"],
            base_payload["module"],
            base_payload,
            ["Figma 文档打印页面"],
            [{"mime": "image/png", "base64": "AA=="}],
            timeout_seconds=60,
        )
    finally:
        ai_skill_service.dashscope_chat_content = original_chat
    require(
        grounded.get("analysis", {}).get("requirement_points") == analysis["requirement_points"],
        "Visual grounding must inherit required analysis context before schema validation",
    )
    require(
        grounded.get("review", {}).get("visual_grounder_skill") == "visual_grounder.v1",
        "Visual grounding must retain a completed AI judgment marker",
    )

    visual_payload = {
        "review": {
            "yaml_visual_batches": {"enabled": True, "total_batches": 1, "completed_batches": 0, "errors": ["schema failed"]},
            "visual_refine_errors": ["schema failed"],
        },
    }
    trace = yaml_service.snapshot_yaml_visual_review(visual_payload)
    restored = yaml_service.restore_yaml_visual_review({"review": {"coverage_audit": {"ok": True}}}, trace)
    require(
        restored.get("review", {}).get("yaml_visual_batches", {}).get("errors") == ["schema failed"],
        "Coverage/planner normalization must not erase visual AI attempt and failure details",
    )
    visual_report = agent_service._agent_visual_reference_report({
        "artifacts": {
            "sourceContext": {
                "figmaUsedPages": [{"page_name": "文档打印"}],
                "figmaImageCount": 1,
            },
        },
    }, visual_payload)
    require(
        visual_report.get("sentToAiForJudgement") is True
        and visual_report.get("aiJudgementCompleted") is False
        and visual_report.get("aiJudgementStatus") == "failed",
        "Agent visual report must distinguish attempted-and-failed from skipped or pending",
    )

    rich_payload = {
        "analysis": {"requirement_points": ["文档打印页面新增百度网盘入口"]},
        "review": {},
    }
    rich_result = yaml_service._ensure_rich_generation_scope(
        rich_payload,
        "基础打印新增百度网盘入口",
        "基础打印",
        ["需求正文" * 500],
        [{"page_name": "文档打印首页备份 2"}, {"page_name": "引导1"}],
        [{"name": "figma-1.png"}, {"name": "figma-2.png"}],
    )
    require(
        rich_result.get("analysis", {}).get("requirement_points") == ["文档打印页面新增百度网盘入口"],
        "Rich requirement/Figma inputs must not turn Figma page names or filler text into requirement points",
    )
    require(
        rich_result.get("review", {}).get("rich_generation_scope", {}).get("synthetic_requirement_points_added") == 0
        and rich_result.get("review", {}).get("rich_generation_scope", {}).get("extra_coverage_round") is False,
        "Rich input metadata must keep Figma as a soft reference and avoid redundant coverage repair rounds",
    )

    fallback_payload = yaml_service.enforce_generated_fallback_execution_floor({
        **base_payload,
        "review": {"automation_filter_skill": "local_fallback_after_ai_timeout"},
    })
    fallback_case = fallback_payload["cases"][0]
    require(fallback_case.get("executionLevel") == "needs_review", "AI-timeout fallback cases must remain review-only")
    require(
        yaml_service.generated_yaml_effective_level("executable", fallback_case, {"ok": True}) == "needs_review",
        "Static scoring must not promote an AI-timeout fallback back to executable",
    )

    ai_source = (ROOT / "task_server" / "services" / "ai_skill_service.py").read_text(encoding="utf-8")
    install_source = (ROOT / "deploy" / "install-server.sh").read_text(encoding="utf-8")
    env_source = (ROOT / "deploy" / "midscene.env.example").read_text(encoding="utf-8")
    require('MIDSCENE_AUTOMATION_FILTER_TIMEOUT_SECONDS", "150"' in ai_source, "Automation filter default timeout must allow the production model more than 90 seconds")
    require('ensure_env_default "MIDSCENE_AUTOMATION_FILTER_TIMEOUT_SECONDS" "150"' in install_source, "Installer must configure the automation filter timeout")
    require('upgrade_env_default_if_old "MIDSCENE_AUTOMATION_FILTER_TIMEOUT_SECONDS" "150" "90"' in install_source, "Installer must migrate the old 90-second automation filter timeout")
    require("MIDSCENE_AUTOMATION_FILTER_TIMEOUT_SECONDS='150'" in env_source, "Environment example must document the new automation filter timeout")


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def check_sonic_batch_payload_shapes():
    from task_server.services import sonic_service

    calls = []
    original_publish_yaml = sonic_service.sonic_publish_yaml
    original_task_dir = sonic_service.cfg.TASK_DIR

    def fake_publish_yaml(payload):
        calls.append(payload)
        return {
            "ok": True,
            "results": [{"status": "published", "task_name": payload.get("taskName") or "case"}],
        }

    sonic_service.sonic_publish_yaml = fake_publish_yaml
    try:
        result = sonic_service.sonic_publish_batch({
            "items": [
                {"module": "M1", "file": "a.yaml", "force": True},
                {"module": "M2", "file": "b.yaml"},
            ]
        })
        require(result["ok"] is True and result["total_files"] == 2 and calls[0]["force"] is True, "Sonic batch must accept explicit items payloads")

        calls.clear()
        result = sonic_service.sonic_publish_batch({"module": "M3", "files": ["c.yaml", "d.yml"], "force": True})
        require(result["total_files"] == 2 and all(call["module"] == "M3" for call in calls), "Sonic batch must accept module/files payloads")

        calls.clear()
        with tempfile.TemporaryDirectory() as temp_dir:
            module_dir = Path(temp_dir) / "M4"
            module_dir.mkdir()
            (module_dir / "e.yaml").write_text("android:\n  tasks: []\n", encoding="utf-8")
            (module_dir / "ignore.txt").write_text("x", encoding="utf-8")
            sonic_service.cfg.TASK_DIR = temp_dir
            result = sonic_service.sonic_publish_batch({"module": "M4"})
        require(result["total_files"] == 1 and calls[0]["file"] == "e.yaml", "Sonic batch must expand module-only payloads to YAML files")
    finally:
        sonic_service.sonic_publish_yaml = original_publish_yaml
        sonic_service.cfg.TASK_DIR = original_task_dir


def check_sonic_feishu_delivery_meta():
    from task_server.services import sonic_service

    old_default = os.environ.get("FEISHU_WEBHOOK_DEFAULT")
    old_package = os.environ.get("FEISHU_WEBHOOK_COM_KFB_MODEL")
    try:
        os.environ["FEISHU_WEBHOOK_DEFAULT"] = "https://open.feishu.cn/open-apis/bot/v2/hook/default"
        os.environ.pop("FEISHU_WEBHOOK_COM_KFB_MODEL", None)
        meta = sonic_service._task_app_feishu_webhook_meta({"package": "com.kfb.model"})
        require(meta.get("source") == "FEISHU_WEBHOOK_DEFAULT" and meta.get("fingerprint"), "Feishu delivery must expose default webhook source and fingerprint")
        os.environ["FEISHU_WEBHOOK_COM_KFB_MODEL"] = "https://open.feishu.cn/open-apis/bot/v2/hook/package"
        meta = sonic_service._task_app_feishu_webhook_meta({"package": "com.kfb.model"})
        require(meta.get("source") == "FEISHU_WEBHOOK_COM_KFB_MODEL", "Package-specific Feishu webhook must be preferred over default")
        meta = sonic_service._task_app_feishu_webhook_meta({
            "package": "com.kfb.model",
            "feishu_webhook": "https://open.feishu.cn/open-apis/bot/v2/hook/app",
        })
        require(meta.get("source") == "task_app", "Task app Feishu webhook must be preferred over env config")
    finally:
        if old_default is None:
            os.environ.pop("FEISHU_WEBHOOK_DEFAULT", None)
        else:
            os.environ["FEISHU_WEBHOOK_DEFAULT"] = old_default
        if old_package is None:
            os.environ.pop("FEISHU_WEBHOOK_COM_KFB_MODEL", None)
        else:
            os.environ["FEISHU_WEBHOOK_COM_KFB_MODEL"] = old_package


def check_agent_fallback_yaml_auto_confirm_split():
    from task_server.services import agent_service

    old_task_dir = agent_service.TASK_DIR
    old_draft_dir = agent_service.AGENT_DRAFT_DIR
    yaml_text = """tasks:
  - name: "AI建模入口验收"
    flow:
      - launch: com.kfb.model
      - aiAssert: "AI建模入口可见"
  - name: "AI建模语音输入验收"
    flow:
      - launch: com.kfb.model
      - aiTap: "语音创作入口"
      - aiAssert: "语音输入或长按说话提示可见"
"""
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            agent_service.TASK_DIR = os.path.join(temp_dir, "tasks")
            agent_service.AGENT_DRAFT_DIR = os.path.join(temp_dir, "drafts")
            run = {
                "runId": "agent-static-split",
                "target": "AI建模需求",
                "module": "AI测试",
                "artifacts": {},
            }
            artifacts = run["artifacts"]
            refs, err = agent_service._confirm_agent_yaml_content_as_files(
                run,
                artifacts,
                yaml_text,
                reason="fallback_after_ui_yaml_pipeline",
            )
            require(not err, f"Fallback YAML split should not fail: {err}")
            require(len(refs) == 2, "Fallback multi-task YAML must split into separate files")
            require(all(ref.get("confirmed") and os.path.exists(ref.get("path", "")) for ref in refs), "Split fallback YAML files must be confirmed and written")
            written = [Path(ref["path"]).read_text(encoding="utf-8") for ref in refs]
            require(all("\n  tasks:" in text and "\ntasks:" not in text for text in written), "Split Agent YAML files must use android.tasks instead of root tasks before Runner")
            validation = artifacts.get("yamlValidation") or {}
            require(validation.get("ok") and validation.get("autoConfirmedFallback"), "Split fallback YAML must be marked as auto-confirmed fallback")
    finally:
        agent_service.TASK_DIR = old_task_dir
        agent_service.AGENT_DRAFT_DIR = old_draft_dir


def check_agent_generation_pipeline_normalizes_validation_state():
    from task_server.services import agent_service

    original_is_new = agent_service._agent_is_new_requirement_run
    original_generate = agent_service._agent_generate_yaml_from_ui_pipeline
    original_confirm = agent_service._confirm_agent_yaml_files
    try:
        def fake_is_new(run, source_context=None):
            return True

        def fake_generate(run, source_context, source_text):
            return [{"module": "AI测试", "file": "case.yaml", "path": "/tmp/case.yaml"}], {
                "caseCount": 1,
                "scenarioCount": 1,
                "summaryFiles": {},
            }

        def fake_confirm(run, artifacts, file_items):
            artifacts["yamlValidation"] = [{"legacy": "list-state"}]
            return [{"type": "file", "module": "AI测试", "file": "case.yaml", "path": "/tmp/case.yaml", "confirmed": True}], ""

        agent_service._agent_is_new_requirement_run = fake_is_new
        agent_service._agent_generate_yaml_from_ui_pipeline = fake_generate
        agent_service._confirm_agent_yaml_files = fake_confirm
        run = {
            "runId": "agent-static-validation-list",
            "target": "AI建模需求",
            "module": "AI测试",
            "sourceType": "requirement",
            "artifacts": {
                "sourceContext": {
                    "sourceType": "requirement",
                    "sourceSummary": "需求文档 + Figma",
                    "figmaUrl": "https://figma.test/file",
                }
            },
        }
        call = agent_service._tool_generate_yaml(run)
        validation = run["artifacts"].get("yamlValidation") or {}
        require(call.get("status") == "SUCCESS", f"Agent pipeline should survive list yamlValidation state: {call}")
        require(isinstance(validation, dict) and validation.get("autoConfirmed"), "Agent yamlValidation must be normalized to dict and auto-confirmed")
    finally:
        agent_service._agent_is_new_requirement_run = original_is_new
        agent_service._agent_generate_yaml_from_ui_pipeline = original_generate
        agent_service._confirm_agent_yaml_files = original_confirm


def check_agent_executable_gate_invokes_ai_rewrite():
    from task_server.services import agent_service

    original_ai_rewrite = agent_service.ai_rewrite_yaml_for_executable_gate
    bad_yaml = """android:
  tasks:
    - name: 扫描复印首页百度网盘入口可见性及同级并列校验
      flow:
        - launch: com.xbxxhz.box
        - ai: 查找并进入扫描复印首页，判断百度网盘入口是否展示，如果没找到就继续滑动并进入授权页
"""
    repaired_yaml = """android:
  tasks:
    - name: 扫描复印首页百度网盘入口可见
      flow:
        - launch: com.xbxxhz.box
        - aiWaitFor: App 首页加载完成，扫描复印入口可见
        - aiTap: 扫描复印入口
        - aiWaitFor: 扫描复印首页已打开，百度网盘入口可见
        - aiAssert: 扫描复印首页展示百度网盘入口
"""
    calls = []

    def fake_ai_rewrite(yaml_text, **kwargs):
        calls.append({"yaml": yaml_text, **kwargs})
        return {
            "changed": True,
            "ok": True,
            "content": repaired_yaml,
            "changes": ["拆分复合 ai 动作", "缩短为入口可见性短链路"],
            "attempts": [{"attempt": 1, "ok": True, "executionLevel": "executable"}],
        }

    try:
        agent_service.ai_rewrite_yaml_for_executable_gate = fake_ai_rewrite
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "bad.yaml")
            Path(path).write_text(bad_yaml, encoding="utf-8")
            ref = {
                "type": "file",
                "source": "generated",
                "generated": True,
                "module": "AI_Agent_草稿",
                "file": "bad.yaml",
                "path": path,
                "confirmed": True,
            }
            run = {
                "runId": "agent-ai-rewrite-static",
                "target": "基础打印新增百度网盘入口",
                "artifacts": {
                    "generationPipeline": {"source": "ui_yaml_pipeline"},
                    "generatedYamlPaths": [path],
                    "yamlRefs": [dict(ref)],
                },
            }
            repaired_ref, repair = agent_service._agent_repair_yaml_ref_for_execution(run, ref, reason="static_check")
            written = Path(path).read_text(encoding="utf-8")
            require(calls, "Generated YAML executable gate must invoke AI rewrite for semantic long-chain failures")
            require(repair and repair.get("type") == "ai_yaml_executable_gate_rewrite" and repair.get("ok"), "AI rewrite repair must be recorded as successful")
            require("查找并进入" not in written and "aiWaitFor: 扫描复印首页已打开" in written, "Successful AI rewrite must overwrite generated YAML with short executable flow")
            repairs = run["artifacts"].get("yamlExecutionRepairs") or []
            require(repairs and repairs[-1].get("aiRewrite", {}).get("ok"), "Agent artifacts must expose AI rewrite attempts for generated YAML repair")
            require(repaired_ref.get("executableScore", {}).get("executionLevel") == "executable", "Repaired ref must be rescored as executable")
    finally:
        agent_service.ai_rewrite_yaml_for_executable_gate = original_ai_rewrite


def check_midscene_yaml_validation_is_mapping():
    from task_server.services import yaml_service

    yaml_text = """android:
  tasks:
    - name: "AI建模入口验收"
      flow:
        - launch: com.kfb.model
        - aiAssert: "AI建模入口可见"
"""
    result = yaml_service.validate_midscene_yaml(yaml_text)
    require(isinstance(result, dict), "validate_midscene_yaml must return a mapping for generation callers")
    require(result.get("ok") is True and isinstance(result.get("warnings"), list), "validate_midscene_yaml must expose ok/warnings")
    _, yaml_items = yaml_service.cases_to_separate_midscene_yamls({
        "title": "AI建模",
        "module": "AI测试",
        "cases": [{
            "title": "AI建模入口验收",
            "steps": ["打开 App", "进入 AI 建模"],
            "assertions": ["AI建模入口可见"],
        }],
    }, app_package="com.kfb.model", base_file="ai-model.yaml")
    merged = [{"file": item["file"], **yaml_service.validate_midscene_yaml(item["content"])} for item in yaml_items]
    require(merged and merged[0].get("ok") is True, "Split YAML validation must be mergeable without list-as-mapping errors")


def check_yaml_static_validation_and_patterns():
    from task_server.services.yaml_pattern_service import (
        build_yaml_pattern_contract_text,
        extract_yaml_patterns_from_examples,
    )
    from task_server.services.yaml_static_validator import (
        load_yaml_action_contract,
        validate_yaml_static_executable,
    )
    from task_server.services.yaml_executable_scorer import (
        assertion_tap_to_wait_prompt,
        rank_executable_yaml_refs,
        score_midscene_yaml_executable,
        tap_prompt_looks_assertion,
    )
    from task_server.services.yaml_execution_plan import (
        build_generated_yaml_execution_plan,
        classify_generated_yaml_smoke_blocker,
    )
    from task_server.services.agent_service import (
        _agent_repair_missing_interaction_followups,
        _agent_yaml_dry_run_rows,
        normalize_yaml_refs,
    )
    from task_server.services.yaml_template_matcher import (
        build_yaml_template_matcher_text,
        evaluate_baseline_template_matching,
        select_best_baseline_template,
    )
    from task_server.services import ai_skill_service
    from task_server.services import yaml_service as yaml_service_module
    from task_server.services.yaml_service import (
        ai_rewrite_yaml_for_executable_gate,
        apply_generated_case_scope_gate,
        build_executable_smoke_yaml_policy_text,
        build_requirement_semantic_constraints_text,
        dry_run_midscene_yaml,
        repair_generated_yaml_executable_gate_issues,
        repair_generated_yaml_static_errors,
        should_ai_rewrite_for_executable_gate,
    )

    contract = load_yaml_action_contract()
    require("aiTap" in contract.get("allowed_actions", []), "YAML action contract must include real Midscene actions")
    require("verify" in contract.get("forbidden_actions", []), "YAML action contract must explicitly forbid pseudo actions")

    valid_yaml = """android:
  tasks:
    - name: demo
      flow:
        - launch: com.kfb.model
        - aiWaitFor: 首页加载完成
        - aiTap: AI建模入口
        - aiWaitFor: AI建模页打开
        - aiAssert: AI建模入口可见
"""
    valid = validate_yaml_static_executable(valid_yaml)
    require(valid.get("ok") and valid.get("executionLevel") in ("executable", "needs_review"), "Valid YAML must pass static executable validation")
    executable_score = score_midscene_yaml_executable(valid_yaml)
    require(executable_score.get("executionLevel") == "executable" and executable_score.get("score", 0) >= 78, "Generated YAML scorer must allow stable executable smoke YAML")
    require(executable_score.get("level") == "executable" and isinstance(executable_score.get("reasons"), list), "Generated YAML scorer must expose level and reasons")

    invalid_yaml = """android:
  tasks:
    - name: bad
      flow:
        - verify: 检查是否正常
"""
    invalid = validate_yaml_static_executable(invalid_yaml)
    require(not invalid.get("ok") and "verify" in invalid.get("blockedActions", []), "Pseudo actions must be blocked before Runner execution")
    invalid_score = score_midscene_yaml_executable(invalid_yaml)
    require(invalid_score.get("level") == "draft" and invalid_score.get("reasons"), "Unsupported Midscene actions must be downgraded to draft with reasons")
    unstable_yaml = """android:
  tasks:
    - name: unstable
      flow:
        - aiTap: AI建模入口
        - aiTap: 图片建模
"""
    unstable_score = score_midscene_yaml_executable(unstable_yaml)
    require(unstable_score.get("executionLevel") != "executable" and unstable_score.get("warnings"), "Generated YAML scorer must block tap-only unstable YAML")
    baseline_reuse_yaml = """android:
  tasks:
    - name: 文档打印-百度网盘打印
      flow:
        - aiTap: 文档打印
        - aiWaitFor: 文档打印首页加载完成
        - aiTap: 百度网盘
        - aiWaitFor: 百度网盘入口页加载完成
        - aiTap: 登录或授权按钮
        - aiWaitFor: 百度网盘文件列表加载完成
        - aiTap: 选择第一个文档
        - aiWaitFor: 打印预览页加载完成
"""
    baseline_generated_score = score_midscene_yaml_executable(baseline_reuse_yaml, generated=True)
    require(
        baseline_generated_score.get("executionLevel") != "executable",
        "Generated YAML scorer should still flag baseline-style YAML when it is newly generated",
    )
    long_generated_yaml = """android:
  tasks:
    - name: 文档打印百度网盘入口展示与点击验证
      flow:
        - runAdbShell: "am force-stop com.xbxxhz.box"
        - sleep: 800
        - launch: com.xbxxhz.box
        - sleep: 1500
        - aiWaitFor: 首页加载完成
        - aiTap: 首页
        - ai: 找到文档导入入口区域
        - ai: 向右翻看入口列表查找百度网盘入口
        - aiWaitFor: 百度网盘入口稳定显示
        - aiTap: 百度网盘入口
        - aiWaitFor: 百度网盘授权或文件选择页面打开
        - aiWaitFor: 文档打印首页的百度网盘入口可见并进入后续流程
        - aiAssert: 文档打印首页的百度网盘入口可见并进入后续流程
        - runAdbShell: "am force-stop com.xbxxhz.box"
        - sleep: 1000
        - aiWaitFor: App 已关闭或回到桌面
        - sleep: 500
"""
    long_generated_score = score_midscene_yaml_executable(long_generated_yaml)
    require(
        long_generated_score.get("executionLevel") != "executable"
        and any("重规划" in reason or "动作" in reason for reason in long_generated_score.get("reasons", [])),
        "Generated long no-baseline YAML must be downgraded before Runner to avoid Midscene replanning/timeouts",
    )
    baseline_run = {
        "runId": "agent-baseline-regression",
        "artifacts": {
            "matchedCases": ["/opt/midscene-tasks/小白学习基线用例-基础打印/百度网盘打印.yaml"],
            "yamlRefs": [{
                "type": "file",
                "module": "小白学习基线用例-基础打印",
                "file": "百度网盘打印.yaml",
                "path": "/opt/midscene-tasks/小白学习基线用例-基础打印/百度网盘打印.yaml",
                "content": baseline_reuse_yaml,
                "confirmed": True,
            }],
        },
    }
    baseline_refs = normalize_yaml_refs(baseline_run)
    baseline_rows, baseline_issues, baseline_ok_count = _agent_yaml_dry_run_rows(baseline_run, baseline_refs)
    require(
        baseline_ok_count == 1
        and not baseline_issues
        and baseline_rows[0].get("ok")
        and baseline_rows[0].get("validationMode") == "baseline"
        and (baseline_rows[0].get("executableScore") or {}).get("baselineValidation") is True,
        "Matched formal baseline YAML must use baseline validation and must not be quarantined by generated-YAML scoring",
    )
    missing_assert_yaml = """android:
  tasks:
    - name: missing assertion
      flow:
        - launch: com.kfb.model
        - aiWaitFor: 首页底部导航已加载
        - aiTap: AI建模入口
        - aiWaitFor: AI建模页打开
"""
    missing_assert_score = score_midscene_yaml_executable(missing_assert_yaml)
    require(
        missing_assert_score.get("executionLevel") == "executable"
        and any("aiAssert" in reason for reason in missing_assert_score.get("reasons", [])),
        "Missing aiAssert must be a quality warning, not an automatic execution blocker",
    )
    generic_query_yaml = """android:
  tasks:
    - name: generic query
      flow:
        - launch: com.kfb.model
        - aiWaitFor: 首页底部导航已加载
        - aiQuery: 页面
        - aiAssert: AI建模入口可见
"""
    generic_query_score = score_midscene_yaml_executable(generic_query_yaml)
    require(generic_query_score.get("executionLevel") != "executable" and any("aiQuery" in reason for reason in generic_query_score.get("reasons", [])), "Generic aiQuery must downgrade generated YAML")
    wait_then_click_yaml = """android:
  tasks:
    - name: 百度网盘入口基础可见性
      flow:
        - launch: com.xbxxhz.box
        - aiWaitFor: App 首页加载完成
        - aiTap: 等待首页加载稳定，点击「文档打印」icon
        - aiWaitFor: 文档打印首页加载完成
        - aiAssert: 百度网盘入口按钮可见
"""
    wait_then_click_score = score_midscene_yaml_executable(wait_then_click_yaml)
    require(
        wait_then_click_score.get("executionLevel") == "executable"
        and not any("aiTap 描述像检查/断言" in reason for reason in wait_then_click_score.get("reasons", [])),
        "Generated YAML scorer must not block actionable aiTap prompts that include wait context and a real click target",
    )
    assertion_tap_yaml = """android:
  tasks:
    - name: 文档打印首页展示百度网盘入口
      flow:
        - launch: com.xbxxhz.box
        - aiWaitFor: App 首页加载完成
        - aiTap: 点击「文档打印」icon
        - aiWaitFor: 文档打印首页加载完成
        - aiTap: 检查页面是否展示「百度网盘」入口按钮
        - aiWaitFor: 页面稳定展示「百度网盘」入口按钮，文案清晰可点击
        - aiAssert: 页面稳定展示「百度网盘」入口按钮，文案清晰可点击
"""
    assertion_tap_score = score_midscene_yaml_executable(assertion_tap_yaml)
    require(
        assertion_tap_score.get("executionLevel") != "executable"
        and any("aiTap 描述像检查/断言" in reason for reason in assertion_tap_score.get("reasons", [])),
        "Generated YAML scorer must block assertion-like aiTap prompts before Runner execution",
    )
    repaired_assertion_tap = _agent_repair_missing_interaction_followups(assertion_tap_yaml)
    require(
        repaired_assertion_tap.get("changed")
        and "aiWaitFor" in repaired_assertion_tap.get("content", "")
        and score_midscene_yaml_executable(repaired_assertion_tap.get("content", "")).get("executionLevel") == "executable",
        "Agent validation must locally repair assertion-like aiTap prompts before failing the whole run",
    )
    require(
        should_ai_rewrite_for_executable_gate(["复合 ai 动作包含查找/进入/判断", "等待链路 10 个但终态断言不足"])
        and should_ai_rewrite_for_executable_gate(["Runner 日志: Replanned 5 times, exceeding the limit"])
        and not should_ai_rewrite_for_executable_gate(["aiTap 描述像检查/断言"]),
        "Executable gate AI rewrite must only trigger for semantic long-chain/replanning failures",
    )
    original_dashscope_chat = yaml_service_module.dashscope_chat_content
    try:
        yaml_service_module.dashscope_chat_content = lambda *args, **kwargs: json.dumps({
            "analysis": "将复合长链路拆成短入口检查",
            "changes": ["保留入口可见性检查", "移除外部授权长链路"],
            "content": """android:
  tasks:
    - name: 文档打印首页百度网盘入口可见
      flow:
        - launch: com.xbxxhz.box
        - aiWaitFor: App 首页加载完成，文档打印入口可见
        - aiTap: 文档打印入口
        - aiWaitFor: 文档打印首页已打开，百度网盘入口可见
        - aiAssert: 文档打印首页展示百度网盘入口
""",
        }, ensure_ascii=False)
        ai_rewrite = ai_rewrite_yaml_for_executable_gate(
            """android:
  tasks:
    - name: 复合长链路示例
      flow:
        - launch: com.xbxxhz.box
        - ai: 查找并进入文档打印首页，判断百度网盘入口是否展示，如果没找到就继续滑动并进入授权页
""",
            reasons=["复合 ai 动作包含查找/进入/判断", "等待链路 10 个但终态断言不足"],
            title="百度网盘入口",
            module="AI_Agent_草稿",
            file="bad.yaml",
            baseline_text="```yaml\n- aiWaitFor: 首页已加载\n- aiTap: 文档打印入口\n- aiWaitFor: 百度网盘入口可见\n```",
        )
        require(
            ai_rewrite.get("changed")
            and ai_rewrite.get("ok")
            and dry_run_midscene_yaml(ai_rewrite.get("content", "")).get("ok"),
            "AI executable-gate rewrite must produce dry-runable short-chain YAML when mocked model returns valid content",
        )
    finally:
        yaml_service_module.dashscope_chat_content = original_dashscope_chat
    require(
        assertion_tap_to_wait_prompt("首页文档打印入口页-百度网盘入口可见性检查") == "页面展示百度网盘入口可见",
        "Assertion-like aiTap title prompts must be converted into concrete page-state waits",
    )
    require(
        tap_prompt_looks_assertion("文档打印首页展示百度网盘入口")
        and assertion_tap_to_wait_prompt("文档打印首页展示百度网盘入口") == "页面展示百度网盘入口",
        "Title-style display prompts must be treated as page-state waits instead of clickable targets",
    )
    title_assertion_tap_yaml = """android:
  tasks:
    - name: 首页文档打印入口页-百度网盘入口可见性检查
      flow:
        - launch: com.xbxxhz.box
        - aiWaitFor: App 首页加载完成
        - aiTap: 点击「文档打印」icon
        - aiWaitFor: 文档打印首页加载完成
        - aiTap: 首页文档打印入口页-百度网盘入口可见性检查
        - aiAssert: 页面展示百度网盘入口可见
"""
    title_assertion_repair = repair_generated_yaml_executable_gate_issues(title_assertion_tap_yaml)
    title_assertion_content = title_assertion_repair.get("content", "")
    require(
        title_assertion_repair.get("changed")
        and "aiTap: 首页文档打印入口页-百度网盘入口可见性检查" not in title_assertion_content
        and "aiWaitFor: 页面展示百度网盘入口可见" in title_assertion_content,
        "Generated YAML repair must handle title-style assertion aiTap prompts from Agent cases",
    )
    homepage_display_tap_yaml = """android:
  tasks:
    - name: 文档打印首页展示百度网盘入口
      flow:
        - launch: com.xbxxhz.box
        - aiWaitFor: App 首页加载完成
        - aiTap: 点击「文档打印」icon
        - aiWaitFor: 文档打印首页加载完成
        - aiTap: 文档打印首页展示百度网盘入口
        - aiAssert: 页面展示百度网盘入口可见
"""
    homepage_display_repair = repair_generated_yaml_executable_gate_issues(homepage_display_tap_yaml)
    homepage_display_content = homepage_display_repair.get("content", "")
    require(
        homepage_display_repair.get("changed")
        and "aiTap: 文档打印首页展示百度网盘入口" not in homepage_display_content
        and "aiWaitFor: 页面展示百度网盘入口" in homepage_display_content
        and score_midscene_yaml_executable(homepage_display_content).get("executionLevel") == "executable",
        "Generated YAML repair must fix display-title aiTap prompts without blocking otherwise executable cases",
    )
    generation_policy = build_executable_smoke_yaml_policy_text()
    require(
        "只有真实点击目标才能用 aiTap" in generation_policy
        and "检查/验证/是否展示/是否存在/页面可见/状态一致" in generation_policy,
        "YAML generation policy must forbid assertion-like aiTap prompts before model generation",
    )
    require(
        "10~12 个动作" in generation_policy
        and "新增入口类需求" in generation_policy
        and "第三方授权页" in generation_policy,
        "YAML generation policy must keep first-smoke entry checks short and defer unstable external flows",
    )
    require(
        "点击百度网盘" in generation_policy
        and "不能继续等待原业务页的入口展示" in generation_policy,
        "YAML generation policy must explicitly handle third-party entry post-click state",
    )
    baidu_mixed_state_yaml = """android:
  tasks:
    - name: 普通证件照百度网盘入口展示与点击验证
      flow:
        - launch: com.xbxxhz.box
        - aiWaitFor: App 首页加载完成
        - aiTap: 点击「证件照」或「一寸照」入口
        - aiWaitFor: 证件照页面加载完成
        - aiTap: 百度网盘入口
        - aiWaitFor: 普通证件照页面展示百度网盘入口
        - aiAssert: 普通证件照页面展示百度网盘入口，点击后进入百度网盘授权、登录或文件选择流程
"""
    baidu_mixed_score = score_midscene_yaml_executable(baidu_mixed_state_yaml)
    require(
        baidu_mixed_score.get("executionLevel") != "executable"
        and any("百度网盘点击后" in reason for reason in baidu_mixed_score.get("reasons", [])),
        "Generated YAML scorer must downgrade cases that check original entry state after clicking Baidu Netdisk",
    )
    baidu_mixed_repair = repair_generated_yaml_executable_gate_issues(baidu_mixed_state_yaml)
    baidu_mixed_content = baidu_mixed_repair.get("content", "")
    require(
        baidu_mixed_repair.get("changed")
        and "普通证件照页面展示百度网盘入口" not in baidu_mixed_content
        and "百度网盘授权页、登录页、文件选择页、空状态页或提示页已打开" in baidu_mixed_content
        and dry_run_midscene_yaml(baidu_mixed_content, app_package="com.xbxxhz.box").get("ok") is True,
        "Generated YAML repair must convert Baidu Netdisk post-click original-page checks to target-page waits/assertions",
    )
    baidu_bad_dry = dry_run_midscene_yaml(baidu_mixed_state_yaml, app_package="com.xbxxhz.box")
    require(
        baidu_bad_dry.get("ok") is False
        and any("点击百度网盘后仍在等待原业务页入口展示" in err for err in baidu_bad_dry.get("errors", [])),
        "Mock dry-run must catch Baidu Netdisk semantic state mismatch before Runner execution",
    )
    conditional_tap_yaml = """android:
  tasks:
    - name: 文档打印百度网盘入口展示与点击验证
      flow:
        - launch: com.xbxxhz.box
        - aiWaitFor: App 首页加载完成
        - aiTap: 如果当前不在首页，点击底部「首页」回到首页
        - aiWaitFor: 文档打印入口可见
        - aiTap: 百度网盘入口
        - aiWaitFor: 文档打印首页的百度网盘入口可见，点击后进入百度网盘授权、登录或文件选择流程
"""
    conditional_score = score_midscene_yaml_executable(conditional_tap_yaml)
    require(
        conditional_score.get("executionLevel") != "executable"
        and any("条件式 aiTap" in reason for reason in conditional_score.get("reasons", [])),
        "Generated YAML scorer must block conditional aiTap prompts before Runner execution",
    )
    conditional_repair = repair_generated_yaml_executable_gate_issues(conditional_tap_yaml)
    conditional_content = conditional_repair.get("content", "")
    require(
        conditional_repair.get("changed")
        and "如果当前不在首页" not in conditional_content
        and "App 首页或底部导航已稳定显示" in conditional_content
        and "百度网盘授权页、登录页、文件选择页、空状态页或提示页已打开" in conditional_content,
        "Generated YAML repair must convert conditional aiTap and mixed Baidu post-click checks before dispatch",
    )
    photo_entry_yaml = """android:
  tasks:
    - name: 普通照片打印百度网盘入口展示与点击验证
      flow:
        - launch: com.xbxxhz.box
        - aiWaitFor: App 首页加载完成
        - aiTap: 点击首页或基础打印入口中的「相册导入」进入普通照片打印
        - aiWaitFor: 等待进入「5寸照片」或普通照片打印页面，页面展示「相册导入」「微信导入」「相机拍照」等导入方式
        - aiTap: 百度网盘入口
        - aiWaitFor: 百度网盘授权页、登录页、文件选择页、空状态页或提示页已打开
"""
    require(
        dry_run_midscene_yaml(photo_entry_yaml, app_package="com.xbxxhz.box").get("ok") is True,
        "Mock dry-run must not mistake photo/album entry display checks for unstable image upload flows",
    )
    fallback_steps, fallback_assertions = ai_skill_service._fallback_steps_for_scenario({
        "feature": "普通证件照",
        "requirement_point": "普通证件照导入方式中展示百度网盘入口，点击后进入百度网盘导入或授权流程。",
    })
    fallback_blob = "\n".join(fallback_steps + fallback_assertions)
    require(
        "普通证件照页面展示百度网盘入口" not in fallback_blob
        and "百度网盘授权页、登录页、文件选择页、空状态页或提示页" in fallback_blob
        and "未停留在原入口页" in fallback_blob,
        "Local Baidu Netdisk fallback generation must not assert the original business page after the click",
    )
    service_repaired_assertion_tap = repair_generated_yaml_executable_gate_issues(assertion_tap_yaml)
    service_repaired_content = service_repaired_assertion_tap.get("content", "")
    require(
        service_repaired_assertion_tap.get("changed")
        and "aiTap: 检查页面是否展示" not in service_repaired_content
        and "aiWaitFor: 页面展示" in service_repaired_content
        and dry_run_midscene_yaml(service_repaired_content, app_package="com.xbxxhz.box").get("ok") is True,
        "Generated YAML service must repair assertion-like aiTap prompts before persisting files",
    )
    service_static_repair = repair_generated_yaml_static_errors(assertion_tap_yaml, app_package="com.xbxxhz.box", max_attempts=0)
    require(
        service_static_repair.get("ok")
        and service_static_repair.get("changed")
        and "local_executable_gate_repair" in json.dumps(service_static_repair.get("attempts") or [], ensure_ascii=False),
        "YAML static repair must include local executable-gate repair, not only parser repair",
    )
    prefixed_action_yaml = """android:
  tasks:
    - name: 文档打印首页展示百度网盘入口
      flow:
        - runAdbShell: "input keyevent 3; sleep 1; size=$(wm size | grep -oE '[0-9]+x[0-9]+' | tail -1); if [ -n \\"$size\\" ]; then w=${size%x*}; h=${size#*x}; input keyevent 187; fi"
        - ai: "runAdbShell: am force-stop com.xbxxhz.box"
        - ai: "sleep: 1500"
        - ai: "launch: com.xbxxhz.box"
        - ai: "sleep: 3000"
        - aiWaitFor: "aiWaitFor: 回到App首页并等待页面加载稳定"
        - aiTap: "aiTap: 点击「文档打印」icon"
        - aiTap: "aiWaitFor: 页面展示「百度网盘」文案，入口可见且可点击"
        - aiTap: "aiAssert: 页面展示「百度网盘」文案，入口可见且可点击"
"""
    prefixed_score = score_midscene_yaml_executable(prefixed_action_yaml)
    require(
        prefixed_score.get("executionLevel") != "executable"
        and any("动作前缀" in reason or "${...}" in reason for reason in prefixed_score.get("reasons", [])),
        "Generated YAML scorer must block unsafe shell expansion and nested action prefixes",
    )
    prefixed_dry = dry_run_midscene_yaml(prefixed_action_yaml, app_package="com.xbxxhz.box")
    require(
        prefixed_dry.get("ok") is False
        and any("动作前缀" in err or "${...}" in err for err in prefixed_dry.get("errors", [])),
        "Mock dry-run must reject nested action prefixes before Runner execution",
    )
    repaired_prefixed = _agent_repair_missing_interaction_followups(prefixed_action_yaml)
    repaired_prefixed_content = repaired_prefixed.get("content", "")
    require(
        repaired_prefixed.get("changed")
        and "${size" not in repaired_prefixed_content
        and "wm size" not in repaired_prefixed_content
        and "input keyevent 187" not in repaired_prefixed_content
        and "runAdbShell: input keyevent 3" in repaired_prefixed_content
        and 'runAdbShell: am force-stop com.xbxxhz.box' in repaired_prefixed_content
        and 'aiWaitFor: 页面展示「百度网盘」文案，入口可见且可点击' in repaired_prefixed_content
        and 'aiAssert: 页面展示「百度网盘」文案，入口可见且可点击' in repaired_prefixed_content
        and score_midscene_yaml_executable(repaired_prefixed_content).get("executionLevel") == "executable",
        "Agent validation must repair prefixed action values and strip unsafe recent-task cleanup before Runner execution",
    )
    require(
        dry_run_midscene_yaml(repaired_prefixed_content, app_package="com.xbxxhz.box").get("ok") is True,
        "Agent-repaired prefixed YAML must load through mock dry-run before Runner execution",
    )
    boundary_smoke_yaml = """android:
  tasks:
    - name: 未安装百度App时WebView降级跳转成功
      flow:
        - launch: com.kfb.model
        - aiWaitFor: 文档打印首页加载完成，百度网盘入口可见
        - aiTap: 百度网盘入口
        - aiWaitFor: WebView 或下载提示页面已打开
        - aiAssert: 未安装百度App时出现可理解的降级提示
"""
    boundary_smoke_score = score_midscene_yaml_executable(boundary_smoke_yaml)
    require(
        boundary_smoke_score.get("executionLevel") == "executable"
        and boundary_smoke_score.get("smokeCandidate") is False,
        "Boundary/permission/degraded executable cases must not enter the first smoke batch",
    )
    baidu_visibility_yaml = """android:
  tasks:
    - name: 文档打印首页百度网盘入口可见性验证
      flow:
        - launch: com.xbxxhz.box
        - aiWaitFor: 小白学习打印首页已加载，底部首页导航可见
        - aiWaitFor: 文档打印首页展示百度网盘入口
        - aiAssert: 文档打印首页百度网盘入口可见
"""
    baidu_visibility_score = score_midscene_yaml_executable(baidu_visibility_yaml)
    require(
        baidu_visibility_score.get("executionLevel") == "executable"
        and baidu_visibility_score.get("smokeCandidate") is True,
        "Baidu netdisk entry visibility-only YAML should be eligible for first smoke",
    )
    baidu_visibility_with_diagnostic_score = {
        **baidu_visibility_score,
        "smokeCandidate": True,
        "reason": "入口展示可执行；提醒：点击后可能涉及第三方授权。",
        "taskScores": [{
            **((baidu_visibility_score.get("taskScores") or [{}])[0]),
            "name": "文档打印首页百度网盘入口展示",
            "smokeCandidate": True,
            "reasons": ["诊断说明：点击后可能进入第三方授权或外部 App。"],
        }],
    }
    selected, blocked = rank_executable_yaml_refs([
        {"file": "01-baidu-entry.yaml", "module": "AI_Agent草稿", "executableScore": baidu_visibility_with_diagnostic_score},
    ], limit=3)
    require(
        len(selected) == 1
        and selected[0]["file"] == "01-baidu-entry.yaml"
        and len(blocked) == 0,
        "Runner gate must not exclude entry-display smoke cases because of scorer diagnostic risk text",
    )
    baidu_external_click_yaml = """android:
  tasks:
    - name: 文档打印百度网盘入口展示与点击验证
      flow:
        - launch: com.xbxxhz.box
        - aiWaitFor: 小白学习打印首页已加载，底部首页导航可见
        - aiWaitFor: 文档打印首页展示百度网盘入口
        - aiTap: 百度网盘入口
        - aiWaitFor: 百度网盘授权、登录或文件选择页面已打开
        - aiAssert: 点击百度网盘入口后进入百度网盘相关流程
"""
    baidu_external_click_score = score_midscene_yaml_executable(baidu_external_click_yaml)
    require(
        baidu_external_click_score.get("executionLevel") == "executable"
        and baidu_external_click_score.get("smokeCandidate") is False,
        "Third-party Baidu click/authorization flow can be executable but must not be selected as first smoke",
    )
    selected, blocked = rank_executable_yaml_refs([
        {"file": "baidu-click.yaml", "executableScore": baidu_external_click_score, "smoke": True},
    ], limit=3)
    require(
        len(selected) == 0
        and len(blocked) == 1
        and "没有稳定的首批冒烟候选" in str(blocked[0].get("gateReason") or ""),
        "Runner gate must not fall back to third-party click flows when no stable first-smoke candidate exists",
    )
    selected, blocked = rank_executable_yaml_refs([
        {"file": "02-p1.yaml", "executableScore": executable_score},
        {"file": "unstable.yaml", "executableScore": unstable_score},
        {"file": "01-p0-main.yaml", "executableScore": {
            **executable_score,
            "taskScores": [{**(executable_score.get("taskScores") or [{}])[0], "priority": "P0", "mainBusinessChain": True}],
        }, "smoke": True},
    ], limit=1)
    require(
        len(selected) == 1
        and selected[0]["file"] == "01-p0-main.yaml"
        and len(blocked) == 2
        and any("超过自动冒烟首批上限" in str(item.get("gateReason") or "") for item in blocked),
        "Runner gate must rank explicit smoke YAML first and defer overflow executable YAML",
    )
    selected, blocked = rank_executable_yaml_refs([
        {"file": "candidate.yaml", "executableScore": {**executable_score, "smokeCandidate": True}},
        {"file": "not-candidate.yaml", "executableScore": {
            **executable_score,
            "smokeCandidate": False,
            "taskScores": [{**(executable_score.get("taskScores") or [{}])[0], "smokeCandidate": False, "mainBusinessChain": False}],
        }},
    ], limit=3)
    require(
        len(selected) == 1
        and selected[0]["file"] == "candidate.yaml"
        and any("非首批冒烟候选" in str(item.get("gateReason") or "") for item in blocked),
        "Runner gate must accept scorer smokeCandidate and defer non-candidates when candidates exist",
    )
    selected, blocked = rank_executable_yaml_refs([
        {"file": "fallback-entry.yaml", "executableScore": {
            **executable_score,
            "smokeCandidate": False,
            "taskScores": [{**(executable_score.get("taskScores") or [{}])[0], "name": "文档打印首页百度网盘入口可见", "smokeCandidate": False, "mainBusinessChain": True}],
        }},
    ], limit=3)
    require(
        len(selected) == 1
        and selected[0]["file"] == "fallback-entry.yaml"
        and selected[0].get("fallbackSmokeSelection") is True
        and "_sourceRowId" not in selected[0]
        and len(blocked) == 0,
        "Runner gate must fall back to a safe executable short-chain case when no explicit smoke candidate exists",
    )
    normal_smoke_score = {
        **executable_score,
        "smokeCandidate": True,
        "taskScores": [{**(executable_score.get("taskScores") or [{}])[0], "name": "文档打印首页正常展示百度网盘入口", "smokeCandidate": True, "mainBusinessChain": True}],
    }
    excluded_smoke_score = {
        **executable_score,
        "smokeCandidate": True,
        "taskScores": [{**(executable_score.get("taskScores") or [{}])[0], "name": "非会员用户访问入口权限边界校验", "smokeCandidate": True, "mainBusinessChain": False}],
    }
    popup_score = {
        **executable_score,
        "smokeCandidate": True,
        "taskScores": [{**(executable_score.get("taskScores") or [{}])[0], "name": "跳转过程中弹窗拦截处理", "smokeCandidate": True, "mainBusinessChain": False}],
    }
    selected, blocked = rank_executable_yaml_refs([
        {"file": "01-normal.yaml", "executableScore": normal_smoke_score},
        {"file": "02-boundary.yaml", "executableScore": excluded_smoke_score, "smoke": True},
        {"file": "03-popup.yaml", "executableScore": popup_score, "smoke": True},
    ], limit=3)
    require(
        len(selected) == 1
        and selected[0]["file"] == "01-normal.yaml"
        and any("异常/边界/权限类用例" in str(item.get("gateReason") or "") for item in blocked),
        "Runner gate must exclude abnormal/boundary/permission cases from the first smoke batch even when AI marks them as smoke",
    )
    selected, blocked = rank_executable_yaml_refs([
        {"file": "fallback-1.yaml", "executableScore": {
            **executable_score,
            "smokeCandidate": False,
            "taskScores": [{**(executable_score.get("taskScores") or [{}])[0], "smokeCandidate": False, "mainBusinessChain": False}],
        }},
        {"file": "fallback-2.yaml", "executableScore": {
            **executable_score,
            "smokeCandidate": False,
            "taskScores": [{**(executable_score.get("taskScores") or [{}])[0], "smokeCandidate": False, "mainBusinessChain": False}],
        }},
    ], limit=1)
    require(
        len(selected) == 1
        and selected[0].get("fallbackSmokeSelection") is True
        and any("超过自动冒烟首批上限" in str(item.get("gateReason") or "") for item in blocked),
        "Runner gate must fall back to top executable YAML instead of failing with zero first-batch cases",
    )
    execution_plan = build_generated_yaml_execution_plan(
        [{"file": "01-normal.yaml", "executableScore": normal_smoke_score}, {"file": "02-boundary.yaml", "executableScore": boundary_smoke_score}],
        [{"file": "01-normal.yaml", "executableScore": normal_smoke_score}],
        [{"file": "02-boundary.yaml", "executableScore": boundary_smoke_score, "gateReason": "非首批冒烟候选，延后执行"}],
        [],
        smoke_limit=1,
        first_smoke_upper=3,
        expand_limit=20,
        expand_batch_limit=5,
    )
    require(
        execution_plan.get("readiness", {}).get("canDispatch") is True
        and execution_plan.get("counts", {}).get("selectedSmoke") == 1
        and execution_plan.get("counts", {}).get("deferredExecutable") == 1,
        "Generated YAML execution plan must expose selected smoke and deferred executable cases",
    )
    product_smoke_blocker = classify_generated_yaml_smoke_blocker(
        [{"failureType": "ASSERTION_FAILED", "reason": "断言失败：百度网盘入口未展示"}],
        [],
        smoke_total=1,
        smoke_failed=1,
        timeout_count=0,
    )
    dry_run_smoke_blocker = classify_generated_yaml_smoke_blocker(
        [],
        [{"file": "bad.yaml", "reason": "YAML dry-run 未通过", "errors": ["非官方 action"]}],
        smoke_total=1,
        smoke_failed=0,
        timeout_count=0,
    )
    require(
        product_smoke_blocker.get("block") is False
        and dry_run_smoke_blocker.get("block") is True,
        "Smoke gate must distinguish product assertion failures from YAML/dry-run execution blockers",
    )
    scoped_payload = apply_generated_case_scope_gate({
        "analysis": {
            "requirement_points": ["基础打印模块新增百度网盘入口"],
            "business_flow": ["进入文档打印首页", "展示百度网盘入口", "点击百度网盘入口"],
        },
        "cases": [
            {
                "case_id": "BAIDU_001",
                "title": "文档打印首页正常展示百度网盘入口",
                "steps": ["进入文档打印首页", "等待百度网盘入口显示"],
                "assertions": ["百度网盘入口可见"],
            },
            {
                "case_id": "HISTORY_001",
                "title": "历史打印记录干扰入口可见性",
                "steps": ["进入历史打印记录", "检查入口显示"],
                "assertions": ["入口可见"],
            },
        ],
    })
    require(
        len(scoped_payload.get("cases") or []) == 1
        and "百度网盘" in scoped_payload["cases"][0]["title"]
        and any("历史打印记录" in str(item.get("title") or "") for item in scoped_payload.get("manual_cases") or []),
        "Generated cases outside the current requirement scope must be kept out of the auto-run case pool",
    )
    baidu_constraints = build_requirement_semantic_constraints_text([
        "基础打印模块增加百度网盘入口：三方文档打印百度网盘入口移至第2个，本地文档之后；"
        "照片打印包含普通照片打印、普通证件照、智能证件照、照片拼版导入时增加百度网盘导入选项；"
        "扫描复印首页增加百度网盘导入入口。"
    ], "基础打印模块增加百度网盘入口")
    require(
        "三方文档打印" in baidu_constraints
        and "照片拼版" in baidu_constraints
        and "扫描复印" in baidu_constraints
        and "首页备份2" in baidu_constraints,
        "Baidu Netdisk requirement constraints must preserve real business scope and forbid Figma internal page names",
    )
    figma_name_yaml = """android:
  tasks:
    - name: 文档打印首页备份2展示百度网盘入口
      flow:
        - launch: com.xbxxhz.box
        - aiWaitFor: 文档打印首页加载完成
        - aiTap: 百度网盘入口
        - aiWaitFor: 百度网盘授权页或导入页打开
        - aiAssert: 百度网盘入口已进入授权或导入流程
"""
    figma_name_score = score_midscene_yaml_executable(figma_name_yaml)
    require(
        figma_name_score.get("executionLevel") != "executable"
        and any("Figma 内部页名" in reason for reason in figma_name_score.get("reasons", [])),
        "Generated YAML with Figma internal page names must not be treated as executable",
    )
    unsupported_expansion_yaml = """android:
  tasks:
    - name: 弹窗遮挡场景下点击百度网盘入口
      flow:
        - launch: com.xbxxhz.box
        - aiWaitFor: 文档打印首页加载完成
        - aiTap: 百度网盘入口
        - aiWaitFor: 百度网盘授权页或导入页打开
        - aiAssert: 百度网盘入口可用
"""
    unsupported_expansion_score = score_midscene_yaml_executable(unsupported_expansion_yaml)
    require(
        unsupported_expansion_score.get("executionLevel") != "executable"
        and any("鲁棒性扩展" in reason for reason in unsupported_expansion_score.get("reasons", [])),
        "Generated YAML with unsupported robustness expansion must require review instead of auto execution",
    )

    examples = [{
        "title": "AI建模入口",
        "module": "AI测试",
        "file": "AI测试/入口.yaml",
        "score": 99,
        "matched_terms": ["AI建模"],
        "snippet": valid_yaml,
    }]
    patterns = extract_yaml_patterns_from_examples(examples)
    require(patterns and "aiTap" in patterns[0].get("actions", []), "YAML baseline pattern extractor must capture action sequences")
    contract_text = build_yaml_pattern_contract_text(patterns, contract)
    require("禁止生成白名单外 action" in contract_text and "动作序列" in contract_text, "YAML pattern contract must constrain model generation")
    block_examples = [{
        "title": "单任务片段",
        "file": "base.yaml",
        "actions": ["name", "aiTap", "aiAssert"],
        "snippet": """- name: 单任务片段
  flow:
    - aiTap: 首页入口
    - aiAssert: 结果出现
""",
    }]
    block_patterns = extract_yaml_patterns_from_examples(block_examples)
    require(
        block_patterns and "name" not in block_patterns[0].get("actions", []) and block_patterns[0].get("actions") == ["aiTap", "aiAssert"],
        "YAML pattern extractor must parse task-block snippets and never treat name as an action",
    )
    profile_text = build_yaml_pattern_contract_text(block_patterns, contract)
    require("至少保留一个最终业务 aiAssert" not in profile_text, "YAML pattern contract must not force extra aiAssert")

    templates = select_best_baseline_template("AI建模 图片建模 上传图片", [
        {
            "title": "图片建模上传",
            "file": "AI测试/图片建模上传.yaml",
            "actions": ["aiTap", "aiWaitFor", "aiInput"],
            "snippet": valid_yaml,
        },
        {"title": "无关客服", "file": "客服.yaml", "actions": ["aiTap"]},
    ])
    require(templates and templates[0].get("title") == "图片建模上传", "YAML template matcher must select the most relevant baseline template")
    template_text = build_yaml_template_matcher_text(templates)
    require("套模板填槽" in template_text and "不要重新设计结构" in template_text, "YAML template matcher prompt must force template-based generation")
    template_eval = evaluate_baseline_template_matching([
        {
            "title": "图片建模上传",
            "file": "AI测试/图片建模上传.yaml",
            "actions": ["aiTap", "aiWaitFor", "aiInput"],
            "snippet": valid_yaml,
        },
        {
            "title": "语音创作长按",
            "file": "AI测试/语音创作长按.yaml",
            "actions": ["aiTap", "aiWaitFor"],
            "snippet": valid_yaml,
        },
    ], samples=[{"name": "图片建模上传", "requirement": "图片建模 上传图片", "must_match_any": ["图片", "上传"]}])
    require(template_eval.get("passed") == 1 and template_eval.get("samples"), "YAML template matcher quality eval must report fixed sample results")
    dry = dry_run_midscene_yaml(valid_yaml)
    require(dry.get("ok") and dry.get("mode") == "mock_dry_run" and dry.get("runnerTouched") is False, "YAML dry-run must validate without touching Runner/device")
    repair = repair_generated_yaml_static_errors(valid_yaml, max_attempts=0)
    require(repair.get("ok") and repair.get("dryRun", {}).get("runnerTouched") is False, "YAML static repair must short-circuit valid YAML without touching Runner/device")
    dry_bad = dry_run_midscene_yaml(invalid_yaml)
    require(not dry_bad.get("ok") and dry_bad.get("errors"), "YAML dry-run must return actionable errors for invalid YAML")


def check_business_flow_filters_product_metrics():
    from task_server.prompts.builders.business_context_builder import BusinessContextBuilder
    from task_server.services import agent_service

    noisy_requirement = (
        "业务主链约束：生成模型释出 → 生成一模一样的 ip 样子 → 生成模型的 token 消耗 → 提高AI\n"
        "AI建模页包含开始创作、图片建模、语音输入-长按，生成模型后查看结果。"
    )
    built = BusinessContextBuilder().build({
        "target": "AI建模需求生成并执行",
        "requirementText": noisy_requirement,
    })
    builder_flow_text = " ".join(built.get("business_flow") or [])
    require("token" not in builder_flow_text.lower() and "一模一样" not in builder_flow_text and "提高AI" not in builder_flow_text, "Prompt business flow must filter product metrics and model goals")
    require("AI建模" in builder_flow_text and ("语音" in builder_flow_text or "长按" in builder_flow_text), "Prompt business flow must keep real AI modeling user actions")

    run = {
        "runId": "agent-static-flow",
        "target": "AI建模需求生成并执行",
        "artifacts": {"sourceContext": {"requirementText": noisy_requirement}},
    }
    constraint = agent_service._ensure_business_flow_constraint(run)
    flow_text = " ".join(constraint.get("businessFlow") or [])
    require("token" not in flow_text.lower() and "一模一样" not in flow_text and "提高AI" not in flow_text, "Agent runtime business flow must filter product metrics and model goals")
    require("AI建模" in flow_text and ("语音" in flow_text or "长按" in flow_text), "Agent runtime business flow must keep AI modeling user actions")


def check_agent_prepared_figma_context_reuse():
    from task_server.services import agent_service, yaml_service

    old_agent_draft_dir = agent_service.AGENT_DRAFT_DIR
    old_asset_dir = yaml_service.ASSET_DIR
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            agent_service.AGENT_DRAFT_DIR = os.path.join(temp_dir, "agent-drafts")
            yaml_service.ASSET_DIR = os.path.join(temp_dir, "assets")
            png_b64 = base64.b64encode(b"\x89PNG\r\n\x1a\nstatic-figma").decode("ascii")
            used_pages = [{
                "page_id": "4458:1905",
                "page_name": "语音输入-长按",
                "route": "AI建模/语音输入",
                "screenshot": "figma-voice.png",
                "figma": {"node_id": "4458:1905", "direct_group": True, "relevance_score": 13},
            }]
            path, _payload = agent_service._persist_agent_prepared_figma_context(
                {"runId": "agent-static-figma"},
                "https://figma.example/design/file?node-id=4458-1905",
                ["[Figma设计稿页面]\n语音输入-长按\n按住说话"],
                [{"name": "figma-voice.png", "mime": "image/png", "base64": png_b64}],
                used_pages,
                [],
                [],
            )
            prepared = agent_service._agent_prepared_figma_context_from_source({"preparedFigmaContextPath": path})
            require(prepared.get("imageAssets") and prepared["imageAssets"][0].get("base64"), "Agent prepared Figma cache must retain reusable image content outside run history")
            normalized = yaml_service._prepared_figma_context_from_request({"preparedFigmaContext": prepared})
            require(normalized.get("usedPages") and normalized.get("imageAssets"), "YAML generation must accept prepared Figma context")
            saved = yaml_service._save_prepared_figma_design_assets("case-static-figma", normalized, title="AI建模", module="AI测试")
            require(saved and saved[0].get("source") == "figma", "Prepared Figma images must be saved as current case UI design assets")
            meta = yaml_service.list_case_ui_design_assets("case-static-figma")
            require((meta.get("designs") or [{}])[0].get("exists"), "Saved prepared Figma UI design asset must be readable for report/download UI")
    finally:
        agent_service.AGENT_DRAFT_DIR = old_agent_draft_dir
        yaml_service.ASSET_DIR = old_asset_dir


def check_agent_risk_detail_explains_source():
    from task_server.services import agent_service

    yaml_text = """android:
  tasks:
    - name: "筛选清空提示验证"
      flow:
        - launch: com.kfb.model
        - aiTap: "清空筛选条件"
        - aiAssert: "筛选条件已恢复默认"
"""
    run = {
        "runId": "agent-static-risk-detail",
        "target": "筛选条件验证",
        "executionMode": "RUNNER_JOB",
        "artifacts": {
            "yamlRefs": [{
                "type": "text",
                "content": yaml_text,
                "confirmed": False,
            }]
        },
        "steps": [{"step": "EXECUTION_PRECHECK", "status": "RUNNING"}],
    }
    detail = agent_service._evaluate_risk_detail(run)
    require(detail.get("level") == "HIGH" and detail.get("keyword") == "清空", "Agent risk detail must identify the high-risk keyword")
    require(detail.get("source") == "生成 YAML 草稿" and "清空筛选条件" in detail.get("snippet", ""), "Agent risk detail must explain the source and triggering snippet")
    agent_service._tool_execution_precheck(run)
    precheck = run.get("artifacts", {}).get("executionPrecheck") or {}
    risk_check = next((item for item in precheck.get("checks") or [] if item.get("name") == "high_risk_confirm"), {})
    require("来源：生成 YAML 草稿" in risk_check.get("detail", "") and "触发片段" in risk_check.get("detail", ""), "Execution precheck must expose risk source and snippet")
    require(any(item.get("name") == "high_risk_confirm" for item in precheck.get("warnings") or []), "Runner clear actions must warn without blocking")
    require(not any(item.get("type") == "high_risk_action" for item in run.get("pendingConfirmations") or []), "Runner clear-action warnings must not create a blocking high-risk confirmation")

    delete_run = {
        "runId": "agent-static-risk-delete-runner",
        "target": "删除入口验证",
        "executionMode": "RUNNER_JOB",
        "artifacts": {
            "yamlRefs": [{
                "type": "text",
                "content": """android:
  tasks:
    - name: "删除旧模块入口验证"
      flow:
        - launch: com.kfb.model
        - aiTap: "删除旧模块入口"
        - aiAssert: "AI建模入口展示正确"
""",
                "confirmed": False,
            }]
        },
        "steps": [{"step": "EXECUTION_PRECHECK", "status": "RUNNING"}],
    }
    agent_service._tool_execution_precheck(delete_run)
    delete_precheck = delete_run.get("artifacts", {}).get("executionPrecheck") or {}
    require(any(item.get("name") == "high_risk_confirm" for item in delete_precheck.get("warnings") or []), "Runner delete actions must warn without blocking")
    require(
        not any(item.get("name") == "high_risk_confirm" for item in delete_precheck.get("blockers") or []),
        "Runner delete actions must not create a high-risk blocker"
    )


def check_agent_requirement_background_delete_is_not_high_risk():
    from task_server.services import agent_service

    requirement_text = (
        "AI建模改版：新增 AI 建模模块，导航栏中间的 3D 改为 AI 建模首页；"
        "文字建模、图片建模删除，整合为 AI建模，并排序在新手必学后面。"
    )
    run = {
        "runId": "agent-static-risk-background",
        "target": "AI建模需求验证",
        "executionMode": "RUNNER_JOB",
        "artifacts": {
            "sourceContext": {
                "requirementText": requirement_text,
                "figmaText": requirement_text,
            }
        },
    }
    detail = agent_service._evaluate_risk_detail(run)
    require(detail.get("level") == "LOW", "Requirement/Figma background delete wording must not become a blocking high risk")
    require(detail.get("keyword") == "删除" and detail.get("blocking") is False, "Requirement background delete should be recorded as non-blocking context")
    summary = agent_service._risk_detail_summary(detail)
    require("需求背景关键词" in summary and "不阻断" in summary, "Non-blocking requirement risk summary must be understandable")
    call = agent_service._tool_risk_review(run)
    require(call.get("riskLevel") == "low" and "需求背景关键词" in call.get("outputSummary", ""), "Risk review must not block product-change delete wording")

    dangerous_run = {
        "runId": "agent-static-risk-delete-action",
        "target": "删除作品流程验证",
        "executionMode": "RUNNER_JOB",
        "artifacts": {
            "yamlRefs": [{
                "type": "text",
                "content": """android:
  tasks:
    - name: 删除作品流程验证
      flow:
        - launch: com.kfb.model
        - aiTap: "点击删除作品按钮"
        - aiAssert: "作品已删除"
""",
                "confirmed": True,
            }]
        },
    }
    dangerous_detail = agent_service._evaluate_risk_detail(dangerous_run)
    require(dangerous_detail.get("level") == "HIGH" and dangerous_detail.get("blocking") is True, "Real delete actions in YAML must remain blocking high risk")


def check_agent_generation_orphan_recovery():
    from task_server.services import agent_service, yaml_service

    old_runs_file = agent_service.AGENT_RUNS_FILE
    old_generate_dir = yaml_service.GENERATE_JOB_DIR
    old_started_ts = agent_service.AGENT_SERVICE_STARTED_TS
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            agent_service.AGENT_RUNS_FILE = os.path.join(temp_dir, "agent-runs.json")
            yaml_service.GENERATE_JOB_DIR = os.path.join(temp_dir, "generate-jobs")
            os.makedirs(yaml_service.GENERATE_JOB_DIR, exist_ok=True)
            now_ts = time.time()
            agent_service.AGENT_SERVICE_STARTED_TS = now_ts
            stale_text = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now_ts - 30))
            run = {
                "runId": "agent-static-orphan",
                "status": "RUNNING",
                "currentStep": "GENERATE_YAML",
                "progress": 30,
                "target": "AI建模需求验证",
                "steps": [
                    {"step": "PLAN", "status": "SUCCESS", "summary": "已规划"},
                    {"step": "GENERATE_YAML", "status": "RUNNING", "startedAt": stale_text, "summary": "生成中"},
                    {"step": "VALIDATE_YAML", "status": "PENDING"},
                    {"step": "RUN_SONIC", "status": "PENDING"},
                ],
                "artifacts": {},
            }
            agent_service.save_agent_runs([run])
            yaml_service.save_generate_job({
                "job_id": agent_service._agent_generate_progress_job_id(run),
                "type": "agent_generate_yaml",
                "status": "running",
                "progress": 65,
                "step": "视觉校准",
                "message": "正在校准入口、步骤和断言",
                "created_at": stale_text,
                "started_at": stale_text,
                "updated_at": stale_text,
            })
            rows = agent_service.list_agent_runs(limit=5)
            require(rows and rows[0].get("status") == "FAILED", "Agent list refresh must recover orphaned GENERATE_YAML runs after worker loss")
            recovered = agent_service.get_agent_run("agent-static-orphan")
            require(recovered.get("status") == "FAILED" and recovered.get("currentStep") == "GENERATE_YAML", "Agent detail refresh must return recovered failed run")
            pipeline = (recovered.get("artifacts") or {}).get("generationPipeline") or {}
            require(pipeline.get("interruptedByWorkerLost") is True, "Recovered Agent generation must explain worker/model interruption")
            require(pipeline.get("interruptedByServiceRestart") is not True, "Recovered Agent generation must not blame user-visible service restart by default")
            require("服务重启后没有恢复后台线程" not in recovered.get("error", ""), "Recovered Agent error must not imply the user restarted the service")
            skipped = [s for s in recovered.get("steps") or [] if s.get("step") == "VALIDATE_YAML"]
            require(skipped and skipped[0].get("status") == "SKIPPED", "Recovered Agent generation failure must skip dependent steps")
    finally:
        agent_service.AGENT_RUNS_FILE = old_runs_file
        yaml_service.GENERATE_JOB_DIR = old_generate_dir
        agent_service.AGENT_SERVICE_STARTED_TS = old_started_ts


def check_yaml_reference_examples_are_general_step_library():
    from task_server.services import yaml_service
    from task_server.services import yaml_baseline_cache
    from task_server.services.yaml_baseline_cache import get_yaml_baseline_cache_status, search_baseline_examples

    examples = yaml_service.collect_yaml_reference_examples(
        "AI建模 图片建模 语音输入 选择图片 上传图片 跳转商城 微信导入 模型生成",
        module="AI测试",
        limit=5,
    )
    require(examples, "YAML generation must retrieve reusable examples from the existing YAML library")
    cached_examples = search_baseline_examples("AI建模 图片建模 上传图片", module="AI测试", limit=3)
    cache_status = get_yaml_baseline_cache_status()
    require(cached_examples and cache_status.get("caseCount", 0) >= len(cached_examples), "YAML baseline cache must provide searchable baseline snippets")
    require("cacheHit" in cache_status and "fingerprint" in cache_status, "YAML baseline cache status must expose cache hit and fingerprint")
    require(
        str(cache_status.get("configuredPath") or "").endswith("/midscene-tasks/cache/yaml-baseline-cache.json"),
        "YAML baseline cache must default to TASK_DIR/cache/yaml-baseline-cache.json",
    )
    old_cache = yaml_baseline_cache._MEMORY_CACHE
    old_cache_at = yaml_baseline_cache._MEMORY_CACHE_AT
    old_calc = yaml_baseline_cache.calc_baseline_fingerprint
    try:
        yaml_baseline_cache._MEMORY_CACHE = {"version": yaml_baseline_cache.CACHE_VERSION, "items": [], "fingerprint": "stale"}
        yaml_baseline_cache._MEMORY_CACHE_AT = time.time()
        def _raise_if_called():
            raise AssertionError("fingerprint should not be calculated while memory TTL is valid")
        yaml_baseline_cache.calc_baseline_fingerprint = _raise_if_called
        yaml_baseline_cache.get_yaml_baseline_cache(force=False)
    finally:
        yaml_baseline_cache.calc_baseline_fingerprint = old_calc
        yaml_baseline_cache._MEMORY_CACHE = old_cache
        yaml_baseline_cache._MEMORY_CACHE_AT = old_cache_at
    all_text = "\n".join(
        " ".join([
            str(item.get("title") or ""),
            str(item.get("file") or ""),
            " ".join(item.get("actions") or []),
            str(item.get("baseline_path") or ""),
        ])
        for item in examples
    )
    require("aiTap" in all_text and "aiWaitFor" in all_text, "YAML reference examples must expose executable Midscene step actions")
    prompt_text = yaml_service.build_yaml_reference_examples_text(examples)
    require("相似成功基线写法参考" in prompt_text and "你不是自由生成 YAML" in prompt_text, "YAML reference prompt must force baseline imitation")
    require("只能替换业务对象、按钮文案、输入内容和断言目标" in prompt_text, "YAML reference prompt must constrain AI to business-variable replacement")


def check_generated_yaml_uses_single_final_assertion():
    from task_server.services import yaml_service

    _, yaml_text = yaml_service.cases_to_midscene_yaml({
        "_automation_ready": True,
        "title": "AI建模入口",
        "cases": [{
            "title": "AI建模入口到达",
            "app_package": "com.kfb.model",
            "steps": [
                {"action": "点击首页 AI建模入口", "expected": "首页 AI建模入口可见"},
                {"action": "点击开始创作", "expected": "开始创作面板已打开"},
                {"action": "进入图片建模上传入口", "expected": "图片建模上传区域可见"},
            ],
            "expected_result": "图片建模上传入口、提示文案或空态区域可见",
            "assertions": [
                "首页 AI建模入口可见",
                "开始创作面板已打开",
                "图片建模上传入口、提示文案或空态区域可见",
            ],
        }],
    }, app_package="com.kfb.model")
    require(yaml_text.count("aiAssert:") == 1, "Generated YAML must keep one final business assertion by default")
    require(re.search(r"aiAssert:\s*[\"']?图片建模上传入口、提示文案或空态区域可见[\"']?", yaml_text), "Generated YAML must keep the final expected business assertion")
    require('aiAssert: "首页 AI建模入口可见"' not in yaml_text, "Generated YAML must not turn every step expected value into aiAssert")
    require(yaml_service.validate_midscene_yaml(yaml_text).get("ok") is True, "Single-assertion generated YAML must remain executable")
    stability = yaml_service.review_generated_yaml_smoke_stability(yaml_text)
    require(stability.get("ok") is True and stability.get("assertCount") == 1 and stability.get("launchGuard") is True, "Generated YAML smoke-stability review must inspect assertion density and launch guards")
    _, no_assert_yaml = yaml_service.cases_to_midscene_yaml({
        "_automation_ready": True,
        "title": "AI建模入口",
        "cases": [{
            "title": "AI建模入口到达",
            "app_package": "com.kfb.model",
            "steps": ["点击首页 AI建模入口", "等待 AI建模页面打开"],
        }],
    }, app_package="com.kfb.model")
    require("aiAssert:" not in no_assert_yaml, "Generated YAML must not invent aiAssert when the case has no assertion material")


def check_ai_skills_receive_yaml_reference_context():
    from task_server.services import ai_skill_service

    calls = []
    original_run_ai_skill = ai_skill_service.run_ai_skill

    def fake_run_ai_skill(skill_name, payload=None, **_kwargs):
        calls.append((skill_name, payload or {}, _kwargs))
        if skill_name == "requirement_analyzer":
            return {
                "business_goals": ["验证 AI 建模入口"],
                "roles": ["普通用户"],
                "entry_points": ["首页 AI建模"],
                "state_assumptions": [],
                "data_assumptions": [],
                "visible_outcomes": ["AI建模页可见"],
                "risks": [],
                "requirement_points": ["REQ-001 AI建模入口可达"],
                "questions": [],
                "confidence": "high",
                "missing_inputs": [],
                "blockers": [],
                "assumptions": [],
                "readiness_score": 90,
                "readiness_level": "ready",
                "source_quality": {"requirement": "sufficient", "ui": "sufficient", "knowledge": "partial"},
            }
        if skill_name == "scenario_designer":
            return {"scenarios": [{
                "feature": "AI建模",
                "requirement_point": "REQ-001 AI建模入口可达",
                "scenario": "入口可达",
                "type": "正常流程",
                "design_method": ["等价类"],
                "business_path": "首页 -> AI建模",
                "expected": "AI建模页可见",
                "automation_suitable": True,
                "reason": "UI 可见",
            }]}
        if skill_name == "automation_filter":
            return {
                "cases": [{
                    "case_id": "TC-001",
                    "title": "AI建模入口可达",
                    "priority": "P1",
                    "smoke": True,
                    "scenario": "入口可达",
                    "goal": "验证 AI 建模入口",
                    "start_page": "App 首页",
                    "business_path": "首页 -> AI建模",
                    "expected_result": "AI建模页可见",
                    "repair_hints": "参考平台入口点击写法",
                    "risk": "",
                    "coverage": "REQ-001 AI建模入口可达",
                    "data_requirements": "",
                    "automation_reason": "路径短且 UI 可见",
                    "preconditions": [],
                    "steps": ["点击首页 AI建模入口"],
                    "assertions": ["AI建模页可见"],
                    "tags": ["冒烟"],
                }],
                "manual_cases": [],
                "review": {},
            }
        if skill_name == "smoke_selector":
            return {
                "smoke_case_ids": ["TC-001"],
                "review": {
                    "normal_chain_covered": True,
                    "selection_reason": "AI 推荐首批冒烟覆盖正常主链，平台负责校验数量和 case id。",
                    "rejected_case_ids": [],
                },
            }
        raise AssertionError(f"unexpected skill: {skill_name}")

    ai_skill_service.run_ai_skill = fake_run_ai_skill
    try:
        payload = ai_skill_service.build_cases_payload_from_skills(
            "AI建模测试",
            "AI测试",
            ["需求正文", "【现有 YAML 步骤经验库】\n```yaml\n- name: 入口\n  flow:\n    - aiTap: \"AI建模入口\"\n```"],
            model_config={"providerId": "qwen_plus", "model": "qwen3.6-plus"},
            generation_scope_plan={
                "size": "large",
                "targetCaseCount": 5,
                "smokeCount": 3,
                "reason": "当前需求规划 5 条",
            },
        )
    finally:
        ai_skill_service.run_ai_skill = original_run_ai_skill

    scenario_payload = next(item[1] for item in calls if item[0] == "scenario_designer")
    automation_payload = next(item[1] for item in calls if item[0] == "automation_filter")
    smoke_payload = next(item[1] for item in calls if item[0] == "smoke_selector")
    for skill_name in ("requirement_analyzer", "scenario_designer", "automation_filter", "smoke_selector"):
        kwargs = next(item[2] for item in calls if item[0] == skill_name)
        require(
            kwargs.get("model_config") == {"providerId": "qwen_plus", "model": "qwen3.6-plus"},
            f"{skill_name} must receive selected model config",
        )
    require("现有 YAML 步骤经验库" in scenario_payload.get("yaml_reference_context", ""), "Scenario designer must receive YAML reference context")
    require("现有 YAML 步骤经验库" in automation_payload.get("yaml_reference_context", ""), "Automation filter must receive YAML reference context")
    require("现有 YAML 步骤经验库" in smoke_payload.get("yaml_reference_context", ""), "Smoke selector must receive YAML reference context")
    for skill_payload in (scenario_payload, automation_payload, smoke_payload):
        targets = skill_payload.get("generation_targets") or {}
        require(
            targets.get("target_automation_cases") == 5
            and targets.get("min_automation_cases") == 5
            and targets.get("size") == "medium",
            "Scenario, automation and smoke skills must share the same platform-clamped scope plan",
        )
    require(
        len(automation_payload.get("yaml_reference_context", "")) <= 6000,
        "Automation filter must receive a bounded Top3 YAML reference instead of the full generation context",
    )
    require(
        payload.get("review", {}).get("generation_targets", {}).get("target_automation_cases") == 5,
        "Generated payload review must preserve the authoritative scope target",
    )
    require(payload["review"]["smoke_selection"]["selector_source"] == "smoke_selector.v1", "Smoke selection should use AI recommendation before platform validation")
    require(payload["review"]["smoke_selection"]["normal_chain_covered"] is True, "Smoke gate must record normal-chain coverage")
    require(payload["review"]["yaml_reference_context_used_by_skills"] is True, "AI skill review must record that YAML reference context was used")


def check_ai_skill_timeout_fallbacks_are_requirement_scoped():
    from task_server.services import ai_skill_service, yaml_service

    original_run_ai_skill = ai_skill_service.run_ai_skill
    def fail_if_ai_skill_called(*_args, **_kwargs):
        raise AssertionError("entry visibility fast path must not block on AI skill calls")

    ai_skill_service.run_ai_skill = fail_if_ai_skill_called
    try:
        fast_payload = ai_skill_service.build_cases_payload_from_skills(
            "基础打印新增百度网盘入口",
            "基础打印",
            [
                "基础打印的入口在首页   文档打印 照片打印 扫描复印",
                yaml_service.build_executable_smoke_yaml_policy_text(),
            ],
            app_package="com.xbxxhz.box",
        )
    finally:
        ai_skill_service.run_ai_skill = original_run_ai_skill

    fast_cases = fast_payload.get("cases") or []
    fast_first_blob = "\n".join((fast_cases[0].get("steps") or []) + (fast_cases[0].get("assertions") or [])) if fast_cases else ""
    require(fast_payload.get("review", {}).get("fast_path_reason"), "Baidu entry visibility requirements must use a non-blocking deterministic fast path before AI skill calls")
    require(fast_cases and fast_cases[0].get("smoke") is True, "Baidu entry visibility fast path must produce a first smoke case")
    require("小白学习打印首页加载完成" in fast_first_blob and "文档打印" in fast_first_blob, "Fast-path smoke case must start from the real homepage entry chain")
    require("点击「百度网盘」入口" not in fast_first_blob and "授权页" not in fast_first_blob, "Display-only fast-path smoke must not click into third-party Baidu Netdisk flow")
    fast_path_text = ["基础打印的入口在首页 文档打印 照片打印 扫描复印"]
    require(yaml_service.entry_visibility_fast_path_enabled({}, "基础打印新增百度网盘入口", "基础打印", fast_path_text), "Entry visibility smoke requests must retain the deterministic fast path by default")
    require(not yaml_service.entry_visibility_fast_path_enabled({"disableEntryVisibilityFastPath": True}, "基础打印新增百度网盘入口", "基础打印", fast_path_text), "Complete requirement requests must be able to disable the deterministic entry fast path")

    original_requirement_analyzer = ai_skill_service.call_skill_requirement_analyzer
    try:
        def full_pipeline_reached(*_args, **_kwargs):
            raise RuntimeError("full_pipeline_reached")

        ai_skill_service.call_skill_requirement_analyzer = full_pipeline_reached
        try:
            ai_skill_service.build_cases_payload_from_skills(
                "基础打印新增百度网盘入口",
                "基础打印",
                ["基础打印的入口在首页 文档打印 照片打印 扫描复印"],
                allow_entry_visibility_fast_path=False,
            )
            raise AssertionError("full requirement scope must bypass the deterministic entry fast path")
        except RuntimeError as exc:
            require(str(exc) == "full_pipeline_reached", "Disabling the entry fast path must enter the full AI requirement pipeline")
    finally:
        ai_skill_service.call_skill_requirement_analyzer = original_requirement_analyzer

    doc_text = "\n".join([
        "三方文档打印：百度网盘入口移至第 2 个，位于本地文档之后",
        "照片打印：普通照片打印、普通证件照、智能证件照、照片拼版导入时增加百度网盘导入选项",
        "扫描复印：复印扫描首页增加百度网盘导入入口",
        "埋点：百度网盘文档、照片、复印入口点击上报",
    ])
    fallback_analysis = ai_skill_service._fallback_requirement_analysis(
        "基础打印模块增加百度网盘入口",
        "基础打印",
        [doc_text],
        error="timeout",
    )
    extracted_points = " ".join(fallback_analysis.get("requirement_points") or [])
    for expected in ["三方文档打印", "普通照片打印", "普通证件照", "智能证件照", "照片拼版", "扫描复印", "埋点"]:
        require(expected in extracted_points, f"Fallback requirement analyzer must extract {expected}")

    analysis = {
        "business_goals": ["基础打印模块增加百度网盘入口"],
        "requirement_points": [
            "三方文档打印：百度网盘入口移至第 2 个，位于本地文档之后",
            "照片打印：普通照片打印、普通证件照、智能证件照、照片拼版导入时增加百度网盘导入选项",
            "扫描复印：复印扫描首页增加百度网盘导入入口",
            "埋点：百度网盘文档、照片、复印入口点击上报",
        ],
        "visible_outcomes": ["百度网盘入口可见", "点击后进入授权或导入流程"],
    }
    targets = {"target_scenarios": 8, "target_automation_cases": 8, "max_cases": 12, "smoke_cases": 3}
    scenarios = ai_skill_service._fallback_scenarios_from_analysis(
        "基础打印模块增加百度网盘入口",
        "基础打印",
        analysis,
        targets=targets,
        error="timeout",
    )
    scenario_titles = " ".join(item.get("scenario", "") for item in scenarios)
    require(len(scenarios) >= 6, "Fallback scenario designer must preserve all explicit Baidu Netdisk requirement branches")
    for expected in ["文档打印", "普通照片打印", "普通证件照", "智能证件照", "照片拼版", "扫描复印"]:
        require(expected in scenario_titles, f"Fallback scenarios must cover {expected}")
    for forbidden in ["历史", "备份", "引导", "Frame", "节点"]:
        require(forbidden not in scenario_titles, f"Fallback scenarios must not use Figma/internal wording: {forbidden}")

    filtered = ai_skill_service._fallback_automation_filter_from_scenarios(
        "基础打印模块增加百度网盘入口",
        "基础打印",
        analysis,
        scenarios,
        targets=targets,
        error="timeout",
    )
    cases = filtered.get("cases") or []
    manual_cases = filtered.get("manual_cases") or []
    case_titles = " ".join(case.get("title", "") for case in cases)
    require(len(cases) >= 6, "Fallback automation filter must emit conservative cases instead of failing the pipeline")
    require(sum(1 for case in cases if case.get("smoke")) == 3, "Fallback automation filter must keep first smoke batch at 3 cases")
    require(all(len(case.get("assertions") or []) <= 1 for case in cases), "Fallback automation filter must keep low assertion density")
    require(any("埋点" in str(item.get("title") or item.get("reason") or "") for item in manual_cases), "Tracking verification must be kept as manual/special verification")
    step_blob = "\n".join("\n".join(case.get("steps") or []) for case in cases)
    for expected in ["本地导入", "相册导入", "微信导入", "5寸照片", "一寸照", "图片拼版", "扫描仪扫描"]:
        require(expected in step_blob, f"Fallback automation steps must use concrete screenshot/page wording: {expected}")
    require("对应打印/导入页面" not in step_blob, "Fallback automation steps must not use vague navigation like 对应打印/导入页面")
    require(
        "小白扫描王" not in step_blob
        and "小白学习打印首页加载完成" in step_blob,
        "Learning Print fallback steps must not use the unrelated 小白扫描王 app name",
    )
    yaml_text = "\n".join(
        item.get("content", "")
        for item in yaml_service.cases_to_separate_midscene_yamls(filtered, app_package="com.xbxxhz.box", base_file="baidu.yaml")[1]
    )
    require("小白扫描王" not in yaml_text, "Fallback YAML for com.xbxxhz.box must not contain the unrelated 小白扫描王 app name")
    require("aiTap: \"等待" not in yaml_text and "aiTap: '等待" not in yaml_text and "aiTap: 等待" not in yaml_text, "Fallback YAML must not turn wait-state checks into aiTap actions")
    polluted_brand_yaml = """android:
  tasks:
    - name: 文档打印页百度网盘入口顺序校验
      flow:
        - launch: com.xbxxhz.box
        - aiWaitFor: 小白扫描王首页已加载完成，能看到文档打印入口
        - aiTap: 文档打印入口
        - aiWaitFor: 文档打印页展示百度网盘入口
        - aiAssert: 百度网盘位于本地文档之后
"""
    polluted_brand_dry = yaml_service.dry_run_midscene_yaml(
        polluted_brand_yaml,
        app_package="com.xbxxhz.box",
    )
    require(
        polluted_brand_dry.get("ok") is False
        and any("小白扫描王" in err for err in polluted_brand_dry.get("errors", [])),
        "Dry-run must block Learning Print YAML polluted with the unrelated 小白扫描王 app name",
    )
    mismatched_3d_brand_yaml = """android:
  tasks:
    - name: AI建模首页入口展示
      flow:
        - launch: com.kfb.model
        - aiWaitFor: 小白学习打印首页已加载，底部首页导航可见
        - aiTap: AI建模
        - aiWaitFor: AI建模页面已打开
        - aiAssert: AI建模入口可用
"""
    mismatched_3d_brand_dry = yaml_service.dry_run_midscene_yaml(mismatched_3d_brand_yaml)
    require(
        mismatched_3d_brand_dry.get("ok") is False
        and any("小白学习打印" in err for err in mismatched_3d_brand_dry.get("errors", [])),
        "Dry-run must block any generated YAML whose app brand text conflicts with its launch package",
    )
    generic_filtered = ai_skill_service._fallback_automation_filter_from_scenarios(
        "AI建模入口展示",
        "AI测试",
        {"requirement_points": ["AI建模入口展示"]},
        [{"feature": "AI建模", "scenario": "AI建模入口展示", "requirement_point": "AI建模入口展示"}],
        targets={"target_automation_cases": 1, "max_cases": 1, "smoke_cases": 1},
        error="timeout",
        app_package="com.kfb.model",
    )
    generic_step_blob = "\n".join("\n".join(case.get("steps") or []) for case in generic_filtered.get("cases") or [])
    require(
        "小白学习打印" not in generic_step_blob
        and "小白扫描王" not in generic_step_blob
        and ("3D 打印首页加载完成" in generic_step_blob or "3D打印首页加载完成" in generic_step_blob),
        "Fallback automation steps must use the selected app context for non-Baidu requirements too",
    )
    visibility_payload = {
        "title": "百度网盘入口可见性",
        "module": "基础打印",
        "cases": [{
            "title": "文档打印页百度网盘入口可见",
            "steps": ["验证文档打印页百度网盘入口按钮可见", "检查选择文件按钮可见"],
            "assertions": ["页面展示百度网盘入口，入口按钮可见"],
        }],
    }
    visibility_yaml = yaml_service.cases_to_separate_midscene_yamls(
        visibility_payload,
        app_package="com.xbxxhz.box",
        base_file="visibility.yaml",
    )[1][0]["content"]
    require(
        "aiTap: 验证文档打印页百度网盘入口按钮可见" not in visibility_yaml
        and "aiTap: 检查选择文件按钮可见" not in visibility_yaml
        and "aiWaitFor:" in visibility_yaml
        and "验证文档打印页百度网盘入口按钮可见" in visibility_yaml
        and "检查选择文件按钮可见" in visibility_yaml,
        "Generated YAML must treat visibility/existence steps as waits, even when the text contains 按钮/选择",
    )
    baidu_click_yaml = yaml_service.cases_to_separate_midscene_yamls({
        "title": "百度网盘入口点击反馈",
        "module": "基础打印",
        "cases": [{
            "title": "文档打印页百度网盘入口点击反馈",
            "steps": ["点击百度网盘入口"],
            "assertions": ["点击后进入百度网盘授权页、文件选择页、空状态或提示页之一"],
        }],
    }, app_package="com.xbxxhz.box", base_file="baidu-click.yaml")[1][0]["content"]
    require(
        "aiTap:" in baidu_click_yaml
        and "点击百度网盘入口" in baidu_click_yaml
        and "百度网盘授权页、登录页、文件选择页、空状态页或提示页已打开" in baidu_click_yaml,
        "Generated YAML must wait for post-click Baidu Netdisk signals instead of leaving a bare third-party entry tap",
    )
    wait_tap_score = yaml_service.score_midscene_yaml_executable("""android:
  tasks:
    - name: 等待句误判点击
      flow:
        - launch: com.xbxxhz.box
        - aiWaitFor: 首页已加载
        - aiTap: 等待百度网盘授权或文件选择页面打开
""")
    require(wait_tap_score.get("executionLevel") != "executable", "Executable scorer must reject aiTap prompts that are actually wait-state checks")
    for forbidden in ["历史", "备份", "引导", "Frame", "节点"]:
        require(forbidden not in case_titles, f"Fallback automation cases must not use Figma/internal wording: {forbidden}")


def check_smoke_selection_requires_explicit_ai_mark():
    from task_server.services import yaml_service
    from task_server.services import ai_skill_service

    keyword_only = {
        "case_id": "TC-001",
        "title": "核心入口展示验证",
        "priority": "P0",
        "scenario": "主流程入口",
        "steps": ["点击入口"],
        "assertions": ["页面展示核心区域"],
    }
    require(yaml_service.is_smoke_case(keyword_only) is False, "P0/P1 and keyword-only cases must not be auto-promoted to smoke")
    explicit = {**keyword_only, "smoke": True}
    require(yaml_service.is_smoke_case(explicit) is True, "Explicit AI/user smoke mark must still be honored")
    cases = [dict(keyword_only), {**keyword_only, "case_id": "TC-002", "smoke": True, "tags": ["冒烟"]}]
    selected, review = ai_skill_service.apply_smoke_selection_to_cases(
        cases,
        {"smoke_case_ids": ["TC-002"], "review": {"normal_chain_covered": True}},
        {"smoke_cases": 3},
    )
    require(selected[0].get("smoke") is False and selected[1].get("smoke") is True, "Smoke selection must clear stale smoke marks and apply selected IDs only")
    require(review.get("selected_case_ids") == ["TC-002"], "Smoke selection review must expose selected IDs")
    payload = {
        "analysis": {"requirement_points": ["新增百度网盘入口", "入口跳转结果"]},
        "cases": [
            {
                "case_id": "TC-001",
                "title": "历史打印记录干扰入口可见性",
                "priority": "P0",
                "steps": ["进入历史打印记录", "检查百度网盘入口"],
                "assertions": ["入口可见"],
            },
            {
                "case_id": "TC-002",
                "title": "文档打印首页展示新增百度网盘入口",
                "priority": "P0",
                "business_path": "首页 -> 文档打印 -> 百度网盘入口",
                "baselineMatched": True,
                "steps": ["等待文档打印首页", "点击百度网盘入口"],
                "assertions": ["百度网盘入口可见"],
            },
            {
                "case_id": "TC-003",
                "title": "百度网盘入口跳转授权页",
                "priority": "P1",
                "business_path": "文档打印 -> 百度网盘入口 -> 授权页",
                "baselineMatched": True,
                "steps": ["等待入口", "点击百度网盘入口", "等待授权页"],
                "assertions": ["授权页可见"],
            },
            {
                "case_id": "TC-004",
                "title": "百度网盘入口返回首页",
                "priority": "P2",
                "business_path": "文档打印 -> 百度网盘入口 -> 返回",
                "steps": ["进入入口", "返回"],
                "assertions": ["文档打印首页可见"],
            },
            {
                "case_id": "TC-005",
                "title": "慢加载重试提示",
                "priority": "P1",
                "steps": ["断网", "点击入口"],
                "assertions": ["错误提示"],
            },
        ],
    }
    selected_payload = ai_skill_service.select_smoke_cases_for_payload("百度网盘入口", "文档打印", payload)
    smoke_ids = selected_payload["review"]["smoke_case_ids"]
    require(len(smoke_ids) <= 3, "Local smoke gate must only select the first batch of at most 3 cases")
    require("TC-001" not in smoke_ids and "TC-005" not in smoke_ids, "Local smoke gate must not prefer history/interference cases over the current normal chain")
    require({"TC-002", "TC-003"}.issubset(set(smoke_ids)), "Local smoke gate must prioritize normal-chain baseline-backed cases")


def check_yaml_runner_eligibility_filter():
    from task_server.services import yaml_service

    payload = {
        "title": "AI建模需求",
        "module": "AI测试",
        "cases": [
            {
                "case_id": "TC-001",
                "title": "AI建模入口与页面核心模块展示",
                "preconditions": ["当前账号已登录"],
                "steps": ["点击底部 Tab「AI建模」", "等待 AI建模主页核心区域加载"],
                "assertions": ["页面展示开始创作、图片建模、语音创作、大家都在做或我的作品等核心模块"],
            },
            {
                "case_id": "TC-002",
                "title": "自传IP模型匹配成功走快速生成",
                "preconditions": ["已配置匹配接口Mock"],
                "steps": ["输入自传IP关键词", "点击开始生成"],
                "assertions": ["页面跳转至快速生成页"],
                "data_requirements": "Mock接口返回自传IP匹配成功数据",
            },
            {
                "case_id": "TC-003",
                "title": "系统通知权限关闭降级处理",
                "preconditions": ["系统通知权限已关闭"],
                "steps": ["提交生成任务"],
                "assertions": ["页面展示权限关闭降级提示"],
            },
            {
                "case_id": "TC-004",
                "title": "首页入口排序与设计稿一致",
                "steps": ["进入首页"],
                "assertions": ["模块排列顺序与设计稿一致"],
            },
            {
                "case_id": "TC-005",
                "title": "Figma 节点视觉一致性检查",
                "steps": ["进入 AI建模页"],
                "assertions": ["页面视觉与 Figma 关键区域一致，画布尺寸与 node-id 保持一致"],
            },
            {
                "case_id": "TC-006",
                "title": "我的作品模块空态与分页加载验证",
                "steps": ["进入 AI建模页", "滚动我的作品列表到底部"],
                "assertions": ["页面展示暂无作品或没有更多了提示"],
            },
            {
                "case_id": "TC-007",
                "title": "搜索引擎无结果兜底提示验证",
                "steps": ["输入无意义字符并提交搜索"],
                "assertions": ["页面展示未找到相关模型或兜底提示"],
            },
            {
                "case_id": "TC-008",
                "title": "语音创作首次权限弹窗验证",
                "steps": ["点击语音创作入口"],
                "assertions": ["页面展示麦克风权限申请弹窗"],
            },
            {
                "case_id": "TC-009",
                "title": "四维评估高匹配度引导文案验证",
                "steps": ["提交图片建模", "等待四维评估结果"],
                "assertions": ["评估结果展示高匹配度引导文案"],
            },
            {
                "case_id": "TC-010",
                "title": "生成按钮防重复点击验证",
                "steps": ["点击生成按钮", "快速重复点击生成按钮"],
                "assertions": ["页面展示生成中或按钮置灰状态"],
            },
            {
                "case_id": "TC-011",
                "title": "首页旧版建模入口清理验证",
                "steps": ["进入首页"],
                "assertions": ["旧版文字建模入口不出现"],
            },
        ],
        "manual_cases": [],
    }
    filtered = yaml_service.split_automation_ready_cases(payload)
    require(len(filtered["cases"]) == 1, "Only directly runnable AI modeling entry case should become YAML")
    require(len(filtered["manual_cases"]) == 10, "Mock/permission/design/data-state/transient cases must remain in manual coverage")
    _, files = yaml_service.cases_to_separate_midscene_yamls(payload, app_package="com.kfb.model", base_file="ai-model.yaml")
    require(len(files) == 1, "Separate YAML generation must only emit runner-eligible cases")
    content = files[0]["content"]
    require("确认前置条件" not in content, "Preconditions must stay in comments, not become flaky ai steps")
    require(re.search(r"- aiWaitFor:\s*[\"']?等待 AI建模主页核心区域加载[\"']?", content), "Natural wait steps must become aiWaitFor actions, not generic ai actions")
    require("input keyevent 3" in content and "wm size" not in content and "input keyevent 187" not in content, "Balanced launch guard must use lightweight app reset instead of recent-task cleanup")
    require("${size" not in content, "Generated YAML must avoid Midscene `${...}` env interpolation in shell snippets")
    require("input swipe 540 1900 540 350" not in content and "am kill-all" not in content, "Generated YAML must not inject fixed-coordinate recent-app cleanup")
    require("自传IP模型匹配成功" not in content and "系统通知权限" not in content and "设计稿一致" not in content and "Figma" not in content, "Runner-ineligible scenarios must not leak into YAML")
    require("我的作品模块空态" not in content and "无结果兜底" not in content and "权限弹窗" not in content and "四维评估" not in content and "防重复点击" not in content and "旧版建模入口" not in content, "Observed flaky AI modeling scenarios must not leak into Runner YAML")


def check_agent_runner_failure_reason_summary():
    from task_server.services import agent_service

    failed = [{
        "job_id": "job-static-failed",
        "status": "failed",
        "module": "AI_Agent_草稿",
        "file": "case.yaml",
        "target_task_name": "AI建模入口",
        "runner_id": "win-runner-01",
        "device_id": "ecbfd645",
        "stdout_tail": "Error: Replanned 5 times, exceeding the limit.\nfailed to locate element: 开始生成",
        "report_url": "http://example.test/report.html",
    }]
    reasons = agent_service._agent_job_failure_reasons(failed, limit=1)
    require(reasons and reasons[0]["target"] == "AI建模入口", "Runner failure summary must keep task target")
    require("Midscene 重规划超限" in reasons[0]["reason"] and "failed to locate element" in reasons[0]["reason"], "Runner failure summary must classify and use stdout/stderr tails when error is empty")
    require(reasons[0]["failureType"] == "Midscene 重规划超限", "Runner failure summary must expose a concrete failure type")
    require(reasons[0]["runnerId"] == "win-runner-01" and reasons[0]["deviceId"] == "ecbfd645", "Runner failure summary must keep runner/device")
    sdk_env_failed = [{
        "job_id": "job-sdk-env",
        "status": "failed",
        "module": "AI_Agent_草稿",
        "file": "case.yaml",
        "target_task_name": "入口可见",
        "stdout_tail": "Unable to get connected Android device list: Neither ANDROID_HOME nor ANDROID_SDK_ROOT environment variable was exported",
    }]
    sdk_reasons = agent_service._agent_job_failure_reasons(sdk_env_failed, limit=1)
    require(sdk_reasons and sdk_reasons[0]["failureType"] == "ENV_ISSUE", "Android SDK/ADB environment failures must be classified as ENV_ISSUE, not YAML script issues")


def check_agent_figma_context_defaults():
    from task_server.services import agent_service, knowledge_service, yaml_service

    run = {
        "runId": "agent-static-figma",
        "normalizedInput": {
            "figmaUrl": "https://www.figma.com/design/file/app?node-id=1-2",
        },
        "sourceRefs": {},
    }
    require(agent_service._agent_use_saved_knowledge_context(run, run.get("sourceRefs")) is False, "Agent new requirement must not use saved page knowledge by default")
    run["normalizedInput"]["useKnowledgeContext"] = True
    require(agent_service._agent_use_saved_knowledge_context(run, run.get("sourceRefs")) is True, "Agent must allow explicit saved page knowledge")

    raw = {
        "figmaUrl": "https://figma.example/design",
        "textAssets": ["a", "b"],
        "usedPages": [
            {"page_id": "p1", "page_name": "页面1", "screenshot": "p1.png", "figma": {"node_id": "1:1"}},
            {"page_id": "p1-dup", "page_name": "页面1副本", "screenshot": "p1-copy.png", "figma": {"node_id": "1:1"}},
            {"page_id": "p2", "page_name": "页面2", "screenshot": "p2.png", "figma": {"node_id": "1:2"}},
        ],
        "imageAssets": [
            {"name": "p1.png", "base64": "aaa", "mime": "image/png"},
            {"name": "p1.png", "base64": "aaa", "mime": "image/png"},
            {"name": "p2.png", "base64": "bbb", "mime": "image/png"},
            {"name": "old-extra.png", "base64": "ccc", "mime": "image/png"},
        ],
    }
    prepared = agent_service._normalize_agent_prepared_figma_context(raw)
    require(len(prepared["usedPages"]) == 2, "Agent prepared Figma pages must dedupe by node id")
    require(len(prepared["imageAssets"]) == 2, "Agent prepared Figma images must dedupe and not exceed used pages")
    server_prepared = yaml_service._prepared_figma_context_from_request({"prepared_figma_context": raw})
    require(len(server_prepared["usedPages"]) == 2 and len(server_prepared["imageAssets"]) == 2, "Server generation must normalize old prepared Figma context")

    direct = [
        {"page_id": f"direct-{idx}", "page_name": f"直链页面{idx}", "figma": {"node_id": f"1:{idx}", "direct_group": True}}
        for idx in range(36)
    ]
    nearby = [
        {"page_id": f"nearby-{idx}", "page_name": f"AI建模附近页面{idx}", "figma": {"node_id": f"2:{idx}"}}
        for idx in range(5)
    ]
    selected, ignored = knowledge_service.filter_figma_drafts_for_requirement(
        direct + nearby,
        "AI建模 页面",
        limit=36,
        min_score=1,
        max_limit=72,
        direct_scope_only=True,
    )
    require(len(selected) == 36 and len(ignored) == 5, "Direct Figma links must keep the exact direct scope instead of adding nearby keyword matches")

    old_learning_dir = knowledge_service.LEARNING_DIR
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            knowledge_service.LEARNING_DIR = temp_dir
            duplicate_png = "figma-引导1-手机-一寸照.png"
            saved, meta = knowledge_service._save_case_ui_design_files("case-dupe-figma", [
                {
                    "asset_id": "figma-1-218",
                    "name": duplicate_png,
                    "contentBase64": base64.b64encode(b"node-218").decode("ascii"),
                    "page_name": "引导1",
                },
                {
                    "asset_id": "figma-1-327",
                    "name": duplicate_png,
                    "contentBase64": base64.b64encode(b"node-327").decode("ascii"),
                    "page_name": "引导1",
                },
            ])
            design_dir = Path(temp_dir) / "case-ui-designs" / "case-dupe-figma"
            saved_files = sorted(path.name for path in design_dir.glob("*.png"))
            filenames = [item.get("filename") for item in meta.get("designs") or []]
            require(len(saved) == 2 and len(saved_files) == 2, "Same-name Figma variants must save as distinct physical image files")
            require(len(set(filenames)) == 2 and all(name in saved_files for name in filenames), "Same-name Figma variants must keep distinct meta filenames")
    finally:
        knowledge_service.LEARNING_DIR = old_learning_dir

    captured_parse_payload = {}
    original_parse_figma_design = knowledge_service.parse_figma_design

    def fake_parse_figma_design(payload):
        captured_parse_payload.update(payload)
        return {"drafts": [], "ignored_drafts": []}

    knowledge_service.parse_figma_design = fake_parse_figma_design
    try:
        knowledge_service.load_figma_generation_context(
            {
                "figma_url": "https://figma.example/design/file/app?node-id=1-2",
                "direct_scope_only": True,
            },
            "com.demo",
            "agent-static-figma",
            "AI建模 页面",
        )
    finally:
        knowledge_service.parse_figma_design = original_parse_figma_design
    require(captured_parse_payload.get("direct_scope_only") is True, "Agent Figma generation context must pass direct_scope_only to the parser")


def check_agent_high_risk_confirm_resumes_precheck():
    from task_server.services import agent_service

    now = "2026-06-24T00:00:00"
    run = {
        "runId": "agent-static-risk-confirm",
        "status": "WAIT_CONFIRM",
        "currentStep": "WAIT_CONFIRM",
        "riskLevel": "HIGH",
        "riskConfirmed": False,
        "pendingConfirmations": [{
            "id": "confirm-risk",
            "type": "high_risk_action",
            "action": "confirm_high_risk_action",
            "createdAt": now,
        }],
        "steps": [
            {"step": "PLAN", "status": "SUCCESS"},
            {"step": "PREPARE_SOURCE", "status": "SUCCESS"},
            {"step": "IMPACT_ANALYSIS", "status": "SUCCESS"},
            {"step": "CASE_RETRIEVAL", "status": "SUCCESS"},
            {"step": "MATCH_CASES", "status": "SUCCESS"},
            {"step": "GENERATE_YAML", "status": "SUCCESS"},
            {"step": "VALIDATE_YAML", "status": "SUCCESS"},
            {"step": "RISK_REVIEW", "status": "SUCCESS"},
            {"step": "EXECUTION_PRECHECK", "status": "PENDING"},
            {"step": "SYNC_SONIC", "status": "PENDING"},
        ],
        "artifacts": {},
    }
    original_load = agent_service.load_agent_runs
    original_save = agent_service.save_agent_runs
    store = [run]
    saved = []

    def fake_load():
        return store

    def fake_save(runs):
        saved[:] = runs

    agent_service.load_agent_runs = fake_load
    agent_service.save_agent_runs = fake_save
    try:
        result = agent_service.confirm_agent_step("agent-static-risk-confirm", "confirm-risk", "confirmed", {})
    finally:
        agent_service.load_agent_runs = original_load
        agent_service.save_agent_runs = original_save

    require(result.get("status") == "RUNNING", "High-risk confirmation must resume the Agent")
    require(result.get("currentStep") == "EXECUTION_PRECHECK", "High-risk confirmation must continue to execution precheck")
    require(result.get("riskConfirmed") is True and not result.get("pendingConfirmations"), "High-risk confirmation must clear pending confirmation")
    require(saved and saved[0].get("currentStep") == "EXECUTION_PRECHECK", "High-risk confirmation resume state must be persisted")


def check_agent_completed_tool_step_recovers_and_avoids_hot_cancel_reads():
    from task_server.services import agent_service

    source = (ROOT / "task_server" / "services" / "agent_service.py").read_text(encoding="utf-8")
    step_body = source.split("def _execute_agent_step", 1)[1].split("\ndef _execute_agent_steps", 1)[0]
    require("_persisted_agent_run_is_cancelled" not in step_body, "Agent step hot path must not read full persisted run history for cancellation")
    require(source.count("def _persisted_agent_run_is_cancelled") == 1, "Agent cancel helper must not be duplicated")

    old_ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - 300))
    run = {
        "runId": "agent-static-completed-tool-recovery",
        "status": "RUNNING",
        "currentStep": "PREPARE_SOURCE",
        "progress": 6,
        "steps": [
            {"step": "PLAN", "status": "SUCCESS"},
            {
                "step": "PREPARE_SOURCE",
                "status": "RUNNING",
                "startedAt": old_ts,
                "summary": "",
                "toolCalls": [{
                    "status": "SUCCESS",
                    "outputSummary": "已整理 requirement 输入来源，Figma 页面 36 个，Figma UI 图 36 张",
                }],
                "liveTrace": [{
                    "time": old_ts,
                    "message": "已整理 requirement 输入来源，Figma 页面 36 个，Figma UI 图 36 张",
                    "status": "SUCCESS",
                }],
            },
            {"step": "IMPACT_ANALYSIS", "status": "PENDING"},
        ],
    }
    recovered, should_resume = agent_service._recover_completed_running_step(run)
    require(recovered is True and should_resume is True, "Completed tool calls left RUNNING must recover and request resume")
    step = run["steps"][1]
    require(step.get("status") == "SUCCESS" and step.get("endedAt"), "Recovered completed tool step must be finalized as SUCCESS")
    require(run.get("currentStep") == "IMPACT_ANALYSIS", "Recovered Agent must advance to the next pending step")
    require("自动补齐步骤完成状态" in (step.get("liveTrace") or [])[-1].get("message", ""), "Recovered step must explain the auto-finalization")

    stalled = {
        "runId": "agent-static-stalled-dispatch",
        "status": "RUNNING",
        "currentStep": "IMPACT_ANALYSIS",
        "progress": 10,
        "steps": [
            {"step": "PLAN", "status": "SUCCESS"},
            {"step": "PREPARE_SOURCE", "status": "SUCCESS"},
            {
                "step": "IMPACT_ANALYSIS",
                "status": "RUNNING",
                "startedAt": old_ts,
                "summary": "",
                "toolCalls": [],
                "liveTrace": [
                    {"time": old_ts, "message": "开始执行 IMPACT_ANALYSIS", "status": "RUNNING"},
                    {"time": old_ts, "message": "准备调用工具：_tool_impact_analysis", "status": "RUNNING"},
                ],
            },
        ],
    }
    recovered, should_resume = agent_service._recover_stalled_tool_dispatch_step(stalled)
    stalled_step = stalled["steps"][2]
    require(recovered is True and should_resume is True, "Steps stalled before actual tool call must be requeued")
    require(stalled_step.get("status") == "PENDING" and stalled.get("currentStep") == "IMPACT_ANALYSIS", "Requeued stalled tool dispatch must remain on the same step")
    require("重新排队" in stalled_step.get("summary", ""), "Requeued stalled tool dispatch must explain the recovery")


def check_agent_history_compacts_uploaded_blobs_after_prepare():
    from task_server.services import agent_service

    blob = base64.b64encode(b"large-pdf-content" * 2000).decode("ascii")
    file_item = {
        "name": "AI 建模页需求文档.pdf",
        "type": "application/pdf",
        "kind": "requirement_file",
        "size": len(blob),
        "contentBase64": blob,
        "content": "x" * 2000,
    }
    run = {
        "runId": "agent-static-compact-input",
        "target": "AI建模UI测试",
        "normalizedInput": {
            "files": [dict(file_item)],
            "sourceInputs": {
                "files": [dict(file_item)],
                "requirementFiles": [dict(file_item)],
                "images": [],
            },
        },
        "artifacts": {
            "sourceContext": {
                "requirementText": "已解析出的需求文本",
                "uploadedFiles": [{"name": file_item["name"], "kind": file_item["kind"], "size": file_item["size"]}],
                "uploadedImages": [],
                "figmaUsedPages": [],
                "figmaImageCount": 0,
            }
        },
    }
    before_size = len(json_dumps_for_check(run))
    changed = agent_service._compact_agent_run_input_blobs(run)
    after_size = len(json_dumps_for_check(run))
    compacted_file = run["normalizedInput"]["files"][0]
    nested_file = run["normalizedInput"]["sourceInputs"]["requirementFiles"][0]
    require(changed is True, "Agent run compaction must report changes when uploaded blobs are present")
    require("contentBase64" not in compacted_file and "content" not in compacted_file, "Top-level normalized files must drop raw uploaded content")
    require("contentBase64" not in nested_file and nested_file.get("contentRemoved") is True, "Nested sourceInputs files must also drop duplicated raw content")
    require(compacted_file.get("name") == file_item["name"] and compacted_file.get("size") == file_item["size"], "Compaction must keep file metadata visible")
    require(after_size < before_size / 3, "Agent persisted run should shrink substantially after source preparation")


def check_agent_worker_start_is_idempotent():
    from task_server.services import agent_service

    original_thread = agent_service.threading.Thread
    started = []

    class FakeThread:
        def __init__(self, target, args=(), daemon=None):
            self.target = target
            self.args = args
            self.daemon = daemon

        def start(self):
            started.append({"target": getattr(self.target, "__name__", ""), "args": self.args, "daemon": self.daemon})

    agent_service.threading.Thread = FakeThread
    agent_service.AGENT_ACTIVE_WORKERS.clear()
    try:
        first = agent_service._start_agent_worker("agent-static-worker")
        second = agent_service._start_agent_worker("agent-static-worker")
        other = agent_service._start_agent_worker("agent-static-worker-2")
    finally:
        agent_service.threading.Thread = original_thread
        agent_service.AGENT_ACTIVE_WORKERS.clear()

    require(first is True and second is False and other is True, "Agent worker start must suppress duplicate run executors")
    require(len(started) == 2, "Agent worker guard must start only one thread per run id")


def check_snapshot_store_concurrent_save():
    from task_server.core.replay import snapshot_store

    old_snapshot_file = snapshot_store.SNAPSHOT_FILE
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot_store.SNAPSHOT_FILE = os.path.join(temp_dir, "snapshots.json")
            store = snapshot_store.SnapshotStore()
            import threading
            threads = [
                threading.Thread(target=store.save, args=({"traceId": f"trace-{idx}"},), kwargs={"source_id": f"src-{idx}"})
                for idx in range(20)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            rows = store.list(limit=None)
            require(len(rows) == 20, "SnapshotStore concurrent saves must not lose records")
            require(len({row.get("id") for row in rows}) == 20, "SnapshotStore concurrent saves must keep unique snapshots")
    finally:
        snapshot_store.SNAPSHOT_FILE = old_snapshot_file


def check_production_debug_execution_guard_static():
    router_source = (ROOT / "task_server" / "router.py").read_text(encoding="utf-8")
    config_source = (ROOT / "task_server" / "config.py").read_text(encoding="utf-8")
    require("TASK_ENABLE_DEBUG_EXECUTION" in config_source, "Config must expose TASK_ENABLE_DEBUG_EXECUTION")
    require("def _debug_execution_enabled" in router_source and "生产环境未开启 Debug 执行接口" in router_source, "Debug execution routes must be guarded in production")
    for marker in ("_post_debug_dag_run", "_post_debug_dag_parallel", "_post_debug_execution_run"):
        chunk = router_source[router_source.find(f"def {marker}") : router_source.find("@route_", router_source.find(f"def {marker}") + 1)]
        require("_debug_execution_enabled(handler)" in chunk, f"{marker} must require debug execution switch")


def check_execution_adapter_prompt_center_delay():
    from task_server.execution.execution_adapter import ExecutionAdapter

    adapter = ExecutionAdapter()
    require(not adapter._should_enrich_with_prompt_center({}, "local"), "Local Runner mode must not enrich through PromptCenter by default")
    require(adapter._should_enrich_with_prompt_center({"usePromptCenter": True}, "local"), "Explicit PromptCenter opt-in must still work")
    require(adapter._should_enrich_with_prompt_center({}, "dag"), "DAG debug mode should keep PromptCenter enrichment")
    require(adapter.available_modes().get("default") == "local", "ExecutionAdapter default mode must remain local")


def check_mindmap_compact_mode():
    from task_server.services import yaml_service

    summary = {
        "title": "AI建模测试",
        "mindmap_mode": "compact",
        "analysis": {
            "business_flow": ["进入 AI建模页", "点击开始创作", "选择图片建模"],
            "requirement_points": ["图片建模入口", "语音输入入口"],
            "risks": ["模型生成耗时较长"],
            "coverage_matrix": [{"requirement_point": "图片建模入口", "auto_cases": ["TC-001"]}],
        },
        "scenarios": [{"scenario": "图片建模入口展示", "feature": "AI建模"}],
        "cases": [{"case_id": "TC-001", "title": "图片建模入口展示", "priority": "P1", "business_path": "进入 AI建模页 点击开始创作 选择图片建模"}],
        "manual_cases": [{"title": "模型生成结果人工复核", "reason": "模型返回耗时和结果内容不稳定"}],
        "review": {"mindmap_only": True, "mindmap_mode": "compact"},
    }
    compact = yaml_service.build_generation_mindmap(summary)
    require("业务主线与覆盖" in compact and "可执行 YAML 用例" in compact, "Compact mindmap must expose readable business flow and YAML sections")
    require("完整需求覆盖追踪矩阵" not in compact, "Compact mindmap must not render the full verbose coverage matrix")
    full = yaml_service.build_generation_mindmap({**summary, "mindmap_mode": "full", "review": {}})
    require("完整需求覆盖追踪矩阵" in full, "Full mindmap mode must remain available for compatibility")


def check_generation_volume_targets_modes():
    from task_server.services import case_service

    analysis = {
        "requirement_points": [f"需求点 {idx}" for idx in range(6)],
        "risks": ["风险1", "风险2"],
        "visible_outcomes": ["结果1", "结果2"],
    }
    full = case_service.generation_volume_targets(analysis, mode="full")
    mindmap = case_service.generation_volume_targets(analysis, mode="mindmap")
    require(full["target_automation_cases"] == 8 and full["max_automation_cases"] == 8, "Large generation must cap automation YAML at 8")
    require(mindmap["mode"] == "mindmap", "Generation targets must record the selected mode")
    small = case_service.generation_volume_targets({"requirement_points": ["入口"]}, mode="full")
    medium = case_service.generation_volume_targets({"requirement_points": ["入口", "列表", "详情"]}, mode="full")
    large = case_service.generation_volume_targets({"requirement_points": [str(i) for i in range(6)]}, mode="full")
    require(
        (small["target_automation_cases"], medium["target_automation_cases"], large["target_automation_cases"]) == (3, 5, 8),
        "Generated automation YAML quantity must converge to 3/5/8 by requirement size",
    )
    require(small["smoke_cases"] == medium["smoke_cases"] == large["smoke_cases"] == 3, "Generated first smoke batch must stay fixed at 3")
    require(large["continue_threshold"] == 0.5 and large["smoke_max_cases"] == 3, "Generated execution gate must keep fixed 50% threshold and smoke cap 3")


def check_ai_gateway_fallback_and_skill_static():
    gateway_source = (ROOT / "ai-gateway" / "server.js").read_text(encoding="utf-8")
    router_config = (ROOT / "ai-gateway" / "config" / "model-router.json").read_text(encoding="utf-8")
    ai_skill_source = (ROOT / "task_server" / "services" / "ai_skill_service.py").read_text(encoding="utf-8")
    app_js_source = (ROOT / "js" / "app.js").read_text(encoding="utf-8")
    require("routeCandidatesFor" in gateway_source and "fallbackProviderIds" in gateway_source, "AI Gateway must support provider fallback routing")
    require("app.post('/ai/skill'" in gateway_source, "AI Gateway must expose a text AI Skill endpoint")
    require("AI_GATEWAY_URL" in ai_skill_source and "ai_gateway_skill_content" in ai_skill_source and "if not image_assets" in ai_skill_source, "Text AI skills must try AI Gateway while image skills stay on DashScope VL")
    require('"fallbackProviderIds"' in router_config and "highway_gpt5_mini" in router_config, "Model router config must include fallback providers")
    require("mindmapMode: 'full'" in app_js_source, "Mindmap-only frontend requests must default to full test-case mindmap mode")


def check_ai_yaml_generation_decision_chain_static():
    yaml_service_source = (ROOT / "task_server" / "services" / "yaml_service.py").read_text(encoding="utf-8")
    ai_skill_source = (ROOT / "task_server" / "services" / "ai_skill_service.py").read_text(encoding="utf-8")
    baseline_cache_source = (ROOT / "task_server" / "services" / "yaml_baseline_cache.py").read_text(encoding="utf-8")
    gateway_source = (ROOT / "ai-gateway" / "server.js").read_text(encoding="utf-8")

    for rel in (
        "ai_skills/prompts/baseline_reranker.v1.md",
        "ai_skills/prompts/execution_scope_planner.v1.md",
        "ai_skills/prompts/executable_yaml_planner.v1.md",
        "ai_skills/schemas/baseline_reranker.schema.json",
        "ai_skills/schemas/execution_scope_planner.schema.json",
        "ai_skills/schemas/executable_yaml_planner.schema.json",
    ):
        require((ROOT / rel).exists(), f"AI YAML decision skill file missing: {rel}")

    require("YAML_BASELINE_SEARCH_MAX_LIMIT" in baseline_cache_source, "Baseline cache search limit must be configurable for AI reranking candidates")
    require("return _MEMORY_CACHE" in baseline_cache_source and "calc_baseline_fingerprint" in baseline_cache_source, "Baseline cache must return fresh memory cache before recalculating fingerprints")
    require("search_baseline_examples" in yaml_service_source and "limit=20" in yaml_service_source, "YAML generation must request a wider baseline candidate pool for AI reranking")
    require("call_skill_baseline_reranker" in yaml_service_source, "YAML generation must call AI baseline reranker before picking Top3 examples")
    require("call_skill_execution_scope_planner" in yaml_service_source, "YAML generation must call AI execution scope planner")
    require("call_skill_executable_yaml_planner" in yaml_service_source, "YAML generation must call AI executable YAML planner")
    require("build_ai_generation_decision_context_text" in yaml_service_source and "AI 生成决策计划" in yaml_service_source, "YAML prompt must include the AI decision plan context")
    require("ai_decision_trace" in yaml_service_source and "executable_yaml_planner_review" in yaml_service_source, "YAML generation review must expose AI decision trace and planner review")
    require("improve_case_coverage(" in yaml_service_source and "model_config=model_config" in yaml_service_source, "Coverage repair must receive selected model config")

    require("def call_skill_baseline_reranker" in ai_skill_source, "AI skill service must expose baseline reranker")
    require("def call_skill_execution_scope_planner" in ai_skill_source, "AI skill service must expose execution scope planner")
    require("def call_skill_executable_yaml_planner" in ai_skill_source, "AI skill service must expose executable YAML planner")
    require("def apply_executable_yaml_plan_to_payload" in ai_skill_source, "Executable YAML planner output must be applied to generated payload")
    require("MIDSCENE_AI_SKILLS_STRICT_MODEL" in ai_skill_source and "AI_SKILLS_STRICT_MODEL" in ai_skill_source, "AI skills must support strict selected-model mode")
    require("model_trace" in ai_skill_source and "providerId" in ai_skill_source, "AI skill reviews must record provider/model trace")
    require(
        "call_coverage_auditor_skill(" in ai_skill_source
        and "model_config=model_config" in ai_skill_source
        and "targets=current_targets" in ai_skill_source,
        "Coverage auditor must receive model config and the authoritative scope targets during the repair loop",
    )
    require("当前平台采用可执行优先策略" in ai_skill_source, "Legacy quantity-driven prompt must be replaced with executable-first 3/5/8 guidance")
    require("每个需求功能点通常至少生成 2-4 条自动化用例" not in ai_skill_source, "Legacy 2-4 cases per requirement prompt must not reappear")
    require("display_only" in ai_skill_source and "点击后进入百度网盘相关流程" in ai_skill_source, "Baidu Netdisk display-only requirements must be separated from click/auth flows")

    generate_yaml_route = gateway_source[gateway_source.find("app.post('/ai/generate-yaml'") : gateway_source.find("app.post('/ai/repair-yaml'")]
    require("modelConfig" in generate_yaml_route and "providerId" in generate_yaml_route and "model:" in generate_yaml_route, "/ai/generate-yaml must forward modelConfig/provider/model")


def check_apk_chunk_upload_roundtrip():
    from task_server import router

    old_package_dir = router.APP_INSTALL_PACKAGE_DIR
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            router.APP_INSTALL_PACKAGE_DIR = temp_dir
            upload_id = "apk-static-check"
            router.save_apk_upload_chunk(
                upload_id,
                "demo.apk",
                0,
                2,
                6,
                base64.b64encode(b"abc").decode("ascii"),
            )
            router.save_apk_upload_chunk(
                upload_id,
                "demo.apk",
                1,
                2,
                6,
                base64.b64encode(b"def").decode("ascii"),
            )
            saved = router.finish_apk_upload_chunks(upload_id, "demo.apk", 2, 6)
            require(saved["apk_url"].startswith("/api/app-install/package?id="), "APK chunk finish must return an internal package URL")
            require(Path(saved["apk_path"]).read_bytes() == b"abcdef", "APK chunk finish must assemble the original bytes")
            ref = router.uploaded_apk_package_from_url(saved["apk_url"])
            require(ref and ref["apk_size"] == 6 and ref["apk_name"] == "demo.apk", "APK install request must resolve chunk-uploaded package references")
    finally:
        router.APP_INSTALL_PACKAGE_DIR = old_package_dir


def json_dumps_for_check(value):
    import json
    return json.dumps(value, ensure_ascii=False)


def check_agent_yaml_validate_partial_quarantine():
    from task_server.services import agent_service

    good_yaml = """
android:
  tasks:
    - name: "P0 AI建模入口冒烟"
      flow:
        - launch: com.kfb.model
        - aiWaitFor: "首页已加载完成，底部导航和 AI建模入口可见"
        - aiTap: "底部中间 AI建模入口"
        - aiWaitFor: "AI建模页已加载完成，开始创作入口可见"
        - aiAssert: "AI建模页核心入口展示正常"
"""
    bad_yaml = """
android:
  tasks:
    - name: "过泛的草稿用例"
      flow:
        - aiTap: "入口"
        - aiWaitFor: "页面"
"""
    run = {
        "runId": "agent-static-partial-validate",
        "target": "AI建模 UI测试",
        "artifacts": {
            "generationPipeline": {"source": "agent_generate_yaml"},
            "yamlRefs": [
                {"type": "file", "file": "good.yaml", "content": good_yaml, "confirmed": True},
                {"type": "file", "file": "bad.yaml", "content": bad_yaml, "confirmed": True},
            ],
        },
    }
    call = agent_service._tool_validate_yaml(run)
    artifacts = run.get("artifacts") or {}
    validation = artifacts.get("yamlValidation") or {}
    require(call.get("status") in ("SUCCESS", "PARTIAL_FAILED"), "Agent YAML validation must not fail the whole run when at least one generated YAML passes")
    require(validation.get("passedCount", 0) >= 1, "Partial YAML validation must keep passing YAML executable")
    if validation.get("autoRepairedCount"):
        require(
            call.get("status") == "SUCCESS"
            and validation.get("failedCount") == 0
            and len(artifacts.get("yamlRefs") or []) == 2
            and not artifacts.get("quarantinedYamlRefs"),
            "AI-repaired generated YAML should remain executable instead of being quarantined",
        )
    else:
        require(validation.get("partialOk") is True and validation.get("passedCount") == 1 and validation.get("failedCount") == 1, "Partial YAML validation must record pass/fail counts")
        require(len(artifacts.get("yamlRefs") or []) == 1 and len(artifacts.get("quarantinedYamlRefs") or []) == 1, "Failed generated YAML must be quarantined while passing YAML remains executable")


def check_agent_yaml_validate_auto_repairs_missing_wait():
    from task_server.services import agent_service

    assertion_tap_yaml = """
android:
  tasks:
    - name: "文档打印首页展示百度网盘入口"
      flow:
        - launch: com.xbxxhz.box
        - aiWaitFor: "App 首页加载完成"
        - aiTap: "点击「文档打印」icon"
        - aiWaitFor: "文档打印首页加载完成"
        - aiTap: "首页文档打印入口页-百度网盘入口可见性检查"
        - aiWaitFor: "页面稳定展示「百度网盘」入口按钮，文案清晰可点击"
        - aiAssert: "页面稳定展示「百度网盘」入口按钮，文案清晰可点击"
"""
    run = {
        "runId": "agent-static-auto-repair-assertion-tap",
        "target": "学习打印基础打印增加百度网盘入口",
        "artifacts": {
            "generationPipeline": {"source": "agent_generate_yaml"},
            "yamlRefs": [
                {"type": "file", "file": "assertion-tap.yaml", "content": assertion_tap_yaml, "confirmed": True},
            ],
        },
    }
    call = agent_service._tool_validate_yaml(run)
    validation = (run.get("artifacts") or {}).get("yamlValidation") or {}
    fixed_content = ((run.get("artifacts") or {}).get("yamlRefs") or [{}])[0].get("content") or ""
    require(call.get("status") == "SUCCESS", "Agent YAML validation must repair assertion-like aiTap prompts before quarantine/precheck")
    require(validation.get("autoRepairedCount") == 1, "Assertion-like aiTap repair must be counted")
    require(
        "aiTap: 首页文档打印入口页-百度网盘入口可见性检查" not in fixed_content
        and "aiWaitFor: 页面展示百度网盘入口可见" in fixed_content,
        "Assertion-like title aiTap must become a concrete aiWaitFor page-state prompt",
    )
    still_needs_review_yaml = """
android:
  tasks:
    - name: "首页文档打印入口页-百度网盘入口可见性检查"
      flow:
        - launch: com.xbxxhz.box
        - aiWaitFor: "App 首页加载完成"
        - aiTap: "首页文档打印入口页-百度网盘入口可见性检查"
"""
    rows, issues, ok_count = agent_service._agent_yaml_dry_run_rows(
        {"runId": "agent-static-auto-repair-adopt", "artifacts": {"generationPipeline": {"source": "agent_generate_yaml"}}},
        [{"type": "file", "file": "title-assertion.yaml", "content": still_needs_review_yaml, "confirmed": True}],
    )
    repaired_row_content = (rows[0] if rows else {}).get("content") or ""
    repaired_row_issues = "；".join(str(item) for item in ((rows[0] if rows else {}).get("issues") or []) + issues)
    require(
        ok_count == 1
        and (
            "aiWaitFor: 页面展示百度网盘入口可见" in repaired_row_content
            or "文档打印页展示百度网盘入口" in repaired_row_content
            or "页面展示百度网盘入口" in repaired_row_content
        )
        and "aiTap: 首页文档打印入口页-百度网盘入口可见性检查" not in repaired_row_content
        and "aiTap 描述像检查/断言" not in repaired_row_issues,
        "Agent dry-run rows must adopt repaired executable YAML instead of keeping a stale blocker",
    )

    missing_wait_yaml = """
android:
  tasks:
    - name: "P0 返回入口状态刷新"
      flow:
        - launch: com.kfb.model
        - aiWaitFor: "首页已加载完成，底部导航和 AI建模入口可见"
        - aiAssert: "首页核心入口展示正常"
        - aiTap: "从其他打印模块返回一寸引导页"
"""
    run = {
        "runId": "agent-static-auto-repair-validate",
        "target": "AI建模 UI测试",
        "artifacts": {
            "generationPipeline": {"source": "agent_generate_yaml"},
            "yamlRefs": [
                {"type": "file", "file": "repair.yaml", "content": missing_wait_yaml, "confirmed": True},
            ],
        },
    }
    call = agent_service._tool_validate_yaml(run)
    validation = (run.get("artifacts") or {}).get("yamlValidation") or {}
    require(call.get("status") == "SUCCESS", "Agent YAML validation must auto-repair missing post-interaction waits when the repaired YAML is executable")
    require(validation.get("autoRepairedCount") == 1, "Auto repair count must be exposed for Agent YAML validation")
    fixed_content = ((run.get("artifacts") or {}).get("yamlRefs") or [{}])[0].get("content") or ""
    require("aiWaitFor" in fixed_content and "返回后的目标页面已加载完成" in fixed_content, "Auto repair must insert a lightweight wait without adding extra business assertions")

    prefixed_yaml = """
android:
  tasks:
    - name: "文档打印首页展示百度网盘入口"
      flow:
        - runAdbShell: "input keyevent 3; sleep 1; size=$(wm size | grep -oE '[0-9]+x[0-9]+' | tail -1); if [ -n \\"$size\\" ]; then w=${size%x*}; h=${size#*x}; input keyevent 187; fi"
        - ai: "runAdbShell: am force-stop com.xbxxhz.box"
        - ai: "sleep: 1500"
        - ai: "launch: com.xbxxhz.box"
        - ai: "sleep: 3000"
        - aiWaitFor: "aiWaitFor: 回到App首页并等待页面加载稳定"
        - aiTap: "aiTap: 点击「文档打印」icon"
        - aiTap: "aiWaitFor: 页面展示「百度网盘」文案，入口可见且可点击"
        - aiTap: "aiAssert: 页面展示「百度网盘」文案，入口可见且可点击"
"""
    run = {
        "runId": "agent-static-auto-repair-prefixed",
        "target": "学习打印基础打印增加百度网盘入口",
        "artifacts": {
            "generationPipeline": {"source": "agent_generate_yaml"},
            "yamlRefs": [
                {"type": "file", "file": "prefixed.yaml", "content": prefixed_yaml, "confirmed": True},
            ],
        },
    }
    call = agent_service._tool_validate_yaml(run)
    validation = (run.get("artifacts") or {}).get("yamlValidation") or {}
    fixed_content = ((run.get("artifacts") or {}).get("yamlRefs") or [{}])[0].get("content") or ""
    require(call.get("status") == "SUCCESS", "Agent YAML validation must auto-repair nested action prefixes instead of failing the whole run")
    require(validation.get("autoRepairedCount") == 1 and "${size" not in fixed_content and "input keyevent 3" in fixed_content, "Agent YAML validation must expose auto repair and replace unsafe recent-task cleanup with lightweight home guard")
    require("aiTap: aiWaitFor" not in fixed_content and "aiTap: aiAssert" not in fixed_content, "Agent YAML validation must not keep action prefixes inside action values")


def check_agent_quarantine_refs_do_not_reenter_precheck():
    from task_server.services import agent_service

    good_yaml = """
android:
  tasks:
    - name: "文档打印首页展示百度网盘入口"
      flow:
        - launch: com.xbxxhz.box
        - aiWaitFor: "App 首页加载完成"
        - aiTap: "点击「文档打印」icon"
        - aiWaitFor: "文档打印首页加载完成，百度网盘入口可见"
        - aiAssert: "百度网盘入口展示正常"
"""
    run = {
        "runId": "agent-static-quarantine-normalize",
        "target": "基础打印新增百度网盘入口",
        "artifacts": {
            "generationPipeline": {"source": "agent_generate_yaml"},
            "yamlRefs": [
                {"type": "file", "file": "01-bad.yaml", "content": "", "path": "/tmp/01-bad.yaml", "confirmed": True},
                {"type": "file", "file": "02-good.yaml", "content": good_yaml, "path": "/tmp/02-good.yaml", "confirmed": True},
            ],
            "generatedYamlPath": "/tmp/01-bad.yaml",
            "generatedYamlPaths": ["/tmp/01-bad.yaml", "/tmp/02-good.yaml"],
            "quarantinedYamlRefs": [
                {"type": "file", "file": "01-bad.yaml", "path": "/tmp/01-bad.yaml", "executionLevel": "needs_review"},
            ],
        },
    }
    refs = agent_service.normalize_yaml_refs(run)
    require(len(refs) == 1 and refs[0].get("file") == "02-good.yaml", "Quarantined generatedYamlPath must not be re-added to executable YAML refs")


def check_agent_execution_gate_repairs_before_smoke_selection():
    from task_server.services import agent_service

    assertion_tap_yaml = """
android:
  tasks:
    - name: "文档打印首页展示百度网盘入口"
      flow:
        - launch: com.xbxxhz.box
        - aiWaitFor: "App 首页加载完成"
        - aiTap: "点击「文档打印」icon"
        - aiWaitFor: "文档打印首页加载完成"
        - aiTap: "首页文档打印入口页-百度网盘入口可见性检查"
        - aiAssert: "页面展示百度网盘入口可见"
"""
    run = {
        "runId": "agent-static-gate-repair-before-smoke",
        "target": "基础打印新增百度网盘入口",
        "artifacts": {
            "generationPipeline": {"source": "agent_generate_yaml"},
            "yamlRefs": [
                {"type": "file", "file": "01-baidu-entry.yaml", "content": assertion_tap_yaml, "confirmed": True, "smoke": True},
            ],
        },
    }
    selected, gate = agent_service._select_agent_runner_refs(run, agent_service.normalize_yaml_refs(run))
    fixed_content = ((run.get("artifacts") or {}).get("yamlRefs") or [{}])[0].get("content") or ""
    require(gate.get("autoRepairCount") == 1, "Generated YAML runner gate must repair local executable issues before smoke selection")
    require(len(selected) == 1 and gate.get("selectedCount") == 1, "Repairable generated YAML must not be blocked before first smoke selection")
    require(
        "aiTap: 首页文档打印入口页-百度网盘入口可见性检查" not in fixed_content
        and "aiWaitFor: 页面展示百度网盘入口可见" in fixed_content,
        "Runner gate repair must persist repaired YAML content before precheck/dispatch",
    )


def main():
    check_runner_inline_android_device_injection()
    check_midscene_model_family_protocol()
    entry_source = ENTRY.read_text(encoding="utf-8")
    require("from task_server.app import main" in entry_source, "midscene-upload.py must be a light task_server entrypoint")
    source_paths = [
        ENTRY,
        MODULE,
        ROOT / "task_server" / "app.py",
        ROOT / "task_server" / "router.py",
        ROOT / "task_server" / "config.py",
        ROOT / "task_server" / "auth.py",
        ROOT / "task_server" / "response.py",
        ROOT / "task_server" / "storage.py",
        ROOT / "task_server" / "services" / "agent_service.py",
        ROOT / "task_server" / "services" / "job_service.py",
        ROOT / "task_server" / "services" / "yaml_service.py",
        ROOT / "task_server" / "services" / "knowledge_service.py",
        ROOT / "task_server" / "services" / "sonic_service.py",
        ROOT / "task_server" / "services" / "repair_service.py",
        ROOT / "task_server" / "execution" / "execution_adapter.py",
        ROOT / "task_server" / "workflow" / "dag_safe" / "dag_wrapper.py",
        ROOT / "task_server" / "workflow" / "parallel_dag" / "parallel_dag_runner.py",
        ROOT / "task_server" / "core" / "debugger" / "trace_exporter.py",
        ROOT / "task_server" / "core" / "replay" / "snapshot_store.py",
    ]
    source = "\n".join(path.read_text(encoding="utf-8") for path in source_paths if path.exists())
    require('path.startswith("/assets/")' in source and 'Cache-Control' in source, "Backend must serve local /assets/ files for the platform logo")
    require('"/api/auth/login"' in source and '"/api/auth/me"' in source and '"/api/auth/logout"' in source, "Backend must expose login/session auth endpoints")
    require("Authorization" in source and "Bearer" in source and "verify_session_token" in source, "Backend must support Bearer session auth")
    require('Access-Control-Allow-Origin", "*"' not in source, "Backend must not default CORS to wildcard")
    require("TASK_ALLOWED_ORIGINS" in source and "Origin" in source, "Backend must restrict CORS by allowed origins")
    require("MAX_BODY_SIZE" in source and "BodyTooLarge" in source and "413" in source, "Backend must enforce request body size limit")
    deploy_install = (ROOT / "deploy" / "install-server.sh").read_text(encoding="utf-8")
    docker_sync_script = (ROOT / "deploy" / "sync-docker-web.sh").read_text(encoding="utf-8")
    nginx_conf = (ROOT / "deploy" / "nginx-midscene-task.conf").read_text(encoding="utf-8")
    env_example = (ROOT / "deploy" / "midscene.env.example").read_text(encoding="utf-8")
    require("300 * 1024 * 1024" in source and "_limit_mb(limit)" in source, "Backend request body limit must default to 300MB and show the active limit")
    require("client_max_body_size 300m" in nginx_conf, "Nginx template must allow 300MB uploads")
    require("NGINX_CLIENT_MAX_BODY_SIZE=\"${NGINX_CLIENT_MAX_BODY_SIZE:-300m}\"" in deploy_install and "midscene-upload-size.conf" in deploy_install, "Installer must apply 300MB Nginx upload override")
    require("find /etc/nginx -type f" in deploy_install and "s/client_max_body_size[[:space:]][^;]*;" in deploy_install, "Installer must replace older Nginx client_max_body_size values")
    require("/usr/share/nginx/html/task-manager.html" in deploy_install and "/usr/share/nginx/html/reports/task-manager.html" in deploy_install and "existing_container_pages" in deploy_install, "Installer must always publish both root and /reports Docker web entrypoints")
    require("/usr/share/nginx/html/task-manager.html" in docker_sync_script and "/usr/share/nginx/html/reports/task-manager.html" in docker_sync_script and "existing_pages" in docker_sync_script, "Docker web sync script must keep both legacy root and /reports URLs available")
    require("TASK_MAX_BODY_SIZE\" \"314572800" in deploy_install and "TASK_MAX_UPLOAD_BODY_SIZE\" \"314572800" in deploy_install, "Installer must set backend upload body limits to 300MB")
    require("TASK_MAX_BODY_SIZE='314572800'" in env_example and "TASK_MAX_UPLOAD_BODY_SIZE='314572800'" in env_example, "Environment example must document 300MB upload limits")
    require("SONIC_CALLBACK_TOKEN" in source and "query token auth is deprecated" in source, "Sonic callback auth must be separated and query token deprecated")
    config_source = (ROOT / "task_server" / "config.py").read_text(encoding="utf-8")
    require("MIDSCENE_API_KEY" in config_source and "MIDSCENE_BASE_URL" in config_source, "Task model config must accept MIDSCENE_API_KEY/MIDSCENE_BASE_URL aliases")
    require('TOKEN = os.getenv("MIDSCENE_RUNNER_TOKEN", "").strip()' in source or 'MIDSCENE_RUNNER_TOKEN", ""' in source, "Runner token must not default to midscene2026")
    require('SONIC_CALLBACK_TOKEN = os.getenv("SONIC_CALLBACK_TOKEN", "").strip()' in source or 'SONIC_CALLBACK_TOKEN", ""' in source, "Sonic callback token must not default to runner token")
    require('TASK_SESSION_SECRET = os.getenv("TASK_SESSION_SECRET", "").strip()' in source or 'TASK_SESSION_SECRET", ""' in source, "Session secret must not default to runner token")
    require("TASK_ALLOW_QUERY_TOKEN" in source and "ALLOW_QUERY_TOKEN" in source and "if not ALLOW_QUERY_TOKEN" in source, "Query token auth must be disabled unless explicitly enabled")
    require("validate_runtime_secrets()" in source and "TASK_ADMIN_PASSWORD_HASH 未配置" in source, "Production startup must validate strong secrets and admin password hash")
    router_source = (ROOT / "task_server" / "router.py").read_text(encoding="utf-8")
    job_service_source = (ROOT / "task_server" / "services" / "job_service.py").read_text(encoding="utf-8")
    runner_service_source = (ROOT / "task_server" / "services" / "runner_service.py").read_text(encoding="utf-8")
    sonic_service_source = (ROOT / "task_server" / "services" / "sonic_service.py").read_text(encoding="utf-8")
    yaml_service_source = (ROOT / "task_server" / "services" / "yaml_service.py").read_text(encoding="utf-8")
    yaml_executable_scorer_source = (ROOT / "task_server" / "services" / "yaml_executable_scorer.py").read_text(encoding="utf-8")
    yaml_execution_plan_source = (ROOT / "task_server" / "services" / "yaml_execution_plan.py").read_text(encoding="utf-8")
    ai_skill_service_source = (ROOT / "task_server" / "services" / "ai_skill_service.py").read_text(encoding="utf-8")
    automation_filter_source = (ROOT / "ai_skills" / "prompts" / "automation_filter.v1.md").read_text(encoding="utf-8")
    knowledge_service_source = (ROOT / "task_server" / "services" / "knowledge_service.py").read_text(encoding="utf-8")
    schemas_source = (ROOT / "task_server" / "schemas.py").read_text(encoding="utf-8")
    agent_service_source = (ROOT / "task_server" / "services" / "agent_service.py").read_text(encoding="utf-8")
    repair_service_source = (ROOT / "task_server" / "services" / "repair_service.py").read_text(encoding="utf-8")
    storage_source = (ROOT / "task_server" / "storage.py").read_text(encoding="utf-8")
    case_service_source = (ROOT / "task_server" / "services" / "case_service.py").read_text(encoding="utf-8")
    execution_adapter_source = (ROOT / "task_server" / "execution" / "execution_adapter.py").read_text(encoding="utf-8")
    dag_wrapper_source = (ROOT / "task_server" / "workflow" / "dag_safe" / "dag_wrapper.py").read_text(encoding="utf-8")
    trace_exporter_source = (ROOT / "task_server" / "core" / "debugger" / "trace_exporter.py").read_text(encoding="utf-8")
    snapshot_store_source = (ROOT / "task_server" / "core" / "replay" / "snapshot_store.py").read_text(encoding="utf-8")
    app_js_source = (ROOT / "js" / "app.js").read_text(encoding="utf-8")
    agent_workbench_source = (ROOT / "js" / "agent-workbench.js").read_text(encoding="utf-8")
    task_page_source = (ROOT / "task-manager.html").read_text(encoding="utf-8")
    execution_js_source = (ROOT / "js" / "execution.js").read_text(encoding="utf-8")
    trace_viewer_source = (ROOT / "trace-viewer.html").read_text(encoding="utf-8")
    prompt_builder_source = (ROOT / "task_server" / "prompts" / "builders" / "business_context_builder.py").read_text(encoding="utf-8")
    agent_prompt_source = (ROOT / "task_server" / "prompts" / "templates" / "agent.prompt").read_text(encoding="utf-8")
    case_prompt_source = (ROOT / "task_server" / "prompts" / "templates" / "case.prompt").read_text(encoding="utf-8")
    require("from .config import ID_COUNTER" not in storage_source and "_ID_COUNTER" in storage_source and "_ID_LOCK" in storage_source, "storage must own ID state instead of importing config global state")
    require('os.getenv(\n    "CASE_INDEX_PATH"' in case_service_source and 'os.path.join(TASK_DIR, "case-index.json")' in case_service_source, "case_service CASE_INDEX_PATH must be configurable and not hardcoded to /opt")
    require("class AgentContext" in agent_service_source and "class ToolRegistry" in agent_service_source and '"normalizedInput": normalized_input' in agent_service_source, "Agent service must expose normalized input and a tool registry")
    require('"sourceInputs": self.source_inputs' in agent_service_source and '"requirementText": self.requirement_text' in agent_service_source, "AgentContext must preserve uploaded source inputs and requirement text")
    require("def _agent_input_summary" in agent_service_source and '"inputSummary": _agent_input_summary' in agent_service_source and "def _agent_run_with_input_summary" in agent_service_source, "Agent run APIs must expose the original input summary for history/detail pages")
    require("def _ensure_business_flow_constraint" in agent_service_source and '"businessFlowConstraint"' in agent_service_source and '"toolEligibility"' in agent_service_source, "Agent service must persist a runtime Business Flow Constraint Layer")
    require("def _business_flow_keywords" in agent_service_source and '"businessFlowKeywords"' in agent_service_source and "业务主链（必须优先匹配）" in agent_service_source, "Agent case matching must use business-flow keywords before widening retrieval")
    require("def _keyword_source_text" in agent_service_source and "CASE_MATCH_META_KEYWORD_PARTS" in agent_service_source and 'constraint.get("source") or "") == "default"' in agent_service_source, "Agent keyword extraction must ignore platform metadata and default business-flow placeholders")
    require("def _probe_agent_ai_health" in agent_service_source and '"agentAiHealth"' in agent_service_source and "def _record_agent_ai_decision" in agent_service_source and '"agentAiDecisions"' in agent_service_source, "Agent service must expose AI health and decision observability")
    require("def _normalize_agent_goal_analysis" in agent_service_source and '"validated": True' in agent_service_source and "business_constraint=business_constraint" in agent_service_source, "Agent AI outputs must be validated and grounded to business flow during semantic retrieval")
    require("def _checkpoint_agent_state" in agent_service_source and '"agentCheckpoints"' in agent_service_source and '"step_started"' in agent_service_source and '"step_finished"' in agent_service_source, "Agent service must checkpoint state around each execution step")
    require("def _evaluate_agent_quality_gate" in agent_service_source and '"agentQualityGates"' in agent_service_source and '"case_retrieval_decision"' in agent_service_source and '"plan_grounding"' in agent_service_source, "Agent service must apply deterministic quality gates to AI decisions")
    require('"executionReports"' in agent_service_source and '"yamlExecutionRefs"' in agent_service_source and '"reportCount"' in agent_service_source and '"nextActions"' in agent_service_source, "Agent report artifacts must distinguish HTML reports from YAML execution refs and expose readable summary fields")
    require("class ExecutionAdapter" in execution_adapter_source and "create_pending_job" in execution_adapter_source and "Sonic 单条远程执行暂不直接创建临时套件" in execution_adapter_source, "ExecutionAdapter must default to local Runner and avoid Sonic temporary suites")
    require("class DAGWrapper" in dag_wrapper_source and "ExecutionPlan" in dag_wrapper_source and "SimpleDAG" in dag_wrapper_source, "DAG safe wrapper modules must be present")
    require("class TraceExporter" in trace_exporter_source and "load_agent_runs" in trace_exporter_source and "load_jobs" in trace_exporter_source, "Trace exporter must use real Agent and Job data")
    require("SNAPSHOT_FILE" in snapshot_store_source and "execution-snapshots.json" in snapshot_store_source and "write_json_file" in snapshot_store_source, "Replay snapshots must persist to learning storage")
    for route in ('"/api/debug/traces"', '"/api/debug/snapshots"', '"/api/debug/replay"', '"/api/debug/diff"', '"/api/debug/dag/run"', '"/api/debug/dag/parallel"'):
        require(route in router_source, f"Backend missing standardized debug route: {route}")
    require("/trace-viewer.html" in (ROOT / "task_server" / "app.py").read_text(encoding="utf-8"), "Backend must serve trace-viewer.html")
    require("Trace 回放" in execution_js_source and "/debug/traces" in execution_js_source and "/debug/replay" in execution_js_source and "/debug/diff" in execution_js_source, "Execution UI must expose Trace replay and diff")
    require("Execution Trace Viewer" in trace_viewer_source and "/debug/traces" in trace_viewer_source and "sessionToken" in trace_viewer_source, "Standalone Trace Viewer must call real trace API with session auth")
    require("read_json_file," in job_service_source and "def load_task_meta" in job_service_source and "def load_task_apps" in job_service_source, "job_service must import read_json_file for task meta/app loading")
    require("clean_filename," in job_service_source and "def update_task_meta" in job_service_source and "def task_key" in job_service_source, "job_service must import clean_filename for task meta updates")
    require("unique_millis_id," in yaml_service_source and "def generate_job_id" in yaml_service_source and "def new_case_set_id" in yaml_service_source, "yaml_service must import unique_millis_id for generation IDs")
    require("unique_millis_id," in knowledge_service_source and "asset_id = file_data.get" in knowledge_service_source, "knowledge_service must import unique_millis_id for UI asset IDs")
    require('prefix = "/api/runner/jobs/"' in router_source and 'action = parts[1]' in router_source, "Runner job action route must parse job_id/action after stripping prefix")
    require("_handle_runner_job_progress(handler, job_id)" in router_source, "Runner progress route must pass parsed job_id")
    require("_handle_runner_job_report_ready(handler, job_id)" in router_source, "Runner report-ready route must pass parsed job_id")
    require("_handle_runner_job_result(handler, job_id)" in router_source, "Runner result route must pass parsed job_id")
    require("_handle_runner_job_progress(handler, parts[3])" not in router_source, "Runner route must not use old path index parsing")
    require("append_job_event" in router_source and "进度回传" in router_source and "执行结果" in router_source, "Runner jobs must persist progress/result events")
    require('r"^/api/jobs/([^/]+)/analyze-failure$"' in router_source and "_read_job_failure_material" in router_source and "stdout.log" in router_source and "stderr.log" in router_source and "summary.json" in router_source, "Job failure analysis must read full runner logs from the backend")
    require('"/api/sonic/refresh-bridges"' in router_source and "sonic_refresh_bridge_scripts" in router_source, "Backend must expose Sonic bridge script refresh route")
    require("def sonic_refresh_bridge_scripts" in sonic_service_source and "sonic_upsert_bridge_step" in sonic_service_source, "Sonic service must refresh stored bridge Groovy steps")
    require("不修改 YAML 或基线内容" in sonic_service_source and "runner token" in sonic_service_source, "Bridge refresh must be documented as script/token-only")
    require('"PREPARE_SOURCE"' in schemas_source, "Agent state machine must include PREPARE_SOURCE before matching cases")
    require("def _tool_prepare_source" in agent_service_source and '"PREPARE_SOURCE": _tool_prepare_source' in agent_service_source, "Agent service must implement and register PREPARE_SOURCE")
    require("def normalize_ai_object" in repair_service_source and "parsed_obj.get" in repair_service_source, "AI repair must normalize model output before using .get")
    require("def extract_midscene_tasks" in yaml_service_source, "YAML service must provide shared Midscene task extraction")
    require("def validate_midscene_yaml_executability" in yaml_service_source, "YAML service must provide executable YAML validation")
    require("validate_midscene_yaml_executability(yaml_text)" in agent_service_source, "Agent YAML validation must use the shared executable YAML validator")
    for service_path in (ROOT / "task_server" / "services").glob("*.py"):
        text = service_path.read_text(encoding="utf-8")
        if service_path.name in ("yaml_service.py", "yaml_pattern_service.py", "yaml_static_validator.py"):
            continue
        require('parsed.get("tasks")' not in text, f"{service_path.name} must not directly read parsed.tasks; use extract_midscene_tasks")
    require('"/api/sonic/callback-diagnose"' in router_source and "healthReachableFromServer" in router_source, "Backend must expose callback diagnosis for HTTP 000")
    require("explainCallbackHttp000" in app_js_source and "/api/sonic/callback-diagnose" in app_js_source, "Frontend must show friendly HTTP 000 callback diagnosis")
    require("AI 分析并生成修复草稿" in task_page_source and "AI 修复当前文件" not in task_page_source, "Main repair button must say repair draft, not direct overwrite")
    require('"yaml": original_yaml' in agent_service_source and 'resp.get("fixedYaml") or resp.get("fixed_yaml") or resp.get("optimizedYaml") or resp.get("yaml")' in agent_service_source, "Agent repair draft must send real YAML to AI Gateway and read all supported AI YAML response fields")
    require('"repairSummary"' in agent_service_source and '"aiAttempted"' in agent_service_source and '"aiUsed"' in agent_service_source and '"evidenceSources"' in agent_service_source, "Agent repair draft must expose evidence, AI usage, and validation summary")
    require("def _agent_failed_execution_items" in agent_service_source and '"failedExecutionItems"' in agent_service_source, "Agent failure, repair, and rerun steps must share one failed-task source of truth")
    require('"failedTaskCount"' in agent_service_source and '"repairTargetCount"' in agent_service_source and '"draftCount"' in agent_service_source, "Agent repair summary must expose batch scope and draft counts")
    require('"sourceFailedCount"' in agent_service_source and '"targetCount"' in agent_service_source and '"rerunSources"' in agent_service_source, "Agent rerun must expose source failed count, target count, and rerun mappings")
    require('"rerunProgress"' in agent_service_source and '"learningSummary"' in agent_service_source, "Agent rerun and learning steps must persist readable timeline summaries")
    require("def _agent_prepare_repair_rerun_targets" in agent_service_source and '"usesRepairDraft"' in agent_service_source and '"notRerunOriginalYaml"' in agent_service_source, "Agent safe rerun must materialize repair drafts and avoid silently rerunning old YAML")
    require("已有修复草稿但没有可执行 YAML" in agent_service_source and "没有可用修复草稿，未重跑旧 YAML" in agent_service_source, "Agent safe rerun must explain missing or invalid repair drafts instead of reporting false success")
    require(
        '"PLAN", "PREPARE_SOURCE", "IMPACT_ANALYSIS", "CASE_RETRIEVAL", "MATCH_CASES"' in agent_service_source,
        "Agent step order must prepare source, analyze impact, retrieve cases, then match cases"
    )
    require('"sourceType"' in agent_service_source and '"sourceRefs"' in agent_service_source and '"sourceContext"' in agent_service_source, "Agent runs must persist sourceType/sourceRefs/sourceContext")
    require("def _agent_source_material_context" in agent_service_source and '"uploadedFiles"' in agent_service_source and '"uploadedImages"' in agent_service_source and '"sourceSummary"' in agent_service_source, "Agent prepare_source must normalize uploaded files/images into sourceContext")
    require("Figma UI 图" in agent_service_source and "其中上传截图" in agent_service_source, "Agent source summary must distinguish Figma exported UI images from user-uploaded screenshots")
    require("def _agent_visual_reference_report" in agent_service_source and '"visualReferenceReport"' in agent_service_source and '"soft_reference"' in agent_service_source and '"hardGate": False' in agent_service_source and '"aiJudgementRequired"' in agent_service_source and '"sentToAiForJudgement"' in agent_service_source, "Agent must expose uploaded screenshots as traceable AI visual soft references, not hard gates")
    require("visual_image_assets = figma_images + uploaded_image_assets" in yaml_service_source and "refine_cases_with_yaml_visual_batches" in yaml_service_source and "uploaded_image_assets" in yaml_service_source, "Uploaded screenshots must be included in AI visual judgment for YAML generation")
    require("def build_cases_payload_from_skills(" in ai_skill_service_source and "allow_entry_visibility_fast_path=True" in ai_skill_service_source and "app_package=app_package" in ai_skill_service_source, "AI skill case payload builder must accept app context and entry fast-path policy passed by YAML generation")
    require(
        "def entry_visibility_fast_path_enabled" in yaml_service_source
        and "disableEntryVisibilityFastPath" in yaml_service_source
        and "deterministic_entry_visibility_source = entry_visibility_fast_path_enabled" in yaml_service_source
        and "should_fast_path_baidu_entry_visibility(title, module, text_assets)" in yaml_service_source
        and "forceEntryVisibilityFastPath" in yaml_service_source
        and "allow_entry_visibility_fast_path=deterministic_entry_visibility_source" in yaml_service_source
        and 'title = d.get("title") or d.get("target") or d.get("goal") or "UI自动化用例"' in yaml_service_source
        and "入口可见性快路径使用本地短链路生成，跳过 AI 基线重排" in yaml_service_source
        and "入口可见性快路径固定生成 3 条首批短链路冒烟" in yaml_service_source
        and "入口可见性快路径：跳过重型 AI 需求解析" in yaml_service_source,
        "Entry visibility generation must support smoke fast path and explicit complete-scope bypass into the full AI pipeline",
    )
    require("def _agent_pdf_text_from_base64" in agent_service_source and "pypdf.PdfReader" in agent_service_source, "Agent must extract PDF requirement text from uploaded source files")
    require("def _infer_agent_source_type" in agent_service_source and 'run["sourceType"] = source_type' in agent_service_source, "Agent must promote manual source type when requirement/Figma material is attached")
    check_agent_generation_pipeline_normalizes_validation_state()
    check_agent_regression_scope_preserves_new_requirement_generation()
    check_generated_yaml_short_guards_and_execution_level_floor()
    check_generated_yaml_semantic_scope_and_visual_trace()
    check_agent_executable_gate_invokes_ai_rewrite()
    check_midscene_yaml_validation_is_mapping()
    check_yaml_static_validation_and_patterns()
    require("def _agent_fallback_yaml_draft" in agent_service_source and "fallback_after_empty_ai_yaml" in agent_service_source and "fallback_after_invalid_ai_yaml" in agent_service_source, "Agent YAML generation must create confirmable drafts when AI returns empty or invalid YAML")
    require("def _agent_generate_yaml_from_ui_pipeline" in agent_service_source and "generate_ui_yaml_from_request" in agent_service_source and '"split_by_case"' in agent_service_source and "ui_yaml_pipeline" in agent_service_source, "Agent new-requirement YAML generation must reuse the full requirement/Figma/YAML pipeline before fallback")
    require(
        '"forceEntryVisibilityFastPath": direct_entry_visibility' in agent_service_source
        and '"disableEntryVisibilityFastPath": has_entry_visibility_intent and not direct_entry_visibility' in agent_service_source
        and "def _agent_use_direct_entry_visibility_smoke" in agent_service_source
        and '"target": title' in agent_service_source,
        "Agent YAML generation must reserve the direct entry fast path for smoke scope and disable it for complete requirement scope",
    )
    require(
        "def _agent_entry_visibility_intent(run)" in agent_service_source
        and "entry_visibility_intent = _agent_entry_visibility_intent(run)" in agent_service_source
        and "agent_direct_entry_visibility_smoke.v1" in agent_service_source
        and "已直接生成入口可见性短链路冒烟 YAML" in agent_service_source,
        "Agent must directly generate generic entry visibility smoke YAML instead of blocking in the generic YAML generator",
    )
    from task_server.services import agent_service as agent_runtime
    from task_server.services.yaml_executable_scorer import score_midscene_yaml_executable as score_entry_smoke_yaml
    entry_run = {
        "target": "基础打印新增百度网盘入口",
        "module": "基础打印",
        "requirementText": "基础打印的入口在首页   文档打印 照片打印 扫描复印",
        "appPackage": "com.xbxxhz.box",
        "scope": "smoke",
    }
    entry_smoke_yaml = agent_runtime._agent_entry_visibility_smoke_yaml(entry_run)
    require(agent_runtime._agent_use_direct_entry_visibility_smoke(entry_run), "Smoke scope must keep the proven direct entry-visibility path")
    require(not agent_runtime._agent_use_direct_entry_visibility_smoke({**entry_run, "scope": "regression"}), "Regression scope must bypass the single-smoke shortcut and generate the complete requirement suite")
    require(
        "应用首页或底部导航中名称为文档打印的入口" in entry_smoke_yaml
        and "文档打印页面或文档打印导入入口区域已加载，并展示百度网盘入口" in entry_smoke_yaml
        and entry_smoke_yaml.count("- aiTap:") == 1
        and "应用首页或底部导航中的打印、学习打印、小白打印入口" not in entry_smoke_yaml
        and "页面同时展示文档打印、照片打印、扫描复印入口" not in entry_smoke_yaml
        and "目标页面" not in entry_smoke_yaml,
        "Agent entry visibility smoke must infer 文档打印 and use one direct semantic hop to the target page",
    )
    entry_smoke_score = score_entry_smoke_yaml(entry_smoke_yaml, generated=True)
    require(entry_smoke_score.get("ok") and entry_smoke_score.get("executionLevel") == "executable", "Agent entry visibility smoke must pass Runner executable gate without needs_review")
    require(
        "monkey -p {app_package} -c android.intent.category.LAUNCHER 1" not in agent_service_source
        and "terminate: {app_package}" in agent_service_source
        and "应用首页或启动页已打开" in agent_service_source
        and "可看到{target_page}入口或底部导航" in agent_service_source
        and "应用首页或底部导航中名称为{target_page}的入口" in agent_service_source
        and "只点击与“{target_page}”文字对应的目标" in agent_service_source
        and "{target_page}页面或{target_page}导入入口区域已加载，并展示{entry_label}入口" in agent_service_source
        and "应用首页或底部导航中的打印、学习打印、小白打印入口" not in agent_service_source,
        "Agent entry visibility smoke must cold-start the app and directly enter the inferred target page without an ambiguous intermediate print-home hop",
    )
    require("def _build_agent_quality_report" in agent_service_source and '"qualityReport"' in agent_service_source and '"完整测试用例 .mm"' in agent_service_source and '"可自动化 YAML"' in agent_service_source, "Agent generation must persist a reviewer-friendly quality report")
    require("figma_image_count = max" in agent_service_source and '"figmaImageCount": figma_image_count' in agent_service_source and '"count": figma_image_count' in agent_service_source, "Agent quality report must reuse parsed Figma visual-reference counts")
    require("def _agent_is_new_requirement_run" in agent_service_source and "new_requirement_source" in agent_service_source, "Agent must treat requirement/Figma inputs as new requirements unless reuse/regression is explicit")
    require("def _agent_wants_all_existing_cases" in agent_service_source and "识别到全量执行意图，复用已有 YAML" in agent_service_source and "不生成 YAML 草稿" in agent_service_source, "Agent must route explicit all-case requests to existing YAML reuse instead of draft generation")
    require('"matchAll": _agent_wants_all_existing_cases(target)' in agent_service_source and "只有用户明确说" in agent_service_source, "Agent goal analysis must not treat generic regression/baseline wording as all-case intent")
    require("def _ensure_agent_goal_analysis" in agent_service_source and "_ensure_agent_goal_analysis(run)" in agent_service_source and "必须优先用语义理解识别用户真实测试目标" in agent_service_source, "Agent target understanding must be AI-first and shared by downstream steps")
    require('"aiKeywords": ai_keywords' in agent_service_source and "goalAnalysis" in agent_service_source, "Agent impact analysis must use AI goal-analysis keywords before rule fallback")
    require("ai_direct = _ai_select_cases" in agent_service_source and "AI 先直选已有 YAML，规则召回只作为兜底" in agent_service_source, "Agent case retrieval must let AI select YAML before rule recall fallback")
    require('"autoConfirmed": True' in agent_service_source and "已自动确认进入下一步" in agent_service_source, "Agent mindmap-pipeline YAML must auto-confirm after executable validation")
    require("def cases_to_separate_midscene_yamls" in yaml_service_source and '"mode": "split_by_case"' in yaml_service_source, "New requirement YAML generation must split automation cases into separate YAML files")
    require("cases_to_separate_midscene_yamls" in router_source and '"yamlFileCount"' in router_source, "YAML generation API must return split YAML file metadata")
    require("def _confirm_agent_yaml_files" in agent_service_source and '"generatedYamlPaths"' in agent_service_source and "YAML 文件" in agent_service_source, "Agent new-requirement pipeline must confirm multiple generated YAML files")
    require("non_executable = []" in agent_service_source and 'executable_score.get("executionLevel") != "executable"' in agent_service_source and '"nonExecutable": non_executable' in agent_service_source, "Agent must not auto-confirm generated YAML below executable level")
    require('"executableFileCount"' in agent_service_source and 'str(item.get("executionLevel") or item.get("level") or "").strip().lower() == "executable"' in agent_service_source, "Agent quality report must count only executable generated YAML as executable tasks")
    require("def _ensure_agent_entry_visibility_smoke_ref" in agent_service_source and "_agent_entry_visibility_smoke_filename(run)" in agent_service_source and '"autoGeneratedSmoke": True' in agent_service_source, "Agent must add a deterministic entry-visibility smoke YAML when generated cases lack a stable first-smoke candidate")
    require("生成结果缺少稳定首批冒烟候选" in agent_service_source and "smokeCandidate" in agent_service_source and "runnerCandidate" in agent_service_source, "Agent fallback entry-visibility smoke must be eligible for first Runner smoke")
    require("has_stable_smoke_candidate" in agent_service_source and "max_action_count <= 8" in agent_service_source and "max_wait_count <= 6" in agent_service_source and 'replanRisk") or "") == "high"' in agent_service_source, "Agent must not treat long high-replan generated YAML as a stable first-smoke candidate")
    require("def _agent_runner_job_material" in agent_service_source and '"summaryText"' in agent_service_source and 'read_json_file(safe_join(run_dir, "summary.json")' in agent_service_source, "Agent report collection must read runner summary.json for failed jobs")
    require('"summaryText": fj.get("summaryText", "")' in agent_service_source and 'f"summary：{target_job.get(' in agent_service_source, "Agent failure analysis and repair evidence must include runner summary details")
    require("def _agent_failure_ai_payload" in agent_service_source and '"screenshotDesc": str(primary_failure.get("failureReason")' in agent_service_source, "Agent failure analysis must populate the concrete AI Gateway task/yaml/log/screenshot contract")
    require("def _agent_failure_type_from_review" in agent_service_source and '"failureReview": failure_review' in agent_service_source and '"failureReview": fj.get("failureReview")' in agent_service_source, "Agent must preserve Runner failure reviews through report collection and AI analysis")
    require("def _agent_canonical_failure_type" in agent_service_source and '"failureKind": failure_kind' in agent_service_source and "_agent_should_confirm_unknown_failure(run, ft)" in agent_service_source, "Agent must canonicalize concrete failure labels and avoid repeated UNKNOWN confirmation")
    require("def _agent_repair_has_semantic_change" in agent_service_source and '"sleep_only_or_noop"' in agent_service_source and '"rejectedYaml"' in agent_service_source, "Agent must reject sleep-only and semantically equivalent AI repair candidates")
    require(
        "def _agent_summary_error_excerpt" in agent_service_source
        and '"summaryText": summary_text[:4000]' in agent_service_source
        and "Midscene 摘要" in agent_service_source,
        "Agent failed execution normalization must preserve Midscene summary errors for failure analysis and repair",
    )
    require("def _confirm_agent_yaml_content_as_files" in agent_service_source and '"autoConfirmedFallback"' in agent_service_source and "已自动拆分并采用多任务兜底 YAML" in agent_service_source, "Agent fallback YAML must auto-confirm and split into files for Runner mode")
    require("def _save_agent_yaml_draft" in agent_service_source and '"WAIT_CONFIRM"' in agent_service_source and '"generated_yaml_draft"' in agent_service_source, "Agent fallback YAML drafts must still support manual confirmation")
    require('mark_step_success("GENERATE_YAML"' in agent_service_source and "已人工确认 YAML 草稿" in agent_service_source, "Confirming a YAML draft must mark GENERATE_YAML complete and resume validation/execution")
    require("确认草稿后再同步 Sonic" not in agent_service_source, "Runner-mode YAML draft confirmation must not tell users to sync Sonic")
    require("pypdf" in (ROOT / "deploy" / "install-server.sh").read_text(encoding="utf-8"), "Server install script must install pypdf for PDF requirement extraction")
    require("def _load_figma_context_for_agent" in agent_service_source and "load_figma_generation_context" in agent_service_source and '"figmaUsedPages"' in agent_service_source and '"figmaIgnoredPages"' in agent_service_source, "Agent Figma source must reuse the shared Figma requirement-filter extraction pipeline")
    require('"figmaScopeQuery"' in agent_service_source and 'file_name_query = " ".join' in agent_service_source, "Agent Figma scope selection must use concise target/file-name query")
    require('query_text = str(context.get("requirementText") or "")[:1200]' in agent_service_source, "Agent Figma scope may only fall back to requirement text when concise query is empty")
    require("preparedFigmaContextPath" in agent_service_source and '"prepared_figma_context": prepared_figma_context' in agent_service_source, "Agent YAML generation must reuse prepared Figma context instead of reparsing when available")
    require("def _prepared_figma_context_from_request" in yaml_service_source and "复用 Figma 解析" in yaml_service_source, "YAML generation must support prepared Figma context reuse")
    require("def extract_pdf_text" in yaml_service_source and "pypdf.PdfReader" in yaml_service_source, "YAML generation must extract PDF requirement text without relying only on pdftotext")
    require("raw_review_type" in yaml_service_source and "if not isinstance(analysis, dict)" in yaml_service_source, "Generated case payload normalization must coerce malformed analysis/review containers")
    require(
        "def _ensure_rich_generation_scope" in yaml_service_source
        and '"synthetic_requirement_points_added": 0' in yaml_service_source
        and 'coverage_rounds = 2 if rich_scope.get("extra_coverage_round") else 1' in yaml_service_source,
        "Rich requirement/Figma inputs must stay soft references and must not synthesize coverage requirements",
    )
    require(
        "generation_targets_for_scope" in ai_skill_service_source
        and "generation_scope_plan=execution_scope_plan" in yaml_service_source
        and "targets=planned_generation_targets" in yaml_service_source,
        "The platform-clamped 3/5/8 scope plan must govern generation, coverage audit and final smoke selection",
    )
    require("base_payload = normalize_cases_payload(base_payload)" in ai_skill_service_source and "grounded = normalize_cases_payload(grounded)" in ai_skill_service_source, "Visual grounding must normalize payload containers before merging review/analysis")
    require("def _run_ai_skill_call_with_hard_timeout" in ai_skill_service_source and "future.result(timeout=timeout_seconds)" in ai_skill_service_source and "executor.shutdown(wait=False, cancel_futures=True)" in ai_skill_service_source, "Text AI skills must have a hard timeout around AI Gateway calls")
    require("except TimeoutError:" in ai_skill_service_source and 'f"AI Gateway skill {skill_name}"' in ai_skill_service_source, "AI Gateway skill timeout must surface to requirement/scenario fallbacks instead of falling into another long provider call")
    require("respect_global_timeout=timeout_seconds is None" in ai_skill_service_source and "retry_count=None if timeout_seconds is None else 0" in ai_skill_service_source, "Short visual grounding timeouts must bypass the global long AI timeout")
    require("AGENT_GENERATE_YAML_TIMEOUT_SECONDS = max(300, env_int(\"MIDSCENE_AGENT_GENERATE_YAML_TIMEOUT_SECONDS\", 900))" in yaml_service_source and 'job_type == "agent_generate_yaml"' in yaml_service_source, "Agent YAML generation must use a bounded Agent-specific timeout")
    require("expire_generate_job_if_stale" in agent_service_source and "timeout_seconds=900" in agent_service_source, "Agent YAML generation watcher must expire stale generation jobs instead of leaving Agent stuck")
    require('flow.append(f"校验{entry_label}入口可见")' in agent_service_source and 'flow.append(f"进入{\'扫描复印\' if label == \'复印扫描\' else label}")' in agent_service_source and 'business_flow_source = "requirement_text"' in agent_service_source, "Agent business flow must prefer explicit requirement entry chains over incidental Figma page text")
    require("def refine_cases_with_yaml_visual_batches" in yaml_service_source and "YAML_VISUAL_BATCH_SIZE" in yaml_service_source and "legacy_fallback=False" in yaml_service_source, "YAML visual grounding must run in bounded batches without doubling timeout via legacy fallback")
    require("def build_executable_smoke_yaml_policy_text" in yaml_service_source and "def review_generated_yaml_smoke_stability" in yaml_service_source and '"yamlSmokeStability"' in yaml_service_source, "YAML generation must enforce and report Runner smoke-execution stability")
    require("yaml_static_validator.py" in "\n".join(str(path) for path in (ROOT / "task_server" / "services").glob("*.py")) and (ROOT / "task_server" / "config_data" / "yaml_actions.json").exists(), "YAML generation must have a static action contract and validator")
    require("useGlobalBaselineProfile" in yaml_service_source and "use_global_baseline_profile" in yaml_service_source and "build_yaml_pattern_contract_text" in yaml_service_source, "YAML generation must gate global baseline pattern extraction behind useGlobalBaselineProfile")
    require("yaml_template_matcher.py" in "\n".join(str(path) for path in (ROOT / "task_server" / "services").glob("*.py")) and "select_best_baseline_template" in yaml_service_source and '"yaml_template_matcher"' in yaml_service_source, "YAML generation must select Top baseline templates before prompting")
    require(
        "yaml_baseline_cache.py" in "\n".join(str(path) for path in (ROOT / "task_server" / "services").glob("*.py"))
        and "search_baseline_examples" in yaml_service_source
        and "limit=3" in yaml_service_source
        and '"yaml_baseline_cache"' in yaml_service_source,
        "YAML generation must use cached Top3 baseline snippets instead of reading the full YAML library on every generation"
    )
    require(
        "@route_get(\"/api/yaml/baseline-cache/status\")" in router_source
        and "@route_post(\"/api/yaml/baseline-cache/refresh\")" in router_source,
        "YAML baseline cache status and refresh APIs must be exposed"
    )
    require("@route_post(\"/api/yaml/dry-run\")" in router_source and "dry_run_midscene_yaml" in router_source, "YAML dry-run API must be exposed for Agent/UI preflight")
    require("@route_post(\"/api/cases/rerun-smoke\")" in router_source and "generation_smoke_yaml_refs" in router_source and "create_pending_job(" in router_source, "Generated smoke YAML must support rerun without re-uploading source material")
    require("generation_smoke_rerun_default_limit(summary)" in router_source and "smoke_cases" in router_source and "MIDSCENE_AGENT_GENERATED_RUNNER_FIRST_SMOKE_LIMIT" in router_source and "run_all" in router_source and "totalSmokeCount" in router_source, "Generated smoke rerun must default to a first batch of at most 3 unless explicitly running all smoke cases")
    require("validate_yaml_static_executable" in yaml_service_source and '"yamlStaticValidation"' in yaml_service_source and '"execution_level"' in yaml_service_source, "Generated YAML must record static execution levels and validation results")
    require("def repair_generated_yaml_static_errors" in yaml_service_source and '"yamlStaticRepair"' in yaml_service_source and "只修复 YAML 结构和动作字段" in yaml_service_source, "Generated YAML must run a narrow static repair loop before writing/executing files")
    require("jobSkippedYamlFiles" in yaml_service_source and "静态可执行校验未通过" in yaml_service_source, "Generated YAML with static errors must not auto-create Runner jobs")
    require("def _agent_yaml_dry_run_for_ref" in agent_service_source and '"yamlDryRun"' in agent_service_source and '"runnerDryRun"' in agent_service_source and "Runner 下发前 dry-run 未通过" in agent_service_source, "Agent must dry-run YAML before validation, precheck and Runner job creation")
    require(
        "AGENT_GENERATED_RUNNER_EXPAND_BATCH_LIMIT" in agent_service_source
        and "def _agent_smoke_execution_blocker" in agent_service_source
        and "产品断言失败或页面状态不匹配会记录为测试结果" in yaml_execution_plan_source
        and "expandedBatchLimit" in agent_service_source
        and "expandedBatches" in agent_service_source
        and "第 {batch_index} 批扩展" in agent_service_source,
        "Agent must continue generated YAML execution in visible batches after the first smoke batch proves executability",
    )
    require("def apply_generated_case_scope_gate" in yaml_service_source and "需求范围不匹配的生成用例不再转换为自动化 YAML" in yaml_service_source, "Generated cases outside current requirement scope must be kept out of auto-run YAML")
    require("baseline.*" not in yaml_executable_scorer_source and "命中基线" in yaml_executable_scorer_source, "Generated baseline metadata comments must not be treated as successful baseline evidence")
    require("重跑来源" in agent_workbench_source and "修复文件" in agent_workbench_source and "progress.usesRepairDraft" in agent_workbench_source, "Agent UI must show whether rerun used repair drafts and which temporary YAML files were executed")
    require("def _agent_rerun_requires_serial_device" in agent_service_source and "安全重跑-同设备串行" in agent_service_source and '"serialSameDevice"' in agent_service_source, "Agent rerun must serialize jobs on the same fixed Runner/device")
    env_example = ENV_EXAMPLE.read_text(encoding="utf-8")
    require("MIDSCENE_AGENT_GENERATE_YAML_TIMEOUT_SECONDS='900'" in env_example and "MIDSCENE_YAML_VISUAL_BATCH_SIZE" in env_example and "MIDSCENE_GENERATED_ASSERTION_LIMIT" in env_example, "Deployment env example must expose bounded Agent YAML timeout, assertion density and visual batching knobs")
    require('ensure_env_default "MIDSCENE_AGENT_GENERATE_YAML_TIMEOUT_SECONDS" "900"' in deploy_install and 'upgrade_env_default_if_old "MIDSCENE_AGENT_GENERATE_YAML_TIMEOUT_SECONDS" "900" "7200|3600|1800"' in deploy_install, "Deployment must migrate old long Agent YAML timeout defaults")
    require("MIDSCENE_AGENT_GENERATED_RUNNER_EXPAND_BATCH_LIMIT" in env_example and "MIDSCENE_AGENT_GENERATED_RUNNER_EXPAND_BATCH_LIMIT" in deploy_install and "MIDSCENE_AGENT_GENERATED_RUNNER_FIRST_SMOKE_LIMIT" in env_example and "MIDSCENE_AGENT_GENERATED_RUNNER_FIRST_SMOKE_LIMIT" in deploy_install and 'MIDSCENE_AGENT_GENERATED_RUNNER_EXPAND_LIMIT" "5"' in deploy_install, "Deployment scripts must expose generated YAML first smoke and expansion batch size")
    require("MIDSCENE_YAML_BASELINE_CACHE_TTL_SECONDS" in env_example and "/opt/midscene-tasks/cache/yaml-baseline-cache.json" in env_example and "/opt/midscene-tasks/cache/yaml-baseline-cache.json" in deploy_install, "Deployment env example must expose TASK_DIR YAML baseline cache knobs")
    install_script = (ROOT / "deploy" / "install-server.sh").read_text(encoding="utf-8")
    require(
        "YAML_VISUAL_BATCH_SIZE = max(1, env_int(\"MIDSCENE_YAML_VISUAL_BATCH_SIZE\", 4))" in config_source
        and "YAML_VISUAL_TIMEOUT_SECONDS = max(60, env_int(\"MIDSCENE_YAML_VISUAL_TIMEOUT_SECONDS\", 900))" in config_source
        and 'ensure_env_default "MIDSCENE_YAML_VISUAL_BATCH_SIZE" "4"' in install_script
        and 'upgrade_env_default_if_old "MIDSCENE_YAML_VISUAL_BATCH_SIZE" "4" "8"' in install_script,
        "YAML visual grounding must default to smaller batches for large Figma inputs"
    )
    require(
        "def yaml_visual_total_budget_for_batches" in yaml_service_source
        and "total_batches * per_batch_timeout" in yaml_service_source
        and "visual_total_budget_seconds" in yaml_service_source
        and "explicit_timeout" in yaml_service_source,
        "YAML visual grounding total budget must scale with actual batch count and extend the outer generation job timeout"
    )
    require("服务重启后没有恢复后台线程" not in agent_service_source and "服务重启后线程丢失" not in yaml_service_source, "Generation timeout copy must not falsely blame manual service restart")
    require("text.find(\"[\")" in ai_skill_service_source and "return normalize_cases_payload(payload)" in ai_skill_service_source, "Model case JSON parser must accept root arrays as valid case payloads")
    require("fallback_normalized_payload" in yaml_service_source and "payload = normalize_cases_payload(payload)" in yaml_service_source, "Coverage repair failure must fall back to normalized payload instead of failing the full generation chain")
    require("def _agent_yaml_validation_state" in agent_service_source and "_agent_yaml_validation_state(artifacts.get(\"yamlValidation\"))" in agent_service_source, "Agent YAML validation state must be normalized before dict merging")
    require("AI_COVERAGE_TOTAL_BUDGET_SECONDS" in config_source and "respect_global_timeout=False" in ai_skill_service_source and "覆盖率审查：调用 coverage_auditor" in ai_skill_service_source, "Coverage auditor must use bounded model calls and report sub-step progress")
    require("def enforce_min_case_count_audit" in ai_skill_service_source and "case_count_below_min" in ai_skill_service_source, "Coverage auditor must not pass when automation case count is below target")
    require("def _agent_has_rich_requirement_material" in agent_service_source and "fallbackDisabled" in agent_service_source and "未采用兜底 YAML" in agent_service_source, "Agent must not auto-run fallback YAML for rich requirement/Figma inputs")
    require("def _agent_yaml_task_names_for_runner" in agent_service_source and '"task_names": task_names' in agent_service_source and '"target_task_name": target_task_name' in agent_service_source, "Agent Runner jobs must use YAML task names instead of file-name guesses")
    check_agent_fallback_yaml_auto_confirm_split()
    check_agent_prepared_figma_context_reuse()
    check_agent_risk_detail_explains_source()
    check_agent_requirement_background_delete_is_not_high_risk()
    check_agent_generation_orphan_recovery()
    check_yaml_reference_examples_are_general_step_library()
    check_generated_yaml_uses_single_final_assertion()
    check_ai_skills_receive_yaml_reference_context()
    check_ai_skill_timeout_fallbacks_are_requirement_scoped()
    check_smoke_selection_requires_explicit_ai_mark()
    check_yaml_runner_eligibility_filter()
    check_agent_yaml_validate_partial_quarantine()
    check_agent_yaml_validate_auto_repairs_missing_wait()
    check_agent_quarantine_refs_do_not_reenter_precheck()
    check_agent_execution_gate_repairs_before_smoke_selection()
    check_agent_runner_failure_reason_summary()
    check_agent_failure_ai_payload_has_primary_evidence()
    check_agent_failure_review_and_repair_guard()
    check_agent_quality_report_uses_figma_visual_reference()
    check_agent_figma_context_defaults()
    check_agent_high_risk_confirm_resumes_precheck()
    check_agent_completed_tool_step_recovers_and_avoids_hot_cancel_reads()
    check_agent_history_compacts_uploaded_blobs_after_prepare()
    check_agent_worker_start_is_idempotent()
    check_snapshot_store_concurrent_save()
    check_production_debug_execution_guard_static()
    check_execution_adapter_prompt_center_delay()
    check_mindmap_compact_mode()
    check_generation_volume_targets_modes()
    check_ai_gateway_fallback_and_skill_static()
    check_ai_yaml_generation_decision_chain_static()
    require("匹配全部用例（兜底模式）" not in agent_service_source, "Agent match must not fallback to all cases when AI/source is unclear")
    require("job_service.wait_jobs_finished" in agent_service_source, "Agent RUN_TASK must use job_service.wait_jobs_finished as the single implementation")
    require('"executionMode": execution_mode' in agent_service_source and 'should_run_suite = execution_mode == "SONIC_SUITE"' in agent_service_source, "Agent must default to Runner jobs and only run Sonic suite when explicitly requested")
    require('should_require_sonic = execution_mode == "SONIC_SUITE"' in agent_service_source and 'Runner 调试模式不阻断' in agent_service_source, "Execution precheck must not block Runner jobs on Sonic publish-only checks")
    require("Runner 调试模式已跳过 Sonic 项目/测试套绑定检查" in agent_service_source and "Runner 调试模式不需要访问 Sonic API" in agent_service_source, "Runner execution precheck must skip Sonic-only gates instead of showing them as failures")
    require("def _runner_precheck_should_warn_risk" in agent_service_source and "测试机执行的业务风险词只提醒，不阻断" in agent_service_source, "Runner execution precheck must warn on test-machine business risks without blocking debug execution")
    require("def _evaluate_risk_detail" in agent_service_source and '"riskDetail"' in agent_service_source and '"riskSource"' in agent_service_source and '"riskSnippet"' in agent_service_source, "Agent high-risk confirmations must include source and snippet details")
    require('step_name == "SYNC_SONIC" and execution_mode != "SONIC_SUITE"' in agent_service_source and "Runner 单条/多条调试模式不需要同步 Sonic" in agent_service_source, "Runner Agent execution must skip Sonic sync and run matched YAML directly")
    router_source = (ROOT / "task_server" / "router.py").read_text(encoding="utf-8")
    require("_start_agent_worker" in router_source and "target=_execute_agent_steps" not in router_source, "Agent routes must start workers through the duplicate-safe service helper")
    require("runner_yaml_dry_run" in agent_service_source and "mock_dry_run" in agent_service_source and "创建 {len(job_ids)} 个本地任务" in agent_service_source and "避免“匹配 1 条却跑完整套件”" in agent_service_source, "Agent RUN_TASK must explain dry-run/local Runner mode instead of suite execution")
    require('"runnerId": runner_id' in agent_service_source and '"deviceId": device_id' in agent_service_source and '"deviceStrategy": device_strategy' in agent_service_source, "Agent runs must persist selected Runner/device execution target")
    require('"runnerSelection"' in agent_service_source and "尚未选择执行设备" in agent_service_source and "runner_service.all_online_devices" in agent_service_source, "Agent execution precheck must validate selected/auto Runner devices")
    require('"runner_id": selected_runner_id' in agent_service_source and '"device_id": selected_device_id' in agent_service_source and '"device_strategy": selected_device_strategy' in agent_service_source, "Agent Runner jobs must use the selected Runner/device strategy")
    require('case.get("device_strategy") or "auto"' in execution_adapter_source, "ExecutionAdapter local Runner jobs must default to automatic online-device assignment")
    require("def _append_step_trace" in agent_service_source and "_persist_agent_run_snapshot" in agent_service_source, "Agent timeline steps must persist live trace for running tools")
    require("def cancel_agent_run(run_id, reason=" in agent_service_source and 'run["currentStep"] = "CANCELLED"' in agent_service_source and "_agent_cancel_progress_job" in agent_service_source, "Agent cancellation must mark a real cancelled state and cancel internal generation progress jobs")
    require("cancel_agent_run(run_id" in router_source and "^/api/agent-runs/([^/]+)/cancel" in router_source, "Agent cancel route must use the unified cancellation service")
    require("def delete_agent_run(run_id)" in agent_service_source and '"FAILED", "CANCELLED"' in agent_service_source and "不能直接删除" in agent_service_source, "Agent history deletion must remove terminal records and protect running runs")
    require('route_delete_regex(r"^/api/agent-runs/([^/]+)$")' in router_source and "delete_agent_run(run_id)" in router_source, "Backend must expose DELETE /api/agent-runs/{runId}")
    require("_persisted_agent_run_is_cancelled" in agent_service_source and 'result, error = _execute_agent_step(run, step_name)' in agent_service_source, "Agent worker must re-check persisted cancellation before overwriting running state")
    require("bridge_groovy_endpoint" in agent_service_source and "http_client.get" in agent_service_source and "PORT," in agent_service_source, "Execution precheck must probe local bridge-groovy endpoint with runner token through the unified HTTP client")
    require('call["blockers"] = blockers' in agent_service_source and 'call["warnings"] = warnings' in agent_service_source, "Execution precheck must expose blockers and warnings to the frontend")
    require("def _fuzzy_match_cases" in agent_service_source and "词序容错模糊匹配" in agent_service_source, "Agent fallback matching must handle Chinese word-order variations")
    require('for marker in ("midscene-tasks", "server-tasks", "server-tasks-all")' in agent_service_source, "Agent must recover module/file from server-tasks paths before Sonic precheck")
    require("bridge_has_endpoint" in agent_service_source and "bridge_has_token" in agent_service_source and "body_ok" in agent_service_source, "Execution precheck must avoid false-negative bridge-groovy checks")
    require("TASK_DIR,\n        os.path.join(base_dir, 'midscene-tasks')" in agent_service_source, "Agent case retrieval must prefer formal TASK_DIR before draft server-tasks")
    require('call["keywords"] = keywords' in agent_service_source and "def _candidate_keyword_reasons" in agent_service_source and '"matchedKeywords"' in agent_service_source and "匹配关键词：" in agent_service_source, "Agent matching must expose actual matched keywords")
    require("DEFAULT_BUSINESS_FLOW" in prompt_builder_source and '"business_flow_required": True' in prompt_builder_source and '"business_flow_source"' in prompt_builder_source, "Prompt Center must provide required business-flow fallback metadata")
    require("FLOW_META_TERMS" in prompt_builder_source and "ip样子" in prompt_builder_source and "token" in prompt_builder_source, "Prompt business flow must filter product/model metric nodes")
    check_business_flow_filters_product_metrics()
    require("business_flow 是强约束" in agent_prompt_source and "不得生成、匹配或执行不在主链上的无关任务" in agent_prompt_source, "Agent prompt must treat business_flow as an execution boundary")
    require("business_flow 是强约束" in case_prompt_source and "禁止生成不属于业务主链" in case_prompt_source, "Case prompt must treat business_flow as a generation boundary")
    require('if isinstance(items, dict):' in sonic_service_source and 'explicit_items = items.get("items")' in sonic_service_source and '"total_files"' in sonic_service_source and '"synced_cases"' in sonic_service_source, "Sonic batch publish must accept frontend module/files/items payload and return clear totals")
    require('if not files and module:' in sonic_service_source and 'os.listdir(module_dir)' in sonic_service_source, "Sonic batch publish must default module-only payloads to all YAML files in that module")
    check_sonic_batch_payload_shapes()
    check_sonic_feishu_delivery_meta()
    require('task_k = f"{mod}::{_clean_filename(file)}"' in sonic_service_source and 'legacy_task_k = f"{mod}/{file}"' in sonic_service_source, "Sonic publish precheck must read task-meta by module::file key with legacy fallback")
    require("projects_payload = sonic_list_projects()" in router_source and 'projects_payload.get("projects")' in router_source, "Sonic diagnose route must handle sonic_list_projects dict payload after migration")
    require('"/api/sonic/run-case"' in router_source and '"deprecated": True' in router_source and '"/api/run-request"' in router_source, "Sonic single-case route must return a clear deprecation diagnostic instead of creating temp suites")
    require("'pageSize': page_size" in router_source and "'hasMore':" in router_source and "return_all = safe_bool" in router_source, "Cases list API must support paginated responses for long case lists")
    require("def sonic_run_single_case" in sonic_service_source and "RUNNER_JOB_ONLY" in sonic_service_source and "Sonic 单条临时测试套执行已下线" in sonic_service_source, "Sonic service must not create temporary suites for single-case debugging")
    require("sonic_force_run_suite(temp_suite_id)" not in sonic_service_source and "SONIC_TEMP_SUITE" not in sonic_service_source, "Code must not keep Sonic temporary-suite execution for single-case debugging")
    require("_cases, by_id, by_app_name = sonic_case_indexes(module, file)" in sonic_service_source and "def _sonic_name_aliases" in sonic_service_source and "name_rule" in sonic_service_source, "Sonic legacy scan must load Task case indexes and support tolerant name matching")
    official_sonic_notes = (ROOT / "docs" / "sonic-official-api-notes.md").read_text(encoding="utf-8")
    require("TestSuitesController.java" in official_sonic_notes and "TestCasesController.java" in official_sonic_notes and "/testSuites/runSuite" in official_sonic_notes and "不再提供 “Sonic 临时套执行”" in official_sonic_notes, "Sonic official API notes must preserve source-backed Runner-only single-case policy")
    live_smoke_source = (ROOT / "tests" / "live_api_smoke.py").read_text(encoding="utf-8")
    require("TASK_SMOKE_BASE_URL" in live_smoke_source and "/api/sonic/diagnose" in live_smoke_source and "/ai-gateway/ai/providers" in live_smoke_source, "Live API smoke script must cover auth, Sonic diagnose and AI Gateway")
    require("visual_image_assets = figma_images + uploaded_image_assets" in yaml_service_source, "Generation/mindmap visual grounding must not feed knowledge screenshots into the visual model")
    require("def _case_manual_block_reason" in yaml_service_source and "接口 Mock" in ai_skill_service_source and "排队/并发状态" in ai_skill_service_source, "YAML generation must keep non-runnable scenario coverage out of Runner YAML")
    require("需求文档是业务真相，Figma 是 UI 参考" in ai_skill_service_source and "需求文档决定本次要覆盖的业务范围" in automation_filter_source, "AI generation must treat requirements as business source of truth and Figma as UI reference")
    require(
        "def call_skill_smoke_selector" in ai_skill_service_source
        and "select_smoke_cases_for_payload" in yaml_service_source
        and "run_ai_skill(\"smoke_selector\"" not in ai_skill_service_source
        and "local_smoke_gate.v1" in ai_skill_service_source,
        "Smoke cases must be selected by local gating before Runner execution"
    )
    require('"direct_scope_only": True' in agent_service_source and "direct_scope_only" in knowledge_service_source and "useSavedKnowledge" in agent_service_source, "Agent Figma parsing must use exact direct-link scope and avoid saved page knowledge unless explicit")
    require('and not run.get("riskConfirmed")' in agent_service_source and 'next_pending_step_after("RISK_REVIEW")' in agent_service_source, "High-risk confirmation must not re-block after approval and must resume at the next pending step")
    require("MIDSCENE_REPLANNING_CYCLE_LIMIT\", 8" in config_source, "Default Midscene replanning limit should be high enough for normal complex UI flows")
    require("mindmap_visual_image_policy" in yaml_service_source, "Mindmap summary must document the visual image policy")
    require("MINDMAP_VISUAL_BATCH_SIZE" in config_source and "MINDMAP_VISUAL_TOTAL_BUDGET_SECONDS" in config_source and "visual_batches" in yaml_service_source, "Mindmap visual grounding must be batched with an overall time budget, not hard-truncated")
    require("refreshMindmapActiveTasks" in app_js_source and "{ refreshJobs: false }" in app_js_source, "Mindmap center must update active tasks without full-list refresh flicker")
    require("generation_mindmap_record_deleted_path" in yaml_service_source and '"/api/cases/mindmap-record"' in router_source, "Mindmap center must support deleting/hiding generation records")
    require('"mindmap_sort_ts": sort_ts' in yaml_service_source and "def _mindmap_time_value" in yaml_service_source and 'item.get("mindmap_sort_ts")' in yaml_service_source, "Mindmap center must sort by robust numeric latest-update timestamp")
    require("完整需求覆盖追踪矩阵" in yaml_service_source and "进入 YAML 的自动化用例" in yaml_service_source and "人工验证 / 待准备" in yaml_service_source, "Mindmap must preserve full requirement coverage beyond executable YAML cases")
    require('job.get("type") in ("generate", "mindmap_only")' in yaml_service_source and 'old_type not in ("generate", "mindmap_only")' in router_source and "run_mindmap_only_job if old_type == \"mindmap_only\"" in router_source, "Mindmap-only background jobs must be listed as retryable and retry through the mindmap worker")
    for runner_name in ("windows-midscene-runner.py", "mac-midscene-runner.py"):
        runner_source = (ROOT / runner_name).read_text(encoding="utf-8")
        require("def http_json_retry" in runner_source, f"{runner_name} must retry transient callback failures")
        require("Progress post failed" in runner_source, f"{runner_name} must print concrete progress callback failures instead of only HTTP 000")
        require("post_job_report_ready" in runner_source and "报告回传" in runner_source, f"{runner_name} must retry report-ready callback")
        require("结果回传" in runner_source and "轻量结果回传" in runner_source, f"{runner_name} must retry result callback and compact fallback")
        require("RUNNER_APP_PACKAGES" in runner_source and "detect_package_info" in runner_source and '"installed_apps"' in runner_source and '"adb_path"' in runner_source, f"{runner_name} must report device preflight and installed app versions")
    require('os.replace(tmp, target)' in source and 'os.fsync(f.fileno())' in source and '.bad' in source, "write_json_file must use atomic replace and .bad fallback")
    require("REPAIR_DRAFTS_FILE" in source and "repair-drafts.json" in source, "Backend must persist AI repair drafts")
    for fn in ("load_repair_drafts", "save_repair_drafts", "upsert_repair_draft", "repair_drafts_for_job", "normalize_job_record"):
        require(f"def {fn}" in source, f"Backend missing repair draft/job normalization function: {fn}")
    for route in ('"/api/repair-drafts"', '"/api/repair-drafts/apply"', '"/api/repair-drafts/reject"'):
        require(route in source, f"Backend missing repair draft route: {route}")
    require("confirmApply" in source and "confirmRisk" in source, "Repair draft apply must require explicit manual and risk confirmation")
    require('reason="before_repair_draft_apply"' in source and "save_file_version(module, file" in source, "Repair draft apply must backup YAML before writing")
    require("validate_midscene_yaml(fixed_yaml)" in source and "YAML 校验未通过，不能应用" in source, "Repair draft apply must block invalid YAML")
    require('"APPLIED"' in source and '"REJECTED"' in source and '"WAIT_CONFIRM"' in source, "Repair draft statuses must include applied/rejected/waiting")
    backend = load_backend()
    from task_server.services import knowledge_service as figma_backend
    require(backend.MAX_BODY_SIZE == 300 * 1024 * 1024, "Default JSON body limit must be 300MB for Agent source uploads")
    require(backend.MAX_UPLOAD_BODY_SIZE == 300 * 1024 * 1024, "Upload body limit must align with the 300MB Nginx limit")
    check_apk_chunk_upload_roundtrip()
    require(callable(backend.verify_session_token), "verify_session_token must be importable")
    require(backend.clean_filename(".yaml") == "task.yaml", "clean_filename must not keep empty .yaml names")
    require(backend.clean_filename("._bad.yaml") == "bad.yaml", "clean_filename must remove hidden macOS prefix")
    require(backend.clean_filename("  ") == "task.yaml", "clean_filename must use default for blank names")
    require(backend.clean_filename("需求/测试") == "需求_测试.yaml", "clean_filename must sanitize slash and add suffix")
    require(not backend.is_visible_yaml_filename(".yaml"), "hidden empty YAML file must not be visible")
    require(not backend.is_visible_yaml_filename("._case.yaml"), "macOS resource YAML file must not be visible")
    require(backend.is_visible_yaml_filename("case.yml"), "normal .yml file should be visible")
    require(backend.slug_for_file("   ...___---") == "测试用例", "slug_for_file must fallback for unreadable names")
    msg = backend.visual_reference_message(
        "正在校准脑图场景",
        figma_texts=[1, 2, 3, 4, 5, 6],
        figma_images=[1, 2, 3, 4, 5, 6],
        ignored_figma_pages=list(range(12)),
        knowledge_texts=[1, 2, 3, 4, 5, 6],
        knowledge_images=[1, 2, 3, 4],
        uploaded_image_assets=[],
    )
    require("本次用图：Figma 6 张 + 页面知识 4 张" in msg, "visual reference message must show actual image count clearly")
    require("未使用：未使用低匹配 Figma 12 页" in msg, "visual reference message must show skipped Figma pages")
    require("Figma 6 页/6 图" not in msg, "visual reference message must not mix pages/images in ambiguous format")
    mm_name = backend.generation_artifact_filename({"title": "耗材确认"}, "cs-demo", "测试用例.mm")
    require(mm_name.endswith(".mm"), "mindmap download filename must end with .mm")
    require(not mm_name.endswith(".mm.yaml"), "mindmap download filename must never become .mm.yaml")
    require(backend.generation_artifact_filename({"title": "耗材确认"}, "cs-demo", "summary.md").endswith(".md"), "markdown artifact must keep .md suffix")
    require(backend.FIGMA_PARSE_LIMIT >= 80, "Figma parse default must support large requirement canvases")
    require(backend.FIGMA_REFERENCE_LIMIT >= 36, "Figma reference default must cover 34-page requirement flows")
    require(backend.FIGMA_MAX_REFERENCE_LIMIT >= 72, "Figma max reference default must allow large flows")
    fuzzy_yamls = [
        {
            "abs_path": "/tmp/server-tasks/3D打印基线/打印记录查看.yaml",
            "rel_path": "server-tasks/3D打印基线/打印记录查看.yaml",
            "dir_name": "3D打印基线",
            "file_name": "打印记录查看.yaml",
            "task_name": "打印记录查看",
        },
        {
            "abs_path": "/tmp/server-tasks/3D打印基线/普通印章打印.yaml",
            "rel_path": "server-tasks/3D打印基线/普通印章打印.yaml",
            "dir_name": "3D打印基线",
            "file_name": "普通印章打印.yaml",
            "task_name": "普通印章打印",
        },
    ]
    from task_server.services import agent_service
    from task_server.services.yaml_service import cases_to_midscene_yaml, dry_run_midscene_yaml, ensure_midscene_platform_root, midscene_cli_dispatch_yaml_text, remove_empty_midscene_platform_roots, split_automation_ready_cases, validate_midscene_yaml_executability, yaml_with_single_task
    from task_server.services.yaml_static_validator import validate_yaml_static_executable
    empty_yaml = validate_midscene_yaml_executability("android:\n  tasks: []\n")
    require(not empty_yaml.get("ok") and "不能为空" in "；".join(empty_yaml.get("issues") or []), "Empty android.tasks must fail executable validation")
    valid_yaml = validate_midscene_yaml_executability("android:\n  tasks:\n    - name: demo\n      flow:\n        - aiTap: 首页搜索框\n")
    require(valid_yaml.get("ok") and valid_yaml.get("taskCount") == 1, "Valid android.tasks YAML must pass executable validation")
    duplicate_platform_yaml = "android: null\ntasks:\n  - name: demo\n    flow:\n      - aiTap: 首页搜索框\n"
    duplicate_platform_check = validate_midscene_yaml_executability(duplicate_platform_yaml)
    require(not duplicate_platform_check.get("ok") and "android: null" in "；".join(duplicate_platform_check.get("issues") or []), "Executable validation must reject android:null plus root tasks before Runner device injection")
    normalized_platform_yaml = remove_empty_midscene_platform_roots(duplicate_platform_yaml)
    require("android: null" not in normalized_platform_yaml and validate_midscene_yaml_executability(normalized_platform_yaml).get("ok"), "YAML normalization must remove empty platform roots before dispatch")
    runner_platform_yaml = ensure_midscene_platform_root(normalized_platform_yaml)
    require("\n  tasks:" in runner_platform_yaml and "\ntasks:" not in runner_platform_yaml and validate_midscene_yaml_executability(runner_platform_yaml).get("platform") == "android", "Runner dispatch normalization must wrap root tasks into android.tasks")
    generated_title, generated_yaml = cases_to_midscene_yaml({
        "title": "百度网盘入口校验",
        "cases": [{"title": "入口可见", "steps": ["确认首页加载完成"], "assertions": ["百度网盘入口可见"]}],
    }, app_package="com.xbxxhz.box")
    require(generated_title and "\n  tasks:" in generated_yaml and "\ntasks:" not in generated_yaml, "Generated Midscene YAML must use android.tasks for Runner dry-run")
    single_task_yaml = yaml_with_single_task(generated_yaml, "入口可见", app_package="com.xbxxhz.box")
    single_task_dispatch_yaml = midscene_cli_dispatch_yaml_text(single_task_yaml)
    single_task_device_dispatch_yaml = midscene_cli_dispatch_yaml_text(single_task_yaml, device_id="ecbfd645")
    require("android:\n  tasks:" in single_task_yaml and '- name: "入口可见"' in single_task_yaml and "# baseline.case_id" in single_task_yaml, "Single-task extraction must preserve existing saved android.tasks layout and comments")
    require(single_task_dispatch_yaml.startswith("android: {}\ntasks:\n- name: 入口可见"), "Runner dispatch YAML must keep official Midscene CLI interface config plus root tasks")
    import yaml as yaml_parser
    single_task_device_dispatch = yaml_parser.safe_load(single_task_device_dispatch_yaml)
    require(single_task_device_dispatch.get("android", {}).get("deviceId") == "ecbfd645", "Runner dispatch YAML must inject selected android.deviceId into temporary CLI YAML only")
    require(single_task_device_dispatch.get("agent", {}).get("screenshotShrinkFactor") == 2, "Android Runner dispatch must use Midscene's recommended mobile screenshot shrink factor for stable coordinate mapping")
    missing_input_value_yaml = "android:\n  tasks:\n    - name: demo\n      flow:\n        - aiInput: 当前页面输入框\n"
    missing_input_value = validate_midscene_yaml_executability(missing_input_value_yaml)
    require(not missing_input_value.get("ok") and "aiInput 必须包含 value" in "；".join(missing_input_value.get("issues") or []), "Executable validation must reject aiInput without value before Runner")
    missing_input_value_static = validate_yaml_static_executable(missing_input_value_yaml)
    require(not missing_input_value_static.get("ok") and "aiInput 必须包含 value" in "；".join(missing_input_value_static.get("errors") or []), "Static YAML validation must reject aiInput without value")
    ai_model_payload = {
        "title": "AI建模 UI测试",
        "module": "AI测试",
        "cases": [
            {
                "title": "「开始创作」三种入口点击跳转验证",
                "steps": ["在首页三维创作区点击文字输入入口"],
                "assertions": ["进入 AI建模页"],
            },
            {
                "title": "首页底部 AI建模入口可达",
                "steps": ["点击 AI建模入口"],
                "assertions": ["AI建模页面展示开始创作或图片建模入口"],
            },
        ],
    }
    split_payload = split_automation_ready_cases(ai_model_payload)
    require(len(split_payload.get("cases") or []) == 1 and len(split_payload.get("manual_cases") or []) == 1, "AI建模 hard gate must move old-entry cases to manual before Runner")
    require("底部中间 Tab「AI建模」" in "；".join(split_payload["cases"][0].get("steps") or []), "AI建模 eligible cases must get current app entry navigation before YAML conversion")
    old_ai_model_yaml = """android:
  tasks:
    - name: "开始创作三种入口点击跳转验证"
      flow:
        - aiTap: "首页三维创作区域的文字输入入口"
        - aiWaitFor: "大家都在做区域出现"
"""
    old_ai_model_dry = dry_run_midscene_yaml(old_ai_model_yaml, app_package="com.kfb.model")
    require(not old_ai_model_dry.get("ok") and "旧版" in "；".join(old_ai_model_dry.get("errors") or []), "YAML dry-run must block current-app old AI modeling entry before Runner")
    fuzzy_items, fuzzy_scored = agent_service._fuzzy_match_cases("回归一下查看打印记录基线测试用例", fuzzy_yamls, ["回归一下查看打印记录基线测试用例"])
    require(fuzzy_items and fuzzy_items[0]["file_name"] == "打印记录查看.yaml", "Agent fuzzy fallback must match 查看打印记录 to 打印记录查看.yaml")
    require(fuzzy_scored and fuzzy_scored[0][0] >= 55, "Agent fuzzy fallback score threshold must accept clear reordered Chinese matches")
    sample = {
        "title": "耗材确认",
        "scenarios": [{"feature": "打印", "scenario": f"场景{i}", "expected": "页面符合预期"} for i in range(3)],
        "cases": [{
            "case_id": f"CASE-{i:03d}",
            "title": f"用例{i}",
            "scenario": f"场景{i % 3}",
            "priority": "P1",
            "steps": ["步骤一", "步骤二", "步骤三"],
            "expected_result": "结果正确"
        } for i in range(18)],
        "manual_cases": [{"title": "人工确认", "reason": "需要真实设备", "suggested_setup": "准备测试设备"}],
        "report_checkpoints": ["检查点一", "检查点二"]
    }
    mindmap = backend.build_generation_mindmap(sample)
    require("测试步骤" in mindmap and "预期结果" in mindmap, "Default mindmap must expand complete case steps and expected results")
    compact_mindmap = backend.build_generation_mindmap({**sample, "mindmap_mode": "compact"})
    require("测试步骤" not in compact_mindmap, "Explicit compact mindmap must remain summary-only")
    require("自动化用例分级" in mindmap, "Full mindmap must keep priority grouping")
    root = {
        "id": "1:1",
        "type": "SECTION",
        "name": "AI建模",
        "_figma_direct_link": True,
        "children": [
            {
                "id": "1:2",
                "type": "FRAME",
                "name": "输入框1行",
                "absoluteBoundingBox": {"width": 375, "height": 812},
                "children": [{"type": "TEXT", "characters": "请输入描述"}],
            },
            {
                "id": "1:3",
                "type": "FRAME",
                "name": "语音输入-按住",
                "absoluteBoundingBox": {"width": 375, "height": 812},
                "children": [{"type": "TEXT", "characters": "按住说话"}],
            },
            {
                "id": "1:4",
                "type": "FRAME",
                "name": "引导5",
                "absoluteBoundingBox": {"width": 375, "height": 812},
                "children": [{"type": "TEXT", "characters": "AI建模引导 点击按钮"}],
            },
            {
                "id": "1:5",
                "type": "FRAME",
                "name": "语音输入-长按",
                "absoluteBoundingBox": {"width": 375, "height": 812},
                "children": [{"type": "TEXT", "characters": "长按语音 直接说给 AI 听"}],
            },
            {
                "id": "1:6",
                "type": "FRAME",
                "name": "隐藏旧版页面",
                "visible": False,
                "absoluteBoundingBox": {"width": 375, "height": 812},
                "children": [{"type": "TEXT", "characters": "旧版隐藏内容"}],
            },
        ],
    }
    frames = figma_backend.figma_frame_candidates(root, limit=10, mode="smart", min_width=240, min_height=360, pinned_node_ids={"1:1"})
    drafts = [figma_backend.figma_frame_to_draft("com.demo", "https://figma.example", "file", frame) for frame in frames]
    terms = figma_backend.figma_requirement_terms("点击语⾳创作弹窗，改交互，⻓按语⾳，AI建模⻚")
    require("语音" in terms and "长按" in terms and "建模" in terms, "Requirement terms must normalize PDF compatibility CJK text")
    selected, ignored = figma_backend.filter_figma_drafts_for_requirement(
        drafts,
        "AI建模 入口 模型生成 图片 语⾳ 输入 引导5 ⻓按语⾳",
        limit=1,
        min_score=5,
        max_limit=10,
        pinned_node_ids={"1:1"},
    )
    selected_names = {draft.get("page_name") for draft in selected}
    require(
        {"输入框1行", "语音输入-按住", "引导5", "语音输入-长按"}.issubset(selected_names),
        "Figma direct-node descendants must be kept as the requested design scope",
    )
    require("隐藏旧版页面" not in selected_names, "Hidden Figma frames must not be counted as UI images")
    require(all((draft.get("figma") or {}).get("direct_group") for draft in selected), "Direct-node descendants must carry direct_group metadata")
    require(all(figma_backend.figma_draft_generation_allowed(draft, min_score=999) for draft in selected), "Direct Figma pages must enter generation even when score threshold is high")
    generation_drafts, generation_ignored = figma_backend.split_generation_figma_drafts(selected, min_score=999)
    generation_names = {draft.get("page_name") for draft in generation_drafts}
    require({"引导5", "语音输入-长按"}.issubset(generation_names), "Direct Figma pages must survive generation filtering")
    require(not generation_ignored, "Direct Figma pages must not be moved to ignored generation list")
    frame_container = {
        "id": "9:1",
        "type": "FRAME",
        "name": "AI建模总画布",
        "_figma_direct_link": True,
        "absoluteBoundingBox": {"width": 1700, "height": 900},
        "children": [
            {
                "id": "9:2",
                "type": "FRAME",
                "name": "AI建模首页",
                "absoluteBoundingBox": {"width": 375, "height": 812},
                "children": [{"type": "TEXT", "characters": "开始创作"}],
            },
            {
                "id": "9:3",
                "type": "FRAME",
                "name": "语音输入-长按",
                "absoluteBoundingBox": {"width": 375, "height": 812},
                "children": [{"type": "TEXT", "characters": "长按语音"}],
            },
            {
                "id": "9:4",
                "type": "FRAME",
                "name": "图片建模-上传图片",
                "absoluteBoundingBox": {"width": 375, "height": 812},
                "children": [{"type": "TEXT", "characters": "上传图片"}],
            },
        ],
    }
    frame_container_pages = figma_backend.figma_frame_candidates(
        frame_container,
        limit=10,
        mode="smart",
        min_width=240,
        min_height=360,
        pinned_node_ids={"9:1"},
    )
    frame_container_names = {
        figma_backend.figma_page_name(frame, frame.get("_figma_canvas_name") or "")
        for frame in frame_container_pages
    }
    require(
        "AI建模总画布" not in frame_container_names
        and {"AI建模首页", "语音输入-长按", "图片建模-上传图片"}.issubset(frame_container_names),
        "Direct Figma frame containers must not be counted as UI images when child screen frames exist",
    )
    model_launch_root = {
        "id": "10:1",
        "type": "FRAME",
        "name": "模型上新",
        "_figma_direct_link": True,
        "absoluteBoundingBox": {"width": 980, "height": 720},
        "children": [
            {
                "id": f"10:{idx}",
                "type": "FRAME",
                "name": f"首页备份 {idx}",
                "absoluteBoundingBox": {"width": 375, "height": 812},
                "children": [
                    {"type": "TEXT", "characters": "发现 1 个新模型～ 立即查看"},
                    {
                        "id": f"10:{idx}:inner",
                        "type": "FRAME",
                        "name": "模型系列推荐",
                        "absoluteBoundingBox": {"width": 330, "height": 520},
                        "children": [
                            {"type": "TEXT", "characters": "热门模型"},
                            {"type": "TEXT", "characters": "最新模型"},
                            {"type": "TEXT", "characters": "可编辑模型"},
                        ],
                    },
                ],
            }
            for idx in (8, 18, 19, 15)
        ],
    }
    model_launch_terms = figma_backend.figma_requirement_terms("模型上新测试：下拉刷新后展示新模型提示，点击立即查看")
    require("上新" in model_launch_terms and "模型上新" in model_launch_terms and "下拉刷新" in model_launch_terms, "Model launch requirements must extract precise launch/refresh anchors")
    model_launch_pages = figma_backend.figma_frame_candidates(
        model_launch_root,
        limit=20,
        mode="smart",
        min_width=240,
        min_height=360,
        pinned_node_ids={"10:1"},
    )
    model_launch_names = {
        figma_backend.figma_page_name(frame, frame.get("_figma_canvas_name") or "")
        for frame in model_launch_pages
    }
    require(
        model_launch_names == {"首页备份 8", "首页备份 18", "首页备份 19", "首页备份 15"},
        "Direct model-launch Figma scope must keep phone screens and exclude nested content modules",
    )
    sibling_scope_root = {
        "id": "11:1",
        "type": "CANVAS",
        "name": "V1.6-AI建模+模型众测补偿+模型上新提示",
        "_figma_direct_link": True,
        "children": [
            {"id": "11:2", "type": "FRAME", "name": "Frame 1", "absoluteBoundingBox": {"x": -1304, "y": -142, "width": 3756, "height": 100}, "children": [{"type": "TEXT", "characters": "AI建模"}]},
            {"id": "11:3", "type": "FRAME", "name": "AI建模", "absoluteBoundingBox": {"x": -1304, "y": 58, "width": 375, "height": 812}, "children": [{"type": "TEXT", "characters": "开始创作"}]},
            {"id": "11:4", "type": "FRAME", "name": "Frame 5", "absoluteBoundingBox": {"x": -1304, "y": 6541, "width": 3756, "height": 100}, "children": [{"type": "TEXT", "characters": "模型上新"}]},
            *[
                {
                    "id": f"11:{idx}",
                    "type": "FRAME",
                    "name": f"首页备份 {name}",
                    "absoluteBoundingBox": {"x": x, "y": 6741, "width": 375, "height": 812},
                    "children": [{"type": "TEXT", "characters": "三维创作 模型系列推荐 立即查看"}],
                }
                for idx, name, x in ((8, 8, -1304), (18, 18, -829), (19, 19, -354), (15, 15, 121))
            ],
            {"id": "11:20", "type": "FRAME", "name": "成长报告", "absoluteBoundingBox": {"x": -1304, "y": 3013, "width": 375, "height": 812}, "children": [{"type": "TEXT", "characters": "成长报告"}]},
        ],
    }
    sibling_scope = figma_backend.figma_requirement_sibling_scope_root(
        sibling_scope_root,
        "模型上新测试：首页下拉刷新，展示模型上新动效、新模型提示和立即查看入口",
    )
    require(sibling_scope and sibling_scope.get("id") == "11:4", "Figma title-bar scopes must be narrowed by explicit requirement subject")
    sibling_pages = figma_backend.figma_frame_candidates(
        sibling_scope,
        limit=20,
        mode="smart",
        min_width=240,
        min_height=360,
        pinned_node_ids={"11:4"},
    )
    sibling_names = {
        figma_backend.figma_page_name(frame, frame.get("_figma_canvas_name") or "")
        for frame in sibling_pages
    }
    require(
        sibling_names == {"首页备份 8", "首页备份 18", "首页备份 19", "首页备份 15"},
        "Figma title-bar sibling scope must keep only the phone screens visually under that title",
    )
    require(not figma_backend.figma_direct_node_needs_parent_lookup({"type": "CANVAS", "children": [{}]}), "Figma canvas links must not force expensive parent lookup")
    require(figma_backend.figma_direct_node_needs_parent_lookup({"type": "TEXT", "characters": "AI建模"}), "Figma title/text links must lookup parent design scope")
    require(
        figma_backend.figma_direct_node_needs_parent_lookup({
            "type": "FRAME",
            "name": "Frame 1",
            "absoluteBoundingBox": {"width": 3756, "height": 100},
            "children": [{"type": "TEXT", "characters": "AI建模"}],
        }),
        "Direct Figma links to wide title-bar frames must lookup parent scope instead of importing one title image",
    )
    ai_title_scope_root = {
        "id": "12:1",
        "type": "CANVAS",
        "name": "V1.6-AI建模",
        "_figma_direct_link": True,
        "children": [
            {
                "id": "12:2",
                "type": "FRAME",
                "name": "Frame 1",
                "absoluteBoundingBox": {"x": -1304, "y": -142, "width": 3756, "height": 100},
                "children": [{"type": "TEXT", "characters": "AI建模"}],
            },
            *[
                {
                    "id": f"12:{100 + idx}",
                    "type": "FRAME",
                    "name": "AI" if idx % 4 else "语音输入",
                    "absoluteBoundingBox": {
                        "x": -1304 + ((idx - 1) % 12) * 475,
                        "y": 58 + ((idx - 1) // 12) * 980,
                        "width": 375,
                        "height": 812,
                    },
                    "children": [{"type": "TEXT", "characters": "开始创作 图片建模 语音输入"}],
                }
                for idx in range(1, 37)
            ],
            {
                "id": "12:999",
                "type": "FRAME",
                "name": "成长报告",
                "absoluteBoundingBox": {"x": -1304, "y": 6000, "width": 3756, "height": 100},
                "children": [{"type": "TEXT", "characters": "成长报告"}],
            },
            {
                "id": "12:1000",
                "type": "FRAME",
                "name": "成长报告页面 01",
                "absoluteBoundingBox": {"x": -1304, "y": 6160, "width": 375, "height": 812},
                "children": [{"type": "TEXT", "characters": "成长报告 反馈有奖"}],
            },
        ],
    }
    ai_scope = figma_backend.figma_requirement_sibling_scope_root(
        ai_title_scope_root,
        "进入 AI建模页 生成模型并查看结果",
    )
    require(ai_scope and ai_scope.get("id") == "12:2", "AI modeling title-bar scope must resolve to the direct title frame")
    require(len(ai_scope.get("children") or []) == 36, "AI modeling title-bar scope must stop at the next title bar and keep exactly 36 phone screens")
    ai_pages = figma_backend.figma_frame_candidates(
        ai_scope,
        limit=40,
        mode="smart",
        min_width=240,
        min_height=360,
        pinned_node_ids={"12:2"},
    )
    require(len(ai_pages) == 36, "AI modeling title-bar scope must keep all 36 phone UI images, not only the title frame")
    _texts, duplicate_name_images, duplicate_name_pages = figma_backend.figma_drafts_to_generation_assets([
        {
            "app_package": "com.kfb.model",
            "page_id": "12:101",
            "page_name": "AI",
            "route": "Figma 设计稿：AI",
            "screenshot": {"name": "figma-AI-手机.png", "contentBase64": base64.b64encode(b"image-a").decode("ascii")},
            "figma": {"node_id": "12:101", "direct_group": True, "pinned": False, "relevance_score": 5},
        },
        {
            "app_package": "com.kfb.model",
            "page_id": "12:102",
            "page_name": "AI",
            "route": "Figma 设计稿：AI",
            "screenshot": {"name": "figma-AI-手机.png", "contentBase64": base64.b64encode(b"image-b").decode("ascii")},
            "figma": {"node_id": "12:102", "direct_group": True, "pinned": False, "relevance_score": 5},
        },
    ], limit_images=10)
    require(len(duplicate_name_images) == 2 and len({item["name"] for item in duplicate_name_images}) == 2, "Figma image assets must keep same-name direct-scope variants by node id")
    require(len(duplicate_name_pages) == 2 and all(page.get("screenshot") for page in duplicate_name_pages), "Figma used pages must keep screenshots for same-name direct-scope variants")
    job_id = "static_duration_check"
    old_generate_dir = backend.GENERATE_JOB_DIR
    with tempfile.TemporaryDirectory() as temp_dir:
        backend.GENERATE_JOB_DIR = temp_dir
        backend.save_generate_job({"job_id": job_id, "status": "pending", "created_at": "2026-06-05 10:00:00"})
        backend.update_generate_job(job_id, status="running", started_at="2026-06-05 10:00:05")
        done = backend.update_generate_job(job_id, status="success", finished_at="2026-06-05 10:02:35")
    require(done.get("elapsed_seconds") == 150, "Background generation jobs must persist elapsed_seconds")
    with tempfile.TemporaryDirectory() as temp_dir:
        backend.GENERATE_JOB_DIR = temp_dir
        stale = {
            "job_id": "static_stale_generation",
            "status": "running",
            "type": "mindmap_only",
            "step": "生成用例结构",
            "created_at": "2026-06-05 10:00:00",
            "started_at": "2026-06-05 10:00:00",
        }
        from task_server.services import yaml_service
        expired = yaml_service.expire_generate_job_if_stale(stale, persist=False)
    expired_text = f"{expired.get('message', '')} {expired.get('error', '')}"
    require(expired.get("status") == "timeout", "Stale generation jobs must be marked timeout")
    require("容量" not in expired_text and "capacity" not in expired_text.lower(), "Stale generation timeout must not blame model capacity without a real capacity error")
    backend.GENERATE_JOB_DIR = old_generate_dir
    nginx = NGINX_CONF.read_text(encoding="utf-8")
    require("location /ai-gateway/" in nginx, "Nginx template must proxy /ai-gateway/")
    require("proxy_pass http://127.0.0.1:8090/" in nginx, "Nginx /ai-gateway/ must point to local AI Gateway")
    require("proxy_read_timeout 300s" in nginx, "AI Gateway proxy must allow long model reads")
    runner_sources = "\n".join([
        (ROOT / "windows-midscene-runner.py").read_text(encoding="utf-8"),
        (ROOT / "mac-midscene-runner.py").read_text(encoding="utf-8"),
    ])
    require("RUNNER_CAPABILITIES" in runner_sources and '"yaml_dry_run": True' in runner_sources, "Both runners must advertise yaml_dry_run capability")
    require("def run_yaml_dry_run_job" in runner_sources and "YAML dry-run 不生成 HTML 报告" in runner_sources, "Both runners must support local YAML dry-run jobs without Midscene execution")
    require("def midscene_cli_yaml_text" in runner_sources and "def ensure_cli_interface_config" in runner_sources and '缺少 Midscene CLI 接口配置 android/web/ios/computer/interface' in runner_sources and '缺少 Midscene CLI 可加载的顶层 tasks' in runner_sources, "Runner dry-run and real execution must normalize platform-root YAML to Midscene CLI root tasks with interface config")
    require("env=midscene_env(device_id)" in runner_sources and '"ANDROID_SERIAL"' in runner_sources, "Runner must pass the selected device through env")
    require('line.strip() == "android: {}"' in runner_sources and 'lines[i] = "android:"' in runner_sources and 'lines.insert(i + 1, f"  deviceId: {device_id}")' in runner_sources, "Runner device injection must expand android: {} before adding deviceId to keep CLI YAML valid")
    require("def normalize_empty_cli_interface_config" in runner_sources and "text = normalize_empty_cli_interface_config(text)" in runner_sources, "Runner CLI normalization must preserve non-empty interface blocks")
    require('midscene_command.extend(["--android.deviceId", device_id])' in runner_sources, "Runner must apply the selected Android device through the official Midscene CLI override")
    require('"2026.07.10-model-family-v4"' in runner_sources, "Runner heartbeat must expose the explicit model-family fix version for deployment verification")
    require('"MIDSCENE_MODEL_FAMILY"' in runner_service_source and '"MIDSCENE_MODEL_API_KEY"' in runner_service_source and '"MIDSCENE_MODEL_BASE_URL"' in runner_service_source, "Server must publish the modern Midscene model configuration contract")
    require('"MIDSCENE_USE_QWEN_VL": "1"' not in runner_service_source, "Server must not declare a Qwen3 model through the legacy qwen2.5-vl switch")
    require("def infer_midscene_model_family" in runner_sources and '"midscene_model_family"' in runner_sources and 'env.pop(legacy_key, None)' in runner_sources, "Runners must infer, report, and enforce the explicit Midscene model family")
    require("def ensure_android_sdk_env" in runner_sources and '"ANDROID_SDK_ROOT"' in runner_sources and '"ANDROID_HOME"' in runner_sources and '"platform-tools"' in runner_sources, "Runner must infer Android SDK env from adb path for Midscene CLI")
    router_source = (ROOT / "task_server" / "router.py").read_text(encoding="utf-8")
    require("register_runner(d)" in router_source and '"capabilities": record.get("capabilities")' in router_source, "Runner heartbeat route must preserve reported capabilities")
    require('"job_type": selected.get("job_type")' in router_source and 'selected_is_yaml_dry_run' in router_source, "Runner job dispatch must pass job_type and exclude yaml_dry_run from task meta")
    require('midscene_cli_dispatch_yaml_text(yaml_content, device_id=selected.get("device_id", ""))' in router_source, "Runner job dispatch must convert YAML to official Midscene CLI layout and inject selected device without changing saved scripts")
    agent_source = (ROOT / "task_server" / "services" / "agent_service.py").read_text(encoding="utf-8")
    require("selected_device_label" in agent_source and '"display_name", "brand", "model"' in agent_source, "Agent precheck must show physical device label/model for selected Runner device")
    require("_runner_supports_yaml_dry_run" in agent_source and '"runner_yaml_dry_run"' in agent_source, "Agent must use real Runner YAML dry-run when runner capability is available")
    require('"neither android_home nor android_sdk_root" in lowered' in agent_source and 'failure_type != "ENV_ISSUE"' in agent_source, "Agent must classify Android SDK/ADB environment failures as ENV_ISSUE and prevent AI from downgrading them to SCRIPT_ISSUE")
    require('POST_FAILURE_ANALYSIS_STEPS = ("RUN_SONIC",)' in agent_source, "RUN_SONIC failure must continue into report collection, failure analysis and repair planning")
    yaml_source = (ROOT / "task_server" / "services" / "yaml_service.py").read_text(encoding="utf-8")
    require('agent_config.setdefault("screenshotShrinkFactor", 2)' in yaml_source, "Android Runner temporary YAML must pre-shrink mobile screenshots for stable Midscene coordinate mapping")
    require("quality_eval" in yaml_source and "evaluate_baseline_template_matching" in yaml_source, "YAML generation review must include template matcher quality eval")
    env_example = ENV_EXAMPLE.read_text(encoding="utf-8")
    require("TASK_APP_ENV='prod'" in env_example and "TASK_ALLOW_QUERY_TOKEN='0'" in env_example, "Env example must document production mode and disabled query token auth")
    for module_path in ["task_server/config.py", "task_server/auth.py", "task_server/storage.py", "task_server/repair_service.py", "task_server/sonic_service.py"]:
        require((ROOT / module_path).exists(), f"Backend service skeleton missing: {module_path}")
    storage_source = (ROOT / "task_server" / "storage.py").read_text(encoding="utf-8")
    require("write_json_atomic" in storage_source and "os.replace(tmp, target)" in storage_source, "Storage skeleton must provide atomic JSON writes")
    print({"ok": True, "file": str(MODULE), "checks": 61})


if __name__ == "__main__":
    main()
