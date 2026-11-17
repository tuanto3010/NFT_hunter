"""Bounded, non-overlapping periodic scans across enabled chain adapters."""

import logging
import os
import threading
import time
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable

from app.database import Database, ScanAlreadyRunning
from app.discovery.chain_adapter import ChainAdapter
from app.process_identity import process_instance_alive, process_start_identity
from app.notifications.mode import NotificationDispatcher, NotificationMode

logger = logging.getLogger(__name__)


class ScanScheduler:
    def __init__(self, adapters: Iterable[ChainAdapter], database: Database,
                 interval_minutes: int = 15, max_workers: int = 3,
                 solana_notifier=None, telegram_notifier=None,
                 notification_mode: NotificationMode | str = NotificationMode.DRY_RUN,
                 live_notifications_enabled: bool = False) -> None:
        if interval_minutes < 1 or max_workers < 1:
            raise ValueError("interval_minutes and max_workers must be positive")
        self.adapters = list(adapters)
        self.database = database
        self.interval_seconds = interval_minutes * 60
        self.max_workers = max_workers
        supplied_notifier = telegram_notifier or solana_notifier
        self.solana_notifier = (
            supplied_notifier if isinstance(supplied_notifier, NotificationDispatcher)
            else NotificationDispatcher(notification_mode, supplied_notifier,
                                         live_authorized=live_notifications_enabled)
        )
        self._scan_lock = threading.Lock()
        self._stop = threading.Event()

    def _scan_adapter(self, adapter: ChainAdapter) -> tuple[str, list, int, bool]:
        name = adapter.spec.name
        try:
            if hasattr(adapter, "last_scanned_block"):
                chain_key = str(adapter.spec.chain_id)
                cursor = self.database.get_scan_cursor(chain_key)
                observations = adapter.scan(from_block=cursor)
            else:
                observations = adapter.scan()
            if str(adapter.spec.chain_id) == "solana-mainnet-beta":
                observations = self._attach_watchlist_evidence(observations)
            inserted = [item for item in observations if self.database.save_observation(item)]
            if str(adapter.spec.chain_id) == "solana-mainnet-beta":
                self._mark_watchlist_results(observations)
            self._persist_source_health(adapter)
            self._notify_new_solana(observations)
            self._notify_qualified_solana(observations)
            last_scanned = getattr(adapter, "last_scanned_block", None)
            if last_scanned is not None:
                self.database.set_scan_cursor(chain_key, last_scanned)
            logger.info("%s scan: %d observations, %d new records", name, len(observations), len(inserted))
            return name, inserted, len(observations), False
        except Exception:
            self._persist_source_health(adapter)
            logger.exception("Scan failed on %s", name)
            return name, [], 0, True

    def _persist_source_health(self, adapter: ChainAdapter) -> None:
        statuses = getattr(adapter, "source_status", None)
        if not isinstance(statuses, dict):
            return
        for source_name, detail in statuses.items():
            if not isinstance(detail, dict):
                continue
            checked_at = detail.get("checked_at") or datetime.now(timezone.utc).isoformat()
            try:
                self.database.record_source_health(
                    str(adapter.spec.chain_id), str(source_name), str(detail.get("status", "unknown")),
                    str(checked_at), int(detail.get("records", 0)), int(detail.get("requests", 0)),
                    str(detail["error_type"]) if detail.get("error_type") else None,
                )
            except Exception:
                logger.warning("Could not persist source health for %s", source_name)

    def _notify_qualified_solana(self, observations: list) -> None:
        if self.solana_notifier is None:
            return
        from app.notifications.solana_alerts import format_verified_solana_alert, is_verified_solana_alert, verified_alert_dedupe_key

        for observation in observations:
            if str(getattr(observation, "chain_id", "")) != "solana-mainnet-beta":
                continue
            if not is_verified_solana_alert(observation):
                continue
            details = observation.details
            key = verified_alert_dedupe_key(observation)
            try:
                self.solana_notifier.send_message(format_verified_solana_alert(observation), key)
            except Exception as exc:
                logger.warning("Verified Solana alert delivery failed (%s)", type(exc).__name__)

    def _notify_new_solana(self, observations: list) -> None:
        if self.solana_notifier is None:
            return
        from app.notifications.solana_alerts import format_new_solana_discovery, is_new_solana_discovery
        for observation in observations:
            if not is_new_solana_discovery(observation):
                continue
            key = "solana-new-discovery:%s" % observation.contract_address
            try:
                self.solana_notifier.send_message(format_new_solana_discovery(observation), key)
            except Exception as exc:
                logger.warning("New Solana discovery alert delivery failed (%s)", type(exc).__name__)

    def _attach_watchlist_evidence(self, observations: list) -> list:
        watchlist = {row["address"]: row for row in self.database.get_solana_watchlist_addresses()}
        if not watchlist:
            return observations
        enriched = []
        for item in observations:
            row = watchlist.get(item.contract_address)
            if not row:
                enriched.append(item)
                continue
            details = dict(item.details)
            official_url = row.get("official_website") or row.get("announcement_url") or row.get("launchpad_url")
            source_verified = bool(row["official_source_verified"])
            details.update({
                "watchlist": {"project_id": row["id"], "address_type": row["address_type"],
                              "source_url": row["source_url"], "provenance": row["provenance"],
                              "review_status": row["review_status"],
                              "blocked_reason": row["blocked_reason"]},
                "official_source_url": official_url,
                "official_source_verified": source_verified,
                "announcement_url": row.get("announcement_url"),
                "announcement_date": row.get("announcement_date"),
                "announcement_date_status": "VERIFIED" if source_verified and row.get("announcement_url") and row.get("announcement_date") else ("UNVERIFIED" if row.get("announcement_date") else "UNKNOWN"),
                "announcement_status": "VERIFIED" if source_verified and row.get("announcement_url") else ("UNVERIFIED" if row.get("announcement_url") else "UNKNOWN"),
                "mint_date": row.get("mint_date"),
                "mint_date_status": "VERIFIED" if source_verified and row.get("announcement_url") and row.get("mint_date") else ("UNVERIFIED" if row.get("mint_date") else "UNKNOWN"),
                "mint_price_claim": row.get("mint_price_claim"),
                "mint_price_claim_status": "UNVERIFIED" if row.get("mint_price_claim") is not None else "UNKNOWN",
                "allowlist_status": "UNKNOWN",
                "allowlist_status_claim": row.get("allowlist_status", "UNKNOWN"),
                "allowlist_status_claim_verified": False,
            })
            if details["announcement_status"] == "VERIFIED":
                details["opportunity_status"] = "ANNOUNCED"
                mint_date = row.get("mint_date")
                if mint_date:
                    try:
                        parsed_mint_date = datetime.fromisoformat(mint_date.replace("Z", "+00:00"))
                        if parsed_mint_date.tzinfo is not None and parsed_mint_date.astimezone(timezone.utc) > datetime.now(timezone.utc):
                            details["mint_status"] = "upcoming"
                            details["launch_at"] = parsed_mint_date.astimezone(timezone.utc).isoformat()
                            details["mint_schedule"] = {"status": "ANNOUNCED", "mint_date": details["launch_at"],
                                                        "source": row.get("announcement_url"), "verified": True}
                        elif parsed_mint_date.tzinfo is not None:
                            details["mint_status"] = "ended"
                            details["mint_schedule"] = {"status": "PAST", "mint_date": parsed_mint_date.astimezone(timezone.utc).isoformat(),
                                                        "source": row.get("announcement_url"), "verified": True}
                    except ValueError:
                        pass
            enriched_item = replace(item, project_name=row["project_name"] or item.project_name,
                                    source_url=row["source_url"], details=details)
            enriched.append(enriched_item)
        return enriched

    def _mark_watchlist_results(self, observations: list) -> None:
        watchlist = {row["address"]: row for row in self.database.get_solana_watchlist_addresses()}
        for item in observations:
            row = watchlist.get(item.contract_address)
            if not row:
                continue
            details = item.details
            machine = details.get("machine") if isinstance(details.get("machine"), dict) else {}
            guard = machine.get("guard_config") if isinstance(machine.get("guard_config"), dict) else {}
            if details.get("price_status") == "PRICE_VERIFIED_FREE":
                if not row["official_source_verified"]:
                    blocked = "On-chain zero price is verified; waiting for manual official-source review"
                else:
                    blocked = "On-chain zero price and source review are verified; run the Phantom wallet check"
            elif row.get("address_type") == "token_mint":
                blocked = (
                    "This address is an already-minted NFT token account, not a Candy Machine. "
                    "An active mint, price, schedule, guard rules, and Phantom eligibility "
                    "cannot be inferred from this NFT mint account."
                )
            else:
                blocked = "Mint price and eligibility remain UNKNOWN: " + str(guard.get("reason", "unsupported account layout"))
            connection = self.database.connect()
            try:
                stored = connection.execute(
                    "SELECT id FROM observations WHERE chain_identifier='solana-mainnet-beta' AND contract_address=?",
                    (item.contract_address,),
                ).fetchone()
            finally:
                connection.close()
            try:
                self.database.mark_solana_watchlist_checked(
                    item.contract_address, int(stored[0]) if stored else None,
                    str(details.get("price_status", "UNKNOWN")), blocked,
                )
            except Exception:
                logger.warning("Could not update Solana watchlist check status")

    def scan_once(self) -> Dict[str, int]:
        """Run enabled chains concurrently; return newly persisted counts."""
        if not self._scan_lock.acquire(blocking=False):
            raise ScanAlreadyRunning("A scan is already running in this scheduler")
        scan_id = None
        owner_pid = os.getpid()
        owner_identity = process_start_identity(owner_pid)
        if owner_identity is None:
            self._scan_lock.release()
            raise RuntimeError("Could not establish this scanner process identity; scan was not started")
        status = "interrupted"
        total = 0
        error = None
        try:
            scan_id = self.database.begin_owned_scan(owner_pid, owner_identity, process_instance_alive)
            counts: Dict[str, int] = {}
            failures = []
            with ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix="nft-scan") as pool:
                futures = [pool.submit(self._scan_adapter, adapter) for adapter in self.adapters]
                for future in as_completed(futures):
                    name, inserted, observed, failed = future.result()
                    counts[name] = len(inserted)
                    total += observed
                    if failed:
                        failures.append(name)
            status = "failed" if failures and len(failures) == len(self.adapters) else "partial" if failures else "complete"
            error = ",".join(failures) or None
            return counts
        except KeyboardInterrupt:
            status = "interrupted"
            error = "Interrupted by keyboard signal"
            raise
        except Exception as exc:
            status = "failed"
            error = "Scan coordinator failed (%s)" % type(exc).__name__
            raise
        finally:
            try:
                if scan_id is not None:
                    self.database.finish_owned_scan(scan_id, owner_pid, owner_identity,
                                                    status, total, error)
            finally:
                self._scan_lock.release()

    def run_forever(self) -> None:
        """Run fixed-interval cycles; shutdown waits for in-flight adapters."""
        now = datetime.now(timezone.utc)
        self.database.update_scheduler_state(
            "starting", now.isoformat(), self.interval_seconds, started_at=now.isoformat()
        )
        deadline = time.monotonic()
        try:
            while not self._stop.is_set():
                self.database.update_scheduler_state(
                    "scanning", datetime.now(timezone.utc).isoformat(), self.interval_seconds
                )
                self.scan_once()
                deadline += self.interval_seconds
                next_scan = datetime.now(timezone.utc) + timedelta(
                    seconds=max(0.0, deadline - time.monotonic())
                )
                while not self._stop.is_set():
                    self.database.update_scheduler_state(
                        "scheduled", datetime.now(timezone.utc).isoformat(),
                        self.interval_seconds, next_scan.isoformat(),
                    )
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or self._stop.wait(min(30.0, remaining)):
                        break
        finally:
            self.database.update_scheduler_state(
                "stopped", datetime.now(timezone.utc).isoformat(), self.interval_seconds
            )

    def stop(self) -> None:
        self._stop.set()
