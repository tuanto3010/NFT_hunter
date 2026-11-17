"""Wallet-scoped, read-only OpenSea Drop checks.

OpenSea's marketing/drop data remains a claim. Eligibility requires a scoped
wallet JWT whose configured address is pinned to the selected public address.
This module never signs or sends a blockchain transaction.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, Request, build_opener

from app.discovery.evm_scanner import JsonRpcClient
from app.wallet.transaction_builder import _keccak256

API_BASE = "https://api.opensea.io/api/v2/drops"
DETAILS_DOC = "https://docs.opensea.io/reference/get_drop_by_slug"
ELIGIBILITY_DOC = "https://docs.opensea.io/reference/get_drop_eligibility"
BUILD_DOC = "https://docs.opensea.io/reference/build_drop_mint_transaction"
ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,199}$")
ETH_SEADROP_V1 = "0x00005EA00Ac477B1030CE78506496e8C2dE24bf5"
ZERO_ADDRESS = "0x" + "0" * 40
MAX_RESPONSE_BYTES = 1_000_000


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str,
                         headers: Any, newurl: str) -> None:
        return None


class OpenSeaDropError(RuntimeError):
    def __init__(self, category: str, http_status: int | None = None):
        self.category = category
        self.http_status = http_status
        super().__init__(category)


class OpenSeaDropClient:
    """No-retry API client with a hard per-check request and response budget."""

    def __init__(self, api_key: str, wallet_jwt: str = "", timeout: float = 10.0,
                 opener: Callable[..., Any] | None = None):
        if not api_key.strip():
            raise OpenSeaDropError("API_KEY_NOT_CONFIGURED")
        self.api_key = api_key.strip()
        self.wallet_jwt = wallet_jwt.strip()
        self.timeout = min(max(float(timeout), 1.0), 15.0)
        self.opener = opener or build_opener(_NoRedirect()).open
        self.requests_made = 0

    def request(self, method: str, path: str, body: dict[str, Any] | None = None,
                wallet_auth: bool = False) -> tuple[int, dict[str, Any]]:
        if self.requests_made >= 3:
            raise OpenSeaDropError("REQUEST_BUDGET_EXHAUSTED")
        if not path.startswith("/") or ".." in path or "?" in path or "#" in path:
            raise OpenSeaDropError("INVALID_API_PATH")
        headers = {"X-API-KEY": self.api_key, "Accept": "application/json"}
        if wallet_auth:
            if not self.wallet_jwt:
                raise OpenSeaDropError("WALLET_JWT_NOT_CONFIGURED")
            headers["Authorization"] = "Bearer " + self.wallet_jwt
        data = None
        if body is not None:
            data = json.dumps(body, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = Request(API_BASE + path, data=data, headers=headers, method=method)
        self.requests_made += 1
        try:
            with self.opener(req, timeout=self.timeout) as response:
                status = int(response.status)
                raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise OpenSeaDropError("RESPONSE_TOO_LARGE", status)
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                raise OpenSeaDropError("UNSUPPORTED_RESPONSE_SCHEMA", status)
            return status, payload
        except HTTPError as exc:
            status = int(exc.code)
            exc.close()
            raise OpenSeaDropError("HTTP_ERROR", status) from None
        except OpenSeaDropError:
            raise
        except (URLError, TimeoutError, OSError):
            raise OpenSeaDropError("TRANSPORT_ERROR") from None
        except (UnicodeError, json.JSONDecodeError):
            raise OpenSeaDropError("INVALID_JSON") from None

    def get_details(self, slug: str) -> tuple[int, dict[str, Any]]:
        return self.request("GET", "/" + quote(slug, safe=""))

    def get_eligibility(self, slug: str) -> tuple[int, dict[str, Any]]:
        return self.request("GET", "/" + quote(slug, safe="") + "/eligibility", wallet_auth=True)

    def build_mint(self, slug: str, minter: str, quantity: int = 1) -> tuple[int, dict[str, Any]]:
        return self.request("POST", "/" + quote(slug, safe="") + "/mint",
                            {"minter": minter, "quantity": quantity})


def _stage_time(stage: dict[str, Any], field: str) -> datetime | None:
    value = stage.get(field)
    if not isinstance(value, str) or len(value) > 80:
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if result.tzinfo is None:
        return None
    return result.astimezone(timezone.utc)


def _selected_eligibility(payload: dict[str, Any], stage_id: str) -> tuple[bool | None, int | None]:
    stages = payload.get("stages")
    if not isinstance(stages, list):
        return None, None
    matches = [s for s in stages if isinstance(s, dict)
               and str(s.get("uuid") or s.get("stage_uuid") or "") == stage_id]
    if len(matches) != 1:
        return None, None
    stage = matches[0]
    remaining = stage.get("mints_remaining")
    eligible = stage.get("eligible") if isinstance(stage.get("eligible"), bool) else stage.get("is_eligible")
    if not isinstance(eligible, bool):
        return None, None
    if remaining is not None:
        try:
            remaining = int(remaining)
            if remaining < 0:
                return None, None
        except (ValueError, TypeError):
            return None, None
    # OpenSea's live schema defines null remaining for several cases (notably
    # ERC-1155 and unavailable on-chain mint counts). Only its explicit
    # is_eligible field determines eligibility; remaining supply is separate.
    if eligible is False:
        return False, 0
    return eligible, remaining


def evaluate_drop(*, slug: str, expected_contract: str, selected_address: str,
                  chain_id: str, api_key: str, wallet_jwt: str = "",
                  pinned_wallet_address: str = "", now: datetime | None = None,
                  client: OpenSeaDropClient | None = None) -> dict[str, Any]:
    """Fetch details and, when safely pinned, wallet eligibility (max 2 requests)."""
    if not SLUG_RE.fullmatch(slug):
        raise OpenSeaDropError("INVALID_DROP_SLUG")
    if not ADDRESS_RE.fullmatch(expected_contract) or not ADDRESS_RE.fullmatch(selected_address):
        raise OpenSeaDropError("INVALID_EVM_ADDRESS")
    if str(chain_id) != "1":
        raise OpenSeaDropError("UNSUPPORTED_CHAIN")
    checked_at = datetime.now(timezone.utc)
    current = (now or checked_at).astimezone(timezone.utc)
    api = client or OpenSeaDropClient(api_key, wallet_jwt)
    result: dict[str, Any] = {
        "status": "UNKNOWN", "checked_at": checked_at.isoformat(),
        "expires_at": (checked_at + timedelta(minutes=5)).isoformat(),
        "chain_id": "1", "selected_wallet": selected_address,
        "expected_contract": expected_contract, "collection_contract": None,
        "stage_id": None, "stage_type": None, "stage_status": "UNKNOWN",
        "stage_start_time": None, "stage_end_time": None,
        "api_price_wei_claim": None, "api_currency_address_claim": None,
        "stage_active": False,
        "mint_price_status": "UNKNOWN", "wallet_eligible": None,
        "mints_remaining": None, "requests_made": 0,
        "details_doc": DETAILS_DOC, "eligibility_doc": ELIGIBILITY_DOC,
        "build_doc": BUILD_DOC, "transaction": None,
    }
    # Refuse an unconfigured or differently pinned wallet before making even
    # the public drop-details request. This prevents an unrelated OpenSea
    # response from masking a wallet identity mismatch.
    if not wallet_jwt:
        result.update(status="BLOCKED_WALLET_AUTH_NOT_CONFIGURED",
                      failure_reason="OpenSea scoped wallet JWT is not configured")
        return result
    if not ADDRESS_RE.fullmatch(pinned_wallet_address) or pinned_wallet_address.lower() != selected_address.lower():
        result.update(status="BLOCKED_WALLET_IDENTITY_MISMATCH",
                      failure_reason="Configured OpenSea wallet identity is not pinned to this selected wallet")
        return result
    try:
        status, details = api.get_details(slug)
        result["details_http_status"] = status
        if status != 200:
            raise OpenSeaDropError("DETAILS_HTTP_ERROR", status)
        contract = details.get("contract_address")
        result["collection_contract"] = contract if isinstance(contract, str) and ADDRESS_RE.fullmatch(contract) else None
        result["chain_slug"] = details.get("chain") if isinstance(details.get("chain"), str) else None
        result["is_minting_claim"] = details.get("is_minting") if isinstance(details.get("is_minting"), bool) else None
        if result["collection_contract"] is None or result["collection_contract"].lower() != expected_contract.lower():
            result.update(status="BLOCKED_CONTRACT_MISMATCH", failure_reason="OpenSea details contract did not match the saved lead")
            return result
        if result["chain_slug"] != "ethereum":
            result.update(status="BLOCKED_CHAIN_MISMATCH", failure_reason="OpenSea details chain did not match Ethereum")
            return result
        active = details.get("active_stage") if isinstance(details.get("active_stage"), dict) else None
        stage = active or (details.get("next_stage") if isinstance(details.get("next_stage"), dict) else None)
        if stage is None:
            result.update(status="DEFERRED_MISSING_EVIDENCE", failure_reason="OpenSea returned no active or next stage")
            return result
        stage_id = stage.get("uuid")
        result.update(stage_id=str(stage_id)[:128] if stage_id else None,
                      stage_type=str(stage.get("stage_type") or stage.get("type") or "UNKNOWN")[:80],
                      stage_start_time=stage.get("start_time"), stage_end_time=stage.get("end_time"),
                      api_price_wei_claim=str(stage.get("price")) if stage.get("price") is not None else None,
                      api_currency_address_claim=stage.get("price_currency_address"))
        start, end = _stage_time(stage, "start_time"), _stage_time(stage, "end_time")
        is_active = bool(active and details.get("is_minting") is True
                         and start is not None and end is not None and start <= current <= end)
        result["stage_status"] = "ACTIVE" if is_active else "UPCOMING_OR_INACTIVE"
        result["stage_active"] = is_active
        price = stage.get("price")
        currency = str(stage.get("price_currency_address") or "").lower()
        if price is not None:
            try:
                price_int = int(str(price))
                if price_int < 0:
                    raise ValueError
                if currency == ZERO_ADDRESS and price_int == 0:
                    result["mint_price_status"] = "API_CLAIMED_ZERO_UNVERIFIED"
                elif currency == ZERO_ADDRESS:
                    result["mint_price_status"] = "API_CLAIMED_NATIVE_PAID_UNVERIFIED"
                elif currency:
                    result["mint_price_status"] = "API_CLAIMED_TOKEN_PAYMENT_UNVERIFIED"
            except (ValueError, TypeError):
                pass
        if not stage_id:
            result.update(status="DEFERRED_MISSING_EVIDENCE", failure_reason="Stage ID is unavailable")
            return result
        status, eligibility = api.get_eligibility(slug)
        result["eligibility_http_status"] = status
        if status != 200:
            raise OpenSeaDropError("ELIGIBILITY_HTTP_ERROR", status)
        eligible, remaining = _selected_eligibility(eligibility, str(stage_id))
        result["wallet_eligible"], result["mints_remaining"] = eligible, remaining
        if eligible is False or remaining == 0:
            result.update(status="INELIGIBLE", failure_reason="OpenSea reports no remaining eligible mints for this stage")
        elif eligible is None:
            result.update(status="DEFERRED_MISSING_EVIDENCE", failure_reason="Eligibility response did not contain an unambiguous matching stage and remaining quantity")
        elif not is_active:
            result.update(status="UPCOMING", failure_reason="Wallet eligibility is reported per stage, but this stage is not active yet")
        else:
            result.update(status="ELIGIBLE_ACTIVE_UNVERIFIED_PRICE",
                          failure_reason="OpenSea eligibility is wallet-scoped; price and contract risk still require independent on-chain verification")
        return result
    except OpenSeaDropError as exc:
        result.update(status="DEFERRED_MISSING_EVIDENCE", failure_reason=exc.category)
        if exc.http_status is not None:
            result["http_status"] = exc.http_status
        return result
    finally:
        result["requests_made"] = api.requests_made


def configured_credentials() -> tuple[str, str, str]:
    """Return credentials for internal use only; callers must never serialize them."""
    return (os.getenv("OPENSEA_API_KEY", "").strip(),
            os.getenv("OPENSEA_WALLET_JWT", "").strip(),
            os.getenv("OPENSEA_WALLET_ADDRESS", "").strip())


def validate_public_mint_transaction(tx: dict[str, Any], *, selected_wallet: str,
                                     expected_contract: str, expected_stage_id: str,
                                     expected_stage_type: str,
                                     expected_price_wei: int) -> dict[str, Any]:
    """Validate the documented SeaDrop V1 public mint payload for quantity 1.

    This intentionally rejects allowlist/signed/dynamic calldata. Those require
    proof/signature semantics that this read-only adapter does not infer.
    """
    data = tx.get("data")
    if not isinstance(data, str) or not re.fullmatch(r"0x[0-9a-fA-F]+", data):
        raise OpenSeaDropError("UNSUPPORTED_MINT_CALLDATA")
    if tx.get("chain") != "ethereum":
        raise OpenSeaDropError("BUILT_TRANSACTION_CHAIN_MISMATCH")
    to = tx.get("to")
    if not isinstance(to, str) or to.lower() != ETH_SEADROP_V1.lower():
        raise OpenSeaDropError("BUILT_TRANSACTION_TARGET_UNSUPPORTED")
    signature = b"mintPublic(address,address,address,uint256)"
    selector = _keccak256(signature)[:4].hex()
    raw = data[2:].lower()
    if not raw.startswith(selector) or len(raw) != 8 + 4 * 64:
        raise OpenSeaDropError("UNSUPPORTED_MINT_CALLDATA")
    words = [raw[8 + i * 64:8 + (i + 1) * 64] for i in range(4)]
    nft_contract = "0x" + words[0][-40:]
    fee_recipient = "0x" + words[1][-40:]
    minter_if_not_payer = "0x" + words[2][-40:]
    quantity = int(words[3], 16)
    if nft_contract.lower() != expected_contract.lower():
        raise OpenSeaDropError("BUILT_TRANSACTION_CONTRACT_MISMATCH")
    if fee_recipient.lower() == ZERO_ADDRESS:
        raise OpenSeaDropError("BUILT_TRANSACTION_FEE_RECIPIENT_INVALID")
    if minter_if_not_payer.lower() not in {ZERO_ADDRESS, selected_wallet.lower()}:
        raise OpenSeaDropError("BUILT_TRANSACTION_RECIPIENT_MISMATCH")
    if quantity != 1:
        raise OpenSeaDropError("BUILT_TRANSACTION_QUANTITY_MISMATCH")
    try:
        value = int(str(tx.get("value")))
    except (TypeError, ValueError):
        raise OpenSeaDropError("BUILT_TRANSACTION_VALUE_INVALID") from None
    if value != expected_price_wei:
        raise OpenSeaDropError("BUILT_TRANSACTION_VALUE_MISMATCH")
    returned_stage = tx.get("stage_uuid")
    if returned_stage is not None and str(returned_stage) != expected_stage_id:
        raise OpenSeaDropError("BUILT_TRANSACTION_STAGE_MISMATCH")
    returned_type = tx.get("stage_type")
    if returned_type is not None and str(returned_type).lower() != expected_stage_type.lower():
        raise OpenSeaDropError("BUILT_TRANSACTION_STAGE_TYPE_MISMATCH")
    return {"from": selected_wallet, "to": to, "chain_id": 1, "value_wei": str(value),
            "data": data, "quantity": 1, "recipient": selected_wallet,
            "stage_id": expected_stage_id, "stage_type": expected_stage_type}


def simulate_public_mint_transaction(transaction: dict[str, Any], rpc_url: str,
                                     *, rpc: Any = None) -> dict[str, Any]:
    """Read-only eth_call + gas estimate; performs no signing or submission."""
    client = rpc or JsonRpcClient(rpc_url, timeout=10, retries=0)
    actual_chain = int(client.call("eth_chainId", []), 16)
    if actual_chain != 1:
        raise OpenSeaDropError("RPC_CHAIN_MISMATCH")
    block = int(client.call("eth_blockNumber", []), 16)
    call = {"from": transaction["from"], "to": transaction["to"],
            "value": hex(int(transaction["value_wei"])), "data": transaction["data"]}
    client.call("eth_call", [call, hex(block)])
    gas = int(client.call("eth_estimateGas", [call]), 16)
    gas_price = int(client.call("eth_gasPrice", []), 16)
    fee = gas * gas_price
    balance = int(client.call("eth_getBalance", [transaction["from"], hex(block)]), 16)
    return {"status": "SIMULATION_PASS", "block_number": block,
            "gas_limit": gas, "gas_price_wei": str(gas_price),
            "network_fee_wei": str(fee), "mint_price_wei": transaction["value_wei"],
            "total_native_cost_wei": str(fee + int(transaction["value_wei"])),
            "wallet_balance_wei": str(balance), "balance_sufficient": balance >= fee + int(transaction["value_wei"])}


def build_and_simulate_free_public_mint(*, client: OpenSeaDropClient, slug: str,
                                       selected_wallet: str, expected_contract: str,
                                       stage: dict[str, Any], eligibility: bool | None,
                                       mints_remaining: int | None,
                                       evidence_expires_at: str,
                                       risk_assessment: str, contract_source_verified: bool,
                                       erc721_verified: bool, rpc_url: str,
                                       rpc: Any = None) -> dict[str, Any]:
    """Build only a verified, active public zero-price ERC-721 SeaDrop V1 call."""
    if str(risk_assessment).lower() != "low":
        raise OpenSeaDropError("BLOCKED_HIGH_RISK")
    if contract_source_verified is not True:
        raise OpenSeaDropError("BLOCKED_CONTRACT_SOURCE_UNVERIFIED")
    if erc721_verified is not True:
        raise OpenSeaDropError("BLOCKED_NFT_STANDARD_UNVERIFIED")
    if eligibility is not True:
        raise OpenSeaDropError("BLOCKED_WALLET_ELIGIBILITY_UNKNOWN")
    try:
        expires = datetime.fromisoformat(evidence_expires_at.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        raise OpenSeaDropError("BLOCKED_ELIGIBILITY_EVIDENCE_EXPIRED") from None
    if expires.tzinfo is None or expires.astimezone(timezone.utc) <= datetime.now(timezone.utc):
        raise OpenSeaDropError("BLOCKED_ELIGIBILITY_EVIDENCE_EXPIRED")
    if not isinstance(mints_remaining, int) or isinstance(mints_remaining, bool) or mints_remaining < 1:
        raise OpenSeaDropError("BLOCKED_WALLET_MINT_CAP_UNKNOWN_OR_EXHAUSTED")
    if str(stage.get("stage_type") or stage.get("type") or "").lower() not in {"public_sale", "public"}:
        raise OpenSeaDropError("BLOCKED_UNSUPPORTED_STAGE")
    if str(stage.get("price")) != "0" or str(stage.get("price_currency_address") or "").lower() != ZERO_ADDRESS:
        raise OpenSeaDropError("BLOCKED_PRICE_NOT_VERIFIED_ZERO")
    stage_id = str(stage.get("uuid") or "")
    if not stage_id:
        raise OpenSeaDropError("BLOCKED_STAGE_ID_MISSING")
    status, tx = client.build_mint(slug, selected_wallet, quantity=1)
    if status != 200:
        raise OpenSeaDropError("MINT_BUILDER_HTTP_ERROR", status)
    transaction = validate_public_mint_transaction(tx, selected_wallet=selected_wallet,
        expected_contract=expected_contract, expected_stage_id=stage_id,
        expected_stage_type=str(stage.get("stage_type") or stage.get("type") or ""),
        expected_price_wei=0)
    simulation = simulate_public_mint_transaction(transaction, rpc_url, rpc=rpc)
    if simulation.get("status") != "SIMULATION_PASS" or simulation.get("balance_sufficient") is not True:
        raise OpenSeaDropError("BLOCKED_SIMULATION_OR_BALANCE")
    transaction["gas_limit"] = simulation["gas_limit"]
    return {"status": "PREVIEW_READY", "transaction": transaction, "simulation": simulation}
