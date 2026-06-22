#!/usr/bin/env python3
import importlib.util
import base64
import os
import sys
import tempfile
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


def check_agent_fallback_yaml_auto_confirm_split():
    from task_server.services import agent_service

    old_task_dir = agent_service.TASK_DIR
    old_draft_dir = agent_service.AGENT_DRAFT_DIR
    yaml_text = """android:
  tasks:
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
            validation = artifacts.get("yamlValidation") or {}
            require(validation.get("ok") and validation.get("autoConfirmedFallback"), "Split fallback YAML must be marked as auto-confirmed fallback")
    finally:
        agent_service.TASK_DIR = old_task_dir
        agent_service.AGENT_DRAFT_DIR = old_draft_dir


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


def main():
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
    nginx_conf = (ROOT / "deploy" / "nginx-midscene-task.conf").read_text(encoding="utf-8")
    env_example = (ROOT / "deploy" / "midscene.env.example").read_text(encoding="utf-8")
    require("300 * 1024 * 1024" in source and "_limit_mb(limit)" in source, "Backend request body limit must default to 300MB and show the active limit")
    require("client_max_body_size 300m" in nginx_conf, "Nginx template must allow 300MB uploads")
    require("NGINX_CLIENT_MAX_BODY_SIZE=\"${NGINX_CLIENT_MAX_BODY_SIZE:-300m}\"" in deploy_install and "midscene-upload-size.conf" in deploy_install, "Installer must apply 300MB Nginx upload override")
    require("find /etc/nginx -type f" in deploy_install and "s/client_max_body_size[[:space:]][^;]*;" in deploy_install, "Installer must replace older Nginx client_max_body_size values")
    require("TASK_MAX_BODY_SIZE\" \"314572800" in deploy_install and "TASK_MAX_UPLOAD_BODY_SIZE\" \"314572800" in deploy_install, "Installer must set backend upload body limits to 300MB")
    require("TASK_MAX_BODY_SIZE='314572800'" in env_example and "TASK_MAX_UPLOAD_BODY_SIZE='314572800'" in env_example, "Environment example must document 300MB upload limits")
    require("SONIC_CALLBACK_TOKEN" in source and "query token auth is deprecated" in source, "Sonic callback auth must be separated and query token deprecated")
    require('TOKEN = os.getenv("MIDSCENE_RUNNER_TOKEN", "").strip()' in source or 'MIDSCENE_RUNNER_TOKEN", ""' in source, "Runner token must not default to midscene2026")
    require('SONIC_CALLBACK_TOKEN = os.getenv("SONIC_CALLBACK_TOKEN", "").strip()' in source or 'SONIC_CALLBACK_TOKEN", ""' in source, "Sonic callback token must not default to runner token")
    require('TASK_SESSION_SECRET = os.getenv("TASK_SESSION_SECRET", "").strip()' in source or 'TASK_SESSION_SECRET", ""' in source, "Session secret must not default to runner token")
    require("TASK_ALLOW_QUERY_TOKEN" in source and "ALLOW_QUERY_TOKEN" in source and "if not ALLOW_QUERY_TOKEN" in source, "Query token auth must be disabled unless explicitly enabled")
    require("validate_runtime_secrets()" in source and "TASK_ADMIN_PASSWORD_HASH 未配置" in source, "Production startup must validate strong secrets and admin password hash")
    router_source = (ROOT / "task_server" / "router.py").read_text(encoding="utf-8")
    job_service_source = (ROOT / "task_server" / "services" / "job_service.py").read_text(encoding="utf-8")
    sonic_service_source = (ROOT / "task_server" / "services" / "sonic_service.py").read_text(encoding="utf-8")
    yaml_service_source = (ROOT / "task_server" / "services" / "yaml_service.py").read_text(encoding="utf-8")
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
        if service_path.name == "yaml_service.py":
            continue
        require('parsed.get("tasks")' not in text, f"{service_path.name} must not directly read parsed.tasks; use extract_midscene_tasks")
    require('"/api/sonic/callback-diagnose"' in router_source and "healthReachableFromServer" in router_source, "Backend must expose callback diagnosis for HTTP 000")
    require("explainCallbackHttp000" in app_js_source and "/api/sonic/callback-diagnose" in app_js_source, "Frontend must show friendly HTTP 000 callback diagnosis")
    require("AI 分析并生成修复草稿" in task_page_source and "AI 修复当前文件" not in task_page_source, "Main repair button must say repair draft, not direct overwrite")
    require(
        '"PLAN", "PREPARE_SOURCE", "IMPACT_ANALYSIS", "CASE_RETRIEVAL", "MATCH_CASES"' in agent_service_source,
        "Agent step order must prepare source, analyze impact, retrieve cases, then match cases"
    )
    require('"sourceType"' in agent_service_source and '"sourceRefs"' in agent_service_source and '"sourceContext"' in agent_service_source, "Agent runs must persist sourceType/sourceRefs/sourceContext")
    require("def _agent_source_material_context" in agent_service_source and '"uploadedFiles"' in agent_service_source and '"uploadedImages"' in agent_service_source and '"sourceSummary"' in agent_service_source, "Agent prepare_source must normalize uploaded files/images into sourceContext")
    require("def _agent_pdf_text_from_base64" in agent_service_source and "pypdf.PdfReader" in agent_service_source, "Agent must extract PDF requirement text from uploaded source files")
    require("def _infer_agent_source_type" in agent_service_source and 'run["sourceType"] = source_type' in agent_service_source, "Agent must promote manual source type when requirement/Figma material is attached")
    require("def _agent_fallback_yaml_draft" in agent_service_source and "fallback_after_empty_ai_yaml" in agent_service_source and "fallback_after_invalid_ai_yaml" in agent_service_source, "Agent YAML generation must create confirmable drafts when AI returns empty or invalid YAML")
    require("def _agent_generate_yaml_from_ui_pipeline" in agent_service_source and "generate_ui_yaml_from_request" in agent_service_source and '"split_by_case"' in agent_service_source and "ui_yaml_pipeline" in agent_service_source, "Agent new-requirement YAML generation must reuse the full requirement/Figma/YAML pipeline before fallback")
    require("def _build_agent_quality_report" in agent_service_source and '"qualityReport"' in agent_service_source and '"完整测试用例 .mm"' in agent_service_source and '"可自动化 YAML"' in agent_service_source, "Agent generation must persist a reviewer-friendly quality report")
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
    require("def _confirm_agent_yaml_content_as_files" in agent_service_source and '"autoConfirmedFallback"' in agent_service_source and "已自动拆分并采用多任务兜底 YAML" in agent_service_source, "Agent fallback YAML must auto-confirm and split into files for Runner mode")
    require("def _save_agent_yaml_draft" in agent_service_source and '"WAIT_CONFIRM"' in agent_service_source and '"generated_yaml_draft"' in agent_service_source, "Agent fallback YAML drafts must still support manual confirmation")
    require('mark_step_success("GENERATE_YAML"' in agent_service_source and "已人工确认 YAML 草稿" in agent_service_source, "Confirming a YAML draft must mark GENERATE_YAML complete and resume validation/execution")
    require("确认草稿后再同步 Sonic" not in agent_service_source, "Runner-mode YAML draft confirmation must not tell users to sync Sonic")
    require("pypdf" in (ROOT / "deploy" / "install-server.sh").read_text(encoding="utf-8"), "Server install script must install pypdf for PDF requirement extraction")
    require("def _load_figma_context_for_agent" in agent_service_source and "load_figma_generation_context" in agent_service_source and '"figmaUsedPages"' in agent_service_source and '"figmaIgnoredPages"' in agent_service_source, "Agent Figma source must reuse the shared Figma requirement-filter extraction pipeline")
    require("preparedFigmaContextPath" in agent_service_source and '"prepared_figma_context": prepared_figma_context' in agent_service_source, "Agent YAML generation must reuse prepared Figma context instead of reparsing when available")
    require("def _prepared_figma_context_from_request" in yaml_service_source and "复用 Figma 解析" in yaml_service_source, "YAML generation must support prepared Figma context reuse")
    check_agent_fallback_yaml_auto_confirm_split()
    check_agent_prepared_figma_context_reuse()
    require("匹配全部用例（兜底模式）" not in agent_service_source, "Agent match must not fallback to all cases when AI/source is unclear")
    require("job_service.wait_jobs_finished" in agent_service_source, "Agent RUN_TASK must use job_service.wait_jobs_finished as the single implementation")
    require('"executionMode": execution_mode' in agent_service_source and 'should_run_suite = execution_mode == "SONIC_SUITE"' in agent_service_source, "Agent must default to Runner jobs and only run Sonic suite when explicitly requested")
    require('should_require_sonic = execution_mode == "SONIC_SUITE"' in agent_service_source and 'Runner 调试模式不阻断' in agent_service_source, "Execution precheck must not block Runner jobs on Sonic publish-only checks")
    require('step_name == "SYNC_SONIC" and execution_mode != "SONIC_SUITE"' in agent_service_source and "Runner 单条/多条调试模式不需要同步 Sonic" in agent_service_source, "Runner Agent execution must skip Sonic sync and run matched YAML directly")
    require("Runner 调试模式：创建" in agent_service_source and "避免“匹配 1 条却跑完整套件”" in agent_service_source, "Agent RUN_TASK must explain single/multi Runner mode instead of suite execution")
    require('"runnerId": runner_id' in agent_service_source and '"deviceId": device_id' in agent_service_source and '"deviceStrategy": device_strategy' in agent_service_source, "Agent runs must persist selected Runner/device execution target")
    require('"runnerSelection"' in agent_service_source and "尚未选择执行设备" in agent_service_source and "runner_service.all_online_devices" in agent_service_source, "Agent execution precheck must validate selected/auto Runner devices")
    require('"runner_id": selected_runner_id' in agent_service_source and '"device_id": selected_device_id' in agent_service_source and '"device_strategy": selected_device_strategy' in agent_service_source, "Agent Runner jobs must use the selected Runner/device strategy")
    require('case.get("device_strategy") or "auto"' in execution_adapter_source, "ExecutionAdapter local Runner jobs must default to automatic online-device assignment")
    require("def _append_step_trace" in agent_service_source and "_persist_agent_run_snapshot" in agent_service_source, "Agent timeline steps must persist live trace for running tools")
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
    require("mindmap_visual_image_policy" in yaml_service_source, "Mindmap summary must document the visual image policy")
    require("generation_mindmap_record_deleted_path" in yaml_service_source and '"/api/cases/mindmap-record"' in router_source, "Mindmap center must support deleting/hiding generation records")
    require('item.get("mindmap_updated_at") or item.get("generated_at")' in yaml_service_source, "Mindmap center must sort by latest mindmap update first")
    require("完整需求覆盖追踪矩阵" in yaml_service_source and "进入 YAML 的自动化用例" in yaml_service_source and "人工验证 / 待准备" in yaml_service_source, "Mindmap must preserve full requirement coverage beyond executable YAML cases")
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
    from task_server.services.yaml_service import validate_midscene_yaml_executability
    empty_yaml = validate_midscene_yaml_executability("android:\n  tasks: []\n")
    require(not empty_yaml.get("ok") and "不能为空" in "；".join(empty_yaml.get("issues") or []), "Empty android.tasks must fail executable validation")
    valid_yaml = validate_midscene_yaml_executability("android:\n  tasks:\n    - name: demo\n      flow:\n        - aiTap: 首页搜索框\n")
    require(valid_yaml.get("ok") and valid_yaml.get("taskCount") == 1, "Valid android.tasks YAML must pass executable validation")
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
    require("测试步骤" not in mindmap, "compact mindmap must not expand every execution step")
    require("自动化用例分级" in mindmap, "compact mindmap must keep priority grouping")
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
    require(all((draft.get("figma") or {}).get("direct_group") for draft in selected), "Direct-node descendants must carry direct_group metadata")
    require(all(figma_backend.figma_draft_generation_allowed(draft, min_score=999) for draft in selected), "Direct Figma pages must enter generation even when score threshold is high")
    generation_drafts, generation_ignored = figma_backend.split_generation_figma_drafts(selected, min_score=999)
    generation_names = {draft.get("page_name") for draft in generation_drafts}
    require({"引导5", "语音输入-长按"}.issubset(generation_names), "Direct Figma pages must survive generation filtering")
    require(not generation_ignored, "Direct Figma pages must not be moved to ignored generation list")
    require(not figma_backend.figma_direct_node_needs_parent_lookup({"type": "CANVAS", "children": [{}]}), "Figma canvas links must not force expensive parent lookup")
    require(figma_backend.figma_direct_node_needs_parent_lookup({"type": "TEXT", "characters": "AI建模"}), "Figma title/text links must lookup parent design scope")
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
    env_example = ENV_EXAMPLE.read_text(encoding="utf-8")
    require("TASK_APP_ENV='prod'" in env_example and "TASK_ALLOW_QUERY_TOKEN='0'" in env_example, "Env example must document production mode and disabled query token auth")
    for module_path in ["task_server/config.py", "task_server/auth.py", "task_server/storage.py", "task_server/repair_service.py", "task_server/sonic_service.py"]:
        require((ROOT / module_path).exists(), f"Backend service skeleton missing: {module_path}")
    storage_source = (ROOT / "task_server" / "storage.py").read_text(encoding="utf-8")
    require("write_json_atomic" in storage_source and "os.replace(tmp, target)" in storage_source, "Storage skeleton must provide atomic JSON writes")
    print({"ok": True, "file": str(MODULE), "checks": 31})


if __name__ == "__main__":
    main()
