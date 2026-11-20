"""Tests for Mission Wheel v1 (the third wheel in services/prize_wheels.py
+ its app.py glue, routes and overlay).

Groups:
  - MissionMathTestCase / MissionValidationTestCase / MissionPickTestCase:
    NO DATABASE_URL required. Threshold math, the structural vote check,
    mission validation, and the weighted draw never landing on a disabled
    mission.
  - MissionTriggerIntegrationTestCase: real Postgres. The polling-loop hook
    driven by the REAL captured Blaze vote payloads in
    tests/fixtures/real_auto_event_captures.json through the actual
    _foxbot_process_channel_rows_v1 loop: threshold math end to end, bot
    senders ignored, chat text ignored, dedupe, auto-play runner.
  - MissionServiceIntegrationTestCase: real Postgres. Defaults (on for the
    owner only), disabled missions never drawn, Prize Wheels routes and
    overlay never see mission rows.
  - MissionRoutesIntegrationTestCase: real Postgres, real signed session
    cookies through the real Layer-1 gate. Admin gating on every
    /api/studio/mission-wheel* route; the overlay stays public.

DB tests follow tests/db_guard.py: local Postgres only unless explicitly
overridden. Run with:
    python -m pytest tests/test_mission_wheel.py -v
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

VOTE_ROW_10 = "acaeb2ad-ba16-414d-8871-548e0170c672"      # piweb, 10 votes (structural vote row)
SUB_ROW = "24d34345-134b-41c0-8d84-bcc0319fbada"          # a structural subscribed row


def _real_row(raw_item_id):
    for capture in _REAL_CAPTURES:
        raw_item = capture.get("raw_item") or {}
        if raw_item.get("id") == raw_item_id:
            return copy.deepcopy(raw_item)
    raise AssertionError(f"no real capture with id {raw_item_id!r}")


def _fresh_row(raw_item_id, **action_overrides):
    """A real captured row with a new id + timestamp (so the polling loop
    treats it as new), actionInfo fields optionally overridden."""
    row = _real_row(raw_item_id)
    row["id"] = f"{raw_item_id}-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)
    row["createdAt"] = now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"
    if action_overrides:
        row.setdefault("actionInfo", {}).update(action_overrides)
    return row


def _vote_text_row(amount):
    """A plain chat-TEXT row that reads exactly like a vote announcement but
    has no structural vote marker -- must never earn a mission spin."""
    row = _fresh_row(VOTE_ROW_10)
    row["type"] = "text"
    row.pop("actionInfo", None)
    row["message"] = f"piweb is giving {amount} Votes to crypt0k1ng96."
    row["sender"] = {"username": "piweb", "displayName": "piweb"}
    return row


def _missions(*specs):
    return [{"title": title, "short_label": title[:14], "description": "", "weight": weight, "enabled": enabled}
            for title, weight, enabled in specs]


class _FixedRoll(casino_rng.RNGProvider):
    def __init__(self, value):
        self.value = value

    def roll(self, minimum, maximum):
        return max(minimum, min(maximum, self.value))

    def choice(self, seq):
        return seq[0]


# --------------------------------------------------------------- no database


class MissionMathTestCase(unittest.TestCase):
    def test_threshold_math(self):
        # spins = floor(amount / threshold), capped at max (defaults 30 / 3)
        for amount, expected in ((29, 0), (30, 1), (59, 1), (60, 2), (89, 2), (90, 3), (1000, 3)):
            with self.subTest(amount=amount):
                self.assertEqual(app._foxbot_mission_spin_count_v1(amount, 30, 3), expected)

    def test_cap_follows_the_setting(self):
        self.assertEqual(app._foxbot_mission_spin_count_v1(1000, 30, 5), 5)
        self.assertEqual(app._foxbot_mission_spin_count_v1(1000, 30, 1), 1)

    def test_real_structural_vote_row_is_read_from_action_info(self):
        self.assertEqual(app._foxbot_mission_vote_amount_v1(_real_row(VOTE_ROW_10)), 10)

    def test_chat_text_is_never_read(self):
        self.assertIsNone(app._foxbot_mission_vote_amount_v1(_vote_text_row(300)))

    def test_vote_type_without_action_info_is_ignored(self):
        row = _real_row(VOTE_ROW_10)
        row.pop("actionInfo")
        self.assertIsNone(app._foxbot_mission_vote_amount_v1(row))

    def test_non_vote_structural_rows_are_ignored(self):
        self.assertIsNone(app._foxbot_mission_vote_amount_v1(_real_row(SUB_ROW)))

    def test_bad_amounts_are_ignored(self):
        for bad in (True, 0, -5, "60", None, 12.5):
            with self.subTest(amount=bad):
                row = _real_row(VOTE_ROW_10)
                row["actionInfo"]["amount"] = bad
                self.assertIsNone(app._foxbot_mission_vote_amount_v1(row))

    def test_chat_lines(self):
        self.assertEqual(app._foxbot_mission_announce_line_v1("piweb", 60, 2),
                         "🎯 piweb dropped 60 votes. MISSION WHEEL x2!")
        spin = {"prize": {"title": "Fists only", "description": "No weapons -- fists only."}}
        self.assertEqual(app._foxbot_mission_result_line_v1(spin), "🌴 MISSION: Fists only: No weapons -- fists only.")


class MissionValidationTestCase(unittest.TestCase):
    def test_starter_missions_are_valid(self):
        starters = wheels.default_segments(wheels.WHEEL_MISSION)
        self.assertEqual(len(starters), 23)
        self.assertEqual(wheels.validate_mission_segments(starters), starters)
        self.assertTrue(all(len(m["short_label"]) <= 14 for m in starters))

    def test_short_label_over_14_rejected(self):
        with self.assertRaisesRegex(ValueError, "14 characters"):
            wheels.validate_mission_segments([
                {"title": "A", "short_label": "x" * 15, "weight": 5},
                {"title": "B", "weight": 5},
            ])

    def test_weight_bounds(self):
        for bad in (0, 11):
            with self.subTest(weight=bad), self.assertRaisesRegex(ValueError, "weight"):
                wheels.validate_mission_segments(_missions(("A", bad, True), ("B", 5, True)))

    def test_blank_duration_means_until_done(self):
        cleaned = wheels.validate_mission_segments([
            {"title": "A", "duration_minutes": "", "weight": 5},
            {"title": "B", "duration_minutes": "10", "weight": 5},
        ])
        self.assertIsNone(cleaned[0]["duration_minutes"])
        self.assertEqual(cleaned[1]["duration_minutes"], 10)

    def test_needs_two_enabled(self):
        with self.assertRaisesRegex(ValueError, "enabled"):
            wheels.validate_mission_segments(_missions(("A", 5, True), ("B", 5, False)))

    def test_text_is_cleaned_and_label_mirrors_short_label(self):
        cleaned = wheels.validate_mission_segments([
            {"title": "  Pool\x00 game  ", "short_label": "", "description": "a\x07b", "weight": 5},
            {"title": "B", "weight": 5},
        ])
        self.assertEqual(cleaned[0]["title"], "Pool game")
        self.assertEqual(cleaned[0]["short_label"], "Pool game")
        self.assertEqual(cleaned[0]["label"], "Pool game")
        self.assertEqual(cleaned[0]["description"], "ab")
        self.assertEqual(cleaned[0]["type"], wheels.MISSION_PRIZE_TYPE)


class MissionPickTestCase(unittest.TestCase):
    def tearDown(self):
        casino_rng.set_provider(casino_rng.SecureRandomProvider())

    def test_disabled_missions_are_never_on_the_wheel_or_picked(self):
        missions = wheels.validate_mission_segments(_missions(
            ("Off heavy", 10, False), ("On A", 1, True), ("Off B", 10, False), ("On C", 2, True)))
        config = wheels.WheelConfig("h", False, False, 50, False, True, {wheels.WHEEL_MISSION: missions})
        active = wheels.active_segments(config, wheels.WHEEL_MISSION)
        self.assertEqual([m["title"] for m in active], ["On A", "On C"])
        # every possible ticket of the weighted draw lands on an enabled mission
        for ticket in range(1, 4):
            casino_rng.set_provider(_FixedRoll(ticket))
            self.assertIn(active[wheels.pick_segment_index(active)]["title"], ("On A", "On C"))


# ----------------------------------------------------------- real Postgres


def _cleanup_handle(handle):
    with wheels._connect() as connection:
        wheels._ensure_schema(connection)
        connection.execute(f"DELETE FROM {wheels.TABLE_SPINS} WHERE creator_handle = %s", (handle,))
        connection.execute(f"DELETE FROM {wheels.TABLE_CONFIG} WHERE creator_handle = %s", (handle,))


@unittest.skipUnless(DATABASE_CONFIGURED, SKIP_REASON)
class MissionTriggerIntegrationTestCase(unittest.TestCase):
    def setUp(self):
        self.handle = f"test-mission-trg-{uuid.uuid4().hex[:8]}"
        self.channel_id = f"test-mission-chan-{uuid.uuid4().hex[:8]}"
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
            mock.patch.object(app, "_foxbot_mission_start_runner_v1"),
        ]
        mocks = [p.start() for p in self.patches]
        self.mock_send = mocks[1]
        self.mock_runner = mocks[5]
        self.creator_id = f"test-mission-cid-{uuid.uuid4().hex[:8]}"
        wheels.set_config(self.handle, mission_enabled=True)  # not the owner handle -> off by default

    def tearDown(self):
        for p in self.patches:
            p.stop()
        _cleanup_handle(self.handle)
        app.foxcoin_economy["by_creator"].pop(self.creator_id, None)
        app._FOXBOT_MULTICHANNEL_INITIALIZED_V1.discard(self.channel_id)
        app.processed_polling_messages.clear()
        app.automation_recent_events.clear()
        app.auto_chat_event_seen.clear()

    def _run(self, *rows):
        app._foxbot_process_channel_rows_v1(self.target, list(rows), resolved_creator_id=self.creator_id)

    def _mission_spins(self):
        return wheels.list_spins(self.handle, oldest_first=True, wheels=(wheels.WHEEL_MISSION,), limit=200)

    def _sent_lines(self):
        return [c.args[0] for c in self.mock_send.call_args_list]

    def test_threshold_math_through_the_real_loop(self):
        # defaults: 30 votes per spin, max 3 spins per event
        for amount, expected in ((29, 0), (30, 1), (60, 2), (1000, 3)):
            with self.subTest(amount=amount):
                _cleanup_handle(self.handle)
                wheels.set_config(self.handle, mission_enabled=True)
                self.mock_send.reset_mock()
                self._run(_fresh_row(VOTE_ROW_10, amount=amount))
                spins = self._mission_spins()
                self.assertEqual(len(spins), expected)
                self.assertTrue(all(s["viewer"] == "piweb" and s["trigger_amount"] == amount for s in spins))
                announces = [line for line in self._sent_lines() if "MISSION WHEEL" in line]
                if expected:
                    self.assertEqual(announces, [f"🎯 piweb dropped {amount} votes. MISSION WHEEL x{expected}!"])
                else:
                    self.assertEqual(announces, [])

    def test_custom_threshold_and_cap(self):
        wheels.set_config(self.handle, mission_vote_threshold=10, mission_max_spins=5)
        self._run(_fresh_row(VOTE_ROW_10, amount=1000))
        self.assertEqual(len(self._mission_spins()), 5)

    def test_bot_sender_ignored(self):
        for bot in ("foxbotai", "CacheBot", "@ScurvyBot", os.getenv("FOXBOT_BLAZE_PROFILE_HANDLE", "foxbotai")):
            with self.subTest(bot=bot):
                self._run(_fresh_row(VOTE_ROW_10, amount=1000, senderDisplayName=bot))
        self.assertEqual(self._mission_spins(), [])
        self.assertFalse([line for line in self._sent_lines() if "MISSION" in line])

    def test_chat_text_ignored(self):
        self._run(_vote_text_row(300))
        no_action_info = _fresh_row(VOTE_ROW_10)
        no_action_info.pop("actionInfo")
        no_action_info["message"] = "piweb is giving 300 Votes to crypt0k1ng96."
        self._run(no_action_info)
        self.assertEqual(self._mission_spins(), [])

    def test_disabled_wheel_queues_nothing(self):
        wheels.set_config(self.handle, mission_enabled=False)
        self._run(_fresh_row(VOTE_ROW_10, amount=1000))
        self.assertEqual(self._mission_spins(), [])

    def test_same_row_queues_once_even_after_restart(self):
        row = _fresh_row(VOTE_ROW_10, amount=60)
        self._run(row)
        app.processed_polling_messages.clear()  # what a restart loses
        self._run(row)
        self.assertEqual(len(self._mission_spins()), 2)
        self.assertEqual(len([line for line in self._sent_lines() if "MISSION WHEEL" in line]), 1)

    def test_pushup_wheel_and_mission_wheel_both_fire(self):
        wheels.set_config(self.handle, exercise_enabled=True, vote_threshold=50)
        self._run(_fresh_row(VOTE_ROW_10, amount=60))
        queued = wheels.list_spins(self.handle, status=wheels.STATUS_QUEUED, oldest_first=True)
        self.assertEqual(sorted(s["wheel"] for s in queued), ["exercise", "mission", "mission"])

    def test_queueing_starts_the_auto_play_runner(self):
        self._run(_fresh_row(VOTE_ROW_10, amount=90))
        self.mock_runner.assert_called_once()
        self.assertEqual(self.mock_runner.call_args.args[0], self.handle)


@unittest.skipUnless(DATABASE_CONFIGURED, SKIP_REASON)
class MissionRunnerIntegrationTestCase(unittest.TestCase):
    """The real auto-play runner (real threads), with the reveal and gap
    delays shrunk so it finishes in well under a second."""

    def setUp(self):
        self.handle = f"test-mission-run-{uuid.uuid4().hex[:8]}"
        self.patches = [
            mock.patch.object(app, "send_blaze_chat_message", return_value={"success": True}),
            mock.patch.object(app, "FOXBOT_MISSION_REVEAL_DELAY_SECONDS_V1", 0.0),
            mock.patch.object(app, "FOXBOT_MISSION_SPIN_GAP_SECONDS_V1", 0.05),
            mock.patch.object(app, "_foxbot_wheel_channel_for_handle_v1", return_value="test-channel"),
        ]
        self.mock_send = self.patches[0].start()
        for p in self.patches[1:]:
            p.start()
        wheels.set_config(self.handle, mission_enabled=True)

    def tearDown(self):
        for p in self.patches:
            p.stop()
        with app._FOXBOT_MISSION_RUNNERS_LOCK_V1:
            app._FOXBOT_MISSION_RUNNERS_V1.discard(self.handle)
        _cleanup_handle(self.handle)

    def test_runner_plays_every_pending_spin_one_after_another(self):
        for n in range(3):
            wheels.queue_spin(self.handle, wheels.WHEEL_MISSION, "piweb", trigger_kind="vote",
                              trigger_amount=90, dedupe_key=f"k{n}")
        self.assertTrue(app._foxbot_mission_start_runner_v1(self.handle, 0.0))
        self.assertFalse(app._foxbot_mission_start_runner_v1(self.handle, 0.0))  # one runner per creator

        deadline = time.time() + 10
        while time.time() < deadline:
            pending = wheels.list_spins(self.handle, status=wheels.STATUS_QUEUED, wheels=(wheels.WHEEL_MISSION,))
            with app._FOXBOT_MISSION_RUNNERS_LOCK_V1:
                running = self.handle in app._FOXBOT_MISSION_RUNNERS_V1
            if not pending and not running:
                break
            time.sleep(0.05)

        spun = wheels.list_spins(self.handle, status=wheels.STATUS_SPUN, oldest_first=True, wheels=(wheels.WHEEL_MISSION,))
        self.assertEqual(len(spun), 3)
        time.sleep(0.2)  # the reveal Timer(0) threads
        lines = [c.args[0] for c in self.mock_send.call_args_list]
        self.assertEqual(len([line for line in lines if line.startswith("🌴 MISSION: ")]), 3)
        for spin in spun:
            self.assertEqual(wheels.mission_state(spin), wheels.MISSION_PLAYING)


@unittest.skipUnless(DATABASE_CONFIGURED, SKIP_REASON)
class MissionServiceIntegrationTestCase(unittest.TestCase):
    def setUp(self):
        self.handle = f"test-mission-svc-{uuid.uuid4().hex[:8]}"

    def tearDown(self):
        _cleanup_handle(self.handle)

    def test_on_by_default_for_the_owner_only(self):
        with mock.patch("services.foxbot_events.resolve_owner_handle", return_value=self.handle):
            owner = wheels.get_config(self.handle)
        with mock.patch("services.foxbot_events.resolve_owner_handle", return_value="someone-else"):
            other = wheels.get_config(self.handle)
        self.assertTrue(owner.mission_enabled)
        self.assertFalse(other.mission_enabled)
        self.assertEqual((owner.mission_vote_threshold, owner.mission_max_spins), (30, 3))
        self.assertEqual(len(owner.segments[wheels.WHEEL_MISSION]), 23)

    def test_explicit_setting_overrides_the_owner_default(self):
        with mock.patch("services.foxbot_events.resolve_owner_handle", return_value=self.handle):
            wheels.set_config(self.handle, mission_enabled=False)
            self.assertFalse(wheels.get_config(self.handle).mission_enabled)

    def test_setting_mission_fields_keeps_prize_wheel_fields(self):
        wheels.set_config(self.handle, exercise_enabled=True, vote_threshold=80)
        config = wheels.set_config(self.handle, mission_vote_threshold=40, mission_max_spins=2)
        self.assertTrue(config.exercise_enabled)
        self.assertEqual(config.vote_threshold, 80)
        self.assertEqual((config.mission_vote_threshold, config.mission_max_spins), (40, 2))

    def test_disabled_missions_never_picked_over_many_real_draws(self):
        wheels.set_config(self.handle, mission_segments=_missions(
            ("Never 1", 10, False), ("Yes A", 1, True), ("Never 2", 10, False), ("Yes B", 1, True)))
        for n in range(40):
            spin = wheels.queue_spin(self.handle, wheels.WHEEL_MISSION, "bob", trigger_kind="vote", dedupe_key=f"k{n}")
            result = wheels.resolve_spin(self.handle, spin["id"])
            self.assertIn(result["prize"]["title"], ("Yes A", "Yes B"))
            self.assertEqual([s["title"] for s in result["segments"]], ["Yes A", "Yes B"])

    def test_mission_spin_is_playing_until_done_and_timed_ones_expire(self):
        wheels.set_config(self.handle, mission_segments=[
            {"title": "Timed", "duration_minutes": 5, "weight": 10},
            {"title": "Untimed", "weight": 1, "enabled": False},
            {"title": "Other", "duration_minutes": 5, "weight": 1},
        ])
        spin = wheels.queue_spin(self.handle, wheels.WHEEL_MISSION, "bob", trigger_kind="vote", dedupe_key="k")
        self.assertEqual(wheels.mission_state(spin), wheels.MISSION_PENDING)
        result = wheels.resolve_spin(self.handle, spin["id"])
        self.assertEqual(result["fulfillment"], wheels.FULFILL_OWED)
        self.assertEqual(wheels.mission_state(result), wheels.MISSION_PLAYING)
        later = wheels.mission_ends_at(result)
        self.assertEqual(wheels.mission_state(result, now=later), wheels.MISSION_DONE)
        done = wheels.set_fulfilled(self.handle, spin["id"])
        self.assertEqual(wheels.mission_state(done), wheels.MISSION_DONE)

    def test_prize_wheel_listings_never_include_mission_rows(self):
        wheels.queue_spin(self.handle, wheels.WHEEL_MISSION, "bob", trigger_kind="vote", dedupe_key="m")
        wheels.queue_spin(self.handle, wheels.WHEEL_SUB, "bob", trigger_kind="sub", dedupe_key="s")
        prize_only = wheels.list_spins(self.handle, wheels=wheels.WHEELS)
        self.assertEqual([s["wheel"] for s in prize_only], ["sub"])
        for spin in wheels.list_spins(self.handle):
            wheels.resolve_spin(self.handle, spin["id"])
        self.assertEqual([s["wheel"] for s in wheels.latest_spun(self.handle, wheels=wheels.WHEELS)], ["sub"])
        self.assertEqual([s["wheel"] for s in wheels.latest_spun(self.handle, wheels=(wheels.WHEEL_MISSION,))], ["mission"])


@unittest.skipUnless(DATABASE_CONFIGURED, SKIP_REASON)
class MissionRoutesIntegrationTestCase(unittest.TestCase):
    ROUTES = (
        ("get", "/api/studio/mission-wheel"),
        ("post", "/api/studio/mission-wheel/config"),
        ("post", "/api/studio/mission-wheel/test"),
        ("post", "/api/studio/mission-wheel/spin-next"),
        ("post", "/api/studio/mission-wheel/done/1"),
        ("post", "/api/studio/mission-wheel/cancel/1"),
    )

    def setUp(self):
        from fastapi.testclient import TestClient

        self.client = TestClient(app.app)
        self.admin_blaze_id = f"test-mission-admin-{uuid.uuid4().hex[:10]}"
        self.creator_b_id = f"test-mission-b-{uuid.uuid4().hex[:10]}"
        self.handle_a = f"test-mission-owner-{uuid.uuid4().hex[:8]}"
        self.handle_b = f"test-mission-b-{uuid.uuid4().hex[:8]}"

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
            mock.patch.object(app, "_foxbot_mission_start_runner_v1"),
            mock.patch("services.foxbot_events.resolve_owner_handle", return_value=self.handle_a),
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

    def _call(self, method, path, cookies=None, **kwargs):
        if method == "post":
            kwargs.setdefault("json", {})
        return getattr(self.client, method)(path, cookies=cookies, **kwargs)

    def test_unauthenticated_is_rejected_everywhere(self):
        for method, path in self.ROUTES:
            with self.subTest(path=path):
                self.assertEqual(self._call(method, path).status_code, 401)

    def test_scoped_creator_is_forbidden_everywhere(self):
        other = self._cookies(self.creator_b_id)
        for method, path in self.ROUTES:
            with self.subTest(path=path):
                res = self._call(method, path, cookies=other)
                self.assertEqual(res.status_code, 403, res.text)
        self.assertEqual(wheels.list_spins(self.handle_b), [])

    def test_scoped_creator_cannot_touch_missions_via_prize_wheel_routes(self):
        owner = self._cookies(self.admin_blaze_id)
        spin = wheels.queue_spin(self.handle_a, wheels.WHEEL_MISSION, "bob", trigger_kind="vote", dedupe_key="m")
        # admin's own Prize Wheels by-id routes refuse a mission spin too
        for path in (f"/api/studio/wheels/spin/{spin['id']}", f"/api/studio/wheels/cancel/{spin['id']}",
                     f"/api/studio/wheels/fulfill/{spin['id']}"):
            with self.subTest(path=path):
                self.assertEqual(self.client.post(path, json={}, cookies=owner).status_code, 404)
        state = self.client.get("/api/studio/wheels", cookies=owner).json()
        self.assertEqual(state["queued"], [])
        self.assertEqual(self.client.post("/api/studio/wheels/spin-next", cookies=owner).status_code, 404)
        self.assertEqual(wheels.get_spin(self.handle_a, spin["id"])["status"], wheels.STATUS_QUEUED)

    def test_basic_auth_admin_is_allowed(self):
        res = self.client.get("/api/studio/mission-wheel", auth=("test-admin", "test-password-not-real"))
        self.assertEqual(res.status_code, 200, res.text)

    def test_admin_full_flow(self):
        cookies = self._cookies(self.admin_blaze_id)
        state = self.client.get("/api/studio/mission-wheel", cookies=cookies)
        self.assertEqual(state.status_code, 200, state.text)
        body = state.json()
        self.assertTrue(body["config"]["enabled"])            # owner default
        self.assertEqual(body["config"]["vote_threshold"], 30)
        self.assertEqual(body["config"]["max_spins"], 3)
        self.assertEqual(len(body["config"]["missions"]), 23)
        self.assertEqual(body["overlay_path"], f"/overlay/mission-wheel?handle={self.handle_a}")

        res = self.client.post("/api/studio/mission-wheel/config", cookies=cookies, json={
            "vote_threshold": 25, "max_spins": 4,
            "missions": [
                {"title": "Pool game", "short_label": "Pool", "description": "Play pool.", "duration_minutes": 10, "weight": 3},
                {"title": "Car meet", "short_label": "Car Meet", "description": "", "weight": 7},
            ],
        })
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual([m["title"] for m in res.json()["config"]["missions"]], ["Pool game", "Car meet"])

        bad = self.client.post("/api/studio/mission-wheel/config", cookies=cookies,
                               json={"missions": [{"title": "A", "short_label": "x" * 15}, {"title": "B"}]})
        self.assertEqual(bad.status_code, 400)

        # pending -> spin next -> playing -> done
        wheels.queue_spin(self.handle_a, wheels.WHEEL_MISSION, "piweb", trigger_kind="vote", trigger_amount=50,
                          dedupe_key="flow")
        self.assertEqual([s["viewer"] for s in self.client.get("/api/studio/mission-wheel", cookies=cookies).json()["pending"]],
                         ["piweb"])
        res = self.client.post("/api/studio/mission-wheel/spin-next", cookies=cookies)
        self.assertEqual(res.status_code, 200, res.text)
        spin = res.json()["spin"]
        self.assertEqual(spin["state"], "playing")
        self.assertIn(spin["prize"]["title"], ("Pool game", "Car meet"))
        self.assertEqual(self.client.post("/api/studio/mission-wheel/spin-next", cookies=cookies).status_code, 404)

        overlay = self.client.get(f"/overlay/mission-wheel-data?handle={self.handle_a}").json()
        self.assertTrue(overlay["ok"])
        self.assertEqual(len(overlay["spins"]), 1)
        shown = overlay["spins"][0]
        self.assertEqual(shown["prize"]["title"], spin["prize"]["title"])
        self.assertEqual(shown["segments"][shown["segment_index"]]["label"], shown["prize"]["short_label"])
        self.assertEqual(set(shown["segments"][0].keys()), {"label", "type"})  # no weights to the public
        self.assertNotIn("weight", shown["prize"])
        self.assertNotIn("creator_id", shown)
        self.assertNotIn("channel_id", shown)
        self.assertEqual(overlay["wheels"]["mission"], [{"label": "Pool", "type": "mission"},
                                                         {"label": "Car Meet", "type": "mission"}])

        res = self.client.post(f"/api/studio/mission-wheel/done/{spin['id']}", cookies=cookies, json={})
        self.assertEqual(res.status_code, 200, res.text)
        state = self.client.get("/api/studio/mission-wheel", cookies=cookies).json()
        self.assertEqual(state["playing"], [])
        self.assertEqual([s["id"] for s in state["done"]], [spin["id"]])

    def test_test_spin_needs_no_votes_and_does_not_chat(self):
        cookies = self._cookies(self.admin_blaze_id)
        self.client.post("/api/studio/mission-wheel/config", cookies=cookies, json={"enabled": False})
        res = self.client.post("/api/studio/mission-wheel/test", cookies=cookies)
        self.assertEqual(res.status_code, 200, res.text)
        self.assertTrue(res.json()["prize"]["title"])
        state = self.client.get("/api/studio/mission-wheel", cookies=cookies).json()
        self.assertEqual(state["playing"], [])
        self.assertEqual(state["done"][0]["trigger_kind"], "test")
        overlay = self.client.get(f"/overlay/mission-wheel-data?handle={self.handle_a}").json()
        self.assertEqual(overlay["spins"][0]["trigger_kind"], "test")
        app.send_blaze_chat_message.assert_not_called()

    def test_cancel_only_mission_spins(self):
        cookies = self._cookies(self.admin_blaze_id)
        sub = wheels.queue_spin(self.handle_a, wheels.WHEEL_SUB, "bob", trigger_kind="sub", dedupe_key="s")
        mission = wheels.queue_spin(self.handle_a, wheels.WHEEL_MISSION, "bob", trigger_kind="vote", dedupe_key="m")
        self.assertEqual(self.client.post(f"/api/studio/mission-wheel/cancel/{sub['id']}", cookies=cookies).status_code, 404)
        self.assertEqual(self.client.post(f"/api/studio/mission-wheel/cancel/{mission['id']}", cookies=cookies).status_code, 200)
        self.assertEqual(wheels.get_spin(self.handle_a, sub["id"])["status"], wheels.STATUS_QUEUED)
        self.assertEqual(wheels.get_spin(self.handle_a, mission["id"])["status"], wheels.STATUS_CANCELLED)

    def test_overlay_is_public_and_read_only(self):
        page = self.client.get("/overlay/mission-wheel")
        self.assertEqual(page.status_code, 200)
        self.assertIn("FoxBot Mission Wheel Overlay", page.text)
        self.assertIn('window.FOXBOT_WHEEL_MODE = "mission"', page.text)
        self.assertIn("background: transparent", page.text)
        self.assertIn("#B026FF", page.text)
        self.assertIn("#FF007F", page.text)
        self.assertEqual(self.client.get(f"/overlay/mission-wheel-data?handle={self.handle_a}").status_code, 200)
        # read-only: no write verbs on the public routes
        self.assertEqual(self.client.post("/overlay/mission-wheel-data").status_code, 405)
        # the original overlay is untouched
        prize_page = self.client.get("/overlay/wheel").text
        self.assertIn("FoxBot Prize Wheel Overlay", prize_page)
        self.assertNotIn('window.FOXBOT_WHEEL_MODE = "mission"', prize_page)


if __name__ == "__main__":
    unittest.main()
