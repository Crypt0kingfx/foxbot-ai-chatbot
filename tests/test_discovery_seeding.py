"""
Tests for the time-gated channel-discovery seeding fix in
_foxbot_process_channel_rows_v1 (app.py).

Background: on first discovery of a channel, the poller used to seed every
currently-visible message as "already processed" and return without
dispatching anything, to avoid replaying a backlog of old commands on every
Render redeploy. That swallowed a brand-new creator's very first message
too, since it has no way to tell "old backlog" from "sent right as we
connected". The fix compares each row's real `createdAt` against the
discovery moment (minus a small grace window for clock skew) instead of
seeding everything unconditionally.

No pytest dependency: this repo has no test framework installed, so this
uses stdlib unittest. Run with:
    python -m unittest tests.test_discovery_seeding -v
"""

import os
import sys
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app  # noqa: E402


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def make_row(message_id, text, username="viewer", created_at=None):
    row = {"id": message_id, "text": text, "displayName": username}
    if created_at is not None:
        row["createdAt"] = created_at
    return row


class FindChatMessageCreatedAtTests(unittest.TestCase):
    def test_confirmed_blaze_format_with_milliseconds(self):
        # Exact string confirmed from a live favigal1 row.
        epoch = app.find_chat_message_created_at({"createdAt": "2026-07-25T06:32:05.000Z"})
        expected = datetime(2026, 7, 25, 6, 32, 5, tzinfo=timezone.utc).timestamp()
        self.assertIsNotNone(epoch)
        self.assertAlmostEqual(epoch, expected, places=3)

    def test_without_fractional_seconds(self):
        epoch = app.find_chat_message_created_at({"createdAt": "2026-07-25T06:32:05Z"})
        expected = datetime(2026, 7, 25, 6, 32, 5, tzinfo=timezone.utc).timestamp()
        self.assertAlmostEqual(epoch, expected, places=3)

    def test_microsecond_precision(self):
        epoch = app.find_chat_message_created_at({"createdAt": "2026-07-25T06:32:05.123456Z"})
        expected = datetime(2026, 7, 25, 6, 32, 5, 123456, tzinfo=timezone.utc).timestamp()
        self.assertAlmostEqual(epoch, expected, places=5)

    def test_numeric_epoch_seconds_fallback(self):
        now = time.time()
        epoch = app.find_chat_message_created_at({"createdAt": now})
        self.assertAlmostEqual(epoch, now, delta=1)

    def test_numeric_epoch_milliseconds_fallback(self):
        now = time.time()
        epoch = app.find_chat_message_created_at({"createdAt": now * 1000.0})
        self.assertAlmostEqual(epoch, now, delta=1)

    def test_missing_field_returns_none(self):
        self.assertIsNone(app.find_chat_message_created_at({"id": "1", "text": "!help"}))

    def test_malformed_string_returns_none(self):
        self.assertIsNone(app.find_chat_message_created_at({"createdAt": "not-a-date"}))

    def test_alternate_key_created_at(self):
        epoch = app.find_chat_message_created_at({"created_at": "2026-07-25T06:32:05Z"})
        self.assertIsNotNone(epoch)


class DiscoverySeedingTests(unittest.TestCase):
    def setUp(self):
        self.channel_id = f"test-channel-{id(self)}-{time.time()}"
        self.target = {
            "channel_id": self.channel_id,
            "channel_slug": "testcreator",
            "handle": "testcreator",
            "is_subscription_channel": False,
        }

        self._env_grace = os.environ.pop("FOXBOT_DISCOVERY_GRACE_SECONDS", None)

        app._FOXBOT_MULTICHANNEL_INITIALIZED_V1.discard(self.channel_id)
        app.processed_polling_messages.clear()

        self.chat_patch = mock.patch.object(
            app, "chat", return_value={"response": "FoxBot help: ..."}
        )
        self.send_patch = mock.patch.object(app, "send_blaze_chat_message")
        self.auto_event_patch = mock.patch.object(
            app, "handle_auto_chat_event", return_value=None
        )
        self.emit_event_patch = mock.patch.object(
            app._foxbot_events_v1, "emit_event", return_value=None
        )

        self.mock_chat = self.chat_patch.start()
        self.mock_send = self.send_patch.start()
        self.auto_event_patch.start()
        self.emit_event_patch.start()

    def tearDown(self):
        self.chat_patch.stop()
        self.send_patch.stop()
        self.auto_event_patch.stop()
        self.emit_event_patch.stop()

        app._FOXBOT_MULTICHANNEL_INITIALIZED_V1.discard(self.channel_id)
        app.processed_polling_messages.clear()

        if self._env_grace is not None:
            os.environ["FOXBOT_DISCOVERY_GRACE_SECONDS"] = self._env_grace
        else:
            os.environ.pop("FOXBOT_DISCOVERY_GRACE_SECONDS", None)

    def test_first_contact_message_at_discovery_is_dispatched(self):
        """The exact bug this fixes: a brand-new creator's first !help,
        present at the moment of channel discovery, must not be swallowed."""
        now = datetime.now(timezone.utc)
        rows = [make_row("msg-1", "!help", "newcreator", created_at=iso(now))]

        processed = app._foxbot_process_channel_rows_v1(self.target, rows)

        self.assertEqual(processed, 1)
        self.mock_chat.assert_called_once()
        self.mock_send.assert_called_once()

    def test_backlog_on_redeploy_is_not_replayed(self):
        """Regression guard for the original problem the seeding guard
        exists to prevent: a wall of old messages on first poll after a
        restart must not be replayed."""
        now = datetime.now(timezone.utc)
        rows = [
            make_row("old-1", "!daily", "viewer1", created_at=iso(now - timedelta(minutes=10))),
            make_row("old-2", "!balance", "viewer2", created_at=iso(now - timedelta(minutes=5))),
            make_row("old-3", "!help", "viewer3", created_at=iso(now - timedelta(minutes=1))),
        ]

        processed = app._foxbot_process_channel_rows_v1(self.target, rows)

        self.assertEqual(processed, 0)
        self.mock_chat.assert_not_called()
        self.mock_send.assert_not_called()
        for message_id in ("old-1", "old-2", "old-3"):
            self.assertIn(f"{self.channel_id}:{message_id}", app.processed_polling_messages)

    def test_mixed_backlog_and_first_contact(self):
        """Old messages seeded and skipped; the new one dispatched, in the
        same first poll."""
        now = datetime.now(timezone.utc)
        rows = [
            make_row("old-1", "!daily", "viewer1", created_at=iso(now - timedelta(minutes=10))),
            make_row("new-1", "!help", "newcreator", created_at=iso(now)),
        ]

        processed = app._foxbot_process_channel_rows_v1(self.target, rows)

        self.assertEqual(processed, 1)
        self.mock_chat.assert_called_once_with(
            message="!help", username="newcreator", creator_handle="testcreator"
        )

    def test_missing_timestamp_fails_closed(self):
        """No createdAt at all -> treated as old, never dispatched. Failing
        toward "not sent" is the safe default, not failing toward replay."""
        rows = [make_row("no-ts-1", "!help", "viewer1", created_at=None)]

        processed = app._foxbot_process_channel_rows_v1(self.target, rows)

        self.assertEqual(processed, 0)
        self.mock_chat.assert_not_called()
        self.assertIn(f"{self.channel_id}:no-ts-1", app.processed_polling_messages)

    def test_malformed_timestamp_fails_closed(self):
        rows = [make_row("bad-ts-1", "!help", "viewer1", created_at="garbage")]

        processed = app._foxbot_process_channel_rows_v1(self.target, rows)

        self.assertEqual(processed, 0)
        self.mock_chat.assert_not_called()

    def test_grace_window_covers_clock_skew(self):
        """A message timestamped a few seconds before our recorded discovery
        instant (plausible clock skew / request latency) must still be
        treated as new, not swallowed as backlog."""
        os.environ["FOXBOT_DISCOVERY_GRACE_SECONDS"] = "10"
        now = datetime.now(timezone.utc)
        rows = [make_row("skewed-1", "!help", "viewer1", created_at=iso(now - timedelta(seconds=4)))]

        processed = app._foxbot_process_channel_rows_v1(self.target, rows)

        self.assertEqual(processed, 1)
        self.mock_chat.assert_called_once()

    def test_outside_grace_window_is_still_seeded(self):
        """A message well before discovery, beyond the grace window, is
        genuine backlog and must still be swallowed."""
        os.environ["FOXBOT_DISCOVERY_GRACE_SECONDS"] = "10"
        now = datetime.now(timezone.utc)
        rows = [make_row("old-skew-1", "!help", "viewer1", created_at=iso(now - timedelta(seconds=30)))]

        processed = app._foxbot_process_channel_rows_v1(self.target, rows)

        self.assertEqual(processed, 0)
        self.mock_chat.assert_not_called()

    def test_second_poll_only_dispatches_genuinely_new_messages(self):
        """Sanity check that normal (post-discovery) behavior is unchanged:
        once initialized, dedup is purely by message ID, same as before."""
        now = datetime.now(timezone.utc)
        first_rows = [make_row("m1", "!help", "viewer1", created_at=iso(now))]
        app._foxbot_process_channel_rows_v1(self.target, first_rows)
        self.mock_chat.reset_mock()
        self.mock_send.reset_mock()

        second_rows = first_rows + [make_row("m2", "!daily", "viewer1", created_at=iso(now))]
        processed = app._foxbot_process_channel_rows_v1(self.target, second_rows)

        self.assertEqual(processed, 1)
        self.mock_chat.assert_called_once_with(
            message="!daily", username="viewer1", creator_handle="testcreator"
        )


if __name__ == "__main__":
    unittest.main()
