"""刷新、重启 ADB 和启动 scrcpy 时自动重连无线设备。"""

import unittest

from panel_harness import PanelTestCase

PHONE = "192.168.3.41:5555"
OTHER = "192.168.3.99:5555"
DEAD = "192.168.3.250:5555"
REACHABLE = (PHONE, OTHER)


class ReconnectTest(PanelTestCase):
    def test_refresh_connects_missing_saved_address(self):
        self.write_state({}, REACHABLE)
        status, data = self.post("/api/refresh", {"address": "192.168.3.41"})
        self.assertEqual(status, 200, data)
        self.assertEqual(self.serials(data).get(PHONE), "device", data)
        self.assertIn(f"connect {PHONE}", self.adb_log())
        self.assertTrue(any(r["address"] == PHONE and r["ok"] for r in data["reconnect"]), data)

    def test_refresh_skips_when_already_connected(self):
        self.write_state({PHONE: "device"}, REACHABLE)
        status, data = self.post("/api/refresh", {"address": PHONE})
        self.assertEqual(status, 200, data)
        self.assertFalse(any(c.startswith("connect") for c in self.adb_log()), self.adb_log())
        self.assertEqual(data["reconnect"], [])

    def test_refresh_without_address_only_lists(self):
        self.write_state({}, REACHABLE)
        status, data = self.post("/api/refresh", {})
        self.assertEqual(status, 200, data)
        self.assertEqual(self.adb_log(), ["devices -l"])
        self.assertIn("emulator-5554", self.serials(data))

    def test_refresh_reports_unreachable_address(self):
        self.write_state({}, REACHABLE)
        status, data = self.post("/api/refresh", {"address": DEAD})
        self.assertEqual(status, 200, data)
        self.assertNotIn(DEAD, self.serials(data))
        [result] = data["reconnect"]
        self.assertFalse(result["ok"])
        self.assertIn("Connection refused", result["message"])
        self.assertIn("adb tcpip", result["message"])

    def test_refresh_reconnects_offline_device(self):
        self.write_state({PHONE: "offline"}, REACHABLE)
        status, data = self.post("/api/refresh", {"address": PHONE})
        self.assertEqual(status, 200, data)
        log = self.adb_log()
        self.assertLess(log.index(f"disconnect {PHONE}"), log.index(f"connect {PHONE}"))
        self.assertEqual(self.serials(data).get(PHONE), "device")

    def test_refresh_leaves_unauthorized_device_alone(self):
        self.write_state({PHONE: "unauthorized"}, REACHABLE)
        status, data = self.post("/api/refresh", {"address": PHONE})
        self.assertEqual(status, 200, data)
        self.assertFalse(any("connect" in c for c in self.adb_log()), self.adb_log())
        [result] = data["reconnect"]
        self.assertFalse(result["ok"])
        self.assertIn("授权", result["message"])

    def test_restart_adb_restores_wireless_devices(self):
        self.write_state({OTHER: "device"}, REACHABLE)
        status, data = self.post("/api/restart-adb", {"address": PHONE})
        self.assertEqual(status, 200, data)
        log = self.adb_log()
        self.assertLess(log.index("kill-server"), log.index(f"connect {OTHER}"))
        self.assertIn(f"connect {PHONE}", log)
        serials = self.serials(data)
        self.assertEqual(serials.get(OTHER), "device", data)
        self.assertEqual(serials.get(PHONE), "device", data)
        self.assertEqual({r["address"] for r in data["reconnect"]}, {OTHER, PHONE})

    def test_start_connects_missing_wireless_serial_first(self):
        self.write_state({}, REACHABLE)
        status, data = self.post("/api/start", {"serial": PHONE})
        self.assertEqual(status, 200, data)
        self.assertIn(f"connect {PHONE}", self.adb_log())
        self.assertEqual(self.read_state()["connected"].get(PHONE), "device")
        self.post("/api/stop", {})

    def test_start_fails_fast_when_wireless_serial_unreachable(self):
        self.write_state({}, REACHABLE)
        status, data = self.post("/api/start", {"serial": DEAD})
        self.assertEqual(status, 400, data)
        self.assertIn(DEAD, data["error"])
        self.assertFalse(self.get("/api/status")["scrcpy_running"])


if __name__ == "__main__":
    unittest.main()
