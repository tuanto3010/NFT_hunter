"""One-shot, read-only reconciliation of saved Solana transaction signatures."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.config import load_env_file
from app.discovery.solana_scanner import SOLANA_MAINNET_RPC


ROOT = Path(__file__).resolve().parents[2]
MAX_RESPONSE_BYTES = 64 * 1024


def load_unknown_signatures(database_path: Path) -> list[tuple[str, str]]:
    """Read unknown public signatures without opening the database for writes."""
    absolute = database_path.resolve()
    uri = absolute.as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=5)
    try:
        return [(str(row[0]), str(row[1])) for row in connection.execute(
            """SELECT preview_id, signature FROM core_asset_transactions
               WHERE status='SUBMISSION_UNKNOWN' AND signature IS NOT NULL
               ORDER BY preview_id"""
        )]
    finally:
        connection.close()


def read_signature_statuses(signatures: list[str], endpoint: str,
                            opener: Callable[..., Any] = urlopen) -> dict[str, Any]:
    """Make exactly one JSON-RPC request for all signatures; never retry."""
    if not signatures:
        return {"status": "NO_UNKNOWN_SIGNATURES", "request_count": 0, "items": []}
    if not endpoint.startswith("https://"):
        return {"status": "BLOCKED_INVALID_RPC_CONFIGURATION", "request_count": 0, "items": []}
    body = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "getSignatureStatuses",
        "params": [signatures, {"searchTransactionHistory": True}],
    }, separators=(",", ":")).encode("utf-8")
    request = Request(endpoint, data=body, headers={
        "Content-Type": "application/json", "Accept": "application/json",
    }, method="POST")
    try:
        with opener(request, timeout=10) as response:
            http_status = int(response.status)
            payload = response.read(MAX_RESPONSE_BYTES + 1)
        if len(payload) > MAX_RESPONSE_BYTES:
            return {"status": "BLOCKED_RESPONSE_TOO_LARGE", "request_count": 1,
                    "http_status": http_status, "items": []}
        decoded = json.loads(payload.decode("utf-8"))
        if not isinstance(decoded, dict) or decoded.get("error") or "result" not in decoded:
            error = decoded.get("error") if isinstance(decoded, dict) else None
            code = error.get("code") if isinstance(error, dict) else None
            return {"status": "RPC_ERROR", "request_count": 1, "http_status": http_status,
                    "rpc_error_code": code, "items": []}
        rpc_result = decoded.get("result")
        values = rpc_result.get("value") if isinstance(rpc_result, dict) else None
        if not isinstance(values, list) or len(values) != len(signatures):
            return {"status": "INVALID_RPC_RESPONSE", "request_count": 1,
                    "http_status": http_status, "items": []}
        items = []
        for index, value in enumerate(values, start=1):
            if value is None:
                # A null result is scoped to this provider's retained/searchable
                # history. It is not proof that the transaction never landed.
                items.append({"record": index, "status": "RPC_HISTORY_UNAVAILABLE",
                              "confirmation_status": None, "slot": None, "execution_error": None})
            elif isinstance(value, dict):
                items.append({"record": index, "status": "FOUND",
                              "confirmation_status": value.get("confirmationStatus"),
                              "slot": value.get("slot"),
                              "execution_error": value.get("err") is not None})
            else:
                return {"status": "INVALID_RPC_RESPONSE", "request_count": 1,
                        "http_status": http_status, "items": []}
        return {"status": "CHECKED", "request_count": 1, "http_status": http_status,
                "items": items}
    except HTTPError as exc:
        return {"status": "HTTP_ERROR", "request_count": 1, "http_status": int(exc.code), "items": []}
    except URLError as exc:
        reason = exc.reason
        return {"status": "NETWORK_ERROR", "request_count": 1,
                "error_category": type(reason).__name__, "items": []}
    except (TimeoutError, OSError) as exc:
        return {"status": "NETWORK_ERROR", "request_count": 1,
                "error_category": type(exc).__name__, "items": []}
    except (ValueError, UnicodeDecodeError):
        return {"status": "INVALID_RPC_RESPONSE", "request_count": 1, "items": []}


def main() -> int:
    load_env_file(ROOT / ".env")
    database_path = Path(os.getenv("DATABASE_PATH", "data/nft_hunter.sqlite3"))
    if not database_path.is_absolute():
        database_path = ROOT / database_path
    records = load_unknown_signatures(database_path)
    endpoint = os.getenv("SOLANA_RPC_URL", "").strip() or SOLANA_MAINNET_RPC
    result = read_signature_statuses([signature for _, signature in records], endpoint)
    print(json.dumps({"unknown_records": len(records), **result}, sort_keys=True))
    # A not-found or transport result does not establish transaction failure.
    return 0 if result["status"] in {"CHECKED", "NO_UNKNOWN_SIGNATURES"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
