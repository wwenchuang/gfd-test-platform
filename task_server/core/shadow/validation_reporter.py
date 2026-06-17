"""Human-readable validation report for shadow mode."""

from __future__ import annotations

from typing import Any, Dict, List


class ValidationReporter:
    def report(self, shadow_results: List[Dict[str, Any]], dry_run: bool = True) -> Dict[str, Any]:
        consistent = all(not item.get("diff", {}).get("blocking") for item in shadow_results)
        return {
            "status": "PASS" if consistent else "FAIL",
            "message": "Legacy 与新执行核心输出一致" if consistent else "Shadow Diff 发现阻断差异",
            "consistent": consistent,
            "dryRun": bool(dry_run),
            "shadowCount": len(shadow_results),
            "blockingModes": [
                item.get("mode")
                for item in shadow_results
                if item.get("diff", {}).get("blocking")
            ],
        }
