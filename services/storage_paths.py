"""Central storage locations for FoxBot files and Neon-backed state."""

from __future__ import annotations

import json
import os
import shutil
import threading
from pathlib import Path
from typing import Any

from services.postgres_state import (
    database_status,
    is_configured as database_is_configured,
    load_or_migrate_json_state,
    save_json_state,
)


_STATE_KEYS = {
    "connected_creators.json": "connected_creators",
    "blaze_oauth_tokens.json": "blaze_oauth_tokens",
    "foxbot_data.json": "foxbot_data",
}
_hydrated_state_keys: set[str] = set()
_failed_state_keys: set[str] = set()
_initialization_lock = threading.Lock()
_BasePath = type(Path())


class _StateBackedPath(_BasePath):
    """Path that mirrors recognized JSON writes into Postgres."""

    def write_text(self, data, *args, **kwargs):
        written = super().write_text(data, *args, **kwargs)
        state_key = _STATE_KEYS.get(self.name)
        if state_key:
            try:
                payload = json.loads(str(data) or "null")
                save_json_state(state_key, payload)
            except Exception:
                pass
        return written


def data_directory() -> Path:
    configured = str(os.getenv("FOXBOT_DATA_DIR") or "").strip()
    if configured:
        path = Path(configured).expanduser()
    else:
        existing_data_file = str(os.getenv("FOXBOT_DATA_FILE") or "").strip()
        existing_parent = Path(existing_data_file).expanduser().parent if existing_data_file else None
        if existing_parent and str(existing_parent) not in {"", "."}:
            path = existing_parent
        else:
            path = Path("data")

    path.mkdir(parents=True, exist_ok=True)
    return path


def storage_path(filename: str, env_key: str | None = None) -> Path:
    explicit = str(os.getenv(env_key) or "").strip() if env_key else ""
    path = Path(explicit).expanduser() if explicit else data_directory() / filename
    path.parent.mkdir(parents=True, exist_ok=True)

    legacy = Path("data") / filename
    try:
        if not path.exists() and legacy.exists() and legacy.resolve() != path.resolve():
            shutil.copy2(legacy, path)
    except Exception:
        pass

    state_path = _StateBackedPath(str(path))
    state_key = _STATE_KEYS.get(filename)
    if state_key and state_key not in _hydrated_state_keys:
        with _initialization_lock:
            if state_key not in _hydrated_state_keys:
                try:
                    load_or_migrate_json_state(state_key, state_path, {})
                    _hydrated_state_keys.add(state_key)
                    _failed_state_keys.discard(state_key)
                except Exception:
                    # Leave state_key out of _hydrated_state_keys so the
                    # next storage_path() call for this file retries the
                    # hydration instead of treating this as done.
                    _failed_state_keys.add(state_key)

    return state_path


def hydration_failed(filename: str) -> bool:
    """True when the most recent Postgres hydration attempt for this
    state-backed file raised, and no successful hydration has happened
    since (including one that legitimately found nothing there).

    Callers that would write state derived from this file's in-memory
    contents back to Postgres must check this first and refuse while it's
    True -- otherwise a transient failure looks identical to "genuinely
    empty" and the write overwrites a good row with incomplete or stale
    local state.
    """
    state_key = _STATE_KEYS.get(filename)
    if not state_key:
        return False
    return state_key in _failed_state_keys


def storage_status() -> dict[str, Any]:
    directory = data_directory()
    creator_file = storage_path(
        "connected_creators.json",
        "FOXBOT_CONNECTED_CREATORS_FILE",
    )
    oauth_file = storage_path(
        "blaze_oauth_tokens.json",
        "FOXBOT_OAUTH_TOKEN_FILE",
    )
    database = database_status()
    return {
        "ok": True,
        "configured": bool(str(os.getenv("FOXBOT_DATA_DIR") or "").strip())
        or database_is_configured(),
        "backend": database.get("backend"),
        "database": database,
        "data_directory": str(directory),
        "creator_file": str(creator_file),
        "creator_file_exists": creator_file.exists(),
        "oauth_file": str(oauth_file),
        "oauth_file_exists": oauth_file.exists(),
        "writable": os.access(directory, os.W_OK),
    }
