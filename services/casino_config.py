"""Per-creator casino configuration -- conversion rate and daily limits for
FoxCoin -> PROMO conversion (Casino Phase 3, providers/promo.py).

Same fail-closed contract as services/casino_ledger.py: no local-file
fallback. Config that controls how real currency converts into casino
credit isn't something that should silently degrade to a different value
on a different host.

A creator with no row yet gets sane defaults (DEFAULT_FOXCOINS_PER_PROMO /
DEFAULT_DAILY_PROMO_LIMIT) rather than an error -- conversion should work
out of the box without requiring an admin to configure every creator
first, the same "first access always succeeds" philosophy app.py's
_creator_economy_v1() and friends already use.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass


TABLE_CONFIG = "casino_config"

_schema_lock = threading.Lock()
_schema_ready = False

DEFAULT_CONNECT_TIMEOUT_SECONDS = 10

# 100 FoxCoins -> 1 PROMO credit, up to 5000 PROMO/day, until a creator
# configures otherwise via set_config().
DEFAULT_FOXCOINS_PER_PROMO = 100
DEFAULT_DAILY_PROMO_LIMIT = 5000


class CasinoConfigUnavailable(Exception):
    """Raised when DATABASE_URL isn't configured -- see module docstring
    for why there is no local-file fallback here."""


@dataclass(frozen=True)
class CasinoConfig:
    creator_id: str
    foxcoins_per_promo: int
    daily_promo_limit: int


def database_url() -> str:
    return str(os.getenv("DATABASE_URL") or "").strip()


def is_available() -> bool:
    return bool(database_url())


def _require_available() -> None:
    if not is_available():
        raise CasinoConfigUnavailable(
            "Casino config requires DATABASE_URL (Postgres) -- there is no "
            "local-file fallback by design."
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
            CREATE TABLE IF NOT EXISTS {TABLE_CONFIG} (
                creator_id         TEXT PRIMARY KEY,
                foxcoins_per_promo BIGINT NOT NULL DEFAULT {DEFAULT_FOXCOINS_PER_PROMO},
                daily_promo_limit  BIGINT NOT NULL DEFAULT {DEFAULT_DAILY_PROMO_LIMIT},
                updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        _schema_ready = True


def get_config(creator_id: str, timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS) -> CasinoConfig:
    _require_available()

    creator_id = str(creator_id or "").strip()
    if not creator_id:
        raise ValueError("creator_id is required.")

    with _connect(timeout=timeout) as connection:
        _ensure_schema(connection)
        row = connection.execute(
            f"SELECT foxcoins_per_promo, daily_promo_limit FROM {TABLE_CONFIG} WHERE creator_id = %s",
            (creator_id,),
        ).fetchone()

    if row is None:
        return CasinoConfig(creator_id, DEFAULT_FOXCOINS_PER_PROMO, DEFAULT_DAILY_PROMO_LIMIT)
    return CasinoConfig(creator_id, int(row[0]), int(row[1]))


def set_config(
    creator_id: str,
    *,
    foxcoins_per_promo: int | None = None,
    daily_promo_limit: int | None = None,
    timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS,
) -> CasinoConfig:
    _require_available()

    creator_id = str(creator_id or "").strip()
    if not creator_id:
        raise ValueError("creator_id is required.")

    rate = DEFAULT_FOXCOINS_PER_PROMO if foxcoins_per_promo is None else int(foxcoins_per_promo)
    limit = DEFAULT_DAILY_PROMO_LIMIT if daily_promo_limit is None else int(daily_promo_limit)
    if rate <= 0:
        raise ValueError("foxcoins_per_promo must be a positive integer.")
    if limit <= 0:
        raise ValueError("daily_promo_limit must be a positive integer.")

    with _connect(timeout=timeout) as connection:
        _ensure_schema(connection)
        current = connection.execute(
            f"SELECT foxcoins_per_promo, daily_promo_limit FROM {TABLE_CONFIG} WHERE creator_id = %s",
            (creator_id,),
        ).fetchone()

        # Partial updates (only one of the two kwargs given) should not
        # silently reset the other field back to the module default.
        if current is not None:
            if foxcoins_per_promo is None:
                rate = int(current[0])
            if daily_promo_limit is None:
                limit = int(current[1])

        connection.execute(
            f"""
            INSERT INTO {TABLE_CONFIG} (creator_id, foxcoins_per_promo, daily_promo_limit, updated_at)
            VALUES (%s, %s, %s, NOW())
            ON CONFLICT (creator_id) DO UPDATE
                SET foxcoins_per_promo = EXCLUDED.foxcoins_per_promo,
                    daily_promo_limit = EXCLUDED.daily_promo_limit,
                    updated_at = NOW()
            """,
            (creator_id, rate, limit),
        )

    return CasinoConfig(creator_id, rate, limit)
