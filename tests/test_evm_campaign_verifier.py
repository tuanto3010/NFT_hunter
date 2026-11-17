"""Unit coverage for the generic, read-only EVM campaign verifier."""

import unittest

from app.analysis.evm_campaign import EvmCampaignVerifier, _selector
from app.wallet.transaction_builder import _keccak256


WALLET = "0x" + "12" * 20
CONTRACT = "0x" + "34" * 20


def fn(name, inputs, outputs, mutability):
    return {"type": "function", "name": name,
            "inputs": [{"type": x} for x in inputs],
            "outputs": [{"type": x} for x in outputs],
            "stateMutability": mutability}


ABI = [
    fn("supportsInterface", ["bytes4"], ["bool"], "view"),
    fn("price", ["address"], ["uint256"], "view"),
    fn("isEligible", ["address"], ["bool"], "view"),
    fn("mintOpen", [], ["bool"], "view"),
    fn("mint", [], [], "nonpayable"),
]


class FakeRpc:
    def __init__(self, *, price=0, eligible=1, open_=1):
        self.calls = []
        self.price, self.eligible, self.open = price, eligible, open_

    def call(self, method, params):
        self.calls.append((method, params))
        if method == "eth_chainId": return "0x38"
        if method == "eth_blockNumber": return "0x10"
        if method == "eth_getCode": return "0x6001"
        if method == "eth_estimateGas": return "0x5208"
        if method == "eth_gasPrice": return "0x3b9aca00"
        if method == "eth_call":
            data = params[0]["data"]
            selector = data[2:10]
            if selector == "01ffc9a7": return "0x1"
            if selector == _selector("price(address)"): return hex(self.price)
            if selector == _selector("isEligible(address)"): return hex(self.eligible)
            if selector == _selector("mintOpen()"): return hex(self.open)
            if selector == _selector("mint()"): return "0x"
        raise AssertionError("unexpected call: %s" % method)


class Source:
    def check_verification(self, address):
        return {"status": "verified", "abi": ABI}


def run(rpc, risk="low", abi=ABI, mint_method="mint()", price_check=None,
        eligibility_check=None, availability_check=None):
    verifier = EvmCampaignVerifier(56, "https://rpc.invalid", rpc=rpc, source_inspector=Source())
    if abi is not ABI:
        verifier.source_inspector = type("OtherSource", (), {
            "check_verification": lambda self, address: {"status": "verified", "abi": abi}
        })()
    return verifier.verify(
        contract_address=CONTRACT, official_source_url="https://project.example/mint",
        source_confirmed=True, source_confirmation="Confirmed project-controlled announcement",
        wallet_address=WALLET, mint_method=mint_method, mint_arguments=[],
        price_check=price_check or {"method": "price(address)", "arguments": ["$wallet"]},
        eligibility_check=eligibility_check or {"method": "isEligible(address)", "arguments": ["$wallet"]},
        availability_check=availability_check or {"method": "mintOpen()", "arguments": []},
        payment_evidence="Verified source has no token payment path", risk_assessment=risk,
    )


class EvmCampaignVerifierTests(unittest.TestCase):
    def test_zero_price_eligible_active_campaign_prepares_exact_unsigned_call(self):
        rpc = FakeRpc()
        result = run(rpc)
        self.assertEqual(result["status"], "VERIFIED_PREVIEWABLE")
        self.assertEqual(result["chain_id"], 56)
        self.assertEqual(result["block_number"], 16)
        self.assertEqual(result["mint_price_wei"], "0")
        tx = result["unsigned_transaction"]
        self.assertEqual(tx["from"], WALLET)
        self.assertEqual(tx["to"], CONTRACT)
        self.assertEqual(tx["value_wei"], "0")
        self.assertEqual(tx["data"], "0x" + _selector("mint()"))
        self.assertEqual(tx["gas_limit_estimate"], 21000)
        self.assertTrue(any(method == "eth_call" and params[0].get("data") == tx["data"]
                            for method, params in rpc.calls))
        self.assertFalse(any(method in {"eth_sendTransaction", "eth_sendRawTransaction"} for method, _ in rpc.calls))

    def test_paid_campaign_is_rejected(self):
        self.assertEqual(run(FakeRpc(price=1))["status"], "REJECTED_PAID")

    def test_closed_or_sold_out_campaign_has_distinct_rejection(self):
        self.assertEqual(run(FakeRpc(open_=0))["status"], "REJECTED_SOLD_OUT")

    def test_high_risk_is_never_downgraded_or_previewed(self):
        rpc = FakeRpc()
        result = run(rpc, risk="high")
        self.assertEqual(result["status"], "BLOCKED_HIGH_RISK")
        self.assertIsNone(result["unsigned_transaction"])
        self.assertFalse(any(method == "eth_estimateGas" for method, _ in rpc.calls))

    def test_unverified_source_is_deferred_without_mint_call(self):
        rpc = FakeRpc()
        verifier = EvmCampaignVerifier(56, "https://rpc.invalid", rpc=rpc,
                                      source_inspector=type("S", (), {"check_verification": lambda *a: {"status": "unknown"}})())
        result = verifier.verify(
            contract_address=CONTRACT, official_source_url="https://project.example/mint",
            source_confirmed=True, source_confirmation="Confirmed project-controlled announcement",
            wallet_address=WALLET, mint_method="mint()", mint_arguments=[],
            price_check={}, eligibility_check={}, availability_check={},
            payment_evidence="Reviewed", risk_assessment="low")
        self.assertEqual(result["status"], "DEFERRED_MISSING_EVIDENCE")
        self.assertFalse(any(method == "eth_estimateGas" for method, _ in rpc.calls))

    def test_unknown_method_is_blocked_and_no_default_mint_signature_is_assumed(self):
        result = run(FakeRpc(), mint_method="mint(uint256)")
        self.assertEqual(result["status"], "BLOCKED_UNSUPPORTED")
        self.assertIsNone(result["unsigned_transaction"])

    def test_selected_wallet_is_embedded_in_wallet_specific_read_call(self):
        rpc = FakeRpc()
        run(rpc)
        eligible_call = next(params[0]["data"] for method, params in rpc.calls
                             if method == "eth_call" and params[0].get("data", "").startswith("0x" + _selector("isEligible(address)")))
        self.assertTrue(eligible_call.endswith(WALLET[2:].lower().rjust(64, "0")))

    def test_nonpayable_campaign_uses_exact_mint_call_for_wallet_eligibility(self):
        rpc = FakeRpc()
        result = run(
            rpc,
            price_check={"interpretation": "verified_nonpayable_mint"},
            eligibility_check={"interpretation": "exact_mint_call"},
        )
        self.assertEqual(result["status"], "VERIFIED_PREVIEWABLE")
        tx = result["unsigned_transaction"]
        matching = [(method, params) for method, params in rpc.calls
                    if method == "eth_call" and params[0].get("data") == tx["data"]]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0][1][0]["from"], WALLET)
        self.assertEqual(matching[0][1][0]["value"], "0x0")

    def test_nonpayable_price_profile_rejects_payable_mint_abi(self):
        payable_abi = [dict(item, stateMutability="payable") if item.get("name") == "mint" else item
                       for item in ABI]
        result = run(FakeRpc(), abi=payable_abi,
                     price_check={"interpretation": "verified_nonpayable_mint"},
                     eligibility_check={"interpretation": "exact_mint_call"})
        self.assertEqual(result["status"], "DEFERRED_MISSING_EVIDENCE")
        self.assertIsNone(result["unsigned_transaction"])

    def test_chain_mismatch_is_rejected(self):
        rpc = FakeRpc()
        rpc.call = lambda method, params: "0x1" if method == "eth_chainId" else FakeRpc.call(rpc, method, params)
        with self.assertRaisesRegex(Exception, "chain ID"):
            run(rpc)


if __name__ == "__main__":
    unittest.main()
