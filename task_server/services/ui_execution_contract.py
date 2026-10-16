"""Trusted app, actor, and exact device contract for UI execution."""

from collections.abc import Mapping

from task_server import identity

from . import business_line_service, runner_service
from .operation_attribution import current_actor


UI_CONTRACT = "ui-v1"
CROSS_PLATFORM_ACTIONS = ("launch", "aiTap", "aiWaitFor", "aiAssert")


class AccessDenied(PermissionError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def require_ui_actor(actor=None, permission="ui.execute", app_id=None):
    """Return a fresh access profile for the trusted request actor or deny access."""
    trusted = current_actor()
    if trusted.get("kind") != "user" or not trusted.get("username") or not trusted.get("user_id"):
        raise AccessDenied("actor_untrusted")
    if actor is not None:
        if not isinstance(actor, Mapping) or any(actor.get(key) != trusted.get(key) for key in ("kind", "username", "user_id", "display_name")):
            raise AccessDenied("actor_untrusted")
    profile = identity.get_access_profile(trusted["username"])
    if not profile or profile.get("user_id") != trusted["user_id"] or profile.get("status") != "active" or profile.get("must_change_password"):
        raise AccessDenied("account_unavailable")
    if permission not in (profile.get("permissions") or []):
        raise AccessDenied("execution_forbidden" if permission == "ui.execute" else "permission_forbidden")
    if app_id:
        allowed = (profile.get("scope") or {}).get("ui_apps")
        if allowed != "*" and app_id not in (allowed or []):
            raise AccessDenied("app_forbidden")
    return profile


def _device_snapshot(device, target_package):
    snapshot = {key: device[key] for key in ("device_id", "platform", "status", "preflight_status", "model",
                                              "label", "android_version", "ios_version") if key in device}
    versions = []
    for app in device.get("installed_apps") or []:
        if isinstance(app, dict) and app.get("package") == target_package:
            versions.append({key: app[key] for key in ("package", "version_name", "version_code", "installed") if key in app})
    snapshot["app_versions"] = versions
    return snapshot


def resolve_ui_target(app_id, device_target, actor=None):
    """Resolve one authorized app to one registered Runner device, without fallback."""
    def blocked(code):
        return {"ok": False, "block_code": code}

    try:
        require_ui_actor(actor, app_id=app_id)
    except AccessDenied as error:
        return blocked(error.code)
    if not isinstance(device_target, dict) or any(not isinstance(device_target.get(key), str) or not device_target[key].strip()
                                                   for key in ("runner_id", "device_id", "platform")):
        return blocked("target_required")
    runner_id = device_target["runner_id"].strip()
    device_id = device_target["device_id"].strip()
    platform = device_target["platform"].strip()
    if platform not in ("android", "ios"):
        return blocked("platform_mismatch")
    app = business_line_service.configured_test_application(app_id)
    if not app or not app.get("enabled") or app.get("historical_only"):
        return blocked("app_unavailable")
    mappings = app.get("platform_mappings") or {}
    target_package = mappings.get(platform) if isinstance(mappings, dict) else None
    if platform == "android" and not target_package:
        target_package = app_id
    if not isinstance(target_package, str) or not target_package:
        return blocked("app_mapping_missing")
    runner = runner_service.list_runners().get(runner_id)
    if not runner:
        return blocked("runner_not_found")
    if runner.get("online") is not True:
        return blocked("runner_offline")
    capabilities = runner.get("capabilities") or {}
    if capabilities.get("ui_execution_contract") != UI_CONTRACT:
        return blocked("runner_upgrade_required")
    if capabilities.get("job_execution") is False:
        return blocked("runner_execution_disabled")
    device = next((item for item in runner.get("devices") or [] if item.get("device_id") == device_id), None)
    if not device:
        return blocked("device_not_found")
    if device.get("platform") != platform:
        return blocked("platform_mismatch")
    if platform not in (capabilities.get("ui_platforms") or []):
        return blocked("runner_capability_missing")
    if not set(CROSS_PLATFORM_ACTIONS).issubset(set(capabilities.get("ui_actions") or [])):
        return blocked("runner_capability_missing")
    if device.get("status") not in ("online", "device") or device.get("preflight_status") != "ready":
        return blocked("device_not_ready")
    return {"ok": True, "block_code": "", "app_id": app_id, "source_package": app_id,
            "platform": platform, "runner_id": runner_id, "device_id": device_id,
            "target_package": target_package,
            "platform_mappings": {"android": app_id, **mappings},
            "device_snapshot": _device_snapshot(device, target_package),
            "capabilities": {key: capabilities[key] for key in ("ui_execution_contract", "ui_platforms", "ui_actions", "android_baseline", "ui_dynamic_object_v1") if key in capabilities}}
