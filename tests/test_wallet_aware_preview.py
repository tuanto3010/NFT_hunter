"""Wallet routing and isolation tests for read-only preview preparation."""

import json
import subprocess
import time
import unittest

from app.discovery.project_sources import ProjectObservation
from app.wallet.binance_wallet import BinanceAgenticWalletAdapter
from app.wallet.transaction_builder import UnsafeMintRequest
from app.wallet.transaction_simulator import (
    UnknownMintPrice,
    WalletAwareMintPreviewService,
)


ADDRESS = "0x" + "a" * 40
OTHER_ADDRESS = "0x" + "c" * 40
CONTRACT = "0x" + "b" * 40


def observation(**overrides):
    values = {
        "chain_id": 56, "contract_address": CONTRACT,
        "source_url": "https://explorer.example/contract",
        "observed_at": "2026-11-14T00:00:00+00:00", "verified": True,
        "nft_standard": "ERC-721", "mint_price": "0", "native_gas_token": "BNB",
        "eligibility": "unknown", "verification_status": "verified", "risk_assessment": "low",
        "details": {
            "mint_price": "0", "mint_price_verified": True,
            "mint_functions": [{"name": "mint", "inputs": [], "stateMutability": "nonpayable"}],
        },
    }
    values.update(overrides)
    return ProjectObservation(**values)


def cli(data):
    return subprocess.CompletedProcess(["baw"], 0, json.dumps({"success": True, "data": data}), "")


class FakeRunner:
    def __init__(self, address=ADDRESS):
        self.address = address
        self.calls = []

    def __call__(self, command, timeout):
        args = tuple(command[1:])
        self.calls.append(args)
        if args == ("wallet", "status", "--json"):
            return cli({"status": "CONNECTED"})
        if args == ("wallet", "chains", "--json"):
            return cli([{"binanceChainId": "56", "name": "BNB Smart Chain"}])
        if args == ("wallet", "address", "--json"):
            return cli({"addresses": [{"binanceChainId": "56", "address": self.address}]})
        if args == ("wallet", "settings", "--json"):
            return cli({"devMode": {"enabled": True, "expiresAt": int(time.time()) + 3600, "balanceExceeded": False}})
        if args[:2] == ("contract-call", "preview"):
            self.preview_args = args
            return cli({"simulationResult": {"simulationCode": "000000000", "simulationErrorDetail": None}})
        raise AssertionError(f"Unexpected CLI command: {args}")


class WalletAwarePreviewTests(unittest.TestCase):
    def test_wallet_specific_eligibility_is_rechecked_by_selected_provider_preview(self):
        runner = FakeRunner()
        service = WalletAwareMintPreviewService(BinanceAgenticWalletAdapter(runner=runner))
        result = service.prepare(observation(), "binance_agentic", ADDRESS, "56", "mint", {})
        self.assertEqual(result.provider_id, "binance_agentic")
        self.assertEqual(result.wallet_address, ADDRESS)
        self.assertEqual(result.eligibility_status, "eligible")
        self.assertEqual(result.status, "SIMULATION_PASSED")
        self.assertIn("--from", runner.preview_args)
        self.assertEqual(runner.preview_args[runner.preview_args.index("--from") + 1], ADDRESS)

    def test_provider_routing_does_not_fall_back_to_binance_for_metamask(self):
        runner = FakeRunner()
        result = WalletAwareMintPreviewService(BinanceAgenticWalletAdapter(runner=runner)).prepare(
            observation(), "metamask", ADDRESS, "56", "mint", {},
        )
        self.assertEqual(result.provider_id, "metamask")
        self.assertEqual(result.status, "AWAITING_METAMASK_READ_ONLY_SIMULATION")
        self.assertEqual(runner.calls, [])

    def test_address_mismatch_rejects_binance_routing_before_simulation(self):
        runner = FakeRunner()
        service = WalletAwareMintPreviewService(BinanceAgenticWalletAdapter(runner=runner))
        with self.assertRaisesRegex(UnsafeMintRequest, "does not match"):
            service.prepare(observation(), "binance_agentic", OTHER_ADDRESS, "56", "mint", {})
        self.assertFalse(any(call[:2] == ("contract-call", "preview") for call in runner.calls))

    def test_chain_mismatch_is_rejected_without_provider_fallback(self):
        runner = FakeRunner()
        service = WalletAwareMintPreviewService(BinanceAgenticWalletAdapter(runner=runner))
        with self.assertRaisesRegex(UnsafeMintRequest, "does not match this opportunity"):
            service.prepare(observation(), "metamask", ADDRESS, "1", "mint", {})
        self.assertEqual(runner.calls, [])

    def test_unknown_or_paid_price_never_enters_free_preview_path(self):
        runner = FakeRunner()
        service = WalletAwareMintPreviewService(BinanceAgenticWalletAdapter(runner=runner))
        with self.assertRaises(UnknownMintPrice):
            service.prepare(observation(details={"mint_functions": []}), "metamask", ADDRESS, "56", "mint", {})
        with self.assertRaisesRegex(UnsafeMintRequest, "PAID"):
            service.prepare(observation(mint_price="0.01"), "metamask", ADDRESS, "56", "mint", {})
        self.assertEqual(runner.calls, [])

    def test_solana_is_rejected_from_evm_preview_builder(self):
        service = WalletAwareMintPreviewService(BinanceAgenticWalletAdapter(runner=FakeRunner()))
        with self.assertRaisesRegex(UnsafeMintRequest, "Solana is separate"):
            service.prepare(observation(chain_id="solana-mainnet-beta"), "binance_agentic", ADDRESS,
                            "solana-mainnet-beta", "mint", {})


if __name__ == "__main__":
    unittest.main()
