"""One-shot, read-only BNB JSON-RPC diagnostics for the local dashboard."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import re
import socket
import ssl
import time
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from app.config import load_env_file


class _RejectRedirects(HTTPRedirectHandler):
    """Do not follow redirects: one manual test must remain one request."""

    def redirect_request(self, request, file, code, message, headers, new_url):
        return None


def _open_once(request: Request, timeout: float):
    return build_opener(_RejectRedirects()).open(request, timeout=timeout)


def _safe_host(host: str) -> str:
    labels = host.rstrip(".").split(".")
    for index, label in enumerate(labels):
        secretish = (
            len(label) > 20
            or re.fullmatch(r"(?i)(?:[0-9a-f]{16,}|[a-z0-9_-]{24,})", label) is not None
            or re.search(r"(?i)(api[-_]?key|token|secret|credential|session)", label) is not None
        )
        if secretish:
            labels[index] = "[hidden]"
    return ".".join(labels)


def masked_endpoint_label(endpoint: str) -> str:
    """Return a UI-safe endpoint label without userinfo, URL paths, or values."""
    if not endpoint:
        return "Not configured"
    try:
        parts = urlsplit(endpoint)
        if not parts.scheme or not parts.hostname:
            return "Configured endpoint (details hidden)"
        host = _safe_host(parts.hostname)
        port = parts.port
        default_port = (parts.scheme.lower() == "https" and port == 443) or (
            parts.scheme.lower() == "http" and port == 80
        )
        authority = host if not port or default_port else f"{host}:{port}"
        label = f"{parts.scheme.lower()}://{authority}"
        if parts.path and parts.path != "/":
            label += "/[path hidden]"
        if parts.query or parts.fragment:
            label += "?[parameters hidden]"
        if parts.username is not None or parts.password is not None:
            # Userinfo itself is never copied into the returned label.
            return label
        return label
    except (ValueError, UnicodeError):
        return "Configured endpoint (details hidden)"


class BnbRpcDiagnostics:
    """Per-dashboard diagnostic state; each test makes exactly one HTTP request."""

    def __init__(self, endpoint: Optional[str] = None,
                 opener: Callable[..., Any] = _open_once,
                 clock: Callable[[], float] = time.monotonic,
                 timeout_seconds: float = 10.0) -> None:
        self.endpoint = (endpoint or "").strip()
        self._opener = opener
        self._clock = clock
        self.timeout_seconds = timeout_seconds
        self._state_lock = Lock()
        self._last: Optional[dict[str, Any]] = None

    @classmethod
    def from_env(cls) -> "BnbRpcDiagnostics":
        load_env_file(Path(__file__).resolve().parents[2] / ".env")
        return cls(os.getenv("BNB_RPC_URL", ""))

    def snapshot(self) -> dict[str, Any]:
        with self._state_lock:
            last = dict(self._last) if self._last else None
        configured = bool(self.endpoint)
        return {
            "endpoint": masked_endpoint_label(self.endpoint),
            "configured": configured,
            "state": last["state"] if last else ("NOT_TESTED" if configured else "NOT_CONFIGURED"),
            "last_checked_at": last["last_checked_at"] if last else None,
            "error_details": last["error_details"] if last else (
                None if configured else "BNB_RPC_URL is not configured."
            ),
            "chain_id": last.get("chain_id") if last else None,
            "http_status": last.get("http_status") if last else None,
        }

    def test_once(self) -> dict[str, Any]:
        checked_at = datetime.now(timezone.utc).isoformat()
        if not self.endpoint:
            return self._save({"state": "NOT_CONFIGURED", "last_checked_at": checked_at,
                               "error_details": "BNB_RPC_URL is not configured.", "chain_id": None,
                               "http_status": None})
        try:
            parts = urlsplit(self.endpoint)
            if parts.scheme.lower() != "https" or not parts.hostname:
                return self._save({"state": "NOT_CONFIGURED", "last_checked_at": checked_at,
                                   "error_details": "BNB RPC endpoint must be a valid HTTPS URL.",
                                   "chain_id": None, "http_status": None})
        except ValueError:
            return self._save({"state": "NOT_CONFIGURED", "last_checked_at": checked_at,
                               "error_details": "BNB RPC endpoint is not a valid HTTPS URL.",
                               "chain_id": None, "http_status": None})

        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_chainId", "params": []}).encode("utf-8")
        request = Request(self.endpoint, data=body,
                          headers={"Content-Type": "application/json", "Accept": "application/json"},
                          method="POST")
        started = self._clock()
        try:
            with self._opener(request, timeout=self.timeout_seconds) as response:
                status = int(response.status)
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            status_code = int(exc.code)
            exc.close()
            return self._save({"state": "HTTP_ERROR", "last_checked_at": checked_at,
                               "error_details": f"RPC returned HTTP {status_code}.",
                               "chain_id": None, "http_status": status_code})
        except (URLError, TimeoutError, socket.timeout, socket.gaierror,
                PermissionError, ssl.SSLError, OSError) as exc:
            state, detail = self._network_failure(exc)
            return self._save({"state": state, "last_checked_at": checked_at,
                               "error_details": detail, "chain_id": None, "http_status": None})
        except (UnicodeError, json.JSONDecodeError):
            return self._save({"state": "HTTP_ERROR", "last_checked_at": checked_at,
                               "error_details": "RPC response was not valid JSON.",
                               "chain_id": None, "http_status": None})
        except Exception as exc:
            # Do not return exception text: urllib errors may contain the full URL.
            state, detail = self._network_failure(exc)
            return self._save({"state": state, "last_checked_at": checked_at,
                               "error_details": detail, "chain_id": None, "http_status": None})

        elapsed_ms = max(0, round((self._clock() - started) * 1000))
        if not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0" or "result" not in payload:
            detail = "RPC response did not contain a valid eth_chainId result."
            state = "HTTP_ERROR"
            chain_id = None
        else:
            try:
                chain_id = int(str(payload["result"]), 16)
            except (TypeError, ValueError):
                chain_id = None
            if chain_id is None:
                state, detail = "HTTP_ERROR", "RPC returned an invalid chain ID result."
            elif chain_id != 56:
                state, detail = "HTTP_ERROR", f"RPC responded with chain ID {chain_id}; expected BNB Chain ID 56."
            else:
                state, detail = "CONNECTED", None
        return self._save({"state": state, "last_checked_at": checked_at,
                           "error_details": detail, "chain_id": chain_id,
                           "http_status": status, "response_time_ms": elapsed_ms})

    @staticmethod
    def _network_failure(exc: BaseException) -> tuple[str, str]:
        reason = exc.reason if isinstance(exc, URLError) else exc
        if isinstance(reason, (TimeoutError, socket.timeout)):
            return "TIMEOUT", "Connection timed out before an RPC response was received."
        if isinstance(reason, socket.gaierror):
            return "NETWORK_BLOCKED", "DNS resolution failed for the configured RPC host."
        if isinstance(reason, PermissionError) or getattr(reason, "winerror", None) == 10013:
            return "NETWORK_BLOCKED", "Socket access was denied by local network policy (WinError 10013)."
        if isinstance(reason, ssl.SSLError):
            return "NETWORK_BLOCKED", "TLS connection failed before an RPC response was received."
        if isinstance(reason, OSError) and getattr(reason, "winerror", None) == 10013:
            return "NETWORK_BLOCKED", "Socket access was denied by local network policy (WinError 10013)."
        if isinstance(reason, BaseException):
            return "NETWORK_BLOCKED", f"Network connection failed ({type(reason).__name__})."
        return "NETWORK_BLOCKED", "Network connection failed before an RPC response was received."

    def _save(self, result: dict[str, Any]) -> dict[str, Any]:
        with self._state_lock:
            self._last = dict(result)
        return self.snapshot()
