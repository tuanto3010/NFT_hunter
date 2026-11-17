import json
from pathlib import Path
import tempfile
import unittest
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from app.discovery.opensea_drops import DROP_LIST_URL, OpenSeaDropsSource, normalize_drop
from app.database import Database


class Response:
    def __init__(self, payload):
        self.raw = json.dumps(payload).encode()
        self.status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def read(self, limit):
        return self.raw[:limit]


def drop(slug="future-collection", price="0", is_minting=False, chain="base"):
    stage = {
        "uuid": "stage-1", "stage_type": "public_sale", "price": price,
        "price_currency_address": "0x0000000000000000000000000000000000000000",
        "start_time": "2026-12-01T12:00:00Z", "end_time": "2026-12-02T12:00:00Z",
        "max_per_wallet": "2",
    }
    return {
        "chain": chain, "collection_name": slug.replace("-", " ").title(),
        "collection_slug": slug, "contract_address": "0x" + "12" * 20,
        "drop_type": "seadrop_v1_erc721", "is_minting": is_minting,
        "active_stage": stage if is_minting else None,
        "next_stage": None if is_minting else stage,
        "opensea_url": "https://opensea.io/collection/" + slug,
    }


class OpenSeaDropsTests(unittest.TestCase):
    def test_missing_key_disables_source_without_network_request(self):
        source = OpenSeaDropsSource(api_key="", opener=lambda *_a, **_k: self.fail("must not request"))
        self.assertEqual(source.fetch(), [])
        self.assertEqual(source.requests_made, 0)
        self.assertEqual(source.last_status["status"], "not_configured")

    def test_hunter_network_aliases_normalize_to_open_sea_drop_chain_slugs(self):
        source = OpenSeaDropsSource(
            api_key="fixture", chains=("bnb", "ethereum", "base", "solana-mainnet-beta", "robinhood-chain")
        )
        self.assertEqual(source.chains, ("bsc", "ethereum", "base", "solana", "robinhood"))

    def test_unknown_open_sea_chain_is_rejected_before_a_request(self):
        with self.assertRaisesRegex(ValueError, "not recognized by OpenSea Drops"):
            OpenSeaDropsSource(api_key="fixture", chains=("not-a-chain",))

    def test_normalizes_documented_drop_and_keeps_price_as_unverified_claim(self):
        lead = normalize_drop(drop(), "upcoming", "2026-11-15T00:00:00+00:00")
        self.assertEqual(lead["chain_identifier"], "8453")
        self.assertEqual(lead["contract_or_mint_address"], "0x" + "12" * 20)
        self.assertEqual(lead["mint_price_status"], "CLAIMED_FREE_UNVERIFIED")
        self.assertEqual(lead["verification_status"], "DEFERRED_MISSING_EVIDENCE")
        self.assertEqual(lead["risk_assessment"], "HIGH")
        self.assertIsNone(lead["official_website_claim"])
        self.assertEqual(lead["evidence"]["stage_id"], "stage-1")

    def test_unknown_and_paid_prices_are_not_classified_as_free(self):
        unknown = drop(price=None)
        unknown["next_stage"]["price"] = None
        paid = drop(price="1500000000000000")
        self.assertEqual(normalize_drop(unknown, "upcoming", "now")["mint_price_status"], "UNKNOWN")
        self.assertEqual(normalize_drop(paid, "upcoming", "now")["mint_price_status"], "CLAIMED_PAID_UNVERIFIED")

    def test_recently_minted_drop_without_any_stage_normalizes_safely(self):
        item = drop("completed-drop")
        item["active_stage"] = None
        item["next_stage"] = None
        lead = normalize_drop(item, "recently_minted", "2026-11-16T00:00:00+00:00")
        self.assertEqual(lead["mint_price_status"], "UNKNOWN")
        self.assertIsNone(lead["evidence"]["price_currency_address_claim"])
        self.assertIsNone(lead["expected_mint_at"])
        self.assertEqual(lead["verification_status"], "DEFERRED_MISSING_EVIDENCE")

    def test_open_sea_robinhood_slug_maps_to_hunter_chain_id(self):
        item = drop("robinhood-collection", chain="robinhood")
        lead = normalize_drop(item, "featured", "2026-11-16T00:00:00+00:00")
        self.assertEqual(lead["chain_identifier"], "4663")

    def test_paginates_within_budget_and_deduplicates_across_types(self):
        pages = {
            "upcoming": [
                {"drops": [drop()], "next": "opaque-next"},
                {"drops": [drop(is_minting=True), {**drop("other", price="1"),
                                                       "contract_address": "0x" + "34" * 20}], "next": None},
            ],
            "featured": [{"drops": [drop()], "next": None}],
            "recently_minted": [{"drops": [], "next": None}],
        }
        calls = []

        def opener(request, timeout):
            query = parse_qs(urlsplit(request.full_url).query)
            kind = query["type"][0]
            calls.append((request, timeout, kind))
            if kind == "upcoming" and query.get("cursor"):
                payload = pages[kind][1]
            else:
                payload = pages[kind][0]
            return Response(payload)

        source = OpenSeaDropsSource(api_key="test-placeholder-api-key", opener=opener,
                                    now=lambda: datetime(2026, 11, 15, tzinfo=timezone.utc))
        leads = source.fetch()
        self.assertEqual(source.requests_made, 4)
        self.assertEqual(len(calls), 4)
        self.assertTrue(all(request.full_url.startswith(DROP_LIST_URL + "?") for request, _, _ in calls))
        self.assertTrue(all(timeout == 8 for _, timeout, _ in calls))
        self.assertEqual(len(leads), 2)
        future = next(row for row in leads if row["project_name"] == "Future Collection")
        self.assertTrue(future["evidence"]["is_minting_claim"])
        self.assertIn("upcoming", future["evidence"]["drop_types"])
        self.assertIn("featured", future["evidence"]["drop_types"])
        self.assertEqual(source.last_status["duplicates"], 2)
        self.assertEqual(source.last_status["by_type"]["upcoming"]["pages"], 2)
        self.assertEqual(source.last_status["by_type"]["upcoming"]["http_statuses"], [200, 200])
        self.assertEqual(source.last_status["by_type"]["featured"]["http_statuses"], [200])

    def test_live_response_shape_from_user_is_parsed_and_persisted_as_unverified_claims(self):
        payload = {"drops": [
            {
                "chain": "ethereum", "collection_name": "Sections by Grant Yun",
                "collection_slug": "sections-by-grant-yun",
                "contract_address": "0xe9d9d14d858cf9cda2d320493726af7fc907309a",
                "drop_type": "seadrop_v1_erc721", "is_minting": False,
                "active_stage": None,
                "next_stage": {
                    "uuid": "section-stage", "stage_type": "signed_presale", "price": "0",
                    "price_currency_address": "0x0000000000000000000000000000000000000000",
                    "start_time": "2026-11-18T17:20:00Z", "end_time": "2026-11-18T17:39:00Z",
                    "max_per_wallet": "2",
                },
                "opensea_url": "https://opensea.io/collection/sections-by-grant-yun",
            },
            {
                "chain": "ethereum", "collection_name": "Nocturnal",
                "collection_slug": "nocturnal-edition-by-derech",
                "contract_address": "0xa99ac8b5053bc2ced4c4c83bb8c6153018f84fc8",
                "drop_type": "seadrop_v1_erc721", "is_minting": True,
                "active_stage": {
                    "uuid": "nocturnal-stage", "stage_type": "public_sale",
                    "price": "50000000000000000",
                    "price_currency_address": "0x0000000000000000000000000000000000000000",
                    "start_time": "2026-11-15T16:00:00Z", "end_time": "2026-11-18T16:00:00Z",
                    "max_per_wallet": "5",
                },
                "next_stage": None,
                "opensea_url": "https://opensea.io/collection/nocturnal-edition-by-derech",
            },
        ], "next": None}
        source = OpenSeaDropsSource(
            api_key="fixture", chains=("bnb", "ethereum", "base", "solana", "robinhood"),
            opener=lambda *_a, **_k: Response(payload),
        )
        leads = source.fetch()
        self.assertEqual(source.last_status["status"], "ok")
        self.assertEqual(source.last_status["leads_found"], 2)
        sections = next(lead for lead in leads if lead["project_name"] == "Sections by Grant Yun")
        self.assertEqual(sections["chain_identifier"], "1")
        self.assertEqual(sections["mint_price_status"], "CLAIMED_FREE_UNVERIFIED")
        self.assertEqual(sections["verification_status"], "DEFERRED_MISSING_EVIDENCE")
        nocturnal = next(lead for lead in leads if lead["project_name"] == "Nocturnal")
        self.assertEqual(nocturnal["mint_price_status"], "CLAIMED_PAID_UNVERIFIED")
        with tempfile.TemporaryDirectory() as tmp:
            database = Database(Path(tmp) / "campaigns.sqlite3")
            self.assertEqual([database.save_campaign_source_lead(lead) for lead in leads], [True, True])
            self.assertEqual(len(database.list_campaign_source_leads()), 2)

    def test_malformed_recently_minted_item_does_not_discard_valid_leads(self):
        good = drop("valid-drop")
        bad = drop("bad-drop")
        bad["contract_address"] = "0x" + "34" * 20
        payload = {"drops": [bad, good], "next": None}
        source = OpenSeaDropsSource(api_key="fixture", chains=(), opener=lambda *_a, **_k: Response(payload))
        original = normalize_drop

        def normalize(item, drop_type, retrieved_at):
            if drop_type == "recently_minted" and item["collection_slug"] == "bad-drop":
                raise AttributeError("fixture parser failure must not escape")
            return original(item, drop_type, retrieved_at)

        with patch("app.discovery.opensea_drops.normalize_drop", side_effect=normalize):
            leads = source.fetch()
        self.assertEqual(source.last_status["status"], "partial")
        self.assertEqual(source.last_status["leads_found"], 2)
        error = source.last_status["source_errors"][0]
        self.assertEqual(error["failure_phase"], "normalize_item:recently_minted:0")
        self.assertRegex(error["failure_location"], r"^test_opensea_drops\.py:\d+$")
        self.assertEqual(error["item_keys"], sorted(bad.keys()))
        self.assertNotIn("fixture parser failure", json.dumps(source.last_status))
        self.assertEqual(len(leads), 2)

    def test_bad_contract_is_not_persisted_as_a_full_address(self):
        item = drop()
        item["contract_address"] = "0x1234"
        candidate = normalize_drop(item, "upcoming", "now")
        self.assertIsNone(candidate["contract_or_mint_address"])
        self.assertEqual(candidate["address_kind"], "CONTRACT_ADDRESS_UNVERIFIED_OR_MISSING")

    def test_rate_limit_stops_bounded_source_without_retry(self):
        from urllib.error import HTTPError

        calls = []

        def opener(request, timeout):
            calls.append(request.full_url)
            raise HTTPError(request.full_url, 429, "rate limited", {}, None)

        source = OpenSeaDropsSource(api_key="fixture", opener=opener)
        self.assertEqual(source.fetch(), [])
        self.assertEqual(len(calls), 1)
        self.assertEqual(source.last_status["source_errors"][0]["http_status"], 429)
        self.assertEqual(source.last_status["error_type"], "HTTPError")

    def test_transport_failure_stops_remaining_drop_types(self):
        from urllib.error import URLError

        calls = []

        def opener(request, timeout):
            calls.append(request.full_url)
            raise URLError("network unreachable")

        source = OpenSeaDropsSource(api_key="fixture", opener=opener)
        self.assertEqual(source.fetch(), [])
        self.assertEqual(len(calls), 1)
        self.assertEqual(source.last_status["error_type"], "URLError")

    def test_fetch_tracks_bounded_failure_phase_without_error_text(self):
        from urllib.error import URLError

        source = OpenSeaDropsSource(api_key="fixture", opener=lambda *_a, **_k: (_ for _ in ()).throw(
            URLError("not printed")
        ))
        source.fetch()
        self.assertEqual(source.failure_phase, "request:upcoming")
        self.assertNotIn("not printed", json.dumps(source.last_status))

    def test_transport_diagnostic_exposes_category_not_exception_message(self):
        from urllib.error import URLError
        reason = PermissionError(10013, "socket blocked; sensitive URL omitted")
        def opener(*_args, **_kwargs):
            raise URLError(reason)
        source = OpenSeaDropsSource(api_key="test-secret", opener=opener)
        self.assertEqual(source.fetch(), [])
        error = source.last_status["source_errors"][0]
        self.assertEqual(error["transport_category"], "PermissionError")
        self.assertEqual(error["transport_code"], 10013)
        self.assertEqual(error["transport_state"], "SOCKET_PERMISSION_DENIED")
        self.assertNotIn("sensitive URL", json.dumps(source.last_status))
        self.assertNotIn("test-secret", json.dumps(source.last_status))


if __name__ == "__main__":
    unittest.main()
