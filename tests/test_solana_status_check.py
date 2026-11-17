"""Read-only, single-request Solana unknown-transaction status helper tests."""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from app.solana.status_check import load_unknown_signatures, read_signature_statuses


class FakeResponse:
    status = 200

    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit):
        return json.dumps(self.payload).encode()


class SolanaStatusCheckTests(unittest.TestCase):
    def test_one_rpc_call_checks_multiple_signatures_without_returning_them(self):
        response = FakeResponse({"jsonrpc": "2.0", "id": 1, "result": {"value": [
            {"confirmationStatus": "finalized", "slot": 123, "err": None}, None,
        ]}})
        opener = Mock(return_value=response)
        result = read_signature_statuses(["public-signature-1", "public-signature-2"],
                                         "https://rpc.example", opener=opener)
        self.assertEqual(opener.call_count, 1)
        request, = opener.call_args.args
        body = json.loads(request.data)
        self.assertEqual(body["method"], "getSignatureStatuses")
        self.assertTrue(body["params"][1]["searchTransactionHistory"])
        self.assertEqual(result["items"][0]["confirmation_status"], "finalized")
        self.assertEqual(result["items"][1]["status"], "RPC_HISTORY_UNAVAILABLE")
        self.assertNotIn("public-signature", json.dumps(result))
        self.assertNotIn("sendTransaction", json.dumps(body))

    def test_network_error_is_sanitized_and_not_retried(self):
        from urllib.error import URLError
        opener = Mock(side_effect=URLError("https://rpc.example/?secret=redacted"))
        result = read_signature_statuses(["sig"], "https://rpc.example", opener=opener)
        self.assertEqual(opener.call_count, 1)
        self.assertEqual(result["status"], "NETWORK_ERROR")
        self.assertNotIn("secret", json.dumps(result))
        self.assertNotIn("rpc.example", json.dumps(result))

    def test_empty_list_and_non_https_endpoint_make_no_request(self):
        opener = Mock()
        self.assertEqual(read_signature_statuses([], "https://rpc.example", opener),
                         {"status": "NO_UNKNOWN_SIGNATURES", "request_count": 0, "items": []})
        self.assertEqual(read_signature_statuses(["sig"], "http://rpc.example", opener)["status"],
                         "BLOCKED_INVALID_RPC_CONFIGURATION")
        opener.assert_not_called()

    def test_unknown_signature_loader_is_read_only_and_filters_resolved_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tx.sqlite3"
            db = sqlite3.connect(path)
            try:
                db.execute("CREATE TABLE core_asset_transactions (preview_id TEXT, signature TEXT, status TEXT)")
                db.executemany("INSERT INTO core_asset_transactions VALUES (?,?,?)", [
                    ("p1", "sig1", "SUBMISSION_UNKNOWN"), ("p2", "sig2", "CONFIRMED"),
                    ("p3", None, "SUBMISSION_UNKNOWN"),
                ])
                db.commit()
            finally:
                db.close()
            self.assertEqual(load_unknown_signatures(path), [("p1", "sig1")])
            db = sqlite3.connect(path)
            try:
                db.execute("INSERT INTO core_asset_transactions VALUES ('p4','sig4','SUBMISSION_UNKNOWN')")
                db.commit()
            finally:
                db.close()
            self.assertEqual(len(load_unknown_signatures(path)), 2)


if __name__ == "__main__":
    unittest.main()
