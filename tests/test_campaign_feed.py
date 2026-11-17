"""Tests for bounded, lead-only RSS discovery and persistent deduplication."""

import tempfile
import unittest
from pathlib import Path

from app.database import Database
from app.discovery.campaign_feed import ColonyCampaignFeed, run_campaign_discovery_cycle, verification_profile


ADDR = "0x1234567890abcdef1234567890abcdef12345678"


class Response:
    def __init__(self, data): self.data = data
    def __enter__(self): return self
    def __exit__(self, *_): return None
    def read(self, limit):
        if len(self.data) > limit:
            return self.data[:limit]
        return self.data


def rss(*descriptions):
    items = "".join(
        "<item><title>Project %d free mint NFT on BNB Chain</title>"
        "<link>https://thecolony.ai/post/%d</link><pubDate>Sun, 15 Nov 2026 10:00:00 +0000</pubDate>"
        "<description><![CDATA[%s]]></description></item>" % (i, i, body)
        for i, body in enumerate(descriptions)
    )
    return ("<?xml version='1.0'?><rss><channel>%s</channel></rss>" % items).encode()


class CampaignFeedTests(unittest.TestCase):
    def test_extracts_lead_but_keeps_claims_unverified(self):
        data = rss("Free mint NFT on BNB Chain. NFT contract: %s. Site: https://project.example. "
                    "Mint() is listed; totalMinted is 2,000 of 3,333." % ADDR)
        source = ColonyCampaignFeed(opener=lambda *a, **k: Response(data))
        leads, count = source.fetch()
        self.assertEqual(count, 1)
        self.assertEqual(len(leads), 1)
        obs = leads[0].observation
        self.assertEqual(obs.contract_address, ADDR.lower())
        self.assertEqual(obs.details["campaign"]["source_verification"], "UNVERIFIED_RSS_LEAD")
        self.assertEqual(obs.risk_assessment, "high")
        self.assertIsNone(obs.mint_price)

    def test_sold_out_and_paid_announcements_are_rejected(self):
        data = rss("Free mint NFT BNB. NFT contract: %s. Sold out 3,333 / 3,333." % ADDR,
                    "Free mint NFT BNB. NFT contract: %s. Paid mint price 0.01 BNB." % ADDR)
        source = ColonyCampaignFeed(opener=lambda *a, **k: Response(data))
        leads, _ = source.fetch()
        self.assertEqual([lead.outcome for lead in leads], ["REJECTED_SOLD_OUT", "REJECTED_PAID"])

    def test_duplicate_contract_is_persisted_once_and_cycle_is_bounded(self):
        data = rss("Free mint NFT BNB. NFT contract: %s." % ADDR,
                    "Free mint NFT BNB. NFT contract: %s." % ADDR)
        source = ColonyCampaignFeed(opener=lambda *a, **k: Response(data))
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "hunter.sqlite3")
            first = run_campaign_discovery_cycle(db, source)
            second = run_campaign_discovery_cycle(db, source)
            self.assertEqual(first["requests"], 1)
            self.assertEqual(first["new_leads"], 1)
            self.assertEqual(first["duplicates"], 1)
            self.assertEqual(second["new_leads"], 0)
            self.assertEqual(second["duplicates"], 2)
            conn = db.connect()
            try:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0], 1)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM discovery_source_health").fetchone()[0], 1)
            finally:
                conn.close()

    def test_feed_response_size_is_bounded(self):
        data = b"x" * (ColonyCampaignFeed.max_bytes + 2)
        source = ColonyCampaignFeed(opener=lambda *a, **k: Response(data))
        with self.assertRaisesRegex(ValueError, "size limit"):
            source.fetch()

    def test_only_explicit_no_argument_mint_and_named_supply_counter_get_profile(self):
        source = ColonyCampaignFeed(opener=lambda *a, **k: Response(rss(
            "Free mint NFT on BNB Chain. NFT contract: %s. Site: https://project.example. "
            "Mint with mint() while totalMinted() is below 3,333 NFTs." % ADDR)))
        leads, _ = source.fetch()
        self.assertIsNotNone(verification_profile(leads[0].observation))
        parameterized = ColonyCampaignFeed(opener=lambda *a, **k: Response(rss(
            "Free mint NFT on BNB Chain. NFT contract: %s. Site: https://project.example. "
            "Call mint(uint256 nonce); totalMinted() is below 3,333 NFTs." % ADDR)))
        leads, _ = parameterized.fetch()
        self.assertIsNone(verification_profile(leads[0].observation))


if __name__ == "__main__":
    unittest.main()
