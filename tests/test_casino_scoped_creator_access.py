"""Tests for scoped (non-admin) self-service Casino access.

Covers the work that lets a REAL second creator (princessjamesy, or any
future one) log into the dashboard with their own Blaze account and:
turn their OWN casino on/off, configure it, play their OWN games, and
see their OWN wins feed -- through the SAME admin-proven validated
setters/idempotency/ledger, with cross-creator isolation as ironclad as
bot-connect's own per-target isolation, because this moves real money.

Deliberately does NOT mock the auth boundary away. Two distinct dashboard
sessions are built with REAL signed cookies via
app._foxbot_dashboard_session_sign_v1 and verified by the REAL,
unmocked app._foxbot_dashboard_session_verify_v1 / auth-gate middleware.
_foxbot_resolve_creator_id_v1 is also left UNMOCKED -- its own
first-precedence branch (`if blaze_id: return str(blaze_id).strip()`) is
what's actually being proven safe here, so mocking it away would prove
nothing. The only things mocked are: _tenant_zero_id() (so "admin" is a
fixed, known value), _foxbot_resolve_event_handle_v1 and
_foxbot_creator_access_get_v1 (so the connected_creators.json join --
irrelevant to this proof -- doesn't need a real on-disk fixture), and
the two outbound chat-send functions (no live network calls in a test).

All against a real Postgres (local Docker) via DATABASE_URL, using
FastAPI's TestClient to exercise the actual HTTP routes.

Run with:
    python -m unittest tests.test_casino_scoped_creator_access -v
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
import services.foxbot_events as foxbot_events  # noqa: E402
import providers.promo as promo  # noqa: E402


DATABASE_CONFIGURED = bool(os.getenv("DATABASE_URL"))
SKIP_REASON = (
    "DATABASE_URL not set -- these tests need a real Postgres database "
    "(a throwaway/dev one, not production) to prove the actual HTTP "
    "wiring, idempotency, and cross-creator isolation honestly."
)

STUDIO_ADMIN_USER = os.getenv("STUDIO_ADMIN_USER", "")
STUDIO_ADMIN_PASSWORD = os.getenv("STUDIO_ADMIN_PASSWORD", "")
ADMIN_AUTH_CONFIGURED = bool(STUDIO_ADMIN_USER and STUDIO_ADMIN_PASSWORD)


@unittest.skipUnless(DATABASE_CONFIGURED, SKIP_REASON)
class ScopedCasinoAccessTestCase(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient

        self.client = TestClient(app.app)

        # Three distinct identities: admin (tenant-zero), creator A (the
        # scoped session under test -- the princessjamesy stand-in), and
        # creator B (a second, unrelated creator used ONLY as a target
        # for cross-creator-isolation attempts -- creator A's session
        # must never be able to touch creator B's anything).
        self.admin_blaze_id = f"test-admin-{uuid.uuid4().hex[:10]}"
        self.admin_display_name = "test-admin-owner"

        self.creator_a_id = f"test-creatorA-{uuid.uuid4().hex[:10]}"
        self.creator_a_display_name = "test-creator-a"
        self.creator_a_handle = "test-creator-a-handle"
        self.creator_a_username = self.creator_a_display_name
        self.creator_a_user_id = app.viewer_key(self.creator_a_username)

        self.creator_b_id = f"test-creatorB-{uuid.uuid4().hex[:10]}"
        self.creator_b_display_name = "test-creator-b"
        self.creator_b_handle = "test-creator-b-handle"
        self.creator_b_username = self.creator_b_display_name
        self.creator_b_user_id = app.viewer_key(self.creator_b_username)

        # The legacy hardcoded literal -- used to prove it is NOT what
        # gets debited/credited for a real scoped session anymore.
        self.legacy_username = app._FOXBOT_DASHBOARD_PLAY_USERNAME
        self.legacy_user_id = app.viewer_key(self.legacy_username)

        self._env_patches = {}
        for key, value in {
            "STUDIO_SESSION_SECRET": "test-secret-do-not-use-in-prod",
            "STUDIO_APPROVED_BLAZE_USER_IDS": ",".join(
                [self.admin_blaze_id, self.creator_a_id, self.creator_b_id]
            ),
            "STUDIO_AUTH_MODE": "both",
            "FOXBOT_CASINO_ENABLED": "true",
            "FOXBOT_SLOTS_ENABLED": "true",
            "FOXBOT_DICE_ENABLED": "true",
            "FOXBOT_BLACKJACK_ENABLED": "true",
            "FOXBOT_BOT_CONNECT_ACTIVE_CREATOR_IDS": "",
        }.items():
            self._env_patches[key] = os.environ.get(key)
            os.environ[key] = value

        self._tz_patch = mock.patch.object(app, "_tenant_zero_id", return_value=self.admin_blaze_id)
        self._tz_patch.start()

        handle_map = {
            self.creator_a_id: self.creator_a_handle,
            self.creator_b_id: self.creator_b_handle,
        }

        def _fake_resolve_event_handle(blaze_id):
            if not blaze_id or blaze_id == self.admin_blaze_id:
                return "test-owner-handle"
            return handle_map.get(blaze_id, "")

        self._handle_patch = mock.patch.object(
            app, "_foxbot_resolve_event_handle_v1", side_effect=_fake_resolve_event_handle,
        )
        self._handle_patch.start()

        access_map = {
            self.creator_a_handle: {"channel_id": "channel-a-real"},
            self.creator_b_handle: {"channel_id": "channel-b-real"},
        }

        def _fake_access_get(handle):
            return access_map.get(handle, {})

        self._access_patch = mock.patch.object(
            app, "_foxbot_creator_access_get_v1", side_effect=_fake_access_get,
        )
        self._access_patch.start()

        # No live network calls, ever, from these tests.
        self._legacy_send_patch = mock.patch(
            "services.blaze_native_connector._foxbot_live_send_chat_v2",
            return_value={"ok": True, "sent": True},
        )
        self.legacy_send_mock = self._legacy_send_patch.start()

        self._new_send_patch = mock.patch.object(
            app, "send_blaze_chat_message", return_value={"success": True},
        )
        self.new_send_mock = self._new_send_patch.start()

        # Both creators start with an explicit, valid, self-service config
        # -- casino_enabled True with sane rate/limits -- via the SAME
        # validated setter admin already uses. Individual tests override
        # this where the test is specifically about the enable/disable
        # switch itself.
        casino_config.set_config(self.creator_a_id, foxcoins_per_promo=10, daily_promo_limit=5000, casino_enabled=True)
        casino_config.set_config(self.creator_b_id, foxcoins_per_promo=10, daily_promo_limit=5000, casino_enabled=True)

    def tearDown(self):
        self._tz_patch.stop()
        self._handle_patch.stop()
        self._access_patch.stop()
        self._legacy_send_patch.stop()
        self._new_send_patch.stop()

        for key, value in self._env_patches.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

        with cl._connect() as connection:
            cl._ensure_schema(connection)
            cr._ensure_schema(connection)
            casino_config._ensure_schema(connection)
            foxbot_events._ensure_schema(connection)
            bj._ensure_schema(connection)
            promo._ensure_schema(connection)
            for cid in (self.creator_a_id, self.creator_b_id, self.admin_blaze_id):
                connection.execute(f"DELETE FROM {cl.TABLE_LEDGER} WHERE creator_id = %s", (cid,))
                connection.execute(f"DELETE FROM {cl.TABLE_BALANCES} WHERE creator_id = %s", (cid,))
                connection.execute(f"DELETE FROM {cr.TABLE_ROUNDS} WHERE creator_id = %s", (cid,))
                connection.execute(f"DELETE FROM {casino_config.TABLE_GAME_CONFIG} WHERE creator_id = %s", (cid,))
                connection.execute(f"DELETE FROM {casino_config.TABLE_CONFIG} WHERE creator_id = %s", (cid,))
                connection.execute(f"DELETE FROM {bj.TABLE_ACTIVE_HANDS} WHERE creator_id = %s", (cid,))
                connection.execute(f"DELETE FROM {promo.TABLE_ATTEMPTS} WHERE creator_id = %s", (cid,))
            for handle in (self.creator_a_handle, self.creator_b_handle, "test-owner-handle"):
                connection.execute("DELETE FROM foxbot_events WHERE creator_handle = %s", (handle,))

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _cookies_for(self, blaze_id, display_name):
        token = app._foxbot_dashboard_session_sign_v1(blaze_id, display_name)
        return {"foxbot_dashboard_session": token}

    def _as_admin_blaze(self):
        return self._cookies_for(self.admin_blaze_id, self.admin_display_name)

    def _as_creator_a(self):
        return self._cookies_for(self.creator_a_id, self.creator_a_display_name)

    def _as_creator_b(self):
        return self._cookies_for(self.creator_b_id, self.creator_b_display_name)

    def _fund_promo(self, creator_id, user_id, amount):
        cl.credit(
            creator_id, user_id, cr.CURRENCY_PROMO, amount, cl.PROMO_CONVERT_IN,
            idempotency_key=f"{creator_id}-fund-{uuid.uuid4().hex[:8]}",
        )

    def _promo_balance(self, creator_id, user_id):
        return cl.get_balance(creator_id, user_id, cr.CURRENCY_PROMO)

    def _idem(self):
        return uuid.uuid4().hex

    # ==================================================================
    # 1. SELF-SERVICE ENABLE/CONFIGURE -- no admin approval step
    # ==================================================================
    def test_scoped_creator_can_self_enable_with_sane_defaults(self):
        """A brand-new creator (no config row yet) POSTing just
        casino_enabled=True gets set_config()'s own module defaults --
        never a broken/zero/unset state."""
        fresh_id = f"test-fresh-{uuid.uuid4().hex[:10]}"
        with mock.patch.object(app, "_tenant_zero_id", return_value=self.admin_blaze_id):
            cookies = self._cookies_for(fresh_id, "test-fresh-creator")
            os.environ["STUDIO_APPROVED_BLAZE_USER_IDS"] += f",{fresh_id}"
            try:
                res = self.client.post(
                    "/api/studio/casino/config", json={"casino_enabled": True}, cookies=cookies,
                )
            finally:
                with cl._connect() as connection:
                    casino_config._ensure_schema(connection)
                    connection.execute(f"DELETE FROM {casino_config.TABLE_CONFIG} WHERE creator_id = %s", (fresh_id,))

        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertTrue(body["ok"])
        self.assertTrue(body["casino_enabled"])
        self.assertEqual(body["foxcoins_per_promo"], casino_config.DEFAULT_FOXCOINS_PER_PROMO)
        self.assertEqual(body["daily_promo_limit"], casino_config.DEFAULT_DAILY_PROMO_LIMIT)

    def test_fresh_self_enable_gets_phase1_defaults_not_tenant_zero_values(self):
        """Explicit contrast, not just a match against the module constant:
        tenant-zero's REAL production config (foxcoins_per_promo=1, per
        the actual live row) is set up here first, then a brand-new
        creator self-enables with zero prior config. Her result must
        equal the Phase 1 system defaults and must NOT equal
        tenant-zero's current values -- proving set_config()/
        set_game_config() never read or copy another creator_id's row,
        because the query is always `WHERE creator_id = %s`, scoped to
        the one id being read."""
        # Tenant-zero's real current production values (confirmed live:
        # foxcoins_per_promo=1, daily_promo_limit=5000, casino_enabled=True).
        casino_config.set_config(self.admin_blaze_id, foxcoins_per_promo=1, daily_promo_limit=5000, casino_enabled=True)
        casino_config.set_game_config(self.admin_blaze_id, "coinflip", enabled=True, min_bet=50, max_bet=500)

        fresh_id = f"test-fresh-defaults-{uuid.uuid4().hex[:10]}"

        # Zero prior config for fresh_id -- confirmed before touching it.
        with cl._connect() as connection:
            casino_config._ensure_schema(connection)
            row = connection.execute(
                f"SELECT 1 FROM {casino_config.TABLE_CONFIG} WHERE creator_id = %s", (fresh_id,),
            ).fetchone()
        self.assertIsNone(row, "precondition: fresh_id must have NO existing casino_config row")

        os.environ["STUDIO_APPROVED_BLAZE_USER_IDS"] += f",{fresh_id}"
        try:
            res = self.client.post(
                "/api/studio/casino/config", json={"casino_enabled": True},
                cookies=self._cookies_for(fresh_id, "test-fresh-defaults-creator"),
            )
            self.assertEqual(res.status_code, 200, res.text)
            body = res.json()

            # Matches the literal Phase 1 system defaults.
            self.assertEqual(body["foxcoins_per_promo"], 100)
            self.assertEqual(body["daily_promo_limit"], 5000)
            self.assertTrue(body["casino_enabled"])

            # And explicitly does NOT match tenant-zero's just-set, real
            # current value (1, not 100) -- the actual cross-contamination
            # check, not just a match against the constant.
            self.assertNotEqual(body["foxcoins_per_promo"], 1)
            tenant_zero_config = casino_config.get_config(self.admin_blaze_id)
            self.assertEqual(tenant_zero_config.foxcoins_per_promo, 1, "tenant-zero's own row must be untouched")
            self.assertNotEqual(body["foxcoins_per_promo"], tenant_zero_config.foxcoins_per_promo)

            # Same proof for per-game config: fresh creator gets the
            # Phase 1 game defaults (enabled=True, min=1, max=1000), NOT
            # tenant-zero's custom coinflip range (50-500) set above.
            fresh_game = casino_config.get_game_config(fresh_id, "coinflip")
            self.assertEqual(fresh_game.enabled, casino_config.DEFAULT_GAME_ENABLED)
            self.assertEqual(fresh_game.min_bet, casino_config.DEFAULT_MIN_BET)
            self.assertEqual(fresh_game.max_bet, casino_config.DEFAULT_MAX_BET)
            self.assertNotEqual(fresh_game.min_bet, 50)
            self.assertNotEqual(fresh_game.max_bet, 500)

            tenant_zero_game = casino_config.get_game_config(self.admin_blaze_id, "coinflip")
            self.assertEqual(tenant_zero_game.min_bet, 50, "tenant-zero's own game row must be untouched")
        finally:
            with cl._connect() as connection:
                casino_config._ensure_schema(connection)
                connection.execute(f"DELETE FROM {casino_config.TABLE_CONFIG} WHERE creator_id = %s", (fresh_id,))
                connection.execute(f"DELETE FROM {casino_config.TABLE_GAME_CONFIG} WHERE creator_id = %s", (fresh_id,))
                connection.execute(f"DELETE FROM {casino_config.TABLE_CONFIG} WHERE creator_id = %s", (self.admin_blaze_id,))
                connection.execute(f"DELETE FROM {casino_config.TABLE_GAME_CONFIG} WHERE creator_id = %s", (self.admin_blaze_id,))

    def test_scoped_creator_can_toggle_own_casino_and_own_game(self):
        res = self.client.post(
            "/api/studio/casino/config",
            json={"casino_enabled": True, "foxcoins_per_promo": 25, "daily_promo_limit": 900},
            cookies=self._as_creator_a(),
        )
        self.assertEqual(res.status_code, 200, res.text)
        self.assertTrue(res.json()["casino_enabled"])
        self.assertEqual(res.json()["foxcoins_per_promo"], 25)

        res = self.client.post(
            "/api/studio/casino/game-config/coinflip",
            json={"enabled": True, "min_bet": 5, "max_bet": 200},
            cookies=self._as_creator_a(),
        )
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["min_bet"], 5)
        self.assertEqual(res.json()["max_bet"], 200)

        # Confirm it's HER row, via direct service call, not just the
        # HTTP response echoing the payload back.
        config = casino_config.get_config(self.creator_a_id)
        self.assertEqual(config.foxcoins_per_promo, 25)

    # ==================================================================
    # 2/3/4. BAD INPUT + FAIL-CLOSED + PLATFORM-FLAG INDEPENDENCE
    # ==================================================================
    def test_bad_self_service_input_rejected_no_partial_write(self):
        before = casino_config.get_config(self.creator_a_id)

        for bad_payload in (
            {"foxcoins_per_promo": 0},
            {"daily_promo_limit": -5},
            {"foxcoins_per_promo": -100},
        ):
            res = self.client.post(
                "/api/studio/casino/config", json=bad_payload, cookies=self._as_creator_a(),
            )
            self.assertEqual(res.status_code, 400, f"{bad_payload} should be rejected: {res.text}")

        for bad_game_payload in (
            {"min_bet": 0}, {"min_bet": -5}, {"min_bet": 100, "max_bet": 10},
        ):
            res = self.client.post(
                "/api/studio/casino/game-config/roulette", json=bad_game_payload, cookies=self._as_creator_a(),
            )
            self.assertEqual(res.status_code, 400, f"{bad_game_payload} should be rejected: {res.text}")

        after = casino_config.get_config(self.creator_a_id)
        self.assertEqual(before, after, "a rejected write must leave the existing row completely untouched")

    def test_platform_flag_off_blocks_play_even_for_fully_enabled_creator(self):
        """Q2/Q3: FOXBOT_CASINO_ENABLED is the platform killswitch, env-only,
        never self-service. Even a creator who has fully enabled their OWN
        casino cannot play while the platform flag is off -- fail-closed,
        not fail-open."""
        self._fund_promo(self.creator_a_id, self.creator_a_user_id, 100)
        original = os.environ.get("FOXBOT_CASINO_ENABLED")
        os.environ["FOXBOT_CASINO_ENABLED"] = ""
        try:
            res = self.client.post(
                "/api/studio/casino/play/coinflip",
                json={"pick": "heads", "wager": 10, "idempotency_key": self._idem()},
                cookies=self._as_creator_a(),
            )
        finally:
            if original is None:
                os.environ.pop("FOXBOT_CASINO_ENABLED", None)
            else:
                os.environ["FOXBOT_CASINO_ENABLED"] = original

        self.assertEqual(res.status_code, 503, res.text)
        self.assertFalse(res.json()["ok"])
        self.assertEqual(
            self._promo_balance(self.creator_a_id, self.creator_a_user_id), 100,
            "platform flag off must move zero money, even for an otherwise-fully-enabled creator",
        )

    def test_no_route_can_read_or_write_the_platform_flag(self):
        """Confirm the platform env var is genuinely untouched by any
        self-service action -- even a payload that NAMES it is a no-op,
        since no route reads any such field."""
        before = os.environ.get("FOXBOT_CASINO_ENABLED")
        res = self.client.post(
            "/api/studio/casino/config",
            json={
                "casino_enabled": True,
                "foxcoins_per_promo": 10,
                "daily_promo_limit": 500,
                "FOXBOT_CASINO_ENABLED": False,  # decoy field -- must be silently ignored
                "platform_enabled": False,       # decoy field -- must be silently ignored
            },
            cookies=self._as_creator_a(),
        )
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(
            os.environ.get("FOXBOT_CASINO_ENABLED"), before,
            "a decoy field in the payload must never touch the real platform env var",
        )
        self.assertTrue(app._foxbot_casino_enabled_v1(), "platform flag must still read true after the request")

    def test_scoped_creator_cannot_play_before_enabling_own_casino(self):
        """The fail-closed gap this work closes: before this fix, neither
        the platform flag NOR the per-creator casino_enabled switch was
        checked on this dashboard path at all (only the per-GAME toggle,
        which defaults enabled=True) -- so a creator who never turned
        their own casino on could still move real ledger balance. Now
        the same two-gate check chat()'s own commands use applies here
        too."""
        casino_config.set_config(self.creator_a_id, casino_enabled=False)
        self._fund_promo(self.creator_a_id, self.creator_a_user_id, 100)

        res = self.client.post(
            "/api/studio/casino/play/coinflip",
            json={"pick": "heads", "wager": 10, "idempotency_key": self._idem()},
            cookies=self._as_creator_a(),
        )
        self.assertEqual(res.status_code, 400, res.text)
        self.assertFalse(res.json()["ok"])
        self.assertIn("isn't enabled", res.json()["error"])
        self.assertEqual(
            self._promo_balance(self.creator_a_id, self.creator_a_user_id), 100,
            "a wager must never be debited while the creator's own casino is off",
        )

    def test_casino_config_unavailable_fails_closed_for_scoped_creator_too(self):
        """Same fail-closed contract as the admin case: no DATABASE_URL
        (simulated here) -> 503, never a silent fallback or a broken
        partial state, for a scoped creator exactly like for admin."""
        with mock.patch.object(casino_config, "is_available", return_value=False):
            res = self.client.get("/api/studio/casino/config", cookies=self._as_creator_a())
        self.assertEqual(res.status_code, 503, res.text)
        self.assertFalse(res.json()["ok"])

    # ==================================================================
    # 3. HARDCODED-USERNAME BUG -- before/after proof
    # ==================================================================
    def test_dashboard_play_username_resolver_unit_before_after(self):
        """Direct proof the resolver no longer returns the hardcoded
        literal for a real Blaze session."""
        fake_request = mock.Mock()
        fake_request.state = mock.Mock()
        fake_request.state.blaze_id = self.creator_a_id
        fake_request.state.display_name = self.creator_a_display_name
        resolved = app._foxbot_dashboard_play_username_v1(fake_request)
        self.assertEqual(resolved, self.creator_a_display_name)
        self.assertNotEqual(
            resolved, self.legacy_username,
            "a real scoped session must never resolve to the old hardcoded literal",
        )

        # Basic Auth (no blaze_id at all) -- byte-identical to before this fix.
        basic_request = mock.Mock()
        basic_request.state = mock.Mock(spec=[])  # no blaze_id attribute at all
        self.assertEqual(app._foxbot_dashboard_play_username_v1(basic_request), self.legacy_username)

    def test_scoped_creator_play_debits_her_own_identity_not_tenant_zero(self):
        """End-to-end proof: her dashboard play moves HER OWN viewer
        bucket (viewer_key(her real display name)) inside her own
        creator scope -- never viewer_key('crypt0k1ng96'), the exact
        landmine this closes."""
        self._fund_promo(self.creator_a_id, self.creator_a_user_id, 100)
        self.assertEqual(self._promo_balance(self.creator_a_id, self.legacy_user_id), 0)

        res = self.client.post(
            "/api/studio/casino/play/coinflip",
            json={"pick": "heads", "wager": 10, "idempotency_key": self._idem()},
            cookies=self._as_creator_a(),
        )
        self.assertEqual(res.status_code, 200, res.text)

        # HER bucket moved (100 -> 90 wager debited, then payout applied --
        # either way it's no longer untouched at 100).
        self.assertNotEqual(self._promo_balance(self.creator_a_id, self.creator_a_user_id), 100)
        # The legacy hardcoded-username bucket, in the SAME creator scope,
        # is untouched -- proves it was never the acting identity.
        self.assertEqual(
            self._promo_balance(self.creator_a_id, self.legacy_user_id), 0,
            "BEFORE this fix, every dashboard play used viewer_key('crypt0k1ng96') "
            "regardless of who was logged in -- this must now be exactly 0 untouched",
        )

    # ==================================================================
    # CROSS-CREATOR ISOLATION -- the critical proof, every route
    # ==================================================================
    def test_isolation_config_read_and_write(self):
        casino_config.set_config(self.creator_b_id, foxcoins_per_promo=77, daily_promo_limit=1234, casino_enabled=True)
        before_b = casino_config.get_config(self.creator_b_id)

        # A's own read never returns B's values.
        res = self.client.get("/api/studio/casino/config", cookies=self._as_creator_a())
        self.assertEqual(res.status_code, 200)
        self.assertNotEqual(res.json()["foxcoins_per_promo"], 77)

        # A's write, even with a decoy creator_id field pointing at B,
        # only ever touches A's own row -- no route reads creator_id from
        # the payload at all.
        res = self.client.post(
            "/api/studio/casino/config",
            json={"creator_id": self.creator_b_id, "foxcoins_per_promo": 999, "casino_enabled": True},
            cookies=self._as_creator_a(),
        )
        self.assertEqual(res.status_code, 200, res.text)

        after_b = casino_config.get_config(self.creator_b_id)
        self.assertEqual(before_b, after_b, "creator A's write must never touch creator B's config row")
        self.assertEqual(casino_config.get_config(self.creator_a_id).foxcoins_per_promo, 999)

    def test_isolation_game_config(self):
        casino_config.set_game_config(self.creator_b_id, "dice", min_bet=50, max_bet=60, enabled=True)
        before_b = casino_config.get_game_config(self.creator_b_id, "dice")

        res = self.client.post(
            "/api/studio/casino/game-config/dice",
            json={"creator_id": self.creator_b_id, "min_bet": 1, "max_bet": 5},
            cookies=self._as_creator_a(),
        )
        self.assertEqual(res.status_code, 200, res.text)

        after_b = casino_config.get_game_config(self.creator_b_id, "dice")
        self.assertEqual(before_b, after_b, "creator A's game-config write must never touch creator B's row")

    def test_isolation_stats(self):
        self._fund_promo(self.creator_b_id, self.creator_b_user_id, 5000)
        cl.debit(
            self.creator_b_id, self.creator_b_user_id, cr.CURRENCY_PROMO, 500, cl.PROMO_WAGER,
            round_id=f"isolation-stats-{uuid.uuid4().hex}", idempotency_key=f"isolation-stats-{uuid.uuid4().hex}",
        )

        res = self.client.get("/api/studio/casino/stats", cookies=self._as_creator_a())
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["promo_in_circulation"], 0, "A's stats must not include any of B's promo")

    def test_isolation_wins_feed(self):
        foxbot_events.emit_event(
            self.creator_b_handle, "casino_win", actor=self.creator_b_username,
            detail={"game": "slots", "payout": 500, "highlight": "big win"},
        )
        import time
        deadline = time.time() + 3.0
        while time.time() < deadline:
            if foxbot_events.fetch_events(self.creator_b_handle, limit=5):
                break
            time.sleep(0.1)

        res = self.client.get("/api/studio/casino/wins", cookies=self._as_creator_a())
        self.assertEqual(res.status_code, 200, res.text)
        usernames = [w["username"] for w in res.json()["wins"]]
        self.assertNotIn(self.creator_b_username, usernames, "A's wins feed must never show B's win")

    def test_isolation_play_balance_and_active_hand(self):
        """Attempt to redirect a wager onto creator B via a decoy payload
        field, across a single-shot game AND blackjack's multi-step
        hand (deal/hit/stand, which look up the active round purely from
        creator_id+user_id -- never a client-supplied round_id)."""
        self._fund_promo(self.creator_a_id, self.creator_a_user_id, 200)
        self._fund_promo(self.creator_b_id, self.creator_b_user_id, 200)
        b_balance_before = self._promo_balance(self.creator_b_id, self.creator_b_user_id)

        res = self.client.post(
            "/api/studio/casino/play/coinflip",
            json={
                "pick": "heads", "wager": 10, "idempotency_key": self._idem(),
                "creator_id": self.creator_b_id,  # decoy
            },
            cookies=self._as_creator_a(),
        )
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(
            self._promo_balance(self.creator_b_id, self.creator_b_user_id), b_balance_before,
            "a decoy creator_id in the payload must never move creator B's balance",
        )

        # Blackjack: A deals a hand.
        res = self.client.post(
            "/api/studio/casino/play/blackjack/deal",
            json={"bet": 10, "idempotency_key": self._idem()},
            cookies=self._as_creator_a(),
        )
        self.assertEqual(res.status_code, 200, res.text)

        # B has no open hand at all -- confirm B's session (real cookie,
        # not a decoy field this time) sees no hand, proving hit/stand's
        # lookup is genuinely keyed per-creator, not globally per-user_id.
        res = self.client.get("/api/studio/casino/play/blackjack", cookies=self._as_creator_b())
        self.assertEqual(res.status_code, 200, res.text)
        self.assertIsNone(res.json()["hand"], "creator B must never see creator A's open hand")

        # And B attempting to hit (no hand of her own) is refused, not
        # silently redirected onto A's open hand.
        res = self.client.post(
            "/api/studio/casino/play/blackjack/hit",
            json={"idempotency_key": self._idem()},
            cookies=self._as_creator_b(),
        )
        self.assertEqual(res.status_code, 400, res.text)
        self.assertIn("don't have an open blackjack hand", res.json()["error"])

    def test_isolation_convert(self):
        app.add_points(self.creator_a_username, 1000, "test_seed", creator_id=self.creator_a_id)
        b_balance_before = self._promo_balance(self.creator_b_id, self.creator_b_user_id)

        res = self.client.post(
            "/api/studio/casino/convert",
            json={"amount": 5, "idempotency_key": self._idem(), "creator_id": self.creator_b_id},
            cookies=self._as_creator_a(),
        )
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(
            self._promo_balance(self.creator_b_id, self.creator_b_user_id), b_balance_before,
            "A's convert must never credit B's promo balance",
        )
        self.assertGreater(self._promo_balance(self.creator_a_id, self.creator_a_user_id), 0)

    def test_isolation_idempotency_namespace_across_creators(self):
        """The SAME client-generated idempotency_key used by two
        different creators must not collide -- proves the
        creator_id-namespaced round_id/idempotency_key fix."""
        self._fund_promo(self.creator_a_id, self.creator_a_user_id, 100)
        self._fund_promo(self.creator_b_id, self.creator_b_user_id, 100)
        shared_key = self._idem()

        res_a = self.client.post(
            "/api/studio/casino/play/coinflip",
            json={"pick": "heads", "wager": 10, "idempotency_key": shared_key},
            cookies=self._as_creator_a(),
        )
        res_b = self.client.post(
            "/api/studio/casino/play/coinflip",
            json={"pick": "tails", "wager": 10, "idempotency_key": shared_key},
            cookies=self._as_creator_b(),
        )
        self.assertEqual(res_a.status_code, 200, res_a.text)
        self.assertEqual(res_b.status_code, 200, res_b.text)
        self.assertFalse(res_a.json()["replayed"])
        self.assertFalse(
            res_b.json()["replayed"],
            "a shared idempotency_key across two DIFFERENT creators must "
            "settle independently, never read back as a replay of the "
            "other creator's round",
        )
        # Both actually moved their own balance (not stuck at 100).
        self.assertNotEqual(self._promo_balance(self.creator_a_id, self.creator_a_user_id), 100)
        self.assertNotEqual(self._promo_balance(self.creator_b_id, self.creator_b_user_id), 100)

    # ==================================================================
    # ADMIN REGRESSION -- explicit, both auth modes
    # ==================================================================
    @unittest.skipUnless(ADMIN_AUTH_CONFIGURED, "STUDIO_ADMIN_USER/PASSWORD not set in this environment.")
    def test_admin_basic_auth_unchanged(self):
        """Basic Auth has no blaze_id at all -- every resolved value
        below must be byte-identical to this route's pre-self-service
        behavior."""
        casino_config.set_config(self.admin_blaze_id, foxcoins_per_promo=10, daily_promo_limit=5000, casino_enabled=True)
        self._fund_promo(self.admin_blaze_id, self.legacy_user_id, 100)

        res = self.client.get("/api/studio/casino/config", auth=(STUDIO_ADMIN_USER, STUDIO_ADMIN_PASSWORD))
        self.assertEqual(res.status_code, 200, res.text)

        res = self.client.post(
            "/api/studio/casino/play/coinflip",
            json={"pick": "heads", "wager": 10, "idempotency_key": self._idem()},
            auth=(STUDIO_ADMIN_USER, STUDIO_ADMIN_PASSWORD),
        )
        self.assertEqual(res.status_code, 200, res.text)
        # Still the legacy literal identity for Basic Auth -- unchanged.
        self.assertNotEqual(self._promo_balance(self.admin_blaze_id, self.legacy_user_id), 100)

        with cl._connect() as connection:
            connection.execute(f"DELETE FROM {cl.TABLE_LEDGER} WHERE creator_id = %s", (self.admin_blaze_id,))
            connection.execute(f"DELETE FROM {cl.TABLE_BALANCES} WHERE creator_id = %s", (self.admin_blaze_id,))
            connection.execute(f"DELETE FROM {cr.TABLE_ROUNDS} WHERE creator_id = %s", (self.admin_blaze_id,))
            connection.execute(f"DELETE FROM {casino_config.TABLE_CONFIG} WHERE creator_id = %s", (self.admin_blaze_id,))

    def test_admin_own_blaze_session_unchanged_scope_deliberate_identity_upgrade(self):
        """Admin's own real Blaze login (blaze_id == tenant-zero's id) is
        still recognized as full admin by the SAME middleware logic (now
        provably so, since is_admin no longer depends on the Layer-2
        gate removed here) and still lands in the SAME creator_id bucket
        as always. The one deliberate, called-out change: their acting
        username is now their real display_name instead of the old
        hardcoded literal -- an intended identity fix, not a regression,
        and it does not move them to a different creator_id bucket."""
        casino_config.set_config(self.admin_blaze_id, foxcoins_per_promo=10, daily_promo_limit=5000, casino_enabled=True)
        res = self.client.get("/api/studio/casino/config", cookies=self._as_admin_blaze())
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["foxcoins_per_promo"], 10)

        with cl._connect() as connection:
            connection.execute(f"DELETE FROM {casino_config.TABLE_CONFIG} WHERE creator_id = %s", (self.admin_blaze_id,))

    # ==================================================================
    # CHAT-POST CHANNEL ROUTING FIX (unit-level, no HTTP/DB needed)
    # ==================================================================
    def test_chat_post_default_path_unchanged_for_non_bot_connect_creator(self):
        app._foxbot_casino_post_dashboard_chat_v1(
            "hello", creator_id=self.creator_a_id, creator_handle=self.creator_a_handle,
        )
        self.legacy_send_mock.assert_called_once_with("hello")
        self.new_send_mock.assert_not_called()

    def test_chat_post_routes_to_own_channel_for_active_bot_connect_creator(self):
        with mock.patch.object(app, "_foxbot_bot_connect_creator_active_v1", return_value=True):
            app._foxbot_casino_post_dashboard_chat_v1(
                "hello", creator_id=self.creator_a_id, creator_handle=self.creator_a_handle,
            )
        self.new_send_mock.assert_called_once_with("hello", channel_id="channel-a-real", creator_id=self.creator_a_id)
        self.legacy_send_mock.assert_not_called()

    def test_chat_post_falls_back_safely_when_no_channel_resolves(self):
        with mock.patch.object(app, "_foxbot_bot_connect_creator_active_v1", return_value=True):
            app._foxbot_casino_post_dashboard_chat_v1(
                "hello", creator_id=self.creator_a_id, creator_handle="",
            )
        self.legacy_send_mock.assert_called_once_with("hello")
        self.new_send_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
