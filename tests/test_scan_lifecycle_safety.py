"""Cross-process scan lifecycle and abandoned-owner recovery tests."""

from __future__ import annotations

import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from app.database import Database, ScanAlreadyRunning, ScanOwnershipUnknown
from app.discovery.chain_adapter import CHAIN_SPECS
from app.scheduler import ScanScheduler


class ScanLifecycleSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.tmp.name) / "scan.sqlite3")

    def tearDown(self):
        self.tmp.cleanup()

    def test_normal_scan_finishes_and_releases_lock(self):
        class Adapter:
            spec = CHAIN_SPECS["bnb"]
            def scan(self):
                return []

        with patch("app.scheduler.process_start_identity", return_value="test-owner"), \
             patch("app.scheduler.process_instance_alive", return_value=True):
            ScanScheduler([Adapter()], self.database).scan_once()
        connection = self.database.connect()
        try:
            row = connection.execute("SELECT status, finished_at, owner_pid FROM scan_history").fetchone()
            lock = connection.execute("SELECT * FROM scan_lock").fetchone()
            self.assertEqual(row["status"], "complete")
            self.assertIsNotNone(row["finished_at"])
            self.assertIsNotNone(row["owner_pid"])
            self.assertIsNone(lock)
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        finally:
            connection.close()

    def test_keyboard_interrupt_is_persisted_as_interrupted(self):
        class Adapter:
            spec = CHAIN_SPECS["bnb"]
            def scan(self):
                raise KeyboardInterrupt()

        with patch("app.scheduler.process_start_identity", return_value="test-owner"), \
             patch("app.scheduler.process_instance_alive", return_value=True):
            with self.assertRaises(KeyboardInterrupt):
                ScanScheduler([Adapter()], self.database, max_workers=1).scan_once()
        connection = self.database.connect()
        try:
            row = connection.execute("SELECT status, finished_at, error FROM scan_history").fetchone()
            self.assertEqual(row["status"], "interrupted")
            self.assertIsNotNone(row["finished_at"])
            self.assertEqual(row["error"], "Interrupted by keyboard signal")
            self.assertIsNone(connection.execute("SELECT * FROM scan_lock").fetchone())
        finally:
            connection.close()

    def test_confirmed_dead_owner_is_abandoned_and_new_lease_acquired(self):
        old_id = self.database.begin_owned_scan(1234, "old-instance", lambda _pid, _identity: True)
        new_id = self.database.begin_owned_scan(5678, "new-instance", lambda _pid, _identity: False)
        connection = self.database.connect()
        try:
            old = connection.execute("SELECT status, finished_at, error FROM scan_history WHERE id=?", (old_id,)).fetchone()
            new = connection.execute("SELECT status FROM scan_history WHERE id=?", (new_id,)).fetchone()
            lock = connection.execute("SELECT scan_id, owner_pid FROM scan_lock WHERE id=1").fetchone()
            self.assertEqual(old["status"], "abandoned")
            self.assertIsNotNone(old["finished_at"])
            self.assertIn("confirmed inactive", old["error"])
            self.assertEqual(new["status"], "running")
            self.assertEqual((lock["scan_id"], lock["owner_pid"]), (new_id, 5678))
        finally:
            connection.close()

    def test_live_or_unknown_owner_is_never_recovered(self):
        scan_id = self.database.begin_owned_scan(1234, "live-instance", lambda _pid, _identity: True)
        with self.assertRaises(ScanAlreadyRunning):
            self.database.begin_owned_scan(5678, "new-instance", lambda _pid, _identity: True)
        with self.assertRaises(ScanOwnershipUnknown):
            self.database.begin_owned_scan(5678, "new-instance", lambda _pid, _identity: None)
        connection = self.database.connect()
        try:
            row = connection.execute("SELECT status, finished_at FROM scan_history WHERE id=?", (scan_id,)).fetchone()
            self.assertEqual(row["status"], "running")
            self.assertIsNone(row["finished_at"])
        finally:
            connection.close()

    def test_concurrent_scanner_instances_are_blocked_by_database_lease(self):
        entered = threading.Event()
        release = threading.Event()
        failures = []

        class BlockingAdapter:
            spec = CHAIN_SPECS["bnb"]
            def scan(self):
                entered.set()
                release.wait(timeout=5)
                return []

        scheduler = ScanScheduler([BlockingAdapter()], self.database, max_workers=1)
        with patch("app.scheduler.process_start_identity", return_value="same-process"), \
             patch("app.scheduler.process_instance_alive", return_value=True):
            thread = threading.Thread(target=lambda: scheduler.scan_once())
            thread.start()
            self.assertTrue(entered.wait(timeout=3))
            try:
                with self.assertRaises(ScanAlreadyRunning):
                    ScanScheduler([], self.database).scan_once()
            except BaseException as exc:
                failures.append(exc)
            finally:
                release.set()
                thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(failures, [])

    def test_legacy_running_rows_without_owner_are_preserved_and_block_scans(self):
        legacy_id = self.database.begin_scan()
        with self.assertRaises(ScanOwnershipUnknown):
            self.database.begin_owned_scan(5678, "new-instance", lambda _pid, _identity: False)
        connection = self.database.connect()
        try:
            row = connection.execute("SELECT status, finished_at FROM scan_history WHERE id=?", (legacy_id,)).fetchone()
            self.assertEqual(row["status"], "running")
            self.assertIsNone(row["finished_at"])
        finally:
            connection.close()

    def test_old_schema_migration_preserves_running_and_completed_history(self):
        path = Path(self.tmp.name) / "legacy.sqlite3"
        raw = sqlite3.connect(path)
        raw.executescript("""
            CREATE TABLE scan_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL,
                finished_at TEXT, status TEXT NOT NULL,
                observations INTEGER NOT NULL DEFAULT 0, error TEXT
            );
            INSERT INTO scan_history (started_at, status) VALUES ('legacy-running', 'running');
            INSERT INTO scan_history (started_at, finished_at, status, observations)
                VALUES ('old-start', 'old-finish', 'complete', 4);
        """)
        raw.close()
        migrated = Database(path)
        connection = migrated.connect()
        try:
            rows = connection.execute(
                "SELECT id, status, finished_at, observations, owner_pid, owner_start_identity "
                "FROM scan_history ORDER BY id"
            ).fetchall()
            self.assertEqual(rows[0]["status"], "running")
            self.assertIsNone(rows[0]["owner_pid"])
            self.assertEqual((rows[1]["status"], rows[1]["finished_at"], rows[1]["observations"]),
                             ("complete", "old-finish", 4))
            self.assertIsNotNone(connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='scan_lock'"
            ).fetchone())
        finally:
            connection.close()
        with self.assertRaises(ScanOwnershipUnknown):
            migrated.begin_owned_scan(5678, "new-instance", lambda _pid, _identity: False)

    def test_operator_recovery_requires_confirmation_and_backup_and_preserves_discoveries(self):
        path = Path(self.tmp.name) / "operator.sqlite3"
        raw = sqlite3.connect(path)
        raw.executescript("""
            CREATE TABLE scan_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL,
                finished_at TEXT, status TEXT NOT NULL,
                observations INTEGER NOT NULL DEFAULT 0, error TEXT
            );
            INSERT INTO scan_history (id, started_at, status) VALUES (18, 'old-a', 'running');
            INSERT INTO scan_history (id, started_at, status) VALUES (19, 'old-b', 'running');
            INSERT INTO scan_history (id, started_at, finished_at, status, observations)
                VALUES (17, 'done', 'done-at', 'complete', 2);
            CREATE TABLE observations (
                id INTEGER PRIMARY KEY, chain_id INTEGER NOT NULL, contract_address TEXT NOT NULL,
                project_name TEXT, source_url TEXT NOT NULL, observed_at TEXT NOT NULL,
                verified INTEGER NOT NULL DEFAULT 0, details_json TEXT NOT NULL DEFAULT '{}',
                UNIQUE(chain_id, contract_address)
            );
            INSERT INTO observations
                (id, chain_id, contract_address, project_name, source_url, observed_at)
                VALUES (1, 56, '0x' || printf('%040d', 1), 'preserve-me', 'https://example.org', 'old');
        """)
        raw.close()
        database = Database(path)
        with self.assertRaises(ValueError):
            database.recover_legacy_scans([18, 19], "operator recovery", False)
        self.assertEqual(list(path.parent.glob(path.name + ".pre-recovery-*.bak")), [])

        result = database.recover_legacy_scans(
            [18, 19], "Operator verified scanner processes stopped", True
        )
        backup = Path(result["backup_path"])
        self.assertTrue(backup.is_file())
        backup_db = sqlite3.connect(str(backup))
        try:
            self.assertEqual(backup_db.execute("SELECT status FROM scan_history WHERE id=18").fetchone()[0], "running")
            self.assertIsNone(backup_db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='scan_lock'"
            ).fetchone())
        finally:
            backup_db.close()

        connection = database.connect()
        try:
            recovered = connection.execute(
                "SELECT id, status, finished_at, observations FROM scan_history WHERE id IN (18,19) ORDER BY id"
            ).fetchall()
            completed = connection.execute("SELECT status, finished_at FROM scan_history WHERE id=17").fetchone()
            audits = connection.execute(
                "SELECT scan_id, recovered_at, reason, previous_status, operator_confirmation, backup_path "
                "FROM scan_recovery_audit ORDER BY scan_id"
            ).fetchall()
            discovery = connection.execute("SELECT project_name FROM observations WHERE id=1").fetchone()[0]
            self.assertTrue(all(row["status"] == "abandoned" and row["finished_at"] for row in recovered))
            self.assertTrue(all(row["observations"] == 0 for row in recovered))
            self.assertEqual((completed["status"], completed["finished_at"]), ("complete", "done-at"))
            self.assertEqual([row["scan_id"] for row in audits], [18, 19])
            self.assertTrue(all(row["previous_status"] == "running" for row in audits))
            self.assertTrue(all("Operator verified" in row["reason"] for row in audits))
            self.assertTrue(all(row["backup_path"] == str(backup) for row in audits))
            self.assertEqual(discovery, "preserve-me")
            self.assertIsNone(connection.execute("SELECT * FROM scan_lock").fetchone())
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
