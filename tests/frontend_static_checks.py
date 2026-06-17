#!/usr/bin/env python3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "task-manager.html"
CSS_DIR = ROOT / "css"
JS_DIR = ROOT / "js"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def _read_bundle() -> str:
    parts = [HTML.read_text(encoding="utf-8")]
    if CSS_DIR.is_dir():
        for path in sorted(CSS_DIR.glob("*.css")):
            parts.append(path.read_text(encoding="utf-8"))
    if JS_DIR.is_dir():
        for path in sorted(JS_DIR.glob("*.js")):
            parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


def main():
    # After the round-3 split, JS/CSS live in separate files. Static substring
    # checks below should still cover the full deployable bundle, so we
    # concatenate task-manager.html + css/app.css + js/*.js as a single blob.
    html = _read_bundle()
    require("<title>功夫豆测试平台</title>" in html, "Browser title must use 功夫豆测试平台")
    require("Midscene Task 管理平台" not in html and "Midscene Task 管理" not in html, "Old product title must not appear in the UI")
    require('<span class="header-logo">⚡</span>' not in html and '<div class="login-logo">⚡' not in html, "Old lightning emoji brand logo must not be used")
    require("brand-mark" in html and "header-subtitle" in html, "New platform brand logo/header structure is missing")
    require("assets/brand/kongfudou-icon.png" in html and "brand-mark-img" in html, "Kongfudou brand image must be used instead of a text placeholder")
    require("Qwen · Midscene · Sonic" in html, "Header subtitle must show core platform integrations")
    require("用例资产" in html, "Sidebar must include 用例资产 entry")
    # Sidebar has 5 nav groups with sub-items
    require(html.count('class="nav-group"') == 5, "Sidebar must include five nav groups (Agent/用例/执行/报告/配置)")
    require('data-nav-group="agent"' in html and 'data-nav-group="cases"' in html and 'data-nav-group="run"' in html and 'data-nav-group="report"' in html and 'data-nav-group="settings"' in html, "Sidebar nav groups must include agent/cases/run/report/settings")
    require("Agent 工作台" in html, "Dashboard must serve as the Agent workbench entry")
    require(("AI修复" in html or "AI 修复" in html) and 'data-workflow="repair"' in html, "Sidebar must expose independent AI repair entry")
    require("const AI_GATEWAY_BASE = '/ai-gateway'" in html, "AI Gateway calls must use same-origin reverse proxy")
    require("const USERS" not in html and "sonic2026" not in html and "test123" not in html, "Frontend must not contain plaintext login credentials")
    require("/auth/login" in html and "/auth/me" in html and "/auth/logout" in html, "Frontend login must use backend auth endpoints")
    require("sessionToken" in html and "Authorization" in html and "Bearer" in html, "Frontend API calls must carry Bearer session token")
    require("// ===== API CLIENT =====" in html and "async function apiRequest" in html and "async function aiRequest" in html, "Unified API client block is missing")
    require("window.fetch =" not in html, "Frontend must not monkey patch global window.fetch")
    require("async function apiTextRequest" in html and "function forceLogoutWithMessage" in html, "API client must include text requests and forced logout handling")
    require("forceLogoutWithMessage" in html and "res.status === 401" in html, "apiRequest must force logout on 401")
    require("fetch(`${API_BASE}" not in html, "Task backend calls must use apiRequest/apiTextRequest, not direct fetch(API_BASE)")
    require("api.highwayapi.ai" not in html and "127.0.0.1:8090" not in html, "Frontend must not call HighwayAPI or local gateway directly")
    require("HIGHWAY_API_KEY" not in html and "your_highway_api_key" not in html, "Frontend must not contain API key placeholders")
    require("测试当前策略" in html and "/ai/providers/test" in html, "Config page must expose AI model service test action")
    require("模型配置" in html and "/ai/providers" in html and "/ai/model-router" in html, "Config page must expose multi-provider model routing")
    require("loadAgentModelOptions" in html and "AI Gateway Provider" in html, "Agent model selector must load AI Gateway providers")
    require("modelProviderId" in html and "aiProviderId" in html and "selectedAgentModelInfo" in html, "Agent payload must keep provider id separate from raw model name")
    require("自动（按模型策略" in html, "Agent model selector must make the router-backed auto model visible")
    for label in ("生成测试用例模型", "生成 YAML 模型", "失败分析模型", "YAML 修复模型", "Agent 判断模型", "飞书缺陷草稿模型"):
        require(label in html, f"Model config label missing: {label}")
    require("测试当前策略" in html and "保存模型策略" in html, "Model config actions are missing")
    require("HIGHWAY_API_KEY" not in html and "QWEN_API_KEY" not in html, "Frontend must not contain model API key env names")
    require("AI分析失败原因" in html and "/ai/analyze-failure" in html, "Failed jobs must support AI failure analysis")
    require("生成修复 YAML" in html and "/ai/optimize-yaml" in html, "AI failure result must support YAML repair draft generation")
    require("修复后的 YAML 只作为草稿展示" in html and "不会自动覆盖当前文件或基线" in html, "YAML repair must be manual-confirm only")
    require("let repairDrafts = []" in html, "Frontend must keep repair draft state")
    for fn in ("createRepairDraftFromAiResult", "upsertRepairDraft", "currentRepairDraft", "repairDraftStatusText", "buildPendingActions"):
        require(fn in html, f"Frontend missing repair draft function: {fn}")
    require("/repair-drafts/apply" in html and "confirmApply" in html and "confirmRisk" in html, "Repair draft apply must be explicit manual confirmation")
    require("人工确认替换" in html and "拒绝草稿" in html and "待我处理" in html, "Repair drafts must surface as pending manual actions")
    require("JSON.stringify(job)" not in html, "Pending actions must not rely on string matching whole job JSON")
    # Full-auto Agent workbench checks
    require("启动 Agent" in html and "Agent 状态" in html and "Agent 产物" in html, "Full-auto Agent workbench sections are missing")
    require("AUTO_SAFE" in html and "FULL_AUTO" in html, "AI Agent mode selector must support AUTO_SAFE and FULL_AUTO")
    require("agent-mode" in html and "AUTO_SAFE" in html, "AI Agent mode radio buttons are missing")
    require("autoOverwriteBaseline: false" in html, "Agent payload must not enable auto baseline overwrite")
    require("WAIT_CONFIRM_RUN" in html and "WAIT_CONFIRM_BUG" in html, "AI Agent confirmation states are missing")
    require("确认打印" in html and "覆盖基线" in html, "AI Agent risk keywords must be present")
    require("POST /agent/run" not in html, "UI should call Agent endpoints without exposing implementation text in visible copy")
    require("/agent-runs" in html and "/agent-runs/" in html, "AI Agent endpoint calls are missing")
    require("Agent 状态" in html and "运行轨迹" in html, "Right panel must become Agent status panel with timeline")
    require("copyAgentArtifact" in html and "downloadAgentYaml" in html, "Agent artifacts must support copy and YAML download")
    require("renderAgentReportArtifact" in html and "renderAgentSummaryArtifact" in html and "executionReports" in html and "yamlExecutionRefs" in html, "Agent report/summary artifacts must render as readable rich cards")
    require("agentInfoGrid" in html and "agentReadableList" in html and "agent-readable-panel" in html and "final-report-hero" in html, "Agent step details and final report must use readable card layouts")
    require("normalizeAgentReportArtifacts" in html and "isAgentYamlRef" in html and "normalizedAgentReportCounts" in html and "agentReportLooksYaml" in html, "Agent report rendering must not count YAML files as HTML execution reports")
    require("YAML 校验失败时，不能显示" not in html, "Implementation details should not be visible as instructional UI text")
    # Assets entry exists in sidebar as asset center
    require("用例资产" in html and 'data-workflow="assets"' in html, "Assets must be accessible from sidebar")
    require("你想让 Agent 测什么" in html and "启动 Agent" in html, "Dashboard hero must present the simplified Agent workbench")
    require("showModelConfigCenter" in html and "查看模型策略" in html, "Dashboard must link to model config")
    require("dashboard-accordion" in html, "Secondary dashboard cards must be collapsible")
    require("dashboard-primary-panel" in html and "dashboard-stack" in html, "Dashboard must separate primary next action from secondary details")
    require("async function copyText(text)" in html, "copyText function is missing")
    require("navigator.clipboard.writeText" in html, "copyText must try Clipboard API first")
    require("document.execCommand" in html, "copyText must include execCommand fallback for HTTP/permission failures")
    require("textarea.setSelectionRange" in html, "copyText fallback must select all text before copying")
    require("复制检查点" in html, "report checkpoint copy button text is missing")
    require("reportCheckpointText" in html, "report checkpoint text builder is missing")
    require("reviewYamlExecutabilityHtml" in html, "YAML executability panel is missing")
    require("function yamlDisplayName(file)" in html, "YAML display-name fallback is missing")
    require("replace('.yaml','')" not in html, "Do not strip YAML suffix inline; use yamlDisplayName()")
    require('replace(".yaml","")' not in html, "Do not strip YAML suffix inline; use yamlDisplayName()")
    require("高级：Figma 扫描范围" in html, "Figma advanced scan controls must be collapsed and clearly named")
    require("最多扫描 UI 页面数，不是生成用例数量" in html, "Figma limit tooltip must clarify it is not case count")
    require("用例条数由需求复杂度、风险、边界和异常覆盖自动分析" in html, "YAML generation form must explain case count is automatic")
    require(html.count("Figma 只作为本次生成 YAML 的临时 UI 参考") == 1, "Figma generation hint must not be duplicated")
    require("grid-template-rows: auto minmax(0, 1fr)" in html, "Sonic sync panel must let the list fill remaining height")
    require("下方可滚动查看全部" in html, "Sonic sync summary must tell users the list is scrollable")
    require(".sonic-preview-list" in html and "max-height: none" in html, "Sonic sync list must not be capped to three visible rows")
    submit_marker = "生成任务已提交，已切到生成记录查看进度"
    close_marker = "closeModal('modal-generate');"
    jobs_marker = "await showGenerateJobsCenter();"
    require(submit_marker in html, "Generate modal must tell the user that the background job was submitted")
    require(close_marker in html and jobs_marker in html, "Generate modal must close and switch to generation records after job creation")
    require(html.index(close_marker, html.index(submit_marker)) < html.index(jobs_marker, html.index(submit_marker)), "Generate modal must close before showing generation records")
    require("function jobDurationText(job)" in html, "Generation records must display elapsed generation time")
    require("function jobTimingText(job)" in html, "Generation job details must include start/end/duration timing")
    require("耗时" in html and "已用时" in html, "Generation duration labels must be user friendly")
    require("function jobTimelineHtml(job)" in html and "进度流水" in html, "Execution job detail must show progress timeline")
    require("单条/多条调试" in html and "multiple size=\"8\"" in html, "Execution modal must support selecting one or multiple tasks")
    require("不会触发 Sonic 测试套整套回归" in html and "每个任务只下发选中的一个 task" in html, "Single/multi-task execution must clearly state it does not run the full Sonic suite")
    require("Sonic 只负责已同步基线的测试套回归" in html, "Execution page must distinguish Runner debugging from Sonic suite regression")
    require("刷新桥接脚本" in html and "refreshSonicBridgeScripts" in html, "Sonic config must expose one-click bridge script refresh")
    require("apiRequest('/sonic/refresh-bridges'" in html, "Bridge script refresh must call backend through apiRequest")
    require("不修改 YAML、不改基线、不触发执行" in html, "Bridge refresh confirmation must clearly distinguish it from YAML sync/execution")
    require("renderSonicPublishResult" in html and "单条用例同步结果" in html and "模块同步结果" in html, "Sonic publish must show explicit single/batch sync results")
    require("AI分析失败" in html and "生成修复 YAML" in html, "Dashboard must expose primary Agent and repair actions")
    require("Agent 状态" in html and "待我处理" in html and "当前任务" in html, "Right panel must be an action-oriented Agent status panel")
    require("normalizeFailureAnalysis" in html and "SCRIPT_ISSUE" in html and "PRODUCT_BUG" in html and "ENV_ISSUE" in html and "UNKNOWN" in html, "AI repair must normalize and gate failure types")
    require("AI修复工作台" in html and "失败任务列表" in html and "结构化分析" in html and "YAML 修复草稿" in html, "AI repair must be an independent three-column workspace")
    require("原始 YAML" in html and "修复 YAML" in html and "Diff / 校验" in html, "YAML repair draft must show original, fixed, diff, and validation")
    require("PRODUCT_BUG 不允许" not in html, "Implementation details must not leak as rough internal copy")
    require("apiRequest('/reports/cleanup'" in html and "apiRequest('/cases/mindmap-only-async'" in html and "apiRequest('/ui/generate-yaml-async'" in html, "Long-running write endpoints must use apiRequest")
    forbidden_write_patterns = [
        "fetch(`${API_BASE}/sonic/publish-batch`",
        "fetch(`${API_BASE}/file/restore`",
        "fetch(`${API_BASE}/baseline/page-refs`,",
        "fetch(`${API_BASE}/run-request`",
        "fetch(`${API_BASE}/knowledge/page`,",
        "fetch(`${API_BASE}/ui/generate-yaml-async`",
        "fetch(mindmapDownloadUrl(caseSetId), { method:"
    ]
    for pattern in forbidden_write_patterns:
        require(pattern not in html, f"Write API must use apiRequest, found direct fetch pattern: {pattern}")
    require("path-rail" in html and "失败分析：Qwen Plus" in html, "Dashboard must show model strategy as visual nodes")
    require("generation-flow" in html and "读资料" in html and "生成 YAML" in html, "Generation records must show a visual generation flow")
    require("nav-group" in html and "配置" in html, "Sidebar navigation must use five task-oriented groups")
    require("setActiveWorkflow('config');\n  renderTaskAppModal();" not in html, "App config modal must not reset workflow back to model config")
    require("setActiveWorkflow('config');\n  document.getElementById('toolbar-path').innerHTML = '<span>📁</span> 环境体检';" not in html, "System preflight must not reset workflow back to model config")
    require("['assets', 'generate', 'yaml_edit', 'execute', 'repair', 'baseline'].includes(activeWorkflow)" in html, "Opening YAML from assets/yaml_edit must preserve the current workflow")
    # Full-auto Agent specific checks
    require("startAutoAgentRun" in html and "startAgentRun" in html, "Agent run functions must be defined")
    require("agent-runs/start" in html and "agent-runs" in html, "Frontend must call backend Agent Run API")
    require("confirmAgentStep" in html and "cancelAgentRunById" in html, "Agent confirm and cancel functions must exist")
    require("agentRiskHits" in html and "classifyRiskLevel" in html, "Agent risk detection functions must exist")
    require("agent-source-type" in html and "AGENT_SOURCE_TYPES" in html, "Agent workbench must expose input source selector")
    require("renderAgentSourcePanel" in html and "collectAgentSourceRefs" in html, "Agent source panel and payload collector are missing")
    require("本次 Agent 输入资料" in html and "agent-source-figma-url" in html and "agent-source-requirement-text" in html, "Agent workbench must expose Figma and requirement inputs")
    require("agent-source-file-input" in html and "handleAgentSourceFiles" in html and "handleAgentSourcePaste" in html, "Agent workbench must support requirement/screenshot upload and paste")
    require("sourceInputs: sourceMaterials" in html and "files: sourceMaterials.files" in html and "images: sourceMaterials.images" in html, "Agent payload must include uploaded source materials")
    require("renderSourceContextDetail" in html and "输入摘要" in html and "上传资料" in html and "Figma" in html and "agent-readable-panel" in html, "Agent timeline must show prepared source details and Figma extraction result")
    for source in ("manual", "requirement", "figma", "failed_job"):
        require(source in html, f"Agent source type missing: {source}")
    require("sourceType: source.sourceType" in html and "sourceRefs: source.sourceRefs" in html, "Agent payload must include sourceType/sourceRefs")
    require("executionMode: 'RUNNER_JOB'" in html, "Agent payload must default to Runner job execution instead of Sonic suite execution")
    require("输入来源：" in html and "整理输入来源" in html, "Agent preview/timeline copy must explain source preparation")
    require("${AGENT_TIMELINE_STEPS.length} 步 Agent 链路" in html and "['PREPARE_SOURCE', '整理输入来源']" in html, "Agent timeline must use dynamic count and include PREPARE_SOURCE")
    require("14 步全自动链路" not in html, "Agent timeline copy must not hard-code stale step count")
    require("timelineLiveTraceDetail" in html and "step-live-trace" in html, "Agent timeline must show live running trace when a step is expanded")
    require("renderExecutionPrecheckDetail" in html and "precheck-warnings" in html and "blockers" in html, "Execution precheck detail must show blockers and warnings")
    require("match-keywords" in html and "匹配关键词" in html and "detail.reasons" in html, "Agent match detail must show concrete matched keywords and candidate reasons")
    require("renderAgentHistoryPage" in html and "Agent 运行记录" in html, "Agent history menu must render a dedicated history page")
    require("renderAgentConfirmPage" in html and "HUMAN IN LOOP" in html, "Agent confirmation menu must render a dedicated pending-confirmation page")
    require("Runner 单条调试" in html and "/run-request" in html, "Sonic status UI must route single/multi debugging to local Runner jobs")
    require("runSonicSingleCase" not in html and "/sonic/run-case" not in html and "Sonic 临时套执行" not in html, "Frontend must not expose Sonic temporary-suite single-case execution")
    require("Trace 回放" in html and "/debug/traces" in html and "/debug/replay" in html and "/debug/diff" in html, "Execution center must expose Trace replay/diff debugger")
    trace_viewer = (ROOT / "trace-viewer.html").read_text(encoding="utf-8")
    require("Execution Trace Viewer" in trace_viewer and "/debug/traces" in trace_viewer and "sessionToken" in trace_viewer, "Trace viewer must render real trace data with session auth")
    require("一键应用推荐策略" in html and "applyRecommendedStrategy" in html, "Model config must support one-click recommended strategy")
    require("deleteGenerationMindmapRecord" in html and "/cases/mindmap-record" in html and "删除记录" in html, "Mindmap center must support deleting generation records")
    print({"ok": True, "file": str(HTML), "checks": 58})


if __name__ == "__main__":
    main()
