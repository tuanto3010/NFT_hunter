"""Dependency-light tests for the local read-only Binance wallet route."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from app.dashboard.api import create_app
from app.database import Database


def endpoint(app, path):
    return next(route.endpoint for route in app.routes if getattr(route, "path", None) == path)


class WalletApiReadOnlyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.directory.name) / "wallet.sqlite3")

    def tearDown(self):
        self.directory.cleanup()

    def test_connected_snapshot_keeps_only_native_balance(self):
        class FakeWallet:
            def get_status(self):
                return SimpleNamespace(connected=True)

            def get_supported_networks(self):
                return (
                    SimpleNamespace(network_id="56", name="BNB Smart Chain", family="evm", native_token="BNB"),
                    SimpleNamespace(network_id="CT_501", name="Solana", family="solana", native_token="SOL"),
                )

            def get_addresses(self):
                return (
                    SimpleNamespace(network_id="56", address="0x" + "a" * 40),
                    SimpleNamespace(network_id="CT_501", address="1" * 32),
                )

            def get_balances(self):
                return (
                    SimpleNamespace(network_id="56", symbol="BNB", amount="0.5"),
                    SimpleNamespace(network_id="56", symbol="USDT", amount="99"),
                    SimpleNamespace(network_id="CT_501", symbol="SOL", amount="2"),
                )

        app = create_app(database=self.database, binance_wallet=FakeWallet())
        result = endpoint(app, "/api/wallets/binance")()
        self.assertEqual(result["status"], "connected")
        self.assertEqual(result["addresses"], [
            {"chain_id": "56", "address": "0x" + "a" * 40},
            {"chain_id": "solana-mainnet-beta", "address": "1" * 32},
        ])
        self.assertEqual(result["balances"], [
            {"chain_id": "56", "symbol": "BNB", "amount": "0.5"},
            {"chain_id": "CT_501", "symbol": "SOL", "amount": "2"},
        ])

    def test_cli_errors_are_sanitized(self):
        class BrokenWallet:
            def get_status(self):
                raise RuntimeError("session token must not leak")

        app = create_app(database=self.database, binance_wallet=BrokenWallet())
        result = endpoint(app, "/api/wallets/binance")()
        self.assertEqual(result, {
            "provider_id": "binance_agentic", "status": "unavailable", "addresses": [], "balances": [],
        })
        self.assertNotIn("session token", str(result))


if __name__ == "__main__":
    unittest.main()
