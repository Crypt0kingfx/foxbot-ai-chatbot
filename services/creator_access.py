"""FoxBot creator trial and Blaze-subscription access records.

This module intentionally stores access fields in the existing
data/connected_creators.json registry so FoxBot has one creator record source.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

TRIAL_DAYS = 7
GRACE_DAYS = 3
DATA_PATH = Path("data") / "connected_creators.json"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def clean_handle(value: Any) -> str:
    text = str(value or "").strip().lstrip("@").strip()
    return "".join(ch for ch in text if ch.isalnum() or ch in "_-." )[:64]


def _load_document() -> dict[str, Any]:
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not DATA_PATH.exists():
        return {}
    try:
        value = json.loads(DATA_PATH.read_text(encoding="utf-8") or "{}")
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _creator_map(document: dict[str, Any]) -> dict[str, Any]:
    wrapped = document.get("creators")
    if isinstance(wrapped, dict):
        return wrapped
    return document


def _save_document(document: dict[str, Any]) -> None:
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATA_PATH.write_text(
        json.dumps(document, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _find_key(creators: dict[str, Any], handle: str) -> str | None:
    wanted = clean_handle(handle).lower()
    for key in creators:
        if str(key).lower() == wanted:
            return str(key)
    return None


def _get_creator(document: dict[str, Any], handle: str) -> tuple[str | None, dict[str, Any] | None]:
    creators = _creator_map(document)
    key = _find_key(creators, handle)
    value = creators.get(key) if key is not None else None
    return key, value if isinstance(value, dict) else None


def _ensure_creator(document: dict[str, Any], handle: str, display_name: str | None = None) -> tuple[str, dict[str, Any]]:
    creators = _creator_map(document)
    clean = clean_handle(handle)
    key = _find_key(creators, clean) or clean
    creator = creators.get(key)
    if not isinstance(creator, dict):
        creator = {
            "handle": clean,
            "status": "connected",
            "commands": ["!join", "!connect", "!profile", "!rank", "!access", "!verify"],
            "badges": ["FoxBot Connected"],
            "foxcoins": 0,
            "stars": 0,
            "messages": 0,
            "connected_at": _iso(_now()),
        }
    creator["handle"] = creator.get("handle") or clean
    if display_name:
        creator["display_name"] = str(display_name).strip()[:80]
    creator["last_seen_at"] = _iso(_now())
    creators[key] = creator
    return key, creator


def access_snapshot(creator: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(creator, dict):
        return {
            "status": "not_started",
            "has_access": False,
            "remaining_days": 0,
            "trial_started_at": None,
            "trial_ends_at": None,
            "subscription_ends_at": None,
            "verification_status": "not_requested",
        }

    now = _now()
    trial_start = _parse(creator.get("trial_started_at"))
    trial_end = _parse(creator.get("trial_ends_at"))
    subscription_end = _parse(creator.get("subscription_ends_at"))
    revoked = bool(creator.get("access_revoked"))

    status = "expired"
    end = trial_end

    if revoked:
        status = "expired"
        end = subscription_end or trial_end
    elif subscription_end and now < subscription_end:
        status = "active"
        end = subscription_end
    elif subscription_end and now < subscription_end + timedelta(days=GRACE_DAYS):
        status = "grace"
        end = subscription_end + timedelta(days=GRACE_DAYS)
    elif trial_end and now < trial_end:
        status = "trialing"
        end = trial_end
    elif not trial_start:
        status = "not_started"
        end = None

    remaining_seconds = max(0.0, (end - now).total_seconds()) if end else 0.0
    remaining_days = int(math.ceil(remaining_seconds / 86400.0)) if remaining_seconds else 0

    return {
        "status": status,
        "has_access": status in {"trialing", "active", "grace"},
        "remaining_days": remaining_days,
        "trial_started_at": _iso(trial_start) if trial_start else None,
        "trial_ends_at": _iso(trial_end) if trial_end else None,
        "subscription_ends_at": _iso(subscription_end) if subscription_end else None,
        "verification_status": str(creator.get("subscription_verification_status") or "not_requested"),
        "channel_id": creator.get("channel_id"),
        "channel_slug": creator.get("channel_slug") or creator.get("handle"),
    }


def get_access(handle: str) -> dict[str, Any]:
    document = _load_document()
    _, creator = _get_creator(document, handle)
    result = access_snapshot(creator)
    result["handle"] = clean_handle(handle)
    return result


def start_trial(handle: str, display_name: str | None = None) -> dict[str, Any]:
    document = _load_document()
    _, creator = _ensure_creator(document, handle, display_name)
    started = False

    if not _parse(creator.get("trial_started_at")):
        now = _now()
        creator["trial_started_at"] = _iso(now)
        creator["trial_ends_at"] = _iso(now + timedelta(days=TRIAL_DAYS))
        creator["access_status"] = "trialing"
        creator["subscription_verification_status"] = "not_requested"
        creator["access_revoked"] = False
        badges = creator.setdefault("badges", [])
        if "FoxBot Trial" not in badges:
            badges.append("FoxBot Trial")
        started = True

    _save_document(document)
    result = access_snapshot(creator)
    result.update({"ok": True, "started": started, "handle": clean_handle(handle)})
    return result


def request_verification(handle: str) -> dict[str, Any]:
    document = _load_document()
    _, creator = _get_creator(document, handle)
    if not creator:
        return {"ok": False, "handle": clean_handle(handle), "error": "Trial not started."}

    creator["subscription_verification_status"] = "pending"
    creator["subscription_verification_requested_at"] = _iso(_now())
    _save_document(document)
    result = access_snapshot(creator)
    result.update({"ok": True, "handle": clean_handle(handle)})
    return result


def mark_subscriber(handle: str, days: int = 30, source: str = "blaze_subscriber_role") -> dict[str, Any]:
    document = _load_document()
    _, creator = _ensure_creator(document, handle)
    now = _now()
    current_end = _parse(creator.get("subscription_ends_at"))
    base = current_end if current_end and current_end > now else now
    creator["subscription_started_at"] = creator.get("subscription_started_at") or _iso(now)
    creator["subscription_ends_at"] = _iso(base + timedelta(days=max(1, int(days))))
    creator["subscription_verification_status"] = "verified"
    creator["subscription_verified_at"] = _iso(now)
    creator["subscription_verification_source"] = source
    creator["access_status"] = "active"
    creator["access_revoked"] = False
    badges = creator.setdefault("badges", [])
    if "FoxBot Subscriber" not in badges:
        badges.append("FoxBot Subscriber")
    _save_document(document)
    result = access_snapshot(creator)
    result.update({"ok": True, "handle": clean_handle(handle)})
    return result


def set_channel(handle: str, channel_id: str | None = None, channel_slug: str | None = None) -> dict[str, Any]:
    document = _load_document()
    _, creator = _ensure_creator(document, handle)
    if channel_id:
        creator["channel_id"] = str(channel_id).strip()
    if channel_slug:
        creator["channel_slug"] = clean_handle(channel_slug)
    _save_document(document)
    result = access_snapshot(creator)
    result.update({"ok": True, "handle": clean_handle(handle)})
    return result


def active_creators() -> list[dict[str, Any]]:
    document = _load_document()
    creators = _creator_map(document)
    rows: list[dict[str, Any]] = []
    for key, creator in creators.items():
        if not isinstance(creator, dict):
            continue
        access = access_snapshot(creator)
        if not access["has_access"]:
            continue
        rows.append({
            "handle": creator.get("handle") or key,
            "display_name": creator.get("display_name"),
            **access,
        })
    return rows


def list_access() -> list[dict[str, Any]]:
    document = _load_document()
    creators = _creator_map(document)
    rows: list[dict[str, Any]] = []
    for key, creator in creators.items():
        if not isinstance(creator, dict):
            continue
        rows.append({
            "handle": creator.get("handle") or key,
            "display_name": creator.get("display_name"),
            **access_snapshot(creator),
        })
    rows.sort(key=lambda row: str(row.get("handle") or "").lower())
    return rows

# === FoxBot Current Blaze Subscription Verification v1 ===
SUBSCRIPTION_ACCESS_DAYS = 30


def verify_current_subscription(
    handle: str,
    source: str = "foxbot_profile_subscriber_role",
) -> dict[str, Any]:
    """Grant a fresh subscription window after a live Blaze role check.

    Repeated checks reset the window from now; they never stack extra months.
    """
    document = _load_document()
    _, creator = _ensure_creator(document, handle)
    now = _now()
    creator["subscription_started_at"] = creator.get("subscription_started_at") or _iso(now)
    creator["subscription_ends_at"] = _iso(
        now + timedelta(days=SUBSCRIPTION_ACCESS_DAYS)
    )
    creator["subscription_verification_status"] = "verified"
    creator["subscription_verified_at"] = _iso(now)
    creator["subscription_verification_source"] = source
    creator["access_status"] = "active"
    creator["access_revoked"] = False
    badges = creator.setdefault("badges", [])
    if "FoxBot Subscriber" not in badges:
        badges.append("FoxBot Subscriber")
    _save_document(document)
    result = access_snapshot(creator)
    result.update({"ok": True, "handle": clean_handle(handle)})
    return result


# === End FoxBot Current Blaze Subscription Verification v1 ===

# === FoxBot Persistent Creator Registry v1 ===
from services.storage_paths import storage_path as _foxbot_access_storage_path_v1
from services.storage_paths import hydration_failed as _foxbot_access_hydration_failed_v1

DATA_PATH = _foxbot_access_storage_path_v1(
    "connected_creators.json",
    "FOXBOT_CONNECTED_CREATORS_FILE",
)


def _save_document(document: dict[str, Any]) -> None:
    """Atomically save creator access records to persistent storage."""
    import os

    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = DATA_PATH.with_suffix(DATA_PATH.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(document, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(temporary_path, DATA_PATH)


# === End FoxBot Persistent Creator Registry v1 ===

# === FoxBot Neon Creator Storage v1 ===
from services.postgres_state import save_json_state as _foxbot_save_creator_state_v1


def _load_document() -> dict[str, Any]:
    """Load creator access from Neon, migrating the JSON file once.

    Goes through storage_path() on every call (not load_or_migrate_json_state
    directly) instead of only once at import time via the module-level
    DATA_PATH assignment. storage_path() itself is a fast no-op once
    hydration has already succeeded, so this costs nothing extra in the
    common case -- but while it's failed, this is what gives it a retry on
    every creator lookup instead of staying stuck failed for the rest of the
    process. A successful retry here also clears the shared hydration-failed
    flag that _save_document() checks, unblocking its Neon mirror again.
    """
    _foxbot_access_storage_path_v1(
        "connected_creators.json",
        "FOXBOT_CONNECTED_CREATORS_FILE",
    )
    try:
        value = json.loads(DATA_PATH.read_text(encoding="utf-8") or "{}")
    except Exception:
        value = {}
    return value if isinstance(value, dict) else {}


def _save_document(document: dict[str, Any]) -> None:
    """Save locally for compatibility and atomically mirror into Neon.

    Skips the Neon mirror while the last hydration attempt for
    connected_creators.json is known to have failed -- writing then risks
    overwriting a good row with a document built from a possibly-incomplete
    local read. The local copy still saves normally either way.

    Retries the hydration gate itself here too (not just relying on a
    preceding _load_document() call in the same request) so a save-only
    caller still self-heals instead of staying stuck refused forever after
    one transient failure.
    """
    import os

    was_hydration_failed = _foxbot_access_hydration_failed_v1(
        "connected_creators.json"
    )

    _foxbot_access_storage_path_v1(
        "connected_creators.json",
        "FOXBOT_CONNECTED_CREATORS_FILE",
    )

    if was_hydration_failed and not _foxbot_access_hydration_failed_v1(
        "connected_creators.json"
    ):
        print(
            "Creator access save skipped: Postgres hydration recovered during "
            "the save; discarded a document built from pre-recovery state."
        )
        return

    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = DATA_PATH.with_suffix(DATA_PATH.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(document, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(temporary_path, DATA_PATH)

    if _foxbot_access_hydration_failed_v1("connected_creators.json"):
        return

    _foxbot_save_creator_state_v1("connected_creators", document)


# === End FoxBot Neon Creator Storage v1 ===
