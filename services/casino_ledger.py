"""Casino ledger -- the auditable, atomic transaction core for FoxBot's
casino subsystem (Phase 2: ledger only, no games, no FoxCoin conversion).

Currency-agnostic by construction: PROMO is the only currency any caller
uses today; BLAZE_* transaction types exist as valid values so adding real-
money later means extending providers/, not touching this module. There is
no PROMO/BLAZE branching anywhere in this file -- currency is a column and
a parameter, nothing more.

FAILS CLOSED. Unlike services/postgres_state.py's JSON-blob stores (which
degrade to local files when DATABASE_URL is unset), there is deliberately
NO local-file fallback here -- an auditable financial ledger backed by a
single-blob-overwrite JSON file isn't a ledger, it's a liability. Every
function raises CasinoUnavailable when DATABASE_URL isn't configured,
rather than silently writing somewhere less durable.

Every balance change is exactly one Postgres transaction: SELECT the
balance row FOR UPDATE (row lock -- this is what actually serializes
concurrent wagers on the same balance, not application-level locking),
compute the new balance, reject atomically if it would go negative,
write the balance, insert the ledger row, commit. psycopg3's connection
context manager commits on clean exit and rolls back on any exception,
so "balance changed but ledger row missing" (or the reverse) cannot
happen -- they are the same transaction, by construction, not by
discipline.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from typing import Any


TABLE_BALANCES = "casino_balances"
TABLE_LEDGER = "casino_ledger"

_schema_lock = threading.Lock()
_schema_ready = False

DEFAULT_CONNECT_TIMEOUT_SECONDS = 10

# Transaction types. PROMO_* are the only ones any caller in this codebase
# creates today (starting Phase 3 for CONVERT_IN, Phase 4+ for the rest).
# BLAZE_* are schema-only -- valid TEXT values, no DB-level CHECK
# constraint restricting the column, so wiring a real BLAZE_* writer later
# (providers/blaze.py, legal-gated) needs zero migration here. Nothing in
# this codebase writes a BLAZE_* row today.
PROMO_CONVERT_IN = "PROMO_CONVERT_IN"
PROMO_WAGER = "PROMO_WAGER"
PROMO_PAYOUT = "PROMO_PAYOUT"
PROMO_REFUND = "PROMO_REFUND"
PROMO_JACKPOT = "PROMO_JACKPOT"

BLAZE_DEPOSIT = "BLAZE_DEPOSIT"
BLAZE_WAGER = "BLAZE_WAGER"
BLAZE_PAYOUT = "BLAZE_PAYOUT"
BLAZE_WITHDRAWAL = "BLAZE_WITHDRAWAL"
BLAZE_REFUND = "BLAZE_REFUND"
BLAZE_JACKPOT = "BLAZE_JACKPOT"

# Caller-triggered transaction types must supply an idempotency_key -- a
# replayed request (network retry, a chat message processed twice) must
# not double-move money. Types the ledger itself derives as a side effect
# of another already-idempotent operation (a refund the ledger issues
# automatically) are not in this set; nothing in Phase 2 writes those yet.
IDEMPOTENCY_REQUIRED_TYPES = {
    PROMO_CONVERT_IN,
    PROMO_WAGER,
    PROMO_PAYOUT,
    PROMO_REFUND,
    PROMO_JACKPOT,
    BLAZE_DEPOSIT,
    BLAZE_WAGER,
    BLAZE_PAYOUT,
    BLAZE_WITHDRAWAL,
    BLAZE_REFUND,
    BLAZE_JACKPOT,
}

# Test-only hook. When set to a callable, _apply() invokes it immediately
# after the balance write and before the ledger INSERT, inside the same
# transaction. A raised exception there proves the atomicity boundary is
# real -- the `with connection:` block rolls back BOTH statements, not just
# the one that "failed" -- without needing to fake an actual Postgres
# outage at that exact instant, which isn't reliably reproducible any other
# way. None in every real code path; only ever set by
# tests/test_casino_ledger.py, and always restored to None afterward.
_TEST_FAILPOINT_AFTER_BALANCE_WRITE = None


class CasinoUnavailable(Exception):
    """Raised by every read/write function when DATABASE_URL isn't
    configured. The casino fails closed -- see module docstring for why
    there is no JSON-file fallback here."""


class InsufficientFunds(Exception):
    """A debit would take a balance negative. Raised before any write in
    the transaction, so nothing needs undoing -- balance and ledger are
    both untouched by the attempt."""


@dataclass(frozen=True)
class LedgerEntry:
    transaction_id: int
    idempotency_key: str | None
    creator_id: str
    user_id: str
    currency: str
    amount: int
    type: str
    game_id: str | None
    round_id: str | None
    balance_after: int
    display_name: str | None
    metadata: dict = field(default_factory=dict)
    created_at: Any = None
    # True when this entry was returned by an idempotent replay (same
    # idempotency_key as an earlier call) rather than a new write.
    replayed: bool = False


def database_url() -> str:
    return str(os.getenv("DATABASE_URL") or "").strip()


def is_available() -> bool:
    return bool(database_url())


def _require_available() -> None:
    if not is_available():
        raise CasinoUnavailable(
            "Casino requires DATABASE_URL (Postgres) -- there is no local-file "
            "ledger fallback by design. Set DATABASE_URL to enable the casino."
        )


def _connect(timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS):
    import psycopg

    return psycopg.connect(database_url(), connect_timeout=timeout)


def _ensure_schema(connection) -> None:
    global _schema_ready
    if _schema_ready:
        return

    with _schema_lock:
        if _schema_ready:
            return

        connection.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE_BALANCES} (
                creator_id TEXT NOT NULL,
                user_id    TEXT NOT NULL,
                currency   TEXT NOT NULL,
                balance    BIGINT NOT NULL DEFAULT 0,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (creator_id, user_id, currency)
            )
            """
        )
        connection.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE_LEDGER} (
                transaction_id  BIGSERIAL PRIMARY KEY,
                idempotency_key TEXT,
                creator_id      TEXT NOT NULL,
                user_id         TEXT NOT NULL,
                currency        TEXT NOT NULL,
                amount          BIGINT NOT NULL,
                type            TEXT NOT NULL,
                game_id         TEXT,
                round_id        TEXT,
                balance_after   BIGINT NOT NULL,
                display_name    TEXT,
                metadata        JSONB,
                created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        connection.execute(
            f"""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_{TABLE_LEDGER}_idempotency
                ON {TABLE_LEDGER} (idempotency_key)
                WHERE idempotency_key IS NOT NULL
            """
        )
        connection.execute(
            f"""
            CREATE INDEX IF NOT EXISTS idx_{TABLE_LEDGER}_creator_user_time
                ON {TABLE_LEDGER} (creator_id, user_id, created_at DESC)
            """
        )
        connection.execute(
            f"""
            CREATE INDEX IF NOT EXISTS idx_{TABLE_LEDGER}_creator_time
                ON {TABLE_LEDGER} (creator_id, created_at DESC)
            """
        )
        connection.execute(
            f"""
            CREATE INDEX IF NOT EXISTS idx_{TABLE_LEDGER}_creator_type_time
                ON {TABLE_LEDGER} (creator_id, type, created_at DESC)
            """
        )
        connection.execute(
            f"""
            CREATE INDEX IF NOT EXISTS idx_{TABLE_LEDGER}_round
                ON {TABLE_LEDGER} (round_id)
            """
        )
        _schema_ready = True


_LEDGER_COLUMNS = (
    "transaction_id, idempotency_key, creator_id, user_id, currency, amount, "
    "type, game_id, round_id, balance_after, display_name, metadata, created_at"
)


def _row_to_entry(row, replayed: bool = False) -> LedgerEntry:
    (
        transaction_id, idempotency_key, creator_id, user_id, currency, amount,
        type_, game_id, round_id, balance_after, display_name, metadata, created_at,
    ) = row
    return LedgerEntry(
        transaction_id=transaction_id,
        idempotency_key=idempotency_key,
        creator_id=creator_id,
        user_id=user_id,
        currency=currency,
        amount=int(amount),
        type=type_,
        game_id=game_id,
        round_id=round_id,
        balance_after=int(balance_after),
        display_name=display_name,
        metadata=metadata or {},
        created_at=created_at,
        replayed=replayed,
    )


def _validate_amount(amount) -> int:
    # bool is a subclass of int in Python -- True/False must never silently
    # pass as 1/0 here, same guard already used elsewhere in this codebase
    # for user-influenced numeric fields (app.py's vote-amount check).
    if not isinstance(amount, int) or isinstance(amount, bool):
        raise TypeError(
            f"amount must be a plain int (integer atomic units, never float) -- "
            f"got {type(amount).__name__}"
        )
    if amount <= 0:
        raise ValueError("amount must be a positive integer; debit()/credit() apply the sign.")
    return amount


def _apply(
    *,
    creator_id: str,
    user_id: str,
    currency: str,
    signed_amount: int,
    type: str,
    game_id: str | None,
    round_id: str | None,
    idempotency_key: str | None,
    display_name: str | None,
    metadata: dict | None,
    timeout: int,
) -> LedgerEntry:
    _require_available()

    creator_id = str(creator_id or "").strip()
    user_id = str(user_id or "").strip()
    currency = str(currency or "").strip()
    type = str(type or "").strip()

    if not creator_id or not user_id or not currency or not type:
        raise ValueError("creator_id, user_id, currency, and type are all required.")

    idempotency_key = str(idempotency_key).strip() if idempotency_key else None
    if type in IDEMPOTENCY_REQUIRED_TYPES and not idempotency_key:
        raise ValueError(f"idempotency_key is required for transaction type {type!r}.")

    from psycopg.errors import UniqueViolation
    from psycopg.types.json import Jsonb

    try:
        with _connect(timeout=timeout) as connection:
            _ensure_schema(connection)

            if idempotency_key:
                existing = connection.execute(
                    f"SELECT {_LEDGER_COLUMNS} FROM {TABLE_LEDGER} WHERE idempotency_key = %s",
                    (idempotency_key,),
                ).fetchone()
                if existing is not None:
                    return _row_to_entry(existing, replayed=True)

            row = connection.execute(
                f"""
                SELECT balance FROM {TABLE_BALANCES}
                WHERE creator_id = %s AND user_id = %s AND currency = %s
                FOR UPDATE
                """,
                (creator_id, user_id, currency),
            ).fetchone()
            current_balance = int(row[0]) if row else 0

            new_balance = current_balance + signed_amount
            if new_balance < 0:
                raise InsufficientFunds(
                    f"{creator_id}/{user_id}/{currency}: balance={current_balance}, "
                    f"requested change={signed_amount} would go negative."
                )

            if row is None:
                connection.execute(
                    f"""
                    INSERT INTO {TABLE_BALANCES} (creator_id, user_id, currency, balance, updated_at)
                    VALUES (%s, %s, %s, %s, NOW())
                    """,
                    (creator_id, user_id, currency, new_balance),
                )
            else:
                connection.execute(
                    f"""
                    UPDATE {TABLE_BALANCES} SET balance = %s, updated_at = NOW()
                    WHERE creator_id = %s AND user_id = %s AND currency = %s
                    """,
                    (new_balance, creator_id, user_id, currency),
                )

            if _TEST_FAILPOINT_AFTER_BALANCE_WRITE is not None:
                _TEST_FAILPOINT_AFTER_BALANCE_WRITE()

            inserted = connection.execute(
                f"""
                INSERT INTO {TABLE_LEDGER}
                    (idempotency_key, creator_id, user_id, currency, amount, type,
                     game_id, round_id, balance_after, display_name, metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING {_LEDGER_COLUMNS}
                """,
                (
                    idempotency_key, creator_id, user_id, currency, signed_amount, type,
                    game_id, round_id, new_balance, display_name, Jsonb(metadata or {}),
                ),
            ).fetchone()

            # Clean exit here is what commits -- balance write and ledger
            # insert live in the same transaction, so they can never land
            # separately. Any exception above (including the test failpoint)
            # rolls the whole block back instead, atomically.
            return _row_to_entry(inserted)

    except UniqueViolation:
        # Lost a genuine concurrent race for this idempotency_key -- another
        # caller's identical request committed first (its FOR UPDATE lock
        # released right before ours acquired it), and this transaction's
        # own balance write already rolled back with it. The winner's row
        # is real and committed; fetch and return it instead of raising, so
        # a true concurrent replay is graceful, not just a crash.
        if not idempotency_key:
            raise
        with _connect(timeout=timeout) as connection:
            _ensure_schema(connection)
            existing = connection.execute(
                f"SELECT {_LEDGER_COLUMNS} FROM {TABLE_LEDGER} WHERE idempotency_key = %s",
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                return _row_to_entry(existing, replayed=True)
            raise


def debit(
    creator_id: str,
    user_id: str,
    currency: str,
    amount: int,
    type: str,
    *,
    game_id: str | None = None,
    round_id: str | None = None,
    idempotency_key: str | None = None,
    display_name: str | None = None,
    metadata: dict | None = None,
    timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS,
) -> LedgerEntry:
    """Remove `amount` (positive int, atomic units) from a balance. Raises
    InsufficientFunds if the balance would go negative -- balances never go
    negative, by construction, not by a post-hoc clamp."""
    amount = _validate_amount(amount)
    return _apply(
        creator_id=creator_id,
        user_id=user_id,
        currency=currency,
        signed_amount=-amount,
        type=type,
        game_id=game_id,
        round_id=round_id,
        idempotency_key=idempotency_key,
        display_name=display_name,
        metadata=metadata,
        timeout=timeout,
    )


def credit(
    creator_id: str,
    user_id: str,
    currency: str,
    amount: int,
    type: str,
    *,
    game_id: str | None = None,
    round_id: str | None = None,
    idempotency_key: str | None = None,
    display_name: str | None = None,
    metadata: dict | None = None,
    timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS,
) -> LedgerEntry:
    """Add `amount` (positive int, atomic units) to a balance."""
    amount = _validate_amount(amount)
    return _apply(
        creator_id=creator_id,
        user_id=user_id,
        currency=currency,
        signed_amount=amount,
        type=type,
        game_id=game_id,
        round_id=round_id,
        idempotency_key=idempotency_key,
        display_name=display_name,
        metadata=metadata,
        timeout=timeout,
    )


def get_balance(
    creator_id: str,
    user_id: str,
    currency: str,
    timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS,
) -> int:
    _require_available()

    creator_id = str(creator_id or "").strip()
    user_id = str(user_id or "").strip()
    currency = str(currency or "").strip()

    with _connect(timeout=timeout) as connection:
        _ensure_schema(connection)
        row = connection.execute(
            f"""
            SELECT balance FROM {TABLE_BALANCES}
            WHERE creator_id = %s AND user_id = %s AND currency = %s
            """,
            (creator_id, user_id, currency),
        ).fetchone()
    return int(row[0]) if row else 0
