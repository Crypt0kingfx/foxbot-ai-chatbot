"""Tests for the auto-event attribution fix: parse_auto_chat_event's
structural sub/giftsub gates (_foxbot_item_has_sub_signal_v1, the
type=="text" exclusion from giftsub/sub) and
_foxbot_resolve_auto_event_username_v1's new gift_sent/subscribed/
CacheBot-text branches -- the root cause of the long-standing
"@viewer thank-you" bug.

No DATABASE_URL required: parse_auto_chat_event and
_foxbot_resolve_auto_event_username_v1 are pure functions, no I/O.

Fixtures are the REAL 20 rows captured from
/api/studio/debug/viewer-fallback-captures, already on disk at
tests/fixtures/real_auto_event_captures.json -- looked up by their real
id, not hand-typed, so a test can't quietly drift from what Blaze actually
sent.

Run with:
    python -m unittest tests.test_auto_event_attribution -v
"""

import copy
import json
import os
import sys
import time
import unittest
from datetime import datetime, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app  # noqa: E402

_FIXTURES_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "real_auto_event_captures.json")

with open(_FIXTURES_PATH, encoding="utf-8") as _fixtures_file:
    _REAL_CAPTURES = json.load(_fixtures_file)


def _raw_item_by_id(raw_item_id):
    for capture in _REAL_CAPTURES:
        raw_item = capture.get("raw_item") or {}
        if raw_item.get("id") == raw_item_id:
            return raw_item
    raise AssertionError(f"no real capture with id {raw_item_id!r} in {_FIXTURES_PATH}")


class AutoEventAttributionTestCase(unittest.TestCase):
    """No DATABASE_URL required -- pure checks on parse_auto_chat_event and
    _foxbot_resolve_auto_event_username_v1, driven by the real captured
    payloads exactly as the per-row polling loop drives them
    (_foxbot_process_channel_rows_v1: find_chat_message_text -> resolve
    username -> parse_auto_chat_event)."""

    def _resolve_and_parse(self, raw_item):
        message_text = app.find_chat_message_text(raw_item)
        username = app._foxbot_resolve_auto_event_username_v1(raw_item, message_text)
        return app.parse_auto_chat_event(message_text, username, raw_item)

    def test_dynastykingd_subscribed_row(self):
        raw_item = _raw_item_by_id("24d34345-134b-41c0-8d84-bcc0319fbada")
        event = self._resolve_and_parse(raw_item)
        self.assertIsNotNone(event)
        self.assertEqual(event["event_type"], "sub")
        self.assertEqual(event["username"], "DynastyKingD")
        self.assertEqual(event["amount"], 1)

    def test_makeitmetal_subscribed_row_newcount_3(self):
        raw_item = _raw_item_by_id("e2dda02c-d734-45ab-9fe3-df090915852a")
        event = self._resolve_and_parse(raw_item)
        self.assertIsNotNone(event)
        self.assertEqual(event["event_type"], "sub")
        self.assertEqual(event["username"], "MakeItMetal")
        self.assertEqual(event["amount"], 3)

    def test_text_row_merely_mentioning_subscribed_is_not_misclassified(self):
        """The false positive that exists today: the OLD sub branch matched
        on the bare "subscribed" substring, so ANY chat message mentioning
        it -- not just Blaze's own structural type=="subscribed" row --
        got misclassified as a real sub. No real capture happens to have
        this exact shape (a plain type=="text" chat message that mentions
        "subscribed"), so this fixture is synthesized -- structurally
        identical to the real text rows in real_auto_event_captures.json
        (top-level type=="text" with a "sender" dict) -- with a message
        that would have tripped the old keyword-only check."""
        raw_item = {
            "id": "synthetic-text-mentions-subscribed",
            "type": "text",
            "sender": {"slug": "someviewer", "displayName": "SomeViewer"},
            "message": "hey I just subscribed for 1 month to another streamer lol",
        }
        event = self._resolve_and_parse(raw_item)
        self.assertIsNone(event)

    def test_marioscy5_gift_sent_row(self):
        raw_item = _raw_item_by_id("36c08261-8998-4036-bb6e-360f6081d1c3")
        event = self._resolve_and_parse(raw_item)
        self.assertIsNotNone(event)
        self.assertEqual(event["event_type"], "giftsub")
        self.assertEqual(event["username"], "marioscy5")

    def test_cachebot_gifted_sub_text_row_is_not_double_counted(self):
        """The structural gift_sent row (test_marioscy5_gift_sent_row) is
        authoritative for this exact same gift -- CacheBot's own text
        announcement of it must not independently fire a second giftsub."""
        raw_item = _raw_item_by_id("876ef2b7-91ab-4f42-8135-9f5752d8659e")
        event = self._resolve_and_parse(raw_item)
        self.assertIsNone(event)

    def test_cachebot_new_follower_text_row(self):
        """No structural follow row exists in any real capture, so this
        text announcement is the only source -- and the subject named
        inline (@nashvillelou), not CacheBot itself, must be credited."""
        raw_item = _raw_item_by_id("650a7beb-7a59-4d42-8e1a-bd546382e539")
        event = self._resolve_and_parse(raw_item)
        self.assertIsNotNone(event)
        self.assertEqual(event["event_type"], "follow")
        self.assertEqual(event["username"], "nashvillelou")

    def test_piweb_vote_row_unchanged(self):
        raw_item = _raw_item_by_id("acaeb2ad-ba16-414d-8871-548e0170c672")
        event = self._resolve_and_parse(raw_item)
        self.assertIsNotNone(event)
        self.assertEqual(event["event_type"], "vote")
        self.assertEqual(event["username"], "piweb")
        self.assertEqual(event["amount"], 10)


class RowAuthorHandleTestCase(unittest.TestCase):
    """No DATABASE_URL required -- pure checks on
    _foxbot_row_author_handle_v1: WHO POSTED a row, as opposed to who
    _foxbot_resolve_auto_event_username_v1 may say the row is ABOUT. The
    two must stay independent -- see the BLOCKER this class guards against:
    the bot-handle filter used to test the (rewritable) "about" value, so
    it silently stopped firing for the exact CacheBot rows it exists to
    catch."""

    def test_cachebot_text_row_returns_cachebot(self):
        raw_item = _raw_item_by_id("650a7beb-7a59-4d42-8e1a-bd546382e539")
        self.assertEqual(app._foxbot_row_author_handle_v1(raw_item), "cachebot")

    def test_structural_gift_sent_row_has_no_sender_and_returns_empty(self):
        raw_item = _raw_item_by_id("36c08261-8998-4036-bb6e-360f6081d1c3")
        self.assertNotIn("sender", raw_item)
        self.assertEqual(app._foxbot_row_author_handle_v1(raw_item), "")

    def test_structural_subscribed_row_has_no_sender_and_returns_empty(self):
        raw_item = _raw_item_by_id("24d34345-134b-41c0-8d84-bcc0319fbada")
        self.assertNotIn("sender", raw_item)
        self.assertEqual(app._foxbot_row_author_handle_v1(raw_item), "")

    def test_structural_vote_row_has_no_sender_and_returns_empty(self):
        raw_item = _raw_item_by_id("acaeb2ad-ba16-414d-8871-548e0170c672")
        self.assertNotIn("sender", raw_item)
        self.assertEqual(app._foxbot_row_author_handle_v1(raw_item), "")

    def test_non_dict_item_returns_empty(self):
        self.assertEqual(app._foxbot_row_author_handle_v1(None), "")
        self.assertEqual(app._foxbot_row_author_handle_v1("not a dict"), "")


class OrdinaryMentionAttributionTestCase(unittest.TestCase):
    """No DATABASE_URL required -- an ordinary viewer's text message that
    happens to @-mention someone else must be attributed to its actual
    author, never to the person mentioned. The CacheBot-text extraction
    branch in _foxbot_resolve_auto_event_username_v1 is scoped to
    CacheBot's own sender identity specifically so this can't happen."""

    def test_ordinary_at_mention_attributed_to_its_author(self):
        raw_item = {
            "id": "synthetic-ordinary-mention",
            "type": "text",
            "sender": {"slug": "regularviewer", "displayName": "RegularViewer"},
            "message": "hey @someoneelse check this out",
        }
        username = app._foxbot_resolve_auto_event_username_v1(raw_item, "hey @someoneelse check this out")
        self.assertEqual(username, "RegularViewer")
        self.assertNotEqual(username, "someoneelse")


class AutoEventRowWiringTestCase(unittest.TestCase):
    """No DATABASE_URL required -- drives the real per-row polling loop
    (_foxbot_process_channel_rows_v1) with the real captured payloads,
    proving the BLOCKER fix end to end: a bot-authored row's chat-only
    side effects (TTS readout, command dispatch) are suppressed while
    auto-event parsing still runs unchanged, and a structural row with no
    sender at all is never mistaken for one."""

    def setUp(self):
        self.channel_id = f"test-auto-event-attr-{id(self)}-{time.time()}"
        self.target = {
            "channel_id": self.channel_id,
            "channel_slug": "testcreator",
            "handle": "testcreator",
            "is_subscription_channel": False,
        }
        app._FOXBOT_MULTICHANNEL_INITIALIZED_V1.discard(self.channel_id)
        app.processed_polling_messages.clear()
        app.automation_recent_events.clear()
        app.auto_chat_event_seen.clear()
        # polling_status is a shared module-level dict, not reset per call --
        # a stale "last_auto_event" from an earlier test/request would
        # otherwise leak into this one's assertions.
        app.polling_status["last_auto_event"] = None

        self.tts_patch = mock.patch.object(app, "_foxbot_tts_emit_chat_message_v1")
        self.send_patch = mock.patch.object(app, "send_blaze_chat_message", return_value={"success": True})
        self.chat_patch = mock.patch.object(app, "chat", return_value={"response": ""})
        self.mock_tts = self.tts_patch.start()
        self.mock_send = self.send_patch.start()
        self.mock_chat = self.chat_patch.start()

    def tearDown(self):
        self.tts_patch.stop()
        self.send_patch.stop()
        self.chat_patch.stop()
        app._FOXBOT_MULTICHANNEL_INITIALIZED_V1.discard(self.channel_id)
        app.processed_polling_messages.clear()
        app.automation_recent_events.clear()
        app.auto_chat_event_seen.clear()

    def _row_from_fixture(self, raw_item_id):
        """A deep copy of the real captured raw_item, with createdAt bumped
        to right now -- the real captured timestamps are in the past, and
        _foxbot_process_channel_rows_v1's own first-discovery seeding step
        would otherwise silently absorb them as pre-existing backlog
        instead of running them through the loop this test is proving."""
        raw_item = copy.deepcopy(_raw_item_by_id(raw_item_id))
        now = datetime.now(timezone.utc)
        raw_item["createdAt"] = now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"
        return raw_item

    def test_cachebot_new_follower_row_still_classifies_as_follow_but_skips_tts(self):
        row = self._row_from_fixture("650a7beb-7a59-4d42-8e1a-bd546382e539")
        app._foxbot_process_channel_rows_v1(self.target, [row])

        self.mock_tts.assert_not_called()

        auto_event_result = app.polling_status.get("last_auto_event")
        self.assertIsNotNone(auto_event_result)
        event = auto_event_result.get("event") or {}
        self.assertEqual(event.get("event_type"), "follow")
        self.assertEqual(event.get("username"), "nashvillelou")

    def test_bot_role_sender_suppresses_but_still_classifies_as_follow(self):
        """FIX 1: the sender.roles=="bot" check (_foxbot_sender_has_bot_role_v1)
        used to `continue` past auto-event parsing entirely -- the same trap
        the CacheBot known_bot_handles fix closed. Follows arrive ONLY as a
        bot's text announcement, so this must suppress (TTS + command
        dispatch), not skip the row outright, or follow detection dies
        silently the day Blaze starts populating this field. Fails on the
        old `continue`."""
        row = self._row_from_fixture("650a7beb-7a59-4d42-8e1a-bd546382e539")
        row["sender"]["roles"] = ["bot"]

        app._foxbot_process_channel_rows_v1(self.target, [row])

        self.mock_tts.assert_not_called()
        self.mock_chat.assert_not_called()

        auto_event_result = app.polling_status.get("last_auto_event")
        self.assertIsNotNone(auto_event_result)
        event = auto_event_result.get("event") or {}
        self.assertEqual(event.get("event_type"), "follow")
        self.assertEqual(event.get("username"), "nashvillelou")

    def test_cachebot_gifted_sub_text_row_fires_no_giftsub_and_skips_tts(self):
        row = self._row_from_fixture("876ef2b7-91ab-4f42-8135-9f5752d8659e")
        app._foxbot_process_channel_rows_v1(self.target, [row])

        self.mock_tts.assert_not_called()
        self.mock_chat.assert_not_called()
        self.assertIsNone(app.polling_status.get("last_auto_event"))

    def test_structural_gift_sent_row_still_fires_giftsub_and_is_not_suppressed(self):
        """The BLOCKER's core proof: a structural row has no "sender" dict
        at all, so the author-handle check must fall back to
        clean_username ("marioscy5", not a bot) -- never misfire and
        suppress a real gift the way it would if it read the wrong
        value."""
        row = self._row_from_fixture("36c08261-8998-4036-bb6e-360f6081d1c3")
        self.assertNotIn("sender", row)

        app._foxbot_process_channel_rows_v1(self.target, [row])

        self.mock_tts.assert_called_once()

        auto_event_result = app.polling_status.get("last_auto_event")
        self.assertIsNotNone(auto_event_result)
        event = auto_event_result.get("event") or {}
        self.assertEqual(event.get("event_type"), "giftsub")
        self.assertEqual(event.get("username"), "marioscy5")

    def test_bot_handle_message_with_no_at_mention_still_suppressed(self):
        """Preserves today's behavior for the ordinary case the
        known_bot_handles filter was originally built for: a known bot
        account posting a plain command-shaped message with nothing for
        the CacheBot @-mention extraction to key off of."""
        now = datetime.now(timezone.utc)
        row = {
            "id": "synthetic-bot-command-no-mention",
            "type": "text",
            "sender": {"slug": "scurvybot", "displayName": "ScurvyBot"},
            "message": "!casino",
            "createdAt": now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z",
        }
        app._foxbot_process_channel_rows_v1(self.target, [row])

        self.mock_tts.assert_not_called()
        self.mock_chat.assert_not_called()


if __name__ == "__main__":
    unittest.main()
