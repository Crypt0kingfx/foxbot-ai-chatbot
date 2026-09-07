"""Financial-correctness tests for providers/promo.py: FoxCoin <-> PROMO
conversion (Casino Phase 3 deposit, Phase 8 cashout), the one place the
non-atomic FoxCoin economy (app.py) and the atomic casino ledger
(services/casino_ledger.py) meet, in both directions.

Same convention as tests/test_casino_ledger.py: real Postgres via
DATABASE_URL, skipped (not faked) without one, since the two-system
ordering/idempotency proofs here (replay, crash-retry) depend on real
transactional behavior that can't be mocked honestly. Run with:
    python -m unittest tests.test_promo_provider -v

Point DATABASE_URL at a throwaway/dev database, not production. Every test
uses a fresh, randomly-suffixed creator_id and cleans its own Postgres rows
up in tearDown. The FoxCoin side (app.py's foxcoin_economy) is an in-memory
dict scoped to this process and never persisted by these tests, so it needs
no cleanup.
"""

import os
import sys
import unittest
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app  # noqa: E402
import providers.promo as promo  # noqa: E402
import services.casino_config as casino_config  # noqa: E402
import services.casino_ledger as cl  # noqa: E402


DATABASE_CONFIGURED = bool(os.getenv("DATABASE_URL"))
SKIP_REASON = (
    "DATABASE_URL not set -- these tests need a real Postgres database "
    "(a throwaway/dev one, not production) to prove the claim/debit/credit "
    "ordering and idempotency honestly. They are skipped, not faked, "
    "without one."
)


@unittest.skipUnless(DATABASE_CONFIGURED, SKIP_REASON)
class PromoProviderTestCase(unittest.TestCase):
    def setUp(self):
        self.creator_id = f"test-promo-{uuid.uuid4().hex[:12]}"
        self.other_creator_id = f"test-promo-other-{uuid.uuid4().hex[:12]}"
        self.user_id = "testviewer"
        self.rate = 10          # FoxCoins per PROMO credit
        self.daily_limit = 5000

        casino_config.set_config(
            self.creator_id, foxcoins_per_promo=self.rate, daily_promo_limit=self.daily_limit,
        )
        casino_config.set_config(
            self.other_creator_id, foxcoins_per_promo=self.rate, daily_promo_limit=self.daily_limit,
        )

        self.provider = promo.PromoProvider()

    def tearDown(self):
        with cl._connect() as connection:
            cl._ensure_schema(connection)
            for cid in (self.creator_id, self.other_creator_id):
                connection.execute(
                    f"DELETE FROM {cl.TABLE_LEDGER} WHERE creator_id = %s", (cid,),
                )
                connection.execute(
                    f"DELETE FROM {cl.TABLE_BALANCES} WHERE creator_id = %s", (cid,),
                )
                connection.execute(
                    f"DELETE FROM {promo.TABLE_ATTEMPTS} WHERE creator_id = %s", (cid,),
                )
                connection.execute(
                    f"DELETE FROM {casino_config.TABLE_CONFIG} WHERE creator_id = %s", (cid,),
                )

    def _seed_foxcoins(self, amount, creator_id=None):
        app.add_points(self.user_id, amount, "test_seed", creator_id=creator_id or self.creator_id)

    def _foxcoin_balance(self, creator_id=None):
        return app.get_balance(self.user_id, creator_id=creator_id or self.creator_id)

    def _promo_balance(self, creator_id=None):
        return cl.get_balance(creator_id or self.creator_id, self.user_id, promo.CURRENCY_PROMO)

    def _key(self, suffix="convert-1"):
        return f"{self.creator_id}-{suffix}"

    def _seed_promo(self, promo_amount, creator_id=None):
        # Dogfoods the proven deposit() path rather than writing a raw
        # ledger row -- gives cashout tests a real, audit-trailed starting
        # PROMO balance the same way one would exist in production. Uses
        # the default daily_promo_limit (5000) headroom; tests that need a
        # tight limit configure it AFTER seeding, or seed+spend within the
        # tight limit's own budget (see test_shared_daily_limit_* below).
        cid = creator_id or self.creator_id
        self._seed_foxcoins(promo_amount * self.rate, creator_id=cid)
        self.provider.deposit(
            cid, self.user_id, promo_amount, idempotency_key=f"seed-promo-{uuid.uuid4().hex[:8]}",
        )

    # ------------------------------------------------------------------
    def test_happy_path_conversion(self):
        self._seed_foxcoins(1000)
        result = self.provider.deposit(
            self.creator_id, self.user_id, 5, idempotency_key=self._key(),
        )

        self.assertEqual(result["promo_amount"], 5)
        self.assertEqual(result["promo_balance"], 5)
        self.assertFalse(result["replayed"])
        self.assertEqual(self._foxcoin_balance(), 1000 - 5 * self.rate)
        self.assertEqual(self._promo_balance(), 5)

    # ------------------------------------------------------------------
    # The core safety proof: replay must not double-debit or double-credit.
    # ------------------------------------------------------------------
    def test_replay_same_idempotency_key_does_not_double_convert(self):
        self._seed_foxcoins(1000)
        key = self._key()

        results = [
            self.provider.deposit(self.creator_id, self.user_id, 5, idempotency_key=key)
            for _ in range(5)
        ]

        self.assertFalse(results[0]["replayed"])
        for later in results[1:]:
            self.assertTrue(later["replayed"])
            self.assertEqual(later["transaction_id"], results[0]["transaction_id"])

        # Exactly ONE conversion's worth of effect on both sides, not five.
        self.assertEqual(self._foxcoin_balance(), 1000 - 5 * self.rate)
        self.assertEqual(self._promo_balance(), 5)

        with cl._connect() as connection:
            ledger_rows = connection.execute(
                f"SELECT COUNT(*) FROM {cl.TABLE_LEDGER} WHERE idempotency_key = %s", (key,),
            ).fetchone()[0]
        self.assertEqual(ledger_rows, 1)

    # ------------------------------------------------------------------
    def test_insufficient_foxcoins_rejected_no_partial_state(self):
        self._seed_foxcoins(20)  # cost for 5 promo would be 50, not enough
        key = self._key()

        with self.assertRaises(app.InsufficientFoxCoins):
            self.provider.deposit(self.creator_id, self.user_id, 5, idempotency_key=key)

        self.assertEqual(self._foxcoin_balance(), 20, "rejected debit must not touch the balance")
        self.assertEqual(self._promo_balance(), 0, "no promo credit without a confirmed debit")

        with cl._connect() as connection:
            status = connection.execute(
                f"SELECT status FROM {promo.TABLE_ATTEMPTS} WHERE idempotency_key = %s", (key,),
            ).fetchone()[0]
        self.assertEqual(status, promo.STATUS_CLAIMED, "still resumable, not stuck")

    def test_insufficient_funds_then_topped_up_retry_succeeds(self):
        self._seed_foxcoins(20)
        key = self._key()

        with self.assertRaises(app.InsufficientFoxCoins):
            self.provider.deposit(self.creator_id, self.user_id, 5, idempotency_key=key)

        self._seed_foxcoins(1000)  # top up
        result = self.provider.deposit(self.creator_id, self.user_id, 5, idempotency_key=key)

        self.assertFalse(result["replayed"])
        self.assertEqual(self._promo_balance(), 5)

    # ------------------------------------------------------------------
    # Crash simulation: FoxCoins already debited, PROMO not yet credited --
    # prove retry resumes cleanly instead of double-debiting or losing the
    # conversion.
    # ------------------------------------------------------------------
    def test_crash_between_debit_and_credit_retry_completes_correctly(self):
        self._seed_foxcoins(1000)
        key = self._key()
        foxcoin_cost = 5 * self.rate

        # Engineer exactly the state a real crash would leave behind: the
        # FoxCoin debit happened, the attempt was claimed, but the process
        # died before the PROMO credit (and before marking DEBITED).
        app.debit_foxcoins_idempotent(
            self.user_id, foxcoin_cost, key, reason="casino_promo_convert", creator_id=self.creator_id,
        )
        with promo._connect() as connection:
            promo._ensure_schema(connection)
            promo._claim(
                connection, key, self.creator_id, self.user_id, foxcoin_cost, 5,
                direction=promo.DIRECTION_DEPOSIT,
            )
            promo._set_status(connection, key, promo.STATUS_DEBITED)

        self.assertEqual(self._foxcoin_balance(), 1000 - foxcoin_cost, "debit already landed")
        self.assertEqual(self._promo_balance(), 0, "credit has not happened yet")

        result = self.provider.deposit(self.creator_id, self.user_id, 5, idempotency_key=key)

        self.assertFalse(result["replayed"])
        self.assertEqual(self._promo_balance(), 5)
        # Not debited a second time -- balance reflects exactly one debit.
        self.assertEqual(self._foxcoin_balance(), 1000 - foxcoin_cost)

        with promo._connect() as connection:
            status = connection.execute(
                f"SELECT status FROM {promo.TABLE_ATTEMPTS} WHERE idempotency_key = %s", (key,),
            ).fetchone()[0]
        self.assertEqual(status, promo.STATUS_COMPLETED)

    # ------------------------------------------------------------------
    def test_daily_limit_enforced(self):
        casino_config.set_config(self.creator_id, foxcoins_per_promo=self.rate, daily_promo_limit=10)
        self._seed_foxcoins(10_000)

        self.provider.deposit(self.creator_id, self.user_id, 10, idempotency_key=self._key("a"))

        with self.assertRaises(promo.DailyLimitExceeded):
            self.provider.deposit(self.creator_id, self.user_id, 1, idempotency_key=self._key("b"))

        # Rejected request must not have moved anything.
        self.assertEqual(self._promo_balance(), 10)

    # ==================================================================
    # Phase 8: cashout (withdraw) -- the reverse leg. Same rigor as the
    # deposit tests above, ordering reversed: PROMO ledger debit first
    # (atomic, provable), FoxCoin credit second (the side safe to retry).
    # ==================================================================
    def test_happy_path_cashout(self):
        self._seed_promo(20)
        key = self._key("cashout-1")

        result = self.provider.withdraw(self.creator_id, self.user_id, 5, idempotency_key=key)

        self.assertEqual(result["promo_amount"], 5)
        self.assertEqual(result["foxcoin_amount"], 5 * self.rate)
        self.assertFalse(result["replayed"])
        self.assertEqual(self._promo_balance(), 15)
        self.assertEqual(self._foxcoin_balance(), 5 * self.rate)
        self.assertEqual(result["foxcoin_balance"], self._foxcoin_balance())
        self.assertEqual(result["promo_balance"], 15)

    # ------------------------------------------------------------------
    # The core safety proof, cashout direction: replay must not
    # double-debit PROMO or double-credit FoxCoins.
    # ------------------------------------------------------------------
    def test_replay_same_idempotency_key_does_not_double_cashout(self):
        self._seed_promo(20)
        key = self._key("cashout-replay")

        results = [
            self.provider.withdraw(self.creator_id, self.user_id, 5, idempotency_key=key)
            for _ in range(5)
        ]

        self.assertFalse(results[0]["replayed"])
        for later in results[1:]:
            self.assertTrue(later["replayed"])
            self.assertEqual(later["transaction_id"], results[0]["transaction_id"])

        # Exactly ONE cashout's worth of effect on both sides, not five.
        self.assertEqual(self._promo_balance(), 15)
        self.assertEqual(self._foxcoin_balance(), 5 * self.rate)

        with cl._connect() as connection:
            ledger_rows = connection.execute(
                f"SELECT COUNT(*) FROM {cl.TABLE_LEDGER} WHERE idempotency_key = %s", (key,),
            ).fetchone()[0]
        self.assertEqual(ledger_rows, 1)

    # ------------------------------------------------------------------
    def test_insufficient_promo_rejected_no_partial_state(self):
        self._seed_promo(3)
        key = self._key("cashout-insufficient")

        with self.assertRaises(cl.InsufficientFunds):
            self.provider.withdraw(self.creator_id, self.user_id, 5, idempotency_key=key)

        self.assertEqual(self._promo_balance(), 3, "rejected debit must not touch the promo balance")
        self.assertEqual(self._foxcoin_balance(), 0, "no FoxCoin credit without a confirmed debit")

        with promo._connect() as connection:
            status = connection.execute(
                f"SELECT status FROM {promo.TABLE_ATTEMPTS} WHERE idempotency_key = %s", (key,),
            ).fetchone()[0]
        self.assertEqual(status, promo.STATUS_CLAIMED, "still resumable, not stuck")

    def test_insufficient_promo_then_topped_up_retry_succeeds(self):
        self._seed_promo(3)
        key = self._key("cashout-topup")

        with self.assertRaises(cl.InsufficientFunds):
            self.provider.withdraw(self.creator_id, self.user_id, 5, idempotency_key=key)

        self._seed_promo(20)  # top up
        result = self.provider.withdraw(self.creator_id, self.user_id, 5, idempotency_key=key)

        self.assertFalse(result["replayed"])
        self.assertEqual(self._foxcoin_balance(), 5 * self.rate)

    # ------------------------------------------------------------------
    # THE critical proof: crash simulation. PROMO already debited, FoxCoin
    # not yet credited -- prove retry resumes cleanly instead of
    # double-debiting PROMO or losing the cashout.
    # ------------------------------------------------------------------
    def test_crash_between_promo_debit_and_foxcoin_credit_retry_completes_correctly(self):
        self._seed_promo(20)
        key = self._key("cashout-crash")
        promo_amount = 5
        foxcoin_amount = promo_amount * self.rate

        # Engineer exactly the state a real crash would leave behind: the
        # PROMO debit happened (real, on the atomic ledger) and the
        # attempt was claimed, but the process died before the FoxCoin
        # credit -- and before even marking the attempt DEBITED.
        cl.debit(
            self.creator_id, self.user_id, promo.CURRENCY_PROMO, promo_amount, cl.PROMO_CONVERT_OUT,
            idempotency_key=key,
        )
        with promo._connect() as connection:
            promo._ensure_schema(connection)
            promo._claim(
                connection, key, self.creator_id, self.user_id, foxcoin_amount, promo_amount,
                direction=promo.DIRECTION_CASHOUT,
            )
            promo._set_status(connection, key, promo.STATUS_DEBITED)

        self.assertEqual(self._promo_balance(), 15, "debit already landed")
        self.assertEqual(self._foxcoin_balance(), 0, "credit has not happened yet")

        result = self.provider.withdraw(self.creator_id, self.user_id, promo_amount, idempotency_key=key)

        # Resuming into the credit leg re-issues the (idempotent) PROMO
        # debit call to fetch its entry for the result -- that debit was
        # already committed by the crash-simulation setup above, so THIS
        # call's debit_entry is legitimately a replay. That's the opposite
        # of deposit()'s equivalent assertion (False) because deposit's
        # result is built from its SECOND leg (the PROMO credit, genuinely
        # new here), while withdraw's result is built from its FIRST leg
        # (the PROMO debit, already-committed here) -- see providers/
        # promo.py's withdraw() docstring.
        self.assertTrue(result["replayed"])
        self.assertEqual(self._foxcoin_balance(), foxcoin_amount)
        # Not debited a second time -- balance reflects exactly one debit.
        self.assertEqual(self._promo_balance(), 15)

        with promo._connect() as connection:
            status = connection.execute(
                f"SELECT status FROM {promo.TABLE_ATTEMPTS} WHERE idempotency_key = %s", (key,),
            ).fetchone()[0]
        self.assertEqual(status, promo.STATUS_COMPLETED)

    # ------------------------------------------------------------------
    def test_shared_daily_limit_enforced_across_convert_and_cashout(self):
        """The daily_promo_limit is shared between deposit() and
        withdraw() by design (Phase 8) -- converting up to the limit and
        immediately cashing back out must NOT bypass the daily cap."""
        casino_config.set_config(self.creator_id, foxcoins_per_promo=self.rate, daily_promo_limit=10)
        self._seed_foxcoins(10_000)

        self.provider.deposit(self.creator_id, self.user_id, 10, idempotency_key=self._key("shared-in"))
        self.assertEqual(self._promo_balance(), 10)

        with self.assertRaises(promo.DailyLimitExceeded):
            self.provider.withdraw(self.creator_id, self.user_id, 1, idempotency_key=self._key("shared-out"))

        # Rejected cashout must not have moved anything.
        self.assertEqual(self._promo_balance(), 10)
        self.assertEqual(self._foxcoin_balance(), 10_000 - 10 * self.rate)

    def test_shared_daily_limit_cashout_alone_also_enforced(self):
        casino_config.set_config(self.creator_id, foxcoins_per_promo=self.rate, daily_promo_limit=10)
        self._seed_promo(10)  # consumes 10 of the 10 limit via deposit() internally

        with self.assertRaises(promo.DailyLimitExceeded):
            self.provider.withdraw(self.creator_id, self.user_id, 1, idempotency_key=self._key("cashout-over"))

        self.assertEqual(self._promo_balance(), 10)

    # ------------------------------------------------------------------
    def test_creator_isolation_cashout(self):
        self._seed_promo(20, creator_id=self.creator_id)
        self._seed_promo(20, creator_id=self.other_creator_id)

        self.provider.withdraw(
            self.creator_id, self.user_id, 5,
            idempotency_key=f"{self.creator_id}-cashout-iso",
        )

        # Same viewer name, other creator: untouched by the above.
        self.assertEqual(self._promo_balance(creator_id=self.other_creator_id), 20)
        self.assertEqual(self._foxcoin_balance(creator_id=self.other_creator_id), 0)

        # First creator's own balances reflect exactly its own cashout.
        self.assertEqual(self._promo_balance(), 15)
        self.assertEqual(self._foxcoin_balance(), 5 * self.rate)

    # ------------------------------------------------------------------
    def test_replay_with_mismatched_amount_rejected_cashout(self):
        self._seed_promo(20)
        key = self._key("cashout-mismatch")

        self.provider.withdraw(self.creator_id, self.user_id, 5, idempotency_key=key)

        with self.assertRaises(ValueError):
            self.provider.withdraw(self.creator_id, self.user_id, 6, idempotency_key=key)

    def test_same_idempotency_key_across_deposit_and_cashout_rejected(self):
        """direction is part of the attempt-row identity, not just an
        implementation detail -- reusing a key across the two directions
        must be refused, same as reusing it across two different amounts."""
        self._seed_promo(20)
        key = "shared-key-different-direction"

        self.provider.withdraw(self.creator_id, self.user_id, 5, idempotency_key=key)

        with self.assertRaises(ValueError):
            self.provider.deposit(self.creator_id, self.user_id, 5, idempotency_key=key)

    # ------------------------------------------------------------------
    def test_creator_isolation(self):
        self._seed_foxcoins(1000, creator_id=self.creator_id)
        self._seed_foxcoins(1000, creator_id=self.other_creator_id)

        self.provider.deposit(
            self.creator_id, self.user_id, 5,
            idempotency_key=f"{self.creator_id}-iso",
        )

        # Same viewer name, other creator: untouched by the above.
        self.assertEqual(self._foxcoin_balance(creator_id=self.other_creator_id), 1000)
        self.assertEqual(self._promo_balance(creator_id=self.other_creator_id), 0)

        # First creator's own balances reflect exactly its own conversion.
        self.assertEqual(self._foxcoin_balance(), 1000 - 5 * self.rate)
        self.assertEqual(self._promo_balance(), 5)

    # ------------------------------------------------------------------
    def test_replay_with_mismatched_amount_rejected(self):
        self._seed_foxcoins(1000)
        key = self._key()

        self.provider.deposit(self.creator_id, self.user_id, 5, idempotency_key=key)

        with self.assertRaises(ValueError):
            self.provider.deposit(self.creator_id, self.user_id, 6, idempotency_key=key)


if __name__ == "__main__":
    unittest.main()
