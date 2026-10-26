import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from server import Handler, LocalServer, serve


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.server = LocalServer(("127.0.0.1", 0), Handler, Path(self.temp.name) / "db")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        payload = response.read()
        result = response.status, response.getheader("Content-Type"), payload
        connection.close()
        return result

    def test_dashboard_state_and_loopback_api(self):
        status, content_type, html = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", content_type)
        self.assertIn("MÔ PHỎNG NỘI BỘ".encode(), html)
        status, _, body = self.request("GET", "/api/state")
        self.assertEqual(status, 200)
        state = json.loads(body)
        self.assertTrue(state["simulation_only"])
        self.assertTrue(state["csrf_token"])

    def test_write_requires_csrf_and_same_origin(self):
        state = json.loads(self.request("GET", "/api/state")[2])
        body = json.dumps({"request_id": "08ce9740-b26b-43f7-8c3e-272a842aab6a", "account_count": 1}).encode()
        status, _, _ = self.request("POST", "/api/runs", body, {"Content-Type":"application/json"})
        self.assertEqual(status, 403)
        status, _, _ = self.request("POST", "/api/runs", body, {
            "Content-Type":"application/json", "X-Lab-CSRF":state["csrf_token"],
            "Origin":f"http://127.0.0.1:{self.port}",
        })
        self.assertEqual(status, 201)
        status, _, _ = self.request("POST", "/api/runs", body, {
            "Content-Type":"application/json", "X-Lab-CSRF":state["csrf_token"],
            "Origin":"http://evil.invalid",
        })
        self.assertEqual(status, 403)

    def test_host_header_rebinding_is_rejected_and_external_bind_is_blocked(self):
        status, _, _ = self.request("GET", "/api/health", headers={"Host":"evil.invalid"})
        self.assertEqual(status, 403)
        with self.assertRaises(ValueError):
            serve(host="0.0.0.0", port=8765, data_dir=self.temp.name)


if __name__ == "__main__":
    unittest.main()
