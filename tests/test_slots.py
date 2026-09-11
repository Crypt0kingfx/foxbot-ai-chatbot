"""Tests for games/slots.py (Casino Phase 10).

Two groups, same split as test_crash.py/test_dice.py:
  - SlotsMathTestCase: NO DATABASE_URL required. The RTP simulation,
    symbol-weight distribution check, and paytable structure tests call
    the resolver's pure logic directly.
  - SlotsRoundsTestCase: real Postgres via DATABASE_URL.

ON THE RTP TOLERANCE: slots' 2650x jackpot multiplier makes this a
genuinely high-variance game -- the theoretical standard error of the
RTP estimate at n=100,000 spins is ~5.1 percentage points (computed from
the payout distribution's variance: Var=264.9, stdev=16.28, SE=stdev/
sqrt(n)), so a 3-sigma band at 100k spins is +/-15.4 points around the
true 96.465% RTP. That is not a flaky test artifact -- it is the honest
statistical reality of a rare, huge-payout jackpot event. This file runs
BOTH tiers so the RTP claim is proven, not just asserted once at a
sample size too small to mean much:
  - test_rtp_simulation_100k_spins_wide_tolerance: exactly the 100k+
    spins requested, with a tolerance wide enough (3-sigma-justified) to
    not be a coin flip on jackpot variance, while still tight enough to
    catch a badly broken paytable (e.g. off by 10x).
  - test_rtp_simulation_large_n_tight_tolerance: a much larger sample
    (5M spins) where 3-sigma narrows to ~+/-2.2 points, giving the real,
    tight confirmation that the paytable+weights land the intended edge.

Run with:
    python -m unittest tests.test_slots -v
"""

import os
import sys
import unittest
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import games.slots as slots  # noqa: E402
import services.casino_config as casino_config  # noqa: E402
import services.casino_ledger as cl  # noqa: E402
import services.casino_rng as casino_rng  # noqa: E402
import services.casino_rounds as cr  # noqa: E402


from tests.db_guard import DATABASE_CONFIGURED, SKIP_REASON  # noqa: E402

# The exact combinatorial RTP -- computed from WEIGHTS/TRIPLE_PAYOUT/
# PAIR_PAYOUT via exact fractions during design, not simulated. This is
# the ground truth the simulations below are checked against.
EXACT_RTP = 0.964653754489611


class _FixedChoiceProvider(casino_rng.RNGProvider):
    """Always returns a specific symbol from choice() regardless of the
    weighted pool passed in -- used to force specific reel outcomes."""

    def __init__(self, value):
        self.value = value

    def roll(self, minimum, maximum):
        return minimum

    def choice(self, seq):
        return self.value


class _SequenceChoiceProvider(casino_rng.RNGProvider):
    """Returns successive values from a fixed sequence on each choice()
    call -- used to force three DIFFERENT specific reels in one spin."""

    def __init__(self, sequence):
        self._sequence = list(sequence)
        self._index = 0

    def roll(self, minimum, maximum):
        return minimum

    def choice(self, seq):
        value = self._sequence[self._index]
        self._index += 1
        return value


def _resolve_spin(rng_choice_fn):
    """Mirrors games/slots.py's _resolve() combo-evaluation logic exactly
    (triple/pair/no-match), driven by an explicit reel-choice function --
    used by the pure-math simulation so it doesn't need a real
    casino_rng provider swap per spin (much faster for millions of
    iterations)."""
    r1, r2, r3 = rng_choice_fn(), rng_choice_fn(), rng_choice_fn()
    if r1 == r2 == r3:
        mult = slots.TRIPLE_PAYOUT[r1]
    elif r1 == r2 or r2 == r3 or r1 == r3:
        pair_symbol = r1 if (r1 == r2 or r1 == r3) else r2
        mult = slots.PAIR_PAYOUT.get(pair_symbol, 0)
    else:
        mult = 0
    return mult


class SlotsMathTestCase(unittest.TestCase):
    """No DATABASE_URL required -- pure math/validation proofs."""

    def setUp(self):
        self._original_rng_provider = casino_rng.get_provider()

    def tearDown(self):
        casino_rng.set_provider(self._original_rng_provider)

    # ------------------------------------------------------------------
    def test_weighted_pool_has_correct_composition(self):
        counts = {s: slots._WEIGHTED_SYMBOLS.count(s) for s in slots.SYMBOLS}
        self.assertEqual(counts, slots.WEIGHTS)
        self.assertEqual(len(slots._WEIGHTED_SYMBOLS), sum(slots.WEIGHTS.values()))

    def test_symbol_distribution_matches_weights_over_many_draws(self):
        """Confirms casino_rng.choice() over the weighted pool actually
        produces the intended per-symbol probability, using the REAL
        CSPRNG (not a fixed stand-in)."""
        casino_rng.set_provider(casino_rng.SecureRandomProvider())
        n = 500_000
        counts = {s: 0 for s in slots.SYMBOLS}
        for _ in range(n):
            counts[casino_rng.choice(slots._WEIGHTED_SYMBOLS)] += 1

        total_weight = sum(slots.WEIGHTS.values())
        for symbol in slots.SYMBOLS:
            expected_p = slots.WEIGHTS[symbol] / total_weight
            observed_p = counts[symbol] / n
            self.assertAlmostEqual(
                observed_p, expected_p, delta=0.01,
                msg=f"{symbol}: expected p~{expected_p:.4f}, observed {observed_p:.4f}",
            )

    def test_only_fox_and_seven_pairs_pay(self):
        self.assertEqual(set(slots.PAIR_PAYOUT.keys()), {"fox", "seven"})
        for symbol in ("diamond", "rocket", "purple"):
            self.assertEqual(slots.PAIR_PAYOUT.get(symbol, 0), 0)

    def test_triple_fox_is_the_jackpot_highest_payout(self):
        self.assertEqual(max(slots.TRIPLE_PAYOUT.values()), slots.TRIPLE_PAYOUT["fox"])

    def test_paytable_ordering_matches_rarity(self):
        """Rarer symbols must pay more on a triple -- fox > seven >
        diamond > rocket > purple, matching weight 1 < 2 < 4 < 8 < 16."""
        ordered = sorted(slots.SYMBOLS, key=lambda s: slots.WEIGHTS[s])
        payouts_in_rarity_order = [slots.TRIPLE_PAYOUT[s] for s in ordered]
        self.assertEqual(payouts_in_rarity_order, sorted(payouts_in_rarity_order, reverse=True))

    # ------------------------------------------------------------------
    # THE RTP PROOF, two tiers -- see module docstring for why both.
    # ------------------------------------------------------------------
    def test_rtp_simulation_100k_spins_wide_tolerance(self):
        casino_rng.set_provider(casino_rng.SecureRandomProvider())
        n = 100_000
        wager = 10
        total_wagered = 0
        total_paid = 0
        for _ in range(n):
            mult = _resolve_spin(lambda: casino_rng.choice(slots._WEIGHTED_SYMBOLS))
            total_wagered += wager
            total_paid += wager * mult

        rtp = total_paid / total_wagered
        print(f"\n[SLOTS RTP] n={n} RTP={rtp:.4f} (exact target {EXACT_RTP:.4f}, +/-0.16 = 3-sigma at this n)")
        # 3-sigma at n=100k is ~+/-0.154 (see module docstring); 0.16
        # gives a hair of margin while still catching a badly broken
        # paytable (e.g. RTP near 0.5 or near 1.5).
        self.assertAlmostEqual(rtp, EXACT_RTP, delta=0.16)

    def test_rtp_simulation_large_n_tight_tolerance(self):
        casino_rng.set_provider(casino_rng.SecureRandomProvider())
        n = 5_000_000
        wager = 10
        total_wagered = 0
        total_paid = 0
        for _ in range(n):
            mult = _resolve_spin(lambda: casino_rng.choice(slots._WEIGHTED_SYMBOLS))
            total_wagered += wager
            total_paid += wager * mult

        rtp = total_paid / total_wagered
        # 3-sigma at n=5M is ~+/-0.0219; 0.03 gives comfortable margin.
        print(f"[SLOTS RTP] n={n} RTP={rtp:.4f} (exact target {EXACT_RTP:.4f}, +/-0.03 tolerance)")
        self.assertAlmostEqual(rtp, EXACT_RTP, delta=0.03)

    def test_rng_is_server_only_no_client_outcome_param(self):
        import inspect
        sig = inspect.signature(slots.play_slots)
        forbidden = {"outcome", "result", "reels", "combo", "won"}
        self.assertTrue(
            forbidden.isdisjoint(sig.parameters.keys()),
            f"play_slots must not accept a client-supplied outcome, got {list(sig.parameters)}",
        )


@unittest.skipUnless(DATABASE_CONFIGURED, SKIP_REASON)
class SlotsRoundsTestCase(unittest.TestCase):
    def setUp(self):
        self.creator_id = f"test-slots-{uuid.uuid4().hex[:12]}"
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
    def test_triple_fox_jackpot(self):
        self._fund_promo(1000)
        casino_rng.set_provider(_FixedChoiceProvider("fox"))

        result = slots.play_slots(self.creator_id, self.user_id, 10, self._key())

        self.assertEqual(result.outcome, "win")
        self.assertEqual(result.payout, 26500)  # 10 * 2650
        self.assertEqual(result.metadata["combo"], "triple_fox")
        self.assertEqual(result.metadata["reels"], ["fox", "fox", "fox"])

    def test_triple_purple_small_win(self):
        self._fund_promo(1000)
        casino_rng.set_provider(_FixedChoiceProvider("purple"))

        result = slots.play_slots(self.creator_id, self.user_id, 10, self._key())

        self.assertEqual(result.outcome, "win")
        self.assertEqual(result.payout, 20)  # 10 * 2

    def test_pair_fox_wins(self):
        self._fund_promo(1000)
        casino_rng.set_provider(_SequenceChoiceProvider(["fox", "fox", "purple"]))

        result = slots.play_slots(self.creator_id, self.user_id, 10, self._key())

        self.assertEqual(result.outcome, "win")
        self.assertEqual(result.payout, 380)  # 10 * 38
        self.assertEqual(result.metadata["combo"], "pair_fox")

    def test_pair_purple_pays_nothing(self):
        self._fund_promo(1000)
        casino_rng.set_provider(_SequenceChoiceProvider(["purple", "purple", "fox"]))

        result = slots.play_slots(self.creator_id, self.user_id, 10, self._key())

        self.assertEqual(result.outcome, "loss")
        self.assertEqual(result.payout, 0)
        self.assertEqual(result.metadata["combo"], "no_win")

    def test_no_match_loses(self):
        self._fund_promo(1000)
        casino_rng.set_provider(_SequenceChoiceProvider(["fox", "seven", "diamond"]))

        result = slots.play_slots(self.creator_id, self.user_id, 10, self._key())

        self.assertEqual(result.outcome, "loss")
        self.assertEqual(result.payout, 0)
        self.assertEqual(self._promo_balance(), 990)

    # ------------------------------------------------------------------
    # THE ANTI-EXPLOIT PROOF: rig a losing spin, persist it, re-arm RNG
    # to a jackpot, retry same round_id -> still the original loss.
    # ------------------------------------------------------------------
    def test_resume_after_loss_reuses_reels_never_respins(self):
        self._fund_promo(1000)
        key = self._key()

        casino_rng.set_provider(_SequenceChoiceProvider(["fox", "seven", "diamond"]))  # no match -> loss
        first = slots.play_slots(self.creator_id, self.user_id, 10, key)
        self.assertEqual(first.outcome, "loss")
        self.assertEqual(first.metadata["reels"], ["fox", "seven", "diamond"])

        # Rig the RNG so a fresh spin would now be a jackpot.
        casino_rng.set_provider(_FixedChoiceProvider("fox"))
        second = slots.play_slots(self.creator_id, self.user_id, 10, key)

        self.assertTrue(second.replayed)
        self.assertEqual(second.outcome, "loss", "must reuse the persisted loss, not re-spin into a jackpot")
        self.assertEqual(second.payout, 0)
        self.assertEqual(
            second.metadata["reels"], ["fox", "seven", "diamond"],
            "must reuse the persisted reels, not the rearmed spin",
        )
        self.assertEqual(self._promo_balance(), 990, "no phantom jackpot payout from a re-spin")

    def test_resume_between_decide_and_settle_reuses_reels(self):
        self._fund_promo(1000)
        key = self._key()

        cl.debit(
            self.creator_id, self.user_id, cr.CURRENCY_PROMO, 10, cl.PROMO_WAGER,
            round_id=key, idempotency_key=f"{key}-wager",
        )
        with cr._connect() as connection:
            cr._ensure_schema(connection)
            cr._claim_round(connection, key, self.creator_id, self.user_id, slots.GAME_ID, 10)

        forced_metadata = {"reels": ["seven", "diamond", "rocket"], "combo": "no_win", "won": False}
        outcome, payout, meta = cr._decide_outcome(
            key, lambda: ("loss", 0, forced_metadata), cr.DEFAULT_CONNECT_TIMEOUT_SECONDS,
        )
        self.assertEqual(outcome, "loss")

        casino_rng.set_provider(_FixedChoiceProvider("fox"))
        result = slots.play_slots(self.creator_id, self.user_id, 10, key)

        self.assertEqual(result.outcome, "loss")
        self.assertEqual(
            result.metadata["reels"], ["seven", "diamond", "rocket"],
            "must reuse the persisted reels, not a rearmed spin",
        )

    # ------------------------------------------------------------------
    def test_wager_and_round_idempotent_on_replay(self):
        self._fund_promo(1000)
        casino_rng.set_provider(_FixedChoiceProvider("purple"))
        key = self._key()

        results = [slots.play_slots(self.creator_id, self.user_id, 10, key) for _ in range(5)]

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
            slots.play_slots(self.creator_id, self.user_id, 10, key)

        self.assertEqual(self._promo_balance(), 5)
        with cl._connect() as connection:
            round_rows = connection.execute(
                f"SELECT COUNT(*) FROM {cr.TABLE_ROUNDS} WHERE round_id = %s", (key,),
            ).fetchone()[0]
        self.assertEqual(round_rows, 0)

    # ------------------------------------------------------------------
    def test_config_disabled_game_rejected(self):
        casino_config.set_game_config(self.creator_id, slots.GAME_ID, enabled=False)
        self._fund_promo(1000)

        with self.assertRaises(cr.GameDisabled):
            slots.play_slots(self.creator_id, self.user_id, 10, self._key())

        self.assertEqual(self._promo_balance(), 1000)

    def test_config_bet_out_of_range_rejected(self):
        casino_config.set_game_config(self.creator_id, slots.GAME_ID, min_bet=5, max_bet=50)
        self._fund_promo(1000)

        with self.assertRaises(cr.BetOutOfRange):
            slots.play_slots(self.creator_id, self.user_id, 1, self._key("too-low"))
        with self.assertRaises(cr.BetOutOfRange):
            slots.play_slots(self.creator_id, self.user_id, 500, self._key("too-high"))

        self.assertEqual(self._promo_balance(), 1000)


if __name__ == "__main__":
    unittest.main()
