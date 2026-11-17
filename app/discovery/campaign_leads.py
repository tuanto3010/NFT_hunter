"""Manual campaign-first EVM leads; announcements are leads, not verification."""

from datetime import datetime, timezone
from urllib.parse import urlsplit

from app.discovery.nft_detector import is_contract_address
from app.discovery.project_sources import ProjectObservation


_EVM_CHAIN_IDS = {1, 56, 8453}


def _https_url(value: str, field: str) -> str:
    parsed = urlsplit(value.strip())
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError(field + " must be an HTTPS URL without embedded credentials")
    return value.strip()


def manual_campaign_lead(*, project_name: str, chain_id: int, contract_address: str,
                         official_url: str, announcement_url: str,
                         mint_url: str = "", announced_mint_date: str = "") -> ProjectObservation:
    """Normalize a manually supplied campaign lead with all verification UNKNOWN."""
    name = project_name.strip()
    if not name or len(name) > 160:
        raise ValueError("project_name must contain 1-160 characters")
    if chain_id not in _EVM_CHAIN_IDS:
        raise ValueError("unsupported EVM chain ID")
    address = contract_address.strip()
    if not is_contract_address(address):
        raise ValueError("contract_address must be a valid EVM address")
    official = _https_url(official_url, "official_url")
    announcement = _https_url(announcement_url, "announcement_url")
    mint = _https_url(mint_url, "mint_url") if mint_url.strip() else None
    schedule = None
    if announced_mint_date.strip():
        try:
            parsed = datetime.fromisoformat(announced_mint_date.strip().replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("announced_mint_date must be ISO 8601") from None
        if parsed.tzinfo is None:
            raise ValueError("announced_mint_date must include a timezone")
        schedule = parsed.astimezone(timezone.utc).isoformat()
    now = datetime.now(timezone.utc).isoformat()
    return ProjectObservation(
        chain_id=chain_id, contract_address=address.lower(), source_url=announcement,
        observed_at=now, project_name=name, verified=False,
        details={"campaign": {
            "lead_source": "manual", "official_website": official,
            "announcement_url": announcement, "mint_url": mint,
            "announced_mint_date_utc": schedule,
            "source_verification": "UNKNOWN", "contract_verification": "UNKNOWN",
            "mint_price_status": "UNKNOWN", "mint_availability": "UNKNOWN",
            "eligibility_status": "UNKNOWN",
        }},
        nft_standard="unknown", mint_price=None,
        native_gas_token={1: "ETH", 56: "BNB", 8453: "ETH"}[chain_id],
        eligibility="unknown", verification_status="unknown", risk_assessment="high",
    )
