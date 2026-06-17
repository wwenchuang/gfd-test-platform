"""Unified execution adapter.

This module is the single stable entry for execution-shaped requests.  It keeps
the existing Windows/Mac Runner path as the default, while exposing optional
safe DAG and parallel DAG modes for debugger/trace workflows.
"""

from __future__ import annotations

from typing import Any, Dict, List

from task_server.prompts import get_prompt_center


class ExecutionAdapter:
    """Dispatch execution requests to supported execution modes.

    Modes:
    - local / legacy / runner: create or describe a local Windows/Mac Runner job.
    - sonic / remote / suite: legacy remote/Sonic entry, intentionally disabled
      for single-case execution.
    - dag: run the conservative sequential DAG wrapper.
    - parallel: run the conservative parallel DAG wrapper.
    """

    DAG_MODES = {"dag", "safe_dag", "dag_safe"}
    PARALLEL_MODES = {"parallel", "parallel_dag", "dag_parallel"}
    REMOTE_MODES = {"groovy", "sonic", "remote", "suite"}
    LOCAL_MODES = {"", "local", "legacy", "runner", "windows", "mac"}
    SHADOW_MODES = {"shadow", "shadow_compare", "shadow-mode"}

    def run(self, case: Dict[str, Any], mode: str | None = None) -> Dict[str, Any]:
        case = case if isinstance(case, dict) else {}
        try:
            case = get_prompt_center().enrich(case)
        except Exception:
            case = dict(case)
        selected_mode = str(
            mode
            or case.get("executionCoreMode")
            or case.get("execution_core_mode")
            or case.get("mode")
            or case.get("executionMode")
            or case.get("execution_mode")
            or "local"
        ).strip().lower()
        if selected_mode in self.DAG_MODES:
            return self._dag(case)
        if selected_mode in self.PARALLEL_MODES:
            return self._parallel(case)
        if selected_mode in self.SHADOW_MODES:
            return self.shadow_compare(case, dry_run=True)
        if selected_mode in self.REMOTE_MODES:
            return self._remote(case)
        return self._local(case)

    def available_modes(self) -> Dict[str, Any]:
        return {
            "ok": True,
            "default": "local",
            "modes": [
                {"id": "local", "label": "本地 Runner", "stable": True},
                {"id": "legacy", "label": "Legacy 兼容本地 Runner", "stable": True},
                {"id": "dag", "label": "顺序 DAG Debug", "stable": True},
                {"id": "parallel", "label": "并行 DAG Debug", "stable": True},
                {"id": "shadow", "label": "Shadow 双轨一致性校验", "stable": True, "dryRunDefault": True},
                {"id": "sonic", "label": "Sonic 远程执行", "stable": False, "deprecatedForSingleCase": True},
            ],
        }

    def shadow_compare(
        self,
        case: Dict[str, Any],
        shadow_modes: List[str] | None = None,
        dry_run: bool = True,
    ) -> Dict[str, Any]:
        """Compare legacy/local output with optional execution cores.

        Shadow mode is intentionally side-effect-safe by default: it disables
        ``createJob`` so the comparison cannot enqueue real Runner work unless
        the caller explicitly opts out of dry-run mode.
        """
        from task_server.core.shadow import ShadowRunner

        def legacy_engine(ctx: Dict[str, Any]) -> Dict[str, Any]:
            return self.run(ctx, mode="local")

        def new_engine_factory(selected_mode: str):
            return lambda ctx: self.run(ctx, mode=selected_mode)

        return ShadowRunner(
            legacy_engine=legacy_engine,
            new_engine_factory=new_engine_factory,
        ).run(case, shadow_modes=shadow_modes, dry_run=dry_run)

    def _output_signature(self, result: Dict[str, Any]) -> Dict[str, Any]:
        result = result if isinstance(result, dict) else {}
        inner = result.get("result") if isinstance(result.get("result"), dict) else {}
        return {
            "ok": bool(result.get("ok", False)),
            "mode": result.get("mode") or "",
            "status": result.get("status") or "",
            "summary": result.get("summary") or "",
            "resultKeys": sorted(inner.keys()) if inner else sorted(result.keys()),
            "traceCount": len(result.get("trace") or inner.get("trace") or []),
        }

    def _compare_execution_outputs(self, baseline: Dict[str, Any], shadow: Dict[str, Any]) -> Dict[str, Any]:
        base_sig = self._output_signature(baseline)
        shadow_sig = self._output_signature(shadow)
        differences = []
        if base_sig["ok"] != shadow_sig["ok"]:
            differences.append({"field": "ok", "baseline": base_sig["ok"], "shadow": shadow_sig["ok"]})
        if shadow_sig["status"] in {"failed", "error", "unsupported"}:
            differences.append({"field": "status", "baseline": base_sig["status"], "shadow": shadow_sig["status"]})
        if not shadow_sig["resultKeys"]:
            differences.append({"field": "resultKeys", "baseline": base_sig["resultKeys"], "shadow": shadow_sig["resultKeys"]})
        return {
            "baseline": base_sig,
            "shadow": shadow_sig,
            "differences": differences,
            "blocking": any(item["field"] in {"ok", "status", "resultKeys"} for item in differences),
        }

    def _local(self, case: Dict[str, Any]) -> Dict[str, Any]:
        module = str(case.get("module") or "").strip()
        file_name = str(case.get("file") or "").strip()
        task_name = str(case.get("taskName") or case.get("target_task_name") or "").strip()
        create_job = bool(case.get("createJob", case.get("create_job", False)))
        if create_job and module and file_name:
            from task_server.services.job_service import create_pending_job

            job = create_pending_job(
                module=module,
                file=file_name,
                target_task_name=task_name,
                device_id=str(case.get("deviceId") or case.get("device_id") or ""),
                runner_id=str(case.get("runnerId") or case.get("runner_id") or ""),
                device_strategy=str(case.get("deviceStrategy") or case.get("device_strategy") or "auto"),
                run_mode=str(case.get("runMode") or case.get("run_mode") or "test"),
            )
            return {
                "ok": True,
                "mode": "local",
                "status": "queued",
                "job": job,
                "summary": "已创建本地 Runner 执行任务",
            }
        return {
            "ok": True,
            "mode": "local",
            "status": "ready",
            "summary": "本地 Runner 执行已就绪，未创建任务",
            "case": {"module": module, "file": file_name, "taskName": task_name},
        }

    def _remote(self, case: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "ok": False,
            "mode": "remote",
            "status": "unsupported",
            "summary": "Sonic 单条远程执行暂不直接创建临时套件，请优先使用本地 Runner",
            "case": {
                "module": str(case.get("module") or "").strip(),
                "file": str(case.get("file") or "").strip(),
            },
        }

    def _dag(self, case: Dict[str, Any]) -> Dict[str, Any]:
        from task_server.workflow.dag_safe.adapter_map import build_adapters
        from task_server.workflow.dag_safe.dag_wrapper import DAGWrapper

        request = case.get("request") if isinstance(case.get("request"), dict) else case
        ctx = case.get("context") if isinstance(case.get("context"), dict) else dict(case)
        result = DAGWrapper(build_adapters()).run(request, ctx)
        return {
            "ok": True,
            "mode": "dag",
            "status": "done",
            "summary": "已通过统一 ExecutionAdapter 执行顺序 DAG",
            "result": result,
            "trace": result.get("trace") or result.get("context", {}).get("trace") or [],
        }

    def _parallel(self, case: Dict[str, Any]) -> Dict[str, Any]:
        from task_server.workflow.dag_safe.adapter_map import build_adapters
        from task_server.workflow.dag_safe.execution_plan import ExecutionPlan
        from task_server.workflow.parallel_dag.execution_graph import ExecutionGraph
        from task_server.workflow.parallel_dag.parallel_dag_runner import ParallelDAGRunner

        request = case.get("request") if isinstance(case.get("request"), dict) else case
        ctx = case.get("context") if isinstance(case.get("context"), dict) else dict(case)
        plan = ExecutionPlan().build(request)
        graph = ExecutionGraph.from_plan(plan)
        result = ParallelDAGRunner(build_adapters()).run(graph, ctx)
        return {
            "ok": True,
            "mode": "parallel",
            "status": "done",
            "summary": "已通过统一 ExecutionAdapter 执行并行 DAG",
            "plan": plan,
            "result": result,
            "trace": result.get("trace") or [],
        }
