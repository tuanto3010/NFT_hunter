"""Tests for the local dashboard's read-only SQLite API."""

import json
import asyncio
import tempfile
import unittest
import os
import threading
import time
from unittest.mock import patch
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from app.database import Database
from app.dashboard.api import create_app
from app.discovery.project_sources import ProjectObservation
from app.discovery.solana_scanner import b58encode


class DashboardApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.directory.name) / "dashboard.sqlite3")
        connection = self.database.connect()
        connection.close()
        self.app = create_app(database=self.database)

    def tearDown(self):
        self.directory.cleanup()

    def request(self, method, path, **kwargs):
        async def send():
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                return await client.request(method, path, **kwargs)
        return asyncio.run(send())

    def test_empty_dashboard_and_discoveries_are_real_empty_data(self):
        summary = self.request("GET", "/api/dashboard")
        listings = self.request("GET", "/api/discoveries")
        self.assertEqual(summary.status_code, 200)
        self.assertEqual(summary.json()["total_discovered"], 0)
        self.assertEqual(summary.json()["free_mint_opportunities"], 0)
        self.assertFalse(summary.json()["is_demo_data"])
        self.assertEqual(listings.json()["items"], [])
        self.assertEqual(listings.json()["total"], 0)
        self.assertEqual(self.request("POST", "/api/discoveries").status_code, 405)
        page = self.request("GET", "/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("No discoveries are stored", page.text)

    def test_shared_budget_endpoint_reports_legacy_unknown_transactions_without_exposing_identity(self):
        connection = self.database.connect()
        connection.execute(
            """INSERT INTO core_asset_transactions
               (preview_id,asset_address,wallet_address,network,metadata_uri,transaction_digest,
                signature,status,estimated_total_lamports,created_at)
               VALUES ('private-preview-id','asset','wallet-private','solana-mainnet-beta','https://metadata.example',
                       'digest','public-signature','SUBMISSION_UNKNOWN','2000000','2026-11-15T00:00:00+00:00')"""
        )
        connection.commit()
        connection.close()
        response = self.request("GET", "/api/budget")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["cap_usd"], "5.00")
        self.assertEqual(response.json()["state"], "BLOCKED_UNKNOWN_LIABILITY")
        self.assertEqual(response.json()["unknown_liability_count"], 1)
        self.assertIsNone(response.json()["available_usd"])
        self.assertNotIn("private-preview-id", response.text)

    def test_campaign_job_runs_existing_cycle_persists_counts_and_campaign_results(self):
        from app.discovery.project_sources import ProjectObservation
        from datetime import datetime, timezone
        lead = ProjectObservation(
            chain_id=56, contract_address="0x" + "a" * 40,
            source_url="https://thecolony.ai/post/test", observed_at=datetime.now(timezone.utc).isoformat(),
            project_name="Campaign test", risk_assessment="high",
            details={"campaign": {"announcement_url": "https://thecolony.ai/post/test",
                                   "source_verification": "UNVERIFIED_RSS_LEAD",
                                   "verification_status": "DEFERRED_MISSING_EVIDENCE"}},
        )
        self.database.save_observation(lead)
        cycle = {"source": "test-rss", "status": "ok", "items_read": 1, "requests": 1,
                 "leads_found": 1, "new_leads": 1, "verified_previewable": 0,
                 "verification_attempts": 0, "deferred": 1, "rejected_paid": 0,
                 "rejected_sold_out": 0, "duplicates": 0, "source_errors": [],
                 "source_metrics": [{"source": "test-rss", "items_received": 1,
                                      "leads_found": 1, "new_leads_saved": 1, "status": "ok"}],
                 "leads": []}
        with patch("app.dashboard.api.run_campaign_discovery_cycle", return_value=cycle), \
             patch("app.dashboard.api.verify_new_campaign_leads") as verification:
            started = self.request("POST", "/api/campaign-cycle")
            self.assertEqual(started.status_code, 200, started.text)
            self.assertTrue(started.json()["job_id"])
            self.assertIn(started.json()["status"], {"running", "completed"})
            verification.assert_called_once()
            job_id = started.json()["job_id"]
            for _ in range(50):
                job = self.request("GET", f"/api/campaign-cycle/jobs/{job_id}").json()
                if job["status"] != "running":
                    break
                time.sleep(.02)
            self.assertEqual(job["status"], "completed")
            self.assertEqual(job["counts"]["new_leads_saved"], 1)
            self.assertEqual(job["counts"]["verified_candidates"], 0)
            self.assertEqual(job["source_metrics"][0]["source"], "test-rss")
        campaigns = self.request("GET", "/api/campaigns?chain=bnb&status=DEFERRED_MISSING_EVIDENCE")
        self.assertEqual(campaigns.status_code, 200)
        self.assertEqual(campaigns.json()["count"], 1)
        self.assertEqual(campaigns.json()["items"][0]["project_name"], "Campaign test")

    def test_addressless_calendar_lead_is_visible_as_unverified_campaign(self):
        candidate = {
            "chain_identifier": "solana-mainnet-beta", "project_name": "Calendar Lead",
            "source_name": "howrare-solana-drops", "source_url": "https://howrare.is/api",
            "official_website_claim": "https://project.example", "announcement_url_claim": None,
            "launchpad_url": None, "expected_mint_at": "2026-12-01T09:00:00+00:00",
            "mint_date_claim": "2026-12-01", "mint_time_claim": "09:00 UTC",
            "contract_or_mint_address": None, "address_kind": "NOT_PROVIDED_BY_SOURCE",
            "mint_price_claim": "0 SOL", "mint_price_status": "CLAIMED_FREE_UNVERIFIED",
            "eligibility_status": "UNKNOWN", "verification_status": "DEFERRED_MISSING_EVIDENCE",
            "risk_assessment": "HIGH", "source_confidence": "THIRD_PARTY_CALENDAR_UNVERIFIED",
            "evidence": {"calendar": True},
        }
        self.database.save_campaign_source_lead(candidate)
        response = self.request("GET", "/api/campaigns?chain=solana")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 1)
        item = response.json()["items"][0]
        self.assertEqual(item["project_name"], "Calendar Lead")
        self.assertIsNone(item["contract_address"])
        self.assertEqual(item["price_status"], "CLAIMED_FREE_UNVERIFIED")
        self.assertEqual(item["risk_status"], "HIGH")
        self.assertEqual(item["compatible_wallet"], "Not verified")

    def test_concurrent_campaign_start_is_rejected(self):
        entered = threading.Event()
        release = threading.Event()
        cycle = {"source": "test-rss", "status": "ok", "items_read": 0, "requests": 1,
                 "leads_found": 0, "new_leads": 0, "verified_previewable": 0,
                 "verification_attempts": 0, "deferred": 0, "rejected_paid": 0,
                 "rejected_sold_out": 0, "duplicates": 0, "source_errors": [], "leads": []}
        def block_cycle(_db):
            entered.set()
            release.wait(3)
            return cycle
        with patch("app.dashboard.api.run_campaign_discovery_cycle", side_effect=block_cycle):
            first = self.request("POST", "/api/campaign-cycle")
            self.assertEqual(first.status_code, 200, first.text)
            self.assertTrue(entered.wait(1))
            second = self.request("POST", "/api/campaign-cycle")
            self.assertEqual(second.status_code, 409)
            release.set()
            for _ in range(50):
                job = self.request("GET", f"/api/campaign-cycle/jobs/{first.json()['job_id']}").json()
                if job["status"] != "running":
                    break
                time.sleep(.02)
            self.assertEqual(job["status"], "completed")

    def test_failed_worker_start_marks_campaign_job_failed(self):
        fake_worker = SimpleNamespace(start=unittest.mock.Mock(side_effect=RuntimeError("thread limit")))
        with patch("app.dashboard.api.threading.Thread", return_value=fake_worker):
            response = self.request("POST", "/api/campaign-cycle")
        self.assertEqual(response.status_code, 503)
        job = self.request("GET", "/api/campaign-cycle/status").json()
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["failure_reason"], "Campaign worker could not be started.")

    def test_manual_campaign_lead_persists_as_unverified_and_deduplicates(self):
        body = {
            "project_name": "Meow Cards",
            "chain_id": 56,
            "contract_address": "0xb8164afefc53910d7a74cece15a60a9e0c86089e",
            "official_url": "https://meow-cards.pages.dev",
            "announcement_url": "https://thecolony.ai/post/a9deca11-7fd5-5161-9995-ce390b290425",
            "mint_url": "https://meow-cards.pages.dev",
        }
        first = self.request("POST", "/api/campaign-leads/manual", json=body)
        second = self.request("POST", "/api/campaign-leads/manual", json=body)
        self.assertEqual(first.status_code, 200)
        self.assertTrue(first.json()["inserted"])
        self.assertFalse(second.json()["inserted"])
        self.assertEqual(first.json()["mint_price_status"], "UNKNOWN")
        item = self.request("GET", "/api/discoveries/1").json()
        self.assertEqual(item["project_name"], "Meow Cards")
        self.assertEqual(item["mint_price"], None)
        self.assertEqual(item["verification_status"], "unknown")
        self.assertEqual(item["eligibility"], "unknown")
        self.assertEqual(item["risk_assessment"], "high")

    def test_manual_campaign_lead_rejects_invalid_provenance_and_network(self):
        base = {"project_name": "Lead", "chain_id": 56,
                "contract_address": "0xb8164afefc53910d7a74cece15a60a9e0c86089e",
                "official_url": "https://site.example", "announcement_url": "https://site.example/post"}
        self.assertEqual(self.request("POST", "/api/campaign-leads/manual",
                                      json={**base, "official_url": "http://site.example"}).status_code, 400)
        self.assertEqual(self.request("POST", "/api/campaign-leads/manual",
                                      json={**base, "chain_id": 4663}).status_code, 400)

    def test_generic_evm_campaign_check_persists_block_evidence_per_wallet_without_lowering_risk(self):
        body = {"project_name": "Independent BNB lead", "chain_id": 56,
                "contract_address": "0x" + "a" * 40, "official_url": "https://project.example",
                "announcement_url": "https://project.example/announcement",
                "mint_url": "https://project.example/mint"}
        created = self.request("POST", "/api/campaign-leads/manual", json=body).json()
        first_wallet, second_wallet = "0x" + "1" * 40, "0x" + "2" * 40

        class Verifier:
            def __init__(self, chain_id, rpc_url):
                self.chain_id = chain_id
            def verify(self, **kwargs):
                return {"status": "BLOCKED_HIGH_RISK", "chain_id": self.chain_id,
                        "contract": kwargs["contract_address"], "selected_wallet": kwargs["wallet_address"],
                        "block_number": 123, "risk_assessment_before": kwargs["risk_assessment"],
                        "unsigned_transaction": None}

        request = {"chain_id": 56, "contract_address": body["contract_address"],
                   "provider_id": "metamask", "official_source_url": body["official_url"],
                   "source_confirmed": True, "source_confirmation": "Reviewed official project source",
                   "payment_evidence": "Reviewed source for other token payments",
                   "wallet_address": first_wallet, "mint_method": "mint()", "mint_arguments": [],
                   "price_check": {}, "eligibility_check": {}, "availability_check": {}}
        with patch.dict(os.environ, {"BNB_RPC_URL": "https://rpc.invalid"}), \
             patch("app.dashboard.api.EvmCampaignVerifier", Verifier):
            first = self.request("POST", "/api/discoveries/%d/evm-campaign-check" % created["id"], json=request)
            second = self.request("POST", "/api/discoveries/%d/evm-campaign-check" % created["id"],
                                  json={**request, "wallet_address": second_wallet})
        self.assertEqual(first.json()["status"], "BLOCKED_HIGH_RISK")
        self.assertEqual(second.status_code, 200)
        connection = self.database.connect()
        try:
            row = connection.execute("SELECT risk_assessment, details_json FROM observations WHERE id=?",
                                     (created["id"],)).fetchone()
        finally:
            connection.close()
        self.assertEqual(row["risk_assessment"], "high")
        saved = json.loads(row["details_json"])["campaign"]
        self.assertEqual(len(saved["verification_history"]), 2)
        self.assertIn(first_wallet.lower(), saved["wallet_checks"])
        self.assertIn(second_wallet.lower(), saved["wallet_checks"])
        first_evidence = self.database.list_wallet_verification_evidence(
            "bnb", body["contract_address"], first_wallet, provider_id="metamask")
        second_evidence = self.database.list_wallet_verification_evidence(
            "bnb", body["contract_address"], second_wallet, provider_id="metamask")
        self.assertEqual(len(first_evidence), 1)
        self.assertEqual(len(second_evidence), 1)
        self.assertEqual(first_evidence[0]["verification_status"], "BLOCKED_HIGH_RISK")
        self.assertEqual(first_evidence[0]["wallet_address"], first_wallet)
        self.assertEqual(first_evidence[0]["block_number"], 123)

    def test_opensea_wallet_check_persists_scoped_evidence_without_promoting_price(self):
        address = "0x" + "1" * 40
        wallet = "0x" + "2" * 40
        self.database.save_campaign_source_lead({
            "chain_identifier": "1", "project_name": "Sections by Grant Yun",
            "source_name": "opensea-drops-api",
            "source_url": "https://opensea.io/collection/sections-by-grant-yun",
            "contract_or_mint_address": address, "address_kind": "NFT_CONTRACT",
            "mint_price_claim": "0 wei", "mint_price_status": "CLAIMED_FREE_UNVERIFIED",
            "risk_assessment": "HIGH", "source_confidence": "THIRD_PARTY_LAUNCHPAD_UNVERIFIED",
            "evidence": {"collection_slug": "sections-by-grant-yun"},
        })

        class OpenSeaClient:
            def __init__(self, *args, **kwargs): self.requests_made = 0
            def get_details(self, slug):
                self.requests_made += 1
                stage = {"uuid": "stage-1", "stage_type": "signed_presale", "price": "0",
                         "price_currency_address": "0x" + "0" * 40,
                         "start_time": "2026-11-18T17:20:00Z", "end_time": "2026-11-18T17:39:00Z"}
                return 200, {"contract_address": address, "chain": "ethereum", "is_minting": False,
                             "active_stage": None, "next_stage": stage}
            def get_eligibility(self, slug):
                self.requests_made += 1
                return 200, {"stages": [{"uuid": "stage-1", "eligible": True, "mints_remaining": 1}]}

        with patch.dict(os.environ, {"ETHEREUM_RPC_URL": ""}), \
             patch("app.dashboard.api.configured_credentials", return_value=("fake-api-key", "fake-wallet-jwt", wallet)), \
             patch("app.dashboard.api.OpenSeaDropClient", OpenSeaClient):
            response = self.request("POST", "/api/campaign-leads/1/opensea-wallet-check", json={
                "provider_id": "metamask", "wallet_address": wallet, "chain_id": "1"})
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["status"], "UPCOMING")
        self.assertTrue(result["wallet_eligible"])
        self.assertEqual(result["mint_price_status"], "API_CLAIMED_ZERO_UNVERIFIED")
        self.assertNotIn("fake-wallet-jwt", response.text)
        rows = self.database.list_wallet_verification_evidence("ethereum", address, wallet,
                                                               provider_id="metamask")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["wallet_eligible"], 1)
        self.assertIsNone(rows[0]["mint_price_wei"])
        saved_evidence = json.loads(rows[0]["evidence_json"])
        self.assertIn("expires_at", saved_evidence)
        self.assertEqual(saved_evidence["stage_status"], "UPCOMING_OR_INACTIVE")
        campaign = self.request("GET", "/api/campaigns?chain=ethereum").json()["items"][0]
        self.assertEqual(campaign["wallet_verifications"][0]["wallet_address"], "0x2222" + chr(0x2026) + "2222", campaign)
        self.assertEqual(campaign["price_status"], "CLAIMED_FREE_UNVERIFIED")

    def test_meow_campaign_check_is_selected_wallet_bound_and_persists_read_only_evidence(self):
        from types import SimpleNamespace

        body = {"project_name": "Meow Cards", "chain_id": 56,
                "contract_address": "0xb8164afefc53910d7a74cece15a60a9e0c86089e",
                "official_url": "https://meow-cards.pages.dev",
                "announcement_url": "https://thecolony.ai/post/a9deca11-7fd5-5161-9995-ce390b290425",
                "mint_url": "https://meow-cards.pages.dev"}
        self.request("POST", "/api/campaign-leads/manual", json=body)
        address = "0x" + "3e" * 20

        class Wallet:
            def get_status(self): return SimpleNamespace(connected=True)
            def get_supported_networks(self): return (SimpleNamespace(network_id="56", family="evm"),)
            def get_addresses(self): return (SimpleNamespace(network_id="56", address=address),)
            def get_developer_mode(self): return SimpleNamespace(active=True)
            def get_agent_id_handle(self): return "sampleagent"

        class Verifier:
            def __init__(self): self.args = None
            def verify(self, wallet_address, handle, cached):
                self.args = (wallet_address, handle, cached)
                return {"status": "READ_ONLY_VERIFIED", "checked_at": datetime.now(timezone.utc).isoformat(),
                        "block_number": 123456, "chain_id": 56,
                        "contract": body["contract_address"], "selected_wallet": wallet_address,
                        "agent_token_id": "123", "agent_eligible": True, "mint_price_wei": "0", "mint_price_bnb": "0",
                        "availability": "SIMULATION_PASS", "simulation": "PASS",
                        "contract_source_verified": True, "nft_standard": "ERC-721", "gas_cost_bnb": "0.0000155404",
                        "source_cache": {"status": "verified", "code_hash": "x", "abi": []}}

        verifier = Verifier()
        self.app.state.binance_wallet = Wallet()
        from app.wallet.transaction_simulator import WalletAwareMintPreviewService
        self.app.state.wallet_preview_service = WalletAwareMintPreviewService(self.app.state.binance_wallet)
        self.app.state.meow_campaign_verifier = verifier
        response = self.request("POST", "/api/discoveries/1/meow-campaign-check", json={
            "provider_id": "binance_agentic", "wallet_address": address, "chain_id": "56"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(verifier.args[:2], (address, "sampleagent"))
        saved = self.request("GET", "/api/discoveries/1").json()
        campaign = saved["evidence"]["details"]["campaign"]
        self.assertEqual(campaign["latest_live_check"]["simulation"], "PASS")
        self.assertEqual(saved["risk_assessment"], "high")
        self.assertEqual(saved["mint_price"], None)
        self.assertEqual(saved["eligibility"], "unknown")
        self.assertEqual(saved["nft_standard"], "ERC-721")

        preview = self.request("POST", "/api/discoveries/1/mint-preview", json={
            "provider_id": "binance_agentic", "wallet_address": address, "chain_id": "56",
            "function_name": "mint", "arguments": [1, 123]})
        self.assertEqual(preview.status_code, 400)
        self.assertIn("low-risk", preview.json()["detail"])

        mismatched = self.request("POST", "/api/discoveries/1/mint-preview", json={
            "provider_id": "binance_agentic", "wallet_address": "0x" + "4f" * 20, "chain_id": "56",
            "function_name": "mint", "arguments": [1, 123]})
        self.assertEqual(mismatched.status_code, 400)

    def test_meow_campaign_check_rejects_wrong_provider_or_wallet(self):
        body = {"project_name": "Meow Cards", "chain_id": 56,
                "contract_address": "0xb8164afefc53910d7a74cece15a60a9e0c86089e",
                "official_url": "https://meow-cards.pages.dev",
                "announcement_url": "https://thecolony.ai/post/a9deca11-7fd5-5161-9995-ce390b290425"}
        self.request("POST", "/api/campaign-leads/manual", json=body)
        wrong = self.request("POST", "/api/discoveries/1/meow-campaign-check", json={
            "provider_id": "metamask", "wallet_address": "0x" + "3e" * 20, "chain_id": "56"})
        self.assertEqual(wrong.status_code, 400)

    def test_solana_watchlist_validates_provenance_and_deduplicates_address(self):
        body = {"project_name": "Sample Solana project", "address": "1" * 32,
                "address_type": "core_candy_machine", "official_website": "https://sample.example/",
                "announcement_url": "https://sample.example/launch", "launchpad_url": "",
                "announcement_date": "2026-11-14T10:00:00Z", "mint_date": "2026-12-01T12:00:00Z",
                "mint_price_claim": "0 SOL", "allowlist_status": "OPEN",
                "source_url": "https://social.example/announcement"}
        first = self.request("POST", "/api/solana/watchlist", json=body)
        second = self.request("POST", "/api/solana/watchlist", json={**body, "project_name": "Different name",
                                                                        "source_url": "https://other.example/report"})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        items = self.request("GET", "/api/solana/watchlist").json()["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["address"], "1" * 32)
        self.assertEqual(items[0]["provenance"], "manual_registration")
        self.assertEqual(len(items[0]["sources"]), 2)
        self.assertEqual(items[0]["announcement_date"], "2026-11-14T10:00:00+00:00")
        self.assertEqual(items[0]["mint_date"], "2026-12-01T12:00:00+00:00")
        self.assertEqual(items[0]["mint_price_claim"], "0 SOL")
        self.assertEqual(items[0]["allowlist_status"], "OPEN")
        self.assertFalse(items[0]["official_source_verified"])
        bad = self.request("POST", "/api/solana/watchlist", json={**body, "address": "not-a-key"})
        self.assertEqual(bad.status_code, 400)
        bad_url = self.request("POST", "/api/solana/watchlist", json={**body, "source_url": "http://unsafe.example"})
        self.assertEqual(bad_url.status_code, 400)

    def test_solana_eligibility_is_bound_to_approved_phantom_wallet(self):
        from types import SimpleNamespace

        class Scanner:
            def inspect_core_candy_machine(self, address):
                return ProjectObservation(
                    chain_id="solana-mainnet-beta", contract_address=address,
                    source_url="https://rpc.example.invalid", observed_at="now", project_name="Sample",
                    risk_assessment="medium", details={
                        "discovery_status": "DISCOVERED", "price_status": "PRICE_VERIFIED_FREE",
                        "mint_price": "0", "mint_status": "active",
                        "price_evidence": {"verified": True, "method": "on_chain_guard",
                                            "amount_lamports": 0, "spl_payment_mint": None},
                        "machine": {"guard_config_status": "VERIFIED_EMPTY", "mint_authority": "1" * 32},
                        "network_fee": {"amount": None}, "priority_fee": {"amount": None},
                        "rent_or_account_cost": {"amount": None},
                    })

        class Notifier:
            def __init__(self): self.messages = []
            def send_message(self, text, key):
                self.messages.append((text, key))
                return SimpleNamespace(sent=True)

        body = {"project_name": "Sample", "address": "1" * 32,
                "address_type": "core_candy_machine", "official_website": "https://sample.example/",
                "source_url": "https://social.example/post"}
        self.request("POST", "/api/solana/watchlist", json=body)
        notifier = Notifier()
        self.app = create_app(database=self.database, solana_scanner=Scanner(), solana_notifier=notifier,
                              notification_mode="LIVE", live_notifications_enabled=True)
        phantom_key = b58encode(bytes([2]) * 32)
        payload = {"provider_id": "phantom", "wallet_address": phantom_key,
                   "chain_id": "solana-mainnet-beta"}
        blocked = self.request("POST", "/api/solana/watchlist/" + "1" * 32 + "/eligibility", json=payload)
        self.assertEqual(blocked.status_code, 200, blocked.text)
        self.assertEqual(blocked.json()["status"], "ELIGIBILITY_UNKNOWN")
        self.request("POST", "/api/solana/watchlist/" + "1" * 32 + "/review",
                     json={"review_status": "approved", "official_source_verified": True})
        wrong_provider = self.request("POST", "/api/solana/watchlist/" + "1" * 32 + "/eligibility",
                                      json={**payload, "provider_id": "metamask"})
        self.assertEqual(wrong_provider.status_code, 400)
        verified = self.request("POST", "/api/solana/watchlist/" + "1" * 32 + "/eligibility", json=payload)
        self.assertEqual(verified.json()["status"], "ELIGIBLE")
        self.assertEqual(verified.json()["evidence"]["wallet"], phantom_key)
        self.assertEqual(verified.json()["telegram"], "sent")
        self.assertEqual(len(notifier.messages), 1)

    def test_source_health_api_returns_sanitized_status(self):
        self.database.record_source_health("solana-mainnet-beta", "magiceden-launchpad", "unavailable",
                                           "2026-11-14T00:00:00+00:00", requests=3,
                                           error_type="HTTPError")
        response = self.request("GET", "/api/source-health", params={"chain": "solana"})
        self.assertEqual(response.status_code, 200)
        item = response.json()["items"][0]
        self.assertEqual(item["source_name"], "magiceden-launchpad")
        self.assertEqual(item["status"], "unavailable")
        self.assertEqual(item["requests"], 3)
        self.assertNotIn("token", response.text.lower())

    def test_summary_filters_and_details_use_database_records(self):
        free = ProjectObservation(
            chain_id=56, contract_address="0x" + "a" * 40,
            source_url="https://rpc.example.invalid/v2/APIKEY?token=private", observed_at="2026-02-03T04:05:06+00:00",
            project_name="Observed BNB project", details={
                "mint_price": "0", "eligibility": "whitelist", "nft_standard": "ERC-721",
                "verification_status": "unknown", "mint_price_verified": True,
                "metadata": {"name": "Observed BNB project"},
                "OPENAI_API_KEY": "must-not-be-returned",
            }, nft_standard="ERC-721", mint_price="0", native_gas_token="BNB",
            eligibility="whitelist", verification_status="unknown", risk_assessment="high",
        )
        paid = ProjectObservation(
            chain_id=1, contract_address="0x" + "b" * 40,
            source_url="https://eth-rpc.example.invalid", observed_at="2026-02-03T04:06:06+00:00",
            project_name="Observed Ethereum project", nft_standard="ERC-721", mint_price="0.05",
            native_gas_token="ETH", eligibility="public", verification_status="verified",
            verified=True, risk_assessment="needs_review",
        )
        self.database.save_observation(free)
        self.database.save_observation(paid)

        summary = self.request("GET", "/api/dashboard").json()
        self.assertEqual(summary["total_discovered"], 2)
        self.assertEqual(summary["free_mint_opportunities"], 1)
        self.assertEqual(summary["whitelist_opportunities"], 1)
        self.assertEqual(summary["unverified_opportunities"], 1)

        bnb = self.request("GET", "/api/discoveries", params={"chain": "bnb"}).json()
        self.assertEqual(bnb["total"], 1)
        self.assertEqual(bnb["items"][0]["network"], "BNB Smart Chain")
        self.assertEqual(bnb["items"][0]["mint_price"], "0")
        self.assertEqual(self.request("GET", "/api/discoveries", params={"chain": "solana"}).json()["total"], 0)
        self.assertEqual(self.request("GET", "/api/discoveries", params={"chain": "unknown"}).status_code, 400)

        detail = self.request("GET", "/api/discoveries/1")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["evidence"]["source_url"], "https://rpc.example.invalid")
        self.assertNotIn("APIKEY", detail.text)
        self.assertNotIn("private", detail.text)
        self.assertNotIn("must-not-be-returned", detail.text)
        self.assertEqual(self.request("GET", "/api/discoveries/999").status_code, 404)

    def test_unverified_zero_price_claim_does_not_count_as_free(self):
        self.database.save_observation(ProjectObservation(
            chain_id="solana-mainnet-beta", contract_address="1" * 32,
            source_url="https://example.invalid/source", observed_at="2026-11-14T00:00:00+00:00",
            project_name="Unverified price claim", mint_price="0", native_gas_token="SOL",
            details={"mint_price": "0", "mint_price_verified": False,
                     "price_claim_status": "ZERO_PRICE_UNVERIFIED", "eligibility": "unknown"},
        ))
        summary = self.request("GET", "/api/dashboard").json()
        self.assertEqual(summary["free_mint_opportunities"], 0)

    def test_sol_launchpad_evidence_and_unknown_eligibility_are_displayable(self):
        self.database.save_observation(ProjectObservation(
            chain_id="solana-mainnet-beta", contract_address="1" * 32,
            source_url="https://api-mainnet.magiceden.dev/v2/launchpad/collections?offset=0&limit=500",
            observed_at="2026-11-14T00:00:00+00:00", project_name="Upcoming Solana listing",
            nft_standard="Solana launchpad listing (standard unverified)", native_gas_token="SOL",
            details={"mint_price": None, "eligibility": {"status": "UNKNOWN", "wallet": None},
                     "mint_status": "upcoming", "launch_at": "2026-11-15T00:00:00+00:00",
                     "price_claim": "0", "price_claim_status": "ZERO_PRICE_UNVERIFIED",
                     "network_fee": {"status": "UNKNOWN", "amount": None, "token": "SOL"},
                     "priority_fee": {"status": "UNKNOWN", "amount": None, "token": "SOL"},
                     "rent_or_account_cost": {"status": "UNKNOWN", "amount": None, "token": "SOL"},
                     "manual_review_required": True, "official_source_url": None,
                     "evidence": {"marketplace_source": "https://docs.magiceden.io/reference/get_launchpad-collections"}},
        ))
        listing = self.request("GET", "/api/discoveries", params={"chain": "solana"}).json()["items"][0]
        self.assertEqual(listing["mint_price"], None)
        self.assertEqual(listing["eligibility"], "UNKNOWN")
        detail = self.request("GET", f"/api/discoveries/{listing['id']}").json()
        evidence = detail["evidence"]["details"]
        self.assertEqual(evidence["launch_at"], "2026-11-15T00:00:00+00:00")
        self.assertEqual(evidence["price_claim_status"], "ZERO_PRICE_UNVERIFIED")
        self.assertTrue(evidence["manual_review_required"])
        self.assertEqual(evidence["network_fee"]["status"], "UNKNOWN")

    def test_sol_status_filters_keep_upcoming_announcement_price_and_free_separate(self):
        self.database.save_observation(ProjectObservation(
            chain_id="solana-mainnet-beta", contract_address="1" * 32,
            source_url="https://api-mainnet.magiceden.dev/v2/launchpad/collections",
            observed_at="2026-11-14T00:00:00+00:00", project_name="Future listing",
            native_gas_token="SOL", risk_assessment="high",
            details={"discovery_status": "DISCOVERED", "opportunity_status": "ANNOUNCED",
                     "mint_status": "upcoming", "launch_at": "2026-12-01T12:00:00+00:00",
                     "announcement_date": "2026-11-13T12:00:00+00:00",
                     "announcement_status": "VERIFIED", "official_source_verified": True,
                     "official_source_url": "https://project.example/mint",
                     "price_status": "PRICE_UNKNOWN", "mint_price_status": "UNKNOWN",
                     "address_kind": "core_candy_machine", "allowlist_status": "UNKNOWN"},
        ))
        for status in ("UPCOMING", "ANNOUNCED", "PRICE_UNKNOWN"):
            response = self.request("GET", "/api/discoveries", params={"status": status})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["total"], 1, status)
        self.assertEqual(self.request("GET", "/api/discoveries", params={"status": "VERIFIED_FREE"}).json()["total"], 0)
        self.assertEqual(self.request("GET", "/api/discoveries", params={"status": "NOT_A_STATUS"}).status_code, 400)
        self.assertEqual(self.request("GET", "/api/discoveries", params={"chain": "ethereum", "status": "UPCOMING"}).json()["total"], 0)

    def test_opportunity_filters_require_future_schedule_and_verified_price_evidence(self):
        now = datetime.now(timezone.utc)
        future = (now + timedelta(days=2)).isoformat()
        past = (now - timedelta(days=2)).isoformat()
        fixtures = [
            (1, "Verified free", future, "0", {
                "mint_status": "upcoming", "mint_date": future, "mint_price_verified": True,
                "mint_price_status": "PRICE_VERIFIED_FREE", "price_status": "PRICE_VERIFIED_FREE",
                "price_evidence": {"verified": True, "amount_lamports": "0", "spl_payment_mint": None},
                "address_kind": "core_candy_machine",
            }),
            (2, "Claimed price", future, None, {
                "mint_status": "upcoming", "mint_date": future, "mint_price_status": "UNKNOWN",
                "price_status": "PRICE_UNKNOWN", "price_claim_status": "ZERO_PRICE_UNVERIFIED",
                "address_kind": "core_candy_machine",
            }),
            (3, "Expired schedule", past, None, {
                "mint_status": "upcoming", "mint_date": past, "mint_price_status": "UNKNOWN",
                "price_status": "PRICE_UNKNOWN", "address_kind": "core_candy_machine",
            }),
            (4, "Historical NFT", future, None, {
                "mint_status": "upcoming", "mint_date": future, "mint_price_status": "UNKNOWN",
                "price_status": "PRICE_UNKNOWN", "address_kind": "nft_mint",
                "address_validation_status": "REJECTED_HISTORICAL_NFT_MINT",
            }),
            (5, "Verified paid", future, "0.2", {
                "mint_status": "upcoming", "mint_date": future, "mint_price_status": "PRICE_VERIFIED_PAID",
                "price_status": "PRICE_VERIFIED_PAID", "address_kind": "core_candy_machine",
            }),
        ]
        for seed, name, mint_date, price, details in fixtures:
            self.database.save_observation(ProjectObservation(
                chain_id="solana-mainnet-beta", contract_address=b58encode(bytes([seed]) * 32),
                source_url="https://source.example/drop", observed_at=now.isoformat(),
                project_name=name, native_gas_token="SOL", mint_price=price,
                details=details,
            ))

        upcoming = self.request("GET", "/api/discoveries", params={"upcoming": "true"}).json()
        self.assertEqual(upcoming["total"], 3)
        names = {item["project_name"] for item in upcoming["items"]}
        self.assertEqual(names, {"Verified free", "Claimed price", "Verified paid"})
        free = self.request("GET", "/api/discoveries", params={"price_status": "VERIFIED_FREE"}).json()
        self.assertEqual([item["project_name"] for item in free["items"]], ["Verified free"])
        claimed = self.request("GET", "/api/discoveries", params={"price_status": "CLAIMED"}).json()
        self.assertEqual([item["project_name"] for item in claimed["items"]], ["Claimed price"])
        paid = self.request("GET", "/api/discoveries", params={"price_status": "VERIFIED_PAID"}).json()
        self.assertEqual([item["project_name"] for item in paid["items"]], ["Verified paid"])
        self.assertEqual(self.request("GET", "/api/discoveries", params={"status": "UPCOMING"}).json()["total"], 3)
        self.assertEqual(self.request("GET", "/api/discoveries", params={"price_status": "NOT_A_FILTER"}).status_code, 400)

    def test_summary_reports_recent_records_and_only_strictly_verified_opportunities(self):
        now = datetime.now(timezone.utc).isoformat()
        self.database.save_observation(ProjectObservation(
            chain_id=56, contract_address="0x" + "c" * 40, source_url="https://source.example/",
            observed_at=now, project_name="Recent but unknown", mint_price="0", native_gas_token="BNB",
            verified=False, verification_status="unknown", risk_assessment="high",
            details={"mint_price_verified": False, "mint_price_status": "UNKNOWN"},
        ))
        summary = self.request("GET", "/api/dashboard").json()
        self.assertEqual(summary["new_discoveries_24h"], 1)
        self.assertEqual(summary["verified_opportunities"], 0)
        self.assertEqual(summary["free_mint_opportunities"], 0)

    def test_first_seen_timestamp_survives_solana_rechecks(self):
        address = b58encode(bytes([8]) * 32)
        first = ProjectObservation(
            chain_id="solana-mainnet-beta", contract_address=address,
            source_url="https://source.example/", observed_at="2026-11-01T00:00:00+00:00",
            project_name="Rechecked candidate",
        )
        self.assertTrue(self.database.save_observation(first))
        connection = self.database.connect()
        try:
            stored_first_seen = connection.execute(
                "SELECT first_seen_at FROM observations WHERE contract_address=?", (address,)
            ).fetchone()["first_seen_at"]
        finally:
            connection.close()
        updated = ProjectObservation(
            chain_id="solana-mainnet-beta", contract_address=address,
            source_url="https://source.example/", observed_at="2026-11-14T12:00:00+00:00",
            project_name="Rechecked candidate",
        )
        self.assertFalse(self.database.save_observation(updated))
        connection = self.database.connect()
        try:
            row = connection.execute(
                "SELECT first_seen_at, observed_at FROM observations WHERE contract_address=?", (address,)
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(row["first_seen_at"], stored_first_seen)
        self.assertEqual(row["observed_at"], updated.observed_at)
        listing = self.request("GET", "/api/discoveries").json()["items"][0]
        self.assertEqual(listing["discovery_time"], stored_first_seen)
        self.assertEqual(listing["source_last_checked_at"], updated.observed_at)

    def test_future_mint_claim_needs_manual_official_source_review(self):
        from app.scheduler import ScanScheduler

        address = "1" * 32
        self.database.register_solana_project(
            "Manual launch candidate", "https://project.example", "https://project.example/announce", None,
            address, "launchpad_candidate", "https://calendar.example/listing",
            announcement_date="2026-11-13T00:00:00+00:00", mint_date="2026-12-01T00:00:00+00:00",
            mint_price_claim="0 SOL", allowlist_status="OPEN")
        observation = ProjectObservation(
            chain_id="solana-mainnet-beta", contract_address=address, source_url="https://rpc.example",
            observed_at="2026-11-14T00:00:00+00:00", project_name="Manual launch candidate",
            details={"discovery_status": "DISCOVERED", "price_status": "PRICE_UNKNOWN",
                     "mint_status": "unknown", "mint_price_status": "UNKNOWN"})
        scheduler = ScanScheduler([], self.database)
        unreviewed = scheduler._attach_watchlist_evidence([observation])[0]
        self.assertEqual(unreviewed.details["announcement_status"], "UNVERIFIED")
        self.assertEqual(unreviewed.details["mint_status"], "unknown")
        self.assertEqual(unreviewed.details["mint_price_status"], "UNKNOWN")
        self.assertEqual(unreviewed.details["allowlist_status"], "UNKNOWN")
        self.database.update_solana_watchlist_review(address, "approved", True, "Reviewed official announcement")
        reviewed = scheduler._attach_watchlist_evidence([observation])[0]
        self.assertEqual(reviewed.details["announcement_status"], "VERIFIED")
        self.assertEqual(reviewed.details["mint_status"], "upcoming")
        self.assertEqual(reviewed.details["mint_schedule"]["status"], "ANNOUNCED")
        self.assertEqual(reviewed.details["mint_price_status"], "UNKNOWN")
        self.assertEqual(reviewed.details["allowlist_status"], "UNKNOWN")

    def test_status_and_next_scan_are_read_from_scheduler_state(self):
        scan_id = self.database.begin_scan()
        self.database.finish_scan(scan_id, "complete", 0)
        future = (datetime.now(timezone.utc) + timedelta(minutes=14)).isoformat()
        self.database.update_scheduler_state(
            "scheduled", datetime.now(timezone.utc).isoformat(), 900, next_scan_at=future,
        )
        summary = self.request("GET", "/api/dashboard").json()
        self.assertEqual(summary["status"], "scheduled")
        self.assertEqual(summary["next_scheduled_scan"], future)
        self.assertIsNotNone(summary["last_scan_time"])

    def test_binance_wallet_endpoint_returns_only_read_only_wallet_data(self):
        class FakeWallet:
            def get_status(self):
                return SimpleNamespace(connected=True)

            def get_supported_networks(self):
                return (SimpleNamespace(network_id="56", name="BNB Smart Chain", family="evm", native_token="BNB"),)

            def get_addresses(self):
                return (SimpleNamespace(network_id="56", address="0x" + "a" * 40),)

            def get_balances(self):
                return (SimpleNamespace(network_id="56", symbol="BNB", amount="1.25"),
                        SimpleNamespace(network_id="56", symbol="USDT", amount="5"))

        app = create_app(database=self.database, binance_wallet=FakeWallet())
        self.app = app
        response = self.request("GET", "/api/wallets/binance")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["provider_id"], "binance_agentic")
        self.assertEqual(response.json()["addresses"], [{"chain_id": "56", "address": "0x" + "a" * 40}])
        self.assertEqual(response.json()["balances"], [{"chain_id": "56", "symbol": "BNB", "amount": "1.25"}])
        self.assertNotIn("USDT", response.text)

    def test_binance_wallet_endpoint_sanitizes_cli_failures(self):
        class BrokenWallet:
            def get_status(self):
                raise RuntimeError("private session details must not leak")

        self.app = create_app(database=self.database, binance_wallet=BrokenWallet())
        response = self.request("GET", "/api/wallets/binance")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "provider_id": "binance_agentic", "status": "unavailable", "addresses": [], "balances": [],
        })
        self.assertNotIn("private session", response.text)

    def test_phantom_balance_endpoint_returns_mainnet_read_only_balance(self):
        class FakeSolanaScanner:
            def get_wallet_balance(self, public_key):
                return {"sol": "1.234", "lamports": 1_234_000_000}

        self.app = create_app(database=self.database, solana_scanner=FakeSolanaScanner())
        response = self.request("GET", "/api/wallets/solana/balance", params={"public_key": "1" * 32})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["provider_id"], "phantom")
        self.assertEqual(response.json()["balance_sol"], "1.234")
        self.assertEqual(self.request("GET", "/api/wallets/solana/balance", params={"public_key": "bad"}).status_code, 422)

    def test_solana_preview_binds_phantom_and_keeps_unknown_costs_blocked(self):
        observation = ProjectObservation(
            chain_id="solana-mainnet-beta", contract_address="1" * 32,
            source_url="https://solana.example.invalid/asset", observed_at="2026-02-03T04:07:06+00:00",
            project_name="Observed Solana NFT", nft_standard="Metaplex Token Metadata", mint_price="0",
            native_gas_token="SOL", details={"metadata": {"name": "Observed Solana NFT"},
                                             "mint_price": "0", "mint_price_verified": True},
        )
        self.database.save_observation(observation)
        body = {"provider_id": "phantom", "wallet_address": "1" * 32, "chain_id": "solana-mainnet-beta"}
        response = self.request("POST", "/api/discoveries/1/solana-preview", json=body)
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(result["status"], "BLOCKED_UNKNOWN_MINT_PRICE")
        self.assertEqual(result["mint_price"]["status"], "UNKNOWN")
        self.assertIsNone(result["mint_price"]["amount"])
        self.assertEqual(result["network_fee"]["status"], "UNKNOWN")
        self.assertEqual(result["rent_or_account_cost"]["status"], "UNKNOWN")
        self.assertEqual(result["wallet_address"], "1" * 32)
        self.assertIsNone(result["total_estimated_cost"]["amount"])
        self.assertEqual(self.request("POST", "/api/discoveries/1/solana-preview", json={**body, "chain_id": "56"}).status_code, 400)
        self.assertEqual(self.request("POST", "/api/discoveries/1/solana-preview", json={**body, "provider_id": "metamask"}).status_code, 400)
        evm = ProjectObservation(chain_id=56, contract_address="0x" + "a" * 40, source_url="", observed_at="now")
        self.database.save_observation(evm)
        self.assertEqual(self.request("POST", "/api/discoveries/2/solana-preview", json=body).status_code, 400)

    def test_wallet_preview_reports_unknown_price_and_rejects_chain_mismatch(self):
        observation = ProjectObservation(
            chain_id=56, contract_address="0x" + "d" * 40,
            source_url="https://explorer.example/contract", observed_at="2026-02-03T04:07:06+00:00",
            nft_standard="ERC-721", mint_price=None, native_gas_token="BNB",
            eligibility="unknown", verification_status="verified", risk_assessment="low",
            verified=True, details={"mint_functions": [{
                "name": "mint", "inputs": [], "stateMutability": "nonpayable",
            }]},
        )
        self.database.save_observation(observation)
        body = {
            "provider_id": "metamask", "wallet_address": "0x" + "a" * 40,
            "chain_id": "56", "function_name": "mint", "arguments": {},
        }
        unknown = self.request("POST", "/api/discoveries/1/mint-preview", json=body)
        self.assertEqual(unknown.status_code, 200)
        self.assertEqual(unknown.json()["status"], "BLOCKED_UNKNOWN_MINT_PRICE")
        self.assertEqual(unknown.json()["mint_price_status"], "UNKNOWN")
        self.assertIsNone(unknown.json()["transaction"])
        self.assertEqual(unknown.json()["provider_id"], "metamask")

        body["chain_id"] = "1"
        mismatch = self.request("POST", "/api/discoveries/1/mint-preview", json=body)
        self.assertEqual(mismatch.status_code, 400)


if __name__ == "__main__":
    unittest.main()
