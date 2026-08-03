"""Canonical Blaze access-token resolver.

One shared bot account authenticates every outbound and inbound Blaze
chat call across every connected creator's channel, so there is exactly
one token to resolve here -- not one per creator. This is the single
place that decides where that token comes from; app.py and
services/blaze_native_connector.py both delegate to it instead of each
maintaining their own precedence order.
"""

from __future__ import annotations

import json
import os

from services.storage_paths import storage_path


def _find_token(payload, possible_keys):
    if isinstance(payload, dict):
        for key in possible_keys:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        for value in payload.values():
            found = _find_token(value, possible_keys)
            if found:
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = _find_token(value, possible_keys)
            if found:
                return found
    return ""


def _saved_tokens():
    path = storage_path("blaze_oauth_tokens.json", "FOXBOT_OAUTH_TOKEN_FILE")
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8") or "{}")
    except Exception:
        return {}


def resolve_blaze_access_token():
    """Return (token, source): the saved OAuth file, then BLAZE_ACCESS_TOKEN."""
    token = _find_token(_saved_tokens(), ["accessToken", "access_token", "token"])
    if token:
        return token, "saved_oauth_file"

    token = str(os.getenv("BLAZE_ACCESS_TOKEN") or "").strip()
    if token:
        return token, "render_environment"

    return "", "missing"


def resolve_blaze_refresh_token():
    token = _find_token(_saved_tokens(), ["refreshToken", "refresh_token"])
    if token:
        return token
    return str(os.getenv("BLAZE_REFRESH_TOKEN") or "").strip()


def sync_tenant_zero_slot(payload, creator_id):
    """Mirror every flat top-level token field into by_creator[creator_id].

    Bot Connection Sub-phase A: storage-shape only. The flat keys stay the
    sole live read/write target -- resolve_blaze_access_token/refresh_token
    above never reach by_creator (they check flat keys first and only
    recurse if those are absent). This just keeps by_creator[tenant-zero]
    an accurate mirror so a later phase (C/F) isn't starting from a stale
    one-time snapshot. Unconditional overwrite, not a gated one-time copy --
    the flat keys keep changing (refresh cycles) all through Sub-phase A,
    so "copy once" would go stale on the very next refresh.

    creator_id is a caller-supplied parameter, not resolved here: this
    module has no access to app.py's _tenant_zero_id() (app.py imports
    from services, not the reverse) so each caller resolves its own
    creator_id and passes it in.
    """
    if not isinstance(payload, dict):
        return payload
    by_creator = payload.setdefault("by_creator", {})
    by_creator[creator_id] = {
        key: value for key, value in payload.items() if key != "by_creator"
    }
    return payload
