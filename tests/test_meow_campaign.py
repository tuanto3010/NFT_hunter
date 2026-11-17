"""Campaign-specific Meow Cards checks use mocked JSON-RPC only."""

import hashlib
import unittest

from app.analysis.meow_campaign import MEOW_CARDS_ADDRESS, MeowCardsCampaignVerifier
from app.wallet.transaction_builder import _keccak256


def verified_abi():
    def fn(name, inputs, outputs, mutability):
        return {"type": "function", "name": name,
                "inputs": [{"type": t} for t in inputs],
                "outputs": [{"type": t} for t in outputs],
                "stateMutability": mutability}
    return [
        fn("isAgent", ["address", "uint256"], ["bool"], "view"),
        fn("priceOf", ["address", "uint256", "uint256"], ["uint256"], "view"),
        fn("totalMinted", [], ["uint256"], "view"),
        fn("mintedByWallet", ["address"], ["uint256"], "view"),
        fn("mint", ["uint256", "uint256"], ["uint256"], "payable"),
    ]


class FakeRpc:
    def __init__(self):
        self.calls = []

    def call(self, method, params):
        self.calls.append((method, params))
        if method == "eth_chainId":
            return "0x38"
        if method == "eth_blockNumber":
            return hex(133_500_000)
        if method == "eth_getCode":
            return "0x6001"
        if method == "eth_estimateGas":
            return hex(310808)
        if method == "eth_gasPrice":
            return hex(50_000_000)
        if method == "eth_call":
            data = params[0]["data"]
            selector = data[2:10]
            if selector == _keccak256(b"isAgent(address,uint256)")[:4].hex():
                return hex(1)
            if selector == _keccak256(b"priceOf(address,uint256,uint256)")[:4].hex():
                return hex(0)
            if selector == _keccak256(b"totalMinted()")[:4].hex():
                return hex(1407)
            if selector == _keccak256(b"mintedByWallet(address)")[:4].hex():
                return hex(0)
            if selector == _keccak256(b"mint(uint256,uint256)")[:4].hex():
                return "0x"
            raise RuntimeError("unknown mocked eth_call selector")
        raise RuntimeError("unexpected RPC method")


class MeowCampaignVerifierTests(unittest.TestCase):
    def test_wallet_bound_read_only_verification_and_gas(self):
        rpc = FakeRpc()
        abi = verified_abi()
        code_hash = hashlib.sha256(bytes.fromhex("6001")).hexdigest()
        source = {"code_hash": code_hash, "status": "verified", "abi": abi}
        verifier = MeowCardsCampaignVerifier("https://rpc.invalid", rpc=rpc)
        result = verifier.verify("0x" + "3e" * 20, "sampleagent", source)
        self.assertEqual(result["contract"], MEOW_CARDS_ADDRESS)
        self.assertTrue(result["agent_eligible"])
        self.assertEqual(result["mint_price_wei"], "0")
        self.assertEqual(result["total_minted"], 1407)
        self.assertEqual(result["simulation"], "PASS")
        self.assertEqual(result["gas_cost_bnb"], "0.0000155404")
        self.assertEqual(result["total_cost_bnb"], "0.0000155404")
        self.assertEqual(result["unsigned_transaction"]["value_wei"], "0")
        self.assertEqual(result["unsigned_transaction"]["chain_id"], 56)
        self.assertEqual(result["unsigned_transaction"]["chainId"], 56)
        self.assertEqual(result["unsigned_transaction"]["from"], "0x" + "3e" * 20)
        self.assertEqual(result["unsigned_transaction"]["to"], MEOW_CARDS_ADDRESS)
        self.assertEqual(result["unsigned_transaction"]["calldata"][:10], "0x1b2ef1ca")
        self.assertEqual(result["unsigned_transaction"]["data"], result["unsigned_transaction"]["calldata"])
        self.assertEqual(result["block_number"], 133_500_000)
        self.assertTrue(result["simulation_calldata_matches_unsigned"])
        self.assertEqual(sum(method == "eth_estimateGas" for method, _ in rpc.calls), 1)
        self.assertFalse(any(method in {"eth_sendTransaction", "eth_sendRawTransaction"} for method, _ in rpc.calls))

    def test_abi_mismatch_fails_closed_before_campaign_calls(self):
        rpc = FakeRpc()
        source = {"code_hash": hashlib.sha256(bytes.fromhex("6001")).hexdigest(),
                  "status": "verified", "abi": verified_abi()[:-1]}
        verifier = MeowCardsCampaignVerifier("https://rpc.invalid", rpc=rpc)
        with self.assertRaisesRegex(Exception, "Verified ABI is missing"):
            verifier.verify("0x" + "3e" * 20, "sampleagent", source)
        self.assertFalse(any(method == "eth_estimateGas" for method, _ in rpc.calls))


if __name__ == "__main__":
    unittest.main()
