"""One-request OpenSea API preflight, optionally followed by one safe campaign cycle.

Run this from the user's normal local Python environment. The API key is read
from .env/process environment, used only as a request header, and never printed.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.config import Settings, load_env_file
from app.database import Database
from app.discovery.opensea_drops import DROP_LIST_URL, normalize_opensea_chains


PAGE_LIMIT = 50  # matches the existing adapter; OpenSea documents 1-100
TIMEOUT_SECONDS = 8
MAX_RESPONSE_BYTES = 262_144
MAX_ERROR_TEXT = 1200
_SENSITIVE_FIELD = re.compile(r"(api.?key|authorization|token|secret|password|credential)", re.I)


def _redact_json(value: Any, api_key: str) -> Any:
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if _SENSITIVE_FIELD.search(str(key)) else _redact_json(item, api_key)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_json(item, api_key) for item in value[:30]]
    if isinstance(value, str):
        return value.replace(api_key, "[REDACTED]") if api_key else value
    return value


def _safe_error_body(raw: bytes, api_key: str) -> str:
    text = raw[:MAX_ERROR_TEXT * 4].decode("utf-8", errors="replace")
    if api_key:
        text = text.replace(api_key, "[REDACTED]")
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        # Redact common credential-like assignments in non-JSON error pages.
        text = re.sub(
            r"(?i)(x-api-key|api[_-]?key|authorization|access[_-]?token|secret|password)"
            r"([\"'\s:=]+)[^\s,;\"'<>]+",
            r"\1\2[REDACTED]", text,
        )
        return text[:MAX_ERROR_TEXT]
    return json.dumps(_redact_json(parsed, api_key), ensure_ascii=True, separators=(",", ":"))[:MAX_ERROR_TEXT]


def _error_response_shape(raw: bytes) -> dict[str, Any] | None:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if isinstance(payload, dict):
        return {"top_level_keys": sorted(str(key) for key in payload.keys())[:30]}
    return {"top_level_type": type(payload).__name__}


def _response_shape(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"valid_drops_schema": False, "top_level_type": type(payload).__name__}
    drops = payload.get("drops")
    valid = isinstance(drops, list)
    first = next((item for item in drops if isinstance(item, dict)), None) if valid else None
    chain_counts: dict[str, int] = {}
    sample_drops: list[dict[str, Any]] = []
    if valid:
        for item in drops:
            if not isinstance(item, dict):
                continue
            chain = str(item.get("chain") or "UNKNOWN")[:80]
            chain_counts[chain] = chain_counts.get(chain, 0) + 1
            if len(sample_drops) < 3:
                raw_stage = item.get("active_stage") if item.get("is_minting") else item.get("next_stage")
                stage = raw_stage if isinstance(raw_stage, dict) else {}
                sample_drops.append({
                    "collection_name": str(item.get("collection_name") or "")[:160] or None,
                    "collection_slug": str(item.get("collection_slug") or "")[:200] or None,
                    "chain": chain,
                    "contract_address": str(item.get("contract_address") or "")[:64] or None,
                    "is_minting_claim": item.get("is_minting") is True,
                    "stage_type_claim": str(stage.get("stage_type") or "")[:80] or None,
                    "price_claim": str(stage.get("price"))[:100] if stage.get("price") is not None else None,
                    "price_currency_address_claim": str(stage.get("price_currency_address") or "")[:64] or None,
                    "start_time_claim": str(stage.get("start_time") or "")[:80] or None,
                    "end_time_claim": str(stage.get("end_time") or "")[:80] or None,
                })
    return {
        "valid_drops_schema": valid,
        "top_level_keys": sorted(str(key) for key in payload.keys())[:40],
        "drops_count": len(drops) if valid else None,
        "first_drop_keys": sorted(str(key) for key in first.keys())[:40] if first else [],
        "next_cursor_present": bool(payload.get("next")),
        "chain_counts": chain_counts,
        "sample_drops": sample_drops,
    }


def _request_params() -> dict[str, str]:
    params = {"type": "upcoming", "limit": str(PAGE_LIMIT)}
    configured_chains = os.getenv("OPENSEA_CHAINS", "").strip()
    if configured_chains:
        slugs = normalize_opensea_chains(
            tuple(part.strip().lower() for part in configured_chains.split(",") if part.strip())
        )
        params["chains"] = ",".join(slugs)
    return params


def _run_cycle() -> dict[str, Any]:
    settings = Settings.from_env()
    database = Database(settings.database_path)
    from app.discovery.campaign_feed import run_campaign_discovery_cycle

    cycle = run_campaign_discovery_cycle(database)
    checked_at = str(cycle.get("checked_at") or "")
    persisted = []
    for row in database.list_campaign_source_leads(limit=1000):
        if str(row["last_seen_at"]) < checked_at:
            continue
        try:
            evidence = json.loads(row["evidence_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            evidence = {}
        source_evidence = evidence.get("source_evidence", []) if isinstance(evidence, dict) else []
        if not isinstance(source_evidence, list) or not any(
            isinstance(source, dict) and source.get("source_name") == "opensea-drops-api"
            for source in source_evidence
        ):
            continue
        persisted.append({
            "project_name": row["project_name"],
            "chain": row["chain_identifier"],
            "contract_or_mint_address": row["contract_or_mint_address"],
            "mint_window_utc": row["expected_mint_at"],
            "price_claim": row["mint_price_claim"],
            "price_status": row["mint_price_status"],
            "verification_status": row["verification_status"],
            "risk_assessment": row["risk_assessment"],
            "source_url": row["source_url"],
        })
    cycle["opensea_persisted_leads"] = persisted
    return cycle


def run_preflight(*, run_cycle_on_success: bool = False, opener=urlopen) -> tuple[int, dict[str, Any]]:
    root = Path(__file__).resolve().parents[2]
    load_env_file(root / ".env")
    api_key = os.getenv("OPENSEA_API_KEY", "").strip()
    report: dict[str, Any] = {
        "endpoint": DROP_LIST_URL,
        "request_params": {},
        "request_count": 0,
        "authentication": "NOT_TESTED",
        "http_status": None,
        "content_type": None,
        "response_bytes": 0,
        "response_time_ms": None,
        "response_schema": None,
        "drops_retrieved": 0,
        "error_body_sanitized": None,
        "campaign_cycle": "NOT_RUN",
    }
    if not api_key:
        report.update(authentication="BLOCKED_NO_KEY", error="OPENSEA_API_KEY is not configured")
        return 2, report

    try:
        params = _request_params()
        report["request_params"] = params
    except ValueError as exc:
        report.update(authentication="NOT_TESTED", error=str(exc))
        return 2, report

    url = f"{DROP_LIST_URL}?{urlencode(params)}"
    request = Request(url, headers={
        "Accept": "application/json",
        "User-Agent": "Binance-NFT-Hunter/0.1",
        "x-api-key": api_key,
    })
    report["request_count"] = 1
    raw = b""
    started = time.monotonic()
    try:
        with opener(request, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            report["http_status"] = int(response.status)
            report["content_type"] = response.headers.get("Content-Type")
    except HTTPError as exc:
        report["http_status"] = int(exc.code)
        report["content_type"] = exc.headers.get("Content-Type") if exc.headers else None
        try:
            raw = exc.read(MAX_RESPONSE_BYTES + 1)
        except OSError:
            raw = b""
        report["response_bytes"] = len(raw)
        report["response_time_ms"] = round((time.monotonic() - started) * 1000, 1)
        report["response_schema"] = _error_response_shape(raw)
        report["error_body_sanitized"] = _safe_error_body(raw, api_key)
        report["authentication"] = (
            "REJECTED" if exc.code == 401 else "KEY_ACCEPTED_BUT_FORBIDDEN" if exc.code == 403
            else "NOT_CONFIRMED"
        )
        exc.close()
        return 1, report
    except (URLError, TimeoutError, OSError) as exc:
        report["response_time_ms"] = round((time.monotonic() - started) * 1000, 1)
        reason = exc.reason if isinstance(exc, URLError) else exc
        report["transport_error_category"] = type(reason).__name__
        code = getattr(reason, "winerror", None) or getattr(reason, "errno", None)
        if isinstance(code, int):
            report["transport_error_code"] = code
        report["authentication"] = "NOT_TESTED"
        return 1, report

    report["response_bytes"] = len(raw)
    report["response_time_ms"] = round((time.monotonic() - started) * 1000, 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        report["authentication"] = "NOT_CONFIRMED"
        report["error"] = "Response exceeded the diagnostic size limit"
        return 1, report
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        report["authentication"] = "NOT_CONFIRMED"
        report["error_body_sanitized"] = _safe_error_body(raw, api_key)
        report["error"] = "Response was not valid JSON"
        return 1, report

    shape = _response_shape(payload)
    report["response_schema"] = shape
    report["drops_retrieved"] = shape.get("drops_count") or 0
    if report["http_status"] != 200 or not shape["valid_drops_schema"]:
        report["authentication"] = "NOT_CONFIRMED"
        report["error_body_sanitized"] = _safe_error_body(raw, api_key)
        report["error"] = "Expected HTTP 200 with a JSON object containing a drops array"
        return 1, report

    report["authentication"] = "ACCEPTED_FOR_DROPS_ENDPOINT"
    if run_cycle_on_success:
        try:
            cycle = _run_cycle()
            report["campaign_cycle"] = {
                "status": cycle.get("status"),
                "requests": cycle.get("requests", 0),
                "leads_found": cycle.get("leads_found", 0),
                "new_leads_saved": cycle.get("new_leads", 0),
                "duplicates": cycle.get("duplicates", 0),
                "verified_free": 0,
                "source_metrics": cycle.get("source_metrics", []),
                "source_errors": cycle.get("source_errors", []),
                "opensea_persisted_leads": cycle.get("opensea_persisted_leads", []),
                "leads": [
                    {
                        "project_name": lead.get("project_name"),
                        "chain_id": lead.get("chain_id"),
                        "contract_address": lead.get("contract_address"),
                        "announcement_url": lead.get("announcement_url"),
                        "status": lead.get("status", "DEFERRED_MISSING_EVIDENCE"),
                        "price_status": lead.get("mint_price_status", "UNKNOWN"),
                        "mint_price_claim": lead.get("mint_price_claim"),
                    }
                    for lead in cycle.get("leads", [])
                ],
            }
        except Exception as exc:  # sanitized category only; never emit exception text
            report["campaign_cycle"] = {"status": "FAILED", "error_category": type(exc).__name__}
            return 1, report
    return 0, report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="One bounded OpenSea Drops API check")
    parser.add_argument(
        "--run-cycle-on-success", action="store_true",
        help="after HTTP 200 and valid Drops schema, run one existing bounded read-only campaign cycle",
    )
    args = parser.parse_args(argv)
    code, report = run_preflight(run_cycle_on_success=args.run_cycle_on_success)
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
