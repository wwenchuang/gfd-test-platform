"""Runtime Prompt Center.

The Prompt Center centralizes business-context construction and prompt
rendering while keeping existing services stable.  It is intentionally small:
services can opt in by reading rendered prompt snippets from this module.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from .builders.business_context_builder import BusinessContextBuilder
from .builders.prompt_renderer import PromptRenderer


class PromptCenter:
    def __init__(self, template_dir: str | None = None):
        base = Path(__file__).resolve().parent
        self.template_dir = Path(template_dir) if template_dir else base / "templates"
        self.renderer = PromptRenderer()
        self.builder = BusinessContextBuilder()
        self._templates: Dict[str, str] = {}

    def build_context(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        return self.builder.build(ctx if isinstance(ctx, dict) else {})

    def get(self, prompt_type: str, ctx: Dict[str, Any], extra: Dict[str, Any] | None = None) -> str:
        prompt_type = str(prompt_type or "").strip() or "agent"
        business_ctx = self.build_context(ctx)
        if isinstance(extra, dict):
            business_ctx.update(extra)
        template = self._load_template(prompt_type)
        return self.renderer.render(template, business_ctx)

    def enrich(self, ctx: Dict[str, Any], prompt_types: list[str] | None = None) -> Dict[str, Any]:
        ctx = dict(ctx or {})
        prompt_types = prompt_types or ["agent", "case", "repair", "sonic"]
        business_ctx = self.build_context(ctx)
        rendered = {}
        for prompt_type in prompt_types:
            try:
                rendered[prompt_type] = self.get(prompt_type, ctx)
            except Exception as exc:
                rendered[prompt_type] = f"Prompt Center 渲染失败：{exc}"
        ctx["businessContext"] = business_ctx
        ctx["promptCenter"] = {
            "version": "v1",
            "businessContext": business_ctx,
            "prompts": rendered,
        }
        return ctx

    def _load_template(self, prompt_type: str) -> str:
        key = str(prompt_type or "").strip()
        if key in self._templates:
            return self._templates[key]
        path = self.template_dir / f"{key}.prompt"
        if not path.exists():
            raise FileNotFoundError(f"Prompt template not found: {path}")
        text = path.read_text(encoding="utf-8")
        self._templates[key] = text
        return text


_PROMPT_CENTER: PromptCenter | None = None


def get_prompt_center() -> PromptCenter:
    global _PROMPT_CENTER
    if _PROMPT_CENTER is None:
        _PROMPT_CENTER = PromptCenter()
    return _PROMPT_CENTER
