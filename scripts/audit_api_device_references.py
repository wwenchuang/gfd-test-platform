#!/usr/bin/env python3
"""Offline-style database reference inventory, never an execution or device guard.

Run from the configured app environment. JSONL contains identifiers, names, paths,
known MACs, object counts and fixed review markers only; no template values or secrets.
All case/environment revisions are scanned, including historical ones. Results are
potential references, not proof of runtime reachability, permission or device safety.
"""
import argparse
import json
import os
from pathlib import Path
import re
import sys

DEVICE_KEY = re.compile(r"^(?:device(?:_?(?:id|sn|serial|mac))?|printer_?(?:id|sn)|serial_?number|udid|sn|mac_?address)$", re.I)
REF = re.compile(r"\{\{.*?\}\}")
SAFE_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,47}$")


def walk(value, known, path="$", depth=0):
    """Yield only paths and known device IDs/fixed markers, never leaf values."""
    if depth > 60:
        yield path, "DEPTH_LIMIT_REVIEW"
        return
    if isinstance(value, dict):
        for index, (key, item) in enumerate(value.items()):
            key = str(key)
            child = path + "." + (key if SAFE_KEY.fullmatch(key) else f"<key-{index}>")
            if DEVICE_KEY.fullmatch(key):
                yield child, "DEVICE_FIELD_REVIEW"
            if key.lower() in {"jsonpath", "json_path", "expression", "extractions", "extraction", "target_name"}:
                yield child, "EXTRACTION_REVIEW"
            yield from walk(item, known, child, depth + 1)
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            yield from walk(item, known, f"{path}[{index}]", depth + 1)
    elif isinstance(value, str):
        for device in sorted(known):
            if device in value.upper():
                yield path, device
        if REF.search(value):
            yield path, "DYNAMIC_REFERENCE"
        if value.lstrip().startswith(("{", "[")):
            try:
                decoded = json.loads(value)
            except (ValueError, TypeError, RecursionError):
                return
            yield from walk(decoded, known, path + ".<json>", depth + 1)


def dependencies(version):
    spec = version.get("dependency_spec") or {}
    items = spec.get("dependencies", []) if isinstance(spec, dict) else []
    return [(i, d["case_version_id"]) for i, d in enumerate(items)
            if isinstance(d, dict) and isinstance(d.get("case_version_id"), str)]


def dependency_closure(versions, seeds):
    """Reverse transitive closure: versions which can bring a seed into a run."""
    affected = set(seeds)
    changed = True
    while changed:
        changed = False
        for version in versions:
            if version["id"] not in affected and any(target in affected for _, target in dependencies(version)):
                affected.add(version["id"])
                changed = True
    return affected


def resolve_targets(kind, targets, baselines, versions, tasks):
    """Structural candidate selection only; does not bypass runtime access checks."""
    active = [b for b in baselines if b["status"] == "active"]
    if kind == "cases":
        return set(targets), "DIRECT_CASE_VERSION"
    if kind == "baselines":
        selected = [b for b in active if b["id"] in targets]
        if {b["id"] for b in selected} != set(targets):
            return set(), "INACTIVE_OR_MISSING_BASELINE"
    elif kind == "baseline_group":
        selected = [b for b in active if b["group_name"] in targets]
    elif kind == "task":
        chosen = [t for t in tasks if t["id"] in targets]
        if len(targets) != 1 or len(chosen) != 1:
            return set(), "INVALID_TASK_TARGET"
        endpoints = set(chosen[0].get("selected_endpoint_ids") or [])
        candidate = {v["id"] for v in versions if v["endpoint_id"] in endpoints}
        selected = [b for b in active if b["case_version_id"] in candidate]
    else:
        return set(), "UNSUPPORTED_TARGET_REVIEW"
    return {b["case_version_id"] for b in selected}, "ACTIVE_BASELINES_ONLY"


def load_rows(session, project_id):
    from sqlalchemy import case, select
    from task_server.api_testing import models as m

    if session.scalar(select(m.ApiProject.id).where(m.ApiProject.id == project_id)) is None:
        raise ValueError("PROJECT_NOT_FOUND")

    def read(model, fields, predicate):
        columns = [
            case((model.is_secret.is_(True), None), else_=model.value).label("value")
            if model is m.ApiEnvironmentVariable and field == "value"
            else getattr(model, field)
            for field in fields
        ]
        return [dict(zip(fields, row)) for row in session.execute(select(*columns).where(predicate))]

    case_ids = select(m.ApiCase.id).where(m.ApiCase.project_id == project_id)
    version_ids = select(m.ApiCaseVersion.id).where(m.ApiCaseVersion.case_id.in_(case_ids))
    env_ids = select(m.ApiEnvironment.id).where(m.ApiEnvironment.project_id == project_id)
    revision_ids = select(m.ApiEnvironmentRevision.id).where(m.ApiEnvironmentRevision.environment_id.in_(env_ids))
    job_ids = select(m.ApiScheduledJob.id).where(m.ApiScheduledJob.project_id == project_id)
    pending_ids = select(m.ApiExecution.id).where(m.ApiExecution.project_id == project_id, m.ApiExecution.state.in_(("QUEUED", "RUNNING")))
    data = {}
    specs = [
        ("cases", m.ApiCase, "id name active_version_id", m.ApiCase.project_id == project_id),
        ("versions", m.ApiCaseVersion, "id case_id endpoint_id request_template dependency_spec processing_spec", m.ApiCaseVersion.case_id.in_(case_ids)),
        ("data_rows", m.ApiCaseDataRow, "id name case_version_id enabled values", m.ApiCaseDataRow.case_version_id.in_(version_ids)),
        ("scripts", m.ApiCaseScript, "id case_version_id phase config source", m.ApiCaseScript.case_version_id.in_(version_ids)),
        ("extractions", m.ApiCaseExtraction, "id case_version_id target_name definition", m.ApiCaseExtraction.case_version_id.in_(version_ids)),
        ("assertions", m.ApiCaseAssertion, "id case_version_id enabled definition", m.ApiCaseAssertion.case_version_id.in_(version_ids)),
        ("baselines", m.ApiBaseline, "id case_id case_version_id environment_revision_id group_name status", m.ApiBaseline.project_id == project_id),
        ("environments", m.ApiEnvironment, "id name active_revision_id", m.ApiEnvironment.project_id == project_id),
        ("environment_revisions", m.ApiEnvironmentRevision, "id name environment_id default_headers", m.ApiEnvironmentRevision.environment_id.in_(env_ids)),
        ("environment_variables", m.ApiEnvironmentVariable, "id name revision_id is_secret enabled value", m.ApiEnvironmentVariable.revision_id.in_(revision_ids)),
        ("environment_services", m.ApiEnvironmentService, "id revision_id service_name base_url metadata_json", m.ApiEnvironmentService.revision_id.in_(revision_ids)),
        ("jobs", m.ApiScheduledJob, "id name enabled target_type environment_strategy environment_id environment_revision_id", m.ApiScheduledJob.project_id == project_id),
        ("targets", m.ApiScheduledJobTarget, "id job_id target_type target_id", m.ApiScheduledJobTarget.job_id.in_(job_ids)),
        ("tasks", m.ApiTestTask, "id name selected_endpoint_ids environment_revision_id", m.ApiTestTask.project_id == project_id),
        ("pending", m.ApiExecution, "id state environment_revision_id request_snapshot", m.ApiExecution.id.in_(pending_ids)),
        ("pending_cases", m.ApiExecutionCase, "id execution_id case_version_id status", m.ApiExecutionCase.execution_id.in_(pending_ids)),
    ]
    for key, model, fields, predicate in specs:
        data[key] = read(model, fields.split(), predicate)
    return data


def report(data, allowed, blocked):
    for kind, rows in data.items():
        yield {"kind": "inventory_count", "object_kind": kind, "count": len(rows)}
    known = allowed | blocked
    names = {c["id"]: c["name"] for c in data["cases"]}
    versions = data["versions"]
    seeds = set()

    def record(kind, row, path, marker=None, related_id=None, device=None):
        name = row.get("name") or names.get(row.get("case_id"), "")
        item = {"kind": kind, "id": row["id"], "name": name, "path": path}
        if marker is not None:
            item["marker"] = marker
        if related_id is not None:
            item["related_id"] = related_id
        if device is not None:
            item["device"] = device
        return item

    scan_fields = {"versions": ("request_template", "dependency_spec", "processing_spec"),
                   "data_rows": ("values",), "scripts": ("source", "config"),
                   "extractions": ("definition",), "assertions": ("definition",),
                   "environment_revisions": ("default_headers",),
                   "environment_variables": ("value",),
                   "environment_services": ("base_url", "metadata_json"),
                   "pending": ("request_snapshot",)}
    for kind, fields in scan_fields.items():
        for row in data[kind]:
            for field in ("case_version_id", "revision_id", "environment_id", "environment_revision_id"):
                if row.get(field):
                    yield record(kind, row, field, related_id=row[field])
            if row.get("is_secret"):
                yield record(kind, row, "value", "SECRET_NOT_INSPECTED")
                continue
            if kind == "extractions":
                yield record(kind, row, "definition", "EXTRACTION_REVIEW")
            for field in fields:
                for path, hit in walk(row.get(field), known, field):
                    if hit in known:
                        yield record(kind, row, path, "BLOCKED_DEVICE" if hit in blocked else "ALLOWED_DEVICE_REFERENCE", device=hit)
                        if hit in blocked:
                            seeds.add(row["id"] if kind == "versions" else row.get("case_version_id"))
                    else:
                        yield record(kind, row, path, hit)
    affected = dependency_closure(versions, seeds - {None})
    for version in versions:
        for index, target in dependencies(version):
            yield record("dependency", version, f"dependency_spec.dependencies[{index}].case_version_id",
                         "BLOCKED_DEVICE_DEPENDENCY" if target in affected else "DEPENDENCY_REFERENCE", target)
    for baseline in data["baselines"]:
        yield record("baseline", baseline, "case_version_id",
                     "ACTIVE_BASELINE" if baseline["status"] == "active" else "INACTIVE_BASELINE", baseline["case_version_id"])
        if baseline["case_version_id"] in affected:
            yield record("baseline", baseline, "case_version_id", "BLOCKED_DEVICE_REACHABLE", baseline["case_version_id"])
        if baseline.get("environment_revision_id"):
            yield record("baseline", baseline, "environment_revision_id", related_id=baseline["environment_revision_id"])
    for case in data["cases"]:
        if case.get("active_version_id"):
            yield record("case", case, "active_version_id", related_id=case["active_version_id"])
    for env in data["environments"]:
        if env.get("active_revision_id"):
            yield record("environment", env, "active_revision_id", related_id=env["active_revision_id"])
    for task in data["tasks"]:
        yield record("task", task, "environment_revision_id", related_id=task.get("environment_revision_id"))
        for index, endpoint_id in enumerate(task.get("selected_endpoint_ids") or []):
            yield record("task", task, f"selected_endpoint_ids[{index}]", related_id=endpoint_id)
    for job in data["jobs"]:
        yield record("schedule", job, "enabled", "ENABLED" if job["enabled"] else "DISABLED")
        targets = [t for t in data["targets"] if t["job_id"] == job["id"]]
        for target in targets:
            yield record("schedule", job, "targets." + job["target_type"], related_id=target["target_id"])
        resolved, marker = resolve_targets(job["target_type"], [t["target_id"] for t in targets],
                                           data["baselines"], versions, data["tasks"])
        yield record("schedule", job, "target_resolution", marker)
        for vid in sorted(resolved):
            yield record("schedule", job, "resolved_case_version_id",
                         "BLOCKED_DEVICE_REACHABLE" if vid in affected else "STRUCTURAL_CANDIDATE", vid)
        rid = job.get("environment_revision_id") if job["environment_strategy"] == "fixed_revision" else next(
            (e["active_revision_id"] for e in data["environments"] if e["id"] == job.get("environment_id")), None)
        yield record("schedule", job, "resolved_environment_revision_id", "FIXED_REVISION" if job["environment_strategy"] == "fixed_revision" else "ACTIVE_REVISION", rid)
    for row in data["pending_cases"]:
        yield record("pending_case", row, "execution_id", related_id=row["execution_id"])
        yield record("pending_case", row, "case_version_id",
                     "BLOCKED_DEVICE_REACHABLE" if row["case_version_id"] in affected else "PENDING_REFERENCE", row["case_version_id"])
    yield {"kind": "audit", "id": "", "name": "", "path": "$", "marker": "COMPLETE_STATIC_INVENTORY_NOT_RUNTIME_SAFETY_PROOF"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--output", required=True, type=Path, help="New JSONL file; existing files are never overwritten")
    parser.add_argument("--allow", action="append", help="Allowed 12-hex device MAC; repeatable (default 9888E0094F2A)")
    parser.add_argument("--block", action="append", help="Blocked 12-hex device MAC; repeatable (default 18CEDF5BA7B2)")
    args = parser.parse_args(argv)
    allowed = {v.upper() for v in (args.allow or ["9888E0094F2A"])}
    blocked = {v.upper() for v in (args.block or ["18CEDF5BA7B2"])}
    if allowed & blocked or any(not re.fullmatch(r"[0-9A-F]{12}", v) for v in allowed | blocked):
        parser.error("device lists must be disjoint 12-hex MAC identifiers")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    # No application imports, environment loading or DB connection during pure tests/help.
    try:
        from sqlalchemy import text
        from task_server.api_testing.db import _session_factory
        with _session_factory()() as session:
            session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            session.execute(text("SET LOCAL statement_timeout = '30s'"))
            session.execute(text("SET LOCAL lock_timeout = '3s'"))
            try:
                data = load_rows(session, args.project_id)
            finally:
                session.rollback()
        with os.fdopen(os.open(args.output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "w", encoding="utf-8") as output:
            for item in report(data, allowed, blocked):
                output.write(json.dumps(item, ensure_ascii=False) + "\n")
    except Exception:
        # SQLAlchemy exceptions can contain query parameters or connection details.
        print("AUDIT_FAILED_NO_DATABASE_OR_TEMPLATE_DETAILS_EMITTED", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
