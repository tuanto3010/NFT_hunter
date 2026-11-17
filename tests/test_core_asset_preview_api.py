import asyncio
import base64
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from app.database import Database
from app.dashboard.api import create_app


class CoreAssetPreviewApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temp.name) / "core-preview.sqlite3")
        self.app = create_app(database=self.database)

    def tearDown(self):
        self.temp.cleanup()

    def request(self, payload):
        async def send():
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                return await client.post("/api/solana/self-created-core-asset-preview", json=payload)
        return asyncio.run(send())

    def payload(self, **changes):
        value = {
            "provider_id": "phantom", "chain_id": "solana-mainnet-beta",
            "wallet_address": "11111111111111111111111111111111",
            "wallet_confirmed": True, "metadata_uri": "",
        }
        value.update(changes)
        return value

    def test_missing_public_metadata_uri_blocks_before_rpc_or_child_process(self):
        result = self.request(self.payload()).json()
        self.assertEqual(result["status"], "BLOCKED_METADATA_URI")
        self.assertIn("Host the supplied metadata", result["reason"])

    def test_unconfirmed_or_wrong_wallet_is_blocked(self):
        result = self.request(self.payload(wallet_confirmed=False)).json()
        self.assertEqual(result["status"], "BLOCKED_WALLET_NOT_CONFIRMED")
        result = self.request(self.payload(provider_id="metamask")).json()
        self.assertEqual(result["status"], "BLOCKED_WALLET_NOT_CONFIRMED")
        result = self.request(self.payload(chain_id="devnet")).json()
        self.assertEqual(result["status"], "BLOCKED_WALLET_NOT_CONFIRMED")

    def test_no_signer_secret_or_wallet_data_is_written_to_sqlite(self):
        response = self.request(self.payload())
        self.assertNotIn("internal_asset_signer_secret_key", response.text)
        connection = self.database.connect()
        try:
            names = {row["name"] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            self.assertNotIn("core_asset_signers", names)
        finally:
            connection.close()

    def test_success_response_keeps_asset_signer_secret_out_of_http_and_sqlite(self):
        asset_address = "11111111111111111111111111111111"
        secret = base64.b64encode(bytes(range(64))).decode("ascii")
        child_result = {
            "status": "PREVIEW_READY", "asset_address": asset_address,
            "wallet_address": "11111111111111111111111111111111",
            "network": "solana-mainnet-beta", "metadata_uri": "https://assets.example.org/meta.json",
            "costs": {"total_lamports": "10"},
            "transaction": {"unsigned_transaction_base64": "AA==", "last_valid_block_height": 99,
                            "message_sha256": "a" * 64},
            "internal_asset_signer_secret_key": secret,
        }
        fake_process = SimpleNamespace(returncode=0, stdout=json.dumps(child_result), stderr="sensitive-error-text")
        with patch("app.dashboard.api.fetch_and_validate_metadata", return_value={"name": "fixture"}), \
             patch("app.dashboard.api.subprocess.run", return_value=fake_process):
            response = self.request(self.payload(metadata_uri="https://assets.example.org/meta.json"))
        self.assertEqual(response.json()["status"], "PREVIEW_READY")
        self.assertNotIn(secret, response.text)
        self.assertNotIn("sensitive-error-text", response.text)
        stored = self.app.state.core_asset_preview_signers[asset_address]
        self.assertEqual(stored["secret"], bytearray(range(64)))
        stored["timer"].cancel()
        connection = self.database.connect()
        try:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0], 0)
        finally:
            connection.close()
