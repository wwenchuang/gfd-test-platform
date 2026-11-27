import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

SPEC = importlib.util.spec_from_file_location("server_log_monitor", Path(__file__).resolve().parents[1] / "deploy/log-monitor/monitor.py")
monitor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(monitor)
UNIT = "midscene-task.service"


class MonitorTests(unittest.TestCase):
    def test_reads_only_unit_aggregate_and_keeps_secret_out_of_output(self):
        command = Mock(side_effect=[
            result([{"PRIORITY": "6"}]),
            result([{"PRIORITY": "6", "MESSAGE": '127.0.0.1 "GET /private?token=DO_NOT_COPY HTTP/1.1" 503 -'},
                    {"PRIORITY": "3", "MESSAGE": "ERROR: secret=DO_NOT_COPY"},
                    {"PRIORITY": "6", "MESSAGE": "[2026-11-27 09:41:23,383: ERROR/ForkPoolWorker-1] Task failed"},
                    {"PRIORITY": "6", "MESSAGE": "GET /error HTTP/1.1 200"}])])
        obs = monitor.observe(UNIT, 300, 600, command)
        self.assertEqual(obs["matches"], {"http_5xx": 1, "error": 2})
        self.assertEqual(obs["coverage"], "complete")
        self.assertNotIn("DO_NOT_COPY", json.dumps(obs))
        for call in command.call_args_list:
            self.assertIn(UNIT, call.args[0])
        with self.assertRaises(ValueError):
            monitor.observe("--file=/tmp/private", 300, 600, command)

    def test_empty_source_incomplete_and_inaccessible_windows(self):
        empty = monitor.observe(UNIT, 300, 600, Mock(return_value=result([])))
        self.assertEqual(empty["coverage"], "source_unavailable")
        self.assertEqual(empty["reason"], "no_visible_history")
        denied = monitor.observe(UNIT, 300, 600, Mock(side_effect=FileNotFoundError()))
        self.assertEqual(denied["coverage"], "source_unavailable")
        self.assertEqual(denied["reason"], "journalctl_missing")
        invalid = monitor.observe(UNIT, 300, 600, Mock(side_effect=[result([{}]), result([{"MESSAGE": None, "PRIORITY":"6"}])]))
        self.assertEqual(invalid["coverage"], "partial")

    def test_incident_dedup_source_loss_recovery_retry_and_preview(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "state"
            observe = Mock()
            send = Mock(return_value="sent")
            def run(end, coverage, matches=None, dry=False):
                observe.return_value = {"coverage": coverage, "matches": matches or {}}
                return monitor.scan([UNIT], root, end, dry_run=dry, observe_unit=observe, deliver=send)
            run(600, "complete", {"http_5xx": 1}, dry=True)
            self.assertFalse(root.exists())
            self.assertFalse(send.called)
            send.return_value = "rejected"
            run(600, "complete", {"http_5xx": 1})
            send.return_value = "sent"
            run(900, "complete", {"http_5xx": 2})
            run(1200, "complete", {"error": 2})
            self.assertEqual(send.call_count, 2)
            run(1500, "source_unavailable")
            self.assertEqual(send.call_count, 3)
            run(1800, "source_unavailable")
            self.assertEqual(send.call_count, 3)
            run(2100, "complete")
            self.assertTrue("恢复" in send.call_args.args[0])
            self.assertIn(UNIT, send.call_args.args[0])
            self.assertIn("journalctl", send.call_args.args[0])
            run(2100, "complete")
            self.assertEqual(send.call_count, 4)


    def test_unknown_delivery_and_partial_scan_keep_the_original_incident(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            observe = Mock()
            send = Mock(return_value="unknown")
            def run(end, coverage, matches):
                observe.return_value = {"coverage": coverage, "matches": matches}
                return monitor.scan([UNIT], root, end, observe_unit=observe, deliver=send)[UNIT]
            self.assertEqual(run(600, "complete", {"error": 1})["delivery"], "unknown")
            run(900, "complete", {"error": 3})
            self.assertEqual(send.call_count, 1)
            self.assertTrue(run(1200, "partial", {})["error_active"])
            current = run(1800, "complete", {})
            self.assertFalse(current["error_active"])
            self.assertEqual(current["gap_seconds"], 300)
            self.assertIn("监控中断", current["last_alert"])


    def test_command_line_preview_and_durable_dedup_with_sample_journal(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            command = root / "journalctl"
            command.write_text(
                "#!" + sys.executable + "\nimport sys\n"
                "if '--lines=1' in sys.argv:\n"
                "    print('{\"PRIORITY\": \"6\"}')\n"
                "else:\n"
                "    print('{\"PRIORITY\": \"6\", \"MESSAGE\": \"ERROR: sample-only-private-value\"}')\n"
            )
            command.chmod(0o700)
            state = root / "state"
            env = {**os.environ, "PATH": str(root), "STATE_DIRECTORY": str(state), "MIDSCENE_LOG_MONITOR_NOTIFY": "0"}
            def invoke(mode):
                proc = subprocess.run([sys.executable, SPEC.origin, mode, "--unit", UNIT],
                                      env=env, capture_output=True, text=True, check=True)
                self.assertNotIn("sample-only-private-value", proc.stdout)
                return json.loads(proc.stdout)["sources"][UNIT]
            self.assertEqual(invoke("--dry-run")["matches"], {"error": 1})
            self.assertFalse(state.exists())
            self.assertEqual(invoke("--run")["delivery"], "disabled")
            stored = (state / "state.json").read_bytes()
            self.assertNotIn(b"sample-only-private-value", stored)
            invoke("--run")
            self.assertEqual(stored, (state / "state.json").read_bytes())

    def test_notification_confirms_only_provider_business_receipt(self):
        sender = Mock()
        sender.resolve_task_app_feishu_channel.return_value = ({"ready": True}, "sample")
        with patch.dict(os.environ, {"MIDSCENE_LOG_MONITOR_NOTIFY": "1"}), patch.dict(
                sys.modules, {"task_server.services.feishu_service": sender}):
            for receipt, outcome in (({"code": 0}, "sent"), ({"code": 7}, "rejected"), ({"ok": True}, "unknown")):
                sender.send_feishu_notification.return_value = receipt
                self.assertEqual(monitor.notify("sample"), outcome)


def result(rows):
    return Mock(returncode=0, stdout="".join(json.dumps(row) + "\n" for row in rows), stderr="")
