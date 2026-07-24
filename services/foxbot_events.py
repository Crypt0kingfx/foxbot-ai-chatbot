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


def _connect():
    import psycopg

    return psycopg.connect(database_url(), connect_timeout=10)


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


def emit_event(
    creator_handle: str,
    kind: str,
    actor: str | None = None,
    detail: dict[str, Any] | None = None,
) -> bool:
    """Log one foxbot_events row. Never raises.

    A failed insert (bad connection, missing table, database down, whatever)
    must never break the chat path calling this -- every failure mode here
    is caught, logged, and swallowed. Returns True only on a confirmed
    insert; callers that don't check the return value are still safe.
    """
    if not is_configured():
        return False

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
        return False

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

    return True
