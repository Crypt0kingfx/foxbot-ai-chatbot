"""Tests for games/blackjack.py (Casino Phase 8): interactive blackjack
on top of casino_rounds.py's TABLE/schema/connection primitives, but NOT
play_round() -- see the module docstring in games/blackjack.py for why.

Two groups, same split as test_crash.py:
  - BlackjackMathTestCase: NO DATABASE_URL required. Pure functions --
    hand_value/is_bust/is_natural_blackjack, deck construction, card
    formatting, staleness math.
  - BlackjackRoundsTestCase: real Postgres via DATABASE_URL. Deck order
    is controlled by mocking _build_shuffled_deck's return value directly
    -- Fisher-Yates output isn't hand-predictable from a fixed RNG value,
    and this needs no test-only seam in production code, consistent with
    how the rest of this test suite engineers specific DB states directly
    (cr._claim_round, cr._decide_outcome) rather than adding hooks.

Run with:
    python -m unittest tests.test_blackjack -v
"""

import os
import sys
import time
import unittest
import uuid
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import games.blackjack as blackjack  # noqa: E402
import services.casino_config as casino_config  # noqa: E402
import services.casino_ledger as cl  # noqa: E402
import services.casino_rounds as cr  # noqa: E402


DATABASE_CONFIGURED = bool(os.getenv("DATABASE_URL"))
SKIP_REASON = (
    "DATABASE_URL not set -- these tests need a real Postgres database "
    "(a throwaway/dev one, not production) to prove the row lock, "
    "deck-integrity resume, per-hit idempotency, and lazy-timeout "
    "behavior honestly."
)


def _deck_with_prefix(*cards):
    """A full, valid 52-card deck whose first N cards are exactly `cards`
    (in order) -- lets tests engineer any hand deterministically without
    needing to hand-predict Fisher-Yates output from a fixed RNG value."""
    full = [rank + suit for suit in blackjack.SUITS for rank in blackjack.RANKS]
    rest = [c for c in full if c not in cards]
    return list(cards) + rest


class BlackjackMathTestCase(unittest.TestCase):
    """No DATABASE_URL required -- pure math/formatting proofs."""

    def test_deck_has_52_unique_cards(self):
        deck = blackjack._build_shuffled_deck()
        self.assertEqual(len(deck), 52)
        self.assertEqual(len(set(deck)), 52)

    def test_deck_shuffle_uses_secure_rng(self):
        import secrets
        with mock.patch("secrets.randbelow", wraps=secrets.randbelow) as spy:
            blackjack._build_shuffled_deck()
        self.assertTrue(spy.called, "the shuffle must go through secrets (via casino_rng.roll)")

    def test_hand_value_simple(self):
        self.assertEqual(blackjack.hand_value(["TS", "7H"]), 17)
        self.assertEqual(blackjack.hand_value(["2S", "3H", "4D"]), 9)

    def test_hand_value_single_ace_soft(self):
        self.assertEqual(blackjack.hand_value(["AS", "6H"]), 17)  # soft 17
        self.assertEqual(blackjack.hand_value(["AS", "KH"]), 21)  # natural

    def test_hand_value_ace_reduces_when_busting(self):
        self.assertEqual(blackjack.hand_value(["AS", "6H", "9D"]), 16)  # 11+6+9=26 -> ace as 1

    def test_hand_value_multiple_aces(self):
        self.assertEqual(blackjack.hand_value(["AS", "AH"]), 12)  # one as 11, one as 1
        self.assertEqual(blackjack.hand_value(["AS", "AH", "9D"]), 21)
        self.assertEqual(blackjack.hand_value(["AS", "AH", "AD", "KH"]), 13)  # all forced to 1

    def test_is_bust(self):
        self.assertFalse(blackjack.is_bust(["TS", "9H"]))
        self.assertTrue(blackjack.is_bust(["TS", "9H", "5D"]))
        self.assertFalse(blackjack.is_bust(["AS", "6H", "9D"]), "soft-reduced to 16, not a bust")

    def test_is_natural_blackjack(self):
        self.assertTrue(blackjack.is_natural_blackjack(["AS", "KH"]))
        self.assertFalse(blackjack.is_natural_blackjack(["AS", "9H"]))
        self.assertFalse(blackjack.is_natural_blackjack(["AS", "9H", "1D"]), "3 cards, even if it summed to 21")

    def test_format_card(self):
        self.assertEqual(blackjack.format_card("TS"), "10♠")
        self.assertEqual(blackjack.format_card("AH"), "A♥")
        self.assertEqual(blackjack.format_card("2C"), "2♣")

    def test_format_hand(self):
        self.assertEqual(blackjack.format_hand(["AS", "TH"]), "A♠ 10♥")

    def test_blackjack_payout_floors_on_odd_bets(self):
        # bet + (bet*3)//2 -- the exact formula deal() uses for naturals.
        self.assertEqual(11 + (11 * 3) // 2, 27)  # true 3:2 would be 27.5 -> floors to 27
        self.assertEqual(10 + (10 * 3) // 2, 25)  # even bet, exact

    def test_is_stale(self):
        fresh = {"deal_timestamp": time.time()}
        stale = {"deal_timestamp": time.time() - blackjack.TIMEOUT_SECONDS - 1}
        no_timestamp = {}
        self.assertFalse(blackjack._is_stale(fresh))
        self.assertTrue(blackjack._is_stale(stale))
        self.assertFalse(blackjack._is_stale(no_timestamp))

    def test_no_client_outcome_param_on_public_functions(self):
        import inspect
        forbidden = {"outcome", "result", "card", "deck", "won", "win"}
        for fn in (blackjack.deal, blackjack.hit, blackjack.stand):
            sig = inspect.signature(fn)
            self.assertTrue(
                forbidden.isdisjoint(sig.parameters.keys()),
                f"{fn.__name__} must not accept a client-supplied outcome, got {list(sig.parameters)}",
            )


@unittest.skipUnless(DATABASE_CONFIGURED, SKIP_REASON)
class BlackjackRoundsTestCase(unittest.TestCase):
    def setUp(self):
        self.creator_id = f"test-bj-{uuid.uuid4().hex[:12]}"
        self.user_id = "testviewer"
        self._deck_patch = None

    def tearDown(self):
        if self._deck_patch is not None:
            self._deck_patch.stop()

        with cl._connect() as connection:
            cl._ensure_schema(connection)
            cr._ensure_schema(connection)
            casino_config._ensure_schema(connection)
            blackjack._ensure_schema(connection)
            connection.execute(f"DELETE FROM {cl.TABLE_LEDGER} WHERE creator_id = %s", (self.creator_id,))
            connection.execute(f"DELETE FROM {cl.TABLE_BALANCES} WHERE creator_id = %s", (self.creator_id,))
            connection.execute(f"DELETE FROM {cr.TABLE_ROUNDS} WHERE creator_id = %s", (self.creator_id,))
            connection.execute(f"DELETE FROM {casino_config.TABLE_GAME_CONFIG} WHERE creator_id = %s", (self.creator_id,))
            connection.execute(f"DELETE FROM {casino_config.TABLE_CONFIG} WHERE creator_id = %s", (self.creator_id,))
            connection.execute(
                f"DELETE FROM {blackjack.TABLE_ACTIVE_HANDS} WHERE creator_id = %s", (self.creator_id,),
            )

    # ------------------------------------------------------------------
    def _fund_promo(self, amount):
        cl.credit(
            self.creator_id, self.user_id, cr.CURRENCY_PROMO, amount, cl.PROMO_CONVERT_IN,
            idempotency_key=f"{self.creator_id}-fund-{uuid.uuid4().hex[:8]}",
        )

    def _promo_balance(self):
        return cl.get_balance(self.creator_id, self.user_id, cr.CURRENCY_PROMO)

    def _key(self, suffix="hand-1"):
        return f"{self.creator_id}-{suffix}"

    def _with_deck(self, *prefix_cards):
        """Forces the NEXT deal() call to use a deck starting with the
        given cards -- see _deck_with_prefix. Stopped in tearDown."""
        deck = _deck_with_prefix(*prefix_cards)
        self._deck_patch = mock.patch.object(blackjack, "_build_shuffled_deck", return_value=deck)
        self._deck_patch.start()
        return deck

    def _set_stale(self, round_id):
        """Directly backdates a round's deal_timestamp past TIMEOUT_SECONDS
        -- engineers staleness without needing to wait for real time to
        pass, same convention as the rest of this suite's direct-state
        engineering."""
        from psycopg.types.json import Jsonb
        with cr._connect() as connection:
            row = connection.execute(
                f"SELECT metadata FROM {cr.TABLE_ROUNDS} WHERE round_id = %s FOR UPDATE", (round_id,),
            ).fetchone()
            metadata = row[0]
            metadata["deal_timestamp"] = time.time() - blackjack.TIMEOUT_SECONDS - 1
            connection.execute(
                f"UPDATE {cr.TABLE_ROUNDS} SET metadata = %s WHERE round_id = %s",
                (Jsonb(metadata), round_id),
            )

    # ------------------------------------------------------------------
    # Full hand: deal -> hit -> stand -> dealer plays -> settle.
    # ------------------------------------------------------------------
    def test_full_hand_deal_hit_stand_win(self):
        self._fund_promo(1000)
        # player: 7S+5D=12 (not natural) -> hit 6C -> 18.
        # dealer: 2H+9H=11 -> hits (< S17) -> 2C(13) -> 4C(17) -> stops, dealer=17.
        self._with_deck("7S", "2H", "5D", "9H", "6C", "2C", "4C")
        key = self._key()

        dealt = blackjack.deal(self.creator_id, self.user_id, 10, key)
        self.assertEqual(dealt.state, cr.STATE_FUNDED)
        self.assertEqual(dealt.metadata["player_cards"], ["7S", "5D"])
        self.assertEqual(dealt.metadata["dealer_cards"], ["2H", "9H"])
        self.assertEqual(self._promo_balance(), 1000 - 10)

        hit1 = blackjack.hit(self.creator_id, self.user_id, key, "action-1")
        self.assertEqual(hit1.state, cr.STATE_FUNDED)
        self.assertEqual(hit1.metadata["player_cards"], ["7S", "5D", "6C"])
        self.assertFalse(blackjack.is_bust(hit1.metadata["player_cards"]))

        stood = blackjack.stand(self.creator_id, self.user_id, key)
        self.assertEqual(stood.state, cr.STATE_SETTLED)
        self.assertEqual(stood.metadata["dealer_cards"], ["2H", "9H", "2C", "4C"])
        self.assertEqual(blackjack.hand_value(stood.metadata["dealer_cards"]), 17)
        self.assertEqual(stood.outcome, "win")
        self.assertEqual(stood.payout, 20)  # 1:1 -> bet*2
        self.assertEqual(self._promo_balance(), 1000 - 10 + 20)

    def test_full_hand_bust(self):
        self._fund_promo(1000)
        # player: TS+9D=19 -> hit 5C -> 24, bust.
        self._with_deck("TS", "2H", "9D", "3H", "5C")
        key = self._key()

        blackjack.deal(self.creator_id, self.user_id, 10, key)
        result = blackjack.hit(self.creator_id, self.user_id, key, "action-1")

        self.assertEqual(result.state, cr.STATE_SETTLED)
        self.assertEqual(result.outcome, "loss")
        self.assertEqual(result.payout, 0)
        self.assertEqual(result.metadata["settled_reason"], "bust")
        self.assertEqual(self._promo_balance(), 1000 - 10)

        # Active hand slot must be freed on bust.
        self.assertIsNone(blackjack.get_active_round_id(self.creator_id, self.user_id))

    def test_push_no_naturals(self):
        self._fund_promo(1000)
        # player: 8S+9H=17. dealer: 7H+TD=17 (S17 -- stands immediately, no draw).
        self._with_deck("8S", "7H", "9H", "TD")
        key = self._key()

        blackjack.deal(self.creator_id, self.user_id, 10, key)
        result = blackjack.stand(self.creator_id, self.user_id, key)

        self.assertEqual(result.outcome, "push")
        self.assertEqual(result.payout, 10)  # stake returned
        self.assertEqual(result.metadata["dealer_cards"], ["7H", "TD"], "S17 must not draw a 3rd card at 17")
        self.assertEqual(self._promo_balance(), 1000)  # unchanged: -10 wager +10 push

    # ------------------------------------------------------------------
    # Blackjack 3:2 payout, floored on odd bets.
    # ------------------------------------------------------------------
    def test_natural_blackjack_player_wins_3_to_2(self):
        self._fund_promo(1000)
        # player: AS+KH = natural 21. dealer: 2S+3S = 5, not natural.
        self._with_deck("AS", "2S", "KH", "3S")
        key = self._key()

        result = blackjack.deal(self.creator_id, self.user_id, 10, key)

        self.assertEqual(result.state, cr.STATE_SETTLED)
        self.assertEqual(result.outcome, "blackjack")
        self.assertEqual(result.payout, 25)  # 10 + (10*3)//2 = 25
        self.assertEqual(result.metadata["settled_reason"], "natural")
        self.assertEqual(self._promo_balance(), 1000 - 10 + 25)
        self.assertIsNone(blackjack.get_active_round_id(self.creator_id, self.user_id))

    def test_natural_blackjack_odd_bet_floors(self):
        self._fund_promo(1000)
        self._with_deck("AS", "2S", "KH", "3S")
        key = self._key()

        result = blackjack.deal(self.creator_id, self.user_id, 11, key)

        self.assertEqual(result.payout, 11 + (11 * 3) // 2)  # 27, not 27.5
        self.assertEqual(result.payout, 27)

    def test_dealer_natural_blackjack_player_loses_immediately(self):
        self._fund_promo(1000)
        # player: 9S+8H=17 (not natural). dealer: AS+KH=natural 21.
        self._with_deck("9S", "AS", "8H", "KH")
        key = self._key()

        result = blackjack.deal(self.creator_id, self.user_id, 10, key)

        self.assertEqual(result.state, cr.STATE_SETTLED)
        self.assertEqual(result.outcome, "loss")
        self.assertEqual(result.payout, 0)
        self.assertEqual(self._promo_balance(), 1000 - 10)

    def test_both_natural_blackjack_push(self):
        self._fund_promo(1000)
        self._with_deck("AS", "AH", "KS", "KH")
        key = self._key()

        result = blackjack.deal(self.creator_id, self.user_id, 10, key)

        self.assertEqual(result.outcome, "push")
        self.assertEqual(result.payout, 10)
        self.assertEqual(self._promo_balance(), 1000)

    # ------------------------------------------------------------------
    # Dealer S17: stands on soft 17, doesn't hit.
    # ------------------------------------------------------------------
    def test_dealer_stands_on_soft_17(self):
        self._fund_promo(1000)
        # player: 9S+8H=17 (stands immediately). dealer: AS+6H=soft 17.
        self._with_deck("9S", "AS", "8H", "6H")
        key = self._key()

        blackjack.deal(self.creator_id, self.user_id, 10, key)
        result = blackjack.stand(self.creator_id, self.user_id, key)

        self.assertEqual(len(result.metadata["dealer_cards"]), 2, "S17 must not draw on soft 17")
        self.assertEqual(blackjack.hand_value(result.metadata["dealer_cards"]), 17)
        self.assertEqual(result.outcome, "push")  # 17 vs 17

    # ------------------------------------------------------------------
    # THE DECK-INTEGRITY ANTI-EXPLOIT PROOF: resume mid-hand shows the
    # SAME cards, the next card drawn is the one already committed at the
    # current cursor position, never a fresh draw.
    # ------------------------------------------------------------------
    def test_resume_mid_hand_next_card_is_the_committed_one_not_a_fresh_draw(self):
        self._fund_promo(1000)
        deck = self._with_deck("7S", "2H", "5D", "9H", "6C", "KC")
        key = self._key()

        blackjack.deal(self.creator_id, self.user_id, 10, key)

        # Simulate a "resume" -- read the round back from a fresh
        # connection, as a genuinely separate process would after a
        # crash, and confirm the persisted deck/cursor/cards are exactly
        # what was committed at deal time.
        with cr._connect() as connection:
            row = connection.execute(
                f"SELECT metadata FROM {cr.TABLE_ROUNDS} WHERE round_id = %s", (key,),
            ).fetchone()
        resumed_metadata = row[0]
        self.assertEqual(resumed_metadata["deck"], deck)
        self.assertEqual(resumed_metadata["deck_cursor"], 4)
        self.assertEqual(resumed_metadata["player_cards"], ["7S", "5D"])
        self.assertEqual(resumed_metadata["dealer_cards"], ["2H", "9H"])

        # A genuinely new hit (new action_key) after "resuming" must draw
        # deck[4] ("6C") -- the pre-committed next card -- not a fresh
        # roll from a reshuffled/re-rolled source.
        result = blackjack.hit(self.creator_id, self.user_id, key, "action-after-resume")
        self.assertEqual(result.metadata["player_cards"][-1], "6C")
        self.assertEqual(result.metadata["deck_cursor"], 5)
        self.assertEqual(result.metadata["deck"], deck, "the committed deck itself must never change")

    # ------------------------------------------------------------------
    # THE PER-HIT IDEMPOTENCY PROOF: a retried !hit (same dedupe_key) must
    # return the SAME card, never draw a second one.
    # ------------------------------------------------------------------
    def test_hit_idempotent_same_action_key_no_redraw(self):
        self._fund_promo(1000)
        self._with_deck("7S", "2H", "5D", "9H", "6C", "KC")
        key = self._key()
        blackjack.deal(self.creator_id, self.user_id, 10, key)

        first = blackjack.hit(self.creator_id, self.user_id, key, "same-action-key")
        self.assertFalse(first.replayed)
        self.assertEqual(first.metadata["player_cards"], ["7S", "5D", "6C"])
        self.assertEqual(first.metadata["deck_cursor"], 5)

        second = blackjack.hit(self.creator_id, self.user_id, key, "same-action-key")
        self.assertTrue(second.replayed)
        self.assertEqual(
            second.metadata["player_cards"], ["7S", "5D", "6C"],
            "retried hit must not draw a second card",
        )
        self.assertEqual(second.metadata["deck_cursor"], 5, "cursor must not advance on a replayed hit")

    # ------------------------------------------------------------------
    # LAZY TIMEOUT: no background job -- staleness is checked (and
    # resolved) only when the hand is next touched.
    # ------------------------------------------------------------------
    def test_stale_hand_auto_stands_on_next_hit(self):
        self._fund_promo(1000)
        # player: 9S+8H=17. dealer: 2H+9H=11 -- would need to hit to reach
        # S17 if actually played; the auto-stand path exercises exactly
        # that dealer-plays-out logic.
        self._with_deck("9S", "2H", "8H", "9H", "5C", "TC")
        key = self._key()
        blackjack.deal(self.creator_id, self.user_id, 10, key)
        self._set_stale(key)

        result = blackjack.hit(self.creator_id, self.user_id, key, "too-late")

        self.assertEqual(result.state, cr.STATE_SETTLED)
        self.assertEqual(result.metadata["settled_reason"], "timeout")
        self.assertEqual(
            result.metadata["player_cards"], ["9S", "8H"],
            "a stale !hit must NOT draw a card -- it auto-stands instead",
        )
        self.assertIsNone(blackjack.get_active_round_id(self.creator_id, self.user_id))

    def test_stale_hand_auto_settles_when_new_deal_attempted(self):
        self._fund_promo(1000)
        self._with_deck("9S", "2H", "8H", "9H", "5C", "TC")
        old_key = self._key("old-hand")
        blackjack.deal(self.creator_id, self.user_id, 10, old_key)
        self._set_stale(old_key)

        # A fresh deck for the NEW hand.
        self._deck_patch.stop()
        self._with_deck("AS", "AH", "KS", "KH")  # both natural -> instantly settles too, keeps it simple
        new_key = self._key("new-hand")

        new_result = blackjack.deal(self.creator_id, self.user_id, 15, new_key)

        self.assertEqual(new_result.state, cr.STATE_SETTLED)  # natural push, but the point is it wasn't rejected
        self.assertFalse(new_result.replayed)

        with cr._connect() as connection:
            old_state = connection.execute(
                f"SELECT state, metadata FROM {cr.TABLE_ROUNDS} WHERE round_id = %s", (old_key,),
            ).fetchone()
        self.assertEqual(old_state[0], cr.STATE_SETTLED)
        self.assertEqual(old_state[1]["settled_reason"], "timeout")

    def test_explicit_stand_does_not_need_staleness_to_behave_the_same(self):
        """stand() doesn't special-case elapsed time -- an explicit !stand
        sent after the timeout window plays out identically to one sent
        immediately. Confirms stand() needs no timeout branch at all."""
        self._fund_promo(1000)
        self._with_deck("9S", "AS", "8H", "6H")  # dealer soft 17, S17 stands
        key = self._key()
        blackjack.deal(self.creator_id, self.user_id, 10, key)
        self._set_stale(key)

        result = blackjack.stand(self.creator_id, self.user_id, key)

        self.assertEqual(result.metadata["settled_reason"], "stand", "explicit stand, not auto-timeout")
        self.assertEqual(len(result.metadata["dealer_cards"]), 2)

    # ------------------------------------------------------------------
    # One active hand per user.
    # ------------------------------------------------------------------
    def test_second_deal_rejected_while_hand_open(self):
        self._fund_promo(1000)
        self._with_deck("7S", "2H", "5D", "9H")
        key1 = self._key("first")
        blackjack.deal(self.creator_id, self.user_id, 10, key1)

        key2 = self._key("second")
        with self.assertRaises(blackjack.HandInProgress):
            blackjack.deal(self.creator_id, self.user_id, 10, key2)

        self.assertEqual(self._promo_balance(), 1000 - 10, "rejected 2nd deal must not debit")
        with cl._connect() as connection:
            round_rows = connection.execute(
                f"SELECT COUNT(*) FROM {cr.TABLE_ROUNDS} WHERE round_id = %s", (key2,),
            ).fetchone()[0]
        self.assertEqual(round_rows, 0, "rejected 2nd deal must not create a round")

    def test_hit_with_no_active_hand_rejected(self):
        with self.assertRaises(blackjack.NoActiveHand):
            blackjack.hit(self.creator_id, self.user_id, "no-such-round", "action-1")

    def test_stand_with_no_active_hand_rejected(self):
        with self.assertRaises(blackjack.NoActiveHand):
            blackjack.stand(self.creator_id, self.user_id, "no-such-round")

    def test_get_active_round_id_none_when_no_hand(self):
        self.assertIsNone(blackjack.get_active_round_id(self.creator_id, self.user_id))

    # ------------------------------------------------------------------
    # Idempotency on deal itself.
    # ------------------------------------------------------------------
    def test_deal_idempotent_on_replay(self):
        self._fund_promo(1000)
        self._with_deck("7S", "2H", "5D", "9H")
        key = self._key()

        results = [blackjack.deal(self.creator_id, self.user_id, 10, key) for _ in range(3)]

        self.assertFalse(results[0].replayed)
        for later in results[1:]:
            self.assertTrue(later.replayed)
            self.assertEqual(later.metadata["player_cards"], results[0].metadata["player_cards"])
        self.assertEqual(self._promo_balance(), 1000 - 10, "must not re-debit on replay")

    # ------------------------------------------------------------------
    # Insufficient funds must not leave a phantom open hand.
    # ------------------------------------------------------------------
    def test_insufficient_funds_releases_active_hand_slot(self):
        self._fund_promo(5)
        key = self._key()

        with self.assertRaises(cl.InsufficientFunds):
            blackjack.deal(self.creator_id, self.user_id, 10, key)

        self.assertEqual(self._promo_balance(), 5)
        self.assertIsNone(
            blackjack.get_active_round_id(self.creator_id, self.user_id),
            "a failed deal must not leave a phantom open hand blocking future deals",
        )
        # And a real deal must now succeed cleanly.
        self._fund_promo(100)
        self._with_deck("7S", "2H", "5D", "9H")
        result = blackjack.deal(self.creator_id, self.user_id, 10, self._key("retry"))
        self.assertEqual(result.state, cr.STATE_FUNDED)

    # ------------------------------------------------------------------
    def test_invalid_bet_rejected(self):
        for bad_bet in (0, -5, 1.5):
            with self.assertRaises(ValueError):
                blackjack.deal(self.creator_id, self.user_id, bad_bet, self._key(f"bad-{bad_bet}"))

    def test_config_disabled_game_rejected(self):
        casino_config.set_game_config(self.creator_id, blackjack.GAME_ID, enabled=False)
        self._fund_promo(1000)

        with self.assertRaises(cr.GameDisabled):
            blackjack.deal(self.creator_id, self.user_id, 10, self._key())

        self.assertEqual(self._promo_balance(), 1000)
        self.assertIsNone(blackjack.get_active_round_id(self.creator_id, self.user_id))

    def test_config_bet_out_of_range_rejected(self):
        casino_config.set_game_config(self.creator_id, blackjack.GAME_ID, min_bet=5, max_bet=50)
        self._fund_promo(1000)

        with self.assertRaises(cr.BetOutOfRange):
            blackjack.deal(self.creator_id, self.user_id, 1, self._key("too-low"))
        with self.assertRaises(cr.BetOutOfRange):
            blackjack.deal(self.creator_id, self.user_id, 500, self._key("too-high"))

        self.assertEqual(self._promo_balance(), 1000)


if __name__ == "__main__":
    unittest.main()
