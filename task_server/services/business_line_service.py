"""Configurable business-line identities for UI and API test cases."""

import re

from task_server.config import TASK_APPS_FILE
from task_server.storage import read_json_file, unique_millis_id


PRIMARY_APP_PACKAGE = "com.kfb.model"
_DEFAULT_BUSINESS_LINES = (
    {"id": "home", "name": "家用", "enabled": True},
    {"id": "shared", "name": "共享", "enabled": True},
)
_DEFAULT_TEST_APPLICATIONS = (
    {
        "package": PRIMARY_APP_PACKAGE,
        "name": "智小白3D",
        "enabled": True,
        "business_lines": _DEFAULT_BUSINESS_LINES,
    },
    {
        "package": "com.xbxxhz.box",
        "name": "小白学习打印",
        "enabled": True,
    },
)
_LEGACY_NAME_TO_ID = {"家用": "home", "共享": "shared"}
_CHINESE_RE = re.compile(r"[\u3400-\u9fff]")
_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")


def default_business_lines() -> list:
    return [dict(item) for item in _DEFAULT_BUSINESS_LINES]


def default_test_applications() -> list:
    result = []
    for item in _DEFAULT_TEST_APPLICATIONS:
        app = dict(item)
        if "business_lines" in app:
            app["business_lines"] = [dict(line) for line in app["business_lines"]]
        result.append(app)
    return result


def _enabled(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "off", "disabled"}
    return value is not False


def normalize_business_lines(value, existing=None, require_enabled=True) -> list:
    if value is None:
        value = existing if existing is not None else default_business_lines()
    if not isinstance(value, list):
        raise ValueError("业务线配置格式不正确")
    if len(value) > 20:
        raise ValueError("业务线最多配置 20 个")

    existing_by_name = {
        str(item.get("name") or "").strip(): str(item.get("id") or "").strip()
        for item in (existing or [])
        if isinstance(item, dict)
    }
    rows = []
    seen_ids = set()
    seen_names = set()
    for raw in value:
        item = {"name": raw} if isinstance(raw, str) else (dict(raw) if isinstance(raw, dict) else {})
        name = str(item.get("name") or "").strip()
        if not name:
            raise ValueError("业务线中文名称不能为空")
        if len(name) > 20:
            raise ValueError("业务线中文名称最多 20 个字符")
        if not _CHINESE_RE.search(name):
            raise ValueError("业务线名称必须包含中文")
        name_key = name.casefold()
        if name_key in seen_names:
            raise ValueError("业务线中文名称不能重复")

        line_id = str(item.get("id") or "").strip()
        if not line_id:
            line_id = existing_by_name.get(name) or _LEGACY_NAME_TO_ID.get(name) or unique_millis_id("biz")
        if not _ID_RE.fullmatch(line_id):
            raise ValueError("业务线内部标识无效，请刷新后重试")
        if line_id in seen_ids:
            raise ValueError("业务线内部标识不能重复")

        rows.append({"id": line_id, "name": name, "enabled": _enabled(item.get("enabled", True))})
        seen_ids.add(line_id)
        seen_names.add(name_key)

    if not rows:
        raise ValueError("至少配置一个业务线")
    if require_enabled and not any(item["enabled"] for item in rows):
        raise ValueError("至少保留一个启用的业务线")
    return rows


def normalize_test_application(value, existing=None, require_business_lines=False, allow_legacy_name=False, allow_all_disabled_business_lines=False) -> dict:
    if not isinstance(value, dict):
        raise ValueError("应用配置格式不正确")
    existing = existing if isinstance(existing, dict) else {}
    package = str(value.get("package") or value.get("app_package") or value.get("appPackage") or "").strip()
    if not package:
        raise ValueError("包名不能为空")
    name = str(value.get("name") or value.get("app_name") or value.get("appName") or existing.get("name") or "").strip()
    if not name and allow_legacy_name:
        legacy = next((item for item in _DEFAULT_TEST_APPLICATIONS if item["package"] == package), {})
        name = str(legacy.get("name") or "").strip()
    if not name:
        raise ValueError("应用中文名称不能为空")
    if len(name) > 40:
        raise ValueError("应用中文名称最多 40 个字符")
    if not _CHINESE_RE.search(name):
        raise ValueError("应用名称必须包含中文")

    app = {"package": package, "name": name, "enabled": _enabled(value.get("enabled", existing.get("enabled", True)))}
    lines_provided = "business_lines" in value or "businessLines" in value
    raw_lines = value.get("business_lines", value.get("businessLines")) if lines_provided else existing.get("business_lines")
    if raw_lines is not None:
        app["business_lines"] = normalize_business_lines(
            raw_lines,
            existing=existing.get("business_lines"),
            require_enabled=not allow_all_disabled_business_lines,
        )
    elif package == PRIMARY_APP_PACKAGE:
        app["business_lines"] = default_business_lines()
    elif require_business_lines:
        raise ValueError("至少配置一个启用的业务线")
    return app


def _historical_test_application(value) -> dict:
    if not isinstance(value, dict):
        return {}
    package = str(value.get("package") or value.get("app_package") or value.get("appPackage") or "").strip()
    if not package:
        return {}
    app = {
        "package": package,
        "name": "未标注应用",
        "enabled": _enabled(value.get("enabled", True)),
        "historical_only": True,
    }
    raw_lines = value.get("business_lines", value.get("businessLines"))
    if raw_lines is not None:
        try:
            app["business_lines"] = normalize_business_lines(raw_lines, require_enabled=False)
        except ValueError:
            pass
    elif package == PRIMARY_APP_PACKAGE:
        app["business_lines"] = default_business_lines()
    return app


def _raw_configured_test_applications():
    data = read_json_file(TASK_APPS_FILE, default=None)
    if isinstance(data, list):
        apps = data
    elif isinstance(data, dict) and "apps" in data:
        apps = data.get("apps") if isinstance(data.get("apps"), list) else []
    else:
        return None
    return [item for item in apps if isinstance(item, dict)]


def configured_test_applications(include_disabled=False) -> list:
    raw_apps = _raw_configured_test_applications()
    source = default_test_applications() if raw_apps is None else raw_apps
    apps = []
    seen_packages = set()
    for raw in source:
        try:
            app = normalize_test_application(
                raw,
                allow_legacy_name=True,
                allow_all_disabled_business_lines=True,
            )
        except ValueError:
            app = _historical_test_application(raw)
        if not app:
            continue
        package = app["package"]
        if package in seen_packages:
            continue
        seen_packages.add(package)
        if include_disabled or (app["enabled"] and not app.get("historical_only")):
            apps.append(app)
    return apps


def configured_test_application(package, include_disabled=True) -> dict:
    package = str(package or "").strip()
    if not package:
        return {}
    for app in configured_test_applications(include_disabled=include_disabled):
        if app["package"] == package:
            return dict(app)
    return {}


def resolve_test_application(package="", snapshot_name="", business="", include_disabled=True) -> dict:
    """Resolve legacy case identity only when platform configuration is unambiguous."""
    package_value = str(package or "").strip()
    if package_value:
        return configured_test_application(
            package_value,
            include_disabled=include_disabled,
        )

    name = str(snapshot_name or "").strip()
    business_value = str(business or "").strip()
    apps = configured_test_applications(include_disabled=include_disabled)
    if name:
        named = [app for app in apps if str(app.get("name") or "").strip() == name]
        if len(named) == 1:
            return dict(named[0])
    if business_value:
        matched = [
            app
            for app in apps
            if any(
                business_value in {str(line.get("id") or ""), str(line.get("name") or "")}
                for line in configured_business_lines(app["package"], include_disabled=True)
            )
        ]
        if len(matched) == 1:
            return dict(matched[0])
    return {}


def test_application_name(package, snapshot_name="") -> str:
    app = configured_test_application(package, include_disabled=True)
    if app.get("name"):
        return app["name"]
    snapshot = str(snapshot_name or "").strip()
    return snapshot if _CHINESE_RE.search(snapshot) else "未标注应用"


def _configured_app(package: str) -> dict:
    return configured_test_application(package, include_disabled=True)


def configured_business_lines(app_package=PRIMARY_APP_PACKAGE, include_disabled=False) -> list:
    package = str(app_package or PRIMARY_APP_PACKAGE).strip() or PRIMARY_APP_PACKAGE
    app = _configured_app(package)
    raw = app.get("business_lines") if isinstance(app, dict) else None
    if raw is None:
        rows = default_business_lines() if package == PRIMARY_APP_PACKAGE else []
    else:
        try:
            rows = normalize_business_lines(raw, require_enabled=False)
        except ValueError:
            rows = default_business_lines() if package == PRIMARY_APP_PACKAGE else []
    if include_disabled:
        return rows
    return [dict(item) for item in rows if item.get("enabled")]


def business_line_id(value, app_package=PRIMARY_APP_PACKAGE, require_active=False) -> str:
    raw = str(value or "").strip()
    if not raw:
        if require_active:
            raise ValueError("请选择所属业务")
        return ""
    rows = configured_business_lines(app_package, include_disabled=True)
    matched = next((item for item in rows if raw in {item["id"], item["name"]}), None)
    if matched:
        if require_active and not matched.get("enabled"):
            raise ValueError(f"所属业务“{matched['name']}”已停用，请重新选择")
        return matched["id"]
    legacy_id = _LEGACY_NAME_TO_ID.get(raw, raw if raw in _LEGACY_NAME_TO_ID.values() else "") if app_package == PRIMARY_APP_PACKAGE else ""
    if legacy_id:
        if require_active:
            raise ValueError("所属业务未配置或已停用，请重新选择")
        return legacy_id
    if require_active:
        raise ValueError("请选择已配置且启用的所属业务")
    return raw


def business_line_name(value, app_package=PRIMARY_APP_PACKAGE, fallback="未标注业务") -> str:
    raw = str(value or "").strip()
    if not raw:
        return fallback
    rows = configured_business_lines(app_package, include_disabled=True)
    matched = next((item for item in rows if raw in {item["id"], item["name"]}), None)
    if matched:
        return matched["name"]
    if app_package == PRIMARY_APP_PACKAGE and raw == "home":
        return "家用"
    if app_package == PRIMARY_APP_PACKAGE and raw == "shared":
        return "共享"
    return raw if _CHINESE_RE.search(raw) else fallback


def preferred_business_line_id(*values, app_package=PRIMARY_APP_PACKAGE) -> str:
    rows = configured_business_lines(app_package)
    joined = " ".join(str(value or "") for value in values)
    if "共享" in joined:
        shared = next((item for item in rows if item["name"] == "共享"), None)
        if shared:
            return shared["id"]
    return rows[0]["id"] if rows else ""
