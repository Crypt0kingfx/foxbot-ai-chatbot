"""Tests for games/crash.py (Casino Phase 7).

Two groups, deliberately split by whether they need Postgres:

  - CrashMathTestCase: NO DATABASE_URL required. The RTP simulation and
    the crash_point >= 1.00 proof call games/crash.py's resolver
    directly (_draw_crash_point()) rather than going through the full
    DB-backed play_round() lifecycle -- 100k+ real Postgres transactions
    would be needlessly slow to prove a pure math property. Target
    validation (_parse_target) also runs entirely before any DB call in
    play_crash(), so those tests belong here too.
  - CrashRoundsTestCase: real Postgres via DATABASE_URL, same convention
    as tests/test_roulette.py -- the anti-exploit proof and crash-resume
    behavior depend on real row-lock behavior that cannot be mocked
    honestly.

Run with:
    python -m unittest tests.test_crash -v
"""

import os
import sys
import unittest
import uuid
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import games.crash as crash  # noqa: E402
import services.casino_config as casino_config  # noqa: E402
import services.casino_ledger as cl  # noqa: E402
import services.casino_rng as casino_rng  # noqa: E402
import services.casino_rounds as cr  # noqa: E402


DATABASE_CONFIGURED = bool(os.getenv("DATABASE_URL"))
SKIP_REASON = (
    "DATABASE_URL not set -- these tests need a real Postgres database "
    "(a throwaway/dev one, not production) to prove the row lock, "
    "crash-resume, and anti-exploit behavior honestly."
)


class _FixedRollProvider(casino_rng.RNGProvider):
    def __init__(self, value):
        self.value = value

    def roll(self, minimum, maximum):
        return self.value

    def choice(self, seq):
        return seq[0]


class CrashMathTestCase(unittest.TestCase):
    """No DATABASE_URL required -- pure math/validation proofs."""

    def setUp(self):
        self._original_rng_provider = casino_rng.get_provider()

    def tearDown(self):
        casino_rng.set_provider(self._original_rng_provider)

    # ------------------------------------------------------------------
    def test_crash_point_always_at_least_one(self):
        casino_rng.set_provider(casino_rng.SecureRandomProvider())
        for _ in range(50_000):
            self.assertGreaterEqual(crash._draw_crash_point(), 1.0)

    def test_crash_point_can_hit_exactly_one(self):
        # r=0 -> raw = (1 - house_edge) / 1 = 0.97 -> max(1.0, 0.97) = 1.0
        casino_rng.set_provider(_FixedRollProvider(0))
        self.assertEqual(crash._draw_crash_point(), 1.0)

    def test_crash_point_never_divides_by_zero(self):
        # Largest possible roll -> r closest to (but always < ) 1 -> huge
        # but finite crash_point, never inf/nan.
        casino_rng.set_provider(_FixedRollProvider(crash.RESOLUTION - 1))
        result = crash._draw_crash_point()
        self.assertTrue(result == result)  # not NaN
        self.assertNotEqual(result, float("inf"))
        self.assertGreater(result, 1.0)

    # ------------------------------------------------------------------
    # THE RTP PROOF: simulate 200k independent crash_point draws with the
    # REAL CSPRNG (not a fixed stand-in) and confirm the empirical RTP for
    # a representative target converges to (1 - house_edge) = 0.97.
    # ------------------------------------------------------------------
    def test_rtp_simulation_matches_house_edge(self):
        casino_rng.set_provider(casino_rng.SecureRandomProvider())
        n_rounds = 200_000
        bet = 10
        target_float, target_cents = crash._parse_target("2.00")

        total_wagered = 0
        total_payout = 0
        wins = 0
        for _ in range(n_rounds):
            crash_point = crash._draw_crash_point()
            won = crash_point >= target_float
            payout = (bet * target_cents) // 100 if won else 0
            total_wagered += bet
            total_payout += payout
            wins += won

        rtp = total_payout / total_wagered
        win_rate = wins / n_rounds

        print(
            f"\n[RTP SIMULATION] n={n_rounds} target={target_float}x "
            f"win_rate={win_rate:.4f} (expected ~{(1 - crash.HOUSE_EDGE) / target_float:.4f}) "
            f"RTP={rtp:.4f} (expected ~{1 - crash.HOUSE_EDGE:.4f})"
        )

        # 200k rounds at target=2x (win prob ~0.485) gives an RTP standard
        # error on the order of 0.002 -- 0.02 is a ~9-sigma tolerance band,
        # tight enough to catch a wrong formula, loose enough to never
        # flake on real randomness.
        self.assertAlmostEqual(rtp, 1 - crash.HOUSE_EDGE, delta=0.02)

    def test_rtp_simulation_holds_across_different_targets(self):
        """The crash formula's defining property: RTP is target-invariant
        (P(win) * target = 1 - house_edge for ANY target > 1), not just at
        one arbitrarily chosen point."""
        casino_rng.set_provider(casino_rng.SecureRandomProvider())
        n_rounds = 100_000
        bet = 10

        for target_str in ("1.50", "3.00", "10.00"):
            target_float, target_cents = crash._parse_target(target_str)
            total_wagered = 0
            total_payout = 0
            for _ in range(n_rounds):
                crash_point = crash._draw_crash_point()
                won = crash_point >= target_float
                payout = (bet * target_cents) // 100 if won else 0
                total_wagered += bet
                total_payout += payout

            rtp = total_payout / total_wagered
            print(f"[RTP SIMULATION] target={target_float}x RTP={rtp:.4f}")
            self.assertAlmostEqual(rtp, 1 - crash.HOUSE_EDGE, delta=0.03)

    # ------------------------------------------------------------------
    # Target validation -- entirely offline, no DB touch by design.
    # ------------------------------------------------------------------
    def test_valid_target_parses_exactly(self):
        target_float, target_cents = crash._parse_target("2.50")
        self.assertEqual(target_float, 2.50)
        self.assertEqual(target_cents, 250)

    def test_integer_target_parses(self):
        target_float, target_cents = crash._parse_target("10")
        self.assertEqual(target_float, 10.0)
        self.assertEqual(target_cents, 1000)

    def test_target_at_or_below_one_rejected(self):
        for bad in ("1.00", "0.99", "1", "0", "-5", "-1.50"):
            with self.assertRaises(ValueError, msg=f"{bad!r} must be rejected"):
                crash._parse_target(bad)

    def test_target_above_max_rejected(self):
        with self.assertRaises(ValueError):
            crash._parse_target("100.01")
        with self.assertRaises(ValueError):
            crash._parse_target("1000")

    def test_target_at_max_accepted(self):
        target_float, target_cents = crash._parse_target("100.00")
        self.assertEqual(target_float, 100.0)
        self.assertEqual(target_cents, 10000)

    def test_target_non_numeric_rejected(self):
        for bad in ("abc", "", "  ", "2.5x", "None", "NaN", "inf"):
            with self.assertRaises(ValueError, msg=f"{bad!r} must be rejected"):
                crash._parse_target(bad)

    def test_target_too_many_decimals_rejected(self):
        for bad in ("2.555", "1.999", "3.14159"):
            with self.assertRaises(ValueError, msg=f"{bad!r} must be rejected"):
                crash._parse_target(bad)

    def test_target_accepts_decimal_instance_directly(self):
        target_float, target_cents = crash._parse_target(Decimal("5.25"))
        self.assertEqual(target_float, 5.25)
        self.assertEqual(target_cents, 525)

    # ------------------------------------------------------------------
    def test_bad_bet_rejected_before_any_db_call(self):
        """play_round() checks wager type/positivity before its first DB
        call -- proven here by calling play_crash with a bogus bet and a
        creator_id that doesn't exist, with no DATABASE_URL configured at
        all, and confirming it still raises ValueError (not a connection
        error) for bad bets, same as coinflip/roulette."""
        if DATABASE_CONFIGURED:
            self.skipTest("DATABASE_URL is set in this environment; this proof needs it unset.")
        for bad_bet in (0, -5, 1.5, "10"):
            with self.assertRaises(ValueError):
                crash.play_crash("no-such-creator", "no-such-user", bad_bet, "2.00", "no-such-round")

    def test_rng_is_server_only_no_client_outcome_param(self):
        import inspect
        sig = inspect.signature(crash.play_crash)
        forbidden = {"outcome", "result", "roll", "won", "win", "crash_point"}
        self.assertTrue(
            forbidden.isdisjoint(sig.parameters.keys()),
            f"play_crash must not accept a client-supplied outcome, got params {list(sig.parameters)}",
        )


@unittest.skipUnless(DATABASE_CONFIGURED, SKIP_REASON)
class CrashRoundsTestCase(unittest.TestCase):
    def setUp(self):
        self.creator_id = f"test-crash-{uuid.uuid4().hex[:12]}"
        self.user_id = "testviewer"
        self._original_rng_provider = casino_rng.get_provider()

    def tearDown(self):
        casino_rng.set_provider(self._original_rng_provider)

        with cl._connect() as connection:
            cl._ensure_schema(connection)
            cr._ensure_schema(connection)
            casino_config._ensure_schema(connection)
            connection.execute(f"DELETE FROM {cl.TABLE_LEDGER} WHERE creator_id = %s", (self.creator_id,))
            connection.execute(f"DELETE FROM {cl.TABLE_BALANCES} WHERE creator_id = %s", (self.creator_id,))
            connection.execute(f"DELETE FROM {cr.TABLE_ROUNDS} WHERE creator_id = %s", (self.creator_id,))
            connection.execute(f"DELETE FROM {casino_config.TABLE_GAME_CONFIG} WHERE creator_id = %s", (self.creator_id,))
            connection.execute(f"DELETE FROM {casino_config.TABLE_CONFIG} WHERE creator_id = %s", (self.creator_id,))

    # ------------------------------------------------------------------
    def _fund_promo(self, amount):
        cl.credit(
            self.creator_id, self.user_id, cr.CURRENCY_PROMO, amount, cl.PROMO_CONVERT_IN,
            idempotency_key=f"{self.creator_id}-fund-{uuid.uuid4().hex[:8]}",
        )

    def _promo_balance(self):
        return cl.get_balance(self.creator_id, self.user_id, cr.CURRENCY_PROMO)

    def _key(self, suffix="round-1"):
        return f"{self.creator_id}-{suffix}"

    # ------------------------------------------------------------------
    def test_happy_path_win_integer_payout(self):
        self._fund_promo(1000)
        casino_rng.set_provider(_FixedRollProvider(crash.RESOLUTION - 1))  # huge crash_point

        result = crash.play_crash(self.creator_id, self.user_id, 10, "2.50", self._key())

        self.assertEqual(result.state, cr.STATE_SETTLED)
        self.assertEqual(result.outcome, "win")
        self.assertEqual(result.payout, 25)  # floor(10 * 2.50) = 25, exact
        self.assertIsInstance(result.payout, int)
        self.assertEqual(self._promo_balance(), 1000 - 10 + 25)

    def test_happy_path_loss(self):
        self._fund_promo(1000)
        casino_rng.set_provider(_FixedRollProvider(0))  # crash_point == 1.0

        result = crash.play_crash(self.creator_id, self.user_id, 10, "2.50", self._key())

        self.assertEqual(result.outcome, "loss")
        self.assertEqual(result.payout, 0)
        self.assertEqual(self._promo_balance(), 1000 - 10)

    def test_exact_crash_point_equal_to_target_is_a_win(self):
        """crash_point >= target is a WIN (>=, not >) -- landing exactly
        on the target still pays."""
        self._fund_promo(1000)
        # Force r such that (1-he)/(1-r) == exactly 2.0 -> r = 1 - (1-he)/2
        target = 2.0
        wanted_r = 1 - (1 - crash.HOUSE_EDGE) / target
        n = round(wanted_r * crash.RESOLUTION)
        casino_rng.set_provider(_FixedRollProvider(n))

        result = crash.play_crash(self.creator_id, self.user_id, 10, "2.00", self._key())
        self.assertAlmostEqual(result.metadata["crash_point"], target, places=6)
        self.assertEqual(result.outcome, "win")

    def test_integer_payout_never_float(self):
        self._fund_promo(10000)
        casino_rng.set_provider(_FixedRollProvider(crash.RESOLUTION - 1))

        for bet, target in ((3, "2.50"), (7, "1.33"), (1, "99.99")):
            result = crash.play_crash(self.creator_id, self.user_id, bet, target, self._key(f"payout-{target}"))
            self.assertIsInstance(result.payout, int)

    # ------------------------------------------------------------------
    # THE ANTI-EXPLOIT PROOF for crash's structured outcome: a player who
    # saw a loss and retries must get the SAME loss -- including the SAME
    # persisted crash_point and target -- never a fresh roll.
    # ------------------------------------------------------------------
    def test_resume_after_loss_reuses_structured_outcome_never_rerolls(self):
        self._fund_promo(1000)
        key = self._key()

        casino_rng.set_provider(_FixedRollProvider(0))  # crash_point == 1.0 -> loss vs target 2.00
        first = crash.play_crash(self.creator_id, self.user_id, 10, "2.00", key)
        self.assertEqual(first.outcome, "loss")
        self.assertEqual(first.metadata["crash_point"], 1.0)

        # Rig the RNG so that IF it rolled again, it would now win big.
        casino_rng.set_provider(_FixedRollProvider(crash.RESOLUTION - 1))
        second = crash.play_crash(self.creator_id, self.user_id, 10, "2.00", key)

        self.assertTrue(second.replayed)
        self.assertEqual(second.outcome, "loss", "must reuse the persisted loss, not re-roll into a win")
        self.assertEqual(second.payout, 0)
        self.assertEqual(second.metadata["crash_point"], 1.0, "must reuse the persisted crash_point")
        self.assertEqual(second.metadata["target"], 2.0, "must reuse the persisted target, not re-read a new one")
        self.assertEqual(self._promo_balance(), 1000 - 10, "no phantom payout from a re-roll")

    def test_resume_between_decide_and_settle_reuses_structured_outcome(self):
        self._fund_promo(1000)
        key = self._key()

        cl.debit(
            self.creator_id, self.user_id, cr.CURRENCY_PROMO, 10, cl.PROMO_WAGER,
            round_id=key, idempotency_key=f"{key}-wager",
        )
        with cr._connect() as connection:
            cr._ensure_schema(connection)
            cr._claim_round(connection, key, self.creator_id, self.user_id, crash.GAME_ID, 10)

        forced_metadata = {"crash_point": 1.10, "target": 2.00, "bet": 10, "payout": 0, "won": False}
        outcome, payout, meta = cr._decide_outcome(
            key, lambda: ("loss", 0, forced_metadata), cr.DEFAULT_CONNECT_TIMEOUT_SECONDS,
        )
        self.assertEqual(outcome, "loss")

        with cl._connect() as connection:
            state = connection.execute(
                f"SELECT state FROM {cr.TABLE_ROUNDS} WHERE round_id = %s", (key,),
            ).fetchone()[0]
        self.assertEqual(state, cr.STATE_FUNDED, "not yet settled -- this is the engineered crash point")

        casino_rng.set_provider(_FixedRollProvider(crash.RESOLUTION - 1))
        result = crash.play_crash(self.creator_id, self.user_id, 10, "2.00", key)

        self.assertEqual(result.outcome, "loss")
        self.assertEqual(result.state, cr.STATE_SETTLED)
        self.assertEqual(result.metadata["crash_point"], 1.10, "must reuse the persisted crash_point, not a rearmed roll")
        self.assertEqual(self._promo_balance(), 1000 - 10)

    def test_crash_mid_round_resume_settles_correctly(self):
        self._fund_promo(1000)
        key = self._key()

        cl.debit(
            self.creator_id, self.user_id, cr.CURRENCY_PROMO, 10, cl.PROMO_WAGER,
            round_id=key, idempotency_key=f"{key}-wager",
        )
        with cr._connect() as connection:
            cr._ensure_schema(connection)
            cr._claim_round(connection, key, self.creator_id, self.user_id, crash.GAME_ID, 10)

        self.assertEqual(self._promo_balance(), 1000 - 10, "wager already landed before the 'crash'")

        casino_rng.set_provider(_FixedRollProvider(crash.RESOLUTION - 1))
        result = crash.play_crash(self.creator_id, self.user_id, 10, "2.00", key)

        self.assertEqual(result.state, cr.STATE_SETTLED)
        self.assertEqual(result.outcome, "win")
        self.assertEqual(self._promo_balance(), 1000 - 10 + result.payout, "not debited twice, payout landed once")

        with cl._connect() as connection:
            wager_rows = connection.execute(
                f"SELECT COUNT(*) FROM {cl.TABLE_LEDGER} WHERE round_id = %s AND type = %s",
                (key, cl.PROMO_WAGER),
            ).fetchone()[0]
        self.assertEqual(wager_rows, 1, "resume must not re-debit the wager")

    def test_wager_and_round_idempotent_on_replay(self):
        self._fund_promo(1000)
        casino_rng.set_provider(_FixedRollProvider(crash.RESOLUTION - 1))
        key = self._key()

        results = [
            crash.play_crash(self.creator_id, self.user_id, 10, "2.00", key)
            for _ in range(5)
        ]

        self.assertFalse(results[0].replayed)
        for later in results[1:]:
            self.assertTrue(later.replayed)
            self.assertEqual(later.outcome, results[0].outcome)
            self.assertEqual(later.metadata, results[0].metadata)

        self.assertEqual(self._promo_balance(), 1000 - 10 + results[0].payout)

    # ------------------------------------------------------------------
    def test_insufficient_promo_rejected_no_round_created(self):
        self._fund_promo(5)
        key = self._key()

        with self.assertRaises(cl.InsufficientFunds):
            crash.play_crash(self.creator_id, self.user_id, 10, "2.00", key)

        self.assertEqual(self._promo_balance(), 5)
        with cl._connect() as connection:
            round_rows = connection.execute(
                f"SELECT COUNT(*) FROM {cr.TABLE_ROUNDS} WHERE round_id = %s", (key,),
            ).fetchone()[0]
        self.assertEqual(round_rows, 0)

    def test_invalid_target_rejected_no_round_created(self):
        self._fund_promo(1000)
        for bad_target in ("1.00", "0", "-5", "abc", "1000", "2.555"):
            key = self._key(f"bad-{bad_target}")
            with self.assertRaises(ValueError):
                crash.play_crash(self.creator_id, self.user_id, 10, bad_target, key)
            with cl._connect() as connection:
                round_rows = connection.execute(
                    f"SELECT COUNT(*) FROM {cr.TABLE_ROUNDS} WHERE round_id = %s", (key,),
                ).fetchone()[0]
            self.assertEqual(round_rows, 0, f"{bad_target!r} must not create a round or touch the ledger")
        self.assertEqual(self._promo_balance(), 1000)

    # ------------------------------------------------------------------
    def test_config_disabled_game_rejected(self):
        casino_config.set_game_config(self.creator_id, crash.GAME_ID, enabled=False)
        self._fund_promo(1000)

        with self.assertRaises(cr.GameDisabled):
            crash.play_crash(self.creator_id, self.user_id, 10, "2.00", self._key())

        self.assertEqual(self._promo_balance(), 1000)

    def test_config_bet_out_of_range_rejected(self):
        casino_config.set_game_config(self.creator_id, crash.GAME_ID, min_bet=5, max_bet=50)
        self._fund_promo(1000)

        with self.assertRaises(cr.BetOutOfRange):
            crash.play_crash(self.creator_id, self.user_id, 1, "2.00", self._key("too-low"))
        with self.assertRaises(cr.BetOutOfRange):
            crash.play_crash(self.creator_id, self.user_id, 500, "2.00", self._key("too-high"))

        self.assertEqual(self._promo_balance(), 1000)


if __name__ == "__main__":
    unittest.main()
