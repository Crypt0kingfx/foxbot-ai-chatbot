"""Central persistent file locations for FoxBot.

Local development defaults to ./data. Production can set FOXBOT_DATA_DIR to
a mounted persistent directory such as /var/data/foxbot.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any


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

    # Migrate a legacy local file once when a persistent directory is enabled.
    legacy = Path("data") / filename
    try:
        if not path.exists() and legacy.exists() and legacy.resolve() != path.resolve():
            shutil.copy2(legacy, path)
    except Exception:
        pass

    return path


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
    return {
        "ok": True,
        "configured": bool(str(os.getenv("FOXBOT_DATA_DIR") or "").strip()),
        "data_directory": str(directory),
        "creator_file": str(creator_file),
        "creator_file_exists": creator_file.exists(),
        "oauth_file": str(oauth_file),
        "oauth_file_exists": oauth_file.exists(),
        "writable": os.access(directory, os.W_OK),
    }