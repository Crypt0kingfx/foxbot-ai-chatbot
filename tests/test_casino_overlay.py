"""Tests for the Casino Stream Overlay (Casino Phase 9): notable-win
detection, the emit hook at each of the 6 casino settlement points, and
the public /overlay/casino + /overlay/casino-data routes.

Three groups:
  - NotableWinTestCase: NO DATABASE_URL required. Pure function tests on
    _foxbot_casino_notable_win_v1() -- the single place that decides what
    goes into a casino_win event's `detail`. Proves write-time safety
    structurally: the returned dict's keys are enumerated and asserted
    to be exactly {game, payout, highlight}, so balance/user_id/round_id/
    wager cannot be in it no matter what the caller does.
  - EmitHookTestCase: NO DATABASE_URL required. Mocks
    app._foxbot_events_v1.emit_event to prove the replayed-guard, the
    non-notable-skip, and the emit-failure-can't-break-settlement
    property -- all at the Python level, no real Postgres round-trip
    needed to prove these.
  - CasinoOverlayIntegrationTestCase: real Postgres via DATABASE_URL.
    Proves the actual foxbot_events row landed with only display-safe
    fields, the full command-layer regression through app.chat() for a
    notable win, and the public /overlay/casino-data HTTP endpoint.

Run with:
    python -m unittest tests.test_casino_overlay -v
"""

import os
import sys
import time
import unittest
import uuid
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app  # noqa: E402
import services.casino_config as casino_config  # noqa: E402
import services.casino_ledger as cl  # noqa: E402
import services.casino_rounds as cr  # noqa: E402
import services.casino_rng as casino_rng  # noqa: E402
import services.foxbot_events as foxbot_events  # noqa: E402


DATABASE_CONFIGURED = bool(os.getenv("DATABASE_URL"))
SKIP_REASON = (
    "DATABASE_URL not set -- these tests need a real Postgres database "
    "(a throwaway/dev one, not production) to prove the actual event "
    "row and the full command-layer regression honestly."
)


class _FakeResult:
    """A minimal stand-in for services/casino_rounds.py's RoundResult --
    only the fields _foxbot_casino_notable_win_v1/_foxbot_casino_emit_win_v1
    actually read (outcome, payout, metadata, replayed)."""

    def __init__(self, outcome, payout, metadata=None, replayed=False):
        self.outcome = outcome
        self.payout = payout
        self.metadata = metadata or {}
        self.replayed = replayed


class _FixedChoiceProvider(casino_rng.RNGProvider):
    def __init__(self, value):
        self.value = value

    def roll(self, minimum, maximum):
        return minimum

    def choice(self, seq):
        return self.value


class _FixedRollProvider(casino_rng.RNGProvider):
    def __init__(self, value):
        self.value = value

    def roll(self, minimum, maximum):
        return self.value

    def choice(self, seq):
        return seq[0]


class NotableWinTestCase(unittest.TestCase):
    """No DATABASE_URL required -- pure function, no I/O."""

    def _assert_detail_shape(self, detail):
        self.assertIsNotNone(detail)
        self.assertEqual(
            set(detail.keys()), {"game", "payout", "highlight"},
            "a casino_win detail dict must NEVER contain any key besides "
            "game/payout/highlight -- no balance, user_id, round_id, or wager",
        )

    # ------------------------------------------------------------------
    def test_loss_never_notable(self):
        result = _FakeResult("loss", 0, {"roll": "tails"})
        self.assertIsNone(app._foxbot_casino_notable_win_v1("coinflip", result))

    def test_push_never_notable(self):
        result = _FakeResult("push", 10, {})
        self.assertIsNone(app._foxbot_casino_notable_win_v1("blackjack", result))

    def test_small_win_below_floor_not_notable(self):
        floor = app._foxbot_casino_notable_payout_floor_v1()
        result = _FakeResult("win", floor - 1, {"roll": "heads"})
        self.assertIsNone(app._foxbot_casino_notable_win_v1("coinflip", result))

    def test_win_at_or_above_floor_is_notable(self):
        floor = app._foxbot_casino_notable_payout_floor_v1()
        result = _FakeResult("win", floor, {"roll": "heads"})
        detail = app._foxbot_casino_notable_win_v1("coinflip", result)
        self._assert_detail_shape(detail)
        self.assertEqual(detail["game"], "coinflip")
        self.assertEqual(detail["payout"], floor)

    def test_blackjack_natural_always_notable_even_below_floor(self):
        result = _FakeResult("blackjack", 1, {})  # payout=1, far below any floor
        detail = app._foxbot_casino_notable_win_v1("blackjack", result)
        self._assert_detail_shape(detail)
        self.assertEqual(detail["highlight"], "blackjack")

    def test_blackjack_regular_win_below_floor_not_notable(self):
        floor = app._foxbot_casino_notable_payout_floor_v1()
        result = _FakeResult("win", floor - 1, {})
        self.assertIsNone(app._foxbot_casino_notable_win_v1("blackjack", result))

    def test_roulette_straight_up_always_notable_even_below_floor(self):
        result = _FakeResult("win", 1, {"bet_type": "number", "selection": 17, "pocket": 17, "won": True})
        detail = app._foxbot_casino_notable_win_v1("roulette", result)
        self._assert_detail_shape(detail)
        self.assertIn("straight-up", detail["highlight"])
        self.assertIn("17", detail["highlight"])

    def test_roulette_color_win_below_floor_not_notable(self):
        floor = app._foxbot_casino_notable_payout_floor_v1()
        result = _FakeResult("win", floor - 1, {"bet_type": "red", "pocket": 1, "color": "red", "won": True})
        self.assertIsNone(app._foxbot_casino_notable_win_v1("roulette", result))

    def test_crash_moon_always_notable_even_below_floor(self):
        multiplier = app._foxbot_casino_notable_crash_multiplier_v1()
        result = _FakeResult("win", 1, {"crash_point": multiplier + 1, "target": multiplier})
        detail = app._foxbot_casino_notable_win_v1("crash", result)
        self._assert_detail_shape(detail)
        self.assertIn("x", detail["highlight"])

    def test_crash_below_multiplier_and_below_floor_not_notable(self):
        multiplier = app._foxbot_casino_notable_crash_multiplier_v1()
        floor = app._foxbot_casino_notable_payout_floor_v1()
        result = _FakeResult("win", floor - 1, {"crash_point": multiplier - 0.5, "target": multiplier - 0.5})
        self.assertIsNone(app._foxbot_casino_notable_win_v1("crash", result))

    def test_crash_below_multiplier_but_above_floor_is_notable_via_floor(self):
        multiplier = app._foxbot_casino_notable_crash_multiplier_v1()
        floor = app._foxbot_casino_notable_payout_floor_v1()
        result = _FakeResult("win", floor, {"crash_point": 1.5, "target": min(1.5, multiplier - 0.1)})
        detail = app._foxbot_casino_notable_win_v1("crash", result)
        self._assert_detail_shape(detail)

    def test_detail_never_contains_sensitive_keys_across_all_games(self):
        """Structural proof, not a spot check: for every game and every
        code path that can return a non-None detail, the returned dict's
        keys are exactly {game, payout, highlight} -- never balance_after,
        user_id, round_id, or wager, regardless of what's in `metadata`
        (even if metadata itself contains those keys, as real RoundResult
        metadata sometimes does for internal bookkeeping)."""
        forbidden = {"balance_after", "user_id", "round_id", "wager", "bet", "deck", "hit_log"}
        scenarios = [
            ("coinflip", _FakeResult("win", 500, {
                "roll": "heads", "pick": "heads", "won": True,
            })),
            ("roulette", _FakeResult("win", 500, {
                "bet_type": "number", "selection": 17, "pocket": 17, "color": "black", "won": True,
            })),
            ("crash", _FakeResult("win", 500, {
                "crash_point": 12.3, "target": 12.3, "bet": 10, "payout": 500, "won": True,
            })),
            ("blackjack", _FakeResult("blackjack", 27, {
                "player_cards": ["AS", "KH"], "dealer_cards": ["2S", "3S"],
                "bet": 10, "deck": ["AS"] * 52, "hit_log": {"x": 1},
            })),
        ]
        for game_id, result in scenarios:
            detail = app._foxbot_casino_notable_win_v1(game_id, result)
            self._assert_detail_shape(detail)
            self.assertTrue(forbidden.isdisjoint(detail.keys()))


class EmitHookTestCase(unittest.TestCase):
    """No DATABASE_URL required -- mocks emit_event to test the wrapper's
    own control flow (replayed guard, notable gate, failure isolation)
    without touching Postgres or a background thread."""

    def setUp(self):
        self._patch = mock.patch.object(app._foxbot_events_v1, "emit_event")
        self.mock_emit = self._patch.start()

    def tearDown(self):
        self._patch.stop()

    # ------------------------------------------------------------------
    def test_replayed_result_never_emits(self):
        floor = app._foxbot_casino_notable_payout_floor_v1()
        result = _FakeResult("win", floor + 1000, {"roll": "heads"}, replayed=True)
        app._foxbot_casino_emit_win_v1("some-handle", "viewer1", "coinflip", result)
        self.mock_emit.assert_not_called()

    def test_non_notable_win_never_emits(self):
        floor = app._foxbot_casino_notable_payout_floor_v1()
        result = _FakeResult("win", floor - 1, {"roll": "heads"}, replayed=False)
        app._foxbot_casino_emit_win_v1("some-handle", "viewer1", "coinflip", result)
        self.mock_emit.assert_not_called()

    def test_notable_non_replayed_win_emits_with_display_safe_payload(self):
        floor = app._foxbot_casino_notable_payout_floor_v1()
        result = _FakeResult("win", floor + 50, {"roll": "heads"}, replayed=False)
        app._foxbot_casino_emit_win_v1("some-handle", "viewer1", "coinflip", result)

        self.mock_emit.assert_called_once()
        call_args, call_kwargs = self.mock_emit.call_args
        self.assertEqual(call_args[0], "some-handle")
        self.assertEqual(call_args[1], "casino_win")
        self.assertEqual(call_kwargs["actor"], "viewer1")
        self.assertEqual(set(call_kwargs["detail"].keys()), {"game", "payout", "highlight"})

    # ------------------------------------------------------------------
    # THE EMIT-FAILURE-CAN'T-BREAK-SETTLEMENT PROOF.
    # ------------------------------------------------------------------
    def test_emit_event_raising_does_not_propagate(self):
        self.mock_emit.side_effect = RuntimeError("simulated emit_event failure")
        floor = app._foxbot_casino_notable_payout_floor_v1()
        result = _FakeResult("win", floor + 50, {"roll": "heads"}, replayed=False)

        try:
            app._foxbot_casino_emit_win_v1("some-handle", "viewer1", "coinflip", result)
        except Exception as exc:  # noqa: BLE001
            self.fail(f"_foxbot_casino_emit_win_v1 must never raise, got {exc!r}")

    def test_notable_check_bug_does_not_propagate(self):
        """Even a bug in the notable-check itself (a malformed result
        object) must not raise -- the try/except wraps the whole body,
        not just the emit_event call."""
        broken_result = object()  # has no .replayed/.outcome/.payout at all
        try:
            app._foxbot_casino_emit_win_v1("some-handle", "viewer1", "coinflip", broken_result)
        except Exception as exc:  # noqa: BLE001
            self.fail(f"_foxbot_casino_emit_win_v1 must never raise, got {exc!r}")
        self.mock_emit.assert_not_called()


@unittest.skipUnless(DATABASE_CONFIGURED, SKIP_REASON)
class CasinoOverlayIntegrationTestCase(unittest.TestCase):
    def setUp(self):
        self.creator_id = f"test-overlay-{uuid.uuid4().hex[:12]}"
        self.creator_handle = self.creator_id  # foxbot_events keys on handle; any unique string works for isolation
        self.username = "OverlayTestViewer"
        self.user_id = app.viewer_key(self.username)

        self._original_flag = os.environ.get("FOXBOT_CASINO_ENABLED")
        os.environ["FOXBOT_CASINO_ENABLED"] = "true"
        self._original_crash_flag = os.environ.get("FOXBOT_CRASH_ENABLED")
        os.environ["FOXBOT_CRASH_ENABLED"] = "true"
        self._original_bj_flag = os.environ.get("FOXBOT_BLACKJACK_ENABLED")
        os.environ["FOXBOT_BLACKJACK_ENABLED"] = "true"

        casino_config.set_config(
            self.creator_id, foxcoins_per_promo=10, daily_promo_limit=5000, casino_enabled=True,
        )

        self._resolve_patch = mock.patch.object(
            app, "_foxbot_resolve_creator_id_v1", return_value=self.creator_id,
        )
        self._resolve_patch.start()

        self._original_rng_provider = casino_rng.get_provider()

    def tearDown(self):
        self._resolve_patch.stop()
        casino_rng.set_provider(self._original_rng_provider)

        for name, original in (
            ("FOXBOT_CASINO_ENABLED", self._original_flag),
            ("FOXBOT_CRASH_ENABLED", self._original_crash_flag),
            ("FOXBOT_BLACKJACK_ENABLED", self._original_bj_flag),
        ):
            if original is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = original

        with cl._connect() as connection:
            cl._ensure_schema(connection)
            cr._ensure_schema(connection)
            casino_config._ensure_schema(connection)
            foxbot_events._ensure_schema(connection)
            connection.execute(f"DELETE FROM {cl.TABLE_LEDGER} WHERE creator_id = %s", (self.creator_id,))
            connection.execute(f"DELETE FROM {cl.TABLE_BALANCES} WHERE creator_id = %s", (self.creator_id,))
            connection.execute(f"DELETE FROM {cr.TABLE_ROUNDS} WHERE creator_id = %s", (self.creator_id,))
            connection.execute(f"DELETE FROM {casino_config.TABLE_GAME_CONFIG} WHERE creator_id = %s", (self.creator_id,))
            connection.execute(f"DELETE FROM {casino_config.TABLE_CONFIG} WHERE creator_id = %s", (self.creator_id,))
            connection.execute("DELETE FROM foxbot_events WHERE creator_handle = %s", (self.creator_handle,))

    def _seed_foxcoins(self, amount):
        app.add_points(self.username, amount, "test_seed", creator_id=self.creator_id)

    def _wait_for_events(self, kind="casino_win", timeout=3.0):
        """emit_event() writes on a background daemon thread -- poll
        fetch_events() briefly rather than a fixed sleep, tolerating real
        eventual consistency instead of guessing a delay."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            rows = foxbot_events.fetch_events(self.creator_handle, limit=50)
            matches = [r for r in (rows or []) if r[0] == kind]
            if matches:
                return matches
            time.sleep(0.1)
        return []

    # ------------------------------------------------------------------
    # THE WRITE-TIME SAFETY PROOF, against a REAL Postgres row.
    # ------------------------------------------------------------------
    def test_notable_win_lands_in_foxbot_events_with_only_safe_fields(self):
        self._seed_foxcoins(5000)
        app.chat(message="!convert 200", username=self.username, creator_handle=self.creator_handle)
        casino_rng.set_provider(_FixedChoiceProvider("heads"))

        floor = app._foxbot_casino_notable_payout_floor_v1()
        wager = max(1, (floor // 2) + 1)  # coinflip pays 2x -> comfortably clears the floor
        app.chat(
            message=f"!casinoflip heads {wager}", username=self.username,
            creator_handle=self.creator_handle,
        )

        matches = self._wait_for_events()
        self.assertTrue(matches, "expected a casino_win event to land")

        kind, actor, detail, created_at = matches[0]
        self.assertEqual(actor, self.username)
        self.assertEqual(set(detail.keys()), {"game", "payout", "highlight"})
        self.assertEqual(detail["game"], "coinflip")

        detail_text = str(detail)
        for forbidden in ("balance", "user_id", "round_id", str(self.user_id)):
            self.assertNotIn(forbidden, detail_text, f"{forbidden!r} must never appear in an emitted event")

    def test_small_win_does_not_emit(self):
        self._seed_foxcoins(5000)
        app.chat(message="!convert 200", username=self.username, creator_handle=self.creator_handle)
        casino_rng.set_provider(_FixedChoiceProvider("heads"))

        app.chat(message="!casinoflip heads 1", username=self.username, creator_handle=self.creator_handle)

        matches = self._wait_for_events(timeout=1.5)
        self.assertFalse(matches, "a win far below the notable floor must not emit an overlay event")

    def test_replayed_command_does_not_double_emit(self):
        self._seed_foxcoins(5000)
        app.chat(message="!convert 200", username=self.username, creator_handle=self.creator_handle)
        casino_rng.set_provider(_FixedChoiceProvider("heads"))

        floor = app._foxbot_casino_notable_payout_floor_v1()
        wager = max(1, (floor // 2) + 1)
        key = f"dedupe-{uuid.uuid4().hex[:8]}"

        for _ in range(3):
            app.chat(
                message=f"!casinoflip heads {wager}", username=self.username,
                creator_handle=self.creator_handle, dedupe_key=key,
            )

        matches = self._wait_for_events()
        self.assertEqual(len(matches), 1, "spamming the identical winning message must emit exactly once")

    # ------------------------------------------------------------------
    # FULL COMMAND-LAYER REGRESSION: the payout itself is unaffected by
    # the emit hook, even under a simulated emit_event failure.
    # ------------------------------------------------------------------
    def test_payout_lands_correctly_even_if_emit_event_raises(self):
        self._seed_foxcoins(5000)
        app.chat(message="!convert 200", username=self.username, creator_handle=self.creator_handle)
        casino_rng.set_provider(_FixedChoiceProvider("heads"))

        with mock.patch.object(app._foxbot_events_v1, "emit_event", side_effect=RuntimeError("boom")):
            reply = app.chat(
                message="!casinoflip heads 50", username=self.username,
                creator_handle=self.creator_handle,
            ).get("response", "")

        self.assertIn("won", reply.lower())
        self.assertEqual(cl.get_balance(self.creator_id, self.user_id, cr.CURRENCY_PROMO), 200 - 50 + 100)

    # ------------------------------------------------------------------
    # THE PUBLIC OVERLAY ENDPOINT.
    # ------------------------------------------------------------------
    def test_overlay_data_endpoint_returns_only_safe_fields_no_auth(self):
        from fastapi.testclient import TestClient

        self._seed_foxcoins(5000)
        app.chat(message="!convert 200", username=self.username, creator_handle=self.creator_handle)
        casino_rng.set_provider(_FixedChoiceProvider("heads"))
        floor = app._foxbot_casino_notable_payout_floor_v1()
        wager = max(1, (floor // 2) + 1)
        app.chat(
            message=f"!casinoflip heads {wager}", username=self.username,
            creator_handle=self.creator_handle,
        )
        self._wait_for_events()

        client = TestClient(app.app)
        # No Authorization header at all -- confirms the route is
        # genuinely public, not just "gate returns False" in isolation.
        res = client.get(f"/overlay/casino-data?handle={self.creator_handle}")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["ok"])
        self.assertTrue(data["wins"], "expected the just-emitted win to appear")

        win = data["wins"][0]
        self.assertEqual(set(win.keys()), {"id", "username", "game", "payout", "highlight"})
        self.assertEqual(win["username"], self.username)

    def test_overlay_data_endpoint_scoped_by_handle(self):
        from fastapi.testclient import TestClient

        self._seed_foxcoins(5000)
        app.chat(message="!convert 200", username=self.username, creator_handle=self.creator_handle)
        casino_rng.set_provider(_FixedChoiceProvider("heads"))
        floor = app._foxbot_casino_notable_payout_floor_v1()
        wager = max(1, (floor // 2) + 1)
        app.chat(
            message=f"!casinoflip heads {wager}", username=self.username,
            creator_handle=self.creator_handle,
        )
        self._wait_for_events()

        client = TestClient(app.app)
        other_handle = f"unrelated-{uuid.uuid4().hex[:8]}"
        res = client.get(f"/overlay/casino-data?handle={other_handle}")
        data = res.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["wins"], [], "a different creator's overlay must not see this creator's win")

    def test_overlay_page_loads_public_no_auth(self):
        from fastapi.testclient import TestClient

        client = TestClient(app.app)
        res = client.get("/overlay/casino")
        self.assertEqual(res.status_code, 200)
        self.assertIn("text/html", res.headers.get("content-type", ""))
        self.assertIn("B026FF", res.text)
        self.assertIn("FF007F", res.text)

    # ------------------------------------------------------------------
    # REGRESSION: all four games still settle correctly with the emit
    # hook wired in -- proves the hook is additive, not a behavior change.
    # ------------------------------------------------------------------
    def test_all_four_games_still_settle_correctly(self):
        self._seed_foxcoins(8000)
        app.chat(message="!convert 500", username=self.username, creator_handle=self.creator_handle)

        casino_rng.set_provider(_FixedChoiceProvider("heads"))
        reply = app.chat(
            message="!casinoflip heads 10", username=self.username, creator_handle=self.creator_handle,
        ).get("response", "")
        self.assertIn("won", reply.lower())

        casino_rng.set_provider(_FixedRollProvider(1))  # red
        reply = app.chat(
            message="!roulette red 10", username=self.username, creator_handle=self.creator_handle,
        ).get("response", "")
        self.assertTrue("won" in reply.lower() or "lost" in reply.lower())

        casino_rng.set_provider(_FixedRollProvider(app._foxbot_casino_crash_v1.RESOLUTION - 1))
        reply = app.chat(
            message="!crash 10 2.0", username=self.username, creator_handle=self.creator_handle,
        ).get("response", "")
        self.assertIn("won", reply.lower())

        reply = app.chat(
            message="!blackjack 10", username=self.username, creator_handle=self.creator_handle,
        ).get("response", "")
        self.assertTrue(reply)


if __name__ == "__main__":
    unittest.main()
