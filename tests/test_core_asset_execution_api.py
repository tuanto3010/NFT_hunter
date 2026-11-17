"""Safety-boundary tests; these never create a signature or call a Solana RPC."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from app.database import Database
from app.dashboard.api import create_app


class CoreAssetExecutionApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp.name) / "core-execution.sqlite3")
        self.app = create_app(database=self.database)

    def tearDown(self):
        for item in self.app.state.core_asset_preview_signers.values():
            item["timer"].cancel()
            item["secret"][:] = b"\x00" * len(item["secret"])
        self.temp.cleanup()

    def post(self, path, body):
        async def send():
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                return await client.post(path, json=body)
        return asyncio.run(send())

    def test_approval_requires_explicit_user_approval_and_phantom_mainnet(self):
        response = self.post("/api/solana/self-created-core-asset-approve", {
            "approved": False, "provider_id": "phantom", "chain_id": "solana-mainnet-beta",
            "wallet_address": "11111111111111111111111111111111", "asset_address": "asset", "preview_id": "missing",
        })
        self.assertEqual(response.status_code, 400)

    def test_submit_without_bound_approved_preview_never_calls_rpc_or_sender(self):
        with patch("urllib.request.urlopen") as send_rpc:
            response = self.post("/api/solana/self-created-core-asset-submit", {
                "wallet_address": "11111111111111111111111111111111", "preview_id": "missing",
                "signed_transaction_base64": "AA==",
            })
        self.assertEqual(response.status_code, 404)
        send_rpc.assert_not_called()

    def test_sqlite_lifecycle_table_has_no_secret_or_transaction_blob_columns(self):
        connection = self.database.connect()
        try:
            columns = {row["name"] for row in connection.execute(
                "PRAGMA table_info(core_asset_transactions)").fetchall()}
        finally:
            connection.close()
        self.assertTrue({"preview_id", "asset_address", "wallet_address", "signature", "status"} <= columns)
        self.assertFalse(any("secret" in name or "transaction_base64" in name for name in columns))

    def test_latest_transaction_read_api_returns_saved_attempt_without_network_or_secret_fields(self):
        self.database.save_core_asset_transaction({
            "preview_id": "saved-preview", "asset_address": "asset-public-address",
            "wallet_address": "wallet-public-address", "network": "solana-mainnet-beta",
            "metadata_uri": "https://example.org/metadata.json", "transaction_digest": "d" * 64,
            "signature": "public-signature", "status": "SUBMISSION_UNKNOWN",
            "estimated_total_lamports": "2967960", "created_at": "2026-11-15T00:00:00+00:00",
            "submitted_at": "2026-11-15T00:00:00+00:00", "confirmed_at": None,
            "failure_category": "RuntimeError",
        })
        async def send():
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                return await client.get("/api/solana/self-created-core-asset-latest")
        response = asyncio.run(send())
        self.assertEqual(response.json()["status"], "SUBMISSION_UNKNOWN")
        self.assertEqual(response.json()["signature"], "public-signature")
        self.assertNotIn("secret", response.text.lower())

    def test_missing_rpc_signature_is_not_reported_as_pending_or_marked_failed(self):
        self.database.save_core_asset_transaction({
            "preview_id": "unresolved-preview", "asset_address": "asset-public-address",
            "wallet_address": "wallet-public-address", "network": "solana-mainnet-beta",
            "metadata_uri": "https://example.org/metadata.json", "transaction_digest": "e" * 64,
            "signature": "public-signature", "status": "SUBMISSION_UNKNOWN",
            "estimated_total_lamports": "2967960", "created_at": "2026-11-15T00:00:00+00:00",
            "submitted_at": "2026-11-15T00:00:00+00:00", "confirmed_at": None,
            "failure_category": "RuntimeError",
        })
        def rpc_call(method, params):
            if method == "getSignatureStatuses": return {"value": [None]}
            if method == "getTransaction": return None
            raise AssertionError(method)
        self.app.state.solana_scanner.rpc = SimpleNamespace(
            endpoint="https://rpc.test.invalid", call=rpc_call)

        async def send():
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                return await client.get("/api/solana/self-created-core-asset-status/unresolved-preview")
        with patch("app.dashboard.api.subprocess.run", return_value=SimpleNamespace(
                returncode=1, stdout='{"ok":false,"reason":"ASSET_ACCOUNT_NOT_FOUND"}', stderr="")) as verify:
            response = asyncio.run(send())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "SUBMISSION_UNKNOWN")
        self.assertEqual(response.json()["confirmation_status"], "rpc_history_unavailable")
        self.assertEqual(response.json()["asset_check_status"], "ACCOUNT_NOT_FOUND")
        self.assertEqual(self.database.get_core_asset_transaction("unresolved-preview")["status"],
                         "SUBMISSION_UNKNOWN")
        self.assertEqual(json.loads(verify.call_args.kwargs["input"])["operation"], "verify_asset")
        connection = self.database.connect()
        try:
            check = connection.execute("SELECT * FROM core_asset_reconciliation_checks").fetchone()
        finally:
            connection.close()
        self.assertEqual(check["signature_status"], "RPC_HISTORY_UNAVAILABLE")
        self.assertEqual(check["asset_account_status"], "ACCOUNT_NOT_FOUND")

    def test_wrong_program_owner_is_distinguished_from_missing_account(self):
        self.database.save_core_asset_transaction({
            "preview_id": "wrong-owner-preview", "asset_address": "asset-public-address",
            "wallet_address": "wallet-public-address", "network": "solana-mainnet-beta",
            "metadata_uri": "https://example.org/metadata.json", "transaction_digest": "g" * 64,
            "signature": "public-signature-3", "status": "SUBMISSION_UNKNOWN",
            "estimated_total_lamports": "2967960", "created_at": "2026-11-15T00:00:00+00:00",
            "submitted_at": "2026-11-15T00:00:00+00:00", "confirmed_at": None,
            "failure_category": "RuntimeError",
        })
        self.app.state.solana_scanner.rpc = SimpleNamespace(
            endpoint="https://rpc.test.invalid",
            call=lambda method, params: {"value": [None]} if method == "getSignatureStatuses" else None)
        async def send():
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                return await client.get("/api/solana/self-created-core-asset-status/wrong-owner-preview")
        with patch("app.dashboard.api.subprocess.run", return_value=SimpleNamespace(
                returncode=1, stdout='{"ok":false,"reason":"WRONG_PROGRAM_OWNER"}', stderr="")):
            response = asyncio.run(send())
        self.assertEqual(response.json()["asset_check_status"], "WRONG_PROGRAM_OWNER")
        self.assertEqual(self.database.get_core_asset_transaction("wrong-owner-preview")["status"],
                         "SUBMISSION_UNKNOWN")

    def test_history_transaction_failure_is_conclusive_and_audited(self):
        self.database.save_core_asset_transaction({
            "preview_id": "history-failed-preview", "asset_address": "asset-public-address",
            "wallet_address": "wallet-public-address", "network": "solana-mainnet-beta",
            "metadata_uri": "https://example.org/metadata.json", "transaction_digest": "h" * 64,
            "signature": "public-signature-4", "status": "SUBMISSION_UNKNOWN",
            "estimated_total_lamports": "2967960", "created_at": "2026-11-15T00:00:00+00:00",
            "submitted_at": "2026-11-15T00:00:00+00:00", "confirmed_at": None,
            "failure_category": "RuntimeError",
        })
        methods = []
        def rpc_call(method, params):
            methods.append(method)
            if method == "getSignatureStatuses": return {"value": [None]}
            if method == "getTransaction": return {"slot": 123, "meta": {"err": {"InstructionError": [0, "Custom"]}}}
            raise AssertionError(method)
        self.app.state.solana_scanner.rpc = SimpleNamespace(endpoint="https://rpc.test.invalid", call=rpc_call)
        async def send():
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                return await client.get("/api/solana/self-created-core-asset-status/history-failed-preview")
        response = asyncio.run(send())
        self.assertEqual(methods, ["getSignatureStatuses", "getTransaction"])
        self.assertEqual(response.json()["status"], "FAILED")
        self.assertEqual(self.database.get_core_asset_transaction("history-failed-preview")["failure_category"],
                         "ONCHAIN_ERROR")
        connection = self.database.connect()
        try:
            check = connection.execute("SELECT * FROM core_asset_reconciliation_checks").fetchone()
        finally:
            connection.close()
        self.assertEqual(check["signature_status"], "ONCHAIN_FAILURE")

    def test_rpc_transport_failure_marks_asset_unchecked_and_preserves_unknown(self):
        self.database.save_core_asset_transaction({
            "preview_id": "rpc-failed-preview", "asset_address": "asset-public-address",
            "wallet_address": "wallet-public-address", "network": "solana-mainnet-beta",
            "metadata_uri": "https://example.org/metadata.json", "transaction_digest": "i" * 64,
            "signature": "public-signature-5", "status": "SUBMISSION_UNKNOWN",
            "estimated_total_lamports": "2967960", "created_at": "2026-11-15T00:00:00+00:00",
            "submitted_at": "2026-11-15T00:00:00+00:00", "confirmed_at": None,
            "failure_category": "RuntimeError",
        })
        self.app.state.solana_scanner.rpc = SimpleNamespace(
            endpoint="https://rpc.test.invalid",
            call=lambda method, params: (_ for _ in ()).throw(PermissionError("blocked")))
        async def send():
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                return await client.get("/api/solana/self-created-core-asset-status/rpc-failed-preview")
        response = asyncio.run(send())
        result = response.json()
        self.assertEqual(result["status"], "SUBMISSION_UNKNOWN")
        self.assertEqual(result["confirmation_status"], "rpc_unavailable")
        self.assertEqual(result["asset_check_status"], "RPC_UNAVAILABLE")
        self.assertEqual(result["diagnostic_error_category"], "PermissionError")
        self.assertNotIn("blocked", response.text)
        self.assertEqual(self.database.get_core_asset_transaction("rpc-failed-preview")["status"],
                         "SUBMISSION_UNKNOWN")

    def test_saved_asset_can_be_verified_when_signature_history_is_unavailable(self):
        self.database.save_core_asset_transaction({
            "preview_id": "asset-found-preview", "asset_address": "asset-public-address",
            "wallet_address": "wallet-public-address", "network": "solana-mainnet-beta",
            "metadata_uri": "https://example.org/metadata.json", "transaction_digest": "f" * 64,
            "signature": "public-signature-2", "status": "SUBMISSION_UNKNOWN",
            "estimated_total_lamports": "2967960", "created_at": "2026-11-15T00:00:00+00:00",
            "submitted_at": "2026-11-15T00:00:00+00:00", "confirmed_at": None,
            "failure_category": "RuntimeError",
        })
        def rpc_call(method, params):
            if method == "getSignatureStatuses": return {"value": [None]}
            if method == "getTransaction": return None
            raise AssertionError(method)
        self.app.state.solana_scanner.rpc = SimpleNamespace(
            endpoint="https://rpc.test.invalid", call=rpc_call)
        verified = {"ok": True, "owner": "wallet-public-address",
                    "metadataUri": "https://example.org/metadata.json"}
        async def send():
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                return await client.get("/api/solana/self-created-core-asset-status/asset-found-preview")
        with patch("app.dashboard.api.subprocess.run", return_value=SimpleNamespace(
                returncode=0, stdout=json.dumps(verified), stderr="")):
            response = asyncio.run(send())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ASSET_VERIFIED_SIGNATURE_UNAVAILABLE")
        self.assertEqual(response.json()["confirmation_status"], "asset_verified_signature_not_found")
        stored = self.database.get_core_asset_transaction("asset-found-preview")
        self.assertEqual(stored["status"], "ASSET_VERIFIED_SIGNATURE_UNAVAILABLE")
        self.assertIsNone(stored["confirmed_at"])

    def test_approval_is_bound_to_preview_wallet_and_asset_and_runs_one_preflight(self):
        wallet = "11111111111111111111111111111111"
        asset = "SysvarRent111111111111111111111111111111111"
        timer = SimpleNamespace(cancel=lambda: None, start=lambda: None)
        preview = {"preview_id": "p-1", "wallet_address": wallet, "asset_address": asset,
                   "metadata_uri": "https://example.org/meta.json", "network": "solana-mainnet-beta",
                   "transaction": {"unsigned_transaction_base64": "AA==", "last_valid_block_height": 99,
                                   "message_sha256": "a" * 64},
                   "costs": {"maximum_lamports": "5000000"}, "secret": bytearray(64),
                   "expires_at": 9999999999, "timer": timer, "approved": False,
                   "submission_started": False, "submitted": False}
        self.app.state.core_asset_preview_signers[asset] = preview
        with patch("app.dashboard.api.subprocess.run", return_value=SimpleNamespace(
                returncode=0, stdout='{"ok":true,"transactionBase64":"AA==","costs":{}}', stderr="")):
            response = self.post("/api/solana/self-created-core-asset-approve", {
                "approved": True, "provider_id": "phantom", "chain_id": "solana-mainnet-beta",
                "wallet_address": wallet, "asset_address": asset, "preview_id": "p-1",
            })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "PHANTOM_SIGNATURE_REQUIRED")
        self.assertTrue(preview["approved"])
        self.assertTrue(preview["submission_started"])

