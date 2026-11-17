"""Separate read-only Solana NFT adapter; it never applies EVM token logic."""

import base64
import hashlib
import json
import logging
import struct
import threading
import time
from dataclasses import replace
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, Iterable, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.discovery.chain_adapter import CHAIN_SPECS, safe_rpc_source
from app.discovery.candy_guard_decoder import decode_core_candy_guard
from app.discovery.project_sources import ProjectObservation

logger = logging.getLogger(__name__)
SOLANA_MAINNET_RPC = "https://api.mainnet-beta.solana.com"
SOLANA_MAINNET_GENESIS_HASH = "5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d"
TOKEN_METADATA_PROGRAM = "metaqbxxUerdq28cj1RbAWkYQm3ybzjb6a8bt518x1s"
CORE_CANDY_MACHINE_PROGRAM = "CMACYFENjoBMHzapRXyo1JZkVS6EtaDDzkjMrmQLvr4J"
CORE_CANDY_GUARD_PROGRAM = "CMAGAKJ67e9hRZgfC5SFTbZH8MgEmtqazKXjmkaJjWTJ"
SPL_TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
CORE_CANDY_MACHINE_DISCRIMINATOR = hashlib.sha256(b"account:CandyMachine").digest()[:8]
MAX_SOLANA_CANDIDATES = 50
MAGIC_EDEN_LAUNCHPAD_API = "https://api-mainnet.magiceden.dev/v2/launchpad/collections"
MAGIC_EDEN_LAUNCHPAD_SOURCE = "https://docs.magiceden.io/reference/get_launchpad-collections"


class MagicEdenLaunchpadSource:
    """Read-only upcoming launch index; provider claims are never treated as proof."""

    name = "magiceden-solana-launchpad"

    def __init__(self, timeout: float = 12.0, limit: int = 500, now=None,
                 max_attempts: int = 3, retry_delay: float = 0.25,
                 max_response_bytes: Optional[int] = None) -> None:
        self.timeout = timeout
        self.limit = min(max(1, limit), 500)
        self._now = now or (lambda: datetime.now(timezone.utc))
        self.max_attempts = min(max(1, max_attempts), 3)
        self.retry_delay = min(max(0.0, retry_delay), 2.0)
        self.max_response_bytes = (max(1, int(max_response_bytes))
                                   if max_response_bytes is not None else None)
        self._request_lock = threading.Lock()
        self._last_request = 0.0
        self.requests_made = 0
        self.last_status: Dict[str, Any] = {"status": "not_scanned", "fetched": 0, "solana": 0, "upcoming": 0}

    def fetch(self) -> list[ProjectObservation]:
        """Fetch one bounded page and retain only future-dated Solana launches."""
        url = MAGIC_EDEN_LAUNCHPAD_API + "?" + urlencode({"offset": 0, "limit": self.limit})
        request = Request(url, headers={"Accept": "application/json", "User-Agent": "BinanceNFTHunter/0.1"})
        self.requests_made = 0
        records = None
        last_error: Optional[Exception] = None
        for attempt in range(self.max_attempts):
            try:
                with self._request_lock:
                    delay = 0.5 - (time.monotonic() - self._last_request)
                    if delay > 0:
                        time.sleep(delay)
                    self._last_request = time.monotonic()
                    self.requests_made += 1
                    with urlopen(request, timeout=self.timeout) as response:
                        raw = response.read(self.max_response_bytes + 1) if self.max_response_bytes else response.read()
                    if self.max_response_bytes and len(raw) > self.max_response_bytes:
                        raise ValueError("Magic Eden response exceeded the configured size limit")
                    records = json.loads(raw.decode("utf-8"))
                break
            except HTTPError as exc:
                last_error = exc
                if exc.code not in {429, 500, 502, 503, 504} or attempt + 1 >= self.max_attempts:
                    break
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                try:
                    delay = min(max(0.0, float(retry_after)), 5.0) if retry_after else min(self.retry_delay * (2 ** attempt), 2.0)
                except ValueError:
                    try:
                        delay = min(max(0.0, (parsedate_to_datetime(retry_after) - datetime.now(timezone.utc)).total_seconds()), 5.0)
                    except Exception:
                        delay = min(self.retry_delay * (2 ** attempt), 2.0)
                try:
                    exc.close()
                except Exception:
                    pass
                time.sleep(delay)
            except (URLError, TimeoutError, ValueError, OSError) as exc:
                last_error = exc
                if attempt + 1 >= self.max_attempts:
                    break
                time.sleep(min(self.retry_delay * (2 ** attempt), 2.0))
        if last_error is not None and records is None:
            self.last_status = {"status": "unavailable", "error_type": type(last_error).__name__, "fetched": 0, "solana": 0, "upcoming": 0}
            if isinstance(last_error, HTTPError):
                self.last_status["http_status"] = int(last_error.code)
            raise RuntimeError("Magic Eden launchpad source unavailable (%s)" % type(last_error).__name__) from None
        if not isinstance(records, list):
            self.last_status = {"status": "invalid_response", "fetched": 0, "solana": 0, "upcoming": 0}
            raise RuntimeError("Magic Eden launchpad source returned an invalid response")

        now = self._now()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        now = now.astimezone(timezone.utc)
        solana_count = 0
        observations: list[ProjectObservation] = []
        for record in records:
            if not isinstance(record, dict) or str(record.get("chainId", "")).lower() != "solana":
                continue
            solana_count += 1
            address = record.get("contractAddress")
            launch_text = record.get("launchDatetime")
            try:
                if not isinstance(address, str) or len(b58decode(address)) != 32 or b58encode(b58decode(address)) != address:
                    continue
                if not isinstance(launch_text, str):
                    continue
                launch_at = datetime.fromisoformat(launch_text.replace("Z", "+00:00"))
                if launch_at.tzinfo is None or launch_at.astimezone(timezone.utc) <= now:
                    continue
            except (ValueError, TypeError, OverflowError):
                continue

            claimed_price = record.get("price")
            if isinstance(claimed_price, (int, float)) and not isinstance(claimed_price, bool) and claimed_price >= 0:
                price_claim_status = "ZERO_PRICE_UNVERIFIED" if claimed_price == 0 else "PAID_PRICE_UNVERIFIED"
                normalized_claim = str(claimed_price)
            else:
                price_claim_status, normalized_claim = "UNKNOWN", None
            source_url = MAGIC_EDEN_LAUNCHPAD_API + "?" + urlencode({"offset": 0, "limit": self.limit})
            observations.append(ProjectObservation(
                chain_id="solana-mainnet-beta", contract_address=address, source_url=source_url,
                observed_at=now.isoformat(), project_name=str(record.get("name") or ""),
                nft_standard="Solana launchpad listing (standard unverified)",
                native_gas_token="SOL", eligibility="unknown", verification_status="unverified",
                risk_assessment="high",
                details={
                    "chain": "solana", "discovery_status": "DISCOVERED",
                    "opportunity_status": "UPCOMING",
                    "price_status": "PRICE_UNKNOWN", "eligibility_status": "ELIGIBILITY_UNKNOWN",
                    "mint_status": "upcoming", "launch_at": launch_at.astimezone(timezone.utc).isoformat(),
                    "mint_schedule": {"status": "UPCOMING_UNVERIFIED",
                                      "mint_date": launch_at.astimezone(timezone.utc).isoformat(),
                                      "source": "Magic Eden launchpad listing", "verified": False},
                    "announcement_date": None, "announcement_status": "UNKNOWN",
                    "mint_price": None, "mint_price_status": "UNKNOWN", "price_claim": normalized_claim,
                    "price_claim_status": price_claim_status, "price_claim_source": "Magic Eden launchpad API; not on-chain verified",
                    "allowlist_status": "UNKNOWN",
                    "native_gas_token": "SOL", "network_fee": {"status": "UNKNOWN", "amount": None, "token": "SOL"},
                    "priority_fee": {"status": "UNKNOWN", "amount": None, "token": "SOL"},
                    "rent_or_account_cost": {"status": "UNKNOWN", "amount": None, "token": "SOL"},
                    "eligibility": {"status": "UNKNOWN", "wallet": None, "reason": "No campaign proof/guard data is exposed by this listing"},
                    "verification_status": "unverified", "risk_assessment": "high",
                    "manual_review_required": True, "official_source_url": None,
                    "address_kind": "unknown", "address_validation_status": "PENDING_ONCHAIN_CLASSIFICATION",
                    "mint_infrastructure_verified": False,
                    "evidence": {"marketplace_source": MAGIC_EDEN_LAUNCHPAD_SOURCE,
                                 "official_project_source": None, "contract_address_kind": "unknown until on-chain classification",
                                 "supply_claim": record.get("size"), "symbol": record.get("symbol"),
                                 "description": record.get("description"), "image": record.get("image")},
                    "classification": "UNKNOWN", "telegram_qualified": False,
                },
            ))
        self.last_status = {"status": "ok", "fetched": len(records), "solana": solana_count, "upcoming": len(observations)}
        return observations


def b58decode(value: str) -> bytes:
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    number = 0
    for char in value:
        number = number * 58 + alphabet.index(char)
    body = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    return b"\0" * (len(value) - len(value.lstrip("1"))) + body


def _is_ed25519_point(data: bytes) -> bool:
    if len(data) != 32:
        return False
    p = 2 ** 255 - 19
    y = int.from_bytes(data, "little") & ((1 << 255) - 1)
    if y >= p:
        return False
    d = (-121665 * pow(121666, p - 2, p)) % p
    y2 = y * y % p
    x2 = (y2 - 1) * pow((d * y2 + 1) % p, p - 2, p) % p
    x = pow(x2, (p + 3) // 8, p)
    if x * x % p != x2:
        x = x * pow(2, (p - 1) // 4, p) % p
    return x * x % p == x2


def find_program_address(seeds: list[bytes], program_id: str) -> str:
    """Derive a Solana PDA using the documented off-curve construction."""
    import hashlib
    program = b58decode(program_id)
    marker = b"ProgramDerivedAddress"
    for bump in range(255, -1, -1):
        digest = hashlib.sha256(b"".join(seeds + [bytes([bump]), program, marker])).digest()
        if not _is_ed25519_point(digest):
            alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
            number = int.from_bytes(digest, "big")
            encoded = ""
            while number:
                number, remainder = divmod(number, 58)
                encoded = alphabet[remainder] + encoded
            return "1" * (len(digest) - len(digest.lstrip(b"\0"))) + encoded
    raise ValueError("No viable PDA bump found")


class SolanaRpc:
    def __init__(self, endpoint: str, timeout: float = 12.0, retries: int = 2):
        if not endpoint.startswith("https://"):
            raise ValueError("Solana RPC endpoint must use HTTPS")
        self.endpoint, self.timeout, self.retries = endpoint, timeout, min(max(0, retries), 2)
        self._id = 0
        self._lock = threading.Lock()
        self._last_call = 0.0
        self.requests_made = 0

    def call(self, method: str, params: list[Any]) -> Any:
        error: Optional[Exception] = None
        for attempt in range(self.retries + 1):
            with self._lock:
                delay = 0.1 - (time.monotonic() - self._last_call)
                if delay > 0:
                    time.sleep(delay)
                self._id += 1
                request_id = self._id
                self._last_call = time.monotonic()
                self.requests_made += 1
            request = Request(self.endpoint, json.dumps({"jsonrpc": "2.0", "id": request_id,
                            "method": method, "params": params}).encode(), {"Content-Type": "application/json"})
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read().decode())
            except HTTPError as exc:
                error = exc
                if exc.code not in {429, 500, 502, 503, 504} or attempt >= self.retries:
                    break
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                try:
                    delay = min(max(0.0, float(retry_after)), 5.0) if retry_after else min(0.25 * (2 ** attempt), 2.0)
                except ValueError:
                    try:
                        delay = min(max(0.0, (parsedate_to_datetime(retry_after) - datetime.now(timezone.utc)).total_seconds()), 5.0)
                    except Exception:
                        delay = min(0.25 * (2 ** attempt), 2.0)
                try:
                    exc.close()
                except Exception:
                    pass
                time.sleep(delay)
            except (URLError, TimeoutError, OSError, ValueError, UnicodeDecodeError) as exc:
                error = exc
                if attempt >= self.retries:
                    break
                time.sleep(min(0.25 * (2 ** attempt), 2.0))
            else:
                if not isinstance(payload, dict):
                    error = ValueError("invalid JSON-RPC response")
                    break
                if payload.get("error"):
                    error = RuntimeError("JSON-RPC error")
                    break
                if "result" not in payload:
                    error = ValueError("missing JSON-RPC result")
                    break
                return payload["result"]
        raise RuntimeError("Solana RPC %s failed (%s)" % (method, type(error).__name__ if error else "unknown error"))


class SolanaScanner:
    """Inspect candidate mints and optionally discover assets through a Metaplex DAS RPC."""

    spec = CHAIN_SPECS["solana"]

    def __init__(self, rpc_url: str = SOLANA_MAINNET_RPC, rpc: Any = None,
                 candidate_mints: Iterable[str] = (), das_rpc_url: str = "",
                 das_rpc: Any = None, das_page_size: int = 50,
                 launchpad_source: Optional[Any] = None,
                 candy_machine_candidates: Iterable[str] = (), watchlist_provider: Any = None) -> None:
        self.rpc = rpc or SolanaRpc(rpc_url)
        self.candidate_mints = list(candidate_mints)[:MAX_SOLANA_CANDIDATES]
        self.candy_machine_candidates = list(candy_machine_candidates)[:MAX_SOLANA_CANDIDATES]
        self.watchlist_candidate_rows: list[dict[str, Any]] = []
        self.watchlist_provider = watchlist_provider
        self.das_rpc = das_rpc or (SolanaRpc(das_rpc_url) if das_rpc_url else None)
        self.das_page_size = min(max(1, das_page_size), 100)
        self.launchpad_source = launchpad_source
        self.source_status: Dict[str, Dict[str, Any]] = {}

    def _set_source_status(self, name: str, status: str, records: int = 0,
                           error_type: Optional[str] = None, requests: int = 0) -> None:
        self.source_status[name] = {
            "status": status, "records": records, "error_type": error_type,
            "requests": requests, "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    @staticmethod
    def _read_string(data: bytes, offset: int) -> tuple[str, int]:
        length = struct.unpack_from("<I", data, offset)[0]
        end = offset + 4 + length
        return data[offset + 4:end].decode("utf-8", "replace").rstrip("\0"), end

    def _metadata_pda(self, mint: str) -> str:
        return find_program_address([b"metadata", b58decode(TOKEN_METADATA_PROGRAM), b58decode(mint)], TOKEN_METADATA_PROGRAM)

    def _fetch_token_metadata(self, mint: str) -> Dict[str, Any]:
        account = self.rpc.call("getAccountInfo", [self._metadata_pda(mint), {"encoding": "base64"}]).get("value")
        if not account:
            return {}
        raw = base64.b64decode(account["data"][0])
        if len(raw) < 65 or raw[0] != 4:
            return {}
        result: Dict[str, Any] = {"update_authority": b58encode(raw[1:33]), "mint": b58encode(raw[33:65])}
        offset = 65
        result["name"], offset = self._read_string(raw, offset)
        result["symbol"], offset = self._read_string(raw, offset)
        result["uri"], offset = self._read_string(raw, offset)
        offset += 2  # seller fee basis points
        creators = []
        if offset < len(raw):
            has_creators = raw[offset]
            offset += 1
            if has_creators and offset + 4 <= len(raw):
                count = struct.unpack_from("<I", raw, offset)[0]
                offset += 4
                for _ in range(min(count, 64)):
                    if offset + 34 > len(raw):
                        break
                    creators.append({"address": b58encode(raw[offset:offset + 32]), "verified": bool(raw[offset + 32]), "share": raw[offset + 33]})
                    offset += 34
                result["creators"] = creators
        # Metadata account fields following creators: primary sale, mutability,
        # optional edition nonce, optional token standard, then collection.
        if offset + 2 <= len(raw):
            result["primary_sale_happened"] = bool(raw[offset])
            result["is_mutable"] = bool(raw[offset + 1])
            offset += 2
        for field_name in ("edition_nonce", "token_standard"):
            if offset >= len(raw):
                break
            present = raw[offset]
            offset += 1
            if present and offset < len(raw):
                result[field_name] = raw[offset]
                offset += 1
        if offset < len(raw):
            has_collection = raw[offset]
            offset += 1
            if has_collection and offset + 33 <= len(raw):
                result["collection"] = {
                    "verified": bool(raw[offset]),
                    "key": b58encode(raw[offset + 1:offset + 33]),
                }
                offset += 33
        # Metaplex Metadata V1 stores optional Uses, CollectionDetails, then
        # ProgrammableConfig. Decode only the documented CollectionDetails V1
        # marker; unknown variants remain unclassified.
        if offset < len(raw):
            has_uses = raw[offset]
            offset += 1
            if has_uses == 1:
                if offset + 17 > len(raw):
                    return result
                offset += 17
            elif has_uses != 0:
                return result
        if offset < len(raw):
            has_collection_details = raw[offset]
            offset += 1
            if has_collection_details == 1:
                if offset + 9 > len(raw) or raw[offset] != 0:
                    return result
                result["collection_details"] = {
                    "version": "V1",
                    "size": struct.unpack_from("<Q", raw, offset + 1)[0],
                }
        return result

    def classify_candidate_address(self, address: str) -> Dict[str, Any]:
        """Classify a launch listing address without treating any token mint as mint infrastructure."""
        try:
            decoded = b58decode(address)
            if len(decoded) != 32 or b58encode(decoded) != address:
                return {"kind": "unknown", "verified": False, "reason": "invalid_public_key"}
            value = self.rpc.call("getAccountInfo", [address, {"encoding": "jsonParsed", "commitment": "confirmed"}]).get("value")
            if not isinstance(value, dict):
                return {"kind": "unknown", "verified": False, "reason": "account_not_found"}
            owner = value.get("owner")
            if owner == CORE_CANDY_MACHINE_PROGRAM:
                machine = self.inspect_core_candy_machine(address)
                if machine is not None:
                    return {"kind": "core_candy_machine", "verified": True,
                            "reason": "account_owner_and_core_machine_discriminator_verified",
                            "machine_observation": machine}
                return {"kind": "unknown", "verified": False, "reason": "invalid_or_unsupported_core_machine_layout"}
            if owner == CORE_CANDY_GUARD_PROGRAM:
                return {"kind": "core_candy_guard", "verified": True,
                        "reason": "account_owner_is_candy_guard_not_mint_infrastructure"}
            if owner not in {SPL_TOKEN_PROGRAM, TOKEN_2022_PROGRAM}:
                return {"kind": "unknown", "verified": False, "reason": "account_owner_not_recognized"}
            parsed = value.get("data", {}).get("parsed", {})
            info = parsed.get("info", {}) if isinstance(parsed, dict) else {}
            if not isinstance(info, dict) or int(info.get("decimals", -1)) != 0:
                return {"kind": "unknown", "verified": False, "reason": "not_a_zero_decimal_token_mint"}
            supply = self.rpc.call("getTokenSupply", [address, {"commitment": "confirmed"}]).get("value", {})
            if str(supply.get("amount", "")) != "1":
                return {"kind": "unknown", "verified": False, "reason": "not_a_single_supply_nft_mint"}
            metadata = self._fetch_token_metadata(address)
            if not metadata or metadata.get("mint") != address:
                return {"kind": "unknown", "verified": False, "reason": "metaplex_metadata_unavailable"}
            if metadata.get("collection_details", {}).get("version") == "V1":
                return {"kind": "collection_mint", "verified": True,
                        "reason": "single_supply_mint_has_metaplex_collection_details"}
            return {"kind": "nft_mint", "verified": True,
                    "reason": "single_supply_metaplex_nft_mint_not_a_candy_machine"}
        except Exception as exc:
            return {"kind": "unknown", "verified": False,
                    "reason": "rpc_classification_unavailable_%s" % type(exc).__name__}

    def inspect_mint(self, mint: str) -> Optional[ProjectObservation]:
        try:
            if len(b58decode(mint)) != 32:
                return None
            value = self.rpc.call("getAccountInfo", [mint, {"encoding": "jsonParsed"}]).get("value")
            if not value:
                return None
            parsed = value.get("data", {}).get("parsed", {}).get("info", {})
            if not parsed or int(parsed.get("decimals", -1)) != 0:
                return None
            supply = self.rpc.call("getTokenSupply", [mint]).get("value", {})
            if int(supply.get("amount", "0")) != 1:
                return None
            metadata = self._fetch_token_metadata(mint)
            if not metadata or metadata.get("mint") != mint:
                return None
            address_kind = "collection_mint" if metadata.get("collection_details", {}).get("version") == "V1" else "nft_mint"
            validation_status = "COLLECTION_ONLY_NOT_MINT_INFRASTRUCTURE" if address_kind == "collection_mint" else "REJECTED_HISTORICAL_NFT_MINT"
            return ProjectObservation(
                chain_id="solana-mainnet-beta", contract_address=mint,
                source_url=safe_rpc_source(getattr(self.rpc, "endpoint", "")),
                observed_at=datetime.now(timezone.utc).isoformat(),
                project_name=metadata.get("name", ""), verified=False,
                nft_standard="Metaplex Token Metadata", native_gas_token="SOL",
                verification_status="unknown", risk_assessment="high",
                details={"chain": "solana", "nft_standard": "Metaplex Token Metadata",
                         "mint_price": None, "native_gas_token": "SOL", "eligibility": "unknown",
                         "discovery_status": "WATCHLIST_REVIEW", "opportunity_status": "UNKNOWN",
                         "price_status": "PRICE_UNKNOWN",
                         "eligibility_status": "ELIGIBILITY_UNKNOWN",
                         "mint_status": "unknown",
                         "mint_schedule": {"status": "UNKNOWN", "evidence": "No mint schedule is encoded in an already-minted NFT token account"},
                         "address_kind": address_kind,
                         "address_validation_status": validation_status,
                         "address_validation_reason": "A single-supply Metaplex NFT account is not a Candy Machine or active mint configuration",
                         "mint_infrastructure_verified": False,
                         "price_evidence": {"verified": False, "status": "UNKNOWN", "reason": "An already-minted NFT token account does not establish its original or current mint price"},
                         "machine": {"account_type": "token_mint", "guard_config": {"status": "UNKNOWN", "reason": "This address is an NFT mint, not a Candy Machine or Candy Guard account"}},
                         "eligibility_evidence": {"verified": False, "status": "UNKNOWN", "reason": "No active Candy Machine or wallet-specific eligibility rule was verified"},
                         "verification_status": "unknown", "risk_assessment": "high",
                         "supply": supply.get("amount"), "decimals": 0,
                         "mint_authority": parsed.get("mintAuthority"),
                         "freeze_authority": parsed.get("freezeAuthority"),
                         "metadata": metadata},
            )
        except Exception:
            logger.exception("Solana mint inspection failed for %s", mint)
            return None

    def inspect_core_candy_machine(self, machine_address: str) -> Optional[ProjectObservation]:
        """Read a known MPL Core Candy Machine account; guard terms stay unknown.

        The fixed header offsets follow Metaplex's published account layout.
        This parser intentionally does not deserialize variable Candy Guard data.
        """
        try:
            raw_address = b58decode(machine_address)
            if len(raw_address) != 32 or b58encode(raw_address) != machine_address:
                return None
            response = self.rpc.call("getAccountInfo", [machine_address, {"encoding": "base64", "commitment": "confirmed"}])
            account = response.get("value") if isinstance(response, dict) else None
            if not isinstance(account, dict) or account.get("owner") != CORE_CANDY_MACHINE_PROGRAM:
                return None
            encoded = account.get("data")
            if not isinstance(encoded, list) or not encoded or encoded[1] != "base64":
                return None
            raw = base64.b64decode(encoded[0], validate=True)
            if (len(raw) < 128 or raw[:8] != CORE_CANDY_MACHINE_DISCRIMINATOR
                    or raw[8] not in {0, 1} or raw[9] not in {0, 4}):
                return None
            authority = b58encode(raw[16:48])
            mint_authority = b58encode(raw[48:80])
            collection = b58encode(raw[80:112])
            redeemed = struct.unpack_from("<Q", raw, 112)[0]
            available = struct.unpack_from("<Q", raw, 120)[0]
            if redeemed > available:
                return None
            guard_response = self.rpc.call("getAccountInfo", [mint_authority, {"encoding": "base64", "commitment": "confirmed"}])
            guard_account = guard_response.get("value") if isinstance(guard_response, dict) else None
            guard_present = isinstance(guard_account, dict) and guard_account.get("owner") == CORE_CANDY_GUARD_PROGRAM
            guard_config = {"status": "UNKNOWN", "reason": "guard_account_unavailable"}
            if guard_present:
                guard_data = guard_account.get("data")
                if isinstance(guard_data, list) and len(guard_data) >= 2 and guard_data[1] == "base64":
                    try:
                        guard_config = decode_core_candy_guard(base64.b64decode(guard_data[0], validate=True))
                    except (ValueError, TypeError):
                        guard_config = {"status": "UNKNOWN", "reason": "invalid_guard_account_data"}
            empty_guard = guard_config.get("status") == "VERIFIED_EMPTY"
            remaining = available - redeemed
            price_evidence = ({"verified": True, "method": "on_chain_guard", "amount_lamports": 0,
                               "spl_payment_mint": None, "guard_config_status": "VERIFIED_EMPTY"}
                              if empty_guard and guard_present else None)
            active = remaining > 0 and empty_guard
            return ProjectObservation(
                chain_id="solana-mainnet-beta", contract_address=machine_address,
                source_url=safe_rpc_source(getattr(self.rpc, "endpoint", "")),
                observed_at=datetime.now(timezone.utc).isoformat(),
                nft_standard="Metaplex Core Candy Machine", native_gas_token="SOL",
                eligibility="unknown", verification_status="onchain_account_owner_verified",
                risk_assessment="medium" if empty_guard else "high",
                details={
                    "chain": "solana", "discovery_status": "DISCOVERED",
                    "price_status": "PRICE_VERIFIED_FREE" if empty_guard else "PRICE_UNKNOWN",
                    "eligibility_status": "ELIGIBILITY_UNKNOWN",
                    "mint_status": "active" if active else ("sold_out" if empty_guard else "unknown"),
                    "mint_price": "0 SOL" if empty_guard else None,
                    "mint_price_status": "VERIFIED_FREE" if empty_guard else "UNKNOWN",
                    "network_fee": {"status": "UNKNOWN", "amount": None, "token": "SOL"},
                    "priority_fee": {"status": "UNKNOWN", "amount": None, "token": "SOL"},
                    "rent_or_account_cost": {"status": "UNKNOWN", "amount": None, "token": "SOL"},
                    "native_gas_token": "SOL", "address_kind": "core_candy_machine_account",
                    "mint_address": None, "collection_address": collection,
                    "mint_schedule": {"status": "UNKNOWN"},
                    "machine": {"program_id": CORE_CANDY_MACHINE_PROGRAM,
                                "account_version": raw[8], "token_standard_code": raw[9],
                                "authority": authority, "mint_authority": mint_authority,
                                "items_available": available, "items_redeemed": redeemed,
                                "items_remaining": available - redeemed,
                                "candy_guard_present": guard_present,
                                "candy_guard_program_id": CORE_CANDY_GUARD_PROGRAM if guard_present else None,
                                "guard_config_status": guard_config.get("status") if guard_present else "UNKNOWN",
                                "guard_config": guard_config},
                    "verification_status": "onchain_account_owner_verified",
                    "risk_assessment": "high", "manual_review_required": True,
                    "official_source_url": None, "price_evidence": price_evidence,
                    "eligibility_evidence": None, "classification": "UNKNOWN",
                    "telegram_qualified": False,
                },
            )
        except Exception as exc:
            logger.warning("Core Candy Machine inspection unavailable (%s)", type(exc).__name__)
            return None

    def estimate_fee(self, serialized_message: str) -> Optional[int]:
        """Estimate lamports using the RPC for a caller-supplied base64 message."""
        return self.rpc.call("getFeeForMessage", [serialized_message, {"commitment": "confirmed"}]).get("value")

    def get_wallet_balance(self, public_key: str) -> Dict[str, Any]:
        """Read a wallet's SOL balance only after confirming this RPC is mainnet-beta."""
        try:
            decoded = b58decode(public_key)
        except (ValueError, IndexError):
            raise ValueError("Invalid Solana public key") from None
        if len(decoded) != 32 or b58encode(decoded) != public_key:
            raise ValueError("Invalid Solana public key")
        genesis_hash = self.rpc.call("getGenesisHash", [])
        if genesis_hash != SOLANA_MAINNET_GENESIS_HASH:
            raise ValueError("Solana RPC is not mainnet-beta")
        response = self.rpc.call("getBalance", [public_key, {"commitment": "confirmed"}])
        lamports = response.get("value") if isinstance(response, dict) else None
        if not isinstance(lamports, int) or lamports < 0:
            raise RuntimeError("Solana RPC returned an invalid balance")
        whole, fraction = divmod(lamports, 1_000_000_000)
        sol = str(whole) if fraction == 0 else f"{whole}.{fraction:09d}".rstrip("0")
        return {"lamports": lamports, "sol": sol, "commitment": "confirmed"}

    def inspect_core_asset(self, asset_id: str) -> Dict[str, Any]:
        """Use optional Metaplex DAS getAsset extension when the RPC supports it."""
        if self.das_rpc is None:
            return {"supported": False, "reason": "No DAS endpoint is configured"}
        try:
            asset = self.das_rpc.call("getAsset", [{"id": asset_id}])
            return {"supported": True, "asset": asset, "standard": "Metaplex Core"}
        except Exception as exc:
            return {"supported": False, "reason": str(exc)}

    def _discover_das_assets(self) -> list[ProjectObservation]:
        if self.das_rpc is None:
            return []
        genesis_hash = self.das_rpc.call("getGenesisHash", [])
        if genesis_hash != SOLANA_MAINNET_GENESIS_HASH:
            raise ValueError("DAS RPC is not Solana mainnet-beta (unexpected genesis hash)")
        interfaces = ("V1_NFT", "LEGACY_NFT", "V2_NFT", "ProgrammableNFT", "MplCoreAsset", "MplBubblegumV2")
        observations = []
        seen_ids: set[str] = set()
        for interface in interfaces:
            response = self.das_rpc.call("searchAssets", [{
                "interface": interface,
                "sortBy": {"sortBy": "created", "sortDirection": "desc"},
                "limit": self.das_page_size,
                "page": 1,
                "options": {"showCollectionMetadata": True},
            }])
            for asset in response.get("items", []) if isinstance(response, dict) else []:
                asset_id = asset.get("id") if isinstance(asset, dict) else None
                if not isinstance(asset_id, str) or asset_id in seen_ids:
                    continue
                try:
                    if len(b58decode(asset_id)) != 32:
                        continue
                except (ValueError, IndexError):
                    continue
                seen_ids.add(asset_id)
                content = asset.get("content") or {}
                metadata = content.get("metadata") or {}
                creators = asset.get("creators") or []
                authorities = asset.get("authorities") or []
                grouping = asset.get("grouping") or []
                observations.append(ProjectObservation(
                    chain_id="solana-mainnet-beta", contract_address=asset_id,
                    source_url=safe_rpc_source(getattr(self.das_rpc, "endpoint", "")),
                    observed_at=datetime.now(timezone.utc).isoformat(),
                    project_name=str(metadata.get("name", "")), verified=False,
                    nft_standard=str(asset.get("interface", interface)), native_gas_token="SOL",
                    verification_status="unknown", risk_assessment="high",
                    details={"chain": "solana", "nft_standard": str(asset.get("interface", interface)),
                             "mint_price": None, "native_gas_token": "SOL", "eligibility": "unknown",
                             "discovery_status": "DISCOVERED", "price_status": "PRICE_UNKNOWN",
                             "eligibility_status": "ELIGIBILITY_UNKNOWN",
                             "verification_status": "unknown", "risk_assessment": "high",
                             "creators": creators, "authorities": authorities, "collection": grouping,
                             "metadata": {"name": metadata.get("name"), "symbol": metadata.get("symbol"),
                                          "description": metadata.get("description"),
                                          "uri": content.get("json_uri") or metadata.get("uri")}},
                ))
        return observations

    def scan(self) -> list[ProjectObservation]:
        self.source_status = {}
        if self.watchlist_provider is not None:
            try:
                rows = self.watchlist_provider()
                self.watchlist_candidate_rows = [dict(row) for row in rows
                                                 if row.get("address_type") == "launchpad_candidate"]
                self.candidate_mints = list(dict.fromkeys(
                    self.candidate_mints + [row["address"] for row in rows if row.get("address_type") == "token_mint"]
                ))[:MAX_SOLANA_CANDIDATES]
                self.candy_machine_candidates = list(dict.fromkeys(
                    self.candy_machine_candidates + [row["address"] for row in rows if row.get("address_type") == "core_candy_machine"]
                ))[:MAX_SOLANA_CANDIDATES]
                self._set_source_status("solana-project-watchlist", "ok", records=len(rows), requests=0)
            except Exception as exc:
                self.watchlist_candidate_rows = []
                logger.warning("Solana watchlist could not be loaded (%s)", type(exc).__name__)
                self._set_source_status("solana-project-watchlist", "unavailable", error_type=type(exc).__name__)
        main_rpc_requests_before = int(getattr(self.rpc, "requests_made", 0))
        try:
            genesis_hash = self.rpc.call("getGenesisHash", [])
            if genesis_hash != SOLANA_MAINNET_GENESIS_HASH:
                raise ValueError("Solana RPC is not mainnet-beta (unexpected genesis hash)")
        except Exception as exc:
            self._set_source_status("solana-mainnet-rpc", "unavailable", error_type=type(exc).__name__, requests=1)
            raise
        self._set_source_status("solana-mainnet-rpc", "ok",
                                requests=max(1, int(getattr(self.rpc, "requests_made", 1)) - main_rpc_requests_before))
        results = []
        seen_this_scan: set[str] = set()
        checked_candidates: set[str] = set()
        candidates_requests_before = int(getattr(self.rpc, "requests_made", 0))
        for mint in self.candidate_mints:
            if mint in checked_candidates:
                continue
            checked_candidates.add(mint)
            observation = self.inspect_mint(mint)
            if observation:
                seen_this_scan.add(mint)
                results.append(observation)
        self._set_source_status("configured-mint-candidates", "ok" if checked_candidates else "not_configured",
                                records=sum(1 for row in results if row.nft_standard == "Metaplex Token Metadata"),
                                requests=(int(getattr(self.rpc, "requests_made", candidates_requests_before))
                                          - candidates_requests_before) if hasattr(self.rpc, "requests_made")
                                else 3 * len(checked_candidates))
        checked_machines: set[str] = set()
        machine_results = []
        machine_requests_before = int(getattr(self.rpc, "requests_made", 0))
        for machine_address in self.candy_machine_candidates:
            if machine_address in checked_machines:
                continue
            checked_machines.add(machine_address)
            machine = self.inspect_core_candy_machine(machine_address)
            if machine is not None:
                machine_results.append(machine)
        for observation in machine_results:
            if observation.contract_address in seen_this_scan:
                continue
            seen_this_scan.add(observation.contract_address)
            results.append(observation)
        checked_watchlist_candidates: set[str] = set()
        watchlist_candidate_requests_before = int(getattr(self.rpc, "requests_made", 0))
        for candidate in self.watchlist_candidate_rows:
            address = candidate.get("address")
            if not isinstance(address, str) or address in checked_watchlist_candidates or address in seen_this_scan:
                continue
            checked_watchlist_candidates.add(address)
            classified = self.classify_candidate_address(address)
            kind = classified.get("kind", "unknown")
            observation = classified.get("machine_observation") if isinstance(classified.get("machine_observation"), ProjectObservation) else None
            if observation is None and kind in {"nft_mint", "collection_mint"}:
                observation = self.inspect_mint(address)
            details = dict(observation.details) if isinstance(observation, ProjectObservation) else {
                "chain": "solana", "nft_standard": "unknown", "mint_price": None,
                "native_gas_token": "SOL", "eligibility": "unknown",
                "price_status": "PRICE_UNKNOWN", "eligibility_status": "ELIGIBILITY_UNKNOWN",
                "mint_status": "unknown", "mint_schedule": {"status": "UNKNOWN"},
                "price_evidence": {"verified": False, "status": "UNKNOWN"},
                "eligibility_evidence": {"verified": False, "status": "UNKNOWN"},
                "risk_assessment": "high", "manual_review_required": True,
            }
            details.update({
                "discovery_status": "DISCOVERED" if candidate.get("review_status") == "approved" and candidate.get("official_source_verified") else "WATCHLIST_REVIEW",
                "opportunity_status": "UPCOMING" if candidate.get("review_status") == "approved" and candidate.get("official_source_verified") else "UNKNOWN",
                "address_kind": kind,
                "address_validation_status": "VERIFIED" if classified.get("verified") else "UNKNOWN",
                "address_validation_reason": classified.get("reason", "unknown"),
                "mint_infrastructure_verified": kind == "core_candy_machine" and bool(classified.get("verified")),
            })
            if kind == "nft_mint":
                details["address_validation_status"] = "REJECTED_HISTORICAL_NFT_MINT"
                details["mint_infrastructure_verified"] = False
            elif kind == "collection_mint":
                details["address_validation_status"] = "COLLECTION_ONLY_NOT_MINT_INFRASTRUCTURE"
                details["mint_infrastructure_verified"] = False
            elif kind == "core_candy_guard":
                details["address_validation_status"] = "GUARD_ONLY_NOT_MINT_INFRASTRUCTURE"
                details["mint_infrastructure_verified"] = False
            observation = ProjectObservation(
                chain_id="solana-mainnet-beta", contract_address=address,
                source_url=candidate.get("source_url") or safe_rpc_source(getattr(self.rpc, "endpoint", "")),
                observed_at=datetime.now(timezone.utc).isoformat(),
                project_name=candidate.get("project_name") or (observation.project_name if observation else ""),
                verified=False, details=details,
                nft_standard=observation.nft_standard if observation else "unknown",
                native_gas_token="SOL", eligibility="unknown", verification_status="unknown",
                risk_assessment="medium" if kind == "core_candy_machine" else "high",
            )
            seen_this_scan.add(address)
            results.append(observation)
        self._set_source_status("watchlist-address-classification", "ok" if checked_watchlist_candidates else "not_configured",
                                records=len(checked_watchlist_candidates),
                                requests=max(0, int(getattr(self.rpc, "requests_made", watchlist_candidate_requests_before))
                                             - watchlist_candidate_requests_before))
        self._set_source_status("metaplex-core-candy-machine",
                                "ok" if machine_results else ("not_configured" if not checked_machines else "ok"),
                                records=len(machine_results),
                                requests=(int(getattr(self.rpc, "requests_made", machine_requests_before))
                                          - machine_requests_before) if hasattr(self.rpc, "requests_made")
                                else 2 * len(checked_machines))
        if self.das_rpc is None:
            self._set_source_status("metaplex-das", "not_configured")
            das_observations = []
        else:
            das_requests_before = int(getattr(self.das_rpc, "requests_made", 0))
            try:
                das_observations = self._discover_das_assets()
                self._set_source_status("metaplex-das", "ok", records=len(das_observations),
                                        requests=(int(getattr(self.das_rpc, "requests_made", das_requests_before))
                                                  - das_requests_before) if hasattr(self.das_rpc, "requests_made") else 7)
            except Exception as exc:
                logger.warning("Solana DAS source unavailable (%s)", type(exc).__name__)
                requests = (int(getattr(self.das_rpc, "requests_made", das_requests_before))
                            - das_requests_before) if hasattr(self.das_rpc, "requests_made") else 1
                self._set_source_status("metaplex-das", "unavailable", error_type=type(exc).__name__, requests=requests)
                das_observations = []
        for observation in das_observations:
            if observation.contract_address in seen_this_scan:
                continue
            seen_this_scan.add(observation.contract_address)
            results.append(observation)
        if self.launchpad_source is not None:
            try:
                for observation in self.launchpad_source.fetch():
                    if observation.contract_address in seen_this_scan:
                        continue
                    if observation.details.get("address_validation_status") == "PENDING_ONCHAIN_CLASSIFICATION":
                        classified = self.classify_candidate_address(observation.contract_address)
                        details = dict(observation.details)
                        details["address_kind"] = classified["kind"]
                        details["address_validation_status"] = "VERIFIED" if classified.get("verified") else "UNKNOWN"
                        details["address_validation_reason"] = classified.get("reason")
                        details["mint_infrastructure_verified"] = classified["kind"] == "core_candy_machine" and bool(classified.get("verified"))
                        evidence = dict(details.get("evidence", {}))
                        evidence["contract_address_kind"] = classified["kind"]
                        evidence["address_validation_reason"] = classified.get("reason")
                        details["evidence"] = evidence
                        machine_observation = classified.get("machine_observation")
                        if isinstance(machine_observation, ProjectObservation):
                            machine_details = dict(machine_observation.details)
                            details.update(machine_details)
                            details["address_kind"] = "core_candy_machine"
                            details["address_validation_status"] = "VERIFIED"
                            details["mint_infrastructure_verified"] = True
                            details["mint_schedule"] = observation.details.get("mint_schedule")
                            details["launch_at"] = observation.details.get("launch_at")
                            details["announcement_date"] = observation.details.get("announcement_date")
                            details["announcement_status"] = observation.details.get("announcement_status", "UNKNOWN")
                            details["price_claim"] = observation.details.get("price_claim")
                            details["price_claim_status"] = observation.details.get("price_claim_status", "UNKNOWN")
                            details["allowlist_status"] = observation.details.get("allowlist_status", "UNKNOWN")
                            details["opportunity_status"] = "UPCOMING"
                            observation = replace(machine_observation,
                                project_name=observation.project_name or machine_observation.project_name,
                                source_url=observation.source_url, details=details)
                        elif classified["kind"] == "nft_mint":
                            details["address_validation_status"] = "REJECTED_HISTORICAL_NFT_MINT"
                            details["mint_infrastructure_verified"] = False
                        elif classified["kind"] == "collection_mint":
                            details["address_validation_status"] = "COLLECTION_ONLY_NOT_MINT_INFRASTRUCTURE"
                            details["mint_infrastructure_verified"] = False
                        elif classified["kind"] == "core_candy_guard":
                            details["address_validation_status"] = "GUARD_ONLY_NOT_MINT_INFRASTRUCTURE"
                            details["mint_infrastructure_verified"] = False
                        observation = replace(observation, details=details)
                    seen_this_scan.add(observation.contract_address)
                    results.append(observation)
                source_status = getattr(self.launchpad_source, "last_status", {})
                self._set_source_status("magiceden-launchpad", "ok",
                                        records=int(source_status.get("upcoming", 0)),
                                        requests=int(getattr(self.launchpad_source, "requests_made", 1)))
            except Exception as exc:
                # A launch-index outage must not hide RPC/DAS results. Do not log URLs or response bodies.
                logger.warning("Solana launchpad source unavailable (%s)", type(exc).__name__)
                source_status = getattr(self.launchpad_source, "last_status", {})
                self._set_source_status("magiceden-launchpad", "unavailable",
                                        error_type=str(source_status.get("error_type") or type(exc).__name__),
                                        requests=int(getattr(self.launchpad_source, "requests_made", 0)))
        else:
            self._set_source_status("magiceden-launchpad", "not_configured")
        self._set_source_status("solana-mainnet-rpc", "ok", records=len(results),
                                requests=max(1, int(getattr(self.rpc, "requests_made", main_rpc_requests_before + 1))
                                             - main_rpc_requests_before))
        return results


def b58encode(value: bytes) -> str:
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    zeros = len(value) - len(value.lstrip(b"\0"))
    number = int.from_bytes(value, "big")
    encoded = ""
    while number:
        number, remainder = divmod(number, 58)
        encoded = alphabet[remainder] + encoded
    return "1" * zeros + encoded

