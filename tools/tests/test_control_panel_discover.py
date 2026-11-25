"""POST /api/discover：扫描局域网中可 ADB 连接的设备。"""

import ipaddress
import socket
import struct
import threading
import time
import unittest

from panel_harness import PanelTestCase

A_CNXN = 0x4E584E43
A_AUTH = 0x48545541


def adb_header(command, arg0, arg1, data=b""):
    return struct.pack("<6I", command, arg0, arg1, len(data), sum(data) & 0xFFFFFFFF,
                       command ^ 0xFFFFFFFF) + data


class Listener:
    """在 127.0.0.1 随机端口上模拟一个服务：adb、非 adb 或不回应。"""

    def __init__(self, mode):
        self.mode = mode
        self.received = []
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self.closed = False
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        while not self.closed:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self.handle, args=(conn,), daemon=True).start()

    def handle(self, conn):
        with conn:
            conn.settimeout(3)
            try:
                data = conn.recv(24 + 64)
                self.received.append(data)
                if self.mode == "adb":
                    conn.sendall(adb_header(A_AUTH, 1, 0, b"\x01" * 20))
                    time.sleep(0.3)
                    try:
                        self.received.append(conn.recv(4096))
                    except OSError:
                        pass
                elif self.mode == "http":
                    conn.sendall(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
                elif self.mode == "silent":
                    time.sleep(3)
            except OSError:
                pass

    def close(self):
        self.closed = True
        self.sock.close()


class DiscoverTest(PanelTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.adb = Listener("adb")
        cls.http = Listener("http")
        cls.silent = Listener("silent")

    @classmethod
    def tearDownClass(cls):
        for listener in (cls.adb, cls.http, cls.silent):
            listener.close()
        super().tearDownClass()

    def discover(self, port, **extra):
        payload = {"subnet": "127.0.0.1/32", "port": port, **extra}
        return self.post("/api/discover", payload)

    def test_finds_adb_service(self):
        self.write_state({})
        status, data = self.discover(self.adb.port)
        self.assertEqual(status, 200, data)
        self.assertTrue(data["ok"])
        self.assertEqual(data["subnets"], ["127.0.0.1/32"])
        self.assertEqual(data["port"], self.adb.port)
        self.assertIsInstance(data["elapsed_ms"], int)
        self.assertEqual(data["devices"], [{
            "address": f"127.0.0.1:{self.adb.port}", "source": ["scan"], "name": "", "serial": "",
            "state": "", "model": "",
        }])

    def test_handshake_is_cnxn_and_never_sends_auth(self):
        self.write_state({})
        before = len(self.adb.received)
        self.discover(self.adb.port)
        time.sleep(0.5)
        sent = b"".join(self.adb.received[before:])
        self.assertGreaterEqual(len(sent), 24)
        command, _, _, length, _, magic = struct.unpack("<6I", sent[:24])
        self.assertEqual(command, A_CNXN)
        self.assertEqual(magic, A_CNXN ^ 0xFFFFFFFF)
        self.assertTrue(sent[24:24 + length].startswith(b"host::"))
        self.assertNotIn(struct.pack("<I", A_AUTH), sent, "不得回复 AUTH，避免在手机上弹出授权")

    def test_ignores_non_adb_service(self):
        self.write_state({})
        status, data = self.discover(self.http.port)
        self.assertEqual(status, 200, data)
        self.assertEqual(data["devices"], [])

    def test_ignores_silent_service_within_time_budget(self):
        self.write_state({})
        start = time.monotonic()
        status, data = self.discover(self.silent.port)
        self.assertEqual(status, 200, data)
        self.assertEqual(data["devices"], [])
        self.assertLess(time.monotonic() - start, 3.0)

    def test_closed_port_finds_nothing(self):
        self.write_state({})
        status, data = self.discover(1)
        self.assertEqual(status, 200, data)
        self.assertEqual(data["devices"], [])

    def test_marks_connected_device_with_model(self):
        address = f"127.0.0.1:{self.adb.port}"
        self.write_state({address: "device"})
        status, data = self.discover(self.adb.port)
        self.assertEqual(status, 200, data)
        [device] = data["devices"]
        self.assertEqual(device["serial"], address)
        self.assertEqual(device["state"], "device")
        self.assertEqual(device["model"], "M")

    def test_includes_mdns_connect_services_only(self):
        self.write_state({}, mdns=[
            "adb-ABC123-xYz\t_adb-tls-connect._tcp\t192.168.3.77:41235",
            "adb-ABC123-xYz\t_adb-tls-pairing._tcp\t192.168.3.77:37000",
            "adb-DEF456\t_adb._tcp\t192.168.3.20:5555",
        ])
        status, data = self.discover(1)
        self.assertEqual(status, 200, data)
        self.assertEqual(data["devices"], [
            {"address": "192.168.3.20:5555", "source": ["mdns"], "name": "adb-DEF456", "serial": "adb-DEF456._adb._tcp",
             "state": "", "model": ""},
            {"address": "192.168.3.77:41235", "source": ["mdns"], "name": "adb-ABC123-xYz",
             "serial": "adb-ABC123-xYz._adb-tls-connect._tcp",
             "state": "", "model": ""},
        ])

    def test_merges_scan_and_mdns_for_same_address(self):
        address = f"127.0.0.1:{self.adb.port}"
        self.write_state({}, mdns=[f"adb-SAME\t_adb._tcp\t{address}"])
        status, data = self.discover(self.adb.port)
        self.assertEqual(status, 200, data)
        self.assertEqual(data["devices"], [
            {"address": address, "source": ["scan", "mdns"], "name": "adb-SAME", "serial": "adb-SAME._adb._tcp",
             "state": "", "model": ""},
        ])

    def test_matches_device_connected_by_mdns_serial(self):
        serial = "adb-ABC123-xYz._adb-tls-connect._tcp"
        self.write_state({serial: "device"}, mdns=[
            "adb-ABC123-xYz\t_adb-tls-connect._tcp.\t192.168.3.77:41235",
        ])
        status, data = self.discover(1)
        self.assertEqual(status, 200, data)
        self.assertEqual(data["devices"], [
            {"address": "192.168.3.77:41235", "source": ["mdns"], "name": "adb-ABC123-xYz", "serial": serial,
             "state": "device", "model": "M"},
        ])

    def test_does_not_connect_anything(self):
        self.write_state({}, reachable=[f"127.0.0.1:{self.adb.port}"])
        self.discover(self.adb.port)
        self.assertFalse(any(c.startswith(("connect", "kill-server", "disconnect")) for c in self.adb_log()),
                         self.adb_log())

    def test_rejects_too_large_range(self):
        status, data = self.post("/api/discover", {"subnet": "10.0.0.0/16"})
        self.assertEqual(status, 400, data)
        self.assertIn("扫描范围", data["error"])

    def test_rejects_public_range(self):
        status, data = self.post("/api/discover", {"subnet": "8.8.8.0/24"})
        self.assertEqual(status, 400, data)
        self.assertIn("局域网", data["error"])

    def test_rejects_invalid_port(self):
        for port in (0, 70000, "abc"):
            status, data = self.post("/api/discover", {"subnet": "127.0.0.1/32", "port": port})
            self.assertEqual(status, 400, (port, data))

    def test_auto_subnet_uses_private_slash_24(self):
        # fake_bin 中的 ifconfig 和 ip 各提供一张额外的私网网卡，以及应被排除的回环、链路本地和 CGNAT 地址。
        self.write_state({})
        status, data = self.post("/api/discover", {"port": 1}, timeout=60)
        self.assertEqual(status, 200, data)
        self.assertLessEqual(len(data["subnets"]), 4)
        self.assertIn("10.20.30.0/24", data["subnets"])
        self.assertIn("172.20.5.0/24", data["subnets"])
        for subnet in data["subnets"]:
            network = ipaddress.ip_network(subnet)
            self.assertEqual(network.prefixlen, 24, subnet)
            self.assertTrue(network.is_private and not network.is_loopback and not network.is_link_local, subnet)
        self.assertEqual(data["port"], 1)


if __name__ == "__main__":
    unittest.main()
