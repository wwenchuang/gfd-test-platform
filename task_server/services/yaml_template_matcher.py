"""Select reusable YAML templates from the maintained baseline library."""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Set


_STOPWORDS = {
    "测试", "验证", "页面", "功能", "需求", "用例", "执行", "当前", "进行", "是否", "可以", "需要",
    "点击", "进入", "打开", "显示", "相关", "流程", "按钮", "模块", "状态", "结果", "完成", "成功",
    "失败", "检查", "确认", "一个", "这个", "那个", "用户", "操作", "场景", "自动化", "生成",
}


def _tokens(text: Any) -> Set[str]:
    raw = str(text or "").lower()
    items = re.findall(r"[\u4e00-\u9fff]{2,}|[a-z0-9_]{2,}", raw)
    return {item for item in items if item and item not in _STOPWORDS}


def _case_text(case: dict) -> str:
    parts: List[str] = []
    for key in ("title", "module", "file", "description", "baseline_path", "snippet"):
        value = case.get(key)
        if value:
            parts.append(str(value))
    for key in ("tags", "matched_terms", "actions"):
        value = case.get(key)
        if isinstance(value, list):
            parts.extend(str(item) for item in value if str(item).strip())
        elif value:
            parts.append(str(value))
    return "\n".join(parts)


def simple_similarity(requirement: Any, candidate: Any) -> int:
    """Small deterministic scorer for choosing baseline templates.

    The result is intentionally simple and explainable: exact phrase hits and
    token overlap. It is used before prompting so the model receives a compact
    Top-N template set instead of the whole baseline library.
    """
    req_text = str(requirement or "")
    cand_text = str(candidate or "")
    req_l = req_text.lower()
    cand_l = cand_text.lower()
    req_tokens = _tokens(req_l)
    cand_tokens = _tokens(cand_l)
    if not req_tokens or not cand_tokens:
        return 0
    overlap = req_tokens & cand_tokens
    score = len(overlap) * 6
    for token in overlap:
        if token in cand_l:
            score += min(10, len(token))
        if token in req_l and token in cand_l:
            score += 2
    if req_l.strip() and req_l.strip() in cand_l:
        score += 20
    return score


def select_best_baseline_template(requirement: Any, baseline_cases: Iterable[dict], limit: int = 3) -> List[dict]:
    """Return Top-N baseline YAML templates relevant to a requirement."""
    limit = max(1, min(3, int(limit or 3)))
    scored = []
    for case in baseline_cases or []:
        if not isinstance(case, dict):
            continue
        text = _case_text(case)
        score = simple_similarity(requirement, text)
        if score <= 0 and scored:
            continue
        row = dict(case)
        row["template_score"] = score
        row["template_reason"] = "关键词/动作/模块相似" if score > 0 else "低相似兜底模板"
        scored.append(row)
    scored.sort(key=lambda item: (int(item.get("template_score") or 0), int(item.get("score") or 0), item.get("title") or ""), reverse=True)
    result = scored[:limit]
    for idx, item in enumerate(result, start=1):
        item["template_rank"] = idx
    return result


def build_yaml_template_matcher_text(templates: Iterable[dict]) -> str:
    templates = [item for item in templates or [] if isinstance(item, dict)]
    if not templates:
        return ""
    lines = [
        "【相似基线模板 Top3：套模板填槽】",
        "生成 YAML 时不要重新设计结构，优先基于下面候选模板做业务变量替换和少量步骤微调。",
        "只能复用相关动作组织方式、等待策略和入口清理写法；不要复制无关业务断言。",
        "",
    ]
    for item in templates[:3]:
        actions = " -> ".join(str(v) for v in (item.get("actions") or []) if str(v).strip()) or "-"
        matched = "、".join(str(v) for v in (item.get("matched_terms") or []) if str(v).strip()) or item.get("template_reason") or "-"
        lines.extend([
            f"{item.get('template_rank') or '-'}. {item.get('title') or item.get('file') or '未命名模板'}",
            f"   来源：{item.get('file') or '-'}",
            f"   分数：{item.get('template_score') or item.get('score') or 0}",
            f"   匹配：{matched}",
            f"   动作序列：{actions}",
        ])
        snippet = str(item.get("snippet") or "").strip()
        if snippet:
            lines.extend(["```yaml", snippet[:1200], "```"])
        lines.append("")
    return "\n".join(lines).strip()


YAML_TEMPLATE_MATCH_EVAL_SAMPLES: List[Dict[str, Any]] = [
    {
        "name": "图片建模上传",
        "requirement": "AI建模 图片建模 上传图片 图库 选择图片",
        "must_match_any": ["图片", "图库", "上传", "建模"],
    },
    {
        "name": "语音创作长按",
        "requirement": "AI建模 语音创作 长按输入 文案",
        "must_match_any": ["语音", "长按", "输入"],
    },
    {
        "name": "模型列表与详情",
        "requirement": "模型列表 推荐卡片 点击跳转 模型详情 返回首页",
        "must_match_any": ["模型", "列表", "详情", "卡片"],
    },
    {
        "name": "外部跳转",
        "requirement": "打开微信 跳转商城 返回 App",
        "must_match_any": ["微信", "商城", "跳转", "返回"],
    },
]


def evaluate_baseline_template_matching(
    baseline_cases: Iterable[dict],
    samples: Optional[Iterable[dict]] = None,
    limit: int = 5,
) -> Dict[str, Any]:
    """Run deterministic smoke evaluation for template retrieval quality.

    This is intentionally lightweight: it checks whether fixed representative
    requirements can retrieve baseline snippets whose title/file/tags/actions
    contain expected domain words. The result is metadata for review, not a
    hard generation blocker.
    """
    cases = [case for case in (baseline_cases or []) if isinstance(case, dict)]
    rows: List[Dict[str, Any]] = []
    for sample in (samples or YAML_TEMPLATE_MATCH_EVAL_SAMPLES):
        if not isinstance(sample, dict):
            continue
        expected = [str(item).lower() for item in (sample.get("must_match_any") or []) if str(item).strip()]
        templates = select_best_baseline_template(sample.get("requirement") or sample.get("name"), cases, limit=limit)
        matched_text = "\n".join(_case_text(item).lower() for item in templates)
        hit_terms = [term for term in expected if term and term in matched_text]
        rows.append({
            "name": sample.get("name") or sample.get("requirement") or "样例",
            "expected": expected,
            "hit_terms": hit_terms,
            "ok": bool(hit_terms),
            "top": [
                {
                    "title": item.get("title"),
                    "module": item.get("module"),
                    "file": item.get("file"),
                    "score": item.get("template_score") or item.get("score") or 0,
                }
                for item in templates[:3]
            ],
        })
    passed = sum(1 for row in rows if row.get("ok"))
    return {
        "enabled": True,
        "sample_count": len(rows),
        "passed": passed,
        "failed": max(0, len(rows) - passed),
        "pass_rate": round(passed / len(rows), 3) if rows else 0,
        "samples": rows,
    }
