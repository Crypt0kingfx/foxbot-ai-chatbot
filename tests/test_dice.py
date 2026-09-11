"""Tests for games/dice.py (Casino Phase 10).

Two groups, same split as test_crash.py:
  - DiceMathTestCase: NO DATABASE_URL required. The RTP simulation calls
    the resolver's pure logic directly (roll + payout formula), not the
    full DB-backed round lifecycle -- matches the pattern already
    established for crash's RTP proof.
  - DiceRoundsTestCase: real Postgres via DATABASE_URL, same convention
    as test_roulette.py/test_crash.py.

Run with:
    python -m unittest tests.test_dice -v
"""

import os
import sys
import unittest
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import games.dice as dice  # noqa: E402
import services.casino_config as casino_config  # noqa: E402
import services.casino_ledger as cl  # noqa: E402
import services.casino_rng as casino_rng  # noqa: E402
import services.casino_rounds as cr  # noqa: E402


from tests.db_guard import DATABASE_CONFIGURED, SKIP_REASON  # noqa: E402


class _FixedRollProvider(casino_rng.RNGProvider):
    def __init__(self, value):
        self.value = value

    def roll(self, minimum, maximum):
        return self.value

    def choice(self, seq):
        return seq[0]


class DiceMathTestCase(unittest.TestCase):
    """No DATABASE_URL required -- pure math/validation proofs."""

    def setUp(self):
        self._original_rng_provider = casino_rng.get_provider()

    def tearDown(self):
        casino_rng.set_provider(self._original_rng_provider)

    # ------------------------------------------------------------------
    def test_wins_high_low_exact(self):
        self.assertTrue(dice._wins("high", 4))
        self.assertTrue(dice._wins("high", 6))
        self.assertFalse(dice._wins("high", 3))
        self.assertTrue(dice._wins("low", 1))
        self.assertTrue(dice._wins("low", 3))
        self.assertFalse(dice._wins("low", 4))
        self.assertTrue(dice._wins("6", 6))
        self.assertFalse(dice._wins("6", 5))

    def test_invalid_prediction_rejected(self):
        with self.assertRaises(ValueError):
            dice.play_dice("creator", "user", "7", 10, "round-1")
        with self.assertRaises(ValueError):
            dice.play_dice("creator", "user", "sideways", 10, "round-2")
        with self.assertRaises(ValueError):
            dice.play_dice("creator", "user", "0", 10, "round-3")

    # ------------------------------------------------------------------
    # THE RTP PROOF: simulate 2M rolls with the REAL CSPRNG (not a fixed
    # stand-in) at two wager sizes -- large (no incidental flooring, shows
    # the true 97% target) and small (shows the expected, documented
    # floor-rounding effect, not a bug).
    # ------------------------------------------------------------------
    def test_rtp_simulation_high_low_large_wager(self):
        casino_rng.set_provider(casino_rng.SecureRandomProvider())
        n_rounds = 2_000_000
        wager = 1000  # divisible by 100 -- no incidental flooring loss

        total_wagered = 0
        total_paid = 0
        for _ in range(n_rounds):
            roll = casino_rng.roll(1, 6)
            won = dice._wins("high", roll)
            payout = (wager * dice.PAYOUT_PERCENT_EVEN_MONEY) // 100 if won else 0
            total_wagered += wager
            total_paid += payout

        rtp = total_paid / total_wagered
        print(f"\n[DICE RTP] high/low wager=1000 n={n_rounds} RTP={rtp:.4f} (expected ~0.9700)")
        self.assertAlmostEqual(rtp, 0.97, delta=0.01)

    def test_rtp_simulation_exact_number_large_wager(self):
        casino_rng.set_provider(casino_rng.SecureRandomProvider())
        n_rounds = 2_000_000
        wager = 1000

        total_wagered = 0
        total_paid = 0
        for _ in range(n_rounds):
            roll = casino_rng.roll(1, 6)
            won = dice._wins("6", roll)
            payout = (wager * dice.PAYOUT_PERCENT_EXACT_NUMBER) // 100 if won else 0
            total_wagered += wager
            total_paid += payout

        rtp = total_paid / total_wagered
        print(f"[DICE RTP] exact-number wager=1000 n={n_rounds} RTP={rtp:.4f} (expected ~0.9700)")
        self.assertAlmostEqual(rtp, 0.97, delta=0.01)

    def test_small_wager_floors_the_effective_multiplier_as_documented(self):
        """Not a bug: at wager=10, (10*194)//100=19 (effective 1.90x, not
        1.94x) -- documented in games/dice.py's own module docstring.
        Confirms this precisely rather than asserting it in prose only."""
        wager = 10
        payout = (wager * dice.PAYOUT_PERCENT_EVEN_MONEY) // 100
        self.assertEqual(payout, 19)
        self.assertLess(payout / wager, 1.94)

        payout_exact = (wager * dice.PAYOUT_PERCENT_EXACT_NUMBER) // 100
        self.assertEqual(payout_exact, 58)
        self.assertLess(payout_exact / wager, 5.82)

    def test_rng_is_server_only_no_client_outcome_param(self):
        import inspect
        sig = inspect.signature(dice.play_dice)
        forbidden = {"outcome", "result", "roll", "won", "win"}
        self.assertTrue(
            forbidden.isdisjoint(sig.parameters.keys()),
            f"play_dice must not accept a client-supplied outcome, got {list(sig.parameters)}",
        )


@unittest.skipUnless(DATABASE_CONFIGURED, SKIP_REASON)
class DiceRoundsTestCase(unittest.TestCase):
    def setUp(self):
        self.creator_id = f"test-dice-{uuid.uuid4().hex[:12]}"
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
    def test_high_win(self):
        self._fund_promo(1000)
        casino_rng.set_provider(_FixedRollProvider(6))

        result = dice.play_dice(self.creator_id, self.user_id, "high", 100, self._key())

        self.assertEqual(result.outcome, "win")
        self.assertEqual(result.payout, 194)  # (100*194)//100
        self.assertEqual(self._promo_balance(), 1000 - 100 + 194)

    def test_high_loss(self):
        self._fund_promo(1000)
        casino_rng.set_provider(_FixedRollProvider(2))

        result = dice.play_dice(self.creator_id, self.user_id, "high", 100, self._key())

        self.assertEqual(result.outcome, "loss")
        self.assertEqual(result.payout, 0)
        self.assertEqual(self._promo_balance(), 900)

    def test_low_win(self):
        self._fund_promo(1000)
        casino_rng.set_provider(_FixedRollProvider(1))

        result = dice.play_dice(self.creator_id, self.user_id, "low", 100, self._key())

        self.assertEqual(result.outcome, "win")
        self.assertEqual(result.payout, 194)

    def test_exact_number_win(self):
        self._fund_promo(1000)
        casino_rng.set_provider(_FixedRollProvider(6))

        result = dice.play_dice(self.creator_id, self.user_id, "6", 100, self._key())

        self.assertEqual(result.outcome, "win")
        self.assertEqual(result.payout, 582)  # (100*582)//100
        self.assertEqual(self._promo_balance(), 1000 - 100 + 582)

    def test_exact_number_loss(self):
        self._fund_promo(1000)
        casino_rng.set_provider(_FixedRollProvider(6))

        result = dice.play_dice(self.creator_id, self.user_id, "3", 100, self._key())

        self.assertEqual(result.outcome, "loss")
        self.assertEqual(result.payout, 0)

    # ------------------------------------------------------------------
    # THE ANTI-EXPLOIT PROOF: rig a loss, persist it, re-arm RNG to a
    # winning roll, retry same round_id -> still original loss.
    # ------------------------------------------------------------------
    def test_resume_after_loss_reuses_roll_never_rerolls(self):
        self._fund_promo(1000)
        key = self._key()

        casino_rng.set_provider(_FixedRollProvider(1))  # predicting high -> loss
        first = dice.play_dice(self.creator_id, self.user_id, "high", 100, key)
        self.assertEqual(first.outcome, "loss")
        self.assertEqual(first.metadata["roll"], 1)

        casino_rng.set_provider(_FixedRollProvider(6))  # rearmed to a winning roll
        second = dice.play_dice(self.creator_id, self.user_id, "high", 100, key)

        self.assertTrue(second.replayed)
        self.assertEqual(second.outcome, "loss", "must reuse the persisted loss, not re-roll into a win")
        self.assertEqual(second.metadata["roll"], 1, "must reuse the persisted roll, not the rearmed one")
        self.assertEqual(self._promo_balance(), 900, "no phantom payout from a re-roll")

    def test_resume_between_decide_and_settle_reuses_roll(self):
        self._fund_promo(1000)
        key = self._key()

        cl.debit(
            self.creator_id, self.user_id, cr.CURRENCY_PROMO, 100, cl.PROMO_WAGER,
            round_id=key, idempotency_key=f"{key}-wager",
        )
        with cr._connect() as connection:
            cr._ensure_schema(connection)
            cr._claim_round(connection, key, self.creator_id, self.user_id, dice.GAME_ID, 100)

        forced_metadata = {"prediction": "high", "roll": 2, "won": False}
        outcome, payout, meta = cr._decide_outcome(
            key, lambda: ("loss", 0, forced_metadata), cr.DEFAULT_CONNECT_TIMEOUT_SECONDS,
        )
        self.assertEqual(outcome, "loss")

        casino_rng.set_provider(_FixedRollProvider(6))
        result = dice.play_dice(self.creator_id, self.user_id, "high", 100, key)

        self.assertEqual(result.outcome, "loss")
        self.assertEqual(result.metadata["roll"], 2, "must reuse the persisted roll, not a rearmed one")

    # ------------------------------------------------------------------
    def test_wager_and_round_idempotent_on_replay(self):
        self._fund_promo(1000)
        casino_rng.set_provider(_FixedRollProvider(6))
        key = self._key()

        results = [dice.play_dice(self.creator_id, self.user_id, "high", 100, key) for _ in range(5)]

        self.assertFalse(results[0].replayed)
        for later in results[1:]:
            self.assertTrue(later.replayed)
            self.assertEqual(later.outcome, results[0].outcome)
            self.assertEqual(later.metadata, results[0].metadata)

        self.assertEqual(self._promo_balance(), 1000 - 100 + 194)

    # ------------------------------------------------------------------
    def test_insufficient_promo_rejected_no_round_created(self):
        self._fund_promo(5)
        key = self._key()

        with self.assertRaises(cl.InsufficientFunds):
            dice.play_dice(self.creator_id, self.user_id, "high", 100, key)

        self.assertEqual(self._promo_balance(), 5)
        with cl._connect() as connection:
            round_rows = connection.execute(
                f"SELECT COUNT(*) FROM {cr.TABLE_ROUNDS} WHERE round_id = %s", (key,),
            ).fetchone()[0]
        self.assertEqual(round_rows, 0)

    def test_invalid_prediction_rejected_no_round_created(self):
        self._fund_promo(1000)
        key = self._key()
        with self.assertRaises(ValueError):
            dice.play_dice(self.creator_id, self.user_id, "sideways", 100, key)
        with cl._connect() as connection:
            round_rows = connection.execute(
                f"SELECT COUNT(*) FROM {cr.TABLE_ROUNDS} WHERE round_id = %s", (key,),
            ).fetchone()[0]
        self.assertEqual(round_rows, 0)

    # ------------------------------------------------------------------
    def test_config_disabled_game_rejected(self):
        casino_config.set_game_config(self.creator_id, dice.GAME_ID, enabled=False)
        self._fund_promo(1000)

        with self.assertRaises(cr.GameDisabled):
            dice.play_dice(self.creator_id, self.user_id, "high", 100, self._key())

        self.assertEqual(self._promo_balance(), 1000)

    def test_config_bet_out_of_range_rejected(self):
        casino_config.set_game_config(self.creator_id, dice.GAME_ID, min_bet=5, max_bet=50)
        self._fund_promo(1000)

        with self.assertRaises(cr.BetOutOfRange):
            dice.play_dice(self.creator_id, self.user_id, "high", 1, self._key("too-low"))
        with self.assertRaises(cr.BetOutOfRange):
            dice.play_dice(self.creator_id, self.user_id, "high", 500, self._key("too-high"))

        self.assertEqual(self._promo_balance(), 1000)


if __name__ == "__main__":
    unittest.main()
