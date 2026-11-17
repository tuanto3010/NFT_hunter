"""Safe, single-request BNB RPC diagnostics tests."""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError, URLError

import httpx

from app.database import Database
from app.dashboard.api import create_app
from app.dashboard.rpc_diagnostics import BnbRpcDiagnostics, masked_endpoint_label


class FakeResponse:
    status = 200
    headers = {"Content-Type": "application/json"}

    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.payload


class RpcDiagnosticsTests(unittest.TestCase):
    def diagnostic(self, outcome, endpoint="https://user:secret@rpc.example.net/path/api-key?token=hidden"):
        calls = []

        def opener(request, timeout):
            calls.append((request, timeout))
            if isinstance(outcome, BaseException):
                raise outcome
            return FakeResponse(outcome)

        return BnbRpcDiagnostics(endpoint, opener=opener), calls

    def test_success_sends_one_eth_chain_id_request(self):
        diagnostic, calls = self.diagnostic({"jsonrpc": "2.0", "id": 1, "result": "0x38"})
        result = diagnostic.test_once()
        self.assertEqual(result["state"], "CONNECTED")
        self.assertEqual(result["chain_id"], 56)
        self.assertEqual(len(calls), 1)
        request = calls[0][0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(json.loads(request.data)["method"], "eth_chainId")
        self.assertNotIn("secret", json.dumps(result))
        self.assertNotIn("api-key", json.dumps(result))
        self.assertNotIn("token=hidden", json.dumps(result))

    def test_socket_permission_failure_is_network_blocked_and_sanitized(self):
        denied = PermissionError(13, "socket forbidden")
        denied.winerror = 10013
        diagnostic, calls = self.diagnostic(URLError(denied))
        result = diagnostic.test_once()
        self.assertEqual(result["state"], "NETWORK_BLOCKED")
        self.assertIn("WinError 10013", result["error_details"])
        self.assertEqual(len(calls), 1)
        self.assertNotIn("rpc.example.net/path", json.dumps(result))

    def test_timeout_state(self):
        diagnostic, calls = self.diagnostic(URLError(TimeoutError("private endpoint details")))
        result = diagnostic.test_once()
        self.assertEqual(result["state"], "TIMEOUT")
        self.assertIn("timed out", result["error_details"])
        self.assertEqual(len(calls), 1)
        self.assertNotIn("private endpoint", json.dumps(result))

    def test_http_error_state_and_status(self):
        error = HTTPError("https://secret.invalid/path", 403, "forbidden", {}, None)
        diagnostic, calls = self.diagnostic(error)
        result = diagnostic.test_once()
        self.assertEqual(result["state"], "HTTP_ERROR")
        self.assertEqual(result["http_status"], 403)
        self.assertEqual(len(calls), 1)
        error.close()

    def test_missing_configuration_makes_no_request(self):
        calls = []
        diagnostic = BnbRpcDiagnostics("", opener=lambda *args, **kwargs: calls.append(args))
        result = diagnostic.test_once()
        self.assertEqual(result["state"], "NOT_CONFIGURED")
        self.assertEqual(calls, [])

    def test_endpoint_masking_redacts_userinfo_path_query_and_sensitive_host_labels(self):
        label = masked_endpoint_label(
            "https://abcdef0123456789abcdef0123456789.rpc.example.net/credential-path?api_key=topsecret"
        )
        self.assertEqual(label, "https://[hidden].rpc.example.net/[path hidden]?[parameters hidden]")
        self.assertNotIn("abcdef012345", label)
        self.assertNotIn("topsecret", label)

    def test_dashboard_post_performs_one_request_and_never_runs_scan(self):
        calls = []

        def opener(request, timeout):
            calls.append(request)
            return FakeResponse({"jsonrpc": "2.0", "id": 1, "result": "0x38"})

        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "dashboard.sqlite3")
            diagnostics = BnbRpcDiagnostics("https://public.rpc.example/path?key=private-value", opener=opener)
            app = create_app(database=db, rpc_diagnostics=diagnostics)

            async def send(method, path):
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                    return await client.request(method, path)

            get_result = asyncio.run(send("GET", "/api/rpc-diagnostics"))
            self.assertEqual(get_result.status_code, 200)
            self.assertEqual(calls, [])
            tested = asyncio.run(send("POST", "/api/rpc-diagnostics/test"))
            self.assertEqual(tested.status_code, 200)
            self.assertEqual(tested.json()["state"], "CONNECTED")
            self.assertEqual(len(calls), 1)
            self.assertEqual(json.loads(calls[0].data)["method"], "eth_chainId")
            self.assertNotIn("private-value", tested.text)
            connection = db.connect()
            try:
                self.assertEqual(connection.execute("SELECT count(*) FROM scan_history").fetchone()[0], 0)
            finally:
                connection.close()

    def test_dashboard_rejects_concurrent_manual_diagnostic_request(self):
        calls = []
        diagnostics = BnbRpcDiagnostics(
            "https://rpc.example.net", opener=lambda *args, **kwargs: calls.append(args)
        )
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(database=Database(Path(directory) / "dashboard.sqlite3"),
                             rpc_diagnostics=diagnostics)

            async def send():
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                    return await client.post("/api/rpc-diagnostics/test")

            self.assertTrue(app.state.bnb_rpc_diagnostic_lock.acquire(blocking=False))
            try:
                response = asyncio.run(send())
            finally:
                app.state.bnb_rpc_diagnostic_lock.release()
            self.assertEqual(response.status_code, 409)
            self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
