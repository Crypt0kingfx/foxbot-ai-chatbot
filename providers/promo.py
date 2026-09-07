"""FoxCoin <-> casino PROMO credit conversion (Casino Phase 3 deposit,
Phase 8 cashout). The currency-specific seam between the FoxCoin economy
(app.py's JSON-blob store) and the currency-agnostic casino ledger
(services/casino_ledger.py).

Two-way by design as of Phase 8: the Phase 1 decision to keep this loop
one-way (withdraw() raised PromoWithdrawalNotAllowed) was explicitly
because the casino was unproven. It has since run live with real players
for weeks on the same debit-before-credit discipline this module already
used for deposits -- withdraw() below is the reverse leg, built on the
identical ordering principle, not a new one.

THE TWO-SYSTEM PROBLEM, both directions. The FoxCoin economy is a non-
atomic, non-idempotent JSON blob (app.py); the casino ledger is atomic,
idempotent Postgres. Whichever side is credited SECOND is the one that
can safely no-op on a retry; crediting the other side first, before its
matching debit is durably confirmed, would let a crash between the two
steps mint currency from nothing. Concretely:

  deposit()  (FoxCoin -> PROMO): FoxCoin debit first (app.py,
             debit_foxcoins_idempotent), THEN the PROMO ledger credit.
  withdraw() (PROMO -> FoxCoin): PROMO ledger debit first (atomic,
             provably-exactly-once via the ledger's own idempotency-key
             uniqueness), THEN the FoxCoin credit (app.py,
             credit_foxcoins_idempotent) -- same principle, ledger side
             goes first in both directions because it is the side that
             can prove "already done" on a resume; the FoxCoin blob side
             goes second because re-issuing an idempotent credit/debit
             against it is always safe.

Both functions track each attempt through three states in a small
Postgres table this module owns (casino_conversion_attempts), now with a
`direction` column ('deposit' or 'cashout') so a resumed attempt knows
which leg was already committed:

    CLAIMED   -- attempt row inserted, first leg not yet confirmed.
    DEBITED   -- first leg (debit) is done, second leg (credit) is not.
    COMPLETED -- both sides done.

Every transition either function drives is itself idempotent on the SAME
idempotency_key, which is what makes every state safe to resume from
after a crash, indefinitely, without special-casing failure: resuming a
CLAIMED attempt just re-attempts the first leg (no-op if it already
happened, a clean InsufficientFoxCoins/InsufficientFunds raise if it
didn't and still can't); resuming a DEBITED attempt just re-issues the
idempotent second leg. Neither step can double-apply. There is
deliberately no fourth "failed" state -- nothing in this flow needs one,
since every step is either not-yet-attempted or safely re-attemptable.
"""

from __future__ import annotations

import threading

from providers.base import SettlementProvider
from services import casino_config
from services import casino_ledger as cl


TABLE_ATTEMPTS = "casino_conversion_attempts"
CURRENCY_PROMO = "PROMO"

_schema_lock = threading.Lock()
_schema_ready = False

DEFAULT_CONNECT_TIMEOUT_SECONDS = 10

STATUS_CLAIMED = "claimed"
STATUS_DEBITED = "debited"
STATUS_COMPLETED = "completed"

DIRECTION_DEPOSIT = "deposit"
DIRECTION_CASHOUT = "cashout"


class DailyLimitExceeded(Exception):
    """The creator's configured daily PROMO conversion limit (services/
    casino_config.py) would be exceeded by this request -- shared between
    deposit() and withdraw() by design (Phase 8): it caps total daily
    PROMO conversion VOLUME in either direction, not each direction
    independently, so converting up to the limit and immediately cashing
    back out cannot be used to move twice the configured daily amount."""


def _default_debit_foxcoins_idempotent(name, amount, idempotency_key, reason="casino_promo_convert", creator_id=None):
    # Deferred import: app.py is the Flask entrypoint and must not be
    # imported at module load time by a provider -- that would both risk
    # an import cycle once app.py wires this provider up, and drag in
    # app.py's full startup just to import this module for tests. Only
    # imported the first time a conversion actually runs.
    import app as _app

    return _app.debit_foxcoins_idempotent(name, amount, idempotency_key, reason=reason, creator_id=creator_id)


def _default_credit_foxcoins_idempotent(name, amount, idempotency_key, reason="casino_promo_cashout", creator_id=None):
    # Same deferred-import discipline as _default_debit_foxcoins_idempotent
    # above, mirrored for the credit side (Phase 8 cashout's second leg).
    import app as _app

    return _app.credit_foxcoins_idempotent(name, amount, idempotency_key, reason=reason, creator_id=creator_id)


def _connect(timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS):
    import psycopg

    if not cl.is_available():
        raise cl.CasinoUnavailable(
            "Casino requires DATABASE_URL (Postgres) -- see services/casino_ledger.py."
        )
    return psycopg.connect(cl.database_url(), connect_timeout=timeout)


def _ensure_schema(connection) -> None:
    global _schema_ready
    if _schema_ready:
        return

    with _schema_lock:
        if _schema_ready:
            return

        connection.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE_ATTEMPTS} (
                idempotency_key TEXT PRIMARY KEY,
                creator_id      TEXT NOT NULL,
                user_id         TEXT NOT NULL,
                foxcoin_amount  BIGINT NOT NULL,
                promo_amount    BIGINT NOT NULL,
                status          TEXT NOT NULL,
                created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        # Phase 8 cashout: which direction this attempt moves value, so a
        # resumed row knows which leg (debit vs credit, and on which
        # system) was already committed. Existing rows predate this
        # column and are all deposits by construction -- DEFAULT
        # 'deposit' backfills them correctly, not just harmlessly.
        connection.execute(
            f"""
            ALTER TABLE {TABLE_ATTEMPTS}
            ADD COLUMN IF NOT EXISTS direction TEXT NOT NULL DEFAULT '{DIRECTION_DEPOSIT}'
            """
        )
        connection.execute(
            f"""
            CREATE INDEX IF NOT EXISTS idx_{TABLE_ATTEMPTS}_creator_user_time
                ON {TABLE_ATTEMPTS} (creator_id, user_id, created_at DESC)
            """
        )
        # casino_ledger's own tables must exist before _today_promo_volume()
        # queries TABLE_LEDGER directly below -- cheap and idempotent (IF NOT
        # EXISTS) to call here every time schema readiness is established.
        cl._ensure_schema(connection)
        _schema_ready = True


def _claim(connection, idempotency_key, creator_id, user_id, foxcoin_amount, promo_amount, direction):
    row = connection.execute(
        f"""
        INSERT INTO {TABLE_ATTEMPTS}
            (idempotency_key, creator_id, user_id, foxcoin_amount, promo_amount, status, direction)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (idempotency_key) DO NOTHING
        RETURNING idempotency_key, creator_id, user_id, foxcoin_amount, promo_amount, status, direction
        """,
        (idempotency_key, creator_id, user_id, foxcoin_amount, promo_amount, STATUS_CLAIMED, direction),
    ).fetchone()
    if row is not None:
        return row

    # Already claimed (by us on a prior attempt, or a concurrent replay) --
    # fetch the existing row to resume from instead of restarting blind.
    return connection.execute(
        f"""
        SELECT idempotency_key, creator_id, user_id, foxcoin_amount, promo_amount, status, direction
        FROM {TABLE_ATTEMPTS} WHERE idempotency_key = %s
        """,
        (idempotency_key,),
    ).fetchone()


def _set_status(connection, idempotency_key, status):
    connection.execute(
        f"UPDATE {TABLE_ATTEMPTS} SET status = %s, updated_at = NOW() WHERE idempotency_key = %s",
        (status, idempotency_key),
    )


def _today_promo_volume(creator_id, user_id, timeout=DEFAULT_CONNECT_TIMEOUT_SECONDS) -> int:
    """Total PROMO conversion volume today, EITHER direction combined --
    Phase 8: deposit() and withdraw() share one daily_promo_limit cap, so
    this sums the absolute size of every PROMO_CONVERT_IN/_OUT ledger row
    rather than each type separately. amount is stored signed (credits
    positive, debits negative) -- ABS() makes both directions add to the
    same running total instead of partially cancelling each other out."""
    with _connect(timeout=timeout) as connection:
        _ensure_schema(connection)
        row = connection.execute(
            f"""
            SELECT COALESCE(SUM(ABS(amount)), 0) FROM {cl.TABLE_LEDGER}
            WHERE creator_id = %s AND user_id = %s
              AND type IN (%s, %s)
              AND created_at >= date_trunc('day', NOW())
            """,
            (creator_id, user_id, cl.PROMO_CONVERT_IN, cl.PROMO_CONVERT_OUT),
        ).fetchone()
    return int(row[0]) if row else 0


class PromoProvider(SettlementProvider):
    currency = CURRENCY_PROMO

    def __init__(self, *, debit_foxcoins_idempotent=None, credit_foxcoins_idempotent=None, get_config=None):
        # Injectable for tests; defaults to the real FoxCoin economy /
        # casino_config accessors in production.
        self._debit_foxcoins_idempotent = debit_foxcoins_idempotent or _default_debit_foxcoins_idempotent
        self._credit_foxcoins_idempotent = credit_foxcoins_idempotent or _default_credit_foxcoins_idempotent
        self._get_config = get_config or casino_config.get_config

    def deposit(
        self,
        creator_id: str,
        user_id: str,
        promo_amount: int,
        *,
        idempotency_key: str,
        display_name: str | None = None,
        timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    ) -> dict:
        """Convert FoxCoins into `promo_amount` PROMO credits. `user_id`
        must already be the canonical viewer key (same identity used on
        both the FoxCoin and casino-ledger sides) -- callers pass the
        resolved key, this function does not re-resolve identity.

        FoxCoin cost is computed from casino_config's per-creator rate as
        `promo_amount * foxcoins_per_promo` -- exact, no rounding, no
        leftover FoxCoins, no fractional units on either side.

        Safe to call twice with the same idempotency_key: whichever stage
        a prior attempt reached, this resumes from there and returns the
        same result rather than moving value again.
        """
        if not idempotency_key:
            raise ValueError("idempotency_key is required.")
        if not isinstance(promo_amount, int) or isinstance(promo_amount, bool) or promo_amount <= 0:
            raise ValueError("promo_amount must be a positive integer.")

        creator_id = str(creator_id or "").strip()
        user_id = str(user_id or "").strip()
        if not creator_id or not user_id:
            raise ValueError("creator_id and user_id are required.")

        config = self._get_config(creator_id, timeout=timeout)
        foxcoin_cost = promo_amount * config.foxcoins_per_promo

        # Daily-limit pre-check against the ledger's own audit trail --
        # authoritative for what has actually completed, but not inside
        # the same transaction as the claim below. A race between two
        # concurrent conversions can both pass this check and jointly
        # exceed the cap by one in-flight request; that's a policy-limit
        # bound being loosely enforced, not a balance/ledger correctness
        # violation -- the debit/credit steps below are still individually
        # exact and safe either way. Tightening this to a hard per-request
        # cap would need a locked daily-counter row, deferred as unneeded
        # complexity unless abuse in practice proves otherwise.
        already_converted = _today_promo_volume(creator_id, user_id, timeout=timeout)
        if already_converted + promo_amount > config.daily_promo_limit:
            raise DailyLimitExceeded(
                f"{user_id}: already converted {already_converted} PROMO today (either direction), "
                f"limit {config.daily_promo_limit}, requested {promo_amount} more."
            )

        with _connect(timeout=timeout) as connection:
            _ensure_schema(connection)
            attempt = _claim(
                connection, idempotency_key, creator_id, user_id, foxcoin_cost, promo_amount,
                direction=DIRECTION_DEPOSIT,
            )

        (_, a_creator, a_user, a_foxcoin, a_promo, status, a_direction) = attempt
        if (
            a_creator != creator_id or a_user != user_id or a_foxcoin != foxcoin_cost
            or a_promo != promo_amount or a_direction != DIRECTION_DEPOSIT
        ):
            raise ValueError(
                f"idempotency_key {idempotency_key!r} was already used for a different "
                f"conversion request -- refusing to reuse it."
            )

        if status == STATUS_CLAIMED:
            # debit_foxcoins_idempotent raises (InsufficientFoxCoins/
            # ValueError) BEFORE any mutation, and is itself idempotent on
            # this same key -- so it's always safe to call here, whether
            # this is the first attempt or a resume: fresh, it debits;
            # replayed, it returns the prior result without re-debiting;
            # still short of funds, it raises again, leaving the attempt
            # at CLAIMED so a later retry (after the balance changes) can
            # simply try again.
            self._debit_foxcoins_idempotent(
                user_id, foxcoin_cost, idempotency_key,
                reason="casino_promo_convert", creator_id=creator_id,
            )
            with _connect(timeout=timeout) as connection:
                _ensure_schema(connection)
                _set_status(connection, idempotency_key, STATUS_DEBITED)
            status = STATUS_DEBITED

        if status == STATUS_DEBITED:
            entry = self._credit_promo(creator_id, user_id, promo_amount, idempotency_key, display_name, timeout)
            with _connect(timeout=timeout) as connection:
                _ensure_schema(connection)
                _set_status(connection, idempotency_key, STATUS_COMPLETED)
            return self._as_result(entry)

        # STATUS_COMPLETED: casino_ledger.credit() is itself idempotent on
        # idempotency_key, so re-issuing it here just returns the original
        # entry (replayed=True) -- authoritative and safe to call again.
        entry = self._credit_promo(creator_id, user_id, promo_amount, idempotency_key, display_name, timeout)
        return self._as_result(entry)

    def _credit_promo(self, creator_id, user_id, promo_amount, idempotency_key, display_name, timeout):
        return cl.credit(
            creator_id, user_id, CURRENCY_PROMO, promo_amount, cl.PROMO_CONVERT_IN,
            idempotency_key=idempotency_key, display_name=display_name,
            metadata={"source": "foxcoin_conversion"}, timeout=timeout,
        )

    @staticmethod
    def _as_result(entry) -> dict:
        return {
            "promo_amount": entry.amount,
            "promo_balance": entry.balance_after,
            "transaction_id": entry.transaction_id,
            "replayed": entry.replayed,
        }

    def withdraw(
        self,
        creator_id: str,
        user_id: str,
        promo_amount: int,
        *,
        idempotency_key: str,
        display_name: str | None = None,
        timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS,
        **kwargs,
    ) -> dict:
        """Cash out `promo_amount` PROMO credits back into FoxCoins -- the
        reverse of deposit() (Phase 8; one-way-by-design ended once the
        casino ran live and proven for weeks -- see module docstring).
        `user_id` must already be the canonical viewer key, same contract
        as deposit().

        FoxCoin credit is computed from the SAME casino_config rate as
        deposit(), as `promo_amount * foxcoins_per_promo` -- exact
        multiplication both directions, no rounding, no divisibility
        policy needed: input is denominated in PROMO (the currency
        actually being debited, the one with a real integer balance),
        never in FoxCoins, so there is nothing to floor or reject.

        Ordering mirrors deposit()'s debit-before-credit discipline, legs
        reversed: the PROMO ledger debit (atomic, provably-exactly-once)
        happens FIRST and is durably recorded as done BEFORE the FoxCoin
        credit (the non-atomic side) is even attempted -- a crash between
        the two can only ever resume into crediting FoxCoins, never
        re-debiting PROMO.

        daily_promo_limit is shared with deposit() -- see
        _today_promo_volume() and DailyLimitExceeded.

        Safe to call twice with the same idempotency_key: whichever stage
        a prior attempt reached, this resumes from there and returns the
        same result rather than moving value again.
        """
        if not idempotency_key:
            raise ValueError("idempotency_key is required.")
        if not isinstance(promo_amount, int) or isinstance(promo_amount, bool) or promo_amount <= 0:
            raise ValueError("promo_amount must be a positive integer.")

        creator_id = str(creator_id or "").strip()
        user_id = str(user_id or "").strip()
        if not creator_id or not user_id:
            raise ValueError("creator_id and user_id are required.")

        config = self._get_config(creator_id, timeout=timeout)
        foxcoin_amount = promo_amount * config.foxcoins_per_promo

        # Same shared, loosely-enforced (pre-transaction) daily cap as
        # deposit() -- see that method's comment for the race-window note,
        # which applies identically here.
        already_converted = _today_promo_volume(creator_id, user_id, timeout=timeout)
        if already_converted + promo_amount > config.daily_promo_limit:
            raise DailyLimitExceeded(
                f"{user_id}: already converted {already_converted} PROMO today (either direction), "
                f"limit {config.daily_promo_limit}, requested {promo_amount} more."
            )

        with _connect(timeout=timeout) as connection:
            _ensure_schema(connection)
            attempt = _claim(
                connection, idempotency_key, creator_id, user_id, foxcoin_amount, promo_amount,
                direction=DIRECTION_CASHOUT,
            )

        (_, a_creator, a_user, a_foxcoin, a_promo, status, a_direction) = attempt
        if (
            a_creator != creator_id or a_user != user_id or a_foxcoin != foxcoin_amount
            or a_promo != promo_amount or a_direction != DIRECTION_CASHOUT
        ):
            raise ValueError(
                f"idempotency_key {idempotency_key!r} was already used for a different "
                f"cashout request -- refusing to reuse it."
            )

        # The PROMO debit is attempted/re-confirmed on every call regardless
        # of resume point: fresh (CLAIMED), it debits for real and raises
        # InsufficientFunds before any mutation if the balance is too low,
        # leaving the attempt safely resumable at CLAIMED; resumed
        # (DEBITED/COMPLETED), casino_ledger's own idempotency-key lookup
        # short-circuits it to the already-committed row (replayed=True) --
        # never a second debit. This is the entry the result is built from
        # (the ledger side is the one with a real transaction_id -- the
        # FoxCoin blob side has none), mirroring deposit()'s use of the
        # PROMO credit entry for the same reason, reversed.
        debit_entry = self._debit_promo(creator_id, user_id, promo_amount, idempotency_key, display_name, timeout)

        if status == STATUS_CLAIMED:
            with _connect(timeout=timeout) as connection:
                _ensure_schema(connection)
                _set_status(connection, idempotency_key, STATUS_DEBITED)
            status = STATUS_DEBITED

        if status == STATUS_DEBITED:
            foxcoin_result = self._credit_foxcoins(user_id, foxcoin_amount, idempotency_key, creator_id)
            with _connect(timeout=timeout) as connection:
                _ensure_schema(connection)
                _set_status(connection, idempotency_key, STATUS_COMPLETED)
            return self._as_withdraw_result(promo_amount, foxcoin_amount, debit_entry, foxcoin_result)

        # STATUS_COMPLETED: credit_foxcoins_idempotent is itself idempotent
        # on idempotency_key, so re-issuing it here just returns the
        # original result instead of crediting again.
        foxcoin_result = self._credit_foxcoins(user_id, foxcoin_amount, idempotency_key, creator_id)
        return self._as_withdraw_result(promo_amount, foxcoin_amount, debit_entry, foxcoin_result)

    def _debit_promo(self, creator_id, user_id, promo_amount, idempotency_key, display_name, timeout):
        return cl.debit(
            creator_id, user_id, CURRENCY_PROMO, promo_amount, cl.PROMO_CONVERT_OUT,
            idempotency_key=idempotency_key, display_name=display_name,
            metadata={"source": "foxcoin_cashout"}, timeout=timeout,
        )

    def _credit_foxcoins(self, user_id, foxcoin_amount, idempotency_key, creator_id):
        return self._credit_foxcoins_idempotent(
            user_id, foxcoin_amount, idempotency_key,
            reason="casino_promo_cashout", creator_id=creator_id,
        )

    @staticmethod
    def _as_withdraw_result(promo_amount, foxcoin_amount, debit_entry, foxcoin_result) -> dict:
        return {
            "promo_amount": promo_amount,
            "foxcoin_amount": foxcoin_amount,
            "foxcoin_balance": foxcoin_result["balance"],
            "promo_balance": debit_entry.balance_after,
            "transaction_id": debit_entry.transaction_id,
            "replayed": debit_entry.replayed,
        }
