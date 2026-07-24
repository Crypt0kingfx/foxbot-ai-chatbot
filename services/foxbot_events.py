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
