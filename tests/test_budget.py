"""Tests for fail-closed shared budget accounting."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from app.budget import BudgetError, BudgetLedger
from app.database import Database


class BudgetLedgerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.directory.name) / "budget.sqlite3")
        self.ledger = BudgetLedger(self.database)
        self.now = datetime.now(timezone.utc)

    def tearDown(self):
        self.directory.cleanup()

    def reserve(self, reference, cost, at=None):
        return self.ledger.reserve(
            transaction_ref=reference, provider_id="phantom", chain_identifier="solana-mainnet-beta",
            wallet_address="wallet-public-address", max_cost_usd=cost, native_amount="0.002",
            native_currency="SOL", quote_source_url="https://quote.example/rate",
            quote_observed_at=(at or self.now).isoformat(), now=self.now,
        )

    def test_unknown_legacy_submission_blocks_available_and_reservation(self):
        connection = self.database.connect()
        connection.execute(
            """INSERT INTO core_asset_transactions
               (preview_id,asset_address,wallet_address,network,metadata_uri,transaction_digest,
                signature,status,estimated_total_lamports,created_at)
               VALUES ('old-preview','asset-address','wallet','solana-mainnet-beta','https://meta.example',
                       'digest','sig','SUBMISSION_UNKNOWN','2000000',?)""",
            (self.now.isoformat(),),
        )
        connection.execute(
            """INSERT INTO core_asset_transactions
               (preview_id,asset_address,wallet_address,network,metadata_uri,transaction_digest,
                signature,status,estimated_total_lamports,created_at)
               VALUES ('asset-unverified','asset-2','wallet','solana-mainnet-beta','https://metadata.example',
                       'digest-2','sig-2','ASSET_VERIFICATION_PENDING','2000000',?)""",
            (self.now.isoformat(),),
        )
        connection.commit()
        connection.close()
        snapshot = self.ledger.snapshot(self.now)
        self.assertEqual(snapshot["state"], "BLOCKED_UNKNOWN_LIABILITY")
        self.assertEqual(snapshot["unknown_liability_count"], 2)
        self.assertIsNone(snapshot["available_usd"])
        with self.assertRaisesRegex(BudgetError, "Unknown transaction liabilities"):
            self.reserve("new", "0.25")

    def test_reservation_requires_fresh_quote_and_stays_under_cap(self):
        snapshot = self.reserve("tx1", "2.00")
        self.assertEqual(snapshot["reserved_usd"], "2.00")
        self.assertEqual(snapshot["available_usd"], "3.00")
        with self.assertRaisesRegex(BudgetError, "exceed"):
            self.reserve("tx2", "3.01")
        with self.assertRaisesRegex(BudgetError, "stale"):
            self.reserve("old-quote", "0.1", self.now - timedelta(minutes=6))

    def test_no_duplicate_reservation_or_unconfirmed_release(self):
        self.reserve("same", "1.00")
        with self.assertRaisesRegex(BudgetError, "already exists"):
            self.reserve("same", "1.00")
        with self.assertRaisesRegex(BudgetError, "confirmation"):
            self.ledger.release("same", "maybe not sent", confirmed_unsubmitted=False)
        self.ledger.release("same", "operator confirmed not submitted", confirmed_unsubmitted=True)
        self.assertEqual(self.ledger.snapshot(self.now)["reserved_usd"], "0")

    def test_saved_submission_signature_cannot_be_released_even_with_operator_flag(self):
        self.reserve("submitted-preview", "0.50")
        connection = self.database.connect()
        connection.execute(
            """INSERT INTO core_asset_transactions
               (preview_id,asset_address,wallet_address,network,metadata_uri,transaction_digest,
                signature,status,estimated_total_lamports,created_at)
               VALUES ('submitted-preview','asset','wallet','solana-mainnet-beta','https://meta.example',
                       'digest','signature','SUBMISSION_UNKNOWN','1000',?)""",
            (self.now.isoformat(),),
        )
        connection.commit()
        connection.close()
        with self.assertRaisesRegex(BudgetError, "saved transaction signature"):
            self.ledger.release("submitted-preview", "operator says not sent", confirmed_unsubmitted=True)
        snapshot = self.ledger.snapshot(self.now)
        self.assertEqual(snapshot["reserved_usd"], "0.50")
        self.assertEqual(snapshot["available_usd"], "4.50")

    def test_settlement_resolves_unknown_only_with_evidenced_source(self):
        self.reserve("tx1", "1.25")
        self.ledger.mark_unknown("tx1", "provider outcome uncertain")
        self.assertEqual(self.ledger.snapshot(self.now)["state"], "BLOCKED_UNKNOWN_LIABILITY")
        self.ledger.settle("tx1", "0.01", "https://explorer.example/tx")
        snapshot = self.ledger.snapshot(self.now)
        self.assertEqual(snapshot["state"], "AVAILABLE")
        self.assertEqual(snapshot["confirmed_spent_usd"], "0.01")

    def test_quote_requires_https_and_nonnegative_finite_values(self):
        with self.assertRaisesRegex(BudgetError, "HTTPS"):
            self.ledger.reserve(
                transaction_ref="bad-url", provider_id="phantom", chain_identifier="solana-mainnet-beta",
                wallet_address="wallet", max_cost_usd="0.1", native_amount="0.1", native_currency="SOL",
                quote_source_url="http://quote.example", quote_observed_at=self.now.isoformat(), now=self.now,
            )
        with self.assertRaisesRegex(BudgetError, "non-negative"):
            self.reserve("negative", "-1")

    def test_concurrent_reservations_cannot_overbook_cap(self):
        def attempt(index):
            try:
                self.reserve(f"tx-{index}", "3.00")
                return "reserved"
            except BudgetError:
                return "blocked"

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, range(2)))
        self.assertEqual(sorted(results), ["blocked", "reserved"])
        self.assertEqual(self.ledger.snapshot(self.now)["reserved_usd"], "3.00")


if __name__ == "__main__":
    unittest.main()
