import json
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

from orchestrator.llm_server import create_handler


class LLMServerTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        def analyzer(payload):
            self.calls.append(payload)
            return {"model": "test", "requested_at": "now", "result": {"summary": "ok"}}
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), create_handler(analyzer))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, path, body=None):
        conn = HTTPConnection("127.0.0.1", self.server.server_port)
        data = None if body is None else json.dumps(body).encode()
        conn.request("POST", path, data, {"Content-Type": "application/json"} if data else {})
        response = conn.getresponse()
        return response.status, json.loads(response.read())

    def test_analyze(self):
        status, result = self.request("/analyze", {"requirements": [], "repository": {}, "commits": [], "changed_files": [], "diff": ""})
        self.assertEqual(status, 200)
        self.assertEqual(result["result"]["summary"], "ok")

    def test_bad_request(self):
        status, result = self.request("/analyze", {"repository": {}})
        self.assertEqual(status, 400)
        self.assertIn("missing", result["error"])

    def test_unknown_route(self):
        status, _ = self.request("/other", {})
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
