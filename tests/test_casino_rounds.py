"""Round-model correctness tests for services/casino_rounds.py and
games/coinflip.py (Casino Phase 4): the wager -> RNG -> settle lifecycle
on top of the already-proven casino_ledger (Phase 2) and casino_config
(Phase 3/4).

Same convention as tests/test_casino_ledger.py and
tests/test_promo_provider.py: real Postgres via DATABASE_URL, skipped
(not faked) without one -- the anti-exploit proof (resume never re-rolls)
and the concurrent-double-roll proof (FOR UPDATE serializes) both depend
on real row-lock behavior that cannot be mocked honestly. Run with:
    python -m unittest tests.test_casino_rounds -v

Point DATABASE_URL at a throwaway/dev database, not production. Every
test uses a fresh, randomly-suffixed creator_id and cleans its own rows
up in tearDown.
"""

import inspect
import os
import secrets
import sys
import threading
import time
import unittest
import uuid
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import games.coinflip as coinflip  # noqa: E402
import services.casino_config as casino_config  # noqa: E402
import services.casino_ledger as cl  # noqa: E402
import services.casino_rng as casino_rng  # noqa: E402
import services.casino_rounds as cr  # noqa: E402


DATABASE_CONFIGURED = bool(os.getenv("DATABASE_URL"))
SKIP_REASON = (
    "DATABASE_URL not set -- these tests need a real Postgres database "
    "(a throwaway/dev one, not production) to prove the row lock, "
    "crash-resume, and anti-exploit behavior honestly. They are skipped, "
    "not faked, without one."
)


class _FixedChoiceProvider(casino_rng.RNGProvider):
    """Deterministic stand-in for SecureRandomProvider -- always returns
    `value` from choice(), never touches `secrets`. Used to force a
    specific coinflip outcome so tests don't depend on real randomness."""

    def __init__(self, value):
        self.value = value

    def roll(self, minimum, maximum):
        return minimum

    def choice(self, seq):
        return self.value


@unittest.skipUnless(DATABASE_CONFIGURED, SKIP_REASON)
class CasinoRoundsTestCase(unittest.TestCase):
    def setUp(self):
        self.creator_id = f"test-rounds-{uuid.uuid4().hex[:12]}"
        self.other_creator_id = f"test-rounds-other-{uuid.uuid4().hex[:12]}"
        self.user_id = "testviewer"
        self._original_rng_provider = casino_rng.get_provider()

    def tearDown(self):
        casino_rng.set_provider(self._original_rng_provider)
        cr._TEST_HOOK_AFTER_ROW_LOCK = None

        with cl._connect() as connection:
            cl._ensure_schema(connection)
            cr._ensure_schema(connection)
            casino_config._ensure_schema(connection)
            for cid in (self.creator_id, self.other_creator_id):
                connection.execute(f"DELETE FROM {cl.TABLE_LEDGER} WHERE creator_id = %s", (cid,))
                connection.execute(f"DELETE FROM {cl.TABLE_BALANCES} WHERE creator_id = %s", (cid,))
                connection.execute(f"DELETE FROM {cr.TABLE_ROUNDS} WHERE creator_id = %s", (cid,))
                connection.execute(f"DELETE FROM {casino_config.TABLE_GAME_CONFIG} WHERE creator_id = %s", (cid,))
                connection.execute(f"DELETE FROM {casino_config.TABLE_CONFIG} WHERE creator_id = %s", (cid,))

    # ------------------------------------------------------------------
    def _fund_promo(self, amount, creator_id=None):
        cl.credit(
            creator_id or self.creator_id, self.user_id, cr.CURRENCY_PROMO, amount, cl.PROMO_CONVERT_IN,
            idempotency_key=f"{creator_id or self.creator_id}-fund-{uuid.uuid4().hex[:8]}",
        )

    def _promo_balance(self, creator_id=None):
        return cl.get_balance(creator_id or self.creator_id, self.user_id, cr.CURRENCY_PROMO)

    def _key(self, suffix="round-1"):
        return f"{self.creator_id}-{suffix}"

    # ------------------------------------------------------------------
    def test_happy_path_win(self):
        self._fund_promo(1000)
        casino_rng.set_provider(_FixedChoiceProvider("heads"))

        result = coinflip.play_coinflip(self.creator_id, self.user_id, "heads", 10, self._key())

        self.assertEqual(result.state, cr.STATE_SETTLED)
        self.assertEqual(result.outcome, "win")
        self.assertEqual(result.payout, 20)
        self.assertFalse(result.replayed)
        self.assertEqual(self._promo_balance(), 1000 - 10 + 20)

        with cl._connect() as connection:
            wager_rows = connection.execute(
                f"SELECT COUNT(*) FROM {cl.TABLE_LEDGER} WHERE round_id = %s AND type = %s",
                (self._key(), cl.PROMO_WAGER),
            ).fetchone()[0]
            payout_rows = connection.execute(
                f"SELECT COUNT(*) FROM {cl.TABLE_LEDGER} WHERE round_id = %s AND type = %s",
                (self._key(), cl.PROMO_PAYOUT),
            ).fetchone()[0]
        self.assertEqual(wager_rows, 1)
        self.assertEqual(payout_rows, 1)

    def test_happy_path_loss(self):
        self._fund_promo(1000)
        casino_rng.set_provider(_FixedChoiceProvider("tails"))

        result = coinflip.play_coinflip(self.creator_id, self.user_id, "heads", 10, self._key())

        self.assertEqual(result.state, cr.STATE_SETTLED)
        self.assertEqual(result.outcome, "loss")
        self.assertEqual(result.payout, 0)
        self.assertEqual(self._promo_balance(), 1000 - 10)

        with cl._connect() as connection:
            payout_rows = connection.execute(
                f"SELECT COUNT(*) FROM {cl.TABLE_LEDGER} WHERE round_id = %s AND type = %s",
                (self._key(), cl.PROMO_PAYOUT),
            ).fetchone()[0]
        self.assertEqual(payout_rows, 0, "a loss must never produce a payout ledger row")

    # ------------------------------------------------------------------
    def test_wager_and_round_idempotent_on_replay(self):
        self._fund_promo(1000)
        casino_rng.set_provider(_FixedChoiceProvider("heads"))
        key = self._key()

        results = [
            coinflip.play_coinflip(self.creator_id, self.user_id, "heads", 10, key)
            for _ in range(5)
        ]

        self.assertFalse(results[0].replayed)
        for later in results[1:]:
            self.assertTrue(later.replayed)
            self.assertEqual(later.outcome, results[0].outcome)
            self.assertEqual(later.payout, results[0].payout)

        self.assertEqual(self._promo_balance(), 1000 - 10 + 20)

        with cl._connect() as connection:
            wager_rows = connection.execute(
                f"SELECT COUNT(*) FROM {cl.TABLE_LEDGER} WHERE round_id = %s AND type = %s",
                (key, cl.PROMO_WAGER),
            ).fetchone()[0]
            round_rows = connection.execute(
                f"SELECT COUNT(*) FROM {cr.TABLE_ROUNDS} WHERE round_id = %s", (key,),
            ).fetchone()[0]
        self.assertEqual(wager_rows, 1)
        self.assertEqual(round_rows, 1)

    # ------------------------------------------------------------------
    # THE ANTI-EXPLOIT PROOF: a player who saw a loss and retries must get
    # the SAME loss, not a fresh roll -- proven by rigging the RNG to
    # guarantee a WIN on the retry and confirming it still returns a LOSS.
    # ------------------------------------------------------------------
    def test_resume_after_loss_reuses_outcome_never_rerolls(self):
        self._fund_promo(1000)
        key = self._key()

        casino_rng.set_provider(_FixedChoiceProvider("tails"))  # pick=heads -> loss
        first = coinflip.play_coinflip(self.creator_id, self.user_id, "heads", 10, key)
        self.assertEqual(first.outcome, "loss")
        self.assertEqual(first.payout, 0)

        # Rig the RNG so that IF it rolled again, it would now win.
        casino_rng.set_provider(_FixedChoiceProvider("heads"))
        second = coinflip.play_coinflip(self.creator_id, self.user_id, "heads", 10, key)

        self.assertTrue(second.replayed)
        self.assertEqual(second.outcome, "loss", "must reuse the persisted loss, not re-roll into a win")
        self.assertEqual(second.payout, 0)
        self.assertEqual(self._promo_balance(), 1000 - 10, "no phantom payout from a re-roll")

    def test_resume_between_decide_and_settle_reuses_outcome(self):
        """Same proof, but engineered at the exact 'funded, outcome decided,
        not yet settled' crash point rather than after full settlement."""
        self._fund_promo(1000)
        key = self._key()

        cl.debit(
            self.creator_id, self.user_id, cr.CURRENCY_PROMO, 10, cl.PROMO_WAGER,
            round_id=key, idempotency_key=f"{key}-wager",
        )
        with cr._connect() as connection:
            cr._ensure_schema(connection)
            cr._claim_round(connection, key, self.creator_id, self.user_id, coinflip.GAME_ID, 10)

        # Decide once, forcing a loss -- simulates the crash landing right
        # after the outcome is persisted but before settlement completes.
        outcome, payout, meta = cr._decide_outcome(key, lambda: ("loss", 0, {"pick": "heads", "roll": "tails"}), cr.DEFAULT_CONNECT_TIMEOUT_SECONDS)
        self.assertEqual(outcome, "loss")

        with cl._connect() as connection:
            state = connection.execute(
                f"SELECT state FROM {cr.TABLE_ROUNDS} WHERE round_id = %s", (key,),
            ).fetchone()[0]
        self.assertEqual(state, cr.STATE_FUNDED, "not yet settled -- this is the engineered crash point")

        # Resolve() would win if it were called again -- prove it isn't.
        result = coinflip.play_coinflip(self.creator_id, self.user_id, "heads", 10, key)
        self.assertEqual(result.outcome, "loss")
        self.assertEqual(result.state, cr.STATE_SETTLED)
        self.assertEqual(self._promo_balance(), 1000 - 10)

    # ------------------------------------------------------------------
    def test_crash_mid_round_resume_settles_correctly(self):
        self._fund_promo(1000)
        key = self._key()

        # Engineer the crash point: wager debited, round claimed FUNDED,
        # nothing else -- the process "died" before deciding anything.
        cl.debit(
            self.creator_id, self.user_id, cr.CURRENCY_PROMO, 10, cl.PROMO_WAGER,
            round_id=key, idempotency_key=f"{key}-wager",
        )
        with cr._connect() as connection:
            cr._ensure_schema(connection)
            cr._claim_round(connection, key, self.creator_id, self.user_id, coinflip.GAME_ID, 10)

        self.assertEqual(self._promo_balance(), 1000 - 10, "wager already landed before the 'crash'")

        casino_rng.set_provider(_FixedChoiceProvider("heads"))
        result = coinflip.play_coinflip(self.creator_id, self.user_id, "heads", 10, key)

        self.assertEqual(result.state, cr.STATE_SETTLED)
        self.assertEqual(result.outcome, "win")
        self.assertEqual(self._promo_balance(), 1000 - 10 + 20, "not debited twice, payout landed once")

        with cl._connect() as connection:
            wager_rows = connection.execute(
                f"SELECT COUNT(*) FROM {cl.TABLE_LEDGER} WHERE round_id = %s AND type = %s",
                (key, cl.PROMO_WAGER),
            ).fetchone()[0]
        self.assertEqual(wager_rows, 1, "resume must not re-debit the wager")

    # ------------------------------------------------------------------
    # CONCURRENT DOUBLE-ROLL: two callers hit the same round_id at
    # outcome=NULL simultaneously -- FOR UPDATE must serialize them so the
    # RNG rolls exactly once and both converge to the same result.
    # ------------------------------------------------------------------
    def test_concurrent_decide_serializes_and_rolls_once(self):
        self._fund_promo(1000)
        key = self._key("concurrent")

        cl.debit(
            self.creator_id, self.user_id, cr.CURRENCY_PROMO, 10, cl.PROMO_WAGER,
            round_id=key, idempotency_key=f"{key}-wager",
        )
        with cr._connect() as connection:
            cr._ensure_schema(connection)
            cr._claim_round(connection, key, self.creator_id, self.user_id, coinflip.GAME_ID, 10)

        call_count = {"n": 0}
        count_lock = threading.Lock()
        arrived = threading.Event()
        release = threading.Event()

        def counting_resolve():
            with count_lock:
                call_count["n"] += 1
            return "win", 20, {"forced": True}

        def hook():
            arrived.set()
            release.wait(timeout=10)

        results = [None, None]
        errors = [None, None]

        def worker(idx):
            try:
                results[idx] = cr._decide_outcome(key, counting_resolve, cr.DEFAULT_CONNECT_TIMEOUT_SECONDS)
            except Exception as exc:  # noqa: BLE001
                errors[idx] = exc

        cr._TEST_HOOK_AFTER_ROW_LOCK = hook
        try:
            t1 = threading.Thread(target=worker, args=(0,))
            t1.start()
            self.assertTrue(arrived.wait(timeout=10), "thread 1 should reach the row lock")

            t2 = threading.Thread(target=worker, args=(1,))
            t2.start()
            # Give thread 2's SELECT ... FOR UPDATE a real chance to reach
            # Postgres and block on thread 1's held row lock before we let
            # thread 1 proceed -- makes this a genuine contention proof,
            # not just a timing-lucky pass.
            time.sleep(0.3)

            release.set()
            t1.join(timeout=10)
            t2.join(timeout=10)
        finally:
            cr._TEST_HOOK_AFTER_ROW_LOCK = None

        self.assertIsNone(errors[0])
        self.assertIsNone(errors[1])
        self.assertEqual(call_count["n"], 1, "RNG must roll exactly once despite two concurrent decides")
        self.assertEqual(results[0], results[1], "both callers must converge to the identical outcome")

    # ------------------------------------------------------------------
    def test_insufficient_promo_rejected_no_round_created(self):
        self._fund_promo(5)
        key = self._key()

        with self.assertRaises(cl.InsufficientFunds):
            coinflip.play_coinflip(self.creator_id, self.user_id, "heads", 10, key)

        self.assertEqual(self._promo_balance(), 5)
        with cl._connect() as connection:
            round_rows = connection.execute(
                f"SELECT COUNT(*) FROM {cr.TABLE_ROUNDS} WHERE round_id = %s", (key,),
            ).fetchone()[0]
            wager_rows = connection.execute(
                f"SELECT COUNT(*) FROM {cl.TABLE_LEDGER} WHERE round_id = %s", (key,),
            ).fetchone()[0]
        self.assertEqual(round_rows, 0, "no round created")
        self.assertEqual(wager_rows, 0, "no ledger trace of a rejected wager")

    # ------------------------------------------------------------------
    def test_rng_is_server_only_no_client_outcome_param(self):
        sig = inspect.signature(coinflip.play_coinflip)
        forbidden = {"outcome", "result", "roll", "won", "win", "side"}
        self.assertTrue(
            forbidden.isdisjoint(sig.parameters.keys()),
            f"play_coinflip must not accept a client-supplied outcome, got params {list(sig.parameters)}",
        )

        # And the real (non-fixed) provider actually goes through `secrets`.
        casino_rng.set_provider(casino_rng.SecureRandomProvider())
        self._fund_promo(1000)
        with mock.patch("secrets.choice", wraps=secrets.choice) as spy:
            coinflip.play_coinflip(self.creator_id, self.user_id, "heads", 10, self._key("rng-proof"))
        self.assertTrue(spy.called, "the coin draw must go through secrets.choice")

    # ------------------------------------------------------------------
    def test_config_disabled_game_rejected(self):
        casino_config.set_game_config(self.creator_id, coinflip.GAME_ID, enabled=False)
        self._fund_promo(1000)
        key = self._key()

        with self.assertRaises(cr.GameDisabled):
            coinflip.play_coinflip(self.creator_id, self.user_id, "heads", 10, key)

        self.assertEqual(self._promo_balance(), 1000)
        with cl._connect() as connection:
            round_rows = connection.execute(
                f"SELECT COUNT(*) FROM {cr.TABLE_ROUNDS} WHERE round_id = %s", (key,),
            ).fetchone()[0]
        self.assertEqual(round_rows, 0)

    def test_config_bet_out_of_range_rejected(self):
        casino_config.set_game_config(self.creator_id, coinflip.GAME_ID, min_bet=5, max_bet=50)
        self._fund_promo(1000)

        with self.assertRaises(cr.BetOutOfRange):
            coinflip.play_coinflip(self.creator_id, self.user_id, "heads", 1, self._key("too-low"))
        with self.assertRaises(cr.BetOutOfRange):
            coinflip.play_coinflip(self.creator_id, self.user_id, "heads", 500, self._key("too-high"))

        self.assertEqual(self._promo_balance(), 1000)

    # ------------------------------------------------------------------
    def test_creator_isolation(self):
        self._fund_promo(1000, creator_id=self.creator_id)
        self._fund_promo(1000, creator_id=self.other_creator_id)
        casino_rng.set_provider(_FixedChoiceProvider("heads"))

        coinflip.play_coinflip(
            self.creator_id, self.user_id, "heads", 10, f"{self.creator_id}-iso",
        )

        self.assertEqual(self._promo_balance(creator_id=self.other_creator_id), 1000)
        self.assertEqual(self._promo_balance(), 1000 - 10 + 20)

        with cl._connect() as connection:
            other_rounds = connection.execute(
                f"SELECT COUNT(*) FROM {cr.TABLE_ROUNDS} WHERE creator_id = %s", (self.other_creator_id,),
            ).fetchone()[0]
        self.assertEqual(other_rounds, 0)


if __name__ == "__main__":
    unittest.main()
