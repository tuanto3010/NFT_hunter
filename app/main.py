"""Minimal command-line entry point for the read-only discovery foundation."""

import argparse
import logging
import json
import os

from app.config import Settings
from app.database import Database, ScanAlreadyRunning, ScanOwnershipUnknown
from app.discovery.registry import build_adapters
from app.scheduler import ScanScheduler
from app.notifications.mode import build_notification_dispatcher
from app.discovery.campaign_feed import run_campaign_discovery_cycle, verification_profile
from app.analysis.evm_campaign import EvmCampaignVerifier
from app.discovery.chain_adapter import CHAIN_SPECS
from app.wallet.binance_wallet import BinanceAgenticWalletAdapter


def verify_new_campaign_leads(database: Database, cycle: dict, review_callback=None) -> None:
    """Locally verify up to three newly discovered leads with explicit source review."""
    candidates = [lead for lead in cycle.get("leads", [])
                  if lead.get("new") and lead.get("verification_profile_supported")]
    cycle["verification_attempts"] = 0
    cycle["verified_previewable"] = 0
    for lead in cycle.get("leads", []):
        if lead.get("new") and not lead.get("verification_profile_supported"):
            lead.update(status="DEFERRED_MISSING_EVIDENCE",
                        reason="Announcement lacks a supported explicit mint/supply profile")
    if not candidates:
        return

    reviewed = []
    for lead in candidates[:3]:
        prompt = "Review the official project site and confirm free/no-other-payment evidence? [y/N] "
        answer = (review_callback or input)(prompt).strip().lower()
        if answer == "y":
            reviewed.append(lead)
        else:
            lead.update(status="DEFERRED_MISSING_EVIDENCE",
                        reason="Operator source/payment review was not confirmed")
            cycle["deferred"] += 1
    candidates = reviewed
    if not candidates:
        return

    wallet = BinanceAgenticWalletAdapter()
    try:
        status = wallet.get_status()
        networks = wallet.get_supported_networks()
        addresses = wallet.get_addresses()
    except Exception:
        wallet_address = None
        status = None
        networks = []
        addresses = []
    wallet_address = next((item.address for item in addresses if str(item.network_id) == "56"), None)
    if (status is None or not status.connected or not wallet_address
            or not any(str(item.network_id) == "56" and item.family == "evm" for item in networks)):
        for lead in candidates[:3]:
            lead.update(status="DEFERRED_MISSING_EVIDENCE", reason="Connected Binance BNB account is unavailable")
        cycle["deferred"] += min(3, len(candidates))
        return

    rpc_url = os.getenv(CHAIN_SPECS["bnb"].rpc_env, "").strip()
    for lead in candidates:
        if not rpc_url:
            lead.update(status="DEFERRED_MISSING_EVIDENCE", reason="BNB RPC is not configured")
            cycle["deferred"] += 1
            continue
        connection = database.connect()
        try:
            row = connection.execute("SELECT * FROM observations WHERE id=?", (lead.get("id"),)).fetchone()
            if row is None:
                lead.update(status="DEFERRED_MISSING_EVIDENCE", reason="Saved lead disappeared")
                cycle["deferred"] += 1
                continue
            details = json.loads(row["details_json"] or "{}")
            campaign = details.get("campaign", {}) if isinstance(details, dict) else {}
            profile = verification_profile(type("CampaignObservation", (), {"details": details})())
            source_url = campaign.get("official_website")
            if not profile or not source_url:
                lead.update(status="DEFERRED_MISSING_EVIDENCE", reason="Official source or verifier profile is missing")
                cycle["deferred"] += 1
                continue
            checked = EvmCampaignVerifier(56, rpc_url).verify(
                contract_address=row["contract_address"], official_source_url=source_url,
                source_confirmed=True,
                source_confirmation="Operator reviewed project-controlled official campaign source.",
                wallet_address=wallet_address,
                payment_evidence="Operator reviewed verified source for no ERC-20 or other payment.",
                risk_assessment=str(row["risk_assessment"] or "high"),
                cached_source=campaign.get("source_cache"), **profile,
            )
            checked["provider_id"] = "binance_agentic"
            database.save_wallet_verification_evidence({
                "provider_id": "binance_agentic", "chain_identifier": "bnb", "chain_id": 56,
                "contract_address": row["contract_address"], "stage_id": str(checked.get("stage_id") or ""),
                "wallet_address": wallet_address, "block_number": checked.get("block_number"),
                "checked_at": checked.get("checked_at"),
                "verification_status": str(checked.get("status") or "UNKNOWN"),
                "mint_price_wei": checked.get("mint_price_wei"),
                "mint_currency_address": checked.get("mint_currency_address"),
                "mint_available": checked.get("mint_available"),
                "wallet_eligible": checked.get("wallet_eligible"), "source_url": source_url,
                "evidence": {
                    "nft_standard": checked.get("nft_standard"),
                    "contract_source_verified": checked.get("contract_source_verified"),
                    "source_code_hash": checked.get("source_code_hash"),
                    "mint_method": checked.get("mint_method"),
                    "simulation": checked.get("simulation"), "gas_limit": checked.get("gas_limit"),
                    "gas_price_wei": checked.get("gas_price_wei"),
                    "gas_cost_wei": checked.get("gas_cost_wei"),
                    "risk_assessment_before": checked.get("risk_assessment_before"),
                    "detail": checked.get("detail"),
                },
            })
            evidence_key = "56:%s:%s:%s" % (
                row["contract_address"].lower(), wallet_address.lower(), checked.get("block_number", "unknown"))
            history = campaign.get("verification_history") if isinstance(campaign.get("verification_history"), dict) else {}
            history[evidence_key] = checked
            campaign["verification_history"] = history
            campaign["wallet_checks"] = {**(campaign.get("wallet_checks") or {}), wallet_address.lower(): checked}
            campaign["latest_live_check"] = checked
            if checked.get("source_cache"):
                campaign["source_cache"] = checked["source_cache"]
            details["campaign"] = campaign
            connection.execute("UPDATE observations SET details_json=? WHERE id=?",
                               (json.dumps(details, sort_keys=True), row["id"]))
            connection.commit()
            cycle["verification_attempts"] += 1
            lead.update(status=checked.get("status"), reason=checked.get("detail"),
                        block_number=checked.get("block_number"), mint_available=checked.get("mint_available"),
                        mint_price_wei=checked.get("mint_price_wei"), wallet_eligible=checked.get("wallet_eligible"),
                        gas_cost_wei=checked.get("gas_cost_wei"))
            if checked.get("status") == "VERIFIED_PREVIEWABLE":
                cycle["verified_previewable"] += 1
            elif checked.get("status") == "REJECTED_PAID":
                cycle["rejected_paid"] += 1
            elif checked.get("status") == "REJECTED_SOLD_OUT":
                cycle["rejected_sold_out"] += 1
            else:
                cycle["deferred"] += 1
        except Exception as exc:
            lead.update(status="DEFERRED_MISSING_EVIDENCE", reason="Live verifier failed (%s)" % type(exc).__name__)
            cycle["deferred"] += 1
        finally:
            connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Binance NFT Hunter read-only multi-chain scanner")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--scan", action="store_true", help="run one read-only scan across enabled chains")
    mode.add_argument("--schedule", action="store_true", help="scan enabled chains on the configured interval")
    mode.add_argument("--recover-legacy-scans", metavar="IDS",
                      help="backup and recover specific legacy scan IDs, e.g. 18,19")
    mode.add_argument("--campaign-cycle", action="store_true",
                      help="run one bounded campaign-announcement discovery cycle (no historical scanner)")
    parser.add_argument("--confirm-all-scanners-stopped", action="store_true",
                         help="operator confirmation required for legacy scan recovery")
    parser.add_argument("--recovery-reason", help="required audit reason for legacy scan recovery")
    args = parser.parse_args()
    settings = Settings.from_env()
    logging.basicConfig(level=getattr(logging, settings.log_level))
    database = Database(settings.database_path)
    if args.recover_legacy_scans is not None:
        if not args.confirm_all_scanners_stopped or not args.recovery_reason:
            logging.getLogger(__name__).error(
                "Legacy recovery requires --confirm-all-scanners-stopped and --recovery-reason"
            )
            return 2
        try:
            scan_ids = [int(value.strip()) for value in args.recover_legacy_scans.split(",") if value.strip()]
            result = database.recover_legacy_scans(
                scan_ids, args.recovery_reason, operator_confirmed_all_stopped=True
            )
        except (ValueError, RuntimeError, OSError) as exc:
            logging.getLogger(__name__).error("Legacy scan recovery refused: %s", exc)
            return 2
        logging.getLogger(__name__).warning(
            "Recovered legacy scan IDs %s as abandoned; backup=%s",
            ",".join(str(item) for item in result["recovered_ids"]), result["backup_path"],
        )
        return 0
    connection = database.connect()
    connection.close()
    if args.campaign_cycle:
        result = run_campaign_discovery_cycle(database)
        if result.get("status") == "ok":
            verify_new_campaign_leads(database, result)
        logging.getLogger(__name__).info("Campaign discovery cycle: %s", json.dumps(result, sort_keys=True))
        return 0 if result.get("status") == "ok" else 1
    adapters = build_adapters(settings, database.get_solana_watchlist_addresses)
    telegram_notifier = build_notification_dispatcher(settings.database_path)
    scheduler = ScanScheduler(adapters, database, settings.scan_interval_minutes,
                              settings.max_concurrent_scans, telegram_notifier=telegram_notifier)
    try:
        if args.scan:
            counts = scheduler.scan_once()
            logging.getLogger(__name__).info("Scan cycle complete: %s", counts)
        elif args.schedule:
            scheduler.run_forever()
    except (ScanAlreadyRunning, ScanOwnershipUnknown) as exc:
        logging.getLogger(__name__).error("Scan not started: %s", exc)
        return 2
    except KeyboardInterrupt:
        scheduler.stop()
        logging.getLogger(__name__).info("Shutdown requested")
    finally:
        telegram_notifier.close()
    if not args.scan and not args.schedule:
        parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

