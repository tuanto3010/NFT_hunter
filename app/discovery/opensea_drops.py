"""Bounded OpenSea Drops API discovery. Results are unverified campaign leads."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import errno
import json
import os
from pathlib import Path
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from app.config import load_env_file


DROP_LIST_URL = "https://api.opensea.io/api/v2/drops"
SOURCE_DOC_URL = "https://docs.opensea.io/reference/get_drops"
DROP_TYPES = ("upcoming", "featured", "recently_minted")
_ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")
_CHAIN_IDS = {
    "ethereum": "1", "mainnet": "1", "base": "8453", "optimism": "10",
    "arbitrum": "42161", "polygon": "137", "avalanche": "43114",
    "zora": "7777777", "blast": "81457", "bnb": "56", "bsc": "56", "robinhood": "4663",
    "solana": "solana-mainnet-beta",
}
_OPENSEA_CHAIN_ALIASES = {
    "bnb": "bsc", "solana-mainnet-beta": "solana", "robinhood-chain": "robinhood",
}
_OPENSEA_RECOGNIZED_CHAINS = frozenset({
    "abstract", "animechain", "ape_chain", "arbitrum", "arbitrum_nova", "arc", "avalanche",
    "b3", "base", "bera_chain", "bitcoin", "blast", "bsc", "elysium", "ethereum", "flow",
    "gunzilla", "hyperevm", "hyperliquid", "immutable", "ink", "megaeth", "monad", "optimism",
    "polygon", "robinhood", "ronin", "sei", "shape", "solana", "somnia", "soneium",
    "stablechain", "tenderly_virtual_base", "tenderly_virtual_mainnet", "unichain", "zora",
})


def normalize_opensea_chains(chains: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """Map Hunter network names to OpenSea's recognized Drops API chain slugs."""
    normalized: list[str] = []
    for raw in chains:
        chain = str(raw).strip().lower()
        slug = _OPENSEA_CHAIN_ALIASES.get(chain, chain)
        if not slug or slug not in _OPENSEA_RECOGNIZED_CHAINS:
            raise ValueError("OPENSEA_CHAINS contains a chain slug not recognized by OpenSea Drops")
        if slug not in normalized:
            normalized.append(slug)
    return tuple(normalized)


def _safe_https(value: Any, allowed_host: str | None = None) -> str | None:
    if not isinstance(value, str) or len(value) > 1000:
        return None
    try:
        parts = urlsplit(value.strip())
    except ValueError:
        return None
    if (parts.scheme != "https" or not parts.hostname or parts.username or parts.password
            or parts.fragment):
        return None
    if allowed_host and parts.hostname.lower() not in {allowed_host, "www." + allowed_host}:
        return None
    return value.strip()


def _stage_datetime(stage: Any, key: str) -> str | None:
    if not isinstance(stage, dict):
        return None
    value = stage.get(key)
    if not isinstance(value, str) or len(value) > 80:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc).isoformat()
    except ValueError:
        return None


def _price_claim(stage: Any) -> tuple[str | None, str]:
    if not isinstance(stage, dict) or stage.get("price") is None:
        return None, "UNKNOWN"
    raw = stage.get("price")
    try:
        wei = int(str(raw))
        if wei < 0:
            return str(raw)[:100], "UNKNOWN"
    except (ValueError, TypeError):
        return str(raw)[:100], "UNKNOWN"
    currency = str(stage.get("price_currency_address") or "").lower()
    if wei == 0:
        return "0 wei", "CLAIMED_FREE_UNVERIFIED"
    if currency == "0x0000000000000000000000000000000000000000":
        return f"{Decimal(wei) / Decimal(10**18)} native token (API claim)", "CLAIMED_PAID_UNVERIFIED"
    return f"{wei} token base units (currency {currency or 'unknown'})", "CLAIMED_PAID_UNVERIFIED"


def normalize_drop(item: dict[str, Any], drop_type: str, retrieved_at: str) -> dict[str, Any] | None:
    """Normalize one documented list response without promoting API claims to proof."""
    chain_slug = str(item.get("chain") or "").strip().lower()
    chain_identifier = _CHAIN_IDS.get(chain_slug, f"opensea:{chain_slug}" if chain_slug else "")
    slug = str(item.get("collection_slug") or "").strip()[:200]
    name = str(item.get("collection_name") or slug).strip()[:160]
    raw_contract = str(item.get("contract_address") or "").strip()
    contract = raw_contract if _ADDRESS.fullmatch(raw_contract) else None
    if not chain_identifier or not name:
        return None

    is_minting = item.get("is_minting") is True
    stage = item.get("active_stage") if is_minting else item.get("next_stage")
    if not isinstance(stage, dict) and isinstance(item.get("active_stage"), dict):
        stage = item["active_stage"]
    stage = stage if isinstance(stage, dict) else None
    price, price_status = _price_claim(stage)
    stage_type = str(stage.get("stage_type") or "UNKNOWN")[:80] if stage else "UNKNOWN"
    start = _stage_datetime(stage, "start_time")
    end = _stage_datetime(stage, "end_time")
    slug_url = _safe_https(item.get("opensea_url"), "opensea.io")
    source_url = slug_url or DROP_LIST_URL
    retrieved = retrieved_at
    stage_uuid = str(stage.get("uuid") or "")[:128] if stage else ""
    return {
        "chain_identifier": chain_identifier,
        "project_name": name,
        "source_name": "opensea-drops-api",
        "source_url": source_url,
        "official_website_claim": None,
        "announcement_url_claim": None,
        "launchpad_url": slug_url,
        "expected_mint_at": start,
        "mint_date_claim": start[:10] if start else None,
        "mint_time_claim": start,
        "contract_or_mint_address": contract,
        "address_kind": "NFT_CONTRACT" if contract else "CONTRACT_ADDRESS_UNVERIFIED_OR_MISSING",
        "mint_price_claim": price,
        "mint_price_status": price_status,
        "eligibility_status": "UNKNOWN",
        "verification_status": "DEFERRED_MISSING_EVIDENCE",
        "risk_assessment": "HIGH",
        "source_confidence": "THIRD_PARTY_LAUNCHPAD_UNVERIFIED",
        "evidence": {
            "api_endpoint": DROP_LIST_URL,
            "documentation_url": SOURCE_DOC_URL,
            "retrieved_at": retrieved,
            "drop_type": drop_type,
            "collection_slug": slug or None,
            "chain_slug": chain_slug,
            "contract_address_claim": raw_contract or None,
            "contract_address_shape_valid": contract is not None,
            "is_minting_claim": is_minting,
            "stage_id": stage_uuid or None,
            "stage_type_claim": stage_type,
            "stage_start_claim": start,
            "stage_end_claim": end,
            "price_wei_claim": str(stage.get("price")) if stage and stage.get("price") is not None else None,
            "price_currency_address_claim": (
                str(stage.get("price_currency_address") or "") or None if stage else None
            ),
            "max_per_wallet_claim": str(stage.get("max_per_wallet")) if stage and stage.get("max_per_wallet") is not None else None,
            "project_controlled_source_verified": False,
            "onchain_verification": "NOT_PERFORMED",
        },
    }


class OpenSeaDropsSource:
    """Official API client with strict request, page, byte and time budgets."""

    name = "opensea-drops-api"
    chain_identifier = "opensea-multi-chain"
    timeout_seconds = 8
    max_response_bytes = 1_048_576
    page_limit = 50
    max_pages_per_type = 2
    request_budget = 6

    def __init__(self, api_key: str | None = None, chains: tuple[str, ...] | None = None,
                 opener: Any = urlopen, now=None) -> None:
        load_env_file(Path(__file__).resolve().parents[2] / ".env")
        self.api_key = (api_key if api_key is not None else os.getenv("OPENSEA_API_KEY", "")).strip()
        if chains is None:
            raw = os.getenv("OPENSEA_CHAINS", "").strip()
            chains = tuple(part.strip().lower() for part in raw.split(",") if part.strip()) if raw else ()
        if len(chains) > 12 or any(not re.fullmatch(r"[a-z0-9_-]{1,40}", c) for c in chains):
            raise ValueError("OpenSea chain filters must be valid documented chain slugs")
        self.chains = normalize_opensea_chains(chains)
        self.opener = opener
        self._now = now or (lambda: datetime.now(timezone.utc))
        self.requests_made = 0
        self.failure_phase: str | None = None
        self.last_status: dict[str, Any] = {"status": "not_scanned", "items_received": 0,
                                            "leads_found": 0, "pages": 0, "source_errors": []}

    def fetch(self) -> list[dict[str, Any]]:
        if not self.api_key:
            self.last_status = {"status": "not_configured", "error_type": "MissingApiKey",
                                "items_received": 0, "leads_found": 0, "pages": 0,
                                "requests": 0, "source_errors": []}
            return []
        self.requests_made = 0
        self.failure_phase = "clock"
        now = self._now()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        retrieved_at = now.astimezone(timezone.utc).isoformat()
        leads: dict[tuple[str, str], dict[str, Any]] = {}
        seen_cursor: set[tuple[str, str]] = set()
        errors: list[dict[str, Any]] = []
        total_items = 0
        page_count = 0
        by_type: dict[str, dict[str, int]] = {}
        stop_source = False

        for drop_type in DROP_TYPES:
            cursor: str | None = None
            type_seen = 0
            pages_for_type = 0
            type_http_statuses: list[int] = []
            while pages_for_type < self.max_pages_per_type and self.requests_made < self.request_budget:
                self.failure_phase = f"request:{drop_type}"
                query: dict[str, Any] = {"type": drop_type, "limit": self.page_limit}
                if self.chains:
                    query["chains"] = ",".join(self.chains)
                if cursor:
                    query["cursor"] = cursor
                request = Request(f"{DROP_LIST_URL}?{urlencode(query)}", headers={
                    "Accept": "application/json", "User-Agent": "Binance-NFT-Hunter/0.1",
                    "x-api-key": self.api_key,
                })
                self.requests_made += 1
                pages_for_type += 1
                page_count += 1
                try:
                    with self.opener(request, timeout=self.timeout_seconds) as response:
                        raw = response.read(self.max_response_bytes + 1)
                        status = int(response.status)
                    type_http_statuses.append(status)
                    self.failure_phase = f"decode_response:{drop_type}"
                    if len(raw) > self.max_response_bytes:
                        raise ValueError("OpenSea response exceeded the configured size limit")
                    payload = json.loads(raw.decode("utf-8"))
                except HTTPError as exc:
                    errors.append({"error_type": "HTTPError", "http_status": int(exc.code), "drop_type": drop_type})
                    exc.close()
                    stop_source = True
                    break
                except (URLError, TimeoutError, OSError) as exc:
                    error: dict[str, Any] = {"error_type": type(exc).__name__, "drop_type": drop_type}
                    # Keep transport diagnostics useful without persisting
                    # exception strings, which may contain URLs or secrets.
                    reason = exc.reason if isinstance(exc, URLError) else exc
                    if isinstance(exc, URLError):
                        error["transport_category"] = type(reason).__name__
                    if isinstance(reason, OSError):
                        code = getattr(reason, "winerror", None) or getattr(reason, "errno", None)
                        if isinstance(code, int):
                            error["transport_code"] = code
                            if code in {10013, errno.EACCES, errno.EPERM}:
                                error["transport_state"] = "SOCKET_PERMISSION_DENIED"
                            elif code in {errno.ETIMEDOUT, 10060}:
                                error["transport_state"] = "TCP_TIMEOUT"
                            elif code in {errno.ECONNREFUSED, 10061}:
                                error["transport_state"] = "TCP_CONNECTION_REFUSED"
                    stop_source = True
                    errors.append(error)
                    break
                except (UnicodeError, json.JSONDecodeError, ValueError):
                    errors.append({"error_type": "InvalidResponse", "drop_type": drop_type})
                    stop_source = True
                    break
                if status != 200 or not isinstance(payload, dict) or not isinstance(payload.get("drops"), list):
                    errors.append({"error_type": "UnexpectedSchema", "drop_type": drop_type})
                    stop_source = True
                    break
                drops = payload["drops"]
                self.failure_phase = f"normalize_items:{drop_type}"
                total_items += len(drops)
                type_seen += len(drops)
                for item_index, item in enumerate(drops):
                    if not isinstance(item, dict):
                        errors.append({"error_type": "InvalidDropShape", "drop_type": drop_type,
                                       "failure_phase": f"normalize_item:{drop_type}:{item_index}"})
                        continue
                    try:
                        candidate = normalize_drop(item, drop_type, retrieved_at)
                        if not candidate:
                            continue
                        address = candidate.get("contract_or_mint_address")
                        slug = candidate["evidence"].get("collection_slug")
                        identity = (candidate["chain_identifier"], (address or "slug:" + str(slug)).lower())
                        if identity[1] == "slug:none":
                            continue
                        if identity not in leads:
                            leads[identity] = candidate
                        else:
                            previous = leads[identity]
                            prior_evidence = previous["evidence"]
                            prior_types = prior_evidence.get("drop_types", [prior_evidence.get("drop_type")])
                            prior_evidence["drop_types"] = sorted(
                                set(x for x in prior_types + [drop_type] if x)
                            )
                            # Prefer live stage data when duplicate records differ.
                            if (candidate["evidence"].get("is_minting_claim")
                                    and not prior_evidence.get("is_minting_claim")):
                                candidate["evidence"]["drop_types"] = prior_evidence["drop_types"]
                                leads[identity] = candidate
                    except AttributeError as exc:
                        # A malformed single item must not discard earlier valid, unverified leads.
                        item_phase = f"normalize_item:{drop_type}:{item_index}"
                        self.failure_phase = item_phase
                        traceback = exc.__traceback__
                        while traceback is not None and traceback.tb_next is not None:
                            traceback = traceback.tb_next
                        failure_location = None
                        if traceback is not None:
                            failure_location = "%s:%d" % (
                                Path(traceback.tb_frame.f_code.co_filename).name, traceback.tb_lineno
                            )
                        errors.append({
                            "error_type": "AttributeError", "drop_type": drop_type,
                            "failure_phase": item_phase,
                            "failure_location": failure_location,
                            "item_keys": sorted(str(key)[:80] for key in item.keys())[:40],
                        })
                self.failure_phase = f"pagination:{drop_type}"
                next_cursor = payload.get("next")
                if not isinstance(next_cursor, str) or not next_cursor:
                    break
                cursor_key = (drop_type, next_cursor)
                if cursor_key in seen_cursor:
                    errors.append({"error_type": "RepeatedCursor", "drop_type": drop_type})
                    break
                seen_cursor.add(cursor_key)
                cursor = next_cursor
            by_type[drop_type] = {"items_received": type_seen, "pages": pages_for_type,
                                  "http_statuses": type_http_statuses}
            if stop_source:
                break

        error_type = errors[0].get("error_type") if errors else None
        self.last_status = {
            "status": "partial" if errors and leads else "error" if errors else "ok",
            "error_type": error_type,
            "requests": self.requests_made, "pages": page_count, "items_received": total_items,
            "leads_found": len(leads), "duplicates": max(0, total_items - len(leads)),
            "by_type": by_type, "source_errors": errors,
        }
        if not errors:
            self.failure_phase = "complete"
        return list(leads.values())

