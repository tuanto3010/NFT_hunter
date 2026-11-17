import io
import json
import unittest
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError
from unittest.mock import Mock

from app.discovery.opensea_drop_verifier import (
    API_BASE, ETH_SEADROP_V1, OpenSeaDropClient, OpenSeaDropError,
    _selected_eligibility, build_and_simulate_free_public_mint,
    evaluate_drop, simulate_public_mint_transaction, validate_public_mint_transaction,
)
from app.wallet.transaction_builder import _encode_static_word, _keccak256


CONTRACT = "0x" + "11" * 20
WALLET = "0x" + "22" * 20
FEE = "0x" + "33" * 20
SLUG = "sections-by-grant-yun"
ZERO = "0x" + "00" * 20


def public_mint_data(contract=CONTRACT, recipient=ZERO, quantity=1):
    signature = b"mintPublic(address,address,address,uint256)"
    selector = _keccak256(signature)[:4].hex()
    words = [
        _encode_static_word("address", contract),
        _encode_static_word("address", FEE),
        _encode_static_word("address", recipient),
        _encode_static_word("uint256", quantity),
    ]
    return "0x" + selector + "".join(words)


def details(active=False, price="0", stage_type="signed_presale"):
    start = "2026-11-18T17:20:00Z" if not active else "2026-11-16T00:00:00Z"
    end = "2026-11-18T17:39:00Z" if not active else "2026-11-17T00:00:00Z"
    stage = {"uuid": "stage-1", "stage_type": stage_type, "price": price,
             "price_currency_address": "0x" + "0" * 40,
             "start_time": start, "end_time": end}
    return {"contract_address": CONTRACT, "chain": "ethereum", "is_minting": active,
            "active_stage": stage if active else None, "next_stage": None if active else stage}


class FakeClient:
    def __init__(self, drop=None, eligibility=None):
        self.drop = drop or details()
        self.eligibility = eligibility or {"stages": [{"uuid": "stage-1", "eligible": True, "mints_remaining": 1}]}
        self.requests_made = 0
        self.build_calls = []

    def get_details(self, slug):
        self.requests_made += 1
        return 200, self.drop

    def get_eligibility(self, slug):
        self.requests_made += 1
        return 200, self.eligibility

    def build_mint(self, slug, minter, quantity=1):
        self.requests_made += 1
        self.build_calls.append((slug, minter, quantity))
        return 200, {"chain": "ethereum", "to": ETH_SEADROP_V1,
                     "data": public_mint_data(recipient=ZERO), "value": "0",
                     "stage_uuid": "stage-1", "stage_type": "public_sale"}


class Rpc:
    def __init__(self, fail_call=False):
        self.calls = []
        self.fail_call = fail_call

    def call(self, method, params):
        self.calls.append((method, params))
        if method == "eth_chainId": return "0x1"
        if method == "eth_blockNumber": return "0x100"
        if method == "eth_call":
            if self.fail_call: raise RuntimeError("revert")
            return "0x"
        if method == "eth_estimateGas": return "0x186a0"
        if method == "eth_gasPrice": return "0x3b9aca00"
        if method == "eth_getBalance": return "0xde0b6b3a7640000"
        raise AssertionError(method)


class OpenSeaDropVerifierTests(unittest.TestCase):
    def test_wallet_token_is_sent_only_to_eligibility_endpoint_and_never_in_error(self):
        seen = []

        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, amount): return json.dumps({"ok": True}).encode()

        def opener(request, timeout):
            seen.append((request.full_url, dict(request.header_items()), timeout))
            return Response()

        client = OpenSeaDropClient("api-secret-value", "wallet-secret-value", opener=opener)
        client.get_details(SLUG)
        client.get_eligibility(SLUG)
        self.assertEqual(seen[0][0], API_BASE + "/" + SLUG)
        self.assertNotIn("Authorization", seen[0][1])
        self.assertEqual(seen[1][1]["Authorization"], "Bearer wallet-secret-value")
        self.assertEqual(client.requests_made, 2)

    def test_request_budget_and_http_error_are_bounded_and_sanitized(self):
        def opener(request, timeout):
            raise HTTPError(request.full_url, 403, "forbidden", {}, io.BytesIO(b"private response"))
        client = OpenSeaDropClient("secret", opener=opener)
        with self.assertRaises(OpenSeaDropError) as caught:
            client.get_details(SLUG)
        self.assertEqual(caught.exception.category, "HTTP_ERROR")
        self.assertEqual(caught.exception.http_status, 403)
        self.assertNotIn("secret", str(caught.exception))
        client.requests_made = 3
        with self.assertRaisesRegex(OpenSeaDropError, "REQUEST_BUDGET_EXHAUSTED"):
            client.get_details(SLUG)

    def test_wallet_eligibility_requires_exact_stage_and_remaining_mints(self):
        self.assertEqual(_selected_eligibility({"stages": [{"stage_uuid": "stage-1", "is_eligible": True,
            "mints_remaining": 2}]}, "stage-1"), (True, 2))
        # Current OpenSea schema separates is_eligible from mints_remaining;
        # null remaining can mean ERC-1155 or an unreadable on-chain count.
        self.assertEqual(_selected_eligibility({"stages": [{"stage_uuid": "stage-1", "is_eligible": True,
            "mints_remaining": None}]}, "stage-1"), (True, None))
        self.assertEqual(_selected_eligibility({"stages": [{"stage_uuid": "stage-1", "is_eligible": False,
            "mints_remaining": None}]}, "stage-1"), (False, 0))
        self.assertEqual(_selected_eligibility({"stages": [{"uuid": "other", "eligible": True,
            "mints_remaining": 2}]}, "stage-1"), (None, None))

    def test_wrong_chain_and_ambiguous_or_duplicate_stage_stay_blocked(self):
        with self.assertRaisesRegex(OpenSeaDropError, "UNSUPPORTED_CHAIN"):
            evaluate_drop(slug=SLUG, expected_contract=CONTRACT, selected_address=WALLET,
                chain_id="8453", api_key="api-key", client=FakeClient())
        self.assertEqual(_selected_eligibility({"stages": [
            {"stage_uuid": "stage-1", "is_eligible": True, "mints_remaining": 1},
            {"stage_uuid": "stage-1", "is_eligible": True, "mints_remaining": 1},
        ]}, "stage-1"), (None, None))

    def test_unknown_mint_cap_blocks_preview_even_when_wallet_is_eligible(self):
        api = FakeClient(drop=details(active=True, stage_type="public_sale"))
        with self.assertRaisesRegex(OpenSeaDropError, "BLOCKED_WALLET_MINT_CAP_UNKNOWN_OR_EXHAUSTED"):
            build_and_simulate_free_public_mint(client=api, slug=SLUG, selected_wallet=WALLET,
                expected_contract=CONTRACT, stage={"uuid": "stage-1", "stage_type": "public_sale",
                "price": "0", "price_currency_address": ZERO}, eligibility=True,
                mints_remaining=None, evidence_expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
                risk_assessment="LOW", contract_source_verified=True,
                erc721_verified=True, rpc_url="https://rpc.invalid")
        self.assertEqual(api.build_calls, [])

    def test_expired_wallet_evidence_blocks_preview_before_api_build(self):
        api = FakeClient()
        stage = {"uuid": "stage-1", "stage_type": "public_sale", "price": "0",
                 "price_currency_address": ZERO}
        with self.assertRaisesRegex(OpenSeaDropError, "BLOCKED_ELIGIBILITY_EVIDENCE_EXPIRED"):
            build_and_simulate_free_public_mint(client=api, slug=SLUG, selected_wallet=WALLET,
                expected_contract=CONTRACT, stage=stage, eligibility=True, mints_remaining=1,
                evidence_expires_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
                risk_assessment="LOW", contract_source_verified=True, erc721_verified=True,
                rpc_url="https://rpc.invalid")
        self.assertEqual(api.build_calls, [])

    def test_upcoming_zero_price_stays_claim_and_wallet_auth_is_required(self):
        api = FakeClient()
        result = evaluate_drop(slug=SLUG, expected_contract=CONTRACT, selected_address=WALLET,
            chain_id="1", api_key="api-key", now=datetime(2026, 11, 16, tzinfo=timezone.utc), client=api)
        self.assertEqual(result["status"], "BLOCKED_WALLET_AUTH_NOT_CONFIGURED")
        self.assertEqual(result["stage_status"], "UNKNOWN")
        self.assertEqual(result["mint_price_status"], "UNKNOWN")
        self.assertEqual(api.requests_made, 0)
        self.assertIsNone(result["wallet_eligible"])

    def test_pinned_wallet_eligibility_is_wallet_and_stage_scoped(self):
        api = FakeClient()
        result = evaluate_drop(slug=SLUG, expected_contract=CONTRACT, selected_address=WALLET,
            chain_id="1", api_key="api-key", wallet_jwt="jwt", pinned_wallet_address=WALLET,
            now=datetime(2026, 11, 16, tzinfo=timezone.utc), client=api)
        self.assertEqual(result["status"], "UPCOMING")
        self.assertTrue(result["wallet_eligible"])
        self.assertEqual(result["mints_remaining"], 1)
        self.assertEqual(api.requests_made, 2)
        mismatch = evaluate_drop(slug=SLUG, expected_contract=CONTRACT, selected_address=WALLET,
            chain_id="1", api_key="api-key", wallet_jwt="jwt", pinned_wallet_address="0x" + "44" * 20,
            client=(mismatch_client := FakeClient()))
        self.assertEqual(mismatch["status"], "BLOCKED_WALLET_IDENTITY_MISMATCH")
        self.assertEqual(mismatch["requests_made"], 0)
        self.assertEqual(mismatch_client.requests_made, 0)

    def test_zero_remaining_mints_is_ineligible_even_if_flag_true(self):
        api = FakeClient(eligibility={"stages": [{"stage_uuid": "stage-1",
            "is_eligible": True, "mints_remaining": "0"}]})
        result = evaluate_drop(slug=SLUG, expected_contract=CONTRACT, selected_address=WALLET,
            chain_id="1", api_key="api-key", wallet_jwt="jwt", pinned_wallet_address=WALLET,
            now=datetime(2026, 11, 16, tzinfo=timezone.utc), client=api)
        self.assertEqual(result["status"], "INELIGIBLE")
        self.assertEqual(result["mints_remaining"], 0)

    def test_details_contract_or_chain_mismatch_blocks_eligibility(self):
        wrong = FakeClient(drop={**details(), "contract_address": "0x" + "44" * 20})
        result = evaluate_drop(slug=SLUG, expected_contract=CONTRACT, selected_address=WALLET,
            chain_id="1", api_key="k", wallet_jwt="jwt", pinned_wallet_address=WALLET, client=wrong)
        self.assertEqual(result["status"], "BLOCKED_CONTRACT_MISMATCH")
        self.assertEqual(wrong.requests_made, 1)

    def test_build_payload_checks_target_recipient_contract_value_stage_and_quantity(self):
        good = {"chain": "ethereum", "to": ETH_SEADROP_V1, "data": public_mint_data(),
                "value": "0", "stage_uuid": "stage-1"}
        tx = validate_public_mint_transaction(good, selected_wallet=WALLET, expected_contract=CONTRACT,
                                               expected_stage_id="stage-1", expected_stage_type="public_sale",
                                               expected_price_wei=0)
        self.assertEqual(tx["from"], WALLET)
        self.assertEqual(tx["chain_id"], 1)
        self.assertEqual(tx["value_wei"], "0")
        for invalid in [
            {**good, "to": CONTRACT},
            {**good, "data": public_mint_data(contract="0x" + "55" * 20)},
            {**good, "data": public_mint_data(recipient="0x" + "66" * 20)},
            {**good, "data": public_mint_data(quantity=2)},
            {**good, "value": "1"},
            {**good, "stage_uuid": "stage-else"},
            {**good, "stage_type": "signed_presale"},
        ]:
            with self.subTest(invalid=invalid):
                with self.assertRaises(OpenSeaDropError):
                    validate_public_mint_transaction(invalid, selected_wallet=WALLET,
                        expected_contract=CONTRACT, expected_stage_id="stage-1",
                        expected_stage_type="public_sale", expected_price_wei=0)

    def test_build_refuses_high_risk_and_allowlist_stage_before_api_call(self):
        api = FakeClient()
        stage = {"uuid": "stage-1", "stage_type": "public_sale", "price": "0",
                 "price_currency_address": "0x" + "0" * 40}
        with self.assertRaisesRegex(OpenSeaDropError, "BLOCKED_HIGH_RISK"):
            build_and_simulate_free_public_mint(client=api, slug=SLUG, selected_wallet=WALLET,
                expected_contract=CONTRACT, stage=stage, eligibility=True, risk_assessment="HIGH",
                mints_remaining=1,
                evidence_expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
                contract_source_verified=True, erc721_verified=True, rpc_url="https://rpc.invalid")
        self.assertEqual(api.build_calls, [])
        with self.assertRaisesRegex(OpenSeaDropError, "BLOCKED_UNSUPPORTED_STAGE"):
            build_and_simulate_free_public_mint(client=api, slug=SLUG, selected_wallet=WALLET,
                expected_contract=CONTRACT, stage={**stage, "stage_type": "signed_presale"},
                eligibility=True, mints_remaining=1, risk_assessment="LOW", contract_source_verified=True,
                evidence_expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
                erc721_verified=True, rpc_url="https://rpc.invalid")
        self.assertEqual(api.build_calls, [])

    def test_unsigned_build_and_rpc_simulation_are_read_only_and_cost_separated(self):
        api = FakeClient(drop=details(active=True, stage_type="public_sale"))
        rpc = Rpc()
        result = build_and_simulate_free_public_mint(client=api, slug=SLUG, selected_wallet=WALLET,
            expected_contract=CONTRACT, stage={"uuid": "stage-1", "stage_type": "public_sale",
            "price": "0", "price_currency_address": "0x" + "0" * 40}, eligibility=True,
            mints_remaining=1,
            evidence_expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
            risk_assessment="LOW", contract_source_verified=True, erc721_verified=True,
            rpc_url="https://rpc.invalid", rpc=rpc)
        self.assertEqual(result["status"], "PREVIEW_READY")
        self.assertEqual(result["transaction"]["value_wei"], "0")
        self.assertEqual(result["simulation"]["mint_price_wei"], "0")
        self.assertTrue(result["simulation"]["balance_sufficient"])
        methods = [method for method, _ in rpc.calls]
        self.assertEqual(methods, ["eth_chainId", "eth_blockNumber", "eth_call", "eth_estimateGas", "eth_gasPrice", "eth_getBalance"])
        self.assertNotIn("eth_sendRawTransaction", methods)


if __name__ == "__main__":
    unittest.main()
