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
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from task_server.services import apifox_discovery_service as discovery
from task_server.services import api_source_service


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

                def emit(payload):
                    text = json.dumps(payload, ensure_ascii=False)
                    if mode == "prefixed_json" and args[:2] in (
                        ["project", "get"],
                        ["branch", "list"],
                        ["environment", "list"],
                    ):
                        print("提示：正在读取 Apifox 资产")
                        print(text)
                        print("提示：读取完成")
                    else:
                        print(text)

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
                        emit({{
                            "success": True,
                            "data": [{{
                                "id": 5904970,
                                "name": "3D 接口",
                                "description": "打印业务",
                                "team": {{"id": 12, "name": "功夫豆"}},
                            }}],
                        }})
                elif args[:2] == ["project", "get"]:
                    emit({{
                        "success": True,
                        "data": {{
                            "id": 5904970,
                            "name": "3D 接口",
                            "description": "打印业务",
                            "team": {{"id": 12, "name": "功夫豆"}},
                        }},
                    }})
                elif args[:2] == ["branch", "list"]:
                    emit({{
                        "success": True,
                        "data": [{{"id": 88, "name": "测试分支"}}],
                    }})
                elif args[:2] == ["environment", "list"]:
                    if mode == "large_json":
                        emit({{
                            "success": True,
                            "data": [
                                {{"id": index, "name": "环境" + str(index), "baseUrls": {{"default": "127.0.0.1:" + str(8000 + index)}}}}
                                for index in range(200)
                            ],
                        }})
                        raise SystemExit(0)
                    emit({{
                        "success": True,
                        "data": [{{
                            "id": 99,
                            "name": "APP 测试环境",
                            "baseUrls": {{"default": "https://app-api.example.test", "upload": "https://upload.example.test"}},
                            "variables": [
                                {{"name": "tenantId", "value": "tenant-3d"}},
                                {{"name": "accessToken", "value": "secret-runtime-token"}},
                            ],
                        }}],
                    }})
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
                {"id": "", "name": "不绑定环境", "is_default": True, "environment_snapshot": {}},
                {
                    "id": "99",
                    "name": "APP 测试环境",
                    "is_default": False,
                    "environment_snapshot": {
                        "base_urls": [
                            {"name": "default", "url": "https://app-api.example.test"},
                            {"name": "upload", "url": "https://upload.example.test"},
                        ],
                        "variables": [
                            {"name": "tenantId", "value": "tenant-3d", "sensitive": False, "scope": "environment"},
                            {"name": "accessToken", "value": "", "sensitive": True, "scope": "environment"},
                        ],
                        "variable_count": 2,
                        "sensitive_variable_count": 1,
                    },
                },
            ],
            result["environments"],
        )
        self.assertNotIn(self.token, json.dumps(result, ensure_ascii=False))
        self.assertNotIn("secret-runtime-token", json.dumps(result, ensure_ascii=False))
        self.assertIn(
            [
                "branch",
                "list",
                "--project",
                "5904970",
                "--type",
                "all",
                "--api-base-url",
                discovery.DEFAULT_BASE_URL,
            ],
            self._argv(),
        )

    def test_project_context_includes_safe_environment_snapshot(self):
        result = discovery.discover_project_context(
            self.token,
            "5904970",
            cli_bin=str(self.cli_path),
            timeout_seconds=5,
        )

        snapshot = result["environments"][1]["environment_snapshot"]

        self.assertEqual(
            [
                {"name": "default", "url": "https://app-api.example.test"},
                {"name": "upload", "url": "https://upload.example.test"},
            ],
            snapshot["base_urls"],
        )
        self.assertEqual(2, snapshot["variable_count"])
        self.assertEqual(1, snapshot["sensitive_variable_count"])
        self.assertEqual("tenant-3d", snapshot["variables"][0]["value"])
        self.assertEqual("", snapshot["variables"][1]["value"])
        self.assertTrue(snapshot["variables"][1]["sensitive"])
        self.assertNotIn("secret-runtime-token", json.dumps(snapshot, ensure_ascii=False))

    def test_project_context_accepts_cli_json_with_prompt_noise(self):
        self._set_mode("prefixed_json")

        result = discovery.discover_project_context(
            self.token,
            "5904970",
            cli_bin=str(self.cli_path),
            timeout_seconds=5,
        )

        self.assertEqual("3D 接口", result["project"]["name"])
        self.assertEqual("测试分支", result["branches"][1]["name"])
        self.assertEqual("APP 测试环境", result["environments"][1]["name"])
        self.assertNotIn(self.token, json.dumps(result, ensure_ascii=False))

    def test_project_context_does_not_truncate_large_cli_json(self):
        self._set_mode("large_json")

        result = discovery.discover_project_context(
            self.token,
            "5904970",
            cli_bin=str(self.cli_path),
            timeout_seconds=5,
        )

        self.assertEqual("3D 接口", result["project"]["name"])
        self.assertGreaterEqual(len(result["environments"]), 200)
        self.assertEqual("环境199", result["environments"][-1]["name"])
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

    def test_server_installer_provisions_the_pinned_cli_without_blocking_deploy(self):
        install_source = (ROOT / "deploy" / "install-server.sh").read_text(
            encoding="utf-8"
        )
        env_source = (ROOT / "deploy" / "midscene.env.example").read_text(
            encoding="utf-8"
        )

        self.assertIn('APIFOX_CLI_VERSION="${APIFOX_CLI_VERSION:-2.2.8}"', install_source)
        self.assertIn(
            'APIFOX_CLI_INSTALL_TIMEOUT_SECONDS="${APIFOX_CLI_INSTALL_TIMEOUT_SECONDS:-600}"',
            install_source,
        )
        self.assertIn('apifox-cli@${APIFOX_CLI_VERSION}', install_source)
        self.assertIn("apifox_cli_usable", install_source)
        self.assertIn("--prefer-offline", install_source)
        self.assertIn("--ignore-scripts", install_source)
        self.assertIn("--fetch-retries=1", install_source)
        self.assertIn('if [ "${install_status}" -eq 124 ]; then', install_source)
        self.assertIn("不再重复切源安装", install_source)
        self.assertIn("手动连接仍可使用", install_source)
        self.assertIn("export APIFOX_CLI_BIN='apifox'", env_source)


class _RouteHandler:
    def __init__(self, body=None, authorized=True):
        self.body = body or {}
        self.authorized = authorized
        self.responses = []

    def _authorized(self):
        return self.authorized

    def _body(self):
        return self.body

    def _json(self, payload, status=200):
        self.responses.append((status, payload))


class ApifoxDiscoveryRouteChecks(unittest.TestCase):
    def setUp(self):
        self.previous_source_dir = api_source_service.API_TESTING_DIR
        self.source_dir = tempfile.mkdtemp(prefix="apifox_discovery_routes_")
        api_source_service.API_TESTING_DIR = self.source_dir

    def tearDown(self):
        api_source_service.API_TESTING_DIR = self.previous_source_dir
        shutil.rmtree(self.source_dir, ignore_errors=True)

    @staticmethod
    def _projects():
        return {
            "capability": {
                "available": True,
                "version": "2.2.8",
                "minimum_version": "2.2.6",
            },
            "projects": [{
                "id": "5904970",
                "name": "3D 接口",
                "description": "打印业务",
                "team": {"id": "12", "name": "功夫豆"},
            }],
        }

    @staticmethod
    def _project_context():
        return {
            "capability": {
                "available": True,
                "version": "2.2.8",
                "minimum_version": "2.2.6",
            },
            "project": {
                "id": "5904970",
                "name": "3D 接口",
                "description": "打印业务",
                "team": {"id": "12", "name": "功夫豆"},
            },
            "branches": [
                {"id": "", "name": "主分支（默认）", "is_default": True},
            ],
            "environments": [
                {"id": "", "name": "不绑定环境", "is_default": True},
            ],
        }

    def test_discovery_routes_require_user_authentication(self):
        from task_server import router

        for path in (
            "/api/api-testing/apifox/discovery/projects",
            "/api/api-testing/apifox/discovery/project-context",
        ):
            handler = _RouteHandler(authorized=False)
            router.POST_ROUTES[path](handler, {})
            self.assertEqual(401, handler.responses[-1][0])

    def test_project_route_uses_direct_write_only_token(self):
        from task_server import router

        captured = {}

        def fake_discover_projects(access_token, **kwargs):
            captured["access_token"] = access_token
            captured.update(kwargs)
            return self._projects()

        handler = _RouteHandler({
            "access_token": "test-only-direct-token",
            "base_url": "https://api.apifox.com",
        })
        with mock.patch.object(
            discovery,
            "discover_projects",
            side_effect=fake_discover_projects,
        ):
            router.POST_ROUTES[
                "/api/api-testing/apifox/discovery/projects"
            ](handler, {})

        status, payload = handler.responses[-1]
        self.assertEqual(200, status)
        self.assertEqual("test-only-direct-token", captured["access_token"])
        self.assertEqual("https://api.apifox.com", captured["base_url"])
        self.assertEqual("3D 接口", payload["projects"][0]["name"])
        self.assertNotIn("test-only-direct-token", json.dumps(payload, ensure_ascii=False))

    def test_project_context_route_reuses_stored_source_credentials(self):
        from task_server import router

        source = api_source_service.save_api_source({
            "name": "3D 接口",
            "base_url": "https://api.apifox.example.test",
            "project_id": "5904970",
            "access_token": "test-only-stored-token",
        })
        captured = {}

        def fake_discover_context(access_token, project_id, **kwargs):
            captured["access_token"] = access_token
            captured["project_id"] = project_id
            captured.update(kwargs)
            return self._project_context()

        handler = _RouteHandler({
            "source_id": source["source_id"],
            "project_id": "5904970",
        })
        with mock.patch.object(
            discovery,
            "discover_project_context",
            side_effect=fake_discover_context,
        ):
            router.POST_ROUTES[
                "/api/api-testing/apifox/discovery/project-context"
            ](handler, {})

        status, payload = handler.responses[-1]
        self.assertEqual(200, status)
        self.assertEqual("test-only-stored-token", captured["access_token"])
        self.assertEqual("5904970", captured["project_id"])
        self.assertEqual("https://api.apifox.example.test", captured["base_url"])
        self.assertEqual("主分支（默认）", payload["branches"][0]["name"])
        self.assertNotIn("test-only-stored-token", json.dumps(payload, ensure_ascii=False))

    def test_discovery_routes_validate_source_credentials_and_project(self):
        from task_server import router

        missing_source = _RouteHandler({"source_id": "missing-source"})
        router.POST_ROUTES[
            "/api/api-testing/apifox/discovery/projects"
        ](missing_source, {})
        self.assertEqual(404, missing_source.responses[-1][0])

        missing_credentials = _RouteHandler()
        router.POST_ROUTES[
            "/api/api-testing/apifox/discovery/projects"
        ](missing_credentials, {})
        self.assertEqual(400, missing_credentials.responses[-1][0])

        missing_project = _RouteHandler({"access_token": "test-only-token"})
        router.POST_ROUTES[
            "/api/api-testing/apifox/discovery/project-context"
        ](missing_project, {})
        self.assertEqual(400, missing_project.responses[-1][0])

    def test_discovery_error_preserves_safe_status_and_manual_fallback(self):
        from task_server import router

        handler = _RouteHandler({"access_token": "test-only-bad-token"})
        error = discovery.ApifoxDiscoveryError(
            "AUTH_FAILED",
            "Apifox 访问令牌无效或已过期",
            401,
        )
        with mock.patch.object(
            discovery,
            "discover_projects",
            side_effect=error,
        ):
            router.POST_ROUTES[
                "/api/api-testing/apifox/discovery/projects"
            ](handler, {})

        status, payload = handler.responses[-1]
        self.assertEqual(401, status)
        self.assertEqual("AUTH_FAILED", payload["code"])
        self.assertTrue(payload["manual_fallback"])
        self.assertNotIn("test-only-bad-token", json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
