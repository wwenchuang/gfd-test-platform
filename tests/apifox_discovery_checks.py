#!/usr/bin/env python3
"""Focused contracts for secure, read-only Apifox CLI discovery."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from task_server.services import apifox_discovery_service as discovery


class ApifoxDiscoveryServiceChecks(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="apifox_discovery_checks_"))
        self.args_log = self.temp_dir / "args.jsonl"
        self.homes_log = self.temp_dir / "homes.txt"
        self.mode_file = self.temp_dir / "mode.txt"
        self.mode_file.write_text("success", encoding="utf-8")
        self.token = "test-only-apifox-token"
        self.cli_path = self.temp_dir / "apifox"
        self.cli_path.write_text(
            textwrap.dedent(
                f"""\
                #!/usr/bin/env python3
                import json
                import pathlib
                import sys
                import time

                args_log = pathlib.Path({str(self.args_log)!r})
                homes_log = pathlib.Path({str(self.homes_log)!r})
                mode_file = pathlib.Path({str(self.mode_file)!r})
                expected_token = {self.token!r}
                mode = mode_file.read_text(encoding="utf-8").strip()
                args = sys.argv[1:]
                home = pathlib.Path.home()

                with args_log.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(args, ensure_ascii=False) + "\\n")
                with homes_log.open("a", encoding="utf-8") as handle:
                    handle.write(str(home) + "\\n")

                if args == ["--version"]:
                    print("2.2.5" if mode == "old_version" else "apifox-cli/2.2.8")
                elif args[:2] == ["auth", "login"]:
                    token = sys.stdin.readline().strip()
                    if mode == "auth_error" or token != expected_token:
                        print(json.dumps({{
                            "success": False,
                            "error": {{
                                "code": "AUTHENTICATION_FAILED",
                                "message": "invalid " + token,
                            }},
                        }}))
                        raise SystemExit(1)
                    (home / "logged-in").write_text("yes", encoding="utf-8")
                    print(json.dumps({{"success": True, "data": {{"message": "ok"}}}}))
                elif not (home / "logged-in").exists():
                    print(json.dumps({{
                        "success": False,
                        "error": {{"code": "AUTHENTICATION_FAILED"}},
                    }}))
                    raise SystemExit(1)
                elif args[:2] == ["project", "list"]:
                    if mode == "timeout":
                        time.sleep(2)
                    if mode == "invalid_json":
                        print("{{broken")
                    else:
                        print(json.dumps({{
                            "success": True,
                            "data": [{{
                                "id": 5904970,
                                "name": "3D 接口",
                                "description": "打印业务",
                                "team": {{"id": 12, "name": "功夫豆"}},
                            }}],
                        }}, ensure_ascii=False))
                elif args[:2] == ["project", "get"]:
                    print(json.dumps({{
                        "success": True,
                        "data": {{
                            "id": 5904970,
                            "name": "3D 接口",
                            "description": "打印业务",
                            "team": {{"id": 12, "name": "功夫豆"}},
                        }},
                    }}, ensure_ascii=False))
                elif args[:2] == ["branch", "list"]:
                    print(json.dumps({{
                        "success": True,
                        "data": [{{"id": 88, "name": "测试分支"}}],
                    }}, ensure_ascii=False))
                elif args[:2] == ["environment", "list"]:
                    print(json.dumps({{
                        "success": True,
                        "data": [{{"id": 99, "name": "APP 测试环境"}}],
                    }}, ensure_ascii=False))
                else:
                    print(json.dumps({{
                        "success": False,
                        "error": {{"code": "UNKNOWN_COMMAND"}},
                    }}))
                    raise SystemExit(2)
                """
            ),
            encoding="utf-8",
        )
        self.cli_path.chmod(0o700)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _set_mode(self, mode):
        self.mode_file.write_text(mode, encoding="utf-8")

    def _argv(self):
        if not self.args_log.exists():
            return []
        return [
            json.loads(line)
            for line in self.args_log.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def _homes(self):
        if not self.homes_log.exists():
            return []
        return [
            Path(line)
            for line in self.homes_log.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def test_project_discovery_uses_stdin_and_removes_isolated_home(self):
        result = discovery.discover_projects(
            self.token,
            cli_bin=str(self.cli_path),
            timeout_seconds=5,
        )

        self.assertEqual("2.2.8", result["capability"]["version"])
        self.assertEqual("5904970", result["projects"][0]["id"])
        self.assertEqual("3D 接口", result["projects"][0]["name"])
        self.assertEqual("功夫豆", result["projects"][0]["team"]["name"])
        self.assertNotIn(self.token, json.dumps(self._argv(), ensure_ascii=False))
        self.assertNotIn(self.token, json.dumps(result, ensure_ascii=False))
        isolated_homes = [path for path in self._homes() if path != Path.home()]
        self.assertTrue(isolated_homes)
        self.assertTrue(all(not path.exists() for path in isolated_homes))

    def test_project_context_returns_named_defaults_and_remote_options(self):
        result = discovery.discover_project_context(
            self.token,
            "5904970",
            cli_bin=str(self.cli_path),
            timeout_seconds=5,
        )

        self.assertEqual("3D 接口", result["project"]["name"])
        self.assertEqual(
            [
                {"id": "", "name": "主分支（默认）", "is_default": True},
                {"id": "88", "name": "测试分支", "is_default": False},
            ],
            result["branches"],
        )
        self.assertEqual(
            [
                {"id": "", "name": "不绑定环境", "is_default": True},
                {"id": "99", "name": "APP 测试环境", "is_default": False},
            ],
            result["environments"],
        )
        self.assertNotIn(self.token, json.dumps(result, ensure_ascii=False))

    def test_unsupported_cli_version_has_a_stable_safe_error(self):
        self._set_mode("old_version")

        with self.assertRaises(discovery.ApifoxDiscoveryError) as raised:
            discovery.discover_projects(
                self.token,
                cli_bin=str(self.cli_path),
                timeout_seconds=5,
            )

        self.assertEqual("CLI_VERSION_UNSUPPORTED", raised.exception.code)
        self.assertEqual(503, raised.exception.http_status)
        self.assertTrue(raised.exception.manual_fallback)
        self.assertNotIn(self.token, str(raised.exception))

    def test_auth_failure_never_echoes_the_token(self):
        self._set_mode("auth_error")

        with self.assertRaises(discovery.ApifoxDiscoveryError) as raised:
            discovery.discover_projects(
                self.token,
                cli_bin=str(self.cli_path),
                timeout_seconds=5,
            )

        self.assertEqual("AUTH_FAILED", raised.exception.code)
        self.assertEqual(401, raised.exception.http_status)
        self.assertNotIn(self.token, str(raised.exception))
        self.assertNotIn(self.token, json.dumps(raised.exception.as_dict(), ensure_ascii=False))

    def test_invalid_json_is_rejected_without_returning_raw_output(self):
        self._set_mode("invalid_json")

        with self.assertRaises(discovery.ApifoxDiscoveryError) as raised:
            discovery.discover_projects(
                self.token,
                cli_bin=str(self.cli_path),
                timeout_seconds=5,
            )

        self.assertEqual("INVALID_RESPONSE", raised.exception.code)
        self.assertEqual(503, raised.exception.http_status)
        self.assertNotIn("{broken", str(raised.exception))

    def test_total_deadline_terminates_slow_discovery(self):
        self._set_mode("timeout")

        with self.assertRaises(discovery.ApifoxDiscoveryError) as raised:
            discovery.discover_projects(
                self.token,
                cli_bin=str(self.cli_path),
                timeout_seconds=0.25,
            )

        self.assertEqual("TIMEOUT", raised.exception.code)
        self.assertEqual(504, raised.exception.http_status)
        isolated_homes = [path for path in self._homes() if path != Path.home()]
        self.assertTrue(all(not path.exists() for path in isolated_homes))


if __name__ == "__main__":
    unittest.main()
