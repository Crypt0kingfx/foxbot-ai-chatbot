"""docs/casino-double-spend-check.sql finds what it says it finds.

Runs the script's queries against TEMP tables named like the real ones
(casino_conversion_attempts, foxbot_state). Postgres resolves pg_temp
first, so the real tables are never read or written; the temp tables are
dropped at commit. Also runs the whole file unmodified against the real
local schema to prove it parses and stays inside its READ ONLY transaction.

Local Docker Postgres only (tests/db_guard.py). Run with:
    python -m unittest tests.test_casino_double_spend_sql -v
"""

import json
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import providers.promo as promo  # noqa: E402
import services.casino_ledger as cl  # noqa: E402
from services import postgres_state  # noqa: E402
from tests.db_guard import DATABASE_CONFIGURED, SKIP_REASON  # noqa: E402

SQL_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "casino-double-spend-check.sql")
CREATOR = "creator-x"


def _sql_text():
    with open(SQL_PATH, encoding="utf-8") as f:
        return f.read()


def _queries():
    text = _sql_text()
    body = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("--"))
    statements = [s.strip() for s in re.split(r";\s*\n", body) if s.strip()]
    assert statements[0].upper().startswith("BEGIN TRANSACTION READ ONLY"), statements[0]
    assert statements[-1].upper().rstrip(";") == "ROLLBACK", statements[-1]
    return statements[1:-1]


def _attempt(key, user, direction, status, foxcoins, start, end):
    return (key, CREATOR, user, foxcoins, foxcoins // 10, status, direction,
            f"2026-11-01 12:00:{start:02d}+00", f"2026-11-01 12:00:{end:02d}+00")


@unittest.skipUnless(DATABASE_CONFIGURED, SKIP_REASON)
class DoubleSpendSqlTestCase(unittest.TestCase):
    def _ensure_real_tables(self):
        with cl._connect() as connection:
            cl._ensure_schema(connection)
        with promo._connect() as connection:
            promo._ensure_schema(connection)
        with postgres_state._connect() as connection:
            postgres_state._ensure_schema(connection)

    def _autocommit(self):
        # How psql runs the file: no wrapping transaction, so the file's
        # own BEGIN TRANSACTION READ ONLY is what governs it.
        import psycopg
        return psycopg.connect(os.environ["DATABASE_URL"], autocommit=True)

    def test_file_runs_against_real_schema(self):
        self._ensure_real_tables()
        with self._autocommit() as connection:
            connection.execute(_sql_text())

    def test_file_transaction_rejects_writes(self):
        import psycopg

        self._ensure_real_tables()
        sneaky = _sql_text().replace(
            "\nROLLBACK;",
            "\nDELETE FROM casino_conversion_attempts WHERE idempotency_key = 'never-exists';\nROLLBACK;",
        )
        self.assertNotEqual(sneaky, _sql_text())
        with self._autocommit() as connection:
            with self.assertRaises(psycopg.errors.ReadOnlySqlTransaction):
                connection.execute(sneaky)

    def test_queries_flag_the_seeded_patterns(self):
        attempts = [
            # Double-spend: overlapping deposits, both debits started from 100.
            _attempt("dep-a", "alice", "deposit", "completed", 100, 0, 3),
            _attempt("dep-b", "alice", "deposit", "completed", 100, 1, 4),
            # Serialized deposits: second starts from the first's result.
            _attempt("dep-c", "bob", "deposit", "completed", 100, 10, 12),
            _attempt("dep-d", "bob", "deposit", "completed", 100, 11, 13),
            # Lost credit: overlapping cashouts, both credits started from 0.
            _attempt("out-a", "carol", "cashout", "completed", 50, 20, 22),
            _attempt("out-b", "carol", "cashout", "completed", 50, 21, 23),
            # Deposit with no FoxCoin debit recorded; cashout with no credit.
            _attempt("dep-missing", "dave", "deposit", "completed", 100, 30, 31),
            _attempt("out-missing", "erin", "cashout", "completed", 50, 40, 41),
        ]
        economy = {"foxcoin_economy": {"by_creator": {CREATOR: {
            "processed_deductions": {
                "dep-a": {"viewer": "alice", "amount": -100, "balance": 0},
                "dep-b": {"viewer": "alice", "amount": -100, "balance": 0},
                "dep-c": {"viewer": "bob", "amount": -100, "balance": 400},
                "dep-d": {"viewer": "bob", "amount": -100, "balance": 300},
            },
            "processed_credits": {
                "out-a": {"viewer": "carol", "amount": 50, "balance": 50},
                "out-b": {"viewer": "carol", "amount": 50, "balance": 50},
            },
        }}}}

        with cl._connect() as connection:
            connection.execute("""
                CREATE TEMP TABLE casino_conversion_attempts (
                    idempotency_key TEXT PRIMARY KEY, creator_id TEXT, user_id TEXT,
                    foxcoin_amount BIGINT, promo_amount BIGINT, status TEXT, direction TEXT,
                    created_at TIMESTAMPTZ, updated_at TIMESTAMPTZ) ON COMMIT DROP""")
            connection.execute("""
                CREATE TEMP TABLE foxbot_state (
                    state_key TEXT PRIMARY KEY, payload JSONB, updated_at TIMESTAMPTZ DEFAULT NOW())
                ON COMMIT DROP""")
            for row in attempts:
                connection.execute("INSERT INTO pg_temp.casino_conversion_attempts VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)", row)
            connection.execute("INSERT INTO pg_temp.foxbot_state (state_key, payload) VALUES ('foxbot_data', %s::jsonb)",
                               (json.dumps(economy),))

            results = []
            for query in _queries():
                cursor = connection.execute(query)
                cols = [c.name for c in cursor.description]
                results.append([dict(zip(cols, r)) for r in cursor.fetchall()])

        overlapping, double_spends, lost_credits, deposit_no_debit, cashout_no_credit = results

        self.assertEqual({(r["key_a"], r["key_b"]) for r in overlapping},
                         {("dep-a", "dep-b"), ("dep-c", "dep-d"), ("out-a", "out-b")})

        self.assertEqual([(r["key_a"], r["key_b"]) for r in double_spends], [("dep-a", "dep-b")],
                         "only the pair that read the same balance; the serialized pair is not flagged")
        self.assertEqual(double_spends[0]["shared_starting_balance"], 100)
        self.assertEqual(double_spends[0]["coins_overspent"], 100)

        self.assertEqual([(r["key_a"], r["key_b"]) for r in lost_credits], [("out-a", "out-b")])
        self.assertEqual(lost_credits[0]["shared_starting_balance"], 0)

        self.assertEqual([r["idempotency_key"] for r in deposit_no_debit], ["dep-missing"])
        self.assertEqual([r["idempotency_key"] for r in cashout_no_credit], ["out-missing"])


if __name__ == "__main__":
    unittest.main()
