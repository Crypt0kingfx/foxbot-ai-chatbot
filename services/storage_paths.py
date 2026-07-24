"""Central storage locations for FoxBot files and Neon-backed state."""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from services.postgres_state import (
    database_status,
    is_configured as database_is_configured,
    load_or_migrate_json_state,
    save_json_state,
    CHAT_PATH_CONNECT_TIMEOUT_SECONDS,
)


_STATE_KEYS = {
    "connected_creators.json": "connected_creators",
    "blaze_oauth_tokens.json": "blaze_oauth_tokens",
    "foxbot_data.json": "foxbot_data",
}
_hydrated_state_keys: set[str] = set()
_failed_state_keys: set[str] = set()
_last_failed_attempt_at: dict[str, float] = {}
_initialization_lock = threading.Lock()
_BasePath = type(Path())

# Once a state_key's hydration has failed, storage_path() retries it on
# every call by default (see below) -- fine at normal request volume, but
# during a sustained Postgres outage that means every request pays a full
# connect_timeout. Rate-limit retries per state_key instead: at most one
# blocking attempt per this many seconds, independent per key (a dead
# connected_creators row must not suppress retries for blaze_oauth_tokens
# or foxbot_data -- they're independent stores with independent outage
# windows). A successful hydration clears this immediately; only repeated
# failure is throttled.
RETRY_COOLDOWN_SECONDS = 30


def _should_attempt_hydration(state_key: str) -> bool:
    if state_key in _hydrated_state_keys:
        return False
    last_failed = _last_failed_attempt_at.get(state_key)
    if last_failed is None:
        return True
    return (time.monotonic() - last_failed) >= RETRY_COOLDOWN_SECONDS


class _StateBackedPath(_BasePath):
    """Path that mirrors recognized JSON writes into Postgres."""

    def write_text(self, data, *args, **kwargs):
        written = super().write_text(data, *args, **kwargs)
        state_key = _STATE_KEYS.get(self.name)
        if state_key:
            try:
                payload = json.loads(str(data) or "null")
                save_json_state(state_key, payload, timeout=CHAT_PATH_CONNECT_TIMEOUT_SECONDS)
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

    legacy_candidates = [Path("data") / filename]
    if filename == "foxbot_data.json":
        legacy_candidates.insert(0, Path("foxbot_data.json"))

    try:
        for legacy in legacy_candidates:
            if not path.exists() and legacy.exists() and legacy.resolve() != path.resolve():
                shutil.copy2(legacy, path)
                break
    except Exception:
        pass

    state_path = _StateBackedPath(str(path))
    state_key = _STATE_KEYS.get(filename)
    if state_key and _should_attempt_hydration(state_key):
        with _initialization_lock:
            if _should_attempt_hydration(state_key):
                try:
                    load_or_migrate_json_state(
                        state_key, state_path, {}, timeout=CHAT_PATH_CONNECT_TIMEOUT_SECONDS
                    )
                    _hydrated_state_keys.add(state_key)
                    _failed_state_keys.discard(state_key)
                    # Recovery is instant: don't let a stale failure
                    # timestamp from this outage throttle a *future*,
                    # unrelated outage of the same store.
                    _last_failed_attempt_at.pop(state_key, None)
                except Exception:
                    # Leave state_key out of _hydrated_state_keys so a
                    # later storage_path() call for this file retries the
                    # hydration instead of treating this as done -- but
                    # not on every call; _should_attempt_hydration() rate
                    # limits it to once per RETRY_COOLDOWN_SECONDS.
                    _failed_state_keys.add(state_key)
                    _last_failed_attempt_at[state_key] = time.monotonic()

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
