"""Build structured business context for prompt rendering."""

from __future__ import annotations

import re
from typing import Any, Dict, List


HIGH_RISK_TERMS = ("支付", "删除", "覆盖基线", "确认打印", "开始打印", "提交订单")


class BusinessContextBuilder:
    """Extract a conservative business context without calling AI."""

    def build(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        ctx = ctx if isinstance(ctx, dict) else {}
        merged = self._merge_nested_context(ctx)
        target = self._first_text(
            merged.get("target"),
            merged.get("goal"),
            merged.get("title"),
            merged.get("prompt"),
        )
        source_context = merged.get("sourceContext") if isinstance(merged.get("sourceContext"), dict) else {}
        artifacts = merged.get("artifacts") if isinstance(merged.get("artifacts"), dict) else {}
        if not source_context and isinstance(artifacts.get("sourceContext"), dict):
            source_context = artifacts.get("sourceContext") or {}

        requirement_text = self._first_text(
            merged.get("requirement"),
            merged.get("requirementText"),
            source_context.get("requirementText"),
            source_context.get("target"),
            target,
        )
        business_flow = self._extract_flow(merged, source_context, requirement_text)
        risk_hits = self._risk_hits(" ".join([target, requirement_text, " ".join(business_flow)]))
        intent = self._detect_intent(merged, source_context, target, requirement_text)
        ui_context = self._ui_context(merged, source_context)
        return {
            "business_flow": business_flow,
            "business_flow_text": "\n".join(f"{idx + 1}. {item}" for idx, item in enumerate(business_flow)) or "待模型从需求中提取",
            "intent": intent,
            "risk_level": "high" if risk_hits else self._first_text(merged.get("riskLevel"), "low"),
            "risk_hits": risk_hits,
            "risk_hits_text": "、".join(risk_hits) if risk_hits else "无",
            "target": target,
            "module": self._first_text(merged.get("module"), source_context.get("module"), "未指定"),
            "app_name": self._first_text(merged.get("appName"), merged.get("app_name"), "智小白3D APP"),
            "platform": self._first_text(merged.get("platform"), "android"),
            "scope": self._first_text(merged.get("scope"), "auto"),
            "requirement_text": requirement_text,
            "ui_context": ui_context,
            "api_context": self._first_text(merged.get("apiSpec"), merged.get("api_context"), "无"),
            "failed_nodes": self._failed_nodes(merged, artifacts),
            "cases": merged.get("cases") or artifacts.get("cases") or [],
            "source_summary": self._first_text(source_context.get("sourceSummary"), merged.get("sourceSummary"), "无"),
        }

    def _merge_nested_context(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        merged = dict(ctx)
        for key in ("context", "data", "request"):
            value = ctx.get(key)
            if isinstance(value, dict):
                nested = self._merge_nested_context(value)
                for nested_key, nested_value in nested.items():
                    merged.setdefault(nested_key, nested_value)
        return merged

    def _first_text(self, *values: Any) -> str:
        for value in values:
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
        return ""

    def _extract_flow(self, ctx: Dict[str, Any], source_context: Dict[str, Any], requirement_text: str) -> List[str]:
        candidates: List[str] = []
        for value in (
            ctx.get("businessPath"),
            ctx.get("business_path"),
            ctx.get("path"),
            source_context.get("path"),
        ):
            candidates.extend(self._split_flow(value))
        for case in ctx.get("cases") or []:
            if isinstance(case, dict):
                candidates.extend(self._split_flow(case.get("business_path") or case.get("path") or case.get("title")))
        if not candidates:
            candidates.extend(self._extract_flow_from_text(requirement_text))
        cleaned = []
        for item in candidates:
            item = re.sub(r"\s+", " ", str(item or "").strip(" -:：>→"))
            if item and item not in cleaned:
                cleaned.append(item)
        return cleaned[:12] or ["进入稳定起点", "执行核心业务动作", "校验业务结果"]

    def _split_flow(self, value: Any) -> List[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        text = str(value or "").strip()
        if not text:
            return []
        return [part.strip() for part in re.split(r"\s*(?:->|→|>|，|,|；|;|\n)\s*", text) if part.strip()]

    def _extract_flow_from_text(self, text: str) -> List[str]:
        text = str(text or "")
        quoted = re.findall(r"[「《\"]([^」》\"]{2,24})[」》\"]", text)
        if quoted:
            return quoted[:8]
        verbs = re.findall(r"(进入[^，。；\n]{2,24}|点击[^，。；\n]{2,24}|选择[^，。；\n]{2,24}|查看[^，。；\n]{2,24}|确认[^，。；\n]{2,24}|生成[^，。；\n]{2,24})", text)
        return verbs[:8]

    def _risk_hits(self, text: str) -> List[str]:
        return [term for term in HIGH_RISK_TERMS if term in str(text or "")]

    def _detect_intent(self, ctx: Dict[str, Any], source_context: Dict[str, Any], target: str, requirement_text: str) -> str:
        text = " ".join([target, requirement_text, str(ctx.get("scope") or "")]).lower()
        if ctx.get("figmaUrl") or ctx.get("figma_url") or source_context.get("figmaUrl") or source_context.get("uiDesigns"):
            return "ui_test"
        if "回归" in text or "regression" in text or "基线" in text:
            return "regression"
        if "修复" in text or "失败" in text:
            return "repair"
        if "接口" in text or "api" in text:
            return "api_test"
        return "ui_test"

    def _ui_context(self, ctx: Dict[str, Any], source_context: Dict[str, Any]) -> str:
        parts = []
        for key in ("figmaUrl", "figma_url"):
            if ctx.get(key):
                parts.append(f"Figma: {ctx.get(key)}")
        if source_context.get("figmaUrl"):
            parts.append(f"Figma: {source_context.get('figmaUrl')}")
        for page in source_context.get("figmaUsedPages") or source_context.get("knowledgePages") or []:
            if isinstance(page, dict):
                title = page.get("pageName") or page.get("title") or page.get("name")
                if title:
                    parts.append(str(title))
        for item in source_context.get("uiDesigns") or []:
            if isinstance(item, dict):
                title = item.get("title") or item.get("page_name") or item.get("name")
                if title:
                    parts.append(str(title))
        return "\n".join(dict.fromkeys(parts)) if parts else "无"

    def _failed_nodes(self, ctx: Dict[str, Any], artifacts: Dict[str, Any]) -> List[str]:
        failure = ctx.get("failureAnalysis") if isinstance(ctx.get("failureAnalysis"), dict) else {}
        if not failure and isinstance(artifacts.get("failureAnalysis"), dict):
            failure = artifacts.get("failureAnalysis") or {}
        nodes = []
        for key in ("failedStep", "current", "summary", "failureType"):
            if failure.get(key):
                nodes.append(str(failure.get(key)))
        return nodes[:6]
