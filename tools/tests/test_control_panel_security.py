"""本地控制台只接受本机页面发起的请求，拒绝跨站请求和 DNS 重绑定。"""

import http.client
import json
import unittest

from panel_harness import PanelTestCase


class RequestOriginTest(PanelTestCase):
    def request(self, method, path, headers, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            conn.putrequest(method, path, skip_host=True)
            for key, value in headers.items():
                conn.putheader(key, value)
            data = json.dumps(body).encode() if body is not None else b""
            if method == "POST":
                conn.putheader("Content-Length", str(len(data)))
            conn.endheaders(data if method == "POST" else None)
            response = conn.getresponse()
            return response.status, json.loads(response.read() or b"{}")
        finally:
            conn.close()

    def host(self, name="127.0.0.1"):
        return f"{name}:{self.port}"

    def test_rejects_cross_site_post_before_running_anything(self):
        self.write_state({})
        status, data = self.request("POST", "/api/shell", {
            "Host": self.host(), "Origin": "https://evil.example", "Content-Type": "text/plain",
        }, {"command": "id"})
        self.assertEqual(status, 403, data)
        self.assertFalse(data["ok"])
        self.assertEqual(self.adb_log(), [])

    def test_rejects_cross_site_discover(self):
        status, data = self.request("POST", "/api/discover", {
            "Host": self.host(), "Origin": "null",
        }, {"subnet": "127.0.0.1/32", "port": 1})
        self.assertEqual(status, 403, data)

    def test_rejects_dns_rebinding_host(self):
        for method, path, body in (("GET", "/api/status", None), ("POST", "/api/refresh", {})):
            status, data = self.request(method, path, {
                "Host": self.host("evil.example"), "Origin": f"http://{self.host('evil.example')}",
            }, body)
            self.assertEqual(status, 403, (method, path, data))

    def test_rejects_malformed_origin_with_json_403(self):
        for method, path, body in (("GET", "/api/status", None), ("POST", "/api/refresh", {})):
            status, data = self.request(method, path, {"Host": self.host(), "Origin": "http://["}, body)
            self.assertEqual(status, 403, (method, path, data))
            self.assertFalse(data["ok"])

    def test_allows_same_origin_page(self):
        for name in ("127.0.0.1", "localhost"):
            status, data = self.request("POST", "/api/refresh", {
                "Host": self.host(name), "Origin": f"http://{self.host(name)}",
                "Content-Type": "application/json",
            }, {})
            self.assertEqual(status, 200, (name, data))

    def test_allows_local_tools_without_origin(self):
        status, data = self.request("GET", "/api/status", {"Host": self.host()})
        self.assertEqual(status, 200, data)


if __name__ == "__main__":
    unittest.main()
