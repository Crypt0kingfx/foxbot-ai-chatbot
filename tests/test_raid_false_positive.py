"""Tests for the raid keyword false-positive fix: parse_auto_chat_event's
raid branch now gates on _foxbot_item_has_raid_signal_v1 (structural
type + actionInfo) instead of a bare "raid" substring match, and
handle_auto_chat_event refuses to reward a known bot handle.

No DATABASE_URL required: recognition_response is mocked, and
parse_auto_chat_event is a pure function.

Run with:
    python -m unittest tests.test_raid_false_positive -v
"""

import os
import sys
import unittest
import uuid
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app  # noqa: E402


def _text_row(text, handle="someviewer"):
    return {
        "id": str(uuid.uuid4()),
        "type": "text",
        "message": text,
        "sender": {"username": handle, "displayName": handle},
    }


def _structural_raid_row(raider="RaiderPerson"):
    # Hypothetical shape -- no real raid capture exists yet (see
    # _foxbot_item_has_raid_signal_v1's docstring).
    return {
        "id": str(uuid.uuid4()),
        "type": "raid",
        "message": f"{raider} raided the stream with 12 viewers",
        "actionInfo": {"senderDisplayName": raider, "viewerCount": 12},
    }


class RaidKeywordFalsePositiveTestCase(unittest.TestCase):

    def _assert_not_an_event(self, text, username="someviewer"):
        item = _text_row(text, username)
        self.assertIsNone(app.parse_auto_chat_event(text, username, item), text)
        # Also with no item at all (the old keyword path never needed one).
        self.assertIsNone(app.parse_auto_chat_event(text, username, None), text)

    def test_raid_time_is_not_a_raid(self):
        self._assert_not_an_event("raid time")

    def test_welcome_raiders_is_not_a_raid(self):
        self._assert_not_an_event("welcome raiders")

    def test_im_afraid_is_not_a_raid(self):
        self._assert_not_an_event("I'm afraid")

    def test_bang_raid_command_is_not_a_raid(self):
        self._assert_not_an_event("!raid")

    def test_bot_welcome_raiders_row_is_not_a_raid(self):
        self._assert_not_an_event("Welcome raiders!", username="CacheBot")

    def test_structural_raid_row_still_classifies_as_raid(self):
        item = _structural_raid_row("RaiderPerson")
        event = app.parse_auto_chat_event(item["message"], "RaiderPerson", item)
        self.assertIsNotNone(event)
        self.assertEqual(event["event_type"], "raid")
        self.assertEqual(event["username"], "RaiderPerson")

    def test_raid_type_without_action_info_is_not_a_raid(self):
        item = _structural_raid_row()
        del item["actionInfo"]
        self.assertFalse(app._foxbot_item_has_raid_signal_v1(item))

    def test_raid_signal_type_is_case_insensitive(self):
        for item_type in ("RAID", "Raided", "channel.raid", "incoming_raid"):
            item = _structural_raid_row()
            item["type"] = item_type
            self.assertTrue(app._foxbot_item_has_raid_signal_v1(item), item_type)


class KnownBotHandleTestCase(unittest.TestCase):

    def test_known_bot_handles(self):
        for handle in ("CacheBot", "@scurvybot", " FoxBotAI ", "blazeian_bot_ai", "Botger"):
            self.assertTrue(app._foxbot_is_known_bot_handle_v1(handle), handle)

    def test_human_and_empty_handles(self):
        for handle in ("RaiderPerson", "", None, "viewer"):
            self.assertFalse(app._foxbot_is_known_bot_handle_v1(handle), handle)

    def test_profile_handle_env_var_counts_as_bot(self):
        with mock.patch.dict(os.environ, {"FOXBOT_BLAZE_PROFILE_HANDLE": "@MyCustomBot"}):
            self.assertTrue(app._foxbot_is_known_bot_handle_v1("mycustombot"))


class HandleAutoChatEventBotTargetTestCase(unittest.TestCase):

    def test_bot_target_never_calls_recognition_response(self):
        item = _structural_raid_row("CacheBot")
        with mock.patch.object(app, "recognition_response") as recognition:
            result = app.handle_auto_chat_event(
                item["id"], item["message"], "CacheBot", item, creator_id="test-creator"
            )
        self.assertIsNone(result)
        recognition.assert_not_called()

    def test_human_target_still_calls_recognition_response(self):
        """Positive control: the bot guard doesn't swallow real events."""
        item = _structural_raid_row("RaiderPerson")
        with mock.patch.object(app, "recognition_response", return_value="ok") as recognition, \
                mock.patch.object(app, "automation_event_enabled", return_value=True), \
                mock.patch.object(app, "foxbot_automation_allowed", return_value=True):
            result = app.handle_auto_chat_event(
                item["id"], item["message"], "RaiderPerson", item, creator_id="test-creator"
            )
        recognition.assert_called_once()
        self.assertEqual(recognition.call_args.args[:2], ("raid", "RaiderPerson"))
        self.assertTrue(result["ok"])


if __name__ == "__main__":
    unittest.main()
