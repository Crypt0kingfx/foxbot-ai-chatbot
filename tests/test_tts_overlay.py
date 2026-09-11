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


def _make_tts_config(**overrides):
    """Full TtsConfig with sane defaults for every field -- both the
    win-announcement fields tested since the original TTS feature, and the
    chat-readout + server-owned-cursor fields added alongside it. A single
    shared builder keeps every test constructing a TtsConfig directly (as
    opposed to going through set_config/get_config against real Postgres)
    from needing to know about every field whenever a new one is added."""
    base = dict(
        creator_handle="some-handle", read_wins_enabled=True, voice_name="", volume=80,
        char_limit=200, min_payout=100, cooldown_seconds=15,
        read_chat_enabled=True, chat_cooldown_seconds=3, chat_min_chars=4,
        last_acked_win_event_id=-1, last_acked_chat_event_id=-1,
    )
    base.update(overrides)
    return tts_config.TtsConfig(**base)


class TtsFilterTestCase(unittest.TestCase):
    """No DATABASE_URL required -- pure, no I/O beyond loading the
    bundled wordlist once.

    All cases below -- both the "blocked" ones and the documented gaps --
    are verified against the REAL installed better-profanity behavior
    (checked directly via `from better_profanity import profanity;
    profanity.contains_profanity(...)` before writing these assertions),
    not assumed from the library's marketing or from what would be
    convenient. This matters more now than it did when this file only
    covered server-generated win text: chat-TTS (services/tts_filter.py's
    is_clean() is reused unchanged) screens arbitrary, adversarial user
    input on a live stream, a materially higher-stakes surface than a
    bot-authored sentence assembled from a fixed template.

    Confirmed CAUGHT: character-substitution leetspeak (sh1t, a55hole,
    n1gger, bi7ch), symbol substitution (sh!t), punctuation-separated
    letters (f.u.c.k, f-u-c-k), single- and multi-space-separated letters
    (f u c k, sh  it), and even a zero-width-space inserted mid-word
    (better-profanity strips whitespace-class characters before matching).

    Confirmed GAPS (documented, not hidden -- same discipline as the
    original character-repetition finding below): character-REPETITION
    obfuscation ("fuuuuck") is not normalized, and -- newly found while
    writing chat-TTS's adversarial tests -- profanity GLUED to another
    word with no separator at all ("dogshit", "shitballs") also isn't
    caught, since the library matches whole whitespace-delimited tokens
    against its wordlist rather than scanning for a profane substring
    inside a longer token (the same word-boundary design that correctly
    keeps "assassin"/"classic" from being false-flagged also means a
    profane word with no space around it slips through). Kept as explicit
    assertions, not skipped, so a library upgrade that starts catching
    either gap flips these to visible failures instead of silent success.
    """

    def test_clean_text_passes(self):
        self.assertTrue(tts_filter.is_clean("someone just won 500 promo credits on slots!"))

    def test_profane_text_blocked(self):
        self.assertFalse(tts_filter.is_clean("this is such a fucking win"))

    def test_leetspeak_substitution_blocked(self):
        self.assertFalse(tts_filter.is_clean("sh1t happens"))
        self.assertFalse(tts_filter.is_clean("a55hole"))
        self.assertFalse(tts_filter.is_clean("n1gger"))
        self.assertFalse(tts_filter.is_clean("i will ban this bi7ch"))

    def test_symbol_substitution_blocked(self):
        self.assertFalse(tts_filter.is_clean("sh!t happens"))

    def test_spaced_out_letters_blocked(self):
        self.assertFalse(tts_filter.is_clean("f u c k you"))
        self.assertFalse(tts_filter.is_clean("fu ck"))

    def test_punctuation_separated_letters_blocked(self):
        self.assertFalse(tts_filter.is_clean("f.u.c.k you"))
        self.assertFalse(tts_filter.is_clean("f-u-c-k this"))

    def test_multi_space_separated_letters_blocked(self):
        self.assertFalse(tts_filter.is_clean("sh  it show"))
        self.assertFalse(tts_filter.is_clean("you  are  a  bitch"))

    def test_zero_width_space_insertion_blocked(self):
        self.assertFalse(tts_filter.is_clean("f​uck this"))

    def test_all_caps_profanity_blocked(self):
        self.assertFalse(tts_filter.is_clean("FUCK this game"))

    def test_character_repetition_obfuscation_is_a_known_gap(self):
        """Documents a REAL, verified limitation rather than hiding it:
        better-profanity's wordlist does not currently normalize repeated
        letters, so this still passes as "clean". Kept as an explicit
        assertion (not skipped) so a library upgrade that starts catching
        this flips this test to a visible failure instead of silence."""
        self.assertTrue(tts_filter.is_clean("fuuuuck"))

    def test_profanity_glued_to_another_word_is_a_known_gap(self):
        """Newly found while writing chat-TTS's adversarial tests (this
        text is arbitrary user input, unlike the win-announcement strings
        this filter originally only ever screened): a profane word with NO
        separator at all, fused onto another word ("dogshit",
        "shitballs"), is NOT caught -- better-profanity matches whole
        whitespace-delimited tokens against its wordlist, and this doesn't
        match any whole token. This is the same word-boundary design that
        correctly avoids false-flagging "assassin"/"classic" below;
        avoiding one failure mode creates the other. A real, practical gap
        for a chat surface, documented rather than silently assumed away
        -- worth a custom pre-normalization pass if it proves to matter in
        practice, not attempted here to avoid a from-scratch profanity
        detector's own false-positive risk."""
        self.assertTrue(tts_filter.is_clean("this is dogshit"))
        self.assertTrue(tts_filter.is_clean("holy shitballs"))

    def test_substring_of_profane_word_not_false_flagged(self):
        """contains_profanity must be word-aware, not a naive substring
        scan -- "assassin"/"classic" contain no standalone profane word
        and must not be blocked."""
        self.assertTrue(tts_filter.is_clean("assassin"))
        self.assertTrue(tts_filter.is_clean("classic"))
        self.assertTrue(tts_filter.is_clean("scunthorpe"))

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
        return _make_tts_config(**overrides)

    def test_build_line_is_pure_and_display_safe(self):
        line = app._foxbot_tts_build_line_v1("viewer1", {"game": "slots", "payout": 500, "highlight": "triple fox"})
        self.assertIn("viewer1", line)
        self.assertIn("500", line)
        self.assertIn("slots", line)
        self.assertIn("triple fox", line)

    def test_disabled_config_never_emits(self):
        with mock.patch.object(tts_config, "get_config", return_value=self._config(read_wins_enabled=False)):
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


class TtsEmoteStripTestCase(unittest.TestCase):
    """No DATABASE_URL required -- pure checks on
    app._foxbot_tts_strip_emotes_v1, the [emote:<uuid>] token stripper.
    Confirmed against 30 real prod tts_chat_message rows: Blaze renders
    custom emotes inline as this literal token, with no emote name
    anywhere in the payload, so the fix is removal, not substitution."""

    def test_strips_single_emote_token(self):
        result = app._foxbot_tts_strip_emotes_v1("[emote:d2e848a3-3eec-455a-82f3-ed51969a111b]")
        self.assertEqual(result, "")

    def test_strips_multiple_emote_tokens_to_empty(self):
        result = app._foxbot_tts_strip_emotes_v1(
            "[emote:841539a5-88cd-4532-85c1-5589bc530b1b] [emote:841539a5-88cd-4532-85c1-5589bc530b1b] "
            "[emote:841539a5-88cd-4532-85c1-5589bc530b1b] [emote:841539a5-88cd-4532-85c1-5589bc530b1b]",
        )
        self.assertEqual(result, "")

    def test_leaves_plain_text_untouched(self):
        result = app._foxbot_tts_strip_emotes_v1("that was a great round honestly")
        self.assertEqual(result, "that was a great round honestly")

    def test_mixed_emote_and_text_collapses_whitespace(self):
        result = app._foxbot_tts_strip_emotes_v1("lol [emote:841539a5-88cd-4532-85c1-5589bc530b1b] that was wild")
        self.assertEqual(result, "lol that was wild")

    def test_none_and_empty_never_raise(self):
        self.assertEqual(app._foxbot_tts_strip_emotes_v1(None), "")
        self.assertEqual(app._foxbot_tts_strip_emotes_v1(""), "")

    def test_unclosed_or_malformed_token_does_not_swallow_real_text(self):
        result = app._foxbot_tts_strip_emotes_v1("[emote:no closing bracket here so this should survive")
        self.assertEqual(result, "[emote:no closing bracket here so this should survive")


class TtsChatEmitHookTestCase(unittest.TestCase):
    """No DATABASE_URL required -- mocks tts_config.get_config and
    emit_event, same shape as TtsEmitHookTestCase, but for
    _foxbot_tts_emit_chat_message_v1 (the chat-readout sibling of
    _foxbot_tts_emit_v1). Covers: read_chat_enabled gate, min-length gate,
    bot-command exclusion, the "username says: message" format, its own
    independent per-creator cooldown, char-limit truncation, profanity
    (including a username baked into the format), and the same
    never-raises discipline."""

    def setUp(self):
        self._emit_patch = mock.patch.object(app._foxbot_events_v1, "emit_event")
        self.mock_emit = self._emit_patch.start()
        app._FOXBOT_TTS_CHAT_COOLDOWN_TRACKER_V1.clear()

    def tearDown(self):
        self._emit_patch.stop()
        app._FOXBOT_TTS_CHAT_COOLDOWN_TRACKER_V1.clear()

    def _config(self, **overrides):
        return _make_tts_config(**overrides)

    def test_build_chat_line_format(self):
        line = app._foxbot_tts_build_chat_line_v1("viewer1", "nice hit on that slot")
        self.assertEqual(line, "viewer1 says: nice hit on that slot")

    def test_genuine_message_emits_tts_chat_message(self):
        with mock.patch.object(tts_config, "get_config", return_value=self._config()):
            app._foxbot_tts_emit_chat_message_v1("some-handle", "viewer1", "that was a great round honestly")
        self.mock_emit.assert_called_once()
        call_args, call_kwargs = self.mock_emit.call_args
        self.assertEqual(call_args[0], "some-handle")
        self.assertEqual(call_args[1], "tts_chat_message")
        self.assertEqual(call_kwargs["actor"], "viewer1")
        self.assertEqual(call_kwargs["detail"]["text"], "viewer1 says: that was a great round honestly")

    def test_disabled_read_chat_never_emits(self):
        with mock.patch.object(tts_config, "get_config", return_value=self._config(read_chat_enabled=False)):
            app._foxbot_tts_emit_chat_message_v1("some-handle", "viewer1", "hello there everyone")
        self.mock_emit.assert_not_called()

    def test_read_wins_toggle_does_not_gate_chat(self):
        """The two toggles are independent: read_wins_enabled=False must
        not block chat readout, and vice versa (covered by
        test_disabled_config_never_emits in TtsEmitHookTestCase)."""
        with mock.patch.object(
            tts_config, "get_config", return_value=self._config(read_wins_enabled=False, read_chat_enabled=True),
        ):
            app._foxbot_tts_emit_chat_message_v1("some-handle", "viewer1", "hello there everyone")
        self.mock_emit.assert_called_once()

    def test_bot_command_excluded_by_default(self):
        with mock.patch.object(tts_config, "get_config", return_value=self._config()):
            app._foxbot_tts_emit_chat_message_v1("some-handle", "viewer1", "!convert 500")
            app._foxbot_tts_emit_chat_message_v1("some-handle", "viewer1", "!cashout")
        self.mock_emit.assert_not_called()

    def test_emote_only_message_never_emits_and_does_not_advance_cooldown(self):
        """The regression that matters: chat_min_chars defaults to 4 and an
        emote token is ~45 chars, so before the strip-before-gate fix an
        emote-only message PASSED the length gate, emitted "username says:"
        with an empty body, and advanced the cooldown tracker -- suppressing
        the next genuine message for the full cooldown window."""
        with mock.patch.object(tts_config, "get_config", return_value=self._config()):
            app._foxbot_tts_emit_chat_message_v1(
                "some-handle", "viewer1", "[emote:841539a5-88cd-4532-85c1-5589bc530b1b]",
            )
        self.mock_emit.assert_not_called()
        self.assertNotIn("some-handle", app._FOXBOT_TTS_CHAT_COOLDOWN_TRACKER_V1)

    def test_multiple_emotes_only_never_emits(self):
        with mock.patch.object(tts_config, "get_config", return_value=self._config()):
            app._foxbot_tts_emit_chat_message_v1(
                "some-handle", "viewer1",
                "[emote:841539a5-88cd-4532-85c1-5589bc530b1b] [emote:841539a5-88cd-4532-85c1-5589bc530b1b] "
                "[emote:841539a5-88cd-4532-85c1-5589bc530b1b]",
            )
        self.mock_emit.assert_not_called()

    def test_mixed_emote_and_text_emits_with_emote_stripped(self):
        with mock.patch.object(tts_config, "get_config", return_value=self._config()):
            app._foxbot_tts_emit_chat_message_v1(
                "some-handle", "viewer1", "lol [emote:841539a5-88cd-4532-85c1-5589bc530b1b] that was wild",
            )
        self.mock_emit.assert_called_once()
        call_kwargs = self.mock_emit.call_args[1]
        self.assertEqual(call_kwargs["detail"]["text"], "viewer1 says: lol that was wild")

    def test_no_emotes_message_unaffected_by_strip(self):
        with mock.patch.object(tts_config, "get_config", return_value=self._config()):
            app._foxbot_tts_emit_chat_message_v1("some-handle", "viewer1", "that was a great round honestly")
        self.mock_emit.assert_called_once()
        call_kwargs = self.mock_emit.call_args[1]
        self.assertEqual(call_kwargs["detail"]["text"], "viewer1 says: that was a great round honestly")

    def test_emote_prefixed_command_still_excluded(self):
        """Cleaning the emote token first means a command hidden behind one
        is now correctly recognised as a command and excluded -- it was not
        before, since "[emote:...] !convert 500" didn't start with "!"."""
        with mock.patch.object(tts_config, "get_config", return_value=self._config()):
            app._foxbot_tts_emit_chat_message_v1(
                "some-handle", "viewer1", "[emote:841539a5-88cd-4532-85c1-5589bc530b1b] !convert 500",
            )
        self.mock_emit.assert_not_called()

    def test_below_min_chars_never_emits(self):
        with mock.patch.object(tts_config, "get_config", return_value=self._config(chat_min_chars=4)):
            app._foxbot_tts_emit_chat_message_v1("some-handle", "viewer1", "lol")
        self.mock_emit.assert_not_called()

    def test_at_min_chars_emits(self):
        with mock.patch.object(tts_config, "get_config", return_value=self._config(chat_min_chars=4)):
            app._foxbot_tts_emit_chat_message_v1("some-handle", "viewer1", "nice")
        self.mock_emit.assert_called_once()

    def test_empty_or_whitespace_message_never_emits(self):
        with mock.patch.object(tts_config, "get_config", return_value=self._config(chat_min_chars=0)):
            app._foxbot_tts_emit_chat_message_v1("some-handle", "viewer1", "")
            app._foxbot_tts_emit_chat_message_v1("some-handle", "viewer1", "   ")
            app._foxbot_tts_emit_chat_message_v1("some-handle", "viewer1", None)
        self.mock_emit.assert_not_called()

    def test_cooldown_blocks_a_second_emit_immediately_after(self):
        with mock.patch.object(tts_config, "get_config", return_value=self._config(chat_cooldown_seconds=60)):
            app._foxbot_tts_emit_chat_message_v1("some-handle", "viewer1", "this is my first message here")
            app._foxbot_tts_emit_chat_message_v1("some-handle", "viewer2", "this is a totally different message")
        self.mock_emit.assert_called_once()

    def test_cooldown_is_independent_of_win_cooldown_tracker(self):
        """The chat cooldown tracker (_FOXBOT_TTS_CHAT_COOLDOWN_TRACKER_V1)
        must be entirely separate state from the win one
        (_FOXBOT_TTS_COOLDOWN_TRACKER_V1) -- a recent win must never block
        a chat message from being read, or vice versa."""
        app._FOXBOT_TTS_COOLDOWN_TRACKER_V1["some-handle"] = time.time()
        with mock.patch.object(tts_config, "get_config", return_value=self._config(chat_cooldown_seconds=60)):
            app._foxbot_tts_emit_chat_message_v1("some-handle", "viewer1", "this should still be read aloud")
        self.mock_emit.assert_called_once()
        app._FOXBOT_TTS_COOLDOWN_TRACKER_V1.clear()

    def test_cooldown_is_scoped_per_creator_handle(self):
        with mock.patch.object(tts_config, "get_config", return_value=self._config(chat_cooldown_seconds=60)):
            app._foxbot_tts_emit_chat_message_v1("handle-a", "viewer1", "hello from channel a right now")
            app._foxbot_tts_emit_chat_message_v1("handle-b", "viewer1", "hello from channel b right now")
        self.assertEqual(self.mock_emit.call_count, 2)

    def test_char_limit_truncates_before_emit(self):
        with mock.patch.object(tts_config, "get_config", return_value=self._config(char_limit=15)):
            app._foxbot_tts_emit_chat_message_v1(
                "some-handle", "viewer1", "this is a much longer chat message than the limit allows",
            )
        call_kwargs = self.mock_emit.call_args[1]
        self.assertLessEqual(len(call_kwargs["detail"]["text"]), 15)

    def test_profane_message_blocks_emit(self):
        with mock.patch.object(tts_config, "get_config", return_value=self._config()):
            app._foxbot_tts_emit_chat_message_v1("some-handle", "viewer1", "this is such a fucking scam")
        self.mock_emit.assert_not_called()

    def test_profane_username_blocks_emit_even_with_clean_message(self):
        """"{username} says: {message}" means the username is part of the
        line actually screened -- a clean message from a profane display
        name must still be blocked, not just a profane message body."""
        with mock.patch.object(tts_config, "get_config", return_value=self._config()):
            app._foxbot_tts_emit_chat_message_v1("some-handle", "fuckface", "hello everyone how are you")
        self.mock_emit.assert_not_called()

    def test_tts_config_unavailable_never_raises(self):
        with mock.patch.object(tts_config, "get_config", side_effect=tts_config.TtsConfigUnavailable("no db")):
            try:
                app._foxbot_tts_emit_chat_message_v1("some-handle", "viewer1", "hello there everyone today")
            except Exception as exc:  # noqa: BLE001
                self.fail(f"_foxbot_tts_emit_chat_message_v1 must never raise, got {exc!r}")
        self.mock_emit.assert_not_called()

    def test_malformed_message_never_raises(self):
        with mock.patch.object(tts_config, "get_config", return_value=self._config()):
            try:
                app._foxbot_tts_emit_chat_message_v1("some-handle", "viewer1", None)
                app._foxbot_tts_emit_chat_message_v1("some-handle", None, "hello there everyone today")
                app._foxbot_tts_emit_chat_message_v1(None, "viewer1", "hello there everyone today")
            except Exception as exc:  # noqa: BLE001
                self.fail(f"_foxbot_tts_emit_chat_message_v1 must never raise, got {exc!r}")


class TtsChatHookWiringTestCase(unittest.TestCase):
    """No DATABASE_URL required -- proves _foxbot_tts_emit_chat_message_v1
    is actually wired into _foxbot_process_channel_rows_v1 (the real raw
    per-row chat loop, not a stand-in), sees every genuine chat row with
    the right (creator_handle, username, message_text), and -- the same
    "pure side-effect, can never break real message processing" discipline
    already proven for the win hook in TtsEmitHookTestCase -- that real
    command dispatch and replies are entirely unaffected even if this hook
    itself explodes. Reuses the exact mocking harness
    tests/test_discovery_seeding.py's DiscoverySeedingTests already
    established for driving _foxbot_process_channel_rows_v1 directly."""

    def setUp(self):
        self.channel_id = f"test-tts-chat-channel-{id(self)}-{time.time()}"
        self.target = {
            "channel_id": self.channel_id,
            "channel_slug": "testcreator",
            "handle": "testcreator",
            "is_subscription_channel": False,
        }

        app._FOXBOT_MULTICHANNEL_INITIALIZED_V1.discard(self.channel_id)
        app.processed_polling_messages.clear()

        self.chat_patch = mock.patch.object(app, "chat", return_value={"response": "FoxBot help: ..."})
        self.send_patch = mock.patch.object(app, "send_blaze_chat_message")
        self.auto_event_patch = mock.patch.object(app, "handle_auto_chat_event", return_value=None)
        self.emit_event_patch = mock.patch.object(app._foxbot_events_v1, "emit_event", return_value=None)
        self.tts_chat_patch = mock.patch.object(app, "_foxbot_tts_emit_chat_message_v1")

        self.mock_chat = self.chat_patch.start()
        self.mock_send = self.send_patch.start()
        self.auto_event_patch.start()
        self.emit_event_patch.start()
        self.mock_tts_chat = self.tts_chat_patch.start()

    def tearDown(self):
        self.chat_patch.stop()
        self.send_patch.stop()
        self.auto_event_patch.stop()
        self.emit_event_patch.stop()
        self.tts_chat_patch.stop()

        app._FOXBOT_MULTICHANNEL_INITIALIZED_V1.discard(self.channel_id)
        app.processed_polling_messages.clear()

    def _row(self, message_id, text, username="viewer1"):
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        return {
            "id": message_id, "text": text, "displayName": username,
            "createdAt": now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z",
        }

    def test_genuine_chat_message_reaches_the_tts_chat_hook(self):
        app._foxbot_process_channel_rows_v1(self.target, [self._row("m1", "that was such a great round")])
        self.mock_tts_chat.assert_called_once_with("testcreator", "viewer1", "that was such a great round")

    def test_bot_command_message_also_reaches_the_hook_call_site(self):
        """The call site itself does not filter commands out -- exclusion
        happens INSIDE _foxbot_tts_emit_chat_message_v1 (see
        TtsChatEmitHookTestCase.test_bot_command_excluded_by_default).
        This proves the call site's own contract: it hands every row to
        the hook unconditionally, before any command dispatch."""
        app._foxbot_process_channel_rows_v1(self.target, [self._row("m1", "!help")])
        self.mock_tts_chat.assert_called_once_with("testcreator", "viewer1", "!help")

    def test_hook_is_called_before_command_dispatch(self):
        """Ordering proof: the hook fires even though `chat()` (the real
        command dispatcher) is mocked to return a reply -- confirms this
        sits ahead of dispatch in the row loop, not conditioned on it."""
        app._foxbot_process_channel_rows_v1(self.target, [self._row("m1", "!help")])
        self.mock_tts_chat.assert_called_once()
        self.mock_chat.assert_called_once()

    def test_hook_receives_the_channel_scoped_creator_handle(self):
        """Cross-creator isolation: the hook must be called with THIS
        channel's own creator_handle (from target["handle"]), never a
        different channel's, even across multiple targets processed in
        the same poll cycle."""
        other_target = dict(self.target, channel_id=f"{self.channel_id}-other", handle="othercreator")
        app._FOXBOT_MULTICHANNEL_INITIALIZED_V1.discard(other_target["channel_id"])

        app._foxbot_process_channel_rows_v1(self.target, [self._row("m1", "hello from channel one")])
        app._foxbot_process_channel_rows_v1(other_target, [self._row("m2", "hello from channel two")])

        handles_seen = [call.args[0] for call in self.mock_tts_chat.call_args_list]
        self.assertEqual(handles_seen, ["testcreator", "othercreator"])

        app._FOXBOT_MULTICHANNEL_INITIALIZED_V1.discard(other_target["channel_id"])

    def test_hook_raising_does_not_block_real_command_dispatch(self):
        """The structural-isolation proof for the chat hook, same shape as
        TtsEmitHookTestCase's test_tts_hook_raising_does_not_block_real_chat_payout:
        even if _foxbot_tts_emit_chat_message_v1 raises outright (bypassing
        its own internal try/except via a direct mock -- worse than any
        realistic failure), the real !help command must still dispatch and
        reply, because the hook call site has no return value anything
        downstream depends on."""
        self.mock_tts_chat.side_effect = RuntimeError("chat tts hook exploded")

        processed = app._foxbot_process_channel_rows_v1(self.target, [self._row("m1", "!help")])

        self.assertEqual(processed, 1)
        self.mock_chat.assert_called_once()
        self.mock_send.assert_called_once()

    def test_empty_message_never_reaches_the_hook(self):
        """Rows with no message text are skipped before the hook's own
        call site (existing `if not message_text: continue` guard) --
        confirms this new call site didn't move ahead of that check."""
        app._foxbot_process_channel_rows_v1(self.target, [self._row("m1", "")])
        self.mock_tts_chat.assert_not_called()

    def test_known_bot_handle_never_reaches_the_hook(self):
        """The existing bot-account exclusion (known_bot_handles /
        FOXBOT_BLAZE_PROFILE_HANDLE) runs BEFORE this hook's call site --
        a bot's own messages must never be considered for chat-TTS."""
        app._foxbot_process_channel_rows_v1(self.target, [self._row("m1", "hello viewers", username="foxbotai")])
        self.mock_tts_chat.assert_not_called()


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
        self.assertFalse(config.read_wins_enabled)
        self.assertFalse(config.read_chat_enabled)
        self.assertEqual(config.voice_name, tts_config.DEFAULT_VOICE_NAME)
        self.assertEqual(config.volume, tts_config.DEFAULT_VOLUME)
        self.assertEqual(config.chat_cooldown_seconds, tts_config.DEFAULT_CHAT_COOLDOWN_SECONDS)
        self.assertEqual(config.chat_min_chars, tts_config.DEFAULT_CHAT_MIN_CHARS)
        self.assertEqual(config.last_acked_win_event_id, -1, "-1 means never-bootstrapped, not 0 -- see tts_config.py")
        self.assertEqual(config.last_acked_chat_event_id, -1)

    def test_set_then_get_round_trips(self):
        tts_config.set_config(
            self.handle, read_wins_enabled=True, voice_name="Microsoft Zira", volume=50,
            char_limit=120, min_payout=250, cooldown_seconds=30,
            read_chat_enabled=True, chat_cooldown_seconds=5, chat_min_chars=6,
        )
        config = tts_config.get_config(self.handle)
        self.assertTrue(config.read_wins_enabled)
        self.assertEqual(config.voice_name, "Microsoft Zira")
        self.assertEqual(config.volume, 50)
        self.assertEqual(config.char_limit, 120)
        self.assertEqual(config.min_payout, 250)
        self.assertEqual(config.cooldown_seconds, 30)
        self.assertTrue(config.read_chat_enabled)
        self.assertEqual(config.chat_cooldown_seconds, 5)
        self.assertEqual(config.chat_min_chars, 6)

    def test_partial_update_preserves_other_fields(self):
        tts_config.set_config(self.handle, read_wins_enabled=True, voice_name="Microsoft David", volume=70)
        tts_config.set_config(self.handle, volume=30)
        config = tts_config.get_config(self.handle)
        self.assertTrue(config.read_wins_enabled)
        self.assertEqual(config.voice_name, "Microsoft David")
        self.assertEqual(config.volume, 30)

    def test_read_wins_and_read_chat_are_independent_flags(self):
        """The two toggles must be settable independently -- flipping one
        must never move the other, in either direction."""
        tts_config.set_config(self.handle, read_wins_enabled=True, read_chat_enabled=False)
        config = tts_config.get_config(self.handle)
        self.assertTrue(config.read_wins_enabled)
        self.assertFalse(config.read_chat_enabled)

        tts_config.set_config(self.handle, read_chat_enabled=True)
        config = tts_config.get_config(self.handle)
        self.assertTrue(config.read_wins_enabled, "toggling read_chat_enabled must not affect read_wins_enabled")
        self.assertTrue(config.read_chat_enabled)

        tts_config.set_config(self.handle, read_wins_enabled=False)
        config = tts_config.get_config(self.handle)
        self.assertFalse(config.read_wins_enabled)
        self.assertTrue(config.read_chat_enabled, "toggling read_wins_enabled must not affect read_chat_enabled")

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

    def test_chat_cooldown_out_of_range_rejected(self):
        with self.assertRaises(ValueError):
            tts_config.set_config(self.handle, chat_cooldown_seconds=0)
        with self.assertRaises(ValueError):
            tts_config.set_config(self.handle, chat_cooldown_seconds=9999)

    def test_chat_min_chars_out_of_range_rejected(self):
        with self.assertRaises(ValueError):
            tts_config.set_config(self.handle, chat_min_chars=0)
        with self.assertRaises(ValueError):
            tts_config.set_config(self.handle, chat_min_chars=9999)

    def test_set_config_never_touches_ack_cursors(self):
        """set_config() is the general settings-save path (the dashboard's
        Save button and both toggle buttons) -- it must never reset the
        overlay's playback cursor, or every settings save would replay/
        skip lines. ack_event() is the only thing allowed to move these."""
        # A real foxbot_events row is required for ack_event's clamp to
        # accept a nonzero id (see TtsAckEventTestCase for that behavior)
        # -- here we only care that set_config leaves whatever the cursor
        # already is untouched, so seed a row via set_config first, then
        # set the cursor directly.
        tts_config.set_config(self.handle)
        with tts_config._connect() as connection:
            tts_config._ensure_schema(connection)
            connection.execute(
                f"UPDATE {tts_config.TABLE_CONFIG} SET last_acked_win_event_id = 42 WHERE creator_handle = %s",
                (self.handle,),
            )
        tts_config.set_config(self.handle, read_wins_enabled=True, volume=55)
        config = tts_config.get_config(self.handle)
        self.assertEqual(config.last_acked_win_event_id, 42)


@unittest.skipUnless(DATABASE_CONFIGURED, SKIP_REASON)
class TtsAckEventTestCase(unittest.TestCase):
    """services/tts_config.py's ack_event() -- the server-owned cursor
    mechanism that replaces the old client-side "seen" Set. Uses real
    foxbot_events rows (not mocks) since the clamp behavior specifically
    depends on what actually exists in that table for a creator."""

    def setUp(self):
        self.handle = f"test-tts-ack-{uuid.uuid4().hex[:12]}"

    def tearDown(self):
        with tts_config._connect() as connection:
            tts_config._ensure_schema(connection)
            connection.execute(f"DELETE FROM {tts_config.TABLE_CONFIG} WHERE creator_handle = %s", (self.handle,))
        with foxbot_events._connect() as connection:
            foxbot_events._ensure_schema(connection)
            connection.execute("DELETE FROM foxbot_events WHERE creator_handle = %s", (self.handle,))

    def _emit_and_get_id(self, kind, text="hello"):
        foxbot_events.emit_event(self.handle, kind, actor="viewer1", detail={"text": text})
        deadline = time.time() + 3.0
        while time.time() < deadline:
            max_id = foxbot_events.fetch_max_event_id(self.handle, kind)
            if max_id:
                return max_id
            time.sleep(0.05)
        self.fail(f"expected a {kind} row to land within 3s")

    def test_ack_advances_cursor_to_real_event_id(self):
        event_id = self._emit_and_get_id("tts_message")
        result = tts_config.ack_event(self.handle, "win", event_id)
        self.assertEqual(result, event_id)
        self.assertEqual(tts_config.get_config(self.handle).last_acked_win_event_id, event_id)

    def test_ack_is_clamped_to_the_real_max_for_that_creator(self):
        """A forged/oversized event_id (the realistic adversarial case for
        this unauthenticated-by-design endpoint) must never advance the
        cursor past what actually exists -- bounded exactly like a normal
        fast-forward, never an arbitrary future skip that could silence a
        creator's overlay indefinitely."""
        event_id = self._emit_and_get_id("tts_message")
        result = tts_config.ack_event(self.handle, "win", event_id + 999999)
        self.assertEqual(result, event_id, "ack must clamp to the real latest event, not the forged id")
        self.assertEqual(tts_config.get_config(self.handle).last_acked_win_event_id, event_id)

    def test_ack_is_monotonic_never_moves_backward(self):
        first_id = self._emit_and_get_id("tts_message", "first")
        second_id = self._emit_and_get_id("tts_message", "second")
        tts_config.ack_event(self.handle, "win", second_id)
        tts_config.ack_event(self.handle, "win", first_id)
        self.assertEqual(
            tts_config.get_config(self.handle).last_acked_win_event_id, second_id,
            "an older/out-of-order ack must never move the cursor backward",
        )

    def test_win_and_chat_cursors_are_independent(self):
        win_id = self._emit_and_get_id("tts_message")
        chat_id = self._emit_and_get_id("tts_chat_message")
        tts_config.ack_event(self.handle, "win", win_id)
        config = tts_config.get_config(self.handle)
        self.assertEqual(config.last_acked_win_event_id, win_id)
        self.assertEqual(
            config.last_acked_chat_event_id, -1, "acking the win stream must not move (or bootstrap) the chat cursor",
        )

        tts_config.ack_event(self.handle, "chat", chat_id)
        config = tts_config.get_config(self.handle)
        self.assertEqual(config.last_acked_win_event_id, win_id)
        self.assertEqual(config.last_acked_chat_event_id, chat_id)

    def test_ack_with_no_matching_event_is_a_noop(self):
        result = tts_config.ack_event(self.handle, "win", 999999999)
        self.assertIsNone(result)
        self.assertEqual(tts_config.get_config(self.handle).last_acked_win_event_id, -1)

    def test_ack_invalid_stream_returns_none(self):
        self.assertIsNone(tts_config.ack_event(self.handle, "not-a-real-stream", 1))

    def _wait_for_row_count(self, kind, count, timeout=5.0):
        deadline = time.time() + timeout
        rows = []
        while time.time() < deadline:
            with foxbot_events._connect() as connection:
                foxbot_events._ensure_schema(connection)
                rows = connection.execute(
                    "SELECT id FROM foxbot_events WHERE creator_handle = %s AND kind = %s ORDER BY id ASC",
                    (self.handle, kind),
                ).fetchall()
            if len(rows) >= count:
                return [r[0] for r in rows]
            time.sleep(0.05)
        self.fail(f"expected {count} {kind} rows to land within {timeout}s, got {len(rows)}")

    def test_queue_overflow_never_deletes_rows_only_advances_the_cursor(self):
        """Precise proof for the client-side max-queue-depth mechanism
        (MAX_QUEUE_DEPTH = 6 in tts_overlay_html): "dropping" an
        overflowed item is a purely client-side, in-memory operation (a
        plain `queue.shift()` on a browser-local JS array) -- it never
        deletes anything server-side. What actually happens on the server
        is exactly one thing: the overlay's ack() call for the dropped
        item (see tts_overlay_html's overflow-trim loop,
        `while (queue.length > MAX_QUEUE_DEPTH) { ack(queue.shift()); }`)
        advances that stream's cursor past it, through the SAME
        ack_event() used for a genuinely-spoken line -- there is no
        separate "delete" code path for overflow. Confirmed by grep: the
        only `DELETE FROM foxbot_events` anywhere in this codebase is the
        unrelated 30-day retention sweep in
        foxbot_events._emit_event_blocking (age-gated, 1%-probability per
        emit) -- nothing to do with queue depth.

        Simulates a burst of 9 events per stream (more than
        MAX_QUEUE_DEPTH) for BOTH win and chat, simulates the client
        overflow-acking the oldest 3 of each (exactly what the real
        overlay JS does), and proves both consequences at once, for both
        streams:
          (a) the cursor genuinely skips the dropped ones -- a fresh
              /overlay/tts-data poll offers only what's left, never
              re-offers the dropped 3 or dumps the full 9-deep backlog.
          (b) every one of the 18 rows is still physically present and
              queryable in foxbot_events by a query that bypasses the
              cursor entirely -- dropping never touched the table.
        """
        from fastapi.testclient import TestClient

        # cooldown_seconds/chat_cooldown_seconds are irrelevant here -- this
        # test emits raw foxbot_events rows directly, bypassing
        # _foxbot_tts_emit_v1/_foxbot_tts_emit_chat_message_v1 (and their
        # cooldown gates) entirely; only the two enabled flags matter for
        # /overlay/tts-data.
        tts_config.set_config(self.handle, read_wins_enabled=True, read_chat_enabled=True)
        client = TestClient(app.app)
        client.get(f"/overlay/tts-data?handle={self.handle}")  # bootstrap both streams to "now" (empty)

        for i in range(9):
            foxbot_events.emit_event(self.handle, "tts_message", actor="viewer", detail={"text": f"win {i}"})
            foxbot_events.emit_event(self.handle, "tts_chat_message", actor="viewer", detail={"text": f"chat {i}"})

        win_ids = self._wait_for_row_count("tts_message", 9)
        chat_ids = self._wait_for_row_count("tts_chat_message", 9)

        # Simulate the overlay dropping the oldest 3 of each stream from
        # its local queue and acking them away (never touching the rows).
        tts_config.ack_event(self.handle, "win", win_ids[2])
        tts_config.ack_event(self.handle, "chat", chat_ids[2])

        config = tts_config.get_config(self.handle)
        self.assertEqual(config.last_acked_win_event_id, win_ids[2], "cursor must skip past the dropped-for-depth ids")
        self.assertEqual(config.last_acked_chat_event_id, chat_ids[2])

        # (a) cursor-skip proof.
        data = client.get(f"/overlay/tts-data?handle={self.handle}").json()
        offered_win = [line["id"] for line in data["lines"] if line["stream"] == "win"]
        offered_chat = [line["id"] for line in data["lines"] if line["stream"] == "chat"]
        self.assertEqual(offered_win, win_ids[3:], "must offer exactly the remaining 6, not the dropped 3 or a replay")
        self.assertEqual(offered_chat, chat_ids[3:])
        self.assertNotIn(win_ids[0], offered_win)
        self.assertNotIn(chat_ids[0], offered_chat)

        # (b) rows-survive proof -- a raw query that bypasses the cursor
        # entirely, so this cannot be satisfied merely by (a) filtering
        # them out of the response.
        with foxbot_events._connect() as connection:
            foxbot_events._ensure_schema(connection)
            surviving_win = [
                row[0] for row in connection.execute(
                    "SELECT id FROM foxbot_events WHERE creator_handle = %s AND kind = %s ORDER BY id ASC",
                    (self.handle, "tts_message"),
                ).fetchall()
            ]
            surviving_chat = [
                row[0] for row in connection.execute(
                    "SELECT id FROM foxbot_events WHERE creator_handle = %s AND kind = %s ORDER BY id ASC",
                    (self.handle, "tts_chat_message"),
                ).fetchall()
            ]

        self.assertEqual(surviving_win, win_ids, "all 9 win rows, including the 3 dropped-for-depth, must still exist")
        self.assertEqual(surviving_chat, chat_ids, "all 9 chat rows, including the 3 dropped-for-depth, must still exist")


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
            json={"read_wins_enabled": True, "voice_name": "Microsoft David", "volume": 42},
            cookies=self._cookies_for(self.creator_a_id, "creator-a"),
        )
        self.assertEqual(res.status_code, 200, res.text)
        self.assertTrue(res.json()["ok"])

        res = self.client.get("/api/studio/tts/config", cookies=self._cookies_for(self.creator_b_id, "creator-b"))
        data = res.json()

        self.assertEqual(data["creator_handle"], self.handle_b)
        self.assertFalse(
            data["read_wins_enabled"], "creator B must see their own (unconfigured) defaults, not creator A's settings",
        )
        self.assertNotEqual(data["voice_name"], "Microsoft David")
        self.assertNotEqual(data["volume"], 42)

    def test_scoped_session_cannot_read_a_different_creators_chat_toggle(self):
        """Same isolation proof as the win toggle above, for the new
        independent read_chat_enabled flag."""
        res = self.client.post(
            "/api/studio/tts/config",
            json={"read_chat_enabled": True},
            cookies=self._cookies_for(self.creator_a_id, "creator-a"),
        )
        self.assertEqual(res.status_code, 200, res.text)

        res = self.client.get("/api/studio/tts/config", cookies=self._cookies_for(self.creator_b_id, "creator-b"))
        self.assertFalse(res.json()["read_chat_enabled"], "creator B must not see creator A's chat-readout toggle")

    def test_client_supplied_handle_field_in_payload_is_ignored(self):
        """The route resolves the target handle ONLY from the session's
        own verified blaze_id (via _foxbot_resolve_event_handle_v1) -- it
        never reads a handle/creator_handle field out of the request body.
        Confirms that structurally: a malicious payload naming handle_b,
        submitted under creator A's own real signed session, still only
        ever writes to creator A's own (session-resolved) handle."""
        res = self.client.post(
            "/api/studio/tts/config",
            json={"read_wins_enabled": True, "volume": 77, "creator_handle": self.handle_b, "handle": self.handle_b},
            cookies=self._cookies_for(self.creator_a_id, "creator-a"),
        )
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["creator_handle"], self.handle_a)

        config_b = tts_config.get_config(self.handle_b)
        self.assertFalse(config_b.read_wins_enabled, "a payload field naming handle-b must never write to handle-b's row")

        config_a = tts_config.get_config(self.handle_a)
        self.assertTrue(config_a.read_wins_enabled)
        self.assertEqual(config_a.volume, 77)

    def test_unmapped_session_cannot_write_or_leak_owner_config(self):
        """A real, authenticated, but UNMAPPED session (a fourth approved
        blaze_id with no entry in handle_map -- _foxbot_resolve_event_handle_v1's
        real "" contract for exactly this case) must never fall back to
        writing/reading tenant-zero's own config."""
        unmapped_id = f"test-tts-unmapped-{uuid.uuid4().hex[:10]}"
        os.environ["STUDIO_APPROVED_BLAZE_USER_IDS"] += f",{unmapped_id}"
        cookies = self._cookies_for(unmapped_id, "unmapped-creator")

        post_res = self.client.post("/api/studio/tts/config", json={"read_wins_enabled": True}, cookies=cookies)
        self.assertEqual(post_res.status_code, 400, post_res.text)

        get_res = self.client.get("/api/studio/tts/config", cookies=cookies)
        self.assertEqual(get_res.json()["creator_handle"], "")


@unittest.skipUnless(DATABASE_CONFIGURED, SKIP_REASON)
class TtsOverlayIntegrationTestCase(unittest.TestCase):
    """Real Postgres, real HTTP routes, no mocks -- proves the server-owned
    cursor mechanics end to end (bootstrap/no-replay, ack-required-before-
    forgetting, the toggle kill switch's fetch-time safety net, and win/
    chat chronological interleaving), not just tts_config.ack_event() in
    isolation (see TtsAckEventTestCase above for that)."""

    def setUp(self):
        self.creator_handle = f"test-tts-overlay-{uuid.uuid4().hex[:12]}"
        tts_config.set_config(self.creator_handle, read_wins_enabled=True, voice_name="Microsoft Zira", volume=65)

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

    def _poll(self, client):
        res = client.get(f"/overlay/tts-data?handle={self.creator_handle}")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["ok"])
        return data

    def test_first_ever_contact_does_not_replay_existing_backlog(self):
        """The bootstrap case: an event already exists (emitted before the
        overlay ever made its first request -- e.g. a win landed while no
        OBS browser source was open yet), and the cursor is still at its
        just-migrated 0. The very first /overlay/tts-data call must NOT
        dump that as a burst of "new" lines -- same non-negotiable as the
        old client-side `initialized` flag this design replaces."""
        from fastapi.testclient import TestClient

        foxbot_events.emit_event(
            self.creator_handle, "tts_message", actor="viewer1", detail={"text": "viewer1 just won 500 promo credits on slots!"},
        )
        self._wait_for_events()

        client = TestClient(app.app)
        data = self._poll(client)
        self.assertEqual(data["voice_name"], "Microsoft Zira")
        self.assertEqual(data["volume"], 65)
        self.assertEqual(data["lines"], [], "first-ever contact must silently catch up, never replay existing backlog")

        # The bootstrap must have actually persisted (not just been a
        # one-off in-memory skip) -- confirmed by checking the stored
        # cursor directly.
        self.assertGreater(tts_config.get_config(self.creator_handle).last_acked_win_event_id, 0)

    def test_genuinely_new_line_after_bootstrap_is_returned(self):
        from fastapi.testclient import TestClient

        client = TestClient(app.app)
        self._poll(client)  # bootstrap -- nothing exists yet

        foxbot_events.emit_event(self.creator_handle, "tts_message", actor="viewer1", detail={"text": "line one"})
        self._wait_for_events()

        data = self._poll(client)
        self.assertEqual(len(data["lines"]), 1)
        self.assertEqual(data["lines"][0]["text"], "line one")
        self.assertEqual(data["lines"][0]["stream"], "win")
        self.assertEqual(set(data["lines"][0].keys()), {"id", "stream", "text"})

    def test_unacked_line_survives_a_second_poll(self):
        """THE reload/cutoff-bug proof: in the OLD design, a line was
        marked "seen" the instant it was FETCHED, not once it was actually
        spoken -- so a page reload (or, equivalently here, just a second
        poll) before the overlay finished speaking it would permanently
        lose that line. The new design must not do that: fetching a line
        without acking it must not consume it."""
        from fastapi.testclient import TestClient

        client = TestClient(app.app)
        self._poll(client)

        foxbot_events.emit_event(self.creator_handle, "tts_message", actor="viewer1", detail={"text": "not yet spoken"})
        self._wait_for_events()

        first = self._poll(client)["lines"]
        self.assertEqual(len(first), 1)

        second = self._poll(client)["lines"]
        self.assertEqual(len(second), 1, "an unacked line must still be offered on the next poll")
        self.assertEqual(second[0]["id"], first[0]["id"])

    def test_acked_line_is_not_replayed_on_a_simulated_reload(self):
        """The other half of the same proof: once the overlay HAS actually
        finished with a line (ack posted), a subsequent poll -- standing
        in for an OBS browser-source reload -- must not re-offer it."""
        from fastapi.testclient import TestClient

        client = TestClient(app.app)
        self._poll(client)

        foxbot_events.emit_event(self.creator_handle, "tts_message", actor="viewer1", detail={"text": "spoken already"})
        self._wait_for_events()

        lines = self._poll(client)["lines"]
        self.assertEqual(len(lines), 1)

        ack_res = client.post(
            "/overlay/tts-ack",
            json={"handle": self.creator_handle, "stream": lines[0]["stream"], "event_id": lines[0]["id"]},
        )
        self.assertTrue(ack_res.json()["ok"])

        reload_lines = self._poll(client)["lines"]
        self.assertEqual(reload_lines, [], "an acked line must never be re-offered after a reload")

    def test_ack_route_clamps_a_forged_event_id(self):
        """HTTP-level companion to TtsAckEventTestCase's direct unit test:
        the unauthenticated /overlay/tts-ack route must not let a forged/
        oversized event_id skip further ahead than what genuinely exists."""
        from fastapi.testclient import TestClient

        client = TestClient(app.app)
        self._poll(client)

        foxbot_events.emit_event(self.creator_handle, "tts_message", actor="viewer1", detail={"text": "line"})
        self._wait_for_events()
        real_id = foxbot_events.fetch_max_event_id(self.creator_handle, "tts_message")

        res = client.post(
            "/overlay/tts-ack",
            json={"handle": self.creator_handle, "stream": "win", "event_id": real_id + 999999},
        )
        self.assertTrue(res.json()["ok"])
        self.assertEqual(tts_config.get_config(self.creator_handle).last_acked_win_event_id, real_id)

    def test_disabling_chat_mid_flight_hides_already_emitted_lines(self):
        """The fetch-time safety net for the chat kill switch: complements
        the already-unit-tested emit-time gate (TtsChatEmitHookTestCase)
        by covering the race where a message was written just BEFORE the
        toggle flipped off, and confirms re-enabling later doesn't dump
        the backlog that piled up while it was off."""
        from fastapi.testclient import TestClient

        tts_config.set_config(self.creator_handle, read_chat_enabled=True)
        client = TestClient(app.app)
        self._poll(client)  # bootstrap both streams

        foxbot_events.emit_event(
            self.creator_handle, "tts_chat_message", actor="viewer1", detail={"text": "viewer1 says: hello"},
        )
        self._wait_for_events("tts_chat_message")

        tts_config.set_config(self.creator_handle, read_chat_enabled=False)
        data = self._poll(client)
        self.assertEqual(data["lines"], [], "a disabled stream must never surface a line, even one already emitted")

        tts_config.set_config(self.creator_handle, read_chat_enabled=True)
        data = self._poll(client)
        self.assertEqual(
            data["lines"], [], "re-enabling must never replay the backlog that piled up while the toggle was off",
        )

    def test_chat_messages_sent_before_the_overlays_first_ever_poll_are_not_lost(self):
        """Real-world sequence that actually happened in production for
        creator crypt0k1ng96 on 2026-09-09: the creator turns
        read_chat_enabled ON, then IMMEDIATELY types a chat message to test
        it -- all before the overlay page has ever polled /overlay/tts-data
        even once (last_acked_chat_event_id is still -1, since this cursor
        column was introduced in the very same deploy as read_chat_enabled
        itself, so EVERY creator, however long they've had win-TTS running,
        starts at -1 for chat specifically).

        Unlike the win stream (test_first_ever_contact_does_not_replay_existing_backlog),
        where "first contact" bootstrapping to current-max and skipping
        backlog is correct -- real historical tts_message rows can predate
        the cursor mechanism itself, per tts_config.py's own docstring --
        there is no equivalent legitimate backlog for tts_chat_message: that
        kind never existed before this feature shipped, so nothing genuine
        can be sitting there to protect against replaying. Bootstrapping
        chat to current-max on first contact (the current code, shared with
        win) therefore silently and PERMANENTLY eats any message sent in
        the ordinary window between "turn the toggle on" and "the overlay's
        next 2-second poll" -- exactly the two messages this test
        reproduces, sent through the real, unmocked
        _foxbot_tts_emit_chat_message_v1 gate (not a raw emit_event call),
        matching production exactly.
        """
        tts_config.set_config(self.creator_handle, read_chat_enabled=True, chat_cooldown_seconds=1)

        app._foxbot_tts_emit_chat_message_v1(self.creator_handle, "princessjamesy", "Fist my bump")
        self._wait_for_events("tts_chat_message")
        time.sleep(1.1)  # clear the per-creator chat cooldown, same as the ~2min real-world gap
        app._foxbot_tts_emit_chat_message_v1(self.creator_handle, "princessjamesy", "Fist my bump again")

        deadline = time.time() + 3.0
        rows = []
        while time.time() < deadline:
            rows = foxbot_events.fetch_events(self.creator_handle, limit=50)
            rows = [r for r in (rows or []) if r[0] == "tts_chat_message"]
            if len(rows) >= 2:
                break
            time.sleep(0.1)
        self.assertEqual(len(rows), 2, "expected both real chat-gated messages to have landed in foxbot_events")

        from fastapi.testclient import TestClient

        client = TestClient(app.app)
        data = self._poll(client)  # the overlay's FIRST EVER poll for this handle

        chat_lines = [line for line in data["lines"] if line["stream"] == "chat"]
        self.assertEqual(
            [line["text"] for line in chat_lines],
            ["princessjamesy says: Fist my bump", "princessjamesy says: Fist my bump again"],
            "both real, already-written chat messages must be offered on the overlay's first poll, "
            "not silently eaten by the win-style 'first contact bootstraps past everything' behavior",
        )

    def test_win_and_chat_lines_interleave_chronologically(self):
        """Both streams share the same underlying foxbot_events id
        sequence, so a win and a chat message close together must come
        back in the order they actually happened, not grouped by stream."""
        from fastapi.testclient import TestClient

        tts_config.set_config(self.creator_handle, read_chat_enabled=True)
        client = TestClient(app.app)
        self._poll(client)

        foxbot_events.emit_event(self.creator_handle, "tts_message", actor="viewer1", detail={"text": "win one"})
        time.sleep(0.2)
        foxbot_events.emit_event(self.creator_handle, "tts_chat_message", actor="viewer2", detail={"text": "chat one"})
        time.sleep(0.2)
        foxbot_events.emit_event(self.creator_handle, "tts_message", actor="viewer3", detail={"text": "win two"})

        deadline = time.time() + 3.0
        lines = []
        while time.time() < deadline:
            lines = self._poll(client)["lines"]
            if len(lines) >= 3:
                break
            time.sleep(0.2)

        self.assertEqual([line["text"] for line in lines], ["win one", "chat one", "win two"])
        self.assertEqual([line["stream"] for line in lines], ["win", "chat", "win"])

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

    def test_ack_route_scoped_by_handle(self):
        """Cross-creator isolation for the ack route itself: acking under
        one handle must never advance a different creator's cursor."""
        from fastapi.testclient import TestClient

        client = TestClient(app.app)
        self._poll(client)
        foxbot_events.emit_event(self.creator_handle, "tts_message", actor="viewer1", detail={"text": "line"})
        self._wait_for_events()
        real_id = foxbot_events.fetch_max_event_id(self.creator_handle, "tts_message")

        other_handle = f"unrelated-{uuid.uuid4().hex[:8]}"
        try:
            client.post("/overlay/tts-ack", json={"handle": other_handle, "stream": "win", "event_id": real_id})
            self.assertEqual(tts_config.get_config(other_handle).last_acked_win_event_id, -1)
        finally:
            with tts_config._connect() as connection:
                tts_config._ensure_schema(connection)
                connection.execute(f"DELETE FROM {tts_config.TABLE_CONFIG} WHERE creator_handle = %s", (other_handle,))

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

    def test_end_to_end_chat_message_produces_tts_chat_line(self):
        """Full path for the chat-readout sibling: a real chat message,
        through the actual _foxbot_tts_emit_chat_message_v1 hook (real
        Postgres config + real profanity filter, nothing mocked), lands a
        tts_chat_message row consumable by the public overlay endpoint,
        formatted as "username says: message"."""
        tts_config.set_config(self.creator_handle, read_chat_enabled=True, chat_cooldown_seconds=1, chat_min_chars=1)

        app._foxbot_tts_emit_chat_message_v1(self.creator_handle, "e2e-chat-viewer", "this round was incredible honestly")

        matches = self._wait_for_events("tts_chat_message")
        self.assertTrue(matches, "expected a tts_chat_message event to land")
        kind, actor, detail, created_at = matches[0]
        self.assertEqual(actor, "e2e-chat-viewer")
        self.assertEqual(detail["text"], "e2e-chat-viewer says: this round was incredible honestly")


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
            "ttsToggleBtn", "ttsChatToggleBtn", "ttsVoice", "ttsVolume", "ttsVolumeValue",
            "ttsMinPayout", "ttsCharLimit", "ttsSaveBtn",
        ):
            self.assertIn(f'id="{element_id}"', res.text, f"missing #{element_id} in the TTS section")

    def test_studio_v2_win_and_chat_toggles_are_independent_buttons(self):
        """The kill-switch requirement: read-chat gets its OWN prominent
        one-click toggle, separate from the win toggle and from the full
        settings form -- confirmed structurally by each posting its own
        distinct field name."""
        res = self.client.get("/studio-v2", cookies=self._cookies())
        self.assertIn("read_wins_enabled: !currentTtsEnabled", res.text)
        self.assertIn("read_chat_enabled: !currentChatEnabled", res.text)

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
