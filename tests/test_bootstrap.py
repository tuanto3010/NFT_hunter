"""Unit tests for the read-only Phase 1 foundation."""

import tempfile
import unittest
from pathlib import Path

from app.config import Settings
from app.database import Database
from app.discovery.nft_detector import is_contract_address
from app.discovery.project_sources import ProjectObservation
from app.discovery.scanner import Scanner


class FakeSource:
    name = "test"

    def __init__(self, values):
        self.values = values

    def fetch(self):
        return self.values


def observation(address, chain_id=56):
    return ProjectObservation(chain_id, address, "https://example.org", "2026-01-01T00:00:00Z")


class BootstrapTests(unittest.TestCase):
    def test_address_shape_validation(self):
        self.assertTrue(is_contract_address("0x" + "a" * 40))
        self.assertFalse(is_contract_address("0x123"))

    def test_scanner_deduplicates_case_insensitively_and_filters_chain(self):
        address = "0x" + "a" * 40
        scanner = Scanner([FakeSource([observation(address), observation(address.upper().replace("0X", "0x")), observation("0x" + "b" * 40, 1)])])
        self.assertEqual(len(scanner.scan()), 1)
        self.assertEqual(scanner.scan(), [])

    def test_sqlite_observation_deduplication(self):
        item = observation("0x" + "c" * 40)
        with tempfile.TemporaryDirectory() as directory:
            database = Database(Path(directory) / "hunter.sqlite3")
            self.assertTrue(database.save_observation(item))
            self.assertFalse(database.save_observation(item))

    def test_settings_reject_non_bsc_chain(self):
        import os
        prior = os.environ.get("BNB_CHAIN_ID")
        try:
            os.environ["BNB_CHAIN_ID"] = "1"
            with self.assertRaises(ValueError):
                Settings.from_env()
        finally:
            if prior is None:
                os.environ.pop("BNB_CHAIN_ID", None)
            else:
                os.environ["BNB_CHAIN_ID"] = prior


if __name__ == "__main__":
    unittest.main()

