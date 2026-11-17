import json
import os
import unittest
from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

from app.discovery import opensea_live_check as live_check


class Response:
    def __init__(self, payload, status=200, content_type="application/json"):
        self.body = json.dumps(payload).encode("utf-8")
        self.status = status
        self.headers = {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, limit):
        return self.body[:limit]


class OpenSeaLiveCheckTests(unittest.TestCase):
    def setUp(self):
        self.old_key = os.environ.get("OPENSEA_API_KEY")
        self.old_chains = os.environ.get("OPENSEA_CHAINS")
        os.environ["OPENSEA_API_KEY"] = "private-test-key-value"
        os.environ["OPENSEA_CHAINS"] = ""

    def tearDown(self):
        if self.old_key is None:
            os.environ.pop("OPENSEA_API_KEY", None)
        else:
            os.environ["OPENSEA_API_KEY"] = self.old_key
        if self.old_chains is None:
            os.environ.pop("OPENSEA_CHAINS", None)
        else:
            os.environ["OPENSEA_CHAINS"] = self.old_chains

    def test_one_documented_request_reports_schema_and_never_prints_key(self):
        captured = []

        def opener(request, timeout):
            captured.append((request, timeout))
            return Response({"drops": [{"collection_slug": "example", "chain": "base"}], "next": None})

        code, report = live_check.run_preflight(opener=opener)
        request, timeout = captured[0]
        self.assertEqual(code, 0)
        self.assertEqual(len(captured), 1)
        self.assertEqual(timeout, 8)
        self.assertEqual(request.get_header("X-api-key"), "private-test-key-value")
        self.assertEqual(parse_qs(urlsplit(request.full_url).query), {"type": ["upcoming"], "limit": ["50"]})
        self.assertEqual(report["authentication"], "ACCEPTED_FOR_DROPS_ENDPOINT")
        self.assertEqual(report["drops_retrieved"], 1)
        self.assertEqual(report["response_schema"]["chain_counts"], {"base": 1})
        self.assertEqual(report["response_schema"]["sample_drops"][0]["collection_slug"], "example")
        self.assertNotIn("private-test-key-value", json.dumps(report))

    def test_configured_chains_match_existing_adapter_parameters(self):
        os.environ["OPENSEA_CHAINS"] = "ethereum,base"
        code, report = live_check.run_preflight(opener=lambda *_a, **_k: Response({"drops": []}))
        self.assertEqual(code, 0)
        self.assertEqual(report["request_params"], {
            "type": "upcoming", "limit": "50", "chains": "ethereum,base"
        })

    def test_hunter_network_configuration_is_normalized_for_live_request(self):
        os.environ["OPENSEA_CHAINS"] = "bnb,ethereum,base,solana,robinhood"
        code, report = live_check.run_preflight(opener=lambda *_a, **_k: Response({"drops": []}))
        self.assertEqual(code, 0)
        self.assertEqual(report["request_params"]["chains"], "bsc,ethereum,base,solana,robinhood")

    def test_400_reports_sanitized_error_body_and_does_not_start_cycle(self):
        body = json.dumps({"message": "invalid query", "api_key": "private-test-key-value"}).encode()

        def opener(request, timeout):
            raise HTTPError(request.full_url, 400, "Bad Request", {"Content-Type": "application/json"}, BytesIO(body))

        with patch.object(live_check, "_run_cycle") as run_cycle:
            code, report = live_check.run_preflight(run_cycle_on_success=True, opener=opener)
        self.assertEqual(code, 1)
        self.assertEqual(report["http_status"], 400)
        self.assertIn("invalid query", report["error_body_sanitized"])
        self.assertNotIn("private-test-key-value", json.dumps(report))
        self.assertEqual(report["campaign_cycle"], "NOT_RUN")
        run_cycle.assert_not_called()

    def test_cycle_only_runs_after_200_and_valid_drops_schema(self):
        with patch.object(live_check, "_run_cycle", return_value={
            "status": "ok", "requests": 1, "leads_found": 0, "new_leads": 0,
            "duplicates": 0, "source_metrics": [], "source_errors": [], "leads": [],
        }) as run_cycle:
            code, report = live_check.run_preflight(
                run_cycle_on_success=True,
                opener=lambda *_a, **_k: Response({"drops": []}),
            )
        self.assertEqual(code, 0)
        run_cycle.assert_called_once_with()
        self.assertEqual(report["campaign_cycle"]["status"], "ok")
        self.assertEqual(report["campaign_cycle"]["verified_free"], 0)

    def test_401_is_reported_as_authentication_rejected(self):
        def opener(request, timeout):
            raise HTTPError(request.full_url, 401, "Unauthorized", {}, BytesIO(b'{"error":"invalid key"}'))

        code, report = live_check.run_preflight(opener=opener)
        self.assertEqual(code, 1)
        self.assertEqual(report["authentication"], "REJECTED")
        self.assertEqual(report["request_count"], 1)


if __name__ == "__main__":
    unittest.main()
