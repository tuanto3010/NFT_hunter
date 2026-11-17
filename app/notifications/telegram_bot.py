"""Minimal Telegram Bot API sender with persistent duplicate suppression.

This module does not import or invoke discovery. It only sends explicitly
requested messages and never logs credentials or raw HTTP/API error details.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
import logging
import os
from pathlib import Path
import sqlite3
import time
from typing import Callable, Optional
from uuid import uuid4

import httpx

from app.config import load_env_file

LOGGER = logging.getLogger(__name__)
BOT_API = "https://api.telegram.org"


class TelegramDeliveryError(RuntimeError):
    """Sanitized error suitable for display; never includes request details."""


@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str = field(repr=False)
    chat_id: str = field(repr=False)
    timeout_seconds: float = 10.0
    max_attempts: int = 2

    @classmethod
    def from_env(cls) -> "TelegramConfig":
        """Read credentials from process environment or the local .env file."""
        load_env_file(Path(__file__).resolve().parents[2] / ".env")
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        if not token or not chat_id:
            raise TelegramDeliveryError(
                "Telegram is not configured. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env."
            )
        return cls(bot_token=token, chat_id=chat_id)


@dataclass(frozen=True)
class DeliveryResult:
    sent: bool
    duplicate: bool = False
    message_id: Optional[int] = None


class DeliveryLedger:
    """SQLite deduplication ledger; message text and credentials are not stored."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def claim(self, dedupe_key: str) -> bool:
        if not dedupe_key or len(dedupe_key) > 200:
            raise ValueError("dedupe_key must contain 1 to 200 characters")
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        connection = sqlite3.connect(str(self.database_path), timeout=10.0)
        try:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS notification_deliveries (
                    dedupe_key TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    message_id INTEGER
                )"""
            )
            cursor = connection.execute(
                "INSERT OR IGNORE INTO notification_deliveries "
                "(dedupe_key, status, created_at, updated_at) VALUES (?, 'sending', ?, ?)",
                (dedupe_key, now, now),
            )
            connection.commit()
            return cursor.rowcount == 1
        finally:
            connection.close()

    def finish(self, dedupe_key: str, status: str, message_id: Optional[int] = None) -> None:
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        connection = sqlite3.connect(str(self.database_path), timeout=10.0)
        try:
            connection.execute(
                "UPDATE notification_deliveries SET status = ?, updated_at = ?, message_id = ? "
                "WHERE dedupe_key = ?",
                (status, now, message_id, dedupe_key),
            )
            connection.commit()
        finally:
            connection.close()


class TelegramNotifier:
    """Send plain-text messages through Telegram's HTTPS Bot API."""

    def __init__(
        self,
        config: TelegramConfig,
        database_path: Path,
        client: Optional[httpx.Client] = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._config = config
        self._ledger = DeliveryLedger(database_path)
        self._client = client or httpx.Client(timeout=httpx.Timeout(config.timeout_seconds))
        self._owns_client = client is None
        self._sleep = sleeper

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def send_message(self, text: str, dedupe_key: str) -> DeliveryResult:
        if not text.strip():
            raise ValueError("message text cannot be empty")
        if len(text) > 4096:
            raise ValueError("Telegram message exceeds the 4096-character limit")
        if not self._ledger.claim(dedupe_key):
            return DeliveryResult(sent=False, duplicate=True)

        # The Bot API requires the token in the URL path. Never expose this URL,
        # request, raw exception or Telegram's error description in logs/errors.
        endpoint = "%s/bot%s/sendMessage" % (BOT_API, self._config.bot_token)
        payload = {"chat_id": self._config.chat_id, "text": text}
        for attempt in range(self._config.max_attempts):
            try:
                response = self._client.post(endpoint, json=payload)
            except httpx.ConnectError as exc:
                if attempt + 1 < self._config.max_attempts:
                    LOGGER.warning("Telegram connection failed; retrying (%d/%d)", attempt + 1, self._config.max_attempts)
                    self._sleep(min(0.5 * (2 ** attempt), 2.0))
                    continue
                self._ledger.finish(dedupe_key, "failed")
                raise TelegramDeliveryError("Could not connect to Telegram Bot API") from None
            except httpx.TimeoutException:
                # A timed-out send may already have been accepted; suppress future
                # retries for this key to avoid knowingly producing a duplicate.
                self._ledger.finish(dedupe_key, "unknown")
                raise TelegramDeliveryError("Telegram send timed out; delivery status is unknown") from None
            except httpx.HTTPError:
                self._ledger.finish(dedupe_key, "unknown")
                raise TelegramDeliveryError("Telegram request failed; delivery status is unknown") from None

            try:
                data = response.json()
            except (ValueError, json.JSONDecodeError):
                self._ledger.finish(dedupe_key, "unknown")
                raise TelegramDeliveryError("Telegram returned an invalid response; delivery status is unknown") from None

            if response.is_success and isinstance(data, dict) and data.get("ok") is True:
                result = data.get("result") or {}
                message = result.get("message_id") if isinstance(result, dict) else None
                self._ledger.finish(dedupe_key, "sent", message if isinstance(message, int) else None)
                return DeliveryResult(sent=True, message_id=message if isinstance(message, int) else None)

            error_code = data.get("error_code") if isinstance(data, dict) else None
            parameters = data.get("parameters") if isinstance(data, dict) else None
            retry_after = parameters.get("retry_after") if isinstance(parameters, dict) else None
            if error_code == 429 and isinstance(retry_after, (int, float)) and attempt + 1 < self._config.max_attempts:
                # Telegram explicitly rejected the request as rate limited.
                self._sleep(max(0.0, min(float(retry_after), 5.0)))
                continue

            self._ledger.finish(dedupe_key, "failed")
            LOGGER.warning("Telegram Bot API rejected a message (HTTP %d)", response.status_code)
            raise TelegramDeliveryError("Telegram Bot API rejected the message")

        self._ledger.finish(dedupe_key, "failed")
        raise TelegramDeliveryError("Telegram delivery failed")


def main() -> int:
    parser = argparse.ArgumentParser(description="Send an explicit Telegram test notification")
    parser.add_argument(
        "--test-notification", action="store_true",
        help="send one test message to TELEGRAM_CHAT_ID",
    )
    args = parser.parse_args()
    if not args.test_notification:
        parser.error("explicit --test-notification is required")
    try:
        config = TelegramConfig.from_env()
        database_path = Path(os.getenv("DATABASE_PATH", "data/nft_hunter.sqlite3"))
        notifier = TelegramNotifier(config, database_path)
        try:
            result = notifier.send_message(
                "Binance NFT Hunter Telegram test. No NFT discovery or transaction was performed.",
                "telegram-test-" + uuid4().hex,
            )
        finally:
            notifier.close()
        print("Telegram test notification sent (message ID %s)." % result.message_id)
        return 0
    except TelegramDeliveryError as exc:
        print("Telegram test notification failed: %s" % exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
