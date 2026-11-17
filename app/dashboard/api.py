"""Read-only FastAPI endpoints and localhost dashboard server."""

import json
import base64
import os
import re
import subprocess
import threading
import time
from uuid import uuid4
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlsplit, urlunsplit

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import Settings
from app.budget import BudgetLedger
from app.analysis.meow_campaign import (
    CampaignVerificationError,
    MEOW_CARDS_ADDRESS,
    MeowCardsCampaignVerifier,
)
from app.analysis.evm_campaign import EvmCampaignVerificationError, EvmCampaignVerifier
from app.database import Database
from app.discovery.chain_adapter import CHAIN_SPECS, safe_rpc_source
from app.discovery.project_sources import ProjectObservation
from app.discovery.campaign_leads import manual_campaign_lead
from app.discovery.solana_scanner import SolanaScanner, b58decode, b58encode
from app.wallet.binance_wallet import BinanceAgenticWalletAdapter
from app.wallet.binance_wallet import BinanceWalletError
from app.wallet.transaction_builder import UnsafeMintRequest
from app.wallet.transaction_simulator import (
    UnknownMintPrice,
    WalletAwareMintPreviewService,
)
from app.notifications.mode import build_notification_dispatcher
from app.dashboard.rpc_diagnostics import BnbRpcDiagnostics
from app.discovery.campaign_feed import run_campaign_discovery_cycle
from app.discovery.opensea_drop_verifier import (
    OpenSeaDropClient, OpenSeaDropError, build_and_simulate_free_public_mint,
    configured_credentials, evaluate_drop,
)
from app.discovery.evm_scanner import EvmScanner, JsonRpcClient
from app.process_identity import process_instance_alive, process_start_identity
from app.main import verify_new_campaign_leads
from app.solana.core_asset_metadata import MetadataBlocked, fetch_and_validate_metadata

STATIC_DIR = Path(__file__).with_name("static")
NETWORKS = {
    "bnb": ("56", "BNB Smart Chain"),
    "ethereum": ("1", "Ethereum"),
    "base": ("8453", "Base"),
    "solana": ("solana-mainnet-beta", "Solana"),
    "robinhood": ("4663", "Robinhood Chain"),
}
_WHITELIST_LABELS = {"whitelist", "whitelisted", "whitelist_only", "allowlist", "allowlisted", "allowlist_only"}


def _price_is_zero(value: Any) -> bool:
    if value is None:
        return False
    match = re.fullmatch(r"\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))(?:\s+[A-Za-z]{2,10})?\s*", str(value))
    if not match:
        return False
    try:
        return Decimal(match.group(1)) == 0
    except InvalidOperation:
        return False


def _network_name(chain_identifier: str, chain_id: Any) -> str:
    key = str(chain_identifier or chain_id)
    for identifier, name in NETWORKS.values():
        if key == identifier:
            return name
    return "Unknown network"


def _parse_time(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _optional_utc_timestamp(payload: Dict[str, Any], key: str) -> Optional[str]:
    value = str(payload.get(key, "")).strip()
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=400, detail=key + " must be ISO 8601 with a timezone") from None
    if parsed.tzinfo is None:
        raise HTTPException(status_code=400, detail=key + " must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat()


def _scanner_state(database: Database, interval_minutes: int) -> Dict[str, Any]:
    connection = database.connect()
    try:
        latest_scan = connection.execute(
            "SELECT started_at, finished_at, status FROM scan_history ORDER BY id DESC LIMIT 1"
        ).fetchone()
    finally:
        connection.close()

    state = database.get_scheduler_state()
    if not state:
        status = "not_started" if not latest_scan else (
            "scanning" if latest_scan["status"] == "running" else "idle"
        )
        return {
            "status": status,
            "last_scan_time": (latest_scan["finished_at"] or latest_scan["started_at"]) if latest_scan else None,
            "last_scan_status": latest_scan["status"] if latest_scan else None,
            "next_scheduled_scan": None,
        }

    heartbeat = _parse_time(state["heartbeat_at"])
    configured_interval = max(1, int(state["interval_seconds"] or interval_minutes * 60))
    stale_after = timedelta(seconds=max(configured_interval * 2, 120))
    is_stale = heartbeat is None or datetime.now(timezone.utc) - heartbeat > stale_after
    status = "stale" if is_stale and state["status"] in {"starting", "scanning", "scheduled"} else state["status"]
    latest_time = (latest_scan["finished_at"] or latest_scan["started_at"]) if latest_scan else None
    next_scan = state["next_scan_at"] if status == "scheduled" else None
    return {
        "status": status,
        "last_scan_time": latest_time,
        "last_scan_status": latest_scan["status"] if latest_scan else None,
        "next_scheduled_scan": next_scan,
    }


def _row_to_discovery(row: Any) -> Dict[str, Any]:
    identifier = row["chain_identifier"] or str(row["chain_id"])
    details = _safe_details(row["details_json"])
    mint_price = row["mint_price"] if row["mint_price"] is not None else details.get("mint_price")
    eligibility = row["eligibility"]
    if not eligibility or eligibility == "unknown":
        eligibility = details.get("eligibility", "unknown")
    if isinstance(eligibility, dict):
        eligibility = eligibility.get("status", "unknown")
    verification = row["verification_status"]
    if not verification or verification == "unknown":
        verification = details.get("verification_status", "unknown")
    return {
        "id": row["id"],
        "chain_id": str(row["chain_id"]),
        "project_name": row["project_name"] or "Unnamed project",
        "chain_identifier": identifier,
        "network": _network_name(identifier, row["chain_id"]),
        "address": row["contract_address"],
        "nft_standard": row["nft_standard"] or "unknown",
        "mint_price": mint_price,
        "native_gas_token": row["native_gas_token"] or None,
        "eligibility": eligibility,
        "verification_status": verification,
        "risk_assessment": row["risk_assessment"] or "high",
        "verified": bool(row["verified"]),
        "discovery_time": row["first_seen_at"] or row["observed_at"],
        "first_seen_at": row["first_seen_at"],
        "source_url": safe_rpc_source(row["source_url"]),
        "source_last_checked_at": details.get("last_checked_at") or row["observed_at"],
        "opportunity_status": details.get("opportunity_status", "UNKNOWN"),
        "announcement_status": details.get("announcement_status", "UNKNOWN"),
        "announcement_date": details.get("announcement_date"),
        "mint_date": details.get("mint_date") or details.get("launch_at"),
        "mint_price_status": details.get("mint_price_status", details.get("price_status", "UNKNOWN")),
        "mint_price_claim": details.get("price_claim") or details.get("mint_price_claim") or mint_price,
        "allowlist_status": details.get("allowlist_status", "UNKNOWN"),
        "address_kind": details.get("address_kind", "unknown"),
        "mint_status": details.get("mint_status", "unknown"),
        "mint_infrastructure_verified": details.get("mint_infrastructure_verified") is True,
        "mint_infrastructure_status": details.get("address_validation_status", "UNKNOWN"),
    }


def _safe_details(raw: str) -> Dict[str, Any]:
    try:
        details = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    if not isinstance(details, dict):
        return {}
    allowed = {
        "chain", "nft_standard", "mint_price", "native_gas_token", "eligibility",
        "verification_status", "risk_assessment", "mint_functions", "metadata",
        "mint_price_verified",
        "creators", "authorities", "collection", "supply", "decimals",
        "mint_authority", "freeze_authority",
        "mint_status", "launch_at", "mint_price_status", "price_claim",
        "price_claim_status", "price_claim_source", "mint_price_claim", "mint_price_claim_status",
        "announcement_url", "announcement_date", "announcement_date_status",
        "announcement_status", "mint_date", "mint_date_status",
        "allowlist_status", "allowlist_status_claim", "allowlist_status_claim_verified",
        "opportunity_status", "address_validation_status",
        "address_validation_reason", "mint_infrastructure_verified",
        "network_fee", "priority_fee",
        "rent_or_account_cost", "manual_review_required", "official_source_url",
        "evidence", "classification", "telegram_qualified",
        "discovery_status", "price_status", "eligibility_status",
        "price_evidence", "eligibility_evidence", "eligibility_wallet",
        "verification_provenance", "official_source_verified",
        "address_kind", "mint_address", "collection_address", "mint_schedule", "machine",
        "watchlist", "last_checked_at", "source_confidence", "mint_price_verified", "campaign",
    }
    return {key: value for key, value in details.items() if key in allowed}


def _has_future_mint(item: Dict[str, Any], now: Optional[datetime] = None) -> bool:
    details = item.get("details", {})
    mint_status = str(item.get("mint_status") or details.get("mint_status") or "").lower()
    infrastructure_status = str(item.get("mint_infrastructure_status")
                                or details.get("address_validation_status") or "").upper()
    address_kind = str(item.get("address_kind") or details.get("address_kind") or "").lower()
    if mint_status not in {"upcoming", "active"}:
        return False
    if "REJECTED" in infrastructure_status or "COLLECTION_ONLY" in infrastructure_status:
        return False
    if address_kind in {"collection", "collection_mint", "nft_mint", "token_mint"}:
        return False
    scheduled = item.get("mint_date") or details.get("mint_date") or details.get("launch_at")
    parsed = _parse_time(scheduled)
    if parsed is None:
        return False
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc) > current.astimezone(timezone.utc)


def _matches_price_filter(item: Dict[str, Any], price_filter: str) -> bool:
    details = item.get("details", {})
    price_status = str(item.get("mint_price_status") or details.get("mint_price_status")
                       or details.get("price_status") or "UNKNOWN").upper()
    claim_status = str(details.get("price_claim_status") or "").upper()
    evidence = details.get("price_evidence") if isinstance(details.get("price_evidence"), dict) else {}
    verified_zero = (
        details.get("mint_price_verified") is True and _price_is_zero(item.get("mint_price"))
    ) or (
        price_status == "PRICE_VERIFIED_FREE" and evidence.get("verified") is True
        and str(evidence.get("amount_lamports", "")) == "0"
        and not evidence.get("spl_payment_mint")
    )
    if price_filter == "VERIFIED_FREE":
        return verified_zero
    if price_filter == "UNKNOWN":
        return item.get("mint_price") is None or price_status in {"UNKNOWN", "PRICE_UNKNOWN"}
    if price_filter == "CLAIMED":
        return claim_status in {"ZERO_PRICE_UNVERIFIED", "PAID_PRICE_UNVERIFIED"} or price_status in {
            "UNVERIFIED", "CLAIMED", "PRICE_CLAIMED", "ZERO_PRICE_UNVERIFIED", "PAID_PRICE_UNVERIFIED",
        }
    if price_filter == "VERIFIED_PAID":
        return price_status in {"PRICE_VERIFIED_PAID", "VERIFIED_PAID", "PAID"}
    return True


def _is_verified_opportunity(row: Any, item: Dict[str, Any]) -> bool:
    details = item.get("details", {})
    if str(row["chain_identifier"]) == "solana-mainnet-beta":
        from app.notifications.solana_alerts import is_verified_solana_alert
        return is_verified_solana_alert(type("SolanaDiscovery", (), {
            "chain_id": item.get("chain_id"), "contract_address": item.get("address"),
            "details": details, "risk_assessment": item.get("risk_assessment"),
        })())
    return (
        bool(row["verified"]) and str(row["verification_status"]).lower() == "verified"
        and str(row["risk_assessment"]).lower() in {"low", "medium"}
        and details.get("mint_price_verified") is True
        and _price_is_zero(item.get("mint_price"))
        and str(item.get("opportunity_status", "")).upper() in {"UPCOMING", "ACTIVE", "OPEN"}
    )


def _matches_discovery_status(item: Dict[str, Any], status: str) -> bool:
    details = item.get("details", {})
    if status == "UPCOMING":
        return _has_future_mint(item)
    if status == "ANNOUNCED":
        return details.get("announcement_status") == "VERIFIED" and details.get("official_source_verified") is True
    if status == "PRICE_UNKNOWN":
        return details.get("price_status") == "PRICE_UNKNOWN" or details.get("mint_price_status") == "UNKNOWN"
    if status == "VERIFIED_FREE":
        from app.notifications.solana_alerts import is_verified_solana_alert
        return is_verified_solana_alert(type("SolanaDiscovery", (), {
            "chain_id": item.get("chain_id"), "contract_address": item.get("address"),
            "details": details, "risk_assessment": item.get("risk_assessment"),
        })())
    return False


def create_app(database: Optional[Database] = None, database_path: Optional[Path] = None,
               scan_interval_minutes: int = 15,
               binance_wallet: Optional[Any] = None,
               solana_scanner: Optional[Any] = None,
               solana_notifier: Optional[Any] = None,
               notification_mode: Optional[str] = None,
               live_notifications_enabled: Optional[bool] = None,
               rpc_diagnostics: Optional[BnbRpcDiagnostics] = None) -> FastAPI:
    """Create API app over existing SQLite and read-only wallet/RPC services."""
    settings = Settings.from_env() if database is None and database_path is None else None
    db = database or Database(database_path or settings.database_path)
    interval = settings.scan_interval_minutes if settings else scan_interval_minutes
    application = FastAPI(title="Binance NFT Hunter Local Dashboard", docs_url=None, redoc_url=None)
    application.state.database = db
    application.state.budget_ledger = BudgetLedger(db)
    application.state.scan_interval_minutes = interval
    application.state.binance_wallet = binance_wallet or BinanceAgenticWalletAdapter()
    application.state.solana_scanner = solana_scanner or SolanaScanner(
        rpc_url=os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com"))
    application.state.wallet_preview_service = WalletAwareMintPreviewService(application.state.binance_wallet)
    application.state.solana_notifier = build_notification_dispatcher(
        db.path, sender=solana_notifier, mode=notification_mode,
        live_enabled=live_notifications_enabled,
    )
    application.state.bnb_rpc_diagnostics = rpc_diagnostics or BnbRpcDiagnostics.from_env()
    application.state.bnb_rpc_diagnostic_lock = threading.Lock()
    application.state.campaign_cycle_start_lock = threading.Lock()
    application.state.core_asset_preview_lock = threading.Lock()
    # Volatile only: generated Core asset signer material is never persisted or returned.
    application.state.core_asset_preview_signers = {}
    application.state.core_asset_execution_lock = threading.Lock()
    application.state.opensea_drop_check_lock = threading.Lock()
    application.state.meow_campaign_verifier = None
    application.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @application.get("/", include_in_schema=False)
    def dashboard_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @application.get("/api/dashboard")
    def dashboard_summary() -> Dict[str, Any]:
        connection = db.connect()
        try:
            rows = connection.execute("SELECT * FROM observations").fetchall()
            total = len(rows)
            details_list = [_safe_details(row["details_json"]) for row in rows]
            recent_cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
            recent = 0
            verified_opportunities = 0
            for row in rows:
                observed = _parse_time(row["first_seen_at"])
                if observed is not None and observed.astimezone(timezone.utc) >= recent_cutoff:
                    recent += 1
                item = _row_to_discovery(row)
                item["details"] = _safe_details(row["details_json"])
                verified_opportunities += _is_verified_opportunity(row, item)
            # A numeric zero alone is only a claim. Only explicit evidence accepted
            # by deterministic analysis may enter the verified-free total.
            free = sum(details.get("mint_price_verified") is True and _price_is_zero(
                           row["mint_price"] if row["mint_price"] is not None else details.get("mint_price"))
                       for row, details in zip(rows, details_list))
            whitelist = sum(
                str(row["eligibility"] if row["eligibility"] != "unknown" else details.get("eligibility", ""))
                .strip().lower() in _WHITELIST_LABELS for row, details in zip(rows, details_list)
            )
            unverified = sum(
                str(row["verification_status"] if row["verification_status"] != "unknown"
                    else details.get("verification_status", "unknown")).lower() != "verified"
                for row, details in zip(rows, details_list)
            )
        finally:
            connection.close()
        return {
            **_scanner_state(db, interval),
            "total_discovered": total,
            "new_discoveries_24h": recent,
            "verified_opportunities": verified_opportunities,
            "free_mint_opportunities": free,
            "whitelist_opportunities": whitelist,
            "unverified_opportunities": unverified,
            "is_demo_data": False,
        }

    @application.get("/api/rpc-diagnostics")
    def rpc_diagnostics_status() -> Dict[str, Any]:
        """Return only a masked endpoint label and the last manual test result."""
        return application.state.bnb_rpc_diagnostics.snapshot()

    @application.get("/api/budget")
    def shared_budget_status() -> Dict[str, Any]:
        """Read-only shared budget status; unknown outcomes fail closed."""
        return application.state.budget_ledger.snapshot()

    def campaign_job_payload(row: Any) -> Dict[str, Any]:
        if row is None:
            return {"status": "idle", "job_id": None, "started_at": None,
                    "duration_seconds": None, "counts": None, "source_errors": [], "failure_reason": None}
        try:
            result = json.loads(row["result_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            result = {}
        leads = result.get("leads", []) if isinstance(result, dict) else []
        return {
            "job_id": row["job_id"], "status": row["status"], "started_at": row["started_at"],
            "finished_at": row["finished_at"], "duration_seconds": row["duration_seconds"],
            "counts": result.get("counts") if isinstance(result, dict) else None,
            "source_errors": result.get("source_errors", []) if isinstance(result, dict) else [],
            "source_metrics": result.get("source_metrics", []) if isinstance(result, dict) else [],
            "failure_reason": row["error_detail"], "leads": leads,
        }

    def execute_campaign_job(job_id: str, started_at: str) -> None:
        began = time.monotonic()
        status = "completed"
        error_detail = None
        result: Dict[str, Any] = {}
        try:
            cycle = run_campaign_discovery_cycle(db)
            # The dashboard never attests to project ownership or payment terms.
            # Candidates requiring that operator review remain deferred here.
            verify_new_campaign_leads(db, cycle, review_callback=lambda _prompt: "n")
            source_errors = cycle.get("source_errors", [])
            if cycle.get("status") == "error":
                status = "failed"
                error_detail = "Campaign source request failed; see sanitized source error category."
            counts = {
                "leads_discovered": int(cycle.get("leads_found", 0)),
                "new_leads_saved": int(cycle.get("new_leads", 0)),
                "verified_candidates": int(cycle.get("verified_previewable", 0)),
                "verification_attempts": int(cycle.get("verification_attempts", 0)),
                "deferred": int(cycle.get("deferred", 0)),
                "rejected_paid": int(cycle.get("rejected_paid", 0)),
                "rejected_sold_out": int(cycle.get("rejected_sold_out", 0)),
                "expired": int(cycle.get("expired", 0)),
                "duplicates": int(cycle.get("duplicates", 0)),
            }
            result = {"source": cycle.get("source"), "status": cycle.get("status"),
                      "items_read": int(cycle.get("items_read", 0)), "requests": int(cycle.get("requests", 0)),
                      "counts": counts, "source_errors": source_errors,
                      "source_metrics": cycle.get("source_metrics", []),
                      "leads": cycle.get("leads", [])}
        except Exception as exc:
            status = "failed"
            error_detail = "Campaign cycle failed (%s)." % type(exc).__name__
            result = {"counts": {"leads_discovered": 0, "new_leads_saved": 0,
                                 "verified_candidates": 0, "verification_attempts": 0,
                                 "deferred": 0, "rejected_paid": 0, "rejected_sold_out": 0,
                                 "expired": 0, "duplicates": 0},
                      "source_errors": [{"error_type": type(exc).__name__}], "leads": []}
        finished_at = datetime.now(timezone.utc).isoformat()
        db.finish_campaign_job(job_id, status, finished_at, time.monotonic() - began,
                               json.dumps(result, sort_keys=True), error_detail)

    @application.post("/api/campaign-cycle")
    def start_campaign_cycle() -> Dict[str, Any]:
        """Start exactly one bounded campaign feed cycle inside this API process."""
        if not application.state.campaign_cycle_start_lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="A campaign cycle is already starting")
        try:
            job_id = uuid4().hex
            started_at = datetime.now(timezone.utc).isoformat()
            identity = process_start_identity(os.getpid())
            if identity is None:
                raise HTTPException(status_code=503, detail="Could not establish dashboard process identity")
            try:
                db.start_campaign_job(job_id, os.getpid(), identity, process_instance_alive, started_at)
            except Exception as exc:
                if type(exc).__name__ in {"ScanAlreadyRunning", "ScanOwnershipUnknown"}:
                    raise HTTPException(status_code=409, detail="A campaign cycle is already running or owner status is unknown") from None
                raise
            worker = threading.Thread(target=execute_campaign_job, args=(job_id, started_at),
                                      name="nft-campaign-cycle", daemon=True)
            try:
                worker.start()
            except RuntimeError:
                # Do not leave a persisted job stuck in running if the OS refuses
                # to create its worker thread.
                db.finish_campaign_job(
                    job_id, "failed", datetime.now(timezone.utc).isoformat(), 0.0,
                    json.dumps({"counts": {}, "source_errors": [], "leads": []}, sort_keys=True),
                    "Campaign worker could not be started.",
                )
                raise HTTPException(status_code=503, detail="Campaign worker could not be started") from None
            row = db.get_campaign_job(job_id)
            return campaign_job_payload(row)
        finally:
            application.state.campaign_cycle_start_lock.release()

    @application.get("/api/campaign-cycle/status")
    def campaign_cycle_status() -> Dict[str, Any]:
        return campaign_job_payload(db.latest_campaign_job())

    @application.get("/api/campaign-cycle/jobs/{job_id}")
    def campaign_cycle_job(job_id: str) -> Dict[str, Any]:
        row = db.get_campaign_job(job_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Campaign job not found")
        return campaign_job_payload(row)

    @application.get("/api/campaigns")
    def campaign_results(chain: Optional[str] = Query(default=None),
                         status: Optional[str] = Query(default=None),
                         limit: int = Query(default=100, ge=1, le=500)) -> Dict[str, Any]:
        """Return only saved campaign leads; no campaign claim is promoted here."""
        if chain is not None and chain.lower() not in NETWORKS:
            raise HTTPException(status_code=400, detail="Unsupported network filter")
        connection = db.connect()
        try:
            rows = connection.execute("SELECT * FROM observations ORDER BY observed_at DESC, id DESC").fetchall()
        finally:
            connection.close()
        items = []
        address_keys: set[tuple[str, str]] = set()
        for row in rows:
            try:
                details = json.loads(row["details_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                continue
            campaign = details.get("campaign") if isinstance(details, dict) else None
            if not isinstance(campaign, dict):
                continue
            chain_id = str(row["chain_id"])
            network = _network_name(row["chain_identifier"], chain_id)
            if chain and network.lower() != NETWORKS[chain.lower()][1].lower():
                continue
            check = campaign.get("latest_live_check") if isinstance(campaign.get("latest_live_check"), dict) else {}
            mint_status = check.get("status") or campaign.get("verification_status") or "UNKNOWN"
            price_status = ("VERIFIED_FREE" if check.get("contract_source_verified") is True
                            and check.get("mint_price_wei") == "0" else campaign.get("mint_price_status", "UNKNOWN"))
            item = {
                "id": int(row["id"]), "project_name": row["project_name"] or "Unnamed campaign",
                "chain_id": chain_id, "network": network, "contract_address": row["contract_address"],
                "source_url": campaign.get("official_website") or campaign.get("announcement_url") or row["source_url"],
                "mint_status": mint_status, "price_status": price_status,
                "risk_status": str(row["risk_assessment"] or "high").upper(),
                "compatible_wallet": check.get("provider_id") or ("EVM wallet required" if chain_id in {"1", "56", "8453"} else "Network-specific wallet required"),
                "last_checked": check.get("checked_at"),
                "discovered_at": row["observed_at"],
                "source_verification": campaign.get("source_verification", "UNKNOWN"),
            }
            if status and str(item["mint_status"]).upper() != status.upper():
                continue
            items.append(item)
            address_keys.add((chain_id, str(item["contract_address"]).lower()))
        for row in db.list_campaign_source_leads(limit=1000):
            chain_id = str(row["chain_identifier"])
            network = _network_name(chain_id, chain_id)
            if chain and network.lower() != NETWORKS[chain.lower()][1].lower():
                continue
            address = row["contract_or_mint_address"]
            if address and (chain_id, str(address).lower()) in address_keys:
                continue
            try:
                evidence = json.loads(row["evidence_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                evidence = {}
            item = {
                "id": "source-lead-%s" % int(row["id"]),
                "project_name": row["project_name"],
                "chain_id": chain_id,
                "network": network,
                "contract_address": address,
                "source_url": row["source_url"],
                "mint_status": row["verification_status"],
                "price_status": row["mint_price_status"],
                "risk_status": row["risk_assessment"],
                "compatible_wallet": "Not verified",
                "last_checked": None,
                "discovered_at": row["first_seen_at"],
                "expected_mint_at": row["expected_mint_at"],
                "mint_date_claim": row["mint_date_claim"],
                "mint_price_claim": row["mint_price_claim"],
                "source_verification": row["source_confidence"],
                "evidence": evidence,
            }
            evidence_chain = {"1": "ethereum", "56": "bnb", "8453": "base"}.get(chain_id, chain_id)
            wallet_rows = db.list_wallet_verification_evidence_for_contract(evidence_chain, str(address or ""), limit=10) if address else []
            item["wallet_verifications"] = []
            for wallet_row in wallet_rows:
                try:
                    wallet_evidence = json.loads(wallet_row["evidence_json"] or "{}")
                except (TypeError, json.JSONDecodeError):
                    wallet_evidence = {}
                wallet_item = {"provider_id": wallet_row["provider_id"],
                    "wallet_address": (str(wallet_row["wallet_address"])[:6] + "…" + str(wallet_row["wallet_address"])[-4:]),
                    "stage_id": wallet_row["stage_id"], "checked_at": wallet_row["checked_at"],
                    "status": wallet_row["verification_status"],
                    "wallet_eligible": None if wallet_row["wallet_eligible"] is None else bool(wallet_row["wallet_eligible"]),
                    "evidence": wallet_evidence}
                item["wallet_verifications"].append(wallet_item)
            if wallet_rows:
                item["last_checked"] = wallet_rows[0]["checked_at"]
                item["compatible_wallet"] = wallet_rows[0]["provider_id"]
            if status and str(item["mint_status"]).upper() != status.upper():
                continue
            items.append(item)
            if address:
                address_keys.add((chain_id, str(address).lower()))
        items.sort(key=lambda entry: str(entry.get("discovered_at") or ""), reverse=True)
        items = items[:limit]
        return {"items": items, "count": len(items), "is_demo_data": False}

    @application.post("/api/campaign-leads/{lead_id}/opensea-wallet-check")
    def opensea_wallet_check(lead_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Read-only OpenSea stage/eligibility check pinned to one selected wallet."""
        if not application.state.opensea_drop_check_lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="An OpenSea wallet check is already running")
        try:
            lead = next((item for item in db.list_campaign_source_leads(limit=1000)
                         if int(item["id"]) == lead_id), None)
            if lead is None or lead["source_name"] != "opensea-drops-api":
                raise HTTPException(status_code=404, detail="OpenSea campaign lead not found")
            if str(lead["chain_identifier"]) != "1":
                raise HTTPException(status_code=400, detail="This wallet check currently supports Ethereum drops only")
            contract = str(lead["contract_or_mint_address"] or "")
            if not re.fullmatch(r"0x[0-9a-fA-F]{40}", contract):
                raise HTTPException(status_code=409, detail="Saved lead has no valid full EVM contract address")
            provider_id = str(payload.get("provider_id", ""))
            wallet_address = str(payload.get("wallet_address", ""))
            chain_id = str(payload.get("chain_id", ""))
            if provider_id not in {"metamask", "binance_agentic"} or chain_id != "1":
                raise HTTPException(status_code=400, detail="Select a connected supported EVM wallet on Ethereum")
            if not re.fullmatch(r"0x[0-9a-fA-F]{40}", wallet_address):
                raise HTTPException(status_code=400, detail="Selected wallet address is invalid")
            if provider_id == "binance_agentic":
                try:
                    wallet = application.state.binance_wallet
                    network_ok = any(item.network_id == "1" and item.family == "evm"
                                     for item in wallet.get_supported_networks())
                    address_ok = any(item.network_id == "1" and item.address
                                     and item.address.lower() == wallet_address.lower()
                                     for item in wallet.get_addresses())
                    if not wallet.get_status().connected or not network_ok or not address_ok:
                        raise HTTPException(status_code=400, detail="Selected Binance address is not connected on Ethereum")
                except HTTPException:
                    raise
                except Exception:
                    raise HTTPException(status_code=503, detail="Selected Binance wallet is unavailable") from None
            try:
                details_evidence = json.loads(lead["evidence_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                details_evidence = {}
            slug = ""
            source_items = details_evidence.get("source_evidence", []) if isinstance(details_evidence, dict) else []
            for source_item in source_items:
                evidence = source_item.get("evidence", {}) if isinstance(source_item, dict) else {}
                candidate = evidence.get("collection_slug") if isinstance(evidence, dict) else None
                if isinstance(candidate, str) and re.fullmatch(r"[a-z0-9][a-z0-9-]{0,199}", candidate):
                    slug = candidate
                    break
            if not slug:
                parsed = urlsplit(str(lead["source_url"]))
                if parsed.hostname not in {"opensea.io", "www.opensea.io"}:
                    raise HTTPException(status_code=409, detail="OpenSea collection slug is unavailable in saved provenance")
                pieces = parsed.path.strip("/").split("/")
                slug = pieces[1] if len(pieces) >= 2 and pieces[0] == "collection" else ""
            if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,199}", slug):
                raise HTTPException(status_code=409, detail="OpenSea collection slug is unavailable in saved provenance")
            Settings.from_env()
            api_key, wallet_jwt, pinned_address = configured_credentials()
            checked_at = datetime.now(timezone.utc)
            # A chain-scoped read-only RPC probe is bounded, has zero retries,
            # and never uses the mint builder. Failures are recorded as unknown.
            rpc_state: Dict[str, Any] = {"status": "NOT_CONFIGURED", "block_number": None,
                                         "bytecode_present": None, "erc721": None, "erc1155": None,
                                         "source_verification_status": "NOT_CHECKED"}
            rpc_url = os.getenv(CHAIN_SPECS["ethereum"].rpc_env, "").strip()
            if rpc_url:
                try:
                    rpc = JsonRpcClient(rpc_url, timeout=8, retries=0)
                    if int(rpc.call("eth_chainId", []), 16) != 1:
                        rpc_state["status"] = "CHAIN_MISMATCH"
                    else:
                        block = int(rpc.call("eth_blockNumber", []), 16)
                        code = rpc.call("eth_getCode", [contract, hex(block)])
                        rpc_state.update(status="READS_OK", block_number=block,
                                         bytecode_present=isinstance(code, str) and code not in {"", "0x"})
                        for key, interface in (("erc721", "80ac58cd"), ("erc1155", "d9b67a26")):
                            try:
                                value = rpc.call("eth_call", [{"to": contract,
                                    "data": "0x01ffc9a7" + interface + "0" * 56}, hex(block)])
                                rpc_state[key] = int(value, 16) == 1
                            except Exception:
                                rpc_state[key] = None
                        try:
                            source_check = EvmScanner(rpc_url, CHAIN_SPECS["ethereum"], rpc=rpc).check_verification(contract)
                            rpc_state["source_verification_status"] = (
                                "VERIFIED" if isinstance(source_check, dict)
                                and source_check.get("status") == "verified"
                                and isinstance(source_check.get("abi"), list) else "UNKNOWN")
                        except Exception:
                            rpc_state["source_verification_status"] = "UNKNOWN"
                except Exception:
                    rpc_state["status"] = "RPC_UNAVAILABLE"
            api_client = OpenSeaDropClient(api_key, wallet_jwt) if api_key else None
            if api_client is None:
                checked = {"status": "BLOCKED_API_KEY_NOT_CONFIGURED",
                    "failure_reason": "API_KEY_NOT_CONFIGURED", "checked_at": checked_at.isoformat(),
                    "expires_at": (checked_at + timedelta(minutes=5)).isoformat(),
                    "chain_id": "1", "selected_wallet": wallet_address,
                    "expected_contract": contract, "collection_contract": None,
                    "stage_id": None, "stage_type": None, "stage_status": "UNKNOWN",
                    "stage_active": False, "api_price_wei_claim": None,
                    "api_currency_address_claim": None, "mint_price_status": "UNKNOWN",
                    "wallet_eligible": None, "mints_remaining": None, "requests_made": 0,
                    "transaction": None}
            else:
                checked = evaluate_drop(slug=slug, expected_contract=contract,
                                       selected_address=wallet_address, chain_id=chain_id,
                                       api_key=api_key, wallet_jwt=wallet_jwt,
                                       pinned_wallet_address=pinned_address, client=api_client)
            checked["provider_id"] = provider_id
            checked["contract_source_verified"] = rpc_state.get("source_verification_status") == "VERIFIED"
            checked["risk_assessment"] = str(lead["risk_assessment"] or "HIGH").upper()
            checked["independent_rpc"] = rpc_state
            # Build is permitted only when every existing deterministic gate
            # has already passed for this wallet and stage. Current OpenSea
            # claims alone never satisfy source verification or LOW risk.
            if api_client is not None and checked.get("stage_active") is True and checked.get("wallet_eligible") is True:
                try:
                    stage = {"uuid": checked.get("stage_id"), "stage_type": checked.get("stage_type"),
                             "price": checked.get("api_price_wei_claim"),
                             "price_currency_address": checked.get("api_currency_address_claim")}
                    prepared = build_and_simulate_free_public_mint(
                        client=api_client, slug=slug, selected_wallet=wallet_address,
                        expected_contract=contract, stage=stage, eligibility=checked.get("wallet_eligible"),
                        mints_remaining=checked.get("mints_remaining"),
                        evidence_expires_at=str(checked.get("expires_at") or ""),
                        risk_assessment=str(lead["risk_assessment"] or "HIGH"),
                        contract_source_verified=checked["contract_source_verified"],
                        erc721_verified=rpc_state.get("erc721") is True,
                        rpc_url=rpc_url, rpc=JsonRpcClient(rpc_url, timeout=8, retries=0) if rpc_url else None)
                    checked["status"] = "VERIFIED_PREVIEWABLE"
                    checked["unsigned_preview"] = prepared
                    checked["mint_price_status"] = "VERIFIED_ZERO_AT_SIMULATED_BLOCK"
                except OpenSeaDropError as exc:
                    checked["preview_blocker"] = exc.category
            checked["mint_price_status"] = (
                checked.get("mint_price_status") if checked.get("mint_price_status") != "API_CLAIMED_ZERO_UNVERIFIED"
                else "API_CLAIMED_ZERO_UNVERIFIED")
            db.save_wallet_verification_evidence({
                "provider_id": provider_id, "chain_identifier": "ethereum", "chain_id": "1",
                "contract_address": contract, "stage_id": str(checked.get("stage_id") or ""),
                "wallet_address": wallet_address, "block_number": rpc_state.get("block_number"),
                "checked_at": checked["checked_at"],
                "verification_status": checked.get("status", "UNKNOWN"),
                # OpenSea fields are claims; do not populate verification columns
                # for price or availability until independent on-chain evidence exists.
                "mint_price_wei": ("0" if checked.get("mint_price_status") == "VERIFIED_ZERO_AT_SIMULATED_BLOCK" else None),
                "mint_currency_address": ("0x" + "0" * 40 if checked.get("mint_price_status") == "VERIFIED_ZERO_AT_SIMULATED_BLOCK" else None),
                "mint_available": (True if checked.get("status") == "VERIFIED_PREVIEWABLE" else None),
                "wallet_eligible": checked.get("wallet_eligible"),
                "source_url": str(lead["source_url"]),
                "evidence": {"source": "OpenSea Drops API", "checked_at": checked["checked_at"],
                    "expires_at": checked["expires_at"], "stage_type": checked.get("stage_type"),
                    "stage_status": checked.get("stage_status"),
                    "price_status": checked.get("mint_price_status"),
                    "requests_made": checked.get("requests_made"),
                    "details_http_status": checked.get("details_http_status"),
                    "eligibility_http_status": checked.get("eligibility_http_status"),
                    "http_status": checked.get("http_status"),
                    "failure_reason": checked.get("failure_reason"),
                    "preview_blocker": checked.get("preview_blocker"),
                    "independent_rpc": rpc_state,
                    "contract_source_verified": checked.get("contract_source_verified"),
                    "risk_assessment": checked.get("risk_assessment")},
            })
            # Full address stays in the append-only evidence row; the dashboard
            # response is masked and contains no credential or bearer header.
            checked["selected_wallet"] = wallet_address[:6] + "…" + wallet_address[-4:]
            return checked
        except OpenSeaDropError as exc:
            raise HTTPException(status_code=503, detail=exc.category) from None
        finally:
            application.state.opensea_drop_check_lock.release()

    @application.post("/api/rpc-diagnostics/test")
    def test_bnb_rpc() -> Dict[str, Any]:
        """Perform exactly one read-only eth_chainId request; never starts a scan."""
        lock = application.state.bnb_rpc_diagnostic_lock
        if not lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="An RPC diagnostic request is already running")
        try:
            return application.state.bnb_rpc_diagnostics.test_once()
        finally:
            lock.release()

    @application.get("/api/wallets/binance")
    def binance_wallet_state() -> Dict[str, Any]:
        """Return read-only Binance wallet state; never return CLI output or errors."""
        wallet = application.state.binance_wallet
        try:
            status = wallet.get_status()
            if not status.connected:
                return {"provider_id": "binance_agentic", "status": "disconnected", "addresses": [], "balances": []}
            networks = wallet.get_supported_networks()
            addresses = wallet.get_addresses()
            balances = wallet.get_balances()
            hunter_network_ids = {"56", "1", "8453", "4663", "CT_501"}
            supported_ids = {str(item.network_id) for item in networks} & hunter_network_ids
            native_by_chain = {"56": "BNB", "1": "ETH", "8453": "ETH", "4663": "ETH", "CT_501": "SOL"}
            return {
                "provider_id": "binance_agentic",
                "status": "connected",
                "networks": [
                    {"chain_id": "solana-mainnet-beta" if str(item.network_id) == "CT_501" else str(item.network_id), "name": item.name, "family": item.family,
                     "native_token": item.native_token}
                    for item in networks if str(item.network_id) in supported_ids
                ],
                "addresses": [
                    {"chain_id": "solana-mainnet-beta" if str(item.network_id) == "CT_501" else str(item.network_id), "address": item.address}
                    for item in addresses if str(item.network_id) in supported_ids
                ],
                "balances": [
                    {"chain_id": str(item.network_id), "symbol": item.symbol, "amount": item.amount}
                    for item in balances
                    if item.symbol == native_by_chain.get(str(item.network_id))
                ],
            }
        except Exception:
            # Do not include exception strings: CLI failures can contain local or session details.
            return {"provider_id": "binance_agentic", "status": "unavailable", "addresses": [], "balances": []}

    @application.get("/api/wallets/solana/balance")
    def solana_wallet_balance(public_key: str = Query(..., min_length=32, max_length=44)) -> Dict[str, Any]:
        """Read only a canonical Solana public key's balance from the configured mainnet RPC."""
        try:
            decoded = b58decode(public_key)
            if len(decoded) != 32 or b58encode(decoded) != public_key:
                raise ValueError("invalid key")
            balance = application.state.solana_scanner.get_wallet_balance(public_key)
            return {"provider_id": "phantom", "public_key": public_key,
                    "chain_id": "solana-mainnet-beta", "network": "Solana mainnet-beta",
                    "native_token": "SOL", "balance_sol": balance["sol"],
                    "balance_lamports": balance["lamports"]}
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid Solana public key or unsupported network") from None
        except Exception:
            raise HTTPException(status_code=502, detail="Solana balance is unavailable") from None

    @application.post("/api/solana/self-created-core-asset-preview")
    def self_created_core_asset_preview(payload: Dict[str, Any]) -> Dict[str, Any]:
        """Build and simulate a distinct self-created Metaplex Core asset; never signs or sends."""
        provider_id = str(payload.get("provider_id", ""))
        chain_id = str(payload.get("chain_id", ""))
        wallet_address = str(payload.get("wallet_address", "")).strip()
        if (provider_id != "phantom" or chain_id != "solana-mainnet-beta"
                or payload.get("wallet_confirmed") is not True):
            return {"status": "BLOCKED_WALLET_NOT_CONFIRMED",
                    "reason": "Connect Phantom on Solana mainnet and confirm this exact public address before preview."}
        try:
            decoded = b58decode(wallet_address)
            if len(decoded) != 32 or b58encode(decoded) != wallet_address:
                raise ValueError("invalid key")
        except ValueError:
            return {"status": "BLOCKED_WALLET_NOT_CONFIRMED", "reason": "The Phantom public key is invalid."}
        metadata_uri = str(payload.get("metadata_uri", "")).strip()
        if not metadata_uri:
            return {"status": "BLOCKED_METADATA_URI",
                    "reason": "Host the supplied metadata JSON and image at public HTTPS URLs, then enter the JSON URL."}
        if not application.state.core_asset_preview_lock.acquire(blocking=False):
            return {"status": "BLOCKED_SIMULATION", "reason": "Another Core asset preview is already running."}
        try:
            now = time.monotonic()
            with application.state.core_asset_execution_lock:
                for key, item in list(application.state.core_asset_preview_signers.items()):
                    if item["expires_at"] <= now:
                        item["timer"].cancel()
                        item["secret"][:] = b"\x00" * len(item["secret"])
                        del application.state.core_asset_preview_signers[key]
            try:
                metadata = fetch_and_validate_metadata(metadata_uri)
            except MetadataBlocked as error:
                return {"status": "BLOCKED_METADATA_URI", "reason": str(error)}
            scanner_rpc = getattr(getattr(application.state.solana_scanner, "rpc", None), "endpoint", None)
            rpc_url = scanner_rpc or os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
            try:
                limit = min(int(os.getenv("CORE_ASSET_PREVIEW_MAX_LAMPORTS", "5000000")), 5000000)
            except ValueError:
                limit = 0
            request = {"walletAddress": wallet_address, "metadataUri": metadata_uri,
                       "metadata": metadata, "rpcUrl": rpc_url, "maxTotalLamports": limit}
            package_root = Path(__file__).resolve().parents[2]
            module_path = package_root / "app" / "solana" / "core_asset_preview.mjs"
            child_env = os.environ.copy()
            child_env["NFT_HUNTER_CORE_SIGNER_BRIDGE"] = "1"
            child_env["NFT_HUNTER_CORE_PREVIEW_BACKEND_CHILD"] = "1"
            try:
                child = subprocess.run(
                    ["node", str(module_path)], input=json.dumps(request), text=True,
                    capture_output=True, timeout=90, cwd=str(package_root), env=child_env, check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                return {"status": "BLOCKED_RPC", "reason": "The local Core preview engine or RPC did not respond."}
            try:
                result = json.loads(child.stdout)
            except (TypeError, json.JSONDecodeError):
                return {"status": "BLOCKED_SIMULATION", "reason": "The Core preview engine returned no valid result."}
            child_returncode = child.returncode
            child = None
            if child_returncode != 0:
                return {"status": "BLOCKED_SIMULATION", "reason": "The Core preview engine failed before simulation."}
            internal_key = result.pop("internal_asset_signer_secret_key", None)
            if result.get("status") == "PREVIEW_READY":
                if not isinstance(internal_key, str):
                    return {"status": "BLOCKED_SIMULATION", "reason": "Temporary asset signer could not be retained in memory."}
                try:
                    key_bytes = bytearray(__import__("base64").b64decode(internal_key, validate=True))
                    if len(key_bytes) != 64:
                        raise ValueError("invalid key size")
                except (ValueError, TypeError):
                    return {"status": "BLOCKED_SIMULATION", "reason": "Temporary asset signer material was invalid."}
                internal_key = None
                result["costs"]["maximum_lamports"] = str(limit)
                old = application.state.core_asset_preview_signers.pop(result["asset_address"], None)
                if old:
                    old["timer"].cancel()
                    old["secret"][:] = b"\x00" * len(old["secret"])
                expires_at = time.monotonic() + 900
                def expire_asset_signer(address=result["asset_address"]):
                    with application.state.core_asset_execution_lock:
                        expired = application.state.core_asset_preview_signers.pop(address, None)
                        if expired:
                            expired["secret"][:] = b"\x00" * len(expired["secret"])
                timer = threading.Timer(900, expire_asset_signer)
                timer.daemon = True
                application.state.core_asset_preview_signers[result["asset_address"]] = {
                    "secret": key_bytes, "wallet_address": wallet_address,
                    "metadata_uri": metadata_uri, "expires_at": expires_at, "timer": timer,
                    "preview_id": uuid4().hex, "transaction": result["transaction"],
                    "asset_address": result["asset_address"], "costs": result["costs"],
                    "network": result["network"], "approved": False, "submission_started": False,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
                timer.start()
                result["asset_signer_retention"] = "volatile server memory for 15 minutes; not returned or written to disk"
                result["preview_id"] = application.state.core_asset_preview_signers[result["asset_address"]]["preview_id"]
                result["expires_at"] = (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat()
            return result
        finally:
            application.state.core_asset_preview_lock.release()

    def _core_preview(preview_id: str, wallet_address: str) -> dict[str, Any]:
        for item in application.state.core_asset_preview_signers.values():
            if item["preview_id"] == preview_id and item["wallet_address"] == wallet_address:
                if item["expires_at"] <= time.monotonic():
                    raise HTTPException(status_code=410, detail="Preview expired; build a fresh preview")
                return item
        raise HTTPException(status_code=404, detail="Preview is unavailable; build a fresh preview")

    def _execution_child(payload: dict[str, Any]) -> dict[str, Any]:
        package_root = Path(__file__).resolve().parents[2]
        module_path = package_root / "app" / "solana" / "core_asset_execution.mjs"
        try:
            child = subprocess.run(["node", str(module_path)], input=json.dumps(payload), text=True,
                                   capture_output=True, timeout=60, cwd=str(package_root), check=False)
        except (OSError, subprocess.TimeoutExpired):
            raise HTTPException(status_code=502, detail="Local Solana execution preflight did not respond") from None
        try:
            result = json.loads(child.stdout)
        except (TypeError, json.JSONDecodeError):
            raise HTTPException(status_code=502, detail="Local Solana execution preflight returned no valid result") from None
        if child.returncode != 0 or result.get("ok") is not True:
            raise HTTPException(status_code=409, detail=str(result.get("reason", "Execution preflight failed"))[:240])
        return result

    @application.post("/api/solana/self-created-core-asset-approve")
    def approve_core_asset(payload: Dict[str, Any]) -> Dict[str, Any]:
        if payload.get("approved") is not True or payload.get("provider_id") != "phantom" \
                or payload.get("chain_id") != "solana-mainnet-beta":
            raise HTTPException(status_code=400, detail="Explicit approval for this Phantom mainnet transaction is required")
        wallet_address = str(payload.get("wallet_address", ""))
        if not application.state.core_asset_execution_lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="Another Core asset approval is in progress")
        try:
            preview = _core_preview(str(payload.get("preview_id", "")), wallet_address)
            if payload.get("asset_address") != preview["asset_address"]:
                raise HTTPException(status_code=409, detail="Approved asset address differs from the preview")
            if preview["approved"] or preview["submission_started"]:
                raise HTTPException(status_code=409, detail="This preview has already been approved or used")
            scanner_rpc = getattr(getattr(application.state.solana_scanner, "rpc", None), "endpoint", None)
            rpc_url = scanner_rpc or os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
            result = _execution_child({
                "operation": "prepare", "network": preview["network"], "rpcUrl": rpc_url,
                "walletAddress": wallet_address, "assetAddress": preview["asset_address"],
                "metadataUri": preview["metadata_uri"], "transactionBase64": preview["transaction"]["unsigned_transaction_base64"],
                "assetSignerSecret": base64.b64encode(preview["secret"]).decode("ascii"),
                "lastValidBlockHeight": preview["transaction"]["last_valid_block_height"],
                "expectedMessageSha256": preview["transaction"]["message_sha256"],
                "maxTotalLamports": preview["costs"]["maximum_lamports"],
            })
            preview["approved"] = True
            preview["submission_started"] = True
            preview["approved_at"] = datetime.now(timezone.utc).isoformat()
            return {"status": "PHANTOM_SIGNATURE_REQUIRED", "preview_id": preview["preview_id"],
                    "transaction_base64": result["transactionBase64"], "costs": result["costs"]}
        finally:
            application.state.core_asset_execution_lock.release()

    @application.post("/api/solana/self-created-core-asset-submit")
    def submit_core_asset(payload: Dict[str, Any]) -> Dict[str, Any]:
        if not application.state.core_asset_execution_lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="Another Core asset submission is in progress")
        try:
            preview = _core_preview(str(payload.get("preview_id", "")), str(payload.get("wallet_address", "")))
            if not preview["approved"] or not preview["submission_started"] or preview.get("submitted"):
                raise HTTPException(status_code=409, detail="This preview is not awaiting its one approved submission")
            scanner_rpc = getattr(getattr(application.state.solana_scanner, "rpc", None), "endpoint", None)
            rpc_url = scanner_rpc or os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
            signed = str(payload.get("signed_transaction_base64", ""))
            if len(signed) > 200_000:
                raise HTTPException(status_code=413, detail="Signed transaction exceeds size limit")
            verified = _execution_child({
                "operation": "validate", "network": preview["network"], "rpcUrl": rpc_url,
                "walletAddress": preview["wallet_address"], "assetAddress": preview["asset_address"],
                "metadataUri": preview["metadata_uri"], "transactionBase64": preview["transaction"]["unsigned_transaction_base64"],
                "signedTransactionBase64": signed,
                "assetSignerSecret": base64.b64encode(preview["secret"]).decode("ascii"),
                "lastValidBlockHeight": preview["transaction"]["last_valid_block_height"],
                "expectedMessageSha256": preview["transaction"]["message_sha256"],
                "maxTotalLamports": preview["costs"]["maximum_lamports"],
            })
            signature = b58encode(base64.b64decode(verified["signature"], validate=True))
            created = datetime.now(timezone.utc).isoformat()
            application.state.database.save_core_asset_transaction({
                "preview_id": preview["preview_id"], "asset_address": preview["asset_address"],
                "wallet_address": preview["wallet_address"], "network": preview["network"],
                "metadata_uri": preview["metadata_uri"], "transaction_digest": preview["transaction"]["message_sha256"],
                "signature": signature, "status": "SUBMISSION_ATTEMPTED",
                "estimated_total_lamports": verified["costs"]["total"], "created_at": created,
                "submitted_at": created, "confirmed_at": None, "failure_category": None,
            })
            preview["submitted"] = True
            # Exactly one sendTransaction request. An ambiguous result is never retried.
            from urllib.request import Request, urlopen
            rpc_request = Request(rpc_url, json.dumps({"jsonrpc": "2.0", "id": 1, "method": "sendTransaction",
                               "params": [verified["signedTransactionBase64"], {"encoding": "base64",
                               "skipPreflight": False, "preflightCommitment": "confirmed", "maxRetries": 0}]}).encode(),
                                  {"Content-Type": "application/json"})
            try:
                with urlopen(rpc_request, timeout=20) as response:
                    rpc_result = json.loads(response.read().decode("utf-8"))
                if rpc_result.get("error") or rpc_result.get("result") != signature:
                    raise RuntimeError("RPC did not return the transaction's expected signature")
            except Exception as error:
                application.state.database.update_core_asset_transaction(
                    preview["preview_id"], status="SUBMISSION_UNKNOWN", failure_category=type(error).__name__)
                preview["secret"][:] = b"\x00" * len(preview["secret"])
                preview["timer"].cancel()
                preview["submitted"] = True
                return {"status": "SUBMISSION_UNKNOWN", "signature": signature,
                        "asset_address": preview["asset_address"], "preview_id": preview["preview_id"],
                        "failure_category": type(error).__name__,
                        "detail": "RPC did not confirm the expected signature. Do not retry; check this saved signature on mainnet."}
            application.state.database.update_core_asset_transaction(preview["preview_id"], status="SUBMITTED")
            preview["secret"][:] = b"\x00" * len(preview["secret"])
            preview["timer"].cancel()
            return {"status": "SUBMITTED", "signature": signature, "asset_address": preview["asset_address"],
                    "estimated_total_lamports": verified["costs"]["total"]}
        finally:
            application.state.core_asset_execution_lock.release()

    @application.get("/api/solana/self-created-core-asset-status/{preview_id}")
    def core_asset_status(preview_id: str) -> Dict[str, Any]:
        item = application.state.database.get_core_asset_transaction(preview_id)
        if not item:
            raise HTTPException(status_code=404, detail="No submitted transaction record for this preview")
        signature_evidence = "NOT_CHECKED"
        asset_evidence = "NOT_CHECKED"
        if item["status"] in {"SUBMISSION_ATTEMPTED", "SUBMISSION_UNKNOWN", "SUBMITTED", "ASSET_VERIFICATION_PENDING", "CONFIRMED", "FAILED"} and item.get("signature"):
            rpc = application.state.solana_scanner.rpc
            try:
                statuses = rpc.call("getSignatureStatuses", [[item["signature"]], {"searchTransactionHistory": True}]).get("value", [])
                status = statuses[0] if statuses else None
                if status is None:
                    # Use the history-capable transaction method as a second
                    # read-only check. A null result from either method is
                    # still provider-scoped absence, never proof of failure.
                    try:
                        transaction = rpc.call("getTransaction", [item["signature"], {
                            "commitment": "confirmed", "maxSupportedTransactionVersion": 0,
                        }])
                    except Exception:
                        transaction = None
                    if isinstance(transaction, dict):
                        meta = transaction.get("meta") if isinstance(transaction.get("meta"), dict) else {}
                        status = {"confirmationStatus": "confirmed", "err": meta.get("err")}
                if status and status.get("err") is not None:
                    signature_evidence = "ONCHAIN_FAILURE"
                    application.state.database.update_core_asset_transaction(preview_id, status="FAILED", failure_category="ONCHAIN_ERROR")
                    item["status"] = "FAILED"
                elif status and status.get("confirmationStatus") in {"confirmed", "finalized"}:
                    signature_evidence = "CONFIRMED"
                    package_root = Path(__file__).resolve().parents[2]
                    child = subprocess.run(["node", str(package_root / "app" / "solana" / "core_asset_execution.mjs")],
                        input=json.dumps({"operation": "verify_asset", "rpcUrl": rpc.endpoint,
                                          "assetAddress": item["asset_address"]}), text=True,
                        capture_output=True, timeout=45, cwd=str(package_root), check=False)
                    verified_asset = json.loads(child.stdout)
                    if child.returncode == 0 and verified_asset.get("ok") and verified_asset.get("owner") == item["wallet_address"] \
                            and verified_asset.get("metadataUri") == item["metadata_uri"]:
                        asset_evidence = "ACCOUNT_FOUND"
                        application.state.database.update_core_asset_transaction(
                            preview_id, status="CONFIRMED", confirmed_at=datetime.now(timezone.utc).isoformat())
                        item["status"] = "CONFIRMED"
                    elif child.returncode == 0 and verified_asset.get("ok"):
                        asset_evidence = "OWNER_OR_METADATA_MISMATCH"
                        application.state.database.update_core_asset_transaction(preview_id, status="OWNERSHIP_UNVERIFIED")
                        item["status"] = "OWNERSHIP_UNVERIFIED"
                    else:
                        application.state.database.update_core_asset_transaction(preview_id, status="ASSET_VERIFICATION_PENDING")
                        item["status"] = "ASSET_VERIFICATION_PENDING"
                elif status is None:
                    signature_evidence = "RPC_HISTORY_UNAVAILABLE"
                    # A missing transaction-history result does not establish
                    # whether the send landed. Independently check the saved
                    # asset address so a pruned/unavailable signature index
                    # cannot hide a successfully created asset. This is a
                    # read-only account lookup; it never resubmits the tx.
                    package_root = Path(__file__).resolve().parents[2]
                    child = subprocess.run(["node", str(package_root / "app" / "solana" / "core_asset_execution.mjs")],
                        input=json.dumps({"operation": "verify_asset", "rpcUrl": rpc.endpoint,
                                          "assetAddress": item["asset_address"]}), text=True,
                        capture_output=True, timeout=45, cwd=str(package_root), check=False)
                    try:
                        verified_asset = json.loads(child.stdout)
                    except (TypeError, json.JSONDecodeError):
                        verified_asset = {}
                    if child.returncode == 0 and verified_asset.get("ok"):
                        asset_evidence = "ACCOUNT_FOUND"
                        if (verified_asset.get("owner") == item["wallet_address"]
                                and verified_asset.get("metadataUri") == item["metadata_uri"]):
                            application.state.database.update_core_asset_transaction(
                                preview_id, status="ASSET_VERIFIED_SIGNATURE_UNAVAILABLE")
                            item["status"] = "ASSET_VERIFIED_SIGNATURE_UNAVAILABLE"
                            item["confirmation_status"] = "asset_verified_signature_not_found"
                        else:
                            asset_evidence = "OWNER_OR_METADATA_MISMATCH"
                            application.state.database.update_core_asset_transaction(
                                preview_id, status="OWNERSHIP_UNVERIFIED")
                            item["status"] = "OWNERSHIP_UNVERIFIED"
                            item["confirmation_status"] = "asset_found_owner_or_metadata_mismatch"
                            item["asset_check_status"] = "OWNER_OR_METADATA_MISMATCH"
                    else:
                        item["confirmation_status"] = "rpc_history_unavailable"
                        reason = str(verified_asset.get("reason") or "").lower()
                        if "asset_account_not_found" in reason:
                            asset_evidence = item["asset_check_status"] = "ACCOUNT_NOT_FOUND"
                        elif "wrong_program_owner" in reason:
                            asset_evidence = item["asset_check_status"] = "WRONG_PROGRAM_OWNER"
                        elif "rpc is not solana mainnet-beta" in reason:
                            asset_evidence = item["asset_check_status"] = "RPC_NETWORK_MISMATCH"
                        else:
                            asset_evidence = item["asset_check_status"] = "ASSET_READ_FAILED"
                # A null status means this RPC did not find the signature in
                # its searched history; it does not mean the transaction is
                # currently pending or failed. Preserve SUBMISSION_UNKNOWN.
                if status is not None:
                    item["confirmation_status"] = status.get("confirmationStatus")
            except Exception as error:
                signature_evidence = "RPC_UNAVAILABLE"
                asset_evidence = "RPC_UNAVAILABLE"
                item["confirmation_status"] = "rpc_unavailable"
                item["asset_check_status"] = "RPC_UNAVAILABLE"
                item["diagnostic_error_category"] = type(error).__name__
        if signature_evidence != "NOT_CHECKED":
            application.state.database.save_core_asset_reconciliation_check(
                preview_id, checked_at=datetime.now(timezone.utc).isoformat(),
                signature_status=signature_evidence, asset_account_status=asset_evidence,
                evidence={"confirmation_status": item.get("confirmation_status"),
                          "asset_check_status": item.get("asset_check_status"),
                          "diagnostic_error_category": item.get("diagnostic_error_category")},
            )
        return {key: item[key] for key in ("preview_id", "asset_address", "wallet_address", "network",
                "signature", "status", "estimated_total_lamports", "created_at", "submitted_at", "confirmed_at",
                "confirmation_status", "asset_check_status", "diagnostic_error_category") if key in item}

    @application.get("/api/solana/self-created-core-asset-latest")
    def latest_core_asset_status() -> Dict[str, Any]:
        item = application.state.database.latest_core_asset_transaction()
        if not item:
            return {"status": "NO_SUBMISSION"}
        return {key: item[key] for key in ("preview_id", "asset_address", "wallet_address", "network",
                "signature", "status", "estimated_total_lamports", "created_at", "submitted_at", "confirmed_at")
                if key in item}

    @application.get("/api/solana/watchlist")
    def solana_watchlist() -> Dict[str, Any]:
        items = db.list_solana_watchlist()
        for item in items:
            item["official_source_verified"] = bool(item["official_source_verified"])
        return {"items": items}

    @application.post("/api/solana/watchlist")
    def register_solana_watchlist(payload: Dict[str, Any]) -> Dict[str, Any]:
        name = str(payload.get("project_name", "")).strip()[:160]
        address = str(payload.get("address", "")).strip()
        address_type = str(payload.get("address_type", ""))
        source_url = str(payload.get("source_url", "")).strip()
        if not name or address_type not in {"core_candy_machine", "token_mint", "collection_mint", "launchpad_candidate", "unknown"}:
            raise HTTPException(status_code=400, detail="Project name and a supported Solana address type are required")
        try:
            decoded = b58decode(address)
            if len(decoded) != 32 or b58encode(decoded) != address:
                raise ValueError("invalid address")
        except (ValueError, IndexError):
            raise HTTPException(status_code=400, detail="Invalid Solana address") from None
        values: dict[str, Optional[str]] = {}
        for key in ("official_website", "announcement_url", "launchpad_url"):
            value = str(payload.get(key, "")).strip()
            if value:
                parsed = urlsplit(value)
                if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
                    raise HTTPException(status_code=400, detail="Project URLs must be valid HTTPS URLs")
                values[key] = urlunsplit(("https", parsed.netloc, parsed.path, "", ""))[:1000]
            else:
                values[key] = None
        parsed = urlsplit(source_url)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            raise HTTPException(status_code=400, detail="A valid HTTPS provenance source URL is required")
        source_url = urlunsplit(("https", parsed.netloc, parsed.path, "", ""))[:1000]
        announcement_date = _optional_utc_timestamp(payload, "announcement_date")
        mint_date = _optional_utc_timestamp(payload, "mint_date")
        mint_price_claim = str(payload.get("mint_price_claim", "")).strip()[:80] or None
        allowlist_status = str(payload.get("allowlist_status", "UNKNOWN")).strip().upper()
        if allowlist_status not in {"UNKNOWN", "REQUIRED", "NOT_REQUIRED", "OPEN", "CLOSED"}:
            raise HTTPException(status_code=400, detail="Unsupported allowlist status")
        try:
            project_id = db.register_solana_project(name, values["official_website"], values["announcement_url"],
                                                    values["launchpad_url"], address, address_type, source_url,
                                                    announcement_date, mint_date, mint_price_claim, allowlist_status)
        except Exception:
            raise HTTPException(status_code=409, detail="Could not register this candidate") from None
        item = next(row for row in db.list_solana_watchlist() if row["address"] == address)
        return {"project_id": project_id, "item": item}

    @application.post("/api/solana/watchlist/{address}/review")
    def review_solana_watchlist(address: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        review_status = str(payload.get("review_status", "pending"))
        source_verified = payload.get("official_source_verified") is True
        reason = str(payload.get("blocked_reason", "")).strip()[:500]
        if review_status not in {"pending", "approved", "rejected"}:
            raise HTTPException(status_code=400, detail="Unsupported review status")
        item = next((row for row in db.list_solana_watchlist() if row["address"] == address), None)
        if item is None:
            raise HTTPException(status_code=404, detail="Watchlist candidate not found")
        if source_verified and not (item["official_website"] or item["announcement_url"]):
            raise HTTPException(status_code=400, detail="An official website or announcement URL is required")
        if not db.update_solana_watchlist_review(address, review_status, source_verified,
                reason or ("Manual review rejected this candidate" if review_status == "rejected" else
                           "Official source manually reviewed; run the selected Phantom eligibility check" if source_verified else
                           "Awaiting manual official-source review")):
            raise HTTPException(status_code=404, detail="Watchlist candidate not found")
        return {"ok": True, "item": next(row for row in db.list_solana_watchlist() if row["address"] == address)}

    @application.post("/api/solana/watchlist/{address}/eligibility")
    def verify_solana_watchlist_eligibility(address: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if payload.get("provider_id") != "phantom" or payload.get("chain_id") != "solana-mainnet-beta":
            raise HTTPException(status_code=400, detail="Eligibility must use the selected Phantom mainnet wallet")
        public_key = payload.get("wallet_address")
        try:
            decoded = b58decode(public_key) if isinstance(public_key, str) else b""
            if len(decoded) != 32 or b58encode(decoded) != public_key:
                raise ValueError("invalid public key")
        except (ValueError, IndexError):
            raise HTTPException(status_code=400, detail="Invalid Phantom public key") from None
        candidate = next((row for row in db.list_solana_watchlist() if row["address"] == address), None)
        if candidate is None:
            raise HTTPException(status_code=404, detail="Watchlist candidate not found")
        def blocked(status: str, reason: str) -> Dict[str, Any]:
            db.mark_solana_watchlist_checked(address, candidate.get("observation_id"), status, reason)
            return {"status": status, "blocked_reason": reason}
        if candidate["address_type"] != "core_candy_machine":
            return blocked("ELIGIBILITY_UNKNOWN", "No supported Candy Guard check for this address type")
        if candidate["review_status"] != "approved" or not candidate["official_source_verified"]:
            return blocked("ELIGIBILITY_UNKNOWN", "Manual review has not verified an official project source")
        observation = application.state.solana_scanner.inspect_core_candy_machine(address)
        if observation is None or observation.details.get("machine", {}).get("guard_config_status") != "VERIFIED_EMPTY":
            return blocked("ELIGIBILITY_UNKNOWN", "Candy Guard configuration is unsupported or unavailable")
        if observation.details.get("mint_status") != "active":
            return blocked("INELIGIBLE", "Candy Machine has no remaining active inventory")
        from dataclasses import replace
        details = dict(observation.details)
        details.update({"official_source_url": candidate["official_website"] or candidate["announcement_url"],
                        "official_source_verified": True, "verification_provenance": "solana_deterministic_onchain",
                        "eligibility_status": "ELIGIBLE", "eligibility_wallet": public_key,
                        "eligibility_evidence": {"verified": True, "status": "ELIGIBLE", "wallet": public_key,
                                                  "method": "empty_core_candy_guard",
                                                  "guard_account": observation.details["machine"]["mint_authority"]}})
        qualified = replace(observation, project_name=candidate["project_name"], source_url=candidate["source_url"],
                            details=details, risk_assessment="medium")
        db.mark_solana_watchlist_checked(address, candidate.get("observation_id"), "ELIGIBLE",
                                         "Eligible for the selected Phantom wallet; minting still requires separate manual review")
        connection = db.connect()
        try:
            connection.execute(
                "UPDATE observations SET project_name=?, source_url=?, mint_price='0', eligibility='ELIGIBLE', verification_status='verified', risk_assessment='medium', details_json=? WHERE chain_identifier='solana-mainnet-beta' AND contract_address=?",
                (candidate["project_name"], candidate["source_url"], json.dumps(details, sort_keys=True), address),
            )
            connection.commit()
        finally:
            connection.close()
        from app.notifications.solana_alerts import is_verified_solana_alert, format_verified_solana_alert, verified_alert_dedupe_key
        sent = False
        if is_verified_solana_alert(qualified) and application.state.solana_notifier is not None:
            try:
                result = application.state.solana_notifier.send_message(format_verified_solana_alert(qualified),
                                                                        verified_alert_dedupe_key(qualified))
                sent = bool(result.sent)
            except Exception:
                sent = False
        return {"status": "ELIGIBLE", "wallet_address": public_key,
                "mint_price": "0 SOL (guard configuration verified)",
                "telegram": "sent" if sent else "not_configured_or_not_sent",
                "evidence": details["eligibility_evidence"]}

    @application.post("/api/discoveries/{discovery_id}/solana-preview")
    def solana_preview(discovery_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Return a Solana-only evidence summary; never construct or execute a transaction."""
        if payload.get("provider_id") != "phantom":
            raise HTTPException(status_code=400, detail="Solana preview requires the selected Phantom wallet")
        public_key = payload.get("wallet_address")
        try:
            decoded = b58decode(str(public_key))
            if len(decoded) != 32 or b58encode(decoded) != public_key:
                raise ValueError
        except (ValueError, IndexError, TypeError):
            raise HTTPException(status_code=400, detail="Invalid Solana wallet public key") from None
        if payload.get("chain_id") != "solana-mainnet-beta":
            raise HTTPException(status_code=400, detail="Solana preview requires Solana mainnet-beta")
        connection = db.connect()
        try:
            row = connection.execute("SELECT * FROM observations WHERE id = ?", (discovery_id,)).fetchone()
        finally:
            connection.close()
        if row is None:
            raise HTTPException(status_code=404, detail="Discovery not found")
        if str(row["chain_id"]) != "solana-mainnet-beta" and str(row["chain_identifier"]) != "solana-mainnet-beta":
            raise HTTPException(status_code=400, detail="EVM discoveries cannot use the Solana preview")
        try:
            details = json.loads(row["details_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            details = {}
        if not isinstance(details, dict):
            details = {}
        return {
            "provider_id": "phantom", "wallet_address": public_key,
            "chain_id": "solana-mainnet-beta", "status": "BLOCKED_UNKNOWN_MINT_PRICE",
            "eligibility_status": "unknown",
            # The existing Solana scanner has no verified mint-price evidence
            # model; an untrusted DB value or flag is never enough to unblock it.
            "mint_price": {"status": "UNKNOWN", "amount": None, "token": "SOL"},
            "network_fee": {"status": "UNKNOWN", "amount": None, "token": "SOL"},
            "rent_or_account_cost": {"status": "UNKNOWN", "amount": None, "token": "SOL"},
            "total_estimated_cost": {"status": "UNKNOWN", "amount": None, "token": "SOL"},
            "evidence": {"project_name": row["project_name"] or "Unnamed project",
                         "mint_address": row["contract_address"], "nft_standard": row["nft_standard"] or "unknown",
                         "source_url": safe_rpc_source(row["source_url"]),
                         "metadata": details.get("metadata"), "mint_price_evidence": None},
            "detail": "No verified Solana mint-price evidence or transaction message is available. No mint transaction was constructed.",
        }

    @application.post("/api/discoveries/{discovery_id}/meow-campaign-check")
    def meow_campaign_check(discovery_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Run targeted read-only verification for the saved Meow Cards candidate."""
        if str(payload.get("provider_id", "")) != "binance_agentic" or str(payload.get("chain_id", "")) != "56":
            raise HTTPException(status_code=400, detail="This campaign check requires the selected Binance wallet on BNB Chain")
        connection = db.connect()
        try:
            row = connection.execute("SELECT * FROM observations WHERE id=?", (discovery_id,)).fetchone()
        finally:
            connection.close()
        if row is None or int(row["chain_id"]) != 56 or row["contract_address"].lower() != MEOW_CARDS_ADDRESS:
            raise HTTPException(status_code=404, detail="Supported campaign candidate not found")
        wallet_address = str(payload.get("wallet_address", ""))
        wallet = application.state.binance_wallet
        try:
            if not wallet.get_status().connected:
                raise HTTPException(status_code=400, detail="Selected Binance wallet is disconnected")
            supported = any(item.network_id == "56" and item.family == "evm"
                            for item in wallet.get_supported_networks())
            addresses = [item.address for item in wallet.get_addresses()
                         if item.network_id == "56" and item.address]
            if not supported or not any(item.lower() == wallet_address.lower() for item in addresses):
                raise HTTPException(status_code=400, detail="Selected address is not the connected Binance BNB wallet")
            handle = wallet.get_agent_id_handle()
            if not handle:
                raise HTTPException(status_code=400, detail="Selected Binance wallet has no claimed Agent ID")
            verifier = application.state.meow_campaign_verifier
            if verifier is None:
                Settings.from_env()
                verifier = MeowCardsCampaignVerifier(os.getenv("BNB_RPC_URL", "").strip())
                application.state.meow_campaign_verifier = verifier
            details = _safe_details(row["details_json"])
            campaign = details.get("campaign") if isinstance(details.get("campaign"), dict) else {}
            checked = verifier.verify(wallet_address, handle, campaign.get("source_cache"))
        except HTTPException:
            raise
        except CampaignVerificationError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        except Exception:
            raise HTTPException(status_code=502, detail="Read-only campaign check failed; no transaction was signed or sent") from None

        source_cache = checked.pop("source_cache", None)
        campaign["source_cache"] = source_cache
        campaign["latest_live_check"] = checked
        if isinstance(source_cache, dict) and isinstance(source_cache.get("abi"), list):
            details["mint_functions"] = [
                {"name": item.get("name"), "inputs": item.get("inputs", []),
                 "payable": item.get("stateMutability") == "payable",
                 "stateMutability": item.get("stateMutability")}
                for item in source_cache["abi"] if isinstance(item, dict)
                and item.get("type") == "function"
                and "mint" in str(item.get("name", "")).lower()
            ]
        wallet_checks = campaign.get("wallet_checks")
        if not isinstance(wallet_checks, dict):
            wallet_checks = {}
        wallet_checks[wallet_address.lower()] = checked
        campaign["wallet_checks"] = wallet_checks
        details["campaign"] = campaign
        connection = db.connect()
        try:
            standard = checked.get("nft_standard")
            if standard in {"ERC-721", "ERC-1155"} and checked.get("contract_source_verified") is True:
                connection.execute(
                    "UPDATE observations SET details_json=?, nft_standard=?, verification_status='verified', verified=1 WHERE id=?",
                    (json.dumps(details, sort_keys=True), standard, discovery_id),
                )
            else:
                connection.execute("UPDATE observations SET details_json=? WHERE id=?",
                                   (json.dumps(details, sort_keys=True), discovery_id))
            connection.commit()
        finally:
            connection.close()
        return checked

    @application.post("/api/discoveries/{discovery_id}/mint-preview")
    def mint_preview(discovery_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Prepare or simulate an EVM mint read-only, bound to the selected provider."""
        connection = db.connect()
        try:
            row = connection.execute("SELECT * FROM observations WHERE id = ?", (discovery_id,)).fetchone()
        finally:
            connection.close()
        if row is None:
            raise HTTPException(status_code=404, detail="Discovery not found")
        try:
            raw_details = json.loads(row["details_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            raw_details = {}
        details = raw_details if isinstance(raw_details, dict) else {}
        raw_chain_id = row["chain_id"]
        chain_id: int | str = int(raw_chain_id) if str(raw_chain_id).isdigit() else str(raw_chain_id)
        function_name = str(payload.get("function_name", ""))
        arguments = payload.get("arguments", {})
        if row["contract_address"].lower() == MEOW_CARDS_ADDRESS:
            provider_id = str(payload.get("provider_id", ""))
            wallet_address = str(payload.get("wallet_address", ""))
            campaign = details.get("campaign") if isinstance(details.get("campaign"), dict) else {}
            checks = campaign.get("wallet_checks") if isinstance(campaign.get("wallet_checks"), dict) else {}
            wallet_check = checks.get(wallet_address.lower())
            checked_at = _parse_time(wallet_check.get("checked_at")) if isinstance(wallet_check, dict) else None
            fresh = checked_at is not None and timedelta(0) <= datetime.now(timezone.utc) - checked_at <= timedelta(minutes=2)
            if (provider_id != "binance_agentic" or str(chain_id) != "56" or not isinstance(wallet_check, dict)
                    or not fresh or wallet_check.get("chain_id") != 56
                    or str(wallet_check.get("selected_wallet", "")).lower() != wallet_address.lower()
                    or wallet_check.get("agent_eligible") is not True
                    or wallet_check.get("contract_source_verified") is not True
                    or wallet_check.get("simulation") != "PASS"
                    or wallet_check.get("mint_price_wei") != "0"):
                raise HTTPException(status_code=400, detail="Fresh zero-price evidence for this exact Binance wallet is required")
            if function_name != "mint":
                raise HTTPException(status_code=400, detail="Only the verified Meow Cards mint method is allowed")
            expected_arguments = [1, int(wallet_check["agent_token_id"])]
            if arguments != expected_arguments:
                raise HTTPException(status_code=400, detail="Mint arguments must match the selected wallet's verified Agent ID evidence")
            source_cache = campaign.get("source_cache") if isinstance(campaign.get("source_cache"), dict) else {}
            abi = source_cache.get("abi") if isinstance(source_cache.get("abi"), list) else []
            details["mint_price"] = "0"
            details["mint_price_verified"] = True  # Request-local only; database stays wallet-neutral.
            details["mint_functions"] = [
                {"name": item.get("name"), "inputs": item.get("inputs", []),
                 "payable": item.get("stateMutability") == "payable",
                 "stateMutability": item.get("stateMutability")}
                for item in abi if isinstance(item, dict) and item.get("type") == "function"
                and item.get("name") == "mint"
            ]
        observation = ProjectObservation(
            chain_id=chain_id,
            contract_address=row["contract_address"],
            source_url=row["source_url"] or "",
            observed_at=row["observed_at"] or "",
            project_name=row["project_name"] or "",
            verified=bool(row["verified"]),
            details=details,
            nft_standard=row["nft_standard"] or "unknown",
            mint_price=row["mint_price"],
            native_gas_token=row["native_gas_token"] or "",
            eligibility=("eligible" if row["contract_address"].lower() == MEOW_CARDS_ADDRESS else row["eligibility"] or "unknown"),
            verification_status=row["verification_status"] or "unknown",
            risk_assessment=row["risk_assessment"] or "high",
        )
        try:
            result = application.state.wallet_preview_service.prepare(
                observation=observation,
                provider_id=str(payload.get("provider_id", "")),
                wallet_address=str(payload.get("wallet_address", "")),
                chain_id=str(payload.get("chain_id", "")),
                function_name=function_name,
                arguments=arguments,
            )
            return result.to_dict()
        except UnknownMintPrice as exc:
            return {
                "provider_id": str(payload.get("provider_id", "")),
                "wallet_address": str(payload.get("wallet_address", "")),
                "chain_id": str(payload.get("chain_id", "")),
                "status": "BLOCKED_UNKNOWN_MINT_PRICE",
                "eligibility_status": "unknown",
                "mint_price_status": "UNKNOWN",
                "detail": str(exc),
                "transaction": None,
            }
        except (UnsafeMintRequest, BinanceWalletError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        except Exception:
            # RPC/CLI exceptions are intentionally not reflected to local clients.
            raise HTTPException(status_code=502, detail="Read-only preview failed; no transaction was signed or sent") from None

    @application.post("/api/discoveries/{discovery_id}/evm-campaign-check")
    def evm_campaign_check(discovery_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Run a profile-driven read-only campaign check; never changes collection risk or price."""
        connection = db.connect()
        try:
            row = connection.execute("SELECT * FROM observations WHERE id=?", (discovery_id,)).fetchone()
        finally:
            connection.close()
        if row is None:
            raise HTTPException(status_code=404, detail="Campaign lead not found")
        chain_id = str(payload.get("chain_id", ""))
        if chain_id not in {"56", "1", "8453"} or str(row["chain_id"]) != chain_id:
            raise HTTPException(status_code=400, detail="Campaign chain does not match the selected supported EVM chain")
        address = str(payload.get("contract_address", ""))
        if address.lower() != str(row["contract_address"]).lower():
            raise HTTPException(status_code=400, detail="Campaign contract address does not match the saved lead")
        provider_id = str(payload.get("provider_id", ""))
        wallet_address = str(payload.get("wallet_address", ""))
        if provider_id not in {"binance_agentic", "metamask"}:
            raise HTTPException(status_code=400, detail="Unsupported selected wallet provider")
        if provider_id == "binance_agentic":
            try:
                wallet = application.state.binance_wallet
                supported = any(item.network_id == chain_id and item.family == "evm"
                                for item in wallet.get_supported_networks())
                addresses = [item.address for item in wallet.get_addresses()
                             if item.network_id == chain_id and item.address]
                if not wallet.get_status().connected or not supported or not any(
                        item.lower() == wallet_address.lower() for item in addresses):
                    raise HTTPException(status_code=400, detail="Selected address is not connected to Binance on this network")
            except HTTPException:
                raise
            except Exception:
                raise HTTPException(status_code=503, detail="Selected Binance wallet is unavailable") from None

        try:
            details = json.loads(row["details_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            details = {}
        if not isinstance(details, dict):
            details = {}
        campaign = details.get("campaign") if isinstance(details.get("campaign"), dict) else {}
        official_source_url = str(payload.get("official_source_url", "")).strip()
        known_sources = {str(campaign.get(key, "")).strip() for key in
                         ("official_website", "announcement_url", "mint_url") if campaign.get(key)}
        if official_source_url not in known_sources:
            raise HTTPException(status_code=400, detail="Official source URL must match the saved campaign provenance")
        Settings.from_env()
        rpc_url = os.getenv(CHAIN_SPECS[{
            "56": "bnb", "1": "ethereum", "8453": "base",
        }[chain_id]].rpc_env, "").strip()
        if not rpc_url:
            raise HTTPException(status_code=503, detail="RPC is not configured for the selected chain")
        try:
            verifier = EvmCampaignVerifier(int(chain_id), rpc_url)
            checked = verifier.verify(
                contract_address=address,
                official_source_url=official_source_url,
                source_confirmed=payload.get("source_confirmed") is True,
                source_confirmation=str(payload.get("source_confirmation", "")),
                wallet_address=wallet_address,
                mint_method=str(payload.get("mint_method", "")),
                mint_arguments=payload.get("mint_arguments", []),
                price_check=payload.get("price_check"),
                eligibility_check=payload.get("eligibility_check"),
                availability_check=payload.get("availability_check"),
                payment_evidence=str(payload.get("payment_evidence", "")),
                risk_assessment=str(row["risk_assessment"] or "high"),
                cached_source=campaign.get("source_cache") if isinstance(campaign.get("source_cache"), dict) else None,
            )
        except EvmCampaignVerificationError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        except Exception:
            raise HTTPException(status_code=502, detail="Read-only campaign verification failed; no transaction was sent") from None
        checked["provider_id"] = provider_id

        chain_identifier = {"56": "bnb", "1": "ethereum", "8453": "base"}[chain_id]
        try:
            db.save_wallet_verification_evidence({
                "provider_id": provider_id,
                "chain_identifier": chain_identifier,
                "chain_id": chain_id,
                "contract_address": address,
                "stage_id": str(payload.get("stage_id") or checked.get("stage_id") or ""),
                "wallet_address": wallet_address,
                "block_number": checked.get("block_number"),
                "checked_at": checked.get("checked_at") or datetime.now(timezone.utc).isoformat(),
                "verification_status": str(checked.get("status") or "UNKNOWN"),
                "mint_price_wei": checked.get("mint_price_wei"),
                "mint_currency_address": checked.get("mint_currency_address"),
                "mint_available": checked.get("mint_available"),
                "wallet_eligible": checked.get("wallet_eligible"),
                "source_url": official_source_url,
                "evidence": {
                    "nft_standard": checked.get("nft_standard"),
                    "contract_source_verified": checked.get("contract_source_verified"),
                    "source_code_hash": checked.get("source_code_hash"),
                    "mint_method": checked.get("mint_method"),
                    "simulation": checked.get("simulation"),
                    "gas_limit": checked.get("gas_limit"),
                    "gas_price_wei": checked.get("gas_price_wei"),
                    "gas_cost_wei": checked.get("gas_cost_wei"),
                    "risk_assessment_before": checked.get("risk_assessment_before"),
                    "detail": checked.get("detail"),
                },
            })
        except Exception:
            raise HTTPException(status_code=500,
                                detail="Wallet verification evidence could not be safely persisted") from None

        evidence_key = "%s:%s:%s:%s" % (
            chain_id, address.lower(), wallet_address.lower(), checked.get("block_number", "unknown"))
        history = campaign.get("verification_history")
        if not isinstance(history, dict):
            history = {}
        history[evidence_key] = checked
        campaign["verification_history"] = history
        latest_checks = campaign.get("wallet_checks")
        if not isinstance(latest_checks, dict):
            latest_checks = {}
        latest_checks[wallet_address.lower()] = checked
        campaign["wallet_checks"] = latest_checks
        campaign["source_cache"] = checked.get("source_cache")
        campaign["latest_live_check"] = checked
        details["campaign"] = campaign
        connection = db.connect()
        try:
            connection.execute("UPDATE observations SET details_json=? WHERE id=?",
                               (json.dumps(details, sort_keys=True), discovery_id))
            connection.commit()
        finally:
            connection.close()
        return checked

    @application.post("/api/campaign-leads/manual")
    def register_manual_campaign_lead(payload: Dict[str, Any]) -> Dict[str, Any]:
        """Save a campaign lead without implying the project's claims are verified."""
        try:
            lead = manual_campaign_lead(
                project_name=str(payload.get("project_name", "")),
                chain_id=int(payload.get("chain_id", 0)),
                contract_address=str(payload.get("contract_address", "")),
                official_url=str(payload.get("official_url", "")),
                announcement_url=str(payload.get("announcement_url", "")),
                mint_url=str(payload.get("mint_url", "")),
                announced_mint_date=str(payload.get("announced_mint_date", "")),
            )
        except (TypeError, ValueError, OverflowError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        inserted = db.save_observation(lead)
        connection = db.connect()
        try:
            row = connection.execute(
                "SELECT id FROM observations WHERE chain_id=? AND contract_address=?",
                (lead.chain_id, lead.contract_address),
            ).fetchone()
        finally:
            connection.close()
        return {"id": row["id"], "inserted": inserted,
                "verification_status": "UNKNOWN", "mint_price_status": "UNKNOWN",
                "eligibility_status": "UNKNOWN", "risk_assessment": "high"}

    @application.get("/api/discoveries")
    def discoveries(chain: Optional[str] = Query(default=None),
                    status: Optional[str] = Query(default=None),
                    price_status: Optional[str] = Query(default=None),
                    upcoming: bool = Query(default=False),
                    limit: int = Query(default=100, ge=1, le=500),
                    offset: int = Query(default=0, ge=0)) -> Dict[str, Any]:
        chain = chain.lower() if chain else None
        if chain is not None and chain not in NETWORKS:
            raise HTTPException(status_code=400, detail="Unsupported network filter")
        status = status.upper() if status else None
        if status is not None and status not in {"UPCOMING", "ANNOUNCED", "PRICE_UNKNOWN", "VERIFIED_FREE"}:
            raise HTTPException(status_code=400, detail="Unsupported discovery status filter")
        price_status = price_status.upper() if price_status else None
        if price_status is not None and price_status not in {"UNKNOWN", "CLAIMED", "VERIFIED_FREE", "VERIFIED_PAID"}:
            raise HTTPException(status_code=400, detail="Unsupported mint price filter")
        connection = db.connect()
        try:
            if chain:
                chain_identifier = NETWORKS[chain][0]
                where, params = "WHERE chain_identifier = ?", (chain_identifier,)
            else:
                where, params = "", ()
            filtered = bool(status or price_status or upcoming)
            if status and chain and chain != "solana":
                rows = []
            elif filtered:
                rows = connection.execute(
                    f"SELECT * FROM observations {where} ORDER BY observed_at DESC, id DESC", params
                ).fetchall()
            else:
                total = connection.execute(f"SELECT COUNT(*) FROM observations {where}", params).fetchone()[0]
                rows = connection.execute(
                    f"SELECT * FROM observations {where} ORDER BY observed_at DESC, id DESC LIMIT ? OFFSET ?",
                    (*params, limit, offset),
                ).fetchall()
        finally:
            connection.close()
        if filtered:
            matched = []
            for row in rows:
                item = _row_to_discovery(row)
                item["details"] = _safe_details(row["details_json"])
                if status and not _matches_discovery_status(item, status):
                    continue
                if price_status and not _matches_price_filter(item, price_status):
                    continue
                if upcoming and not _has_future_mint(item):
                    continue
                matched.append(item)
            total = len(matched)
            page = matched[offset:offset + limit]
            for item in page:
                item.pop("details", None)
            return {"total": total, "limit": limit, "offset": offset,
                    "items": page, "is_demo_data": False, "status_filter": status,
                    "price_status_filter": price_status, "upcoming_only": upcoming}
        return {"total": total, "limit": limit, "offset": offset,
                "items": [_row_to_discovery(row) for row in rows], "is_demo_data": False}

    @application.get("/api/source-health")
    def source_health(chain: Optional[str] = Query(default=None)) -> Dict[str, Any]:
        if chain is not None and chain.lower() not in NETWORKS:
            raise HTTPException(status_code=400, detail="Unsupported network filter")
        chain_identifier = NETWORKS[chain.lower()][0] if chain else None
        rows = db.get_source_health(chain_identifier)
        return {"items": [dict(row) for row in rows], "is_demo_data": False}

    @application.get("/api/discoveries/{discovery_id}")
    def discovery_details(discovery_id: int) -> Dict[str, Any]:
        connection = db.connect()
        try:
            row = connection.execute("SELECT * FROM observations WHERE id = ?", (discovery_id,)).fetchone()
        finally:
            connection.close()
        if row is None:
            raise HTTPException(status_code=404, detail="Discovery not found")
        return {**_row_to_discovery(row), "evidence": {
            "source_url": safe_rpc_source(row["source_url"]),
            "observed_at": row["observed_at"],
            "nft_standard": row["nft_standard"] or "unknown",
            "verification_status": row["verification_status"] or "unknown",
            "risk_assessment": row["risk_assessment"] or "high",
            "details": _safe_details(row["details_json"]),
        }, "is_demo_data": False}

    return application


app = create_app()


def main() -> None:
    import uvicorn

    settings = Settings.from_env()
    # Keep the local dashboard on the established project port. An explicit
    # environment override still takes precedence.
    port = int(os.getenv("DASHBOARD_PORT", "8767"))
    if not 1 <= port <= 65535:
        raise ValueError("DASHBOARD_PORT must be between 1 and 65535")
    uvicorn.run(create_app(database_path=settings.database_path,
                           scan_interval_minutes=settings.scan_interval_minutes),
                host="127.0.0.1", port=port, reload=False, access_log=False)


if __name__ == "__main__":
    main()
