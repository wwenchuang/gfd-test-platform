"""End-to-end offline report tests. All inputs are synthetic unit-test data."""
import json
import tempfile
import unittest
from pathlib import Path

import report


class RecomputeTest(unittest.TestCase):
    def stage(self, root, name, status="passed", end=3000, threads=1):
        stage = root / name
        stage.mkdir()
        metadata = dict(stage_start_ms=1000, stage_end_ms=end, steady_start_ms=2000, steady_end_ms=3000,
                        recovery_start_ms=end, recovery_end_ms=end + 200, status=status, threads=threads,
                        warmup_seconds=1, steady_seconds=1, think_ms=1000)
        (stage / "metadata.json").write_text(json.dumps(metadata))
        failure = "false,question_count" if name == "negative" else "true,"
        (stage / "requests.jtl").write_text(
            'timeStamp,elapsed,label,responseCode,success,failureMessage\n'
            f'1000,10,knowledge-evaluation,200,{failure}\n'
            + ('' if name == 'negative' else '2010,33,knowledge-evaluation,200,true,\n'))
        (stage / "monitor.csv").write_text("timeStamp,source,metric,unit,value\n"
            + f"{end + 20},database,db_bytes,bytes,400\n")
        return stage

    def test_separate_checks_and_baseline_comparison(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ["negative", "baseline", "retest"]:
                self.stage(root, name)
            result = report.build_report(root)
        self.assertEqual([r["id"] for r in result["runs"]], ["baseline", "retest"])
        self.assertEqual(result["checks"][0]["observed"]["assertion_failures"], {"question_count": 1})
        self.assertEqual(result["checks"][0]["expected_outcome_observed"], True)
        self.assertEqual(result["runs"][0]["monitor"]["recovery"][0]["mean"], 400)
        self.assertEqual(result["runs"][0]["steady"]["p95_ms"], 33)
        self.assertEqual(len(result["runs"][0]["sha256"]["requests.jtl"]), 64)
        self.assertEqual(result["comparisons"][0]["p95_delta_ms"], 0)
        self.assertEqual(result["missing_stages"], ["smoke", "medium", "high"])

    def test_incomplete_run_respects_actual_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.stage(root, "baseline", status="aborted", end=2250)
            self.stage(root, "retest")
            result = report.build_report(root)
        run = result["runs"][0]
        self.assertEqual(run["steady"]["duration_s"], .25)
        self.assertEqual(run["steady"]["throughput_rps"], 4)
        self.assertFalse(run["complete_steady_window"])
        self.assertFalse(result["comparisons"][0]["comparable"])
        self.assertNotIn("p95_delta_ms", result["comparisons"][0])

    def test_negative_control_requires_exact_http_200(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage = self.stage(root, "negative")
            jtl = stage / "requests.jtl"
            jtl.write_text(jtl.read_text().replace(",200,false", ",201,false"))
            check = report.build_report(root)["checks"][0]
        self.assertFalse(check["expected_outcome_observed"])

    def test_live_directory_lists_open_stage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.stage(root, "negative")
            stage = self.stage(root, "baseline")
            (stage / "metadata.json").write_text(json.dumps(dict(status="running")))
            result = report.build_report(root)
        self.assertEqual(result["pending_stages"], ["baseline"])
        self.assertEqual(result["runs"], [])
        self.assertNotIn("baseline", result["missing_stages"])

    def test_partial_smoke_does_not_match_two_request_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage = self.stage(root, "smoke")
            jtl = stage / "requests.jtl"
            jtl.write_text("\n".join(jtl.read_text().splitlines()[:2]) + "\n")
            result = report.build_report(root)
        self.assertFalse(result["checks"][0]["expected_outcome_observed"])

    def test_closed_stage_rejects_truncated_jtl(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage = self.stage(root, "baseline")
            metadata = stage / "metadata.json"
            values = json.loads(metadata.read_text())
            values["samples"] = 3
            metadata.write_text(json.dumps(values))
            with self.assertRaisesRegex(ValueError, "sample count"):
                report.build_report(root)

    def test_full_clock_window_requires_traffic_near_end(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage = self.stage(root, "baseline", status="failed", end=12000)
            metadata = stage / "metadata.json"
            values = json.loads(metadata.read_text())
            values.update(steady_end_ms=12000, steady_seconds=10)
            metadata.write_text(json.dumps(values))
            result = report.build_report(root)
        self.assertFalse(result["runs"][0]["complete_steady_window"])

    def test_changed_concurrency_prevents_retest_comparison(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.stage(root, "baseline")
            self.stage(root, "retest", threads=5)
            result = report.build_report(root)
        self.assertFalse(result["comparisons"][0]["comparable"])
