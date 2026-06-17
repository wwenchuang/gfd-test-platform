"""Diff engine for shadow mode validation."""

from __future__ import annotations

from typing import Any, Dict, List

from .result_collector import ResultCollector


class ShadowDiffEngine:
    """Compare legacy and shadow outputs without requiring exact raw equality."""

    BLOCKING_FIELDS = {"ok", "status", "resultKeys"}
    FAILURE_STATUSES = {"failed", "error", "unsupported"}

    def __init__(self, collector: ResultCollector | None = None):
        self.collector = collector or ResultCollector()

    def compare(self, legacy: Dict[str, Any], new: Dict[str, Any]) -> Dict[str, Any]:
        legacy_sig = self.collector.collect(legacy)
        new_sig = self.collector.collect(new)
        differences: List[Dict[str, Any]] = []
        field_diffs: List[Dict[str, Any]] = []

        self._append_if_changed(differences, field_diffs, "ok", legacy_sig["ok"], new_sig["ok"])
        if new_sig["status"] in self.FAILURE_STATUSES:
            self._append_if_changed(differences, field_diffs, "status", legacy_sig["status"], new_sig["status"])
        if not new_sig["resultKeys"]:
            self._append_if_changed(differences, field_diffs, "resultKeys", legacy_sig["resultKeys"], new_sig["resultKeys"])

        legacy_keys = set(legacy_sig["resultKeys"])
        new_keys = set(new_sig["resultKeys"])
        missing_in_new = sorted(legacy_keys - new_keys)
        missing_in_legacy = sorted(new_keys - legacy_keys)
        if missing_in_new:
            differences.append({"field": "missingInNew", "legacy": missing_in_new, "shadow": []})
        blocking = any(item.get("field") in self.BLOCKING_FIELDS for item in differences)

        return {
            "equal": not differences and not missing_in_new,
            "baseline": legacy_sig,
            "shadow": new_sig,
            "fieldDiffs": field_diffs,
            "field_diffs": field_diffs,
            "missingInNew": missing_in_new,
            "missing_in_new": missing_in_new,
            "missingInLegacy": missing_in_legacy,
            "missing_in_legacy": missing_in_legacy,
            "differences": differences,
            "blocking": blocking,
        }

    def _append_if_changed(
        self,
        differences: List[Dict[str, Any]],
        field_diffs: List[Dict[str, Any]],
        field: str,
        legacy_value: Any,
        new_value: Any,
    ) -> None:
        if legacy_value == new_value:
            return
        item = {"field": field, "legacy": legacy_value, "shadow": new_value}
        differences.append(item)
        field_diffs.append({"field": field, "legacy": legacy_value, "new": new_value})
