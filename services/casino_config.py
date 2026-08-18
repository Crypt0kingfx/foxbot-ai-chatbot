"""Per-creator casino configuration: conversion rate and daily limits for
FoxCoin -> PROMO conversion (Casino Phase 3, providers/promo.py), and
per-(creator, game) enabled/min-bet/max-bet gating (Casino Phase 4,
services/casino_rounds.py).

Same fail-closed contract as services/casino_ledger.py: no local-file
fallback. Config that controls how real currency converts, or how much of
it a game will accept, isn't something that should silently degrade to a
different value on a different host.

A creator/game with no row yet gets sane defaults rather than an error --
conversion and games should work out of the box without requiring an
admin to configure every creator first, the same "first access always
succeeds" philosophy app.py's _creator_economy_v1() and friends already
use.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass


TABLE_CONFIG = "casino_config"
TABLE_GAME_CONFIG = "casino_game_config"

_schema_lock = threading.Lock()
_schema_ready = False

DEFAULT_CONNECT_TIMEOUT_SECONDS = 10

# 100 FoxCoins -> 1 PROMO credit, up to 5000 PROMO/day, until a creator
# configures otherwise via set_config().
DEFAULT_FOXCOINS_PER_PROMO = 100
DEFAULT_DAILY_PROMO_LIMIT = 5000

# Opt-in, not opt-out (Casino Phase 5 go-live gate) -- unlike the other
# defaults in this module, a creator's casino is OFF until they explicitly
# turn it on via set_config(casino_enabled=True). Real FoxCoins start
# moving the moment this is True (plus the FOXBOT_CASINO_ENABLED platform
# flag in app.py), so "works out of the box" is the wrong default here.
DEFAULT_CASINO_ENABLED = False

# Every game enabled with a 1..1000 PROMO bet range by default, until a
# creator configures otherwise via set_game_config().
DEFAULT_GAME_ENABLED = True
DEFAULT_MIN_BET = 1
DEFAULT_MAX_BET = 1000


class CasinoConfigUnavailable(Exception):
    """Raised when DATABASE_URL isn't configured -- see module docstring
    for why there is no local-file fallback here."""


@dataclass(frozen=True)
class CasinoConfig:
    creator_id: str
    foxcoins_per_promo: int
    daily_promo_limit: int
    casino_enabled: bool


@dataclass(frozen=True)
class GameConfig:
    creator_id: str
    game_id: str
    enabled: bool
    min_bet: int
    max_bet: int


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
                casino_enabled     BOOLEAN NOT NULL DEFAULT {str(DEFAULT_CASINO_ENABLED).upper()},
                updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        # casino_enabled was added after this table first shipped (Casino
        # Phase 3) -- CREATE TABLE IF NOT EXISTS above is a no-op against
        # an already-existing table, so an explicit migration is needed to
        # backfill the column on any database where casino_config was
        # created before this column existed.
        connection.execute(
            f"""
            ALTER TABLE {TABLE_CONFIG}
            ADD COLUMN IF NOT EXISTS casino_enabled BOOLEAN NOT NULL DEFAULT {str(DEFAULT_CASINO_ENABLED).upper()}
            """
        )
        connection.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE_GAME_CONFIG} (
                creator_id TEXT NOT NULL,
                game_id    TEXT NOT NULL,
                enabled    BOOLEAN NOT NULL DEFAULT {str(DEFAULT_GAME_ENABLED).upper()},
                min_bet    BIGINT NOT NULL DEFAULT {DEFAULT_MIN_BET},
                max_bet    BIGINT NOT NULL DEFAULT {DEFAULT_MAX_BET},
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (creator_id, game_id)
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
            f"SELECT foxcoins_per_promo, daily_promo_limit, casino_enabled FROM {TABLE_CONFIG} WHERE creator_id = %s",
            (creator_id,),
        ).fetchone()

    if row is None:
        return CasinoConfig(creator_id, DEFAULT_FOXCOINS_PER_PROMO, DEFAULT_DAILY_PROMO_LIMIT, DEFAULT_CASINO_ENABLED)
    return CasinoConfig(creator_id, int(row[0]), int(row[1]), bool(row[2]))


def set_config(
    creator_id: str,
    *,
    foxcoins_per_promo: int | None = None,
    daily_promo_limit: int | None = None,
    casino_enabled: bool | None = None,
    timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS,
) -> CasinoConfig:
    _require_available()

    creator_id = str(creator_id or "").strip()
    if not creator_id:
        raise ValueError("creator_id is required.")

    rate = DEFAULT_FOXCOINS_PER_PROMO if foxcoins_per_promo is None else int(foxcoins_per_promo)
    limit = DEFAULT_DAILY_PROMO_LIMIT if daily_promo_limit is None else int(daily_promo_limit)
    enabled = DEFAULT_CASINO_ENABLED if casino_enabled is None else bool(casino_enabled)
    if rate <= 0:
        raise ValueError("foxcoins_per_promo must be a positive integer.")
    if limit <= 0:
        raise ValueError("daily_promo_limit must be a positive integer.")

    with _connect(timeout=timeout) as connection:
        _ensure_schema(connection)
        current = connection.execute(
            f"SELECT foxcoins_per_promo, daily_promo_limit, casino_enabled FROM {TABLE_CONFIG} WHERE creator_id = %s",
            (creator_id,),
        ).fetchone()

        # Partial updates (only some of the kwargs given) should not
        # silently reset the other fields back to module defaults.
        if current is not None:
            if foxcoins_per_promo is None:
                rate = int(current[0])
            if daily_promo_limit is None:
                limit = int(current[1])
            if casino_enabled is None:
                enabled = bool(current[2])

        connection.execute(
            f"""
            INSERT INTO {TABLE_CONFIG} (creator_id, foxcoins_per_promo, daily_promo_limit, casino_enabled, updated_at)
            VALUES (%s, %s, %s, %s, NOW())
            ON CONFLICT (creator_id) DO UPDATE
                SET foxcoins_per_promo = EXCLUDED.foxcoins_per_promo,
                    daily_promo_limit = EXCLUDED.daily_promo_limit,
                    casino_enabled = EXCLUDED.casino_enabled,
                    updated_at = NOW()
            """,
            (creator_id, rate, limit, enabled),
        )

    return CasinoConfig(creator_id, rate, limit, enabled)


def get_game_config(creator_id: str, game_id: str, timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS) -> GameConfig:
    _require_available()

    creator_id = str(creator_id or "").strip()
    game_id = str(game_id or "").strip()
    if not creator_id or not game_id:
        raise ValueError("creator_id and game_id are required.")

    with _connect(timeout=timeout) as connection:
        _ensure_schema(connection)
        row = connection.execute(
            f"""
            SELECT enabled, min_bet, max_bet FROM {TABLE_GAME_CONFIG}
            WHERE creator_id = %s AND game_id = %s
            """,
            (creator_id, game_id),
        ).fetchone()

    if row is None:
        return GameConfig(creator_id, game_id, DEFAULT_GAME_ENABLED, DEFAULT_MIN_BET, DEFAULT_MAX_BET)
    return GameConfig(creator_id, game_id, bool(row[0]), int(row[1]), int(row[2]))


def set_game_config(
    creator_id: str,
    game_id: str,
    *,
    enabled: bool | None = None,
    min_bet: int | None = None,
    max_bet: int | None = None,
    timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS,
) -> GameConfig:
    _require_available()

    creator_id = str(creator_id or "").strip()
    game_id = str(game_id or "").strip()
    if not creator_id or not game_id:
        raise ValueError("creator_id and game_id are required.")

    with _connect(timeout=timeout) as connection:
        _ensure_schema(connection)
        current = connection.execute(
            f"""
            SELECT enabled, min_bet, max_bet FROM {TABLE_GAME_CONFIG}
            WHERE creator_id = %s AND game_id = %s
            """,
            (creator_id, game_id),
        ).fetchone()

        is_enabled = DEFAULT_GAME_ENABLED if enabled is None else bool(enabled)
        lo = DEFAULT_MIN_BET if min_bet is None else int(min_bet)
        hi = DEFAULT_MAX_BET if max_bet is None else int(max_bet)

        # Partial updates should not silently reset the other fields back
        # to module defaults -- same contract as set_config() above.
        if current is not None:
            if enabled is None:
                is_enabled = bool(current[0])
            if min_bet is None:
                lo = int(current[1])
            if max_bet is None:
                hi = int(current[2])

        if lo <= 0:
            raise ValueError("min_bet must be a positive integer.")
        if hi < lo:
            raise ValueError("max_bet must be >= min_bet.")

        connection.execute(
            f"""
            INSERT INTO {TABLE_GAME_CONFIG} (creator_id, game_id, enabled, min_bet, max_bet, updated_at)
            VALUES (%s, %s, %s, %s, %s, NOW())
            ON CONFLICT (creator_id, game_id) DO UPDATE
                SET enabled = EXCLUDED.enabled,
                    min_bet = EXCLUDED.min_bet,
                    max_bet = EXCLUDED.max_bet,
                    updated_at = NOW()
            """,
            (creator_id, game_id, is_enabled, lo, hi),
        )

    return GameConfig(creator_id, game_id, is_enabled, lo, hi)
