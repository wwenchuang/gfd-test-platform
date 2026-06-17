"""Small template renderer for Prompt Center."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict


class PromptRenderer:
    """Render plain ``str.format_map`` templates with safe missing keys."""

    def render(self, template: str, context: Dict[str, Any]) -> str:
        safe_context = defaultdict(str)
        for key, value in (context or {}).items():
            safe_context[key] = self._stringify(value)
        return str(template or "").format_map(safe_context)

    def _stringify(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, (list, tuple, set)):
            if not value:
                return "无"
            return "\n".join(f"- {self._stringify(item)}" for item in value)
        if isinstance(value, dict):
            lines = []
            for key, item in value.items():
                lines.append(f"- {key}: {self._stringify(item)}")
            return "\n".join(lines) if lines else "无"
        return str(value)
