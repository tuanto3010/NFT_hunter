"""Deterministic qualification for persistent Solana Telegram alerts."""

import unittest
from dataclasses import replace
import tempfile
from pathlib import Path

import httpx

from app.database import Database
from app.discovery.project_sources import ProjectObservation
from app.discovery.chain_adapter import CHAIN_SPECS
from app.notifications.telegram_bot import TelegramConfig, TelegramNotifier
from app.notifications.solana_alerts import (
    format_new_solana_discovery, format_verified_solana_alert,
    is_new_solana_discovery, is_verified_solana_alert, verified_alert_dedupe_key,
)
from app.scheduler import ScanScheduler


WALLET = "So11111111111111111111111111111111111111112"


def verified_candidate():
    return ProjectObservation(
        chain_id="solana-mainnet-beta", contract_address="11111111111111111111111111111111",
        source_url="https://rpc.example.invalid", observed_at="2026-11-14T00:00:00+00:00",
        project_name="Verified sample", risk_assessment="low",
        details={
            "discovery_status": "DISCOVERED", "mint_status": "upcoming",
            "launch_at": "2026-12-01T00:00:00+00:00", "mint_price": "0",
            "price_status": "PRICE_VERIFIED_FREE", "eligibility_status": "ELIGIBLE",
            "verification_provenance": "solana_deterministic_onchain",
            "official_source_verified": True, "official_source_url": "https://project.example/mint",
            "price_evidence": {"verified": True, "method": "on_chain_guard", "amount_lamports": 0,
                               "spl_payment_mint": None},
            "eligibility_wallet": WALLET,
            "eligibility_evidence": {"verified": True, "status": "ELIGIBLE", "wallet": WALLET},
            "network_fee": {"amount": None}, "priority_fee": {"amount": None},
            "rent_or_account_cost": {"amount": None},
        },
    )


class SolanaAlertTests(unittest.TestCase):
    def test_new_discovery_is_explicitly_unverified(self):
        item = replace(verified_candidate(), details={"discovery_status": "DISCOVERED", "launch_at": "2030-01-01"})
        message = format_new_solana_discovery(item)
        self.assertTrue(is_new_solana_discovery(item))
        self.assertIn("NEW_DISCOVERY", message)
        self.assertIn("Mint price: UNKNOWN", message)
        self.assertIn("Schedule: UNVERIFIED", message)
        self.assertIn("Eligibility: UNKNOWN", message)
        self.assertNotIn("free mint", message.lower())

    def test_minted_token_account_does_not_claim_active_mint_terms(self):
        from app.discovery.solana_scanner import SolanaScanner

        class Rpc:
            endpoint = "https://rpc.example.invalid"

            def call(self, method, _params):
                if method == "getAccountInfo":
                    return {"value": {"data": {"parsed": {"info": {"decimals": 0,
                                                                          "mintAuthority": None,
                                                                          "freezeAuthority": None}}}}}
                if method == "getTokenSupply":
                    return {"value": {"amount": "1"}}
                raise AssertionError("unexpected RPC method")

        scanner = SolanaScanner(rpc=Rpc())
        scanner._fetch_token_metadata = lambda _mint: {"mint": WALLET, "name": "fixture NFT"}
        observation = scanner.inspect_mint(WALLET)
        self.assertIsNotNone(observation)
        self.assertEqual(observation.details["price_status"], "PRICE_UNKNOWN")
        self.assertEqual(observation.details["address_kind"], "nft_mint")
        self.assertEqual(observation.details["address_validation_status"], "REJECTED_HISTORICAL_NFT_MINT")
        self.assertFalse(observation.details["mint_infrastructure_verified"])
        self.assertEqual(observation.details["mint_schedule"]["status"], "UNKNOWN")
        self.assertEqual(observation.details["machine"]["guard_config"]["status"], "UNKNOWN")
        self.assertEqual(observation.details["eligibility_evidence"]["status"], "UNKNOWN")

    def test_collection_mint_and_candy_guard_addresses_cannot_trigger_new_mint_alerts(self):
        base = verified_candidate()
        for kind, status in (("collection_mint", "COLLECTION_ONLY_NOT_MINT_INFRASTRUCTURE"),
                             ("core_candy_guard", "GUARD_ONLY_NOT_MINT_INFRASTRUCTURE")):
            item = replace(base, details={**base.details, "address_kind": kind,
                                          "address_validation_status": status})
            self.assertFalse(is_new_solana_discovery(item))

    def test_verified_alert_dedupe_is_wallet_bound(self):
        first = verified_candidate()
        second = replace(first, details={**first.details, "eligibility_wallet": "11111111111111111111111111111111"})
        self.assertNotEqual(verified_alert_dedupe_key(first), verified_alert_dedupe_key(second))
        self.assertTrue(verified_alert_dedupe_key(first).startswith("solana-verified-free:"))

    def test_only_verified_zero_price_and_wallet_eligibility_qualify(self):
        item = verified_candidate()
        self.assertTrue(is_verified_solana_alert(item))
        self.assertIn("verified 0 SOL", format_verified_solana_alert(item))
        self.assertIn("priority fee: unknown SOL", format_verified_solana_alert(item))

    def test_marketplace_zero_claim_never_qualifies(self):
        item = verified_candidate()
        item.details["price_evidence"] = {"verified": False, "method": "marketplace", "amount_lamports": 0}
        self.assertFalse(is_verified_solana_alert(item))

    def test_unknown_or_different_phantom_eligibility_never_qualifies(self):
        item = verified_candidate()
        item.details["eligibility_evidence"]["wallet"] = "11111111111111111111111111111111"
        self.assertFalse(is_verified_solana_alert(item))
        item = verified_candidate()
        item.details["eligibility_status"] = "ELIGIBILITY_UNKNOWN"
        self.assertFalse(is_verified_solana_alert(item))

    def test_paid_mint_and_non_solana_observations_never_qualify(self):
        item = verified_candidate()
        item.details["mint_price"] = "0.01"
        self.assertFalse(is_verified_solana_alert(item))
        item = verified_candidate()
        self.assertFalse(is_verified_solana_alert(replace(item, chain_id=56)))

    def test_scheduler_sends_only_qualified_alert_once_across_duplicate_scans(self):
        requests = []
        client = httpx.Client(transport=httpx.MockTransport(lambda request: (
            requests.append(request) or httpx.Response(200, json={"ok": True, "result": {"message_id": 7}}, request=request)
        )))

        class Adapter:
            spec = CHAIN_SPECS["solana"]
            def scan(self):
                return [verified_candidate()]

        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "hunter.sqlite3"
            database = Database(database_path)
            notifier = TelegramNotifier(TelegramConfig("test-token", "test-chat"), database_path,
                                        client=client, sleeper=lambda _delay: None)
            scheduler = ScanScheduler([Adapter()], database, max_workers=1, solana_notifier=notifier,
                                      notification_mode="LIVE", live_notifications_enabled=True)
            try:
                scheduler.scan_once()
                scheduler.scan_once()
            finally:
                client.close()
        self.assertEqual(len(requests), 2)
        messages = [request.content.decode() for request in requests]
        self.assertTrue(any("Verified Solana NFT opportunity" in message for message in messages))
        self.assertTrue(any("NEW_DISCOVERY" in message for message in messages))
        self.assertNotIn("test-token", "".join(messages))


if __name__ == "__main__":
    unittest.main()
