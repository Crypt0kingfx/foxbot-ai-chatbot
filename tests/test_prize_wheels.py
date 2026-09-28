"""Tests for Prize Wheels v1 (services/prize_wheels.py + the app.py glue):
the Pushup (exercise) Wheel and the Sub Prize Wheel.

Groups:
  - SegmentValidationTestCase / WeightedPickTestCase / ResultLineTestCase:
    NO DATABASE_URL required. Pure checks on segment validation, the
    weighted CSPRNG draw, and the chat lines.
  - WheelServiceIntegrationTestCase: real Postgres. Queue/resolve/dedupe/
    fulfill/cancel mechanics, including "resolve twice pays once".
  - WheelTriggerIntegrationTestCase: real Postgres. The polling-loop hook
    driven by the REAL captured Blaze payloads in
    tests/fixtures/real_auto_event_captures.json through the actual
    _foxbot_process_channel_rows_v1 loop, plus FoxCoin payout, respin
    chaining and the chain cap.
  - WheelRoutesIntegrationTestCase: real Postgres, real signed session
    cookies through the real Layer-1 gate -- same discipline as
    tests/test_tts_overlay.py's isolation tests.

Run with:
    python -m unittest tests.test_prize_wheels -v
"""

import copy
import json
import os
import sys
import time
import unittest
import uuid
from datetime import datetime, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app  # noqa: E402
import services.casino_rng as casino_rng  # noqa: E402
import services.prize_wheels as wheels  # noqa: E402

from tests.db_guard import DATABASE_CONFIGURED, SKIP_REASON  # noqa: E402

_FIXTURES_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "real_auto_event_captures.json")
with open(_FIXTURES_PATH, encoding="utf-8") as _fixtures_file:
    _REAL_CAPTURES = json.load(_fixtures_file)

VOTE_ROW_10 = "acaeb2ad-ba16-414d-8871-548e0170c672"      # piweb, 10 votes
SUB_ROW = "24d34345-134b-41c0-8d84-bcc0319fbada"          # DynastyKingD subscribed
GIFT_SENT_ROW = "36c08261-8998-4036-bb6e-360f6081d1c3"    # marioscy5 gift_sent (structural)
GIFT_TEXT_ROW = "876ef2b7-91ab-4f42-8135-9f5752d8659e"    # CacheBot [GIFTED SUB] text -- must NOT spin


def _fresh_row(raw_item_id, **action_overrides):
    for capture in _REAL_CAPTURES:
        raw_item = capture.get("raw_item") or {}
        if raw_item.get("id") == raw_item_id:
            row = copy.deepcopy(raw_item)
            row["id"] = f"{raw_item_id}-{uuid.uuid4().hex[:8]}"
            now = datetime.now(timezone.utc)
            row["createdAt"] = now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"
            if action_overrides:
                row.setdefault("actionInfo", {}).update(action_overrides)
            return row
    raise AssertionError(f"no real capture with id {raw_item_id!r}")


class _FixedRoll(casino_rng.RNGProvider):
    def __init__(self, value):
        self.value = value

    def roll(self, minimum, maximum):
        return max(minimum, min(maximum, self.value))

    def choice(self, seq):
        return seq[0]


def _segments(*specs):
    return [{"label": label, "type": prize_type, "amount": amount, "weight": weight}
            for label, prize_type, amount, weight in specs]


class SegmentValidationTestCase(unittest.TestCase):
    def test_defaults_are_valid(self):
        for wheel in wheels.WHEELS:
            cleaned = wheels.validate_segments(wheel, wheels.default_segments(wheel))
            self.assertEqual(len(cleaned), len(wheels.DEFAULT_SEGMENTS[wheel]))

    def test_exercise_wheel_rejects_exercise_spin(self):
        with self.assertRaises(ValueError):
            wheels.validate_segments("exercise", _segments(("a", "exercise_spin", 0, 1), ("b", "task", 0, 1)))

    def test_foxcoin_amount_bounds(self):
        with self.assertRaises(ValueError):
            wheels.validate_segments("sub", _segments(("a", "foxcoins", 0, 1), ("b", "task", 0, 1)))
        with self.assertRaises(ValueError):
            wheels.validate_segments("sub", _segments(("a", "foxcoins", 10 ** 9, 1), ("b", "task", 0, 1)))

    def test_segment_count_bounds(self):
        with self.assertRaises(ValueError):
            wheels.validate_segments("sub", _segments(("only", "task", 0, 1)))
        too_many = _segments(*[(f"s{i}", "task", 0, 1) for i in range(wheels.MAX_SEGMENTS + 1)])
        with self.assertRaises(ValueError):
            wheels.validate_segments("sub", too_many)

    def test_all_zero_weight_rejected(self):
        with self.assertRaises(ValueError):
            wheels.validate_segments("sub", _segments(("a", "task", 0, 0), ("b", "task", 0, 0)))

    def test_label_is_cleaned_and_capped(self):
        cleaned = wheels.validate_segments(
            "sub", _segments(("  hi\x00\x07there" + "x" * 80, "task", 0, 1), ("b", "task", 0, 1)))
        self.assertNotIn("\x00", cleaned[0]["label"])
        self.assertLessEqual(len(cleaned[0]["label"]), wheels.MAX_LABEL_CHARS)

    def test_duplicate_ids_are_made_unique(self):
        cleaned = wheels.validate_segments("sub", _segments(("Same", "task", 0, 1), ("Same", "task", 0, 1)))
        self.assertNotEqual(cleaned[0]["id"], cleaned[1]["id"])

    def test_non_foxcoin_amount_is_zeroed(self):
        cleaned = wheels.validate_segments("sub", _segments(("a", "task", 999, 1), ("b", "task", 0, 1)))
        self.assertEqual(cleaned[0]["amount"], 0)


class WeightedPickTestCase(unittest.TestCase):
    def tearDown(self):
        casino_rng.set_provider(casino_rng.SecureRandomProvider())

    def test_ticket_boundaries_map_to_the_right_segment(self):
        segs = _segments(("a", "task", 0, 3), ("zero", "task", 0, 0), ("b", "task", 0, 2))
        for ticket, expected in ((1, 0), (3, 0), (4, 2), (5, 2)):
            casino_rng.set_provider(_FixedRoll(ticket))
            self.assertEqual(wheels.pick_segment_index(segs), expected, f"ticket {ticket}")

    def test_zero_weight_segment_is_never_picked(self):
        segs = _segments(("a", "task", 0, 1), ("never", "task", 0, 0), ("b", "task", 0, 1))
        picks = {wheels.pick_segment_index(segs) for _ in range(2000)}
        self.assertNotIn(1, picks)
        self.assertEqual(picks, {0, 2})


class ResultLineTestCase(unittest.TestCase):
    def _spin(self, wheel, prize_type, label="X", amount=0):
        return {"id": 1, "wheel": wheel, "wheel_title": wheels.WHEEL_TITLES[wheel], "viewer": "bob",
                "prize": {"label": label, "type": prize_type, "amount": amount}}

    def test_foxcoin_line_only_claims_a_balance_when_credited(self):
        credited = app._foxbot_wheel_result_line_v1("streamer", self._spin("sub", "foxcoins", "500 FoxCoins", 500),
                                                    {"credited": True, "balance": 1500})
        self.assertIn("1,500", credited)
        not_credited = app._foxbot_wheel_result_line_v1("streamer", self._spin("sub", "foxcoins", "500 FoxCoins", 500),
                                                        {"credited": False})
        self.assertNotIn("Balance", not_credited)

    def test_exercise_task_line_calls_out_the_streamer(self):
        line = app._foxbot_wheel_result_line_v1("crypt0k1ng96", self._spin("exercise", "task", "20 Pushups"), {})
        self.assertIn("20 Pushups", line)
        self.assertIn("@crypt0k1ng96", line)
        self.assertIn("@bob", line)

    def test_capped_chain_says_so(self):
        line = app._foxbot_wheel_result_line_v1("s", self._spin("sub", "respin", "Spin Again!"), {"chain_capped": True})
        self.assertIn("limit", line)

    def test_queued_line_mentions_vote_amount(self):
        spin = {"viewer": "bob", "wheel_title": "Pushup Wheel", "trigger_kind": "vote", "trigger_amount": 75}
        self.assertIn("75 votes", app._foxbot_wheel_queued_line_v1("s", spin, False))


def _cleanup_handle(handle):
    with wheels._connect() as connection:
        wheels._ensure_schema(connection)
        connection.execute(f"DELETE FROM {wheels.TABLE_SPINS} WHERE creator_handle = %s", (handle,))
        connection.execute(f"DELETE FROM {wheels.TABLE_CONFIG} WHERE creator_handle = %s", (handle,))


@unittest.skipUnless(DATABASE_CONFIGURED, SKIP_REASON)
class WheelServiceIntegrationTestCase(unittest.TestCase):
    def setUp(self):
        self.handle = f"test-wheel-svc-{uuid.uuid4().hex[:8]}"

    def tearDown(self):
        _cleanup_handle(self.handle)
        casino_rng.set_provider(casino_rng.SecureRandomProvider())

    def test_config_defaults_are_opt_in(self):
        config = wheels.get_config(self.handle)
        self.assertFalse(config.exercise_enabled)
        self.assertFalse(config.sub_enabled)
        self.assertEqual(config.vote_threshold, 50)

    def test_partial_config_update_keeps_other_fields(self):
        wheels.set_config(self.handle, exercise_enabled=True, vote_threshold=80)
        config = wheels.set_config(self.handle, sub_enabled=True)
        self.assertTrue(config.exercise_enabled)
        self.assertEqual(config.vote_threshold, 80)

    def test_custom_segments_round_trip_and_reset(self):
        custom = _segments(("Plank", "task", 0, 1), ("Squat", "task", 0, 1))
        config = wheels.set_config(self.handle, exercise_segments=custom)
        self.assertEqual([s["label"] for s in config.segments["exercise"]], ["Plank", "Squat"])
        config = wheels.set_config(self.handle, reset_segments="exercise")
        self.assertEqual(len(config.segments["exercise"]), len(wheels.DEFAULT_SEGMENTS["exercise"]))

    def test_dedupe_key_queues_once(self):
        first = wheels.queue_spin(self.handle, "sub", "bob", trigger_kind="sub", dedupe_key="k")
        second = wheels.queue_spin(self.handle, "sub", "bob", trigger_kind="sub", dedupe_key="k")
        self.assertIsNotNone(first)
        self.assertIsNone(second)

    def test_resolve_twice_returns_same_result_replayed(self):
        spin = wheels.queue_spin(self.handle, "sub", "bob", trigger_kind="sub", dedupe_key="k")
        first = wheels.resolve_spin(self.handle, spin["id"])
        second = wheels.resolve_spin(self.handle, spin["id"])
        self.assertFalse(first["replayed"])
        self.assertTrue(second["replayed"])
        self.assertEqual(first["segment_index"], second["segment_index"])
        self.assertEqual(first["prize"], second["prize"])

    def test_resolved_prize_matches_snapshot_index(self):
        spin = wheels.queue_spin(self.handle, "exercise", "bob", trigger_kind="vote", dedupe_key="k")
        result = wheels.resolve_spin(self.handle, spin["id"])
        self.assertEqual(result["segments"][result["segment_index"]]["label"], result["prize"]["label"])

    def test_task_is_owed_until_marked_done(self):
        wheels.set_config(self.handle, exercise_segments=_segments(("Plank", "task", 0, 1), ("Nope", "task", 0, 0)))
        spin = wheels.queue_spin(self.handle, "exercise", "bob", trigger_kind="vote", dedupe_key="k")
        result = wheels.resolve_spin(self.handle, spin["id"])
        self.assertEqual(result["fulfillment"], wheels.FULFILL_OWED)
        done = wheels.set_fulfilled(self.handle, spin["id"])
        self.assertEqual(done["fulfillment"], wheels.FULFILL_DONE)
        self.assertEqual(wheels.list_spins(self.handle, fulfillment=wheels.FULFILL_OWED), [])

    def test_cancelled_spin_cannot_be_resolved(self):
        spin = wheels.queue_spin(self.handle, "sub", "bob", trigger_kind="sub", dedupe_key="k")
        self.assertIsNotNone(wheels.cancel_spin(self.handle, spin["id"]))
        self.assertIsNone(wheels.resolve_spin(self.handle, spin["id"]))

    def test_other_creator_cannot_resolve_my_spin(self):
        spin = wheels.queue_spin(self.handle, "sub", "bob", trigger_kind="sub", dedupe_key="k")
        other = f"test-wheel-other-{uuid.uuid4().hex[:8]}"
        try:
            self.assertIsNone(wheels.resolve_spin(other, spin["id"]))
        finally:
            _cleanup_handle(other)

    def test_latest_spun_only_returns_recent(self):
        spin = wheels.queue_spin(self.handle, "sub", "bob", trigger_kind="sub", dedupe_key="k")
        wheels.resolve_spin(self.handle, spin["id"])
        self.assertEqual([s["id"] for s in wheels.latest_spun(self.handle)], [spin["id"]])
        with wheels._connect() as connection:
            connection.execute(
                f"UPDATE {wheels.TABLE_SPINS} SET spun_at = NOW() - INTERVAL '1 hour' WHERE id = %s", (spin["id"],))
        self.assertEqual(wheels.latest_spun(self.handle), [])


@unittest.skipUnless(DATABASE_CONFIGURED, SKIP_REASON)
class WheelTriggerIntegrationTestCase(unittest.TestCase):
    def setUp(self):
        self.handle = f"test-wheel-trg-{uuid.uuid4().hex[:8]}"
        self.channel_id = f"test-wheel-chan-{uuid.uuid4().hex[:8]}"
        self.target = {"channel_id": self.channel_id, "channel_slug": self.handle, "handle": self.handle,
                       "is_subscription_channel": False}
        app._FOXBOT_MULTICHANNEL_INITIALIZED_V1.discard(self.channel_id)
        app.processed_polling_messages.clear()
        app.automation_recent_events.clear()
        app.auto_chat_event_seen.clear()

        self.patches = [
            mock.patch.object(app, "_foxbot_tts_emit_chat_message_v1"),
            mock.patch.object(app, "send_blaze_chat_message", return_value={"success": True}),
            mock.patch.object(app, "chat", return_value={"response": ""}),
            mock.patch.object(app, "save_persistent_data", return_value=True),
            mock.patch.object(app, "_foxbot_wheel_schedule_auto_spin_v1"),
        ]
        mocks = [p.start() for p in self.patches]
        self.mock_send = mocks[1]
        self.mock_schedule = mocks[4]
        self.creator_id = f"test-wheel-cid-{uuid.uuid4().hex[:8]}"

    def tearDown(self):
        for p in self.patches:
            p.stop()
        _cleanup_handle(self.handle)
        app.foxcoin_economy["by_creator"].pop(self.creator_id, None)
        app._FOXBOT_MULTICHANNEL_INITIALIZED_V1.discard(self.channel_id)
        app.processed_polling_messages.clear()
        app.automation_recent_events.clear()
        app.auto_chat_event_seen.clear()
        casino_rng.set_provider(casino_rng.SecureRandomProvider())

    def _run(self, *rows):
        app._foxbot_process_channel_rows_v1(self.target, list(rows), resolved_creator_id=self.creator_id)

    def _queued(self):
        return wheels.list_spins(self.handle, status=wheels.STATUS_QUEUED, oldest_first=True)

    def test_nothing_happens_while_wheels_are_off(self):
        self._run(_fresh_row(VOTE_ROW_10, amount=500), _fresh_row(SUB_ROW))
        self.assertEqual(self._queued(), [])

    def test_vote_threshold(self):
        wheels.set_config(self.handle, exercise_enabled=True, vote_threshold=50)
        self._run(_fresh_row(VOTE_ROW_10, amount=49))
        self.assertEqual(self._queued(), [])
        self._run(_fresh_row(VOTE_ROW_10, amount=50))
        queued = self._queued()
        self.assertEqual(len(queued), 1)
        self.assertEqual(queued[0]["wheel"], "exercise")
        self.assertEqual(queued[0]["viewer"], "piweb")
        self.assertEqual(queued[0]["trigger_amount"], 50)

    def test_votes_do_not_spin_the_sub_wheel(self):
        wheels.set_config(self.handle, sub_enabled=True)
        self._run(_fresh_row(VOTE_ROW_10, amount=500))
        self.assertEqual(self._queued(), [])

    def test_sub_and_structural_gift_each_queue_one_sub_spin(self):
        wheels.set_config(self.handle, sub_enabled=True)
        self._run(_fresh_row(SUB_ROW), _fresh_row(GIFT_SENT_ROW))
        queued = self._queued()
        self.assertEqual(sorted((s["wheel"], s["trigger_kind"], s["viewer"]) for s in queued),
                         [("sub", "giftsub", "marioscy5"), ("sub", "sub", "DynastyKingD")])

    def test_cachebot_gift_text_row_does_not_double_spin(self):
        wheels.set_config(self.handle, sub_enabled=True)
        self._run(_fresh_row(GIFT_SENT_ROW), _fresh_row(GIFT_TEXT_ROW))
        self.assertEqual(len(self._queued()), 1)

    def test_same_row_seen_twice_queues_once(self):
        wheels.set_config(self.handle, sub_enabled=True)
        row = _fresh_row(SUB_ROW)
        self._run(row)
        app.processed_polling_messages.clear()  # simulate a restart re-reading chat
        self._run(copy.deepcopy(row))
        self.assertEqual(len(self._queued()), 1)

    def test_reply_cooldown_does_not_eat_a_second_big_vote(self):
        wheels.set_config(self.handle, exercise_enabled=True, vote_threshold=50)
        self._run(_fresh_row(VOTE_ROW_10, amount=60))
        self._run(_fresh_row(VOTE_ROW_10, amount=70))
        self.assertEqual([s["trigger_amount"] for s in self._queued()], [60, 70])

    def test_queue_announces_and_auto_spin_schedules(self):
        wheels.set_config(self.handle, sub_enabled=True, auto_spin=True)
        self._run(_fresh_row(SUB_ROW))
        texts = [c.args[0] for c in self.mock_send.call_args_list]
        self.assertTrue(any("Sub Prize Wheel" in t and "Spinning now" in t for t in texts), texts)
        self.mock_schedule.assert_called_once()

    def test_scoped_creator_without_a_channel_never_announces_into_the_owner_channel(self):
        self._force("sub", ("500 FoxCoins", "foxcoins", 500, 1), ("never", "task", 0, 0))
        spin = wheels.queue_spin(self.handle, "sub", "bob", trigger_kind="manual", dedupe_key="no-channel",
                                 creator_id=self.creator_id)
        app._foxbot_wheel_run_spin_v1(self.handle, spin["id"], reveal_delay=0)
        self.mock_send.assert_not_called()

    def test_trigger_failure_never_breaks_the_loop(self):
        wheels.set_config(self.handle, sub_enabled=True)
        with mock.patch.object(app._foxbot_wheels_v1, "queue_spin", side_effect=RuntimeError("db down")):
            self._run(_fresh_row(SUB_ROW))  # must not raise
        self.assertIsNotNone(app.polling_status.get("last_auto_event"))

    # -- resolving / fulfilment --

    def _force(self, wheel, *specs):
        wheels.set_config(self.handle, **{f"{wheel}_segments": _segments(*specs)})

    def _queue(self, wheel, viewer="bob", key=None):
        return wheels.queue_spin(self.handle, wheel, viewer, trigger_kind="sub", dedupe_key=key or uuid.uuid4().hex,
                                 channel_id=self.channel_id, creator_id=self.creator_id)

    def test_foxcoin_prize_credits_exactly_once(self):
        self._force("sub", ("500 FoxCoins", "foxcoins", 500, 1), ("never", "task", 0, 0))
        spin = self._queue("sub")
        first = app._foxbot_wheel_run_spin_v1(self.handle, spin["id"], reveal_delay=0)
        second = app._foxbot_wheel_run_spin_v1(self.handle, spin["id"], reveal_delay=0)
        self.assertTrue(first["outcome"]["credited"])
        self.assertTrue(second["replayed"])
        balance = app._creator_economy_v1(self.creator_id)["balances"].get("bob")
        self.assertEqual(balance, 500)
        texts = [c.args[0] for c in self.mock_send.call_args_list]
        self.assertEqual(sum("500 FoxCoins" in t for t in texts), 1, texts)

    def test_unattributed_foxcoin_prize_is_owed_not_minted(self):
        self._force("sub", ("500 FoxCoins", "foxcoins", 500, 1), ("never", "task", 0, 0))
        spin = self._queue("sub", viewer="viewer")
        result = app._foxbot_wheel_run_spin_v1(self.handle, spin["id"], reveal_delay=0)
        self.assertFalse(result["outcome"]["credited"])
        self.assertNotIn("viewer", app._creator_economy_v1(self.creator_id)["balances"])
        owed = wheels.list_spins(self.handle, fulfillment=wheels.FULFILL_OWED)
        self.assertEqual([s["id"] for s in owed], [spin["id"]])

    def test_exercise_spin_prize_queues_an_exercise_spin(self):
        self._force("sub", ("Pushup Wheel", "exercise_spin", 0, 1), ("never", "task", 0, 0))
        spin = self._queue("sub")
        result = app._foxbot_wheel_run_spin_v1(self.handle, spin["id"], reveal_delay=0)
        child = wheels.get_spin(self.handle, result["outcome"]["child_spin_id"])
        self.assertEqual(child["wheel"], "exercise")
        self.assertEqual(child["parent_spin_id"], spin["id"])
        self.assertEqual(child["status"], wheels.STATUS_QUEUED)

    def test_respin_chain_is_capped(self):
        self._force("sub", ("Spin Again!", "respin", 0, 1), ("never", "task", 0, 0))
        spin = self._queue("sub")
        spun = 0
        next_id = spin["id"]
        while next_id is not None and spun < 10:
            result = app._foxbot_wheel_run_spin_v1(self.handle, next_id, reveal_delay=0)
            spun += 1
            next_id = result["outcome"].get("child_spin_id")
        self.assertEqual(spun, wheels.MAX_CHAIN_DEPTH + 1)
        self.assertTrue(result["outcome"]["chain_capped"])

    def test_wheel_help_command(self):
        wheels.set_config(self.handle, exercise_enabled=True, vote_threshold=75)
        line = app._foxbot_wheel_help_line_v1(self.handle)
        self.assertIn("75+ votes", line)
        self.assertNotIn("Sub Prize Wheel", line)


@unittest.skipUnless(DATABASE_CONFIGURED, SKIP_REASON)
class WheelRoutesIntegrationTestCase(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient

        self.client = TestClient(app.app)
        self.admin_blaze_id = f"test-wheel-admin-{uuid.uuid4().hex[:10]}"
        self.creator_b_id = f"test-wheel-b-{uuid.uuid4().hex[:10]}"
        self.handle_a = f"test-wheel-owner-{uuid.uuid4().hex[:8]}"
        self.handle_b = f"test-wheel-b-{uuid.uuid4().hex[:8]}"

        self._env = {}
        for key, value in {
            "STUDIO_SESSION_SECRET": "test-secret-do-not-use-in-prod",
            "STUDIO_APPROVED_BLAZE_USER_IDS": ",".join([self.admin_blaze_id, self.creator_b_id]),
            "STUDIO_AUTH_MODE": "both",
            "STUDIO_ADMIN_USER": "test-admin",
            "STUDIO_ADMIN_PASSWORD": "test-password-not-real",
        }.items():
            self._env[key] = os.environ.get(key)
            os.environ[key] = value

        handle_map = {self.creator_b_id: self.handle_b}

        def _fake_handle(blaze_id):
            if not blaze_id or blaze_id == self.admin_blaze_id:
                return self.handle_a
            return handle_map.get(blaze_id, "")

        self.patches = [
            mock.patch.object(app, "_tenant_zero_id", return_value=self.admin_blaze_id),
            mock.patch.object(app, "_foxbot_resolve_event_handle_v1", side_effect=_fake_handle),
            mock.patch.object(app, "send_blaze_chat_message", return_value={"success": True}),
            mock.patch.object(app, "save_persistent_data", return_value=True),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        _cleanup_handle(self.handle_a)
        _cleanup_handle(self.handle_b)

    def _cookies(self, blaze_id):
        return {"foxbot_dashboard_session": app._foxbot_dashboard_session_sign_v1(blaze_id, "x")}

    def test_unauthenticated_is_rejected(self):
        self.assertEqual(self.client.get("/api/studio/wheels").status_code, 401)
        self.assertEqual(self.client.post("/api/studio/wheels/spin-next").status_code, 401)

    def test_full_flow_add_spin_overlay(self):
        cookies = self._cookies(self.admin_blaze_id)
        res = self.client.post("/api/studio/wheels/config", json={"sub_enabled": True}, cookies=cookies)
        self.assertEqual(res.status_code, 200, res.text)
        self.assertTrue(res.json()["config"]["sub_enabled"])

        res = self.client.post("/api/studio/wheels/add", json={"wheel": "sub", "viewer": "@Bob"}, cookies=cookies)
        self.assertEqual(res.status_code, 200, res.text)

        state = self.client.get("/api/studio/wheels", cookies=cookies).json()
        self.assertEqual([s["viewer"] for s in state["queued"]], ["Bob"])

        res = self.client.post("/api/studio/wheels/spin-next", cookies=cookies)
        self.assertEqual(res.status_code, 200, res.text)
        prize_label = res.json()["spin"]["prize"]["label"]

        overlay = self.client.get(f"/overlay/wheel-data?handle={self.handle_a}").json()
        self.assertTrue(overlay["ok"])
        self.assertEqual(len(overlay["spins"]), 1)
        shown = overlay["spins"][0]
        self.assertEqual(shown["prize"]["label"], prize_label)
        self.assertEqual(shown["segments"][shown["segment_index"]]["label"], prize_label)
        # display-only: no weights/amounts/ids leak to the anonymous overlay
        self.assertEqual(set(shown["segments"][0].keys()), {"label", "type"})
        self.assertNotIn("creator_id", shown)
        self.assertNotIn("channel_id", shown)

        self.assertEqual(self.client.post("/api/studio/wheels/spin-next", cookies=cookies).status_code, 404)

    def test_bad_segments_are_rejected(self):
        res = self.client.post("/api/studio/wheels/config", json={"sub_segments": [{"label": "one"}]},
                               cookies=self._cookies(self.admin_blaze_id))
        self.assertEqual(res.status_code, 400)

    def test_bad_viewer_name_rejected(self):
        res = self.client.post("/api/studio/wheels/add", json={"wheel": "sub", "viewer": "<script>"},
                               cookies=self._cookies(self.admin_blaze_id))
        self.assertEqual(res.status_code, 400)

    def test_creator_isolation(self):
        owner = self._cookies(self.admin_blaze_id)
        other = self._cookies(self.creator_b_id)
        self.client.post("/api/studio/wheels/add", json={"wheel": "exercise", "viewer": "bob"}, cookies=owner)
        spin_id = self.client.get("/api/studio/wheels", cookies=owner).json()["queued"][0]["id"]

        self.assertEqual(self.client.get("/api/studio/wheels", cookies=other).json()["queued"], [])
        self.assertEqual(self.client.post(f"/api/studio/wheels/spin/{spin_id}", cookies=other).status_code, 404)
        self.assertEqual(self.client.post(f"/api/studio/wheels/cancel/{spin_id}", cookies=other).status_code, 404)
        self.assertEqual(wheels.get_spin(self.handle_a, spin_id)["status"], wheels.STATUS_QUEUED)

    def test_test_spin_owes_nothing_and_does_not_chat(self):
        cookies = self._cookies(self.admin_blaze_id)
        res = self.client.post("/api/studio/wheels/test", json={"wheel": "exercise"}, cookies=cookies)
        self.assertEqual(res.status_code, 200, res.text)
        state = self.client.get("/api/studio/wheels", cookies=cookies).json()
        self.assertEqual(state["owed"], [])
        app.send_blaze_chat_message.assert_not_called()

    def test_overlay_page_is_public(self):
        res = self.client.get("/overlay/wheel")
        self.assertEqual(res.status_code, 200)
        self.assertIn("FoxBot Prize Wheel Overlay", res.text)


if __name__ == "__main__":
    unittest.main()
