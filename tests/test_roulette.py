"""Round-model correctness tests for games/roulette.py (Casino Phase 6):
same play_round() lifecycle proven in tests/test_casino_rounds.py for
coinflip, now exercised with roulette's richer structured metadata
({"bet_type", "selection", "pocket", "color", "won"}).

No changes were made to services/casino_rounds.py or games/coinflip.py to
build roulette -- see games/roulette.py's module docstring. This file's
job is to prove that claim: every anti-exploit/crash-resume guarantee
that holds for coinflip's simple metadata also holds for roulette's
richer one, using the identical, unmodified play_round() machinery.

Same convention as test_casino_rounds.py: real Postgres via DATABASE_URL,
skipped (not faked) without one.
    python -m unittest tests.test_roulette -v
"""

import os
import sys
import unittest
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import games.roulette as roulette  # noqa: E402
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
    """Deterministic stand-in for SecureRandomProvider -- roll() always
    returns `value` regardless of range, never touches `secrets`."""

    def __init__(self, value):
        self.value = value

    def roll(self, minimum, maximum):
        return self.value

    def choice(self, seq):
        return seq[0]


@unittest.skipUnless(DATABASE_CONFIGURED, SKIP_REASON)
class RouletteRoundsTestCase(unittest.TestCase):
    def setUp(self):
        self.creator_id = f"test-roulette-{uuid.uuid4().hex[:12]}"
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
    # Every bet type resolves + pays correctly.
    # ------------------------------------------------------------------
    def test_straight_number_win_pays_35_to_1(self):
        self._fund_promo(1000)
        casino_rng.set_provider(_FixedRollProvider(17))

        result = roulette.play_roulette(self.creator_id, self.user_id, "number", 17, 10, self._key())

        self.assertEqual(result.outcome, "win")
        self.assertEqual(result.payout, 360)  # 10 * 36
        self.assertEqual(result.metadata["pocket"], 17)
        self.assertEqual(self._promo_balance(), 1000 - 10 + 360)

    def test_straight_number_loss(self):
        self._fund_promo(1000)
        casino_rng.set_provider(_FixedRollProvider(18))

        result = roulette.play_roulette(self.creator_id, self.user_id, "number", 17, 10, self._key())

        self.assertEqual(result.outcome, "loss")
        self.assertEqual(result.payout, 0)
        self.assertEqual(self._promo_balance(), 1000 - 10)

    def test_red_win_pays_1_to_1(self):
        self._fund_promo(1000)
        casino_rng.set_provider(_FixedRollProvider(1))  # 1 is red

        result = roulette.play_roulette(self.creator_id, self.user_id, "red", None, 10, self._key())

        self.assertEqual(result.outcome, "win")
        self.assertEqual(result.payout, 20)
        self.assertEqual(result.metadata["color"], "red")

    def test_black_win(self):
        self._fund_promo(1000)
        casino_rng.set_provider(_FixedRollProvider(2))  # 2 is black

        result = roulette.play_roulette(self.creator_id, self.user_id, "black", None, 10, self._key())

        self.assertEqual(result.outcome, "win")
        self.assertEqual(result.payout, 20)

    def test_even_and_odd(self):
        self._fund_promo(1000)

        casino_rng.set_provider(_FixedRollProvider(4))
        even_result = roulette.play_roulette(self.creator_id, self.user_id, "even", None, 10, self._key("even"))
        self.assertEqual(even_result.outcome, "win")

        casino_rng.set_provider(_FixedRollProvider(5))
        odd_result = roulette.play_roulette(self.creator_id, self.user_id, "odd", None, 10, self._key("odd"))
        self.assertEqual(odd_result.outcome, "win")

    def test_high_and_low(self):
        self._fund_promo(1000)

        casino_rng.set_provider(_FixedRollProvider(30))
        high_result = roulette.play_roulette(self.creator_id, self.user_id, "high", None, 10, self._key("high"))
        self.assertEqual(high_result.outcome, "win")

        casino_rng.set_provider(_FixedRollProvider(5))
        low_result = roulette.play_roulette(self.creator_id, self.user_id, "low", None, 10, self._key("low"))
        self.assertEqual(low_result.outcome, "win")

    def test_dozen_win_pays_2_to_1(self):
        self._fund_promo(1000)
        casino_rng.set_provider(_FixedRollProvider(15))  # 2nd dozen (13-24)

        result = roulette.play_roulette(self.creator_id, self.user_id, "dozen", 2, 10, self._key())

        self.assertEqual(result.outcome, "win")
        self.assertEqual(result.payout, 30)  # 10 * 3

    def test_dozen_loss(self):
        self._fund_promo(1000)
        casino_rng.set_provider(_FixedRollProvider(15))  # 2nd dozen, betting on 1st

        result = roulette.play_roulette(self.creator_id, self.user_id, "dozen", 1, 10, self._key())

        self.assertEqual(result.outcome, "loss")

    def test_column_win_pays_2_to_1(self):
        self._fund_promo(1000)
        casino_rng.set_provider(_FixedRollProvider(5))  # column 2 (2,5,8,...)

        result = roulette.play_roulette(self.creator_id, self.user_id, "column", 2, 10, self._key())

        self.assertEqual(result.outcome, "win")
        self.assertEqual(result.payout, 30)

    # ------------------------------------------------------------------
    # 0 behavior: green loses every outside bet; straight-up on 0 can win.
    # ------------------------------------------------------------------
    def test_zero_loses_every_outside_bet(self):
        self._fund_promo(1000)
        outside_bets = [
            ("red", None), ("black", None), ("even", None), ("odd", None),
            ("high", None), ("low", None), ("dozen", 1), ("column", 1),
        ]
        for bet_type, selection in outside_bets:
            casino_rng.set_provider(_FixedRollProvider(0))
            result = roulette.play_roulette(
                self.creator_id, self.user_id, bet_type, selection, 10,
                self._key(f"zero-{bet_type}"),
            )
            self.assertEqual(result.outcome, "loss", f"{bet_type} must lose on 0")
            self.assertEqual(result.metadata["color"], "green")

    def test_straight_up_zero_can_win(self):
        self._fund_promo(1000)
        casino_rng.set_provider(_FixedRollProvider(0))

        result = roulette.play_roulette(self.creator_id, self.user_id, "number", 0, 10, self._key())

        self.assertEqual(result.outcome, "win", "a straight-up bet on 0 must be able to win")
        self.assertEqual(result.payout, 360)

    # ------------------------------------------------------------------
    # THE ANTI-EXPLOIT PROOF for roulette's structured outcome: a player
    # who saw a loss and retries must get the SAME loss -- including the
    # SAME persisted winning_number and bet -- never a fresh roll, even
    # though roulette's metadata is far richer than coinflip's.
    # ------------------------------------------------------------------
    def test_resume_after_loss_reuses_structured_outcome_never_rerolls(self):
        self._fund_promo(1000)
        key = self._key()

        casino_rng.set_provider(_FixedRollProvider(2))  # bet red, pocket 2 is black -> loss
        first = roulette.play_roulette(self.creator_id, self.user_id, "red", None, 10, key)
        self.assertEqual(first.outcome, "loss")
        self.assertEqual(first.metadata["pocket"], 2)
        self.assertEqual(first.metadata["color"], "black")

        # Rig the RNG so that IF it rolled again, red would now win.
        casino_rng.set_provider(_FixedRollProvider(1))  # 1 is red
        second = roulette.play_roulette(self.creator_id, self.user_id, "red", None, 10, key)

        self.assertTrue(second.replayed)
        self.assertEqual(second.outcome, "loss", "must reuse the persisted loss, not re-roll into a win")
        self.assertEqual(second.payout, 0)
        self.assertEqual(second.metadata["pocket"], 2, "must reuse the persisted winning_number, not a new one")
        self.assertEqual(second.metadata["bet_type"], "red", "must reuse the persisted bet, not re-read a new one")
        self.assertEqual(self._promo_balance(), 1000 - 10, "no phantom payout from a re-roll")

    def test_resume_between_decide_and_settle_reuses_structured_outcome(self):
        """Same proof, engineered at the exact 'funded, outcome decided,
        not yet settled' crash point rather than after full settlement."""
        self._fund_promo(1000)
        key = self._key()

        cl.debit(
            self.creator_id, self.user_id, cr.CURRENCY_PROMO, 10, cl.PROMO_WAGER,
            round_id=key, idempotency_key=f"{key}-wager",
        )
        with cr._connect() as connection:
            cr._ensure_schema(connection)
            cr._claim_round(connection, key, self.creator_id, self.user_id, roulette.GAME_ID, 10)

        forced_metadata = {
            "bet_type": "number", "selection": 17, "pocket": 4, "color": "black", "won": False,
        }
        outcome, payout, meta = cr._decide_outcome(
            key, lambda: ("loss", 0, forced_metadata), cr.DEFAULT_CONNECT_TIMEOUT_SECONDS,
        )
        self.assertEqual(outcome, "loss")

        with cl._connect() as connection:
            state = connection.execute(
                f"SELECT state FROM {cr.TABLE_ROUNDS} WHERE round_id = %s", (key,),
            ).fetchone()[0]
        self.assertEqual(state, cr.STATE_FUNDED, "not yet settled -- this is the engineered crash point")

        # resolve() would win a fresh roll if called again -- prove it isn't.
        casino_rng.set_provider(_FixedRollProvider(17))
        result = roulette.play_roulette(self.creator_id, self.user_id, "number", 17, 10, key)

        self.assertEqual(result.outcome, "loss")
        self.assertEqual(result.state, cr.STATE_SETTLED)
        self.assertEqual(result.metadata["pocket"], 4, "must reuse the persisted pocket, not the rearmed roll of 17")
        self.assertEqual(self._promo_balance(), 1000 - 10)

    # ------------------------------------------------------------------
    def test_crash_mid_round_resume_settles_correctly(self):
        self._fund_promo(1000)
        key = self._key()

        cl.debit(
            self.creator_id, self.user_id, cr.CURRENCY_PROMO, 10, cl.PROMO_WAGER,
            round_id=key, idempotency_key=f"{key}-wager",
        )
        with cr._connect() as connection:
            cr._ensure_schema(connection)
            cr._claim_round(connection, key, self.creator_id, self.user_id, roulette.GAME_ID, 10)

        self.assertEqual(self._promo_balance(), 1000 - 10, "wager already landed before the 'crash'")

        casino_rng.set_provider(_FixedRollProvider(1))  # red
        result = roulette.play_roulette(self.creator_id, self.user_id, "red", None, 10, key)

        self.assertEqual(result.state, cr.STATE_SETTLED)
        self.assertEqual(result.outcome, "win")
        self.assertEqual(self._promo_balance(), 1000 - 10 + 20, "not debited twice, payout landed once")

        with cl._connect() as connection:
            wager_rows = connection.execute(
                f"SELECT COUNT(*) FROM {cl.TABLE_LEDGER} WHERE round_id = %s AND type = %s",
                (key, cl.PROMO_WAGER),
            ).fetchone()[0]
        self.assertEqual(wager_rows, 1, "resume must not re-debit the wager")

    def test_wager_and_round_idempotent_on_replay(self):
        self._fund_promo(1000)
        casino_rng.set_provider(_FixedRollProvider(1))
        key = self._key()

        results = [
            roulette.play_roulette(self.creator_id, self.user_id, "red", None, 10, key)
            for _ in range(5)
        ]

        self.assertFalse(results[0].replayed)
        for later in results[1:]:
            self.assertTrue(later.replayed)
            self.assertEqual(later.outcome, results[0].outcome)
            self.assertEqual(later.metadata, results[0].metadata)

        self.assertEqual(self._promo_balance(), 1000 - 10 + 20)

    # ------------------------------------------------------------------
    def test_insufficient_promo_rejected_no_round_created(self):
        self._fund_promo(5)
        key = self._key()

        with self.assertRaises(cl.InsufficientFunds):
            roulette.play_roulette(self.creator_id, self.user_id, "red", None, 10, key)

        self.assertEqual(self._promo_balance(), 5)
        with cl._connect() as connection:
            round_rows = connection.execute(
                f"SELECT COUNT(*) FROM {cr.TABLE_ROUNDS} WHERE round_id = %s", (key,),
            ).fetchone()[0]
        self.assertEqual(round_rows, 0)

    # ------------------------------------------------------------------
    def test_invalid_bet_type_rejected(self):
        self._fund_promo(1000)
        with self.assertRaises(ValueError):
            roulette.play_roulette(self.creator_id, self.user_id, "sideways", None, 10, self._key())

    def test_number_out_of_range_rejected(self):
        self._fund_promo(1000)
        with self.assertRaises(ValueError):
            roulette.play_roulette(self.creator_id, self.user_id, "number", 37, 10, self._key())
        with self.assertRaises(ValueError):
            roulette.play_roulette(self.creator_id, self.user_id, "number", -1, 10, self._key("neg"))

    def test_dozen_column_out_of_range_rejected(self):
        self._fund_promo(1000)
        with self.assertRaises(ValueError):
            roulette.play_roulette(self.creator_id, self.user_id, "dozen", 4, 10, self._key("dozen"))
        with self.assertRaises(ValueError):
            roulette.play_roulette(self.creator_id, self.user_id, "column", 0, 10, self._key("column"))

    # ------------------------------------------------------------------
    def test_config_disabled_game_rejected(self):
        casino_config.set_game_config(self.creator_id, roulette.GAME_ID, enabled=False)
        self._fund_promo(1000)

        with self.assertRaises(cr.GameDisabled):
            roulette.play_roulette(self.creator_id, self.user_id, "red", None, 10, self._key())

        self.assertEqual(self._promo_balance(), 1000)

    def test_config_bet_out_of_range_rejected(self):
        casino_config.set_game_config(self.creator_id, roulette.GAME_ID, min_bet=5, max_bet=50)
        self._fund_promo(1000)

        with self.assertRaises(cr.BetOutOfRange):
            roulette.play_roulette(self.creator_id, self.user_id, "red", None, 1, self._key("too-low"))
        with self.assertRaises(cr.BetOutOfRange):
            roulette.play_roulette(self.creator_id, self.user_id, "red", None, 500, self._key("too-high"))

        self.assertEqual(self._promo_balance(), 1000)


if __name__ == "__main__":
    unittest.main()
