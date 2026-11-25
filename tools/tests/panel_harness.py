"""启动一个使用假 adb/scrcpy 的控制台实例，供端到端测试使用。"""

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
PANEL = HERE.parent / "scrcpy-control-panel.py"
FAKE_BIN = HERE / "fake_bin"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class PanelTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.state = Path(cls.tmp.name) / "adb.json"
        cls.write_state({})
        cls.port = free_port()
        env = dict(os.environ)
        env["PATH"] = f"{FAKE_BIN}:/usr/bin:/bin"
        env["FAKE_ADB_STATE"] = str(cls.state)
        env["HOME"] = cls.tmp.name
        cls.proc = subprocess.Popen(
            [sys.executable, str(PANEL), "--port", str(cls.port), "--no-open",
             "--download-dir", str(Path(cls.tmp.name) / "dl")],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        for _ in range(50):
            try:
                cls.get("/api/status")
                return
            except OSError:
                time.sleep(0.1)
        cls.proc.kill()
        raise RuntimeError("panel did not start")

    @classmethod
    def tearDownClass(cls):
        try:
            cls.post("/api/stop", {})
        except Exception:
            pass
        cls.proc.terminate()
        cls.proc.wait(5)
        cls.tmp.cleanup()

    @classmethod
    def write_state(cls, connected, reachable=(), local=("emulator-5554",), mdns=()):
        cls.state.write_text(json.dumps({
            "server": True, "connected": dict(connected), "reachable": list(reachable),
            "local": list(local), "mdns": list(mdns), "log": [],
        }))

    @classmethod
    def read_state(cls):
        return json.loads(cls.state.read_text())

    @classmethod
    def get(cls, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{cls.port}{path}", timeout=30) as r:
            return json.loads(r.read())

    @classmethod
    def post(cls, path, payload, timeout=60):
        req = urllib.request.Request(
            f"http://127.0.0.1:{cls.port}{path}", data=json.dumps(payload).encode(),
            headers={"content-type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def adb_log(self):
        return self.read_state()["log"]

    @staticmethod
    def serials(data):
        return {d["serial"]: d["state"] for d in data.get("devices", [])}
