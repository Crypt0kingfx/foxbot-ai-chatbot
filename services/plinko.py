"""FoxBot Plinko v1: a free-play FoxCoin mini-game with an OBS overlay.

FREE-PLAY FOXCOINS ONLY. There is no real-money, tipping, vote-cashout or
casino-PROMO path anywhere in this module. FOXBOT_PLINKO_PAID_ENTRY gates a
clearly marked stub (start_paid_session) that does nothing and always
raises -- see the bottom of this file.

GAME. 8 rows of pegs, 9 slots. Every drop is decided here, server-side:
casino_rng.bits(8) (OS CSPRNG via the `secrets` module) gives one bit per
row, 1 = bounce right. The slot is the number of right bounces, so slot k
has probability C(8,k)/256 -- exactly the binomial 1,8,28,56,70,56,28,8,1
out of 256 -- and the 8 bits ARE the path, so the overlay replays the exact
bounce sequence instead of inventing one. The overlay never decides
anything.

    slot:        0     1     2     3     4     5     6     7     8
    multiplier:  10x   3x    1.4x  0.6x  0.4x  0.6x  1.4x  3x    10x
    odds /256:   1     8     28    56    70    56    28    8     1

    RTP = sum(p * m) = 241.6/256 = 94.375%  (rtp() computes it exactly)

SESSIONS. A play (!plinko) costs session_cost FoxCoins and grants
drops_per_session drops; each drop's stake is session_cost /
drops_per_session (config enforces that it divides evenly, so no payout is
ever rounded). One active session per viewer, a cooldown between sessions,
and a max payout per session (drops past the cap pay only up to it -- the
cap can only lower RTP, never raise it). An idle session older than
session_ttl_seconds is finished automatically by finish_expired(): its
remaining drops are played server-side, so a viewer who leaves never loses
drops they paid for.

COINS AND TRANSACTIONS. Every Plinko state change -- claiming a session,
recording a drop together with its payout accrual and audit row, closing a
session -- is exactly one Postgres transaction, row-locked on the session
(SELECT ... FOR UPDATE), so a double-send or a race can never record a drop
twice or exceed the drop count or the cap.

FoxCoin BALANCES themselves are not in Postgres: they live in app.py's
JSON-blob economy, which cannot join a Postgres transaction. So the coin
legs follow the same proven ordering as providers/promo.py's FoxCoin <->
PROMO conversion: an idempotent FoxCoin debit (keyed by session id) happens
after the session is claimed and before it can be played; an idempotent
FoxCoin credit (keyed by drop id) happens after the drop commits. Every
gap a crash could leave is visible in these tables (a 'claimed' session, a
drop with credited_at NULL) and is closed by app.py's reconciler re-issuing
the same idempotent call, which can never double-apply.

Keyed by creator_HANDLE like services/prize_wheels.py (the polling loop,
the anonymous /overlay/plinko OBS source and the Studio session all have a
handle in scope). Fail-closed: Postgres only, no local-file fallback, and
OFF by default per creator.
"""

from __future__ import annotations

import math
import os
import threading
import uuid
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

from services import casino_rng


GAME_ID = "plinko"

TABLE_CONFIG = "plinko_config"
TABLE_SESSIONS = "plinko_sessions"
TABLE_DROPS = "plinko_drops"
TABLE_AUDIT = "plinko_audit"

# ---------------------------------------------------------------- game table
ROWS = 8
SLOTS = ROWS + 1
# C(8, k) for k = 0..8 -> 1, 8, 28, 56, 70, 56, 28, 8, 1 (sums to 256).
BINOMIAL_WEIGHTS = tuple(math.comb(ROWS, k) for k in range(SLOTS))
TOTAL_WEIGHT = 2 ** ROWS
# Total-return multipliers as integer percent (1000 = 10x). Integer percent
# keeps every payout exact integer math -- no float ever touches a balance.
MULTIPLIERS_PCT = (1000, 300, 140, 60, 40, 60, 140, 300, 1000)

# ---------------------------------------------------------------- per-creator defaults
DEFAULT_ENABLED = False
DEFAULT_SESSION_COST = 100          # FoxCoins per play
DEFAULT_DROPS_PER_SESSION = 10
DEFAULT_COOLDOWN_SECONDS = 120      # between one session ending and the next starting
DEFAULT_MAX_SESSION_PAYOUT = 500    # FoxCoins; 5x the default cost
DEFAULT_SESSION_TTL_SECONDS = 600   # idle time before remaining drops auto-play

MIN_SESSION_COST, MAX_SESSION_COST = 10, 1_000_000
MIN_DROPS, MAX_DROPS = 1, 50
MAX_COOLDOWN_SECONDS = 86_400
MIN_TTL_SECONDS, MAX_TTL_SECONDS = 60, 86_400
MAX_SESSION_PAYOUT_LIMIT = 1_000_000_000

STATUS_CLAIMED = "claimed"      # session row exists, FoxCoin debit not yet confirmed
STATUS_ACTIVE = "active"        # paid for; drops may be played
STATUS_CLOSED = "closed"        # every drop played
STATUS_CANCELLED = "cancelled"  # debit never happened (insufficient FoxCoins)
OPEN_STATUSES = (STATUS_CLAIMED, STATUS_ACTIVE)

DEFAULT_CONNECT_TIMEOUT_SECONDS = 10

_schema_lock = threading.Lock()
_schema_ready = False


class PlinkoUnavailable(Exception):
    """DATABASE_URL isn't configured -- no local-file fallback by design."""


class PlinkoDisabled(Exception):
    """This creator hasn't turned Plinko on (no new sessions)."""


class PlinkoKilled(Exception):
    """Kill switch is on: no sessions, no drops, no auto-play."""


class SessionActive(Exception):
    def __init__(self, session: dict):
        super().__init__("viewer already has an open session.")
        self.session = session


class CooldownActive(Exception):
    def __init__(self, seconds_left: int):
        super().__init__(f"cooldown: {seconds_left}s left.")
        self.seconds_left = seconds_left


class NoActiveSession(Exception):
    """No paid-for session to drop in."""


class PaidEntryUnavailable(Exception):
    """There is no paid entry. See start_paid_session()."""


# ---------------------------------------------------------------- math (pure)

def rtp(multipliers_pct=MULTIPLIERS_PCT, weights=BINOMIAL_WEIGHTS) -> Fraction:
    """Exact return-to-player as a Fraction: sum(weight * multiplier) /
    (sum(weights) * 100). No floats."""
    if len(multipliers_pct) != len(weights):
        raise ValueError("multipliers and weights must be the same length.")
    total = sum(weights)
    return Fraction(sum(w * int(m) for w, m in zip(weights, multipliers_pct)), total * 100)


def validate_multipliers(multipliers_pct) -> tuple[int, ...]:
    values = tuple(int(m) for m in multipliers_pct)
    if len(values) != SLOTS:
        raise ValueError(f"need exactly {SLOTS} multipliers.")
    if any(m < 0 for m in values):
        raise ValueError("multipliers can't be negative.")
    if rtp(values) > 1:
        raise ValueError("multipliers would pay out more than 100% RTP.")
    return values


validate_multipliers(MULTIPLIERS_PCT)  # the shipped table must always pass


def slot_for_bits(bits: int) -> int:
    """Slot = number of right bounces (set bits) across the 8 rows."""
    return bin(int(bits) & (TOTAL_WEIGHT - 1)).count("1")


def path_for_bits(bits: int) -> list[int]:
    """Per-row bounce, row 0 first: 1 = right, 0 = left."""
    return [(int(bits) >> row) & 1 for row in range(ROWS)]


def draw() -> tuple[int, int]:
    """One server-side drop: (bits, slot). The ONLY place an outcome is
    decided. casino_rng.bits() is secrets.randbits() in production."""
    bits = casino_rng.bits(ROWS)
    return bits, slot_for_bits(bits)


def drop_payout(stake: int, multiplier_pct: int) -> int:
    return (int(stake) * int(multiplier_pct)) // 100


def format_multiplier(pct: int) -> str:
    pct = int(pct)
    return f"{pct // 100}x" if pct % 100 == 0 else f"{pct / 100:g}x"


def rules_text(config: "PlinkoConfig", currency: str = "FoxCoins") -> str:
    """The chat odds table (!plinkorules). Kept under Blaze's ~480-char
    message limit."""
    slots = " | ".join(format_multiplier(m) for m in MULTIPLIERS_PCT)
    odds = " | ".join(str(w) for w in BINOMIAL_WEIGHTS)
    pct = float(rtp()) * 100
    return (
        f"🦊 Plinko: {config.session_cost} {currency} = {config.drops_per_session} drops "
        f"({config.stake} per drop). Slots: {slots}. Odds per slot (out of {TOTAL_WEIGHT}): {odds}. "
        f"RTP {pct:.2f}%. Max win {config.max_session_payout} per session. "
        f"Free-play {currency} only. !plinko to play, !drop to drop, !plinkobal for balance."
    )


# ---------------------------------------------------------------- config

@dataclass(frozen=True)
class PlinkoConfig:
    creator_handle: str
    enabled: bool
    kill_switch: bool
    session_cost: int
    drops_per_session: int
    cooldown_seconds: int
    max_session_payout: int
    session_ttl_seconds: int

    @property
    def stake(self) -> int:
        return self.session_cost // self.drops_per_session


def database_url() -> str:
    return str(os.getenv("DATABASE_URL") or "").strip()


def is_available() -> bool:
    return bool(database_url())


def _require_available() -> None:
    if not is_available():
        raise PlinkoUnavailable("Plinko requires DATABASE_URL (Postgres) -- there is no local-file fallback by design.")


def _connect(timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS):
    import psycopg

    return psycopg.connect(database_url(), connect_timeout=timeout)


def _clean_handle(creator_handle: str) -> str:
    from services import creator_access

    handle = creator_access.clean_handle(creator_handle)
    if not handle:
        raise ValueError("creator_handle is required.")
    return handle


def _ensure_schema(connection) -> None:
    """Same race-tolerant, commit-before-flag pattern as
    services/prize_wheels.py's _ensure_schema."""
    global _schema_ready
    if _schema_ready:
        return

    with _schema_lock:
        if _schema_ready:
            return

        import psycopg

        statements = (
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE_CONFIG} (
                creator_handle      TEXT PRIMARY KEY,
                enabled             BOOLEAN NOT NULL DEFAULT {str(DEFAULT_ENABLED).upper()},
                kill_switch         BOOLEAN NOT NULL DEFAULT FALSE,
                session_cost        BIGINT NOT NULL DEFAULT {DEFAULT_SESSION_COST},
                drops_per_session   INT NOT NULL DEFAULT {DEFAULT_DROPS_PER_SESSION},
                cooldown_seconds    INT NOT NULL DEFAULT {DEFAULT_COOLDOWN_SECONDS},
                max_session_payout  BIGINT NOT NULL DEFAULT {DEFAULT_MAX_SESSION_PAYOUT},
                session_ttl_seconds INT NOT NULL DEFAULT {DEFAULT_SESSION_TTL_SECONDS},
                updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE_SESSIONS} (
                session_id       TEXT PRIMARY KEY,
                creator_handle   TEXT NOT NULL,
                creator_id       TEXT,
                user_key         TEXT NOT NULL,
                display_name     TEXT NOT NULL,
                request_key      TEXT NOT NULL,
                cost             BIGINT NOT NULL,
                stake            BIGINT NOT NULL,
                drops_total      INT NOT NULL,
                drops_used       INT NOT NULL DEFAULT 0,
                payout_total     BIGINT NOT NULL DEFAULT 0,
                max_payout       BIGINT NOT NULL,
                multipliers      JSONB NOT NULL,
                status           TEXT NOT NULL,
                close_reason     TEXT,
                created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                activated_at     TIMESTAMPTZ,
                last_activity_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                closed_at        TIMESTAMPTZ,
                UNIQUE (creator_handle, request_key),
                CHECK (drops_used >= 0 AND drops_used <= drops_total),
                CHECK (payout_total >= 0 AND payout_total <= max_payout)
            )
            """,
            # One open (claimed or active) session per viewer per channel --
            # enforced by Postgres, not just by the check in claim_session().
            f"""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_{TABLE_SESSIONS}_one_open
                ON {TABLE_SESSIONS} (creator_handle, user_key)
                WHERE status IN ('{STATUS_CLAIMED}', '{STATUS_ACTIVE}')
            """,
            f"""
            CREATE INDEX IF NOT EXISTS idx_{TABLE_SESSIONS}_handle_created
                ON {TABLE_SESSIONS} (creator_handle, created_at DESC)
            """,
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE_DROPS} (
                id             BIGSERIAL PRIMARY KEY,
                drop_key       TEXT NOT NULL,
                session_id     TEXT NOT NULL REFERENCES {TABLE_SESSIONS} (session_id),
                creator_handle TEXT NOT NULL,
                creator_id     TEXT,
                user_key       TEXT NOT NULL,
                display_name   TEXT NOT NULL,
                drop_index     INT NOT NULL,
                bits           INT NOT NULL,
                slot           INT NOT NULL,
                multiplier_pct INT NOT NULL,
                raw_payout     BIGINT NOT NULL,
                payout         BIGINT NOT NULL,
                capped         BOOLEAN NOT NULL DEFAULT FALSE,
                auto           BOOLEAN NOT NULL DEFAULT FALSE,
                credited_at    TIMESTAMPTZ,
                created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE (creator_handle, drop_key),
                UNIQUE (session_id, drop_index),
                CHECK (slot BETWEEN 0 AND {SLOTS - 1}),
                CHECK (payout >= 0 AND payout <= raw_payout)
            )
            """,
            f"""
            CREATE INDEX IF NOT EXISTS idx_{TABLE_DROPS}_handle_id
                ON {TABLE_DROPS} (creator_handle, id)
            """,
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE_AUDIT} (
                id             BIGSERIAL PRIMARY KEY,
                creator_handle TEXT NOT NULL,
                session_id     TEXT,
                event          TEXT NOT NULL,
                detail         JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            f"""
            CREATE INDEX IF NOT EXISTS idx_{TABLE_AUDIT}_session
                ON {TABLE_AUDIT} (session_id, id)
            """,
        )
        for statement in statements:
            try:
                connection.execute(statement)
            except (psycopg.errors.DuplicateTable, psycopg.errors.UniqueViolation, psycopg.errors.DuplicateObject):
                connection.rollback()

        connection.commit()
        _schema_ready = True


_CONFIG_COLUMNS = (
    "enabled, kill_switch, session_cost, drops_per_session, cooldown_seconds, "
    "max_session_payout, session_ttl_seconds"
)


def _row_to_config(handle: str, row) -> PlinkoConfig:
    if row is None:
        return PlinkoConfig(
            creator_handle=handle, enabled=DEFAULT_ENABLED, kill_switch=False,
            session_cost=DEFAULT_SESSION_COST, drops_per_session=DEFAULT_DROPS_PER_SESSION,
            cooldown_seconds=DEFAULT_COOLDOWN_SECONDS, max_session_payout=DEFAULT_MAX_SESSION_PAYOUT,
            session_ttl_seconds=DEFAULT_SESSION_TTL_SECONDS,
        )
    return PlinkoConfig(
        creator_handle=handle, enabled=bool(row[0]), kill_switch=bool(row[1]),
        session_cost=int(row[2]), drops_per_session=int(row[3]), cooldown_seconds=int(row[4]),
        max_session_payout=int(row[5]), session_ttl_seconds=int(row[6]),
    )


def _read_config(connection, handle: str) -> PlinkoConfig:
    row = connection.execute(
        f"SELECT {_CONFIG_COLUMNS} FROM {TABLE_CONFIG} WHERE creator_handle = %s", (handle,)
    ).fetchone()
    return _row_to_config(handle, row)


def get_config(creator_handle: str, timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS) -> PlinkoConfig:
    _require_available()
    handle = _clean_handle(creator_handle)
    with _connect(timeout=timeout) as connection:
        _ensure_schema(connection)
        return _read_config(connection, handle)


def validate_config(config: PlinkoConfig) -> None:
    if not (MIN_DROPS <= config.drops_per_session <= MAX_DROPS):
        raise ValueError(f"drops per session must be {MIN_DROPS}-{MAX_DROPS}.")
    if not (MIN_SESSION_COST <= config.session_cost <= MAX_SESSION_COST):
        raise ValueError(f"session cost must be {MIN_SESSION_COST}-{MAX_SESSION_COST:,} FoxCoins.")
    if config.session_cost % config.drops_per_session:
        raise ValueError("session cost must divide evenly by the number of drops (so no payout is ever rounded).")
    if not (0 <= config.cooldown_seconds <= MAX_COOLDOWN_SECONDS):
        raise ValueError(f"cooldown must be 0-{MAX_COOLDOWN_SECONDS} seconds.")
    if not (MIN_TTL_SECONDS <= config.session_ttl_seconds <= MAX_TTL_SECONDS):
        raise ValueError(f"session timeout must be {MIN_TTL_SECONDS}-{MAX_TTL_SECONDS} seconds.")
    if not (1 <= config.max_session_payout <= MAX_SESSION_PAYOUT_LIMIT):
        raise ValueError("max session payout must be at least 1.")


def set_config(
    creator_handle: str,
    *,
    actor: str = "studio",
    timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    **changes: Any,
) -> PlinkoConfig:
    """Partial update. Every change is audited (session_id NULL) in the same
    transaction as the write."""
    _require_available()
    handle = _clean_handle(creator_handle)
    allowed = {
        "enabled": bool, "kill_switch": bool, "session_cost": int, "drops_per_session": int,
        "cooldown_seconds": int, "max_session_payout": int, "session_ttl_seconds": int,
    }
    unknown = set(changes) - set(allowed)
    if unknown:
        raise ValueError(f"unknown setting(s): {', '.join(sorted(unknown))}.")

    from psycopg.types.json import Jsonb

    with _connect(timeout=timeout) as connection:
        _ensure_schema(connection)
        current = connection.execute(
            f"SELECT {_CONFIG_COLUMNS} FROM {TABLE_CONFIG} WHERE creator_handle = %s FOR UPDATE", (handle,)
        ).fetchone()
        base = _row_to_config(handle, current)
        values = {name: getattr(base, name) for name in allowed}
        for name, value in changes.items():
            try:
                values[name] = allowed[name](value)
            except (TypeError, ValueError):
                raise ValueError(f"{name} must be a {allowed[name].__name__}.")
        updated = PlinkoConfig(creator_handle=handle, **values)
        validate_config(updated)

        connection.execute(
            f"""
            INSERT INTO {TABLE_CONFIG} (creator_handle, {_CONFIG_COLUMNS}, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (creator_handle) DO UPDATE SET
                enabled = EXCLUDED.enabled, kill_switch = EXCLUDED.kill_switch,
                session_cost = EXCLUDED.session_cost, drops_per_session = EXCLUDED.drops_per_session,
                cooldown_seconds = EXCLUDED.cooldown_seconds, max_session_payout = EXCLUDED.max_session_payout,
                session_ttl_seconds = EXCLUDED.session_ttl_seconds, updated_at = NOW()
            """,
            (handle, updated.enabled, updated.kill_switch, updated.session_cost, updated.drops_per_session,
             updated.cooldown_seconds, updated.max_session_payout, updated.session_ttl_seconds),
        )
        diff = {k: {"from": getattr(base, k), "to": getattr(updated, k)}
                for k in allowed if getattr(base, k) != getattr(updated, k)}
        if diff:
            _audit(connection, handle, None, "config_changed", {"actor": str(actor)[:64], "changes": diff}, Jsonb)
    return updated


# ---------------------------------------------------------------- rows

_SESSION_COLUMNS = (
    "session_id, creator_handle, creator_id, user_key, display_name, request_key, cost, stake, "
    "drops_total, drops_used, payout_total, max_payout, multipliers, status, close_reason, "
    "created_at, activated_at, last_activity_at, closed_at"
)
_DROP_COLUMNS = (
    "id, drop_key, session_id, creator_handle, creator_id, user_key, display_name, drop_index, "
    "bits, slot, multiplier_pct, raw_payout, payout, capped, auto, credited_at, created_at"
)


def _iso(value):
    return value.isoformat() if value is not None else None


def _row_to_session(row) -> dict | None:
    if row is None:
        return None
    keys = [k.strip() for k in _SESSION_COLUMNS.split(",")]
    session = dict(zip(keys, row))
    for key in ("cost", "stake", "drops_total", "drops_used", "payout_total", "max_payout"):
        session[key] = int(session[key])
    session["multipliers"] = list(session["multipliers"] or [])
    session["drops_left"] = session["drops_total"] - session["drops_used"]
    for key in ("created_at", "activated_at", "last_activity_at", "closed_at"):
        session[key] = _iso(session[key])
    return session


def _row_to_drop(row) -> dict | None:
    if row is None:
        return None
    keys = [k.strip() for k in _DROP_COLUMNS.split(",")]
    drop = dict(zip(keys, row))
    for key in ("id", "drop_index", "bits", "slot", "multiplier_pct", "raw_payout", "payout"):
        drop[key] = int(drop[key])
    drop["path"] = path_for_bits(drop["bits"])
    drop["multiplier"] = format_multiplier(drop["multiplier_pct"])
    drop["credited_at"] = _iso(drop["credited_at"])
    drop["created_at"] = _iso(drop["created_at"])
    return drop


def _audit(connection, handle, session_id, event, detail, Jsonb) -> None:
    connection.execute(
        f"INSERT INTO {TABLE_AUDIT} (creator_handle, session_id, event, detail) VALUES (%s, %s, %s, %s)",
        (handle, session_id, event, Jsonb(detail or {})),
    )


def _clean_name(display_name: str) -> str:
    return str(display_name or "").strip().lstrip("@")[:64] or "viewer"


def _clean_key(value: str, label: str) -> str:
    key = str(value or "").strip()
    if not key:
        raise ValueError(f"{label} is required.")
    return key[:300]


def _user_lock(connection, handle: str, user_key: str) -> None:
    """Serializes claim_session() per (channel, viewer) so the cooldown
    check and the insert can't interleave between two simultaneous !plinko
    messages. Transaction-scoped: released at commit/rollback."""
    connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"plinko:{handle}:{user_key}",))


# ---------------------------------------------------------------- sessions

def claim_session(
    creator_handle: str,
    *,
    user_key: str,
    display_name: str,
    request_key: str,
    creator_id: str | None = None,
    timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS,
) -> dict:
    """Step 1 of a play: one transaction that checks kill switch / enabled /
    one-open-session / cooldown and inserts a 'claimed' session with the
    current cost, stake, cap and multiplier table snapshotted onto it.
    The caller then debits FoxCoins (idempotent on session_id) and calls
    activate_session(), or cancel_session() if the debit was refused.

    Replaying the same request_key returns the original session with
    replayed=True and never claims a second one."""
    _require_available()
    handle = _clean_handle(creator_handle)
    user = _clean_key(user_key, "user_key").lower()
    name = _clean_name(display_name)
    key = _clean_key(request_key, "request_key")

    from psycopg.types.json import Jsonb

    with _connect(timeout=timeout) as connection:
        _ensure_schema(connection)
        existing = connection.execute(
            f"SELECT {_SESSION_COLUMNS} FROM {TABLE_SESSIONS} WHERE creator_handle = %s AND request_key = %s",
            (handle, key),
        ).fetchone()
        if existing is not None:
            session = _row_to_session(existing)
            session["replayed"] = True
            return session

        config = _read_config(connection, handle)
        if config.kill_switch:
            raise PlinkoKilled("Plinko is paused (kill switch).")
        if not config.enabled:
            raise PlinkoDisabled("Plinko is off in this channel.")

        _user_lock(connection, handle, user)

        open_row = connection.execute(
            f"""SELECT {_SESSION_COLUMNS} FROM {TABLE_SESSIONS}
                 WHERE creator_handle = %s AND user_key = %s AND status = ANY(%s)""",
            (handle, user, list(OPEN_STATUSES)),
        ).fetchone()
        if open_row is not None:
            raise SessionActive(_row_to_session(open_row))

        if config.cooldown_seconds > 0:
            row = connection.execute(
                f"""SELECT CEIL(%s - EXTRACT(EPOCH FROM (NOW() - MAX(COALESCE(closed_at, created_at)))))
                      FROM {TABLE_SESSIONS}
                     WHERE creator_handle = %s AND user_key = %s AND status <> %s""",
                (config.cooldown_seconds, handle, user, STATUS_CANCELLED),
            ).fetchone()
            seconds_left = int(row[0]) if row and row[0] is not None else 0
            if seconds_left > 0:
                raise CooldownActive(seconds_left)

        session_id = uuid.uuid4().hex
        row = connection.execute(
            f"""
            INSERT INTO {TABLE_SESSIONS}
                (session_id, creator_handle, creator_id, user_key, display_name, request_key,
                 cost, stake, drops_total, max_payout, multipliers, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING {_SESSION_COLUMNS}
            """,
            (session_id, handle, creator_id or None, user, name, key, config.session_cost, config.stake,
             config.drops_per_session, config.max_session_payout, Jsonb(list(MULTIPLIERS_PCT)), STATUS_CLAIMED),
        ).fetchone()
        _audit(connection, handle, session_id, "claimed", {
            "viewer": name, "cost": config.session_cost, "drops": config.drops_per_session,
            "stake": config.stake, "max_payout": config.max_session_payout, "request_key": key,
        }, Jsonb)

    session = _row_to_session(row)
    session["replayed"] = False
    return session


def activate_session(creator_handle: str, session_id: str, timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS) -> dict | None:
    """Step 3 of a play, after the FoxCoin debit is confirmed. Idempotent:
    an already-active session is returned unchanged."""
    _require_available()
    handle = _clean_handle(creator_handle)
    from psycopg.types.json import Jsonb

    with _connect(timeout=timeout) as connection:
        _ensure_schema(connection)
        row = connection.execute(
            f"""UPDATE {TABLE_SESSIONS} SET status = %s, activated_at = NOW(), last_activity_at = NOW()
                 WHERE creator_handle = %s AND session_id = %s AND status = %s
             RETURNING {_SESSION_COLUMNS}""",
            (STATUS_ACTIVE, handle, session_id, STATUS_CLAIMED),
        ).fetchone()
        if row is not None:
            _audit(connection, handle, session_id, "debited", {"cost": int(row[6])}, Jsonb)
            return _row_to_session(row)
        row = connection.execute(
            f"SELECT {_SESSION_COLUMNS} FROM {TABLE_SESSIONS} WHERE creator_handle = %s AND session_id = %s",
            (handle, session_id),
        ).fetchone()
    return _row_to_session(row)


def cancel_session(creator_handle: str, session_id: str, reason: str,
                   timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS) -> dict | None:
    """Only a 'claimed' (never debited) session can be cancelled."""
    _require_available()
    handle = _clean_handle(creator_handle)
    from psycopg.types.json import Jsonb

    with _connect(timeout=timeout) as connection:
        _ensure_schema(connection)
        row = connection.execute(
            f"""UPDATE {TABLE_SESSIONS} SET status = %s, close_reason = %s, closed_at = NOW()
                 WHERE creator_handle = %s AND session_id = %s AND status = %s
             RETURNING {_SESSION_COLUMNS}""",
            (STATUS_CANCELLED, str(reason)[:64], handle, session_id, STATUS_CLAIMED),
        ).fetchone()
        if row is not None:
            _audit(connection, handle, session_id, "cancelled", {"reason": str(reason)[:64]}, Jsonb)
    return _row_to_session(row)


def get_open_session(creator_handle: str, user_key: str,
                     timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS) -> dict | None:
    _require_available()
    handle = _clean_handle(creator_handle)
    with _connect(timeout=timeout) as connection:
        _ensure_schema(connection)
        row = connection.execute(
            f"""SELECT {_SESSION_COLUMNS} FROM {TABLE_SESSIONS}
                 WHERE creator_handle = %s AND user_key = %s AND status = ANY(%s)""",
            (handle, str(user_key or "").strip().lower(), list(OPEN_STATUSES)),
        ).fetchone()
    return _row_to_session(row)


# ---------------------------------------------------------------- drops

def _find_drop(connection, handle: str, drop_key: str):
    return connection.execute(
        f"SELECT {_DROP_COLUMNS} FROM {TABLE_DROPS} WHERE creator_handle = %s AND drop_key = %s",
        (handle, drop_key),
    ).fetchone()


def _session_by_id(connection, session_id: str):
    return connection.execute(
        f"SELECT {_SESSION_COLUMNS} FROM {TABLE_SESSIONS} WHERE session_id = %s", (session_id,)
    ).fetchone()


def _record_drop(connection, session: dict, drop_key: str, *, auto: bool, Jsonb) -> tuple[dict, dict]:
    """Inside the caller's transaction, with the session row already locked
    FOR UPDATE: draw, insert the drop, accrue the payout (capped), audit,
    and close the session on its last drop. All or nothing."""
    bits, slot = draw()
    multipliers = session["multipliers"] or list(MULTIPLIERS_PCT)
    multiplier_pct = int(multipliers[slot])
    raw = drop_payout(session["stake"], multiplier_pct)
    remaining_cap = max(0, session["max_payout"] - session["payout_total"])
    payout = min(raw, remaining_cap)
    drop_index = session["drops_used"] + 1
    closing = drop_index >= session["drops_total"]

    drop_row = connection.execute(
        f"""
        INSERT INTO {TABLE_DROPS}
            (drop_key, session_id, creator_handle, creator_id, user_key, display_name, drop_index,
             bits, slot, multiplier_pct, raw_payout, payout, capped, auto, credited_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                CASE WHEN %s = 0 THEN NOW() ELSE NULL END)
        RETURNING {_DROP_COLUMNS}
        """,
        (drop_key, session["session_id"], session["creator_handle"], session["creator_id"], session["user_key"],
         session["display_name"], drop_index, bits, slot, multiplier_pct, raw, payout, payout < raw, auto, payout),
    ).fetchone()

    session_row = connection.execute(
        f"""
        UPDATE {TABLE_SESSIONS}
           SET drops_used = drops_used + 1,
               payout_total = payout_total + %s,
               last_activity_at = NOW(),
               status = CASE WHEN %s THEN '{STATUS_CLOSED}' ELSE status END,
               close_reason = CASE WHEN %s THEN %s ELSE close_reason END,
               closed_at = CASE WHEN %s THEN NOW() ELSE closed_at END
         WHERE session_id = %s
        RETURNING {_SESSION_COLUMNS}
        """,
        (payout, closing, closing, "expired_autoplay" if auto else "all_drops_played", closing,
         session["session_id"]),
    ).fetchone()

    drop = _row_to_drop(drop_row)
    _audit(connection, session["creator_handle"], session["session_id"], "drop", {
        "drop_id": drop["id"], "drop_index": drop_index, "slot": slot, "bits": bits,
        "multiplier_pct": multiplier_pct, "raw_payout": raw, "payout": payout, "capped": payout < raw,
        "auto": auto, "drop_key": drop_key,
    }, Jsonb)
    updated = _row_to_session(session_row)
    if closing:
        _audit(connection, session["creator_handle"], session["session_id"], "closed", {
            "reason": updated["close_reason"], "payout_total": updated["payout_total"], "cost": updated["cost"],
        }, Jsonb)
    return drop, updated


def drop(
    creator_handle: str,
    *,
    user_key: str,
    drop_key: str,
    timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS,
) -> dict:
    """Play one drop in the viewer's active session. One transaction.
    Returns {"drop", "session", "replayed"}. The same drop_key (a re-sent
    or re-processed chat message) returns the original drop with
    replayed=True and pays nothing again -- the UNIQUE (creator_handle,
    drop_key) constraint makes that hold across restarts and races."""
    _require_available()
    handle = _clean_handle(creator_handle)
    user = _clean_key(user_key, "user_key").lower()
    key = _clean_key(drop_key, "drop_key")

    from psycopg.types.json import Jsonb

    def replay(row, connection):
        found = _row_to_drop(row)
        return {"drop": found, "session": _row_to_session(_session_by_id(connection, found["session_id"])),
                "replayed": True}

    with _connect(timeout=timeout) as connection:
        _ensure_schema(connection)
        row = _find_drop(connection, handle, key)
        if row is not None:
            return replay(row, connection)

        if _read_config(connection, handle).kill_switch:
            raise PlinkoKilled("Plinko is paused (kill switch).")

        session_row = connection.execute(
            f"""SELECT {_SESSION_COLUMNS} FROM {TABLE_SESSIONS}
                 WHERE creator_handle = %s AND user_key = %s AND status = %s
                 FOR UPDATE""",
            (handle, user, STATUS_ACTIVE),
        ).fetchone()

        # Re-check under the lock: a concurrent copy of this same message
        # may have just committed (and possibly closed the session).
        row = _find_drop(connection, handle, key)
        if row is not None:
            return replay(row, connection)
        if session_row is None:
            raise NoActiveSession("no active Plinko session.")

        new_drop, session = _record_drop(connection, _row_to_session(session_row), key, auto=False, Jsonb=Jsonb)

    return {"drop": new_drop, "session": session, "replayed": False}


def finish_expired(creator_handle: str, timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS) -> list[dict]:
    """Auto-plays the remaining drops of every active session idle longer
    than session_ttl_seconds, server-side, one transaction per session, with
    deterministic drop keys ("auto:<session>:<index>") so a crash or a
    concurrent call can't play them twice. Does nothing while the kill
    switch is on. Returns the new drops (the caller credits them)."""
    _require_available()
    handle = _clean_handle(creator_handle)
    from psycopg.types.json import Jsonb

    with _connect(timeout=timeout) as connection:
        _ensure_schema(connection)
        config = _read_config(connection, handle)
        if config.kill_switch:
            return []
        expired_ids = [r[0] for r in connection.execute(
            f"""SELECT session_id FROM {TABLE_SESSIONS}
                 WHERE creator_handle = %s AND status = %s
                   AND last_activity_at < NOW() - make_interval(secs => %s)""",
            (handle, STATUS_ACTIVE, config.session_ttl_seconds),
        ).fetchall()]

    played = []
    for session_id in expired_ids:
        with _connect(timeout=timeout) as connection:
            row = connection.execute(
                f"""SELECT {_SESSION_COLUMNS} FROM {TABLE_SESSIONS}
                     WHERE session_id = %s AND status = %s
                       AND last_activity_at < NOW() - make_interval(secs => %s)
                     FOR UPDATE SKIP LOCKED""",
                (session_id, STATUS_ACTIVE, config.session_ttl_seconds),
            ).fetchone()
            session = _row_to_session(row)
            if session is None:
                continue
            _audit(connection, handle, session_id, "expired", {"drops_left": session["drops_left"]}, Jsonb)
            while session["status"] == STATUS_ACTIVE:
                key = f"auto:{session_id}:{session['drops_used'] + 1}"
                new_drop, session = _record_drop(connection, session, key, auto=True, Jsonb=Jsonb)
                played.append(new_drop)
    return played


def mark_drop_credited(creator_handle: str, drop_id: int, timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS) -> bool:
    _require_available()
    handle = _clean_handle(creator_handle)
    from psycopg.types.json import Jsonb

    with _connect(timeout=timeout) as connection:
        _ensure_schema(connection)
        row = connection.execute(
            f"""UPDATE {TABLE_DROPS} SET credited_at = NOW()
                 WHERE creator_handle = %s AND id = %s AND credited_at IS NULL
             RETURNING session_id, payout""",
            (handle, int(drop_id)),
        ).fetchone()
        if row is not None:
            _audit(connection, handle, row[0], "credited", {"drop_id": int(drop_id), "payout": int(row[1])}, Jsonb)
    return row is not None


def uncredited_drops(creator_handle: str, min_age_seconds: int = 0,
                     timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS) -> list[dict]:
    _require_available()
    handle = _clean_handle(creator_handle)
    with _connect(timeout=timeout) as connection:
        _ensure_schema(connection)
        rows = connection.execute(
            f"""SELECT {_DROP_COLUMNS} FROM {TABLE_DROPS}
                 WHERE creator_handle = %s AND credited_at IS NULL AND payout > 0
                   AND created_at <= NOW() - make_interval(secs => %s)
                 ORDER BY id LIMIT 200""",
            (handle, int(min_age_seconds)),
        ).fetchall()
    return [_row_to_drop(r) for r in rows]


def stale_claimed_sessions(creator_handle: str, min_age_seconds: int = 30,
                           timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS) -> list[dict]:
    _require_available()
    handle = _clean_handle(creator_handle)
    with _connect(timeout=timeout) as connection:
        _ensure_schema(connection)
        rows = connection.execute(
            f"""SELECT {_SESSION_COLUMNS} FROM {TABLE_SESSIONS}
                 WHERE creator_handle = %s AND status = %s
                   AND created_at <= NOW() - make_interval(secs => %s)
                 ORDER BY created_at LIMIT 100""",
            (handle, STATUS_CLAIMED, int(min_age_seconds)),
        ).fetchall()
    return [_row_to_session(r) for r in rows]


# ---------------------------------------------------------------- reads (overlay / Studio)

def drops_since(creator_handle: str, since_id: int, limit: int = 30,
                timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS) -> list[dict]:
    _require_available()
    handle = _clean_handle(creator_handle)
    with _connect(timeout=timeout) as connection:
        _ensure_schema(connection)
        rows = connection.execute(
            f"""SELECT {_DROP_COLUMNS} FROM {TABLE_DROPS}
                 WHERE creator_handle = %s AND id > %s ORDER BY id LIMIT %s""",
            (handle, int(since_id), max(1, min(int(limit), 100))),
        ).fetchall()
    return [_row_to_drop(r) for r in rows]


def latest_drop_id(creator_handle: str, timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS) -> int:
    _require_available()
    handle = _clean_handle(creator_handle)
    with _connect(timeout=timeout) as connection:
        _ensure_schema(connection)
        row = connection.execute(
            f"SELECT COALESCE(MAX(id), 0) FROM {TABLE_DROPS} WHERE creator_handle = %s", (handle,)
        ).fetchone()
    return int(row[0] or 0)


def recent_drops(creator_handle: str, limit: int = 25, timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS) -> list[dict]:
    _require_available()
    handle = _clean_handle(creator_handle)
    with _connect(timeout=timeout) as connection:
        _ensure_schema(connection)
        rows = connection.execute(
            f"SELECT {_DROP_COLUMNS} FROM {TABLE_DROPS} WHERE creator_handle = %s ORDER BY id DESC LIMIT %s",
            (handle, max(1, min(int(limit), 200))),
        ).fetchall()
    return [_row_to_drop(r) for r in rows]


def list_sessions(creator_handle: str, limit: int = 25, timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS) -> list[dict]:
    _require_available()
    handle = _clean_handle(creator_handle)
    with _connect(timeout=timeout) as connection:
        _ensure_schema(connection)
        rows = connection.execute(
            f"""SELECT {_SESSION_COLUMNS} FROM {TABLE_SESSIONS}
                 WHERE creator_handle = %s ORDER BY created_at DESC LIMIT %s""",
            (handle, max(1, min(int(limit), 200))),
        ).fetchall()
    return [_row_to_session(r) for r in rows]


def session_audit(creator_handle: str, session_id: str, timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS) -> dict | None:
    """The session plus its full audit trail, oldest first. None if the
    session isn't this creator's."""
    _require_available()
    handle = _clean_handle(creator_handle)
    with _connect(timeout=timeout) as connection:
        _ensure_schema(connection)
        session = _row_to_session(connection.execute(
            f"SELECT {_SESSION_COLUMNS} FROM {TABLE_SESSIONS} WHERE creator_handle = %s AND session_id = %s",
            (handle, str(session_id)),
        ).fetchone())
        if session is None:
            return None
        rows = connection.execute(
            f"SELECT id, event, detail, created_at FROM {TABLE_AUDIT} WHERE session_id = %s ORDER BY id",
            (session["session_id"],),
        ).fetchall()
    return {
        "session": session,
        "events": [{"id": int(r[0]), "event": r[1], "detail": r[2] or {}, "at": _iso(r[3])} for r in rows],
    }


# ---------------------------------------------------------------- FUTURE PAID ENTRY: STUB ONLY
#
# ┌──────────────────────────────────────────────────────────────────────┐
# │ NOT IMPLEMENTED. There is no paid, real-money, tipping, vote-cashout │
# │ or PROMO entry into Plinko. FOXBOT_PLINKO_PAID_ENTRY (default false) │
# │ only exists so a future, separately reviewed (and legally cleared)   │
# │ change has an obvious seam. Turning it on enables nothing: the stub  │
# │ below raises unconditionally and touches no balance, ledger or DB.   │
# └──────────────────────────────────────────────────────────────────────┘

PAID_ENTRY_FLAG = "FOXBOT_PLINKO_PAID_ENTRY"


def paid_entry_enabled() -> bool:
    return (os.getenv(PAID_ENTRY_FLAG, "false") or "").strip().lower() == "true"


def start_paid_session(*_args, **_kwargs):
    """STUB. Always raises PaidEntryUnavailable, whatever the flag says."""
    if not paid_entry_enabled():
        raise PaidEntryUnavailable("Plinko is free-play FoxCoins only.")
    raise PaidEntryUnavailable("Paid Plinko entry is not implemented.")
