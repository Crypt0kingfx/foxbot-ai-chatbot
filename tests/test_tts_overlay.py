"""Tests for the TTS Stream Overlay v1: services/tts_config.py's
validation/partial-update contract, services/tts_filter.py's profanity
screen, the _foxbot_tts_emit_v1 gate (enabled/floor/cooldown/profanity),
and the public /overlay/tts + /overlay/tts-data routes.

Three groups, same split as tests/test_casino_overlay.py:
  - TtsFilterTestCase: NO DATABASE_URL required. Pure checks on
    services/tts_filter.py.
  - TtsEmitHookTestCase: NO DATABASE_URL required. Mocks
    tts_config.get_config and app._foxbot_events_v1.emit_event to prove
    the gating logic (disabled/floor/cooldown/profanity/never-raises) at
    the Python level.
  - TtsOverlayIntegrationTestCase: real Postgres via DATABASE_URL. Proves
    tts_config's own persistence contract and the public overlay routes.

Run with:
    python -m unittest tests.test_tts_overlay -v
"""

import os
import sys
import time
import unittest
import uuid
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app  # noqa: E402
import services.foxbot_events as foxbot_events  # noqa: E402
import services.tts_config as tts_config  # noqa: E402
import services.tts_filter as tts_filter  # noqa: E402


DATABASE_CONFIGURED = bool(os.getenv("DATABASE_URL"))
SKIP_REASON = (
    "DATABASE_URL not set -- these tests need a real Postgres database "
    "(a throwaway/dev one, not production) to prove the actual config "
    "persistence and overlay routes honestly."
)


class TtsFilterTestCase(unittest.TestCase):
    """No DATABASE_URL required -- pure, no I/O beyond loading the
    bundled wordlist once.

    The leetspeak/spacing cases below are verified against the REAL
    installed better-profanity behavior (checked directly via
    `from better_profanity import profanity; profanity.contains_profanity(...)`
    before writing these assertions), not assumed from the library's
    marketing. It catches character-SUBSTITUTION obfuscation (sh1t, a55hole)
    and spaced-out letters (f u c k) -- confirmed. It does NOT catch
    character-REPETITION obfuscation ("fuuuuck") -- also confirmed, and
    asserted here as a known gap so a future better-profanity upgrade that
    silently changes this behavior gets caught by a failing test either way,
    rather than this limitation being silently assumed away.
    """

    def test_clean_text_passes(self):
        self.assertTrue(tts_filter.is_clean("someone just won 500 promo credits on slots!"))

    def test_profane_text_blocked(self):
        self.assertFalse(tts_filter.is_clean("this is such a fucking win"))

    def test_leetspeak_substitution_blocked(self):
        self.assertFalse(tts_filter.is_clean("sh1t happens"))
        self.assertFalse(tts_filter.is_clean("a55hole"))

    def test_spaced_out_letters_blocked(self):
        self.assertFalse(tts_filter.is_clean("f u c k you"))

    def test_character_repetition_obfuscation_is_a_known_gap(self):
        """Documents a REAL, verified limitation rather than hiding it:
        better-profanity's wordlist does not currently normalize repeated
        letters, so this still passes as "clean". Kept as an explicit
        assertion (not skipped) so a library upgrade that starts catching
        this flips this test to a visible failure instead of silence."""
        self.assertTrue(tts_filter.is_clean("fuuuuck"))

    def test_substring_of_profane_word_not_false_flagged(self):
        """contains_profanity must be word-aware, not a naive substring
        scan -- "assassin"/"classic" contain no standalone profane word
        and must not be blocked."""
        self.assertTrue(tts_filter.is_clean("assassin"))
        self.assertTrue(tts_filter.is_clean("classic"))

    def test_empty_text_is_clean(self):
        self.assertTrue(tts_filter.is_clean(""))

    def test_none_text_is_clean(self):
        self.assertTrue(tts_filter.is_clean(None))


class TtsEmitHookTestCase(unittest.TestCase):
    """No DATABASE_URL required -- mocks tts_config.get_config and
    emit_event to test _foxbot_tts_emit_v1's own control flow without
    touching Postgres."""

    def setUp(self):
        self._emit_patch = mock.patch.object(app._foxbot_events_v1, "emit_event")
        self.mock_emit = self._emit_patch.start()
        app._FOXBOT_TTS_COOLDOWN_TRACKER_V1.clear()

    def tearDown(self):
        self._emit_patch.stop()
        app._FOXBOT_TTS_COOLDOWN_TRACKER_V1.clear()

    def _config(self, **overrides):
        base = dict(
            creator_handle="some-handle", enabled=True, voice_name="", volume=80,
            char_limit=200, min_payout=100, cooldown_seconds=15,
        )
        base.update(overrides)
        return tts_config.TtsConfig(**base)

    def test_build_line_is_pure_and_display_safe(self):
        line = app._foxbot_tts_build_line_v1("viewer1", {"game": "slots", "payout": 500, "highlight": "triple fox"})
        self.assertIn("viewer1", line)
        self.assertIn("500", line)
        self.assertIn("slots", line)
        self.assertIn("triple fox", line)

    def test_disabled_config_never_emits(self):
        with mock.patch.object(tts_config, "get_config", return_value=self._config(enabled=False)):
            app._foxbot_tts_emit_v1("some-handle", "viewer1", {"game": "slots", "payout": 5000, "highlight": ""})
        self.mock_emit.assert_not_called()

    def test_below_min_payout_never_emits(self):
        with mock.patch.object(tts_config, "get_config", return_value=self._config(min_payout=1000)):
            app._foxbot_tts_emit_v1("some-handle", "viewer1", {"game": "slots", "payout": 500, "highlight": ""})
        self.mock_emit.assert_not_called()

    def test_at_or_above_min_payout_emits(self):
        with mock.patch.object(tts_config, "get_config", return_value=self._config(min_payout=100)):
            app._foxbot_tts_emit_v1("some-handle", "viewer1", {"game": "slots", "payout": 100, "highlight": ""})
        self.mock_emit.assert_called_once()
        call_args, call_kwargs = self.mock_emit.call_args
        self.assertEqual(call_args[0], "some-handle")
        self.assertEqual(call_args[1], "tts_message")
        self.assertEqual(call_kwargs["actor"], "viewer1")
        self.assertEqual(set(call_kwargs["detail"].keys()), {"text"})

    def test_cooldown_blocks_a_second_emit_immediately_after(self):
        with mock.patch.object(tts_config, "get_config", return_value=self._config(cooldown_seconds=60)):
            app._foxbot_tts_emit_v1("some-handle", "viewer1", {"game": "slots", "payout": 500, "highlight": ""})
            app._foxbot_tts_emit_v1("some-handle", "viewer2", {"game": "dice", "payout": 500, "highlight": ""})
        self.mock_emit.assert_called_once()

    def test_cooldown_is_scoped_per_creator_handle(self):
        with mock.patch.object(tts_config, "get_config", return_value=self._config(cooldown_seconds=60)):
            app._foxbot_tts_emit_v1("handle-a", "viewer1", {"game": "slots", "payout": 500, "highlight": ""})
            app._foxbot_tts_emit_v1("handle-b", "viewer1", {"game": "slots", "payout": 500, "highlight": ""})
        self.assertEqual(self.mock_emit.call_count, 2)

    def test_cooldown_expires_after_configured_window(self):
        with mock.patch.object(tts_config, "get_config", return_value=self._config(cooldown_seconds=0.05)):
            app._foxbot_tts_emit_v1("some-handle", "viewer1", {"game": "slots", "payout": 500, "highlight": ""})
            time.sleep(0.1)
            app._foxbot_tts_emit_v1("some-handle", "viewer1", {"game": "slots", "payout": 500, "highlight": ""})
        self.assertEqual(self.mock_emit.call_count, 2)

    def test_char_limit_truncates_before_emit(self):
        with mock.patch.object(tts_config, "get_config", return_value=self._config(char_limit=10)):
            app._foxbot_tts_emit_v1("some-handle", "viewer1", {"game": "slots", "payout": 500, "highlight": ""})
        call_kwargs = self.mock_emit.call_args[1]
        self.assertLessEqual(len(call_kwargs["detail"]["text"]), 10)

    def test_profane_username_blocks_emit(self):
        with mock.patch.object(tts_config, "get_config", return_value=self._config()):
            app._foxbot_tts_emit_v1("some-handle", "fuckface", {"game": "slots", "payout": 500, "highlight": ""})
        self.mock_emit.assert_not_called()

    def test_tts_config_unavailable_never_raises(self):
        with mock.patch.object(tts_config, "get_config", side_effect=tts_config.TtsConfigUnavailable("no db")):
            try:
                app._foxbot_tts_emit_v1("some-handle", "viewer1", {"game": "slots", "payout": 500, "highlight": ""})
            except Exception as exc:  # noqa: BLE001
                self.fail(f"_foxbot_tts_emit_v1 must never raise, got {exc!r}")
        self.mock_emit.assert_not_called()

    def test_malformed_detail_never_raises(self):
        with mock.patch.object(tts_config, "get_config", return_value=self._config()):
            try:
                app._foxbot_tts_emit_v1("some-handle", "viewer1", None)
                app._foxbot_tts_emit_v1("some-handle", "viewer1", "not-a-dict")
            except Exception as exc:  # noqa: BLE001
                self.fail(f"_foxbot_tts_emit_v1 must never raise, got {exc!r}")
        self.mock_emit.assert_not_called()

    def test_casino_emit_win_still_calls_tts_hook(self):
        """Wiring proof: _foxbot_casino_emit_win_v1 (the existing,
        already-tested casino_win hook) calls _foxbot_tts_emit_v1 as an
        additive step, not a replacement -- casino_win still emits too."""

        class _FakeResult:
            def __init__(self, outcome, payout, metadata=None, replayed=False):
                self.outcome = outcome
                self.payout = payout
                self.metadata = metadata or {}
                self.replayed = replayed

        floor = app._foxbot_casino_notable_payout_floor_v1()
        result = _FakeResult("win", floor + 50, {"roll": "heads"}, replayed=False)

        with mock.patch.object(tts_config, "get_config", return_value=self._config(min_payout=0)):
            app._foxbot_casino_emit_win_v1("some-handle", "viewer1", "coinflip", result)

        kinds = [call.args[1] for call in self.mock_emit.call_args_list]
        self.assertIn("casino_win", kinds)
        self.assertIn("tts_message", kinds)

    # ------------------------------------------------------------------
    # THE STRUCTURAL-ISOLATION PROOF: same shape as
    # test_casino_overlay.py's test_emit_event_raising_does_not_propagate /
    # test_payout_lands_correctly_even_if_emit_event_raises, but for the
    # NEW failure surface this change adds (_foxbot_tts_emit_v1 itself
    # raising) rather than re-testing the pre-existing emit_event failure
    # path those already cover.
    # ------------------------------------------------------------------
    def test_tts_hook_raising_does_not_block_casino_win_emit(self):
        """Even if _foxbot_tts_emit_v1 itself raises (bypassing its own
        internal try/except entirely, via a direct mock -- the worst case,
        not just a config lookup failure), the casino_win event -- which
        _foxbot_casino_emit_win_v1 emits BEFORE calling the TTS hook --
        must have already landed. Proves ordering, not just that the outer
        try/except swallows the error."""

        class _FakeResult:
            def __init__(self, outcome, payout, metadata=None, replayed=False):
                self.outcome = outcome
                self.payout = payout
                self.metadata = metadata or {}
                self.replayed = replayed

        floor = app._foxbot_casino_notable_payout_floor_v1()
        result = _FakeResult("win", floor + 50, {"roll": "heads"}, replayed=False)

        with mock.patch.object(app, "_foxbot_tts_emit_v1", side_effect=RuntimeError("tts hook exploded")):
            try:
                app._foxbot_casino_emit_win_v1("some-handle", "viewer1", "coinflip", result)
            except Exception as exc:  # noqa: BLE001
                self.fail(f"_foxbot_casino_emit_win_v1 must never raise even if the TTS hook does, got {exc!r}")

        # casino_win must have been emitted -- it happens before the TTS
        # call in _foxbot_casino_emit_win_v1's body, so a TTS-side
        # exception thrown afterward cannot have prevented it.
        self.mock_emit.assert_called_once()
        self.assertEqual(self.mock_emit.call_args[0][1], "casino_win")

    def test_tts_hook_raising_does_not_block_real_chat_payout(self):
        """Full command-layer regression, same shape as
        test_casino_overlay.py's test_payout_lands_correctly_even_if_emit_event_raises:
        a real !casinoflip win still pays out and replies "won" even when
        the entire TTS emission path throws."""
        if not DATABASE_CONFIGURED:
            self.skipTest(SKIP_REASON)

        import services.casino_config as casino_config
        import services.casino_ledger as cl
        import services.casino_rng as casino_rng
        import services.casino_rounds as cr

        creator_id = f"test-tts-payout-{uuid.uuid4().hex[:12]}"
        username = "TtsPayoutViewer"
        os.environ["FOXBOT_CASINO_ENABLED"] = "true"
        casino_config.set_config(creator_id, foxcoins_per_promo=10, daily_promo_limit=5000, casino_enabled=True)

        original_rng_provider = casino_rng.get_provider()

        class _FixedChoiceProvider(casino_rng.RNGProvider):
            def roll(self, minimum, maximum):
                return minimum

            def choice(self, seq):
                return "heads"

        try:
            with mock.patch.object(app, "_foxbot_resolve_creator_id_v1", return_value=creator_id):
                app.add_points(username, 5000, "test_seed", creator_id=creator_id)
                app.chat(message="!convert 200", username=username, creator_handle=creator_id)
                casino_rng.set_provider(_FixedChoiceProvider())

                with mock.patch.object(app, "_foxbot_tts_emit_v1", side_effect=RuntimeError("tts hook exploded")):
                    reply = app.chat(
                        message="!casinoflip heads 50", username=username, creator_handle=creator_id,
                    ).get("response", "")

            self.assertIn("won", reply.lower())
            self.assertEqual(
                cl.get_balance(creator_id, app.viewer_key(username), cr.CURRENCY_PROMO), 200 - 50 + 100,
            )
        finally:
            casino_rng.set_provider(original_rng_provider)
            os.environ.pop("FOXBOT_CASINO_ENABLED", None)
            with cl._connect() as connection:
                cl._ensure_schema(connection)
                casino_config._ensure_schema(connection)
                connection.execute(f"DELETE FROM {cl.TABLE_LEDGER} WHERE creator_id = %s", (creator_id,))
                connection.execute(f"DELETE FROM {cl.TABLE_BALANCES} WHERE creator_id = %s", (creator_id,))
                connection.execute(f"DELETE FROM {casino_config.TABLE_CONFIG} WHERE creator_id = %s", (creator_id,))


@unittest.skipUnless(DATABASE_CONFIGURED, SKIP_REASON)
class TtsConfigPersistenceTestCase(unittest.TestCase):
    def setUp(self):
        self.handle = f"test-tts-{uuid.uuid4().hex[:12]}"

    def tearDown(self):
        with tts_config._connect() as connection:
            tts_config._ensure_schema(connection)
            connection.execute(f"DELETE FROM {tts_config.TABLE_CONFIG} WHERE creator_handle = %s", (self.handle,))

    def test_default_config_for_unconfigured_creator(self):
        config = tts_config.get_config(self.handle)
        self.assertFalse(config.enabled)
        self.assertEqual(config.voice_name, tts_config.DEFAULT_VOICE_NAME)
        self.assertEqual(config.volume, tts_config.DEFAULT_VOLUME)

    def test_set_then_get_round_trips(self):
        tts_config.set_config(
            self.handle, enabled=True, voice_name="Microsoft Zira", volume=50,
            char_limit=120, min_payout=250, cooldown_seconds=30,
        )
        config = tts_config.get_config(self.handle)
        self.assertTrue(config.enabled)
        self.assertEqual(config.voice_name, "Microsoft Zira")
        self.assertEqual(config.volume, 50)
        self.assertEqual(config.char_limit, 120)
        self.assertEqual(config.min_payout, 250)
        self.assertEqual(config.cooldown_seconds, 30)

    def test_partial_update_preserves_other_fields(self):
        tts_config.set_config(self.handle, enabled=True, voice_name="Microsoft David", volume=70)
        tts_config.set_config(self.handle, volume=30)
        config = tts_config.get_config(self.handle)
        self.assertTrue(config.enabled)
        self.assertEqual(config.voice_name, "Microsoft David")
        self.assertEqual(config.volume, 30)

    def test_volume_out_of_range_rejected(self):
        with self.assertRaises(ValueError):
            tts_config.set_config(self.handle, volume=101)
        with self.assertRaises(ValueError):
            tts_config.set_config(self.handle, volume=-1)

    def test_cooldown_out_of_range_rejected(self):
        with self.assertRaises(ValueError):
            tts_config.set_config(self.handle, cooldown_seconds=1)
        with self.assertRaises(ValueError):
            tts_config.set_config(self.handle, cooldown_seconds=9999)

    def test_char_limit_out_of_range_rejected(self):
        with self.assertRaises(ValueError):
            tts_config.set_config(self.handle, char_limit=5)
        with self.assertRaises(ValueError):
            tts_config.set_config(self.handle, char_limit=10000)

    def test_negative_min_payout_rejected(self):
        with self.assertRaises(ValueError):
            tts_config.set_config(self.handle, min_payout=-1)


@unittest.skipUnless(DATABASE_CONFIGURED, SKIP_REASON)
class TtsConfigEndpointIsolationTestCase(unittest.TestCase):
    """Cross-creator isolation through the ACTUAL HTTP routes, via REAL
    signed session cookies through the REAL, unmocked Layer-1 auth-gate
    middleware -- same discipline as
    tests/test_casino_scoped_creator_access.py's ScopedCasinoAccessTestCase
    (an unauthenticated TestClient call against /api/studio/tts/config
    gets a 401 from that middleware before this test's own logic is even
    reached -- confirmed by the first version of this test, which used no
    cookies at all and got exactly that). _foxbot_resolve_event_handle_v1
    is mocked to two distinct handles (isolating the specific behavior
    under test -- cross-creator isolation -- from the separate concern of
    how connected_creators.json's join populates in production), while
    authentication itself goes through the real middleware."""

    def setUp(self):
        from fastapi.testclient import TestClient

        self.client = TestClient(app.app)

        self.admin_blaze_id = f"test-tts-admin-{uuid.uuid4().hex[:10]}"
        self.creator_a_id = f"test-tts-creatorA-{uuid.uuid4().hex[:10]}"
        self.creator_b_id = f"test-tts-creatorB-{uuid.uuid4().hex[:10]}"
        self.handle_a = f"test-tts-iso-a-{uuid.uuid4().hex[:8]}"
        self.handle_b = f"test-tts-iso-b-{uuid.uuid4().hex[:8]}"

        self._env_patches = {}
        for key, value in {
            "STUDIO_SESSION_SECRET": "test-secret-do-not-use-in-prod",
            "STUDIO_APPROVED_BLAZE_USER_IDS": ",".join([self.admin_blaze_id, self.creator_a_id, self.creator_b_id]),
            "STUDIO_AUTH_MODE": "both",
        }.items():
            self._env_patches[key] = os.environ.get(key)
            os.environ[key] = value

        self._tz_patch = mock.patch.object(app, "_tenant_zero_id", return_value=self.admin_blaze_id)
        self._tz_patch.start()

        handle_map = {self.creator_a_id: self.handle_a, self.creator_b_id: self.handle_b}

        def _fake_resolve_event_handle(blaze_id):
            if not blaze_id or blaze_id == self.admin_blaze_id:
                return "test-tts-owner-handle"
            return handle_map.get(blaze_id, "")

        self._handle_patch = mock.patch.object(
            app, "_foxbot_resolve_event_handle_v1", side_effect=_fake_resolve_event_handle,
        )
        self._handle_patch.start()

    def tearDown(self):
        self._tz_patch.stop()
        self._handle_patch.stop()

        for key, value in self._env_patches.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

        with tts_config._connect() as connection:
            tts_config._ensure_schema(connection)
            for handle in (self.handle_a, self.handle_b, "test-tts-owner-handle"):
                connection.execute(f"DELETE FROM {tts_config.TABLE_CONFIG} WHERE creator_handle = %s", (handle,))

    def _cookies_for(self, blaze_id, display_name):
        return {"foxbot_dashboard_session": app._foxbot_dashboard_session_sign_v1(blaze_id, display_name)}

    def test_unauthenticated_request_is_rejected_by_the_real_gate(self):
        """Sanity precondition for every other test in this class: proves
        the /api/studio/ prefix really is gated before any cookie is
        involved -- if this ever stopped being true, every isolation
        proof below would be meaningless (mocking a handle behind a gate
        that no longer exists proves nothing)."""
        res = self.client.get("/api/studio/tts/config")
        self.assertEqual(res.status_code, 401)

    def test_scoped_session_cannot_read_a_different_creators_config(self):
        res = self.client.post(
            "/api/studio/tts/config",
            json={"enabled": True, "voice_name": "Microsoft David", "volume": 42},
            cookies=self._cookies_for(self.creator_a_id, "creator-a"),
        )
        self.assertEqual(res.status_code, 200, res.text)
        self.assertTrue(res.json()["ok"])

        res = self.client.get("/api/studio/tts/config", cookies=self._cookies_for(self.creator_b_id, "creator-b"))
        data = res.json()

        self.assertEqual(data["creator_handle"], self.handle_b)
        self.assertFalse(data["enabled"], "creator B must see their own (unconfigured) defaults, not creator A's settings")
        self.assertNotEqual(data["voice_name"], "Microsoft David")
        self.assertNotEqual(data["volume"], 42)

    def test_client_supplied_handle_field_in_payload_is_ignored(self):
        """The route resolves the target handle ONLY from the session's
        own verified blaze_id (via _foxbot_resolve_event_handle_v1) -- it
        never reads a handle/creator_handle field out of the request body.
        Confirms that structurally: a malicious payload naming handle_b,
        submitted under creator A's own real signed session, still only
        ever writes to creator A's own (session-resolved) handle."""
        res = self.client.post(
            "/api/studio/tts/config",
            json={"enabled": True, "volume": 77, "creator_handle": self.handle_b, "handle": self.handle_b},
            cookies=self._cookies_for(self.creator_a_id, "creator-a"),
        )
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["creator_handle"], self.handle_a)

        config_b = tts_config.get_config(self.handle_b)
        self.assertFalse(config_b.enabled, "a payload field naming handle-b must never write to handle-b's row")

        config_a = tts_config.get_config(self.handle_a)
        self.assertTrue(config_a.enabled)
        self.assertEqual(config_a.volume, 77)

    def test_unmapped_session_cannot_write_or_leak_owner_config(self):
        """A real, authenticated, but UNMAPPED session (a fourth approved
        blaze_id with no entry in handle_map -- _foxbot_resolve_event_handle_v1's
        real "" contract for exactly this case) must never fall back to
        writing/reading tenant-zero's own config."""
        unmapped_id = f"test-tts-unmapped-{uuid.uuid4().hex[:10]}"
        os.environ["STUDIO_APPROVED_BLAZE_USER_IDS"] += f",{unmapped_id}"
        cookies = self._cookies_for(unmapped_id, "unmapped-creator")

        post_res = self.client.post("/api/studio/tts/config", json={"enabled": True}, cookies=cookies)
        self.assertEqual(post_res.status_code, 400, post_res.text)

        get_res = self.client.get("/api/studio/tts/config", cookies=cookies)
        self.assertEqual(get_res.json()["creator_handle"], "")


@unittest.skipUnless(DATABASE_CONFIGURED, SKIP_REASON)
class TtsOverlayIntegrationTestCase(unittest.TestCase):
    def setUp(self):
        self.creator_handle = f"test-tts-overlay-{uuid.uuid4().hex[:12]}"
        tts_config.set_config(self.creator_handle, enabled=True, voice_name="Microsoft Zira", volume=65)

    def tearDown(self):
        with tts_config._connect() as connection:
            tts_config._ensure_schema(connection)
            connection.execute(
                f"DELETE FROM {tts_config.TABLE_CONFIG} WHERE creator_handle = %s", (self.creator_handle,)
            )
        with foxbot_events._connect() as connection:
            foxbot_events._ensure_schema(connection)
            connection.execute("DELETE FROM foxbot_events WHERE creator_handle = %s", (self.creator_handle,))

    def _wait_for_events(self, kind="tts_message", timeout=3.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            rows = foxbot_events.fetch_events(self.creator_handle, limit=50)
            matches = [r for r in (rows or []) if r[0] == kind]
            if matches:
                return matches
            time.sleep(0.1)
        return []

    def test_overlay_data_endpoint_returns_config_and_lines_no_auth(self):
        from fastapi.testclient import TestClient

        foxbot_events.emit_event(
            self.creator_handle, "tts_message", actor="viewer1", detail={"text": "viewer1 just won 500 promo credits on slots!"},
        )
        self._wait_for_events()

        client = TestClient(app.app)
        res = client.get(f"/overlay/tts-data?handle={self.creator_handle}")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["voice_name"], "Microsoft Zira")
        self.assertEqual(data["volume"], 65)
        self.assertTrue(data["lines"], "expected the just-emitted line to appear")
        self.assertEqual(set(data["lines"][0].keys()), {"id", "text"})

    def test_overlay_data_endpoint_scoped_by_handle(self):
        from fastapi.testclient import TestClient

        foxbot_events.emit_event(
            self.creator_handle, "tts_message", actor="viewer1", detail={"text": "should not leak"},
        )
        self._wait_for_events()

        client = TestClient(app.app)
        other_handle = f"unrelated-{uuid.uuid4().hex[:8]}"
        res = client.get(f"/overlay/tts-data?handle={other_handle}")
        data = res.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["lines"], [], "a different creator's overlay must not see this creator's TTS lines")

    def test_overlay_page_loads_public_no_auth(self):
        from fastapi.testclient import TestClient

        client = TestClient(app.app)
        res = client.get("/overlay/tts")
        self.assertEqual(res.status_code, 200)
        self.assertIn("text/html", res.headers.get("content-type", ""))
        self.assertIn("speechSynthesis", res.text)

    def test_end_to_end_casino_win_produces_tts_line(self):
        """Full path: a real notable win, through the SAME emit hook the
        casino overlay uses, lands a tts_message row consumable by the
        public overlay endpoint."""

        class _FakeResult:
            def __init__(self, outcome, payout, metadata=None, replayed=False):
                self.outcome = outcome
                self.payout = payout
                self.metadata = metadata or {}
                self.replayed = replayed

        floor = app._foxbot_casino_notable_payout_floor_v1()
        result = _FakeResult("win", floor + 100, {"roll": "heads"}, replayed=False)

        app._foxbot_casino_emit_win_v1(self.creator_handle, "e2e-viewer", "coinflip", result)

        matches = self._wait_for_events()
        self.assertTrue(matches, "expected a tts_message event to land")
        kind, actor, detail, created_at = matches[0]
        self.assertEqual(actor, "e2e-viewer")
        self.assertIn("e2e-viewer", detail["text"])


class TtsStudioV2UiSmokeTestCase(unittest.TestCase):
    """No DATABASE_URL required -- /studio-v2 is served as a static HTML
    file (app.py:18798), no server-side rendering of tts_config data into
    it, so this only needs a valid session, not a database. A real,
    end-to-end browser-driven (Playwright + the actual installed Chrome)
    verification of the load/edit/save/reload round-trip and the 4-voice
    dropdown population was run manually against a throwaway local server
    instance before this test was added -- see the commit description for
    the captured result. This test is the permanent, fast regression
    guard: it can't drive real browser JS, but it DOES prove the markup
    and wiring that JS depends on can never silently vanish (a renamed
    element id, a dropped nav button, a typo'd endpoint path) without a
    test failing.
    """

    def setUp(self):
        from fastapi.testclient import TestClient

        self.client = TestClient(app.app)
        self._env_patches = {}
        for key, value in {
            "STUDIO_SESSION_SECRET": "test-secret-do-not-use-in-prod",
            "STUDIO_APPROVED_BLAZE_USER_IDS": "",
            "STUDIO_AUTH_MODE": "both",
        }.items():
            self._env_patches[key] = os.environ.get(key)
            os.environ[key] = value

        self.admin_blaze_id = f"test-tts-ui-admin-{uuid.uuid4().hex[:10]}"
        os.environ["STUDIO_APPROVED_BLAZE_USER_IDS"] = self.admin_blaze_id
        self._tz_patch = mock.patch.object(app, "_tenant_zero_id", return_value=self.admin_blaze_id)
        self._tz_patch.start()

    def tearDown(self):
        self._tz_patch.stop()
        for key, value in self._env_patches.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _cookies(self):
        token = app._foxbot_dashboard_session_sign_v1(self.admin_blaze_id, "test-admin")
        return {"foxbot_dashboard_session": token}

    def test_studio_v2_page_contains_tts_nav_and_section(self):
        res = self.client.get("/studio-v2", cookies=self._cookies())
        self.assertEqual(res.status_code, 200)

        self.assertIn('data-target="page-tts"', res.text)
        self.assertIn("Text-to-Speech", res.text)
        self.assertIn('id="page-tts"', res.text)

    def test_studio_v2_page_contains_all_expected_tts_form_fields(self):
        res = self.client.get("/studio-v2", cookies=self._cookies())
        for element_id in (
            "ttsToggleBtn", "ttsVoice", "ttsVolume", "ttsVolumeValue",
            "ttsMinPayout", "ttsCharLimit", "ttsSaveBtn",
        ):
            self.assertIn(f'id="{element_id}"', res.text, f"missing #{element_id} in the TTS section")

    def test_studio_v2_page_wires_the_already_tested_config_endpoint(self):
        """The JS must call the SAME /api/studio/tts/config endpoint
        TtsConfigEndpointIsolationTestCase/TtsConfigPersistenceTestCase
        already prove is self-service and cross-creator-isolated -- not a
        new, untested endpoint."""
        res = self.client.get("/studio-v2", cookies=self._cookies())
        self.assertIn("/api/studio/tts/config", res.text)

    def test_studio_v2_tts_save_payload_never_includes_a_handle_field(self):
        """Structural proof (mirroring TtsConfigEndpointIsolationTestCase's
        server-side check) that the CLIENT never constructs a payload
        naming a creator/handle at all -- the isolation guarantee holds
        end to end, not just because the server ignores an extra field."""
        res = self.client.get("/studio-v2", cookies=self._cookies())
        marker = "async function postTts"
        tts_section_start = res.text.find(marker)
        self.assertGreater(tts_section_start, -1, "expected the TTS script block (postTts helper) to be present")
        tts_js = res.text[tts_section_start:]
        for forbidden in ("creator_handle", "creator_id", "handle:"):
            self.assertNotIn(forbidden, tts_js)


if __name__ == "__main__":
    unittest.main()
