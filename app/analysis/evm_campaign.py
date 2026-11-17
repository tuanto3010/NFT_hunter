"""Bounded read-only verifier for EVM NFT mint campaigns.

Campaign profiles map semantic checks (price, availability, eligibility) to
functions in a Sourcify-verified ABI. The mapping is campaign-specific and is
never guessed from a function name. This module cannot sign or broadcast.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit

from app.discovery.chain_adapter import CHAIN_SPECS
from app.discovery.evm_scanner import EvmScanner, JsonRpcClient
from app.wallet.transaction_builder import (
    MintTransactionBuilder, UnsafeMintRequest, _encode_static_word, _keccak256,
)
from app.discovery.project_sources import ProjectObservation


class EvmCampaignVerificationError(RuntimeError):
    """Campaign evidence could not be validated safely."""


CHAINS = {str(spec.chain_id): spec for spec in CHAIN_SPECS.values() if spec.family == "evm"}
_ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")
_MINT_NAMES = {"mint", "publicmint", "whitelistmint", "allowlistmint", "freemint",
               "safemint", "mintto", "mintpublic", "mintwhitelist", "mintallowlist",
               "mintnft", "mintbatch", "mintmany"}


def _selector(signature: str) -> str:
    return _keccak256(signature.encode("ascii"))[:4].hex()


def _calldata(signature: str, types: tuple[str, ...], values: tuple[Any, ...]) -> str:
    if len(types) != len(values):
        raise EvmCampaignVerificationError("Argument count does not match the requested ABI signature")
    try:
        body = "".join(_encode_static_word(kind, value) for kind, value in zip(types, values))
    except UnsafeMintRequest as exc:
        raise EvmCampaignVerificationError("Unsupported ABI argument encoding") from exc
    return "0x" + _selector(signature) + body


def _canonical_type(abi_type: str) -> str:
    return "uint256" if abi_type == "uint" else abi_type


def _signature(item: dict[str, Any]) -> str:
    return "%s(%s)" % (item.get("name", ""), ",".join(
        _canonical_type(str(arg.get("type", ""))) for arg in item.get("inputs", [])
    ))


def _validate_address(value: str, label: str) -> str:
    if not isinstance(value, str) or not _ADDRESS.fullmatch(value):
        raise EvmCampaignVerificationError("%s must be a complete EVM address" % label)
    return value


def _validate_official_source(url: str) -> str:
    parsed = urlsplit(url.strip())
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise EvmCampaignVerificationError("Official source must be a credential-free HTTPS URL")
    return url.strip()


def _encode_call(item: dict[str, Any], args: Any, wallet: str) -> str:
    inputs = item.get("inputs", [])
    if not isinstance(args, list) or len(args) != len(inputs):
        raise EvmCampaignVerificationError("Arguments do not match the verified ABI")
    values = [wallet if value == "$wallet" else value for value in args]
    try:
        body = "".join(_encode_static_word(str(inp["type"]), val) for inp, val in zip(inputs, values))
    except (KeyError, TypeError, ValueError, UnsafeMintRequest) as exc:
        raise EvmCampaignVerificationError("Unsupported ABI argument encoding") from exc
    return "0x" + _keccak256(_signature(item).encode("ascii"))[:4].hex() + body


class EvmCampaignVerifier:
    """Verify one explicitly selected campaign and wallet at a single block."""

    def __init__(self, chain_id: int | str, rpc_url: str, *, rpc: Any = None,
                 source_inspector: Any = None) -> None:
        self.chain_id = str(chain_id)
        self.spec = CHAINS.get(self.chain_id)
        if self.spec is None:
            raise EvmCampaignVerificationError("Unsupported EVM chain")
        if not rpc_url:
            raise EvmCampaignVerificationError("RPC is not configured for the selected chain")
        self.rpc = rpc or JsonRpcClient(rpc_url, timeout=10, retries=0)
        self.source_inspector = source_inspector or EvmScanner(rpc_url, self.spec, rpc=self.rpc)

    def verify(self, *, contract_address: str, official_source_url: str,
               source_confirmed: bool, source_confirmation: str,
               wallet_address: str, mint_method: str, mint_arguments: list[Any],
               price_check: dict[str, Any], eligibility_check: dict[str, Any],
               availability_check: dict[str, Any], risk_assessment: str,
               payment_evidence: str,
               cached_source: dict[str, Any] | None = None) -> dict[str, Any]:
        """Run bounded reads and produce an unsigned transaction only on all gates.

        `source_confirmed` is an operator attestation that the URL is project-
        controlled. It is persisted as an attestation, not presented as an
        automated domain-ownership proof. `risk_assessment` must come from the
        existing record and is never changed here.
        """
        address = _validate_address(contract_address, "contract_address")
        wallet = _validate_address(wallet_address, "wallet_address")
        official_url = _validate_official_source(official_source_url)
        if not source_confirmed or len(str(source_confirmation).strip()) < 12:
            return self._blocked("DEFERRED_MISSING_EVIDENCE", address, wallet, official_url,
                                 "Operator confirmation of the project-controlled source is required")
        if len(str(payment_evidence).strip()) < 12:
            return self._blocked("DEFERRED_MISSING_EVIDENCE", address, wallet, official_url,
                                 "Manual review must confirm the verified source shows no other payment asset")
        chain = int(self.rpc.call("eth_chainId", []), 16)
        if chain != int(self.chain_id):
            raise EvmCampaignVerificationError("Configured RPC chain ID does not match selected network")
        block = int(self.rpc.call("eth_blockNumber", []), 16)
        block_tag = hex(block)
        code = self.rpc.call("eth_getCode", [address, block_tag])
        if not isinstance(code, str) or code in {"", "0x"}:
            return self._blocked("BLOCKED_UNSUPPORTED", address, wallet, official_url,
                                 "No contract bytecode exists at the selected address", block)
        code_hash = hashlib.sha256(bytes.fromhex(code[2:])).hexdigest()
        source = cached_source if isinstance(cached_source, dict) and cached_source.get("code_hash") == code_hash else None
        if source is None:
            check = self.source_inspector.check_verification(address)
            if not isinstance(check, dict) or check.get("status") != "verified" or not isinstance(check.get("abi"), list):
                return self._blocked("DEFERRED_MISSING_EVIDENCE", address, wallet, official_url,
                                     "Verified source and ABI are unavailable", block, code_hash=code_hash)
            source = {"status": "verified", "code_hash": code_hash, "abi": check["abi"]}
        functions = {_signature(item): item for item in source["abi"]
                     if isinstance(item, dict) and item.get("type") == "function"}
        admin_surface = sorted(signature for signature, item in functions.items()
                               if str(item.get("name", "")).lower().startswith((
                                   "set", "pause", "unpause", "blacklist", "upgrade", "withdraw", "rescue", "admin"
                               )))
        if len(functions) == 0:
            return self._blocked("BLOCKED_UNSUPPORTED", address, wallet, official_url,
                                 "Verified ABI contains no callable functions", block, code_hash=code_hash)

        erc165 = {}
        for label, interface_id in (("ERC721", "80ac58cd"), ("ERC1155", "d9b67a26")):
            try:
                value = self.rpc.call("eth_call", [{"to": address, "data": "0x01ffc9a7" + interface_id + "0" * 56}, block_tag])
                erc165[label] = int(value, 16) == 1
            except Exception:
                erc165[label] = "UNKNOWN"
        standard = "ERC-721" if erc165["ERC721"] is True else ("ERC-1155" if erc165["ERC1155"] is True else "UNKNOWN")
        if standard == "UNKNOWN":
            return self._blocked("BLOCKED_UNSUPPORTED", address, wallet, official_url,
                                 "ERC-721/ERC-1155 interface support is not verified", block,
                                 code_hash=code_hash, erc165=erc165)

        mint = functions.get(mint_method)
        if (mint is None or mint.get("stateMutability") not in {"payable", "nonpayable"}
                or mint.get("name", "").lower() not in _MINT_NAMES):
            return self._blocked("BLOCKED_UNSUPPORTED", address, wallet, official_url,
                                 "Requested mint method is not uniquely supported by the verified ABI", block,
                                 code_hash=code_hash, erc165=erc165, nft_standard=standard)
        if any(str(arg.get("type", "")).endswith("]") or str(arg.get("type", "")) in {"string", "bytes", "tuple"}
               for arg in mint.get("inputs", [])):
            return self._blocked("BLOCKED_UNSUPPORTED", address, wallet, official_url,
                                 "Dynamic or tuple mint arguments are not supported", block,
                                 code_hash=code_hash, erc165=erc165, nft_standard=standard)
        try:
            mint_data = _encode_call(mint, mint_arguments, wallet)
        except EvmCampaignVerificationError as exc:
            return self._blocked("BLOCKED_UNSUPPORTED", address, wallet, official_url, str(exc), block,
                                 code_hash=code_hash, erc165=erc165, nft_standard=standard)

        def read(spec: dict[str, Any], role: str) -> int | bool:
            item = functions.get(str(spec.get("method", "")))
            if item is None or item.get("stateMutability") not in {"view", "pure"}:
                raise EvmCampaignVerificationError("%s check is not a verified read-only ABI method" % role)
            outputs = item.get("outputs", [])
            if not isinstance(outputs, list) or len(outputs) != 1:
                raise EvmCampaignVerificationError("%s check has unsupported outputs" % role)
            output_type = str(outputs[0].get("type", ""))
            if output_type != "bool" and not (output_type == "uint" or re.fullmatch(r"uint(?:8|16|24|32|40|48|56|64|72|80|88|96|104|112|120|128|136|144|152|160|168|176|184|192|200|208|216|224|232|240|248|256)", output_type)):
                raise EvmCampaignVerificationError("%s check must return one bool or uint" % role)
            data = _encode_call(item, spec.get("arguments", []), wallet)
            result = self.rpc.call("eth_call", [{"to": address, "data": data}, block_tag])
            number = int(result, 16)
            return bool(number) if output_type == "bool" else number

        try:
            availability_value = read(availability_check, "availability")
            supply_evidence = None
            if availability_check.get("interpretation") == "supply_below":
                cap = availability_check.get("max_supply")
                if isinstance(availability_value, bool) or not isinstance(cap, int) or cap <= 0:
                    raise EvmCampaignVerificationError("Supply availability profile is invalid")
                supply_evidence = {"minted": availability_value, "max_supply": cap}
                available = availability_value < cap
            else:
                available = bool(availability_value)
            if not available:
                return self._blocked(
                    "REJECTED_SOLD_OUT", address, wallet, official_url,
                    "Campaign mint is currently closed or sold out", block,
                    source_code_hash=code_hash, contract_source_verified=True,
                    nft_standard=standard, erc165=erc165, mint_available=False,
                    supply=supply_evidence, risk_assessment_before=risk_assessment,
                    abi_admin_surface=admin_surface,
                    source_cache=source,
                )
            price_mode = price_check.get("interpretation")
            if price_mode == "verified_nonpayable_mint":
                # This proves only zero native-token mint price from the exact
                # verified ABI mutability. The caller must separately attest
                # source review for ERC-20 or other off-call consideration.
                if mint.get("stateMutability") != "nonpayable":
                    raise EvmCampaignVerificationError(
                        "Verified nonpayable mint price profile does not match the ABI"
                    )
                price_wei = 0
            else:
                price_wei = int(read(price_check, "price"))

            eligibility_mode = eligibility_check.get("interpretation")
            mint_simulation_passed = False
            if eligibility_mode == "exact_mint_call":
                if price_wei != 0:
                    raise EvmCampaignVerificationError(
                        "Exact mint-call eligibility mode is limited to verified zero-native-price calls"
                    )
                self.rpc.call("eth_call", [{"from": wallet, "to": address,
                                             "value": "0x0", "data": mint_data}, block_tag])
                eligible = True
                mint_simulation_passed = True
            else:
                eligible = bool(read(eligibility_check, "eligibility"))
        except Exception as exc:
            return self._blocked("DEFERRED_MISSING_EVIDENCE", address, wallet, official_url,
                                 "A campaign price, availability, or wallet eligibility read failed (%s)" % type(exc).__name__,
                                 block, code_hash=code_hash, erc165=erc165, nft_standard=standard,
                                 abi_signatures=sorted(functions))

        base = {"checked_at": datetime.now(timezone.utc).isoformat(), "chain_id": int(self.chain_id),
                "block_number": block, "contract": address, "contract_bytecode_present": True,
                "contract_source_verified": True, "source_code_hash": code_hash,
                "official_source_url": official_url, "official_source_confirmation": source_confirmation.strip(),
                "payment_evidence": payment_evidence.strip(),
                "nft_standard": standard, "erc165": erc165, "selected_wallet": wallet,
                "mint_method": mint_method, "mint_arguments": mint_arguments,
                "abi_admin_surface": admin_surface,
                "mint_price_wei": str(price_wei), "mint_price_native": str(Decimal(price_wei) / Decimal(10**18)),
                "wallet_eligible": eligible, "mint_available": available,
                "risk_assessment_before": risk_assessment, "abi_signatures": sorted(functions),
                "supply": supply_evidence,
                "source_cache": source}
        if price_wei > 0:
            return {**base, "status": "REJECTED_PAID", "detail": "Verified native mint price is nonzero",
                    "unsigned_transaction": None, "gas_limit": None, "gas_cost_wei": None}
        if not eligible or not available:
            return {**base, "status": "DEFERRED_MISSING_EVIDENCE",
                    "detail": "Wallet is ineligible or mint is not currently available",
                    "unsigned_transaction": None, "gas_limit": None, "gas_cost_wei": None}
        if str(risk_assessment).lower() != "low":
            return {**base, "status": "BLOCKED_HIGH_RISK",
                    "detail": "Existing risk assessment is not LOW; verifier does not change risk status",
                    "unsigned_transaction": None, "gas_limit": None, "gas_cost_wei": None}

        tx = {"from": wallet, "to": address,
              "value": "0x0", "data": mint_data}
        try:
            if not mint_simulation_passed:
                self.rpc.call("eth_call", [tx, block_tag])
            gas = int(self.rpc.call("eth_estimateGas", [tx]), 16)
            gas_price = int(self.rpc.call("eth_gasPrice", []), 16)
        except Exception as exc:
            return {**base, "status": "DEFERRED_MISSING_EVIDENCE",
                    "detail": "Exact zero-value mint simulation or gas estimate failed (%s)" % type(exc).__name__,
                    "unsigned_transaction": None, "gas_limit": None, "gas_cost_wei": None}
        unsigned = {"chain_id": int(self.chain_id), "chainId": int(self.chain_id), "from": wallet,
                    "to": address, "value": "0x0", "value_wei": "0", "data": mint_data,
                    "calldata": mint_data, "gas": hex(gas), "gas_limit_estimate": gas,
                    "gasPrice": hex(gas_price), "gas_price_wei": str(gas_price)}
        return {**base, "status": "VERIFIED_PREVIEWABLE", "detail": "Read-only checks passed; no transaction was signed or sent",
                "simulation": "PASS", "gas_limit": gas, "gas_price_wei": str(gas_price),
                "gas_cost_wei": str(gas * gas_price),
                "gas_cost_native": str(Decimal(gas * gas_price) / Decimal(10**18)),
                "total_cost_wei": str(price_wei + gas * gas_price),
                "total_cost_native": str(Decimal(price_wei + gas * gas_price) / Decimal(10**18)),
                "unsigned_transaction": unsigned}

    def _blocked(self, status: str, address: str, wallet: str, source_url: str,
                 detail: str, block: int | None = None, **evidence: Any) -> dict[str, Any]:
        return {"status": status, "checked_at": datetime.now(timezone.utc).isoformat(),
                "chain_id": int(self.chain_id), "block_number": block, "contract": address,
                "selected_wallet": wallet, "official_source_url": source_url, "detail": detail,
                "unsigned_transaction": None, **evidence}
