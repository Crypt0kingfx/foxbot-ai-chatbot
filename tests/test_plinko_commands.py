"""Plinko through the app: chat commands, verified sender identity, bot
blocking, FoxCoins moving exactly once, crash reconciliation, polling-loop
dispatch, the OBS overlay routes and the Studio V2 routes.

Real Postgres (local Docker only -- tests/db_guard.py). FoxCoins use the
real in-memory economy under a throwaway creator_id; save_persistent_data
is stubbed so nothing is written to the shared data store.

Run with:
    python -m unittest tests.test_plinko_commands -v
"""

import asyncio
from datetime import datetime, timezone
import os
import sys
import unittest
import uuid
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.db_guard import DATABASE_CONFIGURED, SKIP_REASON  # noqa: E402
import app  # noqa: E402
from services import plinko  # noqa: E402


def chat_row(display_name, message, slug=None, roles=None, row_id=None):
    """Same shape as a real captured Blaze chat row (see
    tests/fixtures/real_auto_event_captures.json)."""
    return {
        "id": row_id or uuid.uuid4().hex,
        "type": "text",
        "sender": {
            "id": uuid.uuid4().hex, "slug": (slug or display_name).lower(), "displayName": display_name,
            "roles": roles or [], "isOwner": False, "isFollower": True, "isSubscriber": False,
        },
        "message": message,
        "replyTo": None,
        "channelId": "chan-test",
        # Real rows carry createdAt; without it the polling loop's first-poll
        # backlog seeding treats the row as history and never replies.
        "createdAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
    }


@unittest.skipUnless(DATABASE_CONFIGURED, SKIP_REASON)
class PlinkoCommandTestCase(unittest.TestCase):
    def setUp(self):
        self.handle = "plinkocmd_" + uuid.uuid4().hex[:10]
        self.creator_id = "cid-" + self.handle
        plinko.set_config(self.handle, enabled=True, cooldown_seconds=0)
        self._patches = [
            mock.patch.object(app, "save_persistent_data", lambda: None),
            mock.patch.dict(os.environ, {"FOXBOT_PLINKO_DISABLED": "false"}),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        app.foxcoin_economy["by_creator"].pop(self.creator_id, None)

    # ---- helpers
    def fund(self, name, amount):
        app.add_points(name, amount, creator_id=self.creator_id)

    def balance(self, name):
        return int(app._creator_economy_v1(self.creator_id)["balances"].get(app.viewer_key(name), 0))

    def cmd(self, row, key=None):
        command = row["message"].split()[0].lower()
        return app._foxbot_plinko_handle_command_v1(
            command, row, row["message"], self.handle, self.creator_id, key or row["id"],
        )["response"]

    # ---- identity
    def test_player_is_the_verified_sender_never_the_message_text(self):
        self.fund("Alice", 500)
        self.fund("Victim", 500)
        reply = self.cmd(chat_row("Alice", "!plinko @Victim Victim"))
        self.assertIn("@Alice started Plinko", reply)
        self.assertEqual(self.balance("Alice"), 400)
        self.assertEqual(self.balance("Victim"), 500)
        self.assertIsNotNone(plinko.get_open_session(self.handle, "alice"))
        self.assertIsNone(plinko.get_open_session(self.handle, "victim"))

        self.cmd(chat_row("Alice", "!drop for @Victim"))
        self.assertGreater(self.balance("Alice"), 400)
        self.assertEqual(self.balance("Victim"), 500)

    def test_row_without_sender_cannot_play(self):
        row = {"id": "x", "type": "vote", "message": "!plinko", "actionInfo": {"senderDisplayName": "Alice"}}
        self.assertEqual(app._foxbot_plinko_handle_command_v1("!plinko", row, "!plinko", self.handle,
                                                               self.creator_id, "x")["response"], "")
        self.assertEqual(plinko.list_sessions(self.handle), [])

    def test_known_bots_and_bot_role_are_blocked(self):
        for row in (chat_row("CacheBot", "!plinko"), chat_row("FoxBotAI", "!plinko"),
                    chat_row("Sneaky", "!plinko", slug="botger"),
                    chat_row("Helper", "!plinko", roles=["moderator", "bot"])):
            self.fund(row["sender"]["displayName"], 1000)
            self.assertEqual(self.cmd(row), "")
        self.assertEqual(plinko.list_sessions(self.handle), [])

    # ---- coins
    def test_full_session_moves_coins_exactly_as_recorded(self):
        self.fund("Alice", 1000)
        self.cmd(chat_row("Alice", "!plinko"))
        for _ in range(10):
            self.cmd(chat_row("Alice", "!drop"))
        session = plinko.list_sessions(self.handle)[0]
        self.assertEqual(session["status"], plinko.STATUS_CLOSED)
        self.assertEqual(self.balance("Alice"), 1000 - session["cost"] + session["payout_total"])
        self.assertEqual(plinko.uncredited_drops(self.handle), [])

    def test_insufficient_foxcoins_cancels_and_charges_nothing(self):
        self.fund("Poor", 40)
        reply = self.cmd(chat_row("Poor", "!plinko"))
        self.assertIn("costs 100", reply)
        self.assertIn("you have 40", reply)
        self.assertEqual(self.balance("Poor"), 40)
        session = plinko.list_sessions(self.handle)[0]
        self.assertEqual((session["status"], session["close_reason"]), (plinko.STATUS_CANCELLED, "insufficient_foxcoins"))

    def test_replayed_messages_charge_and_pay_once(self):
        self.fund("Alice", 1000)
        play = chat_row("Alice", "!plinko")
        self.cmd(play)
        self.assertEqual(self.cmd(play), "")  # same message processed again
        self.assertEqual(self.balance("Alice"), 900)

        drop = chat_row("Alice", "!drop")
        self.cmd(drop)
        after_one = self.balance("Alice")
        self.assertEqual(self.cmd(drop), "")
        self.assertEqual(self.balance("Alice"), after_one)
        self.assertEqual(plinko.get_open_session(self.handle, "alice")["drops_used"], 1)

    def test_failed_credit_is_recorded_and_reconciled_exactly_once(self):
        self.fund("Alice", 1000)
        self.cmd(chat_row("Alice", "!plinko"))
        with mock.patch.object(app, "credit_foxcoins_idempotent", side_effect=RuntimeError("blob write failed")):
            reply = self.cmd(chat_row("Alice", "!drop"))
        self.assertIn("something went wrong", reply)
        pending = plinko.uncredited_drops(self.handle)
        self.assertEqual(len(pending), 1)
        self.assertEqual(self.balance("Alice"), 900)

        with mock.patch.object(app, "_FOXBOT_PLINKO_RECONCILE_MIN_AGE_SECONDS_V1", 0):
            summary = app._foxbot_plinko_reconcile_v1(self.handle, self.creator_id)
            again = app._foxbot_plinko_reconcile_v1(self.handle, self.creator_id)
        self.assertEqual(summary["credited"], 1)
        self.assertEqual(again["credited"], 0)
        self.assertEqual(self.balance("Alice"), 900 + pending[0]["payout"])

    def test_claimed_session_left_by_a_crash_is_reconciled_once(self):
        self.fund("Alice", 1000)
        session = plinko.claim_session(self.handle, user_key="alice", display_name="Alice",
                                       request_key="crashed", creator_id=self.creator_id)
        with mock.patch.object(app, "_FOXBOT_PLINKO_RECONCILE_MIN_AGE_SECONDS_V1", 0):
            first = app._foxbot_plinko_reconcile_v1(self.handle, self.creator_id)
            second = app._foxbot_plinko_reconcile_v1(self.handle, self.creator_id)
        self.assertEqual((first["activated"], second["activated"]), (1, 0))
        self.assertEqual(self.balance("Alice"), 900)
        self.assertEqual(plinko.get_open_session(self.handle, "alice")["session_id"], session["session_id"])

    def test_idle_session_autoplay_pays_the_viewer(self):
        self.fund("Alice", 1000)
        self.cmd(chat_row("Alice", "!plinko"))
        with plinko._connect() as connection:
            connection.execute(f"UPDATE {plinko.TABLE_SESSIONS} SET last_activity_at = NOW() - INTERVAL '2 hours' "
                               "WHERE creator_handle = %s", (self.handle,))
        summary = app._foxbot_plinko_reconcile_v1(self.handle, self.creator_id)
        self.assertEqual((summary["autoplayed"], summary["credited"]), (10, 10))
        session = plinko.list_sessions(self.handle)[0]
        self.assertEqual(self.balance("Alice"), 900 + session["payout_total"])

    # ---- other commands
    def test_rules_balance_and_guidance(self):
        rules = self.cmd(chat_row("Alice", "!plinkorules"))
        self.assertIn("1 | 8 | 28 | 56 | 70 | 56 | 28 | 8 | 1", rules)
        self.assertLessEqual(len(rules), 480)
        self.fund("Alice", 250)
        self.assertIn("you have 250", self.cmd(chat_row("Alice", "!plinkobal")))
        self.assertIn("no Plinko session yet", self.cmd(chat_row("Alice", "!drop")))
        self.cmd(chat_row("Alice", "!plinko"))
        self.assertIn("already have a Plinko session", self.cmd(chat_row("Alice", "!plinko")))
        self.assertIn("10/10 drops left", self.cmd(chat_row("Alice", "!plinkobal")))

    def test_switches_reach_chat(self):
        self.fund("Alice", 1000)
        plinko.set_config(self.handle, enabled=False)
        self.assertIn("isn't on", self.cmd(chat_row("Alice", "!plinko")))
        plinko.set_config(self.handle, enabled=True, kill_switch=True)
        self.assertIn("paused", self.cmd(chat_row("Alice", "!plinko")))
        with mock.patch.dict(os.environ, {"FOXBOT_PLINKO_DISABLED": "true"}):
            self.assertIn("unavailable", self.cmd(chat_row("Alice", "!plinko")))
        self.assertEqual(self.balance("Alice"), 1000)

    def test_paid_entry_request_moves_nothing(self):
        self.fund("Alice", 1000)
        for flag in ("false", "true"):
            with mock.patch.dict(os.environ, {plinko.PAID_ENTRY_FLAG: flag}):
                reply = self.cmd(chat_row("Alice", "!plinko paid 500"))
            self.assertTrue("free-play" in reply or "not implemented" in reply, reply)
        self.assertEqual(self.balance("Alice"), 1000)
        self.assertEqual(plinko.list_sessions(self.handle), [])

    def test_chat_function_never_plays_plinko(self):
        self.fund("Alice", 1000)
        for command in ("!plinko", "!drop", "!plinkobal", "!plinkorules"):
            self.assertEqual(app.chat(command, username="Alice", creator_handle=self.handle)["response"], "")
        self.assertEqual(self.balance("Alice"), 1000)

    # ---- polling-loop dispatch
    def test_polling_loop_routes_plinko_to_the_verified_handler(self):
        self.fund("Alice", 1000)
        sent = []
        target = {"channel_id": "chan-" + self.handle, "channel_slug": self.handle, "handle": self.handle}
        with mock.patch.object(app, "send_blaze_chat_message", side_effect=lambda text, **kw: sent.append(text) or {"success": True}), \
                mock.patch.object(app, "chat", side_effect=AssertionError("Plinko must not go through chat()")), \
                mock.patch.object(app, "_foxbot_tts_emit_chat_message_v1", lambda *a, **k: None):
            app._foxbot_process_channel_rows_v1(target, [chat_row("Alice", "!plinko")], resolved_creator_id=self.creator_id)
        self.assertEqual(len(sent), 1)
        self.assertIn("@Alice started Plinko", sent[0])
        self.assertEqual(self.balance("Alice"), 900)

    # ---- overlay
    def test_overlay_page_is_display_only_and_obs_safe(self):
        response = asyncio.run(app.foxbot_overlay_plinko_page_v1())
        html = response.body.decode("utf-8")
        self.assertIn("#B026FF", html)
        self.assertIn("#FF007F", html)
        self.assertIn("/overlay/plinko-data", html)
        self.assertNotIn("speechSynthesis", html)
        self.assertNotIn("SpeechSynthesisUtterance", html)
        self.assertNotIn("Math.random", html, "the overlay must never generate outcomes")
        self.assertIn("background: transparent", html)

    def test_overlay_data_exposes_display_fields_only(self):
        self.fund("Alice", 1000)
        start = asyncio.run(app.foxbot_overlay_plinko_data_v1(handle=self.handle, since=-1))
        self.assertEqual((start["ok"], start["drops"], start["latest_id"]), (True, [], 0))
        self.cmd(chat_row("Alice", "!plinko"))
        self.cmd(chat_row("Alice", "!drop"))
        data = asyncio.run(app.foxbot_overlay_plinko_data_v1(handle=self.handle, since=0))
        self.assertEqual(len(data["drops"]), 1)
        drop = data["drops"][0]
        self.assertEqual(set(drop), {"id", "viewer", "slot", "path", "multiplier", "payout", "drop_index", "auto"})
        self.assertEqual(drop["viewer"], "Alice")
        self.assertEqual(sum(drop["path"]), drop["slot"])
        self.assertEqual(data["latest_id"], drop["id"])
        self.assertEqual(len(data["multipliers"]), 9)

    # ---- Studio
    def _studio(self, coro_fn, *args, handle=None):
        request = SimpleNamespace(state=SimpleNamespace(blaze_id="b-test"))
        with mock.patch.object(app, "_foxbot_wheels_session_handle_v1", return_value=handle or self.handle):
            return asyncio.run(coro_fn(*args, request) if args else coro_fn(request))

    def test_studio_panel_switches_feed_and_audit(self):
        self.fund("Alice", 1000)
        self.cmd(chat_row("Alice", "!plinko"))
        self.cmd(chat_row("Alice", "!drop"))

        data = self._studio(app.foxbot_studio_plinko_get_v1)
        self.assertTrue(data["ok"])
        self.assertEqual(len(data["recent_drops"]), 1)
        self.assertEqual(data["sessions"][0]["display_name"], "Alice")
        self.assertEqual(data["rtp_percent"], 94.375)
        self.assertEqual([o["weight"] for o in data["odds"]], [1, 8, 28, 56, 70, 56, 28, 8, 1])
        self.assertEqual(data["paid_entry"]["implemented"], False)
        self.assertEqual(data["overlay_path"], f"/overlay/plinko?handle={self.handle}")

        killed = self._studio(app.foxbot_studio_plinko_config_post_v1, {"kill_switch": True})
        self.assertTrue(killed["config"]["kill_switch"])
        off = self._studio(app.foxbot_studio_plinko_config_post_v1, {"kill_switch": False, "enabled": False})
        self.assertEqual((off["config"]["kill_switch"], off["config"]["enabled"]), (False, False))
        bad = self._studio(app.foxbot_studio_plinko_config_post_v1, {"session_cost": 105})
        self.assertEqual(bad.status_code, 400)

        session_id = data["sessions"][0]["session_id"]
        audit = self._studio(app.foxbot_studio_plinko_session_v1, session_id)
        self.assertEqual([e["event"] for e in audit["events"]][:3], ["claimed", "debited", "drop"])
        other = self._studio(app.foxbot_studio_plinko_session_v1, session_id, handle="plinkocmd_otherchannel")
        self.assertEqual(other.status_code, 404, "a creator can't read another channel's audit log")


if __name__ == "__main__":
    unittest.main()
