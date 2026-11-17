import json
import unittest
from unittest.mock import patch

from app.solana.core_asset_metadata import (
    EXPECTED_DESCRIPTION, EXPECTED_NAME, EXPECTED_SYMBOL, MetadataBlocked,
    _assert_public_https_url, fetch_and_validate_metadata,
)


class _FakeResponse:
    def __init__(self, content_type, body, status_code=200):
        self.headers = {"content-type": content_type}
        self.status_code = status_code
        self.is_redirect = 300 <= status_code < 400
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def iter_bytes(self):
        yield self._body


class _FakeClient:
    def __init__(self, *_args, **_kwargs):
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def stream(self, method, url, **_kwargs):
        self.calls.append((method, url))
        if url.endswith("metadata.json"):
            body = json.dumps({"name": EXPECTED_NAME, "symbol": EXPECTED_SYMBOL,
                               "description": EXPECTED_DESCRIPTION, "image": "test.svg"}).encode()
            return _FakeResponse("application/json", body)
        return _FakeResponse("image/svg+xml", b"<svg></svg>")


class CoreMetadataValidationTests(unittest.TestCase):
    @patch("app.solana.core_asset_metadata.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("8.8.8.8", 443))])
    def test_public_https_metadata_and_image_are_bounded_and_validated(self, _dns):
        with patch("app.solana.core_asset_metadata.httpx.Client", _FakeClient):
            metadata = fetch_and_validate_metadata("https://assets.example.org/metadata.json")
        self.assertEqual(metadata["name"], EXPECTED_NAME)

    def test_local_or_private_metadata_hosts_are_rejected(self):
        with self.assertRaises(MetadataBlocked):
            _assert_public_https_url("http://127.0.0.1/metadata.json")
        with self.assertRaises(MetadataBlocked):
            _assert_public_https_url("https://localhost/metadata.json")
        with self.assertRaises(MetadataBlocked):
            _assert_public_https_url("https://user:pass@assets.example.org/metadata.json")

    @patch("app.solana.core_asset_metadata.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("8.8.8.8", 443))])
    def test_invalid_metadata_response_is_rejected(self, _dns):
        class BadClient(_FakeClient):
            def stream(self, method, url, **kwargs):
                return _FakeResponse("text/html", b"not json")
        with patch("app.solana.core_asset_metadata.httpx.Client", BadClient):
            with self.assertRaises(MetadataBlocked):
                fetch_and_validate_metadata("https://assets.example.org/metadata.json")
