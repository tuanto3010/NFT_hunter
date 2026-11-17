"""Deterministic tests for unsigned NFT mint construction and preview gating."""

from __future__ import annotations

from dataclasses import replace
import json
import subprocess
import time
import unittest

from app.discovery.project_sources import ProjectObservation
from app.wallet.binance_wallet import BinanceAgenticWalletAdapter, BinanceWalletError
from app.wallet.transaction_builder import (
    MintTransactionBuilder,
    UnsafeMintRequest,
    _keccak256,
    validate_prepared_evm_mint,
)
from app.wallet.transaction_simulator import (
    BinanceMintPreviewService,
    DeveloperModeRequired,
    EvmRpcFeeEstimator,
    SolanaMintPreviewUnavailable,
)


WALLET_ADDRESS = "0x" + "a" * 40
CONTRACT_ADDRESS = "0x" + "b" * 40


def verified_observation(**overrides):
    fields = {
        "chain_id": 56,
        "contract_address": CONTRACT_ADDRESS,
        "source_url": "https://explorer.example/contract",
        "observed_at": "2026-11-14T00:00:00+00:00",
        "verified": True,
        "nft_standard": "ERC-721",
        "mint_price": "0",
        "native_gas_token": "BNB",
        "eligibility": "eligible",
        "verification_status": "verified",
        "risk_assessment": "low",
        "details": {
            "mint_price": "0",
            "mint_functions": [{
                "name": "mint",
                "inputs": [{"name": "quantity", "type": "uint256"}],
                "payable": False,
                "stateMutability": "nonpayable",
            }],
        },
    }
    fields.update(overrides)
    return ProjectObservation(**fields)


def cli_result(data, success=True):
    return subprocess.CompletedProcess(
        args=["baw"], returncode=0,
        stdout=json.dumps({"success": success, "data": data}), stderr="",
    )


class FakeRunner:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, command, timeout):
        self.calls.append((tuple(command), timeout))
        result = self.responses.get(tuple(command[1:]))
        if result is None:
            raise AssertionError("unexpected CLI command: %r" % (tuple(command[1:]),))
        return result


def preview_responses(*, enabled=True):
    return {
        ("wallet", "settings", "--json"): cli_result({
            "devMode": {
                "enabled": enabled,
                "expiresAt": int(time.time()) + 3600 if enabled else None,
                "balanceExceeded": False,
            },
        }),
        ("wallet", "chains", "--json"): cli_result([
            {"binanceChainId": "56", "name": "BNB Smart Chain"},
        ]),
        ("wallet", "address", "--json"): cli_result({"addresses": [
            {"binanceChainId": "56", "address": WALLET_ADDRESS},
        ]}),
        ("contract-call", "preview", "--binanceChainId", "56",
         "--from", WALLET_ADDRESS, "--to", CONTRACT_ADDRESS, "--value", "0",
         "--inputData", "0xa0712d68" + "0" * 63 + "2", "--json"):
            cli_result({
                "requestId": "never-kept-in-result",
                "parsedTx": {"transactionType": "ContractCall"},
                "simulationResult": {
                    "simulationCode": "000000000",
                    "simulationErrorDetail": None,
                    "balanceChanges": [],
                    "allowanceChanges": [],
                    "authorityChanges": [],
                },
                "risks": {"riskDetails": [{
                    "code": "RISK_001",
                    "title": "Review",
                    "description": "Target 0x" + "c" * 40 + " needs review",
                }], "addresses": {}},
                "requireConfirmation": False,
                "expiresAt": int(time.time()) + 120,
            }),
    }


class MintTransactionBuilderTests(unittest.TestCase):
    def setUp(self):
        self.builder = MintTransactionBuilder()

    def test_keccak_uses_ethereum_selectors(self):
        self.assertEqual(_keccak256(b"transfer(address,uint256)")[:4].hex(), "a9059cbb")
        self.assertEqual(_keccak256(b"mint(uint256)")[:4].hex(), "a0712d68")

    def test_uint_abi_alias_is_canonicalized_for_the_selector(self):
        observation = verified_observation(details={"mint_functions": [{
            "name": "mint", "inputs": [{"name": "quantity", "type": "uint"}],
            "stateMutability": "nonpayable",
        }]})
        transaction = self.builder.build_evm_mint(observation, WALLET_ADDRESS, "mint", {"quantity": 1})
        self.assertTrue(transaction.calldata.startswith("0xa0712d68"))

    def test_builds_zero_value_mint_and_binds_exact_parameters(self):
        transaction = self.builder.build_evm_mint(
            verified_observation(), WALLET_ADDRESS, "mint", {"quantity": 2},
        )
        self.assertEqual(transaction.calldata, "0xa0712d68" + "0" * 63 + "2")
        self.assertEqual(transaction.value_wei, "0")
        self.assertEqual(transaction.mint_price_wei, "0")
        self.assertEqual(transaction.native_gas_token, "BNB")
        self.assertEqual(len(transaction.transaction_digest), 64)
        validate_prepared_evm_mint(transaction)
        self.assertNotIn(WALLET_ADDRESS, repr(transaction))
        self.assertNotIn(CONTRACT_ADDRESS, repr(transaction))

    def test_rejects_unverified_ineligible_high_risk_or_paid_candidates(self):
        variants = [
            verified_observation(verified=False),
            verified_observation(verification_status="unknown"),
            verified_observation(eligibility="unknown"),
            verified_observation(risk_assessment="needs_review"),
            verified_observation(mint_price="0.01"),
        ]
        for observation in variants:
            with self.subTest(observation=observation):
                with self.assertRaises(UnsafeMintRequest):
                    self.builder.build_evm_mint(observation, WALLET_ADDRESS, "mint", {"quantity": 1})

    def test_rejects_robinhood_even_if_runtime_lists_it(self):
        with self.assertRaisesRegex(UnsafeMintRequest, "not enabled"):
            self.builder.build_evm_mint(
                verified_observation(chain_id=4663, native_gas_token="ETH"),
                WALLET_ADDRESS, "mint", {"quantity": 1},
            )

    def test_rejects_unrecognized_method_dynamic_abi_and_bad_arguments(self):
        admin = verified_observation(details={"mint_functions": [{"name": "setMinting", "inputs": [], "stateMutability": "nonpayable"}]})
        with self.assertRaises(UnsafeMintRequest):
            self.builder.build_evm_mint(admin, WALLET_ADDRESS, "setMinting", ())
        dynamic = verified_observation(details={"mint_functions": [{
            "name": "mint", "inputs": [{"name": "proof", "type": "bytes"}],
            "stateMutability": "nonpayable",
        }]})
        with self.assertRaises(UnsafeMintRequest):
            self.builder.build_evm_mint(dynamic, WALLET_ADDRESS, "mint", {"proof": "0x1234"})
        with self.assertRaises(UnsafeMintRequest):
            self.builder.build_evm_mint(verified_observation(), WALLET_ADDRESS, "mint", {"other": 1})

    def test_rejects_modified_transaction_parameters(self):
        transaction = self.builder.build_evm_mint(
            verified_observation(), WALLET_ADDRESS, "mint", {"quantity": 1},
        )
        modified = replace(transaction, value_wei="1")
        with self.assertRaisesRegex(UnsafeMintRequest, "zero-value"):
            validate_prepared_evm_mint(modified)
        modified_data = replace(transaction, calldata="0xdeadbeef" + transaction.calldata[10:])
        with self.assertRaisesRegex(UnsafeMintRequest, "does not match"):
            validate_prepared_evm_mint(modified_data)


class BinanceMintPreviewServiceTests(unittest.TestCase):
    def test_checks_developer_mode_before_building_and_runs_preview_only(self):
        runner = FakeRunner(preview_responses())
        wallet = BinanceAgenticWalletAdapter(runner=runner)
        service = BinanceMintPreviewService(wallet)
        result = service.preview_evm_mint(verified_observation(), "mint", {"quantity": 2})
        self.assertEqual(result.status, "SIMULATION_PASSED")
        self.assertEqual(result.mint_price_wei, "0")
        self.assertEqual(len(result.risks), 1)
        self.assertNotIn("c" * 40, result.risks[0].description)
        self.assertFalse(hasattr(result, "request_id"))
        self.assertNotIn("never-kept-in-result", repr(result))
        commands = [call[0][1:] for call in runner.calls]
        self.assertEqual(commands[0], ("wallet", "settings", "--json"))
        self.assertEqual(commands[-1][0:2], ("contract-call", "preview"))
        self.assertFalse(any("execute" in command for command in commands))

    def test_disabled_developer_mode_stops_before_network_or_transaction_preparation(self):
        runner = FakeRunner({
            ("wallet", "settings", "--json"): cli_result({
                "devMode": {"enabled": False, "expiresAt": None, "balanceExceeded": False},
            }),
        })
        service = BinanceMintPreviewService(BinanceAgenticWalletAdapter(runner=runner))
        with self.assertRaises(DeveloperModeRequired):
            service.preview_evm_mint(verified_observation(), "mint", {"quantity": 1})
        self.assertEqual(len(runner.calls), 1)

    def test_expired_or_balance_exceeded_developer_mode_is_inactive(self):
        for mode in (
            {"enabled": True, "expiresAt": int(time.time()) - 1, "balanceExceeded": False},
            {"enabled": True, "expiresAt": int(time.time()) + 600, "balanceExceeded": True},
        ):
            runner = FakeRunner({("wallet", "settings", "--json"): cli_result({"devMode": mode})})
            with self.assertRaises(DeveloperModeRequired):
                BinanceMintPreviewService(BinanceAgenticWalletAdapter(runner=runner)).preview_evm_mint(
                    verified_observation(), "mint", {"quantity": 1},
                )
            self.assertEqual(len(runner.calls), 1)

    def test_solana_preview_fails_closed_until_instruction_parser_exists(self):
        service = BinanceMintPreviewService(BinanceAgenticWalletAdapter(runner=FakeRunner({})))
        with self.assertRaises(SolanaMintPreviewUnavailable):
            service.preview_solana_mint(b"unsigned")

    def test_preview_gate_rejects_transaction_tampering_and_never_exposes_execute(self):
        runner = FakeRunner({})
        wallet = BinanceAgenticWalletAdapter(runner=runner)
        transaction = MintTransactionBuilder().build_evm_mint(
            verified_observation(), WALLET_ADDRESS, "mint", {"quantity": 1},
        )
        with self.assertRaises(BinanceWalletError):
            wallet._preview_evm_mint(replace(transaction, value_wei="3"),
                                     type("Mode", (), {"active": True})())
        self.assertFalse(hasattr(wallet, "execute"))
        self.assertFalse(hasattr(wallet, "sign_transaction"))
        self.assertEqual(runner.calls, [])


class EvmRpcFeeEstimatorTests(unittest.TestCase):
    def test_separates_estimated_network_fee_from_zero_mint_price(self):
        class Rpc:
            def __init__(self):
                self.calls = []

            def call(self, method, params):
                self.calls.append((method, params))
                return {"eth_chainId": "0x38", "eth_estimateGas": "0x5208", "eth_gasPrice": "0x3b9aca00"}[method]

        rpc = Rpc()
        transaction = MintTransactionBuilder().build_evm_mint(
            verified_observation(), WALLET_ADDRESS, "mint", {"quantity": 1},
        )
        quote = EvmRpcFeeEstimator({"56": rpc}).estimate(transaction)
        self.assertEqual(quote.gas_limit, 21000)
        self.assertEqual(quote.fee_wei, 21000 * 1_000_000_000)
        self.assertEqual(quote.native_token, "BNB")
        self.assertEqual(transaction.mint_price_wei, "0")
        self.assertEqual([call[0] for call in rpc.calls], ["eth_chainId", "eth_estimateGas", "eth_gasPrice"])

    def test_rejects_rpc_chain_mismatch_before_estimating(self):
        class Rpc:
            def call(self, method, params):
                return "0x1"

        transaction = MintTransactionBuilder().build_evm_mint(
            verified_observation(), WALLET_ADDRESS, "mint", {"quantity": 1},
        )
        with self.assertRaisesRegex(ValueError, "different network"):
            EvmRpcFeeEstimator({"56": Rpc()}).estimate(transaction)


if __name__ == "__main__":
    unittest.main()
