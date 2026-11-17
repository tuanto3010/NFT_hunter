import json
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.database import Database, SCHEMA


class WalletVerificationEvidenceTests(unittest.TestCase):
    def _record(self, wallet, *, block=123, eligible=True):
        return {
            "provider_id": "metamask",
            "chain_identifier": "bnb", "chain_id": 56,
            "contract_address": "0x" + "AB" * 20, "stage_id": "public-stage",
            "wallet_address": wallet, "block_number": block,
            "checked_at": "2026-11-15T12:00:00+00:00",
            "verification_status": "VERIFIED_READ_ONLY", "mint_price_wei": "0",
            "mint_currency_address": "0x" + "00" * 20,
            "mint_available": True, "wallet_eligible": eligible,
            "source_url": "https://project.example/mint",
            "evidence": {"simulation": "PASS", "proof_token": "must-not-persist",
                         "nested": {"private_key": "never", "block": 123}},
        }

    def test_evidence_is_scoped_by_wallet_and_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "hunter.sqlite3")
            first = self._record("0x" + "11" * 20, eligible=True)
            second = self._record("0x" + "22" * 20, eligible=False)
            self.assertTrue(db.save_wallet_verification_evidence(first))
            self.assertFalse(db.save_wallet_verification_evidence(first))
            self.assertTrue(db.save_wallet_verification_evidence(second))
            one = db.list_wallet_verification_evidence("bnb", first["contract_address"].lower(),
                                                       first["wallet_address"].upper().replace("0X", "0x"))
            two = db.list_wallet_verification_evidence("bnb", second["contract_address"], second["wallet_address"])
            self.assertEqual(len(one), 1)
            self.assertEqual(len(two), 1)
            self.assertEqual(one[0]["wallet_eligible"], 1)
            self.assertEqual(two[0]["wallet_eligible"], 0)
            saved = json.loads(one[0]["evidence_json"])
            serialized = json.dumps(saved).lower()
            self.assertNotIn("must-not-persist", serialized)
            self.assertNotIn("private_key", serialized)

    def test_stage_and_block_are_part_of_dedupe_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "hunter.sqlite3")
            base = self._record("0x" + "11" * 20)
            self.assertTrue(db.save_wallet_verification_evidence(base))
            other_stage = {**base, "stage_id": "allowlist-stage"}
            other_block = {**base, "block_number": 124}
            self.assertTrue(db.save_wallet_verification_evidence(other_stage))
            self.assertTrue(db.save_wallet_verification_evidence(other_block))
            self.assertEqual(len(db.list_wallet_verification_evidence(
                "bnb", base["contract_address"], base["wallet_address"])), 3)

    def test_provider_session_is_part_of_evidence_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "hunter.sqlite3")
            record = self._record("0x" + "11" * 20)
            self.assertTrue(db.save_wallet_verification_evidence(record))
            other_provider = {**record, "provider_id": "binance_agentic"}
            self.assertTrue(db.save_wallet_verification_evidence(other_provider))
            self.assertEqual(len(db.list_wallet_verification_evidence(
                "bnb", record["contract_address"], record["wallet_address"])), 2)

    def test_migration_adds_table_without_overwriting_existing_history(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.sqlite3"
            # Create the previous schema (all current tables except the additive evidence table/index).
            legacy_schema = re.sub(
                r"CREATE TABLE IF NOT EXISTS wallet_verification_evidence \(.*?\n\);\nCREATE UNIQUE INDEX IF NOT EXISTS wallet_verification_evidence_identity.*?\n\);\nCREATE INDEX IF NOT EXISTS wallet_verification_evidence_lookup.*?\n\);\n",
                "", SCHEMA, flags=re.DOTALL,
            )
            connection = sqlite3.connect(path)
            try:
                connection.executescript(legacy_schema)
                connection.execute(
                    "INSERT INTO scan_history(started_at,status,observations) VALUES(?,?,?)",
                    ("2026-11-14T00:00:00+00:00", "completed", 8),
                )
                connection.execute(
                    "INSERT INTO core_asset_transactions(preview_id,asset_address,wallet_address,network,metadata_uri,"
                    "transaction_digest,signature,status,estimated_total_lamports,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    ("old", "asset", "wallet", "solana-mainnet-beta", "https://metadata.example/a.json",
                     "digest", "signature", "SUBMISSION_UNKNOWN", "1000", "2026-11-14T00:00:00+00:00"),
                )
                connection.commit()
            finally:
                connection.close()
            db = Database(path)
            connection = db.connect()
            try:
                self.assertIsNotNone(connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='wallet_verification_evidence'").fetchone())
                self.assertEqual(tuple(connection.execute("SELECT status,observations FROM scan_history").fetchone()),
                                 ("completed", 8))
                self.assertEqual(connection.execute("SELECT status FROM core_asset_transactions").fetchone()[0],
                                 "SUBMISSION_UNKNOWN")
            finally:
                connection.close()
            # Reopening is idempotent and leaves the legacy rows intact.
            connection = Database(path).connect()
            try:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM scan_history").fetchone()[0], 1)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM wallet_verification_evidence").fetchone()[0], 0)
            finally:
                connection.close()

    def test_migrates_legacy_evidence_provider_column_and_index_without_losing_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy-evidence.sqlite3"
            connection = sqlite3.connect(path)
            try:
                connection.executescript("""
                    CREATE TABLE wallet_verification_evidence (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        chain_identifier TEXT NOT NULL, chain_id TEXT NOT NULL,
                        contract_address TEXT NOT NULL, contract_address_key TEXT NOT NULL,
                        stage_id TEXT NOT NULL DEFAULT '', wallet_address TEXT NOT NULL,
                        wallet_address_key TEXT NOT NULL, block_number INTEGER,
                        block_ref_key TEXT NOT NULL, checked_at TEXT NOT NULL,
                        verification_status TEXT NOT NULL, mint_price_wei TEXT,
                        mint_currency_address TEXT, mint_available INTEGER,
                        wallet_eligible INTEGER, source_url TEXT NOT NULL,
                        evidence_json TEXT NOT NULL DEFAULT '{}', evidence_sha256 TEXT NOT NULL
                    );
                    CREATE UNIQUE INDEX wallet_verification_evidence_identity
                        ON wallet_verification_evidence(chain_identifier, contract_address_key,
                        stage_id, wallet_address_key, block_ref_key);
                """)
                connection.execute("""INSERT INTO wallet_verification_evidence
                    (chain_identifier,chain_id,contract_address,contract_address_key,stage_id,
                     wallet_address,wallet_address_key,block_ref_key,checked_at,verification_status,
                     source_url,evidence_sha256)
                    VALUES ('ethereum','1','0x111','0x111','stage','0x222','0x222','123',
                     '2026-11-16T00:00:00Z','OLD_EVIDENCE','https://example.org','digest')""")
                connection.commit()
            finally:
                connection.close()

            connection = Database(path).connect()
            try:
                row = connection.execute(
                    "SELECT provider_id,verification_status FROM wallet_verification_evidence"
                ).fetchone()
                self.assertEqual(tuple(row), ("unknown", "OLD_EVIDENCE"))
                self.assertIn("provider_id", {
                    item[2] for item in connection.execute(
                        "PRAGMA index_info(wallet_verification_evidence_identity)")
                })
                self.assertEqual(connection.execute(
                    "SELECT COUNT(*) FROM wallet_verification_evidence").fetchone()[0], 1)
            finally:
                connection.close()

    def test_rejects_non_https_source_and_bad_block_number(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "hunter.sqlite3")
            with self.assertRaises(ValueError):
                db.save_wallet_verification_evidence({**self._record("0x" + "11" * 20),
                                                      "source_url": "http://project.example"})
            with self.assertRaises(ValueError):
                db.save_wallet_verification_evidence({**self._record("0x" + "11" * 20),
                                                      "block_number": -1})


if __name__ == "__main__":
    unittest.main()
