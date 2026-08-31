"""Main-platform action and application boundaries, independent of UI visibility."""

from urllib.parse import unquote


class AccessDenied(PermissionError):
    pass


COLLECTIONS = {
    "/api/apps", "/api/task-apps", "/api/modules", "/api/yaml-stats",
    "/api/task-meta", "/api/jobs", "/api/agent-runs", "/api/reports",
    "/api/tasks", "/api/cases", "/api/cases/mindmaps", "/api/knowledge/apps",
    "/api/repair-drafts", "/api/feishu-drafts", "/api/test-reports",
}
CATALOGS = {"/api/models", "/api/agent-tools", "/api/runners", "/api/sonic/config"}
SUMMARY_ROUTES = {
    "/api/cases/summary", "/api/cases/mindmap", "/api/cases/mindmap-record",
    "/api/cases/ui-designs", "/api/cases/ui-design-image", "/api/cases/ui-design-exclusion",
    "/api/cases/rerun-smoke", "/api/ui/regenerate-yaml-async", "/api/cases/generate",
}
READ_PREFIXES = (
    "/api/file", "/api/ui/", "/api/cases", "/api/knowledge/",
    "/api/baseline/", "/api/jobs/", "/api/agent-runs/", "/api/assets/",
)
EDIT_ROUTES = {
    "/api/convert-cases-json", "/api/generate-yaml", "/api/assets/upload",
    "/api/knowledge/page", "/api/knowledge/analyze", "/api/figma/parse",
    "/api/figma/parse-async", "/api/figma/import", "/api/cases/generate",
    "/api/ui/generate-yaml", "/api/ui/generate-yaml-async", "/api/cases/mindmap",
    "/api/cases/mindmap-only-async", "/api/cases/ui-designs", "/api/cases/ui-design-exclusion",
    "/api/ui/regenerate-yaml-async", "/api/file", "/api/file/op", "/api/files/op",
    "/api/file/restore", "/api/file/repair-latest", "/api/file/repair-task-latest",
    "/api/file/repair-latest-async", "/api/file/repair-task-latest-async",
    "/api/agent-context", "/api/agent-runs/preview", "/api/repair-drafts",
    "/api/repair-drafts/reject", "/api/repair-drafts/apply", "/api/cases/business",
}
EXECUTE_ROUTES = {"/api/run-request", "/api/cases/rerun-smoke", "/api/agent-runs/start", "/api/yaml/dry-run"}
SHARED_AI_ROUTES = {
    "/api/ui/generate-yaml", "/api/ui/generate-yaml-async", "/api/ui/regenerate-yaml-async",
    "/api/cases/mindmap-only-async", "/api/file/repair-latest", "/api/file/repair-task-latest",
    "/api/file/repair-latest-async", "/api/file/repair-task-latest-async",
}
FILE_REPAIR_ROUTES = {
    "/api/file/repair-latest", "/api/file/repair-task-latest",
    "/api/file/repair-latest-async", "/api/file/repair-task-latest-async",
}
MACHINE_ROUTES = {
    ("GET", "/api/runner/jobs/next"), ("GET", "/api/app-install/package"),
    ("POST", "/api/runner/heartbeat"), ("POST", "/report"),
    ("POST", "/api/report/chunk"), ("POST", "/api/report/chunk-finish"),
    ("POST", "/api/sonic/suite-complete"), ("POST", "/api/sonic/suite-report"),
    ("POST", "/api/sonic/report-ready"), ("POST", "/api/sonic/result"),
}
MACHINE_READ_ROUTES = {
    "/api/sonic/runtime-env", "/api/sonic/case", "/api/sonic/case-yaml",
    "/api/sonic/bridge-groovy", "/api/modules", "/api/runners",
}


def application_catalog():
    from task_server.services.job_service import load_task_apps
    return load_task_apps().get("apps", [])


def filter_scope_options(profile, options):
    def allowed(kind, identifier):
        values = profile.get("scope", {}).get(kind, [])
        return profile.get("is_superuser") or values == "*" or identifier in values

    return {
        kind: [item for item in options.get(kind, []) if allowed(kind, item["id"])
               and (kind != "api_environments" or allowed("api_projects", item.get("project_id")))]
        for kind in ("ui_apps", "api_projects", "api_environments")
    }


def _audit_operation(*args):
    from task_server.identity import audit_event
    try:
        audit_event(*args)
    except Exception as error:
        import logging
        logging.getLogger(__name__).warning("主平台操作记录写入失败：%s", type(error).__name__)


def load_access_record(kind, identifier):
    # Use persisted records only. Request-supplied app names cannot authorize a foreign ID.
    if not identifier or "/" in identifier or "\\" in identifier or identifier in {".", ".."}:
        return None
    if kind == "agent":
        from task_server.services.agent_service import get_agent_run
        return get_agent_run(identifier)
    if kind == "job":
        from task_server.services.job_service import get_job
        return get_job(identifier)
    if kind == "generation":
        from task_server.services.yaml_service import load_generate_job
        return load_generate_job(identifier)
    if kind == "summary":
        from task_server.services.yaml_service import generation_summary_path
        from task_server.storage import read_json_file
        return read_json_file(generation_summary_path(identifier), default=None)
    if kind == "asset":
        from task_server.services.knowledge_service import asset_meta_path
        from task_server.storage import read_json_file
        return read_json_file(asset_meta_path(identifier), default=None)
    if kind == "case":
        from task_server.services.yaml_service import cases_path
        from task_server.storage import read_json_file
        return read_json_file(cases_path(identifier), default=None)
    return None


class MainAccess:
    def __init__(self, profile, catalog=None):
        self.profile = profile or {}
        self.permissions = set(self.profile.get("permissions") or [])
        self.all_apps = self.profile.get("scope", {}).get("ui_apps") == "*"
        self.allowed_apps = set(self.profile.get("scope", {}).get("ui_apps") or [])
        self.module_apps = {}
        for app in application_catalog() if catalog is None else catalog:
            package = app.get("package")
            if not package:
                continue
            for module in app.get("modules") or []:
                self.module_apps.setdefault(module, set()).add(package)

    def require(self, permission):
        if not self.profile.get("is_superuser") and permission not in self.permissions:
            raise AccessDenied(f"没有操作权限（{permission}），请联系管理员调整角色")

    def module_visible(self, module):
        if not isinstance(module, str) or not module or "/" in module or "\\" in module or module in {".", ".."}:
            return False
        apps = self.module_apps.get(module, set())
        return bool(apps) and apps <= self.allowed_apps

    def record_apps(self, record):
        if not isinstance(record, dict):
            return set()
        result = set()
        module = record.get("module")
        if module:
            result.update(self.module_apps.get(module, set()))
            if not result:
                return {None}
        for key in ("package", "app_package", "appPackage"):
            if isinstance(record.get(key), str) and record[key]:
                result.add(record[key])
        for key in ("input", "source_refs", "sourceRefs", "request", "request_data", "requestData"):
            nested = record.get(key)
            if isinstance(nested, dict):
                result.update(self.record_apps(nested))
        return result

    def visible(self, record):
        if self.profile.get("is_superuser") or self.all_apps:
            return True
        apps = self.record_apps(record)
        return bool(apps) and apps <= self.allowed_apps

    def _require_record(self, record):
        if not self.visible(record):
            raise AccessDenied("对象不在已授权的数据范围内，或历史数据尚未关联应用，请联系管理员")
        if isinstance(record, dict) and record.get("module"):
            for key in ("file", "yaml_file"):
                if record.get(key):
                    self._require_file_path(record["module"], record[key])

    def _require_module(self, module):
        if not self.module_visible(module):
            raise AccessDenied("模块不在已授权的数据范围内，请联系管理员检查模块所属应用")

    def _require_file_path(self, module, filename):
        from pathlib import Path
        from task_server.config import TASK_DIR
        if not isinstance(filename, str) or filename.startswith("/") or "\\" in filename or "\x00" in filename or any(part in {".", ".."} for part in filename.split("/")):
            raise AccessDenied("文件路径必须位于已授权模块内，不能使用跨目录路径")
        if module:
            try:
                root = Path(TASK_DIR).resolve()
                module_root = (root / module).resolve()
                module_root.relative_to(root)
                (module_root / filename).resolve().relative_to(module_root)
            except (ValueError, OSError, RuntimeError):
                raise AccessDenied("文件路径或链接指向了未授权模块，请检查文件所属目录") from None

    def _check_supplied_targets(self, values):
        for key in ("module", "mod", "targetModule", "target_module"):
            if values.get(key):
                self._require_module(values[key])
        for key in ("app_package", "appPackage"):
            if values.get(key):
                self._require_record({"app_package": values[key]})
        for key in ("file", "targetFile", "target_file"):
            if values.get(key):
                module = values.get("module") or values.get("mod")
                if key != "file":
                    module = values.get("targetModule") or values.get("target_module") or module
                self._require_file_path(module, values[key])

    def _require_summary(self, identifier):
        record = load_access_record("summary", identifier) or load_access_record("asset", identifier)
        self._require_record(record)

    def check(self, method, path, qs=None, body=None):
        if self.profile.get("must_change_password"):
            raise AccessDenied("请先修改密码，再使用平台功能")
        if self.profile.get("is_superuser"):
            return
        qs, body = qs or {}, body or {}
        method = method.upper()
        permission = self.permission_for(method, path, qs, body)
        self.require(permission)
        parts = path.strip("/").split("/")
        saved_job = None
        if method == "POST" and len(parts) == 4 and parts[1] == "jobs" and parts[3] in {"retry", "repair"}:
            from task_server.config import safe_bool
            saved_job = load_access_record("job", unquote(parts[2]))
            inherited = saved_job or {}
            run_mode = (body.get("run_mode") or body.get("runMode")) if parts[3] == "retry" else None
            automatic_repair = parts[3] == "retry" and safe_bool(inherited.get("auto_optimize"))
            if (run_mode or inherited.get("run_mode")) == "baseline" or automatic_repair:
                self.require("ui.baseline")
            if automatic_repair and not self.all_apps:
                raise AccessDenied("原任务启用了共用基线自动修复，需要完整 UI 应用数据范围；请新建普通执行任务")
        if method == "POST" and len(parts) == 5 and parts[1:3] == ["ui", "generate-jobs"] and parts[4] == "retry":
            generation = load_access_record("generation", unquote(parts[3])) or {}
            inherited_request = generation.get("request_data") or generation.get("requestData") or {}
            if isinstance(inherited_request, dict):
                self.require(self.permission_for("POST", "/api/ui/generate-yaml", {}, inherited_request))
        if path.startswith("/api/agent-runs/") and method not in {"GET", "HEAD", "DELETE"}:
            self.require("platform.configure")
            self.require("ui.execute")
            if not self.all_apps:
                raise AccessDenied("Agent 自动编排包含跨应用检索，当前仅向完整应用范围的平台管理员开放")
        if self.all_apps:
            return
        if permission in {"platform.configure", "platform.notify"}:
            raise AccessDenied("此操作影响平台共用数据，需要完整应用数据范围及对应管理权限")
        if path in SHARED_AI_ROUTES or (method == "POST" and path.startswith("/api/ui/generate-jobs/")) or (method == "POST" and path.startswith("/api/jobs/") and path.endswith("/repair")):
            raise AccessDenied("此 AI 操作会检索共用基线库，当前需要完整 UI 应用数据范围；可先手工编辑和执行已授权用例，或联系管理员处理")
        if path in CATALOGS or (method in {"GET", "HEAD"} and path in COLLECTIONS):
            if path in {"/api/cases", "/api/yaml-stats"} and qs.get("module"):
                self._require_module(qs["module"])
            return
        # Some legacy handlers prefer query IDs, others prefer body IDs. Check both.
        for supplied in (qs, body):
            self._check_supplied_targets(supplied)
            for key in ("case_set_id", "caseSetId"):
                if supplied.get(key):
                    self._require_summary(supplied[key])
            if path in SUMMARY_ROUTES and supplied.get("id"):
                self._require_summary(supplied["id"])
        values = {**qs, **body} if method in {"POST", "PUT"} else qs
        if path == "/api/file" or path.startswith("/api/file/") or path in {"/api/run-request", "/api/baseline/page-refs", "/api/cases/business"}:
            self._require_module(values.get("module"))
            if path in {"/api/file/op", "/api/files/op"}:
                self._require_module(values.get("targetModule") or values.get("target_module") or values.get("module"))
            return
        if path == "/api/files/op":
            self._require_module(values.get("targetModule") or values.get("target_module"))
            items = values.get("items")
            if not isinstance(items, list) or not items:
                raise AccessDenied("请明确选择已授权的源文件")
            for item in items:
                self._require_module(item.get("module") if isinstance(item, dict) else None)
                self._check_supplied_targets(item)
            return
        if len(parts) >= 3 and parts[1] == "agent-runs" and parts[2] not in {"start", "preview"}:
            self._require_record(load_access_record("agent", unquote(parts[2])))
            return
        if len(parts) >= 3 and parts[1] == "jobs":
            self._require_record(saved_job or load_access_record("job", unquote(parts[2])))
            return
        if path == "/api/ui/generate-status" or path.startswith("/api/ui/generate-jobs/"):
            identifier = path.split("/")[-1] if path.startswith("/api/ui/generate-jobs/") else qs.get("job_id") or qs.get("id")
            self._require_record(load_access_record("generation", identifier))
            return
        summary_id = values.get("case_set_id") or values.get("caseSetId")
        if not summary_id and path in SUMMARY_ROUTES:
            summary_id = values.get("id")
        if summary_id and path in SUMMARY_ROUTES:
            self._require_summary(summary_id)
            return
        if path == "/api/assets/upload":
            self._require_module(values.get("module"))
            return
        if path.startswith(("/api/assets/", "/api/cases/")) and len(parts) == 3 and method in {"GET", "HEAD"}:
            self._require_summary(unquote(parts[2]))
            return
        if path.startswith("/api/knowledge/") and path in {"/api/knowledge/page", "/api/knowledge/pages", "/api/knowledge/screenshot", "/api/knowledge/analyze"}:
            self._require_record({"app_package": values.get("app_package") or values.get("appPackage")})
            return
        if path in {"/api/agent-runs/start", "/api/agent-runs/preview", "/api/ui/generate-yaml", "/api/ui/generate-yaml-async", "/api/cases/generate", "/api/cases/mindmap-only-async"}:
            if values.get("module"):
                self._require_module(values["module"])
            self._require_record({"app_package": values.get("app_package") or values.get("appPackage")})
            return
        # Arbitrary legacy file IDs, global diagnostics and unknown shapes stay closed.
        raise AccessDenied("该历史或跨应用操作尚不能确认数据归属，请由具有完整数据范围的管理员处理")

    def permission_for(self, method, path, qs, body):
        from task_server.config import safe_bool
        if method in {"GET", "HEAD"}:
            if path == "/api/yaml/baseline-cache/status" or path.startswith(("/api/debug/", "/api/preflight/", "/api/platform/")):
                return "platform.configure"
            if path in COLLECTIONS or path in CATALOGS or path.startswith(READ_PREFIXES):
                return "ui.view"
            return "platform.configure"
        if path.startswith("/api/feishu-drafts"):
            return "platform.notify"
        if path in FILE_REPAIR_ROUTES and safe_bool(body.get("createJob"), True):
            self.require("ui.execute")
            self.require("ui.baseline")
        if body.get("createJob") or body.get("create_job") or (path.startswith("/api/jobs/") and path.endswith("/repair")):
            self.require("ui.execute")
        if (body.get("run_mode") or body.get("runMode")) == "baseline" or body.get("autoOptimize") or body.get("auto_optimize"):
            self.require("ui.baseline")
        if path == "/api/file/status" or path.startswith("/api/baseline/") or path.startswith("/api/yaml/baseline-cache/"):
            return "ui.baseline"
        if method == "DELETE":
            if path in {"/api/task-app", "/api/module"}:
                return "platform.configure"
            if path.startswith(READ_PREFIXES):
                return "ui.delete"
            return "platform.configure"
        if path in {"/api/file/op", "/api/files/op"} and body.get("op", "move" if path == "/api/files/op" else "copy") in {"move", "rename"}:
            self.require("ui.delete")
        if path in EXECUTE_ROUTES or (path.startswith(("/api/agent-runs/", "/api/jobs/")) and path.endswith(("/confirm", "/cancel", "/retry"))):
            return "ui.execute"
        if path in EDIT_ROUTES or path.startswith("/api/ui/generate-jobs/") or (path.startswith("/api/jobs/") and path.endswith(("/repair", "/analyze-failure", "/review"))):
            return "ui.edit"
        return "platform.configure"

    def filter(self, path, payload):
        if not isinstance(payload, dict) or self.profile.get("is_superuser"):
            return payload
        result = dict(payload)
        if path in {"/api/apps", "/api/task-apps"}:
            result["apps"] = [dict(app) for app in result.get("apps", []) if self.visible(app)]
            if "platform.configure" not in self.permissions:
                for app in result["apps"]:
                    for key in ("feishu_webhook", "feishuWebhook", "feishu_bot", "token", "secret"):
                        app.pop(key, None)
            return result
        if self.all_apps:
            return result
        if path == "/api/modules":
            return {key: value for key, value in result.items() if self.module_visible(key)}
        if path == "/api/yaml-stats":
            result["stats"] = {key: value for key, value in result.get("stats", {}).items() if self.module_visible(key)}
        if path == "/api/task-meta":
            result["meta"] = {key: value for key, value in result.get("meta", {}).items() if self.visible(value)}
        if path == "/api/knowledge/apps":
            result["apps"] = [app for app in result.get("apps", []) if app in self.allowed_apps]
            result["appDetails"] = [app for app in result.get("appDetails", []) if self.visible(app)]
        for key in ("jobs", "background_jobs", "runs", "reports", "tasks", "cases", "mindmaps", "drafts"):
            if isinstance(result.get(key), list):
                result[key] = [record for record in result[key] if self.visible(record)]
                if "total" in result and path != "/api/cases":
                    result["total"] = len(result[key])
        if "history_scope" in result:
            result["history_scope"] = {**result["history_scope"], "runner_returned": len(result.get("jobs", [])), "background_returned": len(result.get("background_jobs", []))}
        return result


def prepare_request_access(handler, method, path, qs):
    """Return True when an identity endpoint or denied request is fully handled."""
    from task_server.auth import bearer_token, verify_session_token, is_authorized_with_query
    from task_server.identity import get_access_profile
    from task_server.identity_http import handle_auth_request

    # A handler may serve more than one HTTP request; don't keep another request's body/profile.
    handler.__dict__.pop("_parsed_body", None)
    handler.__dict__.pop("_main_access", None)
    if handle_auth_request(handler, method, path, qs):
        return True
    if path.startswith("/api/api-testing/"):
        return False
    if path == "/api/health" or (not path.startswith("/api/") and path != "/report"):
        return False
    machine_only = (method, path) in MACHINE_ROUTES or (method == "POST" and path.startswith("/api/runner/jobs/"))
    if machine_only or (method in {"GET", "HEAD"} and path in MACHINE_READ_ROUTES):
        # Strip browser credentials: legacy callbacks also accept a user session,
        # but a member session must never impersonate a Runner result or heartbeat.
        if is_authorized_with_query({"x-token": handler.headers.get("x-token", "")}, qs):
            return False
    session = verify_session_token(bearer_token(handler.headers))
    profile = get_access_profile((session or {}).get("user", "")) if session else None
    if not profile:
        handler._json({"ok": False, "error": "登录已失效，请重新登录", "code": "unauthorized"}, 401)
        return True
    policy = MainAccess(profile)
    try:
        if machine_only:
            raise AccessDenied("此接口仅供 Runner/Sonic 机器凭据回传，请通过平台任务入口执行操作")
        if path == "/api/auth/scope-options":
            if method != "GET":
                raise AccessDenied("此接口仅支持查询")
            if profile.get("must_change_password"):
                raise AccessDenied("请先修改密码")
            policy.require("auth.manage")
            options = {"ui_apps": [{"id": row["package"], "name": row.get("name") or row["package"]} for row in application_catalog() if row.get("package")]}
            try:
                from task_server.api_testing.config import ApiTestingSettings
                if ApiTestingSettings.from_env().enabled:
                    from task_server.api_testing.access import list_identity_scope_options
                    options.update(list_identity_scope_options())
                else:
                    options.update(api_projects=[], api_environments=[])
            except Exception:
                handler._json({"ok": False, "code": "scope_options_unavailable", "error": "暂时无法读取可授权的接口项目和环境，原授权未变。请稍后重试；持续失败时请管理员检查接口数据库和配置。"}, 503)
                return True
            handler._json({"ok": True, **filter_scope_options(profile, options)})
            return True
        body = {}
        if method in {"POST", "PUT"} and not profile.get("is_superuser"):
            if not handler._body_size_allowed(path):
                return True
            body = handler._body()
            if not isinstance(body, dict):
                handler._json({"ok": False, "error": "请求内容必须是 JSON 对象"}, 400)
                return True
        policy.check(method, path, qs, body)
        handler._main_access = policy
        handler._access_method = method
        handler._access_path = path
    except AccessDenied as error:
        _audit_operation(profile["username"], "access.denied", path, {"method": method})
        handler._json({"ok": False, "error": str(error), "code": "forbidden"}, 403)
        return True
    return False


def filter_access_response(handler, payload, code):
    policy = getattr(handler, "_main_access", None)
    if policy is None:
        return payload
    path = getattr(handler, "_access_path", "")
    method = getattr(handler, "_access_method", "GET")
    if method not in {"GET", "HEAD"}:
        _audit_operation(policy.profile["username"], "operation.result", path, {"method": method, "status": code, "ok": code < 400 and bool(payload.get("ok", True)) if isinstance(payload, dict) else code < 400})
    return policy.filter(path, payload)
