"""Additional public campaign lead sources; results are never verification evidence."""

from __future__ import annotations

from datetime import date, datetime, time, timezone
import json
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlsplit, urlunsplit


HOWRARE_DROPS_API = "https://api.howrare.is/v0.1/drops"
HOWRARE_SOURCE_URL = "https://howrare.is/api"


def _safe_https_url(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 1000:
        return None
    value = value.strip()
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return None
    return urlunsplit(("https", parsed.netloc, parsed.path[:900], "", ""))


class HowRareDropsSource:
    """One bounded request to HowRare's public, experimental Solana drops API."""

    name = "howrare-solana-drops"
    chain_identifier = "solana-mainnet-beta"
    timeout_seconds = 8
    max_bytes = 1_048_576
    max_items = 100

    def __init__(self, opener: Any = urlopen, now=None) -> None:
        self.opener = opener
        self._now = now or (lambda: datetime.now(timezone.utc))
        self.requests_made = 0
        self.last_status: dict[str, Any] = {"status": "not_scanned", "items_received": 0,
                                            "upcoming": 0, "expired": 0}

    def fetch(self) -> list[dict[str, Any]]:
        request = Request(HOWRARE_DROPS_API, headers={
            "Accept": "application/json", "User-Agent": "Binance-NFT-Hunter/0.1",
        })
        self.requests_made = 1
        try:
            with self.opener(request, timeout=self.timeout_seconds) as response:
                raw = response.read(self.max_bytes + 1)
        except HTTPError as exc:
            self.last_status = {"status": "unavailable", "error_type": "HTTPError",
                                "http_status": int(exc.code), "items_received": 0,
                                "upcoming": 0, "expired": 0}
            exc.close()
            raise
        except (URLError, TimeoutError, OSError) as exc:
            self.last_status = {"status": "unavailable", "error_type": type(exc).__name__,
                                "items_received": 0, "upcoming": 0, "expired": 0}
            raise
        if len(raw) > self.max_bytes:
            self.last_status = {"status": "invalid_response", "error_type": "ResponseTooLarge",
                                "error_reason": "response_over_size_limit",
                                "items_received": 0, "upcoming": 0, "expired": 0}
            raise ValueError("HowRare response exceeded the configured size limit")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.last_status = {"status": "invalid_response", "error_type": "InvalidJSON",
                                "error_reason": "response_body_not_valid_utf8_json",
                                "items_received": 0, "upcoming": 0, "expired": 0}
            raise ValueError("HowRare response was not valid JSON") from None
        result = payload.get("result") if isinstance(payload, dict) else None
        if not isinstance(result, dict) or result.get("api_code") != 200:
            self.last_status = {"status": "invalid_response", "error_type": "UnexpectedSchema",
                                "error_reason": "missing_success_result_envelope",
                                "items_received": 0, "upcoming": 0, "expired": 0}
            raise ValueError("HowRare returned an unexpected response schema")
        calendar = result.get("data")
        if calendar == []:
            self.last_status = {"status": "ok", "items_received": 0, "examined": 0,
                                "upcoming": 0, "expired": 0}
            return []
        if not isinstance(calendar, dict):
            self.last_status = {"status": "invalid_response", "error_type": "UnexpectedSchema",
                                "error_reason": "nonempty_drops_data_is_not_a_date_keyed_object",
                                "items_received": 0, "upcoming": 0, "expired": 0}
            raise ValueError("HowRare returned an unexpected drops schema")

        received = sum(len(rows) for rows in calendar.values() if isinstance(rows, list))
        now = self._now()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        now = now.astimezone(timezone.utc)
        candidates: list[dict[str, Any]] = []
        expired = 0
        examined = 0
        for grouped_date, rows in sorted(calendar.items(), key=lambda item: str(item[0])):
            if not isinstance(rows, list):
                continue
            for item in rows:
                if examined >= self.max_items:
                    break
                examined += 1
                if not isinstance(item, dict):
                    continue
                raw_date = item.get("date") or grouped_date
                try:
                    mint_day = date.fromisoformat(str(raw_date)[:10])
                except (TypeError, ValueError):
                    continue
                mint_time_claim = str(item.get("time") or "").strip()[:80] or None
                mint_at = None
                if mint_time_claim:
                    match = re.fullmatch(r"(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(?:UTC|GMT)?", mint_time_claim, re.I)
                    if match:
                        try:
                            parsed_time = time(int(match.group(1)), int(match.group(2)), int(match.group(3) or 0),
                                               tzinfo=timezone.utc)
                            mint_at = datetime.combine(mint_day, parsed_time)
                        except ValueError:
                            mint_at = None
                if mint_at is not None and mint_at <= now:
                    expired += 1
                    continue
                if mint_at is None and mint_day < now.date():
                    expired += 1
                    continue
                name = str(item.get("name") or "").strip()[:160]
                if not name:
                    continue
                raw_price = str(item.get("price") or "").strip()[:80] or None
                price_status = _price_claim_status(raw_price)
                candidates.append({
                    "chain_identifier": self.chain_identifier,
                    "project_name": name,
                    "source_name": self.name,
                    "source_url": HOWRARE_SOURCE_URL,
                    "official_website_claim": _safe_https_url(item.get("website")),
                    "announcement_url_claim": _safe_https_url(item.get("twitter")),
                    "launchpad_url": None,
                    "expected_mint_at": mint_at.isoformat() if mint_at else None,
                    "mint_date_claim": mint_day.isoformat(),
                    "mint_time_claim": mint_time_claim,
                    "contract_or_mint_address": None,
                    "address_kind": "NOT_PROVIDED_BY_SOURCE",
                    "mint_price_claim": raw_price,
                    "mint_price_status": price_status,
                    "eligibility_status": "UNKNOWN",
                    "verification_status": "DEFERRED_MISSING_EVIDENCE",
                    "risk_assessment": "HIGH",
                    "source_confidence": "THIRD_PARTY_CALENDAR_UNVERIFIED",
                    "evidence": {
                        "source_api": HOWRARE_DROPS_API,
                        "price_claim": raw_price,
                        "nft_count_claim": item.get("nft_count"),
                        "left_claim": str(item.get("left") or "")[:120] or None,
                        "website_claim_verified": False,
                        "announcement_claim_verified": False,
                        "mint_infrastructure": "UNKNOWN",
                    },
                })
            if examined >= self.max_items:
                break
        self.last_status = {"status": "ok", "items_received": received,
                            "examined": examined, "upcoming": len(candidates), "expired": expired}
        return candidates


def _price_claim_status(value: str | None) -> str:
    if not value:
        return "UNKNOWN"
    normalized = value.strip().lower()
    if re.search(r"\bfree\b", normalized) or re.fullmatch(r"0+(?:\.0+)?\s*(?:sol|bnb|eth)?", normalized):
        return "CLAIMED_FREE_UNVERIFIED"
    if re.search(r"\d+(?:\.\d+)?\s*(?:sol|bnb|eth)\b", normalized):
        return "CLAIMED_PAID_UNVERIFIED"
    return "UNKNOWN"


def magic_eden_observation_to_lead(observation: Any, source_name: str,
                                   source_url: str) -> dict[str, Any]:
    details = observation.details if isinstance(observation.details, dict) else {}
    return {
        "chain_identifier": "solana-mainnet-beta",
        "project_name": observation.project_name or "Unnamed Magic Eden launch",
        "source_name": source_name,
        "source_url": source_url,
        "official_website_claim": None,
        "announcement_url_claim": None,
        "launchpad_url": None,
        "expected_mint_at": details.get("launch_at"),
        "mint_date_claim": str(details.get("launch_at") or "")[:10] or None,
        "mint_time_claim": None,
        "contract_or_mint_address": observation.contract_address,
        "address_kind": details.get("address_kind", "UNKNOWN"),
        "mint_price_claim": details.get("price_claim"),
        "mint_price_status": details.get("price_claim_status", "UNKNOWN"),
        "eligibility_status": "UNKNOWN",
        "verification_status": "DEFERRED_MISSING_EVIDENCE",
        "risk_assessment": "HIGH",
        "source_confidence": "THIRD_PARTY_LAUNCHPAD_UNVERIFIED",
        "evidence": details.get("evidence", {}),
    }

