"""Notification mode tests prove DRY_RUN cannot reach Telegram HTTP."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import httpx

from app.notifications.mode import NotificationMode, build_notification_dispatcher
from app.notifications.telegram_bot import TelegramConfig, TelegramNotifier


class NotificationModeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "notify.sqlite3"
        self.requests = []
        self.client = httpx.Client(transport=httpx.MockTransport(
            lambda request: self.requests.append(request) or httpx.Response(
                200, json={"ok": True, "result": {"message_id": 1}}, request=request
            )
        ))
        self.real_http_sender = TelegramNotifier(
            TelegramConfig("synthetic-test-token", "synthetic-test-chat"),
            self.db_path, client=self.client,
        )

    def tearDown(self):
        self.client.close()
        self.tmp.cleanup()

    def test_default_dry_run_never_calls_real_telegram_http_sender(self):
        dispatcher = build_notification_dispatcher(
            self.db_path, env={"TELEGRAM_BOT_TOKEN": "configured", "TELEGRAM_CHAT_ID": "configured"},
            sender=self.real_http_sender,
        )
        result = dispatcher.send_message("notification payload", "key-1")
        self.assertEqual(dispatcher.mode, NotificationMode.DRY_RUN)
        self.assertTrue(result.simulated)
        self.assertFalse(result.sent)
        self.assertEqual(self.requests, [])

    def test_live_mode_without_second_opt_in_is_disabled(self):
        dispatcher = build_notification_dispatcher(
            self.db_path, env={"TELEGRAM_MODE": "LIVE", "TELEGRAM_LIVE_ENABLED": "false"},
            sender=self.real_http_sender,
        )
        result = dispatcher.send_message("notification payload", "key-2")
        self.assertEqual(dispatcher.mode, NotificationMode.DISABLED)
        self.assertFalse(result.sent)
        self.assertEqual(self.requests, [])

    def test_live_requires_both_explicit_flags_and_uses_injected_test_sender(self):
        dispatcher = build_notification_dispatcher(
            self.db_path,
            env={"TELEGRAM_MODE": "LIVE", "TELEGRAM_LIVE_ENABLED": "true"},
            sender=self.real_http_sender,
        )
        result = dispatcher.send_message("fixture only", "key-3")
        self.assertEqual(dispatcher.mode, NotificationMode.LIVE)
        self.assertTrue(result.sent)
        self.assertEqual(len(self.requests), 1)

    def test_disabled_mode_neither_sends_nor_simulates(self):
        dispatcher = build_notification_dispatcher(
            self.db_path, env={"TELEGRAM_MODE": "DISABLED"}, sender=self.real_http_sender
        )
        result = dispatcher.send_message("notification payload", "key-4")
        self.assertEqual(dispatcher.mode, NotificationMode.DISABLED)
        self.assertFalse(result.sent)
        self.assertFalse(result.simulated)
        self.assertEqual(self.requests, [])


if __name__ == "__main__":
    unittest.main()
