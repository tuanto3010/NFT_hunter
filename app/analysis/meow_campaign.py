"""Narrow, read-only verifier for the manually registered Meow Cards lead."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from app.discovery.chain_adapter import CHAIN_SPECS
from app.discovery.evm_scanner import EvmScanner, JsonRpcClient, RpcError
from app.wallet.transaction_builder import _encode_static_word, _keccak256
from app.analysis.evm_campaign import _calldata as _shared_calldata
from app.analysis.evm_campaign import _selector as _shared_selector


MEOW_CARDS_ADDRESS = "0xb8164afefc53910d7a74cece15a60a9e0c86089e"
MAX_SUPPLY = 10_000
MAX_PER_WALLET = 20
_EXPECTED_ABI = {
    "isAgent(address,uint256)": ("view", ["bool"]),
    "priceOf(address,uint256,uint256)": ("view", ["uint256"]),
    "totalMinted()": ("view", ["uint256"]),
    "mintedByWallet(address)": ("view", ["uint256"]),
    "mint(uint256,uint256)": ("payable", None),
}


class CampaignVerificationError(RuntimeError):
    """Candidate evidence could not be validated safely."""


def _signature(item: dict[str, Any]) -> str:
    types = ",".join(str(arg.get("type", "")) for arg in item.get("inputs", []))
    return "%s(%s)" % (item.get("name", ""), types)


def _validate_abi(abi: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(abi, list):
        raise CampaignVerificationError("Verified source did not provide an ABI")
    functions: dict[str, dict[str, Any]] = {}
    for item in abi:
        if isinstance(item, dict) and item.get("type") == "function":
            functions[_signature(item)] = item
    for signature, (mutability, outputs) in _EXPECTED_ABI.items():
        item = functions.get(signature)
        actual_outputs = [str(output.get("type", "")) for output in item.get("outputs", [])] if item else []
        if (not item or item.get("stateMutability") != mutability
                or (outputs is not None and actual_outputs != outputs)):
            raise CampaignVerificationError("Verified ABI is missing or differs for " + signature)
    return functions


def _selector(signature: str) -> str:
    return _shared_selector(signature)


def _word(kind: str, value: Any) -> str:
    return _encode_static_word(kind, value)


def _calldata(signature: str, types: tuple[str, ...] = (), values: tuple[Any, ...] = ()) -> str:
    return _shared_calldata(signature, types, values)


def _uint_result(value: Any, label: str) -> int:
    if not isinstance(value, str) or not value.startswith("0x") or len(value) < 3:
        raise CampaignVerificationError("Invalid read-only response for " + label)
    return int(value, 16)


class MeowCardsCampaignVerifier:
    """Inspect only the explicitly selected Meow Cards BNB campaign."""

    def __init__(self, rpc_url: str, *, rpc: Any = None, source_inspector: Any = None) -> None:
        if not rpc_url:
            raise CampaignVerificationError("BNB RPC is not configured")
        self.rpc = rpc or JsonRpcClient(rpc_url, timeout=10, retries=0)
        self.source_inspector = source_inspector or EvmScanner(
            rpc_url, CHAIN_SPECS["bnb"], rpc=self.rpc,
        )

    def verify(self, wallet_address: str, agent_id_handle: str,
               cached_source: dict[str, Any] | None = None) -> dict[str, Any]:
        if len(wallet_address) != 42 or not wallet_address.lower().startswith("0x"):
            raise CampaignVerificationError("Selected BNB wallet address is invalid")
        handle = agent_id_handle.strip().lower()
        if handle.endswith(".ai"):
            handle = handle[:-3]
        if not handle or len(handle) > 128:
            raise CampaignVerificationError("Claimed Agent ID handle is unavailable")

        chain_id = _uint_result(self.rpc.call("eth_chainId", []), "eth_chainId")
        if chain_id != 56:
            raise CampaignVerificationError("Configured RPC is not BNB Smart Chain")
        block_number = _uint_result(self.rpc.call("eth_blockNumber", []), "eth_blockNumber")
        block_tag = hex(block_number)
        code = self.rpc.call("eth_getCode", [MEOW_CARDS_ADDRESS, block_tag])
        if not isinstance(code, str) or code in {"", "0x"}:
            raise CampaignVerificationError("Candidate contract has no deployed bytecode")
        code_hash = hashlib.sha256(bytes.fromhex(code[2:])).hexdigest()

        source = cached_source if isinstance(cached_source, dict) and cached_source.get("code_hash") == code_hash else None
        if source is None:
            result = self.source_inspector.check_verification(MEOW_CARDS_ADDRESS)
            if not isinstance(result, dict) or result.get("status") != "verified":
                raise CampaignVerificationError("Candidate source/ABI verification is not available")
            source = {"code_hash": code_hash, "status": "verified", "abi": result.get("abi")}
        functions = _validate_abi(source.get("abi"))
        signatures = set(functions)

        def read(signature: str, types: tuple[str, ...], values: tuple[Any, ...]) -> int:
            data = _calldata(signature, types, values)
            return _uint_result(self.rpc.call(
                "eth_call", [{"to": MEOW_CARDS_ADDRESS, "data": data}, block_tag],
            ), signature)

        agent_token_id = int.from_bytes(_keccak256(handle.encode("utf-8")), "big")
        eligible = read("isAgent(address,uint256)", ("address", "uint256"),
                        (wallet_address, agent_token_id)) == 1
        price_wei = read("priceOf(address,uint256,uint256)", ("address", "uint256", "uint256"),
                         (wallet_address, 1, agent_token_id))
        total_minted = read("totalMinted()", (), ())
        wallet_minted = read("mintedByWallet(address)", ("address",), (wallet_address,))
        if total_minted > MAX_SUPPLY:
            raise CampaignVerificationError("On-chain minted count exceeds the announced supply cap")

        standard = "UNKNOWN"
        interface_checks: dict[str, Any] = {}
        for label, standard_name, interface_id in (
            ("ERC721", "ERC-721", "80ac58cd"),
            ("ERC1155", "ERC-1155", "d9b67a26"),
        ):
            try:
                response = self.rpc.call("eth_call", [{
                    "to": MEOW_CARDS_ADDRESS,
                    "data": "0x01ffc9a7" + interface_id + "0" * 56,
                }, block_tag])
                supported = _uint_result(response, label) == 1
                interface_checks[label] = supported
                if supported:
                    standard = standard_name
            except Exception:
                interface_checks[label] = "UNKNOWN"

        available = total_minted < MAX_SUPPLY and wallet_minted < MAX_PER_WALLET
        simulation = "NOT_RUN"
        simulation_result = None
        gas_limit = gas_price = gas_cost = None
        unsigned_transaction = None
        if eligible and available:
            mint_sig = "mint(uint256,uint256)"
            if _selector(mint_sig) != "1b2ef1ca":
                raise CampaignVerificationError("Mint selector does not match the previously verified ABI")
            transaction = {
                "from": wallet_address,
                "to": MEOW_CARDS_ADDRESS,
                "value": hex(price_wei),
                "data": _calldata(mint_sig, ("uint256", "uint256"), (1, agent_token_id)),
            }
            simulation_result = self.rpc.call("eth_call", [transaction, block_tag])
            simulation = "PASS"
            gas_limit = _uint_result(self.rpc.call("eth_estimateGas", [transaction]), "eth_estimateGas")
            gas_price = _uint_result(self.rpc.call("eth_gasPrice", []), "eth_gasPrice")
            gas_cost = gas_limit * gas_price
            unsigned_transaction = {
                "chain_id": 56,
                "chainId": 56,
                "from": wallet_address,
                "to": MEOW_CARDS_ADDRESS,
                "value": hex(price_wei),
                "value_wei": str(price_wei),
                "data": transaction["data"],
                "calldata": transaction["data"],
                "gas": hex(gas_limit),
                "gas_limit_estimate": gas_limit,
                "gasPrice": hex(gas_price),
                "gas_price_wei": str(gas_price),
            }

        checked_at = datetime.now(timezone.utc).isoformat()
        return {
            "status": "READ_ONLY_VERIFIED",
            "checked_at": checked_at,
            "chain_id": 56,
            "block_number": block_number,
            "contract": MEOW_CARDS_ADDRESS,
            "contract_bytecode_present": True,
            "contract_source_verified": True,
            "abi_signatures": sorted(signatures),
            "nft_standard": standard,
            "erc165": interface_checks,
            "selected_wallet": wallet_address,
            "agent_token_id": str(agent_token_id),
            "agent_eligible": eligible,
            "agent_id_claimed": True,
            "quantity": 1,
            "mint_price_wei": str(price_wei),
            "mint_price_bnb": str(Decimal(price_wei) / Decimal(10**18)),
            "total_minted": total_minted,
            "max_supply": MAX_SUPPLY,
            "wallet_minted": wallet_minted,
            "max_per_wallet": MAX_PER_WALLET,
            "availability": "SIMULATION_PASS" if simulation == "PASS" else "BLOCKED",
            "simulation": simulation,
            "simulation_result": simulation_result,
            "simulation_calldata_matches_unsigned": bool(
                unsigned_transaction and unsigned_transaction["data"] == transaction["data"]
            ) if eligible and available else False,
            "gas_limit": gas_limit,
            "gas_price_wei": str(gas_price) if gas_price is not None else None,
            "gas_cost_wei": str(gas_cost) if gas_cost is not None else None,
            "gas_cost_bnb": str(Decimal(gas_cost) / Decimal(10**18)) if gas_cost is not None else None,
            "total_cost_wei": str(price_wei + gas_cost) if gas_cost is not None else None,
            "total_cost_bnb": str(Decimal(price_wei + gas_cost) / Decimal(10**18)) if gas_cost is not None else None,
            "unsigned_transaction": unsigned_transaction,
            "source_cache": {"code_hash": code_hash, "status": "verified", "abi": source["abi"]},
        }
