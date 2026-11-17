"""Bounded campaign-lead discovery from The Colony's documented public RSS.

RSS items are untrusted leads, never official-source or price verification.
The adapter makes one request, reads at most 1 MiB, and does not scrape pages.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
import re
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from app.discovery.project_sources import ProjectObservation
from app.discovery.campaign_sources import (
    HOWRARE_SOURCE_URL, HowRareDropsSource, magic_eden_observation_to_lead,
)
from app.discovery.opensea_drops import OpenSeaDropsSource
from app.discovery.solana_scanner import (
    MAGIC_EDEN_LAUNCHPAD_SOURCE, MagicEdenLaunchpadSource,
)


FEED_URL = "https://thecolony.ai/feed.rss"
_EVM = re.compile(r"0x[0-9a-fA-F]{40}")
_NFT_ADDRESS = re.compile(
    r"(?is)(?:NFT\s*(?:contract|address)?|contract\s*[-:]\s*NFT(?:\s*/\s*miner)?)"
    r"[^\n]{0,100}?(0x[0-9a-fA-F]{40})"
)
_TAG = re.compile(r"<[^>]*>")
_OFFICIAL_URL = re.compile(
    r"(?i)\b(?:official\s+(?:website|site|mint\s+site)|website|mint\s+site|site)\s*[:=-]\s*(https://[^\s<>]+)"
)
_MINT_METHOD = re.compile(r"\bmint\s*\([^)]{0,180}\)", re.IGNORECASE)
_SUPPLY_METHOD = re.compile(r"\b(totalMinted|totalSupply)\s*\(\)", re.IGNORECASE)
_SUPPLY = re.compile(r"(?i)([\d,]+)\s+(?:free[- ]mint\s+)?NFTs?\b")
_DATE = re.compile(r"\b20\d{2}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:Z| UTC)?)?\b")
_FREE_TERMS = re.compile(r"(?i)\bfree[- ]mint|mint(?:ing)?\s+(?:is\s+)?free|gas only\b")
_CAMPAIGN_TERMS = re.compile(r"(?i)\bNFT\b.{0,120}\bmint|\bmint\b.{0,120}\bNFT\b")
_CLOSED = re.compile(r"(?i)\b(?:sold[- ]out|mint(?:ing)? is closed|mint closed|no mints left|all \d[\d,]* (?:NFTs? )?minted)\b")
_PAID = re.compile(r"(?i)\bpaid mint\b|mint price\s*(?:is\s*)?(?:0\.[0-9]*[1-9]\d*|[1-9]\d*(?:\.\d+)?)\s*(?:BNB|ETH|SOL)")


@dataclass(frozen=True)
class FeedLead:
    observation: ProjectObservation | None
    outcome: str
    reason: str


class ColonyCampaignFeed:
    """Parse public RSS only; no HTML scraping, auth, or automatic retries."""

    name = "thecolony-public-rss"
    chain_identifier = "bnb"
    timeout_seconds = 8
    max_bytes = 1_048_576
    max_items = 50

    def __init__(self, opener: Any = urlopen) -> None:
        self.opener = opener

    def fetch(self) -> tuple[list[FeedLead], int]:
        request = Request(FEED_URL, headers={"Accept": "application/rss+xml, application/xml",
                                             "User-Agent": "Binance-NFT-Hunter/0.1"})
        with self.opener(request, timeout=self.timeout_seconds) as response:
            raw = response.read(self.max_bytes + 1)
        if len(raw) > self.max_bytes:
            raise ValueError("RSS response exceeded the configured size limit")
        root = ElementTree.fromstring(raw)
        items = root.findall(".//item")[:self.max_items]
        leads = [self._parse(item) for item in items]
        return [lead for lead in leads if lead is not None], len(items)

    @staticmethod
    def _text(item: ElementTree.Element, names: tuple[str, ...]) -> str:
        for child in item:
            local = child.tag.rsplit("}", 1)[-1]
            if local in names and child.text:
                return unescape(_TAG.sub(" ", child.text)).strip()
        return ""

    def _parse(self, item: ElementTree.Element) -> FeedLead | None:
        title = self._text(item, ("title",))
        link = self._text(item, ("link",))
        description = self._text(item, ("description", "encoded", "content"))
        body = (title + "\n" + description).strip()
        if not link.startswith("https://thecolony.ai/post/") and not link.startswith("https://www.thecolony.ai/post/"):
            return None
        if not _CAMPAIGN_TERMS.search(body) or not _FREE_TERMS.search(body) or not re.search(r"(?i)\bBNB|BNB Chain|BSC\b", body):
            return None

        addresses = list(dict.fromkeys(match.group(1) for match in _NFT_ADDRESS.finditer(body)))
        # A second NFT contract mention makes the lead ambiguous; never pick one.
        if len(addresses) != 1:
            return FeedLead(None, "DEFERRED_MISSING_EVIDENCE", "Missing or ambiguous NFT contract address")
        address = addresses[0]
        if _PAID.search(body):
            return FeedLead(None, "REJECTED_PAID", "Announcement includes a paid-mint claim")
        if _CLOSED.search(body):
            return FeedLead(None, "REJECTED_SOLD_OUT", "Announcement reports the mint closed or sold out")

        official_site = None
        for match in _OFFICIAL_URL.finditer(body):
            candidate = match.group(1).rstrip(".,);]")
            host = candidate.lower().split("/", 3)[2]
            if "thecolony.ai" in host or any(host.endswith(domain) for domain in
                    ("bscscan.com", "opensea.io", "magiceden.io", "flap.sh")):
                continue
            official_site = candidate
            break
        pub_date = self._text(item, ("pubDate",))
        try:
            published = parsedate_to_datetime(pub_date).astimezone(timezone.utc).isoformat()
        except (TypeError, ValueError, OverflowError):
            published = None
        date_claim = _DATE.search(body)
        mint_method_claim = _MINT_METHOD.search(body)
        supply_values = [int(match.group(1).replace(",", "")) for match in _SUPPLY.finditer(body)]
        now = datetime.now(timezone.utc).isoformat()
        campaign = {
            "lead_source": self.name,
            "official_website": official_site,
            "announcement_url": link,
            "mint_url": official_site,
            "announcement_published_at": published,
            "mint_window_claim": date_claim.group(0) if date_claim else None,
            "mint_method_claim": mint_method_claim.group(0) if mint_method_claim else None,
            "supply_method_claim": _SUPPLY_METHOD.search(body).group(1) + "()" if _SUPPLY_METHOD.search(body) else None,
            "max_supply_claim": max((value for value in supply_values if value > 0), default=None),
            "source_verification": "UNVERIFIED_RSS_LEAD",
            "mint_price_status": "CLAIMED_FREE_UNVERIFIED",
            "mint_availability": "UNKNOWN",
            "eligibility_status": "UNKNOWN",
            "verification_status": "DEFERRED_MISSING_EVIDENCE",
            "source_excerpt": body[:1600],
        }
        obs = ProjectObservation(
            chain_id=56, contract_address=address.lower(), source_url=link,
            observed_at=now, project_name=title[:160], verified=False,
            details={"campaign": campaign}, nft_standard="unknown", mint_price=None,
            native_gas_token="BNB", eligibility="unknown",
            verification_status="unknown", risk_assessment="high",
        )
        return FeedLead(obs, "DEFERRED_MISSING_EVIDENCE",
                        "Public RSS is a lead; project-controlled source and verifier profile are not verified")


def run_campaign_discovery_cycle(database: Any, source: ColonyCampaignFeed | None = None,
                                 howrare_source: HowRareDropsSource | None = None,
                                 magic_eden_source: MagicEdenLaunchpadSource | None = None,
                                 include_additional_sources: bool | None = None,
                                 opensea_source: OpenSeaDropsSource | None = None) -> dict[str, Any]:
    """Run one bounded cycle: Colony, Solana launch feeds, and OpenSea Drops.

    Passing ``source`` keeps the original test/CLI seam and runs only that source.
    Calendar and launchpad listings remain unverified leads.
    """
    checked_at = datetime.now(timezone.utc).isoformat()
    colony = source or ColonyCampaignFeed()
    result: dict[str, Any] = {
        "source": colony.name, "checked_at": checked_at, "status": "ok", "requests": 0,
        "items_read": 0, "leads_found": 0, "new_leads": 0, "duplicates": 0,
        "rejected_paid": 0, "rejected_sold_out": 0, "expired": 0, "deferred": 0,
        "verification_attempts": 0, "verified_previewable": 0, "leads": [],
        "source_metrics": [], "source_errors": [],
    }
    successful_sources = 0

    try:
        colony_leads, items_read = colony.fetch()
        successful_sources += 1
        result["requests"] += 1
        result["items_read"] = items_read
        metric = {"source": colony.name, "chain": colony.chain_identifier, "status": "ok",
                  "requests": 1, "items_received": items_read, "leads_found": 0,
                  "new_leads_saved": 0, "duplicates": 0, "deferred": 0,
                  "rejected_paid": 0, "rejected_sold_out": 0, "error_type": None}
        for lead in colony_leads:
            if lead.outcome == "REJECTED_PAID":
                result["rejected_paid"] += 1
                metric["rejected_paid"] += 1
                continue
            if lead.outcome == "REJECTED_SOLD_OUT":
                result["rejected_sold_out"] += 1
                metric["rejected_sold_out"] += 1
                continue
            if lead.observation is None:
                result["deferred"] += 1
                metric["deferred"] += 1
                continue
            result["leads_found"] += 1
            metric["leads_found"] += 1
            inserted = database.save_observation(lead.observation)
            connection = database.connect()
            try:
                row = connection.execute(
                    "SELECT id FROM observations WHERE chain_id=56 AND lower(contract_address)=lower(?)",
                    (lead.observation.contract_address,),
                ).fetchone()
            finally:
                connection.close()
            if inserted:
                result["new_leads"] += 1
                metric["new_leads_saved"] += 1
                profile_supported = verification_profile(lead.observation) is not None
                if not profile_supported:
                    result["deferred"] += 1
                    metric["deferred"] += 1
                reason = lead.reason
            else:
                result["duplicates"] += 1
                metric["duplicates"] += 1
                reason = "Already present by chain ID and contract address"
            result["leads"].append({
                "id": int(row["id"]) if row else None,
                "project_name": lead.observation.project_name,
                "chain_id": lead.observation.chain_id,
                "contract_address": lead.observation.contract_address,
                "announcement_url": lead.observation.source_url,
                "new": bool(inserted), "status": "DEFERRED_MISSING_EVIDENCE", "reason": reason,
                "verification_profile_supported": verification_profile(lead.observation) is not None,
            })
        database.record_source_health(colony.chain_identifier, colony.name, "ok", checked_at,
                                      records=metric["leads_found"], requests=1)
        result["source_metrics"].append(metric)
    except Exception as exc:
        error_type = type(exc).__name__
        result["requests"] += 1
        database.record_source_health(colony.chain_identifier, colony.name, "error", checked_at,
                                      records=0, requests=1, error_type=error_type)
        result["source_metrics"].append({"source": colony.name, "chain": colony.chain_identifier,
                                         "status": "error", "requests": 1, "items_received": 0,
                                         "leads_found": 0, "new_leads_saved": 0, "duplicates": 0,
                                         "deferred": 0, "rejected_paid": 0, "rejected_sold_out": 0,
                                         "error_type": error_type,
                                         **({"http_status": int(exc.code)} if isinstance(exc, HTTPError) else {})})
        result["source_errors"].append({"source": colony.name, "error_type": error_type,
                                         **({"http_status": int(exc.code)} if isinstance(exc, HTTPError) else {})})

    if include_additional_sources is None:
        include_additional_sources = source is None
    if include_additional_sources:
        howrare = howrare_source or HowRareDropsSource()
        try:
            calendar_leads = howrare.fetch()
            successful_sources += 1
            source_status = howrare.last_status
            metric = {"source": howrare.name, "chain": howrare.chain_identifier, "status": "ok",
                      "requests": howrare.requests_made,
                      "items_received": int(source_status.get("items_received", 0)),
                      "leads_found": len(calendar_leads), "new_leads_saved": 0,
                      "duplicates": 0, "deferred": 0, "rejected_paid": 0,
                      "rejected_sold_out": 0, "expired": int(source_status.get("expired", 0)),
                      "error_type": None}
            for candidate in calendar_leads:
                result["leads_found"] += 1
                inserted = database.save_campaign_source_lead(candidate)
                if inserted:
                    result["new_leads"] += 1
                    metric["new_leads_saved"] += 1
                else:
                    result["duplicates"] += 1
                    metric["duplicates"] += 1
                result["deferred"] += 1
                metric["deferred"] += 1
            result["expired"] += metric["expired"]
            result["requests"] += howrare.requests_made
            database.record_source_health(howrare.chain_identifier, howrare.name, "ok", checked_at,
                                          records=len(calendar_leads), requests=howrare.requests_made)
            result["source_metrics"].append(metric)
        except Exception as exc:
            howrare_status = getattr(howrare, "last_status", {})
            error_type = str(howrare_status.get("error_type") or type(exc).__name__)
            requests = int(getattr(howrare, "requests_made", 0))
            result["requests"] += requests
            database.record_source_health(howrare.chain_identifier, howrare.name, "error", checked_at,
                                          records=0, requests=requests, error_type=error_type)
            error_metric = {"source": howrare.name, "chain": howrare.chain_identifier,
                            "status": "error", "requests": requests, "items_received": 0,
                            "leads_found": 0, "new_leads_saved": 0, "duplicates": 0,
                            "deferred": 0, "rejected_paid": 0, "rejected_sold_out": 0,
                            "expired": 0, "error_type": error_type}
            if howrare_status.get("http_status"):
                error_metric["http_status"] = int(howrare_status["http_status"])
            if howrare_status.get("error_reason"):
                error_metric["error_reason"] = str(howrare_status["error_reason"])
            result["source_metrics"].append(error_metric)
            result["source_errors"].append({"source": howrare.name, "error_type": error_type,
                                             **({"http_status": howrare_status["http_status"]}
                                                if howrare_status.get("http_status") else {}),
                                             **({"error_reason": howrare_status["error_reason"]}
                                                if howrare_status.get("error_reason") else {})})

        eden = magic_eden_source or MagicEdenLaunchpadSource(
            limit=100, timeout=8.0, max_attempts=1, max_response_bytes=2_097_152)
        try:
            observations = eden.fetch()
            successful_sources += 1
            status = eden.last_status
            metrics = {"source": "magiceden-solana-launchpad", "chain": "solana-mainnet-beta",
                       "status": "ok", "requests": int(eden.requests_made),
                       "items_received": int(status.get("fetched", 0)),
                       "solana_items": int(status.get("solana", 0)),
                       "leads_found": len(observations), "new_leads_saved": 0,
                       "duplicates": 0, "deferred": 0, "rejected_paid": 0,
                       "rejected_sold_out": 0, "expired": 0, "error_type": None}
            for observation in observations:
                candidate = magic_eden_observation_to_lead(
                    observation, "magiceden-solana-launchpad", MAGIC_EDEN_LAUNCHPAD_SOURCE)
                result["leads_found"] += 1
                inserted = database.save_campaign_source_lead(candidate)
                if inserted:
                    result["new_leads"] += 1
                    metrics["new_leads_saved"] += 1
                else:
                    result["duplicates"] += 1
                    metrics["duplicates"] += 1
                result["deferred"] += 1
                metrics["deferred"] += 1
            result["requests"] += int(eden.requests_made)
            database.record_source_health("solana-mainnet-beta", metrics["source"], "ok", checked_at,
                                          records=len(observations), requests=int(eden.requests_made))
            result["source_metrics"].append(metrics)
        except Exception as exc:
            error_type = str(getattr(eden, "last_status", {}).get("error_type") or type(exc).__name__)
            requests = int(getattr(eden, "requests_made", 0))
            result["requests"] += requests
            database.record_source_health("solana-mainnet-beta", "magiceden-solana-launchpad", "error",
                                          checked_at, records=0, requests=requests, error_type=error_type)
            result["source_metrics"].append({"source": "magiceden-solana-launchpad",
                                             "chain": "solana-mainnet-beta", "status": "error",
                                             "requests": requests, "items_received": 0,
                                             "leads_found": 0, "new_leads_saved": 0, "duplicates": 0,
                                             "deferred": 0, "rejected_paid": 0, "rejected_sold_out": 0,
                                             "expired": 0,
                                             "error_type": error_type,
                                             **({"http_status": int(eden.last_status["http_status"])}
                                                if getattr(eden, "last_status", {}).get("http_status") else {})})
            result["source_errors"].append({"source": "magiceden-solana-launchpad",
                                             "error_type": error_type,
                                             **({"http_status": int(eden.last_status["http_status"])}
                                                if getattr(eden, "last_status", {}).get("http_status") else {})})

        opensea = opensea_source or OpenSeaDropsSource()
        opensea_phase = "fetch"
        try:
            candidates = opensea.fetch()
            opensea_phase = "persist_leads"
            status = opensea.last_status
            status_name = str(status.get("status") or "error")
            metrics = {
                "source": opensea.name, "chain": opensea.chain_identifier,
                "status": status_name, "requests": int(status.get("requests", 0)),
                "pages": int(status.get("pages", 0)),
                "items_received": int(status.get("items_received", 0)),
                "leads_found": len(candidates), "new_leads_saved": 0,
                "duplicates": int(status.get("duplicates", 0)), "deferred": len(candidates),
                "rejected_paid": 0, "rejected_sold_out": 0, "expired": 0,
                "error_type": status.get("error_type"),
                "by_type": status.get("by_type", {}),
                "source_errors": status.get("source_errors", []),
            }
            for candidate in candidates:
                result["leads_found"] += 1
                if database.save_campaign_source_lead(candidate):
                    result["new_leads"] += 1
                    metrics["new_leads_saved"] += 1
                else:
                    result["duplicates"] += 1
                    metrics["duplicates"] += 1
                result["deferred"] += 1
            source_errors = status.get("source_errors", [])
            for error in source_errors:
                if isinstance(error, dict):
                    result["source_errors"].append({"source": opensea.name, **error})
            result["requests"] += metrics["requests"]
            if status_name in {"ok", "partial"}:
                successful_sources += 1
                opensea_phase = "source_health"
                database.record_source_health(opensea.chain_identifier, opensea.name,
                                              status_name, checked_at, records=len(candidates),
                                              requests=metrics["requests"])
            elif status_name == "not_configured":
                database.record_source_health(opensea.chain_identifier, opensea.name,
                                              "not_configured", checked_at, records=0, requests=0)
            else:
                errors = source_errors
                error_type = (errors[0].get("error_type") if errors and isinstance(errors[0], dict)
                              else status.get("error_type") or "SourceError")
                database.record_source_health(opensea.chain_identifier, opensea.name,
                                              "error", checked_at, records=len(candidates),
                                              requests=metrics["requests"], error_type=str(error_type))
            result["source_metrics"].append(metrics)
        except Exception as exc:
            error_type = type(exc).__name__
            source_phase = getattr(opensea, "failure_phase", None)
            failure_phase = source_phase if source_phase and source_phase != "complete" else opensea_phase
            requests = int(getattr(opensea, "requests_made", 0))
            result["requests"] += requests
            database.record_source_health(opensea.chain_identifier, opensea.name,
                                          "error", checked_at, records=0, requests=requests,
                                          error_type=error_type)
            result["source_metrics"].append({"source": opensea.name, "chain": opensea.chain_identifier,
                                             "status": "error", "requests": requests,
                                             "items_received": 0, "leads_found": 0,
                                             "new_leads_saved": 0, "duplicates": 0,
                                             "deferred": 0, "error_type": error_type,
                                             "failure_phase": failure_phase})
            result["source_errors"].append({"source": opensea.name, "error_type": error_type,
                                             "failure_phase": failure_phase})

    result["status"] = "error" if successful_sources == 0 else (
        "partial" if result["source_errors"] else "ok")
    if successful_sources == 0 and result["source_errors"]:
        result["error_type"] = result["source_errors"][0]["error_type"]
    return result


def verification_profile(observation: ProjectObservation) -> dict[str, Any] | None:
    """Return a narrow verifier profile only when the announcement states it.

    Currently supports only no-argument ``mint()`` plus an explicitly named
    supply counter and stated cap. Parameterized/PoW calls remain unsupported.
    """
    campaign = observation.details.get("campaign", {})
    mint_claim = str(campaign.get("mint_method_claim") or "").replace(" ", "")
    supply_method = campaign.get("supply_method_claim")
    cap = campaign.get("max_supply_claim")
    if (mint_claim != "mint()" or supply_method not in {"totalMinted()", "totalSupply()"}
            or not isinstance(cap, int) or cap <= 0 or not campaign.get("official_website")):
        return None
    return {
        "mint_method": "mint()", "mint_arguments": [],
        "price_check": {"interpretation": "verified_nonpayable_mint"},
        "eligibility_check": {"interpretation": "exact_mint_call"},
        "availability_check": {"method": supply_method, "arguments": [],
                               "interpretation": "supply_below", "max_supply": cap},
    }
