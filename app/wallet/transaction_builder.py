"""Fail-closed construction of zero-value EVM NFT mint calls.

This module builds calldata only from a verified observation's inspected ABI.
It has no RPC, wallet, signer, or transaction submission code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from typing import Any, Mapping, Sequence

from app.discovery.project_sources import ProjectObservation


_PREVIEW_EVM_CHAINS = {"56", "1", "8453"}
_NATIVE_TOKENS = {"56": "BNB", "1": "ETH", "8453": "ETH"}
_MINT_FUNCTION_NAMES = {
    "mint", "publicmint", "whitelistmint", "allowlistmint", "freemint",
    "safemint", "mintto", "mintpublic", "mintwhitelist", "mintallowlist",
    "mintnft", "mintbatch", "mintmany",
}
_EVM_ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")
_HEX = re.compile(r"^[0-9a-fA-F]*$")


class UnsafeMintRequest(ValueError):
    """A candidate failed a deterministic transaction policy check."""


@dataclass(frozen=True)
class UnsignedEvmMint:
    """Unsigned, zero-value EVM transaction prepared locally for preview."""

    chain_id: str
    from_address: str = field(repr=False)
    contract_address: str = field(repr=False)
    function_signature: str
    calldata: str = field(repr=False)
    value_wei: str
    mint_price_wei: str
    transaction_digest: str
    native_gas_token: str
    simulated: bool = False


class MintTransactionBuilder:
    """Build only verified, eligible, deterministic zero-cost EVM mint calldata."""

    def build_evm_mint(
        self,
        observation: ProjectObservation,
        wallet_address: str,
        function_name: str,
        arguments: Mapping[str, Any] | Sequence[Any] = (),
    ) -> UnsignedEvmMint:
        chain_id = str(observation.chain_id)
        if chain_id not in _PREVIEW_EVM_CHAINS:
            raise UnsafeMintRequest("Binance EVM mint preview is not enabled for this chain")
        if observation.nft_standard.upper() not in {"ERC-721", "ERC-1155"}:
            raise UnsafeMintRequest("Only identified ERC-721 or ERC-1155 contracts can be prepared")
        if observation.verified is not True or observation.verification_status.lower() != "verified":
            raise UnsafeMintRequest("Verified contract source is required")
        if observation.risk_assessment.lower() != "low":
            raise UnsafeMintRequest("Deterministic low-risk assessment is required")
        if observation.eligibility.lower() != "eligible":
            raise UnsafeMintRequest("Wallet eligibility must be confirmed")
        price = observation.mint_price
        if price is None:
            price = observation.details.get("mint_price")
        try:
            mint_price = Decimal(str(price))
        except (InvalidOperation, TypeError, ValueError):
            raise UnsafeMintRequest("A verified numeric mint price is required") from None
        if not mint_price.is_finite() or mint_price != 0:
            raise UnsafeMintRequest("Only zero-price mints can be prepared")
        if observation.native_gas_token and observation.native_gas_token.upper() != _NATIVE_TOKENS[chain_id]:
            raise UnsafeMintRequest("Native gas token does not match the selected chain")
        if not _EVM_ADDRESS.fullmatch(wallet_address):
            raise UnsafeMintRequest("A valid wallet address for the selected chain is required")
        if not _EVM_ADDRESS.fullmatch(observation.contract_address):
            raise UnsafeMintRequest("A valid NFT contract address is required")

        abi_functions = observation.details.get("mint_functions")
        if not isinstance(abi_functions, list):
            raise UnsafeMintRequest("Verified ABI mint-function evidence is required")
        selected = [
            item for item in abi_functions
            if isinstance(item, dict)
            and item.get("name") == function_name
            and _normalize_function_name(str(item.get("name", ""))) in _MINT_FUNCTION_NAMES
        ]
        if len(selected) != 1:
            raise UnsafeMintRequest("The requested function is not a unique inspected mint function")
        function = selected[0]
        if function.get("stateMutability") not in {"nonpayable", "payable"}:
            raise UnsafeMintRequest("Mint ABI must identify a state-changing function")
        inputs = function.get("inputs", [])
        if not isinstance(inputs, list):
            raise UnsafeMintRequest("Mint function ABI inputs are invalid")
        signature = _canonical_signature(function_name, inputs)
        encoded_args = _encode_arguments(inputs, arguments)
        selector = _keccak256(signature.encode("ascii"))[:4].hex()
        calldata = "0x" + selector + encoded_args
        digest = compute_evm_transaction_digest(
            chain_id, wallet_address, observation.contract_address, "0", calldata,
        )
        return UnsignedEvmMint(
            chain_id=chain_id,
            from_address=wallet_address,
            contract_address=observation.contract_address,
            function_signature=signature,
            calldata=calldata,
            value_wei="0",
            mint_price_wei="0",
            transaction_digest=digest,
            native_gas_token=_NATIVE_TOKENS[chain_id],
        )


def validate_prepared_evm_mint(transaction: UnsignedEvmMint) -> None:
    """Recheck immutable preview parameters before crossing the CLI boundary."""
    if transaction.chain_id not in _PREVIEW_EVM_CHAINS:
        raise UnsafeMintRequest("Binance EVM mint preview is not enabled for this chain")
    if transaction.value_wei != "0" or transaction.mint_price_wei != "0":
        raise UnsafeMintRequest("Only zero-value, zero-price mint previews are allowed")
    if not _EVM_ADDRESS.fullmatch(transaction.from_address) or not _EVM_ADDRESS.fullmatch(transaction.contract_address):
        raise UnsafeMintRequest("A prepared mint has invalid address fields")
    match = re.fullmatch(r"([A-Za-z][A-Za-z0-9_]*)\((.*)\)", transaction.function_signature)
    if not match or _normalize_function_name(match.group(1)) not in _MINT_FUNCTION_NAMES:
        raise UnsafeMintRequest("Only allowlisted mint function names can be previewed")
    expected_selector = _keccak256(transaction.function_signature.encode("ascii"))[:4].hex()
    calldata = transaction.calldata[2:] if transaction.calldata.startswith("0x") else ""
    types = match.group(2).split(",") if match.group(2) else []
    if any(not _is_supported_static_type(abi_type) for abi_type in types):
        raise UnsafeMintRequest("Prepared mint uses an unsupported ABI input type")
    if (not calldata.startswith(expected_selector) or not _HEX.fullmatch(calldata)
            or len(calldata) != 8 + 64 * len(types)):
        raise UnsafeMintRequest("Prepared calldata does not match the approved mint signature")
    expected_digest = compute_evm_transaction_digest(
        transaction.chain_id, transaction.from_address, transaction.contract_address,
        transaction.value_wei, transaction.calldata,
    )
    if transaction.transaction_digest != expected_digest:
        raise UnsafeMintRequest("Prepared transaction parameters changed after review")


def compute_evm_transaction_digest(
    chain_id: str, from_address: str, contract_address: str, value_wei: str, calldata: str,
) -> str:
    canonical = json.dumps({
        "chain_id": str(chain_id),
        "from": str(from_address).lower(),
        "to": str(contract_address).lower(),
        "value_wei": str(value_wei),
        "calldata": str(calldata).lower(),
    }, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _normalize_function_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _canonical_signature(name: str, inputs: list[Any]) -> str:
    types: list[str] = []
    for item in inputs:
        if not isinstance(item, dict) or not isinstance(item.get("type"), str):
            raise UnsafeMintRequest("Mint function ABI contains an invalid input")
        types.append(_canonical_abi_type(item["type"]))
    return f"{name}({','.join(types)})"


def _canonical_abi_type(abi_type: str) -> str:
    # Solidity ABI canonical signatures expand the uint alias to uint256.
    return "uint256" if abi_type == "uint" else abi_type


def _encode_arguments(inputs: list[Any], arguments: Mapping[str, Any] | Sequence[Any]) -> str:
    if isinstance(arguments, Mapping):
        names = [str(item.get("name", "")) for item in inputs]
        if any(not name for name in names) or len(set(names)) != len(names) or set(arguments) != set(names):
            raise UnsafeMintRequest("Mint arguments must match the ABI input names exactly")
        values = [arguments[name] for name in names]
    elif isinstance(arguments, Sequence) and not isinstance(arguments, (str, bytes, bytearray)):
        if len(arguments) != len(inputs):
            raise UnsafeMintRequest("Mint argument count does not match the verified ABI")
        values = list(arguments)
    else:
        raise UnsafeMintRequest("Mint arguments must be a mapping or sequence")
    return "".join(_encode_static_word(str(item["type"]), value) for item, value in zip(inputs, values))


def _encode_static_word(abi_type: str, value: Any) -> str:
    if abi_type == "uint" or re.fullmatch(r"uint(?:8|16|24|32|40|48|56|64|72|80|88|96|104|112|120|128|136|144|152|160|168|176|184|192|200|208|216|224|232|240|248|256)", abi_type):
        bits = 256 if abi_type == "uint" else int(abi_type[4:])
        if isinstance(value, bool):
            raise UnsafeMintRequest("Boolean is not a valid unsigned integer argument")
        if isinstance(value, int):
            number = value
        elif isinstance(value, str) and re.fullmatch(r"[0-9]+", value):
            number = int(value)
        else:
            raise UnsafeMintRequest("Unsigned integer mint argument is invalid") from None
        if number < 0 or number >= 1 << bits:
            raise UnsafeMintRequest("Unsigned integer mint argument is out of range")
        return number.to_bytes(32, "big").hex()
    if abi_type == "address":
        if not isinstance(value, str) or not _EVM_ADDRESS.fullmatch(value):
            raise UnsafeMintRequest("Mint address argument is invalid")
        return value[2:].lower().rjust(64, "0")
    if abi_type == "bool":
        if value is True or value == "true":
            return "1".rjust(64, "0")
        if value is False or value == "false":
            return "0" * 64
        raise UnsafeMintRequest("Mint boolean argument must be true or false")
    match = re.fullmatch(r"bytes([1-9]|[12][0-9]|3[0-2])", abi_type)
    if match:
        size = int(match.group(1))
        raw = value[2:] if isinstance(value, str) and value.startswith("0x") else value
        if not isinstance(raw, str) or len(raw) != size * 2 or not _HEX.fullmatch(raw):
            raise UnsafeMintRequest("Fixed-bytes mint argument has invalid length or encoding")
        return raw.lower().ljust(64, "0")
    raise UnsafeMintRequest("Only static uint, address, bool, and bytesN mint arguments are supported")


def _is_supported_static_type(abi_type: str) -> bool:
    return (
        abi_type == "uint"
        or re.fullmatch(r"uint(?:8|16|24|32|40|48|56|64|72|80|88|96|104|112|120|128|136|144|152|160|168|176|184|192|200|208|216|224|232|240|248|256)", abi_type) is not None
        or abi_type in {"address", "bool"}
        or re.fullmatch(r"bytes(?:[1-9]|[12][0-9]|3[0-2])", abi_type) is not None
    )


# Minimal Keccak-256 implementation so selectors use Ethereum's Keccak padding,
# not hashlib.sha3_256's incompatible NIST padding. No package installation needed.
_ROTATIONS = (
    (0, 36, 3, 41, 18),
    (1, 44, 10, 45, 2),
    (62, 6, 43, 15, 61),
    (28, 55, 25, 21, 56),
    (27, 20, 39, 8, 14),
)
_ROUND_CONSTANTS = (
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A,
    0x8000000080008000, 0x000000000000808B, 0x0000000080000001,
    0x8000000080008081, 0x8000000000008009, 0x000000000000008A,
    0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089,
    0x8000000000008003, 0x8000000000008002, 0x8000000000000080,
    0x000000000000800A, 0x800000008000000A, 0x8000000080008081,
    0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
)
_MASK_64 = (1 << 64) - 1


def _keccak256(data: bytes) -> bytes:
    rate = 136
    padded = bytearray(data)
    padded.append(0x01)
    padded.extend(b"\x00" * ((rate - len(padded) % rate) % rate))
    padded[-1] |= 0x80
    state = [0] * 25
    for block_start in range(0, len(padded), rate):
        block = padded[block_start:block_start + rate]
        for i in range(rate // 8):
            state[i] ^= int.from_bytes(block[i * 8:(i + 1) * 8], "little")
        for rc in _ROUND_CONSTANTS:
            columns = [state[x] ^ state[x + 5] ^ state[x + 10] ^ state[x + 15] ^ state[x + 20] for x in range(5)]
            delta = [columns[(x - 1) % 5] ^ _rotate_left(columns[(x + 1) % 5], 1) for x in range(5)]
            for x in range(5):
                for y in range(5):
                    state[x + 5 * y] ^= delta[x]
            moved = [0] * 25
            for x in range(5):
                for y in range(5):
                    moved[y + 5 * ((2 * x + 3 * y) % 5)] = _rotate_left(state[x + 5 * y], _ROTATIONS[x][y])
            for x in range(5):
                for y in range(5):
                    state[x + 5 * y] = moved[x + 5 * y] ^ ((~moved[(x + 1) % 5 + 5 * y]) & moved[(x + 2) % 5 + 5 * y])
                    state[x + 5 * y] &= _MASK_64
            state[0] ^= rc
    return b"".join(value.to_bytes(8, "little") for value in state[:rate // 8])[:32]


def _rotate_left(value: int, count: int) -> int:
    if count == 0:
        return value & _MASK_64
    return ((value << count) | (value >> (64 - count))) & _MASK_64
