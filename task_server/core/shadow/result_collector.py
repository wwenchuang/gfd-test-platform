"""Normalize execution outputs before shadow diffing."""

from __future__ import annotations

from typing import Any, Dict


class ResultCollector:
    """Build a stable comparison signature from heterogeneous engine outputs."""

    def collect(self, result: Dict[str, Any] | None) -> Dict[str, Any]:
        result = result if isinstance(result, dict) else {}
        inner = result.get("result") if isinstance(result.get("result"), dict) else {}
        trace = result.get("trace") or inner.get("trace") or []
        status = str(result.get("status") or "").strip()
        mode = str(result.get("mode") or "").strip()
        summary = str(result.get("summary") or "").strip()
        result_keys = sorted(inner.keys()) if inner else sorted(result.keys())
        return {
            "ok": bool(result.get("ok", False)),
            "mode": mode,
            "status": status,
            "summary": summary,
            "resultKeys": result_keys,
            "traceCount": len(trace if isinstance(trace, list) else []),
        }
