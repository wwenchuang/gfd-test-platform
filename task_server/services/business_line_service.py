"""Configurable business-line identities for UI and API test cases."""

import re

from task_server.config import TASK_APPS_FILE
from task_server.storage import read_json_file, unique_millis_id


PRIMARY_APP_PACKAGE = "com.kfb.model"
_DEFAULT_BUSINESS_LINES = (
    {"id": "home", "name": "家用", "enabled": True},
    {"id": "shared", "name": "共享", "enabled": True},
)
_LEGACY_NAME_TO_ID = {"家用": "home", "共享": "shared"}
_CHINESE_RE = re.compile(r"[\u3400-\u9fff]")
_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")


def default_business_lines() -> list:
    return [dict(item) for item in _DEFAULT_BUSINESS_LINES]


def _enabled(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "off", "disabled"}
    return value is not False


def normalize_business_lines(value, existing=None) -> list:
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
    if not any(item["enabled"] for item in rows):
        raise ValueError("至少保留一个启用的业务线")
    return rows


def _configured_app(package: str) -> dict:
    data = read_json_file(TASK_APPS_FILE, default={})
    apps = data if isinstance(data, list) else (data.get("apps") or [] if isinstance(data, dict) else [])
    for app in apps:
        if isinstance(app, dict) and str(app.get("package") or "").strip() == package:
            return app
    return {}


def configured_business_lines(app_package=PRIMARY_APP_PACKAGE, include_disabled=False) -> list:
    package = str(app_package or PRIMARY_APP_PACKAGE).strip() or PRIMARY_APP_PACKAGE
    app = _configured_app(package)
    raw = app.get("business_lines") if isinstance(app, dict) else None
    try:
        rows = normalize_business_lines(raw) if raw else default_business_lines()
    except ValueError:
        rows = default_business_lines()
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
    legacy_id = _LEGACY_NAME_TO_ID.get(raw, raw if raw in _LEGACY_NAME_TO_ID.values() else "")
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
    if raw == "home":
        return "家用"
    if raw == "shared":
        return "共享"
    return raw


def preferred_business_line_id(*values, app_package=PRIMARY_APP_PACKAGE) -> str:
    rows = configured_business_lines(app_package)
    joined = " ".join(str(value or "") for value in values)
    if "共享" in joined:
        shared = next((item for item in rows if item["name"] == "共享"), None)
        if shared:
            return shared["id"]
    return rows[0]["id"] if rows else ""
