"""Plinko odds, RTP and RNG -- pure, no database.

  * RTP is computed EXACTLY (fractions.Fraction, no floats) from the shipped
    multiplier table and binomial weights, and asserted <= 100%, both in
    theory and after the integer payout math real sessions use.
  * Enumerating all 256 bit patterns proves the bits -> slot mapping is
    exactly the binomial 1,8,28,56,70,56,28,8,1.
  * A statistical test runs 256,000 real draws (secrets-backed) through a
    chi-square goodness-of-fit test and a 5-sigma check on empirical RTP.

Run with:
    python -m unittest tests.test_plinko_math -v
"""

import math
import os
import sys
import unittest
from collections import Counter
from fractions import Fraction
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import casino_rng  # noqa: E402
from services import plinko  # noqa: E402

EXPECTED_WEIGHTS = (1, 8, 28, 56, 70, 56, 28, 8, 1)
EXPECTED_MULTIPLIERS = (10, 3, 1.4, 0.6, 0.4, 0.6, 1.4, 3, 10)
# Chi-square critical value, df = 8, p = 0.0001 (false-failure rate 1 in 10,000 runs).
CHI2_CRIT_DF8_P0001 = 31.828


class PlinkoTableTestCase(unittest.TestCase):
    def test_table_matches_spec(self):
        self.assertEqual(plinko.ROWS, 8)
        self.assertEqual(plinko.SLOTS, 9)
        self.assertEqual(plinko.BINOMIAL_WEIGHTS, EXPECTED_WEIGHTS)
        self.assertEqual(sum(plinko.BINOMIAL_WEIGHTS), 256)
        self.assertEqual(tuple(m / 100 for m in plinko.MULTIPLIERS_PCT), EXPECTED_MULTIPLIERS)

    def test_rtp_is_exact_and_at_most_100_percent(self):
        exact = plinko.rtp()
        # Hand computation: (2*10*1 + 2*3*8 + 2*1.4*28 + 2*0.6*56 + 0.4*70) / 256 = 241.6/256
        by_hand = (Fraction(2 * 10 * 1) + Fraction(2 * 3 * 8) + Fraction(2 * 14 * 28, 10)
                   + Fraction(2 * 6 * 56, 10) + Fraction(4 * 70, 10)) / 256
        self.assertIsInstance(exact, Fraction)
        self.assertEqual(exact, by_hand)
        self.assertEqual(exact, Fraction(151, 160))  # 94.375%
        self.assertLessEqual(exact, 1)

    def test_effective_rtp_with_integer_payouts_never_exceeds_theory(self):
        """Real payouts are floor(stake * pct / 100). Flooring can only lower
        the return, so effective RTP <= theoretical <= 100% for every stake."""
        theoretical = plinko.rtp()
        for stake in range(1, 501):
            paid = sum(w * plinko.drop_payout(stake, m)
                       for w, m in zip(plinko.BINOMIAL_WEIGHTS, plinko.MULTIPLIERS_PCT))
            effective = Fraction(paid, 256 * stake)
            self.assertLessEqual(effective, theoretical, f"stake={stake}")
            self.assertLessEqual(effective, 1)

    def test_default_session_has_no_rounding_loss(self):
        stake = plinko.DEFAULT_SESSION_COST // plinko.DEFAULT_DROPS_PER_SESSION
        self.assertEqual(plinko.DEFAULT_SESSION_COST % plinko.DEFAULT_DROPS_PER_SESSION, 0)
        paid = sum(w * plinko.drop_payout(stake, m) for w, m in zip(plinko.BINOMIAL_WEIGHTS, plinko.MULTIPLIERS_PCT))
        self.assertEqual(Fraction(paid, 256 * stake), plinko.rtp())

    def test_tables_over_100_percent_are_rejected(self):
        with self.assertRaises(ValueError):
            plinko.validate_multipliers([1000] * 9)
        with self.assertRaises(ValueError):
            plinko.validate_multipliers([1000, 300, 140, 60, 40, 60, 140, 300])  # wrong length
        with self.assertRaises(ValueError):
            plinko.validate_multipliers([-1, 300, 140, 60, 40, 60, 140, 300, 1000])
        # Exactly 100% is allowed; one point more is not.
        weights = plinko.BINOMIAL_WEIGHTS
        self.assertEqual(plinko.rtp([100] * 9, weights), 1)
        plinko.validate_multipliers([100] * 9)
        with self.assertRaises(ValueError):
            plinko.validate_multipliers([101] + [100] * 8)


class PlinkoMappingTestCase(unittest.TestCase):
    def test_all_256_paths_give_exact_binomial(self):
        counts = Counter(plinko.slot_for_bits(b) for b in range(256))
        self.assertEqual(tuple(counts[k] for k in range(9)), EXPECTED_WEIGHTS)

    def test_path_and_slot_agree(self):
        for b in range(256):
            path = plinko.path_for_bits(b)
            self.assertEqual(len(path), 8)
            self.assertEqual(sum(path), plinko.slot_for_bits(b))

    def test_draw_uses_secrets(self):
        with mock.patch("services.casino_rng.secrets.randbits", return_value=0b10110000) as randbits:
            bits, slot = plinko.draw()
        randbits.assert_called_once_with(8)
        self.assertEqual((bits, slot), (0b10110000, 3))
        self.assertIsInstance(casino_rng.get_provider(), casino_rng.SecureRandomProvider)

    def test_default_bits_for_custom_providers(self):
        class AlwaysOne(casino_rng.RNGProvider):
            def roll(self, minimum, maximum):
                return maximum

            def choice(self, seq):
                return seq[-1]

        self.assertEqual(AlwaysOne().bits(8), 255)


class PlinkoStatisticalTestCase(unittest.TestCase):
    """256,000 real draws through the production RNG path."""

    N = 256_000

    @classmethod
    def setUpClass(cls):
        cls.counts = Counter(plinko.draw()[1] for _ in range(cls.N))

    def test_slot_distribution_matches_binomial(self):
        chi2 = 0.0
        for k, weight in enumerate(EXPECTED_WEIGHTS):
            expected = self.N * weight / 256
            chi2 += (self.counts[k] - expected) ** 2 / expected
        self.assertLess(chi2, CHI2_CRIT_DF8_P0001, f"chi2={chi2:.2f}, counts={dict(self.counts)}")

    def test_empirical_rtp_within_5_sigma_of_exact(self):
        p = [w / 256 for w in EXPECTED_WEIGHTS]
        m = [pct / 100 for pct in plinko.MULTIPLIERS_PCT]
        mean = sum(pi * mi for pi, mi in zip(p, m))
        var = sum(pi * mi * mi for pi, mi in zip(p, m)) - mean ** 2
        sigma = math.sqrt(var / self.N)
        empirical = sum(self.counts[k] * m[k] for k in range(9)) / self.N

        self.assertAlmostEqual(mean, float(plinko.rtp()), places=12)
        self.assertLess(abs(empirical - mean), 5 * sigma, f"empirical={empirical:.5f} exact={mean:.5f} sigma={sigma:.5f}")
        self.assertLess(empirical, 1.0)

    def test_every_slot_occurs(self):
        self.assertEqual(set(self.counts), set(range(9)))


class PlinkoRulesAndPaidStubTestCase(unittest.TestCase):
    def _config(self):
        return plinko.PlinkoConfig(
            creator_handle="x", enabled=True, kill_switch=False, session_cost=100, drops_per_session=10,
            cooldown_seconds=0, max_session_payout=500, session_ttl_seconds=600,
        )

    def test_rules_text_posts_full_odds_table_within_chat_limit(self):
        text = plinko.rules_text(self._config())
        self.assertLessEqual(len(text), 480)
        self.assertIn("10x | 3x | 1.4x | 0.6x | 0.4x | 0.6x | 1.4x | 3x | 10x", text)
        self.assertIn("1 | 8 | 28 | 56 | 70 | 56 | 28 | 8 | 1", text)
        self.assertIn("94.38%", text)
        self.assertIn("Free-play", text)

    def test_paid_entry_flag_defaults_off(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(plinko.PAID_ENTRY_FLAG, None)
            self.assertFalse(plinko.paid_entry_enabled())

    def test_paid_entry_stub_does_nothing_even_when_flag_is_on(self):
        for value in ("false", "true"):
            with mock.patch.dict(os.environ, {plinko.PAID_ENTRY_FLAG: value}):
                with self.assertRaises(plinko.PaidEntryUnavailable):
                    plinko.start_paid_session("creator", user_key="v", amount=100)


if __name__ == "__main__":
    unittest.main()
