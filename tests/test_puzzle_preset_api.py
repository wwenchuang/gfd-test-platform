import unittest
from types import SimpleNamespace
from unittest.mock import patch

from task_server.services import puzzle_preset_api as api


class PuzzlePresetApiTest(unittest.TestCase):
    def setUp(self):
        runtime = SimpleNamespace(
            base_url_for=lambda _: "https://print.wisebeginner3d.com/app",
            headers={"Biz": "ZXB", "Authorization": "private-value", "X-Request-Id": "stale"},
        )
        runtime_patch = patch.object(api, "_authorized_runtime", return_value=("revision", runtime))
        self.runtime = runtime_patch.start()
        self.addCleanup(runtime_patch.stop)
        request_patch = patch("task_server.api_testing.executor.HttpExecutor._request")
        self.request = request_patch.start()
        self.addCleanup(request_patch.stop)
        self.response = SimpleNamespace(status_code=200, headers={"X-Request-ID": "from-server"}, json_body={
            "code": 0, "data": [{"name": "太空探险", "imageUrl": "https://assets.example/a.PNG", "token": "ignored"}]})
        self.request.return_value = self.response

    def test_current_get_and_only_public_evidence(self):
        first = api.fetch_presets("tester", "正式")
        second = api.fetch_presets("tester", "正式")
        self.assertTrue(first["ok"])
        self.assertNotEqual(first["request_id"], second["request_id"])
        self.assertEqual(first["response_request_id"], "from-server")
        self.assertEqual(first["presets"], [{"name": "太空探险", "imageUrl": "https://assets.example/a.PNG"}])
        self.assertNotIn("private-value", str(first))
        self.assertEqual(self.request.call_count, 2)
        method, url, headers, body = self.request.call_args.args
        self.assertEqual((method, url, body), ("GET", "https://print.wisebeginner3d.com/app/print3d/api/v1/puzzle/presets", None))
        self.assertEqual(headers["X-Request-ID"], second["request_id"])
        self.assertEqual(headers["Authorization"], "private-value")
        self.assertNotIn("stale", headers.values())
        self.assertFalse(self.request.call_args.kwargs["follow_redirects"])

    def test_no_cross_environment_or_unapproved_url(self):
        self.assertFalse(api.fetch_presets("tester", "测试")["ok"])
        self.assertFalse(api.fetch_presets("tester", "other")["ok"])
        self.request.assert_not_called()

    def test_unavailable_environment_does_not_run_or_disclose(self):
        self.runtime.side_effect = RuntimeError("secret credentials")
        result = api.fetch_presets("tester", "正式")
        self.assertFalse(result["ok"])
        self.assertIsNone(result["http"])
        self.assertFalse(result["request_id"])
        self.assertNotIn("secret credentials", str(result))
        self.request.assert_not_called()

    def test_failed_or_unusable_response(self):
        for http, code, data in [(403, 0, []), (200, 4009, []), (200, False, []), (200, 0, [{}])]:
            with self.subTest(http=http, code=code, data=data):
                self.response.status_code = http
                self.response.json_body = {"code": code, "data": data}
                result = api.fetch_presets("tester", "正式")
                self.assertFalse(result["ok"])
                self.assertEqual(result["http"], http)
                self.assertEqual(result["presets"], [])


class PuzzlePresetResourceTest(unittest.TestCase):
    valid = "https://micro.obs.cn-east-2.myhuaweicloud.com/print3d/template/%E6%B5%AE%E9%9B%95%E6%8B%BC%E5%9B%BE/demo.PNG"

    def test_only_fixed_puzzle_asset_accepted(self):
        with patch("http.client.HTTPSConnection") as transport:
            for target in [self.valid.replace("micro.", "other."), self.valid + "?token=sensitive",
                           self.valid.replace("demo", "%252e%252e%252fprivate"), self.valid.replace("/template/", "/private/"),
                           self.valid.replace("https:", "http:")]:
                with self.subTest(target=target), self.assertRaises(ValueError):
                    api.fetch_resource(target)
            transport.assert_not_called()

    def test_reads_bounded_image_and_rejects_redirect(self):
        with patch("http.client.HTTPSConnection") as transport:
            reply = transport.return_value.getresponse.return_value
            reply.status = 302
            with self.assertRaises(ValueError):
                api.fetch_resource(self.valid)
            reply.read.assert_not_called()
            reply.status = 200
            reply.read.return_value = b"\x89PNG\r\n\x1a\nimage"
            self.assertEqual(api.fetch_resource(self.valid), reply.read.return_value)
            self.assertEqual(reply.read.call_args.args, (api._MAX_IMAGE_BYTES + 1,))
            self.assertEqual(transport.return_value.request.call_args.args[:2],
                             ("GET", self.valid.split(".com", 1)[1]))
            reply.read.return_value = b"x" * (api._MAX_IMAGE_BYTES + 1)
            with self.assertRaises(ValueError):
                api.fetch_resource(self.valid)


class PuzzlePresetAccessTest(unittest.TestCase):
    def test_unknown_user_never_opens_api_database(self):
        with patch("task_server.api_testing.access.get_access_profile", return_value=None), \
                patch("task_server.api_testing.db._session_factory") as database:
            self.assertFalse(api.fetch_presets("unknown", "正式")["ok"])
            database.assert_not_called()

    def test_production_permission_applies_before_resolving_credentials(self):
        profile = {"status": "active", "permissions": ["api.view", "api.execute"]}
        with patch("task_server.api_testing.access.get_access_profile", return_value=profile), \
                patch("task_server.api_testing.db._session_factory") as database:
            self.assertFalse(api.fetch_presets("tester", "正式")["ok"])
            database.assert_not_called()

    def test_environment_selection_rechecks_execution_scope_on_chosen_revision(self):
        profile = {"status": "active", "is_superuser": True}
        with patch("task_server.api_testing.access.get_access_profile", return_value=profile), \
                patch("task_server.api_testing.db._session_factory") as database, \
                patch("task_server.api_testing.access.require_execution_environment", side_effect=PermissionError) as access_check, \
                patch("task_server.api_testing.services.environment_service.EnvironmentService") as environment:
            session = database.return_value.return_value.__enter__.return_value
            rows = session.execute.return_value.all
            for entries in ([], [("revision", "project"), ("another", "other")]):
                rows.return_value = entries
                self.assertFalse(api.fetch_presets("tester", "测试")["ok"])
            access_check.assert_not_called()
            rows.return_value = [("revision", "project")]
            self.assertFalse(api.fetch_presets("tester", "测试")["ok"])
            access_check.assert_called_once_with(session, "revision", "tester", "project")
            environment.assert_not_called()
