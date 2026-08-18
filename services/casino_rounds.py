"""The reusable round model every casino game settles wagers through
(Casino Phase 4). Coinflip (games/coinflip.py) is the first game; every
later game (slots, blackjack, roulette) reuses play_round() below by
supplying its own `resolve` callback -- none of them touch this module's
persistence or ordering logic.

Owns the casino_rounds table on top of the already-proven casino_ledger
(Phase 2: atomicity/idempotency verified against real Postgres) and
casino_config (Phase 3/4: per-creator, per-game gating).

CRASH SAFETY. Unlike Phase 3's FoxCoin<->ledger bridge, every step here
lives in the same Postgres database as casino_ledger, so there is no
non-atomic legacy store to bridge against. But the lifecycle still has one
ordering rule that must never be violated: the RNG outcome is decided and
durably persisted to casino_rounds exactly ONCE, before any payout is
attempted, and a resumed round always reuses the persisted outcome rather
than rolling again. Re-rolling on retry would let a player crash their
connection right after seeing a loss and hope a retry rolls a win --
persisting the outcome before ever acting on it closes that off entirely,
the RNG equivalent of Phase 3's "debit before credit, never the reverse."

A second, subtler race: two concurrent calls for the SAME round_id could
both observe `outcome IS NULL` and both call resolve(), rolling the RNG
twice, with only one UPDATE actually sticking -- the loser would still be
holding a locally-rolled outcome that was never persisted. Closed with
`SELECT ... FOR UPDATE` on the round row around the decide step, the same
row-lock idiom services/casino_ledger.py already uses to serialize
concurrent balance changes.

STATE MACHINE (persisted states):
    FUNDED    -- wager debited (casino_ledger, PROMO_WAGER), round row
                 exists. `outcome` is NULL until the RNG has been rolled
                 for this round_id -- ever; non-NULL once decided, whether
                 or not settlement has completed yet.
    SETTLED   -- outcome decided, payout (possibly zero) resolved: a win
                 has an idempotent PROMO_PAYOUT credit, a loss has none.
                 Terminal, happy path.
    REFUNDED  -- funded, but something prevented normal settlement (e.g.
                 an internal error after the outcome was decided but
                 before it could be durably persisted); wager returned via
                 PROMO_REFUND. Terminal. Not reachable by play_round()
                 itself today -- reserved for manual/administrative use.
    FAILED    -- funded, hit an error even a refund couldn't resolve
                 automatically; needs manual reconciliation. Terminal,
                 not expected to be reached by well-behaved game code.

There is no persisted CREATED state: casino_ledger.debit() raises
InsufficientFunds (or GameDisabled/BetOutOfRange from the config check
below) before writing anything, ledger or round, so a rejected wager
leaves zero trace in casino_rounds -- "no round created" is literal.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable

from services import casino_config
from services import casino_ledger as cl


TABLE_ROUNDS = "casino_rounds"
CURRENCY_PROMO = "PROMO"

_schema_lock = threading.Lock()
_schema_ready = False

DEFAULT_CONNECT_TIMEOUT_SECONDS = 10

STATE_FUNDED = "FUNDED"
STATE_SETTLED = "SETTLED"
STATE_REFUNDED = "REFUNDED"
STATE_FAILED = "FAILED"

_TERMINAL_STATES = (STATE_SETTLED, STATE_REFUNDED, STATE_FAILED)

_ROUND_COLUMNS = (
    "round_id, creator_id, user_id, game_id, currency, wager, payout, "
    "outcome, state, metadata"
)

# Test-only hook: when set, _decide_outcome() invokes it right after the
# row lock is acquired and before resolve() is called, letting tests hold
# the lock open across a second connection to prove FOR UPDATE actually
# serializes concurrent decides. None in every real code path.
_TEST_HOOK_AFTER_ROW_LOCK = None


class GameDisabled(Exception):
    """The creator has this game_id disabled in casino_game_config."""


class BetOutOfRange(Exception):
    """wager is outside the creator's configured min/max for this game."""


class RoundMismatch(Exception):
    """round_id was already used for a different wager/game/user -- refusing
    to reuse it."""


@dataclass(frozen=True)
class RoundResult:
    round_id: str
    state: str
    outcome: str | None
    wager: int
    payout: int
    balance_after: int
    metadata: dict = field(default_factory=dict)
    replayed: bool = False


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
            CREATE TABLE IF NOT EXISTS {TABLE_ROUNDS} (
                round_id    TEXT PRIMARY KEY,
                creator_id  TEXT NOT NULL,
                user_id     TEXT NOT NULL,
                game_id     TEXT NOT NULL,
                currency    TEXT NOT NULL,
                wager       BIGINT NOT NULL,
                payout      BIGINT,
                outcome     TEXT,
                state       TEXT NOT NULL,
                metadata    JSONB,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        connection.execute(
            f"""
            CREATE INDEX IF NOT EXISTS idx_{TABLE_ROUNDS}_creator_user_time
                ON {TABLE_ROUNDS} (creator_id, user_id, created_at DESC)
            """
        )
        connection.execute(
            f"""
            CREATE INDEX IF NOT EXISTS idx_{TABLE_ROUNDS}_creator_game_time
                ON {TABLE_ROUNDS} (creator_id, game_id, created_at DESC)
            """
        )
        # casino_ledger's own tables must exist before this module's calls
        # into cl.debit()/cl.credit() below -- cheap and idempotent (IF NOT
        # EXISTS) to establish here too.
        cl._ensure_schema(connection)
        _schema_ready = True


def _row_to_dict(row) -> dict:
    (round_id, creator_id, user_id, game_id, currency, wager, payout, outcome, state, metadata) = row
    return {
        "round_id": round_id,
        "creator_id": creator_id,
        "user_id": user_id,
        "game_id": game_id,
        "currency": currency,
        "wager": int(wager),
        "payout": int(payout) if payout is not None else None,
        "outcome": outcome,
        "state": state,
        "metadata": metadata or {},
    }


def _claim_round(connection, round_id, creator_id, user_id, game_id, wager):
    row = connection.execute(
        f"""
        INSERT INTO {TABLE_ROUNDS} (round_id, creator_id, user_id, game_id, currency, wager, state)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (round_id) DO NOTHING
        RETURNING {_ROUND_COLUMNS}
        """,
        (round_id, creator_id, user_id, game_id, CURRENCY_PROMO, wager, STATE_FUNDED),
    ).fetchone()
    if row is not None:
        return _row_to_dict(row)

    row = connection.execute(
        f"SELECT {_ROUND_COLUMNS} FROM {TABLE_ROUNDS} WHERE round_id = %s",
        (round_id,),
    ).fetchone()
    return _row_to_dict(row)


def _decide_outcome(round_id, resolve, timeout):
    """Locks the round row, decides the outcome exactly once (ever), and
    returns the round's outcome/payout/metadata -- freshly rolled if this
    call won the race, or the winner's persisted values if it lost the
    race to a concurrent call for the same round_id."""
    from psycopg.types.json import Jsonb

    with _connect(timeout=timeout) as connection:
        _ensure_schema(connection)

        row = connection.execute(
            f"SELECT outcome, payout, metadata FROM {TABLE_ROUNDS} WHERE round_id = %s FOR UPDATE",
            (round_id,),
        ).fetchone()
        outcome, payout, metadata = row[0], row[1], (row[2] or {})

        if _TEST_HOOK_AFTER_ROW_LOCK is not None:
            _TEST_HOOK_AFTER_ROW_LOCK()

        if outcome is None:
            outcome, payout, metadata = resolve()
            if not isinstance(payout, int) or isinstance(payout, bool) or payout < 0:
                raise ValueError("resolve() must return a non-negative int payout.")
            connection.execute(
                f"""
                UPDATE {TABLE_ROUNDS}
                SET outcome = %s, payout = %s, metadata = %s, updated_at = NOW()
                WHERE round_id = %s
                """,
                (outcome, payout, Jsonb(metadata or {}), round_id),
            )
        # Clean exit commits (fresh decide) or simply releases the lock
        # (nothing to write, we're reusing an already-decided outcome).

    return outcome, int(payout), (metadata or {})


def play_round(
    creator_id: str,
    user_id: str,
    game_id: str,
    wager: int,
    round_id: str,
    *,
    resolve: Callable[[], tuple],
    display_name: str | None = None,
    timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS,
) -> RoundResult:
    """Runs one round of any game through the shared wager -> RNG -> settle
    lifecycle. `resolve` is called AT MOST ONCE PER round_id, ever -- it
    must return (outcome_label, payout, metadata), computed by rolling
    services/casino_rng.py itself; games never accept an outcome from a
    caller. Safe to call repeatedly with the same round_id (network retry,
    resumed after a crash) -- always converges to the same settled result
    without re-debiting, re-crediting, or re-rolling.
    """
    creator_id = str(creator_id or "").strip()
    user_id = str(user_id or "").strip()
    game_id = str(game_id or "").strip()
    if not creator_id or not user_id or not game_id:
        raise ValueError("creator_id, user_id, and game_id are all required.")
    if not round_id:
        raise ValueError("round_id is required.")
    if not isinstance(wager, int) or isinstance(wager, bool) or wager <= 0:
        raise ValueError("wager must be a positive integer.")

    game_conf = casino_config.get_game_config(creator_id, game_id, timeout=timeout)
    if not game_conf.enabled:
        raise GameDisabled(f"{game_id!r} is disabled for creator {creator_id!r}.")
    if wager < game_conf.min_bet or wager > game_conf.max_bet:
        raise BetOutOfRange(
            f"wager {wager} outside [{game_conf.min_bet}, {game_conf.max_bet}] for {game_id!r}."
        )

    # Wager: casino_ledger.debit() is already atomic + idempotent -- raises
    # InsufficientFunds with nothing written (ledger or round) if the
    # balance is too low. That's what makes "no round created" literal.
    cl.debit(
        creator_id, user_id, CURRENCY_PROMO, wager, cl.PROMO_WAGER,
        round_id=round_id, idempotency_key=f"{round_id}-wager",
        display_name=display_name, timeout=timeout,
    )

    with _connect(timeout=timeout) as connection:
        _ensure_schema(connection)
        round_ = _claim_round(connection, round_id, creator_id, user_id, game_id, wager)

    if (round_["creator_id"] != creator_id or round_["user_id"] != user_id
            or round_["game_id"] != game_id or round_["wager"] != wager):
        raise RoundMismatch(
            f"round_id {round_id!r} was already used for a different wager -- refusing to reuse it."
        )

    if round_["state"] in _TERMINAL_STATES:
        return RoundResult(
            round_id=round_id, state=round_["state"], outcome=round_["outcome"],
            wager=round_["wager"], payout=round_["payout"] or 0,
            balance_after=cl.get_balance(creator_id, user_id, CURRENCY_PROMO),
            metadata=round_["metadata"], replayed=True,
        )

    # round_["state"] == FUNDED. Decide exactly once, ever; reuse if resuming.
    outcome, payout, metadata = _decide_outcome(round_id, resolve, timeout)

    # Settle: a win credits (idempotent, safe to retry); a loss credits
    # nothing (amount=0 is not a valid ledger entry, and none is needed).
    if payout > 0:
        cl.credit(
            creator_id, user_id, CURRENCY_PROMO, payout, cl.PROMO_PAYOUT,
            round_id=round_id, idempotency_key=f"{round_id}-payout",
            display_name=display_name, timeout=timeout,
        )

    with _connect(timeout=timeout) as connection:
        _ensure_schema(connection)
        connection.execute(
            f"UPDATE {TABLE_ROUNDS} SET state = %s, updated_at = NOW() WHERE round_id = %s",
            (STATE_SETTLED, round_id),
        )

    return RoundResult(
        round_id=round_id, state=STATE_SETTLED, outcome=outcome, wager=wager,
        payout=payout, balance_after=cl.get_balance(creator_id, user_id, CURRENCY_PROMO),
        metadata=metadata, replayed=False,
    )
