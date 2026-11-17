"""Tests for public calendar/launchpad campaign lead sources."""

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from app.database import Database
from app.discovery.campaign_sources import HowRareDropsSource
from app.discovery.opensea_drops import OpenSeaDropsSource
from app.discovery.campaign_feed import run_campaign_discovery_cycle


class Response:
    def __init__(self, data):
        self.data = data
        self.status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def read(self, limit):
        return self.data[:limit]


class HowRareSourceTests(unittest.TestCase):
    def test_successful_empty_drops_list_is_not_an_unsupported_schema(self):
        payload = {"result": {"api_code": 200, "api_response": "Success", "data": []}}
        source = HowRareDropsSource(
            opener=lambda *_args, **_kwargs: Response(json.dumps(payload).encode()),
            now=lambda: datetime(2026, 11, 15, 12, tzinfo=timezone.utc))
        self.assertEqual(source.fetch(), [])
        self.assertEqual(source.requests_made, 1)
        self.assertEqual(source.last_status, {
            "status": "ok", "items_received": 0, "examined": 0, "upcoming": 0, "expired": 0,
        })

    def test_nonempty_list_remains_an_explicit_unsupported_schema(self):
        payload = {"result": {"api_code": 200, "data": [{"name": "Unexpected"}]}}
        source = HowRareDropsSource(opener=lambda *_args, **_kwargs: Response(json.dumps(payload).encode()))
        with self.assertRaisesRegex(ValueError, "unexpected drops schema"):
            source.fetch()
        self.assertEqual(source.last_status["error_type"], "UnexpectedSchema")
        self.assertEqual(source.last_status["error_reason"],
                         "nonempty_drops_data_is_not_a_date_keyed_object")

    def test_single_bounded_request_keeps_price_and_schedule_as_claims(self):
        payload = {"result": {"api_code": 200, "data": {
            "2026-11-20": [{"date": "2026-11-20", "time": "09:30 UTC", "name": "Future Cards",
                            "price": "0 SOL", "website": "https://future.example",
                            "twitter": "https://x.com/future", "nft_count": 100}],
            "2026-11-10": [{"date": "2026-11-10", "time": "10:00 UTC", "name": "Old Cards",
                            "price": "2 SOL"}],
            "2026-11-25": [{"date": "2026-11-25", "time": "not stated", "name": "Unknown Cards",
                            "price": "TBD", "website": "http://insecure.example"}],
        }}}
        calls = []

        def opener(request, timeout):
            calls.append((request.full_url, timeout))
            return Response(json.dumps(payload).encode())

        source = HowRareDropsSource(opener=opener,
            now=lambda: datetime(2026, 11, 15, 12, tzinfo=timezone.utc))
        leads = source.fetch()
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1], 8)
        self.assertEqual(source.last_status["items_received"], 3)
        self.assertEqual(source.last_status["upcoming"], 2)
        self.assertEqual(source.last_status["expired"], 1)
        free_claim = next(item for item in leads if item["project_name"] == "Future Cards")
        self.assertEqual(free_claim["mint_price_status"], "CLAIMED_FREE_UNVERIFIED")
        self.assertIsNone(free_claim["contract_or_mint_address"])
        self.assertEqual(free_claim["verification_status"], "DEFERRED_MISSING_EVIDENCE")
        self.assertEqual(free_claim["risk_assessment"], "HIGH")
        unknown = next(item for item in leads if item["project_name"] == "Unknown Cards")
        self.assertEqual(unknown["mint_price_status"], "UNKNOWN")
        self.assertIsNone(unknown["official_website_claim"])
        self.assertIsNone(unknown["expected_mint_at"])

    def test_cycle_persists_and_cross_source_deduplicates_calendar_leads(self):
        candidate = {
            "chain_identifier": "solana-mainnet-beta", "project_name": "Future Cards",
            "source_name": "howrare-solana-drops", "source_url": "https://howrare.is/api",
            "official_website_claim": "https://future.example", "announcement_url_claim": None,
            "launchpad_url": None, "expected_mint_at": None, "mint_date_claim": "2026-12-01",
            "mint_time_claim": "09:30 UTC", "contract_or_mint_address": None,
            "address_kind": "NOT_PROVIDED_BY_SOURCE", "mint_price_claim": "0 SOL",
            "mint_price_status": "CLAIMED_FREE_UNVERIFIED", "eligibility_status": "UNKNOWN",
            "verification_status": "DEFERRED_MISSING_EVIDENCE", "risk_assessment": "HIGH",
            "source_confidence": "THIRD_PARTY_CALENDAR_UNVERIFIED", "evidence": {"calendar": True},
        }

        class Colony:
            name = "thecolony-public-rss"
            chain_identifier = "bnb"

            def fetch(self):
                return [], 50

        class HowRare:
            name = "howrare-solana-drops"
            chain_identifier = "solana-mainnet-beta"
            requests_made = 1
            last_status = {"items_received": 1, "expired": 0}

            def fetch(self):
                return [candidate]

        class MagicEden:
            requests_made = 1
            last_status = {"fetched": 1, "solana": 1, "upcoming": 1}

            def fetch(self):
                return [SimpleNamespace(
                    project_name="Future Cards", contract_address="So11111111111111111111111111111111111111112",
                    details={"launch_at": "2026-12-01T00:00:00+00:00", "evidence": {"launchpad": True}},
                )]

        class OpenSea:
            name = "opensea-drops-api"
            chain_identifier = "opensea-multi-chain"
            last_status = {"status": "not_configured", "requests": 0, "items_received": 0,
                           "leads_found": 0, "source_errors": []}

            def fetch(self):
                return []

        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "hunter.sqlite3")
            result = run_campaign_discovery_cycle(
                db, source=Colony(), howrare_source=HowRare(), magic_eden_source=MagicEden(),
                include_additional_sources=True, opensea_source=OpenSea())
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["requests"], 3)
            self.assertEqual(result["leads_found"], 2)
            self.assertEqual(result["new_leads"], 1)
            self.assertEqual(result["duplicates"], 1)
            self.assertEqual(result["deferred"], 2)
            self.assertEqual(len(result["source_metrics"]), 4)
            rows = db.list_campaign_source_leads()
            self.assertEqual(len(rows), 1)
            saved = json.loads(rows[0]["evidence_json"])
            self.assertEqual(len(saved["source_evidence"]), 2)
            self.assertEqual(rows[0]["mint_price_status"], "CLAIMED_FREE_UNVERIFIED")
            self.assertEqual(rows[0]["verification_status"], "DEFERRED_MISSING_EVIDENCE")

    def test_campaign_cycle_persists_opensea_lead_as_unverified_with_provenance(self):
        payload = {"drops": [{
            "chain": "base", "collection_name": "Fixture Drop", "collection_slug": "fixture-drop",
            "contract_address": "0x" + "56" * 20, "drop_type": "seadrop_v1_erc721",
            "is_minting": True,
            "active_stage": {"uuid": "active-stage", "stage_type": "public_sale", "price": "0",
                             "price_currency_address": "0x" + "00" * 20,
                             "start_time": "2026-11-15T10:00:00Z", "end_time": "2026-11-16T10:00:00Z"},
            "next_stage": None, "opensea_url": "https://opensea.io/collection/fixture-drop",
        }], "next": None}

        class Colony:
            name = "test-rss"
            chain_identifier = "bnb"

            def fetch(self):
                return [], 0

        class EmptyHowRare:
            name = "howrare-solana-drops"
            chain_identifier = "solana-mainnet-beta"
            requests_made = 0
            last_status = {"status": "ok", "items_received": 0, "expired": 0}

            def fetch(self):
                return []

        class EmptyMagicEden:
            requests_made = 0
            last_status = {"fetched": 0, "solana": 0}

            def fetch(self):
                return []

        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "hunter.sqlite3")
            opensea = OpenSeaDropsSource(api_key="fixture",
                                          opener=lambda *_a, **_k: Response(json.dumps(payload).encode()))
            result = run_campaign_discovery_cycle(
                db, source=Colony(), howrare_source=EmptyHowRare(),
                magic_eden_source=EmptyMagicEden(), opensea_source=opensea,
                include_additional_sources=True,
            )
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["leads_found"], 1)
            self.assertEqual(result["new_leads"], 1)
            self.assertEqual(result["verified_previewable"], 0)
            row = db.list_campaign_source_leads()[0]
            self.assertEqual(row["chain_identifier"], "8453")
            self.assertEqual(row["contract_or_mint_address"], "0x" + "56" * 20)
            self.assertEqual(row["mint_price_status"], "CLAIMED_FREE_UNVERIFIED")
            self.assertEqual(row["risk_assessment"], "HIGH")
            saved = json.loads(row["evidence_json"])["source_evidence"][0]
            self.assertEqual(saved["source_name"], "opensea-drops-api")
            self.assertFalse(saved["evidence"]["project_controlled_source_verified"])


if __name__ == "__main__":
    unittest.main()
