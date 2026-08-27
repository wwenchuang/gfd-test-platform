"""Shared presentation helpers for test result notifications."""

from __future__ import annotations

import re
from typing import Any

from .business_line_service import configured_business_lines


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
    key = re.sub(r"[\s_-]+", "", text).lower()
    if key in _APPLICATION_ALIASES:
        return _APPLICATION_ALIASES[key]
    if text:
        return text
    package_name = str(package or "").strip()
    return _PACKAGE_APPLICATION_NAMES.get(package_name, package_name or "未命名应用")


def canonical_test_business_name(*values: Any) -> str:
    """Resolve configured business lines before legacy name inference."""
    texts = [
        re.sub(r"\s+", " ", str(value or "")).strip()
        for value in values
        if value is not None
    ]
    keys = [re.sub(r"[\s_-]+", "", text).lower() for text in texts if text]
    configured = configured_business_lines(include_disabled=True)
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
    if any("共享" in key for key in keys):
        return configured_by_id.get("shared") or "共享"
    if any("家用" in key for key in keys):
        return configured_by_id.get("home") or "家用"
    if any(
        key in _APPLICATION_ALIASES or "智小白3d" in key or key == "3d打印"
        for key in keys
    ):
        return configured_by_id.get("home") or (configured[0].get("name") if configured else "未标注")
    return "未标注"


def canonical_test_business_summary(values: Any, *fallback_values: Any) -> str:
    """Summarize explicit case businesses before using legacy name inference."""
    configured = configured_business_lines(include_disabled=True)
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
    return canonical_test_business_name(*fallback_values)
