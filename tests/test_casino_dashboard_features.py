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
import services.casino_config as casino_config  # noqa: E402
import services.casino_ledger as cl  # noqa: E402
import services.casino_rounds as cr  # noqa: E402
import services.casino_rng as casino_rng  # noqa: E402
import services.foxbot_events as foxbot_events  # noqa: E402


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

    def _fund_promo(self, amount):
        cl.credit(
            self.creator_id, self.user_id, cr.CURRENCY_PROMO, amount, cl.PROMO_CONVERT_IN,
            idempotency_key=f"{self.creator_id}-fund-{uuid.uuid4().hex[:8]}",
        )

    def _promo_balance(self):
        return cl.get_balance(self.creator_id, self.user_id, cr.CURRENCY_PROMO)

    def _money_table_counts(self):
        with cl._connect() as connection:
            cl._ensure_schema(connection)
            cr._ensure_schema(connection)
            ledger = connection.execute(f"SELECT COUNT(*) FROM {cl.TABLE_LEDGER}").fetchone()[0]
            balances = connection.execute(f"SELECT COUNT(*) FROM {cl.TABLE_BALANCES}").fetchone()[0]
            rounds = connection.execute(f"SELECT COUNT(*) FROM {cr.TABLE_ROUNDS}").fetchone()[0]
        return (ledger, balances, rounds)

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


if __name__ == "__main__":
    unittest.main()
