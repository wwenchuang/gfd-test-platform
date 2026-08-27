"""Shared presentation helpers for test result notifications."""

from __future__ import annotations

import re
from typing import Any

from .business_line_service import (
    PRIMARY_APP_PACKAGE,
    business_line_name,
    configured_business_lines,
    test_application_name,
)


_APPLICATION_ALIASES = {
    "智小白3d": "智小白3D",
    "智小白3dapp": "智小白3D",
    "智小白3d应用": "智小白3D",
    "3d打印": "智小白3D",
    "3d打印app": "智小白3D",
    "3d打印应用": "智小白3D",
    "家用": "智小白3D",
    "3d家用": "智小白3D",
    "智小白3d家用": "智小白3D",
    "智小白3d家用业务": "智小白3D",
    "共享": "智小白3D",
    "3d共享": "智小白3D",
    "智小白3d共享": "智小白3D",
    "智小白3d共享业务": "智小白3D",
}

_PACKAGE_APPLICATION_NAMES = {
    "com.kfb.model": "智小白3D",
}

def canonical_test_application_name(value: Any, package: str = "") -> str:
    """Return a stable card label without changing persisted application data."""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    package_name = str(package or "").strip()
    if package_name:
        return test_application_name(package_name, text)
    key = re.sub(r"[\s_-]+", "", text).lower()
    if key in _APPLICATION_ALIASES:
        return _APPLICATION_ALIASES[key]
    return "未标注应用"


def canonical_test_business_name(*values: Any, app_package=PRIMARY_APP_PACKAGE) -> str:
    """Resolve configured business lines before legacy name inference."""
    texts = [
        re.sub(r"\s+", " ", str(value or "")).strip()
        for value in values
        if value is not None
    ]
    keys = [re.sub(r"[\s_-]+", "", text).lower() for text in texts if text]
    configured = configured_business_lines(app_package, include_disabled=True)
    configured_by_id = {str(item.get("id") or ""): item.get("name") for item in configured}
    labels = {
        re.sub(r"[\s_-]+", "", str(item.get(field) or "")).lower(): item.get("name")
        for item in configured
        for field in ("id", "name")
        if item.get(field)
    }
    for key in keys:
        if key in labels:
            return labels[key]
    if app_package == PRIMARY_APP_PACKAGE and any("共享" in key for key in keys):
        return configured_by_id.get("shared") or "共享"
    if app_package == PRIMARY_APP_PACKAGE and any("家用" in key for key in keys):
        return configured_by_id.get("home") or "家用"
    if app_package == PRIMARY_APP_PACKAGE and any(
        key in _APPLICATION_ALIASES or "智小白3d" in key or key == "3d打印"
        for key in keys
    ):
        return configured_by_id.get("home") or (configured[0].get("name") if configured else "未标注业务")
    return "未标注业务"


def canonical_test_business_summary(values: Any, *fallback_values: Any, app_package=PRIMARY_APP_PACKAGE) -> str:
    """Summarize explicit case businesses before using legacy name inference."""
    configured = configured_business_lines(app_package, include_disabled=True)
    labels = {
        re.sub(r"[\s_-]+", "", str(item.get(field) or "")).lower(): item.get("name")
        for item in configured
        for field in ("id", "name")
        if item.get(field)
    }
    explicit = []
    for value in values or ():
        key = re.sub(r"[\s_-]+", "", str(value or "")).lower()
        label = labels.get(key)
        if label and label not in explicit:
            explicit.append(label)
    if explicit:
        return "、".join(explicit)
    return canonical_test_business_name(*fallback_values, app_package=app_package)


def canonical_test_scope_summary(items: Any, *fallback_values: Any, fallback_package="") -> tuple[str, str]:
    """Resolve application and business labels from immutable case/result snapshots."""
    application_labels = []
    business_labels = []
    default_package = str(fallback_package or PRIMARY_APP_PACKAGE).strip() or PRIMARY_APP_PACKAGE
    for raw in items or ():
        if not isinstance(raw, dict):
            continue
        package = str(raw.get("app_package") or raw.get("package") or fallback_package or "").strip()
        snapshot_name = raw.get("app_name") or raw.get("name") or ""
        if package or snapshot_name:
            application = canonical_test_application_name(snapshot_name, package)
            if application not in application_labels:
                application_labels.append(application)
        business = str(raw.get("business") or raw.get("business_name") or "").strip()
        if business:
            label = business_line_name(
                business,
                app_package=package or default_package,
                fallback="未标注业务",
            )
            if label not in business_labels:
                business_labels.append(label)

    fallback_name = next((value for value in fallback_values if str(value or "").strip()), "")
    if not application_labels:
        application_labels.append(canonical_test_application_name(fallback_name, fallback_package))
    if not business_labels:
        business_labels.append(
            canonical_test_business_name(*fallback_values, app_package=default_package)
        )
    return "、".join(application_labels), "、".join(business_labels)
