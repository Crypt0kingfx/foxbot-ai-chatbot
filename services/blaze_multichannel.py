"""Blaze channel discovery and active FoxBot creator targets.

This module does not run listener threads. It builds a safe, deduplicated
target list for creators who currently have trial or subscription access.
"""

from __future__ import annotations

from typing import Any

import requests

from services import creator_access

BLAZE_CHANNELS_URL = "https://api.blaze.stream/v1/channels"


def _clean(value: Any) -> str:
    return str(value or "").strip()


def resolve_channel(
    slug: str,
    client_id: str,
    access_token: str,
    timeout: int = 15,
) -> dict[str, Any]:
    """Resolve one public Blaze channel slug to its channel ID."""
    clean_slug = creator_access.clean_handle(slug)
    if not clean_slug:
        return {"ok": False, "slug": clean_slug, "error": "Missing channel slug."}
    if not client_id or not access_token:
        return {
            "ok": False,
            "slug": clean_slug,
            "error": "Missing Blaze client ID or access token.",
        }

    try:
        response = requests.get(
            BLAZE_CHANNELS_URL,
            headers={
                "Authorization": f"Bearer {access_token}",
                "client-id": client_id,
                "Accept": "application/json",
            },
            params={"limit": 20, "type": "all", "slug[]": clean_slug},
            timeout=max(3, int(timeout)),
        )
        try:
            payload = response.json()
        except Exception:
            payload = None

        if not response.ok:
            return {
                "ok": False,
                "slug": clean_slug,
                "status_code": response.status_code,
                "error": "Blaze channel lookup failed.",
            }

        rows = []
        if isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, dict) and isinstance(data.get("rows"), list):
                rows = data["rows"]
            elif isinstance(payload.get("rows"), list):
                rows = payload["rows"]

        wanted = clean_slug.lower()
        row = next(
            (
                item
                for item in rows
                if isinstance(item, dict)
                and _clean(item.get("slug")).lower() == wanted
            ),
            rows[0] if rows else None,
        )
        if not isinstance(row, dict) or not row.get("id"):
            return {
                "ok": False,
                "slug": clean_slug,
                "error": "Blaze channel was not found.",
            }

        return {
            "ok": True,
            "channel_id": _clean(row.get("id")),
            "channel_slug": _clean(row.get("slug")) or clean_slug,
        }
    except Exception as error:
        return {"ok": False, "slug": clean_slug, "error": str(error)}


def build_targets(
    client_id: str,
    access_token: str,
    default_channel_id: str | None = None,
    default_channel_slug: str | None = None,
    limit: int = 25,
) -> dict[str, Any]:
    """Return owner plus currently authorized creator channel targets."""
    targets: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    owner_id = _clean(default_channel_id)
    owner_slug = creator_access.clean_handle(default_channel_slug)
    if owner_id:
        targets.append(
            {
                "channel_id": owner_id,
                "channel_slug": owner_slug,
                "handle": owner_slug or "foxbot-owner",
                "access_status": "owner",
                "is_owner_channel": True,
            }
        )
        seen_ids.add(owner_id)

    creators = creator_access.active_creators()
    safe_limit = max(1, min(int(limit or 25), 100))

    for creator in creators[:safe_limit]:
        handle = creator_access.clean_handle(creator.get("handle"))
        slug = creator_access.clean_handle(creator.get("channel_slug") or handle)
        channel_id = _clean(creator.get("channel_id"))

        if not channel_id:
            resolved = resolve_channel(slug, client_id, access_token)
            if resolved.get("ok"):
                channel_id = _clean(resolved.get("channel_id"))
                slug = creator_access.clean_handle(resolved.get("channel_slug") or slug)
                creator_access.set_channel(handle, channel_id, slug)
            else:
                unresolved.append(
                    {
                        "handle": handle,
                        "channel_slug": slug,
                        "access_status": creator.get("status"),
                        "error": resolved.get("error"),
                        "status_code": resolved.get("status_code"),
                    }
                )
                continue

        if not channel_id or channel_id in seen_ids:
            continue

        seen_ids.add(channel_id)
        targets.append(
            {
                "channel_id": channel_id,
                "channel_slug": slug,
                "handle": handle,
                "access_status": creator.get("status"),
                "remaining_days": creator.get("remaining_days", 0),
                "is_owner_channel": False,
            }
        )

    return {
        "ok": True,
        "targets": targets,
        "unresolved": unresolved,
        "active_creator_count": len(creators),
        "target_count": len(targets),
        "limit": safe_limit,
    }