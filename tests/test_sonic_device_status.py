import unittest
from unittest.mock import patch

from task_server.services import sonic_service


class SonicDeviceStatusTest(unittest.TestCase):
    def setUp(self):
        sonic_service._MEM_CACHE.clear()

    def test_status_keeps_trusted_occupant_and_converts_temperature(self):
        row = sonic_service.normalize_sonic_device_status({
            "udId": "p1", "status": "DEBUGGING", "user": "zhang@example.com",
            "level": 76, "temperature": 320,
        })
        self.assertEqual(row["device_id"], "p1")
        self.assertEqual(row["usage_status"], "busy")
        self.assertEqual(row["usage_label"], "zhang@example.com 占用中")
        self.assertEqual(row["battery_temperature_c"], 32.0)

    def test_online_without_user_is_idle(self):
        row = sonic_service.normalize_sonic_device_status({"udId": "p1", "status": "ONLINE", "user": None})
        self.assertEqual(row["usage_status"], "idle")
        self.assertEqual(row["usage_label"], "空闲可选")

    def test_online_ignores_stale_user_and_offline_is_not_selectable(self):
        online = sonic_service.normalize_sonic_device_status({"udId": "p1", "status": "ONLINE", "user": "stale@example.com"})
        offline = sonic_service.normalize_sonic_device_status({"udId": "p2", "status": "OFFLINE"})
        self.assertEqual(online["usage_status"], "idle")
        self.assertEqual(online["sonic_user"], "")
        self.assertEqual(offline["usage_status"], "unknown")
        self.assertEqual(offline["usage_label"], "状态待确认")

    def test_device_list_uses_controller_prefix_only_once(self):
        with patch.object(sonic_service, "sonic_request", return_value={"data": {"records": []}}) as request:
            result = sonic_service.sonic_list_device_statuses()
        self.assertTrue(result["__source__"]["available"])
        request.assert_called_once_with("GET", "/devices/list", params={"page": 1, "pageSize": 500}, timeout=8)


if __name__ == "__main__":
    unittest.main()
