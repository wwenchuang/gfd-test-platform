"""Minimal HTML renderer for trace details."""

from __future__ import annotations

import html
import json
from typing import Any, Dict


class DebugView:
    def render(self, trace_data: Dict[str, Any]) -> str:
        trace = trace_data if isinstance(trace_data, dict) else {}
        nodes = trace.get("nodes") if isinstance(trace.get("nodes"), list) else []
        title = html.escape(str(trace.get("title") or trace.get("traceId") or "Execution Trace"))
        rows = []
        for node in nodes:
            status = html.escape(str(node.get("status") or "unknown"))
            cls = "ok" if status == "success" else ("bad" if status == "failed" else "wait")
            detail = html.escape(json.dumps(node.get("result") or {}, ensure_ascii=False, indent=2)[:3000])
            rows.append(f"""
              <section class="node {cls}">
                <div class="node-head">
                  <strong>{html.escape(str(node.get('node') or '-'))}</strong>
                  <span>{status}</span>
                  <em>{int(node.get('durationMs') or 0)} ms</em>
                </div>
                <p>{html.escape(str(node.get('title') or node.get('error') or ''))}</p>
                <pre>{detail}</pre>
              </section>
            """)
        return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{title}</title>
<style>
body{{margin:0;background:#06111f;color:#d9e6ff;font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;}}
main{{max-width:1120px;margin:0 auto;padding:28px;}}
h1{{font-size:24px;margin:0 0 8px;}} .meta{{color:#91a3bf;margin-bottom:20px;}}
.node{{border:1px solid #23415f;border-radius:10px;padding:14px;margin:12px 0;background:#09182b;}}
.node.ok{{border-left:5px solid #00d39b;}} .node.bad{{border-left:5px solid #ff4f7b;}} .node.wait{{border-left:5px solid #f5b301;}}
.node-head{{display:flex;gap:12px;align-items:center;}} .node-head strong{{font-size:16px;}} .node-head span{{color:#00d4ff;}}
pre{{white-space:pre-wrap;overflow:auto;background:#030915;border-radius:8px;padding:10px;color:#b8c7dd;}}
</style></head><body><main>
<h1>{title}</h1>
<div class="meta">Trace ID: {html.escape(str(trace.get('traceId') or trace.get('id') or ''))} · Source: {html.escape(str(trace.get('sourceType') or ''))} · Nodes: {len(nodes)}</div>
{''.join(rows) or '<p>暂无 Trace 节点。</p>'}
</main></body></html>"""
