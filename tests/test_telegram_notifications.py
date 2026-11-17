"""Telegram tests use a mocked HTTP transport and never contact Telegram."""

import json
import logging
import tempfile
import unittest
from pathlib import Path

import httpx

from app.notifications.telegram_bot import (
    TelegramConfig,
    TelegramDeliveryError,
    TelegramNotifier,
)


class TelegramNotificationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.directory.name) / "notifications.sqlite3"
        self.config = TelegramConfig(bot_token="secret-token-never-log", chat_id="private-chat-id")
        self.requests = []
        self.sleeps = []
        self.clients = []

    def tearDown(self):
        for client in self.clients:
            client.close()
        self.directory.cleanup()

    def notifier(self, handler):
        def record(request):
            self.requests.append(request)
            return handler(request)
        client = httpx.Client(transport=httpx.MockTransport(record))
        self.clients.append(client)
        return TelegramNotifier(self.config, self.database_path, client, self.sleeps.append)

    def test_sends_plain_text_and_records_message_id(self):
        notifier = self.notifier(lambda request: httpx.Response(
            200, json={"ok": True, "result": {"message_id": 73}}, request=request,
        ))
        result = notifier.send_message("Test only", "test-1")
        self.assertTrue(result.sent)
        self.assertEqual(result.message_id, 73)
        self.assertEqual(self.requests[0].url.path, "/botsecret-token-never-log/sendMessage")
        self.assertEqual(json.loads(self.requests[0].content), {"chat_id": "private-chat-id", "text": "Test only"})
        self.assertNotIn("secret-token-never-log", repr(result))

    def test_persistent_duplicate_key_prevents_second_send(self):
        notifier = self.notifier(lambda request: httpx.Response(
            200, json={"ok": True, "result": {"message_id": 5}}, request=request,
        ))
        self.assertTrue(notifier.send_message("one", "same-event").sent)
        duplicate = notifier.send_message("one again", "same-event")
        self.assertTrue(duplicate.duplicate)
        self.assertEqual(len(self.requests), 1)

    def test_connect_error_has_one_bounded_retry_and_sanitized_error(self):
        def handler(request):
            if len(self.requests) == 1:
                raise httpx.ConnectError("contains secret-token-never-log", request=request)
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 9}}, request=request)
        notifier = self.notifier(handler)
        result = notifier.send_message("safe", "connect-retry")
        self.assertTrue(result.sent)
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(len(self.sleeps), 1)
        self.assertNotIn("secret-token-never-log", repr(result))

    def test_timeout_is_not_retried_and_key_is_suppressed(self):
        def handler(request):
            raise httpx.ReadTimeout("secret-token-never-log", request=request)
        notifier = self.notifier(handler)
        with self.assertRaises(TelegramDeliveryError) as caught:
            notifier.send_message("safe", "timeout")
        self.assertNotIn("secret-token-never-log", str(caught.exception))
        duplicate = notifier.send_message("safe", "timeout")
        self.assertTrue(duplicate.duplicate)
        self.assertEqual(len(self.requests), 1)

    def test_telegram_429_retries_only_after_explicit_rate_limit(self):
        def handler(request):
            if len(self.requests) == 1:
                return httpx.Response(429, json={"ok": False, "error_code": 429,
                                                 "parameters": {"retry_after": 2}}, request=request)
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 10}}, request=request)
        notifier = self.notifier(handler)
        self.assertTrue(notifier.send_message("safe", "rate-limited").sent)
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(self.sleeps, [2.0])

    def test_api_error_does_not_leak_server_description_or_token(self):
        notifier = self.notifier(lambda request: httpx.Response(
            401, json={"ok": False, "error_code": 401,
                       "description": "secret-token-never-log invalid"}, request=request,
        ))
        with self.assertLogs("app.notifications.telegram_bot", level=logging.WARNING) as logs:
            with self.assertRaises(TelegramDeliveryError) as caught:
                notifier.send_message("safe", "bad-token")
        combined = str(caught.exception) + " ".join(logs.output)
        self.assertNotIn("secret-token-never-log", combined)

    def test_message_validation_prevents_empty_or_oversized_payloads(self):
        notifier = self.notifier(lambda request: self.fail("should not make a request"))
        with self.assertRaises(ValueError):
            notifier.send_message(" ", "empty")
        with self.assertRaises(ValueError):
            notifier.send_message("x" * 4097, "large")


if __name__ == "__main__":
    unittest.main()
