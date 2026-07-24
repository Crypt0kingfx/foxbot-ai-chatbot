"""Small JSON state store backed by Postgres with a local-file fallback.

FoxBot keeps its existing JSON document shapes. When ``DATABASE_URL`` is set,
the documents are stored atomically in Neon Postgres. Local files remain useful
for development and as a migration source, but they are not the source of truth
in production.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any


TABLE_NAME = "foxbot_state"
_schema_lock = threading.Lock()
_schema_ready = False
_last_error: str | None = None


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
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
                state_key TEXT PRIMARY KEY,
                payload JSONB NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        _schema_ready = True


def load_json_state(state_key: str) -> Any | None:
    """Load a JSON-compatible value, returning ``None`` when unavailable."""
    if not is_configured():
        return None

    try:
        with _connect() as connection:
            _ensure_schema(connection)
            row = connection.execute(
                f"SELECT payload FROM {TABLE_NAME} WHERE state_key = %s",
                (str(state_key),),
            ).fetchone()
        _set_error(None)
        return row[0] if row else None
    except Exception as error:
        _set_error(error)
        return None


def load_json_state_strict(state_key: str) -> Any | None:
    """Like ``load_json_state``, but let a query failure raise instead of
    returning ``None``. ``None`` from this function means the row is
    genuinely absent -- callers that must tell "nothing saved" apart from
    "couldn't reach Postgres" (e.g. a security gate that has to fail closed
    when it can't prove a negative) should use this instead. Assumes the
    caller already checked ``is_configured()``.
    """
    with _connect() as connection:
        _ensure_schema(connection)
        row = connection.execute(
            f"SELECT payload FROM {TABLE_NAME} WHERE state_key = %s",
            (str(state_key),),
        ).fetchone()
    _set_error(None)
    return row[0] if row else None


def save_json_state(state_key: str, payload: Any) -> bool:
    """Atomically insert or replace one JSON-compatible state document."""
    if not is_configured():
        return False

    try:
        from psycopg.types.json import Jsonb

        with _connect() as connection:
            _ensure_schema(connection)
            connection.execute(
                f"""
                INSERT INTO {TABLE_NAME} (state_key, payload, updated_at)
                VALUES (%s, %s, NOW())
                ON CONFLICT (state_key) DO UPDATE
                SET payload = EXCLUDED.payload, updated_at = NOW()
                """,
                (str(state_key), Jsonb(payload)),
            )
        _set_error(None)
        return True
    except Exception as error:
        _set_error(error)
        return False


def _read_local(path: Path, default: Any) -> Any:
    local_path = Path(str(path))
    if not local_path.exists():
        return default
    try:
        return json.loads(local_path.read_text(encoding="utf-8") or "null")
    except Exception:
        return default


def _write_local(path: Path, payload: Any) -> None:
    local_path = Path(str(path))
    local_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = local_path.with_suffix(local_path.suffix + ".dbsync.tmp")
    temporary_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(temporary_path, local_path)


def load_or_migrate_json_state(state_key: str, path: Path, default: Any) -> Any:
    """Prefer Postgres, migrating an existing local JSON document once.

    Raises on a genuine Postgres query failure rather than treating it the
    same as "no row yet" -- ``load_json_state`` used to collapse both cases
    to ``None``, which made a transient connection error indistinguishable
    from a fresh install, and the migrate branch would then write a stale
    or empty local document over a real row. Callers decide how to react to
    the failure (skip a write, retry later); it is not hidden here.
    """
    if is_configured():
        stored = load_json_state_strict(state_key)
        if stored is not None:
            try:
                _write_local(path, stored)
            except Exception:
                pass
            return stored

        local = _read_local(path, default)
        save_json_state(state_key, local)
        return local

    return _read_local(path, default)


def restore_json_state_file(state_key: str, path: Path) -> bool:
    """Restore a database document into the ephemeral compatibility file."""
    if not is_configured():
        return False
    stored = load_json_state(state_key)
    if stored is None:
        return False
    try:
        _write_local(path, stored)
        return True
    except Exception as error:
        _set_error(error)
        return False


def database_status() -> dict[str, Any]:
    result: dict[str, Any] = {
        "configured": is_configured(),
        "connected": False,
        "backend": "neon_postgres" if is_configured() else "local_files",
        "last_error": _last_error,
    }
    if not is_configured():
        return result

    try:
        with _connect() as connection:
            _ensure_schema(connection)
            connection.execute("SELECT 1").fetchone()
        _set_error(None)
        result["connected"] = True
        result["last_error"] = None
    except Exception as error:
        _set_error(error)
        result["last_error"] = _last_error
    return result
