"""Fail-closed Telegram notification modes shared by scanners and dashboard."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import logging
import os
from pathlib import Path
from typing import Any, Optional

from app.config import load_env_file
from app.notifications.telegram_bot import TelegramConfig, TelegramNotifier, DeliveryResult

LOGGER = logging.getLogger(__name__)


class NotificationMode(str, Enum):
    LIVE = "LIVE"
    DRY_RUN = "DRY_RUN"
    DISABLED = "DISABLED"

    @classmethod
    def parse(cls, value: Optional[str]) -> "NotificationMode":
        normalized = (value or "DRY_RUN").strip().upper()
        try:
            return cls(normalized)
        except ValueError as exc:
            raise ValueError("TELEGRAM_MODE must be LIVE, DRY_RUN, or DISABLED") from exc


@dataclass(frozen=True)
class NotificationResult:
    sent: bool = False
    duplicate: bool = False
    simulated: bool = False


class NotificationDispatcher:
    """Guard all scanner/dashboard notification sends behind an explicit mode."""

    def __init__(self, mode: NotificationMode | str = NotificationMode.DRY_RUN,
                 sender: Optional[Any] = None, live_authorized: bool = False) -> None:
        requested = NotificationMode.parse(str(mode.value if isinstance(mode, NotificationMode) else mode))
        # LIVE requires both explicit mode and an independent opt-in. If either
        # is absent, force DISABLED even if a sender object was injected.
        if requested is NotificationMode.LIVE and (not live_authorized or sender is None):
            LOGGER.warning("Telegram LIVE mode not fully authorized; delivery is DISABLED")
            requested = NotificationMode.DISABLED
        self.mode = requested
        self._sender = sender if self.mode is NotificationMode.LIVE else None

    def send_message(self, text: str, dedupe_key: str) -> NotificationResult | DeliveryResult:
        if self.mode is NotificationMode.DISABLED:
            return NotificationResult()
        if self.mode is NotificationMode.DRY_RUN:
            LOGGER.info("DRY_RUN suppressed Telegram notification (dedupe_key=%s, characters=%d)",
                        dedupe_key[:120], len(text))
            return NotificationResult(simulated=True)
        # The only path to the HTTP sender is the doubly gated LIVE mode.
        return self._sender.send_message(text, dedupe_key)

    def close(self) -> None:
        if self._sender is not None and hasattr(self._sender, "close"):
            self._sender.close()


def build_notification_dispatcher(database_path: Path, *,
                                  env: Optional[dict[str, str]] = None,
                                  sender: Optional[Any] = None,
                                  mode: Optional[str] = None,
                                  live_enabled: Optional[bool] = None) -> NotificationDispatcher:
    """Build a dispatcher; defaults to DRY_RUN and never reads secrets there."""
    load_env_file(Path(__file__).resolve().parents[2] / ".env")
    source = os.environ if env is None else env
    selected_mode = NotificationMode.parse(mode if mode is not None else source.get("TELEGRAM_MODE", "DRY_RUN"))
    live_authorized = (live_enabled if live_enabled is not None else
                       source.get("TELEGRAM_LIVE_ENABLED", "").strip().lower() in {"1", "true", "yes"})

    if selected_mode is not NotificationMode.LIVE:
        return NotificationDispatcher(selected_mode, sender=sender, live_authorized=False)
    if not live_authorized:
        return NotificationDispatcher(selected_mode, sender=sender, live_authorized=False)
    if sender is None:
        try:
            config = TelegramConfig.from_env()
            sender = TelegramNotifier(config, database_path)
        except Exception as exc:
            LOGGER.warning("Telegram LIVE mode unavailable (%s); delivery is DISABLED", type(exc).__name__)
            return NotificationDispatcher(NotificationMode.DISABLED)
    return NotificationDispatcher(NotificationMode.LIVE, sender=sender, live_authorized=True)
