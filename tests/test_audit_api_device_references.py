"""Pure-data safety tests; no application or database initialization."""
import importlib.util
import json
from pathlib import Path
import unittest

from sqlalchemy.dialects import postgresql

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/audit_api_device_references.py"
audit = None
if SCRIPT.exists():
    spec = importlib.util.spec_from_file_location("device_audit", SCRIPT)
    audit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(audit)


class RecordingSession:
    """Compile real ORM SQL without creating an engine or opening a connection."""
    def __init__(self, project_id):
        self.project_id = project_id
        self.statements = []

    def record(self, statement):
        self.statements.append(str(statement.compile(dialect=postgresql.dialect())))

    def scalar(self, statement):
        self.record(statement)
        return self.project_id

    def execute(self, statement):
        self.record(statement)
        return []


class DeviceAuditTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(audit, "read-only audit script must exist")

    def test_nested_json_and_sensitive_values_are_never_returned(self):
        data = {"headers": {"Authorization": "Bearer top-secret-18CEDF5BA7B2"},
                "cleanup_steps": [{"request": {"body": json.dumps({
                    "deviceId": "18CEDF5BA7B2", "password": "do-not-print"})}}]}
        hits = list(audit.walk(data, {"18CEDF5BA7B2"}))
        output = json.dumps(hits)
        self.assertIn("18CEDF5BA7B2", output)
        self.assertIn("cleanup_steps", output)
        self.assertNotIn("top-secret", output)
        self.assertNotIn("do-not-print", output)

    def test_dynamic_and_other_device_fields_are_marked_without_values(self):
        hits = list(audit.walk({"deviceSn": "{{picked}}", "device_id": "ABCDEF123456",
                                "extraction": {"jsonpath": "$.devices[0].id"}}, set()))
        output = json.dumps(hits)
        self.assertIn("DEVICE_FIELD_REVIEW", output)
        self.assertIn("DYNAMIC_REFERENCE", output)
        self.assertIn("EXTRACTION_REVIEW", output)
        self.assertNotIn("ABCDEF123456", output)
        self.assertNotIn("$.devices", output)

    def test_reverse_dependency_closure_handles_transitive_cycle(self):
        versions = [
            {"id": "old", "dependency_spec": {}},
            {"id": "parent", "dependency_spec": {"dependencies": [{"case_version_id": "old"}, {"case_version_id": "outer"}]}},
            {"id": "outer", "dependency_spec": {"dependencies": [{"case_version_id": "parent"}]}},
            {"id": "unrelated", "dependency_spec": {}},
        ]
        self.assertEqual(audit.dependency_closure(versions, {"old"}), {"old", "parent", "outer"})

    def test_archived_baseline_and_case_schedule_have_different_reachability(self):
        baselines = [{"id": "archived", "status": "archived", "case_version_id": "old", "group_name": "g"},
                     {"id": "active", "status": "active", "case_version_id": "parent", "group_name": "g"}]
        versions = [{"id": "old", "endpoint_id": "e1"}, {"id": "parent", "endpoint_id": "e2"}]
        self.assertEqual(audit.resolve_targets("cases", ["old"], baselines, versions, []), ({"old"}, "DIRECT_CASE_VERSION"))
        self.assertEqual(audit.resolve_targets("baselines", ["archived", "active"], baselines, versions, []), (set(), "INACTIVE_OR_MISSING_BASELINE"))
        self.assertEqual(audit.resolve_targets("baseline_group", ["g"], baselines, versions, []), ({"parent"}, "ACTIVE_BASELINES_ONLY"))
        self.assertEqual(audit.resolve_targets("task", ["t"], baselines, versions,
                                             [{"id": "t", "selected_endpoint_ids": ["e1", "e2"]}]),
                         ({"parent"}, "ACTIVE_BASELINES_ONLY"))

    def test_report_connects_dependencies_environment_and_pending_without_secrets(self):
        data = {key: [] for key in (
            "cases", "versions", "data_rows", "scripts", "extractions", "assertions",
            "environment_revisions", "environment_variables", "environment_services",
            "pending", "baselines", "environments", "jobs", "targets", "tasks", "pending_cases")}
        data["cases"] = [{"id": "c", "name": "case", "active_version_id": "parent"}]
        data["versions"] = [
            {"id": "old", "case_id": "c", "endpoint_id": "e", "request_template": {"body": {"deviceId": "18CEDF5BA7B2"}}},
            {"id": "parent", "case_id": "c", "endpoint_id": "e", "dependency_spec": {"dependencies": [{"case_version_id": "old"}]}}]
        data["baselines"] = [{"id": "b", "case_id": "c", "case_version_id": "parent", "status": "active", "group_name": "g", "environment_revision_id": "er"}]
        data["environment_variables"] = [{"id": "secret", "name": "token", "revision_id": "er", "is_secret": True, "value": "NEVER_OUTPUT_SECRET"}]
        data["jobs"] = [{"id": "j", "name": "schedule", "enabled": False, "target_type": "baseline_group", "environment_strategy": "fixed_revision", "environment_revision_id": "er"}]
        data["targets"] = [{"id": "jt", "job_id": "j", "target_id": "g"}]
        data["pending_cases"] = [{"id": "pc", "execution_id": "run", "case_version_id": "old"}]
        rows = list(audit.report(data, {"9888E0094F2A"}, {"18CEDF5BA7B2"}))
        self.assertNotIn("NEVER_OUTPUT_SECRET", json.dumps(rows))
        self.assertTrue(any(r.get("id") == "j" and r.get("marker") == "BLOCKED_DEVICE_REACHABLE" for r in rows))
        self.assertTrue(any(r.get("id") == "pc" and r.get("marker") == "BLOCKED_DEVICE_REACHABLE" for r in rows))
        self.assertTrue(any(r.get("id") == "b" and r.get("path") == "environment_revision_id" and r.get("related_id") == "er" for r in rows))
        self.assertTrue(any(r.get("id") == "secret" and r.get("path") == "revision_id" for r in rows))

    def test_load_rows_compiles_real_models_and_hides_secret_values_in_sql(self):
        session = RecordingSession("project-id")
        data = audit.load_rows(session, "project-id")
        self.assertEqual(len(data), 16)
        self.assertEqual(len(session.statements), 17)
        self.assertIn("api_projects.id", session.statements[0])
        sql = next(s for s in session.statements if "FROM api_environment_variables" in s)
        self.assertIn("CASE WHEN (api_environment_variables.is_secret IS true) THEN NULL ELSE api_environment_variables.value END AS value", sql)
        self.assertNotIn("api_secret_values", "\n".join(session.statements))
        self.assertTrue(all(statement.startswith("SELECT ") for statement in session.statements))

    def test_unknown_project_is_rejected_before_inventory_queries(self):
        session = RecordingSession(None)
        with self.assertRaisesRegex(ValueError, "PROJECT_NOT_FOUND"):
            audit.load_rows(session, "missing-project")
        self.assertEqual(len(session.statements), 1)

    def test_empty_existing_project_has_explicit_zero_object_counts(self):
        data = audit.load_rows(RecordingSession("empty-project"), "empty-project")
        rows = list(audit.report(data, set(), set()))
        counts = [r for r in rows if r.get("kind") == "inventory_count"]
        self.assertEqual({r["object_kind"] for r in counts}, set(data))
        self.assertTrue(all(r["count"] == 0 for r in counts))
        self.assertEqual(rows[-1]["marker"], "COMPLETE_STATIC_INVENTORY_NOT_RUNTIME_SAFETY_PROOF")


if __name__ == "__main__":
    unittest.main()
