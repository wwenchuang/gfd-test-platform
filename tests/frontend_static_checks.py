#!/usr/bin/env python3
import json
import re
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


def check_api_automation_frontend_residue_is_removed():
    state_js = (JS_DIR / "state.js").read_text(encoding="utf-8")
    for name in (
        "apiTestingOverview", "apiTestingSnapshots", "apiTestingEndpoints",
        "apiTestingPlans", "apiTestingReports", "apiTestingCurrentSnapshotId",
        "apiTestingCurrentPlan", "apiTestingSources", "apiTestingSyncs",
        "apiTestingProjectScope", "apiTestingSourceDraftMode",
        "apiTestingSelectionByScope", "apiAssetSelectedSourceId",
        "apiAssetSelectedRevisionId", "apiAssetRevisionPinned",
        "apiAssetActiveSyncId", "apiAssetSyncPollTimer", "apiAssetSettingsOpen",
        "apiSourceCredentialEditing", "apiSourceDiscoveryRequestId",
        "apiSourceDiscoveryState", "apiAssetContextRequestId",
        "apiAssetRequestController", "apiPlanRequestController",
        "apiExecutionRequestController", "apiAssetPageScrollTop",
        "apiAssetSyncExpandedKeys", "apiAssetSyncScrollPositions",
        "apiLogExpandedKeys", "apiLogScrollPositions", "apiExecutionContext",
        "apiExecutionActiveId", "apiExecutionPollTimer",
        "apiExecutionSettingsOpen", "apiExecutionContextRequestId",
        "apiExecutionStartingPlanId",
    ):
        require(name not in state_js, f"Removed API frontend state must not remain: {name}")
    for storage_key in ("api_asset_sync_expanded_keys", "api_log_expanded_keys"):
        require(
            storage_key not in state_js,
            f"Removed API state must not parse preserved localStorage data: {storage_key}",
        )
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    scripts = package.get("scripts", {})
    for name in ("test:api-project-workspace", "test:api-sync", "test:apifox-discovery"):
        require(name not in scripts, f"Obsolete API test script must be removed: {name}")
    static_script = scripts.get("test:static", "")
    for filename in (
        "api_asset_sync_checks.py", "api_case_contract_checks.py",
        "api_manual_workflow_checks.py", "api_native_execution_checks.py",
        "api_project_workspace_checks.py", "api_runtime_recovery_checks.py",
        "api_test_lab_checks.py", "api_workbench_checks.py",
        "apifox_discovery_checks.py", "metersphere_v365_adapter_checks.py",
    ):
        require(filename not in static_script, f"Deleted API test must not remain in test:static: {filename}")
    for command in (
        "tests/backend_static_checks.py", "tests/frontend_static_checks.py",
        "tests/ai_gateway_static_checks.py", "tests/undefined_name_checks.py",
        "tests/ai_gateway_catalog_checks.mjs", "ai_skills/evals/run_skill_evals.py",
    ):
        require(command in static_script, f"Active static verification must remain in test:static: {command}")
    visual_smoke = (ROOT / "tests" / "visual_smoke_check.js").read_text(encoding="utf-8")
    for marker in (
        "/api/api-testing/", "apiTestingProjectScope", "apiAssetSyncPollCount",
        "apiPlanGenerationPollCount", "meterExecution", "getSourceRequestBodies",
        'data-workflow="api_', "metersphere-execution.png", "api-assets-sync.png",
    ):
        require(marker not in visual_smoke, f"API-only visual smoke residue must be removed: {marker}")


def check_api_testing_frontend_workspace():
    source_root = ROOT / "api-testing-ui" / "src"
    api_testing_main = (source_root / "main.ts").read_text(encoding="utf-8")
    api_testing_auth_redirect = source_root / "utils" / "authRedirect.ts"
    api_testing_styles = (source_root / "styles" / "app.css").read_text(encoding="utf-8")
    require(source_root.is_dir(), "API testing Vue source workspace is missing")
    app_source = (source_root / "App.vue").read_text(encoding="utf-8")
    router_source = (source_root / "router.ts").read_text(encoding="utf-8")
    for label in ("工作台", "接口资产", "执行记录", "测试报告", "环境配置"):
        require(label in app_source, f"API testing navigation is missing: {label}")
    for route in ("WorkbenchView", "AssetsView", "RunsView", "ReportsView", "SettingsView"):
        require(route in router_source, f"API testing router is missing: {route}")
    require((ROOT / "api-test" / "index.html").exists(), "Built API testing frontend is missing")
    require(api_testing_auth_redirect.exists(), "API testing frontend must centralize login deep-link redirects")
    require(
        "requireApiTestingSession" in api_testing_main
        and "if (requireApiTestingSession()) void bootstrap()" in api_testing_main,
        "API testing frontend must redirect logged-out deep links before loading page data",
    )
    require(
        "baseline-selection-metric" in api_testing_styles and "white-space: nowrap" in api_testing_styles,
        "Baseline counts must stay readable without splitting numbers and labels into fragments",
    )

    view_source = "\n".join(path.read_text(encoding="utf-8") for path in sorted((source_root / "views").glob("*.vue")))
    for english_kicker in (
        "API SOURCE", "PROJECTS", "ASSET DETAIL", "ACTIONS", "API BASELINES",
        "API CASE MANAGEMENT", "API TASK MANAGEMENT", "API SCHEDULED JOBS",
        "API TEST RUNS", "API TEST WORKSPACE", "API TEST REPORTS",
        "API ENVIRONMENT ASSETS", "NEW ENVIRONMENT", "NEW REVISION",
        "PROJECT NOTIFICATION",
    ):
        require(english_kicker not in view_source, f"API testing UI still exposes an English kicker: {english_kicker}")
    confirmation_source = (source_root / "utils" / "executionConfirmation.ts").read_text(encoding="utf-8")
    require("请求将真实发送到该环境" in confirmation_source, "API execution confirmation must explain the real request effect")
    for filename in ("WorkbenchView.vue", "CasesView.vue", "TasksView.vue", "BaselinesView.vue", "ScheduledJobsView.vue", "RunsView.vue"):
        require("confirmApiExecution" in (source_root / "views" / filename).read_text(encoding="utf-8"), f"API execution entry lacks confirmation: {filename}")
    require("confirmApiExecution" in (source_root / "components" / "InlineWorkflowStepEditor.vue").read_text(encoding="utf-8"), "Workflow step preview must confirm real execution")

    visual_source = (ROOT / "tests" / "api_testing_ui_visual_check.js").read_text(encoding="utf-8")
    require("1440" in visual_source and "900" in visual_source, "API visual gate must cover desktop")
    require("390" in visual_source and "844" in visual_source, "API visual gate must cover mobile")
    require("assertNoHorizontalOverflow" in visual_source, "API visual gate must reject horizontal overflow")

    acceptance_source = (ROOT / "tests" / "api_testing_e2e.spec.mjs").read_text(encoding="utf-8")
    for marker in ("workbench-desktop.png", "workbench-mobile.png", "report-desktop.png", "report-mobile.png"):
        require(marker in acceptance_source, f"API acceptance is missing screenshot evidence: {marker}")


def check_original_platform_management_experience():
    html = HTML.read_text(encoding="utf-8")
    auth_js = (JS_DIR / "auth.js").read_text(encoding="utf-8")
    utils_js = (JS_DIR / "utils.js").read_text(encoding="utf-8")
    state_js = (JS_DIR / "state.js").read_text(encoding="utf-8")
    workbench_js = (JS_DIR / "agent-workbench.js").read_text(encoding="utf-8")
    status_js = (JS_DIR / "agent-status.js").read_text(encoding="utf-8")
    empty_states_js = (JS_DIR / "empty-states.js").read_text(encoding="utf-8")
    execution_js = (JS_DIR / "execution.js").read_text(encoding="utf-8")
    reports_js = (JS_DIR / "reports.js").read_text(encoding="utf-8")
    agent_service = (ROOT / "task_server" / "services" / "agent_service.py").read_text(encoding="utf-8")
    router = (ROOT / "task_server" / "router.py").read_text(encoding="utf-8")
    app_js = (JS_DIR / "app.js").read_text(encoding="utf-8")
    repair_js = (JS_DIR / "ai-repair.js").read_text(encoding="utf-8")
    require("fonts.googleapis.com" not in html, "Main platform must not block on external Google Fonts")
    require('rel="icon"' in html and "assets/brand/kongfudou-icon.png" in html, "Main platform must expose a local favicon to avoid a noisy 404")
    require('<form class="login-box"' in html and "onsubmit=\"event.preventDefault(); doLogin();\"" in html, "Login controls must be wrapped in a submit form")
    require('type="submit"' in html, "Login button must submit the login form instead of relying on click-only behavior")
    require("function loginReturnToPath" in auth_js and "return_to" in auth_js, "Login must parse safe same-origin return_to redirects")
    require("window.location.assign(returnTo)" in auth_js, "Login must redirect to return_to after successful authentication")
    require(auth_js.count("continueAfterAuthentication()") >= 2, "Manual and restored login sessions must both honor return_to deep links")
    require("clearTransientAuthFeedback" in auth_js and "showAuthedApp" in auth_js, "Successful login must clear stale error and toast feedback")
    require("showAgentPlanPreview" in workbench_js and "copyAgentPlanPreview" in workbench_js, "Agent startup preview must render in an in-page modal with copy support")
    require("alert(lines.join" not in workbench_js, "Agent startup preview must not use a blocking browser alert")
    require("modal-agent-plan-preview" in html and "agent-plan-preview-body" in html, "Agent startup preview modal is missing")
    for filename in ("auth.js", "agent-workbench.js", "agent-status.js", "empty-states.js", "execution.js", "utils.js"):
        require(
            f'{filename}?v=20260828-full-platform-ux' in html,
            f"Changed static asset must use the full-flow UX cache key: {filename}",
        )
        require(
            f'{filename}?v=20260826-platform-management-ux' not in html and f'{filename}?v=20260701-install-refresh' not in html,
            f"Changed static asset must not keep stale cache key: {filename}",
        )
    require("title: '系统设置'" not in utils_js, "Configuration workflow title must not use stale 系统设置 copy")
    require("可在「系统设置」调整风险策略" not in empty_states_js, "Agent confirmation empty-state must not point to a stale 系统设置 page")
    require("系统设置页面运行预检脚本" not in execution_js, "Runner empty-state must not point to a stale 系统设置 page")
    for marker in ("模块创建失败", "YAML 用例创建失败", "文件上传失败"):
        require(marker in utils_js, f"Persistence failure must be visible instead of reporting false success: {marker}")

    require("AGENT_HISTORY_PAGE_SIZE" in state_js and "agentHistoryFilters" in state_js, "Agent history must keep explicit filter and pagination state")
    for marker in ("filterAgentRuns", "搜索任务、应用或结果", "agent-history-pager"):
        require(marker in status_js, f"Agent history management control is missing: {marker}")
    require(
        "resultMeta.smokeAllFailed ? '冒烟全失败'" in status_js
        and "resultMeta.scriptFailed ? '脚本问题'" in status_js
        and "resultMeta.productFailed ? '产品缺陷'" in status_js,
        "Agent history search must include the result labels already visible on each card",
    )
    require("REPORT_PAGE_SIZE" in state_js and "reportFilters" in state_js, "Report center must keep explicit filter and pagination state")
    for marker in ("filterReportsForCenter", "搜索任务、应用或报告", "report-center-pager"):
        require(marker in reports_js, f"Report management control is missing: {marker}")

    require("loadFullAgentModelCatalog" in workbench_js and "加载更多模型" in workbench_js, "Large model catalog must be opt-in instead of blocking workbench entry")
    require(
        '<details class="agent-source-materials">' in workbench_js,
        "Optional Agent source materials must stay collapsed until the user needs them",
    )
    require(
        "agent-onboarding-steps" in status_js and "本次产物（空）" not in status_js,
        "Empty Agent status must guide first-time users instead of rendering several empty panels",
    )
    preview_plan = workbench_js[workbench_js.index("async function previewAgentPlan"):workbench_js.index("function showAgentPlanPreview")]
    require("请先输入测试目标" in preview_plan, "Agent plan preview must reject an empty goal before requesting AI planning")
    load_agent_model_options = workbench_js[workbench_js.index("async function loadAgentModelOptions"):workbench_js.index("function dashboardStats")]
    require(
        "aiGatewayGet('/ai/providers')" not in load_agent_model_options,
        "Agent workbench model selector must not fetch the full AI Gateway provider catalog on normal render",
    )
    load_full_agent_catalog = workbench_js[workbench_js.index("async function loadFullAgentModelCatalog"):workbench_js.index("async function loadAgentModelOptions")]
    require(
        "aiGatewayGet('/ai/providers')" in load_full_agent_catalog and "apiRequest('/models')" in load_full_agent_catalog,
        "Full Agent model catalog must be loaded only from the explicit opt-in action",
    )
    require(
        "async function loadAllYamlStats" in status_js and "apiRequest('/yaml-stats')" in status_js,
        "YAML stats warmup must use one bounded batch request instead of per-module refresh requests",
    )
    warmup_yaml_stats = status_js[status_js.index("async function warmupYamlStats"):status_js.index("function modulePriorityFilterLabel")]
    require(
        "loadYamlStatsForModule(mod)" not in warmup_yaml_stats and "/yaml-stats?module=" not in warmup_yaml_stats,
        "YAML stats warmup must not fan out one /yaml-stats request per module",
    )
    for marker in ("showAppConfigCenter", "showFeishuConfigCenter", "showSonicConfigCenter", "showBugDraftCenter"):
        require(marker in status_js, f"Standalone management page is missing: {marker}")
    sonic_branch = status_js[status_js.index("activeWorkflow === 'sonic_config'"):status_js.index("activeWorkflow === 'feishu_config'")]
    require("scanLegacySonicCases('all')" not in sonic_branch, "Opening execution environment must not start a destructive/slow Sonic scan")
    require("执行环境" in html and "环境体检" in html, "Configuration navigation labels must describe their actual page")
    require("workflow-badge" in html and "updateNavigationBadges" in status_js, "Sidebar must expose actionable history and confirmation counts")
    require("restoreWorkflowPreference" in status_js and "midscene_active_workflow" in status_js, "Reloading must restore the last valid management page")

    for route in ("/api/feishu-drafts", "/api/feishu-drafts/submit", "/api/feishu-drafts/reject"):
        require(route in router, f"Feishu draft management route is missing: {route}")
    require("def _require_admin_session" in router and router.count("if _require_admin_session(handler):") >= 2, "External draft actions must require a verified administrator session")
    require("def _authenticated_user" in router and "user=_authenticated_user(handler)" in router, "Draft audit operators must come from the verified session")
    require("history_limit" in router and "background_limit" in router and "history_scope" in router, "Job history API must expose a bounded, explicit search scope")
    require("jobHistoryScope" in app_js and "jobHistoryScopeText" in app_js, "History pages must explain which records are searchable")
    require("clearManagementSearchTimers" in status_js, "Changing pages must cancel pending management-search renders")
    require("activeWorkflow !== 'reports'" in reports_js, "Report search must not redraw after leaving the page")
    require("!['repair', 'failure_analysis'].includes(activeWorkflow)" in repair_js, "Repair search must not redraw after leaving the page")
    require("result.outcome === 'failed'" in status_js, "Agent history filters must prioritize the execution outcome over DONE orchestration state")
    require("create_feishu_draft" in agent_service and "sourceRunId" in agent_service, "Agent bug drafts must be persisted with their source run")


def main():
    # After the round-3 split, JS/CSS live in separate files. Static substring
    # checks below should still cover the full deployable bundle, so we
    # concatenate task-manager.html + css/app.css + js/*.js as a single blob.
    check_api_automation_frontend_residue_is_removed()
    check_api_testing_frontend_workspace()
    check_original_platform_management_experience()
    html = _read_bundle()
    execution_js = (JS_DIR / "execution.js").read_text(encoding="utf-8")
    utils_js = (JS_DIR / "utils.js").read_text(encoding="utf-8")
    state_js = (JS_DIR / "state.js").read_text(encoding="utf-8")
    app_js = (JS_DIR / "app.js").read_text(encoding="utf-8")
    agent_workbench_js = (JS_DIR / "agent-workbench.js").read_text(encoding="utf-8")
    agent_status_js = (JS_DIR / "agent-status.js").read_text(encoding="utf-8")
    navigation_js = (JS_DIR / "navigation.js").read_text(encoding="utf-8")
    app_css = (ROOT / "css" / "app.css").read_text(encoding="utf-8")
    require("<title>功夫豆测试平台</title>" in html, "Browser title must use 功夫豆测试平台")
    require("Midscene Task 管理平台" not in html and "Midscene Task 管理" not in html, "Old product title must not appear in the UI")
    require('<span class="header-logo">⚡</span>' not in html and '<div class="login-logo">⚡' not in html, "Old lightning emoji brand logo must not be used")
    require("brand-mark" in html and "header-subtitle" in html, "New platform brand logo/header structure is missing")
    require("assets/brand/kongfudou-icon.png" in html and "brand-mark-img" in html, "Kongfudou brand image must be used instead of a text placeholder")
    require("Qwen · Midscene · Sonic" in html, "Header subtitle must show core platform integrations")
    require("用例资产" in html, "Sidebar must include 用例资产 entry")
    require(html.count('class="nav-group"') == 5, "Sidebar must include five nav groups (Agent/用例/执行/报告/配置)")
    require('data-nav-group="agent"' in html and 'data-nav-group="cases"' in html and 'data-nav-group="run"' in html and 'data-nav-group="report"' in html and 'data-nav-group="settings"' in html, "Sidebar nav groups must include agent/cases/run/report/settings")
    require('data-nav-group="api-testing"' not in html, "API testing navigation must be removed")
    require('js/api.js' not in html, "API frontend client script must not be loaded")
    require('js/api-testing.js' not in html, "API testing script must not be loaded")
    require('js/api-test-lab.js' not in html, "API test lab script must not be loaded")
    require('api_dashboard:' not in agent_status_js, "API dashboard toolbar entry must be removed")
    require("sectionKey === 'api_dashboard'" not in navigation_js, "API dashboard navigation branch must be removed")
    for workflow_key in ("api_sync:", "api_baselines:", "api_execution:"):
        require(workflow_key not in agent_status_js, f"API workflow alias must be removed: {workflow_key}")
    require(not (ROOT / 'js' / 'api.js').exists(), "js/api.js must be removed")
    require(not (ROOT / 'js' / 'api-testing.js').exists(), "js/api-testing.js must be removed")
    require(not (ROOT / 'js' / 'api-test-lab.js').exists(), "js/api-test-lab.js must be removed")
    require("const API_BASE = '/api'" in utils_js and "async function apiRequest" in utils_js and "const WORKFLOW_SECTIONS" in utils_js, "Shared frontend client and workflow registry must remain available outside the removed API module")
    require("api_dashboard:" not in utils_js and "api_assets:" not in utils_js, "Shared workflow registry must not retain API workflow definitions")
    require("Agent 工作台" in html, "Dashboard must serve as the Agent workbench entry")
    require('data-workflow="repair"' not in html, "Sidebar must not duplicate failure handling across execution and report groups")
    require('data-workflow="failure_analysis"' in html and "失败处理" in html, "Sidebar must expose one clear failure handling entry")
    require('data-nav-group="agent" open' in html, "First visit must expand the active Agent navigation group")
    for collapsed_group in ("cases", "run", "report", "settings"):
        require(
            f'data-nav-group="{collapsed_group}" open' not in html,
            f"First visit must keep the {collapsed_group} navigation group collapsed",
        )
    require("midscene_nav_density_v2" in agent_status_js, "Existing all-open sidebar preferences must receive a one-time compact migration")
    require("closeTransientUiForNavigation" in agent_status_js and "hideToast" in utils_js, "Page navigation must close stale modals and page-scoped notices")
    require("const AI_GATEWAY_BASE = '/ai-gateway'" in html, "AI Gateway calls must use same-origin reverse proxy")
    require("const USERS" not in html and "test123" not in html, "Frontend must not contain plaintext login credentials")
    require("/auth/login" in html and "/auth/me" in html and "/auth/logout" in html, "Frontend login must use backend auth endpoints")
    require("登录服务版本不匹配" in html, "Login must explain a stale backend 404 instead of blaming credentials")
    require("sessionToken" in html and "Authorization" in html and "Bearer" in html, "Frontend API calls must carry Bearer session token")
    require("// ===== API CLIENT =====" in html and "async function apiRequest" in html and "async function aiRequest" in html, "Unified API client block is missing")
    require("window.fetch =" not in html, "Frontend must not monkey patch global window.fetch")
    require("async function apiTextRequest" in html and "function forceLogoutWithMessage" in html, "API client must include text requests and forced logout handling")
    require("forceLogoutWithMessage" in html and "res.status === 401" in html, "apiRequest must force logout on 401")
    require("fetch(`${API_BASE}" not in html, "Task backend calls must use apiRequest/apiTextRequest, not direct fetch(API_BASE)")
    require("api.highwayapi.ai" not in html and "127.0.0.1:8090" not in html, "Frontend must not call HighwayAPI or local gateway directly")
    require("HIGHWAY_API_KEY" not in html and "your_highway_api_key" not in html, "Frontend must not contain API key placeholders")
    require("测试当前策略" in html and "/ai/providers/test" in html, "Config page must expose AI model service test action")
    require("showModelServiceTestResult" in html and "alert(JSON.stringify(data" not in html, "Model service test must use a dedicated result dialog instead of a raw alert or YAML repair dialog")
    require("模型配置" in html and "/ai/providers" in html and "/ai/model-router" in html, "Config page must expose multi-provider model routing")
    require("loadAgentModelOptions" in html and "AI 网关模型" in html, "Agent model selector must load AI Gateway providers with localized copy")
    require("modelProviderId" in html and "aiProviderId" in html and "selectedAgentModelInfo" in html, "Agent payload must keep provider id separate from raw model name")
    require("catalogSource" in html and "实时目录" in html and "model.group === 'AI Gateway'" in html, "Agent model selector must label live catalog entries and suppress stale Task API Gateway duplicates")
    require("自动（按模型策略" in html, "Agent model selector must make the router-backed auto model visible")
    for label in ("生成测试用例模型", "生成 YAML 模型", "失败分析模型", "YAML 修复模型", "Agent 判断模型", "飞书缺陷草稿模型"):
        require(label in html, f"Model config label missing: {label}")
    require("测试当前策略" in html and "保存模型策略" in html, "Model config actions are missing")
    require("nextTaskAppStep" in html and "请先填写应用中文名和包名" in html, "Application wizard must validate basic information before advancing")
    require("核心链路可用，但有" in html and "项待处理" in html, "Environment health toast must preserve warning state")
    require("openAgentRunTrace(runId, workflow = 'dashboard')" in html, "Agent history trace must open the visible workbench instead of an instruction-only page")
    agent_section_head_css = app_css[app_css.index(".agent-section-head > span"):app_css.index(".agent-section-head div")]
    require("min-width: 64px" in agent_section_head_css and "white-space: nowrap" in agent_section_head_css, "Agent step labels must stay horizontal instead of wrapping one character per line")
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
    require(
        'data-agent-workbench-mode="${run ? \'run\' : \'new\'}"' in html
        and "还没有选择运行记录" not in html,
        "Agent workbench must expose new-run mode without duplicating an empty history panel",
    )
    require("return normalizeAgentRun(agentCurrentRun || null)" in html and "agentCurrentRun = agentRuns[0]" not in html, "Loading Agent history must not auto-select the latest run")
    require("copyAgentArtifact" in html and "downloadAgentYaml" in html and "downloadAgentMindmap" in html and "下载脑图" in html, "Agent artifacts must support copy plus YAML and mindmap download")
    require("agentGeneratedCaseArtifact" in html and "artifactHasValue(artifacts.matchedCases)" not in html and "Array.isArray(artifacts.matchedCases) && artifacts.matchedCases.length" in html, "Agent case artifact rendering must not treat an empty matchedCases array as real output")
    require("agentYamlArtifactPayload" in html and "agentYamlRefsFromArtifacts" in html and "本次 YAML 已按单用例拆分保存" in html, "Agent YAML artifact rendering must show split yamlRefs instead of only generatedYaml")
    require("apiTextRequest(`/file?module=" in html and "chunks.join('\\n\\n---\\n\\n')" in html, "Agent YAML download must fetch split YAML file contents when generatedYaml is empty")
    require("renderAgentReportArtifact" in html and "renderAgentSummaryArtifact" in html and "executionReports" in html and "yamlExecutionRefs" in html, "Agent report/summary artifacts must render as readable rich cards")
    require("agentCaseLabel" in html and "report-case-link" in html and "生成时参考的历史步骤" in html, "Agent reports must show the concrete case name and YAML reference examples")
    require("normalizeAgentReportJobs" in html and "agentReportOutcomeGroups" in html and "失败用例" in html and "通过用例" in html and "未完成 / 待判定" in html, "Agent Runner report must separate failed, passed, running, and unknown case outcomes")
    require("collectAgentReportProgressJobs" in html and "normalizeAgentReportJobs(report, normalizedReport, artifacts" in html and "artifacts.jobProgressByPhase" in html and "report_url" in html, "Agent Runner report must aggregate live Runner jobs and report URLs from jobProgressByPhase")
    require("isAgentDryRunPhase" in html and "if (isAgentDryRunPhase(phase)) return;" in html and "const reportJobs = normalizeAgentReportJobs(report, normalizedReport, artifacts || {})" in html, "Agent Runner report must not count smoke dry-run as phone execution")
    require(".agent-report-job-card.failed" in html and ".agent-report-status-pill.success" in html and "metric-danger" in html, "Agent Runner report outcomes must have distinct visual states for failed and passed cases")
    require("agentInputSummaryFromRun" in html and "agentInputSummaryHtml" in html and "本次输入资料" in html, "Agent history/detail pages must show the original target, Figma, files, and execution input")
    require("agent-input-chips" in html and "采用的 Figma 页面" in html and "上传资料" in html, "Agent input summary must be readable in cards instead of only showing runId")
    require("质量检查" in html and "renderAgentQualityArtifact" in html and "agent-quality-layers" in html and "完整用例、自动化 YAML、人工用例、Figma 图片" in html, "Agent quality report must render as readable layered cards")
    require("renderVisualReferenceReport" in html and "图片参考" in html and "上传截图" in html and "AI 判断" in html and "硬门禁" in html, "Agent quality report must show screenshot references and AI judgment status")
    require("agentInfoGrid" in html and "agentReadableList" in html and "agent-readable-panel" in html and "final-report-hero" in html, "Agent step details and final report must use readable card layouts")
    require("key === 'DONE' && runTerminal" in html and "前序步骤失败，Agent 流程未进入完成态" in html, "Agent terminal timeline must not leave the virtual DONE step pending after failure/cancel")
    require("captureAgentTimelineViewState" in html and "restoreAgentTimelineViewState" in html and "agentTimelineDetailToggled" in html and "agentRestoringTimelineDetails" in html, "Agent timeline technical logs must preserve open state and scroll during polling refresh")
    require("data-agent-timeline-detail-key" in html and "agent-technical-trace-body" in html, "Agent technical log details must use stable keys and a scrollable body")
    require("agentTechnicalTracePointer(event)" in html and "onpointerdown" in html, "Agent technical log clicks must not collapse the parent timeline step")
    require("agentRunnerRealtimeSummary" in html and "artifacts.jobProgress" in html and "jobProgressByPhase" in html, "Agent RUN_SONIC timeline summary must prefer live Runner progress data")
    require("renderGenerateYamlDetail" in html and "完整 YAML 生成主链" in html and "主链错误" in html, "Agent YAML generation step must show readable pipeline details")
    require("normalizeAgentReportArtifacts" in html and "isAgentYamlRef" in html and "normalizedAgentReportCounts" in html and "agentReportLooksYaml" in html, "Agent report rendering must not count YAML files as HTML execution reports")
    require("YAML 校验失败时，不能显示" not in html, "Implementation details should not be visible as instructional UI text")
    # Assets entry exists in sidebar as asset center
    require("用例资产" in html and 'data-workflow="assets"' in html, "Assets must be accessible from sidebar")
    require("function showAssetsCenter" in html and "assets-table" in html and "选择当前列表" in html, "Assets page must render a full-width asset table")
    require("toggleCurrentAssetRows" in html and "assetFileOp" in html and "deleteAssetFile" in html, "Assets page must support select-all, rename, move, and delete without leaving the directory")
    require("重命名</button>" in html and ">移动</button>" in html and ">删除</button>" in html, "Assets table rows must expose maintenance actions")
    require("function updateWorkbenchPanelMode" in html and "hide-jobs" in html and "'execute'" in html, "Only Agent and execution workflows should keep the right status panel")
    panel_workflows = html[html.index("const rightPanelWorkflows"):html.index("]);", html.index("const rightPanelWorkflows"))]
    require("'baseline'" not in panel_workflows and "'repair'" not in panel_workflows, "Baseline and repair pages must not keep the duplicate Runner history panel")
    require("const recentDoneLimit = activeWorkflow === 'execute' ? 6 : 18" in html, "Execution Runner panel must limit completed history while retaining active jobs")
    require("'repair',\n    'reports'" not in html and "'repair', 'reports'" not in html, "Report and asset-style pages should not keep a stale Agent side panel")
    require("你想让 Agent 测什么" in html and "启动全自动 Agent" in html, "Dashboard hero must present the simplified Agent workbench")
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
    require("GENERATION_RECORD_PAGE_SIZE = 20" in app_js and "loadMoreGenerationRecords" in app_js, "Generation history must render incrementally instead of creating every record at once")
    require("请先选择应用，再查看该应用的页面知识" in app_js, "Generation must not load a fallback application's knowledge before the user chooses an application")
    nginx_source = (ROOT / "deploy" / "nginx-midscene-task.conf").read_text(encoding="utf-8")
    require("location = /api-test" in nginx_source and "absolute_redirect off" in nginx_source, "The /api-test entry redirect must retain the public host and port")
    require("function jobTimelineHtml(job)" in html and "进度流水" in html, "Execution job detail must show progress timeline")
    require("单条/多条调试" in html and "multiple size=\"8\"" in html, "Execution modal must support selecting one or multiple tasks")
    require("不会触发 Sonic 测试套整套回归" in html and "每个任务只下发选中的一个 task" in html, "Single/multi-task execution must clearly state it does not run the full Sonic suite")
    require("Sonic 只负责已同步基线的测试套回归" in html, "Execution page must distinguish Runner debugging from Sonic suite regression")
    require("Sonic 维护" in html and "日常同步请在「用例资产」" in html and "清理可匹配旧步骤" in html, "Sonic maintenance page must clearly distinguish maintenance from normal YAML sync")
    require("刷新桥接脚本" in html and "refreshSonicBridgeScripts" in html, "Sonic config must expose one-click bridge script refresh")
    require("apiRequest('/sonic/refresh-bridges'" in html, "Bridge script refresh must call backend through apiRequest")
    require("不修改 YAML、不改基线、不触发执行" in html, "Bridge refresh confirmation must clearly distinguish it from YAML sync/execution")
    require("renderSonicPublishResult" in html and "单条用例同步结果" in html and "模块同步结果" in html, "Sonic publish must show explicit single/batch sync results")
    require("AI分析失败" in html and "生成修复 YAML" in html, "Dashboard must expose primary Agent and repair actions")
    require("Runner 进度" in html and "待我处理" in html and "Runner 当前任务" in html, "Execution right panel must focus on Runner progress")
    require("执行前设备检查" in html and "renderRunnerDevicePreflightCards" in html and "runner-preflight-card" in html and "runnerDeviceVersionLabel" in html, "Execution center must show Runner device/app preflight before install or run")
    require("function executionYamlRows" in html and "execution-yaml-table" in html, "Execution debug center must render an inline YAML table")
    require("选择要调试的 YAML" in html and "单条调试" in html and "整文件执行" in html, "Execution debug center must expose concrete run actions")
    require("selectExecutionModule" in html and "execution-yaml-search" in html, "Execution debug center must support module and YAML filtering")
    require("展开用例树" not in html and "查看 YAML 列表" not in html, "Execution debug center must not keep dead library-toggle actions")
    require("同步至 Sonic 平台" in html and "Sonic 同步" not in html and "Sonic同步" not in html and "设备同步" not in html, "Sonic sync UI copy must use 同步至 Sonic 平台")
    require("publishSelectedFilesToSonic" in html and "publishSonicBatchItems" in html and "同步当前已选至 Sonic 平台" in html and "selectedSonicFilesForCurrentFilters" in html, "Sonic batch sync must only publish selected YAML files from the current asset filter")
    require("await loadModules({force: true})" in html and "loadModules({force:true})" in html, "Sonic publish and asset refresh must force reload modules and sonic case rows")
    require("row.step_state === 'bridge'" in html and "(row.sonic || {}).step_state === 'bridge'" in html, "Asset Sonic status must treat bridge step state as synced")
    require("请求返回业务失败" in html and "请求失败：HTTP ${res.status}" in html and "HTTP 200" not in html, "API business failures must not be surfaced as HTTP 200 errors")
    require("function jobRunModeText" in html and "function markJobHandled" in html and "manual_confirmed" in html, "Runner side panel must label run mode and let users clear handled failures")
    require("function isRunnerExecutionJob" in html and "locallyHiddenRunnerJobIds" in html and "/^(gen|figma|mindmap|repair)_/i" in html, "Runner side panel must filter background/generated jobs and stale handled ids")
    require("activeWorkflow === 'assets'" in html and "selectedAssetRowsForCurrentFilters().map" in html, "Asset batch move/delete must use the current filtered selection")
    require("ASSET_PAGE_SIZE" in html and "MODULE_DIRECTORY_PAGE_SIZE" in html and "paginationHtml" in html and "setAssetListPage" in html, "Long YAML asset lists must render with pagination")
    require("取消任务" in html and "基线回归" in html and "调试执行" in html, "Runner side panel must expose cancel action and distinguish baseline from debug runs")
    require("取消运行" in html and "cancelAgentRunById" in html, "Agent confirmation cards must allow cancelling without entering the run detail")
    require("const canCancel = !agentRunIsTerminal(run)" in html and "canCancel ? `<button class=\"btn-sm danger\"" in html, "Running Agent history cards must expose direct cancellation")
    require("normalizeFailureAnalysis" in html and "SCRIPT_ISSUE" in html and "PRODUCT_BUG" in html and "ENV_ISSUE" in html and "UNKNOWN" in html, "AI repair must normalize and gate failure types")
    require("AI修复工作台" in html and "失败任务列表" in html and "结构化分析" in html and "YAML 修复草稿" in html, "AI repair must be an independent three-column workspace")
    require("原始 YAML" in html and "修复 YAML" in html and "Diff / 校验" in html, "YAML repair draft must show original, fixed, diff, and validation")
    require("/analyze-failure" in html and "used_full_logs" in html and "Runner 完整日志" in html, "Failed-job AI analysis must prefer backend full runner logs")
    require("function renderRepairDraftDetail" in html and "修复方式" in html and "AI 调用" in html and "使用的失败日志" in html and "修复草稿文件" in html, "Agent timeline must explain repair draft source, AI usage, target drafts, and failure evidence")
    require("function renderRerunDetail" in html and "agent-rerun-overview" in html and "重跑触发" in html and "AI 修复" in html and "固定设备重跑" in html and "function renderLearningDetail" in html and "沉淀内容" in html and "学习明细" in html, "Agent timeline must show task-level failure, AI repair, fixed-device rerun, and learning contents")
    require("执行前 dry-run" in html and "dry-run 拦截明细" in html and "YAML dry-run 结果" in html, "Agent timeline must expose dry-run checks and blockers")
    require("failureReason" in html and "failureType" in html and "失败类型：" in html, "Agent final report must show concrete Runner failure reasons")
    require("PRODUCT_BUG 不允许" not in html, "Implementation details must not leak as rough internal copy")
    require("apiRequest('/reports/cleanup'" in html and "apiRequest('/cases/mindmap-only-async'" in html and "apiRequest('/ui/generate-yaml-async'" in html, "Long-running write endpoints must use apiRequest")
    require('id="generate-business-options"' in html and "function renderGenerateBusinessOptions" in html and 'name="generate-business"' in html, "UI generation must render an explicit configured business selection")
    require('id="generate-application"' in html and "function selectedGenerateApplication" in html and "function handleGenerateApplicationChange" in html, "UI generation must choose an enabled configured application before its business lines")
    require('id="generate-app-package-detail"' in html and "readonly" in html and "renderGenerateApplicationOptions" in html, "UI generation must expose the selected application package as readonly detail")
    require("selectedGenerateApplication()?.package" in app_js and "renderGenerateBusinessOptions(\"\")" in app_js, "Changing UI generation application must clear an incompatible business selection")
    require("generationJobApplication" in app_js and "businessLineLabel(business, generationJobApplication(job).package)" in app_js, "Generation history must resolve business names with its saved application")
    require("taskBusinessLabel(business, task.name)" in (JS_DIR / "execution.js").read_text(encoding="utf-8"), "Multi-case navigation must resolve each business label with that case's application")
    require("function taskCaseBusinessEditable" in (JS_DIR / "execution.js").read_text(encoding="utf-8") and "仅保留历史业务展示" in (JS_DIR / "execution.js").read_text(encoding="utf-8"), "Disabled and historical-only cases must preserve business display without edit controls")
    require("return '未标注应用'" in (JS_DIR / "cases.js").read_text(encoding="utf-8") and "name: historicalApplicationName" in app_js, "Unresolved application history must use the safe label instead of a raw package")
    require('id="generate-app-config-action"' in html and "function updateGenerateSubmitState" in app_js and "openGenerateApplicationConfiguration" in app_js, "No-enabled-application generation state must disable submit and link to application configuration")
    require('id="task-app-enabled"' in html and "enabled: document.getElementById('task-app-enabled')" in agent_status_js, "Application configuration must expose and persist enabled status")
    require("apiRequest('/task-apps?include_disabled=1')" in (JS_DIR / "cases.js").read_text(encoding="utf-8"), "Application configuration must load disabled applications for history and re-enablement")
    require("taskCaseApplicationPackage(taskName)" in (JS_DIR / "execution.js").read_text(encoding="utf-8"), "Per-case business controls must prefer the case application package over the module fallback")
    require('id="task-app-business-lines"' in html and "function addTaskAppBusinessLine" in html and "business_lines: businessLines" in html, "Application configuration must manage business lines by Chinese display name")
    require("请选择所属业务" in html and "请选择所属业务（家用或共享）" not in html, "Business validation must not expose fixed legacy enum choices")
    require("business," in html and "resolve_ui_generation_business" in (ROOT / "task_server/services/yaml_service.py").read_text(encoding="utf-8"), "UI generation request and backend must preserve the selected business")
    require('id="agent-business"' in html and "agentBusinessOptionsHtml" in html and "const business = document.getElementById('agent-business')" in html, "Agent-created UI cases must use the same configured business contract")
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
    require("nav-group" in html and "接口测试" not in html and "配置" in html, "Sidebar navigation must remove the legacy API group while retaining task-oriented groups")
    api_test_link = re.search(r'<a\b[^>]*\bclass="[^"]*\bapi-test-link\b[^"]*"[^>]*\bhref="/api-test/"[^>]*>.*?</a>', html, re.DOTALL)
    require(api_test_link is not None, "Sidebar must include a same-tab API testing link")
    require("target=" not in api_test_link.group(0), "API testing navigation must keep the same browser tab")
    require('assets/icons/flask-conical.svg' in api_test_link.group(0) and '↗' not in api_test_link.group(0), "API testing navigation must use the local Lucide icon instead of a text symbol")
    require('data-workflow="api_' not in html and 'workflow-index">API<' not in html, "API testing navigation must not restore the legacy workflow or a letter-box icon")
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
    require("renderPlanDetail" in html and "业务分支" in html and "平台执行与门禁" in html and "Agent 启动前预览" in html and "需求显式候选（非业务路径）" in html and "AI 业务计划：尚未执行" in html, "Agent UI must keep startup candidates separate from the later AI-owned business plan")
    require("重跑后 AI 闭环" in html and "postRerunAutonomy" in html, "Agent rerun UI must expose the bounded latest-evidence repair cycle")
    require("agentFigmaPreviewItems" in html and "Figma 解析图片" in html and "agent-figma-grid" in html, "Agent Figma extraction must list parsed UI images for review")
    for source in ("manual", "requirement", "figma", "failed_job"):
        require(source in html, f"Agent source type missing: {source}")
    require("sourceType: source.sourceType" in html and "sourceRefs: source.sourceRefs" in html, "Agent payload must include sourceType/sourceRefs")
    require("agent-runner-device" in html and "renderAgentRunnerDeviceOptions" in html and "updateAgentRunnerDeviceHint" in html, "Agent workbench must expose online Runner/device selection")
    require("runnerId: runnerSelection.runner_id" in html and "deviceId: runnerSelection.device_id" in html and "deviceStrategy: runnerSelection.device_strategy" in html, "Agent payload must carry selected Runner/device strategy")
    require("selectedAgentAppPackage" in html and "appPackage," in html and "app_package: appPackage" in html, "Agent payload must carry selected app package")
    require("opt.dataset.package" in html and "runnerDeviceVersionLabel(device, appPackage)" in html, "Runner device version hint must use the selected app package")
    require("agentRunnerVersionSummary" in html and "版本：" in html and "refreshAgentRunnerDeviceByApp" in html, "Agent auto runner mode must show selected-app versions and refresh on app change")
    require("agent-start-layout" in html and "agent-form-section" in html and "agent-compact-grid" in html, "Agent start form must use grouped readable sections instead of one long form")
    require("agent-url-input" in html and "word-break: break-all" in html and "agent-device-hint" in html and "overflow-wrap: anywhere" in html, "Agent long Figma/device information must wrap and remain fully visible")
    require(".agent-field label {\n    display: inline-flex" in html and "label.agent-field > span {\n    display: inline-flex" in html and ".modal-label {\n    display: inline-flex" in html, "Agent and modal field labels must use highlighted label chips")
    require(".form-group label {\n    display: inline-flex" in html and ".figma-limit-field span {\n    display: inline-flex" in html, "Login and advanced Figma field labels must use highlighted label chips")
    require("agent-start-button" in html and "agent-action-buttons" in html and "openAgentAppInstall" in html, "Agent primary actions must stay explicit and clickable after layout optimization")
    require("DEFAULT_AGENT_APP_NAME" in html and "appendAgentAppOptions" in html and "智小白3D APP" in html, "Agent app selectors must default to 智小白3D APP even before /api/apps finishes")
    require("shouldHydrateRuns" in html and "ensureAgentRunsLoaded({ limit: 10 }).then" in html, "Agent workbench must render before background history refresh completes")
    require("agent-source-upload-copy" in html and "添加需求资料" in html and "添加资料" in html, "Agent source upload area must be visually obvious and actionable")
    require("loadRunnerDevices({force: true, quiet: true})" in html and "自动选择在线设备（推荐）" in html, "Agent workbench must load online Runner devices without re-rendering the whole dashboard")
    require("if (sourceType === 'manual')" in html and "hasFiles" in html and "sourceType = 'requirement'" in html and "sourceType = 'figma'" in html, "Agent frontend must promote manual source type when files or Figma are attached")
    require("executionMode: 'RUNNER_JOB'" in html, "Agent payload must default to Runner job execution instead of Sonic suite execution")
    require("输入来源：" in html and "整理输入来源" in html, "Agent preview/timeline copy must explain source preparation")
    require("Agent 执行阶段" in html and "AGENT_EXECUTION_PHASES" in html and "['PREPARE_SOURCE', '整理输入来源']" in html, "Agent progress must show source preparation before the AI planning phase")
    require("诊断与恢复" in html and "conditional: true" in html and "agentRecoveryPhaseUsed" in html, "Agent failure recovery must be a conditional phase instead of a fixed happy-path step")
    require("agent-checkpoint-trace" in html and "内部执行轨迹" in html and 'ontoggle="agentCheckpointTraceOpen=this.open"' in html, "Agent internal checkpoints must preserve the user toggle state across polling renders")
    require("AGENT_ARTIFACT_GROUPS" in html and "agent-artifact-layout" in html and "agent-artifact-nav-group" in html, "Agent artifacts must use grouped navigation instead of eleven flat tabs")
    require("agentArtifactState" in html and "等待前序阶段" in html and "失败恢复类产物只在出现可分析的执行失败时生成" in html, "Agent artifacts must explain pending and conditional states instead of showing a blank box")
    require("activeState === 'ready'" in html and "yamlReady" in html and "mindmapReady" in html, "Agent artifact actions must only appear after their real artifact exists")
    require("if (tab === 'failure') return renderAnalysisDetail" in html and "agent-failure-card-grid" in html and "AI 判断依据" in html and "技术详情" in html, "Agent failure artifacts must use progressive structured cards instead of raw JSON")
    require("captureAgentArtifactViewState" in html and "restoreAgentArtifactViewState" in html and "data-agent-run-id" in html and "boxScrollTop" in html, "Polling must preserve the active Agent artifact reading position")
    require("14 步全自动链路" not in html, "Agent timeline copy must not hard-code stale step count")
    require("timelineLiveTraceDetail" in html and "step-live-trace" in html, "Agent timeline must show live running trace when a step is expanded")
    require("agent-technical-trace" in html and "技术日志" in html and "stepDetailHtml" in html and html.index("stepDetailHtml ?") < html.index("technicalTraceDetail ?"), "Readable step results must appear before collapsed technical traces")
    require("rerunProgressHistory" in html and "progress.items" in html and "agentRerunCycleMetrics" in html and "失败恢复执行链" in html and "原脚本证据重试" in html and "AI 修复脚本验证" in html and "AI 修复与环境重试" in html and "原脚本重试" in html and "仅保留失败诊断" in html, "Agent rerun UI must preserve a readable causal chain across serial, mixed, diagnosis-only, and bounded repair cycles")
    require("stepName === 'RERUN'" in html and "agentTraceMessageText" in html and "正在准备失败任务重跑" in html, "Running rerun steps must render readable progress before the internal tool call finishes")
    require("agentRunnerProgressMetrics" in html and "jobProgressByPhase" in html and "Runner 真实执行累计" in html and "当前阶段" in html and "排队中" in html, "Runner detail must separate cumulative phase outcomes, active execution, queueing, and timeout limit")
    require("renderExecutionPrecheckDetail" in html and "precheck-warnings" in html and "blockers" in html, "Execution precheck detail must show blockers and warnings")
    require("match-keywords" in html and "匹配关键词" in html and "detail.reasons" in html, "Agent match detail must show concrete matched keywords and candidate reasons")
    require("renderAgentHistoryPage" in html and "Agent 运行记录" in html, "Agent history menu must render a dedicated history page")
    require("agentRunErrorHtml" in html and "无法加载 Agent 运行记录" in html and "请求超时，请稍后重试" in html, "Agent history refresh must show an error/retry state instead of staying in loading")
    require("agentHistoryRequestSeq" in html and "activeWorkflow !== 'agent_history'" in html and "timeoutMs: 15000" in html, "Agent history refresh must guard stale requests and use a bounded timeout")
    require("async function selectAgentRun" in html and "apiRequest(`/agent-runs/${encodeURIComponent(runId)}`" in html, "Agent history cards must fetch full run detail before showing timeline")
    require("deleteAgentRunById" in html and "删除记录" in html and "method: 'DELETE'" in html, "Agent terminal run history cards must allow deleting failed/completed records")
    require("agentRunsByCreatedDesc" in html and "agentRunSortTime" in html and "mergeAgentRun" in html, "Agent history updates must keep cards sorted by creation time instead of last update time")
    require("reportSummary" in html and "agentRunResultMeta" in html and "agentRunResultSummaryHtml" in html and "查看报告" in html and "openAgentRunReport" in html and "agent-run-card-time" in html and "${escapeHtml(agentRunDisplayTime(run))}" in html, "Agent history cards must show final report results, full timestamp and link directly to the report tab")
    require("nonSmokeAttempted" in html and "nonSmokePlanned" in html and "已计划，未进入扩展执行" in html and "bucketAttempted" in html and "rawPassed" in html and "通过 ${passed} 条 / 共 ${total} 条" in html and "${passed}/${total} 通过" not in html, "Agent history cards must separate planned non-smoke YAML from actually executed non-smoke results and avoid impossible or unclear passed/total ratios")
    require("finalAttempted" in html and "finalPassed" in html and "finalFailed" in html and "hasFinalCounts" in html and "修复恢复 ${result.recovered}" in html and "原始失败 ${result.rawFailed}" in html, "Agent history cards must use final logical report counts while keeping raw failed attempts as repair context only")
    require("agentRunFailureDetail" in html and "productFailed" in html and "产品缺陷 ${productFailed}" in html and "脚本问题 ${scriptFailed}" in html and "未判定 ${unknownFailed}" in html, "Agent history cards must distinguish product defects, script problems and unclassified failures instead of showing every failed test as the same failure")
    require("agent-run-history-card.partial" in html and "status-pill.partial" in html and "部分通过" in html, "Partial Agent report outcomes must have their own non-failed card state")
    require("agentRunSelectionSeq" in html and "openAgentRunTrace" in html and "selectionSeq !== agentRunSelectionSeq" in html, "Agent history detail loading must ignore stale click/request races")
    require("renderAgentConfirmPage" in html and "人工确认中心" in html, "Agent confirmation menu must render a dedicated pending-confirmation page")
    require("renderActiveWorkflowPage" in html and "renderAgentHistoryPage(options)" in html and "renderAgentConfirmPage({ refresh: false" in html, "Agent refresh routing must render the current Agent sub-page instead of stale workbench content")
    require("agentRiskDetailFrom" in html and "agentRiskDetailHtml" in html and "风险来源" in html and "触发片段" in html, "Agent high-risk confirmations must show concrete source and triggering snippet")
    require("renderAgentPageAfterRunUpdate" in html and "activeWorkflow === 'agent_confirm'" in html and "activeWorkflow === 'agent_history'" in html, "Agent confirm/cancel/refresh must update the current sub-page")
    require("Runner 单条调试" in html and "/run-request" in html, "Sonic status UI must route single/multi debugging to local Runner jobs")
    require("rerunGenerationSmokeCases" in html and "/cases/rerun-smoke" in html and "重跑冒烟" in html, "Generated smoke cases must expose a rerun action without re-uploading materials")
    require("generationSmokeAdjustmentHtml" in html and "generatedSmokeRefs" in html and "冒烟用例调整" in html and "首次生成时，Agent 会自动下发" in html and "编辑 YAML" in html and "当前已保存的 YAML" in html, "Generated smoke cases must auto-run first and remain editable before rerun")
    require("GENERATED_SMOKE_RERUN_DEFAULT_LIMIT = 3" in html and "generatedSmokeTargets" in html and "generatedSmokeRerunLimit(summary" in html and "重跑首批冒烟" in html and "重跑全部冒烟" in html and "run_all" in html, "Generated smoke rerun must default to at most 3 first-batch smoke cases and require an explicit all-smoke action")
    require("继续下一批可执行" in html and "执行全部当前可执行" in html and "remaining_executable" in html, "Generated executable rerun must support small-batch continuation and explicit full execution")
    require("agentGeneratedSmokeRerunLimit" in html and "重跑首批冒烟 ${escapeHtml(smokeLimit)}/${escapeHtml(smokeExecutableCount)}" in html and "重跑冒烟${smokeExecutableCount" not in html, "Agent artifact rerun button must show first-batch count instead of total executable smoke count")
    require("expandedBatches" in html and "expandedStopReason" in html and "第 ${escapeHtml(batch.batch || '-')} 批" in html, "Agent final report must expose expanded execution batches instead of looking smoke-only")
    require("summary.execution" in html and "summary.orchestration" in html and "Runner 通过" in html and "产品断言失败" in html and "脚本 / 环境 / 待归因" in html and "编排阻断" in html and "final-report-orchestration" in html, "Agent final report must separate real Runner outcomes, product failures, broken tests, and orchestration status")
    require("renderAgentStatusBreakdown" in html and "结果拆分" in html and "原始执行" in html and "修复验证" in html and "覆盖缺口" in html, "Agent final report must split final conclusion, original failures, repair validation, and coverage gaps")
    require("execution.logicalFailedCount" in html and "hasLogicalExecutionCounts" in html and "修复后通过" in html, "Agent final report must let logical recovery override stale raw failed attempts")
    require("runnerEvidenceJobs.length" in html and "failedJobsForSummary" in html and "reportLinksForSummary" in html, "Agent final report must override stale summary/failureAnalysis with live Runner evidence")
    require("runSonicSingleCase" not in html and "/sonic/run-case" not in html and "Sonic 临时套执行" not in html, "Frontend must not expose Sonic temporary-suite single-case execution")
    require("Trace 回放" in html and "/debug/traces" in html and "/debug/replay" in html and "/debug/diff" in html, "Execution center must expose Trace replay/diff debugger")
    require(
        "AppState.loading.agentRuns" in html
        and "AppState.loading.jobs" in html
        and "AppState.loading.modelConfig" in html,
        "Agent, jobs and model config loads must reuse in-flight requests",
    )
    require(
        "['dashboard', 'agent'].includes(sectionKey)" in html,
        "Agent history and confirmation pages must not preload the 10-row dashboard list",
    )
    require(
        "retryJobWithConfirmation" in html
        and "确认重跑" in html
        and "onclick=\"retryJobWithConfirmation" in html,
        "Failed-job rerun page must expose a confirmed real rerun action",
    )
    require(
        "agentHistorySelection" in html
        and "toggleAgentHistoryPageSelection" in html
        and "deleteSelectedAgentRuns" in html
        and "批量删除" in html,
        "Agent history must support current-page selection and batch deletion",
    )
    require(
        "已显示历史摘要，完整轨迹" in html,
        "Agent detail timeout must keep and explain the already-rendered summary",
    )
    require(
        "filterAgentTaskModelCatalog" in html
        and 'id="agent-model-search"' in html,
        "The explicitly loaded full model catalog must be searchable",
    )
    require(
        "filterTaskAppModules" in html
        and 'id="task-app-module-search"' in html
        and "未归属模块" in html,
        "Application module assignment must expose search and unassigned counts",
    )
    require(
        "renderEmptyState('trace')" in html
        and "renderEmptyState('trace_snapshot')" in html,
        "Trace and snapshot tabs must use dedicated empty states",
    )
    require(
        "EXECUTION_YAML_PAGE_SIZE" in state_js
        and "executionYamlPage" in state_js
        and "setExecutionYamlPage" in execution_js,
        "Execution YAML chooser must paginate large libraries instead of rendering every row",
    )
    require(
        "EXECUTION_RERUN_PAGE_SIZE" in state_js
        and "executionRerunFilters" in state_js
        and "setExecutionRerunPage" in execution_js
        and "搜索失败任务或模块" in execution_js,
        "Failed rerun view must support search, filtering and pagination",
    )
    require(
        "function summarizeJobError" in app_js
        and "summarizeJobError(error)" in app_js,
        "Runner cards must summarize large raw errors and keep full diagnostics in details",
    )
    require(
        "正在加载 YAML 用例" in agent_status_js
        and "AppState.loading.modules" in agent_status_js,
        "Asset center must show loading state instead of misleading zero counts",
    )
    require("renderEmptyState('app_install')" in execution_js, "App installation must not reuse the execution report empty state")
    require(
        "正在加载缺陷草稿" in agent_status_js
        and "读取失败，可以重试" in agent_status_js
        and "AppState.loading.feishuDrafts" in agent_status_js,
        "Bug draft navigation must render its own loading and retry states instead of leaving stale page content",
    )
    require(
        "UQG0220513008845" not in (JS_DIR / "utils.js").read_text(encoding="utf-8")
        and "android: {}" in (JS_DIR / "utils.js").read_text(encoding="utf-8")
        and "YAML 用例" in (JS_DIR / "utils.js").read_text(encoding="utf-8"),
        "New YAML starter must be device-neutral, valid and use consistent terminology",
    )
    for obsolete_kicker in ("REPORTS ·", "DEFECT DRAFTS ·", "APPLICATIONS ·", "EXECUTE ·"):
        require(obsolete_kicker not in html, f"User-facing decorative English kicker remains: {obsolete_kicker}")
    require(
        "failureTypeText(ft)" in html
        and "failureTypeText(draft.failureType" in html,
        "Failure codes in management views must render as Chinese labels",
    )
    require(
        "第 1 步" in html and "第 2 步" in html,
        "Agent setup steps must use readable Chinese ordinal labels",
    )
    require(
        "apiRequest('/task-apps')" in html
        and "apiRequest('/apps')" not in agent_workbench_js
        and "handleAgentApplicationChange" in agent_workbench_js,
        "Agent creation must use the configurable application catalog and refresh app-scoped business options",
    )
    require("[hidden]" in (ROOT / "css" / "app.css").read_text(encoding="utf-8"), "Semantic hidden controls must not be overridden by component display rules")
    require("第 ${escapeHtml(section.index)} 步" in html and "STEP ${escapeHtml(section.index)}" not in html, "Workflow guides must use Chinese step labels")
    trace_viewer = (ROOT / "trace-viewer.html").read_text(encoding="utf-8")
    require("Execution Trace Viewer" in trace_viewer and "/debug/traces" in trace_viewer and "sessionToken" in trace_viewer, "Trace viewer must render real trace data with session auth")
    require("一键应用推荐策略" in html and "applyRecommendedStrategy" in html, "Model config must support one-click recommended strategy")
    require("deleteGenerationMindmapRecord" in html and "/cases/mindmap-record" in html and "删除记录" in html, "Mindmap center must support deleting generation records")
    require("uploadApkInChunks" in execution_js and "/app-install/upload-chunk" in execution_js and "/app-install/upload-finish" in execution_js, "APK install uploads must use chunk upload endpoints")
    require("readAsDataURL(file)" not in execution_js and "contentBase64: dataUrl.split" not in execution_js, "APK install uploads must not send the whole APK as one Base64 JSON body")
    for active_asset in (
        "js/utils.js", "js/execution.js", "js/app.js", "js/state.js",
        "js/navigation.js", "js/agent-workbench.js", "js/agent-status.js",
        "css/app.css", "css/round5.css",
    ):
        require(f"{active_asset}?v=20260828-full-platform-ux" in html, f"Frontend cache version is stale for active asset: {active_asset}")
    require("function jobDeviceLabel" in html and "runnerDevices" in html and "runnerDeviceDisplayName(device)" in html, "Job rows must resolve device ids to public runner device names when available")
    require(
        "const job = activeJobs.find(isRunnerExecutionJob);" in html
        and "latestJobs.find(isRunnerExecutionJob)" not in html,
        "Runner current task card must not show the latest completed job as an active device occupation"
    )
    require("handleApkInstallJobsUpdated" in html and "loadRunnerDevices({force: true, quiet: true})" in html and "previousJobs" in html and "[0, 3000, 8000]" in html, "APK install completion must refresh runner devices and app versions automatically")
    require("closeMindmapCreateModal(options = {})" in html and "#modal-mindmap-create .modal-close, #modal-mindmap-create .btn-cancel" in html, "Mindmap create modal must remain closable while background generation is running")
    require("脑图生成任务已提交，已切到脑图中心查看进度" in html and "closeMindmapCreateModal({ quiet: true })" in html and "await showMindmapCenter();" in html, "Mindmap create modal must auto-close after background job submission")
    require("function isMindmapBackgroundJob" in html and "mindmapTaskSectionHtml" in html and "mindmapFilesSectionHtml" in html, "Mindmap center must render background task status separately from downloadable files")
    require("generation-record-sections" in html and "脑图生成任务" in html and "脑图文件" in html, "Mindmap center must split task progress and mindmap files into readable sections")
    require("function mindmapRecordTimeValue" in html and "mindmap_sort_ts" in html and "mindmap-compact-list" in html and "function mindmapTaskRow" in html, "Mindmap center must sort latest first and use compact rows instead of oversized cards")
    require("activeWorkspaceMode === 'mindmap'" in html and "await showMindmapCenter();" in html, "Mindmap background actions must refresh the mindmap center after cancel/retry")
    require("mindmapCenterRefreshTimer" in html and "scheduleMindmapCenterRefresh(taskRows)" in html and "pending', 'running" in html, "Mindmap center must auto-refresh while mindmap jobs are active")
    require("生成报告" in html and "openMindmapReportBuilder" in html, "Mindmap center must expose test report generation")
    require("mindmapReportSelectedCaseSetIds" in html and "openSelectedMindmapReportBuilder" in html, "Mindmap center must support selecting multiple mindmap files for one report")
    require("case_set_ids" in html and "selection_id" in html and "mindmapReportCaseSelectionId" in html, "Mindmap report builder must submit multi-mindmap case selections without case-id collisions")
    require("选择当前列表" in html and "生成合并报告" in html and "mindmap-record-check" in html, "Mindmap file list must expose multi-select report actions")
    require("/test-reports/cases" in html and "/test-reports/preview" in html and "/test-reports" in html, "Frontend must call mindmap test report APIs")
    require("mindmapReportToday()" in html and 'id="mindmap-report-start" type="date" value="${today}"' in html and 'id="mindmap-report-end" type="date" value="${today}"' in html, "Report builder must default test period to today")
    require("mindmapReportSelectedClientSides" in html and "mindmap-report-client-side" in html and "mini" in html and "Android" in html and "iOS" in html and "中台" in html and "后台" in html and "iPad" in html and "安卓pad" in html, "Report builder must expose all multi-select client side options")
    require('id="mindmap-report-env"' in html and '<option value="正式环境" selected>正式环境</option>' in html and "预发布环境" in html and '<option value="测试环境">测试环境</option>' in html, "Report builder must use fixed environment dropdown and default to production")
    require("DEFAULT_MINDMAP_REPORT_GOAL" in html and "验证需求核心流程是否符合预期，并确保核心业务流程不受影响。" in html and "<textarea id=\"mindmap-report-goal\"" in html, "Report builder must prefill an editable default test goal")
    require("mindmapReportDefectsPayload" in html and "mindmap-report-defect-fatal" in html and "mindmap-report-defect-serious" in html and "mindmap-report-defect-normal" in html and "mindmap-report-defect-minor" in html, "Report builder must support manual defect severity input")
    require("MINDMAP_REPORT_HISTORY_KEY" in html and "mindmapReportRememberMeta" in html and "deleteMindmapReportMemory" in html and "applyMindmapReportMemory" in html, "Report builder must persist tester and version history with delete support")
    require("applyMindmapReportRequirementMemory" in html and "syncMindmapReportVersionFromRequirement" in html and "mindmap-report-requirement-select" in html and "飞书需求" in html, "Report builder must support Feishu requirement selection and auto-fill version")
    require("mindmap-report-case-link-select" in html and "测试用例平台" in html and "applyMindmapReportCaseLinkMemory" in html, "Report builder must support selectable/manual case platform links")
    require("/test-reports/case-platform/search" in html and "searchMindmapReportCasePlatform" in html and "applyMindmapReportCasePlatformResult" in html and "mindmap-report-case-platform-results" in html, "Report builder must search and apply external case platform candidates")
    require("下载 Word" in html and "download?.word" in html and "format=doc" in html, "Report builder must expose Word export download")
    require("测试范围" in html and "测试人员" in html and "测试周期" in html and "涉及端侧" in html, "Report builder must expose required metadata fields")
    require("mindmap-report-layout" in html and "mindmap-report-scope" in html, "Report builder styles must be present")
    print({"ok": True, "file": str(HTML), "checks": 84})


if __name__ == "__main__":
    main()
