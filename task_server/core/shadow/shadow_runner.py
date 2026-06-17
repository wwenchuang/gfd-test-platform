"""Run legacy and new execution cores side by side in shadow mode."""

from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List

from .diff_engine import ShadowDiffEngine
from .validation_reporter import ValidationReporter


Engine = Callable[[Dict[str, Any]], Dict[str, Any]]
EngineFactory = Callable[[str], Engine]


class ShadowRunner:
    """Side-effect-safe dual-track execution comparator.

    The runner itself does not know how to execute a test.  Callers provide the
    stable legacy engine and a factory for candidate new engines.  This keeps
    shadow mode as a validation layer, not a replacement for production flow.
    """

    def __init__(
        self,
        legacy_engine: Engine,
        new_engine_factory: EngineFactory,
        diff_engine: ShadowDiffEngine | None = None,
        reporter: ValidationReporter | None = None,
    ):
        self.legacy_engine = legacy_engine
        self.new_engine_factory = new_engine_factory
        self.diff_engine = diff_engine or ShadowDiffEngine()
        self.reporter = reporter or ValidationReporter()

    def run(
        self,
        ctx: Dict[str, Any],
        shadow_modes: Iterable[str] | None = None,
        dry_run: bool = True,
    ) -> Dict[str, Any]:
        request = dict(ctx or {})
        if dry_run:
            request.update({
                "createJob": False,
                "create_job": False,
                "createExecutionJob": False,
            })

        modes = [
            str(mode or "").strip().lower()
            for mode in (shadow_modes or ["dag", "parallel"])
            if str(mode or "").strip()
        ]
        legacy_result = self.legacy_engine(dict(request))
        shadows: List[Dict[str, Any]] = []
        for mode in modes:
            new_result = self.new_engine_factory(mode)(dict(request))
            shadows.append({
                "mode": mode,
                "result": new_result,
                "diff": self.diff_engine.compare(legacy_result, new_result),
            })
        report = self.reporter.report(shadows, dry_run=dry_run)
        return {
            "ok": True,
            "mode": "shadow_compare",
            "dryRun": bool(dry_run),
            "baseline": legacy_result,
            "shadows": shadows,
            "consistent": report["consistent"],
            "validationReport": report,
            "validation_report": report,
        }
