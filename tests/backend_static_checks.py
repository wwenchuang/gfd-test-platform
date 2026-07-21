#!/usr/bin/env python3
import importlib.util
import base64
import copy
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


def check_ai_gateway_response_diagnostics():
    from task_server.services import ai_skill_service

    parsed = ai_skill_service._decode_ai_gateway_json_response(
        b'{"success":true,"content":"ok"}',
        status=200,
        content_type="application/json",
    )
    require(parsed.get("content") == "ok", "Gateway response decoder must preserve valid JSON")

    cases = [
        (b"", "AI Gateway 返回空响应"),
        (b"<html>gateway timeout</html>", "AI Gateway 返回非 JSON 响应"),
        (b"[]", "AI Gateway 返回了非对象 JSON"),
    ]
    for raw, expected in cases:
        rejected = False
        try:
            ai_skill_service._decode_ai_gateway_json_response(
                raw,
                status=504,
                content_type="text/html",
            )
        except RuntimeError as exc:
            rejected = expected in str(exc) and "status=504" in str(exc)
        require(rejected, f"Gateway response decoder must diagnose {expected}")

    class EmptySkillResponse:
        status = 200
        headers = {"Content-Type": "application/json"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps({
                "success": True,
                "content": "",
                "providerId": "highway_gpt5_mini",
                "model": "gpt-5-mini",
                "fallbackUsed": False,
                "finishReason": "length",
                "usage": {"completionTokens": 4096, "reasoningTokens": 4096},
            }).encode("utf-8")

    original_urlopen = ai_skill_service.urllib.request.urlopen
    trace = {}
    try:
        ai_skill_service.urllib.request.urlopen = lambda *_args, **_kwargs: EmptySkillResponse()
        rejected = False
        try:
            ai_skill_service.ai_gateway_skill_content(
                "requirement_analyzer",
                "Return JSON.",
                model_config={"providerId": "highway_gpt5_mini", "model": "gpt-5-mini"},
                runtime_trace=trace,
            )
        except RuntimeError as exc:
            rejected = (
                "AI Gateway skill 返回空内容" in str(exc)
                and "finish_reason=length" in str(exc)
                and "reasoning_tokens=4096" in str(exc)
            )
    finally:
        ai_skill_service.urllib.request.urlopen = original_urlopen
    require(rejected, "Task AI Skill client must reject a success wrapper with empty model content")
    require(
        trace.get("finishReason") == "length"
        and trace.get("usage", {}).get("reasoningTokens") == 4096,
        "Task AI Skill trace must retain finish and token evidence before rejecting empty content",
    )


def check_automation_filter_invalid_json_self_repair():
    from task_server.services import ai_skill_service, yaml_service

    model_config = {"providerId": "qwen_plus", "model": "qwen3.6-plus"}
    malformed = '{"cases":[{"case_id":"TC-001" "title":"入口可见"}],"manual_cases":[],"review":{}}'
    repaired = json.dumps({
        "cases": [{"case_id": "TC-001", "title": "入口可见"}],
        "manual_cases": [],
        "review": {},
    }, ensure_ascii=False)
    original_gateway = ai_skill_service.ai_gateway_skill_content
    calls = []
    responses = [malformed, repaired]

    def fake_gateway(skill_name, prompt, **kwargs):
        calls.append({
            "skill": skill_name,
            "prompt": prompt,
            "modelConfig": dict(kwargs.get("model_config") or {}),
            "maxTokens": kwargs.get("max_tokens"),
        })
        runtime_trace = kwargs.get("runtime_trace")
        if isinstance(runtime_trace, dict):
            runtime_trace.update({
                "providerId": "qwen_plus",
                "model": "qwen3.6-plus",
                "fallbackUsed": False,
                "finishReason": "stop",
                "usage": {"totalTokens": 128},
                "source": "ai_gateway",
            })
        return responses.pop(0)

    success_trace = {}
    try:
        ai_skill_service.ai_gateway_skill_content = fake_gateway
        result = ai_skill_service.run_ai_skill(
            "automation_filter",
            {"title": "入口可见"},
            model_config=model_config,
            runtime_trace=success_trace,
            repair_invalid_json=True,
        )
    finally:
        ai_skill_service.ai_gateway_skill_content = original_gateway
    require(result.get("cases", [{}])[0].get("title") == "入口可见", "Malformed skill JSON must be repaired without losing business values")
    require(len(calls) == 2 and all(item.get("modelConfig") == model_config for item in calls), "JSON syntax repair must make one bounded call with the selected model config")
    require("只修复下方模型输出中的 JSON 语法错误" in calls[1].get("prompt", ""), "JSON repair must be syntax-only instead of regenerating the business plan")
    require(
        success_trace.get("jsonRepairAttempted") is True
        and success_trace.get("jsonRepairSucceeded") is True
        and success_trace.get("jsonRepair", {}).get("sameSelectedModel") is True,
        "Successful JSON repair must remain observable in the AI model trace",
    )

    responses = [malformed, malformed]
    failed_trace = {}
    try:
        ai_skill_service.ai_gateway_skill_content = fake_gateway
        failed = ai_skill_service.call_skill_automation_filter(
            "通用入口展示",
            "AI测试",
            {"requirement_points": ["REQ-001 通用入口展示"]},
            [{
                "feature": "通用入口",
                "scenario": "入口展示",
                "requirement_point": "REQ-001 通用入口展示",
                "steps": ["进入首页", "查看通用入口"],
                "assertions": ["通用入口可见"],
            }],
            model_config=model_config,
            runtime_trace=failed_trace,
        )
    finally:
        ai_skill_service.ai_gateway_skill_content = original_gateway
    invalid_case = failed.get("cases", [{}])[0]
    require(
        invalid_case.get("source") == "local_fallback_after_ai_invalid_json"
        and invalid_case.get("executionLevel") == "needs_review"
        and failed.get("review", {}).get("fallback_failure_type") == "invalid_json",
        "A failed JSON repair must use an accurate review-only provenance instead of pretending to be a timeout",
    )
    require(
        all(item.get("maxTokens") == ai_skill_service.AI_AUTOMATION_FILTER_MAX_TOKENS for item in calls[-2:]),
        "The verbose automation filter and its bounded syntax repair must receive the expanded structured-output budget",
    )
    invalid_floor = yaml_service.enforce_generated_fallback_execution_floor(failed)
    require(
        invalid_floor.get("cases", [{}])[0].get("source") == "local_fallback_after_ai_invalid_json"
        and yaml_service.generated_yaml_effective_level(
            "executable",
            invalid_floor.get("cases", [{}])[0],
            {"ok": True},
        ) == "needs_review",
        "Static scoring must not promote a malformed-JSON local fallback to executable",
    )

    original_run_ai_skill = ai_skill_service.run_ai_skill
    try:
        ai_skill_service.run_ai_skill = lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("model timeout"))
        timed_out = ai_skill_service.call_skill_automation_filter(
            "通用入口展示",
            "AI测试",
            {"requirement_points": ["REQ-001 通用入口展示"]},
            [{"feature": "通用入口", "scenario": "入口展示"}],
            model_config=model_config,
        )
    finally:
        ai_skill_service.run_ai_skill = original_run_ai_skill
    require(
        timed_out.get("cases", [{}])[0].get("source") == "local_fallback_after_ai_timeout"
        and timed_out.get("review", {}).get("fallback_failure_type") == "timeout",
        "A real model timeout must remain separately classified and review-only",
    )


def check_report_image_context_uses_midscene_execution_refs():
    from task_server.services import report_service

    fake_bundle = base64.b64encode(b"bundle-demo-screen" * 100).decode("ascii")
    payloads = {
        "real-a": b"real-execution-a" * 100,
        "real-b": b"real-execution-b" * 100,
        "real-c": b"real-execution-c" * 100,
    }
    encoded = {key: base64.b64encode(value).decode("ascii") for key, value in payloads.items()}
    dump = {
        "executions": [{
            "tasks": [{
                "snapshots": [
                    {"type": "midscene_screenshot_ref", "id": "real-a"},
                    {"type": "midscene_screenshot_ref", "id": "real-c"},
                    {"type": "midscene_screenshot_ref", "id": "real-b"},
                ],
            }],
        }],
    }
    old_candidates = report_service.report_html_candidates_for_job
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            report_path = Path(temp_dir) / "execution.html"
            report_path.write_text(
                "<html><body>"
                f"<script>window.demo='data:image/png;base64,{fake_bundle}'</script>"
                f"<script data-id=\"real-a\" type=\"midscene-image\">data:image/jpeg;base64,{encoded['real-a']}</script>"
                f"<script type=\"midscene-image\" data-id=\"real-b\">data:image/jpeg;base64,{encoded['real-b']}</script>"
                f"<script type=\"midscene-image\" data-id=\"real-c\">data:image/jpeg;base64,{encoded['real-c']}</script>"
                f"<script type=\"midscene_web_dump\">{json.dumps(dump)}</script>"
                "</body></html>",
                encoding="utf-8",
            )
            report_service.report_html_candidates_for_job = lambda _job: [report_path]
            images = report_service.report_image_context({"job_id": "static-report"}, limit=2)
    finally:
        report_service.report_html_candidates_for_job = old_candidates
    require(
        [item.get("name", "").split("-midscene-")[-1].split(".")[0] for item in images]
        == ["real-c", "real-b"]
        and [base64.b64decode(item.get("base64") or "") for item in images]
        == [payloads["real-c"], payloads["real-b"]]
        and all(base64.b64decode(item.get("base64") or "") != base64.b64decode(fake_bundle) for item in images),
        "Failure evidence must follow Midscene execution screenshot refs and exclude bundled demo assets",
    )


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
    old_keyframes = agent_service._agent_failure_report_keyframes
    old_baselines = agent_service._agent_repair_baseline_examples
    try:
        agent_service._agent_failure_report_keyframes = lambda _job, limit=4: [{
            "name": "failed-report-frame.png",
            "mime": "image/png",
            "base64": "a" * 1200,
        }]
        agent_service._agent_repair_baseline_examples = lambda *_args, **_kwargs: [{
            "id": "base-photo-sibling",
            "provenancePath": "server-tasks-all/基础打印/相邻照片规格.yaml",
            "businessPath": "照片打印 -> 照片打印 -> 相邻规格 -> 相册导入",
        }]
        with tempfile.TemporaryDirectory() as temp_dir:
            agent_service.TASK_DIR = temp_dir
            module_dir = Path(temp_dir) / "AI_Agent_草稿"
            module_dir.mkdir()
            (module_dir / "case.yaml").write_text("android:\n  tasks: []\n", encoding="utf-8")
            payload = agent_service._agent_failure_ai_payload(
                {
                    "target": "基础打印新增百度网盘入口",
                    "modelProviderId": "highway_gpt5_mini",
                    "aiModel": "gpt-5-mini",
                    "runnerId": "win-runner-01",
                    "deviceId": "ecbfd645",
                    "deviceStrategy": "fixed",
                    "artifacts": {
                        "sourceContext": {
                            "requirementText": "基础打印三个业务入口需要覆盖展示、同级关系、文案和可达页面",
                            "figmaText": "[Figma设计稿页面]\n状态/变体：5寸照片\n可见文案：相册导入、百度网盘",
                            "figmaUsedPages": [{"page_name": "内部备份名", "node_id": "1:70"}],
                            "figmaImageCount": 4,
                        },
                    },
                },
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
        require(payload.get("reportKeyframes") == ["failed-report-frame.png"] and len(payload.get("imageAssets") or []) == 1, "Agent failure analysis must attach bounded Midscene report keyframes")
        require(payload.get("baselineExamples", [{}])[0].get("id") == "base-photo-sibling", "Agent failure analysis must align report keyframes with trustworthy sibling-branch baselines")
        require(payload.get("executionConstraint", {}).get("allowOtherDevices") is False and payload["executionConstraint"].get("deviceId") == "ecbfd645", "Agent failure AI must retain the fixed-device constraint and forbid a second phone")
        require(
            payload.get("modelConfig") == {"providerId": "highway_gpt5_mini", "model": "gpt-5-mini"}
            and payload.get("fallbackModelConfig", {}).get("model") == agent_service.dashscope_vl_model(),
            "Failure analysis with report frames must try the Agent-selected model first and declare its visual fallback explicitly",
        )
        require(payload.get("sourceEvidence", {}).get("figmaPageCount") == 1 and "5寸照片" in payload["sourceEvidence"].get("figmaText", ""), "Agent failure AI must receive bounded Figma same-frame evidence without reparsing it")
        require("基础打印三个业务入口" in payload.get("requirement", "") and payload.get("target") == "基础打印新增百度网盘入口", "Agent failure AI must receive the original target and requirement")
    finally:
        agent_service.TASK_DIR = old_task_dir
        agent_service._agent_failure_report_keyframes = old_keyframes
        agent_service._agent_repair_baseline_examples = old_baselines


def check_agent_ai_owned_plan_and_evidence_loop():
    from task_server.services import agent_service, ai_skill_service, yaml_baseline_cache, yaml_service
    from task_server.services.yaml_execution_plan import classify_generated_yaml_failure_bucket

    preview = agent_service.preview_agent_plan({
        "target": "基础打印新增百度网盘入口",
        "requirementText": "基础打印入口在首页：文档打印、照片打印、扫描复印。覆盖展示、同级关系、文案和可达页面。",
        "scope": "regression",
    })
    require(not preview.get("businessFlows") and not preview.get("steps"), "Agent startup preview must not expose rule candidates as an AI business plan")
    branches = [item.get("branch") for item in preview.get("requirementCandidates") or []]
    require(branches == ["文档打印", "照片打印", "扫描复印"], "Agent startup preview must retain explicit requirement candidates for later AI coverage auditing")
    generic_preview = agent_service.preview_agent_plan({
        "target": "会员服务新增发票入口",
        "requirementText": "会员服务入口在首页：订单管理、优惠券。发票入口是新增能力，需要校验展示和文案。",
        "scope": "regression",
    })
    require(
        [item.get("branch") for item in generic_preview.get("requirementCandidates") or []] == ["订单管理", "优惠券"],
        "Agent requirement preview must extract arbitrary sibling entry lists without product-specific branch names",
    )
    require(
        not any(item.get("steps") or item.get("checks") for item in generic_preview.get("requirementCandidates") or []),
        "Agent startup preview must not present deterministic steps or checks before MM AI planning",
    )
    generic_contract_run = {
        "target": "会员服务新增发票入口",
        "normalizedInput": {
            "requirementText": "会员服务入口在首页：订单管理、优惠券。发票入口是新增能力，需要校验展示、同级关系、文案和可达页面。",
        },
        "artifacts": {},
    }
    generic_contract = agent_service._ensure_business_flow_constraint(generic_contract_run)
    generic_points = ai_skill_service.source_requirement_contract_points(generic_contract)
    require(
        len(generic_points) == 2
        and all(term in " ".join(generic_points) for term in ("订单管理", "优惠券", "发票入口", "同级", "文案", "稳定可达")),
        "Source requirement contracts must generalize to arbitrary entry labels and sibling branches without product-specific hardcoding",
    )
    generic_analysis = ai_skill_service.apply_source_requirement_contract({
        "business_goals": ["验收发票入口"],
        "requirement_points": ["AI 候选需求点"],
    }, generic_contract)
    generic_acceptance_checks = generic_analysis.get("requirement_acceptance_checks") or []
    require(
        len(generic_acceptance_checks) == 8
        and {item.get("kind") for item in generic_acceptance_checks} == {"visibility", "relation", "copy", "reachability"},
        "Each source branch must retain independently auditable visibility, relation, copy and reachability dimensions",
    )
    generic_display_cases = []
    generic_reachability_manual = []
    for index, branch in enumerate(("订单管理", "优惠券"), start=1):
        requirement_id = f"REQ-{index:03d}"
        generic_display_cases.append({
            "case_id": f"TC-G{index:02d}",
            "title": f"{branch}发票入口展示验收",
            "executionLevel": "executable",
            "requirementRefs": [requirement_id],
            "steps": [f"进入{branch}", "等待发票入口可见"],
            "assertions": ["发票入口可见，和当前页面入口同级，显示文案为发票"],
        })
        generic_reachability_manual.append({
            "case_id": f"MC-G{index:02d}",
            "title": f"{branch}发票入口首个落地页",
            "executionLevel": "manual",
            "requirementRefs": [requirement_id],
            "steps": [
                f"进入{branch}",
                "点击发票入口",
                "等待授权页、登录页或内容列表任一合法页面可见",
                f"断言页面已离开{branch}并且无长时间白屏或崩溃",
            ],
            "assertions": ["授权页、登录页或内容列表任一合法页面可见，且无白屏或崩溃"],
        })
    generic_display_payload = {
        "analysis": generic_analysis,
        "cases": generic_display_cases,
        "manual_cases": generic_reachability_manual,
    }
    generic_display_audit = ai_skill_service.executable_yaml_portfolio_audit(
        generic_display_payload,
        {"min_automation_cases": 2},
    )
    require(
        not generic_display_audit.get("ok")
        and generic_display_audit.get("coveredAcceptanceCheckCount") == 6
        and generic_display_audit.get("missingAcceptanceCheckCount") == 2
        and all(item.get("kind") == "reachability" for item in generic_display_audit.get("missingAcceptanceChecks") or []),
        "Requirement refs and display assertions must not falsely satisfy an unexecuted click-to-destination acceptance check",
    )
    shared_tail_payload = json.loads(json.dumps(generic_display_payload, ensure_ascii=False))
    shared_tail_baselines = []
    for index, case in enumerate(shared_tail_payload.get("cases") or [], start=1):
        branch = ("订单管理", "优惠券")[index - 1]
        baseline_id = f"base-branch-{index}"
        case["ai_case_plan"] = {
            "baselineId": baseline_id,
            "baselineGrounded": True,
            "pathPlanApplied": True,
            "precondition": "App 首页",
            "flow": list(case.get("steps") or []),
            "assertionTarget": (case.get("assertions") or [""])[0],
        }
        shared_tail_baselines.append({
            "id": baseline_id,
            "title": f"{branch}成功导航",
            "sourceKind": "verified_execution",
            "verificationStatus": "execution_success",
            "aiSelectedBranchName": branch,
            "snippet": f'- aiTap: "{branch}"\n- aiWaitFor: "{branch}页面加载完成"\n- aiTap: "本地导入"',
        })
    shared_tail_case = {
        "case_id": "TC-SHARED-TAIL",
        "title": "任一业务页发票入口首屏校验",
        "executionLevel": "needs_review",
        "originExecutionLevel": "automatic",
        "requirementRefs": ["REQ-001", "REQ-002"],
        "steps": [
            "进入任一业务页面（订单管理或优惠券）",
            "等待发票入口可见",
            "点击发票入口",
            "等待授权页、登录页或内容列表任一合法页面可见",
        ],
        "assertions": ["授权页、登录页或内容列表任一合法页面可见，且无白屏或崩溃"],
    }
    shared_tail_payload["cases"].append(shared_tail_case)
    shared_tail_audit = ai_skill_service.executable_yaml_portfolio_audit(
        shared_tail_payload,
        {"min_automation_cases": 2},
    )
    shared_tail_records = [
        {
            "raw": case,
            "compact": ai_skill_service._compact_case_for_plan(case, index, origin_level="automatic"),
        }
        for index, case in enumerate(shared_tail_payload.get("cases") or [])
    ]
    shared_tail_evidence = ai_skill_service._bounded_convergence_evidence(
        shared_tail_payload,
        shared_tail_records,
        shared_tail_audit,
        selected_baselines=shared_tail_baselines,
        manual_records=[],
    )
    require(
        set(shared_tail_evidence) == {"TC-G01", "TC-G02"}
        and all(item.get("sharedTailBoundToBranchSource") is True for item in shared_tail_evidence.values())
        and {
            tuple(item.get("requirementRefs") or [])
            for item in shared_tail_evidence.values()
        } == {("REQ-001",), ("REQ-002",)},
        "One AI-authored state-independent landing tail must bind to separate verified branch cases instead of claiming one cross-branch execution",
    )
    require(
        not ai_skill_service.case_covers_requirement_acceptance(
            {
                "title": "点击发票入口后稳定可达",
                "requirementRefs": ["REQ-001"],
                "steps": ["等待发票入口可见"],
                "assertions": ["发票入口可见"],
            },
            next(item for item in generic_acceptance_checks if item.get("id") == "REQ-001-CHECK-04"),
        ),
        "A reachability claim in the case title must not substitute for a real click and terminal assertion",
    )
    require(
        not ai_skill_service.case_covers_requirement_acceptance(
            {
                "requirementRefs": ["REQ-001"],
                "steps": ["进入订单管理", "等待发票入口可见"],
                "assertions": ["发票入口可见，点击后应显示授权页或文件选择相关界面"],
            },
            next(item for item in generic_acceptance_checks if item.get("id") == "REQ-001-CHECK-04"),
        ),
        "A terminal phrase containing 文件选择 must not count as a target action without an ordered target click",
    )
    confirmation_landing_case = {
        "case_id": "MC-CONFIRM-LANDING",
        "requirementRefs": ["REQ-001"],
        "steps": [
            "进入订单管理页面",
            "点击发票入口",
            "观察页面跳转或授权窗口弹出情况",
            "确认无App崩溃、无长时间白屏",
            "确认内容列表页加载完成，底部操作按钮可见",
        ],
        "expected_result": (
            "点击发票入口后页面跳转或弹出授权窗口，无App崩溃、无长时间白屏，"
            "内容列表页加载完成"
        ),
    }
    confirmation_landing_tail = ai_skill_service._bounded_landing_tail(
        confirmation_landing_case,
        ["发票"],
    )
    require(
        confirmation_landing_tail
        and ai_skill_service._bounded_landing_tail_is_executable(
            confirmation_landing_tail,
            confirmation_landing_case["requirementRefs"],
        ),
        "Declarative confirmation steps must remain usable as bounded AI observations",
    )
    conditional_required_landing = ai_skill_service._bounded_landing_tail({
        "requirementRefs": ["REQ-003 扫描复印百度网盘入口可达"],
        "steps": [
            "进入扫描复印页",
            "若「百度网盘」入口可见，则点击该入口",
            "等待页面跳转或弹窗出现",
        ],
        "expected_result": (
            "点击后已离开扫描复印页，百度网盘授权页、文件列表或系统弹窗之一可见，"
            "无Crash或白屏"
        ),
    }, ["百度网盘"])
    conditional_reachability_check = {
        "id": "REQ-003-CHECK-04",
        "requirementId": "REQ-003",
        "branch": "扫描复印",
        "kind": "reachability",
        "text": "点击百度网盘入口并校验目标页面稳定可达",
    }
    require(
        conditional_required_landing
        and conditional_required_landing.get("conditionalTargetCanonicalized") is True
        and conditional_required_landing.get("flow", [""])[0] == "点击「百度网盘」入口"
        and ai_skill_service.case_covers_requirement_acceptance({
            "steps": conditional_required_landing.get("flow"),
            "assertions": [conditional_required_landing.get("assertionTarget")],
            "requirementRefs": ["REQ-003 扫描复印百度网盘入口可达"],
        }, conditional_reachability_check),
        "An AI-authored conditional target click must become a real visible-text product assertion instead of silently skipping a missing required entry",
    )
    require(
        ai_skill_service._bounded_landing_tail({
            "steps": ["点击发票入口", "确认打印"],
            "expected_result": "打印成功",
        }, ["发票"]) is None,
        "Confirmation commands that mutate external state must not become observations",
    )
    single_state_landing_case = {
        "case_id": "MC-SINGLE-LANDING",
        "requirementRefs": ["REQ-001"],
        "steps": [
            "进入订单管理页面",
            "点击发票入口",
            "观察跳转到发票内容列表页",
            "确认页面包含文件名和操作按钮",
            "确认无崩溃、无白屏",
        ],
        "expected_result": "点击发票入口后成功跳转，内容列表页稳定显示，无崩溃、无白屏",
    }
    single_state_tail = ai_skill_service._bounded_landing_tail(
        single_state_landing_case,
        ["发票"],
    )
    normalized_single_state_tail = ai_skill_service._merge_bounded_landing_tails(
        [single_state_tail],
    )
    vague_landing_tail = ai_skill_service._bounded_landing_tail({
        "steps": ["点击发票入口", "观察页面跳转情况", "确认无崩溃、无白屏"],
        "expected_result": "点击后页面有响应，无崩溃、无白屏",
    }, ["发票"])
    require(
        single_state_tail
        and not ai_skill_service._bounded_landing_tail_is_executable(
            single_state_tail,
            single_state_landing_case["requirementRefs"],
        )
        and ai_skill_service._bounded_landing_tail_is_executable(
            normalized_single_state_tail,
            single_state_landing_case["requirementRefs"],
        )
        and "已离开来源页" in str(normalized_single_state_tail.get("assertionTarget") or "")
        and not ai_skill_service._bounded_landing_tail_is_executable(
            ai_skill_service._merge_bounded_landing_tails([vague_landing_tail]),
            ["REQ-001"],
        ),
        "One concrete AI-authored landing state plus explicit stability may gain a target-bound visible alternative, while vague transition-only evidence must stay blocked",
    )
    automatic_records = [{"raw": item, "compact": item} for item in generic_display_cases]
    manual_records = [{"raw": item, "compact": item} for item in generic_reachability_manual]
    _focused_auto, focused_manual, _focused_context, focus_meta = ai_skill_service._focus_executable_convergence_candidates(
        generic_display_payload,
        automatic_records,
        manual_records,
        {"pass": "coverage_convergence", "portfolioAudit": generic_display_audit},
    )
    require(
        {item.get("case_id") for item in focused_manual} == {"MC-G01", "MC-G02"}
        and focus_meta.get("candidateSelectionMode") == "missing_requirement_alternates",
        "The existing convergence AI pass must receive the bounded reachability alternates that actually match each missing acceptance dimension",
    )
    generic_complete_payload = json.loads(json.dumps(generic_display_payload, ensure_ascii=False))
    for case in generic_complete_payload.get("cases") or []:
        case["steps"].extend([
            "点击发票入口",
            "等待授权页、登录页或内容列表任一合法页面可见",
        ])
        case["assertions"].append("授权页、登录页或内容列表任一合法页面可见，且无白屏或崩溃")
    require(
        ai_skill_service.executable_yaml_portfolio_audit(generic_complete_payload, {"min_automation_cases": 2}).get("ok"),
        "A visible-text bounded destination check must close the source acceptance contract without requiring deep account actions",
    )
    copy_check = next(
        item for item in generic_acceptance_checks
        if item.get("requirementId") == "REQ-002" and item.get("kind") == "copy"
    )
    require(
        ai_skill_service.case_covers_requirement_acceptance({
            "requirementRefs": ["REQ-002"],
            "steps": ["进入优惠券页面"],
            "assertions": ["优惠券页面展示「发票」入口"],
        }, copy_check),
        "A concrete assertion that displays the expected target literal must satisfy copy acceptance",
    )
    require(
        not ai_skill_service.case_covers_requirement_acceptance({
            "requirementRefs": ["REQ-002"],
            "steps": ["点击「发票」入口"],
        }, copy_check),
        "A click-only target mention must not satisfy copy acceptance",
    )
    require(
        not ai_skill_service.case_covers_requirement_acceptance({
            "requirementRefs": ["REQ-002"],
            "assertions": ["「发票」入口仅显示图标、无文字"],
        }, copy_check),
        "An icon-only assertion must not be misreported as target-copy coverage",
    )
    coupon_visibility_check = next(
        item for item in generic_acceptance_checks
        if item.get("requirementId") == "REQ-002" and item.get("kind") == "visibility"
    )
    require(
        not ai_skill_service.case_covers_requirement_acceptance({
            "requirementRefs": ["REQ-001", "REQ-002"],
            "steps": ["进入订单管理", "等待发票入口可见"],
            "assertions": ["两个业务页面都展示发票入口"],
        }, coupon_visibility_check),
        "A multi-requirement case must contain concrete steps for each branch it claims to cover",
    )
    aggregate_cross_branch_case = {
        "case_id": "TC-CROSS-001",
        "title": "多个业务页发票入口统一校验",
        "executionLevel": "executable",
        "requirementRefs": ["REQ-001", "REQ-002"],
        "steps": [
            "依次进入订单管理、优惠券页面",
            "在每个页面等待发票入口可见",
            "点击发票入口",
            "等待授权页、登录页或内容列表任一合法页面可见",
        ],
        "assertions": ["所有业务页面均展示发票入口且与同级入口并列，点击后稳定可达"],
    }
    aggregate_audit = ai_skill_service.executable_yaml_portfolio_audit(
        {"analysis": generic_analysis, "cases": [aggregate_cross_branch_case]},
        {"min_automation_cases": 1},
    )
    require(
        aggregate_audit.get("coveredAcceptanceCheckCount") == 0
        and aggregate_audit.get("missingAcceptanceCheckCount") == len(generic_acceptance_checks),
        "Aggregate prose such as 'visit every branch' must not count as executed evidence for sibling business paths",
    )
    independently_executed_cross_case = {
        **aggregate_cross_branch_case,
        "case_id": "TC-CROSS-002",
        "steps": [
            "进入订单管理",
            "等待发票入口可见",
            "校验发票入口文案完整且与当前入口同级",
            "点击发票入口",
            "等待授权页、登录页或内容列表任一合法页面可见",
            "返回App首页",
            "进入优惠券",
            "等待发票入口可见",
            "校验发票入口文案完整且与当前入口同级",
            "点击发票入口",
            "等待授权页、登录页或内容列表任一合法页面可见",
        ],
        "assertions": [
            "订单管理发票入口可见、文案完整、与同级入口并列且点击后稳定可达",
            "优惠券发票入口可见、文案完整、与同级入口并列且点击后稳定可达",
        ],
    }
    independently_executed_audit = ai_skill_service.executable_yaml_portfolio_audit(
        {"analysis": generic_analysis, "cases": [independently_executed_cross_case]},
        {"min_automation_cases": 1},
    )
    require(
        independently_executed_audit.get("coveredAcceptanceCheckCount") == len(generic_acceptance_checks),
        "A multi-reference case may count coverage only when every branch has its own navigation, checks, target click and terminal evidence",
    )
    require(
        ai_skill_service._source_navigation_has_alternative_destinations([
            "点击首页中名称为「扫描复印」或「扫描仪扫描」的入口",
        ])
        and ai_skill_service._source_navigation_has_alternative_destinations([
            "依次进入订单管理、优惠券页面",
        ])
        and not ai_skill_service._source_navigation_has_alternative_destinations(
            ["等待授权页、登录页或内容列表任一合法页面可见"],
            allow_terminal_wait_alternatives=True,
        ),
        "Navigation must name one concrete target while waits may retain multiple legitimate terminal states",
    )
    aggregate_plan = {
        "allowedBaselineIds": ["base-entry-nav"],
        "requirementPoints": generic_analysis.get("requirement_points") or [],
        "cases": [{
            "caseId": "TC-CROSS-001",
            "baselineId": "base-entry-nav",
            "baselineGrounded": True,
            "precondition": "App 首页",
            "flow": list(aggregate_cross_branch_case["steps"]),
            "assertionTarget": aggregate_cross_branch_case["assertions"][0],
            "requirementRefs": ["REQ-001", "REQ-002"],
            "batch": "remaining",
        }],
        "authoritative": True,
    }
    aggregate_applied = ai_skill_service.apply_executable_yaml_plan_to_payload(
        {"analysis": generic_analysis, "cases": [aggregate_cross_branch_case]},
        aggregate_plan,
    )
    require(
        aggregate_applied.get("cases", [{}])[0].get("executionLevel") == "needs_review"
        and aggregate_applied.get("review", {}).get("executable_yaml_plan", {}).get("ambiguous_navigation_guard_count") == 1,
        "AI planning must downgrade aggregate or alternative navigation before YAML conversion",
    )

    bounded_convergence_payload = json.loads(json.dumps(generic_display_payload, ensure_ascii=False))
    for case in bounded_convergence_payload.get("cases") or []:
        case["ai_case_plan"] = {
            "baselineId": "base-entry-nav",
            "baselineGrounded": True,
            "precondition": "App 首页",
            "flow": list(case.get("steps") or []) + ["校验「发票」入口可见"],
            "assertionTarget": (case.get("assertions") or [""])[0],
            "pathPlanApplied": True,
            "batch": "smoke",
        }
    for index, branch in enumerate(("订单管理", "优惠券"), start=1):
        landing_steps = [
            f"进入{branch}",
            "点击发票入口",
            "等待授权页、登录页或内容列表任一合法页面可见",
        ]
        if index == 2:
            landing_steps = [
                f"进入{branch}",
                "点击发票入口",
                "观察页面跳转后授权页或内容列表页任一合法页面可见",
                "确认无App崩溃、无长时间白屏",
                "确认内容列表页加载完成，底部操作按钮可见",
            ]
        bounded_convergence_payload["manual_cases"].append({
            "case_id": f"TC-R{index:02d}",
            "title": f"{branch}发票入口首个落地页",
            "executionLevel": "manual",
            "originExecutionLevel": "automatic",
            "ai_case_classification": {
                "level": "manual",
                "originLevel": "automatic",
                "reason": "首轮 AI 对外部首屏过度保守",
            },
            "requirementRefs": [f"REQ-{index:03d}"],
            "steps": landing_steps,
            "assertions": [
                (
                    "授权页、登录页或内容列表任一合法页面可见，且无长时间白屏或崩溃"
                    if index == 1
                    else f"页面已离开{branch}页并显示外部相关页面，无Crash或长时间白屏"
                ),
            ],
        })
    bounded_audit = ai_skill_service.executable_yaml_portfolio_audit(
        bounded_convergence_payload,
        {"min_automation_cases": 2},
    )
    require(
        not bounded_audit.get("ok")
        and bounded_audit.get("missingAcceptanceCheckCount") == 2
        and all(item.get("kind") == "reachability" for item in bounded_audit.get("missingAcceptanceChecks") or []),
        "Bounded landing candidates must remain unresolved until the existing AI convergence pass classifies them",
    )
    bounded_requests = []
    old_run_ai_skill = ai_skill_service.run_ai_skill
    try:
        def fake_bounded_convergence_planner(skill_name, request, **_kwargs):
            require(skill_name == "executable_yaml_planner", "Unexpected AI skill during bounded convergence replay")
            bounded_requests.append(request)
            executable = []
            downgraded = []
            for candidate in request.get("cases") or []:
                if candidate.get("currentLevel") == "executable":
                    executable.append({
                        "caseId": candidate["case_id"],
                        "baselineId": "base-entry-nav",
                        "precondition": "App 首页",
                        "flow": candidate.get("steps") or [],
                        "assertionTarget": (candidate.get("assertions") or [""])[0],
                        "requirementRefs": candidate.get("requirementRefs") or [],
                        "executableReason": "成功基线已覆盖来源页导航",
                        "batch": "smoke",
                    })
                else:
                    downgraded.append({
                        "caseId": candidate["case_id"],
                        "reason": "新目标落地页没有历史成功执行基线，建议人工确认",
                        "requirementRefs": candidate.get("requirementRefs") or [],
                    })
            return {
                "cases": executable,
                "needs_review_cases": [],
                "draft_cases": [],
                "manual_cases": downgraded,
                "review": {"planning_reason": "模拟 AI 对新端点过度保守"},
            }

        ai_skill_service.run_ai_skill = fake_bounded_convergence_planner
        bounded_plan = ai_skill_service.call_skill_executable_yaml_planner(
            "新增发票入口",
            "会员服务",
            bounded_convergence_payload,
            [{
                "id": "base-entry-nav",
                "title": "会员入口来源页稳定导航",
                "sourceKind": "verified_execution",
                "verificationStatus": "execution_success",
                "businessPath": "首页 -> 会员服务入口",
            }],
            {"smokeCount": 2},
            planning_context={
                "pass": "coverage_convergence",
                "portfolioAudit": bounded_audit,
            },
        )
    finally:
        ai_skill_service.run_ai_skill = old_run_ai_skill
    bounded_request_candidates = {
        item.get("case_id"): item for item in bounded_requests[0].get("cases") or []
    }
    require(
        all(
            bounded_request_candidates[f"TC-R{index:02d}"].get("convergenceEvidence", {}).get("eligible") is True
            for index in (1, 2)
        ),
        "The one existing convergence AI call must receive platform-verified source-path plus bounded-tail evidence",
    )
    adapted_leaf_plan = {
        "authoritative": True,
        "allowedBaselineIds": ["base-entry-nav"],
        "verifiedBaselineIds": ["base-entry-nav"],
        "requirementPoints": generic_analysis.get("requirement_points") or [],
        "planningContext": {"pass": "coverage_convergence"},
        "focusedCandidateIds": ["TC-R01"],
        "candidateEligibilityById": {
            "TC-R01": bounded_request_candidates["TC-R01"]["convergenceEvidence"],
        },
        "cases": [{
            "caseId": "TC-R01",
            "baselineId": "base-entry-nav",
            "baselineGrounded": True,
            "precondition": "App 首页",
            "flow": [
                "启动App并等待首页加载完成",
                "进入订单管理",
                "点击「新版发票」",
                "等待发票入口可见",
                "点击发票入口",
                "等待授权页、登录页或内容列表任一合法页面可见",
            ],
            "assertionTarget": "授权页、登录页或内容列表任一合法页面可见，且无白屏或崩溃",
            "requirementRefs": ["REQ-001"],
            "executableReason": "结合当前 UI 证据把成功基线的叶子适配为新版发票",
            "batch": "remaining",
        }],
        "manual_cases": [],
    }
    adapted_leaf_applied = ai_skill_service.apply_executable_yaml_plan_to_payload(
        bounded_convergence_payload,
        adapted_leaf_plan,
    )
    adapted_leaf_case = next(
        item for item in adapted_leaf_applied.get("cases") or []
        if item.get("case_id") == "TC-R01"
    )
    adapted_leaf_review = adapted_leaf_applied.get("review", {}).get("executable_yaml_plan", {})
    require(
        "点击「新版发票」" in adapted_leaf_case.get("steps", [])
        and adapted_leaf_case.get("ai_case_plan", {}).get("boundedConvergence", {}).get("modelPathPreserved") is True
        and adapted_leaf_review.get("bounded_convergence_ai_path_count") == 1
        and adapted_leaf_review.get("bounded_convergence_override_count") == 0,
        "A complete AI-adapted current leaf must survive bounded evidence instead of being overwritten by the historical baseline leaf",
    )
    trusted_prefix = ai_skill_service._trusted_baseline_source_navigation_flow({
        "baselineUsable": True,
        "trusted": True,
        "snippet": """
          - aiTap: \"会员服务\"
          - aiWaitFor: \"开票服务可见\"
          - aiTap: \"开票服务\"
          - aiTap: \"电子发票\"
          - aiTap: \"本地导入\"
          - aiTap: \"确认上传\"
        """,
    }, ["云端发票"], "会员服务")
    require(
        trusted_prefix == [
            "点击「会员服务」",
            "等待开票服务可见",
            "点击「开票服务」",
            "点击「电子发票」",
        ],
        "Trusted baseline navigation must stop before data import while preserving every visible parent-page action",
    )
    adapted_current_leaf, current_leaf_adapted = ai_skill_service._adapt_trusted_navigation_to_candidate(
        trusted_prefix,
        {
            "steps": [
                "启动App并等待首页加载完成",
                "点击名称为「会员服务」的入口",
                "等待开票服务可见",
                "点击「开票服务」",
                "等待当前可选发票类型加载完成",
                "点击名称为「新版发票」的入口",
                "等待新版发票页面加载完成",
                "等待「云端发票」入口可见",
            ],
        },
        ["云端发票"],
        "会员服务",
    )
    require(
        current_leaf_adapted is True
        and adapted_current_leaf == [
            "点击「会员服务」",
            "等待开票服务可见",
            "点击「开票服务」",
            "等待当前可选发票类型加载完成",
            "点击名称为「新版发票」的入口",
            "等待新版发票页面加载完成",
        ]
        and "电子发票" not in " ".join(adapted_current_leaf),
        "A concrete AI candidate leaf must replace only the divergent historical leaf while retaining the verified parent path",
    )
    target_transition_flow, target_transition_adapted = ai_skill_service._adapt_trusted_navigation_to_candidate(
        trusted_prefix,
        {
            "steps": [
                "启动App并等待首页加载完成",
                "点击名称为「会员服务」的入口",
                "等待开票服务可见",
                "点击「开票服务」",
                "等待当前可选发票类型加载完成",
                "点击名称为「新版发票」的入口",
                "点击「云端发票」入口",
                "等待云端发票落地页首个稳定页面可见，无白屏或崩溃",
            ],
        },
        ["云端发票"],
        "会员服务",
    )
    require(
        target_transition_adapted is True
        and target_transition_flow[-2:] == [
            "点击「云端发票」入口",
            "等待云端发票落地页首个稳定页面可见，无白屏或崩溃",
        ]
        and "电子发票" not in " ".join(target_transition_flow),
        "Trusted parent-path adaptation must retain the candidate's target click and bounded landing terminal",
    )
    require(
        ai_skill_service._candidate_navigation_specificity({
            "steps": [
                "进入会员服务",
                "等待新版发票页面加载完成",
                "等待云端发票入口可见",
            ],
        }, ["云端发票"], "会员服务")
        < ai_skill_service._candidate_navigation_specificity({
            "steps": [
                "进入会员服务",
                "点击新版发票",
                "等待新版发票页面加载完成",
                "等待云端发票入口可见",
            ],
        }, ["云端发票"], "会员服务"),
        "A candidate with an explicit safe leaf action must outrank a shorter path that only assumes the landing state",
    )
    current_leaf_payload = {
        "analysis": {
            "requirement_points": [
                "REQ-041 资料归档：企业云盘入口可见、同级、文案正确且点击后首屏可达",
            ],
            "requirement_acceptance_checks": [
                {"id": "REQ-041-CHECK-01", "requirementId": "REQ-041", "branch": "资料归档", "kind": "visibility", "text": "校验企业云盘入口可见"},
                {"id": "REQ-041-CHECK-02", "requirementId": "REQ-041", "branch": "资料归档", "kind": "relation", "text": "校验企业云盘入口与其它入口同级"},
                {"id": "REQ-041-CHECK-03", "requirementId": "REQ-041", "branch": "资料归档", "kind": "copy", "text": "校验企业云盘入口文案正确"},
                {"id": "REQ-041-CHECK-04", "requirementId": "REQ-041", "branch": "资料归档", "kind": "reachability", "text": "点击企业云盘入口并校验首屏稳定可达"},
            ],
        },
        "cases": [{
            "case_id": "TC-HISTORICAL-LEAF",
            "title": "资料归档企业云盘入口展示",
            "executionLevel": "executable",
            "requirementRefs": ["REQ-041 资料归档企业云盘入口"],
            "preconditions": ["App 首页"],
            "steps": [
                "进入资料服务",
                "进入资料归档",
                "等待旧版归档页面加载完成",
                "等待企业云盘入口可见",
            ],
            "assertions": ["企业云盘入口可见、文案正确且与其它入口同级"],
            "ai_case_plan": {
                "baselineId": "base-archive-current-leaf",
                "baselineGrounded": True,
                "pathPlanApplied": True,
                "flow": ["进入资料服务", "进入资料归档", "等待旧版归档页面加载完成", "等待企业云盘入口可见"],
                "precondition": "App 首页",
            },
        }, {
            "case_id": "TC-CLOUD-LANDING",
            "title": "企业云盘首屏反馈",
            "executionLevel": "needs_review",
            "requirementRefs": ["REQ-041 资料归档企业云盘入口"],
            "steps": [
                "进入资料归档",
                "点击企业云盘入口",
                "等待授权页、登录页或内容列表任一稳定状态可见",
            ],
            "assertions": ["点击企业云盘后首个稳定页面可见且无白屏或崩溃"],
        }],
        "manual_cases": [{
            "case_id": "TC-CURRENT-LEAF",
            "title": "新版归档企业云盘入口",
            "executionLevel": "manual",
            "originExecutionLevel": "manual",
            "requirementRefs": ["REQ-041 资料归档企业云盘入口"],
            "preconditions": ["App 首页"],
            "steps": [
                "启动App并等待首页加载完成",
                "进入资料服务",
                "等待资料服务页面加载完成",
                "进入资料归档",
                "等待当前归档类型可见",
                "点击新版归档",
                "等待企业云盘入口可见",
            ],
            "assertions": ["企业云盘入口可见、文案正确且与其它入口同级"],
        }],
    }
    current_leaf_audit = ai_skill_service.executable_yaml_portfolio_audit(current_leaf_payload, {})
    current_leaf_automatic_records = [{
        "raw": item,
        "compact": ai_skill_service._compact_case_for_plan(item, index, origin_level="automatic"),
    } for index, item in enumerate(current_leaf_payload["cases"])]
    current_leaf_manual_records = [{
        "raw": item,
        "compact": ai_skill_service._compact_case_for_plan(item, index, origin_level="manual"),
    } for index, item in enumerate(current_leaf_payload["manual_cases"])]
    current_leaf_baseline = ai_skill_service._compact_baseline_candidate({
        "id": "base-archive-current-leaf",
        "title": "历史归档成功路径",
        "aiSelectedBranchName": "资料归档",
        "sourceKind": "verified_execution",
        "verificationStatus": "execution_success",
        "snippet": (
            "# baseline.start_page: App 首页\n"
            "- aiTap: 资料服务\n"
            "- aiWaitFor: 等待资料服务页面加载完成\n"
            "- aiTap: 资料归档\n"
            "- aiWaitFor: 等待旧版归档入口可见\n"
            "- aiTap: 旧版归档\n"
            "- aiTap: 本地导入"
        ),
    })
    current_leaf_evidence_by_id = ai_skill_service._bounded_convergence_evidence(
        current_leaf_payload,
        current_leaf_automatic_records,
        current_leaf_audit,
        selected_baselines=[current_leaf_baseline],
        manual_records=current_leaf_manual_records,
    )
    current_leaf_evidence = next((
        item for item in current_leaf_evidence_by_id.values()
        if item.get("currentLeafSourceCaseId") == "TC-CURRENT-LEAF"
    ), {})
    require(
        current_leaf_evidence.get("eligible") is True
        and current_leaf_evidence.get("currentLeafAdapted") is True
        and current_leaf_evidence.get("currentLeafSourceCaseId") == "TC-CURRENT-LEAF"
        and "点击新版归档" in current_leaf_evidence.get("flow", [])
        and "旧版归档" not in " ".join(current_leaf_evidence.get("flow") or [])
        and len(current_leaf_evidence.get("flow") or []) <= 8
        and set(current_leaf_evidence.get("acceptanceCheckIds") or []) == {
            "REQ-041-CHECK-04",
        },
        "Reachability convergence must prefer a concrete current AI leaf while using the verified baseline only for its common parent path",
    )
    visual_leaf_payload = json.loads(json.dumps(current_leaf_payload, ensure_ascii=False))
    visual_leaf_payload["manual_cases"] = []
    visual_leaf_payload["review"] = {
        "current_page_evidence": [{
            "caseId": "TC-VISUAL-SIBLING",
            "requirementId": "REQ-041 资料归档",
            "branch": "资料归档",
            "pageTitle": "新版归档",
            "parentPath": ["资料服务", "资料归档"],
            "navigationLeaf": "新版归档",
            "targetText": "企业云盘",
            "sameBranch": True,
            "confidence": 0.96,
            "source": "figma_current_frame",
        }],
    }
    visual_leaf_audit = ai_skill_service.executable_yaml_portfolio_audit(visual_leaf_payload, {})
    visual_leaf_records = [{
        "raw": item,
        "compact": ai_skill_service._compact_case_for_plan(item, index, origin_level="automatic"),
    } for index, item in enumerate(visual_leaf_payload["cases"])]
    visual_leaf_evidence_by_id = ai_skill_service._bounded_convergence_evidence(
        visual_leaf_payload,
        visual_leaf_records,
        visual_leaf_audit,
        selected_baselines=[current_leaf_baseline],
        manual_records=[],
    )
    visual_leaf_evidence = next((
        item for item in visual_leaf_evidence_by_id.values()
        if item.get("currentLeafEvidenceSource") == "figma_current_frame"
    ), {})
    require(
        visual_leaf_evidence.get("currentLeafAdapted") is True
        and visual_leaf_evidence.get("currentLeafSourceCaseId") == "TC-VISUAL-SIBLING"
        and "点击「新版归档」" in visual_leaf_evidence.get("flow", [])
        and "旧版归档" not in " ".join(visual_leaf_evidence.get("flow") or [])
        and (visual_leaf_evidence.get("currentLeafEvidence") or {}).get("confidence") == 0.96,
        "High-confidence current-frame evidence must replace only a historical baseline leaf after the shared parent path is proven",
    )
    broad_leaf_evidence = ai_skill_service._normalize_visual_current_page_evidence([{
        "caseId": "TC-HISTORICAL-LEAF",
        "requirementId": "REQ-041",
        "branch": "资料归档",
        "pageTitle": "新版归档",
        "parentPath": ["资料服务"],
        "navigationLeaf": "资料归档页",
        "targetText": "企业云盘",
        "sameBranch": True,
        "confidence": 0.93,
        "source": "figma_current_frame",
    }])[0]
    broad_leaf_adapted, broad_leaf_changed = ai_skill_service._adapt_trusted_navigation_to_visual_evidence(
        [
            "点击「资料服务」",
            "等待资料服务页面加载完成",
            "点击「资料归档」",
            "等待旧版归档入口可见",
            "点击「旧版归档」",
        ],
        broad_leaf_evidence,
        "资料归档",
    )
    require(
        broad_leaf_evidence.get("leafDerivedFromPageTitle") is True
        and broad_leaf_evidence.get("navigationLeaf") == "新版归档"
        and broad_leaf_evidence.get("parentPath") == ["资料服务", "资料归档页"]
        and broad_leaf_changed is True
        and "点击「新版归档」" in broad_leaf_adapted
        and "旧版归档" not in " ".join(broad_leaf_adapted),
        "A concrete current Frame title must become the navigation leaf when visual AI returned only its broad parent page as navigationLeaf",
    )
    photo_variant_case = {
        "case_id": "TC-PHOTO-VARIANT",
        "title": "照片打印 5寸照片百度网盘入口",
        "requirementRefs": ["REQ-042 照片打印百度网盘入口"],
        "steps": [
            "点击「照片打印」入口",
            "若存在尺寸选择，点击「5寸照片」或类似选项进入编辑页",
            "等待「百度网盘」入口可见",
        ],
        "ai_case_plan": {
            "originalFlow": [
                "点击「照片打印」入口",
                "点击「5寸照片」",
                "等待「百度网盘」入口可见",
            ],
        },
    }
    photo_variant_payload = {
        "review": {
            "current_page_evidence": [{
                "caseId": "TC-PHOTO-VARIANT",
                "requirementId": "REQ-042",
                "branch": "照片打印-5寸照片",
                "pageTitle": "5寸照片",
                "parentPath": ["照片打印"],
                "navigationLeaf": "5寸照片",
                "targetText": "百度网盘",
                "sameBranch": True,
                "confidence": 0.9,
                "source": "figma_current_frame",
            }, {
                "caseId": "TC-PHOTO-VARIANT",
                "requirementId": "REQ-042",
                "branch": "照片打印-一寸照",
                "pageTitle": "一寸照",
                "parentPath": ["照片打印"],
                "navigationLeaf": "一寸照",
                "targetText": "百度网盘",
                "sameBranch": True,
                "confidence": 0.95,
                "source": "figma_current_frame",
            }],
        },
    }
    selected_photo_variant = ai_skill_service._current_visual_page_evidence_for_case(
        photo_variant_payload,
        photo_variant_case,
        "TC-PHOTO-VARIANT",
        "照片打印",
        ["百度网盘"],
    )
    concrete_photo_flow, concrete_photo_changed = (
        ai_skill_service._adapt_trusted_navigation_to_visual_evidence(
            photo_variant_case["steps"],
            selected_photo_variant,
            "照片打印",
        )
    )
    require(
        selected_photo_variant.get("navigationLeaf") == "5寸照片"
        and concrete_photo_changed is True
        and "点击「5寸照片」" in concrete_photo_flow
        and "一寸照" not in " ".join(concrete_photo_flow)
        and "或类似" not in " ".join(concrete_photo_flow)
        and ai_skill_service._source_navigation_has_alternative_destinations(
            photo_variant_case["steps"]
        ) is True,
        "A source-authored concrete design state must outrank a higher-confidence sibling Frame and replace ambiguous navigation with one visible target",
    )
    photo_reachability_flow, photo_reachability_changed = (
        ai_skill_service._adapt_trusted_navigation_to_visual_evidence(
            [
                "等待 App 首页稳定显示",
                "点击「照片打印」入口",
                "等待照片打印页面加载完成",
                "点击「6寸照片」",
                "等待导入区域加载完成",
                "点击「百度网盘」入口",
                "等待百度网盘落地页首个稳定页面可见，无白屏或崩溃",
            ],
            selected_photo_variant,
            "照片打印",
        )
    )
    require(
        photo_reachability_changed is True
        and "点击「5寸照片」" in photo_reachability_flow
        and "6寸照片" not in " ".join(photo_reachability_flow)
        and "点击「百度网盘」入口" in photo_reachability_flow
        and photo_reachability_flow[-1] == "等待百度网盘落地页首个稳定页面可见，无白屏或崩溃"
        and ai_skill_service.case_covers_requirement_acceptance(
            {
                "steps": photo_reachability_flow,
                "assertions": ["百度网盘落地页首个稳定页面可见，无白屏或崩溃"],
                "requirementRefs": ["REQ-042"],
            },
            {
                "requirementId": "REQ-042",
                "branch": "照片打印",
                "kind": "reachability",
                "text": "点击百度网盘入口并校验目标页面稳定可达",
            },
        ),
        "Replacing a historical visual leaf must preserve the target click and bounded landing terminal",
    )
    unresolved_only_evidence = ai_skill_service._bounded_convergence_evidence(
        visual_leaf_payload,
        visual_leaf_records,
        {
            "missingAcceptanceChecks": [],
            "unresolvedAutomaticCaseIds": ["TC-HISTORICAL-LEAF"],
        },
        selected_baselines=[current_leaf_baseline],
        manual_records=[],
    ).get("TC-HISTORICAL-LEAF") or {}
    require(
        unresolved_only_evidence.get("eligible") is True
        and unresolved_only_evidence.get("currentLeafAdapted") is True
        and unresolved_only_evidence.get("flow", [""])[0] == "启动App并等待首页加载完成"
        and "点击「新版归档」" in unresolved_only_evidence.get("flow", []),
        "An unresolved home-start automatic case must receive bounded baseline and current-Frame evidence even when a sibling case already covers the REQ checks",
    )
    home_guarded_prefix = ai_skill_service._ensure_trusted_home_start_guard(
        ["点击「会员服务」", "等待开票服务可见"],
        {"snippet": "# baseline.start_page: App 首页"},
    )
    require(
        home_guarded_prefix == [
            "启动App并等待首页加载完成",
            "点击「会员服务」",
            "等待开票服务可见",
        ]
        and ai_skill_service._ensure_trusted_home_start_guard(
            home_guarded_prefix,
            {"startPage": "App 首页"},
        ) == home_guarded_prefix,
        "A trusted home-start baseline must expose one stable launch checkpoint before its first visible-text tap",
    )
    repeated_parent_payload = {
        "analysis": {
            "requirement_points": ["REQ-052 媒体打印：云盘入口展示"],
            "requirement_acceptance_checks": [
                {
                    "id": "REQ-052-CHECK-01",
                    "requirementId": "REQ-052",
                    "branch": "媒体打印",
                    "kind": "visibility",
                    "text": "校验云盘入口可见",
                },
            ],
        },
        "cases": [{
            "case_id": "TC-REPEATED-PARENT",
            "title": "媒体打印云盘入口展示",
            "executionLevel": "executable",
            "requirementRefs": ["REQ-052 媒体打印"],
            "start_page": "App首页",
            "steps": ["等待 App首页稳定显示", "点击媒体打印入口", "点击当前规格", "等待云盘入口可见"],
            "assertions": ["云盘入口可见"],
        }],
        "manual_cases": [],
        "review": {
            "current_page_evidence": [{
                "caseId": "TC-REPEATED-PARENT",
                "requirementId": "REQ-052",
                "branch": "媒体打印",
                "pageTitle": "当前规格",
                "parentPath": ["App首页", "媒体打印"],
                "navigationLeaf": "当前规格",
                "targetText": "云盘",
                "sameBranch": True,
                "confidence": 0.99,
                "source": "current_design_frame",
            }],
        },
    }
    repeated_parent_plan = {
        "authoritative": True,
        "allowedBaselineIds": ["base-repeated-parent"],
        "verifiedBaselineIds": ["base-repeated-parent"],
        "selectedBaselines": [{
            "id": "base-repeated-parent",
            "sourceKind": "verified_execution",
            "verificationStatus": "execution_success",
            "startPage": "App 首页",
            "snippet": (
                "# baseline.start_page: App 首页\n"
                "- aiTap: 媒体打印 icon\n"
                "- aiWaitFor: 等待媒体打印主页加载完成\n"
                "- aiTap: 媒体打印\n"
                "- aiWaitFor: 等待规格入口可见\n"
                "- aiTap: 历史规格\n"
                "- aiWaitFor: 等待云盘入口可见"
            ),
        }],
        "requirementPoints": ["REQ-052 媒体打印：云盘入口展示"],
        "scopePlan": {"smokeCount": 1},
        "cases": [{
            "caseId": "TC-REPEATED-PARENT",
            "baselineId": "base-repeated-parent",
            "baselineGrounded": True,
            "precondition": "App 首页",
            "flow": ["等待 App 首页稳定显示", "点击媒体打印入口", "点击当前规格"],
            "assertionTarget": "云盘入口可见",
            "requirementRefs": ["REQ-052 媒体打印"],
            "executableReason": "成功基线提供父页面路径，当前设计提供具体规格",
            "batch": "smoke",
        }],
        "needs_review_cases": [],
        "draft_cases": [],
        "manual_cases": [],
    }
    repeated_parent_applied = ai_skill_service.apply_executable_yaml_plan_to_payload(
        repeated_parent_payload,
        repeated_parent_plan,
    )
    repeated_parent_case = repeated_parent_applied["cases"][0]
    repeated_parent_flow = repeated_parent_case.get("steps") or []
    require(
        repeated_parent_flow == [
            "启动App并等待首页加载完成",
            "点击「媒体打印 icon」",
            "等待媒体打印主页加载完成",
            "点击「媒体打印」",
            "等待规格入口可见",
            "点击当前规格",
        ]
        and repeated_parent_case.get("ai_case_plan", {}).get("trustedBaselineNavigationAdapted") is True
        and repeated_parent_applied.get("review", {}).get("executable_yaml_plan", {}).get(
            "trusted_baseline_navigation_adapted_count"
        ) == 1,
        "A selected successful baseline must preserve repeated same-label parent navigation before applying the current visual leaf",
    )
    require(
        ai_skill_service._source_navigation_has_alternative_destinations([
            "点击「会员服务」",
            "等待「电子发票」或「纸质发票」页面加载完成",
        ]),
        "A source path with alternative destination leaves must not be promoted as grounded Runner navigation",
    )
    require(
        not ai_skill_service._baseline_navigation_matches_landing_source(
            trusted_prefix,
            {"assertionTarget": "页面已离开「纸质发票」页并出现云端发票授权页"},
            "会员服务",
        ),
        "A sibling baseline must not be joined to an AI tail that explicitly names a different source leaf",
    )
    bounded_applied = ai_skill_service.apply_executable_yaml_plan_to_payload(
        bounded_convergence_payload,
        bounded_plan,
    )
    bounded_by_id = {item.get("case_id"): item for item in bounded_applied.get("cases") or []}
    require(
        all(
            bounded_by_id[f"TC-R{index:02d}"].get("executionLevel") == "executable"
            and bounded_by_id[f"TC-R{index:02d}"].get("ai_case_plan", {}).get("boundedConvergence", {}).get("sourceCaseId")
            and bounded_by_id[f"TC-R{index:02d}"].get("ai_case_plan", {}).get("batch") == "remaining"
            and not bounded_by_id[f"TC-R{index:02d}"].get("smoke")
            and "校验「发票」入口可见" not in bounded_by_id[f"TC-R{index:02d}"].get("steps", [])
            and "授权页" in (bounded_by_id[f"TC-R{index:02d}"].get("assertions") or [""])[0]
            and any(
                term in (bounded_by_id[f"TC-R{index:02d}"].get("assertions") or [""])[0]
                for term in ("无Crash", "无长时间白屏")
            )
            for index in (1, 2)
        )
        and bounded_applied.get("review", {}).get("executable_yaml_plan", {}).get("bounded_convergence_override_count") == 2,
        "A contradictory AI downgrade must retain the safe upstream AI landing candidates as auditable remaining cases",
    )
    require(
        ai_skill_service.executable_yaml_portfolio_audit(
            bounded_applied,
            {"min_automation_cases": 2},
        ).get("ok"),
        "Grounded bounded landing cases must close the explicit reachability gaps without a synthetic case-count floor",
    )
    for index in (1, 2):
        require(
            yaml_service._case_manual_block_reason(bounded_by_id[f"TC-R{index:02d}"]) == "",
            "A source-grounded bounded landing check must survive the deterministic Runner eligibility gate",
        )
    slash_state_bounded = json.loads(json.dumps(bounded_by_id["TC-R01"], ensure_ascii=False))
    slash_state_bounded["assertions"] = [
        "已离开来源页，出现授权页/文件页/WebView，且无白屏或崩溃",
    ]
    require(
        yaml_service._case_manual_block_reason(slash_state_bounded) == "",
        "Equivalent slash-separated file-page and WebView terms must remain a bounded first-screen result",
    )
    branded_state_bounded = json.loads(json.dumps(bounded_by_id["TC-R01"], ensure_ascii=False))
    branded_state_bounded["steps"] = [
        "等待会员服务页面稳定",
        "点击「云端发票」入口",
        "断言页面已离开会员服务，出现「云端发票」页面区域或授权页，且无白屏或崩溃",
    ]
    branded_state_bounded["assertions"] = [
        "页面已离开会员服务，出现「云端发票」页面区域或授权页，且无白屏或崩溃",
    ]
    require(
        yaml_service._case_manual_block_reason(branded_state_bounded) == "",
        "A visible target-branded landing state plus one explicit alternate must remain Runner-verifiable",
    )
    deep_bounded = json.loads(json.dumps(bounded_by_id["TC-R01"], ensure_ascii=False))
    deep_bounded["steps"].extend(["点击同意授权", "输入账号和验证码", "选择文件"])
    require(
        yaml_service._case_manual_block_reason(deep_bounded),
        "Bounded convergence must never admit credential, authorization-confirmation, or file-selection actions",
    )

    branch_fallback_payload = json.loads(json.dumps(bounded_applied, ensure_ascii=False))
    branch_fallback_payload["analysis"]["requirement_points"].append(
        "REQ-003 售后服务：校验发票入口可见；校验发票入口与当前页面同级入口的层级和位置关系；"
        "校验发票入口使用需求约定的可见文案；点击发票入口并校验目标页面稳定可达"
    )
    third_branch_checks = [
        {
            "id": f"REQ-003-CHECK-{index:02d}",
            "requirementId": "REQ-003",
            "branch": "售后服务",
            "kind": kind,
            "text": text,
        }
        for index, (kind, text) in enumerate((
            ("visibility", "校验发票入口可见"),
            ("relation", "校验发票入口与当前页面同级入口的层级和位置关系"),
            ("copy", "校验发票入口使用需求约定的可见文案"),
            ("reachability", "点击发票入口并校验目标页面稳定可达"),
        ), start=1)
    ]
    branch_fallback_payload["analysis"]["requirement_acceptance_checks"].extend(third_branch_checks)
    branch_fallback_payload["cases"].append({
        "case_id": "TC-DUP",
        "title": "订单管理发票入口冗余同级检查",
        "executionLevel": "needs_review",
        "requirementRefs": ["REQ-001"],
        "steps": ["进入订单管理", "等待发票入口和订单入口同时可见"],
        "assertions": ["发票入口和订单入口同级展示"],
    })
    branch_fallback_payload["manual_cases"].extend([{
        "case_id": "TC-S03",
        "title": "售后服务发票入口展示",
        "executionLevel": "manual",
        "originExecutionLevel": "automatic",
        "requirementRefs": ["REQ-003"],
        "preconditions": "App 首页，用户已登录",
        "steps": ["进入售后服务", "等待发票入口可见"],
        "assertions": ["售后服务页面展示文案为发票的入口"],
        "ai_case_classification": {"level": "manual", "originLevel": "automatic"},
    }, {
        "case_id": "TC-R03",
        "title": "售后服务发票入口首个落地页",
        "executionLevel": "manual",
        "originExecutionLevel": "automatic",
        "requirementRefs": ["REQ-003"],
        "preconditions": "已进入售后服务页面，发票入口可见",
        "steps": [
            "进入售后服务",
            "点击发票入口",
            "等待授权页、登录页或内容列表任一合法页面可见",
        ],
        "assertions": ["授权页、登录页或内容列表任一合法页面可见，且无白屏或崩溃"],
        "ai_case_classification": {"level": "manual", "originLevel": "automatic"},
    }])
    branch_fallback_audit = ai_skill_service.executable_yaml_portfolio_audit(
        branch_fallback_payload,
        {"min_automation_cases": 5},
    )
    fallback_requests = []
    old_run_ai_skill = ai_skill_service.run_ai_skill
    try:
        def fake_branch_fallback_planner(skill_name, request, **_kwargs):
            require(skill_name == "executable_yaml_planner", "Unexpected AI skill in branch fallback replay")
            fallback_requests.append(request)
            executable = []
            manual = []
            for candidate in request.get("cases") or []:
                case_id = candidate.get("case_id")
                if case_id == "TC-DUP":
                    continue
                if candidate.get("currentLevel") == "executable":
                    executable.append({
                        "caseId": case_id,
                        "baselineId": "base-entry-nav",
                        "precondition": "App 首页",
                        "flow": candidate.get("steps") or [],
                        "assertionTarget": (candidate.get("assertions") or [""])[0],
                        "requirementRefs": candidate.get("requirementRefs") or [],
                        "executableReason": "保留已通过的可信来源页路径",
                        "batch": "smoke",
                    })
                else:
                    manual.append({
                        "caseId": case_id,
                        "reason": "目标页缺少历史执行结果，模型建议人工确认",
                        "requirementRefs": candidate.get("requirementRefs") or [],
                    })
            return {
                "cases": executable,
                "needs_review_cases": [],
                "draft_cases": [],
                "manual_cases": manual,
                "review": {"planning_reason": "模拟线上先降级、再漏回一个冗余候选"},
            }

        ai_skill_service.run_ai_skill = fake_branch_fallback_planner
        branch_fallback_plan = ai_skill_service.call_skill_executable_yaml_planner(
            "新增发票入口",
            "会员服务",
            branch_fallback_payload,
            [{
                "id": "base-entry-nav",
                "title": "订单与优惠券入口导航",
                "sourceKind": "verified_execution",
                "verificationStatus": "execution_success",
            }, {
                "id": "base-service-nav",
                "title": "售后服务入口导航",
                "aiSelectedBranchName": "会员服务-售后服务",
                "sourceKind": "verified_execution",
                "verificationStatus": "execution_success",
                "businessPath": "首页 -> 售后服务",
            }],
            {"smokeCount": 3},
            planning_context={
                "pass": "coverage_convergence",
                "portfolioAudit": branch_fallback_audit,
            },
        )
    finally:
        ai_skill_service.run_ai_skill = old_run_ai_skill
    fallback_request_by_id = {
        item.get("case_id"): item for item in fallback_requests[0].get("cases") or []
    }
    require(
        fallback_request_by_id["TC-R03"].get("originLevel") == "automatic"
        and fallback_request_by_id["TC-R03"].get("currentLevel") == "manual"
        and fallback_request_by_id["TC-R03"].get("convergenceEvidence", {}).get("sourceCaseId") == "TC-S03"
        and fallback_request_by_id["TC-R03"].get("convergenceEvidence", {}).get("baselineId") == "base-service-nav"
        and "REQ-003-CHECK-02" in fallback_request_by_id["TC-R03"].get("convergenceEvidence", {}).get("acceptanceCheckIds", []),
        "A first-pass AI downgrade must retain automatic provenance and use the trusted same-branch baseline for missing source-page evidence",
    )
    branch_fallback_applied = ai_skill_service.apply_executable_yaml_plan_to_payload(
        branch_fallback_payload,
        branch_fallback_plan,
    )
    branch_fallback_by_id = {
        item.get("case_id"): item for item in branch_fallback_applied.get("cases") or []
    }
    require(
        branch_fallback_by_id["TC-R03"].get("executionLevel") == "executable"
        and branch_fallback_plan.get("verifiedBaselineIds") == ["base-entry-nav", "base-service-nav"]
        and branch_fallback_by_id["TC-R03"].get("ai_case_plan", {}).get("baselineVerified") is True
        and "同级入口" in " ".join(branch_fallback_by_id["TC-R03"].get("steps") or [])
        and yaml_service._case_manual_block_reason(branch_fallback_by_id["TC-R03"]) == ""
        and ai_skill_service.executable_yaml_portfolio_audit(
            branch_fallback_applied,
            {"min_automation_cases": 5},
        ).get("ok")
        and any(item.get("case_id") == "TC-DUP" for item in branch_fallback_applied.get("manual_cases") or [])
        and branch_fallback_applied.get("review", {}).get("executable_yaml_plan", {}).get("redundant_unmentioned_manualized_count") == 1,
        "Same-branch bounded evidence must close every explicit dimension while an omitted redundant candidate is preserved as manual, never auto-promoted",
    )
    source_ui_payload = {
        "analysis": {
            "requirement_points": ["REQ-009 售后服务：展示发票入口、文案正确且与同页入口同级"],
            "requirement_acceptance_checks": [
                {"id": "REQ-009-CHECK-01", "requirementId": "REQ-009", "branch": "售后服务", "kind": "visibility", "text": "校验发票入口可见"},
                {"id": "REQ-009-CHECK-02", "requirementId": "REQ-009", "branch": "售后服务", "kind": "relation", "text": "校验发票入口与当前页面其它入口同级展示"},
                {"id": "REQ-009-CHECK-03", "requirementId": "REQ-009", "branch": "售后服务", "kind": "copy", "text": "校验发票入口使用需求约定的可见文案"},
            ],
        },
        "cases": [{
            "case_id": "TC-SOURCE-UI",
            "title": "售后服务发票入口展示",
            "executionLevel": "manual",
            "originExecutionLevel": "automatic",
            "requirementRefs": ["REQ-009 售后服务：展示发票入口、文案正确且与同页入口同级"],
            "preconditions": ["App 首页，用户已登录"],
            "steps": ["进入售后服务", "等待发票入口可见"],
            "assertions": ["发票入口可见"],
        }],
        "manual_cases": [],
    }
    source_ui_audit = ai_skill_service.executable_yaml_portfolio_audit(source_ui_payload, {})
    source_ui_record = {
        "raw": source_ui_payload["cases"][0],
        "compact": ai_skill_service._compact_case_for_plan(
            source_ui_payload["cases"][0],
            0,
            origin_level="automatic",
        ),
    }
    source_ui_evidence = ai_skill_service._bounded_convergence_evidence(
        source_ui_payload,
        [source_ui_record],
        source_ui_audit,
        selected_baselines=[{
            "id": "base-source-ui",
            "selectedBranchName": "售后服务",
            "sourceKind": "verified_execution",
            "verificationStatus": "execution_success",
            "snippet": "- aiTap: 会员服务\n- aiWaitFor: 等待会员服务页面加载完成\n- aiTap: 售后服务\n- aiWaitFor: 等待售后服务页面加载完成\n- aiTap: 选择历史订单",
        }],
    )
    source_ui_plan = {
        "authoritative": True,
        "allowedBaselineIds": ["base-source-ui"],
        "requirementPoints": source_ui_payload["analysis"]["requirement_points"],
        "planningContext": {"pass": "coverage_convergence"},
        "focusedCandidateIds": ["TC-SOURCE-UI"],
        "candidateEligibilityById": source_ui_evidence,
        "cases": [{
            "caseId": "TC-SOURCE-UI",
            "reason": "模型保留当前可执行分类，但没有主动补写缺失的同级与文案断言",
        }],
        "manual_cases": [],
    }
    source_ui_applied = ai_skill_service.apply_executable_yaml_plan_to_payload(
        source_ui_payload,
        source_ui_plan,
    )
    source_ui_case = source_ui_applied.get("cases", [{}])[0]
    require(
        source_ui_evidence.get("TC-SOURCE-UI", {}).get("kind") == "source_ui_assertion"
        and set(source_ui_evidence["TC-SOURCE-UI"].get("acceptanceCheckIds") or []) == {
            "REQ-009-CHECK-01", "REQ-009-CHECK-02", "REQ-009-CHECK-03",
        }
        and source_ui_case.get("executionLevel") == "executable"
        and source_ui_case.get("ai_case_plan", {}).get("batch") == "remaining"
        and ai_skill_service.executable_yaml_portfolio_audit(source_ui_applied, {}).get("ok"),
        "A trusted same-branch action path plus the explicit UI contract must let Runner verify source-page visibility/copy/relation without requiring a sibling Figma frame",
    )
    inferred_source_ui_payload = {
        "analysis": {
            "requirement_points": [
                "REQ-030 扫描复印：校验百度网盘入口与当前页面同级入口的层级和位置关系",
                "REQ-031 扫描复印：点击百度网盘入口并校验目标页面稳定可达",
            ],
            "requirement_acceptance_checks": [
                {
                    "id": "REQ-030-CHECK-01",
                    "requirementId": "REQ-030",
                    "branch": "扫描复印",
                    "kind": "relation",
                    "text": "校验百度网盘入口与当前页面同级入口的层级和位置关系",
                },
                {
                    "id": "REQ-031-CHECK-01",
                    "requirementId": "REQ-031",
                    "branch": "扫描复印",
                    "kind": "reachability",
                    "text": "点击百度网盘入口并校验目标页面稳定可达",
                },
            ],
        },
        "cases": [{
            "case_id": "TC-SCAN-REACH",
            "title": "扫描复印百度网盘入口跳转",
            "executionLevel": "executable",
            "originExecutionLevel": "automatic",
            "requirementRefs": ["REQ-031 扫描复印：点击百度网盘入口并校验目标页面稳定可达"],
            "preconditions": ["App 首页"],
            "steps": [
                "点击「扫描复印」入口",
                "等待扫描复印页加载完成",
                "点击「百度网盘」入口",
                "等待百度网盘授权页或登录页稳定可见，无白屏、无崩溃",
            ],
            "assertions": ["百度网盘授权页或登录页稳定可达，无白屏、无崩溃"],
            "ai_case_plan": {"baselineGrounded": True, "pathPlanApplied": True},
        }],
        "manual_cases": [{
            "case_id": "MC-SCAN-REL",
            "title": "扫描复印页百度网盘入口同级关系人工确认",
            "executionLevel": "manual",
            "originExecutionLevel": "manual",
            "steps": [
                "进入App首页",
                "点击「扫描复印」入口",
                "观察页面导入区域",
                "确认「百度网盘」入口与当前页面其它入口同级并列",
            ],
            "expected_result": "百度网盘入口与产品设计稿中的同级入口布局一致",
        }, {
            "case_id": "MC-WRONG-BRANCH",
            "title": "文档打印页百度网盘入口同级关系人工确认",
            "executionLevel": "manual",
            "originExecutionLevel": "manual",
            "steps": ["点击「文档打印」入口", "确认「百度网盘」入口与其它入口同级并列"],
            "expected_result": "文档打印页百度网盘入口位置关系正确",
        }],
    }
    inferred_source_ui_audit = ai_skill_service.executable_yaml_portfolio_audit(
        inferred_source_ui_payload,
        {"min_automation_cases": 1},
    )
    inferred_source_auto_records = [{
        "raw": item,
        "compact": ai_skill_service._compact_case_for_plan(item, index, origin_level="automatic"),
    } for index, item in enumerate(inferred_source_ui_payload["cases"])]
    inferred_source_manual_records = [{
        "raw": item,
        "compact": ai_skill_service._compact_case_for_plan(item, index, origin_level="manual"),
    } for index, item in enumerate(inferred_source_ui_payload["manual_cases"])]
    inferred_source_baseline = ai_skill_service._compact_baseline_candidate({
        "id": "base-scan-nav",
        "title": "证件扫描",
        "aiSelectedBranchName": "扫描复印",
        "sourceKind": "verified_execution",
        "verificationStatus": "execution_success",
        "snippet": (
            "# baseline.start_page: App 首页\n"
            "- aiTap: 扫描复印 icon\n"
            "- aiTap: 证件扫描\n"
            "- aiTap: 立即使用\n"
            "- aiTap: 相册导入"
        ),
    })
    (
        inferred_source_automatic,
        inferred_source_manual,
        inferred_source_context,
        inferred_source_focus,
    ) = ai_skill_service._focus_executable_convergence_candidates(
        inferred_source_ui_payload,
        inferred_source_auto_records,
        inferred_source_manual_records,
        {
            "pass": "coverage_convergence",
            "portfolioAudit": inferred_source_ui_audit,
        },
        selected_baselines=[inferred_source_baseline],
    )
    inferred_source_evidence = {
        item.get("case_id"): item.get("convergenceEvidence")
        for item in inferred_source_automatic + inferred_source_manual
        if item.get("convergenceEvidence")
    }
    inferred_relation_evidence = inferred_source_evidence.get("MC-SCAN-REL") or {}
    inferred_source_plan = {
        "authoritative": True,
        "allowedBaselineIds": ["base-scan-nav"],
        "verifiedBaselineIds": ["base-scan-nav"],
        "requirementPoints": inferred_source_ui_payload["analysis"]["requirement_points"],
        "planningContext": inferred_source_context,
        "focusedCandidateIds": inferred_source_focus.get("focusedCandidateIds"),
        "candidateEligibilityById": inferred_source_evidence,
        "cases": [],
        "manual_cases": [{
            "caseId": "MC-SCAN-REL",
            "reason": "模型因缺少扫描页设计 Frame 建议人工确认",
        }],
    }
    inferred_source_applied = ai_skill_service.apply_executable_yaml_plan_to_payload(
        inferred_source_ui_payload,
        inferred_source_plan,
    )
    inferred_source_case = next(
        item for item in inferred_source_applied.get("cases") or []
        if item.get("case_id") == "MC-SCAN-REL"
    )
    inferred_relation_flow = "\n".join(inferred_relation_evidence.get("flow") or [])
    require(
        inferred_source_focus.get("focusedCandidateIds") == ["MC-SCAN-REL"]
        and inferred_source_focus.get("acceptanceCheckCandidateIds") == {
            "REQ-030-CHECK-01": ["MC-SCAN-REL"],
        }
        and inferred_relation_evidence.get("requirementRefsInferredFromAcceptanceIntent") is True
        and inferred_relation_evidence.get("acceptanceCheckIds") == ["REQ-030-CHECK-01"]
        and "扫描复印 icon" in inferred_relation_flow
        and "证件扫描" not in inferred_relation_flow
        and "立即使用" not in inferred_relation_flow
        and "观察页面" not in inferred_relation_flow
        and "设计稿" not in str(inferred_relation_evidence.get("assertionTarget") or "")
        and "当前页面同级入口的层级和位置关系" in str(
            inferred_relation_evidence.get("assertionTarget") or ""
        )
        and "MC-WRONG-BRANCH" not in inferred_source_evidence
        and inferred_source_case.get("executionLevel") == "executable"
        and ai_skill_service.executable_yaml_portfolio_audit(
            inferred_source_applied,
            {"min_automation_cases": 1},
        ).get("ok"),
        "A no-ref AI candidate may inherit one canonical requirement only from exact branch/target/acceptance intent; convergence must keep the request focused and replace a historical deep leaf without claiming absent visual evidence",
    )
    source_ui_merge_payload = json.loads(json.dumps(source_ui_payload, ensure_ascii=False))
    source_ui_merge_case = source_ui_merge_payload["cases"][0]
    source_ui_merge_case["executionLevel"] = "executable"
    source_ui_merge_case["expected_result"] = "售后服务页面展示文案为‘发票’的入口，无缺失"
    source_ui_merge_case["assertions"] = ["售后服务页面展示文案为‘发票’的入口，无缺失"]
    source_ui_merge_payload["analysis"]["requirement_acceptance_checks"][1]["text"] = (
        "校验发票入口与当前页面同级入口的层级和位置关系"
    )
    source_ui_merge_audit = ai_skill_service.executable_yaml_portfolio_audit(
        source_ui_merge_payload,
        {},
    )
    require(
        [
            item.get("id")
            for item in source_ui_merge_audit.get("missingAcceptanceChecks") or []
        ] == ["REQ-009-CHECK-02"],
        "The source candidate fixture must already cover visibility/copy and lack only the relation dimension",
    )
    source_ui_merge_record = {
        "raw": source_ui_merge_case,
        "compact": ai_skill_service._compact_case_for_plan(
            source_ui_merge_case,
            0,
            origin_level="automatic",
        ),
    }
    source_ui_merge_evidence = ai_skill_service._bounded_convergence_evidence(
        source_ui_merge_payload,
        [source_ui_merge_record],
        source_ui_merge_audit,
        selected_baselines=[{
            "id": "base-source-ui",
            "selectedBranchName": "售后服务",
            "sourceKind": "verified_execution",
            "verificationStatus": "execution_success",
            "snippet": "- aiTap: 会员服务\n- aiWaitFor: 等待会员服务页面加载完成\n- aiTap: 售后服务\n- aiWaitFor: 等待售后服务页面加载完成\n- aiTap: 选择历史订单",
        }],
    )
    source_ui_merge_plan = {
        **source_ui_plan,
        "candidateEligibilityById": source_ui_merge_evidence,
    }
    source_ui_merge_applied = ai_skill_service.apply_executable_yaml_plan_to_payload(
        source_ui_merge_payload,
        source_ui_merge_plan,
    )
    source_ui_merge_contract = source_ui_merge_evidence.get(
        "TC-SOURCE-UI", {}
    ).get("assertionTarget", "")
    require(
        source_ui_merge_evidence.get("TC-SOURCE-UI", {}).get("acceptanceCheckIds")
        == ["REQ-009-CHECK-02"]
        and "展示文案为‘发票’的入口" in source_ui_merge_contract
        and "同级入口的层级和位置关系" in source_ui_merge_contract
        and ai_skill_service.executable_yaml_portfolio_audit(
            source_ui_merge_applied,
            {},
        ).get("ok"),
        "Adding trusted source-page evidence must merge with an executable candidate's existing visible-copy assertion instead of exchanging one covered dimension for another",
    )
    manual_branch_payload = {
        "analysis": {
            "requirement_points": [
                "REQ-021 资料归档：企业云盘入口可见、同级、文案正确且点击后首屏可达",
            ],
            "requirement_acceptance_checks": [
                {"id": "REQ-021-CHECK-01", "requirementId": "REQ-021", "branch": "资料归档", "kind": "visibility", "text": "校验企业云盘入口可见"},
                {"id": "REQ-021-CHECK-02", "requirementId": "REQ-021", "branch": "资料归档", "kind": "relation", "text": "校验企业云盘入口与当前页面其它入口同级展示"},
                {"id": "REQ-021-CHECK-03", "requirementId": "REQ-021", "branch": "资料归档", "kind": "copy", "text": "校验企业云盘入口使用需求约定的可见文案"},
                {"id": "REQ-021-CHECK-04", "requirementId": "REQ-021", "branch": "资料归档", "kind": "reachability", "text": "点击企业云盘入口并校验目标页面稳定可达"},
            ],
        },
        "cases": [{
            "case_id": "TC-SIBLING-LANDING",
            "title": "文档中心企业云盘首屏",
            "executionLevel": "executable",
            "originExecutionLevel": "automatic",
            "requirementRefs": ["REQ-001 文档中心企业云盘入口点击可达"],
            "preconditions": ["App 首页"],
            "steps": [
                "进入文档中心",
                "点击企业云盘入口",
                "等待企业云盘内容列表页加载完成，显示文件名及继续按钮",
            ],
            "assertions": ["成功唤起企业云盘内容列表页，页面无崩溃、无白屏"],
        }],
        "manual_cases": [{
            "case_id": "MC-BRANCH-SOURCE",
            "title": "资料归档企业云盘入口展示",
            "executionLevel": "manual",
            "originExecutionLevel": "manual",
            "requirementRefs": ["REQ-021 资料归档：企业云盘入口可见、同级、文案正确且点击后首屏可达"],
            "steps": [
                "启动App并登录",
                "进入首页 → 资料归档页",
                "观察企业云盘入口是否可见",
                "确认企业云盘入口文案及同级关系",
                "记录入口展示结果",
            ],
            "expected_result": "企业云盘入口在资料归档页可见，文案正确且与其它入口同级并列",
        }, {
            "case_id": "MC-BRANCH-LANDING",
            "title": "资料归档企业云盘入口点击",
            "executionLevel": "manual",
            "originExecutionLevel": "manual",
            "requirementRefs": ["REQ-021 资料归档：企业云盘入口可见、同级、文案正确且点击后首屏可达"],
            "steps": [
                "进入资料归档页",
                "点击企业云盘入口",
                "观察页面跳转或授权窗口弹出情况",
                "确认无崩溃、无长时间白屏",
                "记录跳转结果",
            ],
            "expected_result": "操作企业云盘入口后页面跳转或弹出授权窗口，无崩溃或长时间白屏",
        }],
    }
    # Put the tail-only candidate first so convergence cannot pass by relying on
    # the original manual-candidate order.
    manual_branch_payload["manual_cases"].reverse()
    manual_branch_audit = ai_skill_service.executable_yaml_portfolio_audit(
        manual_branch_payload,
        {"min_automation_cases": 1},
    )
    manual_branch_automatic_records = [{
        "raw": item,
        "compact": ai_skill_service._compact_case_for_plan(item, index, origin_level="automatic"),
    } for index, item in enumerate(manual_branch_payload["cases"])]
    manual_branch_manual_records = [{
        "raw": item,
        "compact": ai_skill_service._compact_case_for_plan(item, index, origin_level="manual"),
    } for index, item in enumerate(manual_branch_payload["manual_cases"])]
    manual_branch_baseline = ai_skill_service._compact_baseline_candidate({
        "id": "base-archive-nav",
        "title": "资料归档导入",
        "aiSelectedBranchName": "资料归档",
        "sourceKind": "verified_execution",
        "verificationStatus": "execution_success",
        "snippet": (
            "# baseline.start_page: App 首页\n"
            "- aiTap: 资料服务\n"
            "- aiWaitFor: 等待资料服务页面加载完成\n"
            "- aiTap: 资料归档\n"
            "- aiWaitFor: 等待资料归档页面加载完成\n"
            "- aiTap: 相册导入"
        ),
    })
    manual_branch_evidence = ai_skill_service._bounded_convergence_evidence(
        manual_branch_payload,
        manual_branch_automatic_records,
        manual_branch_audit,
        selected_baselines=[manual_branch_baseline],
        manual_records=manual_branch_manual_records,
    )
    manual_branch_source_evidence = manual_branch_evidence.get("MC-BRANCH-SOURCE") or {}
    _, manual_branch_focused_manual, _, manual_branch_focus = (
        ai_skill_service._focus_executable_convergence_candidates(
            manual_branch_payload,
            manual_branch_automatic_records,
            manual_branch_manual_records,
            {
                "pass": "coverage_convergence",
                "portfolioAudit": manual_branch_audit,
            },
            selected_baselines=[manual_branch_baseline],
        )
    )
    manual_branch_focused_by_id = {
        item.get("case_id"): item for item in manual_branch_focused_manual
    }
    manual_branch_plan = {
        "authoritative": True,
        "allowedBaselineIds": ["base-archive-nav"],
        "verifiedBaselineIds": ["base-archive-nav"],
        "requirementPoints": manual_branch_payload["analysis"]["requirement_points"],
        "planningContext": {"pass": "coverage_convergence"},
        "focusedCandidateIds": ["MC-BRANCH-SOURCE"],
        "candidateEligibilityById": manual_branch_evidence,
        "cases": [],
        "manual_cases": [{
            "caseId": "MC-BRANCH-SOURCE",
            "reason": "模型仍因缺少对应设计 Frame 建议人工确认",
        }],
    }
    manual_branch_applied = ai_skill_service.apply_executable_yaml_plan_to_payload(
        manual_branch_payload,
        manual_branch_plan,
    )
    manual_branch_case = next(
        item for item in manual_branch_applied.get("cases") or []
        if item.get("case_id") == "MC-BRANCH-SOURCE"
    )
    require(
        manual_branch_source_evidence.get("kind") == "bounded_landing"
        and manual_branch_source_evidence.get("manualPromotionEligible") is True
        and manual_branch_source_evidence.get("precondition") == "App 首页"
        and manual_branch_focused_by_id["MC-BRANCH-SOURCE"].get("convergenceEvidence", {}).get("eligible") is True
        and {
            item.get("id")
            for item in manual_branch_focused_by_id["MC-BRANCH-SOURCE"].get("requiredAcceptanceChecks") or []
        } == {
            "REQ-021-CHECK-01", "REQ-021-CHECK-02", "REQ-021-CHECK-03", "REQ-021-CHECK-04",
        }
        and "MC-BRANCH-SOURCE" in manual_branch_focus.get("boundedEvidenceCandidateIds", [])
        and set(manual_branch_source_evidence.get("acceptanceCheckIds") or []) == {
            "REQ-021-CHECK-01", "REQ-021-CHECK-02", "REQ-021-CHECK-03", "REQ-021-CHECK-04",
        }
        and "MC-BRANCH-LANDING" in set(
            manual_branch_source_evidence.get("landingEvidenceCaseIds") or []
        )
        and "已离开来源页" in str(manual_branch_source_evidence.get("assertionTarget") or "")
        and manual_branch_case.get("executionLevel") == "executable"
        and manual_branch_case.get("ai_case_plan", {}).get("batch") == "remaining"
        and yaml_service._case_manual_block_reason(manual_branch_case) == ""
        and ai_skill_service.executable_yaml_portfolio_audit(
            manual_branch_applied,
            {"min_automation_cases": 1},
        ).get("ok"),
        "A required branch that AI initially marked manual must reuse its verified navigation baseline and same-target AI first-screen evidence instead of claiming that no baseline exists",
    )
    sibling_tail_payload = {
        "analysis": {
            "requirement_points": [
                "REQ-003 扫描复印：校验百度网盘入口可见、同级、文案正确且点击后稳定可达",
            ],
            "requirement_acceptance_checks": [
                {"id": "REQ-003-CHECK-01", "requirementId": "REQ-003", "branch": "扫描复印", "kind": "visibility", "text": "校验百度网盘入口可见"},
                {"id": "REQ-003-CHECK-02", "requirementId": "REQ-003", "branch": "扫描复印", "kind": "relation", "text": "校验百度网盘入口与当前页面同级入口的层级和位置关系"},
                {"id": "REQ-003-CHECK-03", "requirementId": "REQ-003", "branch": "扫描复印", "kind": "copy", "text": "校验百度网盘入口使用需求约定的可见文案"},
                {"id": "REQ-003-CHECK-04", "requirementId": "REQ-003", "branch": "扫描复印", "kind": "reachability", "text": "点击「百度网盘」入口，并校验「文件列表」按钮所在目标页面稳定可达"},
            ],
        },
        "cases": [{
            "case_id": "TC-DOCUMENT-LANDING",
            "title": "文档打印百度网盘入口点击后首屏",
            "executionLevel": "executable",
            "originExecutionLevel": "automatic",
            "requirementRefs": ["REQ-001 文档打印：点击百度网盘入口并校验目标页面稳定可达"],
            "steps": [
                "进入文档打印页",
                "点击「百度网盘」入口",
                "等待百度网盘授权页或文件列表页稳定可见，无崩溃、无白屏",
            ],
            "assertions": ["百度网盘授权页或文件列表页稳定可见，无崩溃、无白屏"],
            "ai_case_plan": {
                "baselineId": "base-document-nav",
                "baselineGrounded": True,
                "baselineVerified": True,
                "pathPlanApplied": True,
            },
        }],
        "manual_cases": [{
            "case_id": "MC-SCAN-COMBINED",
            "title": "扫描复印页百度网盘入口展示及跳转",
            "executionLevel": "manual",
            "originExecutionLevel": "manual",
            "requirementRefs": [
                "REQ-003 扫描复印：校验百度网盘入口可见、同级、文案正确且点击后稳定可达",
            ],
            "preconditions": ["App 首页"],
            "steps": [
                "启动App并登录",
                "点击首页「扫描复印」入口",
                "等待页面加载完成",
                "查找页面中是否有「百度网盘」入口",
                "若存在，检查文案是否为“百度网盘”，位置是否与同级入口并列",
                "若存在，点击入口，观察是否跳转至百度网盘相关页面",
                "若不存在，记录UI缺失缺陷",
            ],
            "expected_result": (
                "如果百度网盘入口可见，则文案必须严格为“百度网盘”；"
                "若UI已实现，则百度网盘入口可见、文案正确、跳转稳定；"
                "若未实现，则记录UI缺失缺陷"
            ),
        }],
    }
    sibling_tail_audit = ai_skill_service.executable_yaml_portfolio_audit(
        sibling_tail_payload,
        {"min_automation_cases": 1},
    )
    sibling_tail_automatic_records = [{
        "raw": item,
        "compact": ai_skill_service._compact_case_for_plan(item, index, origin_level="automatic"),
    } for index, item in enumerate(sibling_tail_payload["cases"])]
    sibling_tail_manual_records = [{
        "raw": item,
        "compact": ai_skill_service._compact_case_for_plan(item, index, origin_level="manual"),
    } for index, item in enumerate(sibling_tail_payload["manual_cases"])]
    sibling_tail_baseline = ai_skill_service._compact_baseline_candidate({
        "id": "base-scan-nav",
        "title": "证件扫描",
        "aiSelectedBranchName": "扫描复印",
        "sourceKind": "verified_execution",
        "verificationStatus": "execution_success",
        "snippet": (
            "# baseline.start_page: App 首页\n"
            "- aiTap: 扫描复印 icon\n"
            "- aiWaitFor: 等待扫描复印页面加载完成\n"
            "- aiTap: 证件扫描\n"
            "- aiTap: 立即使用\n"
            "- aiTap: 相册导入"
        ),
    })
    sibling_tail_evidence = ai_skill_service._bounded_convergence_evidence(
        sibling_tail_payload,
        sibling_tail_automatic_records,
        sibling_tail_audit,
        selected_baselines=[sibling_tail_baseline],
        manual_records=sibling_tail_manual_records,
    )
    sibling_tail_case_evidence = sibling_tail_evidence.get("MC-SCAN-COMBINED") or {}
    sibling_source_assertion = next(
        (
            step for step in sibling_tail_case_evidence.get("flow") or []
            if str(step).startswith("等待并校验")
        ),
        "",
    )
    require(
        sibling_tail_case_evidence.get("kind") == "bounded_landing"
        and sibling_tail_case_evidence.get("sharedTargetTailBoundToBranchSource") is True
        and set(sibling_tail_case_evidence.get("acceptanceCheckIds") or []) == {
            "REQ-003-CHECK-01", "REQ-003-CHECK-02", "REQ-003-CHECK-03", "REQ-003-CHECK-04",
        }
        and sibling_tail_case_evidence.get("landingEvidenceCaseIds") == ["TC-DOCUMENT-LANDING"]
        and "扫描复印" in " ".join(sibling_tail_case_evidence.get("flow") or [])
        and "文档打印" not in " ".join(sibling_tail_case_evidence.get("flow") or [])
        and "文案必须严格为“百度网盘”" in " ".join(sibling_tail_case_evidence.get("flow") or [])
        and "跳转稳定" not in sibling_source_assertion
        and "若UI已实现" not in " ".join(sibling_tail_case_evidence.get("flow") or [])
        and "记录UI缺失缺陷" not in " ".join(sibling_tail_case_evidence.get("flow") or []),
        "A current branch with trusted source-page evidence may reuse only the bounded landing tail from an executable sibling branch when the visible target is identical, without leaking manual alternatives into Runner steps",
    )
    sibling_tail_missing_precondition_payload = json.loads(json.dumps(sibling_tail_payload, ensure_ascii=False))
    sibling_tail_missing_precondition_payload["manual_cases"] = []
    sibling_tail_missing_precondition_payload["cases"].append({
        "case_id": "TC-SCAN-SOURCE",
        "title": "扫描复印页百度网盘入口展示",
        "executionLevel": "executable",
        "originExecutionLevel": "automatic",
        "coverage": "REQ-003 扫描复印：校验百度网盘入口可见、同级、文案正确",
        "steps": [
            "回到首页",
            "点击「扫描复印」入口",
            "等待扫描复印页面加载完成",
            "等待「百度网盘」入口可见",
        ],
        "assertions": ["「百度网盘」入口文案为“百度网盘”，可见"],
        "ai_case_plan": {
            "baselineId": "base-scan-nav",
            "baselineGrounded": True,
            "baselineVerified": True,
            "pathPlanApplied": True,
        },
    })
    sibling_tail_missing_precondition_audit = {
        "missingAcceptanceChecks": sibling_tail_payload["analysis"]["requirement_acceptance_checks"],
        "unresolvedAutomaticCaseIds": ["TC-SCAN-SOURCE"],
        "executableCaseIds": ["TC-DOCUMENT-LANDING", "TC-SCAN-SOURCE"],
        "executableCount": 2,
        "targetExecutableCount": 5,
    }
    sibling_tail_missing_precondition_records = [{
        "raw": item,
        "compact": ai_skill_service._compact_case_for_plan(item, index, origin_level="automatic"),
    } for index, item in enumerate(sibling_tail_missing_precondition_payload["cases"])]
    sibling_tail_missing_precondition_evidence = ai_skill_service._bounded_convergence_evidence(
        sibling_tail_missing_precondition_payload,
        sibling_tail_missing_precondition_records,
        sibling_tail_missing_precondition_audit,
        selected_baselines=[sibling_tail_baseline],
        manual_records=[],
    ).get("TC-SCAN-SOURCE") or {}
    require(
        sibling_tail_missing_precondition_evidence.get("kind") == "bounded_landing"
        and sibling_tail_missing_precondition_evidence.get("precondition") == "App 首页"
        and sibling_tail_missing_precondition_evidence.get("sourceCaseId") == "TC-SCAN-SOURCE"
        and sibling_tail_missing_precondition_evidence.get("tailSourceCaseId") == "TC-DOCUMENT-LANDING"
        and "REQ-003-CHECK-04" in set(
            sibling_tail_missing_precondition_evidence.get("acceptanceCheckIds") or []
        ),
        "A verified source-page baseline must provide the precondition for sibling landing-tail convergence even when the AI source candidate omitted preconditions",
    )
    manual_conditional_tail_payload = json.loads(json.dumps(sibling_tail_missing_precondition_payload, ensure_ascii=False))
    leaking_document_tail = manual_conditional_tail_payload["cases"][0]
    leaking_document_tail["steps"][-1] = (
        "等待页面跳转，已离开文档打印页，出现百度网盘文件列表或授权登录页面"
    )
    leaking_document_tail["assertions"] = [
        "页面已离开文档打印页，且显示百度网盘相关文件选择界面或授权界面，无白屏或崩溃",
    ]
    manual_conditional_tail_payload["manual_cases"] = [{
        "title": "扫描复印页-点击百度网盘入口可达性校验",
        "priority": "P1",
        "scenario": "扫描复印页-点击百度网盘入口可达性校验",
        "reason": "【证据缺失】同REQ-003主场景，缺乏UI定位依据，自动化风险高，保留为人工检查点",
        "steps": [
            "进入App首页",
            "点击「扫描复印」入口",
            "查找并点击「百度网盘」入口（若存在）",
            "观察是否跳转至百度网盘文件选择页或授权页",
            "验证页面加载情况",
        ],
        "assertions": "若入口存在，点击后能稳定跳转至百度网盘文件选择页或授权页，无报错弹窗",
    }]
    manual_conditional_tail_records = [{
        "raw": item,
        "compact": ai_skill_service._compact_case_for_plan(item, index, origin_level="automatic"),
    } for index, item in enumerate(manual_conditional_tail_payload["cases"])]
    manual_conditional_tail_manual_records = [{
        "raw": item,
        "compact": ai_skill_service._compact_case_for_plan(item, index, origin_level="manual"),
    } for index, item in enumerate(manual_conditional_tail_payload["manual_cases"])]
    manual_conditional_tail_evidence = ai_skill_service._bounded_convergence_evidence(
        manual_conditional_tail_payload,
        manual_conditional_tail_records,
        sibling_tail_missing_precondition_audit,
        selected_baselines=[sibling_tail_baseline],
        manual_records=manual_conditional_tail_manual_records,
    ).get("MC-001") or {}
    require(
        manual_conditional_tail_evidence.get("kind") == "bounded_landing"
        and manual_conditional_tail_evidence.get("sourceCaseId") == "TC-SCAN-SOURCE"
        and manual_conditional_tail_evidence.get("tailSourceCaseId") == "MC-001"
        and "REQ-003-CHECK-04" in set(
            manual_conditional_tail_evidence.get("acceptanceCheckIds") or []
        )
        and "点击「百度网盘」入口" in manual_conditional_tail_evidence.get("flow", [])
        and "若存在" not in " ".join(manual_conditional_tail_evidence.get("flow") or [])
        and "文档打印" not in " ".join(manual_conditional_tail_evidence.get("flow") or [])
        and "若入口存在" not in str(manual_conditional_tail_evidence.get("assertionTarget") or ""),
        "A current-branch manual landing tail without requirementRefs must be bound to the explicit missing check and canonicalized before Runner when sibling tails leak their donor source page",
    )
    guarded_sibling_payloads = []
    wrong_target_sibling_payload = json.loads(json.dumps(sibling_tail_payload, ensure_ascii=False))
    wrong_target_case = wrong_target_sibling_payload["cases"][0]
    for key in ("title", "requirementRefs", "steps", "assertions"):
        value = wrong_target_case.get(key)
        if isinstance(value, list):
            wrong_target_case[key] = [str(item).replace("百度网盘", "企业云盘") for item in value]
        elif isinstance(value, str):
            wrong_target_case[key] = value.replace("百度网盘", "企业云盘")
    guarded_sibling_payloads.append(wrong_target_sibling_payload)
    prefixed_target_sibling_payload = json.loads(json.dumps(sibling_tail_payload, ensure_ascii=False))
    prefixed_target_case = prefixed_target_sibling_payload["cases"][0]
    for key in ("title", "requirementRefs", "steps", "assertions"):
        value = prefixed_target_case.get(key)
        if isinstance(value, list):
            prefixed_target_case[key] = [
                str(item).replace("百度网盘", "百度网盘上传") for item in value
            ]
        elif isinstance(value, str):
            prefixed_target_case[key] = value.replace("百度网盘", "百度网盘上传")
    guarded_sibling_payloads.append(prefixed_target_sibling_payload)
    secondary_target_sibling_payload = json.loads(json.dumps(sibling_tail_payload, ensure_ascii=False))
    secondary_target_case = secondary_target_sibling_payload["cases"][0]
    for key in ("title", "requirementRefs", "steps", "assertions"):
        value = secondary_target_case.get(key)
        if isinstance(value, list):
            secondary_target_case[key] = [
                str(item).replace("百度网盘", "文件列表") for item in value
            ]
        elif isinstance(value, str):
            secondary_target_case[key] = value.replace("百度网盘", "文件列表")
    guarded_sibling_payloads.append(secondary_target_sibling_payload)
    branch_bound_sibling_payload = json.loads(json.dumps(sibling_tail_payload, ensure_ascii=False))
    branch_bound_case = branch_bound_sibling_payload["cases"][0]
    branch_bound_case["steps"][-1] = (
        "等待已离开文档打印来源页，百度网盘授权页或文件列表页稳定可见，无崩溃、无白屏"
    )
    branch_bound_case["assertions"] = [
        "已离开文档打印来源页，百度网盘授权页或文件列表页稳定可见，无崩溃、无白屏",
    ]
    guarded_sibling_payloads.append(branch_bound_sibling_payload)
    unverified_sibling_payload = json.loads(json.dumps(sibling_tail_payload, ensure_ascii=False))
    unverified_sibling_payload["cases"][0]["ai_case_plan"]["baselineVerified"] = False
    guarded_sibling_payloads.append(unverified_sibling_payload)
    for guarded_payload in guarded_sibling_payloads:
        guarded_audit = ai_skill_service.executable_yaml_portfolio_audit(
            guarded_payload,
            {"min_automation_cases": 1},
        )
        guarded_automatic_records = [{
            "raw": item,
            "compact": ai_skill_service._compact_case_for_plan(item, index, origin_level="automatic"),
        } for index, item in enumerate(guarded_payload["cases"])]
        guarded_manual_records = [{
            "raw": item,
            "compact": ai_skill_service._compact_case_for_plan(item, index, origin_level="manual"),
        } for index, item in enumerate(guarded_payload["manual_cases"])]
        guarded_evidence = ai_skill_service._bounded_convergence_evidence(
            guarded_payload,
            guarded_automatic_records,
            guarded_audit,
            selected_baselines=[sibling_tail_baseline],
            manual_records=guarded_manual_records,
        ).get("MC-SCAN-COMBINED") or {}
        require(
            guarded_evidence.get("kind") == "source_ui_assertion"
            and "REQ-003-CHECK-04" not in set(
                guarded_evidence.get("acceptanceCheckIds") or []
            ),
            "A sibling landing tail with a different, prefixed, or secondary assertion target, a donor-branch page assertion, or no verified executable baseline must not satisfy current-branch reachability",
        )
    redundant_branch_payload = json.loads(json.dumps(manual_branch_payload, ensure_ascii=False))
    redundant_branch_payload["cases"].append({
        "case_id": "TC-BRANCH-COVER",
        "title": "资料归档企业云盘完整验收",
        "executionLevel": "needs_review",
        "originExecutionLevel": "automatic",
        "requirementRefs": [manual_branch_payload["analysis"]["requirement_points"][0]],
        "steps": [
            "启动App并等待首页稳定显示",
            "进入资料服务",
            "进入资料归档",
            "检查企业云盘入口展示、同级关系及文案",
            "点击企业云盘入口",
            "等待首个稳定页面可见",
        ],
        "assertions": ["企业云盘入口完整验收通过"],
    })
    redundant_branch_flow = [
        "启动App并等待首页稳定显示",
        "点击「资料服务」",
        "等待资料服务页面加载完成",
        "点击「资料归档」",
        "等待资料归档页面加载完成",
        "检查「企业云盘」入口可见，文案显示为「企业云盘」，且与当前页面其它入口同级并列",
        "点击「企业云盘」入口",
        "检查已离开资料归档来源页，企业云盘授权页、登录页或内容列表任一首个稳定页面可见，且无白屏或崩溃",
    ]
    redundant_branch_plan = {
        "authoritative": True,
        "allowedBaselineIds": ["base-archive-nav"],
        "verifiedBaselineIds": ["base-archive-nav"],
        "requirementPoints": manual_branch_payload["analysis"]["requirement_points"],
        "planningContext": {"pass": "coverage_convergence"},
        "focusedCandidateIds": ["TC-BRANCH-COVER", "MC-BRANCH-SOURCE"],
        "candidateEligibilityById": manual_branch_evidence,
        "cases": [{
            "caseId": "TC-BRANCH-COVER",
            "baselineId": "base-archive-nav",
            "baselineGrounded": True,
            "precondition": "App 首页",
            "flow": redundant_branch_flow,
            "assertionTarget": "企业云盘入口可见，文案显示为「企业云盘」，与其它入口同级；点击后首个稳定页面可见且无白屏或崩溃",
            "requirementRefs": [manual_branch_payload["analysis"]["requirement_points"][0]],
            "executableReason": "成功来源路径与当前需求共同证明短链路可执行",
            "batch": "remaining",
        }],
        "manual_cases": [{
            "caseId": "MC-BRANCH-SOURCE",
            "reason": "相同验收已由更完整的自动候选覆盖，无需重复执行",
        }],
    }
    redundant_branch_applied = ai_skill_service.apply_executable_yaml_plan_to_payload(
        redundant_branch_payload,
        redundant_branch_plan,
    )
    redundant_branch_review = redundant_branch_applied.get("review", {}).get(
        "executable_yaml_plan", {}
    )
    redundant_branch_manual = next(
        item for item in redundant_branch_applied.get("manual_cases") or []
        if item.get("case_id") == "MC-BRANCH-SOURCE"
    )
    require(
        any(
            item.get("case_id") == "TC-BRANCH-COVER"
            and item.get("executionLevel") == "executable"
            for item in redundant_branch_applied.get("cases") or []
        )
        and redundant_branch_manual.get("ai_case_classification", {}).get(
            "redundantBoundedEvidence"
        ) is True
        and redundant_branch_review.get("bounded_convergence_redundant_count") == 1
        and redundant_branch_review.get("bounded_convergence_override_count") == 0
        and ai_skill_service.executable_yaml_portfolio_audit(
            redundant_branch_applied,
            {"min_automation_cases": 1},
        ).get("ok"),
        "A bounded fallback must respect the AI manual decision when another final executable flow already proves every covered acceptance check",
    )
    guarded_replacement_plan = json.loads(json.dumps(redundant_branch_plan, ensure_ascii=False))
    guarded_replacement_plan["cases"][0]["flow"] = [
        "等待 App 首页稳定显示",
        "检查企业云盘入口可见、文案正确且与其它入口同级",
    ]
    guarded_replacement_applied = ai_skill_service.apply_executable_yaml_plan_to_payload(
        redundant_branch_payload,
        guarded_replacement_plan,
    )
    guarded_replacement_review = guarded_replacement_applied.get("review", {}).get(
        "executable_yaml_plan", {}
    )
    require(
        any(
            item.get("case_id") == "MC-BRANCH-SOURCE"
            and item.get("executionLevel") == "executable"
            for item in guarded_replacement_applied.get("cases") or []
        )
        and guarded_replacement_review.get("bounded_convergence_redundant_count") == 0
        and guarded_replacement_review.get("bounded_convergence_override_count") == 1
        and guarded_replacement_review.get("navigation_path_guard_count", 0) >= 1,
        "A model candidate rejected by final navigation guards must never suppress the bounded acceptance fallback",
    )
    manual_tail_payload = json.loads(json.dumps(branch_fallback_payload, ensure_ascii=False))
    for item in manual_tail_payload.get("manual_cases") or []:
        if item.get("case_id") != "TC-R03":
            continue
        item["case_id"] = "MC-R03"
        item["originExecutionLevel"] = "manual"
        item["ai_case_classification"] = {
            "level": "manual",
            "originLevel": "manual",
            "reason": "上游 AI 将外部首屏观察设计为人工候选",
        }
        item["steps"] = [
            "进入售后服务",
            "点击发票入口",
            "观察跳转到发票内容列表页",
            "确认页面包含文件名和操作按钮",
            "检查没有白屏/崩溃",
        ]
        item["assertions"] = []
        item["expected_result"] = ""
    manual_tail_source = next(
        item for item in manual_tail_payload.get("manual_cases") or []
        if item.get("case_id") == "TC-S03"
    )
    manual_tail_payload["manual_cases"].remove(manual_tail_source)
    manual_tail_source["executionLevel"] = "executable"
    manual_tail_source["originExecutionLevel"] = "automatic"
    manual_tail_source.pop("ai_case_classification", None)
    manual_tail_source["ai_case_plan"] = {
        "baselineId": "base-service-nav",
        "baselineGrounded": True,
        "pathPlanApplied": True,
        "precondition": "App 首页，用户已登录",
        "flow": ["启动 App 并等待首页加载", "进入售后服务", "等待发票入口可见"],
        "assertionTarget": "售后服务页面展示文案为发票的入口",
        "batch": "smoke",
    }
    manual_tail_payload["cases"].append(manual_tail_source)
    manual_tail_audit = ai_skill_service.executable_yaml_portfolio_audit(
        manual_tail_payload,
        {"min_automation_cases": 5},
    )
    manual_tail_requests = []
    old_run_ai_skill = ai_skill_service.run_ai_skill
    try:
        def fake_manual_tail_planner(skill_name, request, **_kwargs):
            require(skill_name == "executable_yaml_planner", "Unexpected AI skill in manual-tail convergence replay")
            manual_tail_requests.append(request)
            executable = []
            manual = []
            for candidate in request.get("cases") or []:
                case_id = candidate.get("case_id")
                if candidate.get("currentLevel") == "executable":
                    executable.append({
                        "caseId": case_id,
                        "baselineId": "base-entry-nav",
                        "precondition": "App 首页",
                        "flow": candidate.get("steps") or [],
                        "assertionTarget": (candidate.get("assertions") or [""])[0],
                        "requirementRefs": candidate.get("requirementRefs") or [],
                        "executableReason": "保留已通过门禁的来源页路径",
                        "batch": "smoke",
                    })
                elif case_id in ("TC-S03", "MC-R03"):
                    manual.append({
                        "caseId": case_id,
                        "reason": "模拟模型仍因缺少新落地页历史执行而保守降级",
                        "requirementRefs": candidate.get("requirementRefs") or [],
                    })
            return {
                "cases": executable,
                "needs_review_cases": [],
                "draft_cases": [],
                "manual_cases": manual,
                "review": {"planning_reason": "模拟同分支人工尾链场景"},
            }

        ai_skill_service.run_ai_skill = fake_manual_tail_planner
        manual_tail_plan = ai_skill_service.call_skill_executable_yaml_planner(
            "新增发票入口",
            "会员服务",
            manual_tail_payload,
            [{
                "id": "base-entry-nav",
                "title": "订单与优惠券入口导航",
                "sourceKind": "verified_execution",
                "verificationStatus": "execution_success",
            }, {
                "id": "base-service-nav",
                "title": "售后服务入口导航",
                "aiSelectedBranchName": "会员服务-售后服务",
                "sourceKind": "verified_execution",
                "verificationStatus": "execution_success",
                "businessPath": "首页 -> 售后服务",
                "snippet": "- aiTap: 售后服务\n- aiWaitFor: 等待售后服务页面加载完成",
            }],
            {"smokeCount": 3},
            planning_context={
                "pass": "coverage_convergence",
                "portfolioAudit": manual_tail_audit,
            },
        )
    finally:
        ai_skill_service.run_ai_skill = old_run_ai_skill
    manual_tail_request_by_id = {
        item.get("case_id"): item for item in manual_tail_requests[0].get("cases") or []
    }
    manual_tail_evidence = manual_tail_request_by_id["MC-R03"].get("convergenceEvidence") or {}
    manual_tail_acceptance_candidates = (
        (manual_tail_requests[0].get("planningContext") or {}).get("focus") or {}
    ).get("acceptanceCheckCandidateIds") or {}
    manual_tail_missing_check_ids = {
        item.get("id") for item in manual_tail_audit.get("missingAcceptanceChecks") or []
        if item.get("requirementId") == "REQ-003"
    }
    require(
        manual_tail_evidence.get("eligible") is True
        and manual_tail_evidence.get("sourceCaseId") == "TC-S03"
        and manual_tail_evidence.get("tailSourceCaseId") == "MC-R03"
        and set(manual_tail_evidence.get("acceptanceCheckIds") or []) == manual_tail_missing_check_ids
        and "REQ-003-CHECK-04" in manual_tail_missing_check_ids
        and "MC-R03" in manual_tail_acceptance_candidates.get("REQ-003-CHECK-04", [])
        and {
            case_id
            for case_ids in manual_tail_acceptance_candidates.values()
            for case_id in case_ids
        }.issubset(manual_tail_request_by_id)
        and len(manual_tail_evidence.get("flow") or []) <= 8
        and manual_tail_evidence.get("flow", [""])[0] == "启动 App 并等待首页加载"
        and "售后服务页面展示文案为发票的入口" in " ".join(manual_tail_evidence.get("flow") or [])
        and "售后服务页面展示文案为发票的入口" not in str(manual_tail_evidence.get("assertionTarget") or "")
        and "发票内容列表页" in str(manual_tail_evidence.get("assertionTarget") or "")
        and "已离开来源页" in str(manual_tail_evidence.get("assertionTarget") or ""),
        "A trusted automatic source-page candidate must reuse a concrete single-state AI observation tail without requiring speculative external states",
    )
    manual_tail_applied = ai_skill_service.apply_executable_yaml_plan_to_payload(
        manual_tail_payload,
        manual_tail_plan,
    )
    manual_tail_by_id = {
        item.get("case_id"): item for item in manual_tail_applied.get("cases") or []
    }
    require(
        manual_tail_by_id["TC-S03"].get("executionLevel") == "executable"
        and manual_tail_by_id["TC-S03"].get("steps")
        == manual_tail_source.get("ai_case_plan", {}).get("flow")
        and not manual_tail_by_id["TC-S03"].get("ai_case_plan", {}).get("boundedConvergence")
        and yaml_service._case_manual_block_reason(manual_tail_by_id["TC-S03"]) == ""
        and manual_tail_by_id["MC-R03"].get("executionLevel") == "executable"
        and "点击后首个可见页" in str(manual_tail_by_id["MC-R03"].get("title") or "")
        and manual_tail_by_id["MC-R03"].get("ai_case_plan", {}).get("boundedConvergence", {}).get("sourceCaseId") == "TC-S03"
        and manual_tail_by_id["MC-R03"].get("ai_case_plan", {}).get("boundedConvergence", {}).get("tailSourceCaseId") == "MC-R03"
        and yaml_service._case_manual_block_reason(manual_tail_by_id["MC-R03"]) == ""
        and ai_skill_service.executable_yaml_portfolio_audit(
            manual_tail_applied,
            {"min_automation_cases": 5},
        ).get("ok")
        and not any(item.get("case_id") == "MC-R03" for item in manual_tail_applied.get("manual_cases") or []),
        "A safe model-authored landing candidate must own the missing acceptance check while the approved source-page executable stays immutable",
    )
    omission_requests = []
    old_run_ai_skill = ai_skill_service.run_ai_skill
    try:
        def planner_omitting_bounded_tail(skill_name, request, **_kwargs):
            require(skill_name == "executable_yaml_planner", "Unexpected AI skill in omission replay")
            omission_requests.append(request)
            return {
                "cases": [],
                "needs_review_cases": [],
                "draft_cases": [],
                "manual_cases": [{
                    "caseId": candidate.get("case_id"),
                    "reason": "模拟最终模型对其它候选完成分类",
                    "requirementRefs": candidate.get("requirementRefs") or [],
                } for candidate in request.get("cases") or []
                if candidate.get("case_id") != "MC-R03"],
                "review": {"planning_reason": "模拟最终模型漏回唯一可达性候选"},
            }

        ai_skill_service.run_ai_skill = planner_omitting_bounded_tail
        omission_plan = ai_skill_service.call_skill_executable_yaml_planner(
            "新增发票入口",
            "会员服务",
            manual_tail_payload,
            [{
                "id": "base-entry-nav",
                "title": "订单与优惠券入口导航",
                "sourceKind": "verified_execution",
                "verificationStatus": "execution_success",
            }, {
                "id": "base-service-nav",
                "title": "售后服务入口导航",
                "aiSelectedBranchName": "会员服务-售后服务",
                "sourceKind": "verified_execution",
                "verificationStatus": "execution_success",
                "businessPath": "首页 -> 售后服务",
                "snippet": "- aiTap: 售后服务\n- aiWaitFor: 等待售后服务页面加载完成",
            }],
            {"smokeCount": 3},
            planning_context={
                "pass": "coverage_convergence",
                "portfolioAudit": manual_tail_audit,
            },
        )
    finally:
        ai_skill_service.run_ai_skill = old_run_ai_skill
    omission_applied = ai_skill_service.apply_executable_yaml_plan_to_payload(
        manual_tail_payload,
        omission_plan,
    )
    omission_by_id = {
        item.get("case_id"): item for item in omission_applied.get("cases") or []
    }
    omission_trace = omission_plan.get("trace") or {}
    recovered_omission = next(
        item for item in omission_plan.get("needs_review_cases") or []
        if item.get("caseId") == "MC-R03"
    )
    require(
        len(omission_requests) == 1
        and omission_trace.get("unclassified_focused_candidate_ids") == ["MC-R03"]
        and omission_trace.get("bounded_omission_recovered_case_ids") == ["MC-R03"]
        and recovered_omission.get("recoveredFromModelOmission") is True
        and omission_by_id["MC-R03"].get("executionLevel") == "executable"
        and "REQ-003-CHECK-04" in omission_by_id["MC-R03"].get(
            "ai_case_plan", {}
        ).get("boundedConvergence", {}).get("acceptanceCheckIds", [])
        and ai_skill_service.executable_yaml_portfolio_audit(
            omission_applied,
            {"min_automation_cases": 5},
        ).get("ok"),
        "A final model omission must recover only the omitted candidate with audited bounded evidence and leave all execution gates in place",
    )
    unbounded_omission_requirement = (
        "REQ-003 扫描复印：点击百度网盘入口并校验目标页面稳定可达"
    )
    unbounded_omission_check = {
        "id": "REQ-003-CHECK-04",
        "requirementId": "REQ-003",
        "branch": "扫描复印",
        "kind": "reachability",
        "text": "点击百度网盘入口并校验目标页面稳定可达",
    }
    unbounded_omission_payload = {
        "analysis": {
            "requirement_points": [unbounded_omission_requirement],
            "requirement_acceptance_checks": [unbounded_omission_check],
        },
        "cases": [{
            "case_id": "TC-SCAN-LANDING",
            "title": "扫描复印百度网盘入口可达性校验",
            "coverage": unbounded_omission_requirement,
            "requirementRefs": [unbounded_omission_requirement],
            "executionLevel": "executable",
            "steps": [
                "等待 App 首页稳定显示",
                "点击「扫描复印」入口",
                "等待「百度网盘」入口可见",
            ],
            "assertions": ["扫描复印页面展示「百度网盘」入口"],
            "ai_case_plan": {
                "baselineId": "base-scan-nav",
                "baselineGrounded": True,
                "pathPlanApplied": True,
                "precondition": "App 首页",
                "flow": [
                    "等待 App 首页稳定显示",
                    "点击「扫描复印」入口",
                    "等待「百度网盘」入口可见",
                ],
                "assertionTarget": "扫描复印页面展示「百度网盘」入口",
                "batch": "smoke",
            },
        }],
        "manual_cases": [],
    }
    unbounded_omission_audit = ai_skill_service.executable_yaml_portfolio_audit(
        unbounded_omission_payload,
        {"min_automation_cases": 1},
    )
    unbounded_omission_requests = []
    old_run_ai_skill = ai_skill_service.run_ai_skill
    try:
        def planner_omitting_required_automatic(skill_name, request, **_kwargs):
            require(skill_name == "executable_yaml_planner", "Unexpected AI skill in unbounded omission replay")
            unbounded_omission_requests.append(request)
            if len(unbounded_omission_requests) == 1:
                return {
                    "cases": [],
                    "needs_review_cases": [],
                    "draft_cases": [],
                    "manual_cases": [],
                    "review": {"planning_reason": "模拟模型漏回承担可达性缺口的自动候选"},
                }
            return {
                "cases": [{
                    "caseId": "TC-SCAN-LANDING",
                    "baselineId": "base-scan-nav",
                    "precondition": "App 首页",
                    "flow": [
                        "等待 App 首页稳定显示",
                        "点击「扫描复印」入口",
                        "等待「百度网盘」入口可见",
                        "点击「百度网盘」入口",
                        "等待百度网盘授权页或文件列表页任一稳定可见",
                    ],
                    "assertionTarget": "百度网盘授权页或文件列表页任一稳定可见",
                    "requirementRefs": [unbounded_omission_requirement],
                    "executableReason": "同模型语义纠偏补齐漏回候选的显式可达性契约",
                    "batch": "remaining",
                }],
                "needs_review_cases": [],
                "draft_cases": [],
                "manual_cases": [],
                "review": {"planning_reason": "定向补齐漏回的扫描可达性候选"},
            }

        ai_skill_service.run_ai_skill = planner_omitting_required_automatic
        unbounded_omission_plan = ai_skill_service.call_skill_executable_yaml_planner(
            "基础打印新增百度网盘入口",
            "基础打印",
            unbounded_omission_payload,
            [{
                "id": "base-scan-nav",
                "title": "扫描复印成功导航",
                "sourceKind": "verified_execution",
                "verificationStatus": "execution_success",
                "businessPath": "App 首页 -> 扫描复印",
                "snippet": "- aiTap: 扫描复印\n- aiWaitFor: 等待扫描复印页面加载完成",
            }],
            {"smokeCount": 1},
            planning_context={
                "pass": "coverage_convergence",
                "portfolioAudit": unbounded_omission_audit,
            },
        )
    finally:
        ai_skill_service.run_ai_skill = old_run_ai_skill
    unbounded_retry_trace = (
        (unbounded_omission_plan.get("trace") or {}).get("acceptance_repair_retry") or {}
    )
    unbounded_retry_request = (
        unbounded_omission_requests[1]
        if len(unbounded_omission_requests) > 1
        else {}
    )
    unbounded_retry_applied = ai_skill_service.apply_executable_yaml_plan_to_payload(
        unbounded_omission_payload,
        unbounded_omission_plan,
    )
    require(
        len(unbounded_omission_requests) == 2
        and [
            item.get("case_id") for item in unbounded_retry_request.get("cases") or []
        ] == ["TC-SCAN-LANDING"]
        and unbounded_retry_request.get("responseContract", {}).get(
            "acceptanceRepairRetry"
        ) is True
        and unbounded_retry_trace.get("succeeded") is True
        and unbounded_retry_trace.get("candidate_ids") == ["TC-SCAN-LANDING"]
        and unbounded_retry_trace.get("feedback", [{}])[0].get(
            "omittedFromClassification"
        ) is True
        and ai_skill_service.executable_yaml_portfolio_audit(
            unbounded_retry_applied,
            {"min_automation_cases": 1},
        ).get("ok"),
        "A focused automatic candidate omitted without recoverable bounded evidence must receive one same-model semantic retry before the final gate",
    )
    old_run_ai_skill = ai_skill_service.run_ai_skill
    try:
        def timeout_convergence_planner(*_args, **_kwargs):
            raise TimeoutError("simulated final convergence timeout")

        ai_skill_service.run_ai_skill = timeout_convergence_planner
        timeout_evidence_plan = ai_skill_service.call_skill_executable_yaml_planner(
            "新增发票入口",
            "会员服务",
            manual_tail_payload,
            [{
                "id": "base-entry-nav",
                "title": "订单与优惠券入口导航",
                "sourceKind": "verified_execution",
                "verificationStatus": "execution_success",
            }, {
                "id": "base-service-nav",
                "title": "售后服务入口导航",
                "aiSelectedBranchName": "会员服务-售后服务",
                "sourceKind": "verified_execution",
                "verificationStatus": "execution_success",
            }],
            {"smokeCount": 3},
            planning_context={
                "pass": "coverage_convergence",
                "portfolioAudit": manual_tail_audit,
            },
        )
    finally:
        ai_skill_service.run_ai_skill = old_run_ai_skill
    timeout_evidence_applied = ai_skill_service.apply_executable_yaml_plan_to_payload(
        manual_tail_payload,
        timeout_evidence_plan,
    )
    require(
        timeout_evidence_plan.get("authoritative") is False
        and timeout_evidence_plan.get("evidenceFallback") is True
        and timeout_evidence_plan.get("trace", {}).get("evidence_fallback") is True
        and timeout_evidence_plan.get("trace", {}).get("timeout_seconds")
        == ai_skill_service.AI_EXECUTABLE_YAML_EVIDENCE_CONVERGENCE_TIMEOUT_SECONDS
        and timeout_evidence_plan.get("trace", {}).get("timeout_seconds")
        <= ai_skill_service.AI_EXECUTABLE_YAML_PLANNER_TIMEOUT_SECONDS
        and timeout_evidence_applied.get("review", {}).get("executable_yaml_plan", {}).get("evidenceFallbackApplied") is True
        and ai_skill_service.executable_yaml_portfolio_audit(
            timeout_evidence_applied,
            {"min_automation_cases": 5},
        ).get("ok"),
        "A final convergence timeout must reuse only validated upstream AI evidence without adding a second model call",
    )
    require(preview.get("candidateOnly") and preview.get("platformLifecycle") and preview.get("source") == "requirement_preview", "Agent preview must separate coverage candidates from the later AI business plan")
    plan_context = yaml_service.build_agent_business_plan_context_text({
        "source": "platform_mindmap_ai",
        "objective": "覆盖三个入口",
        "businessFlows": [{
            "name": f"{branch}业务验收",
            "branch": branch,
            "steps": ["进入首页", f"进入{branch}"],
            "checks": ["校验新增入口可见"],
        } for branch in branches],
    })
    require("Agent 上游业务计划" in plan_context and "页面路径" in plan_context and "可见验收点" in plan_context, "Agent business plan must feed the downstream case/YAML generation context")

    old_health = agent_service._probe_agent_ai_health
    old_mindmap = yaml_service.generate_mindmap_from_request
    old_update_generate_job = yaml_service.update_generate_job
    old_log_tool_call = agent_service._log_tool_call
    mindmap_calls = []
    try:
        agent_service._probe_agent_ai_health = lambda run=None: {"gatewayReachable": True, "ready": True}

        def fake_mindmap(request, job_id=None):
            mindmap_calls.append({"request": request, "jobId": job_id})
            return {
                "case_set_id": "agent-plan-static-1",
                "cases": {
                    "analysis": {
                        "summary": "覆盖三个业务入口的展示、同级关系、文案和可达页面",
                        "business_goals": ["三个入口完整验收"],
                        "entry_points": ["文档打印", "照片打印", "扫描复印"],
                        "requirement_points": ["文档打印入口", "照片打印入口", "扫描复印入口"],
                        "confidence": "high",
                        "readiness_level": "ready",
                    },
                    "scenarios": [
                        {"scenario": "文档打印百度网盘入口", "feature": "打印-百度网盘入口", "requirement_point": "文档打印入口", "business_path": "进入首页 -> 点击文档打印 -> 到达文档打印页", "assertions": ["检查新增入口可见和文案正确"]},
                        {"scenario": "照片打印百度网盘入口", "feature": "打印-百度网盘入口", "requirement_point": "照片打印入口", "business_path": "进入首页 -> 点击照片打印 -> 到达照片打印页", "assertions": ["检查新增入口同级展示并可达"]},
                        {"scenario": "扫描复印百度网盘入口", "feature": "打印-百度网盘入口", "requirement_point": "扫描复印入口", "business_path": "进入首页 -> 点击扫描复印 -> 到达扫描复印页", "assertions": ["检查新增入口文案和可达页面"]},
                    ],
                    "cases": [
                        {"case_id": "TC-001", "title": "文档打印入口", "requirement_point": "文档打印入口", "smoke": True},
                        {"case_id": "TC-002", "title": "照片打印入口", "requirement_point": "照片打印入口", "smoke": True},
                        {"case_id": "TC-003", "title": "扫描复印入口", "requirement_point": "扫描复印入口", "smoke": True},
                    ],
                    "review": {
                        "skill_pipeline": "requirement_analyzer.v1 -> scenario_designer.v1 -> automation_filter.v1 -> smoke_selector.v1/platform_gate",
                        "mindmap_visual_batches": "1/1",
                        "mindmap_visual_images_grounded": 4,
                        "prepared_figma_context_reused": {"enabled": True, "used_count": 4, "image_count": 4},
                        "yaml_reference_examples": [{"provenancePath": "server-tasks-all/基础打印/6寸照片打印.yaml", "sourceTrust": 80}],
                    },
                },
            }

        yaml_service.generate_mindmap_from_request = fake_mindmap
        yaml_service.update_generate_job = lambda *args, **kwargs: None
        agent_service._log_tool_call = lambda *args, **kwargs: None
        live_plan_run = {
            "runId": "agent-static-ai-plan",
            "target": "基础打印新增百度网盘入口",
            "scope": "regression",
            "appName": "小白学习打印",
            "appPackage": "com.xbxxhz.box",
            "runnerId": "win-runner-01",
            "deviceId": "ecbfd645",
            "deviceStrategy": "fixed",
            "aiModel": "qwen3.6-plus",
            "normalizedInput": {"requirementText": "基础打印入口在首页：文档打印、照片打印、扫描复印。覆盖展示、同级关系、文案和可达页面。"},
            "artifacts": {
                "sourceContext": {
                    "requirementText": "基础打印入口在首页：文档打印、照片打印、扫描复印。覆盖展示、同级关系、文案和可达页面。",
                    "figmaUrl": "https://www.figma.com/design/test",
                    "figmaUsedPages": [{"page_name": f"page-{index}"} for index in range(4)],
                    "figmaImageCount": 4,
                },
            },
        }
        candidate_constraint = agent_service._ensure_business_flow_constraint(live_plan_run)
        require(candidate_constraint.get("candidateOnly") and not candidate_constraint.get("strict"), "Raw requirement extraction must stay an unverified coverage candidate before AI PLAN")
        require(candidate_constraint.get("businessFlow") == [], "Sibling requirement candidates must not be flattened into a fake sequential main chain")
        source_guarded_analysis = ai_skill_service.apply_source_requirement_contract({
            "business_goals": ["新增云盘入口"],
            "requirement_points": [
                "REQ-001 文档打印入口展示",
                "REQ-002 照片打印入口展示",
                "REQ-003 扫描复印入口是否需要待确认",
                "REQ-004 未绑定账号授权",
                "REQ-005 已绑定账号文件列表",
                "REQ-006 手机与宽屏适配",
            ],
            "questions": ["扫描复印缺少 Figma 帧"],
            "missing_inputs": ["第三方账号"],
            "source_quality": {"requirement": "sufficient", "ui": "partial", "knowledge": "missing"},
        }, candidate_constraint)
        guarded_points = source_guarded_analysis.get("requirement_points") or []
        require(
            len(guarded_points) == 3
            and all(term in " ".join(guarded_points) for term in ("文档打印", "照片打印", "扫描复印", "入口可见", "同级", "文案", "稳定可达")),
            "Source-derived entry branches/checks must own the hard coverage gate instead of AI-inferred auth, account, or device states",
        )
        require(
            len(source_guarded_analysis.get("ai_suggested_requirement_points") or []) == 6
            and source_guarded_analysis.get("requirement_contract", {}).get("applied"),
            "AI requirement suggestions must remain observable after the source contract removes them from the hard gate",
        )
        require(
            len(source_guarded_analysis.get("requirement_acceptance_checks") or []) == 12
            and source_guarded_analysis.get("requirement_contract", {}).get("acceptance_check_count") == 12,
            "Three explicit source branches with four checks each must preserve all twelve hard acceptance dimensions",
        )
        plan_call = agent_service._tool_agent_plan(live_plan_run)
        live_plan = live_plan_run.get("artifacts", {}).get("plan", {})
        require(plan_call.get("status") == "SUCCESS" and live_plan.get("aiGenerated"), "Agent PLAN must use the platform MM AI result instead of silently returning a generic local lifecycle")
        require(mindmap_calls and mindmap_calls[0]["request"].get("requireAiPlanning"), "Agent PLAN must reuse platform mindmap skills with deterministic entry fast paths disabled")
        require(mindmap_calls[0]["request"].get("useYamlBaselineContext"), "Agent MM planning must include trusted baseline reranking context")
        require(
            mindmap_calls[0]["request"].get("requirementCoverageContract", {}).get("businessFlows") == candidate_constraint.get("businessFlows"),
            "Agent PLAN must pass the original source coverage contract into the MM requirement analyzer",
        )
        require(len(live_plan.get("businessFlows") or []) == 3 and live_plan.get("qualityGate", {}).get("passed"), "AI PLAN must preserve every required business branch and pass deterministic grounding")
        require(
            [item.get("branch") for item in live_plan.get("businessFlows") or []] == branches
            and all(item.get("branchSource") == "source_requirement_contract" for item in live_plan.get("businessFlows") or []),
            "Generic AI feature labels must recover the one matching source-defined business branch before baseline retrieval",
        )
        require(
            agent_service._agent_plan_constraint_branch_match(
                {"name": "文档与照片入口一致性", "steps": ["对比文档打印和照片打印"]},
                candidate_constraint.get("businessFlows") or [],
            ) == "",
            "Cross-branch AI flows must not be forced into one source branch when multiple branches match",
        )
        require(live_plan.get("model") == "qwen3.6-plus", "Agent PLAN must retain the actual model provenance")
        require(live_plan.get("source") == "platform_mindmap_ai" and live_plan.get("mindmapTrace", {}).get("preparedFigmaReused"), "Agent PLAN must expose MM and prepared-Figma provenance")
        require(live_plan.get("visualReference", {}).get("sentToAiForJudgement") and live_plan["visualReference"].get("aiJudgementCompleted"), "Agent PLAN must distinguish visual AI dispatch from completed MM grounding")
        live_visual_report = live_plan_run.get("artifacts", {}).get("visualReferenceReport", {})
        require(
            live_visual_report.get("sentToAiForJudgement")
            and live_visual_report.get("aiJudgementStatus") == "completed"
            and live_visual_report.get("visualBatchesDone") == 1,
            "PLAN must immediately refresh the top-level visual report from the real mindmap batch outcome",
        )
        require(live_plan.get("businessFlowConstraint", {}).get("strict"), "Only a validated AI PLAN may become the strict downstream business constraint")
        required_branches = yaml_service.baseline_required_branches_from_agent_plan(live_plan)
        require(
            [item.get("name") for item in required_branches] == ["文档打印", "照片打印", "扫描复印"],
            "Baseline Top3 branch targets must come from the AI-selected smoke flows instead of a product-specific list",
        )
        require(
            [item.get("anchors") for item in required_branches]
            == [["文档打印", "文档"], ["照片打印", "照片"], ["扫描复印", "扫描"]],
            "Required baseline branches must derive reusable leaf anchors from the AI-authored hierarchy",
        )

        partial_result = fake_mindmap(mindmap_calls[0]["request"], job_id="partial-visual")
        partial_result["cases"]["review"].update({
            "mindmap_visual_batches": "1/2",
            "mindmap_visual_grounded": True,
            "visual_refine_error": "second batch timeout",
        })
        partial_plan, partial_issues = agent_service._agent_business_plan_from_mindmap(
            live_plan_run,
            partial_result,
            candidate_constraint,
        )
        require(not partial_issues and partial_plan.get("visualReference", {}).get("aiJudgementStatus") == "partial" and not partial_plan["visualReference"].get("aiJudgementCompleted"), "Partial visual batches must remain a soft reference without being reported as fully completed")

        fallback_requests = []

        def fallback_mindmap(request, job_id=None):
            fallback_requests.append(request)
            return {
                "case_set_id": "agent-plan-fallback",
                "cases": {
                    "analysis": {"fallback_reason": "model timeout", "requirement_points": ["文档打印入口"]},
                    "scenarios": [{"feature": "文档打印", "source": "local_fallback_after_ai_timeout", "fallback_reason": "model timeout"}],
                    "cases": [],
                    "review": {"skill_pipeline": "requirement_analyzer.v1 -> scenario_designer.v1 -> automation_filter.v1"},
                },
            }

        yaml_service.generate_mindmap_from_request = fallback_mindmap
        failed_plan_run = {
            **live_plan_run,
            "runId": "agent-static-failed-plan",
            "artifacts": {"sourceContext": dict(live_plan_run["artifacts"]["sourceContext"])},
        }
        failed_plan_call = agent_service._tool_agent_plan(failed_plan_run)
        failed_plan = failed_plan_run.get("artifacts", {}).get("plan", {})
        require(failed_plan_call.get("status") == "FAILED", "Agent PLAN must fail after bounded MM retries when core AI skills only return local fallbacks")
        require(failed_plan.get("status") == "failed" and not failed_plan.get("fallbackUsed"), "Rule candidates must never be persisted as a successful AI plan")
        require(len(fallback_requests) == 2 and fallback_requests[1].get("planValidationIssues"), "The bounded MM retry must receive the previous AI output-gate issues instead of blindly repeating the same call")
    finally:
        agent_service._probe_agent_ai_health = old_health
        yaml_service.generate_mindmap_from_request = old_mindmap
        yaml_service.update_generate_job = old_update_generate_job
        agent_service._log_tool_call = old_log_tool_call

    old_get_baseline_cache = yaml_baseline_cache.get_yaml_baseline_cache
    try:
        noisy_rows = [
            {
                "id": f"generic-{index}",
                "title": f"云盘入口通用成功样本 {index}",
                "module": "AI_Agent_草稿",
                "file": f"AI_Agent_草稿/generic-{index}.yaml",
                "businessPath": "首页 -> 文档打印 -> 百度网盘入口",
                "snippet": "文档打印 百度网盘 入口 展示 同级 文案 可达",
                "keywords": ["百度网盘", "入口", "文档打印"],
                "actions": ["aiWaitFor", "aiTap", "aiAssert"],
                "baselineUsable": True,
                "trusted": True,
                "sourceTrust": 100,
                "lastRunStatus": "success",
            }
            for index in range(24)
        ]
        branch_rows = [
            {
                "id": "doc-grounded",
                "title": "文档打印成功基线",
                "module": "基础打印",
                "file": "基础打印/文档打印.yaml",
                "businessPath": "首页 -> 文档打印 -> 本地文档",
                "snippet": "文档打印入口与本地文档页面",
                "keywords": ["文档打印"],
                "actions": ["aiWaitFor", "aiTap"],
                "baselineUsable": True,
                "trusted": True,
                "sourceTrust": 80,
            },
            {
                "id": "photo-grounded",
                "title": "6寸照片打印",
                "module": "基础打印",
                "file": "基础打印/6寸照片打印.yaml",
                "businessPath": "首页 -> 照片打印 -> 6寸照片 -> 相册导入",
                "snippet": "照片打印页面等待后进入相册导入",
                "keywords": ["照片打印", "照片"],
                "actions": ["aiWaitFor", "aiTap"],
                "baselineUsable": True,
                "trusted": True,
                "sourceTrust": 80,
            },
            {
                "id": "scan-grounded",
                "title": "文件扫描",
                "module": "基础打印",
                "file": "基础打印/文件扫描.yaml",
                "businessPath": "首页 -> 扫描复印 -> 文件扫描",
                "snippet": "扫描复印页面进入文件扫描",
                "keywords": ["扫描复印", "扫描"],
                "actions": ["aiWaitFor", "aiTap"],
                "baselineUsable": True,
                "trusted": True,
                "sourceTrust": 80,
            },
        ]
        yaml_baseline_cache.get_yaml_baseline_cache = lambda force=False: {"items": noisy_rows + branch_rows}
        required_retrieval = [
            {"id": "FLOW-001", "name": "基础打印-文档打印", "query": "百度网盘入口展示同级文案可达 文档打印", "anchors": ["文档打印", "文档"]},
            {"id": "FLOW-002", "name": "基础打印-照片打印", "query": "百度网盘入口展示同级文案可达 照片打印", "anchors": ["照片打印", "照片"]},
            {"id": "FLOW-003", "name": "基础打印-扫描复印", "query": "百度网盘入口展示同级文案可达 扫描复印", "anchors": ["扫描复印", "扫描"]},
        ]
        grounded_pool = yaml_baseline_cache.search_diverse_baseline_examples(
            "百度网盘入口展示同级文案可达",
            branch_queries=required_retrieval,
            limit=20,
            per_branch=4,
        )
        compact_grounded_pool = [
            ai_skill_service._compact_baseline_candidate(item, index)
            for index, item in enumerate(grounded_pool)
        ]
        grounded_counts = ai_skill_service._annotate_baseline_branch_eligibility(
            compact_grounded_pool,
            ai_skill_service._normalize_required_baseline_branches(required_retrieval),
        )
        require(
            all(grounded_counts.get(branch_id, 0) >= 1 for branch_id in ("FLOW-001", "FLOW-002", "FLOW-003"))
            and {"photo-grounded", "scan-grounded"}.issubset({item.get("id") for item in compact_grounded_pool}),
            "Required branch retrieval must keep grounded photo/scan baselines even when generic successful samples dominate global similarity",
        )
        require(
            next(item for item in compact_grounded_pool if item.get("id") == "photo-grounded").get("retrievalBranchIds") == ["FLOW-002"]
            and next(item for item in compact_grounded_pool if item.get("id") == "scan-grounded").get("retrievalBranchIds") == ["FLOW-003"],
            "Branch retrieval provenance must survive compaction for the AI reranker eligibility gate",
        )
    finally:
        yaml_baseline_cache.get_yaml_baseline_cache = old_get_baseline_cache

    rerank_requests = []
    old_run_ai_skill = ai_skill_service.run_ai_skill
    try:
        required = [
            {"id": "FLOW-001", "name": "文档打印", "query": "文档打印分支"},
            {"id": "FLOW-002", "name": "照片打印", "query": "照片打印分支"},
            {"id": "FLOW-003", "name": "扫描复印", "query": "扫描复印分支"},
        ]
        candidates = [
            {"id": "doc-a", "title": "文档路径A", "file": "doc-a.yaml", "provenancePath": "doc-a.yaml", "retrievalQueries": ["文档打印分支", "照片打印分支", "扫描复印分支"], "baselineUsable": True, "trusted": True},
            {"id": "doc-b", "title": "文档路径B", "file": "doc-b.yaml", "provenancePath": "doc-b.yaml", "retrievalQueries": ["文档打印分支"], "baselineUsable": True, "trusted": True},
            {"id": "doc-c", "title": "文档断言", "file": "doc-c.yaml", "provenancePath": "doc-c.yaml", "retrievalQueries": ["文档打印分支"], "baselineUsable": True, "trusted": True},
            {"id": "photo-a", "title": "6寸照片打印", "file": "photo-a.yaml", "provenancePath": "photo-a.yaml", "retrievalQueries": ["照片打印分支"], "baselineUsable": True, "trusted": True},
            {"id": "scan-a", "title": "文件扫描", "file": "scan-a.yaml", "provenancePath": "scan-a.yaml", "retrievalQueries": ["扫描复印分支"], "baselineUsable": True, "trusted": True},
        ]
        misleading_photo = ai_skill_service._compact_baseline_candidate({
            "id": "broad-doc-photo",
            "title": "小屏首页文档/照片入口",
            "file": "broad-doc-photo.yaml",
            "provenancePath": "broad-doc-photo.yaml",
            "businessPath": "文档打印/照片打印",
            "retrievalQueries": ["照片打印分支"],
            "retrievalBranchIds": ["FLOW-002"],
            "snippet": "- name: 小屏入口\n  flow:\n    - aiTap: 文档打印\n    - aiWaitFor: 本地文档入口可见",
            "baselineUsable": True,
            "trusted": True,
        })
        misleading_counts = ai_skill_service._annotate_baseline_branch_eligibility(
            [misleading_photo],
            ai_skill_service._normalize_required_baseline_branches([required[1]]),
        )
        require(
            not misleading_photo.get("eligibleBranchIds") and misleading_counts.get("FLOW-002") == 0,
            "Broad document/photo metadata must not impersonate photo navigation when actual actions enter only documents",
        )

        def fake_branch_reranker(skill_name, request, **_kwargs):
            require(skill_name == "baseline_reranker", "Unexpected skill in branch reranker replay")
            rerank_requests.append(request)
            if len(rerank_requests) == 1:
                return {
                    "selected": [
                        {"id": item["id"], "candidatePath": item["provenancePath"], "branchId": "FLOW-001", "role": role}
                        for item, role in zip(request["candidates"][:3], ("navigation_path", "capability_pattern", "assertion_pattern"))
                    ],
                    "review": {"selection_reason": "错误地把三个角色都给了文档分支"},
                }
            by_id = {item["id"]: item for item in request["candidates"]}
            return {
                "selected": [
                    {"id": candidate_id, "candidatePath": by_id[candidate_id]["provenancePath"], "branchId": branch_id, "role": "navigation_path"}
                    for candidate_id, branch_id in (("doc-a", "FLOW-001"), ("photo-a", "FLOW-002"), ("scan-a", "FLOW-003"))
                ],
                "review": {"selection_reason": "按三个 AI 首批业务分支纠正 Top3"},
            }

        ai_skill_service.run_ai_skill = fake_branch_reranker
        reranked = ai_skill_service.call_skill_baseline_reranker(
            "多分支入口",
            "基础打印",
            "文档打印、照片打印、扫描复印",
            candidates,
            limit=3,
            required_branches=required,
        )
    finally:
        ai_skill_service.run_ai_skill = old_run_ai_skill
    require(len(rerank_requests) == 2 and rerank_requests[1].get("selectionValidationIssues"), "AI baseline reranking must receive one bounded self-correction when Top3 misses an AI-selected branch")
    first_request_candidates = {item["id"]: item for item in rerank_requests[0]["candidates"]}
    require(
        first_request_candidates["doc-a"].get("eligibleBranchIds") == ["FLOW-001"]
        and first_request_candidates["photo-a"].get("eligibleBranchIds") == ["FLOW-002"]
        and first_request_candidates["scan-a"].get("eligibleBranchIds") == ["FLOW-003"],
        "Merged retrieval queries must not let a document baseline impersonate photo or scan branch evidence",
    )
    require(
        all(item.get("eligibleBranchIds") for item in rerank_requests[1].get("candidates") or []),
        "The bounded AI correction must receive only candidates with auditable branch evidence",
    )
    require(
        reranked.get("trace", {}).get("branch_coverage_ok")
        and reranked.get("trace", {}).get("branch_repair_succeeded")
        and {item.get("ai_selected_branch_name") for item in reranked.get("selected") or []} == {"文档打印", "照片打印", "扫描复印"}
        and {
            ai_skill_service._compact_baseline_candidate(item, index).get("selectedBranchName")
            for index, item in enumerate(reranked.get("selected") or [])
        } == {"文档打印", "照片打印", "扫描复印"},
        "Corrected Top3 baselines must cover distinct required business branches before role diversity",
    )

    rejected_requests = []
    old_run_ai_skill = ai_skill_service.run_ai_skill
    try:
        def always_cross_branch(skill_name, request, **_kwargs):
            rejected_requests.append(request)
            doc_candidate = next(item for item in request["candidates"] if item["id"] == "doc-a")
            return {
                "selected": [
                    {"id": "doc-a", "candidatePath": doc_candidate["provenancePath"], "branchId": branch_id}
                    for branch_id in ("FLOW-001", "FLOW-002", "FLOW-003")
                ]
            }

        ai_skill_service.run_ai_skill = always_cross_branch
        rejected = ai_skill_service.call_skill_baseline_reranker(
            "多分支入口",
            "基础打印",
            "文档打印、照片打印、扫描复印",
            candidates,
            limit=3,
            required_branches=required,
        )
    finally:
        ai_skill_service.run_ai_skill = old_run_ai_skill
    require(len(rejected_requests) == 2, "Invalid cross-branch AI selection must receive exactly one bounded correction")
    require(
        [item.get("ai_selected_branch_id") for item in rejected.get("selected") or []] == ["FLOW-001"]
        and rejected.get("trace", {}).get("branch_coverage_ok") is False,
        "Invalid branch assignments must not occupy Top3 slots or trigger an unrelated local fallback",
    )

    maintained = yaml_baseline_cache._baseline_source_info(
        str(ROOT / "server-tasks-all"), "基础打印/6寸照片打印.yaml", {}, "unknown"
    )
    working = yaml_baseline_cache._baseline_source_info(
        str(ROOT / "server-tasks"), "AI_Agent_草稿/5寸照片.yaml", {}, "unknown"
    )
    require(maintained.get("trusted") and maintained.get("baselineUsable"), "Maintained baseline library must stay eligible for AI grounding")
    require(not working.get("trusted") and not working.get("baselineUsable"), "Unverified runtime drafts must not teach generation or repair AI")
    require(maintained.get("provenancePath", "").startswith("server-tasks-all/"), "Trusted baseline provenance must identify the maintained source root")

    with tempfile.TemporaryDirectory() as temp_dir:
        runtime_root = Path(temp_dir) / "server-tasks"
        maintained_root = Path(temp_dir) / "server-tasks-all"
        for root in (runtime_root, maintained_root):
            yaml_path = root / "基础打印" / "同一基线.yaml"
            yaml_path.parent.mkdir(parents=True, exist_ok=True)
            yaml_path.write_text("android:\n  tasks:\n    - name: 同一基线\n      flow:\n        - aiWaitFor: 首页可见\n        - aiTap: 照片打印\n", encoding="utf-8")
        old_roots = yaml_baseline_cache._baseline_roots
        old_save_cache = yaml_baseline_cache.save_yaml_baseline_cache
        try:
            yaml_baseline_cache._baseline_roots = lambda: [str(runtime_root), str(maintained_root)]
            yaml_baseline_cache.save_yaml_baseline_cache = lambda cache: None
            deduped_cache = yaml_baseline_cache.build_yaml_baseline_cache()
        finally:
            yaml_baseline_cache._baseline_roots = old_roots
            yaml_baseline_cache.save_yaml_baseline_cache = old_save_cache
        require(len(deduped_cache.get("items") or []) == 1, "Identical baseline blocks should remain deduplicated")
        require(deduped_cache["items"][0].get("sourceKind") == "maintained_library", "Baseline dedupe must retain the stronger maintained provenance instead of the first unverified working copy")

    yaml_service_source = (ROOT / "task_server" / "services" / "yaml_service.py").read_text(encoding="utf-8")
    require(
        yaml_service_source.index("semantic_constraints_text = build_requirement_semantic_constraints_text")
        < yaml_service_source.index("agent_business_plan_text = build_agent_business_plan_context_text"),
        "AI business-plan guidance must not be parsed as a new deterministic hard requirement",
    )
    agent_service_source = (ROOT / "task_server" / "services" / "agent_service.py").read_text(encoding="utf-8")
    require("artifacts = run.get(\"artifacts\") if isinstance(run.get(\"artifacts\"), dict) else {}\n    files = _agent_source_files_for_generation(run)" in agent_service_source, "Agent generation must initialize artifacts before forwarding the AI business plan")
    require(
        '"executionContext": {' in agent_service_source
        and '"executionContext": execution_context' in yaml_service_source,
        "Agent YAML planning must forward the fixed single-device execution context to AI without selecting another device",
    )
    figma_soft_evidence = yaml_service.build_figma_soft_evidence_context_text([
        "[Figma设计稿页面]\n页面名称：内部备份名\n状态/变体：5寸照片\n可见文案：相册导入、百度网盘",
    ])
    require("Figma 同帧软证据规则" in figma_soft_evidence and "状态/变体：5寸照片" in figma_soft_evidence and "不得推广到相邻业务页" in figma_soft_evidence, "Existing Figma parser output must be reused under an explicit same-frame soft-evidence contract")
    planner_prompt = (ROOT / "ai_skills" / "prompts" / "executable_yaml_planner.v1.md").read_text(encoding="utf-8")
    require("每个候选 case 恰好放入" in planner_prompt and "sourceEvidence" in planner_prompt, "AI executable planner must classify every grounded candidate exactly once")
    require(
        "多个合法终态" in planner_prompt
        and "originLevel=manual" in planner_prompt
        and "requirementRefs" in planner_prompt
        and "真实文案或明确页面区域" in planner_prompt,
        "AI executable planner must re-evaluate prior manual candidates and support bounded state-variant outcomes with explicit requirement mapping",
    )
    require(
        "Figma、截图和页面知识是软参考" in planner_prompt
        and "运行时入口不存在属于产品断言失败" in planner_prompt
        and "planningContext.focus" in planner_prompt
        and "convergenceEvidence.eligible=true" in planner_prompt
        and "acceptanceCheckCandidateIds" in planner_prompt
        and "visibility/relation/copy" in planner_prompt
        and "不要求新增入口或目标落地页已有历史成功结果" in planner_prompt
        and "kind=source_ui_assertion" in planner_prompt
        and "requiredAcceptanceChecks" in planner_prompt
        and "contractRoles=preserve" in planner_prompt
        and "历史成功基线里的文件名、账号、手机号、订单号" in planner_prompt,
        "Final executable planning must test explicit visible requirements without turning missing sibling Figma frames into a hard gate",
    )
    requirement_prompt = (ROOT / "ai_skills" / "prompts" / "requirement_analyzer.v1.md").read_text(encoding="utf-8")
    require(
        "不要擅自在需求点正文后追加“待确认 / 需补充 UI 证据”" in requirement_prompt
        and "requirementContract" in requirement_prompt
        and "不能升级为硬覆盖点" in requirement_prompt,
        "Requirement analysis must preserve the source contract and keep inferred states outside the hard acceptance gate",
    )
    automation_prompt = (ROOT / "ai_skills" / "prompts" / "automation_filter.v1.md").read_text(encoding="utf-8")
    require(
        "任一合法终态" in automation_prompt
        and "第三方入口本身不是人工判定理由" in automation_prompt
        and "不要把小屏和宽屏分别都放进 `cases`" in automation_prompt
        and "横切需求 ID" in automation_prompt
        and "不得擅自把横切需求替换成" in automation_prompt
        and "历史成功基线中的文件名、账号名、手机号、订单号" in automation_prompt,
        "Automation filtering must keep bounded reachability automatable and collapse multi-device execution to one current-device case",
    )

    payload = {
        "analysis": {
            "requirement_points": ["REQ-001 照片打印"],
            "requirement_acceptance_checks": [{
                "id": "REQ-001-CHECK-01",
                "requirementId": "REQ-001",
                "branch": "照片打印",
                "kind": "visibility",
                "text": "百度网盘入口可见",
            }],
        },
        "review": {
            "current_page_evidence": [{
                "caseId": "TC-VISUAL-PHOTO-SIBLING",
                "requirementId": "REQ-001 照片打印",
                "branch": "照片打印",
                "pageTitle": "5寸照片",
                "parentPath": ["首页", "照片打印页"],
                "navigationLeaf": "5寸照片",
                "targetText": "百度网盘",
                "sameBranch": True,
                "confidence": 0.90,
                "source": "figma_current_frame",
            }, {
                "caseId": "TC-001",
                "requirementId": "REQ-001 照片打印",
                "branch": "照片打印",
                "pageTitle": "一寸照",
                "parentPath": ["首页"],
                "navigationLeaf": "照片打印页",
                "targetText": "百度网盘",
                "sameBranch": True,
                "confidence": 0.90,
                "source": "page_title_inference",
            }],
        },
        "cases": [{
            "case_id": "TC-001",
            "title": "5寸照片入口校验",
            "requirementRefs": ["REQ-001 照片打印"],
            "steps": ["进入照片打印", "点击5寸照片"],
            "assertions": ["百度网盘可见"],
            "expected_result": "历史候选中的旧位置描述",
        }],
    }
    grounded_plan = {
        "allowedBaselineIds": ["base-photo-6"],
        "scopePlan": {"smokeCount": 1},
        "cases": [{
            "caseId": "TC-001",
            "title": "5寸照片入口校验",
            "baselineId": "base-photo-6",
            "baselineGrounded": True,
            "precondition": "App 首页",
            "flow": [
                "进入照片打印",
                "点击页面内照片打印",
                "点击6寸照片",
                "等待文件列表包含“百度文档测试.doc”和底部“去打印”按钮",
            ],
            "assertionTarget": "百度网盘可见",
            "batch": "smoke",
        }],
    }
    applied = ai_skill_service.apply_executable_yaml_plan_to_payload(payload, grounded_plan)
    applied_steps = applied["cases"][0]["steps"]
    require(
        "点击「5寸照片」" in applied_steps
        and "6寸照片" not in " ".join(applied_steps)
        and "百度文档测试.doc" not in " ".join(applied_steps),
        "A grounded AI path must prefer the current Figma leaf and must not inherit a history-only dynamic file name",
    )
    require(applied["cases"][0]["ai_case_plan"].get("pathPlanApplied"), "Applied AI path plan must be observable")
    require(
        applied["cases"][0]["ai_case_plan"].get("currentVisualLeafAdapted") is True
        and applied.get("review", {}).get("executable_yaml_plan", {}).get("current_visual_leaf_adapted_count") == 1
        and not applied["cases"][0]["ai_case_plan"].get("unsupportedDynamicLiterals"),
        "Current visual adaptation must remain observable and must not leave history-only dynamic literals behind",
    )
    late_leaf_plan = json.loads(json.dumps(grounded_plan, ensure_ascii=False))
    late_leaf_plan["cases"][0]["flow"] = [
        "进入照片打印",
        "点击页面内照片打印",
        "等待百度网盘入口可见",
        "断言百度网盘入口文案正确",
        "点击5寸照片",
    ]
    late_leaf_applied = ai_skill_service.apply_executable_yaml_plan_to_payload(payload, late_leaf_plan)
    late_leaf_steps = late_leaf_applied["cases"][0]["steps"]
    require(
        late_leaf_steps.index("点击5寸照片") < late_leaf_steps.index("等待百度网盘入口可见")
        and late_leaf_applied["cases"][0]["ai_case_plan"].get("currentVisualLeafAdapted") is True,
        "A visual leaf already present after its target assertion must be moved before the first target check",
    )
    grounded_observation, observation_changed, unsupported_literals = ai_skill_service._ground_planner_terminal_observation(
        ["点击百度网盘入口", "等待文件列表包含“百度文档测试.doc”和底部“去打印”按钮"],
        "百度网盘授权页、登录页、内容列表或空态页任一稳定状态可见",
        payload,
    )
    require(
        observation_changed is True
        and unsupported_literals == ["百度文档测试.doc"]
        and grounded_observation[-1] == "等待百度网盘授权页、登录页、内容列表或空态页任一稳定状态可见",
        "A terminal observation copied from historical sample data must be grounded to the current planner assertion contract",
    )
    require(
        applied["cases"][0].get("assertions") == [grounded_plan["cases"][0]["assertionTarget"]]
        and applied["cases"][0].get("expected_result") == grounded_plan["cases"][0]["assertionTarget"],
        "An accepted AI path and its final assertion must be written back as one execution contract instead of retaining stale generated copy",
    )
    wait_only_plan = json.loads(json.dumps(grounded_plan, ensure_ascii=False))
    wait_only_plan["cases"][0]["flow"] = [
        "等待照片打印页面加载完成",
        "等待企业云盘入口可见",
    ]
    wait_only_applied = ai_skill_service.apply_executable_yaml_plan_to_payload(payload, wait_only_plan)
    require(
        wait_only_applied.get("cases", [{}])[0].get("executionLevel") == "needs_review"
        and wait_only_applied.get("review", {}).get("executable_yaml_plan", {}).get("navigation_path_guard_count") == 1,
        "A home-start plan that only waits for a child page must not be marked pathPlanApplied or receive a Runner slot",
    )
    require(
        ai_skill_service._planner_flow_reaches_required_branch(
            {"title": "首页通知横幅展示", "business_path": "App 首页"},
            ["等待首页稳定显示", "等待通知横幅可见"],
            "App 首页",
            ["REQ-009 首页通知横幅展示"],
            [{"requirementId": "REQ-009", "branch": "App 首页", "kind": "visibility"}],
        ),
        "A requirement that explicitly targets the home page must not be forced to invent a navigation action",
    )
    ungrounded_plan = json.loads(json.dumps(grounded_plan, ensure_ascii=False))
    ungrounded_plan["cases"][0]["baselineId"] = "invented-baseline"
    ungrounded_plan["cases"][0]["baselineGrounded"] = False
    not_applied = ai_skill_service.apply_executable_yaml_plan_to_payload(payload, ungrounded_plan)
    require(not_applied["cases"][0]["steps"] == payload["cases"][0]["steps"], "Ungrounded AI baseline citations must not change case paths")

    cross_cutting_case = {
        "case_id": "TC-UI-001",
        "title": "照片打印入口可见文案与同级关系",
        "coverage": "REQ-001 照片打印入口展示",
        "requirementRefs": [
            "REQ-001 照片打印入口展示",
            "REQ-005 多个业务页入口文案与布局一致",
        ],
        "steps": ["进入照片打印", "等待百度网盘入口可见"],
        "assertions": ["百度网盘文案完整，与相册导入在同一入口区域，无遮挡"],
        "executionLevel": "executable",
    }
    source_requirement_ids = ai_skill_service._source_case_requirement_ids(cross_cutting_case)
    require(source_requirement_ids == ["REQ-001", "REQ-005"], "Planner grounding must preserve the union of a branch requirement and its AI-authored cross-cutting requirementRefs")
    cross_refs, cross_guarded = ai_skill_service._ground_planner_requirement_refs(
        cross_cutting_case,
        {"requirementRefs": ["REQ-001 照片打印入口展示", "REQ-005 多个业务页入口文案与布局一致"]},
        ["REQ-001 照片打印入口展示", "REQ-005 多个业务页入口文案与布局一致"],
    )
    require(not cross_guarded and {item.split()[0] for item in cross_refs} == {"REQ-001", "REQ-005"}, "A visible branch case must be allowed to carry its original cross-cutting acceptance mapping")
    _, invented_guarded = ai_skill_service._ground_planner_requirement_refs(
        cross_cutting_case,
        {"requirementRefs": ["REQ-003 扫描复印入口展示"]},
        ["REQ-001 照片打印入口展示", "REQ-003 扫描复印入口展示", "REQ-005 多个业务页入口文案与布局一致"],
    )
    require(invented_guarded, "Cross-cutting support must not weaken the guard against planner-invented cross-branch mappings")
    cross_audit = ai_skill_service.executable_yaml_portfolio_audit(
        {"analysis": {"requirement_points": ["REQ-001 照片打印入口展示", "REQ-005 多个业务页入口文案与布局一致"]}, "cases": [cross_cutting_case]},
        {"min_automation_cases": 1},
    )
    require(cross_audit.get("ok"), "One grounded visible case may satisfy both its branch requirement and an original cross-cutting UI requirement")
    target_shortfall_audit = ai_skill_service.executable_yaml_portfolio_audit(
        {"analysis": {"requirement_points": ["REQ-001 照片打印入口展示", "REQ-005 多个业务页入口文案与布局一致"]}, "cases": [cross_cutting_case]},
        {"min_automation_cases": 5},
    )
    require(
        target_shortfall_audit.get("ok")
        and target_shortfall_audit.get("targetMet") is False
        and target_shortfall_audit.get("targetShortfall") == 4
        and target_shortfall_audit.get("advisories"),
        "A complete executable portfolio must report a 3/5/8 target shortfall without manufacturing low-value cases or failing the gate",
    )

    classified_payload = {
        "analysis": {"requirement_points": ["REQ-001 入口展示"]},
        "cases": [
            {"case_id": "TC-001", "title": "可信短链路", "steps": ["进入首页"]},
            {"case_id": "TC-002", "title": "路径待确认", "steps": ["进入父页面"]},
            {"case_id": "TC-003", "title": "只能人工", "steps": ["触发外部依赖"]},
            {"case_id": "TC-004", "title": "AI 未提及", "steps": ["进入未知页面"]},
        ],
    }
    authoritative_plan = {
        "authoritative": True,
        "allowedBaselineIds": ["base-1"],
        "cases": [
            {"caseId": "TC-001", "title": "可信短链路", "baselineId": "base-1", "baselineGrounded": True, "precondition": "首页", "flow": ["进入首页", "点击入口"], "assertionTarget": "入口可见", "batch": "smoke"},
            {"caseId": "TC-002", "title": "路径待确认", "baselineId": "base-1", "baselineGrounded": True, "precondition": "首页", "flow": ["进入首页", "进入父页面"], "assertionTarget": "目标可见"},
        ],
        "needs_review_cases": [{"caseId": "TC-002", "title": "路径待确认", "reason": "Figma 只有叶子状态，缺少父子导航"}],
        "manual_cases": [{"caseId": "TC-003", "title": "只能人工", "reason": "依赖不可控外部账号"}],
    }
    classified = ai_skill_service.apply_executable_yaml_plan_to_payload(classified_payload, authoritative_plan)
    classified_by_id = {item.get("case_id"): item for item in classified.get("cases") or []}
    require(classified_by_id["TC-001"].get("executionLevel") == "executable", "AI executable classification must remain eligible for the static scorer")
    require(classified_by_id["TC-002"].get("executionLevel") == "needs_review" and not classified_by_id["TC-002"].get("smoke"), "Overlapping AI classifications must resolve to the stricter review level")
    require(classified_by_id["TC-004"].get("executionLevel") == "needs_review", "Cases omitted by a successful AI planner must default to needs_review instead of being silently promoted")
    require(any(item.get("case_id") == "TC-003" and item.get("executionLevel") == "manual" for item in classified.get("manual_cases") or []), "AI manual classification must leave the Runner candidate pool")
    require(classified.get("review", {}).get("executable_yaml_plan", {}).get("classificationApplied") is True and classified["review"]["executable_yaml_plan"].get("unmentioned_count") == 1, "AI planner classification application must remain auditable")

    replan_payload = {
        "analysis": {
            "requirement_points": [
                "REQ-001 入口展示",
                "REQ-002 点击后进入授权页或内容列表",
            ],
        },
        "cases": [{
            "case_id": "TC-001",
            "title": "入口展示",
            "steps": ["进入资料页", "等待外部资料入口可见"],
            "assertions": ["外部资料入口可见"],
            "coverage": "REQ-001 入口展示",
        }],
        "manual_cases": [{
            "title": "点击外部资料入口后的首个落地页",
            "steps": ["进入资料页", "点击外部资料入口", "观察首个落地页"],
            "expected_result": "授权页、登录页或内容列表任一合法页面可见，无白屏或崩溃",
            "reason": "上游认为账号状态不确定",
        }],
    }
    captured_planner_requests = []
    old_run_ai_skill = ai_skill_service.run_ai_skill
    try:
        def fake_executable_planner(skill_name, request, **_kwargs):
            require(skill_name == "executable_yaml_planner", "Unexpected AI skill during executable planner replay")
            captured_planner_requests.append(request)
            automatic = next(item for item in request.get("cases") or [] if item.get("originLevel") == "automatic")
            manual = next(item for item in request.get("cases") or [] if item.get("originLevel") == "manual")
            return {
                "cases": [{
                    "caseId": manual["case_id"],
                    "title": manual["title"],
                    "baselineId": "base-nav",
                    "precondition": "App 首页",
                    "flow": [
                        "等待首页",
                        "进入资料页",
                        "点击外部资料入口",
                        "等待授权页、登录页或内容列表任一合法页面可见",
                    ],
                    "assertionTarget": "授权页、登录页或内容列表任一合法页面可见，且无白屏或崩溃",
                    "requirementRefs": ["REQ-002 点击后进入授权页或内容列表"],
                    "executableReason": "只验证首个可见落地页，不操作账号或第三方数据",
                    "batch": "remaining",
                }],
                "needs_review_cases": [{
                    "caseId": automatic["case_id"],
                    "title": automatic["title"],
                    "reason": "本次仅验证重分类",
                    "requirementRefs": ["REQ-001 入口展示"],
                }],
                "draft_cases": [],
                "manual_cases": [],
                "review": {"planning_reason": "状态无关首个落地页可自动化"},
            }

        ai_skill_service.run_ai_skill = fake_executable_planner
        replan = ai_skill_service.call_skill_executable_yaml_planner(
            "外部资料入口可达性",
            "资料导入",
            replan_payload,
            [{
                "id": "base-nav",
                "title": "资料页稳定导航",
                "businessPath": "首页 -> 资料页",
                "sourceKind": "verified_execution",
                "verificationStatus": "execution_success",
                "trusted": True,
                "baselineUsable": True,
            }],
            {"smokeCount": 1},
            source_evidence={
                "executionContext": {
                    "deviceStrategy": "fixed",
                    "deviceId": "device-one",
                    "singleDeviceOnly": True,
                },
            },
        )
    finally:
        ai_skill_service.run_ai_skill = old_run_ai_skill
    require(
        captured_planner_requests
        and {item.get("originLevel") for item in captured_planner_requests[0].get("cases") or []} == {"automatic", "manual"},
        "Executable planner must receive both automatic and prior-manual candidates for one authoritative reclassification",
    )
    require(
        "manual_cases" not in captured_planner_requests[0]
        and captured_planner_requests[0].get("priorManualCandidateCount") == 1,
        "Executable planner input must not duplicate prior-manual candidates and inflate AI latency",
    )
    require(replan.get("trace", {}).get("manual_candidate_count") == 1, "Executable planner trace must expose prior-manual candidate count")
    replanned = ai_skill_service.apply_executable_yaml_plan_to_payload(replan_payload, replan)
    promoted = next(item for item in replanned.get("cases") or [] if item.get("case_id") == "MC-001")
    require(
        promoted.get("executionLevel") == "executable"
        and promoted.get("originExecutionLevel") == "manual"
        and "REQ-002" in promoted.get("coverage", "")
        and promoted.get("requirementRefs") == ["REQ-002 点击后进入授权页或内容列表"]
        and promoted.get("ai_case_plan", {}).get("pathPlanApplied"),
        "A prior-manual candidate may be promoted only through a grounded AI path with explicit requirement coverage",
    )
    require(not any(item.get("case_id") == "MC-001" for item in replanned.get("manual_cases") or []), "Promoted manual candidates must leave the manual-only pool")
    promoted_yaml = yaml_service.case_to_task_yaml(promoted, indent="    ", case_index=1)
    require(
        "aiTap:" in promoted_yaml
        and "aiWaitFor:" in promoted_yaml
        and "任一合法页面可见" in promoted_yaml
        and "coordinate" not in promoted_yaml.lower(),
        "State-variant reachability must convert into visible-text Midscene actions without coordinates",
    )
    bounded_external = {
        **promoted,
        "steps": [
            "等待首页",
            "进入资料页",
            "点击外部资料入口",
            "等待授权页、登录页或文件选择页任一合法页面可见",
        ],
        "assertions": ["授权页、登录页或文件选择页之一可见，且无白屏或崩溃"],
        "requirementRefs": ["REQ-002 点击后进入授权页或内容列表"],
        "ai_case_plan": {
            **(promoted.get("ai_case_plan") or {}),
            "baselineGrounded": True,
            "pathPlanApplied": True,
        },
    }
    require(
        yaml_service._case_manual_block_reason(bounded_external) == "",
        "A grounded external-entry check that stops at the first observable state must not be reclassified as manual merely for mentioning authorization",
    )
    assertion_terminal_bounded = {
        **bounded_external,
        "case_id": "TC-007",
        "title": "媒体打印云盘入口首个可见页校验",
        "executionLevel": "executable",
        "steps": [
            "等待 App 首页稳定显示媒体打印入口",
            "点击「媒体打印」入口",
            "等待规格列表加载完成",
            "点击「当前规格」",
            "等待云盘入口可见",
            "点击「云盘」入口",
        ],
        "assertions": [
            "点击后出现云盘相关页面、授权弹窗或系统跳转提示之一，且未白屏、未崩溃",
        ],
        "ai_case_plan": {
            **(bounded_external.get("ai_case_plan") or {}),
            "baselineGrounded": True,
            "pathPlanApplied": True,
            "boundedConvergence": {
                "kind": "bounded_landing",
                "acceptanceCheckIds": ["REQ-007-CHECK-04"],
                "modelLevel": "executable",
            },
        },
    }
    require(
        yaml_service._case_manual_block_reason(assertion_terminal_bounded) == "",
        "A separately rendered terminal assertion must count as the post-click observation for a grounded bounded landing case",
    )
    seven_case_payload = {
        "title": "七条已收敛可执行用例",
        "review": {"generation_targets": {"target_automation_cases": 5}},
        "cases": [
            {
                "case_id": f"TC-{index:03d}",
                "title": f"已接受入口校验 {index}",
                "executionLevel": "executable",
                "steps": [f"点击「入口 {index}」", "等待目标页面稳定显示"],
                "assertions": [f"入口 {index} 的目标页面可见"],
            }
            for index in range(1, 7)
        ] + [assertion_terminal_bounded],
        "manual_cases": [],
    }
    seven_ready = yaml_service.split_automation_ready_cases(seven_case_payload)
    _, seven_yamls = yaml_service.cases_to_separate_midscene_yamls(
        seven_ready,
        app_package="com.example.app",
    )
    seven_contract = yaml_service.audit_executable_yaml_conversion(
        seven_case_payload,
        seven_ready,
        seven_yamls,
    )
    require(
        seven_contract.get("passed")
        and seven_contract.get("acceptedExecutableCount") == 7
        and seven_contract.get("yamlCaseIds", [])[-1] == "TC-007"
        and len(seven_yamls) == 7,
        "A late AI convergence candidate must survive Runner eligibility and YAML conversion even when the advisory planning target was five",
    )
    rejected_terminal_payload = json.loads(json.dumps(seven_case_payload, ensure_ascii=False))
    rejected_terminal_payload["cases"][-1]["ai_case_plan"] = {}
    rejected_ready = yaml_service.split_automation_ready_cases(rejected_terminal_payload)
    _, rejected_yamls = yaml_service.cases_to_separate_midscene_yamls(
        rejected_ready,
        app_package="com.example.app",
    )
    rejected_contract = yaml_service.audit_executable_yaml_conversion(
        rejected_terminal_payload,
        rejected_ready,
        rejected_yamls,
    )
    require(
        not rejected_contract.get("passed")
        and rejected_contract.get("missingYamlCaseIds") == ["TC-007"]
        and rejected_contract.get("rejected", [{}])[0].get("stage") == "runner_eligibility",
        "A deterministic Runner gate may reject an accepted case, but conversion must expose and block the partial portfolio instead of silently returning fewer YAML files",
    )
    deep_external = json.loads(json.dumps(bounded_external, ensure_ascii=False))
    deep_external["steps"].extend(["点击同意授权", "输入账号和验证码", "选择文件"])
    require(
        yaml_service._case_manual_block_reason(deep_external),
        "Deep authorization, credential, or file operations must remain blocked from automatic Runner execution",
    )

    bulk_points = [
        "REQ-001 文档打印百度网盘入口展示与可达",
        "REQ-002 照片打印百度网盘入口展示与可达",
        "REQ-003 扫描复印百度网盘入口展示与可达",
    ]
    bulk_payload = {
        "analysis": {
            "requirement_points": bulk_points,
            "requirement_acceptance_checks": [
                {
                    "id": f"REQ-{req:03d}-CHECK-{check:02d}",
                    "requirementId": f"REQ-{req:03d}",
                    "branch": ("文档打印", "照片打印", "扫描复印")[req - 1],
                    "kind": ("visibility", "relation", "copy", "reachability")[check - 1],
                    "text": "百度网盘入口验收" + ("详细说明" * 20),
                }
                for req in range(1, 4)
                for check in range(1, 5)
            ],
            "requirement_contract": {"applied": True, "branch_count": 3},
            "visible_outcomes": ["百度网盘入口与同级入口可见" * 20 for _ in range(20)],
            "visual_notes": ["Figma 当前帧软证据" * 40 for _ in range(30)],
            "ui_notes": ["当前页面布局说明" * 40 for _ in range(30)],
            "risks": ["不应进入执行规划请求的冗长风险" * 100],
        },
        "scenarios": [
            {
                "scenario_id": f"SC-{index + 1:03d}",
                "scenario": f"业务场景 {index + 1}" + ("场景说明" * 100),
                "requirement_point": bulk_points[index % 3],
                "business_path": "首页 -> 业务入口 -> 百度网盘",
                "unused": "不应发送" * 500,
            }
            for index in range(20)
        ],
        "cases": [
            {
                "case_id": f"TC-{index + 1:03d}",
                "title": f"自动候选 {index + 1}",
                "coverage": bulk_points[index % 3],
                "requirementRefs": [bulk_points[index % 3]],
                "steps": ["等待首页", "点击业务入口", "等待百度网盘入口可见"],
                "assertions": ["百度网盘入口可见"],
            }
            for index in range(8)
        ],
        "manual_cases": [
            {
                "case_id": f"MC-{index + 1:03d}",
                "title": f"上游人工项 {index + 1}",
                "coverage": bulk_points[index % 3],
                "steps": ["准备外部状态", "人工观察结果"],
                "reason": "上游 AI 已判定需要人工环境",
            }
            for index in range(12)
        ],
    }
    bulk_calls = []
    old_run_ai_skill = ai_skill_service.run_ai_skill
    try:
        def fake_bulk_planner(skill_name, request, **kwargs):
            require(skill_name == "executable_yaml_planner", "Unexpected AI skill during planner truncation replay")
            bulk_calls.append({"request": request, "kwargs": kwargs})
            if len(bulk_calls) == 1:
                raise RuntimeError(
                    "AI Gateway HTTP 500: Structured output truncated: "
                    "finish_reason=length, completion_tokens=4096"
                )
            return {
                "cases": [],
                "needs_review_cases": [],
                "draft_cases": [],
                "manual_cases": [
                    {
                        "caseId": item.get("case_id"),
                        "reason": "当前证据不足，保留人工终态",
                        "requirementRefs": item.get("requirementRefs") or [],
                    }
                    for item in request.get("cases") or []
                ],
                "review": {"planning_reason": "完成有界分类"},
            }

        ai_skill_service.run_ai_skill = fake_bulk_planner
        bulk_plan = ai_skill_service.call_skill_executable_yaml_planner(
            "批量入口覆盖",
            "基础打印",
            bulk_payload,
            [{
                "id": "base-nav",
                "title": "可信入口导航",
                "sourceKind": "verified_execution",
                "verificationStatus": "execution_success",
                "businessPath": "首页 -> 打印入口",
            }],
            {"smokeCount": 3, "targetCaseCount": 5},
            model_config={"providerId": "qwen_plus", "model": "qwen3.6-plus"},
            source_evidence={
                "requirementText": "原始需求" * 4000,
                "figmaSoftEvidence": "Figma 软证据" * 4000,
                "visualBatchJudgements": [
                    {"batch": index + 1, "judgement": "当前帧判断" * 100}
                    for index in range(4)
                ],
                "executionContext": {"deviceStrategy": "fixed", "deviceId": "oppo-one"},
                "unusedLargeField": "不应发送" * 4000,
            },
        )
    finally:
        ai_skill_service.run_ai_skill = old_run_ai_skill
    first_bulk_request = bulk_calls[0]["request"]
    require(
        len(bulk_calls) == 2
        and len(first_bulk_request.get("cases") or []) == 8
        and {item.get("originLevel") for item in first_bulk_request.get("cases") or []} == {"automatic"}
        and first_bulk_request.get("priorManualCandidateCount") == 12
        and first_bulk_request.get("includedManualCandidateCount") == 0,
        "A full eight-case planner pass must defer already-manual candidates instead of forcing 20 verbose classifications into one response",
    )
    require(
        len(json.dumps(first_bulk_request, ensure_ascii=False)) < 30000
        and len((first_bulk_request.get("sourceEvidence") or {}).get("requirementText") or "") <= 6000
        and len((first_bulk_request.get("sourceEvidence") or {}).get("figmaSoftEvidence") or "") <= 6000
        and "risks" not in (first_bulk_request.get("analysis") or {})
        and all("unused" not in item for item in first_bulk_request.get("scenarios") or []),
        "Executable planning must retain semantic contracts while compacting repeated display context before the model call",
    )
    require(
        bulk_calls[0]["kwargs"].get("max_tokens") == ai_skill_service.AI_EXECUTABLE_YAML_PLANNER_MAX_TOKENS
        and bulk_calls[1]["kwargs"].get("max_tokens") == ai_skill_service.AI_EXECUTABLE_YAML_PLANNER_RETRY_MAX_TOKENS
        and bulk_calls[1]["kwargs"].get("model_config") == bulk_calls[0]["kwargs"].get("model_config")
        and bulk_plan.get("authoritative") is True
        and len(bulk_plan.get("manual_cases") or []) == 8
        and bulk_plan.get("trace", {}).get("truncation_retry", {}).get("succeeded") is True
        and bulk_plan.get("trace", {}).get("deferred_manual_candidate_count") == 12,
        "A length-truncated structured response must receive one compact retry on the same selected model and return terminal classifications",
    )

    relation_points = [
        "REQ-002 照片打印：百度网盘入口展示、同级关系、文案及可达页面",
        "REQ-003 扫描复印：百度网盘入口展示、同级关系、文案及可达页面",
    ]
    relation_payload = {
        "analysis": {
            "requirement_points": relation_points,
            "requirement_acceptance_checks": [
                {
                    "id": "REQ-002-CHECK-02",
                    "requirementId": "REQ-002",
                    "branch": "照片打印",
                    "kind": "relation",
                    "text": "校验百度网盘入口与当前页面同级入口的层级和位置关系",
                },
                {
                    "id": "REQ-003-CHECK-02",
                    "requirementId": "REQ-003",
                    "branch": "扫描复印",
                    "kind": "relation",
                    "text": "校验百度网盘入口与当前页面同级入口的层级和位置关系",
                },
            ],
        },
        "cases": [
            {
                "case_id": "TC-PHOTO",
                "title": "照片打印百度网盘入口可见",
                "coverage": relation_points[0],
                "requirementRefs": [relation_points[0]],
                "steps": ["等待首页", "点击照片打印", "等待百度网盘入口可见"],
                "assertions": ["百度网盘入口可见"],
                "executionLevel": "executable",
                "ai_case_plan": {
                    "baselineId": "base-photo",
                    "baselineGrounded": True,
                    "precondition": "App 首页",
                    "flow": ["等待首页", "点击照片打印", "等待百度网盘入口可见"],
                    "assertionTarget": "百度网盘入口可见",
                    "batch": "smoke",
                },
            },
            {
                "case_id": "TC-SCAN",
                "title": "扫描复印百度网盘入口可见",
                "coverage": relation_points[1],
                "requirementRefs": [relation_points[1]],
                "steps": ["等待首页", "点击文件扫描/复印", "等待百度网盘入口可见"],
                "assertions": ["百度网盘入口可见"],
                "executionLevel": "executable",
                "ai_case_plan": {
                    "baselineId": "base-scan",
                    "baselineGrounded": True,
                    "precondition": "App 首页",
                    "flow": ["等待首页", "点击文件扫描/复印", "等待百度网盘入口可见"],
                    "assertionTarget": "百度网盘入口可见",
                    "batch": "smoke",
                },
            },
        ],
        "manual_cases": [
            {
                "case_id": "MC-PHOTO",
                "title": "照片打印动态加载位置检查",
                "steps": ["进入照片打印", "人工观察入口位置"],
                "assertions": ["入口位置稳定"],
            },
            {
                "case_id": "MC-SCAN",
                "title": "扫描复印多入口布局检查",
                "steps": ["进入扫描复印", "人工观察同级入口"],
                "assertions": ["入口同级展示"],
            },
        ],
    }
    relation_audit = ai_skill_service.executable_yaml_portfolio_audit(
        relation_payload,
        {"min_automation_cases": 2},
    )
    relation_requests = []
    old_run_ai_skill = ai_skill_service.run_ai_skill
    try:
        def fake_relation_planner(skill_name, request, **_kwargs):
            require(skill_name == "executable_yaml_planner", "Unexpected AI skill during relation-gap replay")
            relation_requests.append(request)
            return {
                "cases": [],
                "needs_review_cases": [],
                "draft_cases": [],
                "manual_cases": [
                    {"caseId": item.get("case_id"), "reason": "仅验证候选聚焦"}
                    for item in request.get("cases") or []
                ],
                "review": {},
            }

        ai_skill_service.run_ai_skill = fake_relation_planner
        ai_skill_service.call_skill_executable_yaml_planner(
            "兄弟分支关系收敛",
            "基础打印",
            relation_payload,
            [
                {"id": "base-photo", "title": "照片入口导航", "sourceKind": "verified_execution", "verificationStatus": "execution_success"},
                {"id": "base-scan", "title": "扫描入口导航", "sourceKind": "verified_execution", "verificationStatus": "execution_success"},
            ],
            {"smokeCount": 2, "targetCaseCount": 2},
            planning_context={"pass": "coverage_convergence", "portfolioAudit": relation_audit},
        )
    finally:
        ai_skill_service.run_ai_skill = old_run_ai_skill
    relation_request = relation_requests[0]
    relation_focus = (relation_request.get("planningContext") or {}).get("focus") or {}
    relation_candidates = {
        item.get("case_id"): item for item in relation_request.get("cases") or []
    }
    require(
        set(relation_candidates)
        == {"TC-PHOTO", "TC-SCAN", "MC-PHOTO", "MC-SCAN"}
        and set(relation_focus.get("repairableExecutableCandidateIds") or [])
        == {"TC-PHOTO", "TC-SCAN"}
        and [item.get("id") for item in relation_candidates["TC-PHOTO"].get("repairAcceptanceChecks") or []]
        == ["REQ-002-CHECK-02"]
        and [item.get("id") for item in relation_candidates["TC-SCAN"].get("repairAcceptanceChecks") or []]
        == ["REQ-003-CHECK-02"],
        "Convergence must offer the AI the executable from each missing REQ branch plus only same-branch manual alternatives",
    )
    split_reachability_payload = {
        "analysis": {
            "requirement_points": [
                "REQ-001 文档打印：百度网盘入口展示、同级、文案及可达页面",
                "REQ-002 照片打印：百度网盘入口展示、同级、文案及可达页面",
            ],
            "requirement_acceptance_checks": [
                {
                    "id": "REQ-001-CHECK-04",
                    "requirementId": "REQ-001",
                    "branch": "文档打印",
                    "kind": "reachability",
                    "text": "点击百度网盘入口并校验目标页面稳定可达",
                },
                {
                    "id": "REQ-002-CHECK-04",
                    "requirementId": "REQ-002",
                    "branch": "照片打印",
                    "kind": "reachability",
                    "text": "点击百度网盘入口并校验目标页面稳定可达",
                },
            ],
        },
        "cases": [
            {
                "case_id": "TC-DOC",
                "title": "文档打印百度网盘入口展示与文案",
                "coverage": "REQ-001 文档打印：百度网盘入口展示、同级、文案及可达页面",
                "requirementRefs": ["REQ-001 文档打印：百度网盘入口展示、同级、文案及可达页面"],
                "steps": ["等待首页", "点击文档打印", "等待百度网盘入口可见"],
                "assertions": ["文档打印页百度网盘入口可见，文案显示为百度网盘"],
                "executionLevel": "executable",
                "ai_case_plan": {
                    "baselineId": "base-doc",
                    "baselineGrounded": True,
                    "precondition": "App 首页",
                    "flow": ["等待首页", "点击文档打印", "等待百度网盘入口可见"],
                    "assertionTarget": "文档打印页百度网盘入口可见，文案显示为百度网盘",
                    "batch": "smoke",
                },
            },
            {
                "case_id": "TC-PHOTO",
                "title": "照片打印百度网盘入口展示与文案",
                "coverage": "REQ-002 照片打印：百度网盘入口展示、同级、文案及可达页面",
                "requirementRefs": ["REQ-002 照片打印：百度网盘入口展示、同级、文案及可达页面"],
                "steps": ["等待首页", "点击照片打印", "等待百度网盘入口可见"],
                "assertions": ["照片打印页百度网盘入口可见，文案显示为百度网盘"],
                "executionLevel": "executable",
                "ai_case_plan": {
                    "baselineId": "base-photo",
                    "baselineGrounded": True,
                    "precondition": "App 首页",
                    "flow": ["等待首页", "点击照片打印", "等待百度网盘入口可见"],
                    "assertionTarget": "照片打印页百度网盘入口可见，文案显示为百度网盘",
                    "batch": "smoke",
                },
            },
            {
                "case_id": "TC-GENERIC-AUTH",
                "title": "首次点击百度网盘入口触发授权流程",
                "coverage": "REQ-001/002 点击百度网盘入口并校验目标页面稳定可达",
                "requirementRefs": [
                    "REQ-001 文档打印：百度网盘入口展示、同级、文案及可达页面",
                    "REQ-002 照片打印：百度网盘入口展示、同级、文案及可达页面",
                ],
                "steps": ["任意打印子页面", "点击百度网盘", "检测授权窗"],
                "assertions": ["若未绑定账号，应弹出授权窗"],
                "executionLevel": "needs_review",
            },
        ],
        "manual_cases": [],
    }
    split_reachability_audit = ai_skill_service.executable_yaml_portfolio_audit(
        split_reachability_payload,
        {"min_automation_cases": 2},
    )
    split_automatic_records = [
        {
            "raw": item,
            "compact": ai_skill_service._compact_case_for_plan(item, index, origin_level="automatic"),
            "origin": "automatic",
        }
        for index, item in enumerate(split_reachability_payload["cases"])
    ]
    split_auto, _, split_context, split_focus = ai_skill_service._focus_executable_convergence_candidates(
        split_reachability_payload,
        split_automatic_records,
        [],
        {"pass": "coverage_convergence", "portfolioAudit": split_reachability_audit},
    )
    split_by_id = {item.get("case_id"): item for item in split_auto}
    require(
        set(split_focus.get("repairableExecutableCandidateIds") or []) == {"TC-DOC", "TC-PHOTO"}
        and set(split_by_id) == {"TC-DOC", "TC-PHOTO", "TC-GENERIC-AUTH"}
        and [item.get("id") for item in split_by_id["TC-DOC"].get("repairAcceptanceChecks") or []]
        == ["REQ-001-CHECK-04"]
        and [item.get("id") for item in split_by_id["TC-PHOTO"].get("repairAcceptanceChecks") or []]
        == ["REQ-002-CHECK-04"]
        and split_context.get("focus", {}).get("policy"),
        "When AI splits explicit reachability into a generic risk flow, convergence must still repair each main branch executable instead of preserving it",
    )
    guarded_repair_payload = {
        "analysis": {
            "requirement_points": [
                "REQ-002 照片打印：百度网盘入口展示、同级、文案及可达页面",
            ],
            "requirement_acceptance_checks": [
                {
                    "id": "REQ-002-CHECK-01",
                    "requirementId": "REQ-002",
                    "branch": "照片打印",
                    "kind": "visibility",
                    "text": "校验百度网盘入口可见",
                },
                {
                    "id": "REQ-002-CHECK-04",
                    "requirementId": "REQ-002",
                    "branch": "照片打印",
                    "kind": "reachability",
                    "text": "点击百度网盘入口并校验目标页面稳定可达",
                },
            ],
        },
        "cases": [{
            "case_id": "TC-PHOTO-GUARDED",
            "title": "照片打印百度网盘入口展示",
            "coverage": "REQ-002 照片打印：百度网盘入口展示、同级、文案及可达页面",
            "requirementRefs": ["REQ-002 照片打印：百度网盘入口展示、同级、文案及可达页面"],
            "steps": ["等待首页", "点击照片打印", "等待百度网盘入口可见"],
            "assertions": ["照片打印页百度网盘入口可见"],
            "executionLevel": "executable",
            "ai_case_plan": {
                "baselineId": "base-photo",
                "baselineGrounded": True,
                "baselineVerified": True,
                "precondition": "App 首页",
                "flow": ["等待首页", "点击照片打印", "等待百度网盘入口可见"],
                "assertionTarget": "照片打印页百度网盘入口可见",
                "batch": "smoke",
                "pathPlanApplied": True,
            },
        }],
        "manual_cases": [],
    }
    guarded_repair_plan = {
        "authoritative": True,
        "cases": [{
            "caseId": "TC-PHOTO-GUARDED",
            "baselineId": "base-photo",
            "baselineGrounded": True,
            "precondition": "App 首页",
            "flow": [
                "等待首页",
                "点击照片打印",
                "点击百度网盘入口",
                "等待「abc123.pdf」文件可见",
            ],
            "assertionTarget": "「abc123.pdf」文件可见",
            "requirementRefs": ["REQ-002 照片打印：百度网盘入口展示、同级、文案及可达页面"],
            "batch": "smoke",
        }],
        "needs_review_cases": [],
        "draft_cases": [],
        "manual_cases": [],
        "selectedBaselines": [{
            "id": "base-photo",
            "title": "照片打印成功基线",
            "sourceKind": "verified_execution",
            "verificationStatus": "execution_success",
        }],
        "allowedBaselineIds": ["base-photo"],
        "verifiedBaselineIds": ["base-photo"],
        "planningContext": {"pass": "coverage_convergence"},
        "focusedCandidateIds": ["TC-PHOTO-GUARDED"],
        "convergenceFocus": {
            "repairableExecutableCandidateIds": ["TC-PHOTO-GUARDED"],
            "focusedCandidateIds": ["TC-PHOTO-GUARDED"],
        },
        "preserveContractByCaseId": {},
        "scopePlan": {"smokeCount": 1, "targetCaseCount": 1},
    }
    guarded_repair_applied = ai_skill_service.apply_executable_yaml_plan_to_payload(
        guarded_repair_payload,
        guarded_repair_plan,
    )
    guarded_repair_case = next(
        item for item in guarded_repair_applied.get("cases") or []
        if item.get("case_id") == "TC-PHOTO-GUARDED"
    )
    require(
        guarded_repair_case.get("executionLevel") == "executable"
        and "abc123.pdf" not in json.dumps(guarded_repair_case, ensure_ascii=False)
        and (guarded_repair_applied.get("review", {}).get("executable_yaml_plan", {}) or {}).get("convergence_repair_restore_count") == 1,
        "A guarded rewrite of an existing repairable executable must restore the prior executable path instead of regressing covered acceptance",
    )
    semantic_calls = []
    old_run_ai_skill = ai_skill_service.run_ai_skill
    try:
        def fake_semantic_repair_planner(skill_name, request, **_kwargs):
            require(skill_name == "executable_yaml_planner", "Unexpected AI skill during semantic repair replay")
            semantic_calls.append(request)
            candidates_by_id = {
                item.get("case_id"): item for item in request.get("cases") or []
            }
            if len(semantic_calls) == 1:
                return {
                    "cases": [
                        {
                            "caseId": "TC-PHOTO",
                            "baselineId": "base-photo",
                            "precondition": "App 首页",
                            "flow": ["等待首页", "点击照片打印", "校验百度网盘与相册导入同级并列"],
                            "assertionTarget": "百度网盘与相册导入同级并列展示",
                            "requirementRefs": [relation_points[0]],
                            "batch": "smoke",
                        },
                        {
                            "caseId": "TC-SCAN",
                            "baselineId": "base-scan",
                            "precondition": "App 首页",
                            "flow": ["等待首页", "点击文件扫描/复印", "等待百度网盘入口可见"],
                            "assertionTarget": "百度网盘入口可见",
                            "requirementRefs": [relation_points[1]],
                            "batch": "smoke",
                        },
                    ],
                    "needs_review_cases": [],
                    "draft_cases": [],
                    "manual_cases": [
                        {"caseId": case_id, "reason": "保留人工"}
                        for case_id in ("MC-PHOTO", "MC-SCAN")
                        if case_id in candidates_by_id
                    ],
                    "review": {"planning_reason": "首次返回漏掉扫描关系断言"},
                }
            require(
                set(candidates_by_id) == {"TC-SCAN"}
                and (request.get("responseContract") or {}).get("acceptanceRepairRetry") is True
                and ((request.get("planningContext") or {}).get("repairValidationFeedback") or [])[0].get("caseId") == "TC-SCAN",
                "Semantic repair retry must contain only the executable candidate that failed its local acceptance contract",
            )
            return {
                "cases": [{
                    "caseId": "TC-SCAN",
                    "baselineId": "base-scan",
                    "precondition": "App 首页",
                    "flow": ["等待首页", "点击文件扫描/复印", "校验百度网盘与其他导入方式同级并列"],
                    "assertionTarget": "百度网盘与其他导入方式在当前页面同级并列展示",
                    "requirementRefs": [relation_points[1]],
                    "batch": "smoke",
                }],
                "needs_review_cases": [],
                "draft_cases": [],
                "manual_cases": [],
                "review": {"planning_reason": "已补齐扫描关系断言"},
            }

        ai_skill_service.run_ai_skill = fake_semantic_repair_planner
        semantic_plan = ai_skill_service.call_skill_executable_yaml_planner(
            "兄弟分支关系收敛",
            "基础打印",
            relation_payload,
            [
                {"id": "base-photo", "title": "照片入口导航", "sourceKind": "verified_execution", "verificationStatus": "execution_success"},
                {"id": "base-scan", "title": "扫描入口导航", "sourceKind": "verified_execution", "verificationStatus": "execution_success"},
            ],
            {"smokeCount": 2, "targetCaseCount": 2},
            model_config={"providerId": "qwen_plus", "model": "qwen3.6-plus"},
            planning_context={"pass": "coverage_convergence", "portfolioAudit": relation_audit},
        )
    finally:
        ai_skill_service.run_ai_skill = old_run_ai_skill
    semantic_scan = next(
        item for item in semantic_plan.get("cases") or []
        if item.get("caseId") == "TC-SCAN"
    )
    require(
        len(semantic_calls) == 2
        and semantic_plan.get("trace", {}).get("acceptance_repair_retry", {}).get("succeeded") is True
        and "同级并列" in " ".join(semantic_scan.get("flow") or [])
        and "同级并列" in str(semantic_scan.get("assertionTarget") or ""),
        "An executable AI result that only claims coverage in review must receive one same-model semantic correction before the gate",
    )
    preserved_contract_feedback = ai_skill_service._executable_plan_repair_feedback(
        {
            "cases": [{
                "caseId": "TC-ATOMIC",
                "flow": [
                    "等待首页",
                    "点击照片打印",
                    "点击百度网盘入口",
                    "等待授权页、文件列表或系统弹窗之一可见",
                ],
                "assertionTarget": "授权页、文件列表或系统弹窗之一可见，无Crash或白屏",
                "requirementRefs": [relation_points[0]],
            }],
        },
        [{
            "case_id": "TC-ATOMIC",
            "requiredAcceptanceChecks": [{
                "id": "REQ-002-CHECK-02",
                "requirementId": "REQ-002",
                "branch": "照片打印",
                "kind": "relation",
                "text": "校验百度网盘入口与当前页面同级入口的层级和位置关系",
                "contractRoles": ["preserve"],
            }, {
                "id": "REQ-002-CHECK-04",
                "requirementId": "REQ-002",
                "branch": "照片打印",
                "kind": "reachability",
                "text": "点击百度网盘入口并校验目标页面稳定可达",
                "contractRoles": ["repair"],
            }],
        }],
    )
    require(
        len(preserved_contract_feedback) == 1
        and preserved_contract_feedback[0].get("missingPreservedCheckIds") == ["REQ-002-CHECK-02"]
        and [
            item.get("id") for item in preserved_contract_feedback[0].get("missingChecks") or []
        ] == ["REQ-002-CHECK-02"],
        "A convergence rewrite that closes a new gap but drops prior coverage must receive candidate-local semantic feedback before atomic portfolio application",
    )
    atomic_reachability_check = {
        "id": "REQ-002-CHECK-04",
        "requirementId": "REQ-002",
        "branch": "照片打印",
        "kind": "reachability",
        "text": "点击百度网盘入口并校验目标页面稳定可达",
    }
    atomic_preservation_payload = {
        "analysis": {
            "requirement_points": [relation_points[0]],
            "requirement_acceptance_checks": [
                relation_payload["analysis"]["requirement_acceptance_checks"][0],
                atomic_reachability_check,
            ],
        },
        "review": {
            "current_page_evidence": [{
                "caseId": "TC-ATOMIC",
                "requirementId": "REQ-002",
                "branch": "照片打印",
                "pageTitle": "5寸照片",
                "parentPath": ["照片打印"],
                "navigationLeaf": "5寸照片",
                "targetText": "百度网盘",
                "sameBranch": True,
                "confidence": 0.99,
                "source": "figma_current_frame",
            }],
        },
        "cases": [{
            "case_id": "TC-ATOMIC",
            "title": "照片打印百度网盘入口同级关系及可达性校验",
            "coverage": relation_points[0],
            "requirementRefs": [relation_points[0]],
            "executionLevel": "executable",
            "steps": [
                "等待 App 首页稳定显示",
                "点击底部 Tab「照片打印」",
                "点击「照片打印」入口",
                "点击「5寸照片」",
                "等待「百度网盘」入口可见",
                "校验「百度网盘」入口与「相机拍照」同级并列展示",
            ],
            "assertions": ["「百度网盘」入口与「相机拍照」同级并列展示"],
            "ai_case_plan": {
                "baselineId": "base-photo",
                "baselineGrounded": True,
                "pathPlanApplied": True,
                "precondition": "App 首页",
                "flow": [
                    "等待 App 首页稳定显示",
                    "点击底部 Tab「照片打印」",
                    "点击「照片打印」入口",
                    "点击「5寸照片」",
                    "等待「百度网盘」入口可见",
                    "校验「百度网盘」入口与「相机拍照」同级并列展示",
                ],
                "assertionTarget": "「百度网盘」入口与「相机拍照」同级并列展示",
                "batch": "smoke",
            },
        }],
        "manual_cases": [],
    }
    atomic_preservation_audit = ai_skill_service.executable_yaml_portfolio_audit(
        atomic_preservation_payload,
        {"min_automation_cases": 1},
    )
    atomic_preservation_calls = []
    old_run_ai_skill = ai_skill_service.run_ai_skill
    try:
        def fake_atomic_preservation_planner(skill_name, request, **_kwargs):
            require(skill_name == "executable_yaml_planner", "Unexpected AI skill in atomic preservation replay")
            atomic_preservation_calls.append(request)
            return {
                "cases": [{
                    "title": "照片打印百度网盘入口同级关系及可达性校验",
                    "baselineId": "base-photo",
                    "precondition": "App 首页",
                    "flow": [
                        "校验「百度网盘」入口与「相机拍照」同级并列展示",
                        "点击底部 Tab「照片打印」",
                        "点击「照片打印」入口",
                        "点击「5寸照片」",
                        "等待「百度网盘」入口可见",
                        "点击「百度网盘」入口",
                        "校验「百度网盘」入口与「相机拍照」同级并列展示",
                        "等待百度网盘授权页稳定可见",
                    ],
                    "assertionTarget": "百度网盘授权页稳定可见",
                    "requirementRefs": [relation_points[0]],
                    "executableReason": "AI 补齐点击后的首个稳定可见终态",
                    "batch": "remaining",
                }],
                "needs_review_cases": [],
                "draft_cases": [],
                "manual_cases": [],
                "review": {"planning_reason": "模拟新增可达性时漏回既有同级关系"},
            }

        ai_skill_service.run_ai_skill = fake_atomic_preservation_planner
        atomic_preservation_plan = ai_skill_service.call_skill_executable_yaml_planner(
            "照片打印百度网盘入口",
            "基础打印",
            atomic_preservation_payload,
            [{
                "id": "base-photo",
                "title": "照片打印成功导航",
                "sourceKind": "verified_execution",
                "verificationStatus": "execution_success",
                "businessPath": "App 首页 -> 照片打印 -> 6寸照片",
                "snippet": (
                    "# baseline.start_page: App 首页\n"
                    "- aiTap: 照片打印 icon\n"
                    "- aiTap: 照片打印\n"
                    "- aiTap: 6寸照片\n"
                    "- aiWaitFor: 百度网盘入口可见"
                ),
            }],
            {"smokeCount": 1},
            model_config={"providerId": "qwen_plus", "model": "qwen3.6-plus"},
            planning_context={
                "pass": "coverage_convergence",
                "portfolioAudit": atomic_preservation_audit,
            },
        )
    finally:
        ai_skill_service.run_ai_skill = old_run_ai_skill
    atomic_preservation_case = atomic_preservation_plan.get("cases", [{}])[0]
    atomic_preservation_flow = atomic_preservation_case.get("flow") or []
    atomic_preservation_applied = ai_skill_service.apply_executable_yaml_plan_to_payload(
        atomic_preservation_payload,
        atomic_preservation_plan,
    )
    atomic_preservation_applied_case = atomic_preservation_applied.get("cases", [{}])[0]
    atomic_preservation_applied_flow = atomic_preservation_applied_case.get("steps") or []
    require(
        len(atomic_preservation_calls) == 1
        and atomic_preservation_plan.get("trace", {}).get(
            "preserved_acceptance_contract", {}
        ).get("case_ids") == ["TC-ATOMIC"]
        and atomic_preservation_flow.index(
            "校验「百度网盘」入口与「相机拍照」同级并列展示"
        ) < atomic_preservation_flow.index("点击「百度网盘」入口")
        and atomic_preservation_flow.index(
            "校验「百度网盘」入口与「相机拍照」同级并列展示"
        ) > atomic_preservation_flow.index("点击「5寸照片」")
        and atomic_preservation_applied_flow.index(
            "校验「百度网盘」入口与「相机拍照」同级并列展示"
        ) < atomic_preservation_applied_flow.index("点击「百度网盘」入口")
        and atomic_preservation_applied_flow.index(
            "校验「百度网盘」入口与「相机拍照」同级并列展示"
        ) > atomic_preservation_applied_flow.index("点击「5寸照片」")
        and atomic_preservation_applied_flow.count(
            "校验「百度网盘」入口与「相机拍照」同级并列展示"
        ) == 1
        and atomic_preservation_applied_case.get("ai_case_plan", {}).get(
            "trustedBaselineNavigationAdapted"
        ) is True
        and ai_skill_service.executable_yaml_portfolio_audit(
            atomic_preservation_applied,
            {"min_automation_cases": 1},
        ).get("ok"),
        "A convergence reachability delta must retain already-proven source-page acceptance before the target click without another model call",
    )
    unsafe_preservation_evidence = [
        "校验「百度网盘」入口未与「相机拍照」同级并列展示",
        "若可见，确认「百度网盘」入口与「相机拍照」同级并列展示",
        "确认点击「百度网盘」入口后仍与「相机拍照」同级并列展示",
        "校验「百度网盘」入口不显示且与「相机拍照」同级并列展示",
        "校验「百度网盘」与「相机拍照」的层级关系错误",
        "校验「百度网盘」入口文案错误且与「相机拍照」同级并列展示",
        "确认跳转到「百度网盘」后仍与「相机拍照」同级并列展示",
        "校验「百度网盘」与「相机拍照」同级关系有误",
        "校验「百度网盘」与「相机拍照」并列位置存在偏差",
        "校验「百度网盘」入口文案乱码且与「相机拍照」同级展示",
        "校验「百度网盘」 copy incorrect 且与「相机拍照」同级展示",
        "校验「百度网盘」与「相机拍照」的层级 mismatch",
        "校验「百度网盘」文案 truncated 且与「相机拍照」并列展示",
        "确认 navigate to 「百度网盘」后仍与「相机拍照」同级并列展示",
        "校验「百度网盘」与「相机拍照」同级关系颠倒",
    ]
    for unsafe_evidence in unsafe_preservation_evidence:
        unsafe_result, unsafe_trace = (
            ai_skill_service._preserve_existing_acceptance_contract_in_plan(
                {
                    "cases": [{
                        "caseId": "TC-UNSAFE-PRESERVE",
                        "flow": [
                            "等待照片打印页面稳定显示",
                            "点击「百度网盘」入口",
                            "等待授权页稳定可见",
                        ],
                        "assertionTarget": "授权页稳定可见",
                        "requirementRefs": [relation_points[0]],
                    }],
                },
                [{
                    "case_id": "TC-UNSAFE-PRESERVE",
                    "requiredAcceptanceChecks": [{
                        **relation_payload["analysis"]["requirement_acceptance_checks"][0],
                        "contractRoles": ["preserve"],
                    }],
                    "assertions": [unsafe_evidence],
                }],
            )
        )
        require(
            not unsafe_trace.get("case_ids")
            and unsafe_result.get("cases", [{}])[0].get("flow") == [
                "等待照片打印页面稳定显示",
                "点击「百度网盘」入口",
                "等待授权页稳定可见",
            ],
            "Negative, conditional, or compound-navigation text must never become platform-owned preserve evidence",
        )
    visibility_preserve_check = {
        "id": "REQ-002-CHECK-01",
        "requirementId": "REQ-002",
        "branch": "照片打印",
        "kind": "visibility",
        "text": "校验百度网盘入口可见",
        "contractRoles": ["preserve"],
    }
    for unsafe_visibility_evidence in (
        "校验「百度网盘」入口存在问题",
        "校验「百度网盘」入口显示“not visible”",
    ):
        unsafe_visibility_result, unsafe_visibility_trace = (
            ai_skill_service._preserve_existing_acceptance_contract_in_plan(
                {
                    "cases": [{
                        "caseId": "TC-UNSAFE-VISIBILITY",
                        "flow": [
                            "等待照片打印页面稳定显示",
                            "点击「百度网盘」入口",
                            "等待授权页稳定可见",
                        ],
                        "assertionTarget": "授权页稳定可见",
                        "requirementRefs": [relation_points[0]],
                    }],
                },
                [{
                    "case_id": "TC-UNSAFE-VISIBILITY",
                    "requiredAcceptanceChecks": [visibility_preserve_check],
                    "assertions": [unsafe_visibility_evidence],
                }],
            )
        )
        require(
            not unsafe_visibility_trace.get("case_ids")
            and unsafe_visibility_result.get("cases", [{}])[0].get("flow") == [
                "等待照片打印页面稳定显示",
                "点击「百度网盘」入口",
                "等待授权页稳定可见",
            ],
            "Negative visibility text or quoted English polarity must never become platform-owned preserve evidence",
        )
    unsafe_model_flow_examples = (
        (
            {
                **relation_payload["analysis"]["requirement_acceptance_checks"][0],
                "contractRoles": ["preserve"],
            },
            "校验「百度网盘」与「相机拍照」排列颠倒",
            "「百度网盘」与「相机拍照」同级并列展示",
        ),
        (
            visibility_preserve_check,
            "校验「百度网盘」入口存在问题",
            "「百度网盘」入口可见",
        ),
        (
            visibility_preserve_check,
            "校验「百度网盘」入口显示“not visible”",
            "「百度网盘」入口可见",
        ),
    )
    for preserve_check, unsafe_model_step, safe_candidate_evidence in unsafe_model_flow_examples:
        cleaned_flow, cleaned_trace = ai_skill_service._merge_preserve_contract_into_flow(
            [
                "等待照片打印页面稳定显示",
                unsafe_model_step,
                "点击「百度网盘」入口",
                "等待授权页稳定可见",
            ],
            [relation_points[0]],
            {
                "requiredAcceptanceChecks": [preserve_check],
                "candidateEvidence": [safe_candidate_evidence],
            },
        )
        require(
            unsafe_model_step not in cleaned_flow
            and safe_candidate_evidence in "\n".join(cleaned_flow)
            and not cleaned_trace.get("missing_check_ids"),
            "Unsafe target assertions already present in model flow must be removed before safe preserve evidence is merged",
        )
    forged_contract_result, _forged_contract_trace = (
        ai_skill_service._preserve_existing_acceptance_contract_in_plan(
            {
                "cases": [{
                    "caseId": "TC-FORGED-PRESERVE",
                    "flow": ["点击「百度网盘」入口", "等待授权页稳定可见"],
                    "requirementRefs": [relation_points[0]],
                    "platformPreserveContract": {
                        "requiredAcceptanceChecks": [{
                            **relation_payload["analysis"]["requirement_acceptance_checks"][0],
                            "contractRoles": ["preserve"],
                        }],
                        "candidateEvidence": ["伪造的同级关系证据"],
                    },
                }],
            },
            [{"case_id": "TC-FORGED-PRESERVE"}],
        )
    )
    require(
        "platformPreserveContract" not in forged_contract_result.get("cases", [{}])[0],
        "Only the platform may derive a preserve contract from the original candidate",
    )
    duplicate_preserve_flow, duplicate_preserve_trace = (
        ai_skill_service._merge_preserve_contract_into_flow(
            [
                "点击「照片打印」入口",
                "点击「5寸照片」",
                "等待「百度网盘」入口可见",
                "校验「百度网盘」与「相机拍照」同级并列展示",
                "校验「百度网盘」与「相机拍照」同级并列展示",
                "等待导入区域稳定显示",
                "点击「百度网盘」入口",
                "等待百度网盘授权页稳定可见",
            ],
            [relation_points[0]],
            {
                "requiredAcceptanceChecks": [{
                    **relation_payload["analysis"]["requirement_acceptance_checks"][0],
                    "contractRoles": ["preserve"],
                }, {
                    "id": "REQ-002-CHECK-03",
                    "requirementId": "REQ-002",
                    "branch": "照片打印",
                    "kind": "copy",
                    "text": "校验百度网盘入口使用需求约定的可见文案",
                    "contractRoles": ["preserve"],
                }],
                "candidateEvidence": [
                    "「百度网盘」与「相机拍照」同级并列展示",
                    "「百度网盘」入口文案为“百度网盘”",
                ],
            },
        )
    )
    require(
        not duplicate_preserve_trace.get("missing_check_ids")
        and duplicate_preserve_flow.count(
            "校验「百度网盘」与「相机拍照」同级并列展示"
        ) == 1
        and "校验「百度网盘」入口文案为“百度网盘”" in duplicate_preserve_flow
        and len(duplicate_preserve_flow) == 8,
        "Exact duplicate source-page assertions must not consume the bounded flow budget",
    )
    bounded_preserve_plan = {
        "authoritative": True,
        "allowedBaselineIds": ["base-photo"],
        "verifiedBaselineIds": ["base-photo"],
        "selectedBaselines": atomic_preservation_plan.get("selectedBaselines") or [],
        "requirementPoints": [relation_points[0]],
        "scopePlan": {"smokeCount": 1},
        "planningContext": {"pass": "coverage_convergence"},
        "focusedCandidateIds": ["TC-ATOMIC"],
        "convergenceFocus": {
            "repairableExecutableCandidateIds": ["TC-ATOMIC"],
        },
        "preserveContractByCaseId": {
            "TC-ATOMIC": {
                "requiredAcceptanceChecks": [{
                    **relation_payload["analysis"]["requirement_acceptance_checks"][0],
                    "contractRoles": ["preserve"],
                }],
                "candidateEvidence": [
                    "「百度网盘」入口与「相机拍照」同级并列展示",
                ],
            },
        },
        "candidateEligibilityById": {
            "TC-ATOMIC": {
                "eligible": True,
                "kind": "bounded_external_landing",
                "baselineId": "base-photo",
                "acceptanceCheckIds": ["REQ-002-CHECK-04"],
                "precondition": "App 首页",
                "flow": [
                    "等待 App 首页稳定显示",
                    "点击底部 Tab「照片打印」",
                    "点击「照片打印」入口",
                    "点击「5寸照片」",
                    "等待「百度网盘」入口可见",
                    "点击「百度网盘」入口",
                    "等待百度网盘授权页稳定可见",
                ],
                "assertionTarget": "百度网盘授权页稳定可见",
                "requirementRefs": [relation_points[0]],
            },
        },
        "cases": [],
        "needs_review_cases": [],
        "draft_cases": [],
        "manual_cases": [{"caseId": "TC-ATOMIC", "reason": "模型保留人工"}],
    }
    bounded_preserve_applied = ai_skill_service.apply_executable_yaml_plan_to_payload(
        atomic_preservation_payload,
        bounded_preserve_plan,
    )
    bounded_preserve_case = bounded_preserve_applied.get("cases", [{}])[0]
    bounded_preserve_flow = bounded_preserve_case.get("steps") or []
    require(
        bounded_preserve_case.get("executionLevel") == "executable"
        and bounded_preserve_flow.index(
            "校验「百度网盘」入口与「相机拍照」同级并列展示"
        ) > bounded_preserve_flow.index("点击「5寸照片」")
        and bounded_preserve_flow.index(
            "校验「百度网盘」入口与「相机拍照」同级并列展示"
        ) < bounded_preserve_flow.index("点击「百度网盘」入口")
        and ai_skill_service.executable_yaml_portfolio_audit(
            bounded_preserve_applied,
            {"min_automation_cases": 1},
        ).get("ok"),
        "A bounded evidence override must retain the canonical candidate preserve contract",
    )

    swapped_payload = {
        "analysis": {"requirement_points": [
            "REQ-002 照片打印入口可见",
            "REQ-003 扫描复印入口可见",
        ]},
        "cases": [
            {
                "case_id": "TC-002",
                "title": "照片打印入口可见",
                "coverage": "REQ-002",
                "steps": ["等待首页", "点击照片打印", "等待百度网盘入口可见"],
                "assertions": ["百度网盘入口可见"],
            },
            {
                "case_id": "TC-003",
                "title": "扫描复印入口可见",
                "coverage": "REQ-003",
                "steps": ["等待首页", "点击扫描复印", "等待百度网盘入口可见"],
                "assertions": ["百度网盘入口可见"],
            },
        ],
    }
    swapped_plan = {
        "authoritative": True,
        "allowedBaselineIds": ["base-nav"],
        "requirementPoints": swapped_payload["analysis"]["requirement_points"],
        "cases": [
            {
                "caseId": "TC-002",
                "baselineId": "base-nav",
                "baselineGrounded": True,
                "precondition": "首页",
                "flow": ["等待首页", "点击扫描复印", "等待百度网盘入口可见"],
                "assertionTarget": "百度网盘入口可见",
                "requirementRefs": ["REQ-003 扫描复印入口可见"],
                "batch": "smoke",
            },
            {
                "caseId": "TC-003",
                "baselineId": "base-nav",
                "baselineGrounded": True,
                "precondition": "首页",
                "flow": ["等待首页", "点击照片打印", "等待百度网盘入口可见"],
                "assertionTarget": "百度网盘入口可见",
                "requirementRefs": ["REQ-002 照片打印入口可见"],
                "batch": "smoke",
            },
        ],
    }
    guarded_mapping = ai_skill_service.apply_executable_yaml_plan_to_payload(swapped_payload, swapped_plan)
    guarded_by_id = {item.get("case_id"): item for item in guarded_mapping.get("cases") or []}
    require(
        guarded_by_id["TC-002"].get("steps") == swapped_payload["cases"][0]["steps"]
        and guarded_by_id["TC-003"].get("steps") == swapped_payload["cases"][1]["steps"],
        "A planner requirement swap must not replace a candidate with a sibling business path",
    )
    require(
        guarded_by_id["TC-002"].get("requirementRefs") == ["REQ-002 照片打印入口可见"]
        and guarded_by_id["TC-003"].get("requirementRefs") == ["REQ-003 扫描复印入口可见"]
        and guarded_mapping.get("review", {}).get("executable_yaml_plan", {}).get("path_mapping_guard_count") == 2,
        "Requirement mapping guard must restore source refs and expose every rejected cross-branch path",
    )
    portfolio = ai_skill_service.executable_yaml_portfolio_audit(guarded_mapping, {
        "min_automation_cases": 2,
    })
    require(portfolio.get("ok") and portfolio.get("executableCount") == 2, "Executable portfolio audit must accept complete grounded coverage")
    incomplete_mapping = json.loads(json.dumps(guarded_mapping, ensure_ascii=False))
    incomplete_mapping["cases"][1]["executionLevel"] = "needs_review"
    incomplete_portfolio = ai_skill_service.executable_yaml_portfolio_audit(incomplete_mapping, {
        "min_automation_cases": 2,
    })
    require(
        not incomplete_portfolio.get("ok")
        and incomplete_portfolio.get("unresolvedAutomaticCount") == 1
        and any("REQ-003" in point for point in incomplete_portfolio.get("missingRequirementPoints") or []),
        "Executable portfolio audit must trigger final AI convergence for unresolved requirement coverage",
    )

    convergence_payload = {
        "analysis": {"requirement_points": ["REQ-001 文档入口可见", "REQ-002 照片入口可见"]},
        "cases": [
            {
                "case_id": "TC-101",
                "title": "文档入口可见",
                "coverage": "REQ-001",
                "requirementRefs": ["REQ-001 文档入口可见"],
                "executionLevel": "executable",
                "steps": ["等待首页", "点击文档打印", "等待百度网盘可见"],
                "assertions": ["百度网盘可见"],
                "ai_case_plan": {
                    "baselineId": "base-nav",
                    "baselineGrounded": True,
                    "precondition": "App 首页",
                    "flow": ["等待首页", "点击文档打印", "等待百度网盘可见"],
                    "assertionTarget": "百度网盘可见",
                    "batch": "smoke",
                },
            },
            {
                "case_id": "TC-102",
                "title": "照片入口可见",
                "coverage": "REQ-002",
                "requirementRefs": ["REQ-002 照片入口可见"],
                "executionLevel": "needs_review",
                "steps": ["等待首页", "点击照片打印", "等待百度网盘可见"],
                "assertions": ["百度网盘可见"],
            },
        ],
        "manual_cases": [
            {
                "case_id": "MC-101",
                "title": "照片入口人工备选",
                "coverage": "REQ-002",
                "steps": ["进入照片打印", "观察百度网盘入口"],
                "assertions": ["百度网盘可见"],
            },
            {
                "case_id": "MC-102",
                "title": "文档深层人工项",
                "coverage": "REQ-001",
                "steps": ["进入第三方账号深层流程"],
                "assertions": ["人工确认"],
            },
        ],
    }
    convergence_audit = ai_skill_service.executable_yaml_portfolio_audit(
        convergence_payload,
        {"min_automation_cases": 2},
    )
    focused_requests = []
    old_run_ai_skill = ai_skill_service.run_ai_skill
    try:
        def fake_convergence_planner(skill_name, request, **_kwargs):
            require(skill_name == "executable_yaml_planner", "Unexpected AI skill during convergence replay")
            focused_requests.append(request)
            return {
                # Intentionally omit TC-101: the platform must preserve an already-approved executable.
                "cases": [{
                    "caseId": "TC-102",
                    "baselineId": "base-nav",
                    "precondition": "App 首页",
                    "flow": ["等待首页", "点击照片打印", "等待百度网盘入口可见"],
                    "assertionTarget": "百度网盘入口可见",
                    "requirementRefs": ["REQ-002 照片入口可见"],
                    "executableReason": "显式需求可由固定设备上的可见文字短链路验证",
                    "batch": "remaining",
                }],
                "needs_review_cases": [],
                "draft_cases": [],
                "manual_cases": [{"caseId": "MC-101", "reason": "保留人工备选"}],
                "review": {"planning_reason": "只处理当前覆盖缺口"},
            }

        ai_skill_service.run_ai_skill = fake_convergence_planner
        focused_plan = ai_skill_service.call_skill_executable_yaml_planner(
            "入口覆盖收敛",
            "基础打印",
            convergence_payload,
            [{
                "id": "base-nav",
                "title": "可信兄弟入口导航",
                "sourceKind": "verified_execution",
                "verificationStatus": "execution_success",
                "businessPath": "首页 -> 打印入口",
            }],
            {"smokeCount": 2},
            source_evidence={
                "mode": "soft_reference",
                "requirementText": "入口覆盖需求" * 1200,
                "figmaSoftEvidence": "设计稿软证据" * 1800,
                "figmaPageCount": 4,
                "figmaImageCount": 4,
                "executionContext": {"runnerId": "runner-01", "deviceId": "device-01"},
                "uiDesigns": [{"large": "unused" * 2000}],
            },
            planning_context={"pass": "coverage_convergence", "portfolioAudit": convergence_audit},
        )
    finally:
        ai_skill_service.run_ai_skill = old_run_ai_skill
    focused_ids = {item.get("case_id") for item in focused_requests[0].get("cases") or []}
    focused_request = focused_requests[0]
    require(
        focused_ids == {"TC-102", "MC-101"}
        and (focused_request.get("planningContext") or {}).get("focus", {}).get("fullCandidateCount") == 4
        and (focused_request.get("planningContext") or {}).get("focus", {}).get("preservedExecutableCandidateIds") == ["TC-101"]
        and focused_request.get("scenarios") == []
        and set((focused_request.get("analysis") or {}).keys()).issubset({
            "requirement_points", "requirement_acceptance_checks", "requirement_contract", "visible_outcomes",
        })
        and len((focused_request.get("sourceEvidence") or {}).get("requirementText") or "") <= 6000
        and len((focused_request.get("sourceEvidence") or {}).get("figmaSoftEvidence") or "") <= 6000
        and "uiDesigns" not in (focused_request.get("sourceEvidence") or {}),
        "Final convergence must omit already-approved executable cases and compact soft context while keeping one gap-matched manual alternate",
    )
    require(
        focused_plan.get("trace", {}).get("context_compacted") is True
        and focused_plan.get("trace", {}).get("request_candidate_ids") == ["TC-102", "MC-101"]
        and focused_plan.get("trace", {}).get("request_context_chars") < 20000,
        "Convergence trace must expose the compact request size and exact focused candidate IDs",
    )
    convergence_automatic_records = [{
        "raw": item,
        "compact": ai_skill_service._compact_case_for_plan(
            item,
            index,
            origin_level="automatic",
        ),
    } for index, item in enumerate(convergence_payload["cases"])]
    convergence_manual_records = [{
        "raw": item,
        "compact": ai_skill_service._compact_case_for_plan(
            item,
            index,
            origin_level="manual",
        ),
    } for index, item in enumerate(convergence_payload["manual_cases"])]
    original_bounded_convergence_evidence = ai_skill_service._bounded_convergence_evidence
    try:
        ai_skill_service._bounded_convergence_evidence = lambda *_args, **_kwargs: {
            "TC-101": {"eligible": True, "acceptanceCheckIds": ["REQ-001-CHECK-01"]},
            "TC-102": {"eligible": True, "acceptanceCheckIds": ["REQ-002-CHECK-01"]},
        }
        bounded_focus_auto, _bounded_focus_manual, _bounded_context, bounded_focus = (
            ai_skill_service._focus_executable_convergence_candidates(
                convergence_payload,
                convergence_automatic_records,
                convergence_manual_records,
                {"pass": "coverage_convergence", "portfolioAudit": convergence_audit},
                selected_baselines=[],
            )
        )
    finally:
        ai_skill_service._bounded_convergence_evidence = original_bounded_convergence_evidence
    require(
        [item.get("case_id") for item in bounded_focus_auto] == ["TC-102"]
        and bounded_focus.get("boundedEvidenceCandidateIds") == ["TC-102"]
        and bounded_focus.get("acceptanceCheckCandidateIds") == {
            "REQ-002-CHECK-01": ["TC-102"],
        }
        and "TC-101" not in bounded_focus.get("focusedCandidateIds", []),
        "Bounded evidence must expose its exact acceptance ownership without re-adding an already-approved executable",
    )
    focused_applied = ai_skill_service.apply_executable_yaml_plan_to_payload(convergence_payload, focused_plan)
    focused_by_id = {item.get("case_id"): item for item in focused_applied.get("cases") or []}
    require(
        focused_by_id["TC-101"].get("executionLevel") == "executable"
        and focused_by_id["TC-102"].get("executionLevel") == "executable"
        and focused_applied.get("review", {}).get("executable_yaml_plan", {}).get("preserved_executable_count") == 1,
        "A focused convergence omission must preserve a previously approved executable while allowing AI to close the real gap",
    )
    require(
        any(item.get("case_id") == "MC-102" and item.get("executionLevel") == "manual" for item in focused_applied.get("manual_cases") or [])
        and focused_applied.get("review", {}).get("executable_yaml_plan", {}).get("outside_focus_preserved_count") == 1,
        "Unrelated manual candidates outside the convergence focus must remain manual without bloating the model request",
    )
    require(
        ai_skill_service.executable_yaml_portfolio_audit(focused_applied, {"min_automation_cases": 2}).get("ok"),
        "Focused AI convergence must be able to close explicit requirement coverage without weakening the portfolio gate",
    )
    explicit_demotion_plan = json.loads(json.dumps(focused_plan, ensure_ascii=False))
    explicit_demotion_plan["manual_cases"].append({
        "caseId": "TC-101",
        "reason": "为保持首批 Smoke 精简，将已有可执行项转人工",
    })
    explicit_demotion = ai_skill_service.apply_executable_yaml_plan_to_payload(
        convergence_payload,
        explicit_demotion_plan,
    )
    explicit_demotion_by_id = {
        item.get("case_id"): item for item in explicit_demotion.get("cases") or []
    }
    require(
        explicit_demotion_by_id["TC-101"].get("executionLevel") == "executable"
        and explicit_demotion.get("review", {}).get("executable_yaml_plan", {}).get("convergence_demotion_blocked_count") == 1,
        "Coverage convergence must keep an already-approved executable; Smoke overflow belongs to the remaining batch, not Manual",
    )
    explicit_rewrite_plan = json.loads(json.dumps(focused_plan, ensure_ascii=False))
    explicit_rewrite_plan["cases"].append({
        "caseId": "TC-101",
        "baselineId": "base-nav",
        "baselineGrounded": True,
        "precondition": "App 首页",
        "flow": ["等待首页", "点击照片打印", "等待错误分支可见"],
        "assertionTarget": "错误分支可见",
        "requirementRefs": ["REQ-001 文档入口可见"],
        "batch": "remaining",
    })
    explicit_rewrite = ai_skill_service.apply_executable_yaml_plan_to_payload(
        convergence_payload,
        explicit_rewrite_plan,
    )
    explicit_rewrite_by_id = {
        item.get("case_id"): item for item in explicit_rewrite.get("cases") or []
    }
    require(
        explicit_rewrite_by_id["TC-101"].get("steps")
        == convergence_payload["cases"][0]["steps"]
        and explicit_rewrite_by_id["TC-101"].get("assertions")
        == convergence_payload["cases"][0]["assertions"]
        and explicit_rewrite.get("review", {}).get("executable_yaml_plan", {}).get("convergence_rewrite_blocked_count") == 1,
        "Final convergence may classify gap candidates but must not rewrite an already-approved executable path or assertion",
    )
    degraded_photo_requirement = (
        "REQ-042 照片打印：点击百度网盘入口并校验目标页面稳定可达"
    )
    degraded_photo_check = {
        "id": "REQ-042-CHECK-04",
        "requirementId": "REQ-042",
        "branch": "照片打印",
        "kind": "reachability",
        "text": "点击百度网盘入口并校验目标页面稳定可达",
    }
    degraded_photo_payload = {
        "analysis": {
            "requirement_points": [degraded_photo_requirement],
            "requirement_acceptance_checks": [degraded_photo_check],
        },
        "cases": [{
            "case_id": "TC-PHOTO-DISPLAY",
            "title": "照片打印百度网盘入口可见性及位置校验",
            "executionLevel": "executable",
            "originExecutionLevel": "automatic",
            "requirementRefs": [degraded_photo_requirement],
            "steps": [
                "等待 App 首页稳定显示",
                "点击「照片打印」入口",
                "点击「5寸照片」",
                "等待「百度网盘」入口可见",
            ],
            "assertions": ["「百度网盘」入口可见且文案正确"],
            "ai_case_plan": {
                "baselineId": "base-photo",
                "baselineGrounded": True,
                "precondition": "App 首页",
                "flow": [
                    "等待 App 首页稳定显示",
                    "点击「照片打印」入口",
                    "点击「5寸照片」",
                    "等待「百度网盘」入口可见",
                ],
                "assertionTarget": "「百度网盘」入口可见且文案正确",
            },
        }, {
            "case_id": "TC-PHOTO-LANDING-DEGRADED",
            "title": "照片打印页-点击百度网盘入口可达性校验",
            "scenario": "照片打印页-点击百度网盘入口-文件列表跳转",
            "goal": "验证点击照片打印页的百度网盘入口后目标页面稳定可达",
            "business_path": "照片打印页 -> 点击百度网盘 -> 校验文件选择页",
            "executionLevel": "executable",
            "originExecutionLevel": "automatic",
            "requirementRefs": [degraded_photo_requirement],
            "steps": [
                "等待 App 首页稳定显示",
                "点击「照片打印」入口",
                "点击「5寸照片」",
            ],
            "assertions": ["百度网盘落地页首个稳定页面可见，无白屏或崩溃"],
            "ai_case_plan": {
                "baselineId": "base-photo",
                "baselineGrounded": True,
                "precondition": "App 首页",
                "flow": [
                    "等待 App 首页稳定显示",
                    "点击「照片打印」入口",
                    "点击「5寸照片」",
                ],
                "assertionTarget": "百度网盘落地页首个稳定页面可见，无白屏或崩溃",
            },
        }],
        "manual_cases": [],
    }
    degraded_photo_audit = ai_skill_service.executable_yaml_portfolio_audit(
        degraded_photo_payload,
        {"min_automation_cases": 0},
    )
    degraded_photo_records = [{
        "raw": item,
        "compact": ai_skill_service._compact_case_for_plan(
            item,
            index,
            origin_level="automatic",
        ),
    } for index, item in enumerate(degraded_photo_payload["cases"])]
    degraded_focus_auto, _degraded_focus_manual, _degraded_context, degraded_focus = (
        ai_skill_service._focus_executable_convergence_candidates(
            degraded_photo_payload,
            degraded_photo_records,
            [],
            {"pass": "coverage_convergence", "portfolioAudit": degraded_photo_audit},
            selected_baselines=[],
        )
    )
    require(
        degraded_photo_audit.get("missingAcceptanceCheckCount") == 1
        and [item.get("case_id") for item in degraded_focus_auto]
        == ["TC-PHOTO-LANDING-DEGRADED"]
        and degraded_focus.get("preservedExecutableCandidateIds") == ["TC-PHOTO-DISPLAY"]
        and degraded_focus.get("repairableExecutableCandidateIds")
        == ["TC-PHOTO-LANDING-DEGRADED"],
        "A nominal executable that declares a missing acceptance intent must remain repairable while approved sibling paths stay frozen",
    )
    repaired_photo_plan = {
        "authoritative": True,
        "cases": [{
            "caseId": "TC-PHOTO-LANDING-DEGRADED",
            "baselineId": "base-photo",
            "baselineGrounded": True,
            "precondition": "App 首页",
            "flow": [
                "等待 App 首页稳定显示",
                "点击「照片打印」入口",
                "点击「5寸照片」",
                "等待「百度网盘」入口可见",
                "点击「百度网盘」入口",
                "等待百度网盘落地页首个稳定页面可见，无白屏或崩溃",
            ],
            "assertionTarget": "百度网盘落地页首个稳定页面可见，无白屏或崩溃",
            "requirementRefs": [degraded_photo_requirement],
            "batch": "remaining",
        }],
        "needs_review_cases": [],
        "draft_cases": [],
        "manual_cases": [],
        "allowedBaselineIds": ["base-photo"],
        "verifiedBaselineIds": ["base-photo"],
        "requirementPoints": [degraded_photo_requirement],
        "planningContext": {
            "pass": "coverage_convergence",
            "portfolioAudit": degraded_photo_audit,
        },
        "focusedCandidateIds": degraded_focus.get("focusedCandidateIds") or [],
        "convergenceFocus": degraded_focus,
        "candidateEligibilityById": {},
        "scopePlan": {"smokeCount": 1},
    }
    repaired_photo_payload = ai_skill_service.apply_executable_yaml_plan_to_payload(
        degraded_photo_payload,
        repaired_photo_plan,
    )
    repaired_photo_by_id = {
        item.get("case_id"): item for item in repaired_photo_payload.get("cases") or []
    }
    repaired_photo_review = (
        repaired_photo_payload.get("review", {}).get("executable_yaml_plan", {})
    )
    require(
        repaired_photo_by_id["TC-PHOTO-DISPLAY"].get("steps")
        == degraded_photo_payload["cases"][0]["steps"]
        and "点击「百度网盘」入口" in repaired_photo_by_id[
            "TC-PHOTO-LANDING-DEGRADED"
        ].get("steps", [])
        and repaired_photo_review.get("preserved_executable_count") == 1
        and repaired_photo_review.get("repairable_executable_count") == 1
        and repaired_photo_review.get("convergence_rewrite_blocked_count") == 0
        and ai_skill_service.executable_yaml_portfolio_audit(
            repaired_photo_payload,
            {"min_automation_cases": 0},
        ).get("ok"),
        "Final AI convergence must be able to repair a degraded acceptance owner without rewriting a valid executable sibling",
    )
    bounded_variant_requirement = (
        "REQ-042 照片打印：点击百度网盘入口并校验目标页面稳定可达"
    )
    bounded_variant_payload = {
        "analysis": {
            "requirement_points": [bounded_variant_requirement],
            "requirement_acceptance_checks": [{
                "id": "REQ-042-CHECK-04",
                "requirementId": "REQ-042",
                "branch": "照片打印",
                "kind": "reachability",
                "text": "点击百度网盘入口并校验目标页面稳定可达",
            }],
        },
        "cases": [{
            "case_id": "TC-PHOTO-LANDING",
            "title": "照片打印百度网盘入口可达",
            "executionLevel": "needs_review",
            "originExecutionLevel": "automatic",
            "requirementRefs": [bounded_variant_requirement],
            "preconditions": ["App 首页"],
            "steps": [
                "等待 App 首页稳定显示",
                "点击「照片打印」入口",
                "点击「百度网盘」入口",
                "检查百度网盘落地页首个稳定页面可见",
            ],
            "assertions": ["百度网盘落地页首个稳定页面可见，无白屏或崩溃"],
            "repair_hints": "【视觉校准冲突】旧视觉映射采用一寸照，需要人工确认",
            "ai_case_plan": {
                "currentVisualLeafEvidence": {
                    "navigationLeaf": "一寸照",
                },
            },
        }],
        "manual_cases": [],
        "review": {
            "current_page_evidence": [{
                "caseId": "TC-PHOTO-LANDING",
                "requirementId": "REQ-042",
                "branch": "照片打印-5寸照片",
                "pageTitle": "5寸照片",
                "parentPath": ["照片打印"],
                "navigationLeaf": "5寸照片",
                "targetText": "百度网盘",
                "sameBranch": True,
                "confidence": 0.9,
                "source": "figma_current_frame",
            }, {
                "caseId": "TC-PHOTO-LANDING",
                "requirementId": "REQ-042",
                "branch": "照片打印-一寸照",
                "pageTitle": "一寸照",
                "parentPath": ["照片打印"],
                "navigationLeaf": "一寸照",
                "targetText": "百度网盘",
                "sameBranch": True,
                "confidence": 0.95,
                "source": "figma_current_frame",
            }],
        },
    }
    bounded_variant_flow = [
        "等待 App 首页稳定显示",
        "点击「照片打印」入口",
        "等待照片打印页面加载完成",
        "点击「5寸照片」",
        "等待「百度网盘」入口可见",
        "点击「百度网盘」入口",
        "检查百度网盘落地页首个稳定页面可见，无白屏或崩溃",
    ]
    bounded_variant_evidence = {
        "eligible": True,
        "kind": "bounded_landing",
        "sourceCaseId": "TC-PHOTO-DISPLAY",
        "tailSourceCaseId": "TC-PHOTO-LANDING",
        "baselineId": "base-photo-variant",
        "precondition": "App 首页",
        "flow": bounded_variant_flow,
        "assertionTarget": "百度网盘落地页首个稳定页面可见，无白屏或崩溃",
        "requirementRefs": [bounded_variant_requirement],
        "acceptanceCheckIds": ["REQ-042-CHECK-04"],
        "currentLeafAdapted": True,
        "currentLeafSourceCaseId": "TC-PHOTO-DISPLAY",
        "currentLeafEvidenceSource": "figma_current_frame",
        "currentLeafEvidence": bounded_variant_payload["review"]["current_page_evidence"][0],
    }
    bounded_variant_plan = {
        "authoritative": True,
        "cases": [{
            "caseId": "TC-PHOTO-LANDING",
            "baselineId": "base-photo-variant",
            "baselineGrounded": True,
            "precondition": "App 首页",
            "flow": bounded_variant_flow,
            "assertionTarget": "百度网盘落地页首个稳定页面可见，无白屏或崩溃",
            "requirementRefs": [bounded_variant_requirement],
            "batch": "remaining",
        }],
        "needs_review_cases": [],
        "draft_cases": [],
        "manual_cases": [],
        "selectedBaselines": [{
            "id": "base-photo-variant",
            "title": "照片打印历史成功路径",
            "sourceKind": "verified_execution",
            "verificationStatus": "execution_success",
            "snippet": (
                "# baseline.start_page: App 首页\n"
                "- aiTap: 照片打印\n"
                "- aiTap: 6寸照片\n"
                "- aiWaitFor: 百度网盘入口可见"
            ),
        }],
        "allowedBaselineIds": ["base-photo-variant"],
        "verifiedBaselineIds": ["base-photo-variant"],
        "requirementPoints": [bounded_variant_requirement],
        "planningContext": {"pass": "coverage_convergence"},
        "focusedCandidateIds": ["TC-PHOTO-LANDING"],
        "candidateEligibilityById": {
            "TC-PHOTO-LANDING": bounded_variant_evidence,
        },
        "scopePlan": {"smokeCount": 3},
    }
    bounded_variant_applied = ai_skill_service.apply_executable_yaml_plan_to_payload(
        bounded_variant_payload,
        bounded_variant_plan,
    )
    bounded_variant_case = bounded_variant_applied["cases"][0]
    bounded_variant_steps = " ".join(bounded_variant_case.get("steps") or [])
    require(
        bounded_variant_case.get("executionLevel") == "executable"
        and "点击「5寸照片」" in bounded_variant_steps
        and "点击「百度网盘」入口" in bounded_variant_steps
        and "一寸照" not in bounded_variant_steps
        and "6寸照片" not in bounded_variant_steps,
        "An AI-selected bounded path must keep its accepted Figma state through application instead of being re-grounded to a sibling Frame or historical baseline leaf",
    )
    require(
        "5寸照片" in str(bounded_variant_case.get("repair_hints") or "")
        and "一寸照" not in str(bounded_variant_case.get("repair_hints") or "")
        and bounded_variant_applied.get("review", {}).get("executable_yaml_plan", {}).get(
            "visual_variant_hint_refreshed_count"
        ) == 1,
        "Accepted current visual evidence must replace stale sibling-Frame repair metadata before the YAML can become a future baseline",
    )
    manualized_smoke = ai_skill_service.apply_executable_yaml_plan_to_payload(
        {
            "analysis": {"requirement_points": ["REQ-010 入口展示"]},
            "cases": [{
                "case_id": "TC-SMOKE-MANUAL",
                "title": "入口冒烟候选",
                "coverage": "REQ-010",
                "smoke": True,
                "flag": ["冒烟"],
                "steps": ["进入页面", "等待入口可见"],
                "assertions": ["入口可见"],
            }],
        },
        {
            "authoritative": True,
            "cases": [],
            "manual_cases": [{"caseId": "TC-SMOKE-MANUAL", "reason": "路径不确定"}],
        },
    )
    manualized_smoke_case = manualized_smoke.get("manual_cases", [{}])[0]
    require(
        manualized_smoke_case.get("smoke") is False
        and "冒烟" not in (manualized_smoke_case.get("flag") or []),
        "A candidate classified as Manual must not retain stale Smoke metadata",
    )
    focused_portfolio = ai_skill_service.executable_yaml_portfolio_audit(
        focused_applied,
        {"min_automation_cases": 2},
    )
    accepted_convergence = yaml_service.executable_yaml_convergence_decision(
        convergence_audit,
        focused_portfolio,
    )
    regressed_convergence = yaml_service.executable_yaml_convergence_decision(
        {
            "ok": False,
            "coveredAcceptanceCheckIds": ["REQ-001-CHECK-01", "REQ-003-CHECK-04"],
            "missingRequirementPoints": ["REQ-002 visibility"],
        },
        {
            "ok": False,
            "coveredAcceptanceCheckIds": ["REQ-001-CHECK-01", "REQ-002-CHECK-04"],
            "missingRequirementPoints": ["REQ-003 reachability"],
        },
    )
    require(
        accepted_convergence.get("accepted") is True
        and regressed_convergence.get("accepted") is False
        and regressed_convergence.get("regressedAcceptanceCheckIds") == ["REQ-003-CHECK-04"],
        "Final convergence must be monotonic: adding one branch may not erase an already covered acceptance dimension",
    )
    omitted_gap_plan = json.loads(json.dumps(focused_plan, ensure_ascii=False))
    omitted_gap_plan["cases"] = []
    omitted_gap = ai_skill_service.apply_executable_yaml_plan_to_payload(convergence_payload, omitted_gap_plan)
    omitted_by_id = {item.get("case_id"): item for item in omitted_gap.get("cases") or []}
    require(
        omitted_by_id["TC-101"].get("executionLevel") == "executable"
        and omitted_by_id["TC-102"].get("executionLevel") == "needs_review"
        and not ai_skill_service.executable_yaml_portfolio_audit(omitted_gap, {"min_automation_cases": 2}).get("ok"),
        "Focused convergence must never promote an unresolved candidate that AI omitted",
    )

    unsafe_replan = json.loads(json.dumps(replan, ensure_ascii=False))
    unsafe_replan["cases"][0]["baselineGrounded"] = False
    guarded = ai_skill_service.apply_executable_yaml_plan_to_payload(replan_payload, unsafe_replan)
    guarded_manual_origin = next(item for item in guarded.get("cases") or [] if item.get("case_id") == "MC-001")
    require(
        guarded_manual_origin.get("executionLevel") == "needs_review"
        and guarded.get("review", {}).get("executable_yaml_plan", {}).get("promotion_guard_failed_count") == 1,
        "Prior-manual promotion without a grounded baseline must be downgraded before Runner eligibility",
    )
    old_smoke_selector = ai_skill_service.call_skill_smoke_selector
    smoke_candidates = []
    try:
        def fake_smoke_selector(_title, _module, _analysis, _scenarios, cases, *_args, **_kwargs):
            smoke_candidates.extend(item.get("case_id") for item in cases)
            return {"smoke_case_ids": [item.get("case_id") for item in cases], "review": {"selector_source": "static"}}

        ai_skill_service.call_skill_smoke_selector = fake_smoke_selector
        smoke_classified = ai_skill_service.select_smoke_cases_for_payload("入口", "AI测试", classified)
    finally:
        ai_skill_service.call_skill_smoke_selector = old_smoke_selector
    require(smoke_candidates == ["TC-001"], "Final smoke selection must only spend AI effort on planner-approved executable candidates")
    require(not any(item.get("smoke") for item in smoke_classified.get("cases") or [] if item.get("executionLevel") != "executable"), "Review/draft cases must not regain a smoke marker after AI classification")

    generated_yaml = """android:
  tasks:
    - name: 照片打印
      flow:
        - terminate: com.xbxxhz.box
        - launch: com.xbxxhz.box
        - ai: 终止并重启App
        - aiWaitFor: 首页加载完成
        - aiTap: 照片打印
"""
    repaired = yaml_service.repair_generated_yaml_executable_gate_issues(generated_yaml)
    require(repaired.get("changed") and "终止并重启App" not in repaired.get("content", ""), "Generated YAML must remove redundant AI restart after deterministic launch")
    require("被测 App 已按前置 launch 启动" in repaired.get("content", ""), "Redundant restart must become a visible stable-state wait")

    run = {
        "scope": "regression",
        "artifacts": {
            "generationPipeline": {
                "caseCount": 2,
                "yamlFileCount": 2,
                "coverageAudit": {
                    "requirement_point_count": 2,
                    "missing_case_points": ["REQ-004 多端展示", "REQ-005 点击可达"],
                },
                "generatedCaseGroups": {"counts": {}},
            },
        },
    }
    refs = [{"scopeReview": {"matchedRequirementIds": ["REQ-005"]}}, {}]
    gap = agent_service._agent_generated_yaml_coverage_gap(run, refs)
    require(gap.get("missingRequirementPoints") == ["REQ-004 多端展示"], "Coverage gate must remove stale missing IDs already mapped by confirmed YAML")
    online_shape_run = {
        "scope": "regression",
        "artifacts": {
            "generatedCases": {
                "analysis": {
                    "requirement_points": [
                        "REQ-001 文档打印入口展示",
                        "REQ-002 照片打印入口展示",
                        "REQ-003 扫描复印入口展示",
                        "REQ-004 点击入口后可达",
                        "REQ-005 当前固定设备文案完整",
                    ],
                },
                "cases": [{"case_id": "TC-001"}, {"case_id": "TC-002"}, {"case_id": "TC-005"}],
            },
            "generationPipeline": {
                "caseCount": 3,
                "yamlFileCount": 3,
                "coverageAudit": {
                    "requirement_point_count": 5,
                    "missing_case_points": ["REQ-005 当前固定设备文案完整"],
                },
                "generatedCaseGroups": {
                    "counts": {"executable": 3, "needs_review": 0, "draft": 0, "manual": 2},
                    "manual_cases": [
                        {"requirementRefs": ["REQ-003 扫描复印入口展示"]},
                        {"requirementRefs": ["REQ-004 点击入口后可达"]},
                    ],
                },
            },
        },
    }
    online_shape_refs = [
        {"file": "doc.yaml", "confirmed": True, "runnerCandidate": False, "executionLevel": "executable", "scopeReview": {"matchedRequirementIds": ["REQ-001"]}},
        {"file": "photo.yaml", "confirmed": True, "runnerCandidate": False, "executionLevel": "executable", "scopeReview": {"matchedRequirementIds": ["REQ-002"]}},
        {"file": "copy-smoke.yaml", "scopeReview": {}},
        {"file": "device-copy.yaml", "confirmed": True, "runnerCandidate": False, "executionLevel": "executable", "scopeReview": {"matchedRequirementIds": ["REQ-005"]}},
        {"file": "manual-reachability.yaml", "executionLevel": "manual", "scopeReview": {"matchedRequirementIds": ["REQ-004"]}},
    ]
    online_shape_gap = agent_service._agent_generated_yaml_coverage_gap(online_shape_run, online_shape_refs)
    require(
        online_shape_gap.get("missingRequirementPoints")
        == ["REQ-003 扫描复印入口展示", "REQ-004 点击入口后可达"],
        "Final Runner coverage must compare all requirement IDs with confirmed YAML; manual cases cannot mask missing executable branches",
    )

    def generic_acceptance_yaml(branch, include_reachability=False):
        reachability_flow = ""
        if include_reachability:
            reachability_flow = """
        - aiTap: 发票入口
        - aiWaitFor: 授权页、登录页或内容列表任一合法页面可见
        - aiAssert: 授权页、登录页或内容列表任一合法页面可见，且无白屏或崩溃"""
        return f"""android:
  tasks:
    - name: {branch}发票入口验收
      flow:
        - aiWaitFor: {branch}页面已打开
        - aiWaitFor: 发票入口可见
        - aiAssert: 发票入口可见，和当前页面入口同级，显示文案为发票{reachability_flow}
"""

    generic_yaml_refs = [
        {
            "file": f"generic-{index}.yaml",
            "content": generic_acceptance_yaml(branch),
            "confirmed": True,
            "executionLevel": "executable",
            "scopeReview": {"matchedRequirementIds": [f"REQ-{index:03d}"]},
        }
        for index, branch in enumerate(("订单管理", "优惠券"), start=1)
    ]
    final_yaml_gaps, _mapped_ids, _required_ids = agent_service._agent_final_yaml_coverage_points(
        {"analysis": generic_analysis},
        {},
        generic_yaml_refs,
    )
    require(
        len(final_yaml_gaps) == 2 and all("[acceptance:reachability]" in item for item in final_yaml_gaps),
        "Confirmed YAML must be audited from executable flow actions; case metadata alone cannot hide missing destination checks",
    )
    for index, branch in enumerate(("订单管理", "优惠券")):
        generic_yaml_refs[index]["content"] = generic_acceptance_yaml(branch, include_reachability=True)
    final_yaml_gaps, _mapped_ids, _required_ids = agent_service._agent_final_yaml_coverage_points(
        {"analysis": generic_analysis},
        {},
        generic_yaml_refs,
    )
    require(
        not final_yaml_gaps,
        "Visible-text click, bounded terminal wait and terminal assertion in confirmed YAML must satisfy reachability",
    )
    require(classify_generated_yaml_failure_bucket([{"failureType": "ENV_ISSUE", "reason": "model request was aborted"}]) == "模型/环境失败", "Model service failures must not be classified as YAML failures")

    separated = agent_service._agent_create_runner_jobs_for_refs(
        {}, [], "runner", "device", "fixed", initial_blocked=[{"reason": "smoke selection excluded"}]
    )
    require(not separated.get("dryRunBlocked") and separated.get("selectionExcluded"), "Smoke selection exclusions must stay separate from actual dry-run failures")


def check_agent_failure_review_and_repair_guard():
    from task_server.services import agent_service
    from task_server.services import ai_skill_service
    from task_server.services import job_service
    from task_server.services import repair_service
    from task_server.services import yaml_service

    progress_run = {
        "currentStep": "RUN_TASK",
        "steps": [{"step": "RUN_TASK", "status": "RUNNING"}],
        "artifacts": {},
    }
    job_service._update_agent_job_progress_trace(
        progress_run,
        completed=[],
        failed=[],
        running=[{"status": "running", "file": "active.yaml"}],
        queued=[{"status": "pending", "file": "queued.yaml"}],
        elapsed=10,
        timeout=1800,
        phase="首批冒烟",
        force=True,
    )
    progress_summary = progress_run["steps"][0].get("summary") or ""
    require("1 执行中 / 1 排队中" in progress_summary and "2 运行中" not in progress_summary, "One fixed device must expose one executing and one queued job instead of two running jobs")

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
    bare_timeout_review = agent_service._normalize_failed_execution_item({
        "jobId": "job-static-bare-timeout",
        "stderrTail": "Timeout after 300s",
        "failureReview": {
            "category": "env_issue",
            "confidence": 0.95,
            "reason": "Timeout after 300s",
        },
    })
    require(bare_timeout_review.get("failureType") != "ENV_ISSUE", "A bare wall-clock timeout must remain open to AI keyframe reclassification")
    require(
        agent_service._agent_failed_item_has_concrete_environment_evidence({"stderrTail": "Timeout after 300s"}) is False,
        "A raw wall-clock timeout must not lock the Agent into an infrastructure diagnosis",
    )
    require(
        agent_service._agent_failed_item_has_concrete_environment_evidence({"stderrTail": "model request was aborted"}) is True,
        "Concrete model infrastructure evidence must remain locked as an environment failure",
    )
    original_navigation = "android:\n  tasks:\n    - name: photo\n      flow:\n        - aiTap: 照片打印\n        - aiWaitFor: 目标入口可见\n"
    repaired_navigation = "android:\n  tasks:\n    - name: photo\n      flow:\n        - aiTap: 照片打印\n        - aiTap: 照片打印\n        - aiWaitFor: 目标入口可见\n"
    repaired_assertion = original_navigation.replace("目标入口可见", "页面已稳定")
    require(
        agent_service._agent_repair_navigation_signature(original_navigation)
        != agent_service._agent_repair_navigation_signature(repaired_navigation),
        "Navigation evidence gate must detect added parent/child actions from YAML rather than AI prose",
    )
    require(
        agent_service._agent_repair_navigation_signature(original_navigation)
        == agent_service._agent_repair_navigation_signature(repaired_assertion),
        "An assertion-only repair must not be mistaken for a navigation rewrite",
    )
    low_confidence_review = agent_service._normalize_failed_execution_item({
        "jobId": "job-static-low-confidence-review",
        "stderrTail": "failed to locate element: 照片打印",
        "failureReview": {
            "category": "env_issue",
            "confidence": 0.32,
            "reason": "可能是模型服务波动",
        },
    })
    require(low_confidence_review.get("failureType") == "SCRIPT_ISSUE", "Low-confidence failure review must not override concrete Runner script evidence")
    source_mismatch_review = agent_service._normalize_failed_execution_item({
        "jobId": "job-static-review-source-mismatch",
        "summaryText": (
            "waitFor timeout: 当前页面是应用的首页，虽然首页上有“文档打印”的入口按钮，"
            "但用户并没有进入“文档打印”的具体页面。因此，“等待文档打印页面加载完成”这个状态描述不准确。"
        ),
        "failureReview": {
            "category": "unknown",
            "failure_type": "review_source_mismatch",
            "confidence": 0.45,
            "reason": "失败复检引用了当前 YAML、执行日志或报告文本中不存在的控件/步骤",
            "can_auto_repair": False,
        },
    })
    source_mismatch_eligibility = agent_service._agent_repair_eligibility(source_mismatch_review)
    require(
        source_mismatch_review.get("failureType") == "SCRIPT_ISSUE"
        and source_mismatch_review.get("canAutoRepair") is None
        and source_mismatch_eligibility.get("eligible") is True,
        "Low-confidence source-mismatch review must not block auto repair for concrete Runner script evidence",
    )
    require(
        agent_service._agent_job_failure_type("invalid_enum_value: expected 'down' | 'up' | 'right' | 'left', received horizontal") == "YAML 动作参数不兼容",
        "Midscene parameter schema errors must be classified as script issues instead of unknown failures",
    )
    overlay_yaml = """android:
  tasks:
    - name: overlay check
      flow:
        - aiWaitFor: 页面加载完成
        - aiAssert: 页面无遮挡
"""
    negated_overlay = ai_skill_service.classify_failure_by_context({
        "yaml_text": overlay_yaml,
        "task_block": overlay_yaml,
        "evidence_text": "assertion failed: 页面无遮挡，未出现弹窗",
        "failure_brief": {},
    }) or {}
    require(negated_overlay.get("failure_type") != "popup_overlay", "Negated no-overlay assertions must not be misclassified as an observed popup")
    positive_overlay = ai_skill_service.classify_failure_by_context({
        "yaml_text": overlay_yaml,
        "task_block": overlay_yaml,
        "evidence_text": "运行截图显示权限弹窗弹出并遮挡入口按钮",
        "failure_brief": {},
    }) or {}
    require(positive_overlay.get("failure_type") == "popup_overlay", "Concrete runtime popup evidence must remain auto-repairable")
    visible_controls_overlay = ai_skill_service.positive_overlay_evidence(
        "failed to locate element: 截图中未找到文本为“立即使用”的元素。弹窗底部只有“取消”和“确定”按钮。"
    )
    require(
        visible_controls_overlay,
        "A runtime screenshot description with concrete popup controls must count as positive overlay evidence",
    )
    exact_copy_yaml = """android:
  tasks:
    - name: 企业云盘入口文案
      flow:
        - launch: com.example.app
        - aiWaitFor: App 首页加载完成，可见「业务中心」入口
        - aiTap: 业务中心
        - aiWaitFor: 页面展示「企业云盘」入口
        - aiAssert: '「企业云盘」入口文案严格等于“企业云盘”'
"""
    exact_copy_mismatch = ai_skill_service.classify_failure_by_context({
        "yaml_text": exact_copy_yaml,
        "task_block": exact_copy_yaml,
        "evidence_text": (
            "waitFor timeout: 当前页面实际文案是“企业云盘上传”。"
            "需求要求入口文案严格等于“企业云盘”，实际值并不严格等于期望值，因此陈述为假。"
        ),
        "failure_brief": {},
    }) or {}
    require(
        exact_copy_mismatch.get("category") == "product_bug"
        and exact_copy_mismatch.get("failure_type") == "visible_value_mismatch"
        and exact_copy_mismatch.get("can_auto_repair") is False,
        "A visible actual-versus-exact-expected copy mismatch must remain a product failure, not an assertion repair",
    )
    normalized_product_mismatch = agent_service._normalize_failed_execution_item({
        "jobId": "job-visible-value-mismatch",
        "stderrTail": "waitFor timeout",
        "failureReview": exact_copy_mismatch,
    })
    require(
        normalized_product_mismatch.get("failureType") == "PRODUCT_BUG"
        and agent_service._agent_repair_eligibility(normalized_product_mismatch).get("eligible") is False,
        "High-confidence visible-value product evidence must block automatic YAML repair and rerun",
    )
    assertion_drift_yaml = exact_copy_yaml.replace(
        "入口文案严格等于“企业云盘”",
        "入口文案严格等于“企业云盘上传”",
    )
    assertion_drift_gate = agent_service._agent_repair_candidate_gate(
        exact_copy_yaml,
        {"fixedYaml": assertion_drift_yaml, "analysis": "按当前页面实际文案修正断言"},
        [],
        platform="android",
    )
    assertion_preserved_yaml = exact_copy_yaml.replace(
        "页面展示「企业云盘」入口",
        "企业云盘入口区域已加载，页面展示「企业云盘」入口",
    )
    assertion_preserved_gate = agent_service._agent_repair_candidate_gate(
        exact_copy_yaml,
        {"fixedYaml": assertion_preserved_yaml, "analysis": "只补充可观察稳定态，保持文案断言不变"},
        [],
        platform="android",
    )
    require(
        any(item.get("code") == "assertion_contract_drift" for item in assertion_drift_gate.get("issues") or [])
        and assertion_drift_gate.get("assertionContractPreserved") is False,
        "Agent repair must reject candidates that replace an exact requirement value with the current product value",
    )
    require(
        assertion_preserved_gate.get("ok") is True
        and assertion_preserved_gate.get("assertionContractPreserved") is True,
        "A wait-only repair that preserves the exact visible-value contract must remain eligible",
    )
    clipped_scan_yaml = """android:
  tasks:
    - name: 扫描复印入口展示
      flow:
        - aiTap: 扫描复印
        - aiWaitFor: 百度网盘入口可见
"""
    clipped_scan_issue = ai_skill_service.detect_horizontal_scroll_script_issue(
        clipped_scan_yaml,
        "等待百度网盘入口失败，没有发现目标；右侧有一个被截断的同级导入图标，只显示入口行前部",
    ) or {}
    require(
        clipped_scan_issue.get("category") == "script_issue"
        and clipped_scan_issue.get("can_auto_repair") is True
        and "aiScroll" in clipped_scan_issue.get("suggested_action", "")
        and "禁止坐标或 ADB swipe" in clipped_scan_issue.get("suggested_action", ""),
        "A report keyframe describing a clipped sibling row must allow one visible-text aiScroll repair even when the original YAML omitted scrolling",
    )
    clipped_scan_english_issue = ai_skill_service.detect_horizontal_scroll_script_issue(
        clipped_scan_yaml,
        (
            'I see "Local Import", "Album Import", and "WeChat Import". '
            'To the right of "WeChat Import", there is a partially visible icon that resembles the '
            'Baidu Netdisk logo, but the text "Baidu Netdisk" is cut off and not visible.'
        ),
    ) or {}
    require(
        clipped_scan_english_issue.get("category") == "script_issue"
        and clipped_scan_english_issue.get("can_auto_repair") is True
        and "aiScroll" in clipped_scan_english_issue.get("suggested_action", ""),
        "English visual-model evidence for a clipped sibling row must enter the same bounded horizontal repair path",
    )
    patch_source = """android:
  tasks:
    - name: 横向来源入口检查
      flow:
        - launch: com.example.app
        - aiWaitFor: 来源页面加载完成
        - aiWaitFor: 企业云盘入口可见
"""
    patch_info = yaml_service.find_yaml_task_block(patch_source, "横向来源入口检查")
    patched_block, applied_patches = repair_service.apply_task_repair_patches(
        patch_info["block"],
        [{
            "op": "insert_after",
            "anchor": "aiWaitFor: 来源页面加载完成",
            "lines": [
                "aiScroll: 本地导入、相册导入、微信导入所在的横向入口区域",
                "scrollType: singleAction",
                "direction: right",
                "distance: 350",
                "sleep: 500",
            ],
            "reason": "失败关键帧显示右侧同级入口被裁切",
        }],
    )
    patched_yaml = yaml_service.normalize_full_yaml_structure(
        yaml_service.replace_yaml_task_block(patch_source, patch_info, patched_block)
    )
    patch_validation = yaml_service.validate_midscene_yaml_executability(patched_yaml)
    require(
        patch_validation.get("ok") is True
        and applied_patches
        and re.search(r"direction:\s*[\"']?right", patched_yaml)
        and not any("未知动作：direction" in item for item in patch_validation.get("issues") or []),
        "Structured repair patches must nest aiScroll child fields under one official action before candidate validation",
    )
    home_guard_source = """android:
  tasks:
    - name: 回到首页后进入业务页
      flow:
        - launch: com.example.app
        - aiWaitFor: 被测 App 首页已加载完成，首页核心功能入口可见
        - aiTap: 点击「文档打印」入口
"""
    home_guard_info = yaml_service.find_yaml_task_block(home_guard_source, "回到首页后进入业务页")
    guarded_block, guarded_patches = repair_service.apply_task_repair_patches(
        home_guard_info["block"],
        [{
            "op": "insert_after",
            "anchor": "launch: com.example.app",
            "lines": [
                "aiTap: 点击底部导航栏的'首页'图标",
                "aiWaitFor: 首页顶部'文档打印'、'照片打印'等核心入口可见",
                "timeout: 8000",
            ],
            "reason": "启动后可能停留在非首页 Tab",
        }, {
            "op": "replace_step",
            "anchor": 'aiWaitFor: "被测 App 首页已加载完成，首页核心功能入口可见"\n  timeout: 8000',
            "lines": [
                "aiWaitFor: 首页顶部'文档打印'、'照片打印'等核心入口可见",
                "timeout: 8000",
            ],
            "reason": "用真实首页核心入口替代泛化等待",
        }],
    )
    require(
        len(guarded_patches) == 2
        and "点击底部导航栏" in guarded_block
        and "被测 App 首页已加载完成" not in guarded_block,
        "Repair patch anchors that include an optional timeout child must still match a unique original flow item by its exact action text",
    )
    ambiguous_patch_source = patch_source.replace(
        "        - aiWaitFor: 企业云盘入口可见",
        "        - aiWaitFor: 来源页面加载完成\n        - aiWaitFor: 企业云盘入口可见",
    )
    ambiguous_info = yaml_service.find_yaml_task_block(ambiguous_patch_source, "横向来源入口检查")
    try:
        repair_service.apply_task_repair_patches(
            ambiguous_info["block"],
            [{
                "op": "insert_after",
                "anchor": "aiWaitFor: 来源页面加载完成",
                "lines": ["sleep: 500"],
                "reason": "歧义锚点测试",
            }],
        )
        ambiguous_rejected = False
    except ValueError as exc:
        ambiguous_rejected = "不唯一" in str(exc)
    require(ambiguous_rejected, "A repair patch with a repeated anchor must be rejected instead of mutating the first matching step")
    for prohibited_lines in (
        ["runAdbShell: input swipe 100 100 900 100"],
        ["aiTap: 企业云盘入口", "xpath: //*[@text='企业云盘']"],
        ["aiTap: 点击坐标 (120, 300)"],
    ):
        try:
            repair_service.apply_task_repair_patches(
                patch_info["block"],
                [{
                    "op": "insert_after",
                    "anchor": "aiWaitFor: 来源页面加载完成",
                    "lines": prohibited_lines,
                    "reason": "不安全补丁测试",
                }],
            )
            prohibited_rejected = False
        except ValueError as exc:
            prohibited_rejected = "禁止" in str(exc)
        require(prohibited_rejected, "AI repair patches must deterministically reject shell and XPath locator mechanics")
    try:
        repair_service.apply_task_repair_patches(
            patch_info["block"],
            [{
                "op": "insert_after",
                "anchor": "aiWaitFor: 来源页面",
                "lines": ["aiWaitFor: 企业云盘入口可见"],
                "reason": "部分锚点测试",
            }],
        )
        partial_anchor_rejected = False
    except ValueError as exc:
        partial_anchor_rejected = "未找到" in str(exc)
    require(partial_anchor_rejected, "A repair patch anchor must match one complete original flow item, not a substring")
    for protected_anchor in ("launch: com.example.app", "aiWaitFor: 企业云盘入口可见"):
        protected_op = "replace_step" if protected_anchor.startswith("launch:") else "remove_step"
        protected_lines = ["aiWaitFor: 来源页面加载完成"] if protected_op == "replace_step" else []
        try:
            repair_service.apply_task_repair_patches(
                patch_info["block"],
                [{
                    "op": protected_op,
                    "anchor": protected_anchor,
                    "lines": protected_lines,
                    "reason": "受保护步骤测试",
                }],
            )
            protected_step_rejected = False
        except ValueError as exc:
            protected_step_rejected = "禁止" in str(exc)
        require(
            protected_step_rejected,
            "AI repair patches must not replace lifecycle mechanics or remove business observations",
        )
    quoted_block, _ = repair_service.apply_task_repair_patches(
        patch_info["block"],
        [{
            "op": "replace_step",
            "anchor": "aiWaitFor: 企业云盘入口可见",
            "lines": ['aiWaitFor: 页面文案为"企业云盘"且入口稳定可见'],
            "reason": "标量安全序列化测试",
        }],
    )
    quoted_yaml = yaml_service.normalize_full_yaml_structure(
        yaml_service.replace_yaml_task_block(patch_source, patch_info, quoted_block)
    )
    require(
        yaml_service.validate_midscene_yaml_executability(quoted_yaml).get("ok") is True,
        "Task Server must safely serialize model patch scalars that contain UI-copy quotes",
    )
    require(
        agent_service._normalize_agent_failed_items([
            {"jobId": "job-a", "stderrTail": "failed to locate element: 照片打印"},
            {"jobId": "job-a", "stderrTail": "duplicate"},
        ])[0].get("failureType") == "SCRIPT_ISSUE",
        "Latest rerun evidence must be normalized and deduplicated before bounded AI repair",
    )
    bounded = agent_service._agent_post_rerun_autonomy(
        {"artifacts": {}},
        [{"jobId": "job-b", "stderrTail": "failed to locate element: 照片打印"}],
        repair_depth=1,
    )
    require(not bounded.get("analyzed") and "上限" in bounded.get("reason", ""), "Post-rerun AI repair must stop after one bounded cycle")
    old_analyze_failure = agent_service._tool_analyze_failure
    old_generate_repair = agent_service._tool_generate_repair
    try:
        def fake_analyze_failure(run, failed_jobs_override=None):
            run.setdefault("artifacts", {})["failureAnalysis"] = {
                "failureType": "SCRIPT_ISSUE",
                "canAutoRepair": True,
                "evidence": {"reportKeyframes": ["latest-failure.png"]},
            }
            return {"status": "SUCCESS"}

        agent_service._tool_analyze_failure = fake_analyze_failure
        agent_service._tool_generate_repair = lambda run, failed_jobs_override=None: {
            "status": "SUCCESS",
            "aiUsed": True,
            "repairDraftIds": ["repair-latest"],
        }
        autonomy = agent_service._agent_post_rerun_autonomy(
            {"artifacts": {}},
            [{"jobId": "job-c", "stderrTail": "failed to locate element: 5寸照片"}],
            repair_depth=0,
        )
        require(autonomy.get("repairGenerated") and autonomy.get("repairDraftIds") == ["repair-latest"], "Latest script evidence must trigger one AI repair draft before same-device verification")
        require(autonomy.get("reportKeyframes") == ["latest-failure.png"], "Post-rerun repair must retain the latest report keyframe provenance")
    finally:
        agent_service._tool_analyze_failure = old_analyze_failure
        agent_service._tool_generate_repair = old_generate_repair
    script_timeout = agent_service._normalize_failed_execution_item({
        "jobId": "job-static-page-timeout",
        "failureType": "等待目标超时",
        "summaryText": "waitFor timeout: 当前已经进入文档打印页面，但脚本仍在等待打印首页",
    })
    require(script_timeout.get("failureType") == "SCRIPT_ISSUE", "Display labels such as 等待目标超时 must normalize to SCRIPT_ISSUE")
    require(script_timeout.get("failureKind") == "等待目标超时", "Agent must retain the concrete failure kind for display and evidence")
    require(agent_service._agent_should_confirm_unknown_failure({}, "UNKNOWN"), "Unreviewed UNKNOWN failures must request confirmation")
    require(not agent_service._agent_should_confirm_unknown_failure({"unknownFailureConfirmed": True}, "UNKNOWN"), "Reviewed UNKNOWN failures must not enter a confirmation loop")

    old_gateway_available_for_analysis = agent_service._ai_gateway_available
    old_gateway_post_for_analysis = agent_service._ai_gateway_post
    old_log_for_analysis = agent_service._log_tool_call
    try:
        agent_service._ai_gateway_available = lambda: False
        agent_service._log_tool_call = lambda *args, **kwargs: None
        stale_precheck_run = {
            "target": "任意入口验证",
            "artifacts": {
                "executionPrecheck": {
                    "diagnosis": {"rootCause": "准备阶段曾出现环境提示", "impact": "旧快照", "nextActions": []},
                },
            },
        }
        agent_service._tool_analyze_failure(stale_precheck_run, failed_jobs_override=[{
            "jobId": "job-latest-script",
            "status": "failed",
            "stderrTail": "failed to locate element: 目标入口",
        }])
        require(stale_precheck_run["artifacts"]["failureAnalysis"].get("failureType") == "SCRIPT_ISSUE", "Latest Runner terminal evidence must take precedence over stale execution-precheck diagnosis")
        agent_service._ai_gateway_available = lambda: True
        agent_service._ai_gateway_post = lambda *_args, **_kwargs: {
            "failureType": "SCRIPT_ISSUE",
            "analysis": "尝试放宽断言",
            "canAutoRepair": True,
        }
        product_lock_run = {"target": "明确文案契约", "artifacts": {}}
        agent_service._tool_analyze_failure(product_lock_run, failed_jobs_override=[{
            "jobId": "job-product-lock",
            "status": "failed",
            "failureType": "PRODUCT_BUG",
            "failureReview": exact_copy_mismatch,
            "stderrTail": "waitFor timeout",
        }])
        require(
            product_lock_run["artifacts"]["failureAnalysis"].get("failureType") == "PRODUCT_BUG",
            "Aggregate AI analysis must not downgrade a high-confidence product mismatch into an auto-repairable script issue",
        )
    finally:
        agent_service._ai_gateway_available = old_gateway_available_for_analysis
        agent_service._ai_gateway_post = old_gateway_post_for_analysis
        agent_service._log_tool_call = old_log_for_analysis

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

    executable_original = """android:
  tasks:
    - name: 文档打印入口展示冒烟
      flow:
        - launch: com.xbxxhz.box
        - aiWaitFor: 首页中可见文档打印入口
        - aiTap: 首页中名称为文档打印的入口
        - aiWaitFor: 文档打印页面加载完成并可见本地文档入口
        - aiAssert: 文档打印页面展示百度网盘入口
"""
    executable_degraded = executable_original.replace(
        "        - aiWaitFor: 首页中可见文档打印入口\n",
        "",
    ).replace(
        "        - aiWaitFor: 文档打印页面加载完成并可见本地文档入口\n",
        "",
    )
    executable_regression_gate = agent_service._agent_repair_candidate_gate(
        executable_original,
        {"fixedYaml": executable_degraded, "analysis": "删除入口前等待"},
        [],
        platform="android",
    )
    require(
        any(
            item.get("code") == "repair_executable_gate_regression"
            for item in executable_regression_gate.get("issues") or []
        )
        and executable_regression_gate.get("originalExecutableScore", {}).get("executionLevel") == "executable"
        and executable_regression_gate.get("fixedExecutableScore", {}).get("executionLevel") == "needs_review",
        "AI repair must not downgrade a YAML that already passed the generated executable scorer",
    )

    old_task_dir = agent_service.TASK_DIR
    old_gateway_available = agent_service._ai_gateway_available
    old_repair_skill = ai_skill_service.run_ai_skill
    old_log_tool_call = agent_service._log_tool_call
    old_upsert = repair_service.upsert_repair_draft
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            agent_service.TASK_DIR = temp_dir
            module_dir = Path(temp_dir) / "AI_Agent_草稿"
            module_dir.mkdir()
            (module_dir / "case.yaml").write_text(original, encoding="utf-8")
            agent_service._ai_gateway_available = lambda: True
            ai_skill_service.run_ai_skill = lambda *args, **kwargs: {
                "analysis": "只增加固定等待",
                "changes": ["启动后等待 3 秒"],
                "patches": [{
                    "op": "insert_after",
                    "anchor": "launch: com.xbxxhz.box",
                    "lines": ["sleep: 3000"],
                    "reason": "等待页面",
                }],
                "usedBaselineIds": [],
            }
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
        ai_skill_service.run_ai_skill = old_repair_skill
        agent_service._log_tool_call = old_log_tool_call
        repair_service.upsert_repair_draft = old_upsert

    malformed_quoted_fix = original.replace(
        "        - aiAssert: 百度网盘入口可见",
        "        - aiScroll:\n"
        "            target: 本地导入、相册导入、微信导入等入口所在的横向区域\n"
        "            direction: right\n"
        "            distance: 350\n"
        "            scrollType: singleAction\n"
        '        - aiAssert: "「百度网盘」入口可见，文案为"百度网盘""',
    )
    horizontal_scroll_fix = original.replace(
        "        - aiAssert: 百度网盘入口可见",
        "        - aiScroll: 本地导入、相册导入、微信导入等入口所在的横向区域\n"
        "          direction: right\n"
        "          distance: 350\n"
        "          scrollType: singleAction\n"
        "        - aiAssert: 百度网盘入口可见，文案为“百度网盘”",
    )
    unguarded_navigation_fix = original.replace(
        "        - aiAssert: 百度网盘入口可见",
        "        - aiTap: 照片打印\n        - aiTap: 5寸照片\n        - aiAssert: 百度网盘入口可见",
    )
    grounded_navigation_fix = unguarded_navigation_fix.replace(
        "        - aiTap: 文档打印",
        "        - aiWaitFor: 首页入口区域稳定显示\n        - aiTap: 文档打印",
    )
    branch_baseline = {
        "id": "base-photo-path",
        "provenancePath": "server-tasks-all/基础打印/6寸照片打印.yaml",
        "retrievalRoles": ["business_branch"],
        "retrievalBranchIds": ["FLOW-PHOTO"],
        "retrievalAnchors": ["照片打印", "照片"],
    }
    global_baseline = {
        "id": "base-global-doc",
        "provenancePath": "server-tasks-all/基础打印/文档打印.yaml",
        "retrievalRoles": ["global_relevance"],
    }
    cross_branch_gate = agent_service._agent_repair_candidate_gate(
        original,
        {
            "fixedYaml": grounded_navigation_fix,
            "analysis": "补齐父子导航路径",
            "usedBaselineIds": ["base-global-doc"],
        },
        [branch_baseline, global_baseline],
        platform="android",
    )
    require(
        any(item.get("code") == "navigation_change_without_branch_baseline" for item in cross_branch_gate.get("issues") or []),
        "A global or sibling baseline citation must not authorize a current-branch navigation change",
    )
    overlay_original = """android:
  tasks:
    - name: 扫描复印入口校验
      flow:
        - launch: com.xbxxhz.box
        - aiWaitFor: 首页核心入口可见
        - aiTap: 点击「扫描复印 icon」
        - aiTap: 点击「证件扫描」
        - aiTap: 点击「立即使用」
        - aiAssert: 「百度网盘」入口可见
"""
    overlay_fixed = overlay_original.replace(
        "        - aiTap: 点击「证件扫描」",
        "        - aiTap: 点击「证件扫描」\n"
        "        - ai: 如果出现权限说明弹窗，点击其中可见的「确定」按钮；若随后出现系统权限弹窗则点击「允许」",
    )
    overlay_response = {
        "fixedYaml": overlay_fixed,
        "analysis": "失败关键帧显示权限弹窗遮挡原业务步骤，仅插入临时弹窗处理并保持导航不变",
        "changes": ["在证件扫描后插入权限弹窗处理"],
        "patches": [{
            "op": "insert_after",
            "anchor": "aiTap: 点击「证件扫描」",
            "lines": ["ai: 如果出现权限说明弹窗，点击其中可见的「确定」按钮；若随后出现系统权限弹窗则点击「允许」"],
            "reason": "失败关键帧显示弹窗底部只有取消和确定",
        }],
        "usedBaselineIds": [],
    }
    overlay_runtime_evidence = {
        "error": "failed to locate element: 截图中未找到文本为“立即使用”的元素。弹窗底部只有“取消”和“确定”按钮。",
        "reportKeyframes": ["failure-frame-1.jpg"],
    }
    overlay_gate = agent_service._agent_repair_candidate_gate(
        overlay_original,
        overlay_response,
        [branch_baseline],
        platform="android",
        runtime_evidence=overlay_runtime_evidence,
    )
    require(
        overlay_gate.get("ok") is True
        and overlay_gate.get("navigationChanged") is True
        and overlay_gate.get("baselineCitationExempt") is True
        and overlay_gate.get("transientOverlayChange", {}).get("matchedControls") == ["确定"],
        "A keyframe-backed transient overlay handler may preserve the business path without citing a navigation baseline",
    )
    ungrounded_overlay_gate = agent_service._agent_repair_candidate_gate(
        overlay_original,
        overlay_response,
        [branch_baseline],
        platform="android",
        runtime_evidence={"error": "failed to locate element", "reportKeyframes": ["failure-frame-1.jpg"]},
    )
    require(
        any(item.get("code") == "navigation_change_without_baseline_citation" for item in ungrounded_overlay_gate.get("issues") or [])
        and ungrounded_overlay_gate.get("baselineCitationExempt") is False,
        "A generic locate failure must not unlock the transient-overlay baseline exception",
    )
    cross_job_overlay_gate = agent_service._agent_repair_candidate_gate(
        overlay_original,
        overlay_response,
        [branch_baseline],
        platform="android",
        runtime_evidence={
            "error": "failed to locate element",
            "failureAnalysis": overlay_runtime_evidence["error"],
            "reportKeyframes": ["failure-frame-1.jpg"],
        },
    )
    require(
        any(item.get("code") == "navigation_change_without_baseline_citation" for item in cross_job_overlay_gate.get("issues") or [])
        and cross_job_overlay_gate.get("baselineCitationExempt") is False,
        "A popup mentioned only in aggregate or AI analysis must not authorize another failed job",
    )
    business_navigation_response = dict(overlay_response)
    business_navigation_response["fixedYaml"] = overlay_original.replace(
        "        - aiTap: 点击「立即使用」",
        "        - aiTap: 点击「照片打印」\n        - aiTap: 点击「立即使用」",
    )
    business_navigation_response["patches"] = [{
        "op": "insert_before",
        "anchor": "aiTap: 点击「立即使用」",
        "lines": ["aiTap: 点击「照片打印」"],
        "reason": "改走其他入口",
    }]
    business_navigation_gate = agent_service._agent_repair_candidate_gate(
        overlay_original,
        business_navigation_response,
        [branch_baseline],
        platform="android",
        runtime_evidence=overlay_runtime_evidence,
    )
    require(
        any(item.get("code") == "navigation_change_without_baseline_citation" for item in business_navigation_gate.get("issues") or [])
        and business_navigation_gate.get("baselineCitationExempt") is False,
        "Popup evidence must not authorize an unrelated business navigation insertion",
    )
    embedded_navigation_response = dict(overlay_response)
    embedded_navigation_response["fixedYaml"] = overlay_original.replace(
        "        - aiTap: 点击「立即使用」",
        "        - ai: 如果权限弹窗出现，点击「确定」，然后点击「照片打印」\n"
        "        - aiTap: 点击「立即使用」",
    )
    embedded_navigation_response["patches"] = [{
        "op": "insert_before",
        "anchor": "aiTap: 点击「立即使用」",
        "lines": ["ai: 如果权限弹窗出现，点击「确定」，然后点击「照片打印」"],
        "reason": "混入业务导航",
    }]
    embedded_navigation_gate = agent_service._agent_repair_candidate_gate(
        overlay_original,
        embedded_navigation_response,
        [branch_baseline],
        platform="android",
        runtime_evidence=overlay_runtime_evidence,
    )
    require(
        any(item.get("code") == "navigation_change_without_baseline_citation" for item in embedded_navigation_gate.get("issues") or [])
        and embedded_navigation_gate.get("baselineCitationExempt") is False,
        "A compound AI action must not hide business navigation inside an overlay dismissal",
    )
    startup_guard_gate = agent_service._agent_repair_candidate_gate(
        original,
        {
            "fixedYaml": unguarded_navigation_fix,
            "analysis": "根据分支基线补齐导航",
            "usedBaselineIds": ["base-photo-path"],
        },
        [branch_baseline],
        platform="android",
    )
    require(
        any(item.get("code") == "navigation_missing_ready_wait" for item in startup_guard_gate.get("issues") or []),
        "A repair that adds navigation immediately after launch must receive one bounded AI correction for a visible start-page ready wait",
    )
    unchanged_navigation_gate = agent_service._agent_repair_candidate_gate(
        original,
        {
            "fixedYaml": horizontal_scroll_fix,
            "analysis": "新增横向探索；保持原有导航路径和断言逻辑不变",
            "changes": ["在当前入口行增加一次有证据约束的横向滚动"],
        },
        [],
        platform="android",
    )
    require(
        unchanged_navigation_gate.get("ok") is True
        and unchanged_navigation_gate.get("navigationClaimed") is False
        and unchanged_navigation_gate.get("navigationChanged") is False,
        "A real aiScroll-only repair that explicitly preserves navigation must not be rejected as a navigation mutation claim",
    )
    source_backed_original = """android:
  tasks:
    - name: 照片打印页百度网盘入口校验
      # baseline.case_id: TC-003
      flow:
        - launch: com.xbxxhz.box
        - aiTap: 照片打印
        - aiWaitFor: 百度网盘入口可见
        - aiTap: 5寸照片
        - aiAssert: 百度网盘入口可见
"""
    source_backed_late = source_backed_original.replace(
        "        - aiTap: 照片打印",
        "        - aiWaitFor: App 首页加载完成，照片打印入口可见\n        - aiTap: 照片打印",
    )
    source_backed_fixed = source_backed_late.replace(
        "        - aiWaitFor: 百度网盘入口可见\n        - aiTap: 5寸照片",
        "        - aiTap: 5寸照片\n        - aiWaitFor: 百度网盘入口可见",
    )
    source_backed_drift = source_backed_fixed.replace("aiTap: 5寸照片", "aiTap: 6寸照片")
    source_backed_run = {
        "artifacts": {
            "visualReferenceReport": {
                "visualBatchResults": [{
                    "status": "completed",
                    "currentPageEvidence": [{
                        "caseId": "TC-003",
                        "requirementId": "REQ-002",
                        "branch": "照片打印",
                        "pageTitle": "5寸照片",
                        "parentPath": ["首页", "照片打印"],
                        "navigationLeaf": "5寸照片",
                        "targetText": "百度网盘",
                        "sameBranch": True,
                        "confidence": 0.95,
                        "source": "figma_current_frame",
                    }],
                }],
            },
        },
    }
    source_backed_evidence = agent_service._agent_source_evidence(source_backed_run)
    require(
        source_backed_evidence.get("visualCurrentPageEvidence", [{}])[0].get("navigationLeaf") == "5寸照片",
        "Agent repair input must retain bounded visual AI current-page evidence",
    )
    late_leaf_gate = agent_service._agent_repair_candidate_gate(
        source_backed_original,
        {"fixedYaml": source_backed_late, "analysis": "补充启动等待"},
        [branch_baseline],
        platform="android",
        source_evidence=source_backed_evidence,
    )
    drift_gate = agent_service._agent_repair_candidate_gate(
        source_backed_original,
        {
            "fixedYaml": source_backed_drift,
            "analysis": "根据分支基线调整父子导航",
            "usedBaselineIds": ["base-photo-path"],
        },
        [branch_baseline],
        platform="android",
        source_evidence=source_backed_evidence,
    )
    grounded_source_gate = agent_service._agent_repair_candidate_gate(
        source_backed_original,
        {"fixedYaml": source_backed_fixed, "analysis": "补充启动等待并保持当前目标叶子"},
        [branch_baseline],
        platform="android",
        source_evidence=source_backed_evidence,
    )
    require(
        any(item.get("code") == "source_backed_leaf_after_target_check" for item in late_leaf_gate.get("issues") or [])
        and any(item.get("code") == "source_backed_navigation_target_removed" for item in drift_gate.get("issues") or [])
        and grounded_source_gate.get("ok") is True,
        "Repair candidates must enter the adopted visual leaf before assertions and must not replace it with a baseline sample value",
    )
    runtime_leaf_original = source_backed_fixed.replace("5寸照片", "一寸照")
    runtime_leaf_fixed = source_backed_fixed
    runtime_leaf_baseline = {
        **branch_baseline,
        "id": "base-photo-runtime-leaf",
        "businessPath": "首页 -> 照片打印 -> 6寸照片 -> 相册导入",
        "content": "aiTap: 点击「6寸照片」",
    }
    runtime_source_evidence = copy.deepcopy(source_backed_evidence)
    runtime_source_evidence["visualCurrentPageEvidence"].append({
        "caseId": "TC-003",
        "requirementId": "REQ-002",
        "branch": "照片打印",
        "pageTitle": "一寸照",
        "parentPath": ["首页", "照片打印"],
        "navigationLeaf": "一寸照",
        "targetText": "百度网盘",
        "sameBranch": True,
        "confidence": 0.95,
        "source": "figma_current_frame",
    })
    runtime_leaf_response = {
        "fixedYaml": runtime_leaf_fixed,
        "analysis": "真机尺寸弹窗明确没有一寸照；按当前分支 Figma 与成功基线改为可见的 5寸照片",
        "changes": ["将失败步骤的一寸照替换为 5寸照片，保持百度网盘断言不变"],
        "usedBaselineIds": ["base-photo-runtime-leaf"],
    }
    runtime_leaf_rejected_without_failure = agent_service._agent_repair_candidate_gate(
        runtime_leaf_original,
        runtime_leaf_response,
        [runtime_leaf_baseline],
        platform="android",
        source_evidence=runtime_source_evidence,
        runtime_evidence={"reportKeyframes": ["photo-size-dialog.jpg"]},
    )
    runtime_leaf_corrected = agent_service._agent_repair_candidate_gate(
        runtime_leaf_original,
        runtime_leaf_response,
        [runtime_leaf_baseline],
        platform="android",
        source_evidence=runtime_source_evidence,
        runtime_evidence={
            "summaryText": "failed to locate element: 我看到了照片尺寸选择弹窗，但其中并没有「一寸照」这个选项。",
            "reportKeyframes": ["photo-size-dialog.jpg"],
        },
    )
    require(
        any(
            item.get("code") == "source_backed_navigation_target_removed"
            for item in runtime_leaf_rejected_without_failure.get("issues") or []
        ),
        "A sibling source leaf must remain protected when runtime evidence does not disprove the adopted leaf",
    )
    require(
        runtime_leaf_corrected.get("ok") is True
        and runtime_leaf_corrected.get("assertionContractPreserved") is True
        and (runtime_leaf_corrected.get("sourceLeafRuntimeOverrides") or [{}])[0].get("fromLeaf") == "一寸照"
        and (runtime_leaf_corrected.get("sourceLeafRuntimeOverrides") or [{}])[0].get("toLeaf") == "5寸照片",
        "A keyframe-backed missing runtime leaf may be corrected only by a cited same-branch baseline and alternate current Figma leaf",
    )

    old_task_dir = agent_service.TASK_DIR
    old_gateway_available = agent_service._ai_gateway_available
    old_repair_skill = ai_skill_service.run_ai_skill
    old_log_tool_call = agent_service._log_tool_call
    old_upsert = repair_service.upsert_repair_draft
    old_report_keyframes = agent_service._agent_failure_report_keyframes
    old_repair_baselines = agent_service._agent_repair_baseline_examples
    correction_requests = []
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            agent_service.TASK_DIR = temp_dir
            module_dir = Path(temp_dir) / "AI_Agent_草稿"
            module_dir.mkdir()
            (module_dir / "case.yaml").write_text(original, encoding="utf-8")
            agent_service._ai_gateway_available = lambda: True

            def repair_with_bounded_correction(_skill, payload, **kwargs):
                correction_requests.append({"payload": payload, "imageCount": len(kwargs.get("image_assets") or [])})
                if len(correction_requests) == 1:
                    return {
                        "analysis": "先尝试横向探索，导航保持不变",
                        "changes": ["增加横向滚动"],
                        "patches": [{
                            "op": "insert_before",
                            "anchor": "aiAssert: 百度网盘入口可见",
                            "lines": [
                                "aiScroll: 本地导入、相册导入、微信导入所在的横向入口区域",
                                "scrollType: singleAction",
                                "direction: horizontal",
                                "distance: 350",
                            ],
                            "reason": "探索屏外入口",
                        }],
                        "usedBaselineIds": [],
                    }
                return {
                    "analysis": "根据照片分支基线真实补齐父子导航",
                    "changes": ["在原入口后新增照片打印和 5 寸照片两个可见文字点击"],
                    "patches": [{
                        "op": "insert_before",
                        "anchor": "aiTap: 文档打印",
                        "lines": ["aiWaitFor: 首页入口区域稳定显示"],
                        "reason": "新增导航前等待起始页稳定",
                    }, {
                        "op": "insert_after",
                        "anchor": "aiTap: 文档打印",
                        "lines": ["aiTap: 照片打印", "aiTap: 5寸照片"],
                        "reason": "按当前分支成功基线补齐父子路径",
                    }],
                    "usedBaselineIds": ["base-photo-path"],
                }

            ai_skill_service.run_ai_skill = repair_with_bounded_correction
            agent_service._log_tool_call = lambda *args, **kwargs: None
            agent_service._agent_failure_report_keyframes = lambda *_args, **_kwargs: [
                {"name": f"frame-{index}", "data": "image"}
                for index in range(3)
            ]
            agent_service._agent_repair_baseline_examples = lambda *_args, **_kwargs: [branch_baseline]
            repair_service.upsert_repair_draft = lambda draft: dict(draft)
            correction_run = {
                "runId": "agent-static-bounded-repair-correction",
                "target": "通用父子页面导航修复",
                "platform": "android",
                "runnerId": "win-runner-01",
                "deviceId": "ecbfd645",
                "deviceStrategy": "fixed",
                "artifacts": {
                    "failureAnalysis": {"failureType": "SCRIPT_ISSUE", "summary": "仍停留在父页面"},
                    "report": {"failedJobs": [{
                        "jobId": "job-static-bounded-correction",
                        "module": "AI_Agent_草稿",
                        "file": "case.yaml",
                        "taskName": "smoke",
                        "status": "failed",
                        "error": "failed to locate element",
                        "failureType": "SCRIPT_ISSUE",
                    }]},
                },
            }
            correction_call = agent_service._tool_generate_repair(correction_run)
        correction_summary = correction_run["artifacts"].get("repairSummary") or {}
        correction_item = (correction_summary.get("items") or [{}])[0]
        correction_draft = (correction_run["artifacts"].get("repairDrafts") or [{}])[0]
        require(
            len(correction_requests) == 2
            and correction_item.get("aiCorrectionAttempted") is True
            and correction_item.get("aiRequestCount") == 2
            and correction_requests[1]["payload"].get("candidateValidationIssues", [{}])[0].get("code") == "repair_patch_application_failed"
            and correction_requests[1]["payload"].get("correctionContext", {}).get("previousCandidate", {}).get("patches")
            and "horizontal" in json.dumps(correction_requests[1]["payload"]["correctionContext"], ensure_ascii=False)
            and correction_requests[1]["imageCount"] == 2
            and len(correction_requests[1]["payload"].get("allFailedJobs") or []) == 1,
            "An invalid first patch must receive exactly one compact correction with the rejected patch and exact platform issues",
        )
        require(
            correction_call.get("status") == "SUCCESS"
            and correction_draft.get("status") == "WAIT_CONFIRM"
            and correction_item.get("navigationChanged") is True
            and correction_item.get("usedBaselineIds") == ["base-photo-path"],
            "Only the corrected YAML with a real navigation diff and current-branch citation may become rerunnable",
        )
    finally:
        agent_service.TASK_DIR = old_task_dir
        agent_service._ai_gateway_available = old_gateway_available
        ai_skill_service.run_ai_skill = old_repair_skill
        agent_service._log_tool_call = old_log_tool_call
        repair_service.upsert_repair_draft = old_upsert
        agent_service._agent_failure_report_keyframes = old_report_keyframes
        agent_service._agent_repair_baseline_examples = old_repair_baselines

    eligible_script_fix = original.replace(
        "        - aiTap: 文档打印",
        "        - aiWaitFor: 首页入口区域稳定显示\n        - aiTap: 首页中名称为文档打印的入口",
    )
    old_task_dir = agent_service.TASK_DIR
    old_gateway_available = agent_service._ai_gateway_available
    old_repair_skill = ai_skill_service.run_ai_skill
    old_log_tool_call = agent_service._log_tool_call
    old_upsert = repair_service.upsert_repair_draft
    old_report_keyframes = agent_service._agent_failure_report_keyframes
    old_repair_baselines = agent_service._agent_repair_baseline_examples
    mixed_repair_requests = []
    empty_retry_requests = []
    repair_frame_limits = []
    repair_baseline_limits = []
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            agent_service.TASK_DIR = temp_dir
            module_dir = Path(temp_dir) / "AI_Agent_草稿"
            module_dir.mkdir()
            (module_dir / "empty.yaml").write_text(original, encoding="utf-8")
            (module_dir / "script.yaml").write_text(original, encoding="utf-8")
            (module_dir / "product.yaml").write_text(original, encoding="utf-8")
            agent_service._ai_gateway_available = lambda: True

            def executable_patch():
                return {
                    "analysis": "根据最新失败帧补充起始页稳定等待并修正可见文字定位",
                    "changes": ["在首次入口点击前增加首页稳定等待"],
                    "patches": [{
                        "op": "insert_before",
                        "anchor": "aiTap: 文档打印",
                        "lines": ["aiWaitFor: 首页入口区域稳定显示"],
                        "reason": "等待稳定起点",
                    }, {
                        "op": "replace_step",
                        "anchor": "aiTap: 文档打印",
                        "lines": ["aiTap: 首页中名称为文档打印的入口"],
                        "reason": "使用真实可见文字定位",
                    }],
                    "usedBaselineIds": [],
                }

            def empty_then_repaired_skill(_skill, payload, **kwargs):
                empty_retry_requests.append({"payload": payload, "imageCount": len(kwargs.get("image_assets") or [])})
                if len(empty_retry_requests) == 1:
                    raise TimeoutError("AI skill timeout after 88000ms")
                return executable_patch()

            ai_skill_service.run_ai_skill = empty_then_repaired_skill
            agent_service._log_tool_call = lambda *args, **kwargs: None
            agent_service._agent_failure_report_keyframes = lambda *_args, **kwargs: (
                repair_frame_limits.append(kwargs.get("limit"))
                or [{"name": f"frame-{index}", "data": "image"} for index in range(3)]
            )
            agent_service._agent_repair_baseline_examples = lambda *_args, **kwargs: (
                repair_baseline_limits.append(kwargs.get("limit")) or []
            )
            repair_service.upsert_repair_draft = lambda draft: dict(draft)
            empty_retry_run = {
                "runId": "agent-static-empty-repair-retry",
                "target": "通用入口展示",
                "platform": "android",
                "artifacts": {
                    "failureAnalysis": {"failureType": "SCRIPT_ISSUE", "canAutoRepair": True},
                    "report": {"failedJobs": [{
                        "jobId": "job-empty-retry",
                        "module": "AI_Agent_草稿",
                        "file": "empty.yaml",
                        "taskName": "入口定位失败",
                        "failureType": "SCRIPT_ISSUE",
                        "error": "failed to locate element",
                    }]},
                },
            }
            empty_retry_call = agent_service._tool_generate_repair(empty_retry_run)
            empty_retry_item = (
                (empty_retry_run.get("artifacts", {}).get("repairSummary") or {}).get("items") or [{}]
            )[0]

            def mixed_repair_skill(*_args, **_kwargs):
                mixed_repair_requests.append(True)
                return executable_patch()

            ai_skill_service.run_ai_skill = mixed_repair_skill
            agent_service._agent_failure_report_keyframes = lambda *_args, **_kwargs: []
            agent_service._agent_repair_baseline_examples = lambda *_args, **_kwargs: []
            mixed_run = {
                "runId": "agent-static-mixed-failure-repair",
                "target": "通用入口展示",
                "platform": "android",
                "artifacts": {
                    "failureAnalysis": {"failureType": "SCRIPT_ISSUE", "canAutoRepair": True},
                    "report": {"failedJobs": [{
                        "jobId": "job-script",
                        "module": "AI_Agent_草稿",
                        "file": "script.yaml",
                        "taskName": "脚本定位失败",
                        "failureType": "SCRIPT_ISSUE",
                        "error": "failed to locate element",
                    }, {
                        "jobId": "job-product",
                        "module": "AI_Agent_草稿",
                        "file": "product.yaml",
                        "taskName": "需求入口未展示",
                        "failureType": "PRODUCT_BUG",
                        "failureReview": {
                            "category": "product_bug",
                            "confidence": 0.95,
                            "canAutoRepair": False,
                            "reason": "运行截图确认需求入口不存在",
                        },
                    }]},
                },
            }
            mixed_call = agent_service._tool_generate_repair(mixed_run)
            mixed_drafts = {
                item.get("jobId"): item
                for item in mixed_run.get("artifacts", {}).get("repairDrafts") or []
            }
            malicious_product_run = {
                "runId": "agent-static-product-rerun-defense",
                "artifacts": {
                    "repairDrafts": [{
                        "draftId": "repair-product-invalid",
                        "jobId": "job-product",
                        "failureType": "PRODUCT_BUG",
                        "status": "WAIT_CONFIRM",
                        "fixedYaml": eligible_script_fix,
                    }],
                    "repairSummary": {"draftIds": ["repair-product-invalid"], "draftCount": 1},
                },
            }
            malicious_plan = agent_service._agent_prepare_repair_rerun_targets(
                malicious_product_run,
                mixed_run["artifacts"]["report"]["failedJobs"][1:],
                [{"job_id": "job-product", "module": "AI_Agent_草稿", "file": "product.yaml"}],
            )
        require(
            empty_retry_call.get("status") == "SUCCESS"
            and len(empty_retry_requests) == 2
            and empty_retry_item.get("aiCorrectionAttempted") is True
            and empty_retry_item.get("aiRequestCount") == 2
            and empty_retry_item.get("aiAttemptErrors", [{}])[0].get("errorType") == "timeout"
            and empty_retry_requests[1]["imageCount"] == 2
            and len(empty_retry_requests[1]["payload"].get("allFailedJobs") or []) == 1
            and empty_retry_requests[1]["payload"].get("correctionContext", {}).get("previousError")
            and repair_frame_limits == [3]
            and repair_baseline_limits == [3],
            "A timed-out patch plan must receive one compact evidence-bound retry and preserve the first error",
        )
        require(
            len(mixed_repair_requests) == 1
            and mixed_call.get("aiUsedCount") == 1
            and mixed_drafts.get("job-script", {}).get("status") == "WAIT_CONFIRM"
            and mixed_drafts.get("job-product", {}).get("status") == "REJECTED"
            and not mixed_drafts.get("job-product", {}).get("fixedYaml"),
            "Batch repair must call AI only for each eligible SCRIPT_ISSUE and retain PRODUCT_BUG as diagnosis-only evidence",
        )
        require(
            not malicious_plan.get("targets")
            and malicious_plan.get("skipped", [{}])[0].get("status") == "repair_not_eligible",
            "A persisted product-failure YAML draft must remain blocked at the final Runner rerun boundary",
        )
    finally:
        agent_service.TASK_DIR = old_task_dir
        agent_service._ai_gateway_available = old_gateway_available
        ai_skill_service.run_ai_skill = old_repair_skill
        agent_service._log_tool_call = old_log_tool_call
        repair_service.upsert_repair_draft = old_upsert
        agent_service._agent_failure_report_keyframes = old_report_keyframes
        agent_service._agent_repair_baseline_examples = old_repair_baselines

    invalid_ai_scroll_repair = """android:
  tasks:
    - name: smoke repair
      flow:
        - launch: com.xbxxhz.box
        - aiWaitFor: 首页文档打印入口可见
        - aiTap: 文档打印入口
        - aiWaitFor: 文档打印页导入入口列表可见
        - aiScroll:
            direction: right
            distance: 1
            scrollType: singleAction
        - aiAssert: 目标入口可见
"""
    old_task_dir = agent_service.TASK_DIR
    old_gateway_available = agent_service._ai_gateway_available
    old_repair_skill = ai_skill_service.run_ai_skill
    old_log_tool_call = agent_service._log_tool_call
    old_upsert = repair_service.upsert_repair_draft
    old_report_keyframes = agent_service._agent_failure_report_keyframes
    old_repair_baselines = agent_service._agent_repair_baseline_examples
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            agent_service.TASK_DIR = temp_dir
            module_dir = Path(temp_dir) / "AI_Agent_草稿"
            module_dir.mkdir()
            (module_dir / "case.yaml").write_text(original, encoding="utf-8")
            agent_service._ai_gateway_available = lambda: True
            ai_skill_service.run_ai_skill = lambda *args, **kwargs: {
                "analysis": "在入口列表中横向滑动",
                "changes": ["在入口列表中横向滑动"],
                "patches": [{
                    "op": "insert_before",
                    "anchor": "aiAssert: 百度网盘入口可见",
                    "lines": [
                        "aiScroll: 文档打印页导入入口列表",
                        "direction: horizontal",
                        "distance: 1",
                        "scrollType: singleAction",
                    ],
                    "reason": "尝试探索入口",
                }],
                "usedBaselineIds": [],
            }
            agent_service._log_tool_call = lambda *args, **kwargs: None
            agent_service._agent_failure_report_keyframes = lambda *_args, **_kwargs: []
            agent_service._agent_repair_baseline_examples = lambda *_args, **_kwargs: []
            repair_service.upsert_repair_draft = lambda draft: dict(draft)
            gateway_invalid_run = {
                "runId": "agent-static-gateway-invalid-repair",
                "target": "通用入口横向列表验收",
                "platform": "android",
                "artifacts": {
                    "failureAnalysis": {"failureType": "SCRIPT_ISSUE", "summary": "目标入口不可见"},
                    "report": {
                        "failedJobs": [{
                            "jobId": "job-static-invalid-scroll",
                            "module": "AI_Agent_草稿",
                            "file": "case.yaml",
                            "taskName": "smoke",
                            "status": "failed",
                            "error": "failed to locate element",
                            "failureType": "SCRIPT_ISSUE",
                        }],
                    },
                },
            }
            gateway_invalid_call = agent_service._tool_generate_repair(gateway_invalid_run)
            gateway_invalid_draft = (gateway_invalid_run["artifacts"].get("repairDrafts") or [{}])[0]
            gateway_invalid_summary = gateway_invalid_run["artifacts"].get("repairSummary") or {}
            require(
                gateway_invalid_call.get("status") == "SKIPPED" and not gateway_invalid_call.get("aiUsed"),
                "An invalid AI patch must never be reported as usable",
            )
            require(
                gateway_invalid_draft.get("status") == "REJECTED"
                and not gateway_invalid_draft.get("fixedYaml")
                and gateway_invalid_draft.get("patchPlan", {}).get("patches")
                and "aiScroll.direction" in str(gateway_invalid_draft.get("aiError") or ""),
                "A structurally invalid patch result must be retained as rejected evidence and removed from the Runner payload surface",
            )
            require(
                gateway_invalid_summary.get("validationPassedCount") == 0
                and gateway_invalid_summary.get("blockedCount") == 1
                and gateway_invalid_summary.get("items", [{}])[0].get("blockedReason") == "repair_patch_application_failed",
                "Repair summary must expose the early Task Server patch rejection instead of claiming validation passed",
            )

            legacy_invalid_draft = {
                "draftId": "repair-legacy-invalid",
                "jobId": "job-static-invalid-scroll",
                "module": "AI_Agent_草稿",
                "file": "case.yaml",
                "taskName": "smoke",
                "fixedYaml": invalid_ai_scroll_repair,
                "aiGatewayValidation": {"success": True, "valid": False, "errors": ["invalid aiScroll"]},
            }
            legacy_run = {
                "runId": "agent-static-legacy-invalid-repair",
                "artifacts": {
                    "repairDrafts": [legacy_invalid_draft],
                    "repairSummary": {"draftCount": 1, "draftIds": ["repair-legacy-invalid"]},
                },
            }
            legacy_plan = agent_service._agent_prepare_repair_rerun_targets(
                legacy_run,
                [{"jobId": "job-static-invalid-scroll", "module": "AI_Agent_草稿", "file": "case.yaml", "taskName": "smoke"}],
                [{"job_id": "job-static-invalid-scroll", "module": "AI_Agent_草稿", "file": "case.yaml", "target_task_name": "smoke"}],
            )
            require(
                not legacy_plan.get("targets")
                and legacy_plan.get("skipped", [{}])[0].get("status") == "ai_gateway_invalid",
                "Persisted invalid repairs from an older server version must still be blocked before any Runner rerun",
            )

            latest_draft = {"draftId": "repair-latest", "jobId": "job-latest", "fixedYaml": semantic_fix}
            old_draft = {"draftId": "repair-old", "jobId": "job-old", "fixedYaml": semantic_fix}
            latest_only = agent_service._agent_repair_drafts_for_rerun({
                "repairDrafts": [latest_draft, old_draft],
                "repairDraft": old_draft,
                "repairSummary": {"draftIds": ["repair-latest"], "draftCount": 1},
            })
            require(
                [item.get("draftId") for item in latest_only] == ["repair-latest"],
                "A new AI repair cycle must not silently rerun stale drafts from an earlier failed attempt",
            )
    finally:
        agent_service.TASK_DIR = old_task_dir
        agent_service._ai_gateway_available = old_gateway_available
        ai_skill_service.run_ai_skill = old_repair_skill
        agent_service._log_tool_call = old_log_tool_call
        repair_service.upsert_repair_draft = old_upsert
        agent_service._agent_failure_report_keyframes = old_report_keyframes
        agent_service._agent_repair_baseline_examples = old_repair_baselines

    from task_server.services import job_service
    old_load_jobs = job_service.load_jobs
    old_create_pending_job = job_service.create_pending_job
    old_wait_jobs_finished = job_service.wait_jobs_finished
    old_persist_snapshot = agent_service._persist_agent_run_snapshot
    old_log_rerun = agent_service._log_tool_call
    old_post_rerun = agent_service._agent_post_rerun_autonomy
    old_prepare_rerun_targets = agent_service._agent_prepare_repair_rerun_targets
    snapshots = []
    created_counter = {"value": 0}
    try:
        job_service.load_jobs = lambda: [
            {"job_id": "job-source-1", "status": "failed", "module": "AI测试", "file": "one.yaml", "target_task_name": "文档入口", "runner_id": "win-runner-01", "target_runner_id": "win-runner-01", "device_id": "ecbfd645", "device_strategy": "fixed", "attempt": 1},
            {"job_id": "job-source-2", "status": "failed", "module": "AI测试", "file": "two.yaml", "target_task_name": "照片入口", "runner_id": "win-runner-01", "target_runner_id": "win-runner-01", "device_id": "ecbfd645", "device_strategy": "fixed", "attempt": 1},
        ]

        def fake_create_pending_job(*args, **kwargs):
            created_counter["value"] += 1
            return {"job_id": f"job-rerun-{created_counter['value']}", "created_at": "2026-07-14T12:00:00"}

        def fake_wait_jobs_finished(job_ids, run, **kwargs):
            job_id = job_ids[0]
            entry = {"job_id": job_id, "runner_id": "win-runner-01", "device_id": "ecbfd645", "report_url": f"/reports/{job_id}.html"}
            if job_id.endswith("1"):
                return {"completed": [{**entry, "status": "success"}], "failed": [], "running": [], "timeout": []}
            return {"completed": [], "failed": [{**entry, "status": "failed", "error": "目标页面不匹配"}], "running": [], "timeout": []}

        job_service.create_pending_job = fake_create_pending_job
        job_service.wait_jobs_finished = fake_wait_jobs_finished
        agent_service._persist_agent_run_snapshot = lambda run: snapshots.append(json.loads(json.dumps(run.get("artifacts", {}).get("rerunProgress") or {}, ensure_ascii=False)))
        agent_service._log_tool_call = lambda *args, **kwargs: None
        agent_service._agent_post_rerun_autonomy = lambda *args, **kwargs: {"analyzed": True, "repairGenerated": False, "reason": "static check"}
        rerun_run = {
            "runId": "agent-static-rerun-progress",
            "target": "入口回归",
            "runnerId": "win-runner-01",
            "deviceId": "ecbfd645",
            "deviceStrategy": "fixed",
            "artifacts": {},
        }
        rerun_call = agent_service._tool_rerun(rerun_run, failed_items_override=[
            {"jobId": "job-source-1", "status": "failed", "module": "AI测试", "file": "one.yaml", "taskName": "文档入口", "failureType": "SCRIPT_ISSUE", "failureReason": "原文档入口失败"},
            {"jobId": "job-source-2", "status": "failed", "module": "AI测试", "file": "two.yaml", "taskName": "照片入口", "failureType": "SCRIPT_ISSUE", "failureReason": "原照片入口失败"},
        ])
        rerun_progress = rerun_run["artifacts"].get("rerunProgress") or {}
        require(rerun_call.get("status") == "PARTIAL_FAILED" and rerun_progress.get("successCount") == 1 and rerun_progress.get("failedCount") == 1, "Serial fixed-device rerun progress must retain both earlier success and later failure")
        require([item.get("status") for item in rerun_progress.get("items") or []] == ["success", "failed"], "Task-level rerun items must preserve terminal status in source order")
        require(all(item.get("runnerId") == "win-runner-01" and item.get("deviceId") == "ecbfd645" for item in rerun_progress.get("items") or []), "Every rerun progress item must expose the actual fixed Runner/device")
        require(any(snapshot.get("successCount") == 1 and snapshot.get("runningCount") == 1 for snapshot in snapshots), "Cumulative rerun snapshots must retain completed successes while the next serial task is running")

        created_counter["value"] = 0
        mixed_creates = []
        script_source_job = {
            "job_id": "job-script", "status": "failed", "module": "AI测试", "file": "scan.yaml",
            "target_task_name": "扫描入口", "runner_id": "win-runner-01", "target_runner_id": "win-runner-01",
            "device_id": "ecbfd645", "device_strategy": "fixed", "attempt": 1,
        }
        env_source_job = {
            "job_id": "job-env", "status": "failed", "module": "AI测试", "file": "photo.yaml",
            "target_task_name": "照片入口", "runner_id": "win-runner-01", "target_runner_id": "win-runner-01",
            "device_id": "ecbfd645", "device_strategy": "fixed", "attempt": 1,
        }
        product_source_job = {
            "job_id": "job-product", "status": "failed", "module": "AI测试", "file": "product.yaml",
            "target_task_name": "产品入口", "runner_id": "win-runner-01", "target_runner_id": "win-runner-01",
            "device_id": "ecbfd645", "device_strategy": "fixed", "attempt": 1,
        }
        job_service.load_jobs = lambda: [script_source_job, env_source_job, product_source_job]

        def fake_mixed_create(module, file_name, **kwargs):
            created_counter["value"] += 1
            mixed_creates.append({"module": module, "file": file_name, **kwargs})
            return {"job_id": f"job-mixed-{created_counter['value']}", "created_at": "2026-07-17T12:00:00"}

        def fake_mixed_wait(job_ids, run, **kwargs):
            job_id = job_ids[0]
            return {
                "completed": [{
                    "job_id": job_id,
                    "status": "success",
                    "runner_id": "win-runner-01",
                    "device_id": "ecbfd645",
                    "report_url": f"/reports/{job_id}.html",
                }],
                "failed": [], "running": [], "timeout": [],
            }

        agent_service._agent_prepare_repair_rerun_targets = lambda *_args, **_kwargs: {
            "hasRepairDrafts": True,
            "draftCount": 1,
            "targets": [{
                "draftId": "repair-scan",
                "sourceJobId": "job-script",
                "sourceModule": "AI测试",
                "sourceFile": "scan.yaml",
                "sourceTaskName": "扫描入口",
                "module": "AI_Agent_修复草稿.test",
                "file": "scan-repair.yaml",
                "path": "/tmp/scan-repair.yaml",
                "taskNames": ["扫描入口"],
                "sourceItem": {},
                "sourceJob": script_source_job,
                "failureReason": "入口行右侧被截断",
            }],
            "skipped": [{
                "jobId": "job-product",
                "taskName": "产品入口",
                "status": "repair_not_eligible",
                "failureType": "PRODUCT_BUG",
                "reason": "产品失败只保留诊断证据",
            }],
        }
        job_service.create_pending_job = fake_mixed_create
        job_service.wait_jobs_finished = fake_mixed_wait
        mixed_rerun_run = {
            "runId": "agent-static-mixed-rerun",
            "target": "入口回归",
            "runnerId": "win-runner-01",
            "deviceId": "ecbfd645",
            "deviceStrategy": "fixed",
            "artifacts": {},
        }
        mixed_rerun_call = agent_service._tool_rerun(mixed_rerun_run, failed_items_override=[{
            "jobId": "job-script", "status": "failed", "module": "AI测试", "file": "scan.yaml",
            "taskName": "扫描入口", "failureType": "SCRIPT_ISSUE",
            "failureReview": {"category": "script_issue", "confidence": 0.96, "canAutoRepair": True},
        }, {
            "jobId": "job-env", "status": "failed", "module": "AI测试", "file": "photo.yaml",
            "taskName": "照片入口", "failureType": "ENV_ISSUE", "error": "model request was aborted",
        }, {
            "jobId": "job-product", "status": "failed", "module": "AI测试", "file": "product.yaml",
            "taskName": "产品入口", "failureType": "PRODUCT_BUG",
            "failureReview": {"category": "product_bug", "confidence": 0.96, "canAutoRepair": False},
        }])
        mixed_progress = mixed_rerun_run.get("artifacts", {}).get("rerunProgress") or {}
        require(
            mixed_rerun_call.get("rerunSource") == "mixed"
            and mixed_rerun_call.get("targetCount") == 2
            and len(mixed_creates) == 2
            and {item.get("parent_job_id") for item in mixed_creates} == {"job-script", "job-env"}
            and all(item.get("device_id") == "ecbfd645" for item in mixed_creates)
            and not any(item.get("parent_job_id") == "job-product" for item in mixed_creates),
            "Mixed failures must execute the AI repair and one evidence-backed environment retry on the same fixed device without rerunning product failures",
        )
        require(
            mixed_progress.get("successCount") == 2
            and mixed_progress.get("skippedCount") == 1
            and mixed_progress.get("originalRetryCount") == 1,
            "Mixed rerun progress must preserve two real recoveries and one diagnosis-only product result",
        )
    finally:
        job_service.load_jobs = old_load_jobs
        job_service.create_pending_job = old_create_pending_job
        job_service.wait_jobs_finished = old_wait_jobs_finished
        agent_service._persist_agent_run_snapshot = old_persist_snapshot
        agent_service._log_tool_call = old_log_rerun
        agent_service._agent_post_rerun_autonomy = old_post_rerun
        agent_service._agent_prepare_repair_rerun_targets = old_prepare_rerun_targets

    old_load_jobs = job_service.load_jobs
    old_runner_material = agent_service._agent_runner_job_material
    old_log_report = agent_service._log_tool_call
    try:
        job_service.load_jobs = lambda: [{
            "job_id": "job-report-pass",
            "status": "success",
            "module": "AI测试",
            "file": "pass.yaml",
            "target_task_name": "文档入口",
            "report_url": "/reports/job-report-pass.html",
        }, {
            "job_id": "job-report-fail",
            "status": "failed",
            "module": "AI测试",
            "file": "fail.yaml",
            "target_task_name": "扫描入口",
            "report_url": "/reports/job-report-fail.html",
            "error": "等待百度网盘入口超时",
        }]
        agent_service._agent_runner_job_material = lambda _job_id: {}
        agent_service._log_tool_call = lambda *_args, **_kwargs: None
        report_run = {
            "runId": "agent-static-terminal-reports",
            "executionMode": "RUNNER_JOB",
            "artifacts": {"jobIds": ["job-report-pass", "job-report-fail"]},
        }
        report_call = agent_service._tool_collect_report(report_run)
        terminal_reports = report_run.get("artifacts", {}).get("report", {}).get("executionReports") or []
        terminal_refs = report_run.get("artifacts", {}).get("report", {}).get("yamlExecutionRefs") or []
        require(
            report_call.get("status") == "PARTIAL_FAILED"
            and {item.get("status") for item in terminal_reports} == {"success", "failed"}
            and len(terminal_reports) == 2
            and len(terminal_refs) == 2,
            "Report collection must retain both passed and failed terminal HTML reports instead of hiding failure evidence",
        )
    finally:
        job_service.load_jobs = old_load_jobs
        agent_service._agent_runner_job_material = old_runner_material
        agent_service._log_tool_call = old_log_report

    old_create_refs = agent_service._agent_create_runner_jobs_for_refs
    old_runner_dry_support = agent_service._runner_supports_yaml_dry_run
    old_persist_snapshot = agent_service._persist_agent_run_snapshot
    recovered_dispatches = []
    try:
        def fake_create_recovered_refs(run, refs, runner_id, device_id, device_strategy, **kwargs):
            recovered_dispatches.append({
                "refs": list(refs),
                "runnerId": runner_id,
                "deviceId": device_id,
                "deviceStrategy": device_strategy,
                "phase": kwargs.get("phase"),
            })
            jobs = [
                {
                    "job_id": f"job-expanded-{index}",
                    "status": "success",
                    "runner_id": runner_id,
                    "device_id": device_id,
                }
                for index, _ref in enumerate(refs, start=1)
            ]
            return {
                "jobIds": [item["job_id"] for item in jobs],
                "dryRunResults": [{"ok": True, "phase": kwargs.get("phase")} for _ref in refs],
                "dryRunBlocked": [],
                "runnerDryRunJobs": [],
                "formalWaitResult": {"completed": jobs, "failed": [], "timeout": []},
            }

        agent_service._agent_create_runner_jobs_for_refs = fake_create_recovered_refs
        agent_service._runner_supports_yaml_dry_run = lambda _runner_id: (False, "static mock")
        agent_service._persist_agent_run_snapshot = lambda _run: None
        recovered_expand_run = {
            "runId": "agent-static-recovered-expand",
            "runnerId": "win-runner-01",
            "deviceId": "ecbfd645",
            "deviceStrategy": "fixed",
            "artifacts": {
                "jobIds": ["job-smoke-failed"],
                "jobResult": {
                    "completed": [],
                    "failed": [{"job_id": "job-smoke-failed", "status": "failed"}],
                    "timeout": [],
                    "phases": {"smoke": {"jobIds": ["job-smoke-failed"]}},
                },
                "runnerExecutionGate": {
                    "enabled": True,
                    "stopFurtherExecution": True,
                    "deferred": [
                        {"module": "AI测试", "file": "photo.yaml", "path": "/tmp/photo.yaml"},
                        {"module": "AI测试", "file": "scan.yaml", "path": "/tmp/scan.yaml"},
                    ],
                },
            },
        }
        recovered_expand = agent_service._agent_resume_deferred_after_recovery(recovered_expand_run)
        recovered_gate = recovered_expand_run["artifacts"].get("runnerExecutionGate") or {}
        require(
            recovered_expand.get("status") == "SUCCESS"
            and len(recovered_expand.get("createdJobIds") or []) == 2
            and recovered_gate.get("remainingDeferredCount") == 0
            and recovered_gate.get("stopFurtherExecution") is False,
            "A successful smoke repair must reopen and finish every deferred executable ref instead of ending the Agent at the repaired smoke job",
        )
        require(
            len(recovered_dispatches) == 1
            and recovered_dispatches[0].get("runnerId") == "win-runner-01"
            and recovered_dispatches[0].get("deviceId") == "ecbfd645"
            and recovered_dispatches[0].get("deviceStrategy") == "fixed"
            and recovered_expand_run["artifacts"]["jobResult"].get("completedCount") == 2
            and recovered_expand_run["artifacts"]["jobResult"].get("failedCount") == 1,
            "Recovered expansion must preserve the selected fixed device and retain the original failed attempt in the raw result ledger",
        )
    finally:
        agent_service._agent_create_runner_jobs_for_refs = old_create_refs
        agent_service._runner_supports_yaml_dry_run = old_runner_dry_support
        agent_service._persist_agent_run_snapshot = old_persist_snapshot


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
    partial_visual_report = agent_service._agent_visual_reference_report(
        visual_run,
        {
            "summary": {"ui_design_assets": [{"name": "only-returned-image.png"}]},
            "review": {"yaml_visual_batches": {"enabled": True, "completed_batches": 0, "errors": ["timeout"]}},
        },
    )
    require(
        partial_visual_report.get("figmaImageCount") == 4,
        "Visual report must preserve source Figma image count when a failed visual batch returns only partial assets",
    )
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

    requirement_text = "基础打印的入口在首页：文档打印、照片打印、扫描复印。百度网盘入口是新增能力，需覆盖展示、同级关系、文案及可达页面。"
    start_payload = {
        "target": "基础打印新增百度网盘入口",
        "requirement": requirement_text,
        "sourceType": "figma",
        "sourceRefs": {"figmaUrl": "https://www.figma.com/design/static/new-feature"},
        "scope": "regression",
        "executionMode": "RUNNER_JOB",
        "runnerId": "win-runner-01",
        "deviceId": "ecbfd645",
        "deviceStrategy": "fixed",
    }
    normalized = agent_service.normalize_agent_input(start_payload)
    run_with_requirement_alias = {
        "target": start_payload["target"],
        "normalizedInput": normalized,
        "artifacts": {},
    }
    require(
        normalized.get("requirementText") == requirement_text,
        "Agent start must preserve the requirement alias as authoritative requirementText",
    )
    require(
        agent_service._agent_plan_requirement_text(run_with_requirement_alias) == requirement_text,
        "Agent PLAN must receive the full requirement text instead of falling back to the short target",
    )
    require(
        agent_service._agent_source_material_context(run_with_requirement_alias).get("requirementText") == requirement_text,
        "Agent PREPARE_SOURCE must carry the full requirement alias into source material",
    )

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

            high_replan_yaml = """android:
  tasks:
    - name: 文档打印页百度网盘入口可见性与相对位置校验
      flow:
        - launch: com.xbxxhz.box
        - aiWaitFor: 小白学习首页加载完成，文档打印入口可见
        - runAdbShell: am force-stop com.xbxxhz.box
        - sleep: 1000
        - launch: com.xbxxhz.box
        - aiWaitFor: 小白学习首页加载完成，文档打印入口可见
        - aiTap: 文档打印入口
        - aiWaitFor: 文档打印页面加载完成
        - sleep: 500
        - aiWaitFor: 本地文档入口可见
        - aiScroll: 在横向入口列表向左滑动一次
        - aiWaitFor: 百度网盘入口可见
        - aiAssert: 百度网盘入口位于本地文档右侧且同级展示
"""
            high_replan_path = module_dir / "high-replan.yaml"
            high_replan_path.write_text(high_replan_yaml, encoding="utf-8")
            high_replan_run = {"target": "普通新需求", "scope": "regression", "module": "AI_Agent_草稿", "artifacts": {}}
            high_replan_refs, high_replan_err = agent_service._confirm_agent_yaml_files(high_replan_run, high_replan_run["artifacts"], [{
                "module": "AI_Agent_草稿",
                "file": high_replan_path.name,
                "path": str(high_replan_path),
                "executionLevel": "executable",
                "score": 89,
                "scopeReview": {"ok": True, "reasons": [], "matchedRequirementIds": ["REQ-001"]},
            }])
            require(not high_replan_err and [ref.get("file") for ref in high_replan_refs] == ["high-replan.yaml"], "Confirmation must preserve generated executable classification for high-replan display YAML that already passed scope review")

            blocked_run = {"target": "普通新需求", "scope": "regression", "module": "AI_Agent_草稿", "artifacts": {}}
            blocked_refs, blocked_err = agent_service._confirm_agent_yaml_files(blocked_run, blocked_run["artifacts"], [{
                "module": "AI_Agent_草稿",
                "file": review_path.name,
                "path": str(review_path),
                "executionLevel": "needs_review",
                "scopeReview": {"ok": False, "reasons": ["需求范围待确认"]},
            }])
            require(not blocked_refs and "完整回归生成结果未达到" in blocked_err, "Regression must not continue with only a synthetic smoke when all requirement YAML needs review")

            from task_server.services import job_service
            original_create_job = job_service.create_job
            original_wait_jobs_finished = job_service.wait_jobs_finished
            created_payloads = []
            try:
                job_service.create_job = lambda payload: created_payloads.append(payload) or {"job_id": f"job-static-{len(created_payloads)}"}
                job_service.wait_jobs_finished = lambda *args, **kwargs: {"completed": [], "failed": [], "running": [], "timeout": [{"job_id": "job-static-1", "status": "timeout"}]}
                created = agent_service._agent_create_runner_jobs_for_refs(
                    {"runId": "agent-static", "platform": "android", "appPackage": "com.xbxxhz.box"},
                    [{"module": "AI_Agent_草稿", "file": executable_path.name, "path": str(executable_path)}],
                    "win-runner-01",
                    "ecbfd645",
                    "fixed",
                    runner_dry_run_enabled=True,
                    dry_run_timeout=1,
                    phase="smoke",
                )
            finally:
                job_service.create_job = original_create_job
                job_service.wait_jobs_finished = original_wait_jobs_finished
            require(
                len(created.get("dryRunBlocked") or []) == 1
                and not created.get("jobIds")
                and len(created.get("dryRunResults") or []) == 1
                and created["dryRunResults"][0].get("ok") is False
                and (created["dryRunResults"][0].get("runnerDryRun") or {}).get("inconclusive") is True,
                "An inconclusive Runner dry-run must explicitly block formal dispatch instead of disappearing from execution totals",
            )

            ordered_payloads = []
            waited_batches = []
            try:
                def fake_create_ordered_job(payload):
                    ordered_payloads.append(dict(payload))
                    prefix = "dry" if payload.get("dry_run") else "formal"
                    count = sum(1 for item in ordered_payloads if ("dry" if item.get("dry_run") else "formal") == prefix)
                    return {"job_id": f"job-{prefix}-{count}"}

                def fake_wait_ordered(job_ids, _run, **_kwargs):
                    waited_batches.append(list(job_ids))
                    return {
                        "completed": [{"job_id": job_id, "status": "success"} for job_id in job_ids],
                        "failed": [],
                        "running": [],
                        "timeout": [],
                    }

                job_service.create_job = fake_create_ordered_job
                job_service.wait_jobs_finished = fake_wait_ordered
                ordered = agent_service._agent_create_runner_jobs_for_refs(
                    {"runId": "agent-static-serial", "platform": "android", "appPackage": "com.xbxxhz.box"},
                    [
                        {"module": "AI_Agent_草稿", "file": executable_path.name, "path": str(executable_path)},
                        {"module": "AI_Agent_草稿", "file": review_path.name, "path": str(review_path)},
                    ],
                    "win-runner-01",
                    "ecbfd645",
                    "fixed",
                    runner_dry_run_enabled=True,
                    dry_run_timeout=10,
                    phase="smoke",
                )
            finally:
                job_service.create_job = original_create_job
                job_service.wait_jobs_finished = original_wait_jobs_finished
            require(
                ["dry" if item.get("dry_run") else "formal" for item in ordered_payloads] == ["dry", "dry", "formal", "formal"],
                "All Runner dry-runs must finish before the first formal fixed-device UI job is created",
            )
            require(
                waited_batches == [["job-dry-1", "job-dry-2"], ["job-formal-1"], ["job-formal-2"]]
                and ordered.get("jobIds") == ["job-formal-1", "job-formal-2"]
                and ordered.get("serialSameDevice") is True,
                "A fixed phone must receive one formal job at a time and each job must reach terminal state before the next is created",
            )
            require(
                len((ordered.get("formalWaitResult") or {}).get("completed") or []) == 2
                and not ordered.get("dryRunBlocked"),
                "Serial formal waits must be returned for truthful aggregate reporting without losing either completion",
            )
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
    bounded_wait_review = yaml_service.generated_case_requirement_scope_review({
        "case_id": "TC-BOUND-WAIT",
        "title": "扫描复印页点击百度网盘入口唤起响应校验",
        "coverage": "REQ-003",
        "requirementRefs": ["REQ-003 扫描复印：点击百度网盘入口并校验目标页面稳定可达"],
        "steps": [
            "点击扫描复印入口",
            "点击百度网盘入口",
            "等待百度网盘授权页、文件列表页或H5登录页加载，超时15秒",
        ],
        "assertions": ["百度网盘相关页面稳定可达，无崩溃、无白屏"],
    }, {
        "requirement_points": [
            "REQ-003 扫描复印：点击百度网盘入口并校验目标页面稳定可达",
        ],
    })
    require(
        bounded_wait_review.get("ok"),
        "A numeric deadline on a normal wait must remain execution mechanics, not become an unrequested timeout scenario",
    )
    related_page_reachability_check = {
        "id": "REQ-003-CHECK-04",
        "requirementId": "REQ-003",
        "branch": "扫描复印",
        "kind": "reachability",
        "text": "点击百度网盘入口并校验目标页面稳定可达",
    }
    related_page_case = {
        "case_id": "TC-RELATED-PAGE",
        "coverage": "REQ-003 扫描复印：点击百度网盘入口并校验目标页面稳定可达",
        "requirementRefs": ["REQ-003 扫描复印：点击百度网盘入口并校验目标页面稳定可达"],
        "steps": [
            "等待 App 首页加载完成",
            "点击「扫描复印」入口",
            "点击「证件扫描」",
            "点击「立即使用」",
            "点击「百度网盘」入口",
            "等待跳转至百度网盘相关页面",
        ],
    }
    require(
        ai_skill_service.case_covers_requirement_acceptance(related_page_case, related_page_reachability_check),
        "A bounded first-screen wait for the target cloud-disk related page after the target tap must satisfy reachability",
    )
    negative_related_page_case = {
        **related_page_case,
        "steps": [
            "点击「扫描复印」入口",
            "点击「百度网盘」入口",
            "未跳转至百度网盘相关页面",
        ],
    }
    require(
        not ai_skill_service.case_covers_requirement_acceptance(negative_related_page_case, related_page_reachability_check),
        "A negative related-page observation must not satisfy reachability",
    )
    scan_relation_check = {
        "id": "REQ-003-CHECK-02",
        "requirementId": "REQ-003",
        "branch": "扫描复印",
        "kind": "relation",
        "text": "校验百度网盘入口与当前页面同级入口的层级和位置关系",
    }
    scan_landing_case_with_full_refs = {
        "case_id": "TC-SCAN-LANDING",
        "title": "扫描复印百度网盘点击后首个可见页校验",
        "coverage": "REQ-003 扫描复印：点击百度网盘入口并校验目标页面稳定可达",
        "requirementRefs": [
            "REQ-003 扫描复印：校验百度网盘入口可见；"
            "校验百度网盘入口与当前页面同级入口的层级和位置关系；"
            "校验百度网盘入口使用需求约定的可见文案；"
            "点击百度网盘入口并校验目标页面稳定可达"
        ],
        "steps": [
            "被测 App 首页已加载完成，首页核心功能入口可见",
            "点击「扫描复印」入口",
            "等待扫描复印页面加载完成",
            "等待并校验「百度网盘」入口可见且文案为“百度网盘”",
            "点击「百度网盘」入口",
            "等待页面跳转或授权弹窗出现",
        ],
        "assertions": [
            "点击百度网盘入口后进入百度网盘相关页面或出现可识别提示，未白屏、未闪退、未停留在原入口页"
        ],
    }
    require(
        ai_skill_service._case_intends_requirement_acceptance(scan_landing_case_with_full_refs, scan_relation_check),
        "A same-branch executable landing case whose requirementRefs include the relation obligation must be eligible for relation repair",
    )
    require(
        not ai_skill_service.case_covers_requirement_acceptance(scan_landing_case_with_full_refs, scan_relation_check),
        "Intent to repair a relation obligation must not itself satisfy relation coverage without a concrete same-level assertion",
    )
    scan_landing_case_with_relation_assertion = {
        **scan_landing_case_with_full_refs,
        "steps": scan_landing_case_with_full_refs["steps"][:4] + [
            "等待并校验「百度网盘」入口可见、文案准确，且与扫描复印页面同级入口并列展示",
        ] + scan_landing_case_with_full_refs["steps"][4:],
    }
    require(
        ai_skill_service.case_covers_requirement_acceptance(scan_landing_case_with_relation_assertion, scan_relation_check),
        "The repaired scan branch case must satisfy relation only after adding a concrete same-level assertion",
    )
    scan_relation_payload = {
        "analysis": {
            "requirement_points": [
                scan_landing_case_with_full_refs["requirementRefs"][0],
            ],
            "requirement_acceptance_checks": [scan_relation_check],
        },
        "cases": [{
            **scan_landing_case_with_full_refs,
            "executionLevel": "executable",
            "ai_case_plan": {
                "baselineGrounded": True,
                "pathPlanApplied": True,
                "baselineId": "scan-base",
                "flow": scan_landing_case_with_full_refs["steps"],
            },
        }],
        "manual_cases": [],
    }
    scan_relation_audit = ai_skill_service.executable_yaml_portfolio_audit(
        scan_relation_payload,
        {"min_automation_cases": 1},
    )
    scan_relation_records = [{
        "raw": scan_relation_payload["cases"][0],
        "compact": ai_skill_service._compact_case_for_plan(
            scan_relation_payload["cases"][0],
            0,
            origin_level="automatic",
        ),
        "origin": "automatic",
    }]
    scan_focused_auto, _, _, scan_focus = ai_skill_service._focus_executable_convergence_candidates(
        scan_relation_payload,
        scan_relation_records,
        [],
        {"pass": "coverage_convergence", "portfolioAudit": scan_relation_audit},
    )
    scan_focused_by_id = {item.get("case_id"): item for item in scan_focused_auto}
    require(
        scan_focus.get("repairableExecutableCandidateIds") == ["TC-SCAN-LANDING"]
        and [
            item.get("id")
            for item in scan_focused_by_id["TC-SCAN-LANDING"].get("repairAcceptanceChecks") or []
        ] == ["REQ-003-CHECK-02"],
        "A scan branch landing executable with full requirement refs must be focused for relation repair instead of being preserved as complete",
    )
    timeout_scenario_review = yaml_service.generated_case_requirement_scope_review({
        "case_id": "TC-TIMEOUT-SCENARIO",
        "title": "扫描复印页百度网盘网络超时处理校验",
        "coverage": "REQ-003",
        "steps": ["断开网络", "点击百度网盘入口", "等待网络超时提示"],
        "assertions": ["页面展示网络超时提示并允许重试"],
    }, {
        "requirement_points": [
            "REQ-003 扫描复印：点击百度网盘入口并校验目标页面稳定可达",
        ],
    })
    require(
        not timeout_scenario_review.get("ok")
        and any("扩展场景：超时" in reason for reason in timeout_scenario_review.get("reasons") or []),
        "A real timeout-behavior scenario must remain blocked when the requirement does not request it",
    )
    grounded_yaml = """android:
  tasks:
    - name: 扫描复印页点击入口唤起响应校验
      flow:
        - launch: com.xbxxhz.box
        - aiWaitFor: 首页展示扫描复印入口
        - aiTap: 扫描复印
        - sleep: 300
        - aiTap: 证件扫描
        - sleep: 300
        - aiTap: 立即使用
        - sleep: 300
        - aiWaitFor: 扫描复印页面加载完成
        - aiTap: 百度网盘
        - sleep: 300
        - aiWaitFor: 授权页、文件列表页或登录页任一稳定显示
        - aiAssert: 页面无崩溃、无白屏
"""
    verified_case = {
        "ai_case_plan": {
            "baselineId": "verified-scan-baseline",
            "baselineGrounded": True,
            "baselineVerified": True,
            "pathPlanApplied": True,
        },
    }
    grounded_yaml, evidence_attached = yaml_service.attach_verified_baseline_evidence(
        grounded_yaml,
        verified_case,
    )
    grounded_score = yaml_service.score_midscene_yaml_executable(grounded_yaml, generated=True)
    unverified_yaml, unverified_attached = yaml_service.attach_verified_baseline_evidence(
        grounded_yaml,
        {"ai_case_plan": {**verified_case["ai_case_plan"], "baselineVerified": False}},
    )
    require(
        evidence_attached
        and grounded_score.get("baselineEvidence") is True
        and grounded_score.get("executionLevel") == "executable"
        and not unverified_attached
        and "matched baseline" not in unverified_yaml,
        "Only a server-verified successful baseline path may survive YAML repair as scorer evidence",
    )

    multi_point_analysis = {
        "requirement_points": [
            "REQ-001 文档打印页面展示百度网盘入口",
            "REQ-002 照片打印导入方式展示百度网盘入口",
            "REQ-003 扫描复印页面展示百度网盘入口",
            "REQ-004 宽屏端展示三个基础打印入口",
            "REQ-005 移动端展示文档打印、照片打印、扫描复印三个入口及对应文案",
        ],
    }
    scan_review = yaml_service.generated_case_requirement_scope_review({
        "case_id": "TC-003",
        "title": "扫描复印百度网盘入口可见性",
        "coverage": "REQ-003",
        "steps": ["点击扫描复印", "等待扫描复印页面展示百度网盘入口"],
        "assertions": ["扫描复印页面可见百度网盘入口"],
    }, multi_point_analysis)
    require(
        scan_review.get("ok")
        and scan_review.get("matchedRequirementIds") == ["REQ-003"]
        and scan_review.get("matchedRequirementPointCount") == 1,
        "Scope review must compare a case with its mapped REQ point instead of the first global requirement tokens",
    )
    ai_suggestion_review = yaml_service.generated_case_requirement_scope_review({
        "case_id": "TC-AI-SUGGESTED",
        "title": "资料服务首页企业云盘入口展示",
        "coverage": "business_goals[0]；ai_suggested_requirement_points[0]",
        "steps": ["等待首页稳定显示", "等待企业云盘入口可见"],
        "assertions": ["企业云盘与资料归档、文件管理入口同级展示"],
    }, {
        "requirement_points": [
            "REQ-001 资料归档页面展示企业云盘入口",
            "REQ-002 文件管理页面展示企业云盘入口",
        ],
        "business_goals": ["企业云盘入口展示、文案及同级关系"],
        "ai_suggested_requirement_points": ["资料服务首页也可增加企业云盘入口"],
    })
    require(
        not ai_suggestion_review.get("ok")
        and ai_suggestion_review.get("requiresExplicitRequirementId") is True
        and not ai_suggestion_review.get("matchedRequirementIds")
        and any("显式 REQ 契约" in reason for reason in ai_suggestion_review.get("reasons") or []),
        "An AI-suggested branch without a valid explicit REQ mapping must not satisfy scope through global business goals",
    )
    mobile_copy_review = yaml_service.generated_case_requirement_scope_review({
        "case_id": "TC-005",
        "title": "移动端三个基础打印入口文案一致性",
        "coverage": "REQ-005",
        "steps": ["进入首页并查看文档打印、照片打印、扫描复印入口"],
        "assertions": ["三个入口展示需求指定的业务文案"],
    }, multi_point_analysis)
    require(
        mobile_copy_review.get("ok"),
        "Business copy consistency mapped to an explicit requirement must not be treated as an unrequested robustness scenario",
    )
    wide_adaptation_review = yaml_service.generated_case_requirement_scope_review({
        "case_id": "TC-006",
        "title": "宽屏设备下百度网盘入口横向列表滑动可见性验证",
        "coverage": "REQ-005",
        "steps": ["进入首页", "进入文档打印页", "横向滑动入口列表直到百度网盘入口可见"],
        "assertions": ["宽屏设备下百度网盘入口文案、位置、可见性与手机端保持一致"],
    }, {
        "requirement_points": [
            "REQ-005 多设备形态适配验证：宽屏（375x1203）与手机（375x812）下入口文案、位置、可见性保持一致",
        ],
    })
    require(
        wide_adaptation_review.get("ok"),
        "Explicitly mapped display/adaptation requirements must not be moved to manual only because wording differs between multi-device and wide-screen",
    )

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
    visual_prompt = ai_skill_service.load_ai_skill_prompt("visual_grounder")
    require(
        "只返回当前图片能直接支持的增量" in visual_prompt
        and "不得复制 `base_payload`" in visual_prompt,
        "Visual grounding must request a bounded delta instead of regenerating the complete case payload per image",
    )
    visual_body = ai_skill_service.build_dashscope_chat_body(
        "视觉增量",
        image_assets=[{"mime": "image/png", "base64": "AA=="}],
        max_tokens=2048,
    )
    require(
        visual_body.get("max_tokens") == 2048,
        "Visual grounding must cap response generation independently of the configured vision model",
    )
    original_gateway_skill = ai_skill_service.ai_gateway_skill_content
    visual_gateway_calls = []
    try:
        def fake_visual_gateway(*args, **kwargs):
            visual_gateway_calls.append(kwargs)
            kwargs.get("runtime_trace", {}).update({
                "selectedProviderId": "highway_gpt5_mini",
                "selectedModel": "gpt-5-mini",
                "providerId": "qwen_plus",
                "model": "qwen3.6-plus",
                "fallbackUsed": True,
                "fallbackIndex": 1,
                "fallbackReason": "selected model does not support image input",
                "source": "ai_gateway",
                "imageCount": 1,
            })
            return json.dumps({
                "title": "基础打印新增百度网盘入口",
                "module": "基础打印",
                "review": {"visual_grounding_check": "已结合截图核对入口文案"},
            }, ensure_ascii=False)

        ai_skill_service.ai_gateway_skill_content = fake_visual_gateway
        grounded = ai_skill_service.call_visual_grounder_skill(
            base_payload["title"],
            base_payload["module"],
            base_payload,
            ["Figma 文档打印页面"],
            [{"mime": "image/png", "base64": "AA=="}],
            timeout_seconds=60,
            model_config={"providerId": "highway_gpt5_mini", "model": "gpt-5-mini"},
        )
    finally:
        ai_skill_service.ai_gateway_skill_content = original_gateway_skill
    require(
        grounded.get("analysis", {}).get("requirement_points") == analysis["requirement_points"],
        "Visual grounding must inherit required analysis context before schema validation",
    )
    require(
        grounded.get("review", {}).get("visual_grounder_skill") == "visual_grounder.v1",
        "Visual grounding must retain a completed AI judgment marker",
    )
    require(
        visual_gateway_calls
        and len(visual_gateway_calls[0].get("image_assets") or []) == 1
        and grounded.get("review", {}).get("model_trace", {}).get("selectedModel") == "gpt-5-mini"
        and grounded.get("review", {}).get("model_trace", {}).get("model") == "qwen3.6-plus"
        and grounded.get("review", {}).get("model_trace", {}).get("fallbackUsed") is True,
        "Visual grounding must send images through Gateway and record selected versus actual fallback model",
    )
    require(
        grounded.get("cases") == [concrete_case]
        and grounded.get("review", {}).get("visual_case_preservation", {}).get("base_case_count") == 1,
        "Visual grounding must preserve base cases when the AI returns only its visual judgment",
    )
    original_gateway_skill = ai_skill_service.ai_gateway_skill_content
    try:
        ai_skill_service.ai_gateway_skill_content = lambda *args, **kwargs: json.dumps({
            "title": base_payload["title"],
            "module": base_payload["module"],
            "analysis": {},
            "scenarios": [],
            "cases": [],
            "manual_cases": [],
            "review": {},
        }, ensure_ascii=False)
        missing_judgement_rejected = False
        try:
            ai_skill_service.call_visual_grounder_skill(
                base_payload["title"],
                base_payload["module"],
                base_payload,
                ["Figma 文档打印页面"],
                [{"mime": "image/png", "base64": "AA=="}],
                timeout_seconds=45,
            )
        except ValueError as exc:
            missing_judgement_rejected = "visual_grounding_check" in str(exc)
    finally:
        ai_skill_service.ai_gateway_skill_content = original_gateway_skill
    require(
        missing_judgement_rejected,
        "A visual batch without its own auditable judgment must never be counted as completed",
    )
    original_grounder = ai_skill_service.call_visual_grounder_skill
    visual_attempt_timeouts = []
    try:
        def flaky_visual_grounder(title, module, payload, visual_text_assets, image_assets, timeout_seconds=None, model_config=None):
            visual_attempt_timeouts.append(timeout_seconds)
            if len(visual_attempt_timeouts) == 1:
                raise TimeoutError("first visual attempt timed out")
            result = json.loads(json.dumps(payload, ensure_ascii=False))
            result.setdefault("review", {})["visual_grounding_check"] = "bounded retry completed"
            return result

        ai_skill_service.call_visual_grounder_skill = flaky_visual_grounder
        retried_visual = ai_skill_service.call_dashscope_refine_cases(
            base_payload["title"],
            base_payload["module"],
            base_payload,
            ["Figma 文档打印页面"],
            [{"mime": "image/png", "base64": "AA=="}],
            timeout_seconds=90,
            legacy_fallback=False,
            bounded_retry=True,
        )
    finally:
        ai_skill_service.call_visual_grounder_skill = original_grounder
    require(
        len(visual_attempt_timeouts) == 2
        and visual_attempt_timeouts[0] == 45
        and 45 <= visual_attempt_timeouts[1] <= 90
        and retried_visual.get("review", {}).get("visual_grounder_attempts", {}).get("retryUsed") is True,
        "Mindmap visual grounding must provide two useful attempts inside the original per-batch budget",
    )
    rich_visual_payload = json.loads(json.dumps(base_payload, ensure_ascii=False))
    rich_visual_payload["cases"][0]["execution_trace"] = {"large": "x" * 2000}
    rich_visual_payload["review"]["orchestration_history"] = ["y" * 2000]
    rich_visual_payload["analysis"]["visual_notes"] = ["前一批视觉状态已核对"]
    compact_visual_payload = ai_skill_service.compact_visual_grounder_base_payload(rich_visual_payload)
    require(
        compact_visual_payload.get("analysis", {}).get("requirement_points") == analysis["requirement_points"]
        and compact_visual_payload.get("cases", [{}])[0].get("steps") == concrete_case.get("steps")
        and "execution_trace" not in compact_visual_payload.get("cases", [{}])[0]
        and compact_visual_payload.get("review") == {},
        "Visual grounding compaction must retain requirement/case evidence while dropping orchestration history",
    )
    merged_visual_payload = ai_skill_service.merge_visual_grounder_payload(rich_visual_payload, {
        "title": base_payload["title"],
        "module": base_payload["module"],
        "analysis": {"requirement_points": analysis["requirement_points"], "visual_notes": ["后一批视觉状态已核对"]},
        "cases": [{
            "title": concrete_case["title"],
            "assertions": ["设计稿中的百度网盘入口文案完整可见"],
        }],
        "review": {
            "visual_grounding_check": "completed",
            "current_page_evidence": [{
                "caseId": "TC-VISUAL-CURRENT-1",
                "requirementId": "REQ-001",
                "branch": "资料页",
                "pageTitle": "标准版",
                "parentPath": ["资料服务", "资料页"],
                "navigationLeaf": "标准版",
                "targetText": "企业云盘",
                "sameBranch": True,
                "confidence": 0.91,
                "source": "figma_current_frame",
            }],
        },
    })
    require(
        merged_visual_payload.get("cases", [{}])[0].get("execution_trace") == {"large": "x" * 2000}
        and merged_visual_payload.get("cases", [{}])[0].get("assertions") == ["设计稿中的百度网盘入口文案完整可见"]
        and merged_visual_payload.get("analysis", {}).get("visual_notes") == [
            "前一批视觉状态已核对",
            "后一批视觉状态已核对",
        ]
        and merged_visual_payload.get("review", {}).get("orchestration_history") == ["y" * 2000]
        and merged_visual_payload.get("review", {}).get("visual_grounding_check") == "completed",
        "Visual grounding merge must apply visual corrections without erasing full planning evidence",
    )
    merged_visual_payload = ai_skill_service.merge_visual_grounder_payload(merged_visual_payload, {
        "review": {
            "visual_grounding_check": "second batch completed",
            "current_page_evidence": [{
                "caseId": "TC-VISUAL-CURRENT-2",
                "requirementId": "REQ-002",
                "branch": "归档页",
                "pageTitle": "新版归档",
                "parentPath": ["资料服务", "归档页"],
                "navigationLeaf": "新版归档",
                "targetText": "企业云盘",
                "sameBranch": True,
                "confidence": "high",
                "source": "uploaded_current_frame",
            }],
        },
    })
    require(
        len(merged_visual_payload.get("review", {}).get("current_page_evidence") or []) == 2
        and merged_visual_payload["review"]["current_page_evidence"][0].get("navigationLeaf") == "标准版"
        and merged_visual_payload["review"]["current_page_evidence"][1].get("navigationLeaf") == "新版归档",
        "Independent visual batches must accumulate structured current-page evidence instead of letting the last frame erase earlier AI judgements",
    )
    compact_convergence_analysis, compact_convergence_source = ai_skill_service._compact_executable_convergence_context(
        merged_visual_payload.get("analysis"),
        {
            "visualBatchJudgements": [{
                "batch": 1,
                "imageNames": ["current-variant.png"],
                "judgement": "当前批次确认了新的可见叶子和入口文案",
            }],
        },
    )
    require(
        compact_convergence_analysis.get("visual_notes") == [
            "前一批视觉状态已核对",
            "后一批视觉状态已核对",
        ]
        and compact_convergence_source.get("visualBatchJudgements", [{}])[0].get("batch") == 1,
        "The final AI convergence pass must retain every completed visual batch note instead of seeing only the last frame",
    )
    acceptance_visual_base = {
        "title": "新增企业云盘入口",
        "module": "资料服务",
        "analysis": {
            "requirement_points": ["REQ-031 资料页企业云盘入口展示、文案及同级关系"],
            "requirement_acceptance_checks": [
                {"id": "REQ-031-CHECK-01", "requirementId": "REQ-031", "branch": "资料页", "kind": "visibility", "text": "校验企业云盘入口可见"},
                {"id": "REQ-031-CHECK-02", "requirementId": "REQ-031", "branch": "资料页", "kind": "relation", "text": "校验企业云盘入口与当前页面其它入口同级展示"},
                {"id": "REQ-031-CHECK-03", "requirementId": "REQ-031", "branch": "资料页", "kind": "copy", "text": "校验企业云盘入口使用需求约定的可见文案"},
            ],
        },
        "cases": [{
            "case_id": "TC-VIS-CONTRACT",
            "title": "资料页企业云盘入口展示",
            "coverage": "REQ-031",
            "requirementRefs": ["REQ-031 资料页企业云盘入口展示、文案及同级关系"],
            "steps": ["进入资料页", "等待企业云盘入口可见"],
            "assertions": ["企业云盘入口可见，文案完整，并与本地文件入口同级展示"],
        }],
        "manual_cases": [],
    }
    acceptance_visual_merge = ai_skill_service.merge_visual_grounder_payload(
        acceptance_visual_base,
        {
            "cases": [{
                "case_id": "TC-VIS-CONTRACT",
                "assertions": ["当前设计 Frame 展示资料详情标题", "页面底部展示温馨提示"],
            }],
            "review": {"visual_grounding_check": "当前 Frame 的标题与提示已核对"},
        },
    )
    acceptance_visual_assertions = acceptance_visual_merge.get("cases", [{}])[0].get("assertions") or []
    require(
        acceptance_visual_assertions[0] == "企业云盘入口可见，文案完整，并与本地文件入口同级展示"
        and "当前设计 Frame 展示资料详情标题" in acceptance_visual_assertions
        and acceptance_visual_merge.get("review", {}).get("visual_acceptance_guard", {}).get("preservedPatchCount") == 1
        and set(
            acceptance_visual_merge.get("review", {})
            .get("visual_acceptance_guard", {})
            .get("preservedRecords", [{}])[0]
            .get("acceptanceCheckIds", [])
        ) == {"REQ-031-CHECK-01", "REQ-031-CHECK-02", "REQ-031-CHECK-03"},
        "A soft visual delta may add current-frame assertions but must not replace requirement-mapped visibility, copy, or relation assertions with an adjacent page title",
    )
    scoped_visual_base = {
        "title": "新增发票入口",
        "module": "会员服务",
        "analysis": {"requirement_points": ["订单页展示发票入口"]},
        "cases": [{
            "case_id": "TC-VIS-001",
            "title": "订单页发票入口展示与同级关系校验",
            "scenario": "订单页入口展示",
            "steps": ["进入订单页", "等待发票入口与订单筛选入口同屏可见"],
            "assertions": ["发票入口与订单筛选入口同级展示，文案完整可见"],
            "expected_result": "订单页展示发票入口",
        }, {
            "case_id": "TC-VIS-002",
            "title": "禁用状态不展示发票入口",
            "scenario": "入口隐藏状态",
            "steps": ["进入禁用状态订单页"],
            "assertions": ["发票入口不可见"],
        }],
        "manual_cases": [],
        "review": {},
    }
    scoped_visual_merge = ai_skill_service.merge_visual_grounder_payload(scoped_visual_base, {
        "cases": [{
            "case_id": "TC-VIS-001",
            "steps": ["进入参数配置页", "检查当前页未发现任何文件导入入口"],
            "assertions": ["当前参数配置页未出现发票入口"],
            "expected_result": "当前页无文件导入入口",
            "repair_hints": "当前图片是参数配置状态，无法证明订单页入口布局",
        }, {
            "case_id": "TC-VIS-002",
            "assertions": ["禁用状态下未展示发票入口"],
        }],
        "review": {"visual_grounding_check": "参数配置页没有文件导入入口"},
    })
    scoped_visual_by_id = {
        item.get("case_id"): item for item in scoped_visual_merge.get("cases") or []
    }
    require(
        scoped_visual_by_id["TC-VIS-001"].get("steps") == scoped_visual_base["cases"][0]["steps"]
        and scoped_visual_by_id["TC-VIS-001"].get("assertions") == scoped_visual_base["cases"][0]["assertions"]
        and scoped_visual_by_id["TC-VIS-001"].get("expected_result") == scoped_visual_base["cases"][0]["expected_result"]
        and "参数配置状态" in scoped_visual_by_id["TC-VIS-001"].get("repair_hints", "")
        and scoped_visual_by_id["TC-VIS-002"].get("assertions") == ["禁用状态下未展示发票入口"]
        and scoped_visual_merge.get("review", {}).get("visual_scope_guard", {}).get("blockedPatchCount") == 1,
        "A later unrelated frame may record a conflict but must not invert a positive requirement case; true negative cases remain calibratable",
    )
    visual_reclassification_merge = ai_skill_service.merge_visual_grounder_payload(
        scoped_visual_base,
        {
            "manual_cases": [{
                "case_id": "TC-VIS-001",
                "reason": "当前局部 Frame 没有展示目标入口，建议转人工",
            }],
            "review": {"visual_grounding_check": "局部 Frame 未展示目标入口"},
        },
    )
    visual_reclassification_by_id = {
        item.get("case_id"): item for item in visual_reclassification_merge.get("cases") or []
    }
    require(
        "局部 Frame" in visual_reclassification_by_id["TC-VIS-001"].get("repair_hints", "")
        and not visual_reclassification_merge.get("manual_cases")
        and visual_reclassification_merge.get("review", {}).get("visual_classification_guard", {}).get("blockedReclassificationCount") == 1,
        "A soft visual batch may record a conflict but must not move an automatic candidate into the manual pool",
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
    mindmap_failed_visual = agent_service._agent_visual_reference_report({
        "artifacts": {
            "sourceContext": {
                "figmaUsedPages": [{"page_name": f"page-{index}"} for index in range(4)],
                "figmaImageCount": 4,
            },
        },
    }, {
        "cases": {
            "review": {
                "mindmap_visual_batches": "0/4",
                "mindmap_visual_batches_attempted": 4,
                "mindmap_visual_batch_results": [
                    {"batch": index, "status": "failed", "imageCount": 1, "error": "timeout"}
                    for index in range(1, 5)
                ],
                "visual_refine_error": "4 visual batches timed out",
            },
        },
    })
    require(
        mindmap_failed_visual.get("sentToAiForJudgement") is True
        and mindmap_failed_visual.get("aiJudgementStatus") == "failed"
        and mindmap_failed_visual.get("visualBatchesAttempted") == 4
        and mindmap_failed_visual.get("visualBatchesTotal") == 4,
        "Mindmap visual batch failures must remain truthful even when YAML generation never returns",
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
    display_case = {
        "case_id": "TC-REQ-COPY",
        "title": "移动端首页入口文案一致性校验",
        "coverage": "REQ-004",
        "executionLevel": "executable",
        "risk": "low",
        "steps": ["进入首页", "查看目标入口展示文案"],
        "assertions": ["入口展示需求指定文案且与同级入口布局一致"],
    }
    display_scope_review = {
        "ok": True,
        "matchedRequirementIds": ["REQ-004"],
        "matchedRequirementPointCount": 1,
        "reasons": [],
    }
    display_score = {
        "level": "needs_review",
        "executionLevel": "needs_review",
        "reasons": ["移动端首页入口文案一致性校验: 异常/边界/鲁棒性扩展缺少成功基线依据，默认需确认后执行"],
    }
    require(
        yaml_service.generated_yaml_effective_level("needs_review", display_case, display_scope_review, display_score) == "executable",
        "Requirement-mapped low-risk visible copy/display checks must not be blocked only by the generic robustness diagnostic",
    )
    manual_hint_display_case = {
        **display_case,
        "title": "扫描复印页-百度网盘入口UI展示及同级位置校验（需人工确认）",
        "tags": ["基础打印", "扫描复印", "needs_review"],
        "executionLevel": "executable",
        "reason": "扫描复印页需人工确认是否复用通用导入组件",
    }
    require(
        yaml_service.generated_yaml_effective_level("needs_review", manual_hint_display_case, display_scope_review, display_score) == "needs_review",
        "Generated cases with explicit manual/needs-review hints must not be promoted to executable by display-check correction",
    )
    stale_hint_verified_case = {
        **manual_hint_display_case,
        "steps": [
            "等待 App 首页加载完成",
            "点击「扫描复印」入口",
            "点击「证件扫描」",
            "点击「立即使用」",
            "校验「百度网盘」入口可见且文案为“百度网盘”",
            "点击「百度网盘」入口",
            "等待跳转至百度网盘相关页面",
        ],
        "ai_case_plan": {
            "baselineGrounded": True,
            "baselineVerified": True,
            "pathPlanApplied": True,
            "baselineId": "d623c1e73180bfac",
            "flow": [
                "等待 App 首页加载完成",
                "点击「扫描复印」入口",
                "点击「证件扫描」",
                "点击「立即使用」",
                "校验「百度网盘」入口可见且文案为“百度网盘”",
                "点击「百度网盘」入口",
                "等待跳转至百度网盘相关页面",
            ],
        },
    }
    require(
        yaml_service.generated_yaml_effective_level("needs_review", stale_hint_verified_case, display_scope_review, {**display_score, "score": 100}) == "executable",
        "Stale manual wording must not demote a case after platform verified baseline grounding and path planning have already made it executable",
    )
    stale_hint_conditional_case = {
        **stale_hint_verified_case,
        "steps": ["点击「扫描复印」入口", "若不存在「百度网盘」入口则记录缺陷"],
        "ai_case_plan": {
            **stale_hint_verified_case["ai_case_plan"],
            "flow": ["点击「扫描复印」入口", "若不存在「百度网盘」入口则记录缺陷"],
        },
    }
    require(
        yaml_service.generated_yaml_effective_level("needs_review", stale_hint_conditional_case, display_scope_review, {**display_score, "score": 100}) == "needs_review",
        "Verified-plan override must not allow conditional manual defect-recording branches into Runner",
    )
    non_display_case = {
        **display_case,
        "title": "移动端首页入口加载中点击重试校验",
        "steps": ["进入首页", "在加载中连续点击目标入口"],
        "assertions": ["加载中点击后页面无异常"],
    }
    require(
        yaml_service.generated_yaml_effective_level("needs_review", non_display_case, display_scope_review, display_score) == "needs_review",
        "Non-display robustness expansions must remain review-only even when they mention an explicit requirement id",
    )

    ai_source = (ROOT / "task_server" / "services" / "ai_skill_service.py").read_text(encoding="utf-8")
    install_source = (ROOT / "deploy" / "install-server.sh").read_text(encoding="utf-8")
    env_source = (ROOT / "deploy" / "midscene.env.example").read_text(encoding="utf-8")
    require('MIDSCENE_AUTOMATION_FILTER_TIMEOUT_SECONDS", "150"' in ai_source, "Automation filter default timeout must allow the production model more than 90 seconds")
    require('ensure_env_default "MIDSCENE_AUTOMATION_FILTER_TIMEOUT_SECONDS" "150"' in install_source, "Installer must configure the automation filter timeout")
    require('upgrade_env_default_if_old "MIDSCENE_AUTOMATION_FILTER_TIMEOUT_SECONDS" "150" "90"' in install_source, "Installer must migrate the old 90-second automation filter timeout")
    require("MIDSCENE_AUTOMATION_FILTER_TIMEOUT_SECONDS='150'" in env_source, "Environment example must document the new automation filter timeout")
    planner_source = (ROOT / "ai_skills" / "prompts" / "executable_yaml_planner.v1.md").read_text(encoding="utf-8")
    filter_source = (ROOT / "ai_skills" / "prompts" / "automation_filter.v1.md").read_text(encoding="utf-8")
    require(
        "同一条 case 的当前页面路径" in planner_source
        and "同一页面路径和同一业务检查点" in filter_source
        and "证据不足时进入 `needs_review_cases` 或 `manual_cases`" in filter_source,
        "AI planning/filtering must use same-page evidence for relative-position assertions instead of hardcoded business terms",
    )


def check_agent_blocks_incomplete_generated_yaml_coverage():
    from task_server.services import agent_service

    run = {
        "runId": "agent-static-coverage-gap",
        "scope": "regression",
        "artifacts": {
            "generationPipeline": {
                "source": "ui_yaml_pipeline",
                "caseCount": 5,
                "yamlFileCount": 2,
                "coverageAudit": {"requirement_point_count": 5},
                "generatedCaseGroups": {
                    "counts": {"executable": 2, "needs_review": 3, "draft": 0, "manual": 0},
                    "needs_review_cases": [
                        {"name": "业务入口 B 可见性", "reasons": ["生成结果缺少该用例的 YAML 文件，需补齐后才能自动下发 Runner"]},
                    ],
                },
            },
            "generatedCases": {"cases": [{"case_id": f"TC-{idx:03d}", "title": f"用例{idx}"} for idx in range(1, 6)]},
        },
    }
    gap = agent_service._agent_generated_yaml_coverage_gap(run, refs=[{"file": "a.yaml"}, {"file": "b.yaml"}])
    require(
        gap and gap.get("caseCount") == 5 and gap.get("yamlCount") == 2 and gap.get("ok") is False,
        "Agent must detect generated case/YAML coverage gaps before Runner execution",
    )
    quality = agent_service._build_agent_quality_report(
        {"scope": "regression", "artifacts": {}},
        {
            "caseCount": 5,
            "yamlFileCount": 2,
            "scenarioCount": 8,
            "coverageAudit": {"requirement_point_count": 5, "ok": True},
            "cases": {"analysis": {"requirement_points": [f"REQ-{idx:03d}" for idx in range(1, 6)]}, "cases": [{}, {}, {}, {}, {}]},
            "summary": {"counts": {}},
        },
        [{"file": "a.yaml"}, {"file": "b.yaml"}],
        {"taskCount": 2},
    )
    require(quality.get("status") == "blocked" and any("只生成 2 个 YAML" in item for item in quality.get("blockers", [])), "Quality report must block incomplete generated YAML coverage")

    converted = {
        "analysis": {"requirement_points": ["REQ-001", "REQ-002", "REQ-003"]},
        "cases": [
            {"case_id": "TC-001", "title": "业务入口 A 可见性"},
            {"case_id": "TC-002", "title": "业务入口 B 可见性"},
            {"case_id": "TC-003", "title": "业务入口 C 可见性"},
        ],
        "manual_cases": [],
    }
    yaml_groups = {
        "executable_cases": [{"case_id": "TC-001", "file": "01.yaml"}, {"case_id": "TC-002", "file": "02.yaml"}],
        "needs_review_cases": [],
        "draft_cases": [],
        "manual_cases": [],
    }
    # Static source check ensures yaml_service records cases that did not produce YAML.
    yaml_source = (ROOT / "task_server" / "services" / "yaml_service.py").read_text(encoding="utf-8")
    require("该自动化用例未生成对应 YAML 文件" in yaml_source and "生成结果缺少该用例的 YAML 文件" in yaml_source, "YAML generation must surface automatic cases that failed to produce YAML")
    require(converted and yaml_groups, "Fixture sanity check")


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


def check_agent_generation_pipeline_preserves_selected_ai_model():
    from task_server.services import agent_service
    from task_server.services import yaml_service

    original_generate = yaml_service.generate_ui_yaml_from_request
    original_update = yaml_service.update_generate_job
    captured = {}
    try:
        def fake_generate(request_data, job_id=None):
            captured["request"] = request_data
            captured["job_id"] = job_id
            return {
                "case_set_id": request_data.get("case_set_id"),
                "cases": {"analysis": {}, "cases": [], "manual_cases": [], "review": {}},
                "yamlFiles": [],
                "files": [],
                "caseCount": 0,
                "manualCaseCount": 0,
                "scenarioCount": 0,
            }

        yaml_service.generate_ui_yaml_from_request = fake_generate
        yaml_service.update_generate_job = lambda *args, **kwargs: {}
        run = {
            "runId": "agent-static-selected-model",
            "target": "通用页面回归",
            "module": "AI测试",
            "modelProviderId": "highway_gpt5_mini",
            "aiProviderId": "highway_gpt5_mini",
            "aiModel": "gpt-5-mini",
            "model": "provider:highway_gpt5_mini",
            "executionMode": "RUNNER_JOB",
            "runnerId": "win-runner-01",
            "deviceId": "ecbfd645",
            "deviceStrategy": "fixed",
            "artifacts": {},
        }
        agent_service._agent_generate_yaml_from_ui_pipeline(
            run,
            {"target": run["target"], "requirementText": "验证通用页面回归"},
            "验证通用页面回归",
        )
        request = captured.get("request") or {}
        require(request.get("modelProviderId") == "highway_gpt5_mini", "Agent YAML generation must preserve the selected provider id")
        require(request.get("aiProviderId") == "highway_gpt5_mini", "Agent YAML generation must preserve the selected AI provider id")
        require(request.get("aiModel") == "gpt-5-mini", "Agent YAML generation must preserve the selected AI model")
        require(request.get("model") == "gpt-5-mini", "Agent YAML generation must not replace the selected model with the provider selector token")
        execution = request.get("executionContext") or {}
        require(
            execution.get("runnerId") == "win-runner-01"
            and execution.get("deviceId") == "ecbfd645"
            and execution.get("deviceStrategy") == "fixed"
            and execution.get("singleDeviceOnly") is True,
            "Agent YAML generation must preserve the fixed single-device execution context with the selected model",
        )
    finally:
        yaml_service.generate_ui_yaml_from_request = original_generate
        yaml_service.update_generate_job = original_update


def check_agent_executable_gate_invokes_ai_rewrite():
    from task_server.services import agent_service
    from task_server.services import yaml_service
    from task_server.services.yaml_executable_scorer import score_midscene_yaml_executable

    home_ai_yaml = """android:
  tasks:
    - name: 首页入口文案展示校验
      flow:
        - runAdbShell: input keyevent 3
        - runAdbShell: am force-stop com.example.app
        - launch: com.example.app
        - ai: 回到首页
        - aiTap: 点击「目标入口」入口
        - sleep: 300
        - aiWaitFor: 目标业务页加载完成，展示目标文案
        - aiAssert: 目标业务页展示目标文案
"""
    original_score = score_midscene_yaml_executable(home_ai_yaml, generated=True)
    local_repair = yaml_service.repair_generated_yaml_executable_gate_issues(home_ai_yaml)
    repaired_score = score_midscene_yaml_executable(local_repair.get("content", ""), generated=True)
    require(local_repair.get("changed") and "aiWaitFor: App 首页加载完成" in local_repair.get("content", ""), "Home recovery ai planning must be normalized into an explicit aiWaitFor state")
    require(
        original_score.get("executionLevel") != "executable" and repaired_score.get("executionLevel") == "executable",
        "Local executable-gate repair must improve generated home-entry flow without relaxing the scorer",
    )
    observable_wait_yaml = """android:
  tasks:
    - name: 企业云盘入口可达性
      flow:
        - launch: com.example.app
        - aiWaitFor: 等待 App 首页稳定显示
        - aiTap: 点击「照片打印」入口
        - aiWaitFor: 照片打印页面加载完成
        - aiWaitFor: 校验确认「企业云盘」入口是否存在，若存在则UI符合设计规范；若不存在，需反馈产品确认是否为遗漏
        - aiTap: 点击「企业云盘」入口
        - aiWaitFor: 等待页面跳转或弹窗出现
        - aiWaitFor: 企业云盘文件列表页已加载，页面标题和文件列表可见
        - aiAssert: 企业云盘文件列表页无白屏或崩溃
"""
    observable_wait_repair = yaml_service.repair_generated_yaml_executable_gate_issues(observable_wait_yaml)
    observable_wait_content = observable_wait_repair.get("content") or ""
    require(
        "App 首页加载完成，可见「照片打印」入口" in observable_wait_content
        and "等待 App 首页稳定显示" not in observable_wait_content,
        "An abstract generated home wait must be anchored to the next visible-text navigation target",
    )
    require(
        "等待页面跳转或弹窗出现" not in observable_wait_content
        and "企业云盘文件列表页已加载" in observable_wait_content,
        "A process-only transition wait must be removed when the following stable target state already defines completion",
    )
    require(
        "若不存在" not in observable_wait_content
        and "页面展示「企业云盘」入口，入口文案及所在区域清晰可见" in observable_wait_content,
        "A human existence-review branch must become the expected observable UI state before Runner dispatch",
    )
    generated_task = yaml_service.case_to_task_yaml({
        "title": "文档打印入口校验",
        "app_package": "com.example.app",
        "steps": ["点击底部 Tab「首页」", "点击「文档打印」入口"],
        "assertions": ["文档打印页展示百度网盘入口"],
    })
    require(
        generated_task.index("launch: com.example.app")
        < generated_task.index("aiWaitFor: \"被测 App 首页已加载完成")
        < generated_task.index("aiTap: \"点击底部 Tab「首页」"),
        "Every newly generated Agent YAML must wait for a visible home state before its first AI navigation",
    )
    explicit_launch_wait_task = yaml_service.case_to_task_yaml({
        "title": "显式启动等待",
        "app_package": "com.example.app",
        "steps": ["启动App并等待首页加载完成", "点击「文档打印」入口"],
        "assertions": ["文档打印页可见"],
    })
    require(
        explicit_launch_wait_task.count("首页核心功能入口可见") == 1,
        "An AI-authored launch-ready step must replace the generic guard instead of adding a duplicate model wait",
    )

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
                "modelProviderId": "highway_gpt5_mini",
                "aiModel": "gpt-5-mini",
                "artifacts": {
                    "generationPipeline": {"source": "ui_yaml_pipeline"},
                    "generatedYamlPaths": [path],
                    "yamlRefs": [dict(ref)],
                },
            }
            repaired_ref, repair = agent_service._agent_repair_yaml_ref_for_execution(run, ref, reason="static_check")
            written = Path(path).read_text(encoding="utf-8")
            require(calls, "Generated YAML executable gate must invoke AI rewrite for semantic long-chain failures")
            require(
                calls[-1].get("model_config") == {"providerId": "highway_gpt5_mini", "model": "gpt-5-mini"},
                "Generated YAML executable-gate rewrite must retain the Agent-selected model",
            )
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
        _agent_runner_gate_ref_is_deferred,
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
    invalid_scroll_yaml = """android:
  tasks:
    - name: invalid scroll params
      flow:
        - aiScroll: 页面内容区域
          scrollType: singleAction
          direction: horizontal
          distance: medium
"""
    invalid_scroll_static = validate_yaml_static_executable(invalid_scroll_yaml)
    invalid_scroll_strong = yaml_service_module.validate_midscene_yaml_executability(invalid_scroll_yaml)
    require(not invalid_scroll_static.get("ok") and any("direction" in item for item in invalid_scroll_static.get("errors") or []) and any("distance" in item for item in invalid_scroll_static.get("errors") or []), "Static YAML validation must block invalid Midscene aiScroll enum and type values")
    require(not invalid_scroll_strong.get("ok") and any("direction" in item for item in invalid_scroll_strong.get("issues") or []) and any("distance" in item for item in invalid_scroll_strong.get("issues") or []), "Strong Agent/repair validation must independently block invalid aiScroll parameters")
    nested_scroll_yaml = """android:
  tasks:
    - name: invalid nested scroll
      flow:
        - aiScroll:
            direction: right
            distance: 1
            scrollType: singleAction
"""
    nested_scroll_static = validate_yaml_static_executable(nested_scroll_yaml)
    nested_scroll_strong = yaml_service_module.validate_midscene_yaml_executability(nested_scroll_yaml)
    require(
        not nested_scroll_static.get("ok")
        and any("非空字符串" in item for item in nested_scroll_static.get("errors") or []),
        "Static validation must reject the nested aiScroll object shape returned by the production repair model",
    )
    require(
        not nested_scroll_strong.get("ok")
        and any("非空字符串" in item for item in nested_scroll_strong.get("issues") or []),
        "Repair validation must reject nested aiScroll even when the AI Gateway response also contains a success flag",
    )
    valid_scroll_yaml = """android:
  tasks:
    - name: valid horizontal scroll
      flow:
        - aiWaitFor: 横向入口列表可见
        - aiScroll: 在横向入口列表向右滑动一次
          direction: right
          distance: 400
          scrollType: singleAction
        - aiAssert: 目标入口可见
"""
    require(
        validate_yaml_static_executable(valid_scroll_yaml).get("ok")
        and yaml_service_module.validate_midscene_yaml_executability(valid_scroll_yaml).get("ok"),
        "The official aiScroll string target with sibling direction/distance options must remain executable",
    )
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
    passive_clickability_step = "校验「百度网盘」入口可见、可点击且文案完整"
    _, passive_clickability_yaml = yaml_service_module.cases_to_midscene_yaml({
        "_automation_ready": True,
        "title": "入口能力校验",
        "cases": [{
            "title": "入口能力校验",
            "steps": [
                "启动App并等待首页加载完成",
                "点击「扫描复印」入口",
                passive_clickability_step,
            ],
            "assertions": ["百度网盘入口可见且文案完整"],
        }],
    }, app_package="com.xbxxhz.box")
    require(
        yaml_service_module.action_type(passive_clickability_step) == "aiWaitFor"
        and 'aiWaitFor: "校验「百度网盘」入口可见、可点击且文案完整"' in passive_clickability_yaml
        and 'aiTap: "校验「百度网盘」' not in passive_clickability_yaml,
        "A 校验 statement containing the capability adjective 可点击 must remain a passive wait",
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
        - aiTap: 点击「普通证件照」入口
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
    broad_check_tap_yaml = """android:
  tasks:
    - name: 扫描复印页-百度网盘入口UI展示及同级位置校验
      flow:
        - launch: com.xbxxhz.box
        - aiWaitFor: App 首页加载完成
        - aiTap: 点击「扫描复印」入口
        - aiWaitFor: 等待扫描复印页面加载完成
        - aiTap: 检查页面导入或文件选择区域
        - aiWaitFor: 「百度网盘」入口可见，文案为“百度网盘”，与同级入口并列展示
        - aiAssert: 「百度网盘」入口可见，文案为“百度网盘”，与同级入口并列展示
"""
    broad_check_repair = repair_generated_yaml_executable_gate_issues(broad_check_tap_yaml)
    broad_check_content = broad_check_repair.get("content", "")
    require(
        broad_check_repair.get("changed")
        and "aiTap: 检查页面导入或文件选择区域" not in broad_check_content
        and "aiWaitFor: 检查页面导入或文件选择区域" in broad_check_content
        and dry_run_midscene_yaml(broad_check_content, app_package="com.xbxxhz.box").get("ok") is True,
        "Generated YAML service must convert broad page-inspection aiTap prompts before Runner dispatch",
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
    require(
        len([item for item in blocked if _agent_runner_gate_ref_is_deferred(item)]) == 2,
        "Executable boundary or reachability cases excluded from first smoke must remain deferred for gated expansion",
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
    threshold_smoke_blocker = classify_generated_yaml_smoke_blocker(
        [{"failureType": "ASSERTION_FAILED", "reason": "1 条产品断言失败，另 1 条已通过"}],
        [],
        smoke_total=2,
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
        product_smoke_blocker.get("block") is True
        and product_smoke_blocker.get("executable") is True
        and product_smoke_blocker.get("thresholdPassed") is False
        and threshold_smoke_blocker.get("block") is False
        and threshold_smoke_blocker.get("thresholdPassed") is True
        and threshold_smoke_blocker.get("passRate") == 0.5
        and dry_run_smoke_blocker.get("block") is True,
        "Smoke gate must preserve executable product failures while requiring at least 50% real passes before expansion",
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
    flow_text = json.dumps(constraint.get("businessFlows") or [], ensure_ascii=False)
    require("token" not in flow_text.lower() and "一模一样" not in flow_text and "提高AI" not in flow_text, "Agent runtime business flow must filter product metrics and model goals")
    require("AI建模" in flow_text and ("语音" in flow_text or "长按" in flow_text), "Agent runtime requirement candidates must keep real AI modeling user actions")
    require(constraint.get("candidateOnly") and constraint.get("businessFlow") == [], "Agent must not promote pre-PLAN requirement candidates into a strict sequential flow")


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
    from task_server.services import agent_service
    from task_server.services.yaml_baseline_cache import get_yaml_baseline_cache_status, search_baseline_examples, search_diverse_baseline_examples

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
    diverse = search_diverse_baseline_examples(
        "基础打印新增第三方导入入口",
        branch_queries=[
            "文档打印 进入文档导入页",
            "照片打印 进入照片规格选择和导入页",
            "扫描复印 进入文件或图片选择页",
        ],
        module="基础打印",
        limit=12,
    )
    diverse_paths = [str(item.get("provenancePath") or "") for item in diverse]
    require(
        any(path.endswith("/6寸照片打印.yaml") for path in diverse_paths)
        and any(path.endswith("/文件扫描.yaml") for path in diverse_paths),
        "Branch-aware retrieval must retain sibling photo and scan navigation baselines instead of only global capability matches",
    )
    repair_examples = agent_service._agent_repair_baseline_examples(
        {
            "target": "新增第三方导入入口",
            "artifacts": {"businessFlowConstraint": {"businessFlows": [{
                "branch": "照片打印",
                "name": "照片打印导入页入口校验",
                "steps": ["首页", "点击照片打印", "进入照片规格选择页", "进入导入方式页"],
            }]}},
        },
        {
            "taskName": "照片打印页入口可见性校验",
            "module": "AI_Agent_草稿",
            "failureReason": "失败关键帧仍停在照片打印父页面",
        },
        "android:\n  tasks:\n    - name: 照片入口\n      flow:\n        - aiTap: 照片打印\n        - aiWaitFor: 目标入口可见\n",
        limit=6,
    )
    require(
        repair_examples and str(repair_examples[0].get("provenancePath") or "").endswith("/6寸照片打印.yaml"),
        "AI repair evidence must prioritize the sibling photo-size baseline for a failed photo branch",
    )
    mixed_flow_repair_examples = agent_service._agent_repair_baseline_examples(
        {
            "target": "新增第三方导入入口",
            "artifacts": {"businessFlowConstraint": {"businessFlows": [
                {
                    "branch": "文档打印",
                    "name": "文档打印导入页入口校验",
                    "steps": ["首页", "点击文档打印", "进入文档导入页"],
                },
                {
                    "branch": "照片打印",
                    "name": "照片打印导入页入口校验",
                    "steps": ["首页", "点击照片打印", "点击照片打印", "进入照片规格页"],
                },
                {
                    "branch": "扫描复印",
                    "name": "扫描复印导入页入口校验",
                    "steps": ["首页", "点击扫描复印", "进入文件选择页"],
                },
            ]}},
        },
        {
            "taskName": "照片打印页入口可见性校验",
            "file": "02-照片打印页入口可见性校验.yaml",
            "module": "AI_Agent_草稿",
            "failureReason": "失败关键帧仍停在照片打印父页面",
        },
        "# automation: 复用文档打印等待策略\nandroid:\n  tasks:\n    - name: 照片入口\n      flow:\n        - aiTap: 照片打印\n",
        limit=6,
    )
    require(
        mixed_flow_repair_examples
        and str(mixed_flow_repair_examples[0].get("provenancePath") or "").endswith("/6寸照片打印.yaml"),
        "Failed-task identity must keep sibling branch navigation ahead of unrelated baseline names mentioned only in YAML comments",
    )
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
    require("可信相似基线写法参考" in prompt_text and "你不是自由生成 YAML" in prompt_text, "YAML reference prompt must force provenance-grounded baseline imitation")
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
    bounded_terminal = "授权窗口或内容列表任一首个稳定状态可见，且无崩溃、无白屏"
    _, bounded_yaml = yaml_service.cases_to_midscene_yaml({
        "_automation_ready": True,
        "title": "外部入口首屏",
        "cases": [{
            "title": "外部入口首屏可达",
            "app_package": "com.example.app",
            "steps": ["点击企业云盘入口", f"检查{bounded_terminal}"],
            "assertions": [bounded_terminal],
        }],
    }, app_package="com.example.app")
    require(
        bounded_yaml.count("aiWaitFor:") == 2
        and sum(
            1 for line in bounded_yaml.splitlines()
            if "aiWaitFor:" in line and bounded_terminal in line
        ) == 1
        and bounded_yaml.count("aiAssert:") == 1,
        "The launch-ready wait must not duplicate an explicit final assertion wait",
    )
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

    fail_fast_calls = []

    def fake_fail_fast_skill(skill_name, payload=None, **_kwargs):
        fail_fast_calls.append(skill_name)
        if skill_name == "requirement_analyzer":
            return {
                "business_goals": ["验证入口"],
                "roles": ["用户"],
                "entry_points": ["首页"],
                "state_assumptions": [],
                "data_assumptions": [],
                "visible_outcomes": ["入口可见"],
                "risks": [],
                "requirement_points": ["REQ-001 入口可见"],
                "questions": [],
                "confidence": "high",
                "missing_inputs": [],
                "blockers": [],
                "assumptions": [],
                "readiness_score": 90,
                "readiness_level": "ready",
                "source_quality": {"requirement": "sufficient", "ui": "partial", "knowledge": "partial"},
            }
        if skill_name == "scenario_designer":
            raise TimeoutError("scenario timeout")
        raise AssertionError(f"Agent PLAN core failure must skip downstream skill: {skill_name}")

    ai_skill_service.run_ai_skill = fake_fail_fast_skill
    try:
        fail_fast_payload = ai_skill_service.build_cases_payload_from_skills(
            "任意入口需求",
            "任意模块",
            ["首页新增入口并校验可见"],
            allow_entry_visibility_fast_path=False,
            require_ai_core=True,
        )
    finally:
        ai_skill_service.run_ai_skill = original_run_ai_skill
    require(fail_fast_calls == ["requirement_analyzer", "scenario_designer"], "Agent PLAN must stop after a core scenario fallback instead of invoking downstream AI skills")
    require(fail_fast_payload.get("review", {}).get("core_ai_failure", {}).get("stage") == "scenario_designer", "Agent PLAN must preserve the core AI failure stage for bounded retry")
    require("visual_grounder" in (fail_fast_payload.get("review", {}).get("downstream_skipped") or []), "Agent PLAN fail-fast trace must show that visual grounding was intentionally skipped for the failed attempt")


def check_qwen_structured_skills_disable_thinking():
    from task_server.services import ai_skill_service

    original_model_for_images = ai_skill_service.dashscope_model_for_images
    try:
        ai_skill_service.dashscope_model_for_images = lambda _images=None: "qwen3.6-plus"
        body = ai_skill_service.build_dashscope_chat_body("请输出 JSON", json_response=True)
    finally:
        ai_skill_service.dashscope_model_for_images = original_model_for_images
    require(body.get("response_format") == {"type": "json_object"}, "DashScope structured skills must request JSON Mode")
    require(body.get("enable_thinking") is False, "Qwen3.6 structured skills must disable default thinking mode for JSON compatibility and bounded latency")

    yaml_service_source = (ROOT / "task_server" / "services" / "yaml_service.py").read_text(encoding="utf-8")
    require(
        'if require_ai_planning:' in yaml_service_source
        and '"stage": "skill_pipeline"' in yaml_service_source
        and '"core_ai_failure"' in yaml_service_source,
        "Agent MM pipeline exceptions must become explicit core AI failures instead of invoking a legacy planning fallback",
    )


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
    old_smoke_selector_enabled = ai_skill_service.AI_SMOKE_SELECTOR_ENABLED
    try:
        ai_skill_service.AI_SMOKE_SELECTOR_ENABLED = False
        selected_payload = ai_skill_service.select_smoke_cases_for_payload("百度网盘入口", "文档打印", payload)
    finally:
        ai_skill_service.AI_SMOKE_SELECTOR_ENABLED = old_smoke_selector_enabled
    smoke_ids = selected_payload["review"]["smoke_case_ids"]
    require(len(smoke_ids) <= 3, "Local smoke gate must only select the first batch of at most 3 cases")
    require("TC-001" not in smoke_ids and "TC-005" not in smoke_ids, "Local smoke gate must not prefer history/interference cases over the current normal chain")
    require({"TC-002", "TC-003"}.issubset(set(smoke_ids)), "Local smoke gate must prioritize normal-chain baseline-backed cases")


def check_yaml_runner_eligibility_filter():
    from task_server.services import ai_skill_service, yaml_service

    requirement_point = (
        "REQ-003 扫描复印：校验云盘入口可见、同级关系、文案及点击后的首个稳定页面"
    )
    source_candidate = {
        "title": "扫描复印页云盘入口展示",
        "scenario": "扫描复印页新增入口",
        "coverage": "REQ-003",
        "requirementRefs": [requirement_point],
        "start_page": "App 首页",
        "business_path": "App 首页 -> 扫描复印 -> 证件扫描",
        "goal": "校验入口展示；若授权态不确定则需 Mock 或预置后台状态",
        "data_requirements": "需后台配置固定授权态",
        "expected_result": "云盘入口可见且与同页导入入口同级展示",
        "assertions": ["云盘入口文案准确且与同页导入入口同级展示"],
        "repair_hints": "使用同分支成功基线补齐真实可见文字导航",
        "steps": [],
    }
    evidence_payload = {
        "title": "扫描复印新增入口",
        "analysis": {"requirement_points": [requirement_point]},
        "cases": [
            {
                "case_id": f"TC-{index:03d}",
                "title": f"既有分支入口展示 {index}",
                "requirementRefs": [f"REQ-{100 + index:03d} 既有分支入口展示"],
                "steps": ["进入业务页面", "等待云盘入口可见"],
                "assertions": ["云盘入口可见"],
            }
            for index in range(1, 5)
        ] + [source_candidate],
        "manual_cases": [],
    }
    evidence_filtered = yaml_service.split_automation_ready_cases(evidence_payload)
    preserved = next(
        item for item in evidence_filtered["manual_cases"]
        if item.get("case_id") == "TC-005"
    )
    require(
        preserved.get("originExecutionLevel") == "automatic"
        and preserved.get("executionLevel") == "manual"
        and preserved.get("requirementRefs") == [requirement_point]
        and preserved.get("business_path") == source_candidate["business_path"]
        and preserved.get("assertions") == source_candidate["assertions"]
        and preserved.get("repair_hints") == source_candidate["repair_hints"],
        "A blocked automatic candidate must retain its stable id, requirement mapping, path and assertions",
    )
    require(
        "case_id" not in source_candidate
        and "executionLevel" not in source_candidate
        and "originExecutionLevel" not in source_candidate,
        "Runner eligibility splitting must not mutate the AI source candidate",
    )
    automatic_records = []
    manual_records = []
    for default_origin, items in (
        ("automatic", evidence_filtered.get("cases") or []),
        ("manual", evidence_filtered.get("manual_cases") or []),
    ):
        for index, item in enumerate(items):
            origin_level = ai_skill_service._planner_case_origin_level(item, default_origin)
            record = {
                "raw": item,
                "compact": ai_skill_service._compact_case_for_plan(
                    item,
                    index,
                    origin_level=origin_level,
                ),
            }
            (manual_records if origin_level == "manual" else automatic_records).append(record)
    focused_automatic, focused_manual, _context, focus = (
        ai_skill_service._focus_executable_convergence_candidates(
            evidence_filtered,
            automatic_records,
            manual_records,
            {
                "pass": "coverage_convergence",
                "portfolioAudit": {
                    "executableCaseIds": ["TC-001"],
                    "unresolvedAutomaticCaseIds": [],
                    "missingRequirementPoints": [requirement_point],
                    "missingAcceptanceChecks": [],
                    "targetExecutableCount": 3,
                    "executableCount": 1,
                },
            },
            selected_baselines=[],
        )
    )
    require(
        "TC-005" in [item.get("case_id") for item in focused_automatic]
        and not focused_manual
        and "TC-005" in focus.get("focusedCandidateIds", []),
        "A downgraded automatic candidate must remain available to the existing AI convergence pass",
    )
    planner_requests = []
    original_run_ai_skill = ai_skill_service.run_ai_skill
    try:
        def fake_planner(skill_name, request, **_kwargs):
            require(skill_name == "executable_yaml_planner", "Unexpected AI skill in candidate identity replay")
            planner_requests.append(request)
            return {
                "cases": [{
                    "caseId": "TC-005",
                    "baselineId": "scan-success-baseline",
                    "precondition": "App 首页",
                    "flow": [
                        "等待 App 首页稳定显示扫描复印入口",
                        "点击扫描复印入口",
                        "点击证件扫描入口",
                        "等待扫描复印页面展示云盘入口",
                    ],
                    "assertionTarget": "云盘入口文案准确且与同页导入入口同级展示",
                    "requirementRefs": [requirement_point],
                    "executableReason": "复用同分支成功路径补齐可见文字导航",
                    "batch": "remaining",
                }],
                "needs_review_cases": [],
                "draft_cases": [],
                "manual_cases": [],
                "review": {"planning_reason": "候选身份保真重放"},
            }

        ai_skill_service.run_ai_skill = fake_planner
        identity_plan = ai_skill_service.call_skill_executable_yaml_planner(
            "扫描复印新增入口",
            "基础打印",
            evidence_filtered,
            [{
                "id": "scan-success-baseline",
                "title": "扫描复印稳定导航",
                "sourceKind": "verified_execution",
                "verificationStatus": "execution_success",
                "businessPath": "App 首页 -> 扫描复印 -> 证件扫描",
            }],
            {"smokeCount": 3},
        )
    finally:
        ai_skill_service.run_ai_skill = original_run_ai_skill
    require(
        planner_requests
        and any(
            item.get("case_id") == "TC-005"
            for item in planner_requests[0].get("cases") or []
        )
        and identity_plan.get("trace", {}).get("rejected_case_count") == 0
        and identity_plan.get("cases", [{}])[0].get("caseId") == "TC-005",
        "The AI planner must ground its TC id back to the preserved source candidate instead of rejecting it",
    )
    identity_applied = ai_skill_service.apply_executable_yaml_plan_to_payload(
        evidence_filtered,
        identity_plan,
    )
    restored_candidate = next(
        item for item in identity_applied.get("cases") or []
        if item.get("case_id") == "TC-005"
    )
    require(
        restored_candidate.get("executionLevel") == "executable"
        and restored_candidate.get("originExecutionLevel") == "automatic"
        and restored_candidate.get("ai_case_plan", {}).get("baselineVerified") is True
        and restored_candidate.get("goal") == "云盘入口文案准确且与同页导入入口同级展示"
        and restored_candidate.get("data_requirements") is None
        and (restored_candidate.get("previous_manual_context") or {}).get("goal") == source_candidate["goal"]
        and identity_applied.get("review", {}).get("executable_yaml_plan", {}).get(
            "manual_reclassification_canonicalized_count"
        ) == 1,
        "A grounded AI path must atomically replace stale manual execution conditions when it restores an automatic candidate",
    )
    for ordered_cases in (
        list(identity_applied.get("cases") or []),
        list(reversed(identity_applied.get("cases") or [])),
    ):
        replay_payload = json.loads(json.dumps(identity_applied, ensure_ascii=False))
        replay_payload["cases"] = ordered_cases
        replay_filtered = yaml_service.split_automation_ready_cases(replay_payload)
        _, replay_files = yaml_service.cases_to_separate_midscene_yamls(
            replay_filtered,
            app_package="com.xbxxhz.box",
        )
        require(
            "TC-005" in {item.get("case_id") for item in replay_filtered.get("cases") or []}
            and "TC-005" not in {item.get("case_id") for item in replay_filtered.get("manual_cases") or []},
            "Runner eligibility must honor the current grounded AI contract regardless of candidate order instead of reviving stale manual metadata",
        )
        require(
            {item.get("case_id") for item in replay_files}
            == {item.get("case_id") for item in replay_filtered.get("cases") or []},
            "Every candidate retained after AI reclassification must reach the split YAML conversion output",
        )

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


def check_agent_cancel_cascades_runner_jobs():
    from task_server.services import agent_service, job_service

    jobs = [
        {"job_id": "job-pending", "parent_run_id": "agent-cancel", "status": "pending"},
        {"job_id": "job-dispatched", "parent_run_id": "agent-cancel", "status": "dispatched"},
        {"job_id": "job-running", "parent_run_id": "agent-cancel", "status": "running"},
        {"job_id": "job-success", "parent_run_id": "agent-cancel", "status": "success"},
        {"job_id": "job-other", "parent_run_id": "agent-other", "status": "running"},
    ]
    updates = []
    original_load = job_service.load_jobs
    original_update = job_service.update_job

    def fake_update(job_id, patch):
        updates.append((job_id, dict(patch)))
        source = next(item for item in jobs if item.get("job_id") == job_id)
        return {**source, **patch}

    job_service.load_jobs = lambda limit=None: [dict(item) for item in jobs]
    job_service.update_job = fake_update
    try:
        cancelled = agent_service._agent_cancel_runner_jobs("agent-cancel", "parent cancelled")
    finally:
        job_service.load_jobs = original_load
        job_service.update_job = original_update

    require(
        cancelled == ["job-pending", "job-dispatched", "job-running"]
        and [item[0] for item in updates] == cancelled,
        "Agent cancellation must cancel every active child Runner job and leave terminal or unrelated jobs untouched",
    )
    require(
        all(patch.get("status") == "cancelled" and patch.get("cancelled_by") == "agent_run" for _, patch in updates),
        "Cascaded Runner cancellation must persist a real cancelled status and source",
    )


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


def check_agent_run_snapshot_concurrent_persistence():
    from concurrent.futures import ThreadPoolExecutor
    from task_server import storage
    from task_server.services import agent_service

    old_agent_runs_file = agent_service.AGENT_RUNS_FILE
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_file = os.path.join(temp_dir, "agent-runs.json")
            agent_service.AGENT_RUNS_FILE = run_file
            storage.write_json_file(run_file, {
                "runs": [
                    {"runId": "agent-history-a", "status": "DONE"},
                    {"runId": "agent-history-b", "status": "FAILED"},
                ]
            })
            live_runs = [
                {
                    "runId": f"agent-live-{index:02d}",
                    "status": "RUNNING",
                    "steps": [{"step": "GENERATE_YAML", "liveTrace": [{"message": "x" * 2000}]}],
                }
                for index in range(16)
            ]
            with ThreadPoolExecutor(max_workers=8) as executor:
                list(executor.map(agent_service._persist_agent_run_snapshot, live_runs))
            persisted = storage.read_json_file(run_file, default={}).get("runs") or []
            persisted_ids = {item.get("runId") for item in persisted}
            require(
                {"agent-history-a", "agent-history-b"}.issubset(persisted_ids)
                and {item["runId"] for item in live_runs}.issubset(persisted_ids),
                "Concurrent Agent snapshots must preserve history and upsert every current run",
            )

            shared_file = os.path.join(temp_dir, "shared-state.json")

            def write_shared(index):
                storage.write_json_file(shared_file, {"writer": index, "payload": "y" * 200000})

            with ThreadPoolExecutor(max_workers=12) as executor:
                list(executor.map(write_shared, range(24)))
            shared = storage.read_json_file(shared_file, default={})
            require(shared.get("writer") in range(24), "Concurrent atomic JSON writes must leave a complete valid document")
            require(
                not list(Path(temp_dir).glob(".shared-state.json.tmp.*")),
                "Unique JSON temporary files must be consumed after successful atomic writes",
            )
    finally:
        agent_service.AGENT_RUNS_FILE = old_agent_runs_file


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
    require(
        "AI_GATEWAY_URL" in ai_skill_source
        and "ai_gateway_skill_content" in ai_skill_source
        and "image_assets=image_assets" in ai_skill_source
        and "fallbackModelConfig" in ai_skill_source
        and "禁止静默切换到平台直连视觉模型" in ai_skill_source,
        "Text and image AI skills must use Gateway, with an explicit audited vision fallback and no silent direct-model switch",
    )
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
    require("search_diverse_baseline_examples" in yaml_service_source and "baseline_branch_queries" in yaml_service_source and "limit=20" in yaml_service_source, "YAML generation must build a bounded branch-diverse candidate pool for AI reranking")
    require("call_skill_baseline_reranker" in yaml_service_source and "limit=3" in yaml_service_source, "YAML generation must let AI select the Top3 complementary branch/path examples")
    require("baseline_required_branches_from_agent_plan" in yaml_service_source and "required_branches=baseline_required_branches" in yaml_service_source, "Multi-branch generation must ground Top3 baseline coverage in AI-selected smoke flows")
    require("call_skill_execution_scope_planner" in yaml_service_source, "YAML generation must call AI execution scope planner")
    require("call_skill_executable_yaml_planner" in yaml_service_source, "YAML generation must call AI executable YAML planner")
    require("build_ai_generation_decision_context_text" in yaml_service_source and "AI 生成决策计划" in yaml_service_source, "YAML prompt must include the AI decision plan context")
    require("ai_decision_trace" in yaml_service_source and "executable_yaml_planner_review" in yaml_service_source, "YAML generation review must expose AI decision trace and planner review")
    require("executable_yaml_portfolio_audit" in yaml_service_source and '"pass": "coverage_convergence"' in yaml_service_source and 'step="最终覆盖收敛"' in yaml_service_source and 'step="最终覆盖门禁"' in yaml_service_source and 'convergence_plan.get("evidenceFallback") is True' in yaml_service_source and "最终可执行 YAML 覆盖门禁未通过" in yaml_service_source, "YAML generation must run one AI convergence pass, apply only validated evidence fallback when that pass is unavailable, and hard-stop incomplete final coverage before conversion")
    require("improve_case_coverage(" in yaml_service_source and "model_config=model_config" in yaml_service_source, "Coverage repair must receive selected model config")

    require("def call_skill_baseline_reranker" in ai_skill_source, "AI skill service must expose baseline reranker")
    require("selectionValidationIssues" in ai_skill_source and "branch_repair_attempted" in ai_skill_source and "branch_coverage_ok" in ai_skill_source, "AI baseline reranking must self-correct one branch-incomplete Top3 result without expanding the prompt beyond three examples")
    require("def call_skill_execution_scope_planner" in ai_skill_source, "AI skill service must expose execution scope planner")
    require("def call_skill_executable_yaml_planner" in ai_skill_source, "AI skill service must expose executable YAML planner")
    require("def apply_executable_yaml_plan_to_payload" in ai_skill_source, "Executable YAML planner output must be applied to generated payload")
    require("path_mapping_guard_count" in ai_skill_source and "pathMappingGuarded" in ai_skill_source and "def executable_yaml_portfolio_audit" in ai_skill_source, "Executable planning must reject cross-requirement path swaps and audit the final portfolio")
    require('"targetShortfall"' in ai_skill_source and "数量目标不作为硬门禁" in ai_skill_source, "Executable coverage must report 3/5/8 target shortfalls without forcing low-value Runner cases")
    require("def compact_visual_grounder_base_payload" in ai_skill_source and "def merge_visual_grounder_payload" in ai_skill_source and "visual_input_compaction" in ai_skill_source, "Visual grounding must compact repeated history while preserving and merging design evidence")
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


def check_agent_summary_separates_runner_outcomes_from_orchestration():
    from task_server.services import agent_service

    run = {
        "runId": "agent-static-partial-outcome",
        "status": "FAILED",
        "target": "基础打印新增百度网盘入口",
        "steps": [
            {"step": "RUN_TASK", "status": "PARTIAL_FAILED", "summary": "扩展任务失败"},
            {"step": "GENERATE_SUMMARY", "status": "RUNNING"},
        ],
        "artifacts": {
            "jobProgressByPhase": {
                "首批冒烟": {
                    "phase": "首批冒烟",
                    "total": 3,
                    "completed": 2,
                    "failed": 1,
                    "running": 0,
                    "timeout": 1800,
                    "jobs": [
                        {"job_id": "smoke-1", "status": "success"},
                        {"job_id": "smoke-2", "status": "success"},
                        {"job_id": "smoke-3", "status": "failed"},
                    ],
                },
            },
            "report": {
                "status": "failed",
                "jobStatuses": [
                    {"jobId": "smoke-1", "status": "success"},
                    {"jobId": "smoke-2", "status": "success"},
                    {"jobId": "smoke-3", "status": "failed"},
                ],
                "successJobs": [
                    {"jobId": "smoke-1", "status": "success"},
                    {"jobId": "smoke-2", "status": "success"},
                ],
                "failedJobs": [{"jobId": "smoke-3", "status": "failed", "failureType": "SCRIPT_ISSUE", "error": "定位失败"}],
                "timeoutJobs": [],
                "runningJobs": [],
            },
        },
    }
    execution = agent_service._agent_runner_execution_summary(run)
    require(execution.get("outcome") == "partial", "Runner results with passes and failures must be partial, not blanket failed")
    require(execution.get("passedCount") == 2 and execution.get("failedCount") == 1, "Runner summary must preserve successful smoke counts")
    require(execution.get("productFailedCount") == 0 and execution.get("brokenCount") == 1, "Script/environment failures must remain Broken instead of product failures")
    require(execution.get("timeoutCount") == 0, "Runner wait limit seconds must not be counted as timed-out jobs")

    old_health = agent_service._ai_gateway_available
    old_log = agent_service._log_tool_call
    try:
        agent_service._ai_gateway_available = lambda: False
        agent_service._log_tool_call = lambda *_args, **_kwargs: None
        agent_service._tool_generate_summary(run)
    finally:
        agent_service._ai_gateway_available = old_health
        agent_service._log_tool_call = old_log
    summary = (run.get("artifacts") or {}).get("summary") or {}
    require(summary.get("conclusion") == "部分通过", "Final summary must expose partial Runner success")
    require((summary.get("orchestration") or {}).get("label") == "编排阻断", "Final summary must separately expose Agent orchestration blocking")

    from task_server.services import job_service
    old_load_jobs = job_service.load_jobs
    try:
        job_service.load_jobs = lambda: [
            {"job_id": "original-pass-1", "status": "success", "phase": "smoke"},
            {"job_id": "original-pass-2", "status": "success", "phase": "smoke"},
            {"job_id": "original-fail", "status": "failed", "phase": "expanded-1", "failure_type": "SCRIPT_ISSUE"},
            {"job_id": "repair-fail-1", "status": "failed", "error": "failed to locate element 'undefined'"},
            {"job_id": "repair-fail-2", "status": "failed", "error": "failed to locate element 'undefined'"},
            {"job_id": "repair-fail-3", "status": "failed", "error": "failed to locate element 'undefined'"},
        ]
        retry_run = {
            "runId": "agent-static-attempt-ledger",
            "status": "RUNNING",
            "target": "通用入口回归",
            "steps": [
                {"step": "RUN_SONIC", "status": "PARTIAL_FAILED", "summary": "部分失败"},
                {"step": "RERUN", "status": "FAILED", "summary": "AI 修复重跑未通过"},
                {"step": "GENERATE_SUMMARY", "status": "RUNNING"},
            ],
            "artifacts": {
                "jobIds": ["original-pass-1", "original-pass-2", "original-fail"],
                "rerunAttempts": [
                    {"createdJobIds": ["repair-fail-1", "repair-fail-2"], "completedCount": 0, "failedCount": 2},
                    {"createdJobIds": ["repair-fail-3"], "completedCount": 0, "failedCount": 1},
                ],
                "report": {
                    "successJobs": [
                        {"jobId": "original-pass-1", "status": "success"},
                        {"jobId": "original-pass-2", "status": "success"},
                    ],
                    "failedJobs": [{"jobId": "original-fail", "status": "failed", "failureType": "SCRIPT_ISSUE"}],
                },
            },
        }
        retry_execution = agent_service._agent_runner_execution_summary(retry_run)
        require(
            retry_execution.get("attemptedCount") == 6
            and retry_execution.get("originalAttemptCount") == 3
            and retry_execution.get("rerunAttemptCount") == 3,
            "Final Runner totals must include every original formal job and every bounded AI repair rerun attempt",
        )
        require(
            retry_execution.get("passedCount") == 2
            and retry_execution.get("failedCount") == 4
            and retry_execution.get("brokenCount") == 4,
            "Successful smoke attempts must remain visible while script failures and repair failures stay classified as Broken",
        )
        try:
            agent_service._ai_gateway_available = lambda: False
            agent_service._log_tool_call = lambda *_args, **_kwargs: None
            agent_service._tool_generate_summary(retry_run)
        finally:
            agent_service._ai_gateway_available = old_health
            agent_service._log_tool_call = old_log
        retry_summary = retry_run["artifacts"].get("summary") or {}
        retry_orchestration = retry_summary.get("orchestration") or {}
        require(
            retry_summary.get("runnerAttemptCount") == 6
            and retry_summary.get("passedJobCount") == 2
            and retry_summary.get("brokenJobCount") == 4,
            "Final report must expose both actual attempt count and preserved pass/broken counts",
        )
        require(
            retry_orchestration.get("runStatus") == "FAILED"
            and retry_orchestration.get("observedRunStatus") == "RUNNING"
            and retry_orchestration.get("statusProjectedAtSummary") is True,
            "Summary generation must project the deterministic final Agent state instead of persisting a stale RUNNING status",
        )

        job_service.load_jobs = lambda: [
            {"job_id": "recovered-source", "status": "failed", "failure_type": "SCRIPT_ISSUE", "phase": "smoke"},
            {"job_id": "recovered-repair", "status": "success", "phase": "safe-rerun"},
            {"job_id": "recovered-expanded", "status": "success", "phase": "recovered-expanded-1"},
        ]
        recovered_run = {
            "runId": "agent-static-recovered-summary",
            "status": "RUNNING",
            "steps": [
                {"step": "RUN_SONIC", "status": "FAILED", "summary": "首次冒烟失败", "error": "断言过严"},
                {"step": "COLLECT_REPORT", "status": "PARTIAL_FAILED", "summary": "原始报告含失败"},
                {"step": "RERUN", "status": "RUNNING"},
                {"step": "GENERATE_SUMMARY", "status": "RUNNING"},
            ],
            "artifacts": {
                "jobIds": ["recovered-source", "recovered-expanded"],
                "rerunAttempts": [{"createdJobIds": ["recovered-repair"]}],
                "rerunSources": [{"sourceJobId": "recovered-source", "newJobId": "recovered-repair"}],
                "runnerExecutionGate": {"enabled": True, "remainingDeferredCount": 0, "remainingDeferred": []},
                "failedExecutionItems": [{
                    "jobId": "recovered-source",
                    "failureType": "SCRIPT_ISSUE",
                    "failureReason": "首次断言过严",
                }],
                "report": {
                    "status": "failed",
                    "failedJobs": [{"jobId": "recovered-source", "status": "failed", "failureType": "SCRIPT_ISSUE"}],
                    "successJobs": [
                        {"jobId": "recovered-repair", "status": "success"},
                        {"jobId": "recovered-expanded", "status": "success"},
                    ],
                },
            },
        }
        recovered_execution = agent_service._agent_runner_execution_summary(recovered_run)
        require(
            recovered_execution.get("outcome") == "passed"
            and recovered_execution.get("label") == "修复后通过"
            and recovered_execution.get("attemptedCount") == 3
            and recovered_execution.get("passedCount") == 2
            and recovered_execution.get("failedCount") == 1
            and recovered_execution.get("logicalPassedCount") == 2
            and recovered_execution.get("recoveredCount") == 1
            and not recovered_execution.get("unresolvedFailedJobIds"),
            "A passed repair descendant must recover the logical case without erasing the original failed attempt from raw totals",
        )
        agent_service._agent_mark_recovered_execution_steps(recovered_run, recovered_execution)
        try:
            agent_service._ai_gateway_available = lambda: False
            agent_service._log_tool_call = lambda *_args, **_kwargs: None
            agent_service._tool_generate_summary(recovered_run)
        finally:
            agent_service._ai_gateway_available = old_health
            agent_service._log_tool_call = old_log
        recovered_summary = recovered_run["artifacts"].get("summary") or {}
        require(
            recovered_summary.get("conclusion") == "修复后通过"
            and recovered_summary.get("recoveredJobCount") == 1
            and recovered_summary.get("failedJobCount") == 1
            and not recovered_summary.get("failedTasks")
            and (recovered_summary.get("orchestration") or {}).get("runStatus") == "DONE"
            and all(step.get("status") == "SUCCESS" for step in recovered_run["steps"][:2]),
            "Recovered orchestration must finish while preserving the raw failed-attempt count and clearing only resolved active failures",
        )
    finally:
        job_service.load_jobs = old_load_jobs

    blocked_without_runner = {
        "status": "FAILED",
        "steps": [{"step": "GENERATE_YAML", "status": "FAILED", "error": "覆盖门禁阻断"}],
        "artifacts": {},
    }
    try:
        agent_service._ai_gateway_available = lambda: False
        agent_service._log_tool_call = lambda *_args, **_kwargs: None
        agent_service._tool_generate_summary(blocked_without_runner)
    finally:
        agent_service._ai_gateway_available = old_health
        agent_service._log_tool_call = old_log
    no_runner_summary = (blocked_without_runner.get("artifacts") or {}).get("summary") or {}
    require(no_runner_summary.get("conclusion") == "未执行", "Coverage blocking before dispatch must be reported as not executed")
    require((no_runner_summary.get("execution") or {}).get("attemptedCount") == 0, "A pre-dispatch failure must not invent Runner attempts")


def main():
    check_ai_gateway_response_diagnostics()
    check_automation_filter_invalid_json_self_repair()
    check_report_image_context_uses_midscene_execution_refs()
    check_runner_inline_android_device_injection()
    check_midscene_model_family_protocol()
    check_agent_summary_separates_runner_outcomes_from_orchestration()
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
    require("def _business_flow_keywords" in agent_service_source and '"businessFlowKeywords"' in agent_service_source and "AI 业务计划（PLAN 前仅为未验证候选）" in agent_service_source, "Agent case matching must use AI-plan keywords before widening retrieval")
    require("def _keyword_source_text" in agent_service_source and "CASE_MATCH_META_KEYWORD_PARTS" in agent_service_source and 'constraint.get("source") or "") in ("default", "unverified_input")' in agent_service_source, "Agent keyword extraction must ignore platform metadata and unverified flow placeholders")
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
    require(
        '"repair_patch_planner"' in agent_service_source
        and '"task_block": task_info.get("block")' in agent_service_source
        and "repair_service.apply_task_repair_patches" in agent_service_source
        and "_agent_repair_candidate_gate(" in agent_service_source
        and '"repairSource"] = "ai_skill_patch"' in agent_service_source,
        "Agent repair must ask the selected model for a local patch, apply it to the real failed task, and retain platform gates",
    )
    require('"repairSummary"' in agent_service_source and '"aiAttempted"' in agent_service_source and '"aiUsed"' in agent_service_source and '"evidenceSources"' in agent_service_source, "Agent repair draft must expose evidence, AI usage, and validation summary")
    require("def _agent_failed_execution_items" in agent_service_source and '"failedExecutionItems"' in agent_service_source, "Agent failure, repair, and rerun steps must share one failed-task source of truth")
    require('"failedTaskCount"' in agent_service_source and '"repairTargetCount"' in agent_service_source and '"draftCount"' in agent_service_source, "Agent repair summary must expose batch scope and draft counts")
    require('"sourceFailedCount"' in agent_service_source and '"targetCount"' in agent_service_source and '"rerunSources"' in agent_service_source, "Agent rerun must expose source failed count, target count, and rerun mappings")
    require('"rerunProgress"' in agent_service_source and '"learningSummary"' in agent_service_source, "Agent rerun and learning steps must persist readable timeline summaries")
    require("def _agent_prepare_repair_rerun_targets" in agent_service_source and '"usesRepairDraft"' in agent_service_source and '"notRerunOriginalYaml"' in agent_service_source, "Agent safe rerun must materialize repair drafts and avoid silently rerunning old YAML")
    require("def _agent_post_rerun_autonomy" in agent_service_source and '"maxRepairCycles": 1' in agent_service_source and "repair_depth < 1" in agent_service_source, "Agent must use latest rerun evidence for one bounded AI repair cycle without an unbounded retry loop")
    require(
        "已有修复草稿但没有可执行 YAML" in agent_service_source
        and "AI 未生成通过门禁的修复 YAML，禁止原样重跑" in agent_service_source,
        "Agent safe rerun must explain missing or invalid repair drafts instead of reporting false success",
    )
    require(
        '"PREPARE_SOURCE", "PLAN", "IMPACT_ANALYSIS", "CASE_RETRIEVAL", "MATCH_CASES"' in agent_service_source,
        "Agent step order must prepare source before AI planning, then analyze impact, retrieve cases, and match cases"
    )
    require(
        "generate_mindmap_from_request" in agent_service_source
        and '"requireAiPlanning": True' in agent_service_source
        and '"useYamlBaselineContext": True' in agent_service_source
        and '"source": "platform_mindmap_ai"' in agent_service_source,
        "Agent PLAN must reuse the platform MM skill pipeline instead of a standalone chat fallback",
    )
    require(
        "prepared_figma_context = _prepared_figma_context_from_request(d)" in yaml_service_source
        and 'agent_plan_review = {"agent_ai_planning_required": require_ai_planning}' in yaml_service_source
        and 'review["agent_mindmap_plan_reused"]' in yaml_service_source,
        "Platform MM planning must reuse prepared Figma and pass its structured cases into YAML generation",
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
    check_agent_generation_pipeline_preserves_selected_ai_model()
    check_agent_regression_scope_preserves_new_requirement_generation()
    check_generated_yaml_short_guards_and_execution_level_floor()
    check_generated_yaml_semantic_scope_and_visual_trace()
    check_agent_blocks_incomplete_generated_yaml_coverage()
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
    require(
        "has_stable_smoke_candidate" in agent_service_source
        and "max_action_count <= 12" in agent_service_source
        and "max_wait_count <= 6" in agent_service_source
        and "max_transition_count <= 2" in agent_service_source
        and "min_assert_count >= 1" in agent_service_source
        and 'replanRisk") or "") == "high"' in agent_service_source,
        "Agent must reuse a bounded asserted short case as smoke while still rejecting long or high-replan YAML",
    )
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
    require(
        "base_payload = normalize_cases_payload(base_payload)" in ai_skill_service_source
        and "grounded = copy.deepcopy(grounded_payload)" in ai_skill_service_source
        and "return normalize_cases_payload(merged)" in ai_skill_service_source,
        "Visual grounding must accept sparse deltas while normalizing the final merged payload",
    )
    require("def _run_ai_skill_call_with_hard_timeout" in ai_skill_service_source and "future.result(timeout=timeout_seconds)" in ai_skill_service_source and "executor.shutdown(wait=False, cancel_futures=True)" in ai_skill_service_source, "Text AI skills must have a hard timeout around AI Gateway calls")
    require("except TimeoutError:" in ai_skill_service_source and 'f"AI Gateway skill {skill_name}"' in ai_skill_service_source, "AI Gateway skill timeout must surface to requirement/scenario fallbacks instead of falling into another long provider call")
    require("respect_global_timeout=timeout_seconds is None" in ai_skill_service_source and "retry_count=None if timeout_seconds is None else 0" in ai_skill_service_source, "Short visual grounding timeouts must bypass the global long AI timeout")
    require("AGENT_GENERATE_YAML_TIMEOUT_SECONDS = max(300, env_int(\"MIDSCENE_AGENT_GENERATE_YAML_TIMEOUT_SECONDS\", 900))" in yaml_service_source and 'job_type == "agent_generate_yaml"' in yaml_service_source, "Agent YAML generation must use a bounded Agent-specific timeout")
    require("expire_generate_job_if_stale" in agent_service_source and "timeout_seconds=900" in agent_service_source, "Agent YAML generation watcher must expire stale generation jobs instead of leaving Agent stuck")
    require(
        "def _fallback_business_flows_from_text" in agent_service_source
        and 'checks = [f"校验{entry_label}入口可见"]' in agent_service_source
        and 'steps.append(f"进入{branch}")' in agent_service_source
        and '"checks": list(checks)' in agent_service_source
        and '"source": "requirement_candidates" if business_flows else "unverified_input"' in agent_service_source
        and '"candidateOnly": True' in agent_service_source,
        "Agent must preserve explicit requirement branches as unverified coverage candidates and keep navigation steps separate from visible checks",
    )
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
        and "产品断言失败或页面状态不匹配仍保留为真实测试结果" in yaml_execution_plan_source
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
    check_qwen_structured_skills_disable_thinking()
    check_ai_skill_timeout_fallbacks_are_requirement_scoped()
    check_smoke_selection_requires_explicit_ai_mark()
    check_yaml_runner_eligibility_filter()
    check_agent_yaml_validate_partial_quarantine()
    check_agent_yaml_validate_auto_repairs_missing_wait()
    check_agent_quarantine_refs_do_not_reenter_precheck()
    check_agent_execution_gate_repairs_before_smoke_selection()
    check_agent_runner_failure_reason_summary()
    check_agent_failure_ai_payload_has_primary_evidence()
    check_agent_ai_owned_plan_and_evidence_loop()
    check_agent_failure_review_and_repair_guard()
    check_agent_quality_report_uses_figma_visual_reference()
    check_agent_figma_context_defaults()
    check_agent_high_risk_confirm_resumes_precheck()
    check_agent_completed_tool_step_recovers_and_avoids_hot_cancel_reads()
    check_agent_cancel_cascades_runner_jobs()
    check_agent_history_compacts_uploaded_blobs_after_prepare()
    check_agent_worker_start_is_idempotent()
    check_snapshot_store_concurrent_save()
    check_agent_run_snapshot_concurrent_persistence()
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
    require("def cancel_agent_run(run_id, reason=" in agent_service_source and 'run["currentStep"] = "CANCELLED"' in agent_service_source and "_agent_cancel_progress_job" in agent_service_source and "_agent_cancel_runner_jobs" in agent_service_source, "Agent cancellation must mark a real cancelled state and cancel internal generation and Runner jobs")
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
    require(
        'MIDSCENE_MINDMAP_VISUAL_BATCH_SIZE", 1' in config_source
        and 'MIDSCENE_MINDMAP_VISUAL_TIMEOUT_SECONDS", 90' in config_source
        and 'MIDSCENE_MINDMAP_VISUAL_TOTAL_BUDGET_SECONDS", 360' in config_source
        and 'MIDSCENE_MINDMAP_VISUAL_BATCH_SIZE" "1"' in deploy_install
        and "MIDSCENE_MINDMAP_VISUAL_BATCH_SIZE='1'" in env_example,
        "Mindmap visual grounding must use short single-image calls after repeated multi-image timeouts",
    )
    require(
        "mindmap_visual_batch_results" in yaml_service_source
        and "mindmap_visual_batches_attempted" in yaml_service_source
        and "bounded_retry=True" in yaml_service_source
        and '"attemptCount"' in yaml_service_source
        and '"judgement"' in yaml_service_source
        and "已记录并继续下一批" in yaml_service_source,
        "Mindmap visual grounding must retry inside the batch budget, preserve every outcome and continue after one soft-reference failure",
    )
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
    ambiguous_tap_yaml = "android:\n  tasks:\n    - name: demo\n      flow:\n        - aiTap: 点击「5寸照片」或「一寸照」等任一照片规格\n"
    ambiguous_tap = validate_midscene_yaml_executability(ambiguous_tap_yaml)
    require(
        not ambiguous_tap.get("ok") and "多个备选目标" in "；".join(ambiguous_tap.get("issues") or []),
        "One aiTap must name one visible target; alternative outcomes belong in waits or assertions",
    )
    sequential_tap_yaml = "android:\n  tasks:\n    - name: demo\n      flow:\n        - aiTap: 依次进入文档打印、照片打印和扫描复印页面\n"
    sequential_tap = validate_midscene_yaml_executability(sequential_tap_yaml)
    require(
        not sequential_tap.get("ok") and "多个备选目标" in "；".join(sequential_tap.get("issues") or []),
        "One aiTap must not hide multiple sequential business branches",
    )
    from task_server.services import yaml_service as yaml_service_module
    original_static_repair_gateway = yaml_service_module.ai_gateway_skill_content
    static_repair_called = []
    try:
        yaml_service_module.ai_gateway_skill_content = lambda *_args, **_kwargs: static_repair_called.append(True) or "{}"
        ambiguous_static_repair = yaml_service_module.repair_generated_yaml_static_errors(
            ambiguous_tap_yaml,
            module="AI测试",
            file="ambiguous.yaml",
            max_attempts=1,
            model_config={"providerId": "qwen_plus", "model": "qwen3.6-plus"},
        )
    finally:
        yaml_service_module.ai_gateway_skill_content = original_static_repair_gateway
    require(
        not ambiguous_static_repair.get("ok")
        and not static_repair_called
        and any(item.get("type") == "semantic_planning_guard" for item in ambiguous_static_repair.get("attempts") or []),
        "Static repair must not choose one alternative branch or split one ambiguous tap into sequential business actions",
    )
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
    require('"neither android_home nor android_sdk_root" in lowered' in agent_source and "_agent_failed_item_has_concrete_environment_evidence" in agent_source and "not environment_locked" in agent_source, "Agent must lock concrete Android SDK/ADB failures as ENV_ISSUE while allowing bare timeouts to use AI keyframe reclassification")
    require('POST_FAILURE_ANALYSIS_STEPS = ("RUN_SONIC",)' in agent_source, "RUN_SONIC failure must continue into report collection, failure analysis and repair planning")
    require("AGENT_PLAN_MINDMAP_TIMEOUT_SECONDS" in agent_source, "Agent PLAN must have its own bounded MM planning timeout instead of relying only on shared job expiry")
    require("_run_agent_call_with_hard_timeout" in agent_source and "executor.shutdown(wait=False, cancel_futures=True)" in agent_source, "Agent hard timeout wrapper must stop waiting for stuck planning calls without blocking on executor shutdown")
    require("generate_mindmap_from_request(" in agent_source and "_run_agent_call_with_hard_timeout(" in agent_source and "PLAN AI业务规划" in agent_source, "Agent PLAN must wrap platform mindmap generation in a hard timeout")
    yaml_source = (ROOT / "task_server" / "services" / "yaml_service.py").read_text(encoding="utf-8")
    require('agent_config.setdefault("screenshotShrinkFactor", 2)' in yaml_source, "Android Runner temporary YAML must pre-shrink mobile screenshots for stable Midscene coordinate mapping")
    require("quality_eval" in yaml_source and "evaluate_baseline_template_matching" in yaml_source, "YAML generation review must include template matcher quality eval")
    from task_server.services.yaml_executable_scorer import rank_executable_yaml_refs, score_midscene_yaml_executable
    conditional_runner_yaml = """android:
  tasks:
    - name: 扫描复印页-百度网盘入口UI展示与文案校验（待确认UI稿）
      flow:
        - aiWaitFor: 被测 App 首页已加载完成，首页核心功能入口可见
        - aiTap: 点击「扫描复印」入口
        - aiWaitFor: 检查页面中是否存在「百度网盘」入口
        - aiWaitFor: 若存在，检查文案是否为“百度网盘”
        - aiWaitFor: 记录入口的具体位置和样式
        - aiAssert: 「百度网盘」入口可见，文案为“百度网盘”，与同级入口并列，或确认该页面无此入口
"""
    conditional_score = score_midscene_yaml_executable(conditional_runner_yaml, generated=True)
    conditional_ranked, conditional_blocked = rank_executable_yaml_refs([{
        "file": "06-扫描复印页-百度网盘入口UI展示与文案校验（待确认UI稿）.yaml",
        "executableScore": conditional_score,
    }])
    require(
        conditional_score.get("executionLevel") != "executable"
        and not conditional_ranked
        and conditional_blocked,
        "Generated YAML with 若存在/待确认/or-confirm-absent manual branches must not enter Runner smoke",
    )
    env_example = ENV_EXAMPLE.read_text(encoding="utf-8")
    require("TASK_APP_ENV='prod'" in env_example and "TASK_ALLOW_QUERY_TOKEN='0'" in env_example, "Env example must document production mode and disabled query token auth")
    for module_path in ["task_server/config.py", "task_server/auth.py", "task_server/storage.py", "task_server/repair_service.py", "task_server/sonic_service.py"]:
        require((ROOT / module_path).exists(), f"Backend service skeleton missing: {module_path}")
    storage_source = (ROOT / "task_server" / "storage.py").read_text(encoding="utf-8")
    require("write_json_atomic" in storage_source and "os.replace(tmp, target)" in storage_source, "Storage skeleton must provide atomic JSON writes")
    print({"ok": True, "file": str(MODULE), "checks": 61})


if __name__ == "__main__":
    main()
