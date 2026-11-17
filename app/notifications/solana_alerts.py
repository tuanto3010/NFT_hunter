"""Strict deterministic gate and formatter for verified Solana opportunity alerts."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import hashlib
from typing import Any
from urllib.parse import urlsplit

from app.discovery.solana_scanner import b58decode, b58encode


def is_verified_solana_alert(observation: Any) -> bool:
    """Allow alerts only when on-chain zero price and wallet eligibility are proven.

    Marketplace and AI claims cannot satisfy this gate. A future deterministic
    Candy Guard verifier must attach all required evidence explicitly.
    """
    if str(getattr(observation, "chain_id", "")) != "solana-mainnet-beta":
        return False
    details = getattr(observation, "details", None)
    if not isinstance(details, dict):
        return False
    if details.get("discovery_status") != "DISCOVERED":
        return False
    if details.get("price_status") != "PRICE_VERIFIED_FREE":
        return False
    if details.get("eligibility_status") != "ELIGIBLE":
        return False
    if details.get("verification_provenance") != "solana_deterministic_onchain":
        return False
    if details.get("official_source_verified") is not True:
        return False
    official_url = details.get("official_source_url")
    if not isinstance(official_url, str) or urlsplit(official_url).scheme != "https" or not urlsplit(official_url).netloc:
        return False
    try:
        price = Decimal(str(details.get("mint_price")))
    except (InvalidOperation, TypeError, ValueError):
        return False
    price_evidence = details.get("price_evidence")
    if price != 0 or not isinstance(price_evidence, dict):
        return False
    if (price_evidence.get("verified") is not True
            or price_evidence.get("method") != "on_chain_guard"
            or price_evidence.get("amount_lamports") != 0
            or price_evidence.get("spl_payment_mint") is not None):
        return False

    wallet = details.get("eligibility_wallet")
    eligibility_evidence = details.get("eligibility_evidence")
    if not isinstance(wallet, str) or not isinstance(eligibility_evidence, dict):
        return False
    try:
        decoded = b58decode(wallet)
    except (ValueError, IndexError):
        return False
    if len(decoded) != 32 or b58encode(decoded) != wallet:
        return False
    if (eligibility_evidence.get("verified") is not True
            or eligibility_evidence.get("status") != "ELIGIBLE"
            or eligibility_evidence.get("wallet") != wallet):
        return False
    if getattr(observation, "risk_assessment", "high") == "high":
        return False
    if details.get("mint_status") not in {"active", "upcoming"}:
        return False
    return True


def format_verified_solana_alert(observation: Any) -> str:
    """Format evidence-only plain text; do not include credentials or AI claims."""
    if not is_verified_solana_alert(observation):
        raise ValueError("Solana alert evidence did not pass deterministic qualification")
    details = observation.details
    clean = lambda value: " ".join(str(value or "unknown").split())[:500]
    fee = details.get("network_fee") or {}
    priority = details.get("priority_fee") or {}
    rent = details.get("rent_or_account_cost") or {}
    return "\n".join((
        "Verified Solana NFT opportunity",
        "Project: " + clean(getattr(observation, "project_name", "")),
        "Mint/collection: " + clean(observation.contract_address),
        "Official source: " + clean(details.get("official_source_url")),
        "Status/date: %s / %s" % (clean(details.get("mint_status")), clean(details.get("launch_at"))),
        "Mint price: verified 0 SOL; gas is separate",
        "Eligibility: verified for Phantom address " + _mask_wallet(details.get("eligibility_wallet")),
        "Network fee: %s SOL; priority fee: %s SOL; rent/account cost: %s SOL" % (
            clean(fee.get("amount")), clean(priority.get("amount")), clean(rent.get("amount"))),
        "Evidence: on-chain Candy Guard check plus verified official source",
    ))


def is_new_solana_discovery(observation: Any) -> bool:
    details = getattr(observation, "details", None)
    return bool(
        str(getattr(observation, "chain_id", "")) == "solana-mainnet-beta"
        and isinstance(details, dict)
        and details.get("discovery_status") == "DISCOVERED"
        and details.get("address_validation_status") not in {
            "REJECTED_HISTORICAL_NFT_MINT",
            "COLLECTION_ONLY_NOT_MINT_INFRASTRUCTURE",
            "GUARD_ONLY_NOT_MINT_INFRASTRUCTURE",
        }
    )


def format_new_solana_discovery(observation: Any) -> str:
    """Label a credible indexed/on-chain candidate without implying free mint."""
    if not is_new_solana_discovery(observation):
        raise ValueError("Not a Solana discovery")
    details = observation.details
    clean = lambda value: " ".join(str(value or "unknown").split())[:400]
    schedule = details.get("mint_schedule") if isinstance(details.get("mint_schedule"), dict) else {}
    schedule_status = schedule.get("status", "UNVERIFIED")
    mint_date = details.get("mint_date") or schedule.get("mint_date") or details.get("launch_at")
    return "\n".join((
        "NEW_DISCOVERY · Solana candidate",
        "Project: " + clean(getattr(observation, "project_name", "")),
        "Candidate address: " + clean(observation.contract_address) + " · kind: " + clean(details.get("address_kind")),
        "Source: " + clean(observation.source_url),
        "Mint price: UNKNOWN (not verified; marketplace/project claims are not proof) · listing claim: " + clean(details.get("price_claim")),
        "Schedule: " + clean(schedule_status) + " · mint date: " + clean(mint_date),
        "Announcement date: " + clean(details.get("announcement_date")) + " · source status: " + clean(details.get("announcement_status")),
        "Allowlist claim: " + clean(details.get("allowlist_status_claim", details.get("allowlist_status"))) + " (unverified)",
        "Eligibility: UNKNOWN; selected Phantom wallet has not been verified",
        "Review status: unverified candidate; this is not a free-mint recommendation",
    ))


def verified_alert_dedupe_key(observation: Any) -> str:
    wallet = str(observation.details.get("eligibility_wallet", ""))
    wallet_hash = hashlib.sha256(wallet.encode("utf-8")).hexdigest()[:24]
    return "solana-verified-free:%s:%s" % (observation.contract_address, wallet_hash)


def _mask_wallet(value: Any) -> str:
    wallet = str(value or "")
    return wallet[:6] + "…" + wallet[-4:] if len(wallet) > 12 else "unknown"
