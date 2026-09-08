"""Per-creator text-to-speech configuration for the Casino Stream Overlay's
TTS sibling (/overlay/tts): which voice/volume the browser's own
SpeechSynthesis API should use, and the guardrails that decide whether a
given casino_win is worth interrupting the stream's audio for.

Keyed by creator_HANDLE, not creator_id -- deliberately different from
services/casino_config.py's creator_id keying. Both real consumers of this
config only ever have a handle in scope: the emit hook
(app.py's _foxbot_casino_emit_win_v1) receives creator_handle, not
creator_id, and the public /overlay/tts-data endpoint is an anonymous OBS
browser source scoped by the SAME ?handle= query param /overlay/casino-data
already uses (no session to resolve a creator_id from at all). Introducing
a creator_id lookup here would mean resolving handle->id at both call
sites for no benefit.

Same fail-closed contract as casino_config.py: no local-file fallback, and
a creator with no row yet gets sane defaults (TTS is opt-in like
casino_enabled, so `enabled` defaults to False -- everything else works
out of the box once a creator turns it on).
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass


TABLE_CONFIG = "tts_config"

_schema_lock = threading.Lock()
_schema_ready = False

DEFAULT_CONNECT_TIMEOUT_SECONDS = 10

# Opt-in, not opt-out -- same Casino-Phase-5-style go-live gate as
# casino_config.DEFAULT_CASINO_ENABLED. A creator's stream shouldn't
# suddenly start talking because a column defaulted to True.
DEFAULT_TTS_ENABLED = False

# '' means "browser default voice" -- speak-time code must fall back to
# the overlay page's default voice when this name isn't found in that
# page's own live getVoices() list (a different machine, a removed voice
# pack, or simply never configured yet).
DEFAULT_VOICE_NAME = ""

DEFAULT_VOLUME = 80
DEFAULT_CHAR_LIMIT = 200
DEFAULT_MIN_PAYOUT = 100
DEFAULT_COOLDOWN_SECONDS = 15

MIN_VOLUME = 0
MAX_VOLUME = 100
MIN_CHAR_LIMIT = 20
MAX_CHAR_LIMIT = 500
MIN_COOLDOWN_SECONDS = 5
MAX_COOLDOWN_SECONDS = 300


class TtsConfigUnavailable(Exception):
    """Raised when DATABASE_URL isn't configured -- see module docstring
    for why there is no local-file fallback here."""


@dataclass(frozen=True)
class TtsConfig:
    creator_handle: str
    enabled: bool
    voice_name: str
    volume: int
    char_limit: int
    min_payout: int
    cooldown_seconds: int


def database_url() -> str:
    return str(os.getenv("DATABASE_URL") or "").strip()


def is_available() -> bool:
    return bool(database_url())


def _require_available() -> None:
    if not is_available():
        raise TtsConfigUnavailable(
            "TTS config requires DATABASE_URL (Postgres) -- there is no "
            "local-file fallback by design."
        )


def _connect(timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS):
    import psycopg

    return psycopg.connect(database_url(), connect_timeout=timeout)


def _ensure_schema(connection) -> None:
    """CREATE TABLE IF NOT EXISTS is NOT safe against two independent
    connections/processes both calling this for the very first time
    concurrently, before the table exists: both see "doesn't exist yet"
    (Postgres visibility rules -- an uncommitted DDL from another
    session is invisible to this session's own existence check) and both
    attempt the CREATE; only one can win at commit time. Confirmed by a
    direct reproduction (10 parallel first-time callers against a freshly
    dropped table): most raised psycopg.errors.UniqueViolation on
    pg_type_typname_nsp_index, the underlying catalog collision. The
    module-level _schema_lock only serializes callers WITHIN this one
    process -- it cannot prevent a race against a different process (a
    second test run, a live app server) hitting the same fresh database
    at the same moment.

    Losing the race must not surface as an error to a caller who did
    nothing wrong: the table exists either way once the dust settles, so
    catch the specific duplicate-object errors and roll back this
    connection's now-aborted transaction (a failed statement poisons the
    rest of the transaction until rolled back) so the caller's own query
    right after this can proceed normally.
    """
    global _schema_ready
    if _schema_ready:
        return

    with _schema_lock:
        if _schema_ready:
            return

        import psycopg

        try:
            connection.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {TABLE_CONFIG} (
                    creator_handle   TEXT PRIMARY KEY,
                    enabled          BOOLEAN NOT NULL DEFAULT {str(DEFAULT_TTS_ENABLED).upper()},
                    voice_name       TEXT NOT NULL DEFAULT '',
                    volume           INT NOT NULL DEFAULT {DEFAULT_VOLUME},
                    char_limit       INT NOT NULL DEFAULT {DEFAULT_CHAR_LIMIT},
                    min_payout       INT NOT NULL DEFAULT {DEFAULT_MIN_PAYOUT},
                    cooldown_seconds INT NOT NULL DEFAULT {DEFAULT_COOLDOWN_SECONDS},
                    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
        except (psycopg.errors.DuplicateTable, psycopg.errors.UniqueViolation):
            connection.rollback()

        _schema_ready = True


def get_config(creator_handle: str, timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS) -> TtsConfig:
    _require_available()

    from services import creator_access

    handle = creator_access.clean_handle(creator_handle)
    if not handle:
        raise ValueError("creator_handle is required.")

    with _connect(timeout=timeout) as connection:
        _ensure_schema(connection)
        row = connection.execute(
            f"""
            SELECT enabled, voice_name, volume, char_limit, min_payout, cooldown_seconds
            FROM {TABLE_CONFIG} WHERE creator_handle = %s
            """,
            (handle,),
        ).fetchone()

    if row is None:
        return TtsConfig(
            handle, DEFAULT_TTS_ENABLED, DEFAULT_VOICE_NAME, DEFAULT_VOLUME,
            DEFAULT_CHAR_LIMIT, DEFAULT_MIN_PAYOUT, DEFAULT_COOLDOWN_SECONDS,
        )
    return TtsConfig(
        handle, bool(row[0]), str(row[1] or ""), int(row[2]), int(row[3]), int(row[4]), int(row[5]),
    )


def set_config(
    creator_handle: str,
    *,
    enabled: bool | None = None,
    voice_name: str | None = None,
    volume: int | None = None,
    char_limit: int | None = None,
    min_payout: int | None = None,
    cooldown_seconds: int | None = None,
    timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS,
) -> TtsConfig:
    _require_available()

    from services import creator_access

    handle = creator_access.clean_handle(creator_handle)
    if not handle:
        raise ValueError("creator_handle is required.")

    is_enabled = DEFAULT_TTS_ENABLED if enabled is None else bool(enabled)
    name = DEFAULT_VOICE_NAME if voice_name is None else str(voice_name).strip()[:200]
    vol = DEFAULT_VOLUME if volume is None else int(volume)
    limit = DEFAULT_CHAR_LIMIT if char_limit is None else int(char_limit)
    floor = DEFAULT_MIN_PAYOUT if min_payout is None else int(min_payout)
    cooldown = DEFAULT_COOLDOWN_SECONDS if cooldown_seconds is None else int(cooldown_seconds)

    with _connect(timeout=timeout) as connection:
        _ensure_schema(connection)
        current = connection.execute(
            f"""
            SELECT enabled, voice_name, volume, char_limit, min_payout, cooldown_seconds
            FROM {TABLE_CONFIG} WHERE creator_handle = %s
            """,
            (handle,),
        ).fetchone()

        # Partial updates (only some kwargs given) must not silently reset
        # the other fields back to module defaults -- same contract as
        # casino_config.set_config().
        if current is not None:
            if enabled is None:
                is_enabled = bool(current[0])
            if voice_name is None:
                name = str(current[1] or "")
            if volume is None:
                vol = int(current[2])
            if char_limit is None:
                limit = int(current[3])
            if min_payout is None:
                floor = int(current[4])
            if cooldown_seconds is None:
                cooldown = int(current[5])

        if not (MIN_VOLUME <= vol <= MAX_VOLUME):
            raise ValueError(f"volume must be between {MIN_VOLUME} and {MAX_VOLUME}.")
        if not (MIN_CHAR_LIMIT <= limit <= MAX_CHAR_LIMIT):
            raise ValueError(f"char_limit must be between {MIN_CHAR_LIMIT} and {MAX_CHAR_LIMIT}.")
        if floor < 0:
            raise ValueError("min_payout must be zero or a positive integer.")
        if not (MIN_COOLDOWN_SECONDS <= cooldown <= MAX_COOLDOWN_SECONDS):
            raise ValueError(f"cooldown_seconds must be between {MIN_COOLDOWN_SECONDS} and {MAX_COOLDOWN_SECONDS}.")

        connection.execute(
            f"""
            INSERT INTO {TABLE_CONFIG}
                (creator_handle, enabled, voice_name, volume, char_limit, min_payout, cooldown_seconds, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (creator_handle) DO UPDATE
                SET enabled = EXCLUDED.enabled,
                    voice_name = EXCLUDED.voice_name,
                    volume = EXCLUDED.volume,
                    char_limit = EXCLUDED.char_limit,
                    min_payout = EXCLUDED.min_payout,
                    cooldown_seconds = EXCLUDED.cooldown_seconds,
                    updated_at = NOW()
            """,
            (handle, is_enabled, name, vol, limit, floor, cooldown),
        )

    return TtsConfig(handle, is_enabled, name, vol, limit, floor, cooldown)
