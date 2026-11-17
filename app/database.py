"""Small SQLite persistence layer for scan history and source observations."""

import json
import hashlib
import re
import sqlite3
import threading
import unicodedata
from uuid import uuid4
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS scan_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    observations INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    owner_pid INTEGER,
    owner_start_identity TEXT
);
CREATE TABLE IF NOT EXISTS scan_lock (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    scan_id INTEGER NOT NULL REFERENCES scan_history(id),
    owner_pid INTEGER NOT NULL,
    owner_start_identity TEXT NOT NULL,
    acquired_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS scan_recovery_audit (
    scan_id INTEGER PRIMARY KEY REFERENCES scan_history(id),
    recovered_at TEXT NOT NULL,
    reason TEXT NOT NULL,
    previous_status TEXT NOT NULL,
    operator_confirmation TEXT NOT NULL,
    backup_path TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS scan_cursors (
    chain_identifier TEXT PRIMARY KEY,
    last_scanned_block INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS scheduler_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    status TEXT NOT NULL,
    started_at TEXT,
    heartbeat_at TEXT NOT NULL,
    next_scan_at TEXT,
    interval_seconds INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chain_id INTEGER NOT NULL,
    contract_address TEXT NOT NULL,
    chain_identifier TEXT NOT NULL DEFAULT '',
    project_name TEXT,
    source_url TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    first_seen_at TEXT,
    nft_standard TEXT NOT NULL DEFAULT 'unknown',
    mint_price TEXT,
    native_gas_token TEXT NOT NULL DEFAULT '',
    eligibility TEXT NOT NULL DEFAULT 'unknown',
    verification_status TEXT NOT NULL DEFAULT 'unknown',
    risk_assessment TEXT NOT NULL DEFAULT 'high',
    verified INTEGER NOT NULL DEFAULT 0,
    details_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(chain_id, contract_address)
);
CREATE TABLE IF NOT EXISTS notification_deliveries (
    dedupe_key TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    message_id INTEGER
);
CREATE TABLE IF NOT EXISTS discovery_source_health (
    source_name TEXT NOT NULL,
    chain_identifier TEXT NOT NULL,
    status TEXT NOT NULL,
    checked_at TEXT NOT NULL,
    last_success_at TEXT,
    records INTEGER NOT NULL DEFAULT 0,
    requests INTEGER NOT NULL DEFAULT 0,
    error_type TEXT,
    PRIMARY KEY (chain_identifier, source_name)
);
CREATE TABLE IF NOT EXISTS campaign_cycle_jobs (
    job_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    duration_seconds REAL,
    owner_pid INTEGER NOT NULL,
    owner_start_identity TEXT NOT NULL,
    result_json TEXT NOT NULL DEFAULT '{}',
    error_detail TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS one_running_campaign_cycle
    ON campaign_cycle_jobs(status) WHERE status = 'running';
CREATE TABLE IF NOT EXISTS campaign_source_leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chain_identifier TEXT NOT NULL,
    identity_key TEXT NOT NULL,
    project_name TEXT NOT NULL,
    source_name TEXT NOT NULL,
    source_url TEXT NOT NULL,
    official_website_claim TEXT,
    announcement_url_claim TEXT,
    launchpad_url TEXT,
    expected_mint_at TEXT,
    mint_date_claim TEXT,
    mint_time_claim TEXT,
    contract_or_mint_address TEXT,
    address_kind TEXT NOT NULL DEFAULT 'UNKNOWN',
    mint_price_claim TEXT,
    mint_price_status TEXT NOT NULL DEFAULT 'UNKNOWN',
    eligibility_status TEXT NOT NULL DEFAULT 'UNKNOWN',
    verification_status TEXT NOT NULL DEFAULT 'DEFERRED_MISSING_EVIDENCE',
    risk_assessment TEXT NOT NULL DEFAULT 'HIGH',
    source_confidence TEXT NOT NULL DEFAULT 'LOW',
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(chain_identifier, identity_key)
);
CREATE UNIQUE INDEX IF NOT EXISTS campaign_source_lead_address_unique
    ON campaign_source_leads(chain_identifier, lower(contract_or_mint_address))
    WHERE contract_or_mint_address IS NOT NULL;
CREATE TABLE IF NOT EXISTS solana_watchlist_projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_name TEXT NOT NULL,
    official_website TEXT,
    announcement_url TEXT,
    launchpad_url TEXT,
    announcement_date TEXT,
    mint_date TEXT,
    mint_price_claim TEXT,
    allowlist_status TEXT NOT NULL DEFAULT 'UNKNOWN',
    review_status TEXT NOT NULL DEFAULT 'pending',
    official_source_verified INTEGER NOT NULL DEFAULT 0,
    blocked_reason TEXT NOT NULL DEFAULT 'Awaiting on-chain verification and manual source review',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(project_name, official_website)
);
CREATE TABLE IF NOT EXISTS solana_watchlist_addresses (
    address TEXT PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES solana_watchlist_projects(id) ON DELETE CASCADE,
    address_type TEXT NOT NULL,
    source_url TEXT NOT NULL,
    provenance TEXT NOT NULL DEFAULT 'manual_registration',
    verification_status TEXT NOT NULL DEFAULT 'UNKNOWN',
    last_checked_at TEXT,
    observation_id INTEGER,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS solana_watchlist_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    address TEXT NOT NULL REFERENCES solana_watchlist_addresses(address) ON DELETE CASCADE,
    source_url TEXT NOT NULL,
    provenance TEXT NOT NULL DEFAULT 'manual_registration',
    created_at TEXT NOT NULL,
    UNIQUE(address, source_url)
);
CREATE TABLE IF NOT EXISTS core_asset_transactions (
    preview_id TEXT PRIMARY KEY,
    asset_address TEXT NOT NULL UNIQUE,
    wallet_address TEXT NOT NULL,
    network TEXT NOT NULL,
    metadata_uri TEXT NOT NULL,
    transaction_digest TEXT NOT NULL,
    signature TEXT UNIQUE,
    status TEXT NOT NULL,
    estimated_total_lamports TEXT NOT NULL,
    created_at TEXT NOT NULL,
    submitted_at TEXT,
    confirmed_at TEXT,
    failure_category TEXT
);
CREATE TABLE IF NOT EXISTS core_asset_reconciliation_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    preview_id TEXT NOT NULL REFERENCES core_asset_transactions(preview_id),
    checked_at TEXT NOT NULL,
    signature_status TEXT NOT NULL,
    asset_account_status TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'configured_solana_rpc',
    evidence_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS wallet_verification_evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_id TEXT NOT NULL DEFAULT 'unknown',
    chain_identifier TEXT NOT NULL,
    chain_id TEXT NOT NULL,
    contract_address TEXT NOT NULL,
    contract_address_key TEXT NOT NULL,
    stage_id TEXT NOT NULL DEFAULT '',
    wallet_address TEXT NOT NULL,
    wallet_address_key TEXT NOT NULL,
    block_number INTEGER,
    block_ref_key TEXT NOT NULL,
    checked_at TEXT NOT NULL,
    verification_status TEXT NOT NULL,
    mint_price_wei TEXT,
    mint_currency_address TEXT,
    mint_available INTEGER,
    wallet_eligible INTEGER,
    source_url TEXT NOT NULL,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    evidence_sha256 TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS wallet_verification_evidence_identity
    ON wallet_verification_evidence(provider_id, chain_identifier, contract_address_key, stage_id,
                                    wallet_address_key, block_ref_key);
CREATE INDEX IF NOT EXISTS wallet_verification_evidence_lookup
    ON wallet_verification_evidence(chain_identifier, contract_address_key,
                                    wallet_address_key, checked_at DESC);
CREATE TABLE IF NOT EXISTS budget_ledger_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_ref TEXT NOT NULL UNIQUE,
    provider_id TEXT NOT NULL,
    chain_identifier TEXT NOT NULL,
    wallet_address TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('reserved','settled','unknown','released')),
    max_cost_usd TEXT,
    actual_spend_usd TEXT,
    native_amount TEXT,
    native_currency TEXT,
    quote_source_url TEXT,
    quote_observed_at TEXT,
    settlement_source_url TEXT,
    reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS budget_ledger_status_updated
    ON budget_ledger_entries(status, updated_at DESC);
"""


class ScanAlreadyRunning(RuntimeError):
    """Another verified process owns the global scan lease."""


class ScanOwnershipUnknown(RuntimeError):
    """A scan owner could not be verified, so recovery failed closed."""


class Database:
    """SQLite store. It deliberately has no fields for wallet secrets."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._schema_lock = threading.Lock()
        self._schema_ready = False

    def start_campaign_job(self, job_id: str, owner_pid: int, owner_start_identity: str,
                           owner_is_alive: Any, started_at: str) -> None:
        """Acquire the SQLite single-runner guard and insert a real campaign job."""
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            active = connection.execute(
                "SELECT job_id, owner_pid, owner_start_identity FROM campaign_cycle_jobs WHERE status='running'"
            ).fetchone()
            if active is not None:
                alive = owner_is_alive(int(active["owner_pid"]), str(active["owner_start_identity"]))
                if alive is True:
                    raise ScanAlreadyRunning("A campaign discovery cycle is already running")
                if alive is None:
                    raise ScanOwnershipUnknown("Could not verify the existing campaign cycle owner")
                connection.execute(
                    "UPDATE campaign_cycle_jobs SET status='interrupted', finished_at=?, "
                    "error_detail='Owning dashboard process is confirmed inactive' WHERE job_id=? AND status='running'",
                    (started_at, active["job_id"]),
                )
            connection.execute(
                "INSERT INTO campaign_cycle_jobs "
                "(job_id,status,started_at,owner_pid,owner_start_identity,result_json) "
                "VALUES (?,'running',?,?,?,'{}')",
                (job_id, started_at, owner_pid, owner_start_identity),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def finish_campaign_job(self, job_id: str, status: str, finished_at: str,
                            duration_seconds: float, result_json: str,
                            error_detail: Optional[str] = None) -> None:
        """Persist the terminal result without overwriting an earlier completion."""
        if status not in {"completed", "failed", "interrupted"}:
            raise ValueError("invalid campaign job terminal status")
        connection = self.connect()
        try:
            connection.execute(
                "UPDATE campaign_cycle_jobs SET status=?, finished_at=?, duration_seconds=?, "
                "result_json=?, error_detail=? WHERE job_id=? AND status='running'",
                (status, finished_at, max(0.0, float(duration_seconds)), result_json, error_detail, job_id),
            )
            connection.commit()
        finally:
            connection.close()

    def get_campaign_job(self, job_id: str) -> Optional[sqlite3.Row]:
        connection = self.connect()
        try:
            return connection.execute("SELECT * FROM campaign_cycle_jobs WHERE job_id=?", (job_id,)).fetchone()
        finally:
            connection.close()

    def latest_campaign_job(self) -> Optional[sqlite3.Row]:
        connection = self.connect()
        try:
            return connection.execute(
                "SELECT * FROM campaign_cycle_jobs ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
        finally:
            connection.close()

    def connect(self) -> sqlite3.Connection:
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.path), timeout=15.0)
        connection.row_factory = sqlite3.Row
        if not self._schema_ready:
            with self._schema_lock:
                if not self._schema_ready:
                    try:
                        self._migrate_wallet_verification_evidence(connection)
                        connection.executescript(SCHEMA)
                        self._migrate_scan_history(connection)
                        self._migrate_observations(connection)
                        self._migrate_solana_watchlist(connection)
                        self._schema_ready = True
                    except Exception:
                        connection.close()
                        raise
        return connection

    def save_core_asset_transaction(self, item: dict[str, Any]) -> None:
        """Persist non-secret transaction lifecycle data; never store signer material."""
        connection = self.connect()
        try:
            connection.execute(
                "INSERT INTO core_asset_transactions "
                "(preview_id,asset_address,wallet_address,network,metadata_uri,transaction_digest,signature,status,"
                "estimated_total_lamports,created_at,submitted_at,confirmed_at,failure_category) "
                "VALUES (:preview_id,:asset_address,:wallet_address,:network,:metadata_uri,:transaction_digest,"
                ":signature,:status,:estimated_total_lamports,:created_at,:submitted_at,:confirmed_at,:failure_category)",
                item,
            )
            connection.commit()
        finally:
            connection.close()

    def update_core_asset_transaction(self, preview_id: str, **fields: Any) -> bool:
        allowed = {"signature", "status", "submitted_at", "confirmed_at", "failure_category"}
        if not fields or set(fields) - allowed:
            raise ValueError("Invalid Core asset transaction update")
        connection = self.connect()
        try:
            clause = ",".join(f"{name}=?" for name in fields)
            cursor = connection.execute(
                f"UPDATE core_asset_transactions SET {clause} WHERE preview_id=?",
                (*fields.values(), preview_id),
            )
            connection.commit()
            return cursor.rowcount == 1
        finally:
            connection.close()

    def get_core_asset_transaction(self, preview_id: str) -> Optional[dict[str, Any]]:
        connection = self.connect()
        try:
            row = connection.execute(
                "SELECT * FROM core_asset_transactions WHERE preview_id=?", (preview_id,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            connection.close()

    def latest_core_asset_transaction(self) -> Optional[dict[str, Any]]:
        connection = self.connect()
        try:
            row = connection.execute(
                "SELECT * FROM core_asset_transactions ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            return dict(row) if row else None
        finally:
            connection.close()

    def save_core_asset_reconciliation_check(self, preview_id: str, *, checked_at: str,
                                             signature_status: str, asset_account_status: str,
                                             evidence: Optional[dict[str, Any]] = None,
                                             source: str = "configured_solana_rpc") -> None:
        """Append read-only reconciliation evidence without rewriting transaction history."""
        allowed_statuses = {"FOUND", "CONFIRMED", "ONCHAIN_FAILURE", "RPC_HISTORY_UNAVAILABLE", "RPC_ERROR", "RPC_UNAVAILABLE",
                            "ACCOUNT_FOUND", "ACCOUNT_NOT_FOUND", "WRONG_PROGRAM_OWNER",
                            "ASSET_READ_FAILED", "RPC_NETWORK_MISMATCH", "OWNER_OR_METADATA_MISMATCH",
                            "NOT_CHECKED"}
        if signature_status not in allowed_statuses or asset_account_status not in allowed_statuses:
            raise ValueError("Invalid reconciliation status")
        connection = self.connect()
        try:
            connection.execute(
                "INSERT INTO core_asset_reconciliation_checks "
                "(preview_id,checked_at,signature_status,asset_account_status,source,evidence_json) "
                "VALUES (?,?,?,?,?,?)",
                (preview_id, checked_at, signature_status, asset_account_status,
                 source[:80], json.dumps(evidence or {}, sort_keys=True)[:4000]),
            )
            connection.commit()
        finally:
            connection.close()

    def begin_scan(self) -> int:
        connection = self.connect()
        try:
            cursor = connection.execute(
                "INSERT INTO scan_history (started_at, status) VALUES (?, ?)",
                (datetime.now(timezone.utc).isoformat(), "running"),
            )
            connection.commit()
            return int(cursor.lastrowid)
        finally:
            connection.close()

    def begin_owned_scan(self, owner_pid: int, owner_start_identity: str,
                         owner_is_alive: Any) -> int:
        """Atomically acquire the global scan lease and create its history row.

        ``owner_is_alive(pid, identity)`` must return True, False only when the
        exact process instance is confirmed absent, or None when inspection is
        inconclusive. Legacy running rows without ownership remain untouched and
        block acquisition because their owner cannot be verified safely.
        """
        if owner_pid <= 0 or not owner_start_identity:
            raise ValueError("A verifiable process identity is required")
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            lock = connection.execute("SELECT * FROM scan_lock WHERE id=1").fetchone()
            if lock is not None:
                run = connection.execute(
                    "SELECT status FROM scan_history WHERE id=?", (lock["scan_id"],)
                ).fetchone()
                if run is not None and run["status"] == "running":
                    alive = owner_is_alive(int(lock["owner_pid"]), str(lock["owner_start_identity"]))
                    if alive is True:
                        raise ScanAlreadyRunning("A scanner process already owns the scan lease")
                    if alive is None:
                        raise ScanOwnershipUnknown("Could not verify the existing scanner process; scan is blocked")
                    finished = datetime.now(timezone.utc).isoformat()
                    connection.execute(
                        "UPDATE scan_history SET finished_at=?, status='abandoned', "
                        "error='Owning process instance confirmed inactive' "
                        "WHERE id=? AND status='running' AND finished_at IS NULL",
                        (finished, lock["scan_id"]),
                    )
                connection.execute("DELETE FROM scan_lock WHERE id=1")

            active_rows = connection.execute(
                "SELECT id, owner_pid, owner_start_identity FROM scan_history "
                "WHERE status='running' AND finished_at IS NULL"
            ).fetchall()
            for active in active_rows:
                if active["owner_pid"] is None or not active["owner_start_identity"]:
                    raise ScanOwnershipUnknown(
                        "Legacy running scan %s has no process identity; it was preserved and blocks scanning"
                        % active["id"]
                    )
                alive = owner_is_alive(int(active["owner_pid"]), str(active["owner_start_identity"]))
                if alive is True:
                    raise ScanAlreadyRunning("A scanner process already owns an active scan")
                if alive is None:
                    raise ScanOwnershipUnknown(
                        "Could not verify owner of running scan %s; it was preserved and scanning is blocked"
                        % active["id"]
                    )
                connection.execute(
                    "UPDATE scan_history SET finished_at=?, status='abandoned', "
                    "error='Owning process instance confirmed inactive' "
                    "WHERE id=? AND status='running' AND finished_at IS NULL",
                    (datetime.now(timezone.utc).isoformat(), active["id"]),
                )

            started = datetime.now(timezone.utc).isoformat()
            cursor = connection.execute(
                "INSERT INTO scan_history (started_at, status, owner_pid, owner_start_identity) "
                "VALUES (?, 'running', ?, ?)",
                (started, owner_pid, owner_start_identity),
            )
            scan_id = int(cursor.lastrowid)
            connection.execute(
                "INSERT INTO scan_lock (id, scan_id, owner_pid, owner_start_identity, acquired_at) "
                "VALUES (1, ?, ?, ?, ?)",
                (scan_id, owner_pid, owner_start_identity, started),
            )
            connection.commit()
            return scan_id
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def create_file_backup(path: Path) -> Path:
        """Create and integrity-check a SQLite backup without modifying source."""
        source_path = Path(path).resolve()
        if not source_path.is_file():
            raise FileNotFoundError("Database file does not exist; recovery backup cannot be created")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_path = source_path.with_name(
            source_path.name + ".pre-recovery-" + stamp + "-" + uuid4().hex[:8] + ".bak"
        )
        source = sqlite3.connect(str(source_path), timeout=15.0)
        destination = sqlite3.connect(str(backup_path), timeout=15.0)
        try:
            source.execute("PRAGMA query_only=ON")
            source.backup(destination)
            check = destination.execute("PRAGMA integrity_check").fetchone()
            destination.commit()
            if not check or check[0] != "ok":
                raise sqlite3.DatabaseError("Database backup integrity check failed")
            return backup_path
        except Exception:
            destination.close()
            source.close()
            backup_path.unlink(missing_ok=True)
            raise
        finally:
            try:
                destination.close()
            finally:
                source.close()

    def recover_legacy_scans(self, scan_ids: list[int], reason: str,
                             operator_confirmed_all_stopped: bool) -> dict[str, Any]:
        """Back up first, then mark only confirmed legacy running rows abandoned.

        This deliberately refuses rows with ownership metadata: those use the
        automatic exact-process recovery path in ``begin_owned_scan``.
        Call on a fresh Database object so the backup predates schema migration.
        """
        if not operator_confirmed_all_stopped:
            raise ValueError("Explicit confirmation that all scanner/scheduler processes are stopped is required")
        if self._schema_ready:
            raise RuntimeError("Legacy recovery must run before database schema initialization to back up first")
        if not reason or not reason.strip():
            raise ValueError("A recovery reason is required")
        normalized_ids = list(dict.fromkeys(int(item) for item in scan_ids))
        if not normalized_ids or any(item <= 0 for item in normalized_ids):
            raise ValueError("At least one positive scan history ID is required")

        backup_path = self.create_file_backup(self.path)
        connection = self.connect()
        recovery_time = datetime.now(timezone.utc).isoformat()
        confirmation = "Operator confirmed all scanner/scheduler processes stopped"
        try:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT 1 FROM scan_lock WHERE id=1").fetchone():
                raise ScanAlreadyRunning("A scanner lease exists; legacy recovery is blocked")
            placeholders = ",".join("?" for _ in normalized_ids)
            rows = connection.execute(
                "SELECT id, status, finished_at, owner_pid, owner_start_identity FROM scan_history "
                "WHERE id IN (%s) ORDER BY id" % placeholders,
                normalized_ids,
            ).fetchall()
            if len(rows) != len(normalized_ids):
                raise ValueError("One or more requested scan IDs do not exist")
            for row in rows:
                if (row["status"] != "running" or row["finished_at"] is not None
                        or row["owner_pid"] is not None or row["owner_start_identity"] is not None):
                    raise ValueError("Only unfinished legacy running rows without owner identity can be recovered")
            for row in rows:
                updated = connection.execute(
                    "UPDATE scan_history SET status='abandoned', finished_at=?, "
                    "error='Operator-confirmed legacy scan recovery' "
                    "WHERE id=? AND status='running' AND finished_at IS NULL "
                    "AND owner_pid IS NULL AND owner_start_identity IS NULL",
                    (recovery_time, row["id"]),
                )
                if updated.rowcount != 1:
                    raise RuntimeError("Scan row changed during recovery; transaction was rolled back")
                connection.execute(
                    "INSERT INTO scan_recovery_audit "
                    "(scan_id, recovered_at, reason, previous_status, operator_confirmation, backup_path) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (row["id"], recovery_time, reason.strip(), row["status"], confirmation, str(backup_path)),
                )
            connection.commit()
            return {"recovered_ids": normalized_ids, "recovered_at": recovery_time,
                    "backup_path": str(backup_path)}
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def finish_owned_scan(self, scan_id: int, owner_pid: int, owner_start_identity: str,
                          status: str, observations: int, error: Optional[str] = None) -> bool:
        """Finish only this process's still-running scan, then release its lease."""
        if status not in {"complete", "partial", "failed", "interrupted"}:
            raise ValueError("Invalid final scan status")
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "UPDATE scan_history SET finished_at=?, status=?, observations=?, error=? "
                "WHERE id=? AND status='running' AND owner_pid=? AND owner_start_identity=?",
                (datetime.now(timezone.utc).isoformat(), status, max(0, int(observations)),
                 error, scan_id, owner_pid, owner_start_identity),
            )
            if cursor.rowcount == 1:
                connection.execute(
                    "DELETE FROM scan_lock WHERE id=1 AND scan_id=? AND owner_pid=? "
                    "AND owner_start_identity=?",
                    (scan_id, owner_pid, owner_start_identity),
                )
            connection.commit()
            return cursor.rowcount == 1
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


    def finish_scan(self, scan_id: int, status: str, observations: int,
                    error: Optional[str] = None) -> None:
        connection = self.connect()
        try:
            connection.execute(
                "UPDATE scan_history SET finished_at = ?, status = ?, observations = ?, error = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), status, observations, error, scan_id),
            )
            connection.commit()
        finally:
            connection.close()

    def get_scan_cursor(self, chain_identifier: str) -> Optional[int]:
        connection = self.connect()
        try:
            row = connection.execute(
                "SELECT last_scanned_block FROM scan_cursors WHERE chain_identifier = ?",
                (chain_identifier,),
            ).fetchone()
            return int(row[0]) if row else None
        finally:
            connection.close()

    def set_scan_cursor(self, chain_identifier: str, block_number: int) -> None:
        connection = self.connect()
        try:
            connection.execute(
                """INSERT INTO scan_cursors (chain_identifier, last_scanned_block, updated_at)
                VALUES (?, ?, ?) ON CONFLICT(chain_identifier) DO UPDATE SET
                last_scanned_block = excluded.last_scanned_block, updated_at = excluded.updated_at""",
                (chain_identifier, block_number, datetime.now(timezone.utc).isoformat()),
            )
            connection.commit()
        finally:
            connection.close()

    def update_scheduler_state(self, status: str, heartbeat_at: str,
                               interval_seconds: int, next_scan_at: Optional[str] = None,
                               started_at: Optional[str] = None) -> None:
        connection = self.connect()
        try:
            connection.execute(
                """INSERT INTO scheduler_state
                (id, status, started_at, heartbeat_at, next_scan_at, interval_seconds)
                VALUES (1, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET status = excluded.status,
                    started_at = COALESCE(excluded.started_at, scheduler_state.started_at),
                    heartbeat_at = excluded.heartbeat_at, next_scan_at = excluded.next_scan_at,
                    interval_seconds = excluded.interval_seconds""",
                (status, started_at, heartbeat_at, next_scan_at, interval_seconds),
            )
            connection.commit()
        finally:
            connection.close()

    def get_scheduler_state(self) -> Optional[sqlite3.Row]:
        connection = self.connect()
        try:
            return connection.execute("SELECT * FROM scheduler_state WHERE id = 1").fetchone()
        finally:
            connection.close()

    @staticmethod
    def _migrate_wallet_verification_evidence(connection: sqlite3.Connection) -> None:
        """Upgrade the earlier wallet-evidence schema without rewriting rows.

        Run before SCHEMA because the current provider-scoped index references
        ``provider_id`` and would fail to create against an existing legacy table.
        """
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='wallet_verification_evidence'"
        ).fetchone()
        if not table:
            return
        existing = {row[1] for row in connection.execute("PRAGMA table_info(wallet_verification_evidence)")}
        if "provider_id" not in existing:
            connection.execute(
                "ALTER TABLE wallet_verification_evidence "
                "ADD COLUMN provider_id TEXT NOT NULL DEFAULT 'unknown'"
            )
        index = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name='wallet_verification_evidence_identity'"
        ).fetchone()
        if index:
            indexed_columns = {row[2] for row in connection.execute(
                "PRAGMA index_info(wallet_verification_evidence_identity)"
            )}
            if "provider_id" not in indexed_columns:
                connection.execute("DROP INDEX wallet_verification_evidence_identity")
        connection.commit()

    @staticmethod
    def _migrate_scan_history(connection: sqlite3.Connection) -> None:
        """Add nullable ownership metadata; legacy rows are not auto-recovered."""
        existing = {row["name"] for row in connection.execute("PRAGMA table_info(scan_history)")}
        for name, declaration in {
            "owner_pid": "INTEGER",
            "owner_start_identity": "TEXT",
        }.items():
            if name not in existing:
                connection.execute("ALTER TABLE scan_history ADD COLUMN %s %s" % (name, declaration))
        connection.commit()

    @staticmethod
    def _migrate_observations(connection: sqlite3.Connection) -> None:
        """Add unified discovery fields without dropping legacy observations."""
        existing = {row["name"] for row in connection.execute("PRAGMA table_info(observations)")}
        additions = {
            "chain_identifier": "TEXT NOT NULL DEFAULT ''",
            "first_seen_at": "TEXT",
            "nft_standard": "TEXT NOT NULL DEFAULT 'unknown'",
            "mint_price": "TEXT",
            "native_gas_token": "TEXT NOT NULL DEFAULT ''",
            "eligibility": "TEXT NOT NULL DEFAULT 'unknown'",
            "verification_status": "TEXT NOT NULL DEFAULT 'unknown'",
            "risk_assessment": "TEXT NOT NULL DEFAULT 'high'",
        }
        for name, declaration in additions.items():
            if name not in existing:
                connection.execute("ALTER TABLE observations ADD COLUMN %s %s" % (name, declaration))
        connection.execute("UPDATE observations SET chain_identifier = CAST(chain_id AS TEXT) WHERE chain_identifier = ''")
        connection.commit()

    @staticmethod
    def _migrate_solana_watchlist(connection: sqlite3.Connection) -> None:
        """Add independently sourced announcement fields without dropping rows."""
        existing = {row["name"] for row in connection.execute("PRAGMA table_info(solana_watchlist_projects)")}
        for name, declaration in {
            "announcement_date": "TEXT",
            "mint_date": "TEXT",
            "mint_price_claim": "TEXT",
            "allowlist_status": "TEXT NOT NULL DEFAULT 'UNKNOWN'",
        }.items():
            if name not in existing:
                connection.execute("ALTER TABLE solana_watchlist_projects ADD COLUMN %s %s" % (name, declaration))
        connection.commit()

    def save_observation(self, observation: Any) -> bool:
        """Insert a unique observation; return False when it is a duplicate."""
        connection = self.connect()
        try:
            cursor = connection.execute(
                """INSERT OR IGNORE INTO observations
                (chain_id, contract_address, chain_identifier, project_name, source_url, observed_at, first_seen_at,
                 nft_standard, mint_price, native_gas_token, eligibility, verification_status,
                 risk_assessment, verified, details_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    observation.chain_id,
                    observation.contract_address.lower() if isinstance(observation.chain_id, int) else observation.contract_address,
                    str(observation.chain_id),
                    observation.project_name,
                    observation.source_url,
                    observation.observed_at,
                    datetime.now(timezone.utc).isoformat(),
                    observation.nft_standard,
                    observation.mint_price,
                    observation.native_gas_token,
                    observation.eligibility,
                    observation.verification_status,
                    observation.risk_assessment,
                    int(observation.verified),
                    json.dumps(observation.details, sort_keys=True),
                ),
            )
            if cursor.rowcount != 1 and str(observation.chain_id) == "solana-mainnet-beta":
                # Solana source evidence can improve on later read-only scans.
                # Keep the identity stable while refreshing its verified facts.
                connection.execute(
                    """UPDATE observations SET project_name=COALESCE(NULLIF(?, ''), project_name),
                       source_url=COALESCE(NULLIF(?, ''), source_url), observed_at=?, nft_standard=?,
                       mint_price=?, native_gas_token=?, eligibility=?, verification_status=?,
                       risk_assessment=?, verified=?, details_json=?
                       WHERE chain_id='solana-mainnet-beta' AND contract_address=?""",
                    (observation.project_name, observation.source_url, observation.observed_at,
                     observation.nft_standard, observation.mint_price, observation.native_gas_token,
                     observation.eligibility, observation.verification_status, observation.risk_assessment,
                     int(observation.verified), json.dumps(observation.details, sort_keys=True),
                     observation.contract_address),
                )
            connection.commit()
            return cursor.rowcount == 1
        finally:
            connection.close()

    def record_source_health(self, chain_identifier: str, source_name: str, status: str,
                             checked_at: str, records: int = 0, requests: int = 0,
                             error_type: Optional[str] = None) -> None:
        """Persist sanitized source status, counts, and the latest successful check."""
        connection = self.connect()
        try:
            connection.execute(
                """INSERT INTO discovery_source_health
                (chain_identifier, source_name, status, checked_at, last_success_at, records, requests, error_type)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chain_identifier, source_name) DO UPDATE SET
                    status=excluded.status, checked_at=excluded.checked_at,
                    last_success_at=CASE WHEN excluded.status='ok' THEN excluded.checked_at
                                         ELSE discovery_source_health.last_success_at END,
                    records=excluded.records, requests=excluded.requests, error_type=excluded.error_type""",
                (chain_identifier, source_name, status, checked_at,
                 checked_at if status == "ok" else None, max(0, int(records)),
                 max(0, int(requests)), error_type),
            )
            connection.commit()
        finally:
            connection.close()

    def get_source_health(self, chain_identifier: Optional[str] = None) -> list[sqlite3.Row]:
        connection = self.connect()
        try:
            if chain_identifier is None:
                return connection.execute(
                    "SELECT * FROM discovery_source_health ORDER BY chain_identifier, source_name"
                ).fetchall()
            return connection.execute(
                "SELECT * FROM discovery_source_health WHERE chain_identifier = ? ORDER BY source_name",
                (chain_identifier,),
            ).fetchall()
        finally:
            connection.close()

    @staticmethod
    def _campaign_lead_identity(lead: dict[str, Any]) -> str:
        name = unicodedata.normalize("NFKD", str(lead.get("project_name") or "").casefold())
        name = name.encode("ascii", "ignore").decode("ascii")
        name = re.sub(r"[^a-z0-9]+", "-", name).strip("-") or "unnamed"
        mint_day = str(lead.get("mint_date_claim") or lead.get("expected_mint_at") or "")[:10]
        return "name:%s:%s" % (name, mint_day or "undated")

    def save_campaign_source_lead(self, lead: dict[str, Any]) -> bool:
        """Persist an unverified source lead, deduplicating by chain/address or name/date."""
        chain = str(lead.get("chain_identifier") or "").strip()
        name = str(lead.get("project_name") or "").strip()[:160]
        source_name = str(lead.get("source_name") or "").strip()[:100]
        source_url = str(lead.get("source_url") or "").strip()[:1000]
        if not chain or not name or not source_name or not source_url:
            raise ValueError("Campaign source leads require chain, project name, source name and source URL")
        now = datetime.now(timezone.utc).isoformat()
        identity = self._campaign_lead_identity(lead)
        address = str(lead.get("contract_or_mint_address") or "").strip() or None
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = None
            if address:
                existing = connection.execute(
                    "SELECT * FROM campaign_source_leads WHERE chain_identifier=? "
                    "AND lower(contract_or_mint_address)=lower(?)",
                    (chain, address),
                ).fetchone()
            if existing is None:
                existing = connection.execute(
                    "SELECT * FROM campaign_source_leads WHERE chain_identifier=? AND identity_key=?",
                    (chain, identity),
                ).fetchone()
            source_evidence_item = {
                "source_name": source_name, "source_url": source_url,
                "source_confidence": str(lead.get("source_confidence") or "LOW"),
                "price_claim": lead.get("mint_price_claim"),
                "price_status": str(lead.get("mint_price_status") or "UNKNOWN"),
                "evidence": lead.get("evidence") if isinstance(lead.get("evidence"), dict) else {},
            }
            if existing is not None:
                try:
                    merged = json.loads(existing["evidence_json"] or "{}")
                except (TypeError, json.JSONDecodeError):
                    merged = {}
                source_evidence = merged.get("source_evidence", [])
                if not isinstance(source_evidence, list):
                    source_evidence = []
                if not any(isinstance(item, dict) and item.get("source_name") == source_name
                           and item.get("source_url") == source_url for item in source_evidence):
                    source_evidence.append(source_evidence_item)
                merged["source_evidence"] = source_evidence
                connection.execute(
                    """UPDATE campaign_source_leads SET
                       contract_or_mint_address=COALESCE(contract_or_mint_address, ?),
                       address_kind=CASE WHEN address_kind IN ('UNKNOWN','NOT_PROVIDED_BY_SOURCE') THEN ? ELSE address_kind END,
                       official_website_claim=COALESCE(official_website_claim, ?),
                       announcement_url_claim=COALESCE(announcement_url_claim, ?),
                       launchpad_url=COALESCE(launchpad_url, ?),
                       expected_mint_at=COALESCE(expected_mint_at, ?),
                       mint_date_claim=COALESCE(mint_date_claim, ?), mint_time_claim=COALESCE(mint_time_claim, ?),
                       mint_price_claim=CASE WHEN mint_price_claim IS NULL OR mint_price_claim='' THEN ? ELSE mint_price_claim END,
                       mint_price_status=CASE WHEN mint_price_status='UNKNOWN' THEN ? ELSE mint_price_status END,
                       source_confidence=CASE WHEN source_confidence='LOW' THEN ? ELSE source_confidence END,
                       last_seen_at=?, evidence_json=? WHERE id=?""",
                    (address, str(lead.get("address_kind") or "UNKNOWN"),
                     lead.get("official_website_claim"), lead.get("announcement_url_claim"),
                     lead.get("launchpad_url"), lead.get("expected_mint_at"), lead.get("mint_date_claim"),
                     lead.get("mint_time_claim"), lead.get("mint_price_claim"),
                     str(lead.get("mint_price_status") or "UNKNOWN"),
                     str(lead.get("source_confidence") or "LOW"), now,
                     json.dumps(merged, sort_keys=True), int(existing["id"])),
                )
                connection.commit()
                return False

            connection.execute(
                """INSERT INTO campaign_source_leads
                   (chain_identifier, identity_key, project_name, source_name, source_url,
                    official_website_claim, announcement_url_claim, launchpad_url, expected_mint_at,
                    mint_date_claim, mint_time_claim, contract_or_mint_address, address_kind,
                    mint_price_claim, mint_price_status, eligibility_status, verification_status,
                    risk_assessment, source_confidence, first_seen_at, last_seen_at, evidence_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (chain, identity, name, source_name, source_url,
                 lead.get("official_website_claim"), lead.get("announcement_url_claim"), lead.get("launchpad_url"),
                 lead.get("expected_mint_at"), lead.get("mint_date_claim"), lead.get("mint_time_claim"), address,
                 str(lead.get("address_kind") or "UNKNOWN"), lead.get("mint_price_claim"),
                 str(lead.get("mint_price_status") or "UNKNOWN"),
                 str(lead.get("eligibility_status") or "UNKNOWN"),
                 str(lead.get("verification_status") or "DEFERRED_MISSING_EVIDENCE"),
                 str(lead.get("risk_assessment") or "HIGH"),
                 str(lead.get("source_confidence") or "LOW"), now, now,
                 json.dumps({"source_evidence": [source_evidence_item]}, sort_keys=True)),
            )
            connection.commit()
            return True
        finally:
            connection.close()

    def list_campaign_source_leads(self, limit: int = 500) -> list[sqlite3.Row]:
        connection = self.connect()
        try:
            return connection.execute(
                "SELECT * FROM campaign_source_leads ORDER BY last_seen_at DESC, id DESC LIMIT ?",
                (max(1, min(int(limit), 1000)),),
            ).fetchall()
        finally:
            connection.close()

    @staticmethod
    def _verification_address_key(value: str) -> str:
        return value.lower() if re.fullmatch(r"0x[0-9a-fA-F]{40}", value) else value

    @staticmethod
    def _clean_verification_evidence(value: Any, depth: int = 0) -> Any:
        """Bound stored evidence and drop fields that could contain wallet secrets/proofs."""
        sensitive = re.compile(r"(?i)(secret|private.?key|seed|mnemonic|password|credential|token|session|proof)")
        if depth > 6:
            return "[omitted: nesting limit]"
        if isinstance(value, dict):
            cleaned = {}
            for key, item in list(value.items())[:100]:
                safe_key = str(key)[:100]
                if sensitive.search(safe_key):
                    continue
                cleaned[safe_key] = Database._clean_verification_evidence(item, depth + 1)
            return cleaned
        if isinstance(value, (list, tuple)):
            return [Database._clean_verification_evidence(item, depth + 1) for item in list(value)[:100]]
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return str(value)[:4000]

    def save_wallet_verification_evidence(self, item: dict[str, Any]) -> bool:
        """Persist immutable, wallet/chain/stage/block-scoped verification evidence.

        This table is deliberately separate from collection-level discovery records;
        one wallet's eligibility or zero price never upgrades a global campaign row.
        """
        required = ("provider_id", "chain_identifier", "chain_id", "contract_address", "wallet_address",
                    "checked_at", "verification_status", "source_url")
        if any(not str(item.get(key) or "").strip() for key in required):
            raise ValueError("Wallet verification evidence is missing a required identity field")
        chain = str(item["chain_identifier"]).strip()[:64]
        chain_id = str(item["chain_id"]).strip()[:32]
        provider_id = str(item["provider_id"]).strip()[:64]
        contract = str(item["contract_address"]).strip()[:128]
        wallet = str(item["wallet_address"]).strip()[:128]
        stage_id = str(item.get("stage_id") or "")[:128]
        checked_at = str(item["checked_at"]).strip()[:80]
        status = str(item["verification_status"]).strip()[:80]
        source_url = str(item["source_url"]).strip()[:1000]
        try:
            parsed_url = urlsplit(source_url)
        except ValueError:
            parsed_url = None
        if (not provider_id or not chain or not chain_id or not contract or not wallet or not status
                or parsed_url is None or parsed_url.scheme != "https" or not parsed_url.hostname
                or parsed_url.username or parsed_url.password):
            raise ValueError("Wallet verification evidence contains an invalid identity or source URL")
        block = item.get("block_number")
        if block is not None:
            try:
                block = int(block)
            except (TypeError, ValueError):
                raise ValueError("block_number must be a non-negative integer or null") from None
            if block < 0:
                raise ValueError("block_number must be a non-negative integer or null")
        block_ref = str(block) if block is not None else "unknown"
        evidence = self._clean_verification_evidence(item.get("evidence") or {})
        evidence_json = json.dumps(evidence, sort_keys=True, separators=(",", ":"))
        if len(evidence_json.encode("utf-8")) > 32768:
            raise ValueError("Wallet verification evidence exceeds the storage limit")
        digest_material = json.dumps({
            "provider_id": provider_id, "chain": chain, "chain_id": chain_id, "contract": contract,
            "stage_id": stage_id, "wallet": wallet, "block": block_ref,
            "checked_at": checked_at, "status": status,
            "mint_price_wei": None if item.get("mint_price_wei") is None else str(item["mint_price_wei"]),
            "mint_currency_address": item.get("mint_currency_address"),
            "mint_available": item.get("mint_available"),
            "wallet_eligible": item.get("wallet_eligible"), "source_url": source_url,
            "evidence": evidence,
        }, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(digest_material.encode("utf-8")).hexdigest()
        bool_value = lambda key: None if item.get(key) is None else int(bool(item[key]))
        connection = self.connect()
        try:
            cursor = connection.execute(
                """INSERT OR IGNORE INTO wallet_verification_evidence
                   (provider_id,chain_identifier,chain_id,contract_address,contract_address_key,stage_id,
                    wallet_address,wallet_address_key,block_number,block_ref_key,checked_at,
                    verification_status,mint_price_wei,mint_currency_address,mint_available,
                    wallet_eligible,source_url,evidence_json,evidence_sha256)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (provider_id, chain, chain_id, contract, self._verification_address_key(contract), stage_id,
                 wallet, self._verification_address_key(wallet), block, block_ref, checked_at,
                 status, None if item.get("mint_price_wei") is None else str(item["mint_price_wei"])[:128],
                 None if item.get("mint_currency_address") is None else str(item["mint_currency_address"])[:128],
                 bool_value("mint_available"), bool_value("wallet_eligible"), source_url,
                 evidence_json, digest),
            )
            connection.commit()
            return cursor.rowcount == 1
        finally:
            connection.close()

    def list_wallet_verification_evidence(self, chain_identifier: str, contract_address: str,
                                          wallet_address: str, limit: int = 100,
                                          provider_id: str | None = None) -> list[sqlite3.Row]:
        connection = self.connect()
        try:
            if provider_id:
                return connection.execute(
                    """SELECT * FROM wallet_verification_evidence
                       WHERE provider_id=? AND chain_identifier=? AND contract_address_key=?
                         AND wallet_address_key=?
                       ORDER BY checked_at DESC,id DESC LIMIT ?""",
                    (str(provider_id)[:64], str(chain_identifier)[:64],
                     self._verification_address_key(str(contract_address).strip()),
                     self._verification_address_key(str(wallet_address).strip()),
                     max(1, min(int(limit), 500))),
                ).fetchall()
            return connection.execute(
                """SELECT * FROM wallet_verification_evidence
                   WHERE chain_identifier=? AND contract_address_key=? AND wallet_address_key=?
                   ORDER BY checked_at DESC,id DESC LIMIT ?""",
                (str(chain_identifier)[:64], self._verification_address_key(str(contract_address).strip()),
                 self._verification_address_key(str(wallet_address).strip()),
                 max(1, min(int(limit), 500))),
            ).fetchall()
        finally:
            connection.close()

    def list_wallet_verification_evidence_for_contract(self, chain_identifier: str,
                                                       contract_address: str,
                                                       limit: int = 20) -> list[sqlite3.Row]:
        """Return latest wallet-scoped checks for a campaign, without merging them globally."""
        connection = self.connect()
        try:
            return connection.execute(
                """SELECT provider_id,chain_id,contract_address,wallet_address,stage_id,
                          checked_at,verification_status,mint_price_wei,mint_currency_address,
                          mint_available,wallet_eligible,evidence_json,evidence_sha256
                   FROM wallet_verification_evidence
                   WHERE chain_identifier=? AND contract_address_key=?
                   ORDER BY checked_at DESC,id DESC LIMIT ?""",
                (str(chain_identifier)[:64], self._verification_address_key(contract_address),
                 max(1, min(int(limit), 100))),
            ).fetchall()
        finally:
            connection.close()

    def register_solana_project(self, project_name: str, official_website: Optional[str],
                                announcement_url: Optional[str], launchpad_url: Optional[str],
                                address: str, address_type: str, source_url: str,
                                announcement_date: Optional[str] = None,
                                mint_date: Optional[str] = None,
                                mint_price_claim: Optional[str] = None,
                                allowlist_status: str = "UNKNOWN") -> int:
        """Persist a manually sourced Solana candidate, deduplicated by address."""
        now = datetime.now(timezone.utc).isoformat()
        connection = self.connect()
        try:
            existing = connection.execute(
                "SELECT project_id FROM solana_watchlist_addresses WHERE address = ?", (address,)
            ).fetchone()
            if existing:
                connection.execute(
                    "INSERT OR IGNORE INTO solana_watchlist_sources (address, source_url, provenance, created_at) VALUES (?, ?, 'manual_registration', ?)",
                    (address, source_url, now),
                )
                connection.commit()
                return int(existing[0])
            project = connection.execute(
                """SELECT id FROM solana_watchlist_projects
                   WHERE lower(project_name)=lower(?) AND
                         ((official_website IS NULL AND ? IS NULL) OR lower(official_website)=lower(?))
                   ORDER BY id LIMIT 1""",
                (project_name, official_website, official_website),
            ).fetchone()
            if project:
                connection.execute(
                    """UPDATE solana_watchlist_projects SET
                       announcement_url=COALESCE(?, announcement_url), launchpad_url=COALESCE(?, launchpad_url),
                       announcement_date=COALESCE(?, announcement_date), mint_date=COALESCE(?, mint_date),
                       mint_price_claim=COALESCE(?, mint_price_claim),
                       allowlist_status=CASE WHEN ?='UNKNOWN' THEN allowlist_status ELSE ? END,
                       updated_at=? WHERE id=?""",
                    (announcement_url, launchpad_url, announcement_date, mint_date, mint_price_claim,
                     allowlist_status, allowlist_status, now, int(project[0])),
                )
            else:
                connection.execute(
                    """INSERT INTO solana_watchlist_projects
                    (project_name, official_website, announcement_url, launchpad_url,
                     announcement_date, mint_date, mint_price_claim, allowlist_status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (project_name, official_website, announcement_url, launchpad_url, announcement_date,
                     mint_date, mint_price_claim, allowlist_status, now, now),
                )
                project = connection.execute("SELECT last_insert_rowid()").fetchone()
            project_id = int(project[0])
            connection.execute(
                """INSERT INTO solana_watchlist_addresses
                (address, project_id, address_type, source_url, provenance, created_at)
                VALUES (?, ?, ?, ?, 'manual_registration', ?)""",
                (address, project_id, address_type, source_url, now),
            )
            connection.execute(
                "INSERT OR IGNORE INTO solana_watchlist_sources (address, source_url, provenance, created_at) VALUES (?, ?, 'manual_registration', ?)",
                (address, source_url, now),
            )
            connection.commit()
            return project_id
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def list_solana_watchlist(self) -> list[dict[str, Any]]:
        connection = self.connect()
        try:
            rows = connection.execute(
                """SELECT p.*, a.address, a.address_type, a.source_url, a.provenance,
                          a.verification_status, a.last_checked_at, a.observation_id
                   FROM solana_watchlist_projects p JOIN solana_watchlist_addresses a ON a.project_id=p.id
                   ORDER BY p.created_at DESC, a.created_at"""
            ).fetchall()
            sources = connection.execute(
                "SELECT address, source_url, provenance, created_at FROM solana_watchlist_sources ORDER BY id"
            ).fetchall()
            by_address: dict[str, list[dict[str, Any]]] = {}
            for source in sources:
                by_address.setdefault(source["address"], []).append({
                    "source_url": source["source_url"], "provenance": source["provenance"],
                    "created_at": source["created_at"],
                })
            result = []
            for row in rows:
                item = dict(row)
                item["sources"] = by_address.get(item["address"], [])
                result.append(item)
            return result
        finally:
            connection.close()

    def get_solana_watchlist_addresses(self) -> list[dict[str, Any]]:
        """Return public candidate addresses and source provenance for read-only scans."""
        return self.list_solana_watchlist()

    def mark_solana_watchlist_checked(self, address: str, observation_id: Optional[int],
                                      verification_status: str, blocked_reason: str) -> None:
        connection = self.connect()
        try:
            connection.execute(
                "UPDATE solana_watchlist_addresses SET last_checked_at=?, observation_id=?, verification_status=? WHERE address=?",
                (datetime.now(timezone.utc).isoformat(), observation_id, verification_status, address),
            )
            connection.execute(
                "UPDATE solana_watchlist_projects SET blocked_reason=?, updated_at=? WHERE id=(SELECT project_id FROM solana_watchlist_addresses WHERE address=?)",
                (blocked_reason[:500], datetime.now(timezone.utc).isoformat(), address),
            )
            connection.commit()
        finally:
            connection.close()

    def update_solana_watchlist_review(self, address: str, review_status: str,
                                       official_source_verified: bool, blocked_reason: str) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        connection = self.connect()
        try:
            row = connection.execute(
                "SELECT project_id FROM solana_watchlist_addresses WHERE address = ?", (address,)
            ).fetchone()
            if not row:
                return False
            connection.execute(
                "UPDATE solana_watchlist_projects SET review_status=?, official_source_verified=?, blocked_reason=?, updated_at=? WHERE id=?",
                (review_status, int(official_source_verified), blocked_reason[:500], now, int(row[0])),
            )
            connection.commit()
            return True
        finally:
            connection.close()

