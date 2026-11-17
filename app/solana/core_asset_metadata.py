"""Strict public metadata checks for the self-created Core test asset."""

import ipaddress
import json
import socket
from urllib.parse import urljoin, urlsplit

import httpx

EXPECTED_NAME = "NFT Hunter Test #001"
EXPECTED_SYMBOL = "HUNTER"
EXPECTED_DESCRIPTION = "First Solana NFT created by NFT Hunter"
MAX_METADATA_BYTES = 256 * 1024
MAX_IMAGE_BYTES = 5 * 1024 * 1024


class MetadataBlocked(ValueError):
    """Metadata cannot safely be used in a public Core asset preview."""


def _assert_public_https_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment):
            raise MetadataBlocked("Use a public HTTPS URL without credentials, query parameters, or fragments")
        host = parsed.hostname.encode("idna").decode("ascii")
        if host.lower() in {"localhost", "localhost.localdomain"} or host.endswith(".localhost"):
            raise MetadataBlocked("Local metadata hosts are not allowed")
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            ip = None
        if ip is not None:
            if not ip.is_global:
                raise MetadataBlocked("Metadata host must use a public address")
        else:
            answers = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
            if not answers or any(not ipaddress.ip_address(answer[4][0]).is_global for answer in answers):
                raise MetadataBlocked("Metadata host must resolve only to public addresses")
        return value
    except MetadataBlocked:
        raise
    except (UnicodeError, ValueError, OSError):
        raise MetadataBlocked("Metadata URL is invalid or its public host could not be verified") from None


def _read_public_response(client: httpx.Client, url: str, max_bytes: int):
    try:
        with client.stream("GET", url, headers={"Accept": "application/json,image/*"}) as response:
            if response.is_redirect:
                raise MetadataBlocked("Metadata redirects are not accepted; use the final HTTPS URL")
            if response.status_code != 200:
                raise MetadataBlocked("Metadata or image URL did not return HTTP 200")
            content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            chunks = []
            size = 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > max_bytes:
                    raise MetadataBlocked("Metadata or image exceeds the allowed size")
                chunks.append(chunk)
            return content_type, b"".join(chunks)
    except MetadataBlocked:
        raise
    except (httpx.HTTPError, OSError, TimeoutError):
        raise MetadataBlocked("Metadata or image could not be fetched from the public HTTPS host") from None


def fetch_and_validate_metadata(metadata_uri: str) -> dict:
    """Fetch public JSON and image using one bounded request each, with no redirects."""
    _assert_public_https_url(metadata_uri)
    with httpx.Client(timeout=httpx.Timeout(8.0), follow_redirects=False, trust_env=False) as client:
        content_type, body = _read_public_response(client, metadata_uri, MAX_METADATA_BYTES)
        if content_type not in {"application/json", "application/ld+json"}:
            raise MetadataBlocked("Metadata URL must return JSON content")
        try:
            metadata = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise MetadataBlocked("Metadata response is not valid JSON") from None
        if not isinstance(metadata, dict):
            raise MetadataBlocked("Metadata JSON must be an object")
        if (metadata.get("name") != EXPECTED_NAME or metadata.get("symbol") != EXPECTED_SYMBOL
                or metadata.get("description") != EXPECTED_DESCRIPTION):
            raise MetadataBlocked("Metadata fields do not match the NFT Hunter test asset")
        image = metadata.get("image")
        if not isinstance(image, str) or not image:
            raise MetadataBlocked("Metadata must include an image URL")
        image_url = urljoin(metadata_uri, image)
        _assert_public_https_url(image_url)
        image_type, image_body = _read_public_response(client, image_url, MAX_IMAGE_BYTES)
        if not image_type.startswith("image/") or not image_body:
            raise MetadataBlocked("Image URL must return a non-empty image")
        return metadata
