"""Tests for the three Casino Studio Tab admin features built on top of
the proven casino stack: Feature 1 (live wins feed, read-only), Feature 2
(test alert, provably money-free), Feature 3 (play from dashboard --
coinflip/roulette/crash only, blackjack deferred).

All against a real Postgres (local Docker) via DATABASE_URL, using
FastAPI's TestClient to exercise the actual HTTP routes -- not just the
underlying game functions, which are already proven elsewhere in this
suite. The point of these tests is the WIRING: does the new route
actually enforce the admin gate, actually ignore a payload creator_id,
actually turn a repeated idempotency_key into a replay instead of a
second wager.

Run with:
    python -m unittest tests.test_casino_dashboard_features -v
"""

import os
import sys
import unittest
import uuid
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app  # noqa: E402
import games.blackjack as bj  # noqa: E402
import services.casino_config as casino_config  # noqa: E402
import services.casino_ledger as cl  # noqa: E402
import services.casino_rounds as cr  # noqa: E402
import services.casino_rng as casino_rng  # noqa: E402
import services.foxbot_events as foxbot_events  # noqa: E402
import providers.promo as promo  # noqa: E402


def _deck_with_prefix(*cards):
    """A full, valid 52-card deck whose first N cards are exactly `cards`
    (in order) -- same helper as tests/test_blackjack.py, duplicated here
    (not imported cross-file, consistent with this suite's existing
    per-file fixture convention) so dashboard tests can engineer specific
    hands deterministically."""
    full = [rank + suit for suit in bj.SUITS for rank in bj.RANKS]
    rest = [c for c in full if c not in cards]
    return list(cards) + rest


DATABASE_CONFIGURED = bool(os.getenv("DATABASE_URL"))
SKIP_REASON = (
    "DATABASE_URL not set -- these tests need a real Postgres database "
    "(a throwaway/dev one, not production) to prove the actual HTTP "
    "wiring, idempotency, and scoping honestly."
)

STUDIO_ADMIN_USER = os.getenv("STUDIO_ADMIN_USER", "")
STUDIO_ADMIN_PASSWORD = os.getenv("STUDIO_ADMIN_PASSWORD", "")
ADMIN_AUTH_CONFIGURED = bool(STUDIO_ADMIN_USER and STUDIO_ADMIN_PASSWORD)


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


@unittest.skipUnless(DATABASE_CONFIGURED, SKIP_REASON)
@unittest.skipUnless(ADMIN_AUTH_CONFIGURED, "STUDIO_ADMIN_USER/PASSWORD not set in this environment.")
class CasinoDashboardFeaturesTestCase(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient

        self.client = TestClient(app.app)
        self.auth = (STUDIO_ADMIN_USER, STUDIO_ADMIN_PASSWORD)

        self.creator_id = f"test-dash-{uuid.uuid4().hex[:12]}"
        self.creator_handle = self.creator_id
        self.username = app._FOXBOT_DASHBOARD_PLAY_USERNAME
        self.user_id = app.viewer_key(self.username)

        self._original_flag = os.environ.get("FOXBOT_CASINO_ENABLED")
        os.environ["FOXBOT_CASINO_ENABLED"] = "true"

        self._original_slots_flag = os.environ.get("FOXBOT_SLOTS_ENABLED")
        os.environ["FOXBOT_SLOTS_ENABLED"] = "true"

        self._original_dice_flag = os.environ.get("FOXBOT_DICE_ENABLED")
        os.environ["FOXBOT_DICE_ENABLED"] = "true"

        self._original_blackjack_flag = os.environ.get("FOXBOT_BLACKJACK_ENABLED")
        os.environ["FOXBOT_BLACKJACK_ENABLED"] = "true"

        casino_config.set_config(
            self.creator_id, foxcoins_per_promo=10, daily_promo_limit=5000, casino_enabled=True,
        )

        self._resolve_id_patch = mock.patch.object(
            app, "_foxbot_resolve_creator_id_v1", return_value=self.creator_id,
        )
        self._resolve_id_patch.start()
        self._resolve_handle_patch = mock.patch.object(
            app, "_foxbot_resolve_event_handle_v1", return_value=self.creator_handle,
        )
        self._resolve_handle_patch.start()

        self._original_rng_provider = casino_rng.get_provider()

    def tearDown(self):
        self._resolve_id_patch.stop()
        self._resolve_handle_patch.stop()
        casino_rng.set_provider(self._original_rng_provider)

        if self._original_flag is None:
            os.environ.pop("FOXBOT_CASINO_ENABLED", None)
        else:
            os.environ["FOXBOT_CASINO_ENABLED"] = self._original_flag

        if self._original_slots_flag is None:
            os.environ.pop("FOXBOT_SLOTS_ENABLED", None)
        else:
            os.environ["FOXBOT_SLOTS_ENABLED"] = self._original_slots_flag

        if self._original_dice_flag is None:
            os.environ.pop("FOXBOT_DICE_ENABLED", None)
        else:
            os.environ["FOXBOT_DICE_ENABLED"] = self._original_dice_flag

        if self._original_blackjack_flag is None:
            os.environ.pop("FOXBOT_BLACKJACK_ENABLED", None)
        else:
            os.environ["FOXBOT_BLACKJACK_ENABLED"] = self._original_blackjack_flag

        with cl._connect() as connection:
            cl._ensure_schema(connection)
            cr._ensure_schema(connection)
            casino_config._ensure_schema(connection)
            foxbot_events._ensure_schema(connection)
            bj._ensure_schema(connection)
            connection.execute(f"DELETE FROM {cl.TABLE_LEDGER} WHERE creator_id = %s", (self.creator_id,))
            connection.execute(f"DELETE FROM {cl.TABLE_BALANCES} WHERE creator_id = %s", (self.creator_id,))
            connection.execute(f"DELETE FROM {cr.TABLE_ROUNDS} WHERE creator_id = %s", (self.creator_id,))
            connection.execute(f"DELETE FROM {casino_config.TABLE_GAME_CONFIG} WHERE creator_id = %s", (self.creator_id,))
            connection.execute(f"DELETE FROM {casino_config.TABLE_CONFIG} WHERE creator_id = %s", (self.creator_id,))
            connection.execute("DELETE FROM foxbot_events WHERE creator_handle = %s", (self.creator_handle,))
            connection.execute(f"DELETE FROM {bj.TABLE_ACTIVE_HANDS} WHERE creator_id = %s", (self.creator_id,))
            promo._ensure_schema(connection)
            connection.execute(f"DELETE FROM {promo.TABLE_ATTEMPTS} WHERE creator_id = %s", (self.creator_id,))

        if getattr(self, "_deck_patch", None) is not None:
            self._deck_patch.stop()
            self._deck_patch = None

    def _fund_promo(self, amount):
        cl.credit(
            self.creator_id, self.user_id, cr.CURRENCY_PROMO, amount, cl.PROMO_CONVERT_IN,
            idempotency_key=f"{self.creator_id}-fund-{uuid.uuid4().hex[:8]}",
        )

    def _promo_balance(self):
        return cl.get_balance(self.creator_id, self.user_id, cr.CURRENCY_PROMO)

    def _seed_foxcoins(self, amount):
        app.add_points(self.username, amount, "test_seed", creator_id=self.creator_id)

    def _foxcoin_balance(self):
        return app.get_balance(self.username, creator_id=self.creator_id)

    def _money_table_counts(self):
        with cl._connect() as connection:
            cl._ensure_schema(connection)
            cr._ensure_schema(connection)
            ledger = connection.execute(f"SELECT COUNT(*) FROM {cl.TABLE_LEDGER}").fetchone()[0]
            balances = connection.execute(f"SELECT COUNT(*) FROM {cl.TABLE_BALANCES}").fetchone()[0]
            rounds = connection.execute(f"SELECT COUNT(*) FROM {cr.TABLE_ROUNDS}").fetchone()[0]
        return (ledger, balances, rounds)

    def _with_deck(self, *prefix_cards):
        """Forces the NEXT deal() call (chat-side OR dashboard-side --
        both go through the same bj.deal()) to use a deck starting with
        the given cards. Stopped in tearDown."""
        deck = _deck_with_prefix(*prefix_cards)
        self._deck_patch = mock.patch.object(bj, "_build_shuffled_deck", return_value=deck)
        self._deck_patch.start()
        return deck

    def _active_hand_count(self):
        with cl._connect() as connection:
            bj._ensure_schema(connection)
            return connection.execute(
                f"SELECT COUNT(*) FROM {bj.TABLE_ACTIVE_HANDS} WHERE creator_id = %s", (self.creator_id,),
            ).fetchone()[0]

    # ------------------------------------------------------------------
    # ADMIN GATE: unauthenticated -> rejected, on all 5 new routes.
    # ------------------------------------------------------------------
    def test_all_five_routes_reject_unauthenticated(self):
        cases = [
            ("GET", "/api/studio/casino/wins", None),
            ("POST", "/api/studio/casino/test-alert", {}),
            ("POST", "/api/studio/casino/play/coinflip", {"pick": "heads", "wager": 10, "idempotency_key": "x"}),
            ("POST", "/api/studio/casino/play/roulette", {"bet_type": "red", "wager": 10, "idempotency_key": "x"}),
            ("POST", "/api/studio/casino/play/crash", {"wager": 10, "target": "2.0", "idempotency_key": "x"}),
            ("POST", "/api/studio/casino/convert", {"amount": 10, "idempotency_key": "x"}),
        ]
        for method, path, body in cases:
            if method == "GET":
                res = self.client.get(path)
            else:
                res = self.client.post(path, json=body)
            self.assertEqual(res.status_code, 401, f"{method} {path} must reject unauthenticated requests")

    # ------------------------------------------------------------------
    # FEATURE 2: THE MONEY-FREE PROOF.
    # ------------------------------------------------------------------
    def test_test_alert_moves_zero_money_globally(self):
        before = self._money_table_counts()

        res = self.client.post("/api/studio/casino/test-alert", json={}, auth=self.auth)
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["ok"])

        after = self._money_table_counts()
        self.assertEqual(
            before, after,
            "firing a test alert must not add a single row to casino_ledger, "
            "casino_balances, or casino_rounds -- globally, for any creator",
        )

    def test_test_alert_creates_an_unmistakably_test_event(self):
        res = self.client.post("/api/studio/casino/test-alert", json={}, auth=self.auth)
        self.assertEqual(res.status_code, 200)

        import time
        deadline = time.time() + 3.0
        matches = []
        while time.time() < deadline:
            rows = foxbot_events.fetch_events(self.creator_handle, limit=10)
            matches = [r for r in (rows or []) if r[0] == "casino_win" and r[1] == "TEST"]
            if matches:
                break
            time.sleep(0.1)
        self.assertTrue(matches, "expected a casino_win event with actor='TEST'")

        _, actor, detail, _ = matches[0]
        self.assertEqual(actor, "TEST")
        self.assertEqual(detail["game"], "test")
        self.assertIn("TEST", detail["highlight"])

    # ------------------------------------------------------------------
    # FEATURE 3: THE ENDPOINT-LEVEL IDEMPOTENCY PROOF.
    # ------------------------------------------------------------------
    def test_play_coinflip_same_idempotency_key_twice_plays_once(self):
        self._fund_promo(1000)
        casino_rng.set_provider(_FixedChoiceProvider("heads"))
        key = str(uuid.uuid4())
        payload = {"pick": "heads", "wager": 10, "idempotency_key": key}

        first = self.client.post("/api/studio/casino/play/coinflip", json=payload, auth=self.auth)
        second = self.client.post("/api/studio/casino/play/coinflip", json=payload, auth=self.auth)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        first_data, second_data = first.json(), second.json()

        self.assertFalse(first_data["replayed"])
        self.assertTrue(second_data["replayed"], "a repeated idempotency_key must come back replayed=True")
        self.assertEqual(first_data["outcome"], second_data["outcome"])
        self.assertEqual(first_data["payout"], second_data["payout"])
        self.assertEqual(first_data["balance_after"], second_data["balance_after"])

        self.assertEqual(self._promo_balance(), 1000 - 10 + 20, "must have wagered exactly once, not twice")

        with cl._connect() as connection:
            wager_rows = connection.execute(
                f"SELECT COUNT(*) FROM {cl.TABLE_LEDGER} WHERE round_id = %s AND type = %s",
                (f"dashboard:coinflip:{key}", cl.PROMO_WAGER),
            ).fetchone()[0]
            round_rows = connection.execute(
                f"SELECT COUNT(*) FROM {cr.TABLE_ROUNDS} WHERE round_id = %s",
                (f"dashboard:coinflip:{key}",),
            ).fetchone()[0]
        self.assertEqual(wager_rows, 1, "exactly one wager ledger row for this round_id")
        self.assertEqual(round_rows, 1, "exactly one round row for this round_id")

    def test_play_roulette_same_idempotency_key_twice_plays_once(self):
        self._fund_promo(1000)
        casino_rng.set_provider(_FixedChoiceProvider("heads"))  # roulette uses roll(), but harmless here
        key = str(uuid.uuid4())
        payload = {"bet_type": "red", "wager": 10, "idempotency_key": key}

        first = self.client.post("/api/studio/casino/play/roulette", json=payload, auth=self.auth)
        second = self.client.post("/api/studio/casino/play/roulette", json=payload, auth=self.auth)

        self.assertFalse(first.json()["replayed"])
        self.assertTrue(second.json()["replayed"])
        self.assertEqual(first.json()["balance_after"], second.json()["balance_after"])

        with cl._connect() as connection:
            wager_rows = connection.execute(
                f"SELECT COUNT(*) FROM {cl.TABLE_LEDGER} WHERE round_id = %s AND type = %s",
                (f"dashboard:roulette:{key}", cl.PROMO_WAGER),
            ).fetchone()[0]
        self.assertEqual(wager_rows, 1)

    def test_play_crash_same_idempotency_key_twice_plays_once(self):
        self._fund_promo(1000)
        key = str(uuid.uuid4())
        payload = {"wager": 10, "target": "2.0", "idempotency_key": key}

        first = self.client.post("/api/studio/casino/play/crash", json=payload, auth=self.auth)
        second = self.client.post("/api/studio/casino/play/crash", json=payload, auth=self.auth)

        self.assertFalse(first.json()["replayed"])
        self.assertTrue(second.json()["replayed"])
        self.assertEqual(first.json()["balance_after"], second.json()["balance_after"])

        with cl._connect() as connection:
            wager_rows = connection.execute(
                f"SELECT COUNT(*) FROM {cl.TABLE_LEDGER} WHERE round_id = %s AND type = %s",
                (f"dashboard:crash:{key}", cl.PROMO_WAGER),
            ).fetchone()[0]
        self.assertEqual(wager_rows, 1)

    def test_missing_idempotency_key_rejected_no_round_created(self):
        self._fund_promo(1000)
        res = self.client.post(
            "/api/studio/casino/play/coinflip", json={"pick": "heads", "wager": 10}, auth=self.auth,
        )
        self.assertEqual(res.status_code, 400)
        self.assertFalse(res.json()["ok"])
        self.assertEqual(self._promo_balance(), 1000, "a rejected request must not move any promo")

    # ------------------------------------------------------------------
    # FEATURE 3: SCOPING -- a payload creator_id is ignored.
    # ------------------------------------------------------------------
    def test_play_ignores_payload_creator_id_uses_session_scope(self):
        self._fund_promo(1000)
        casino_rng.set_provider(_FixedChoiceProvider("heads"))
        other_creator_id = f"other-{uuid.uuid4().hex[:8]}"

        res = self.client.post(
            "/api/studio/casino/play/coinflip",
            json={
                "pick": "heads", "wager": 10, "idempotency_key": str(uuid.uuid4()),
                "creator_id": other_creator_id,  # must be silently ignored
            },
            auth=self.auth,
        )
        self.assertEqual(res.status_code, 200)

        # The wager landed on the SESSION-resolved creator (self.creator_id
        # via the mock), never on the payload-supplied other_creator_id.
        self.assertEqual(self._promo_balance(), 1000 - 10 + 20)
        other_balance = cl.get_balance(other_creator_id, self.user_id, cr.CURRENCY_PROMO)
        self.assertEqual(other_balance, 0, "a payload creator_id must not redirect the wager to another balance")

    # ------------------------------------------------------------------
    # FEATURE 3: routes through the exact same proven function -- a
    # dashboard win settles identically to a chat win (same math).
    # ------------------------------------------------------------------
    def test_play_coinflip_settles_identically_to_chat_command(self):
        self._fund_promo(1000)
        casino_rng.set_provider(_FixedChoiceProvider("heads"))

        res = self.client.post(
            "/api/studio/casino/play/coinflip",
            json={"pick": "heads", "wager": 10, "idempotency_key": str(uuid.uuid4())},
            auth=self.auth,
        )
        data = res.json()

        # Same payout math test_casinoflip_command_win already proves for
        # the chat path: 1:1 payout, i.e. wager*2 total return.
        self.assertEqual(data["outcome"], "win")
        self.assertEqual(data["payout"], 20)
        self.assertEqual(data["balance_after"], 1000 - 10 + 20)

    def test_play_notable_win_fires_the_overlay_event(self):
        """A dashboard-played win big enough to be notable calls the exact
        same _foxbot_casino_emit_win_v1 hook a chat win does."""
        self._fund_promo(10000)
        casino_rng.set_provider(_FixedChoiceProvider("heads"))
        floor = app._foxbot_casino_notable_payout_floor_v1()
        wager = max(1, (floor // 2) + 1)  # coinflip pays 2x -> comfortably clears the floor

        self.client.post(
            "/api/studio/casino/play/coinflip",
            json={"pick": "heads", "wager": wager, "idempotency_key": str(uuid.uuid4())},
            auth=self.auth,
        )

        import time
        deadline = time.time() + 3.0
        matches = []
        while time.time() < deadline:
            rows = foxbot_events.fetch_events(self.creator_handle, limit=10)
            matches = [r for r in (rows or []) if r[0] == "casino_win" and r[1] == app._FOXBOT_DASHBOARD_PLAY_USERNAME]
            if matches:
                break
            time.sleep(0.1)
        self.assertTrue(matches, "a notable dashboard win must fire the same overlay event a chat win would")

    # ------------------------------------------------------------------
    # FEATURE 1: wins feed.
    # ------------------------------------------------------------------
    def test_wins_feed_returns_display_safe_fields(self):
        self._fund_promo(10000)
        casino_rng.set_provider(_FixedChoiceProvider("heads"))
        floor = app._foxbot_casino_notable_payout_floor_v1()
        wager = max(1, (floor // 2) + 1)

        self.client.post(
            "/api/studio/casino/play/coinflip",
            json={"pick": "heads", "wager": wager, "idempotency_key": str(uuid.uuid4())},
            auth=self.auth,
        )

        import time
        time.sleep(0.5)

        res = self.client.get("/api/studio/casino/wins", auth=self.auth)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["ok"])
        self.assertTrue(data["wins"])
        win = data["wins"][0]
        self.assertEqual(set(win.keys()), {"username", "game", "payout", "highlight", "created_at", "age_seconds"})

    # ------------------------------------------------------------------
    # FEATURE 4 (CONVERT): thin wrapper over the proven deposit().
    # ------------------------------------------------------------------
    def test_convert_routes_through_proven_deposit(self):
        self._seed_foxcoins(1000)

        res = self.client.post(
            "/api/studio/casino/convert",
            json={"amount": 10, "idempotency_key": str(uuid.uuid4())},
            auth=self.auth,
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["ok"])
        self.assertFalse(data["replayed"])
        self.assertEqual(data["foxcoin_cost"], 100)  # 10 promo * rate 10
        self.assertEqual(data["promo_amount"], 10)
        self.assertEqual(data["promo_balance"], 10)

        self.assertEqual(self._promo_balance(), 10)
        self.assertEqual(self._foxcoin_balance(), 1000 - 100)

    def test_convert_same_idempotency_key_twice_converts_once(self):
        self._seed_foxcoins(1000)
        key = str(uuid.uuid4())
        payload = {"amount": 10, "idempotency_key": key}

        first = self.client.post("/api/studio/casino/convert", json=payload, auth=self.auth)
        second = self.client.post("/api/studio/casino/convert", json=payload, auth=self.auth)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        first_data, second_data = first.json(), second.json()

        self.assertFalse(first_data["replayed"])
        self.assertTrue(second_data["replayed"], "a repeated idempotency_key must come back replayed=True")
        self.assertEqual(first_data["promo_balance"], second_data["promo_balance"])

        # No double-debit, no double-credit -- checked against real balances,
        # not just the JSON response.
        self.assertEqual(self._promo_balance(), 10, "must have converted exactly once, not twice")
        self.assertEqual(self._foxcoin_balance(), 1000 - 100, "FoxCoins must be debited exactly once")

        with cl._connect() as connection:
            attempt_rows = connection.execute(
                f"SELECT COUNT(*) FROM {promo.TABLE_ATTEMPTS} WHERE idempotency_key = %s",
                (f"dashboard-convert:{key}",),
            ).fetchone()[0]
            ledger_rows = connection.execute(
                f"SELECT COUNT(*) FROM {cl.TABLE_LEDGER} WHERE creator_id = %s AND user_id = %s "
                f"AND currency = %s AND type = %s",
                (self.creator_id, self.user_id, cr.CURRENCY_PROMO, cl.PROMO_CONVERT_IN),
            ).fetchone()[0]
        self.assertEqual(attempt_rows, 1, "exactly one conversion-attempt row for this idempotency_key")
        self.assertEqual(ledger_rows, 1, "exactly one PROMO_CONVERT_IN ledger row -- no double-credit")

    def test_convert_ignores_payload_creator_id(self):
        self._seed_foxcoins(1000)
        other_creator_id = f"other-convert-{uuid.uuid4().hex[:8]}"

        res = self.client.post(
            "/api/studio/casino/convert",
            json={"amount": 10, "idempotency_key": str(uuid.uuid4()), "creator_id": other_creator_id},
            auth=self.auth,
        )
        self.assertEqual(res.status_code, 200)

        self.assertEqual(self._promo_balance(), 10)
        other_balance = cl.get_balance(other_creator_id, self.user_id, cr.CURRENCY_PROMO)
        self.assertEqual(other_balance, 0, "a payload creator_id must not redirect the conversion elsewhere")

    def test_convert_insufficient_foxcoins_clean_rejection(self):
        # No FoxCoins seeded at all.
        res = self.client.post(
            "/api/studio/casino/convert",
            json={"amount": 10, "idempotency_key": str(uuid.uuid4())},
            auth=self.auth,
        )
        self.assertEqual(res.status_code, 400)
        data = res.json()
        self.assertFalse(data["ok"])
        self.assertIn("100", data["error"])  # the FoxCoin cost, same detail !convert's own message shows

        self.assertEqual(self._promo_balance(), 0, "a rejected conversion must not credit any promo")

    def test_convert_daily_limit_enforced(self):
        self._seed_foxcoins(100000)
        casino_config.set_config(self.creator_id, daily_promo_limit=5)  # inherited straight from deposit()

        res = self.client.post(
            "/api/studio/casino/convert",
            json={"amount": 10, "idempotency_key": str(uuid.uuid4())},  # exceeds the 5-promo daily limit
            auth=self.auth,
        )
        self.assertEqual(res.status_code, 400)
        data = res.json()
        self.assertFalse(data["ok"])
        self.assertIn("limit", data["error"].lower())
        self.assertEqual(self._promo_balance(), 0, "a limit-rejected conversion must not credit any promo")

    def test_convert_missing_idempotency_key_rejected(self):
        self._seed_foxcoins(1000)
        res = self.client.post("/api/studio/casino/convert", json={"amount": 10}, auth=self.auth)
        self.assertEqual(res.status_code, 400)
        self.assertFalse(res.json()["ok"])
        self.assertEqual(self._promo_balance(), 0)

    # ------------------------------------------------------------------
    # FEATURE 5: dashboard plays also post to Blaze chat.
    # ------------------------------------------------------------------
    def test_dashboard_play_posts_the_exact_chat_reply_text(self):
        self._fund_promo(1000)
        casino_rng.set_provider(_FixedChoiceProvider("heads"))

        with mock.patch(
            "services.blaze_native_connector._foxbot_live_send_chat_v2",
            return_value={"ok": True, "sent": True},
        ) as mock_send:
            res = self.client.post(
                "/api/studio/casino/play/coinflip",
                json={"pick": "heads", "wager": 10, "idempotency_key": str(uuid.uuid4())},
                auth=self.auth,
            )
        self.assertEqual(res.status_code, 200)

        mock_send.assert_called_once()
        (posted_text,), _ = mock_send.call_args
        # The route calls _foxbot_coinflip_reply_v1 directly (confirmed by
        # reading app.py) -- the SAME function chat()'s !casinoflip block
        # calls, so checking the posted text has the right shape/content
        # here is checking the shared helper's real output, not a copy.
        self.assertTrue(posted_text.startswith("🪙"))
        self.assertIn("HEADS", posted_text)
        self.assertIn("won 20 promo", posted_text)
        self.assertIn("Balance: 1010 promo", posted_text)
        self.assertIn("crypt0k1ng96", posted_text)  # the dashboard's own username, same identity as real chat

    def test_dashboard_play_settles_even_if_chat_post_fails(self):
        """THE POST-SETTLEMENT SIDE-EFFECT PROOF: a send failure must not
        break the play -- the round already settled (via play_round(),
        untouched) before this side-effect ever runs."""
        self._fund_promo(1000)
        casino_rng.set_provider(_FixedChoiceProvider("heads"))

        with mock.patch(
            "services.blaze_native_connector._foxbot_live_send_chat_v2",
            side_effect=RuntimeError("simulated Blaze API failure"),
        ):
            res = self.client.post(
                "/api/studio/casino/play/coinflip",
                json={"pick": "heads", "wager": 10, "idempotency_key": str(uuid.uuid4())},
                auth=self.auth,
            )

        self.assertEqual(res.status_code, 200, "a chat-post failure must not surface as an endpoint error")
        data = res.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["outcome"], "win")
        self.assertEqual(data["payout"], 20)
        self.assertEqual(self._promo_balance(), 1000 - 10 + 20, "the payout must land regardless of the send failure")

    def test_replayed_dashboard_play_does_not_repost_to_chat(self):
        """No double-post on replay: same idempotency_key twice ->
        _foxbot_live_send_chat_v2 called exactly once, not twice."""
        self._fund_promo(1000)
        casino_rng.set_provider(_FixedChoiceProvider("heads"))
        key = str(uuid.uuid4())
        payload = {"pick": "heads", "wager": 10, "idempotency_key": key}

        with mock.patch(
            "services.blaze_native_connector._foxbot_live_send_chat_v2",
            return_value={"ok": True, "sent": True},
        ) as mock_send:
            first = self.client.post("/api/studio/casino/play/coinflip", json=payload, auth=self.auth)
            second = self.client.post("/api/studio/casino/play/coinflip", json=payload, auth=self.auth)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertFalse(first.json()["replayed"])
        self.assertTrue(second.json()["replayed"])
        mock_send.assert_called_once()

    def test_chat_post_fires_with_no_open_transaction(self):
        """STRUCTURAL PROOF (not just code inspection): at the exact
        moment the chat-post fires, query Postgres's own pg_stat_activity
        for any connection idle-in-transaction. If play_round() (or
        anything upstream) had left a connection/transaction open across
        this call, it would show up here as a real, measurable fact
        about the database's state -- not an inference from reading
        Python source."""
        self._fund_promo(1000)
        casino_rng.set_provider(_FixedChoiceProvider("heads"))

        observed_idle_in_transaction = []

        def capture_send(message):
            with cl._connect() as check_connection:
                count = check_connection.execute(
                    "SELECT count(*) FROM pg_stat_activity WHERE state = 'idle in transaction'"
                ).fetchone()[0]
            observed_idle_in_transaction.append(count)
            return {"ok": True, "sent": True}

        with mock.patch(
            "services.blaze_native_connector._foxbot_live_send_chat_v2", side_effect=capture_send,
        ) as mock_send:
            res = self.client.post(
                "/api/studio/casino/play/coinflip",
                json={"pick": "heads", "wager": 10, "idempotency_key": str(uuid.uuid4())},
                auth=self.auth,
            )

        self.assertEqual(res.status_code, 200)
        mock_send.assert_called_once()
        self.assertEqual(
            observed_idle_in_transaction, [0],
            "no connection should be idle-in-transaction at the moment the chat post fires",
        )

    def test_overlay_still_fires_alongside_the_chat_post(self):
        """(d) Confirms the overlay emit is unaffected by the new
        chat-post side-effect sitting next to it -- both fire from the
        same genuine (non-replayed) settle."""
        self._fund_promo(10000)
        casino_rng.set_provider(_FixedChoiceProvider("heads"))
        floor = app._foxbot_casino_notable_payout_floor_v1()
        wager = max(1, (floor // 2) + 1)

        with mock.patch(
            "services.blaze_native_connector._foxbot_live_send_chat_v2",
            return_value={"ok": True, "sent": True},
        ) as mock_send:
            res = self.client.post(
                "/api/studio/casino/play/coinflip",
                json={"pick": "heads", "wager": wager, "idempotency_key": str(uuid.uuid4())},
                auth=self.auth,
            )
        self.assertEqual(res.status_code, 200)
        mock_send.assert_called_once()

        import time
        deadline = time.time() + 3.0
        matches = []
        while time.time() < deadline:
            rows = foxbot_events.fetch_events(self.creator_handle, limit=10)
            matches = [r for r in (rows or []) if r[0] == "casino_win" and r[1] == app._FOXBOT_DASHBOARD_PLAY_USERNAME]
            if matches:
                break
            time.sleep(0.1)
        self.assertTrue(matches, "the overlay event must still fire alongside the new chat-post side-effect")

    def test_roulette_and_crash_also_post_to_chat(self):
        self._fund_promo(1000)

        casino_rng.set_provider(_FixedChoiceProvider("heads"))  # roulette uses roll(), harmless here
        with mock.patch(
            "services.blaze_native_connector._foxbot_live_send_chat_v2",
            return_value={"ok": True, "sent": True},
        ) as mock_send:
            self.client.post(
                "/api/studio/casino/play/roulette",
                json={"bet_type": "red", "wager": 10, "idempotency_key": str(uuid.uuid4())},
                auth=self.auth,
            )
        mock_send.assert_called_once()
        (posted_text,), _ = mock_send.call_args
        self.assertIn("🎡", posted_text)

        with mock.patch(
            "services.blaze_native_connector._foxbot_live_send_chat_v2",
            return_value={"ok": True, "sent": True},
        ) as mock_send:
            self.client.post(
                "/api/studio/casino/play/crash",
                json={"wager": 10, "target": "2.0", "idempotency_key": str(uuid.uuid4())},
                auth=self.auth,
            )
        mock_send.assert_called_once()
        (posted_text,), _ = mock_send.call_args
        self.assertTrue(posted_text.startswith("🚀") or posted_text.startswith("💥"))

    # ------------------------------------------------------------------
    # FEATURE 6: play/slots, play/dice -- identical pattern to
    # play/coinflip, play/roulette, play/crash above.
    # ------------------------------------------------------------------
    def test_play_slots_routes_through_proven_play_slots(self):
        self._fund_promo(1000)
        casino_rng.set_provider(_FixedChoiceProvider("fox"))  # triple fox -> jackpot

        res = self.client.post(
            "/api/studio/casino/play/slots",
            json={"wager": 10, "idempotency_key": str(uuid.uuid4())},
            auth=self.auth,
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["outcome"], "win")
        self.assertEqual(data["payout"], 26500)  # 10 * 2650, games/slots.py's own TRIPLE_PAYOUT["fox"]
        self.assertEqual(data["highlight"]["combo"], "triple_fox")
        self.assertEqual(self._promo_balance(), 1000 - 10 + 26500)

    def test_play_slots_same_idempotency_key_twice_plays_once(self):
        self._fund_promo(1000)
        casino_rng.set_provider(_FixedChoiceProvider("purple"))  # triple purple, small win
        key = str(uuid.uuid4())
        payload = {"wager": 10, "idempotency_key": key}

        first = self.client.post("/api/studio/casino/play/slots", json=payload, auth=self.auth)
        second = self.client.post("/api/studio/casino/play/slots", json=payload, auth=self.auth)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertFalse(first.json()["replayed"])
        self.assertTrue(second.json()["replayed"], "a repeated idempotency_key must come back replayed=True")
        self.assertEqual(first.json()["balance_after"], second.json()["balance_after"])

        with cl._connect() as connection:
            wager_rows = connection.execute(
                f"SELECT COUNT(*) FROM {cl.TABLE_LEDGER} WHERE round_id = %s AND type = %s",
                (f"dashboard:slots:{key}", cl.PROMO_WAGER),
            ).fetchone()[0]
            round_rows = connection.execute(
                f"SELECT COUNT(*) FROM {cr.TABLE_ROUNDS} WHERE round_id = %s", (f"dashboard:slots:{key}",),
            ).fetchone()[0]
        self.assertEqual(wager_rows, 1, "exactly one wager -- no double-debit")
        self.assertEqual(round_rows, 1, "exactly one round -- no double-spin")

    def test_play_slots_ignores_payload_creator_id(self):
        self._fund_promo(1000)
        casino_rng.set_provider(_FixedChoiceProvider("purple"))
        other_creator_id = f"other-slots-{uuid.uuid4().hex[:8]}"

        res = self.client.post(
            "/api/studio/casino/play/slots",
            json={"wager": 10, "idempotency_key": str(uuid.uuid4()), "creator_id": other_creator_id},
            auth=self.auth,
        )
        self.assertEqual(res.status_code, 200)
        other_balance = cl.get_balance(other_creator_id, self.user_id, cr.CURRENCY_PROMO)
        self.assertEqual(other_balance, 0, "a payload creator_id must not redirect the spin elsewhere")

    def test_play_slots_posts_to_chat_and_settles_even_if_send_fails(self):
        self._fund_promo(1000)
        casino_rng.set_provider(_FixedChoiceProvider("purple"))

        with mock.patch(
            "services.blaze_native_connector._foxbot_live_send_chat_v2",
            side_effect=RuntimeError("simulated Blaze API failure"),
        ):
            res = self.client.post(
                "/api/studio/casino/play/slots",
                json={"wager": 10, "idempotency_key": str(uuid.uuid4())},
                auth=self.auth,
            )
        self.assertEqual(res.status_code, 200, "a chat-post failure must not surface as an endpoint error")
        self.assertTrue(res.json()["ok"])
        self.assertEqual(self._promo_balance(), 1000 - 10 + 20)

    def test_play_slots_jackpot_fires_the_overlay_event(self):
        self._fund_promo(1000)
        casino_rng.set_provider(_FixedChoiceProvider("fox"))

        with mock.patch(
            "services.blaze_native_connector._foxbot_live_send_chat_v2",
            return_value={"ok": True, "sent": True},
        ):
            self.client.post(
                "/api/studio/casino/play/slots",
                json={"wager": 10, "idempotency_key": str(uuid.uuid4())},
                auth=self.auth,
            )

        import time
        deadline = time.time() + 3.0
        matches = []
        while time.time() < deadline:
            rows = foxbot_events.fetch_events(self.creator_handle, limit=10)
            matches = [r for r in (rows or []) if r[0] == "casino_win" and r[1] == app._FOXBOT_DASHBOARD_PLAY_USERNAME]
            if matches:
                break
            time.sleep(0.1)
        self.assertTrue(matches, "a slots jackpot must fire the same overlay event a chat win would")

    def test_play_slots_disabled_flag_returns_clean_error(self):
        self._fund_promo(1000)
        os.environ.pop("FOXBOT_SLOTS_ENABLED", None)

        res = self.client.post(
            "/api/studio/casino/play/slots",
            json={"wager": 10, "idempotency_key": str(uuid.uuid4())},
            auth=self.auth,
        )
        self.assertEqual(res.status_code, 404)
        self.assertFalse(res.json()["ok"])
        self.assertEqual(self._promo_balance(), 1000, "no wager must move when the game is dormant")

    # ------------------------------------------------------------------
    def test_play_dice_high_low_and_exact_number_all_work(self):
        self._fund_promo(1000)

        casino_rng.set_provider(_FixedRollProvider(6))
        res_high = self.client.post(
            "/api/studio/casino/play/dice",
            json={"prediction": "high", "wager": 10, "idempotency_key": str(uuid.uuid4())},
            auth=self.auth,
        )
        self.assertEqual(res_high.status_code, 200)
        data_high = res_high.json()
        self.assertEqual(data_high["outcome"], "win")
        self.assertEqual(data_high["payout"], 19)  # (10*194)//100

        casino_rng.set_provider(_FixedRollProvider(1))
        res_low = self.client.post(
            "/api/studio/casino/play/dice",
            json={"prediction": "low", "wager": 10, "idempotency_key": str(uuid.uuid4())},
            auth=self.auth,
        )
        self.assertEqual(res_low.json()["outcome"], "win")

        casino_rng.set_provider(_FixedRollProvider(6))
        res_exact = self.client.post(
            "/api/studio/casino/play/dice",
            json={"prediction": "6", "wager": 10, "idempotency_key": str(uuid.uuid4())},
            auth=self.auth,
        )
        data_exact = res_exact.json()
        self.assertEqual(data_exact["outcome"], "win")
        self.assertEqual(data_exact["payout"], 58)  # (10*582)//100

    def test_play_dice_invalid_prediction_rejected(self):
        self._fund_promo(1000)
        res = self.client.post(
            "/api/studio/casino/play/dice",
            json={"prediction": "sideways", "wager": 10, "idempotency_key": str(uuid.uuid4())},
            auth=self.auth,
        )
        self.assertEqual(res.status_code, 400)
        self.assertFalse(res.json()["ok"])
        self.assertEqual(self._promo_balance(), 1000)

    def test_play_dice_same_idempotency_key_twice_plays_once(self):
        self._fund_promo(1000)
        casino_rng.set_provider(_FixedRollProvider(6))
        key = str(uuid.uuid4())
        payload = {"prediction": "high", "wager": 10, "idempotency_key": key}

        first = self.client.post("/api/studio/casino/play/dice", json=payload, auth=self.auth)
        second = self.client.post("/api/studio/casino/play/dice", json=payload, auth=self.auth)

        self.assertFalse(first.json()["replayed"])
        self.assertTrue(second.json()["replayed"])
        self.assertEqual(first.json()["balance_after"], second.json()["balance_after"])

        with cl._connect() as connection:
            wager_rows = connection.execute(
                f"SELECT COUNT(*) FROM {cl.TABLE_LEDGER} WHERE round_id = %s AND type = %s",
                (f"dashboard:dice:{key}", cl.PROMO_WAGER),
            ).fetchone()[0]
        self.assertEqual(wager_rows, 1, "exactly one wager -- no double-debit")

    def test_play_dice_ignores_payload_creator_id(self):
        self._fund_promo(1000)
        casino_rng.set_provider(_FixedRollProvider(6))
        other_creator_id = f"other-dice-{uuid.uuid4().hex[:8]}"

        res = self.client.post(
            "/api/studio/casino/play/dice",
            json={
                "prediction": "high", "wager": 10, "idempotency_key": str(uuid.uuid4()),
                "creator_id": other_creator_id,
            },
            auth=self.auth,
        )
        self.assertEqual(res.status_code, 200)
        other_balance = cl.get_balance(other_creator_id, self.user_id, cr.CURRENCY_PROMO)
        self.assertEqual(other_balance, 0, "a payload creator_id must not redirect the roll elsewhere")

    def test_play_dice_posts_the_exact_chat_reply_text(self):
        self._fund_promo(1000)
        casino_rng.set_provider(_FixedRollProvider(6))

        with mock.patch(
            "services.blaze_native_connector._foxbot_live_send_chat_v2",
            return_value={"ok": True, "sent": True},
        ) as mock_send:
            self.client.post(
                "/api/studio/casino/play/dice",
                json={"prediction": "high", "wager": 10, "idempotency_key": str(uuid.uuid4())},
                auth=self.auth,
            )
        mock_send.assert_called_once()
        (posted_text,), _ = mock_send.call_args
        self.assertTrue(posted_text.startswith("🎲"))
        self.assertIn("rolled 6", posted_text.lower())

    def test_play_dice_disabled_flag_returns_clean_error(self):
        self._fund_promo(1000)
        os.environ.pop("FOXBOT_DICE_ENABLED", None)

        res = self.client.post(
            "/api/studio/casino/play/dice",
            json={"prediction": "high", "wager": 10, "idempotency_key": str(uuid.uuid4())},
            auth=self.auth,
        )
        self.assertEqual(res.status_code, 404)
        self.assertFalse(res.json()["ok"])
        self.assertEqual(self._promo_balance(), 1000, "no wager must move when the game is dormant")

    def test_play_slots_and_dice_reject_unauthenticated(self):
        cases = [
            ("/api/studio/casino/play/slots", {"wager": 10, "idempotency_key": "x"}),
            ("/api/studio/casino/play/dice", {"prediction": "high", "wager": 10, "idempotency_key": "x"}),
        ]
        for path, body in cases:
            res = self.client.post(path, json=body)
            self.assertEqual(res.status_code, 401, f"POST {path} must reject unauthenticated requests")

    # ------------------------------------------------------------------
    # FEATURE 7: play/blackjack -- interactive deal/hit/stand, thin
    # wrappers over games/blackjack.py's proven deal()/hit()/stand()/
    # get_active_round_id(). games/blackjack.py, services/casino_rounds.py,
    # and casino_active_hands: zero diff -- confirmed separately via
    # `git diff --stat`, not re-proven here.
    # ------------------------------------------------------------------
    def test_blackjack_routes_reject_unauthenticated(self):
        cases = [
            ("GET", "/api/studio/casino/play/blackjack", None),
            ("POST", "/api/studio/casino/play/blackjack/deal", {"bet": 10, "idempotency_key": "x"}),
            ("POST", "/api/studio/casino/play/blackjack/hit", {"idempotency_key": "x"}),
            ("POST", "/api/studio/casino/play/blackjack/stand", {}),
        ]
        for method, path, body in cases:
            res = self.client.get(path) if method == "GET" else self.client.post(path, json=body)
            self.assertEqual(res.status_code, 401, f"{method} {path} must reject unauthenticated requests")

    def test_dashboard_blackjack_disabled_flag_returns_clean_error_on_all_routes(self):
        self._fund_promo(1000)
        os.environ.pop("FOXBOT_BLACKJACK_ENABLED", None)

        self.assertEqual(self.client.get("/api/studio/casino/play/blackjack", auth=self.auth).status_code, 404)

        deal_res = self.client.post(
            "/api/studio/casino/play/blackjack/deal",
            json={"bet": 10, "idempotency_key": str(uuid.uuid4())}, auth=self.auth,
        )
        self.assertEqual(deal_res.status_code, 404)
        self.assertEqual(self._promo_balance(), 1000, "no wager must move when the game is dormant")

        hit_res = self.client.post(
            "/api/studio/casino/play/blackjack/hit",
            json={"idempotency_key": str(uuid.uuid4())}, auth=self.auth,
        )
        self.assertEqual(hit_res.status_code, 404)

        stand_res = self.client.post("/api/studio/casino/play/blackjack/stand", json={}, auth=self.auth)
        self.assertEqual(stand_res.status_code, 404)

    def test_dashboard_blackjack_deal_ignores_payload_creator_id(self):
        self._fund_promo(1000)
        self._with_deck("8S", "7H", "9H", "TD")
        other_creator_id = f"other-bj-{uuid.uuid4().hex[:8]}"

        res = self.client.post(
            "/api/studio/casino/play/blackjack/deal",
            json={"bet": 10, "idempotency_key": str(uuid.uuid4()), "creator_id": other_creator_id},
            auth=self.auth,
        )
        self.assertEqual(res.status_code, 200)
        self.assertIsNone(
            bj.get_active_round_id(other_creator_id, self.user_id),
            "a payload creator_id must not redirect the deal elsewhere",
        )

    # --- THE CROSS-INTERFACE ONE-HAND PROOF -----------------------------
    def test_cross_interface_chat_opened_hand_is_the_same_row_dashboard_sees(self):
        """A hand opened via chat's own code path (bj.deal() called
        directly, with a chat-style round_id -- exactly what chat()'s
        !blackjack block does) must be the SAME casino_active_hands row
        the dashboard's GET/hit endpoints see and act on -- not a second,
        independent hand. A dashboard deal on top of it must be rejected,
        not silently open hand #2."""
        self._fund_promo(1000)
        self._with_deck("7S", "2H", "5D", "9H", "6C")
        chat_round_id = f"blackjack:{uuid.uuid4().hex}"  # chat's own round_id shape

        chat_dealt = bj.deal(self.creator_id, self.user_id, 10, chat_round_id, display_name=self.username)
        self.assertEqual(chat_dealt.state, cr.STATE_FUNDED)
        self.assertEqual(self._active_hand_count(), 1)

        get_res = self.client.get("/api/studio/casino/play/blackjack", auth=self.auth)
        self.assertEqual(get_res.status_code, 200)
        hand = get_res.json()["hand"]
        self.assertIsNotNone(hand, "the dashboard must see the hand chat opened")
        self.assertEqual(hand["player_cards"], ["7S", "5D"])
        self.assertEqual(hand["state"], cr.STATE_FUNDED)

        deal_res = self.client.post(
            "/api/studio/casino/play/blackjack/deal",
            json={"bet": 10, "idempotency_key": str(uuid.uuid4())}, auth=self.auth,
        )
        self.assertEqual(deal_res.status_code, 409, "a second deal while a chat-opened hand is open must be rejected")
        self.assertEqual(self._active_hand_count(), 1, "still exactly one hand row -- no phantom second hand")

        hit_res = self.client.post(
            "/api/studio/casino/play/blackjack/hit",
            json={"idempotency_key": str(uuid.uuid4())}, auth=self.auth,
        )
        self.assertEqual(hit_res.status_code, 200)
        hit_hand = hit_res.json()["hand"]
        self.assertEqual(
            hit_hand["player_cards"], ["7S", "5D", "6C"],
            "the dashboard hit must draw the next card of the SAME chat-opened deck",
        )
        self.assertEqual(bj.get_active_round_id(self.creator_id, self.user_id), chat_round_id)
        self.assertEqual(self._active_hand_count(), 1)

    def test_cross_interface_dashboard_opened_hand_is_the_same_row_chat_sees(self):
        """The reverse direction: a hand opened via the dashboard's own
        POST .../deal must be the exact row chat's hit()/stand() (called
        directly, simulating chat) act on."""
        self._fund_promo(1000)
        self._with_deck("TS", "2H", "9D", "3H")
        key = str(uuid.uuid4())

        deal_res = self.client.post(
            "/api/studio/casino/play/blackjack/deal",
            json={"bet": 10, "idempotency_key": key}, auth=self.auth,
        )
        self.assertEqual(deal_res.status_code, 200)
        dashboard_round_id = f"dashboard:blackjack:{key}"
        self.assertEqual(bj.get_active_round_id(self.creator_id, self.user_id), dashboard_round_id)
        self.assertEqual(self._active_hand_count(), 1)

        chat_result = bj.stand(self.creator_id, self.user_id, dashboard_round_id, display_name=self.username)
        self.assertEqual(chat_result.state, cr.STATE_SETTLED, "chat's stand must be able to settle the dashboard-opened hand")
        self.assertEqual(self._active_hand_count(), 0, "settlement releases the active-hand slot")

        get_res = self.client.get("/api/studio/casino/play/blackjack", auth=self.auth)
        self.assertIsNone(get_res.json()["hand"], "no active hand any more -- the dashboard must see it as settled too")

    # --- Full dashboard hand + per-hit idempotency + deck integrity -----
    def test_full_dashboard_hand_deal_hit_stand_settle(self):
        self._fund_promo(1000)
        # player: 7S+5D=12 -> hit 6C -> 18. dealer: 2H+9H=11 -> hits (<17)
        # -> 2C(13) -> 4C(17) -> stops.
        self._with_deck("7S", "2H", "5D", "9H", "6C", "2C", "4C")

        deal_res = self.client.post(
            "/api/studio/casino/play/blackjack/deal",
            json={"bet": 10, "idempotency_key": str(uuid.uuid4())}, auth=self.auth,
        )
        self.assertEqual(deal_res.status_code, 200)
        dealt = deal_res.json()["hand"]
        self.assertEqual(dealt["state"], cr.STATE_FUNDED)
        self.assertEqual(dealt["player_cards"], ["7S", "5D"])
        self.assertEqual(dealt["dealer_up_card"], "2H")
        self.assertIsNone(dealt["dealer_cards"], "the dealer's hole card must stay hidden mid-hand")
        self.assertEqual(self._promo_balance(), 1000 - 10)

        hit_res = self.client.post(
            "/api/studio/casino/play/blackjack/hit",
            json={"idempotency_key": str(uuid.uuid4())}, auth=self.auth,
        )
        self.assertEqual(hit_res.status_code, 200)
        hit_hand = hit_res.json()["hand"]
        self.assertEqual(hit_hand["state"], cr.STATE_FUNDED)
        self.assertEqual(hit_hand["player_cards"], ["7S", "5D", "6C"])

        stand_res = self.client.post("/api/studio/casino/play/blackjack/stand", json={}, auth=self.auth)
        self.assertEqual(stand_res.status_code, 200)
        settled = stand_res.json()["hand"]
        self.assertEqual(settled["state"], cr.STATE_SETTLED)
        self.assertEqual(settled["outcome"], "win")
        self.assertEqual(settled["dealer_cards"], ["2H", "9H", "2C", "4C"])
        self.assertEqual(settled["payout"], 20)
        self.assertEqual(self._promo_balance(), 1000 - 10 + 20)
        self.assertEqual(self._active_hand_count(), 0)

    def test_dashboard_hit_double_post_same_key_draws_one_card_not_two(self):
        self._fund_promo(1000)
        self._with_deck("7S", "2H", "5D", "9H", "6C", "3C")
        self.client.post(
            "/api/studio/casino/play/blackjack/deal",
            json={"bet": 10, "idempotency_key": str(uuid.uuid4())}, auth=self.auth,
        )
        payload = {"idempotency_key": str(uuid.uuid4())}

        first = self.client.post("/api/studio/casino/play/blackjack/hit", json=payload, auth=self.auth)
        second = self.client.post("/api/studio/casino/play/blackjack/hit", json=payload, auth=self.auth)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        first_hand, second_hand = first.json()["hand"], second.json()["hand"]
        self.assertFalse(first_hand["replayed"])
        self.assertTrue(second_hand["replayed"], "a repeated hit idempotency_key must come back replayed")
        self.assertEqual(first_hand["player_cards"], ["7S", "5D", "6C"])
        self.assertEqual(second_hand["player_cards"], ["7S", "5D", "6C"], "double-hit (double-click Hit) must not draw a second card")

    def test_dashboard_deck_integrity_resume_shows_same_cards(self):
        self._fund_promo(1000)
        self._with_deck("7S", "2H", "5D", "9H", "6C")
        self.client.post(
            "/api/studio/casino/play/blackjack/deal",
            json={"bet": 10, "idempotency_key": str(uuid.uuid4())}, auth=self.auth,
        )
        self.client.post(
            "/api/studio/casino/play/blackjack/hit",
            json={"idempotency_key": str(uuid.uuid4())}, auth=self.auth,
        )

        first_read = self.client.get("/api/studio/casino/play/blackjack", auth=self.auth).json()["hand"]
        second_read = self.client.get("/api/studio/casino/play/blackjack", auth=self.auth).json()["hand"]
        self.assertEqual(first_read["player_cards"], ["7S", "5D", "6C"])
        self.assertEqual(
            second_read["player_cards"], ["7S", "5D", "6C"],
            "repeated reads (e.g. a page reload) must show the same persisted deck, never redraw",
        )

    # --- Chat-post on final settle only, overlay, replay-guard -----------
    def test_chat_post_fires_only_on_the_action_that_settles_the_hand(self):
        self._fund_promo(1000)
        self._with_deck("7S", "2H", "5D", "9H", "6C", "2C", "4C")

        with mock.patch(
            "services.blaze_native_connector._foxbot_live_send_chat_v2",
            return_value={"ok": True, "sent": True},
        ) as mock_send:
            deal_res = self.client.post(
                "/api/studio/casino/play/blackjack/deal",
                json={"bet": 10, "idempotency_key": str(uuid.uuid4())}, auth=self.auth,
            )
        self.assertEqual(deal_res.json()["hand"]["state"], cr.STATE_FUNDED)
        mock_send.assert_not_called()

        with mock.patch(
            "services.blaze_native_connector._foxbot_live_send_chat_v2",
            return_value={"ok": True, "sent": True},
        ) as mock_send:
            hit_res = self.client.post(
                "/api/studio/casino/play/blackjack/hit",
                json={"idempotency_key": str(uuid.uuid4())}, auth=self.auth,
            )
        self.assertEqual(hit_res.json()["hand"]["state"], cr.STATE_FUNDED)
        mock_send.assert_not_called()

        with mock.patch(
            "services.blaze_native_connector._foxbot_live_send_chat_v2",
            return_value={"ok": True, "sent": True},
        ) as mock_send:
            stand_res = self.client.post("/api/studio/casino/play/blackjack/stand", json={}, auth=self.auth)
        self.assertEqual(stand_res.json()["hand"]["state"], cr.STATE_SETTLED)
        mock_send.assert_called_once()
        (posted_text,), _ = mock_send.call_args
        self.assertTrue(posted_text.startswith("🃏"))
        self.assertIn("crypt0k1ng96", posted_text)

    def test_hit_that_busts_settles_and_posts_to_chat(self):
        self._fund_promo(1000)
        self._with_deck("TS", "2H", "9D", "3H", "5C")  # 19 -> hit 5C -> 24, bust
        self.client.post(
            "/api/studio/casino/play/blackjack/deal",
            json={"bet": 10, "idempotency_key": str(uuid.uuid4())}, auth=self.auth,
        )

        with mock.patch(
            "services.blaze_native_connector._foxbot_live_send_chat_v2",
            return_value={"ok": True, "sent": True},
        ) as mock_send:
            hit_res = self.client.post(
                "/api/studio/casino/play/blackjack/hit",
                json={"idempotency_key": str(uuid.uuid4())}, auth=self.auth,
            )
        self.assertEqual(hit_res.status_code, 200)
        hand = hit_res.json()["hand"]
        self.assertEqual(hand["state"], cr.STATE_SETTLED)
        self.assertEqual(hand["outcome"], "loss")
        self.assertEqual(hand["settled_reason"], "bust")
        mock_send.assert_called_once()
        (posted_text,), _ = mock_send.call_args
        self.assertTrue(posted_text.startswith("🃏"))

    def test_natural_blackjack_on_deal_posts_to_chat_and_fires_overlay(self):
        self._fund_promo(1000)
        self._with_deck("AS", "2S", "KH", "3S")  # player natural 21, dealer 5 (not natural)

        with mock.patch(
            "services.blaze_native_connector._foxbot_live_send_chat_v2",
            return_value={"ok": True, "sent": True},
        ) as mock_send:
            res = self.client.post(
                "/api/studio/casino/play/blackjack/deal",
                json={"bet": 10, "idempotency_key": str(uuid.uuid4())}, auth=self.auth,
            )
        self.assertEqual(res.status_code, 200)
        hand = res.json()["hand"]
        self.assertEqual(hand["state"], cr.STATE_SETTLED)
        self.assertEqual(hand["outcome"], "blackjack")
        self.assertEqual(hand["payout"], 25)  # 10 + (10*3)//2
        mock_send.assert_called_once()

        import time
        deadline = time.time() + 3.0
        matches = []
        while time.time() < deadline:
            rows = foxbot_events.fetch_events(self.creator_handle, limit=10)
            matches = [r for r in (rows or []) if r[0] == "casino_win" and r[1] == app._FOXBOT_DASHBOARD_PLAY_USERNAME]
            if matches:
                break
            time.sleep(0.1)
        self.assertTrue(matches, "a dashboard-dealt natural blackjack must fire the same overlay event a chat one would")
        _, _, detail, _ = matches[0]
        self.assertEqual(detail["game"], "blackjack")
        self.assertEqual(detail["highlight"], "blackjack")

    def test_replayed_settle_action_does_not_repost_to_chat(self):
        self._fund_promo(1000)
        self._with_deck("AS", "2S", "KH", "3S")  # natural -- settles on deal itself
        key = str(uuid.uuid4())
        payload = {"bet": 10, "idempotency_key": key}

        with mock.patch(
            "services.blaze_native_connector._foxbot_live_send_chat_v2",
            return_value={"ok": True, "sent": True},
        ) as mock_send:
            first = self.client.post("/api/studio/casino/play/blackjack/deal", json=payload, auth=self.auth)
            second = self.client.post("/api/studio/casino/play/blackjack/deal", json=payload, auth=self.auth)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertFalse(first.json()["hand"]["replayed"])
        self.assertTrue(
            second.json()["hand"]["replayed"],
            "a repeated deal idempotency_key on an already-settled natural must come back replayed",
        )
        mock_send.assert_called_once()


if __name__ == "__main__":
    unittest.main()
