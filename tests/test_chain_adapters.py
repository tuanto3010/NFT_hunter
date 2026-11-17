"""Network-free tests for the multi-chain read-only adapter layer."""

import base64
import io
import json
import sqlite3
import struct
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

from app.discovery.base_scanner import BaseScanner
from app.discovery.bnb_scanner import BnbScanner
from app.discovery.chain_adapter import CHAIN_SPECS, safe_rpc_source, validate_chain_id
from app.discovery.ethereum_scanner import EthereumScanner
from app.discovery.evm_scanner import (
    TRANSFER_721, TRANSFER_SINGLE_1155, EvmScanner, JsonRpcClient, RpcError,
)
from app.discovery.ethereum_scanner import EthereumScanner
from app.discovery.robinhood_scanner import RobinhoodScanner
from app.discovery.solana_scanner import (
    CORE_CANDY_GUARD_PROGRAM, CORE_CANDY_MACHINE_DISCRIMINATOR, CORE_CANDY_MACHINE_PROGRAM,
    MagicEdenLaunchpadSource, SolanaRpc, SolanaScanner, b58decode, b58encode,
)
from app.discovery.candy_guard_decoder import CORE_CANDY_GUARD_DISCRIMINATOR
from app.database import Database
from app.scheduler import ScanScheduler
from app.discovery.project_sources import ProjectObservation
from app.wallet.capabilities import BINANCE_AGENTIC_CAPABILITIES, can_use_for_nft_mint


ADDR = "0x" + "a" * 40
MINT = "So11111111111111111111111111111111111111112"
ZERO = "0x" + "0" * 64


class FakeEvmRpc:
    endpoint = "https://rpc.example.invalid"

    def __init__(self, chain_id=1, fail=False):
        self.chain_id = chain_id
        self.fail = fail
        self.calls = []

    def call(self, method, params):
        self.calls.append((method, params))
        if self.fail:
            raise RpcError("offline")
        if method == "eth_chainId":
            return hex(self.chain_id)
        if method == "eth_blockNumber":
            return "0x64"
        if method == "eth_getLogs":
            topic = params[0]["topics"][0]
            if topic == TRANSFER_721:
                return [{"address": ADDR, "topics": [TRANSFER_721, ZERO, "0x" + "1" * 64, "0x" + "2" * 64], "data": "0x"}]
            return []
        if method == "eth_getCode":
            return "0x6001"
        if method == "eth_call":
            data = params[0]["data"]
            if data.startswith("0x01ffc9a7"):
                return hex(1 if data.endswith("80ac58cd") else 0)
            return "0x"
        if method == "eth_estimateGas":
            return "0x5208"
        raise AssertionError("Unexpected RPC method: " + method)


class RejectingLogsEvmRpc(FakeEvmRpc):
    def call(self, method, params):
        if method == "eth_getLogs":
            self.calls.append((method, params))
            raise RpcError("eth_getLogs returned JSON-RPC error code -32005")
        return super().call(method, params)


class FakeSolanaRpc:
    def __init__(self, fail=False, genesis_hash="5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d"):
        self.fail = fail
        self.genesis_hash = genesis_hash
        self.calls = []

    def call(self, method, params):
        self.calls.append((method, params))
        if self.fail:
            raise RuntimeError("offline")
        if method == "getGenesisHash":
            return self.genesis_hash
        if method == "getAccountInfo" and params[1]["encoding"] == "jsonParsed":
            return {"value": {"data": {"parsed": {"info": {
                "decimals": 0, "mintAuthority": None, "freezeAuthority": None,
            }}}}}
        if method == "getAccountInfo":
            data = bytearray([4]) + bytes([1]) * 32 + b58decode(MINT)
            for text in ("Example NFT", "EX", "https://example.invalid/nft.json"):
                encoded = text.encode()
                data.extend(struct.pack("<I", len(encoded)) + encoded)
            data.extend(struct.pack("<H", 0))  # seller fee
            data.extend(bytes([1]))  # creators option
            data.extend(struct.pack("<I", 1) + bytes([2]) * 32 + bytes([1, 100]))
            data.extend(bytes([0, 1, 0, 1, 0, 1, 1]) + bytes([3]) * 32)
            return {"value": {"data": [base64.b64encode(data).decode(), "base64"]}}
        if method == "getTokenSupply":
            return {"value": {"amount": "1"}}
        if method == "getFeeForMessage":
            return {"value": 5000}
        if method == "getBalance":
            return {"value": 1_234_567_890}
        raise AssertionError("Unexpected RPC method: " + method)


class FakeDasRpc:
    endpoint = "https://das.example.invalid"

    def __init__(self, genesis_hash="5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d"):
        self.genesis_hash = genesis_hash
        self.calls = []

    def call(self, method, params):
        self.calls.append((method, params))
        if method == "getGenesisHash":
            return self.genesis_hash
        if method == "searchAssets":
            return {"items": [{"id": MINT, "interface": params[0]["interface"],
                               "content": {"metadata": {"name": "Indexed NFT"}, "json_uri": "https://metadata.invalid/1"},
                               "creators": [{"address": "creator", "verified": False}],
                               "authorities": [{"address": "authority"}],
                               "grouping": [{"group_key": "collection", "group_value": "collection-id"}]}]}
        if method == "getAsset":
            return {"id": params[0]["id"], "interface": "MplCoreAsset"}
        raise AssertionError("Unexpected DAS RPC method: " + method)


class ChainAdapterTests(unittest.TestCase):
    def test_chain_ids_for_all_evm_adapters(self):
        adapters = [
            BnbScanner("https://rpc.invalid", rpc=FakeEvmRpc(56)),
            EthereumScanner("https://rpc.invalid", rpc=FakeEvmRpc(1)),
            BaseScanner("https://rpc.invalid", rpc=FakeEvmRpc(8453)),
            RobinhoodScanner(rpc=FakeEvmRpc(4663)),
        ]
        for adapter in adapters:
            self.assertEqual(int(adapter.rpc.call("eth_chainId", []), 16), adapter.spec.chain_id)
        with self.assertRaises(ValueError):
            validate_chain_id(56, 1)

    def test_rpc_source_labels_remove_path_query_and_userinfo_secrets(self):
        self.assertEqual(
            safe_rpc_source("https://user:secret@rpc.example.invalid/v2/APIKEY?token=private"),
            "https://rpc.example.invalid",
        )

    def test_evm_mint_event_parsing_and_duplicate_contracts(self):
        rpc = FakeEvmRpc(chain_id=1)
        scanner = EthereumScanner("https://rpc.invalid", rpc=rpc, lookback_blocks=2, block_chunk=1)
        observations = scanner.scan()
        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0].chain_id, 1)
        self.assertEqual(observations[0].native_gas_token, "ETH")
        self.assertTrue(observations[0].risk_assessment == "high")
        self.assertEqual(len(scanner.scan()), 1)

    def test_evm_cursor_restarts_with_reorg_overlap(self):
        rpc = FakeEvmRpc(chain_id=1)
        scanner = EthereumScanner("https://rpc.invalid", rpc=rpc, lookback_blocks=500, block_chunk=100)
        scanner.scan(from_block=100)
        first_log_request = next(params[0] for method, params in rpc.calls if method == "eth_getLogs")
        self.assertEqual(int(first_log_request["fromBlock"], 16), 89)
        self.assertEqual(scanner.last_scanned_block, 100)

    def test_erc1155_mint_event_from_zero_address(self):
        log = {"topics": [TRANSFER_SINGLE_1155, "0x1", ZERO, "0x2"]}
        self.assertTrue(EvmScanner._is_mint_log(log))
        self.assertFalse(EvmScanner._is_mint_log({"topics": [TRANSFER_SINGLE_1155, "0x1", "0x2", "0x3"]}))

    def test_mint_abi_inspection_catches_camel_and_snake_case(self):
        abi = [
            {"type": "function", "name": "whitelistMint", "inputs": [], "stateMutability": "payable"},
            {"type": "function", "name": "safe_mint", "inputs": [], "payable": False},
            {"type": "function", "name": "transferFrom", "inputs": []},
        ]
        result = EvmScanner.inspect_mint_functions(abi)
        self.assertEqual([item["name"] for item in result], ["whitelistMint", "safe_mint"])
        self.assertTrue(result[0]["payable"])

    def test_rpc_failure_is_reported(self):
        scanner = BnbScanner("https://rpc.invalid", rpc=FakeEvmRpc(chain_id=56, fail=True))
        with self.assertRaises(RpcError):
            scanner.scan()

    def test_bnb_log_limit_error_does_not_claim_method_is_unsupported(self):
        rpc = RejectingLogsEvmRpc(chain_id=56)
        scanner = BnbScanner(
            "https://rpc.invalid", rpc=rpc, lookback_blocks=1,
            logs_api_key="test-key-never-log", logs_api_url="https://api.example.invalid/v2/api",
        )
        with patch("app.discovery.evm_scanner.urlopen") as mocked_urlopen:
            with self.assertRaisesRegex(RpcError, "rejected eth_getLogs.*-32005.*limits"):
                scanner.scan()
        mocked_urlopen.assert_not_called()

    def test_bnb_rpc_failure_explains_log_capable_endpoint(self):
        scanner = BnbScanner(
            "https://rpc.invalid", rpc=RejectingLogsEvmRpc(chain_id=56), lookback_blocks=1,
        )
        with self.assertRaisesRegex(RpcError, "rejected eth_getLogs.*-32005.*limits"):
            scanner.scan()

    def test_bnb_explicit_method_not_found_is_reported_as_unsupported(self):
        class NoGetLogsRpc(RejectingLogsEvmRpc):
            def call(self, method, params):
                if method == "eth_getLogs":
                    self.calls.append((method, params))
                    raise RpcError("eth_getLogs returned JSON-RPC error code -32601")
                return super().call(method, params)

        scanner = BnbScanner("https://rpc.invalid", rpc=NoGetLogsRpc(chain_id=56), lookback_blocks=1)
        with self.assertRaisesRegex(RpcError, "explicitly reports eth_getLogs as unsupported"):
            scanner.scan()

    def test_bnb_http_rate_limit_is_reported_without_rpc_url(self):
        rpc = JsonRpcClient("https://rpc.invalid/secret-path", retries=0, min_interval=0)
        scanner = BnbScanner("https://rpc.invalid/secret-path", rpc=rpc, lookback_blocks=1)
        error = HTTPError("https://rpc.invalid/secret-path", 429, "Too Many Requests", {}, None)
        with patch("app.discovery.evm_scanner.urlopen", side_effect=error):
            with self.assertRaisesRegex(RpcError, r"rate-limited eth_getLogs \(HTTP 429\)") as raised:
                scanner._mint_logs(1, 1)
        self.assertNotIn("secret-path", str(raised.exception))

    def test_etherscan_v2_source_lookup_returns_verified_abi(self):
        scanner = EthereumScanner(
            "https://rpc.invalid", rpc=FakeEvmRpc(chain_id=1),
            logs_api_key="test-key-never-log",
        )
        payload = {
            "status": "1",
            "message": "OK",
            "result": [{
                "SourceCode": "contract Example { function mint() external {} }",
                "ABI": json.dumps([{"type": "function", "name": "mint", "inputs": [], "stateMutability": "nonpayable"}]),
            }],
        }

        def fake_urlopen(request, timeout):
            query = parse_qs(urlsplit(request.full_url).query)
            self.assertEqual(query["chainid"], ["1"])
            self.assertEqual(query["module"], ["contract"])
            self.assertEqual(query["action"], ["getsourcecode"])
            self.assertEqual(query["address"], [ADDR])
            self.assertEqual(query["apikey"], ["test-key-never-log"])
            return io.BytesIO(json.dumps(payload).encode())

        with patch("app.discovery.evm_scanner.urlopen", side_effect=fake_urlopen):
            result = scanner.check_verification(ADDR)

        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["abi"][0]["name"], "mint")

    def test_bnb_verification_uses_sourcify_without_api_key(self):
        scanner = BnbScanner("https://rpc.invalid", rpc=FakeEvmRpc(chain_id=56))
        payload = {"match": "exact_match", "abi": [{"type": "function", "name": "mint"}]}

        def fake_urlopen(request, timeout):
            self.assertIn("/v2/contract/56/" + ADDR, request.full_url)
            self.assertNotIn("apikey", request.full_url.lower())
            return io.BytesIO(json.dumps(payload).encode())

        with patch("app.discovery.evm_scanner.urlopen", side_effect=fake_urlopen):
            result = scanner.check_verification(ADDR)

        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["abi"][0]["name"], "mint")

    def test_native_gas_tokens_and_read_only_evm_estimate(self):
        cases = [(BnbScanner, 56, "BNB"), (EthereumScanner, 1, "ETH"),
                 (BaseScanner, 8453, "ETH"), (RobinhoodScanner, 4663, "ETH")]
        for cls, chain_id, token in cases:
            rpc = FakeEvmRpc(chain_id=chain_id)
            scanner = cls("https://rpc.invalid", rpc=rpc)
            self.assertEqual(scanner.spec.native_token, token)
            self.assertEqual(scanner.estimate_gas({"to": ADDR, "data": "0x", "value": "0x0"}), 21000)
            with self.assertRaises(ValueError):
                scanner.estimate_gas({"to": ADDR, "data": "0x", "value": "0x1"})

    def test_solana_mint_detection_and_fee_units(self):
        rpc = FakeSolanaRpc()
        scanner = SolanaScanner(rpc=rpc, candidate_mints=[MINT, MINT])
        observations = scanner.scan()
        self.assertEqual(len(observations), 1)
        item = observations[0]
        self.assertEqual(item.chain_id, "solana-mainnet-beta")
        self.assertEqual(item.nft_standard, "Metaplex Token Metadata")
        self.assertEqual(item.native_gas_token, "SOL")
        self.assertEqual(item.project_name, "Example NFT")
        self.assertTrue(item.details["metadata"]["is_mutable"])
        self.assertEqual(item.details["metadata"]["creators"][0]["address"], b58encode(bytes([2]) * 32))
        self.assertEqual(item.details["metadata"]["collection"]["key"], b58encode(bytes([3]) * 32))
        self.assertEqual(scanner.estimate_fee("AQID"), 5000)
        self.assertEqual(len([call for call in rpc.calls if call[0] == "getAccountInfo"]), 2)

    def test_solana_wallet_balance_is_mainnet_verified_and_in_lamports_and_sol(self):
        rpc = FakeSolanaRpc()
        scanner = SolanaScanner(rpc=rpc)
        self.assertEqual(scanner.get_wallet_balance(MINT), {
            "lamports": 1_234_567_890, "sol": "1.23456789", "commitment": "confirmed",
        })
        self.assertEqual([call[0] for call in rpc.calls], ["getGenesisHash", "getBalance"])
        with self.assertRaises(ValueError):
            scanner.get_wallet_balance("not-a-key")
        with self.assertRaises(ValueError):
            SolanaScanner(rpc=FakeSolanaRpc(genesis_hash="devnet")).get_wallet_balance(MINT)

    def test_solana_rpc_failure_and_base58(self):
        scanner = SolanaScanner(rpc=FakeSolanaRpc(fail=True), candidate_mints=[MINT])
        with self.assertRaises(RuntimeError):
            scanner.scan()
        self.assertEqual(b58encode(b58decode(MINT)), MINT)

    def test_solana_rpc_retries_http_429_with_bounded_retry_after(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def read(self): return b'{"jsonrpc":"2.0","id":2,"result":"mainnet"}'

        rate_limit = HTTPError("https://rpc.invalid", 429, "rate limited", {"Retry-After": "0"}, io.BytesIO(b""))
        client = SolanaRpc("https://rpc.invalid", retries=4)
        with patch("app.discovery.solana_scanner.urlopen", side_effect=[rate_limit, Response()]) as opener:
            with patch("app.discovery.solana_scanner.time.sleep", return_value=None):
                self.assertEqual(client.call("getGenesisHash", []), "mainnet")
        self.assertEqual(opener.call_count, 2)
        self.assertEqual(client.requests_made, 2)

    def test_solana_rejects_non_mainnet_rpc(self):
        rpc = FakeSolanaRpc(genesis_hash="devnet")
        with self.assertRaises(ValueError):
            SolanaScanner(rpc=rpc, candidate_mints=[MINT]).scan()

    def test_solana_das_discovers_indexed_token_metadata_and_core_assets(self):
        das = FakeDasRpc()
        scanner = SolanaScanner(rpc=FakeSolanaRpc(), das_rpc=das)
        assets = scanner.scan()
        self.assertEqual(len(assets), 1)
        self.assertEqual(assets[0].project_name, "Indexed NFT")
        self.assertEqual(assets[0].risk_assessment, "high")
        self.assertEqual(len([call for call in das.calls if call[0] == "searchAssets"]), 6)
        self.assertTrue(scanner.inspect_core_asset(MINT)["supported"])

    def test_solana_das_rejects_non_mainnet_source(self):
        scanner = SolanaScanner(rpc=FakeSolanaRpc(), das_rpc=FakeDasRpc(genesis_hash="devnet"))
        self.assertEqual(scanner.scan(), [])
        self.assertEqual(scanner.source_status["metaplex-das"]["status"], "unavailable")
        self.assertEqual(scanner.source_status["metaplex-das"]["error_type"], "ValueError")

    def test_magic_eden_launchpad_only_emits_future_solana_as_unknown(self):
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        records = [
            {"chainId": "solana", "contractAddress": MINT, "name": "Future listing",
             "launchDatetime": "2026-01-02T00:00:00Z", "price": 0, "size": 50},
            {"chainId": "solana", "contractAddress": b58encode(bytes([4]) * 32), "name": "Ended",
             "launchDatetime": "2025-12-31T00:00:00Z", "price": 1},
            {"chainId": "ethereum", "contractAddress": ADDR, "name": "Wrong chain",
             "launchDatetime": "2026-01-02T00:00:00Z", "price": 0},
        ]

        class Response:
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def read(self): return json.dumps(records).encode()

        source = MagicEdenLaunchpadSource(now=lambda: now)
        with patch("app.discovery.solana_scanner.urlopen", return_value=Response()) as opener:
            found = source.fetch()
        opener.assert_called_once()
        self.assertEqual(source.last_status, {"status": "ok", "fetched": 3, "solana": 2, "upcoming": 1})
        self.assertEqual(len(found), 1)
        item = found[0]
        self.assertEqual(item.contract_address, MINT)
        self.assertEqual(item.mint_price, None)
        self.assertEqual(item.details["price_claim_status"], "ZERO_PRICE_UNVERIFIED")
        self.assertEqual(item.details["mint_price_status"], "UNKNOWN")
        self.assertEqual(item.details["opportunity_status"], "UPCOMING")
        self.assertEqual(item.details["mint_schedule"]["status"], "UPCOMING_UNVERIFIED")
        self.assertEqual(item.details["announcement_status"], "UNKNOWN")
        self.assertEqual(item.details["allowlist_status"], "UNKNOWN")
        self.assertEqual(item.details["address_kind"], "unknown")
        self.assertEqual(item.details["classification"], "UNKNOWN")
        self.assertEqual(item.details["eligibility"]["status"], "UNKNOWN")
        self.assertIsNone(item.details["official_source_url"])
        self.assertEqual(item.details["priority_fee"]["status"], "UNKNOWN")
        self.assertFalse(item.details["telegram_qualified"])

    def test_launchpad_address_classifier_distinguishes_nft_collection_and_machine(self):
        from app.discovery.solana_scanner import CORE_CANDY_MACHINE_PROGRAM, SPL_TOKEN_PROGRAM

        address = b58encode(bytes([7]) * 32)

        class Rpc:
            owner = SPL_TOKEN_PROGRAM

            def call(self, method, _params):
                if method == "getAccountInfo":
                    return {"value": {"owner": self.owner,
                                       "data": {"parsed": {"info": {"decimals": 0}}}}}
                if method == "getTokenSupply":
                    return {"value": {"amount": "1"}}
                raise AssertionError("unexpected RPC method")

        rpc = Rpc()
        scanner = SolanaScanner(rpc=rpc)
        scanner._fetch_token_metadata = lambda _address: {"mint": address}
        self.assertEqual(scanner.classify_candidate_address(address)["kind"], "nft_mint")
        scanner._fetch_token_metadata = lambda _address: {"mint": address, "collection_details": {"version": "V1"}}
        self.assertEqual(scanner.classify_candidate_address(address)["kind"], "collection_mint")
        rpc.owner = CORE_CANDY_MACHINE_PROGRAM
        scanner.inspect_core_candy_machine = lambda _address: ProjectObservation(
            chain_id="solana-mainnet-beta", contract_address=address, source_url="https://rpc.invalid",
            observed_at="now", nft_standard="Metaplex Core Candy Machine")
        self.assertEqual(scanner.classify_candidate_address(address)["kind"], "core_candy_machine")

    def test_launchpad_rejects_historical_nft_mint_as_mint_infrastructure(self):
        from app.notifications.solana_alerts import is_new_solana_discovery
        future = ProjectObservation(
            chain_id="solana-mainnet-beta", contract_address=MINT, source_url="https://launchpad.invalid",
            observed_at="2026-01-01T00:00:00+00:00", project_name="Future listing",
            details={"discovery_status": "DISCOVERED", "mint_status": "upcoming",
                     "address_validation_status": "PENDING_ONCHAIN_CLASSIFICATION"})

        class Source:
            last_status = {"upcoming": 1}
            requests_made = 1
            def fetch(self): return [future]

        scanner = SolanaScanner(rpc=FakeSolanaRpc(), launchpad_source=Source())
        scanner.classify_candidate_address = lambda _address: {
            "kind": "nft_mint", "verified": True, "reason": "single_supply_metaplex_nft_mint_not_a_candy_machine"}
        found = scanner.scan()
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].details["address_validation_status"], "REJECTED_HISTORICAL_NFT_MINT")
        self.assertFalse(found[0].details["mint_infrastructure_verified"])
        self.assertFalse(is_new_solana_discovery(found[0]))

    def test_magic_eden_launchpad_has_bounded_retry_budget(self):
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)

        class Response:
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def read(self): return b"[]"

        rate_limit = HTTPError("https://api.invalid", 429, "rate limited", {}, io.BytesIO(b""))
        source = MagicEdenLaunchpadSource(now=lambda: now, max_attempts=2, retry_delay=0)
        with patch("app.discovery.solana_scanner.urlopen", side_effect=[rate_limit, Response()]) as opener:
            self.assertEqual(source.fetch(), [])
        self.assertEqual(opener.call_count, 2)
        self.assertEqual(source.requests_made, 2)

    def test_magic_eden_can_enforce_campaign_response_size_bound(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def read(self, limit): return b"x" * limit

        source = MagicEdenLaunchpadSource(max_attempts=1, max_response_bytes=32)
        with patch("app.discovery.solana_scanner.urlopen", return_value=Response()) as opener:
            with self.assertRaisesRegex(RuntimeError, "source unavailable"):
                source.fetch()
        opener.assert_called_once()
        self.assertEqual(source.last_status["error_type"], "ValueError")
        self.assertEqual(source.last_status["status"], "unavailable")

    def test_solana_scan_persists_deduplicable_launchpad_observation(self):
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        observation = ProjectObservation("solana-mainnet-beta", MINT, "https://source.invalid", now.isoformat(),
                                         project_name="Upcoming", details={"mint_price_status": "UNKNOWN"})

        class Source:
            def fetch(self): return [observation, observation]

        scanner = SolanaScanner(rpc=FakeSolanaRpc(), launchpad_source=Source())
        found = scanner.scan()
        self.assertEqual(len(found), 1)
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "solana.sqlite3")
            self.assertTrue(database.save_observation(found[0]))
            self.assertFalse(database.save_observation(found[0]))

    def test_solana_source_health_is_visible_and_persisted_by_scheduler(self):
        class Source:
            last_status = {"upcoming": 0}
            requests_made = 1
            def fetch(self): return []

        scanner = SolanaScanner(rpc=FakeSolanaRpc(), launchpad_source=Source())
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "source_health.sqlite3")
            ScanScheduler([scanner], database, max_workers=1).scan_once()
            health = {row["source_name"]: row for row in database.get_source_health("solana-mainnet-beta")}
        self.assertEqual(health["solana-mainnet-rpc"]["status"], "ok")
        self.assertEqual(health["metaplex-das"]["status"], "not_configured")
        self.assertEqual(health["magiceden-launchpad"]["status"], "ok")
        self.assertEqual(health["magiceden-launchpad"]["requests"], 1)

    def test_core_candy_machine_known_address_parser_is_owner_and_discriminator_gated(self):
        machine_address = b58encode(bytes([8]) * 32)
        mint_authority = b58encode(bytes([2]) * 32)
        collection = b58encode(bytes([3]) * 32)
        raw = bytearray(128)
        raw[:8] = CORE_CANDY_MACHINE_DISCRIMINATOR
        raw[8], raw[9] = 1, 0
        raw[16:48] = bytes([1]) * 32
        raw[48:80] = bytes([2]) * 32
        raw[80:112] = bytes([3]) * 32
        struct.pack_into("<Q", raw, 112, 7)
        struct.pack_into("<Q", raw, 120, 20)
        encoded = base64.b64encode(raw).decode()

        class Rpc:
            endpoint = "https://rpc.example.invalid"
            def __init__(self, owner=CORE_CANDY_MACHINE_PROGRAM, discriminator=True):
                self.owner, self.discriminator = owner, discriminator
                self.calls = []
            def call(self, method, params):
                self.calls.append((method, params))
                address = params[0]
                if method == "getAccountInfo" and address == machine_address:
                    account_raw = raw if self.discriminator else b"x" * 128
                    return {"value": {"owner": self.owner, "data": [base64.b64encode(account_raw).decode(), "base64"]}}
                if method == "getAccountInfo" and address == mint_authority:
                    guard = bytearray(85)
                    guard[:8] = CORE_CANDY_GUARD_DISCRIMINATOR
                    return {"value": {"owner": CORE_CANDY_GUARD_PROGRAM, "data": [base64.b64encode(guard).decode(), "base64"]}}
                return {"value": None}

        observation = SolanaScanner(rpc=Rpc()).inspect_core_candy_machine(machine_address)
        self.assertEqual(observation.nft_standard, "Metaplex Core Candy Machine")
        self.assertEqual(observation.details["collection_address"], collection)
        self.assertEqual(observation.details["machine"]["items_remaining"], 13)
        self.assertTrue(observation.details["machine"]["candy_guard_present"])
        self.assertEqual(observation.details["price_status"], "PRICE_VERIFIED_FREE")
        self.assertEqual(observation.details["machine"]["guard_config_status"], "VERIFIED_EMPTY")
        self.assertEqual(observation.details["eligibility_status"], "ELIGIBILITY_UNKNOWN")
        self.assertIsNone(observation.details["mint_address"])
        self.assertIsNone(SolanaScanner(rpc=Rpc(owner="11111111111111111111111111111111")).inspect_core_candy_machine(machine_address))
        self.assertIsNone(SolanaScanner(rpc=Rpc(discriminator=False)).inspect_core_candy_machine(machine_address))

    def test_binance_wallet_does_not_infer_nft_mint_from_chain_support(self):
        for network in ("bnb", "ethereum", "base", "solana"):
            self.assertTrue(BINANCE_AGENTIC_CAPABILITIES[network].chain_supported)
            self.assertIsNone(BINANCE_AGENTIC_CAPABILITIES[network].nft_mint_supported)
            self.assertFalse(can_use_for_nft_mint(network))
        self.assertIsNone(BINANCE_AGENTIC_CAPABILITIES["robinhood"].chain_supported)
        self.assertFalse(can_use_for_nft_mint("robinhood"))

    def test_solana_database_keeps_case_sensitive_mint_addresses(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "assets.sqlite3")
            upper = ProjectObservation("solana-mainnet-beta", "AbcMint", "https://rpc.invalid", "now")
            lower = ProjectObservation("solana-mainnet-beta", "abcmint", "https://rpc.invalid", "now")
            self.assertTrue(database.save_observation(upper))
            self.assertTrue(database.save_observation(lower))

    def test_scheduler_persists_scan_history_and_deduplicates(self):
        class Adapter:
            spec = CHAIN_SPECS["ethereum"]

            def scan(self):
                return [ProjectObservation(1, ADDR, "https://rpc.invalid", "now", native_gas_token="ETH")]

        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "scan.sqlite3")
            scheduler = ScanScheduler([Adapter()], database, max_workers=1)
            self.assertEqual(scheduler.scan_once(), {"Ethereum": 1})
            self.assertEqual(scheduler.scan_once(), {"Ethereum": 0})
            connection = database.connect()
            try:
                rows = connection.execute("SELECT status, observations FROM scan_history ORDER BY id").fetchall()
                self.assertEqual([(row["status"], row["observations"]) for row in rows], [("complete", 1), ("complete", 1)])
            finally:
                connection.close()

    def test_scheduler_persists_evm_cursor_between_cycles(self):
        class CursorAdapter:
            spec = CHAIN_SPECS["ethereum"]
            last_scanned_block = None

            def __init__(self):
                self.starts = []

            def scan(self, from_block=None):
                self.starts.append(from_block)
                self.last_scanned_block = 100 if from_block is None else from_block + 100
                return []

        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "cursor.sqlite3")
            adapter = CursorAdapter()
            scheduler = ScanScheduler([adapter], database, max_workers=1)
            scheduler.scan_once()
            scheduler.scan_once()
            self.assertEqual(adapter.starts, [None, 100])
            self.assertEqual(database.get_scan_cursor("1"), 200)

    def test_legacy_database_schema_migrates_without_data_loss(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.sqlite3"
            connection = sqlite3.connect(path)
            connection.executescript("""
                CREATE TABLE observations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, chain_id INTEGER NOT NULL,
                    contract_address TEXT NOT NULL, project_name TEXT, source_url TEXT NOT NULL,
                    observed_at TEXT NOT NULL, verified INTEGER NOT NULL DEFAULT 0,
                    details_json TEXT NOT NULL DEFAULT '{}', UNIQUE(chain_id, contract_address)
                );
                INSERT INTO observations (chain_id, contract_address, source_url, observed_at)
                VALUES (56, '0xabc', 'https://rpc.invalid', 'old');
            """)
            connection.close()
            database = Database(path)
            connection = database.connect()
            try:
                row = connection.execute("SELECT chain_identifier, contract_address FROM observations").fetchone()
                self.assertEqual((row["chain_identifier"], row["contract_address"]), ("56", "0xabc"))
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main()
