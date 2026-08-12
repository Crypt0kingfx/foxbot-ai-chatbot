"""Financial-correctness tests for services/casino_ledger.py (Casino Phase 2).

These are the tests that matter more than any game will: concurrency,
atomicity, idempotency, insufficient-funds, integer-only units, and audit
consistency. Everything here runs against a REAL Postgres database via
DATABASE_URL -- same env var the rest of this codebase already uses
(services/postgres_state.py, services/foxbot_events.py), no separate
test-database convention invented. The row lock (`SELECT ... FOR UPDATE`)
that makes the concurrency guarantee real cannot be mocked and still prove
anything, so these tests are skipped (not faked) when DATABASE_URL isn't
set, with a clear message saying why.

No pytest dependency -- this repo has no test framework installed (see
tests/test_discovery_seeding.py). Run with:
    python -m unittest tests.test_casino_ledger -v

Point DATABASE_URL at a throwaway/dev database, not production -- this
suite performs real INSERT/UPDATE/DELETE. Every test uses a fresh,
randomly-suffixed creator_id and cleans its own rows up in tearDown, so
it's safe to run repeatedly against a shared dev database, but it is still
real data hitting whatever database DATABASE_URL points to.
"""

import os
import sys
import threading
import unittest
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import services.casino_ledger as cl  # noqa: E402


DATABASE_CONFIGURED = bool(os.getenv("DATABASE_URL"))
SKIP_REASON = (
    "DATABASE_URL not set -- these tests need a real Postgres database "
    "(a throwaway/dev one, not production) to prove the row lock, "
    "transaction rollback, and idempotency-constraint behavior honestly. "
    "They are skipped, not faked, without one."
)


@unittest.skipUnless(DATABASE_CONFIGURED, SKIP_REASON)
class CasinoLedgerTestCase(unittest.TestCase):
    def setUp(self):
        self.creator_id = f"test-casino-ledger-{uuid.uuid4().hex[:12]}"
        self.user_id = "testviewer"
        self.currency = "PROMO"

    def tearDown(self):
        with cl._connect() as connection:
            cl._ensure_schema(connection)
            connection.execute(
                f"DELETE FROM {cl.TABLE_LEDGER} WHERE creator_id = %s",
                (self.creator_id,),
            )
            connection.execute(
                f"DELETE FROM {cl.TABLE_BALANCES} WHERE creator_id = %s",
                (self.creator_id,),
            )

    def _row_count(self, creator_id=None):
        with cl._connect() as connection:
            cl._ensure_schema(connection)
            row = connection.execute(
                f"SELECT COUNT(*) FROM {cl.TABLE_LEDGER} WHERE creator_id = %s",
                (creator_id or self.creator_id,),
            ).fetchone()
        return int(row[0])

    # ------------------------------------------------------------------
    # Test 3: insufficient funds
    # ------------------------------------------------------------------
    def test_insufficient_funds_rejected_atomically(self):
        cl.credit(
            self.creator_id, self.user_id, self.currency, 100, cl.PROMO_CONVERT_IN,
            idempotency_key=f"{self.creator_id}-fund",
        )
        rows_before = self._row_count()

        with self.assertRaises(cl.InsufficientFunds):
            cl.debit(
                self.creator_id, self.user_id, self.currency, 150, cl.PROMO_WAGER,
                idempotency_key=f"{self.creator_id}-overdraft",
            )

        self.assertEqual(cl.get_balance(self.creator_id, self.user_id, self.currency), 100)
        self.assertEqual(self._row_count(), rows_before, "no orphan ledger row from the failed debit")

    # ------------------------------------------------------------------
    # Test 2: idempotency
    # ------------------------------------------------------------------
    def test_idempotent_replay_returns_original_result_once(self):
        cl.credit(
            self.creator_id, self.user_id, self.currency, 500, cl.PROMO_CONVERT_IN,
            idempotency_key=f"{self.creator_id}-fund",
        )
        key = f"{self.creator_id}-wager-1"

        results = [
            cl.debit(
                self.creator_id, self.user_id, self.currency, 50, cl.PROMO_WAGER,
                idempotency_key=key,
            )
            for _ in range(5)
        ]

        first = results[0]
        self.assertFalse(first.replayed)
        for later in results[1:]:
            self.assertTrue(later.replayed)
            self.assertEqual(later.transaction_id, first.transaction_id)
            self.assertEqual(later.balance_after, first.balance_after)

        self.assertEqual(cl.get_balance(self.creator_id, self.user_id, self.currency), 450)
        with cl._connect() as connection:
            row = connection.execute(
                f"SELECT COUNT(*) FROM {cl.TABLE_LEDGER} WHERE idempotency_key = %s",
                (key,),
            ).fetchone()
        self.assertEqual(int(row[0]), 1, "five replayed debits must produce exactly one ledger row")

    def test_missing_idempotency_key_rejected_for_required_types(self):
        with self.assertRaises(ValueError):
            cl.credit(self.creator_id, self.user_id, self.currency, 10, cl.PROMO_CONVERT_IN)

    # ------------------------------------------------------------------
    # Test 4: atomicity
    # ------------------------------------------------------------------
    def test_atomicity_rollback_on_failure_between_balance_write_and_ledger_insert(self):
        cl.credit(
            self.creator_id, self.user_id, self.currency, 200, cl.PROMO_CONVERT_IN,
            idempotency_key=f"{self.creator_id}-fund",
        )
        balance_before = cl.get_balance(self.creator_id, self.user_id, self.currency)
        rows_before = self._row_count()

        def boom():
            raise RuntimeError("simulated failure between balance write and ledger insert")

        cl._TEST_FAILPOINT_AFTER_BALANCE_WRITE = boom
        try:
            with self.assertRaises(RuntimeError):
                cl.debit(
                    self.creator_id, self.user_id, self.currency, 50, cl.PROMO_WAGER,
                    idempotency_key=f"{self.creator_id}-wager-atomic",
                )
        finally:
            cl._TEST_FAILPOINT_AFTER_BALANCE_WRITE = None

        # Neither half of the transaction may have landed.
        self.assertEqual(
            cl.get_balance(self.creator_id, self.user_id, self.currency), balance_before,
            "balance must be unchanged -- the UPDATE rolled back with the rest of the transaction",
        )
        self.assertEqual(
            self._row_count(), rows_before,
            "no ledger row may exist for a transaction whose balance write rolled back",
        )

        # And the ledger must genuinely be usable afterward -- prove the
        # connection/transaction state wasn't left poisoned.
        entry = cl.debit(
            self.creator_id, self.user_id, self.currency, 50, cl.PROMO_WAGER,
            idempotency_key=f"{self.creator_id}-wager-after-rollback",
        )
        self.assertEqual(entry.balance_after, balance_before - 50)

    # ------------------------------------------------------------------
    # Test 5: integer atomic units only
    # ------------------------------------------------------------------
    def test_float_amount_rejected(self):
        with self.assertRaises(TypeError):
            cl.credit(
                self.creator_id, self.user_id, self.currency, 10.5, cl.PROMO_CONVERT_IN,
                idempotency_key=f"{self.creator_id}-float",
            )

    def test_bool_amount_rejected(self):
        with self.assertRaises(TypeError):
            cl.credit(
                self.creator_id, self.user_id, self.currency, True, cl.PROMO_CONVERT_IN,
                idempotency_key=f"{self.creator_id}-bool",
            )

    def test_zero_or_negative_amount_rejected(self):
        with self.assertRaises(ValueError):
            cl.credit(
                self.creator_id, self.user_id, self.currency, 0, cl.PROMO_CONVERT_IN,
                idempotency_key=f"{self.creator_id}-zero",
            )
        with self.assertRaises(ValueError):
            cl.debit(
                self.creator_id, self.user_id, self.currency, -5, cl.PROMO_WAGER,
                idempotency_key=f"{self.creator_id}-neg",
            )

    def test_balance_column_is_integer_in_database(self):
        cl.credit(
            self.creator_id, self.user_id, self.currency, 77, cl.PROMO_CONVERT_IN,
            idempotency_key=f"{self.creator_id}-int-check",
        )
        with cl._connect() as connection:
            row = connection.execute(
                f"""
                SELECT pg_typeof(balance)::text FROM {cl.TABLE_BALANCES}
                WHERE creator_id = %s AND user_id = %s AND currency = %s
                """,
                (self.creator_id, self.user_id, self.currency),
            ).fetchone()
        self.assertEqual(row[0], "bigint")

    # ------------------------------------------------------------------
    # Test 6: audit consistency
    # ------------------------------------------------------------------
    def test_ledger_sum_matches_balance_and_running_totals(self):
        cl.credit(
            self.creator_id, self.user_id, self.currency, 1000, cl.PROMO_CONVERT_IN,
            idempotency_key=f"{self.creator_id}-fund",
        )
        cl.debit(
            self.creator_id, self.user_id, self.currency, 300, cl.PROMO_WAGER,
            idempotency_key=f"{self.creator_id}-w1", round_id="r1",
        )
        cl.credit(
            self.creator_id, self.user_id, self.currency, 150, cl.PROMO_PAYOUT,
            idempotency_key=f"{self.creator_id}-p1", round_id="r1",
        )
        cl.debit(
            self.creator_id, self.user_id, self.currency, 400, cl.PROMO_WAGER,
            idempotency_key=f"{self.creator_id}-w2", round_id="r2",
        )

        expected_balance = 1000 - 300 + 150 - 400
        actual_balance = cl.get_balance(self.creator_id, self.user_id, self.currency)
        self.assertEqual(actual_balance, expected_balance)

        with cl._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT amount, balance_after FROM {cl.TABLE_LEDGER}
                WHERE creator_id = %s AND user_id = %s
                ORDER BY transaction_id ASC
                """,
                (self.creator_id, self.user_id),
            ).fetchall()

        running_total = 0
        for amount, balance_after in rows:
            running_total += int(amount)
            self.assertEqual(
                int(balance_after), running_total,
                "balance_after on each row must equal the running total up to that row",
            )

        self.assertEqual(running_total, actual_balance, "sum of ledger amounts must equal the live balance")
        self.assertEqual(running_total, expected_balance)

    # ------------------------------------------------------------------
    # Test 1: concurrency -- the foundation's real proof
    # ------------------------------------------------------------------
    def test_concurrent_debits_serialize_no_double_spend(self):
        """Two threads, two real connections, forced to submit their debit
        at the same instant via a Barrier. Balance is funded to exactly
        cover ONE of the two debits, not both (1000, two debits of 600) --
        a broken (unlocked) implementation would let both succeed via a
        classic read-then-write race (both read 1000, both compute 400,
        both write 400 -- no InsufficientFunds ever raised, balance ends
        up wrong). FOR UPDATE must instead serialize them: the second
        thread's SELECT ... FOR UPDATE blocks until the first thread's
        transaction commits, then sees the POST-commit balance (400) and
        correctly rejects its own 600 debit as insufficient funds.
        """
        cl.credit(
            self.creator_id, self.user_id, self.currency, 1000, cl.PROMO_CONVERT_IN,
            idempotency_key=f"{self.creator_id}-fund",
        )

        barrier = threading.Barrier(2)
        results = {}
        errors = {}

        def attempt(label, idem_suffix):
            barrier.wait(timeout=10)
            try:
                results[label] = cl.debit(
                    self.creator_id, self.user_id, self.currency, 600, cl.PROMO_WAGER,
                    idempotency_key=f"{self.creator_id}-race-{idem_suffix}",
                )
            except cl.InsufficientFunds as error:
                errors[label] = error

        t1 = threading.Thread(target=attempt, args=("a", "a"))
        t2 = threading.Thread(target=attempt, args=("b", "b"))
        t1.start()
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)

        self.assertEqual(
            len(results) + len(errors), 2, "both threads must resolve (no hang, no silent drop)"
        )
        self.assertEqual(len(results), 1, "exactly one debit must succeed")
        self.assertEqual(len(errors), 1, "exactly one debit must be rejected as insufficient funds")

        final_balance = cl.get_balance(self.creator_id, self.user_id, self.currency)
        self.assertEqual(
            final_balance, 400,
            "no lost update: final balance must reflect exactly the ONE debit that succeeded (1000-600)",
        )

        with cl._connect() as connection:
            wager_rows = connection.execute(
                f"""
                SELECT COUNT(*) FROM {cl.TABLE_LEDGER}
                WHERE creator_id = %s AND user_id = %s AND type = %s
                """,
                (self.creator_id, self.user_id, cl.PROMO_WAGER),
            ).fetchone()
        self.assertEqual(
            int(wager_rows[0]), 1,
            "exactly one PROMO_WAGER ledger row -- the failed attempt must not have inserted anything",
        )

    def test_concurrent_replays_of_same_idempotency_key_produce_one_row(self):
        """Two threads submit the SAME idempotency_key at the same instant.
        Whichever loses the row-lock race must hit the UniqueViolation
        fallback path in _apply() and return the winner's committed row --
        not crash, not silently double-apply."""
        cl.credit(
            self.creator_id, self.user_id, self.currency, 500, cl.PROMO_CONVERT_IN,
            idempotency_key=f"{self.creator_id}-fund",
        )

        barrier = threading.Barrier(2)
        key = f"{self.creator_id}-shared-race"
        results = []
        lock = threading.Lock()

        def attempt():
            barrier.wait(timeout=10)
            entry = cl.debit(
                self.creator_id, self.user_id, self.currency, 50, cl.PROMO_WAGER,
                idempotency_key=key,
            )
            with lock:
                results.append(entry)

        threads = [threading.Thread(target=attempt) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        self.assertEqual(len(results), 2, "both threads must return, neither may raise")
        self.assertEqual(results[0].transaction_id, results[1].transaction_id)
        self.assertEqual(
            sum(1 for r in results if r.replayed), 1,
            "exactly one of the two concurrent callers must observe the replay path",
        )
        self.assertEqual(cl.get_balance(self.creator_id, self.user_id, self.currency), 450)


class CasinoLedgerFailClosedTestCase(unittest.TestCase):
    """No DATABASE_URL required -- these prove the fail-closed contract
    itself, which must hold precisely when there's no database to test
    against."""

    def setUp(self):
        self._original = os.environ.pop("DATABASE_URL", None)

    def tearDown(self):
        if self._original is not None:
            os.environ["DATABASE_URL"] = self._original

    def test_is_available_false_without_database_url(self):
        self.assertFalse(cl.is_available())

    def test_get_balance_raises_casino_unavailable(self):
        with self.assertRaises(cl.CasinoUnavailable):
            cl.get_balance("c", "u", "PROMO")

    def test_debit_raises_casino_unavailable_not_a_file_fallback(self):
        with self.assertRaises(cl.CasinoUnavailable):
            cl.debit("c", "u", "PROMO", 10, cl.PROMO_WAGER, idempotency_key="k")

    def test_credit_raises_casino_unavailable_not_a_file_fallback(self):
        with self.assertRaises(cl.CasinoUnavailable):
            cl.credit("c", "u", "PROMO", 10, cl.PROMO_CONVERT_IN, idempotency_key="k")


if __name__ == "__main__":
    unittest.main()
