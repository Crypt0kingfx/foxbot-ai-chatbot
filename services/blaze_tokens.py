"""Canonical Blaze access-token resolver.

Today, in practice, one shared bot account still authenticates every
outbound and inbound Blaze chat call across every connected creator's
channel -- every caller resolves with no creator_id, so real resolution
is still exactly one token, process-wide. This is the single place that
decides where that token comes from; app.py and
services/blaze_native_connector.py both delegate to it instead of each
maintaining their own precedence order.

resolve_blaze_access_token() optionally accepts a creator_id (Bot
Connection Sub-phase F, stage 0) to resolve a specific creator's own
by_creator token slot instead of the shared one. Nothing calls it with a
real creator_id yet -- this is dormant scaffolding, additive only, wired
up in a later stage once Sub-phase E has a real per-creator token to
resolve.
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


def resolve_blaze_access_token(creator_id=None):
    """Return (token, source): the saved OAuth file, then BLAZE_ACCESS_TOKEN.

    Bot Connection Sub-phase F, stage 0: creator_id is optional and additive.
    Every caller today passes none, so this is unchanged for all of them --
    the block below never runs and resolution falls straight through to the
    original two-step lookup (saved flat file, then the env var).

    When a caller DOES pass a creator_id, that creator's own by_creator
    slot is tried FIRST. A slot with a real token wins. No slot yet (every
    creator_id today, until Sub-phase E lands a real one) falls through to
    the exact same shared/tenant-zero resolution as creator_id=None --
    dormant by construction, never an error and never an empty result just
    because a creator_id was passed. This can never change what a
    creator_id=None caller sees, and a creator_id with no slot behaves
    identically to passing no creator_id at all.
    """
    saved = _saved_tokens()

    if creator_id:
        slot = saved.get("by_creator", {}).get(creator_id) if isinstance(saved, dict) else None
        if isinstance(slot, dict):
            token = _find_token(slot, ["accessToken", "access_token", "token"])
            if token:
                return token, "saved_oauth_file_by_creator"

    token = _find_token(saved, ["accessToken", "access_token", "token"])
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
