"""Durable event log and onboarding-progress storage for FoxBot Studio.

Unlike postgres_state.py's single-JSONB-blob-per-key store (used for
connected_creators/blaze_oauth_tokens/foxbot_data), this is real relational
storage: an append-only events table and a tiny per-creator progress row.
Both are pure Postgres, no local-file fallback -- there's nothing to migrate
from and no dual-store hydration race to guard against here.
"""

from __future__ import annotations

import os
import random
import threading
from typing import Any


_schema_lock = threading.Lock()
_schema_ready = False
_last_error: str | None = None

RETENTION_DAYS = 30
RETENTION_DELETE_PROBABILITY = 0.01


def resolve_owner_handle() -> str:
    """Best-effort creator_handle for events with no specific per-creator
    target in scope: listener lifecycle events, and the native connector's
    path, which is single-channel and has no per-target concept at all.
    Same fallback services/blaze_multichannel.py's build_targets() already
    uses for the owner's own target entry -- not a new convention.
    """
    from services import creator_access

    raw = os.getenv("BLAZE_CHANNEL_SLUG") or os.getenv("FOXBOT_BLAZE_PROFILE_HANDLE") or ""
    handle = creator_access.clean_handle(raw)
    return handle or "foxbot-owner"


def database_url() -> str:
    return str(os.getenv("DATABASE_URL") or "").strip()


def is_configured() -> bool:
    return bool(database_url())


def _set_error(error: Exception | str | None) -> None:
    global _last_error
    _last_error = str(error)[:500] if error else None


# Short on purpose: emit_event() runs the actual connect/insert on a
# background daemon thread (see below), so this timeout does not add
# latency to the calling chat path -- it only bounds how long a single
# background thread stays alive trying to reach a dead database, which
# matters for not accumulating threads during a sustained outage.
EMIT_CONNECT_TIMEOUT_SECONDS = 2


def _connect():
    import psycopg

    return psycopg.connect(database_url(), connect_timeout=EMIT_CONNECT_TIMEOUT_SECONDS)


def _ensure_schema(connection) -> None:
    global _schema_ready
    if _schema_ready:
        return

    with _schema_lock:
        if _schema_ready:
            return

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS foxbot_events (
                id             BIGSERIAL PRIMARY KEY,
                creator_handle TEXT NOT NULL,
                kind           TEXT NOT NULL,
                actor          TEXT,
                detail         JSONB,
                created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_foxbot_events_handle_time
                ON foxbot_events (creator_handle, created_at DESC)
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS onboarding_progress (
                creator_handle TEXT PRIMARY KEY,
                dismissed      BOOLEAN NOT NULL DEFAULT FALSE,
                completed_at   TIMESTAMPTZ,
                updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        _schema_ready = True


def _emit_event_blocking(
    creator_handle: str,
    kind: str,
    actor: str | None,
    detail: dict[str, Any] | None,
) -> None:
    """The real connect/insert/retention-delete. Only ever runs on the
    background thread emit_event() spawns -- never call this directly
    from a request or chat path, it can block for EMIT_CONNECT_TIMEOUT_SECONDS.
    """
    try:
        from psycopg.types.json import Jsonb

        with _connect() as connection:
            _ensure_schema(connection)
            connection.execute(
                """
                INSERT INTO foxbot_events (creator_handle, kind, actor, detail)
                VALUES (%s, %s, %s, %s)
                """,
                (
                    str(creator_handle or "").strip() or "foxbot-owner",
                    str(kind or "").strip(),
                    str(actor).strip() if actor else None,
                    Jsonb(detail if isinstance(detail, dict) else {}),
                ),
            )
        _set_error(None)
    except Exception as error:
        _set_error(error)
        print(f"FoxBot event emit failed (kind={kind}): {error}")
        return

    if random.random() < RETENTION_DELETE_PROBABILITY:
        try:
            with _connect() as connection:
                connection.execute(
                    f"DELETE FROM foxbot_events WHERE created_at < NOW() - INTERVAL '{RETENTION_DAYS} days'"
                )
        except Exception as error:
            # Retention cleanup failing must not read as an emit failure --
            # the event above already landed.
            print(f"FoxBot event retention cleanup failed: {error}")


def fetch_events(creator_handle: str, limit: int = 20) -> list[tuple] | None:
    """Most recent events for a creator, newest first. Returns None on a
    database failure -- the query never ran -- which is distinct from an
    empty list, a legitimate "no events yet" result for a fresh channel.
    Callers must not conflate the two.
    """
    if not is_configured():
        return None

    from services import creator_access

    handle = creator_access.clean_handle(creator_handle) or resolve_owner_handle()
    capped_limit = max(1, min(int(limit or 20), 100))

    try:
        with _connect() as connection:
            _ensure_schema(connection)
            cursor = connection.execute(
                """
                SELECT kind, actor, detail, created_at
                FROM foxbot_events
                WHERE creator_handle = %s
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (handle, capped_limit),
            )
            rows = cursor.fetchall()
        _set_error(None)
        return rows
    except Exception as error:
        _set_error(error)
        print(f"FoxBot events read failed: {error}")
        return None


def event_exists(creator_handle: str, kind: str) -> bool | None:
    """Whether at least one event of `kind` has ever landed for this
    creator. Returns None (not False) on a database failure so callers
    can tell "checked, and no" apart from "couldn't check."
    """
    if not is_configured():
        return None

    from services import creator_access

    handle = creator_access.clean_handle(creator_handle) or resolve_owner_handle()

    try:
        with _connect() as connection:
            _ensure_schema(connection)
            cursor = connection.execute(
                "SELECT EXISTS(SELECT 1 FROM foxbot_events WHERE creator_handle = %s AND kind = %s)",
                (handle, kind),
            )
            row = cursor.fetchone()
        _set_error(None)
        return bool(row[0]) if row else False
    except Exception as error:
        _set_error(error)
        print(f"FoxBot event-exists check failed (kind={kind}): {error}")
        return None


def count_events(creator_handle: str, kind: str) -> int | None:
    """Total count of `kind` events ever recorded for this creator.
    Returns None (not 0) on a database failure so callers can tell
    "checked, and zero" apart from "couldn't check" -- same convention
    as event_exists() above.
    """
    if not is_configured():
        return None

    from services import creator_access

    handle = creator_access.clean_handle(creator_handle) or resolve_owner_handle()

    try:
        with _connect() as connection:
            _ensure_schema(connection)
            cursor = connection.execute(
                "SELECT COUNT(*) FROM foxbot_events WHERE creator_handle = %s AND kind = %s",
                (handle, kind),
            )
            row = cursor.fetchone()
        _set_error(None)
        return int(row[0]) if row else 0
    except Exception as error:
        _set_error(error)
        print(f"FoxBot event count failed (kind={kind}): {error}")
        return None


def fetch_onboarding_dismissal(creator_handle: str) -> dict[str, Any] | None:
    """Dismissal/completion row for a creator. Returns None on a database
    failure; a missing row (never dismissed, never completed) is a valid
    default, not a failure.
    """
    if not is_configured():
        return None

    from services import creator_access

    handle = creator_access.clean_handle(creator_handle) or resolve_owner_handle()

    try:
        with _connect() as connection:
            _ensure_schema(connection)
            cursor = connection.execute(
                "SELECT dismissed, completed_at FROM onboarding_progress WHERE creator_handle = %s",
                (handle,),
            )
            row = cursor.fetchone()
        _set_error(None)
        if row is None:
            return {"dismissed": False, "completed_at": None}
        return {"dismissed": bool(row[0]), "completed_at": row[1]}
    except Exception as error:
        _set_error(error)
        print(f"FoxBot onboarding-progress read failed: {error}")
        return None


def set_onboarding_dismissed(creator_handle: str, dismissed: bool = True) -> bool | None:
    """Persist the checklist dismiss state for a creator. Returns None on
    a database failure so the caller can tell "didn't write" apart from
    a legitimate write of False -- mirrors every other function here.

    Callers must enforce the register-complete gate themselves before
    calling this; it performs no such check on its own.
    """
    if not is_configured():
        return None

    from services import creator_access

    handle = creator_access.clean_handle(creator_handle) or resolve_owner_handle()

    try:
        with _connect() as connection:
            _ensure_schema(connection)
            connection.execute(
                """
                INSERT INTO onboarding_progress (creator_handle, dismissed, updated_at)
                VALUES (%s, %s, NOW())
                ON CONFLICT (creator_handle) DO UPDATE
                    SET dismissed = EXCLUDED.dismissed, updated_at = NOW()
                """,
                (handle, bool(dismissed)),
            )
        _set_error(None)
        return True
    except Exception as error:
        _set_error(error)
        print(f"FoxBot onboarding-dismiss write failed: {error}")
        return None


def emit_event(
    creator_handle: str,
    kind: str,
    actor: str | None = None,
    detail: dict[str, Any] | None = None,
) -> bool:
    """Schedule one foxbot_events row for background insert. Returns
    almost immediately -- this must never block the chat path calling it,
    regardless of whether Postgres is reachable at all.

    The connect/insert/retention-delete happens on a short-lived daemon
    thread instead of inline, so a dead DATABASE_URL can only ever cost
    that background thread up to EMIT_CONNECT_TIMEOUT_SECONDS -- never the
    caller. Returns True once the emit has been *scheduled*, not once it
    has landed; there is no way to report insert success synchronously
    anymore. Check logs for "FoxBot event emit failed" for actual
    failures. No call site in this codebase reads the return value as
    "inserted" -- all of them call this as a bare statement.
    """
    if not is_configured():
        return False

    threading.Thread(
        target=_emit_event_blocking,
        args=(creator_handle, kind, actor, detail),
        daemon=True,
    ).start()
    return True
