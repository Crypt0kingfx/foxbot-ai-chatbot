

# === FoxBot Force Shop Emoji Response v1 ===
def foxbot_clean_shop_response_v1():
    return (
        "FoxBot Reward Shop: "
        "hug 10 | "
        "hype 25 | "
        "flex 50 | "
        "mysterybox 75 | "
        "sponsor 150 | "
        "Use !redeem rewardname"
    )
# === End FoxBot Force Shop Emoji Response v1 ===

from fastapi import Request

# === FoxBot Request Payload Type Aliases v1 ===
# Shared aliases used by connected-creator route signatures below.
from typing import Any as _FoxAny

_FoxDict = dict
# === End FoxBot Request Payload Type Aliases v1 ===



from services import blaze_listener

from services.recognition_engine import studio_recognition_response as service_studio_recognition_response

from models.studio_state import (

    STUDIO_STATE,

    BLAZE_LISTENER_STATE,

    RECOGNITION_HISTORY,

    SUPPORT_REWARDS,

    RECOGNITION_TEMPLATES,

    BLAZE_EVENT_MAP,

    studio_log,

    add_foxcoins

)

import os

import json

import random

import threading

import time

from datetime import date



import requests

from dotenv import load_dotenv

from fastapi import FastAPI

from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from services.storage_paths import storage_path as _foxbot_storage_path_v1
from services.storage_paths import hydration_failed as _foxbot_hydration_failed_v1
from services import foxbot_events as _foxbot_events_v1
from services.blaze_tokens import resolve_blaze_access_token
from services.blaze_tokens import sync_tenant_zero_slot as _foxbot_blaze_oauth_sync_tenant_zero_slot_v1

from fastapi.staticfiles import StaticFiles



load_dotenv()



app = FastAPI()



app.mount("/static", StaticFiles(directory="static", check_dir=False), name="static")



# ----------------------------

# App state

# ----------------------------



giveaway_entries = []



BLAZE_CLIENT_ID = os.environ.get("BLAZE_CLIENT_ID", "")

BLAZE_CLIENT_SECRET = os.environ.get("BLAZE_CLIENT_SECRET", "")



bot_tokens = {}



polling_thread = None



_blaze_oauth_refresh_thread = None



polling_status = {

    "running": False,

    "started_at": None,

    "checks": 0,

    "messages_seen": 0,

    "commands_processed": 0,

    "last_error": None,

    "last_response": None,

    "last_message": None

}



processed_polling_messages = set()



# === TEMP DIAGNOSTIC — remove once a real payload has been captured ===
# Captures the raw chat item whenever find_chat_username() falls through to
# "viewer" on a vote/follow auto-event (the @viewer thank-you bug). Purely
# diagnostic -- no fallback/recognition behavior depends on this list.
# Persisted (small, capped) so a redeploy doesn't wipe it before we catch
# one; see _foxbot_capture_viewer_fallback_debug_v1 and its call site in
# _foxbot_process_channel_rows_v1.
viewer_fallback_debug_log = []
VIEWER_FALLBACK_DEBUG_LOG_CAP = 20
# === End TEMP DIAGNOSTIC ===



viewer_stats = {"by_creator": {}}



bot_mode = os.getenv("FOXBOT_MODE", "hype").lower()



custom_commands = {"by_creator": {}}



stream_info = {

    "game": os.getenv("STREAM_GAME", "Off The Grid"),

    "title": os.getenv("STREAM_TITLE", "FoxBot is live on Blaze!"),

    "lurkers": {}

}



arcade_stats = {

    "plays": 0,

    "coinflip": 0,

    "roll": 0,

    "eightball": 0,

    "rps": 0,

    "rps_wins": 0,

    "rps_losses": 0,

    "rps_ties": 0,

    "foxhunt": 0

}



# Phase 1 multi-tenant economy migration: real per-creator IDs land in a
# later phase (bot-connection territory). Until then, every call implicitly
# targets this one tenant. See docs/phase-1-economy-migration.md.
FOXBOT_TENANT_ZERO_CREATOR_ID = os.getenv("FOXBOT_TENANT_ZERO_CREATOR_ID", "").strip()

foxcoin_economy = {

    "currency_name": os.getenv("POINTS_NAME", "FoxCoins"),

    "by_creator": {}

}



support_rewards = {

    "new_sub": 500,

    "gift_sub": 500,

    "tip_per_dollar": 200,

    "minimum_tip": 1,

    "vote_token": 3,

    "follow": 100,

    "raid": 250,

    "chat_message": 10

}



# Ceilings on auto-detected recognition awards. RECOGNITION_UNIT_CAPS
# bounds the parsed count/amount before multiplying -- a chat-inferred
# "gifted 999999 subs" is not a real gift-sub burst. RECOGNITION_MAX_REWARD
# bounds the final computed reward regardless of multiplier or per-unit
# config, so a large support_rewards value can't slip a huge award through
# the per-unit cap alone.
RECOGNITION_UNIT_CAPS = {

    "giftsub": 50,

    "vote": 50,

    "tip": 500,

}

RECOGNITION_MAX_REWARD = 5000



recognition_settings = {

    "enabled": True,

    "surprise_bonus_enabled": True,

    "surprise_bonus_chance": 15

}



recognition_log = []





fox_spirit_ranks = [

    {"name": "Fox Pup", "minimum": 0},

    {"name": "Fox Hunter", "minimum": 250},

    {"name": "Fox Warrior", "minimum": 750},

    {"name": "Fox Elder", "minimum": 1500},

    {"name": "Fox Spirit", "minimum": 3000},

    {"name": "Fox King", "minimum": 5000}

]



stream_event = {

    "active": False,

    "name": None,

    "key": None,

    "description": None,

    "claimed": {}

}



community_quest = {

    "active": False,

    "type": None,

    "goal": 0,

    "progress": 0,

    "reward": 100,

    "claimed": {},

    "completed": False

}



viewer_streaks = {"by_creator": {}}



stream_event_templates = {

    "goldenfox": {

        "name": "Golden Fox",

        "description": "Fox Hunt rewards are doubled while Golden Fox is active.",

        "claim_reward": 50

    },

    "spiritstorm": {

        "name": "Spirit Storm",

        "description": "Boss attacks earn bonus FoxCoins while Spirit Storm is active.",

        "claim_reward": 35

    },

    "treasuredrop": {

        "name": "Treasure Drop",

        "description": "Everyone can type !event to claim bonus FoxCoins.",

        "claim_reward": 75

    },

    "foxfrenzy": {

        "name": "Fox Frenzy",

        "description": "Arcade mini games bring bonus FoxCoins.",

        "claim_reward": 40

    }

}



reward_shop = {

    "hug": {

        "cost": 10,

        "response": "@{username} redeemed a FoxBot hug from the shop!"

    },

    "hype": {

        "cost": 25,

        "response": "@{username} redeemed HYPE MODE energy for the chat!"

    },

    "flex": {

        "cost": 50,

        "response": "@{username} redeemed a FoxBot flex. Big creator energy!"

    },

    "mysterybox": {

        "cost": 75,

        "response": "@{username} opened a mystery box!"

    },

    "sponsor": {

        "cost": 150,

        "response": "@{username} redeemed a fake sponsor read: This stream is powered by FoxCoins!"

    }

}



redemption_queue = []



cooldown_settings = {

    "!foxhunt": 30,

    "!coinflip": 5,

    "!roll": 5,

    "!8ball": 10,

    "!rps": 5,

    "!redeem": 15,

    "!daily": 60,

    "!lurk": 30,

    "!attack": 5,

    "!powerattack": 10

}



cooldown_tracker = {}



boss_battle = {

    "active": False,

    "name": "Cyber Fox Dragon",

    "max_hp": 500,

    "hp": 0,

    "damage_log": {},

    "defeated_count": 0,

    "last_winner": None

}





DATA_FILE = os.getenv("FOXBOT_DATA_FILE", "foxbot_data.json")





def get_persistent_snapshot():

    return {

        "bot_mode": globals().get("bot_mode", "hype"),

        "custom_commands": globals().get("custom_commands", {}),

        "stream_info": globals().get("stream_info", {}),

        "arcade_stats": globals().get("arcade_stats", {}),

        "foxcoin_economy": globals().get("foxcoin_economy", {}),

        "support_rewards": globals().get("support_rewards", {}),

        "recognition_settings": globals().get("recognition_settings", {}),

        "recognition_log": globals().get("recognition_log", []),

        "fox_spirit_ranks": globals().get("fox_spirit_ranks", []),

        "stream_event": globals().get("stream_event", {}),

        "stream_event_templates": globals().get("stream_event_templates", {}),

        "community_quest": globals().get("community_quest", {}),

        "viewer_streaks": globals().get("viewer_streaks", {}),

        "reward_shop": globals().get("reward_shop", {}),

        "redemption_queue": globals().get("redemption_queue", []),

        "cooldown_settings": globals().get("cooldown_settings", {}),

        "boss_battle": globals().get("boss_battle", {}),

        # TEMP DIAGNOSTIC key -- see viewer_fallback_debug_log definition.
        # Safe to delete this key (and the one in apply_persistent_snapshot)
        # once the @viewer thank-you bug fix is designed and shipped.
        "viewer_fallback_debug_log_TEMP": globals().get("viewer_fallback_debug_log", [])

    }





def apply_persistent_snapshot(data):

    global bot_mode

    global custom_commands

    global stream_info

    global arcade_stats

    global foxcoin_economy

    global support_rewards

    global recognition_settings

    global recognition_log

    global fox_spirit_ranks

    global stream_event

    global stream_event_templates

    global community_quest

    global viewer_streaks

    global reward_shop

    global redemption_queue

    global cooldown_settings

    global cooldown_tracker

    global boss_battle

    global viewer_fallback_debug_log



    if not isinstance(data, dict):

        return False



    if isinstance(data.get("bot_mode"), str):

        bot_mode = data["bot_mode"].lower()



    if isinstance(data.get("custom_commands"), dict):

        custom_commands = data["custom_commands"]

        # Unlike foxcoin_economy/viewer_streaks, this store's hydration is
        # a wholesale reassignment (custom_commands = data[...]), not an
        # in-place .update() -- so the module-level custom_commands =
        # {"by_creator": {}} default at declaration time provides no
        # protection here; the rebound object needs its own setdefault
        # every time this runs, including on the mid-process hydration-
        # recovery path (see load_persistent_data()'s caller), not just
        # at startup. This is permanent, not migration scaffolding --
        # leave it in place even if custom_commands ever needs another
        # cleanup pass.
        custom_commands.setdefault("by_creator", {})



    if isinstance(data.get("stream_info"), dict):

        stream_info = data["stream_info"]

        stream_info.setdefault("game", os.getenv("STREAM_GAME", "Off The Grid"))

        stream_info.setdefault("title", os.getenv("STREAM_TITLE", "FoxBot is live on Blaze!"))

        stream_info.setdefault("lurkers", {})



    if isinstance(data.get("arcade_stats"), dict):

        arcade_stats.update(data["arcade_stats"])



    if isinstance(data.get("fox_spirit_ranks"), list):

        fox_spirit_ranks = data["fox_spirit_ranks"]



    if isinstance(data.get("stream_event"), dict):

        stream_event.update(data["stream_event"])

        stream_event.setdefault("active", False)

        stream_event.setdefault("name", None)

        stream_event.setdefault("key", None)

        stream_event.setdefault("description", None)

        stream_event.setdefault("claimed", {})



    if isinstance(data.get("stream_event_templates"), dict):

        stream_event_templates.update(data["stream_event_templates"])



    if isinstance(data.get("viewer_streaks"), dict):

        viewer_streaks.update(data["viewer_streaks"])

        viewer_streaks.setdefault("by_creator", {})



    if isinstance(data.get("community_quest"), dict):

        community_quest.update(data["community_quest"])

        community_quest.setdefault("active", False)

        community_quest.setdefault("type", None)

        community_quest.setdefault("goal", 0)

        community_quest.setdefault("progress", 0)

        community_quest.setdefault("reward", 100)

        community_quest.setdefault("claimed", {})

        community_quest.setdefault("completed", False)



    if isinstance(data.get("recognition_settings"), dict):

        recognition_settings.update(data["recognition_settings"])



    if isinstance(data.get("recognition_log"), list):

        recognition_log[:] = data["recognition_log"][:25]



    if isinstance(data.get("support_rewards"), dict):

        support_rewards.update(data["support_rewards"])



    if isinstance(data.get("foxcoin_economy"), dict):

        foxcoin_economy.update(data["foxcoin_economy"])

        foxcoin_economy.setdefault("currency_name", os.getenv("POINTS_NAME", "FoxCoins"))

        foxcoin_economy.setdefault("by_creator", {})



    if isinstance(data.get("reward_shop"), dict):

        reward_shop = data["reward_shop"]



    if isinstance(data.get("redemption_queue"), list):

        redemption_queue = data["redemption_queue"][:10]



    if isinstance(data.get("cooldown_settings"), dict):

        cooldown_settings.update(data["cooldown_settings"])



    if isinstance(data.get("boss_battle"), dict):

        boss_battle.update(data["boss_battle"])

        boss_battle.setdefault("active", False)

        boss_battle.setdefault("name", "Cyber Fox Dragon")

        boss_battle.setdefault("max_hp", 500)

        boss_battle.setdefault("hp", 0)

        boss_battle.setdefault("damage_log", {})

        boss_battle.setdefault("defeated_count", 0)

        boss_battle.setdefault("last_winner", None)



    # TEMP DIAGNOSTIC restore -- see viewer_fallback_debug_log definition.
    if isinstance(data.get("viewer_fallback_debug_log_TEMP"), list):
        viewer_fallback_debug_log[:] = data["viewer_fallback_debug_log_TEMP"][:VIEWER_FALLBACK_DEBUG_LOG_CAP]



    return True





_foxbot_last_saved_snapshot_json_v1 = None


def _foxbot_describe_discarded_state_v1(before, after, path=""):

    # Reports which keys/entries were present in `before` and are gone or
    # changed in `after` -- key names and counts only, never values, so
    # this is safe to print (custom command names are identifiers a creator
    # set up, not secrets, but the response text / balances / etc. behind
    # them stay out of the log).

    notes = []

    if isinstance(before, dict) and isinstance(after, dict):

        dropped = sorted(str(key) for key in (before.keys() - after.keys()))

        if dropped:

            notes.append(f"{path or 'snapshot'}: dropped keys {dropped}")

        for key in (before.keys() & after.keys()):

            notes.extend(
                _foxbot_describe_discarded_state_v1(
                    before[key], after[key], f"{path}.{key}" if path else str(key)
                )
            )

    elif isinstance(before, list) and isinstance(after, list):

        if len(before) != len(after):

            notes.append(f"{path}: {len(before)} entries before reload vs {len(after)} after")

    else:

        if before != after:

            notes.append(f"{path}: value replaced by reload")

    return notes


def save_persistent_data():

    global _foxbot_last_saved_snapshot_json_v1

    try:

        # Calling this on every save (not just on a real write) lets a
        # previously-failed Postgres hydration retry on the next request
        # instead of staying stuck failed for the rest of the process.
        was_hydration_failed = _foxbot_hydration_failed_v1("foxbot_data.json")

        path = _foxbot_storage_path_v1("foxbot_data.json", "FOXBOT_DATA_FILE")

        if was_hydration_failed and not _foxbot_hydration_failed_v1("foxbot_data.json"):

            # Hydration just recovered on the call above. In-memory globals
            # were never updated while it was failing (refusing to write
            # doesn't refresh them), so they're still whatever they were
            # during the outage -- almost certainly defaults, since this
            # path only matters when hydration never succeeded yet this
            # process. Reload from the freshly-hydrated file before
            # trusting them as the basis for a write, or this save would
            # immediately overwrite the just-recovered Postgres row with
            # that stale in-memory state. Known trade-off: any in-memory-only
            # edits made during the outage window are discarded in favor of
            # the recovered row, since there's no version/timestamp to
            # arbitrate between them. Not silent, though -- log exactly
            # what got dropped so a creator whose command/reward/balance
            # vanished has something in the logs pointing at why.
            #
            # apply_persistent_snapshot() mutates some of these globals in
            # place (e.g. foxcoin_economy.update(...)) rather than rebinding
            # them, so a plain reference to get_persistent_snapshot()'s
            # result would get silently mutated out from under this
            # comparison once load_persistent_data() runs. Deep-copy via a
            # JSON round-trip (this data is already known JSON-safe) so
            # "before" actually stays before.
            before_reload = json.loads(json.dumps(get_persistent_snapshot()))

            load_persistent_data()

            discarded = _foxbot_describe_discarded_state_v1(before_reload, get_persistent_snapshot())

            if discarded:

                print(
                    "FoxBot data recovery reload discarded in-memory-only state "
                    "made during the Postgres outage (values omitted): "
                    + "; ".join(discarded)
                )

        data = get_persistent_snapshot()

        serialized = json.dumps(data, indent=2)

        if serialized == _foxbot_last_saved_snapshot_json_v1:

            return True

        if _foxbot_hydration_failed_v1("foxbot_data.json"):

            print("FoxBot data save skipped: last Postgres hydration for foxbot_data failed; refusing to write until a hydration succeeds, to avoid overwriting a good row with incomplete local state.")

            return False

        path.write_text(serialized, encoding="utf-8")

        _foxbot_last_saved_snapshot_json_v1 = serialized

        return True

    except Exception as exc:

        print(f"FoxBot data save failed: {exc}")

        return False





def load_persistent_data():

    global _foxbot_last_saved_snapshot_json_v1

    path = _foxbot_storage_path_v1("foxbot_data.json", "FOXBOT_DATA_FILE")

    if not path.exists():

        print("FoxBot data file not found. Starting fresh.")

        return False



    try:

        data = json.loads(path.read_text(encoding="utf-8"))



        loaded = apply_persistent_snapshot(data)



        if loaded:

            _foxbot_last_saved_snapshot_json_v1 = json.dumps(get_persistent_snapshot(), indent=2)

            print(f"FoxBot data loaded from {path}")



        return loaded

    except Exception as exc:

        print(f"FoxBot data load failed: {exc}")

        return False





load_persistent_data()





@app.middleware("http")

async def foxbot_auto_save_middleware(request, call_next):

    response = await call_next(request)



    try:

        if not request.url.path.startswith("/static"):

            save_persistent_data()

    except Exception as exc:

        print(f"FoxBot auto-save middleware failed: {exc}")



    return response



# === FoxBot Studio Admin Auth Gate v1 ===

FOXBOT_ADMIN_GATED_EXACT_PATHS = {

    "/studio", "/studio-v2", "/admin", "/legacy-admin", "/foxbot-control",
    "/dashboard",

    "/api/foxbot/admin-command", "/chat", "/save-data", "/data-status",
    "/project-status", "/smoke-test", "/proof",

    "/api/blaze/event-bridge", "/api/blaze/parse-auto-event",
    "/api/blaze/test-auto-chat-event", "/api/blaze/event",
    "/api/foxbot/events", "/api/foxbot/onboarding",

    # /api/blaze/service-test doesn't match the "/api/blaze/service/" prefix
    # below (no trailing slash before "-test"), so it fell through the gate
    # entirely despite triggering a real side effect (blaze_listener.connect()).
    # Listed here explicitly rather than fixing the prefix, since the prefix
    # is correct for the real /api/blaze/service/* routes it's meant to match.
    "/api/blaze/service-test",

    "/foxcoins", "/viewer-stats", "/arcade-stats", "/rewards",
    "/recognition", "/community-quest", "/streaks", "/custom-commands",

    "/api/connected-creators/demo",

}

FOXBOT_ADMIN_GATED_PREFIXES = (

    "/api/studio/", "/api/automation/",
    "/api/blaze/native/", "/api/blaze/oauth/", "/api/blaze/service/",
    "/api/blaze/listener/", "/api/recognition/", "/blaze/",

)

# Paths that would otherwise match a gated prefix above, but are fetched
# directly by a public /overlay/* page (OBS browser source — can't answer
# a Basic Auth prompt). Carved out of /api/studio/ specifically so the
# rest of that prefix (action dispatch, activity clear/demo, the other
# /api/studio/giveaways/* admin writes) stays gated.
FOXBOT_ADMIN_PUBLIC_EXCEPTIONS = {

    "/api/studio/giveaways/status",

}


def _foxbot_studio_path_is_gated(path: str) -> bool:

    normalized = path.rstrip("/") or "/"

    if normalized in FOXBOT_ADMIN_PUBLIC_EXCEPTIONS:
        return False

    if normalized in FOXBOT_ADMIN_GATED_EXACT_PATHS:
        return True

    # /api/connected-creators/{handle}/foxcoins mints FoxCoins for an
    # arbitrary handle and has no admin check of its own. /message has the
    # same shape -- records an attacker-controlled `amount` of messages
    # against any handle, no admin check either. Both handle segments are
    # dynamic, so neither can go in the exact-path set above -- matched by
    # suffix instead. Everything else under /api/connected-creators/ (list,
    # connect, chat-test, me) stays public; connect is the creator
    # self-registration path and must never be gated.
    if normalized.startswith("/api/connected-creators/") and normalized.endswith(("/foxcoins", "/message")):
        return True

    return any(normalized.startswith(prefix.rstrip("/")) for prefix in FOXBOT_ADMIN_GATED_PREFIXES)


def _foxbot_require_admin_v1(request: Request):
    """Bot Connection C2, Step 2: second-layer scope check, layered AFTER
    the existing auth gate (foxbot_studio_admin_auth_gate_v1, immediately
    below) -- that gate already required Basic Auth or an approved Blaze
    session before any route body using this helper is ever reached; this
    only narrows WHICH of those already-approved sessions may proceed.
    request.state.is_admin is set by that same gate (Bot Connection C2,
    Step 0) -- True for Basic Auth or tenant-zero's own Blaze session,
    False for an approved-but-non-tenant-zero (scoped) creator.

    Usage at the top of a route body:
        guard = _foxbot_require_admin_v1(request)
        if guard:
            return guard

    Apply only to posting/infra/unmigrated-shared-state routes that must
    stay admin-only for now -- never to Tier 1 creator-data routes, which
    scoped creators must be able to reach.
    """
    from fastapi.responses import JSONResponse

    if not getattr(request.state, "is_admin", False):
        return JSONResponse(
            {"ok": False, "error": "This action requires full admin access, not a scoped creator session."},
            status_code=403,
        )
    return None


@app.middleware("http")
async def foxbot_studio_admin_auth_gate_v1(request, call_next):

    if _foxbot_studio_path_is_gated(request.url.path):

        import base64
        import secrets
        from fastapi.responses import JSONResponse, Response

        auth_mode = os.getenv("STUDIO_AUTH_MODE", "both").strip().lower()
        authorized = False

        # Blaze dashboard session -- checked first, purely additive. Never
        # replaces Basic Auth below; both stay live simultaneously while
        # STUDIO_AUTH_MODE=both (the default). See
        # docs/blaze-dashboard-auth-plan.md and foxbot_dashboard_callback_v1.
        # Approval is re-derived from STUDIO_APPROVED_BLAZE_USER_IDS on
        # every request (not baked into the cookie), so removing someone
        # from the allowlist takes effect without needing to invalidate
        # any already-issued session cookie.
        if auth_mode in ("blaze", "both"):
            session_token = request.cookies.get("foxbot_dashboard_session")
            if session_token:
                identity = _foxbot_dashboard_session_verify_v1(session_token)
                if identity and _foxbot_dashboard_user_is_approved_v1(identity.get("blaze_id")):
                    authorized = True
                    # Bot Connection Sub-phase D, stage 6: stash the
                    # verified blaze_id so a downstream route can resolve
                    # its own creator identity (request.state is a plain
                    # per-request namespace FastAPI/Starlette already
                    # provides -- this is the only place anything writes
                    # to it). Only set on a genuinely successful,
                    # allowlist-approved Blaze session; never a
                    # caller-supplied value. Basic-Auth-only requests
                    # never reach this branch, so request.state.blaze_id
                    # stays unset for them -- downstream code must read it
                    # via getattr(request.state, "blaze_id", None), never
                    # assume it exists.
                    request.state.blaze_id = identity.get("blaze_id")

        if not authorized and auth_mode in ("basic", "both"):
            expected_user = os.getenv("STUDIO_ADMIN_USER")
            expected_password = os.getenv("STUDIO_ADMIN_PASSWORD")

            if not expected_user or not expected_password:
                return JSONResponse(
                    {"ok": False, "error": "Studio admin auth is not configured (STUDIO_ADMIN_USER / STUDIO_ADMIN_PASSWORD unset)."},
                    status_code=503,
                )

            auth_header = request.headers.get("authorization", "")

            if auth_header.lower().startswith("basic "):
                try:
                    decoded = base64.b64decode(auth_header.split(" ", 1)[1]).decode("utf-8")
                    supplied_user, _, supplied_password = decoded.partition(":")
                except Exception:
                    supplied_user, supplied_password = "", ""

                authorized = (
                    secrets.compare_digest(supplied_user, expected_user)
                    and secrets.compare_digest(supplied_password, expected_password)
                )

        if not authorized:
            return Response(
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="FoxBot Studio Admin"'},
            )

        # Bot Connection C2, Step 0: is_admin / scoped_creator_id, computed
        # once the auth outcome above is final -- purely additive, nothing
        # downstream reads either field yet, so this changes no existing
        # behavior. Basic Auth (blaze_id never set above) and a Blaze
        # session whose blaze_id IS tenant-zero's own both mean full admin,
        # exactly like today's single "authorized" bit already treated
        # them -- unchanged. Only a Blaze session whose blaze_id is NOT
        # tenant-zero's own is a genuinely scoped creator: request.state.blaze_id
        # is Blaze-verified (see the comment above where it's set), so
        # scoped_creator_id inherits that same guarantee -- never a
        # caller-supplied value.
        session_blaze_id = getattr(request.state, "blaze_id", None)
        if session_blaze_id and session_blaze_id != _tenant_zero_id():
            request.state.is_admin = False
            request.state.scoped_creator_id = session_blaze_id
        else:
            request.state.is_admin = True
            request.state.scoped_creator_id = None

    return await call_next(request)

# === End FoxBot Studio Admin Auth Gate v1 ===





proof_stats = {

    "blaze_connected": False,

    "channel_id": os.getenv("BLAZE_CHANNEL_ID"),

    "channel_slug": os.getenv("BLAZE_CHANNEL_SLUG"),

    "listener_running": False,

    "messages_checked": 0,

    "messages_seen": 0,

    "commands_processed": 0,

    "last_command": None,

    "last_reply": None,

    "last_username": None,

    "last_message": None,

    "last_reply_at": None

}





# ----------------------------

# HTML pages

# ----------------------------



html_content = """

<!DOCTYPE html>

<html lang="en">

<head>

    <meta charset="UTF-8">

    <meta name="viewport" content="width=device-width, initial-scale=1.0">

    <title>FoxBot AI Chatbot</title>

    <style>

        * { box-sizing: border-box; }

        body {

            margin: 0;

            font-family: Arial, sans-serif;

            background: linear-gradient(135deg, #0b1020, #111827, #1f2937);

            color: white;

        }

        .page { min-height: 100vh; padding: 24px; }

        .app-shell {

            max-width: 1200px;

            margin: 0 auto;

            display: grid;

            grid-template-columns: 320px 1fr;

            gap: 20px;

        }

        .panel {

            background: rgba(17, 24, 39, 0.92);

            border: 1px solid rgba(255, 255, 255, 0.08);

            border-radius: 20px;

            box-shadow: 0 12px 40px rgba(0, 0, 0, 0.35);

        }

        .sidebar { padding: 22px; }

        .brand {

            display: flex;

            align-items: center;

            gap: 14px;

            margin-bottom: 20px;

        }

        .brand-logo {

            width: 70px;

            height: 70px;

            border-radius: 18px;

            object-fit: cover;

            border: 2px solid rgba(249, 115, 22, 0.35);

            box-shadow: 0 10px 28px rgba(249, 115, 22, 0.30);

            background: #111827;

        }

        .brand h1 { margin: 0; font-size: 28px; line-height: 1; }

        .brand p { margin: 6px 0 0; color: #cbd5e1; font-size: 14px; }

        .badge {

            display: inline-block;

            background: rgba(249, 115, 22, 0.16);

            color: #fdba74;

            border: 1px solid rgba(249, 115, 22, 0.3);

            padding: 7px 12px;

            border-radius: 999px;

            font-size: 13px;

            margin-bottom: 18px;

        }

        .section-title {

            margin: 20px 0 10px;

            font-size: 14px;

            color: #94a3b8;

            text-transform: uppercase;

            letter-spacing: 1px;

        }

        .info-card {

            background: rgba(255, 255, 255, 0.04);

            border-radius: 16px;

            padding: 14px;

            margin-bottom: 12px;

        }

        .info-card strong { display: block; margin-bottom: 6px; color: #ffffff; }

        .info-card span { color: #cbd5e1; font-size: 14px; line-height: 1.4; }

        .command-list { display: grid; gap: 10px; }

        .command-chip {

            background: rgba(255, 255, 255, 0.05);

            border-radius: 12px;

            padding: 10px 12px;

            color: #e5e7eb;

            font-size: 14px;

        }

        .main {

            padding: 22px;

            display: flex;

            flex-direction: column;

            min-height: 760px;

        }

        .topbar {

            display: flex;

            justify-content: space-between;

            align-items: center;

            gap: 16px;

            margin-bottom: 18px;

            flex-wrap: wrap;

        }

        .title-block h2 { margin: 0; font-size: 30px; }

        .title-block p { margin: 8px 0 0; color: #cbd5e1; }

        .status {

            background: rgba(34, 197, 94, 0.12);

            color: #86efac;

            border: 1px solid rgba(34, 197, 94, 0.28);

            padding: 10px 14px;

            border-radius: 999px;

            font-size: 14px;

        }

        .controls {

            display: grid;

            grid-template-columns: 1fr;

            gap: 14px;

            margin-bottom: 16px;

        }

        .username-box input {

            width: 100%;

            padding: 14px;

            border-radius: 14px;

            border: none;

            outline: none;

            font-size: 15px;

            background: #0f172a;

            color: white;

        }

        .quick-buttons { display: flex; flex-wrap: wrap; gap: 10px; }

        .quick-buttons button, .send-row button, .link-button {

            background: linear-gradient(135deg, #f97316, #ea580c);

            color: white;

            border: none;

            border-radius: 12px;

            padding: 12px 14px;

            cursor: pointer;

            font-size: 14px;

            font-weight: bold;

            text-decoration: none;

            display: inline-block;

        }

        .chat-box {

            flex: 1;

            background: #0f172a;

            border-radius: 18px;

            padding: 18px;

            overflow-y: auto;

            min-height: 420px;

            border: 1px solid rgba(255, 255, 255, 0.06);

        }

        .message {

            margin: 12px 0;

            padding: 14px 16px;

            border-radius: 16px;

            max-width: 78%;

            line-height: 1.5;

            white-space: pre-wrap;

            word-wrap: break-word;

        }

        .bot { background: #1f2937; color: #f8fafc; margin-right: auto; }

        .user {

            background: linear-gradient(135deg, #2563eb, #1d4ed8);

            color: white;

            margin-left: auto;

            text-align: right;

        }

        .send-row { display: flex; gap: 12px; margin-top: 16px; }

        .send-row input {

            flex: 1;

            padding: 15px;

            border-radius: 14px;

            border: none;

            outline: none;

            font-size: 15px;

            background: #0f172a;

            color: white;

        }

        .footer-note { margin-top: 14px; color: #94a3b8; font-size: 13px; text-align: center; }

        @media (max-width: 920px) {

            .app-shell { grid-template-columns: 1fr; }

            .main { min-height: auto; }

            .message { max-width: 90%; }

        }

    </style>

</head>

<body>

    <div class="page">

        <div class="app-shell">

            <div class="panel sidebar">

                <div class="brand">

                    <img src="/static/foxbot-logo.png" alt="FoxBot Logo" class="brand-logo">

                    <div>

                        <h1>FoxBot</h1>

                        <p>AI Chatbot Demo</p>

                    </div>

                </div>

                <div class="badge">Blaze Builder Challenge</div>



                <div class="section-title">What it does</div>

                <div class="info-card"><strong>Community Assistant</strong><span>Helps creators manage chat, answer common questions, and improve engagement.</span></div>

                <div class="info-card"><strong>Giveaway System</strong><span>Starts giveaways, tracks entries, blocks duplicate signups, and picks a winner.</span></div>

                <div class="info-card"><strong>Blaze Integration</strong><span>Uses Blaze OAuth and the Blaze Chat API to post and respond in real chat.</span></div>



                <div class="section-title">Pages</div>

                <a class="link-button" href="/dashboard">Dashboard</a>

                <a class="link-button" href="/judges">Judges Page</a>

                <a class="link-button" href="/features">Features</a>

                <a class="link-button" href="/project-status">Status</a>



                <div class="section-title">Commands</div>

                <div class="command-list">

                    <div class="command-chip">!foxhelp</div>

                    <div class="command-chip">!schedule</div>

                    <div class="command-chip">!faq</div>

                    <div class="command-chip">!socials</div>

                    <div class="command-chip">!mode</div>

                    <div class="command-chip">!commands</div>

                    <div class="command-chip">!arcade</div>

                    <div class="command-chip">!coinflip</div>

                    <div class="command-chip">!roll</div>

                    <div class="command-chip">!8ball</div>

                    <div class="command-chip">!rps</div>

                    <div class="command-chip">!game</div>

                    <div class="command-chip">!title</div>

                    <div class="command-chip">!lurk</div>

                    <div class="command-chip">!giveaway</div>

                    <div class="command-chip">!enter</div>

                    <div class="command-chip">!entries</div>

                    <div class="command-chip">!stats</div>

                    <div class="command-chip">!leaderboard</div>

                    <div class="command-chip">!pickwinner</div>

                    <div class="command-chip">!shoutout</div>

                    <div class="command-chip">!addcmd</div>

                    <div class="command-chip">!setgame</div>

                    <div class="command-chip">!settitle</div>

                    <div class="command-chip">!hugs</div>

                    <div class="command-chip">!ask</div>

                </div>

            </div>



            <div class="panel main">

                <div class="topbar">

                    <div class="title-block">

                        <h2>FoxBot AI Chatbot</h2>

                        <p>The ultimate Blaze creator assistant demo.</p>

                    </div>

                    <div class="status">Live Blaze Build</div>

                </div>



                <div class="controls">

                    <div class="username-box">

                        <input id="username" type="text" value="Ryan" placeholder="Enter your username">

                    </div>

                    <div class="quick-buttons">

                        <button onclick="sendQuickMessage('!foxhelp')">!foxhelp</button>

                        <button onclick="sendQuickMessage('!schedule')">!schedule</button>

                        <button onclick="sendQuickMessage('!faq')">!faq</button>

                        <button onclick="sendQuickMessage('!socials')">!socials</button>

                        <button onclick="sendQuickMessage('!mode')">!mode</button>

                        <button onclick="sendQuickMessage('!commands')">!commands</button>

                        <button onclick="sendQuickMessage('!arcade')">!arcade</button>

                        <button onclick="sendQuickMessage('!coinflip')">!coinflip</button>

                        <button onclick="sendQuickMessage('!roll 20')">!roll 20</button>

                        <button onclick="sendQuickMessage('!8ball Will FoxBot win?')">!8ball</button>

                        <button onclick="sendQuickMessage('!rps rock')">!rps</button>

                        <button onclick="sendQuickMessage('!game')">!game</button>

                        <button onclick="sendQuickMessage('!title')">!title</button>

                        <button onclick="sendQuickMessage('!lurk')">!lurk</button>

                        <button onclick="sendQuickMessage('!setgame Off The Grid')">set game</button>

                        <button onclick="sendQuickMessage('!settitle Playing Off The Grid with FoxBot live')">set title</button>

                        <button onclick="sendQuickMessage('!addcmd discord Join the Discord here: your-link')">add !discord</button>

                        <button onclick="sendQuickMessage('!mode hype')">hype mode</button>

                        <button onclick="sendQuickMessage('!giveaway')">!giveaway</button>

                        <button onclick="sendQuickMessage('!enter')">!enter</button>

                        <button onclick="sendQuickMessage('!entries')">!entries</button>

                        <button onclick="sendQuickMessage('!stats')">!stats</button>

                        <button onclick="sendQuickMessage('!leaderboard')">!leaderboard</button>

                        <button onclick="sendQuickMessage('!pickwinner')">!pickwinner</button>

                        <button onclick="sendQuickMessage('!shoutout avisi')">!shoutout</button>

                        <button onclick="sendQuickMessage('!hugs')">!hugs</button>

                        <button onclick="sendQuickMessage('!ask What does FoxBot do?')">!ask demo</button>

                    </div>

                </div>



                <div class="chat-box" id="chatBox">

                    <div class="message bot">Welcome to FoxBot. Try !foxhelp to see commands.</div>

                    <div class="message bot">FoxBot supports Blaze OAuth, chat posting, command replies, polling-based chat reading, and giveaway tools.</div>

                </div>



                <div class="send-row">

                    <input id="messageInput" type="text" placeholder="Type a command or message...">

                    <button onclick="sendMessage()">Send</button>

                </div>



                <div class="footer-note">FoxBot AI Chatbot for the Blaze Builder Challenge</div>

            </div>

        </div>

    </div>



    <script>

        async function sendMessage() {

            const input = document.getElementById("messageInput");

            const username = document.getElementById("username").value.trim() || "viewer";

            const message = input.value.trim();

            if (!message) return;



            addMessage(message, "user");

            input.value = "";



            try {

                const response = await fetch(`/chat?message=${encodeURIComponent(message)}&username=${encodeURIComponent(username)}`);

                const data = await response.json();

                addMessage(data.response, "bot");

            } catch (error) {

                addMessage("Error talking to FoxBot.", "bot");

            }

        }



        function sendQuickMessage(message) {

            document.getElementById("messageInput").value = message;

            sendMessage();

        }



        function addMessage(text, sender) {

            const chatBox = document.getElementById("chatBox");

            const messageDiv = document.createElement("div");

            messageDiv.className = `message ${sender}`;

            messageDiv.textContent = text;

            chatBox.appendChild(messageDiv);

            chatBox.scrollTop = chatBox.scrollHeight;

        }



        document.getElementById("messageInput").addEventListener("keypress", function(event) {

            if (event.key === "Enter") {

                sendMessage();

            }

        });

    </script>

</body>

</html>

"""



dashboard_html = """

<!DOCTYPE html>

<html lang="en">

<head>

    <meta charset="UTF-8">

    <meta name="viewport" content="width=device-width, initial-scale=1.0">

    <title>FoxBot Control Dashboard</title>

    <style>

        body {

            margin: 0;

            font-family: Arial, sans-serif;

            background: linear-gradient(135deg, #0b1020, #111827, #1f2937);

            color: white;

            padding: 30px;

        }

        .dashboard {

            max-width: 1000px;

            margin: 0 auto;

            background: rgba(17, 24, 39, 0.95);

            border: 1px solid rgba(255,255,255,0.08);

            border-radius: 22px;

            padding: 28px;

            box-shadow: 0 12px 40px rgba(0,0,0,0.35);

        }

        .brand { display: flex; align-items: center; gap: 16px; margin-bottom: 20px; }

        .brand img {

            width: 76px;

            height: 76px;

            border-radius: 18px;

            object-fit: cover;

            border: 2px solid rgba(249, 115, 22, 0.45);

        }

        h1 { margin: 0; font-size: 32px; }

        p { color: #cbd5e1; line-height: 1.5; }

        .grid {

            display: grid;

            grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));

            gap: 14px;

            margin-top: 24px;

        }

        button, a.button {

            display: block;

            text-align: center;

            text-decoration: none;

            background: linear-gradient(135deg, #f97316, #ea580c);

            color: white;

            border: none;

            border-radius: 14px;

            padding: 15px 16px;

            font-weight: bold;

            cursor: pointer;

            font-size: 15px;

        }

        .secondary { background: linear-gradient(135deg, #2563eb, #1d4ed8); }

        .danger { background: linear-gradient(135deg, #dc2626, #991b1b); }

        .proof-grid {

            display: grid;

            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));

            gap: 12px;

            margin-top: 24px;

        }

        .proof-card {

            background: #0f172a;

            border: 1px solid rgba(255,255,255,0.08);

            border-radius: 16px;

            padding: 16px;

        }

        .proof-card strong {

            display: block;

            color: #94a3b8;

            font-size: 13px;

            margin-bottom: 8px;

            text-transform: uppercase;

            letter-spacing: 0.5px;

        }

        .proof-card span {

            font-size: 20px;

            font-weight: bold;

            color: #fdba74;

        }

        .output {

            margin-top: 24px;

            background: #0f172a;

            border-radius: 16px;

            padding: 18px;

            min-height: 180px;

            white-space: pre-wrap;

            overflow-x: auto;

            border: 1px solid rgba(255,255,255,0.08);

            color: #e5e7eb;

        }

        .note { margin-top: 18px; color: #94a3b8; font-size: 14px; }

    </style>

</head>

<body>

    <div class="dashboard">

        <div class="brand">

            <img src="/static/foxbot-logo.png" alt="FoxBot Logo">

            <div>

                <h1>FoxBot Control Dashboard</h1>

                <p>Manage your Blaze-connected AI chatbot from one place.</p>

            </div>

        </div>



        <p>

            Use this dashboard to connect FoxBot to Blaze, start the chat listener,

            check status, and test real chat commands.

        </p>



        <div class="proof-grid">

            <div class="proof-card"><strong>Blaze Connected</strong><span id="proofConnected">Loading</span></div>

            <div class="proof-card"><strong>Listener</strong><span id="proofListener">Loading</span></div>

            <div class="proof-card"><strong>Messages Checked</strong><span id="proofChecks">0</span></div>

            <div class="proof-card"><strong>Commands Processed</strong><span id="proofCommands">0</span></div>

            <div class="proof-card"><strong>Last Command</strong><span id="proofLastCommand">None</span></div>

            <div class="proof-card"><strong>Last User</strong><span id="proofLastUser">None</span></div>

        </div>



        <div class="grid">

            <button onclick="callEndpoint('/blaze/start-polling-listener')">Start Listener</button>

            <button class="danger" onclick="callEndpoint('/blaze/stop-polling-listener')">Stop Listener</button>

            <button class="secondary" onclick="callEndpoint('/blaze/polling-status')">Check Status</button>

            <button class="secondary" onclick="callEndpoint('/blaze/check-recent-messages')">Check Recent Chat</button>

            <button onclick="callEndpoint('/blaze/send-test-message')">Send Test Message</button>

            <button onclick="callEndpoint('/blaze/run-command?message=!foxhelp&username=Ryan')">Run !foxhelp</button>

            <button onclick="callEndpoint('/blaze/judge-demo')">Run Judge Demo</button>

            <a class="button secondary" href="/">Open Demo Chat</a>

            <a class="button secondary" href="/judges">Judges Page</a>

            <a class="button secondary" href="/features">Features</a>

        </div>



        <div class="output" id="output">FoxBot dashboard ready.</div>



        <div class="note">

            After every Render restart, click Login with Blaze first, then Start Listener.

        </div>

    </div>



    <script>

        async function refreshProof() {

            try {

                const response = await fetch('/proof');

                const data = await response.json();

                const proof = data.proof || {};

                document.getElementById("proofConnected").textContent = proof.blaze_connected ? "Yes" : "No";

                document.getElementById("proofListener").textContent = proof.listener_running ? "Running" : "Stopped";

                document.getElementById("proofChecks").textContent = proof.messages_checked ?? 0;

                document.getElementById("proofCommands").textContent = proof.commands_processed ?? 0;

                document.getElementById("proofLastCommand").textContent = proof.last_command || "None";

                document.getElementById("proofLastUser").textContent = proof.last_username || "None";

            } catch (error) {

                document.getElementById("proofConnected").textContent = "Error";

            }

        }



        async function callEndpoint(url) {

            const output = document.getElementById("output");

            output.textContent = "Loading " + url + "...";

            try {

                const response = await fetch(url);

                const data = await response.json();

                output.textContent = JSON.stringify(data, null, 2);

                refreshProof();

            } catch (error) {

                output.textContent = "Error: " + error;

            }

        }



        refreshProof();

        setInterval(refreshProof, 5000);

    </script>

</body>

</html>

"""



judges_html = """

<!DOCTYPE html>

<html lang="en">

<head>

    <meta charset="UTF-8">

    <meta name="viewport" content="width=device-width, initial-scale=1.0">

    <title>FoxBot For Judges</title>

    <style>

        body {

            margin: 0;

            font-family: Arial, sans-serif;

            background: linear-gradient(135deg, #0b1020, #111827, #1f2937);

            color: white;

            padding: 30px;

        }

        .page {

            max-width: 950px;

            margin: 0 auto;

            background: rgba(17, 24, 39, 0.95);

            border: 1px solid rgba(255,255,255,0.08);

            border-radius: 24px;

            padding: 32px;

            box-shadow: 0 12px 40px rgba(0,0,0,0.35);

        }

        .brand { display: flex; align-items: center; gap: 18px; margin-bottom: 24px; }

        .brand img {

            width: 82px;

            height: 82px;

            border-radius: 20px;

            object-fit: cover;

            border: 2px solid rgba(249, 115, 22, 0.45);

        }

        h1 { margin: 0; font-size: 36px; }

        h2 { margin-top: 30px; color: #fdba74; }

        p, li { color: #d1d5db; line-height: 1.6; font-size: 16px; }

        .badge {

            display: inline-block;

            background: rgba(249, 115, 22, 0.16);

            color: #fdba74;

            border: 1px solid rgba(249, 115, 22, 0.3);

            padding: 8px 14px;

            border-radius: 999px;

            font-size: 14px;

            margin-top: 10px;

        }

        .links { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 24px; }

        .button {

            display: inline-block;

            text-decoration: none;

            background: linear-gradient(135deg, #f97316, #ea580c);

            color: white;

            padding: 14px 18px;

            border-radius: 14px;

            font-weight: bold;

        }

        .secondary { background: linear-gradient(135deg, #2563eb, #1d4ed8); }

        .box {

            background: #0f172a;

            border: 1px solid rgba(255,255,255,0.08);

            border-radius: 16px;

            padding: 18px;

            margin-top: 16px;

        }

        code {

            color: #93c5fd;

            background: #020617;

            padding: 2px 6px;

            border-radius: 6px;

        }

    </style>

</head>

<body>

    <div class="page">

        <div class="brand">

            <img src="/static/foxbot-logo.png" alt="FoxBot Logo">

            <div>

                <h1>FoxBot AI Chatbot</h1>

                <div class="badge">Blaze Builder Challenge Submission</div>

            </div>

        </div>



        <p>

            FoxBot AI Chatbot is a Blaze-connected creator assistant that helps streamers automate chat engagement,

            run giveaways, answer common questions, and respond to live chat commands.

        </p>



        <div class="links">

            <a class="button" href="/">Open Demo Chat</a>

            <a class="button secondary" href="/dashboard">Open Control Dashboard</a>

            <a class="button secondary" href="/project-status">Project Status</a>

            <a class="button secondary" href="/proof">Live Proof JSON</a>

            <a class="button secondary" href="/features">Features</a>

        </div>



        <h2>What This Project Proves</h2>

        <div class="box">

            <p>

                FoxBot connects to a real Blaze account using OAuth, finds the creator's Blaze channel,

                sends messages into real Blaze chat, checks recent chat messages, and responds to commands.

            </p>

        </div>



        <h2>Core Features</h2>

        <ul>

            <li>Blaze OAuth login</li>

            <li>Real Blaze channel lookup</li>

            <li>Real Blaze chat message posting</li>

            <li>Polling-based live chat command detection</li>

            <li>Automatic command replies in Blaze chat</li>

            <li>Giveaway entry tracking</li>

            <li>Duplicate entry protection</li>

            <li>Random winner selection</li>

            <li>Creator control dashboard</li>

            <li>Live proof panel for judges</li>

        </ul>



        <h2>Test Commands</h2>

        <ul>

            <li><code>!foxhelp</code> — shows available commands</li>

            <li><code>!schedule</code> — shows the stream schedule</li>

            <li><code>!faq</code> — explains FoxBot</li>

            <li><code>!giveaway</code> — starts a giveaway</li>

            <li><code>!enter</code> — enters a viewer into the giveaway</li>

            <li><code>!entries</code> — shows current giveaway entries</li>

            <li><code>!pickwinner</code> — randomly selects a winner</li>

            <li><code>!ask</code> — demo AI response mode</li>

        </ul>



        <h2>How To Demo</h2>

        <ol>

            <li>Open the dashboard.</li>

            <li>Click <strong>Login with Blaze</strong>.</li>

            <li>Click <strong>Start Listener</strong>.</li>

            <li>Type <code>!foxhelp</code> in Blaze chat.</li>

            <li>FoxBot replies directly in Blaze chat.</li>

            <li>Watch the Live Proof Panel update.</li>

        </ol>



        <h2>Tech Stack</h2>

        <p>Python, FastAPI, Render, Blaze OAuth, Blaze Chat API, HTML, CSS, and JavaScript.</p>

    </div>

</body>

</html>

"""





# ----------------------------

# Basic pages

# ----------------------------



@app.get("/", response_class=HTMLResponse)
def public_home():
    return FileResponse(
        "templates/foxbot_landing.html",
        media_type="text/html"
    )


@app.get("/demo-chat", response_class=HTMLResponse)

def home():

    return html_content





@app.get("/dashboard", response_class=HTMLResponse)

def dashboard():

    return dashboard_html





@app.get("/judges", response_class=HTMLResponse)

def judges_page():

    return judges_html





# ----------------------------

# FoxBot command logic

# ----------------------------

def format_boss_status():

    currency = get_currency_name()



    if not boss_battle.get("active"):

        defeated_count = boss_battle.get("defeated_count", 0)

        last_winner = boss_battle.get("last_winner")



        if last_winner:

            return f"No boss is active. Last MVP: @{last_winner}. Bosses defeated: {defeated_count}. Admins can use !startboss Cyber Fox Dragon"



        return f"No boss is active. Bosses defeated: {defeated_count}. Admins can use !startboss Cyber Fox Dragon"



    name = boss_battle.get("name", "Unknown Boss")

    hp = int(boss_battle.get("hp", 0))

    max_hp = int(boss_battle.get("max_hp", 500))



    return f"Boss Battle: {name} has {hp}/{max_hp} HP. Type !attack to fight or !powerattack to spend 25 {currency} for bigger damage."





def format_boss_leaderboard(limit: int = 5):

    damage_log = boss_battle.get("damage_log", {})



    if not damage_log:

        return "No boss damage yet. Type !attack to get on the board."



    sorted_damage = sorted(

        damage_log.items(),

        key=lambda item: int(item[1]),

        reverse=True

    )



    parts = []



    for index, (viewer, damage) in enumerate(sorted_damage[:limit], start=1):

        parts.append(f"{index}. {viewer} — {damage} damage")



    return "Boss damage leaderboard: " + " | ".join(parts)





def add_boss_damage(username: str, damage: int):

    clean_name = normalize_viewer_name(username)

    key = clean_name.lower()



    damage_log = boss_battle.setdefault("damage_log", {})

    current_damage = int(damage_log.get(key, 0))

    damage_log[key] = current_damage + int(damage)



    boss_battle["hp"] = max(0, int(boss_battle.get("hp", 0)) - int(damage))



    return damage_log[key]





def finish_boss_if_defeated(creator_id: str = None):

    if int(boss_battle.get("hp", 0)) > 0:

        return None



    damage_log = boss_battle.get("damage_log", {})



    if damage_log:

        top_player = max(damage_log.items(), key=lambda item: int(item[1]))[0]

    else:

        top_player = "unknown"



    boss_battle["active"] = False

    boss_battle["defeated_count"] = int(boss_battle.get("defeated_count", 0)) + 1

    add_quest_progress("boss", 1)

    boss_battle["last_winner"] = top_player



    bonus = 100

    currency = get_currency_name()



    if top_player != "unknown":

        add_points(top_player, bonus, "boss battle mvp", creator_id=creator_id)



    return f"Boss defeated! MVP @{top_player} earned a {bonus} {currency} bonus. Total bosses defeated: {boss_battle['defeated_count']}."





def command_root(message: str):

    clean = (message or "").strip().lower()



    if not clean.startswith("!"):

        return ""



    return clean.split()[0]





def format_cooldowns():

    if not cooldown_settings:

        return "No FoxBot cooldowns are active."



    parts = []



    for command, seconds in sorted(cooldown_settings.items()):

        parts.append(f"{command}: {seconds}s")



    return "FoxBot cooldowns: " + " | ".join(parts)





def check_command_cooldown(username: str, message: str, admin: bool = False):

    if admin:

        return None



    command = command_root(message)



    if command not in cooldown_settings:

        return None



    seconds = int(cooldown_settings.get(command, 0))



    if seconds <= 0:

        return None



    key = f"{viewer_key(username)}:{command}"

    now = time.time()

    last_used = float(cooldown_tracker.get(key, 0))

    remaining = int(seconds - (now - last_used))



    if remaining > 0:

        return f"@{username}, {command} is on cooldown. Try again in {remaining}s."



    cooldown_tracker[key] = now

    return None





def normalize_viewer_name(name: str):

    clean = (name or "viewer").strip().lstrip("@")

    return clean or "viewer"





def viewer_key(name: str):

    return normalize_viewer_name(name).lower()





def get_currency_name():

    return foxcoin_economy.get("currency_name", "FoxCoins")




_tenant_zero_fallback_warned = False




def _tenant_zero_id():

    # Falls back to a literal sentinel (never "") if the env var isn't set,
    # e.g. local dev/tests -- keeps this a distinct, clearly-labeled empty
    # bucket rather than crashing or silently colliding with a real ID.
    # Warn once (not per-call -- this runs on every economy read/write) so
    # real activity landing in the wrong bucket in production surfaces
    # immediately instead of silently, without spamming the logs.
    if FOXBOT_TENANT_ZERO_CREATOR_ID:

        return FOXBOT_TENANT_ZERO_CREATOR_ID



    global _tenant_zero_fallback_warned

    if not _tenant_zero_fallback_warned:

        _tenant_zero_fallback_warned = True

        print(

            "!!! FOXBOT ECONOMY FALLBACK !!! FOXBOT_TENANT_ZERO_CREATOR_ID "

            "is not set -- economy activity is landing in a separate "

            "'tenant-zero' bucket, not the real creator's data. Set "

            "FOXBOT_TENANT_ZERO_CREATOR_ID in Render and restart."

        )



    return "tenant-zero"


def _foxbot_resolve_creator_id_v1(creator_handle=None, blaze_id=None):
    """Bot Connection Sub-phase D, stage 1: THE canonical creator-identity
    resolver. Every one of the by_creator touchpoints below funnels
    through this one function so all 25+ call sites resolve identity the
    SAME way -- the split-brain guard. Do not reimplement this logic at
    a call site; call this function.

    Precedence:
    1. blaze_id given directly (dashboard-request-driven callers, which
       already have a Blaze-verified blaze_id from the session) -- used
       as-is, already canonical.
    2. creator_handle given (chat-message-driven callers) -- looked up
       via the connected_creators.json join
       (_foxbot_resolve_blaze_id_for_handle_v1). Mapped -> that blaze_id.
       Unmapped -> falls through to 3.
    3. Neither given, or an unmapped handle -- _tenant_zero_id(). This is
       what keeps every call site byte-identical to today's hardcoded
       behavior for as long as nothing has a blaze_id mapped yet (the
       real state of the system right now): every resolution lands in
       tenant-zero's bucket, exactly as the direct _tenant_zero_id()
       calls this function is replacing already did.

    Never returns a bucket keyed on the raw creator_handle itself -- an
    unmapped handle is not a new identity, it's an unresolved one, and
    falls back to tenant-zero rather than fragmenting storage.
    """
    if blaze_id:
        return str(blaze_id).strip()

    if creator_handle:
        mapped = _foxbot_resolve_blaze_id_for_handle_v1(creator_handle)
        if mapped:
            return mapped

    return _tenant_zero_id()




def _creator_economy_v1(creator_id):

    # Lazy setdefault: works whether or not the hydration-time migration
    # populated this entry (a fresh install with no pre-Phase-1 balances
    # never triggers that copy) -- first access always succeeds, never
    # KeyErrors.
    return foxcoin_economy["by_creator"].setdefault(creator_id, {

        "balances": {},

        "daily_claims": {},

        "transactions": []

    })


def _tenant_zero_economy():
    return _creator_economy_v1(_tenant_zero_id())


def _creator_streaks_v1(creator_id):

    # Lazy setdefault, same reasoning as _creator_economy_v1(): works
    # whether or not the hydration-time migration populated this entry, and
    # returns a live mutable reference -- callers that mutate the returned
    # dict's values in place (get_streak_data's callers do exactly this,
    # e.g. data["streak"] += 1) are mutating the real by_creator storage,
    # not a copy, so the change persists without a separate write-back call.
    return viewer_streaks["by_creator"].setdefault(creator_id, {})


def _tenant_zero_streaks():
    return _creator_streaks_v1(_tenant_zero_id())


def _creator_viewer_stats_v1(creator_id):

    # Lazy setdefault, same reference-preserving contract as
    # _creator_economy_v1()/_creator_streaks_v1(). viewer_stats has no
    # persistence and no migration -- it resets to {"by_creator": {}} on
    # every process start regardless -- so unlike the other two creator
    # helpers, there's no frozen flat data behind this one, ever.
    return viewer_stats["by_creator"].setdefault(creator_id, {})


def _tenant_zero_viewer_stats():
    return _creator_viewer_stats_v1(_tenant_zero_id())


def _creator_commands_v1(creator_id):

    # Lazy setdefault, same reference-preserving contract as the other
    # three creator helpers.
    return custom_commands["by_creator"].setdefault(creator_id, {})


def _tenant_zero_commands():
    # Bot Connection Sub-phase D, stage 1: kept as a thin wrapper around
    # _creator_commands_v1(_tenant_zero_id()) so every call site that
    # genuinely has no per-request identity concept (admin/test/debug
    # routes, background triggers) keeps working completely unchanged.
    # Call sites that DO have a real creator_handle/blaze_id should call
    # _creator_commands_v1(_foxbot_resolve_creator_id_v1(...)) instead of
    # this function -- later stages migrate them one at a time.
    return _creator_commands_v1(_tenant_zero_id())





def get_balance(name: str, creator_id: str = None):

    key = viewer_key(name)

    return int(_creator_economy_v1(creator_id or _tenant_zero_id())["balances"].get(key, 0))





def add_points(name: str, amount: int, reason: str = "activity", creator_id: str = None):

    clean_name = normalize_viewer_name(name)

    key = viewer_key(clean_name)

    economy = _creator_economy_v1(creator_id or _tenant_zero_id())



    current = int(economy["balances"].get(key, 0))

    new_balance = max(0, current + int(amount))

    economy["balances"][key] = new_balance



    economy["transactions"].append({

        "viewer": clean_name,

        "amount": int(amount),

        "reason": reason,

        "balance": new_balance

    })



    # Keep transaction history small

    economy["transactions"] = economy["transactions"][-50:]



    return new_balance





def add_redemption(username: str, reward_name: str, message: str, cost: int, creator_handle: str = None):

    redemption = {

        "username": normalize_viewer_name(username),

        "reward": reward_name,

        "message": message,

        "cost": int(cost)

    }



    redemption_queue.insert(0, redemption)



    # Keep the latest 10 redemptions

    del redemption_queue[10:]



    _foxbot_events_v1.emit_event(
        creator_handle or _foxbot_events_v1.resolve_owner_handle(),
        "reward",
        actor=redemption["username"],
        detail={"reward": reward_name, "cost": int(cost)},
    )



    return redemption





def format_redemptions(limit: int = 5):

    if not redemption_queue:

        return "No active redemptions yet. Use !shop and !redeem to spend FoxCoins."



    parts = []



    for item in redemption_queue[:limit]:

        parts.append(f"@{item['username']} redeemed {item['reward']}")



    return "Recent redemptions: " + " | ".join(parts)





def add_recognition_log(event_type: str, username: str, message: str, reward: int = 0):

    item = {

        "event_type": event_type,

        "username": normalize_viewer_name(username),

        "message": message,

        "reward": int(reward)

    }



    recognition_log.insert(0, item)

    del recognition_log[25:]



    return item





def surprise_bonus(username: str, creator_id: str = None):

    if not recognition_settings.get("surprise_bonus_enabled", True):

        return ""



    chance = int(recognition_settings.get("surprise_bonus_chance", 15))

    roll = random.randint(1, 100)



    if roll > chance:

        return ""



    bonus = random.choice([25, 50, 100, 150, 250])

    currency = get_currency_name()

    new_balance = add_points(username, bonus, "surprise recognition bonus", creator_id=creator_id)



    return f" Lucky Bonus: @{normalize_viewer_name(username)} found a Golden Fox Chest and earned +{bonus} {currency}. Balance: {new_balance} {currency}."





def recognition_response(event_type: str, target: str, amount=None, creator_id: str = None):

    target = normalize_viewer_name(target)

    currency = get_currency_name()



    if not recognition_settings.get("enabled", True):

        return f"Recognition is currently disabled. Event received for @{target}: {event_type}."



    if event_type == "follow":

        reward = int(support_rewards.get("follow", 100))

        reward = min(reward, RECOGNITION_MAX_REWARD)

        new_balance = add_points(target, reward, "auto follow recognition", creator_id=creator_id)

        msg = f"Welcome @{target} to the FoxBot AI pack! Thanks for the follow. +{reward} {currency}. Balance: {new_balance} {currency}."

        msg += surprise_bonus(target, creator_id=creator_id)

        add_recognition_log(event_type, target, msg, reward)

        return msg



    if event_type == "sub":

        reward = int(support_rewards.get("new_sub", 500))

        reward = min(reward, RECOGNITION_MAX_REWARD)

        new_balance = add_points(target, reward, "auto sub recognition", creator_id=creator_id)

        msg = f"HUGE THANK YOU @{target} for subscribing! Welcome to the FoxBot AI family. +{reward} {currency}. Balance: {new_balance} {currency}."

        msg += surprise_bonus(target, creator_id=creator_id)

        add_recognition_log(event_type, target, msg, reward)

        return msg



    if event_type == "giftsub":

        count = min(int(amount or 1), RECOGNITION_UNIT_CAPS["giftsub"])

        reward = int(support_rewards.get("gift_sub", 500)) * count

        reward = min(reward, RECOGNITION_MAX_REWARD)

        new_balance = add_points(target, reward, f"auto gifted subs x{count}", creator_id=creator_id)

        msg = f"LEGEND ALERT: @{target} gifted {count} subs! Everyone show some love. +{reward} {currency}. Balance: {new_balance} {currency}."

        msg += surprise_bonus(target, creator_id=creator_id)

        add_recognition_log(event_type, target, msg, reward)

        return msg



    if event_type == "vote":

        votes = min(int(amount or 1), RECOGNITION_UNIT_CAPS["vote"])

        reward = int(support_rewards.get("vote_token", 3)) * votes

        reward = min(reward, RECOGNITION_MAX_REWARD)

        new_balance = add_points(target, reward, f"auto vote recognition x{votes}", creator_id=creator_id)

        msg = f"Thank you @{target} for voting with {votes} votes! FoxBot AI appreciates your support! +{reward} {currency}. Balance: {new_balance} {currency}."

        msg += surprise_bonus(target, creator_id=creator_id)

        add_recognition_log(event_type, target, msg, reward)

        return msg



    if event_type == "tip":

        dollars = min(float(amount or 1), RECOGNITION_UNIT_CAPS["tip"])

        reward = int(dollars * int(support_rewards.get("tip_per_dollar", 200)))

        reward = min(reward, RECOGNITION_MAX_REWARD)

        new_balance = add_points(target, reward, f"auto tip recognition ${dollars}", creator_id=creator_id)

        msg = f"Big shoutout to @{target} for the ${dollars:g} tip! Thank you for supporting the stream. +{reward} {currency}. Balance: {new_balance} {currency}."

        msg += surprise_bonus(target, creator_id=creator_id)

        add_recognition_log(event_type, target, msg, reward)

        return msg



    if event_type == "raid":

        reward = int(support_rewards.get("raid", 250))

        reward = min(reward, RECOGNITION_MAX_REWARD)

        new_balance = add_points(target, reward, "auto raid recognition", creator_id=creator_id)

        msg = f"RAID LOVE! Huge thanks to @{target} for bringing the community over. +{reward} {currency}. Balance: {new_balance} {currency}."

        msg += surprise_bonus(target, creator_id=creator_id)

        add_recognition_log(event_type, target, msg, reward)

        return msg



    if event_type == "mvp":

        reward = 250

        reward = min(reward, RECOGNITION_MAX_REWARD)

        new_balance = add_points(target, reward, "MVP recognition", creator_id=creator_id)

        msg = f"MVP SHOUTOUT: @{target} is carrying the stream today! +{reward} {currency}. Balance: {new_balance} {currency}."

        add_recognition_log(event_type, target, msg, reward)

        return msg



    if event_type == "og":

        reward = 500

        reward = min(reward, RECOGNITION_MAX_REWARD)

        new_balance = add_points(target, reward, "OG recognition", creator_id=creator_id)

        msg = f"OG FOX SPIRIT: @{target} has been here from the jump. Respect to one of the real ones. +{reward} {currency}. Balance: {new_balance} {currency}."

        add_recognition_log(event_type, target, msg, reward)

        return msg



    return f"Unknown recognition event: {event_type}"





def format_reward_shop():

    currency = get_currency_name()



    if not reward_shop:

        return f"The reward shop is empty. Admins can add rewards with !addreward name cost message"



    parts = []



    for reward_name, reward_data in sorted(reward_shop.items(), key=lambda item: item[1].get("cost", 0)):

        cost = reward_data.get("cost", 0)

        parts.append(f"{reward_name} — {cost} {currency}")



    return "FoxBot Reward Shop: " + " | ".join(parts) + " | Use !redeem rewardname"





def format_reward_response(template: str, username: str, cost: int, balance: int):

    currency = get_currency_name()



    return (

        template

        .replace("{username}", username)

        .replace("{cost}", str(cost))

        .replace("{balance}", str(balance))

        .replace("{currency}", currency)

    )





def today_string():

    return date.today().isoformat()





def get_streak_data(username: str, creator_id: str = None):

    key = viewer_key(username)

    tenant_streaks = _creator_streaks_v1(creator_id or _tenant_zero_id())



    if key not in tenant_streaks:

        tenant_streaks[key] = {

            "display_name": normalize_viewer_name(username),

            "streak": 0,

            "best_streak": 0,

            "last_checkin": None

        }



    return tenant_streaks[key]





def format_streak_leaderboard(limit: int = 5, creator_id: str = None):

    tenant_streaks = _creator_streaks_v1(creator_id or _tenant_zero_id())

    if not tenant_streaks:

        return "No streaks yet. Type !checkin to start your FoxBot streak."



    sorted_streaks = sorted(

        tenant_streaks.values(),

        key=lambda item: int(item.get("streak", 0)),

        reverse=True

    )



    parts = []



    for index, item in enumerate(sorted_streaks[:limit], start=1):

        parts.append(f"{index}. {item.get('display_name')} — {item.get('streak', 0)} streak")



    return "FoxBot streak leaderboard: " + " | ".join(parts)





def format_quest_status():

    currency = get_currency_name()



    if not community_quest.get("active"):

        return "No community quest is active. Admins can start one with !startquest foxhunt 10"



    quest_type = community_quest.get("type", "unknown")

    progress = int(community_quest.get("progress", 0))

    goal = int(community_quest.get("goal", 0))

    reward = int(community_quest.get("reward", 100))

    completed = community_quest.get("completed", False)



    if completed:

        return f"Community Quest Complete: {quest_type} {progress}/{goal}. Type !claimquest to claim {reward} {currency}."



    return f"Community Quest: {quest_type} {progress}/{goal}. Reward: {reward} {currency}. Everyone helps complete it!"





def add_quest_progress(quest_type: str, amount: int = 1):

    if not community_quest.get("active"):

        return None



    if community_quest.get("completed"):

        return None



    if community_quest.get("type") != quest_type:

        return None



    community_quest["progress"] = int(community_quest.get("progress", 0)) + int(amount)



    if int(community_quest.get("progress", 0)) >= int(community_quest.get("goal", 0)):

        community_quest["progress"] = int(community_quest.get("goal", 0))

        community_quest["completed"] = True

        return "completed"



    return "progress"





def format_stream_event():

    if not stream_event.get("active"):

        return "No stream event is active. Admins can start one with !startevent goldenfox, spiritstorm, treasuredrop, or foxfrenzy."



    return f"Active Event: {stream_event.get('name')} | {stream_event.get('description')} | Type !event to check or claim."





def activate_stream_event(event_key: str):

    key = (event_key or "").strip().lower()

    templates = globals().get("stream_event_templates", {})



    if key == "random":

        key = random.choice(list(templates.keys()))



    if key not in templates:

        return None



    template = templates[key]



    stream_event["active"] = True

    stream_event["key"] = key

    stream_event["name"] = template["name"]

    stream_event["description"] = template["description"]

    stream_event["claimed"] = {}



    return template





def current_event_multiplier(command_name: str):

    if not stream_event.get("active"):

        return 1



    key = stream_event.get("key")



    if key == "goldenfox" and command_name == "!foxhunt":

        return 2



    if key == "spiritstorm" and command_name in ["!attack", "!powerattack"]:

        return 2



    if key == "foxfrenzy" and command_name in ["!coinflip", "!roll", "!8ball", "!rps"]:

        return 2



    return 1





def get_fox_rank(balance: int):

    current_rank = fox_spirit_ranks[0]



    for rank in fox_spirit_ranks:

        if int(balance) >= int(rank.get("minimum", 0)):

            current_rank = rank



    return current_rank





def get_next_fox_rank(balance: int):

    for rank in fox_spirit_ranks:

        if int(balance) < int(rank.get("minimum", 0)):

            return rank



    return None





def format_rank_list():

    currency = get_currency_name()

    parts = []



    for rank in fox_spirit_ranks:

        parts.append(f"{rank['name']} = {rank['minimum']} {currency}")



    return "Fox Spirit Ranks: " + " | ".join(parts)





def format_coin_leaderboard(limit: int = 5, creator_id: str = None):

    currency = get_currency_name()

    balances = _creator_economy_v1(creator_id or _tenant_zero_id())["balances"]



    if not balances:

        return f"No {currency} balances yet. Type !daily or !foxhunt to earn some."



    sorted_balances = sorted(

        balances.items(),

        key=lambda item: item[1],

        reverse=True

    )



    parts = []

    for index, (name, balance) in enumerate(sorted_balances[:limit], start=1):

        parts.append(f"{index}. {name} — {balance} {currency}")



    return f"{currency} leaderboard: " + " | ".join(parts)





def normalize_custom_command(command_name: str):

    cleaned = command_name.strip().lower()



    if not cleaned:

        return ""



    if not cleaned.startswith("!"):

        cleaned = "!" + cleaned



    return cleaned





def format_custom_commands(creator_id: str = None):

    tenant_commands = _creator_commands_v1(creator_id or _tenant_zero_id())

    if not tenant_commands:

        return "No custom commands yet. Admins can add one with !addcmd name response"



    command_names = sorted(tenant_commands.keys())

    return "Custom FoxBot commands: " + ", ".join(command_names)





def mode_style_response(message_type: str, username: str = "viewer", target: str = "", question: str = ""):

    mode = bot_mode.lower()



    if message_type == "hug":

        if mode == "chill":

            return f"@{username} sends a chill FoxBot hug to the chat."

        if mode == "pro":

            return f"@{username} sends a respectful FoxBot hug to the community."

        return f"@{username} sends a big FoxBot hug to the chat! FoxBot energy is high!"



    if message_type == "shoutout":

        if mode == "chill":

            return f"Shoutout to @{target}. Appreciate you hanging with the Blaze community."

        if mode == "pro":

            return f"Creator shoutout: @{target}. Thank you for supporting the stream."

        return f"HUGE shoutout to @{target}! Go show them some Blaze love!"



    if message_type == "ask":

        if mode == "chill":

            return f"FoxBot AI demo mode: good question. Once full AI billing is enabled, I would answer: {question}"

        if mode == "pro":

            return f"FoxBot AI demo mode: AI responses are prepared for future activation. Question received: {question}"

        return f"FoxBot AI demo mode: awesome question! Once full AI billing is enabled, I would answer this next: {question}"



    return message_type





def track_viewer_command(username: str, command: str, creator_id: str = None):

    clean_name = username.strip() or "viewer"

    clean_key = clean_name.lower()

    tenant_stats = _creator_viewer_stats_v1(creator_id or _tenant_zero_id())



    if clean_key not in tenant_stats:

        tenant_stats[clean_key] = {

            "display_name": clean_name,

            "commands": 0,

            "last_command": None

        }



    tenant_stats[clean_key]["commands"] += 1

    tenant_stats[clean_key]["last_command"] = command





def format_leaderboard(limit: int = 5, creator_id: str = None):

    tenant_stats = _creator_viewer_stats_v1(creator_id or _tenant_zero_id())

    if not tenant_stats:

        return "FoxBot leaderboard is empty. Type !foxhelp to get started."



    sorted_users = sorted(

        tenant_stats.values(),

        key=lambda item: item.get("commands", 0),

        reverse=True

    )



    top_users = sorted_users[:limit]



    parts = []

    for index, user in enumerate(top_users, start=1):

        parts.append(f"{index}. {user['display_name']} — {user['commands']} commands")



    return "FoxBot leaderboard: " + " | ".join(parts)





def is_admin(username: str):

    admin_usernames = os.getenv("ADMIN_USERNAMES", "crypt0k1ng96,Ryan")

    admins = [name.strip().lower() for name in admin_usernames.split(",")]

    return username.strip().lower() in admins





@app.get("/chat")

def chat(message: str = "", username: str = "viewer", creator_handle: str = None, allow_admin: bool = True):

    global giveaway_entries

    global bot_mode

    global custom_commands

    global stream_info

    global arcade_stats

    global foxcoin_economy

    global support_rewards

    global fox_spirit_ranks

    global stream_event

    global stream_event_templates

    global community_quest

    global viewer_streaks

    global reward_shop

    global redemption_queue

    global cooldown_settings

    global cooldown_tracker

    global boss_battle



    original_message = message.strip()

    lower_message = original_message.lower()

    username = username.strip() or "viewer"

    creator_handle = str(creator_handle or "").strip() or _foxbot_events_v1.resolve_owner_handle()

    # Bot Connection Sub-phase D, stage 3: resolve ONCE per message, not
    # at each of the ~30 call sites below -- same guaranteed-consistent
    # value used everywhere in this message's processing (the split-brain
    # guard), and avoids a redundant connected_creators.json read per
    # call site. Falls back to tenant-zero automatically for as long as
    # creator_handle has no blaze_id mapping (today's real state), so
    # every call site below stays byte-identical to its old hardcoded
    # _tenant_zero_*() behavior until a real join exists.
    resolved_creator_id = _foxbot_resolve_creator_id_v1(creator_handle=creator_handle)

    # === FoxBot Studio Giveaway Viewer Entry v3 ===
    # Real stream entry command for the Admin Hub Giveaway Center.
    if lower_message == "!enter":
        state = globals().setdefault("FOXBOT_STUDIO_GIVEAWAY_STATE_V3", {
            "active": False,
            "prize": "Weekly Giveaway",
            "rules": "Type !enter to join.",
            "last_winner": None,
            "last_entry": None,
            "last_action": "Waiting"
        })

        entries = globals().setdefault("giveaway_entries", [])

        if not state.get("active", False):
            return {
                "response": "No giveaway is open right now. Watch for the next giveaway!"
            }

        clean_user = username.strip() or "viewer"
        existing = [str(x).lower() for x in entries]

        if clean_user.lower() in existing:
            return {
                "response": f"@{clean_user} you are already entered for {state.get('prize', 'the giveaway')}!"
            }

        entries.append(clean_user)
        state["last_entry"] = clean_user
        state["last_action"] = f"{clean_user} entered"

        return {
            "response": f"@{clean_user} entered {state.get('prize', 'the giveaway')}! Entries: {len(entries)}"
        }

    if lower_message == "!giveaway":
        state = globals().setdefault("FOXBOT_STUDIO_GIVEAWAY_STATE_V3", {
            "active": False,
            "prize": "Weekly Giveaway",
            "rules": "Type !enter to join.",
            "last_winner": None,
            "last_entry": None,
            "last_action": "Waiting"
        })
        entries = globals().setdefault("giveaway_entries", [])

        if state.get("active", False):
            return {
                "response": f"Giveaway live: {state.get('prize', 'Weekly Giveaway')} | Type !enter to join | Entries: {len(entries)}"
            }

        return {
            "response": "No giveaway is open right now."
        }

    if lower_message == "!entries":
        entries = globals().setdefault("giveaway_entries", [])
        if not entries:
            return {
                "response": "No giveaway entries yet."
            }

        preview = ", ".join([str(x) for x in entries[-10:]])
        return {
            "response": f"Giveaway entries: {len(entries)} total | Latest: {preview}"
        }


    # === FoxBot Clean Base Shop Override v1 ===
    # Keeps base !shop clean in Blaze chat: no broken emoji, no FoxCoins/FC labels.
    if lower_message in ["!shop", "!rewards", "!rewardshop"]:
        return {
            "response": "FoxBot Reward Shop: hug 10 | hype 25 | flex 50 | mysterybox 75 | sponsor 150 | More: !shop premium or !shop elite | Redeem: !redeem name"
        }

    # === FoxBot Early Shop Category Router v1 ===
    # Fixes !shop premium / !shop elite falling through to Unknown command.
    if lower_message.startswith("!shop ") or lower_message.startswith("!rewards "):
        parts = lower_message.split()
        page = parts[1].strip().lower() if len(parts) > 1 else "main"

        allowed_pages = {"main", "cheap", "social", "control", "premium", "elite", "all"}

        if page not in allowed_pages:
            return {
                "response": "🦊❓ Page not found. Try !shop cheap, social, control, premium, elite, or all."
            }

        try:
            return {
                "response": foxbot_safe_rewards21_shop_text_v1(page)
            }
        except Exception as e1:
            try:
                return {
                    "response": foxbot_rewards_v2_shop_text(page)
                }
            except Exception as e2:
                return {
                    "response": f"🦊 Shop category command loaded, but reward menu failed: {type(e2).__name__}"
                }

    # allow_admin=False forces this to False regardless of what is_admin(username)
    # would say -- used by public, unauthenticated callers (the Blaze Chat Bridge,
    # app.py:18186/18274) where username is caller-supplied and can't be trusted
    # to grant admin authority. Every trusted call site (the gated /blaze/*
    # routes, /api/foxbot/admin-command, the real chat-listener pipelines) keeps
    # the allow_admin=True default and is unaffected.
    admin = allow_admin and is_admin(username)



    # === FoxBot Connect Exact Chat Hook v1 ===

    # Handles !connect, !profile, !rank, and !disconnect through the normal /chat route.

    if lower_message.split(" ", 1)[0] in ["!join", "!connect", "!profile", "!rank", "!access", "!verify", "!disconnect"]:

        try:

            foxbot_connect_result = _foxbot_connect_process_command_v1(

                handle=username,

                message=original_message,

                display_name=username

            )

            foxbot_connect_reply = foxbot_connect_result.get("reply") or "🦊 FoxBot Connect command handled."

            return {

                "response": foxbot_connect_reply,

                "reply": foxbot_connect_reply,

                "handled": True,

                "type": "foxbot_connect",

                "foxbot_connect": foxbot_connect_result

            }

        except Exception as foxbot_connect_error:

            return {

                "response": f"🦊 FoxBot Connect error: {foxbot_connect_error}",

                "reply": f"🦊 FoxBot Connect error: {foxbot_connect_error}",

                "handled": True,

                "type": "foxbot_connect_error"

            }

    # === End FoxBot Connect Exact Chat Hook v1 ===



    cooldown_message = check_command_cooldown(username, lower_message, admin)

    if cooldown_message:

        return {

            "response": cooldown_message

        }



    if lower_message.startswith("!"):

        track_viewer_command(username, lower_message, creator_id=resolved_creator_id)



    if lower_message == "!cooldowns":

        return {

            "response": format_cooldowns()

        }



    if lower_message.startswith("!setcooldown"):

        if not admin:

            return {

                "response": f"@{username}, only the creator or mods can change cooldowns."

            }



        parts = original_message.split()



        if len(parts) < 3:

            return {

                "response": "Use !setcooldown command seconds. Example: !setcooldown foxhunt 60"

            }



        command_name = parts[1].strip().lower()



        if not command_name.startswith("!"):

            command_name = "!" + command_name



        try:

            seconds = int(parts[2])

        except ValueError:

            return {

                "response": "Cooldown seconds must be a number. Example: !setcooldown foxhunt 60"

            }



        if seconds < 0:

            return {

                "response": "Cooldown seconds cannot be negative."

            }



        cooldown_settings[command_name] = seconds



        return {

            "response": f"Cooldown for {command_name} set to {seconds}s."

        }



    if lower_message == "!clearcooldowns":

        if not admin:

            return {

                "response": f"@{username}, only the creator or mods can clear cooldown timers."

            }



        cooldown_tracker.clear()



        return {

            "response": "FoxBot cooldown timers cleared."

        }



    if lower_message in ["!goodnight", "!endstream"]:

        if not admin:

            return {

                "response": f"@{username}, only the creator or mods can use the stream ending message."

            }



        creator_name = os.getenv("CREATOR_NAME", "Ryan")

        signoff = os.getenv(

            "GOODNIGHT_MESSAGE",

            f"{creator_name} is ending stream. Thank you for hanging out, earning FoxCoins, playing FoxBot games, and supporting the Blaze community. Goodnight everyone!"

        )



        return {

            "response": signoff

        }



    # !rules and !giveawaylink used to be hardcoded here, short-circuiting
    # before the custom_commands dispatch further down. Seeding them once
    # via setdefault (never overwrites an existing entry, so a prior or
    # future !addcmd edit sticks) and falling through instead of
    # returning lets !addcmd actually take effect for these two.
    #
    # Bot Connection Sub-phase D: seeds into resolved_creator_id's
    # by_creator slice -- must use the exact same resolved id every other
    # touchpoint in this function uses (computed once, see
    # resolved_creator_id above), or this would split commands across two
    # different creator buckets and dispatch would only ever find one of
    # them.
    tenant_commands = _creator_commands_v1(resolved_creator_id)

    tenant_commands.setdefault("!rules", {

        "response": "BLAZE COMMUNITY SPIN RULES | $25 USDC Giveaway | +100 Votes Sponsored by FoxBot AI | Tag 3 Friends | Like + Repost | Be Active in FoxBot AI Discord | Up to 1.50x Multiplier | 1-5 Gifted Subs = Bonus Entries | Sunday 5 PM PST | Enter here: https://x.com/Pardon_my_trade/status/2069089169738289206?s=20",

        "created_by": "system-default"

    })

    tenant_commands.setdefault("!giveawaylink", {

        "response": "$25 USDC Giveaway + 100 Votes Sponsored by FoxBot AI | Enter here: https://x.com/Pardon_my_trade/status/2069089169738289206?s=20",

        "created_by": "system-default"

    })



    if lower_message == "!help":

        # BLAZEIAN_BOT (a platform bot we don't control) also answers !help, which
        # gave viewers two overlapping help lists. FoxBot now stays silent on !help
        # and only answers !foxhelp, so this must not fall through to the unknown-
        # command catch-all below.
        return {

            "response": ""

        }



    if lower_message == "!foxhelp":

        if admin:

            return {

                "response": "FoxBot help: !daily, !foxhunt, !balance, !shop, !redeem, !boss, !attack, !arcade, !socials, !leaderboard | Admin: !giveaway, !pickwinner, !resetstreak, !startquest, !endquest, !questadd, !startevent, !endevent, !goodnight, !endstream, !startboss, !givepoints, !addreward"

            }



        return {

            "response": "FoxBot help: !daily, !foxhunt, !balance, !shop, !redeem hug, !boss, !attack, !arcade, !socials, !leaderboard"

        }



    if lower_message == "!schedule":

        return {

            "response": os.getenv("STREAM_SCHEDULE", "Ryan streams Web3 gaming on Blaze.")

        }



    if lower_message == "!faq":

        return {

            "response": os.getenv("FOXBOT_FAQ", "FoxBot is a Blaze-connected AI chatbot for creators.")

        }



    # !giveaway, !enter, and !entries used to be handled here. That code is
    # deleted (Phase 0 cleanup, e33a91d follow-up) -- it was unreachable:
    # the "FoxBot Studio Giveaway Viewer Entry v3" block above (~line 3710)
    # intercepts all three with an unconditional return before execution
    # could ever reach this point. See giveaway-state-split memory.

    if lower_message == "!pickwinner":

        if not admin:

            return {

                "response": f"@{username}, only the creator or mods can pick giveaway winners."

            }



        if not giveaway_entries:

            return {

                "response": "No giveaway entries yet. Type !enter to join first."

            }



        winner = random.choice(giveaway_entries)

        # Writes the same FOXBOT_STUDIO_GIVEAWAY_STATE_V3 store
        # /overlay/giveaway-data reads.
        foxbot_studio_giveaway_state_v3()["last_winner"] = winner

        _foxbot_events_v1.emit_event(
            creator_handle,
            "giveaway_complete",
            actor=winner,
            detail={},
        )



        return {

            "response": f"The fox has chosen... @{winner} wins!"

        }



    if lower_message in ["!boss", "!bossstatus"]:

        return {

            "response": format_boss_status()

        }



    if lower_message == "!bossleaderboard":

        return {

            "response": format_boss_leaderboard()

        }



    if lower_message.startswith("!startboss"):

        if not admin:

            return {

                "response": f"@{username}, only the creator or mods can start boss battles."

            }



        boss_name = original_message.replace("!startboss", "", 1).strip()



        if not boss_name:

            boss_name = "Cyber Fox Dragon"



        boss_hp = 500



        # Optional format: !startboss 750 Cyber Fox Dragon

        parts = boss_name.split(" ", 1)



        if parts and parts[0].isdigit():

            boss_hp = int(parts[0])

            boss_name = parts[1].strip() if len(parts) > 1 else "Cyber Fox Dragon"



        if boss_hp < 100:

            boss_hp = 100



        if boss_hp > 5000:

            boss_hp = 5000



        boss_battle["active"] = True

        boss_battle["name"] = boss_name

        boss_battle["max_hp"] = boss_hp

        boss_battle["hp"] = boss_hp

        boss_battle["damage_log"] = {}

        boss_battle["last_winner"] = None



        return {

            "response": f"A boss has appeared: {boss_name} with {boss_hp} HP! Type !attack to fight."

        }



    if lower_message == "!endboss":

        if not admin:

            return {

                "response": f"@{username}, only the creator or mods can end boss battles."

            }



        boss_battle["active"] = False



        return {

            "response": "Boss battle ended."

        }



    if lower_message == "!attack":

        if not boss_battle.get("active"):

            return {

                "response": "No boss is active right now. Admins can start one with !startboss Cyber Fox Dragon"

            }



        damage = random.randint(15, 45)

        reward = random.randint(5, 18)

        multiplier = current_event_multiplier("!attack")

        reward = reward * multiplier

        currency = get_currency_name()

        boss_name = boss_battle.get("name", "Boss")



        total_damage = add_boss_damage(username, damage)

        new_balance = add_points(username, reward, "boss attack", creator_id=resolved_creator_id)

        boss_hp = int(boss_battle.get("hp", 0))

        defeat_message = finish_boss_if_defeated(creator_id=resolved_creator_id)



        response = f"@{username} attacked {boss_name} for {damage} damage and earned {reward} {currency}! Boss HP: {boss_hp}/{boss_battle.get('max_hp', 500)}. Your total boss damage: {total_damage}. Balance: {new_balance} {currency}."



        if defeat_message:

            response += " " + defeat_message



        return {

            "response": response

        }



    if lower_message == "!powerattack":

        if not boss_battle.get("active"):

            return {

                "response": "No boss is active right now. Admins can start one with !startboss Cyber Fox Dragon"

            }



        currency = get_currency_name()

        power_cost = 25

        balance = get_balance(username, creator_id=resolved_creator_id)



        if balance < power_cost:

            return {

                "response": f"@{username}, power attack costs {power_cost} {currency}. Your balance: {balance} {currency}."

            }



        add_points(username, -power_cost, "power attack cost", creator_id=resolved_creator_id)



        damage = random.randint(50, 110)

        reward = random.randint(15, 35)

        multiplier = current_event_multiplier("!powerattack")

        reward = reward * multiplier

        boss_name = boss_battle.get("name", "Boss")



        total_damage = add_boss_damage(username, damage)

        new_balance = add_points(username, reward, "boss power attack reward", creator_id=resolved_creator_id)

        boss_hp = int(boss_battle.get("hp", 0))

        defeat_message = finish_boss_if_defeated(creator_id=resolved_creator_id)



        response = f"@{username} used POWER ATTACK on {boss_name} for {damage} damage! Cost: {power_cost} {currency}. Reward: {reward} {currency}. Boss HP: {boss_hp}/{boss_battle.get('max_hp', 500)}. Your total boss damage: {total_damage}. Balance: {new_balance} {currency}."



        if defeat_message:

            response += " " + defeat_message



        return {

            "response": response

        }



    if lower_message == "!checkin":

        data = get_streak_data(username, creator_id=resolved_creator_id)

        today = today_string()

        currency = get_currency_name()



        if data.get("last_checkin") == today:

            return {

                "response": f"@{username}, you already checked in today. Current streak: {data.get('streak', 0)}."

            }



        data["last_checkin"] = today

        data["streak"] = int(data.get("streak", 0)) + 1

        data["best_streak"] = max(int(data.get("best_streak", 0)), int(data.get("streak", 0)))



        reward = 20 + min(int(data["streak"]) * 5, 100)

        new_balance = add_points(username, reward, "daily streak checkin", creator_id=resolved_creator_id)



        return {

            "response": f"@{username} checked in! Streak: {data['streak']} | Best: {data['best_streak']} | +{reward} {currency}. Balance: {new_balance} {currency}."

        }



    if lower_message.startswith("!streak"):

        parts = original_message.split()

        target = username



        if len(parts) >= 2:

            target = normalize_viewer_name(parts[1])



        data = get_streak_data(target, creator_id=resolved_creator_id)



        return {

            "response": f"@{target}'s FoxBot streak: {data.get('streak', 0)} | Best streak: {data.get('best_streak', 0)} | Last check-in: {data.get('last_checkin') or 'never'}"

        }



    if lower_message == "!streaks":

        return {

            "response": format_streak_leaderboard(creator_id=resolved_creator_id)

        }



    if lower_message.startswith("!resetstreak"):

        if not admin:

            return {

                "response": f"@{username}, only the creator or mods can reset streaks."

            }



        parts = original_message.split()



        if len(parts) < 2:

            return {

                "response": "Use !resetstreak username. Example: !resetstreak avisi"

            }



        target = normalize_viewer_name(parts[1])

        key = viewer_key(target)



        _creator_streaks_v1(resolved_creator_id)[key] = {

            "display_name": target,

            "streak": 0,

            "best_streak": 0,

            "last_checkin": None

        }



        return {

            "response": f"@{target}'s FoxBot streak has been reset."

        }



    if lower_message in ["!quest", "!questprogress"]:

        return {

            "response": format_quest_status()

        }



    if lower_message == "!quests":

        return {

            "response": "Community Quest types: foxhunt, boss, redeem, chat, arcade. Admin examples: !startquest foxhunt 10 | !startquest boss 1 | !startquest redeem 5"

        }



    if lower_message.startswith("!startquest"):

        if not admin:

            return {

                "response": f"@{username}, only the creator or mods can start community quests."

            }



        parts = original_message.split()



        if len(parts) < 3:

            return {

                "response": "Use !startquest type goal. Example: !startquest foxhunt 10"

            }



        quest_type = parts[1].strip().lower()



        allowed_types = ["foxhunt", "boss", "redeem", "chat", "arcade"]



        if quest_type not in allowed_types:

            return {

                "response": "Quest type must be one of: foxhunt, boss, redeem, chat, arcade"

            }



        try:

            goal = int(parts[2])

        except ValueError:

            return {

                "response": "Quest goal must be a number. Example: !startquest foxhunt 10"

            }



        if goal <= 0:

            return {

                "response": "Quest goal must be greater than 0."

            }



        if goal > 10000:

            return {

                "response": "Quest goal is too high. Keep it under 10000."

            }



        reward = 100



        if len(parts) >= 4:

            try:

                reward = int(parts[3])

            except ValueError:

                reward = 100



        community_quest["active"] = True

        community_quest["type"] = quest_type

        community_quest["goal"] = goal

        community_quest["progress"] = 0

        community_quest["reward"] = reward

        community_quest["claimed"] = {}

        community_quest["completed"] = False



        currency = get_currency_name()



        return {

            "response": f"Community Quest Started: {quest_type} 0/{goal}. Reward: {reward} {currency} for everyone who claims after completion."

        }



    if lower_message == "!endquest":

        if not admin:

            return {

                "response": f"@{username}, only the creator or mods can end community quests."

            }



        community_quest["active"] = False

        community_quest["type"] = None

        community_quest["goal"] = 0

        community_quest["progress"] = 0

        community_quest["claimed"] = {}

        community_quest["completed"] = False



        return {

            "response": "Community quest ended."

        }



    if lower_message.startswith("!questadd"):

        if not admin:

            return {

                "response": f"@{username}, only the creator or mods can manually add quest progress."

            }



        parts = original_message.split()

        amount = 1



        if len(parts) >= 2:

            try:

                amount = int(parts[1])

            except ValueError:

                return {

                    "response": "Use !questadd amount. Example: !questadd 5"

                }



        if amount <= 0:

            return {

                "response": "Quest progress amount must be greater than 0."

            }



        if not community_quest.get("active"):

            return {

                "response": "No community quest is active."

            }



        community_quest["progress"] = int(community_quest.get("progress", 0)) + amount



        if int(community_quest["progress"]) >= int(community_quest.get("goal", 0)):

            community_quest["progress"] = int(community_quest.get("goal", 0))

            community_quest["completed"] = True



        return {

            "response": format_quest_status()

        }



    if lower_message == "!claimquest":

        if not community_quest.get("active"):

            return {

                "response": "No community quest is active."

            }



        if not community_quest.get("completed"):

            return {

                "response": "The community quest is not complete yet. " + format_quest_status()

            }



        key = viewer_key(username)



        if key in community_quest.get("claimed", {}):

            return {

                "response": f"@{username}, you already claimed this quest reward."

            }



        reward = int(community_quest.get("reward", 100))

        currency = get_currency_name()

        new_balance = add_points(username, reward, f"claimed community quest {community_quest.get('type')}", creator_id=resolved_creator_id)



        community_quest.setdefault("claimed", {})[key] = True



        return {

            "response": f"@{username} claimed the community quest reward: +{reward} {currency}. Balance: {new_balance} {currency}."

        }



    if lower_message == "!events":

        return {

            "response": "FoxBot Events: goldenfox, spiritstorm, treasuredrop, foxfrenzy. Admins can use !startevent random or !startevent goldenfox."

        }



    if lower_message == "!event":

        if not stream_event.get("active"):

            return {

                "response": format_stream_event()

            }



        key = viewer_key(username)

        currency = get_currency_name()

        reward = int(stream_event_templates.get(stream_event.get("key"), {}).get("claim_reward", 25))



        if key in stream_event.get("claimed", {}):

            return {

                "response": f"@{username}, you already claimed the {stream_event.get('name')} event reward. {stream_event.get('description')}"

            }



        stream_event.setdefault("claimed", {})[key] = True

        new_balance = add_points(username, reward, f"stream event {stream_event.get('key')}", creator_id=resolved_creator_id)



        return {

            "response": f"@{username} claimed the {stream_event.get('name')} event reward: +{reward} {currency}. Balance: {new_balance} {currency}."

        }



    if lower_message.startswith("!startevent"):

        if not admin:

            return {

                "response": f"@{username}, only the creator or mods can start stream events."

            }



        parts = original_message.split()



        event_key = "random"

        if len(parts) >= 2:

            event_key = parts[1].strip().lower()



        template = activate_stream_event(event_key)



        if not template:

            return {

                "response": "Unknown event. Use: !startevent goldenfox, spiritstorm, treasuredrop, foxfrenzy, or random."

            }



        return {

            "response": f"Stream Event Started: {stream_event.get('name')} | {stream_event.get('description')} Type !event to claim/check."

        }



    if lower_message == "!endevent":

        if not admin:

            return {

                "response": f"@{username}, only the creator or mods can end stream events."

            }



        old_name = stream_event.get("name") or "current event"



        stream_event["active"] = False

        stream_event["name"] = None

        stream_event["key"] = None

        stream_event["description"] = None

        stream_event["claimed"] = {}



        return {

            "response": f"Stream event ended: {old_name}."

        }



    if lower_message == "!ranks":

        return {

            "response": format_rank_list()

        }



    if lower_message.startswith("!rank"):

        parts = original_message.split()

        target = username



        if len(parts) >= 2:

            target = normalize_viewer_name(parts[1])



        balance = get_balance(target, creator_id=resolved_creator_id)

        currency = get_currency_name()

        current_rank = get_fox_rank(balance)

        next_rank = get_next_fox_rank(balance)



        if next_rank:

            needed = int(next_rank["minimum"]) - int(balance)

            return {

                "response": f"@{target} is ranked {current_rank['name']} with {balance} {currency}. Next rank: {next_rank['name']} in {needed} {currency}."

            }



        return {

            "response": f"@{target} is ranked {current_rank['name']} with {balance} {currency}. Max Fox Spirit rank reached."

        }



    if lower_message == "!recognition":

        status = "ON" if recognition_settings.get("enabled", True) else "OFF"

        bonus = "ON" if recognition_settings.get("surprise_bonus_enabled", True) else "OFF"

        return {

            "response": f"FoxBot Recognition: {status} | Surprise Bonuses: {bonus} | Commands: !thankfollow user, !thanksub user, !thankvote user 10, !thanktip user 5, !mvp user, !og user"

        }



    if lower_message == "!recognitionlog":

        if not recognition_log:

            return {"response": "No recognition events logged yet."}



        parts = []

        for item in recognition_log[:5]:

            parts.append(f"{item['event_type']} @{item['username']} +{item['reward']}")



        return {"response": "Recent recognition: " + " | ".join(parts)}



    if lower_message.startswith("!recognitionon"):

        if not admin:

            return {"response": f"@{username}, only creator/mods can change recognition settings."}

        recognition_settings["enabled"] = True

        return {"response": "FoxBot automatic recognition is now ON."}



    if lower_message.startswith("!recognitionoff"):

        if not admin:

            return {"response": f"@{username}, only creator/mods can change recognition settings."}

        recognition_settings["enabled"] = False

        return {"response": "FoxBot automatic recognition is now OFF."}



    if lower_message.startswith("!thankfollow"):

        if not admin:

            return {"response": f"@{username}, only creator/mods can thank followers."}

        parts = original_message.split()

        target = parts[1] if len(parts) >= 2 else username

        return {"response": recognition_response("follow", target, creator_id=resolved_creator_id)}



    if lower_message.startswith("!thanksub"):

        if not admin:

            return {"response": f"@{username}, only creator/mods can thank subs."}

        parts = original_message.split()

        target = parts[1] if len(parts) >= 2 else username

        return {"response": recognition_response("sub", target, creator_id=resolved_creator_id)}



    if lower_message.startswith("!thankgiftsub"):

        if not admin:

            return {"response": f"@{username}, only creator/mods can thank gift subs."}

        parts = original_message.split()

        target = parts[1] if len(parts) >= 2 else username

        count = parts[2] if len(parts) >= 3 else 1

        return {"response": recognition_response("giftsub", target, count, creator_id=resolved_creator_id)}



    if lower_message.startswith("!thankvote"):

        if not admin:

            return {"response": f"@{username}, only creator/mods can thank voters."}

        parts = original_message.split()

        target = parts[1] if len(parts) >= 2 else username

        votes = parts[2] if len(parts) >= 3 else 1

        return {"response": recognition_response("vote", target, votes, creator_id=resolved_creator_id)}



    if lower_message.startswith("!thanktip"):

        if not admin:

            return {"response": f"@{username}, only creator/mods can thank tippers."}

        parts = original_message.split()

        target = parts[1] if len(parts) >= 2 else username

        amount = parts[2] if len(parts) >= 3 else 1

        return {"response": recognition_response("tip", target, amount, creator_id=resolved_creator_id)}



    if lower_message.startswith("!thankraid"):

        if not admin:

            return {"response": f"@{username}, only creator/mods can thank raids."}

        parts = original_message.split()

        target = parts[1] if len(parts) >= 2 else username

        return {"response": recognition_response("raid", target, creator_id=resolved_creator_id)}



    if lower_message.startswith("!mvp"):

        if not admin:

            return {"response": f"@{username}, only creator/mods can shout out MVPs."}

        parts = original_message.split()

        target = parts[1] if len(parts) >= 2 else username

        return {"response": recognition_response("mvp", target, creator_id=resolved_creator_id)}



    if lower_message.startswith("!og"):

        if not admin:

            return {"response": f"@{username}, only creator/mods can shout out OGs."}

        parts = original_message.split()

        target = parts[1] if len(parts) >= 2 else username

        return {"response": recognition_response("og", target, creator_id=resolved_creator_id)}



    if lower_message.startswith("!channel"):

        if not admin:

            return {"response": f"@{username}, only creator/mods can shout out channels."}

        parts = original_message.split(" ", 2)

        if len(parts) < 3:

            return {"response": "Use !channel username link. Example: !channel avisi https://blaze.stream/avisi"}

        target = normalize_viewer_name(parts[1])

        link = parts[2].strip()

        return {"response": f"Blaze channel shoutout: Go support @{target}! Follow their channel here: {link}"}



    if lower_message.startswith("!so "):

        if not admin:

            return {"response": f"@{username}, only creator/mods can use shoutouts."}

        parts = original_message.split(" ", 2)

        target = normalize_viewer_name(parts[1]) if len(parts) >= 2 else "viewer"

        if len(parts) >= 3:

            return {"response": f"Shoutout to @{target}! Go show their Blaze channel some love: {parts[2].strip()}"}

        return {"response": f"Shoutout to @{target}! Go show them love and support their Blaze content."}



    if lower_message == "!support":

        return {

            "response": "FoxBot Support Rewards: !claimchat, !claimvote amount, !claimfollow, !claimraid, !claimtip amount, !claimsub, !claimgiftsub amount, !rewardconfig"

        }



    if lower_message == "!rewardconfig":

        currency = get_currency_name()

        return {

            "response": f"Support Rewards: New Sub {support_rewards['new_sub']} {currency} | Gift Sub {support_rewards['gift_sub']} each | Tips {support_rewards['tip_per_dollar']} per $1 | Votes {support_rewards['vote_token']} per token | Follow {support_rewards['follow']} | Raid {support_rewards['raid']} | Chat {support_rewards['chat_message']}"

        }



    if lower_message == "!claimchat":

        reward = int(support_rewards.get("chat_message", 10))

        add_quest_progress("chat", 1)

        currency = get_currency_name()

        new_balance = add_points(username, reward, "chat activity", creator_id=resolved_creator_id)

        return {

            "response": f"@{username} earned {reward} {currency} for chat activity. Balance: {new_balance} {currency}."

        }



    if lower_message.startswith("!claimvote"):

        parts = original_message.split()

        amount = 1



        if len(parts) >= 2:

            try:

                amount = int(parts[1])

            except ValueError:

                return {"response": "Use !claimvote followed by a number. Example: !claimvote 10"}



        if amount <= 0:

            return {"response": "Vote amount must be greater than 0."}



        if amount > 1000:

            return {"response": "Vote claim max is 1000 at once."}



        reward = int(support_rewards.get("vote_token", 3)) * amount

        currency = get_currency_name()

        new_balance = add_points(username, reward, f"claimed {amount} vote tokens", creator_id=resolved_creator_id)

        return {

            "response": f"@{username} claimed {amount} vote tokens and earned {reward} {currency}. Balance: {new_balance} {currency}."

        }



    if lower_message == "!claimfollow":

        reward = int(support_rewards.get("follow", 100))

        currency = get_currency_name()

        new_balance = add_points(username, reward, "follow reward", creator_id=resolved_creator_id)

        return {

            "response": f"@{username} earned {reward} {currency} for following. Balance: {new_balance} {currency}."

        }



    if lower_message == "!claimraid":

        reward = int(support_rewards.get("raid", 250))

        currency = get_currency_name()

        new_balance = add_points(username, reward, "raid reward", creator_id=resolved_creator_id)

        return {

            "response": f"@{username} earned {reward} {currency} for raid support. Balance: {new_balance} {currency}."

        }



    if lower_message.startswith("!claimtip"):

        parts = original_message.split()



        if len(parts) < 2:

            return {"response": "Use !claimtip amount. Example: !claimtip 5"}



        try:

            dollars = float(parts[1])

        except ValueError:

            return {"response": "Tip amount must be a number. Example: !claimtip 5"}



        minimum = float(support_rewards.get("minimum_tip", 1))



        if dollars < minimum:

            return {"response": f"Minimum tip reward amount is ${minimum}."}



        reward = int(dollars * int(support_rewards.get("tip_per_dollar", 200)))

        currency = get_currency_name()

        new_balance = add_points(username, reward, f"tip reward ${dollars}", creator_id=resolved_creator_id)

        return {

            "response": f"@{username} earned {reward} {currency} for a ${dollars:g} tip. Balance: {new_balance} {currency}."

        }



    if lower_message == "!claimsub":

        reward = int(support_rewards.get("new_sub", 500))

        currency = get_currency_name()

        new_balance = add_points(username, reward, "subscription reward", creator_id=resolved_creator_id)

        return {

            "response": f"@{username} earned {reward} {currency} for subscribing. Balance: {new_balance} {currency}."

        }



    if lower_message.startswith("!claimgiftsub"):

        parts = original_message.split()

        amount = 1



        if len(parts) >= 2:

            try:

                amount = int(parts[1])

            except ValueError:

                return {"response": "Use !claimgiftsub amount. Example: !claimgiftsub 3"}



        if amount <= 0:

            return {"response": "Gift sub amount must be greater than 0."}



        if amount > 100:

            return {"response": "Gift sub claim max is 100 at once."}



        reward = int(support_rewards.get("gift_sub", 500)) * amount

        currency = get_currency_name()

        new_balance = add_points(username, reward, f"gift sub reward x{amount}", creator_id=resolved_creator_id)

        return {

            "response": f"@{username} claimed {amount} gifted sub rewards and earned {reward} {currency}. Balance: {new_balance} {currency}."

        }



    if lower_message in ["!balance", "!points", "!foxcoins"]:

        balance = get_balance(username, creator_id=resolved_creator_id)

        currency = get_currency_name()



        return {

            "response": f"@{username}, you have {balance} {currency}."

        }



    if lower_message.startswith("!balance ") or lower_message.startswith("!points ") or lower_message.startswith("!foxcoins "):

        parts = original_message.split()



        if len(parts) >= 2:

            target = normalize_viewer_name(parts[1])

            balance = get_balance(target, creator_id=resolved_creator_id)

            currency = get_currency_name()



            return {

                "response": f"@{target} has {balance} {currency}."

            }



    if lower_message == "!daily":

        key = viewer_key(username)

        currency = get_currency_name()

        daily_claims = _creator_economy_v1(resolved_creator_id)["daily_claims"]



        if daily_claims.get(key):

            return {

                "response": f"@{username}, you already claimed your daily {currency} this session."

            }



        reward = 25

        new_balance = add_points(username, reward, "daily", creator_id=resolved_creator_id)

        daily_claims[key] = True



        return {

            "response": f"@{username} claimed {reward} {currency}! New balance: {new_balance} {currency}."

        }



    if lower_message == "!coinleaderboard":

        return {

            "response": format_coin_leaderboard(creator_id=resolved_creator_id)

        }



    if lower_message.startswith("!givepoints"):

        if not admin:

            return {

                "response": f"@{username}, only the creator or mods can give points."

            }



        parts = original_message.split()



        if len(parts) < 3:

            return {

                "response": "Use !givepoints username amount. Example: !givepoints avisi 100"

            }



        target = normalize_viewer_name(parts[1])



        try:

            amount = int(parts[2])

        except ValueError:

            return {

                "response": "Point amount must be a number. Example: !givepoints avisi 100"

            }



        if amount <= 0:

            return {

                "response": "Point amount must be greater than 0."

            }



        new_balance = add_points(target, amount, f"given by {username}", creator_id=resolved_creator_id)

        currency = get_currency_name()



        return {

            "response": f"@{target} received {amount} {currency}! New balance: {new_balance} {currency}."

        }



    if lower_message.startswith("!takepoints"):

        if not admin:

            return {

                "response": f"@{username}, only the creator or mods can remove points."

            }



        parts = original_message.split()



        if len(parts) < 3:

            return {

                "response": "Use !takepoints username amount. Example: !takepoints avisi 50"

            }



        target = normalize_viewer_name(parts[1])



        try:

            amount = int(parts[2])

        except ValueError:

            return {

                "response": "Point amount must be a number. Example: !takepoints avisi 50"

            }



        if amount <= 0:

            return {

                "response": "Point amount must be greater than 0."

            }



        new_balance = add_points(target, -amount, f"removed by {username}", creator_id=resolved_creator_id)

        currency = get_currency_name()



        return {

            "response": f"@{target} lost {amount} {currency}. New balance: {new_balance} {currency}."

        }



    if lower_message == "!redeems":

        return {

            "response": format_redemptions()

        }



    if lower_message == "!clearredeems":

        if not admin:

            return {

                "response": f"@{username}, only the creator or mods can clear redemptions."

            }



        redemption_queue.clear()



        return {

            "response": "FoxBot redemption queue cleared."

        }



    if lower_message == "!shop":

        return {

            "response": format_reward_shop()

        }



    if lower_message.startswith("!redeem"):

        parts = original_message.split(" ", 1)



        if len(parts) < 2:

            return {

                "response": "Use !redeem followed by a reward name. Example: !redeem hug"

            }



        reward_name = parts[1].strip().lower()



        if reward_name not in reward_shop:

            return {

                "response": f"That reward does not exist. Type !shop to see rewards."

            }



        reward = reward_shop[reward_name]

        cost = int(reward.get("cost", 0))

        currency = get_currency_name()

        balance = get_balance(username, creator_id=resolved_creator_id)



        if balance < cost:

            return {

                "response": f"@{username}, you need {cost} {currency} to redeem {reward_name}. Your balance: {balance} {currency}."

            }



        new_balance = add_points(username, -cost, f"redeemed {reward_name}", creator_id=resolved_creator_id)

        response_template = reward.get("response", "@{username} redeemed a reward!")



        if reward_name == "mysterybox":

            mystery_roll = random.randint(1, 100)



            if mystery_roll <= 10:

                bonus = 150

                new_balance = add_points(username, bonus, "mysterybox jackpot", creator_id=resolved_creator_id)

                redeem_message = f"@{username} opened a mystery box and hit the JACKPOT! +{bonus} {currency}. Balance: {new_balance} {currency}."

                add_redemption(username, reward_name, redeem_message, cost, creator_handle=creator_handle)

                add_quest_progress("redeem", 1)

                return {

                    "response": redeem_message

                }



            if mystery_roll <= 35:

                bonus = 50

                new_balance = add_points(username, bonus, "mysterybox prize", creator_id=resolved_creator_id)

                redeem_message = f"@{username} opened a mystery box and found {bonus} {currency}! Balance: {new_balance} {currency}."

                add_redemption(username, reward_name, redeem_message, cost, creator_handle=creator_handle)

                add_quest_progress("redeem", 1)

                return {

                    "response": redeem_message

                }



            if mystery_roll <= 70:

                redeem_message = f"@{username} opened a mystery box and found bonus hype for the chat! Balance: {new_balance} {currency}."

                add_redemption(username, reward_name, redeem_message, cost, creator_handle=creator_handle)

                add_quest_progress("redeem", 1)

                return {

                    "response": redeem_message

                }



            redeem_message = f"@{username} opened a mystery box... and the fox ran away with the loot. Balance: {new_balance} {currency}."

            add_redemption(username, reward_name, redeem_message, cost, creator_handle=creator_handle)

            add_quest_progress("redeem", 1)

            return {

                "response": redeem_message

            }



        redeem_message = format_reward_response(response_template, username, cost, new_balance) + f" Balance: {new_balance} {currency}."

        add_redemption(username, reward_name, redeem_message, cost, creator_handle=creator_handle)

        add_quest_progress("redeem", 1)



        return {

            "response": redeem_message

        }



    if lower_message.startswith("!addreward"):

        if not admin:

            return {

                "response": f"@{username}, only the creator or mods can add rewards."

            }



        parts = original_message.split(" ", 3)



        if len(parts) < 4:

            return {

                "response": "Use !addreward name cost message. Example: !addreward hydrate 25 @{username} redeemed hydrate!"

            }



        reward_name = parts[1].strip().lower().lstrip("!")

        reward_cost_text = parts[2].strip()

        reward_response = parts[3].strip()



        try:

            reward_cost = int(reward_cost_text)

        except ValueError:

            return {

                "response": "Reward cost must be a number. Example: !addreward hydrate 25 message"

            }



        if reward_cost <= 0:

            return {

                "response": "Reward cost must be greater than 0."

            }



        if not reward_response:

            return {

                "response": "Reward message cannot be empty."

            }



        reward_shop[reward_name] = {

            "cost": reward_cost,

            "response": reward_response

        }



        currency = get_currency_name()



        return {

            "response": f"Reward {reward_name} added for {reward_cost} {currency}."

        }



    if lower_message.startswith("!delreward"):

        if not admin:

            return {

                "response": f"@{username}, only the creator or mods can delete rewards."

            }



        parts = original_message.split(" ", 1)



        if len(parts) < 2:

            return {

                "response": "Use !delreward rewardname. Example: !delreward hydrate"

            }



        reward_name = parts[1].strip().lower().lstrip("!")



        if reward_name not in reward_shop:

            return {

                "response": f"{reward_name} is not in the reward shop."

            }



        del reward_shop[reward_name]



        return {

            "response": f"Reward {reward_name} deleted."

        }



    if lower_message == "!foxhunt":

        arcade_stats["plays"] += 1

        arcade_stats["foxhunt"] += 1

        add_quest_progress("foxhunt", 1)



        currency = get_currency_name()



        outcomes = [

            ("found a glowing fox chest", 50),

            ("caught a silver Blaze fox", 35),

            ("found hidden stream loot", 25),

            ("tracked paw prints through the chat", 15),

            ("got tricked by a sneaky fox", 5),

            ("fell into a fox trap but escaped", 1),

            ("found the legendary golden fox", 100)

        ]



        event, reward = random.choice(outcomes)

        multiplier = current_event_multiplier("!foxhunt")

        reward = reward * multiplier

        reason = "foxhunt"

        if multiplier > 1:

            reason = f"foxhunt x{multiplier} stream event"

        new_balance = add_points(username, reward, reason, creator_id=resolved_creator_id)



        bonus_text = ""

        if current_event_multiplier("!foxhunt") > 1:

            bonus_text = f" {stream_event.get('name')} bonus active!"



        return {

            "response": f"@{username} went on a fox hunt and {event}! +{reward} {currency}. Balance: {new_balance} {currency}.{bonus_text}"

        }



    if lower_message == "!arcade":

        return {

            "response": "FoxBot Arcade commands: !foxhunt, !coinflip, !roll, !roll 20, !8ball your question, !rps rock/paper/scissors"

        }



    if lower_message == "!coinflip":

        arcade_stats["plays"] += 1

        add_quest_progress("arcade", 1)

        arcade_stats["coinflip"] += 1



        result = random.choice(["Heads", "Tails"])



        return {

            "response": f"FoxBot flips a coin... {result}!"

        }



    if lower_message.startswith("!roll"):

        arcade_stats["plays"] += 1

        add_quest_progress("arcade", 1)

        arcade_stats["roll"] += 1



        parts = original_message.split()

        sides = 6



        if len(parts) >= 2:

            try:

                sides = int(parts[1])

            except ValueError:

                return {

                    "response": "Use !roll or !roll followed by a number. Example: !roll 20"

                }



        if sides < 2:

            return {

                "response": "Dice must have at least 2 sides."

            }



        if sides > 1000:

            return {

                "response": "FoxBot dice can only go up to 1000 sides."

            }



        result = random.randint(1, sides)



        return {

            "response": f"@{username} rolled a D{sides} and got {result}!"

        }



    if lower_message.startswith("!8ball"):

        arcade_stats["plays"] += 1

        add_quest_progress("arcade", 1)

        arcade_stats["eightball"] += 1



        question = original_message.replace("!8ball", "", 1).strip()



        if not question:

            return {

                "response": "Ask FoxBot 8-ball a question. Example: !8ball Will I win?"

            }



        answers = [

            "Absolutely.",

            "The fox says yes.",

            "Looking strong.",

            "Signs point to yes.",

            "Not looking great.",

            "Ask again after this match.",

            "FoxBot says maybe.",

            "Big W energy.",

            "Careful... that one is risky.",

            "No doubt."

        ]



        return {

            "response": f"FoxBot 8-ball says: {random.choice(answers)}"

        }



    if lower_message.startswith("!rps"):

        arcade_stats["plays"] += 1

        add_quest_progress("arcade", 1)

        arcade_stats["rps"] += 1



        parts = original_message.split()



        if len(parts) < 2:

            return {

                "response": "Use !rps rock, !rps paper, or !rps scissors."

            }



        player_choice = parts[1].strip().lower()

        choices = ["rock", "paper", "scissors"]



        if player_choice not in choices:

            return {

                "response": "Choose rock, paper, or scissors. Example: !rps rock"

            }



        bot_choice = random.choice(choices)



        if player_choice == bot_choice:

            arcade_stats["rps_ties"] += 1

            result = "It's a tie!"

        elif (

            (player_choice == "rock" and bot_choice == "scissors") or

            (player_choice == "paper" and bot_choice == "rock") or

            (player_choice == "scissors" and bot_choice == "paper")

        ):

            arcade_stats["rps_wins"] += 1

            result = f"@{username} wins!"

        else:

            arcade_stats["rps_losses"] += 1

            result = "FoxBot wins!"



        return {

            "response": f"Rock Paper Scissors: @{username} chose {player_choice}, FoxBot chose {bot_choice}. {result}"

        }



    if lower_message == "!game":

        creator_name = os.getenv("CREATOR_NAME", "Ryan")

        return {

            "response": f"{creator_name} is currently playing: {stream_info.get('game', 'Not set yet')}"

        }



    if lower_message.startswith("!setgame"):

        if not admin:

            return {

                "response": f"@{username}, only the creator or mods can update the stream game."

            }



        new_game = original_message.replace("!setgame", "", 1).strip()



        if not new_game:

            return {

                "response": "Use !setgame followed by the game name. Example: !setgame Off The Grid"

            }



        stream_info["game"] = new_game



        return {

            "response": f"Stream game set to: {new_game}"

        }



    if lower_message == "!title":

        creator_name = os.getenv("CREATOR_NAME", "Ryan")

        return {

            "response": f"{creator_name}'s stream title: {stream_info.get('title', 'Not set yet')}"

        }



    if lower_message.startswith("!settitle"):

        if not admin:

            return {

                "response": f"@{username}, only the creator or mods can update the stream title."

            }



        new_title = original_message.replace("!settitle", "", 1).strip()



        if not new_title:

            return {

                "response": "Use !settitle followed by the stream title. Example: !settitle Playing Off The Grid with FoxBot live"

            }



        stream_info["title"] = new_title



        return {

            "response": f"Stream title set to: {new_title}"

        }



    if lower_message == "!lurk":

        clean_key = username.lower()

        stream_info["lurkers"][clean_key] = username



        return {

            "response": f"@{username} is now lurking. Thanks for supporting the stream!"

        }



    if lower_message == "!unlurk":

        clean_key = username.lower()



        if clean_key in stream_info["lurkers"]:

            del stream_info["lurkers"][clean_key]



        return {

            "response": f"@{username} is back from lurking. Welcome back!"

        }



    if lower_message == "!lurkers":

        lurker_count = len(stream_info.get("lurkers", {}))

        return {

            "response": f"Current lurkers supporting the stream: {lurker_count}"

        }



    if lower_message.startswith("!addcmd"):

        if not admin:

            return {

                "response": f"@{username}, only the creator or mods can add custom commands."

            }



        parts = original_message.split(" ", 2)



        if len(parts) < 3:

            return {

                "response": "Use !addcmd name response. Example: !addcmd discord Join the Discord here: your-link"

            }



        command_name = normalize_custom_command(parts[1])

        command_response = parts[2].strip()



        reserved_commands = {

            "!help", "!foxhelp", "!schedule", "!faq", "!socials", "!mode",

            "!giveaway", "!enter", "!entries", "!pickwinner",

            "!stats", "!leaderboard", "!hugs", "!ask", "!arcade", "!goodnight", "!endstream", "!boss", "!bossstatus", "!startboss", "!endboss", "!attack", "!powerattack", "!bossleaderboard", "!foxhunt", "!coinflip", "!roll", "!8ball", "!rps", "!balance", "!points", "!foxcoins", "!rank", "!ranks", "!event", "!events", "!startevent", "!endevent", "!checkin", "!streak", "!streaks", "!resetstreak", "!quest", "!quests", "!questprogress", "!startquest", "!endquest", "!questadd", "!claimquest", "!daily", "!shop", "!redeem", "!redeems", "!clearredeems", "!cooldowns", "!setcooldown", "!clearcooldowns", "!addreward", "!delreward", "!coinleaderboard", "!givepoints", "!takepoints",

            "!shoutout", "!addcmd", "!delcmd", "!commands"

        }



        if command_name in reserved_commands:

            return {

                "response": f"{command_name} is a built-in FoxBot command and cannot be replaced."

            }



        if not command_response:

            return {

                "response": "Custom command response cannot be empty."

            }



        _creator_commands_v1(resolved_creator_id)[command_name] = {

            "response": command_response,

            "created_by": username

        }



        return {

            "response": f"Custom command {command_name} added."

        }



    if lower_message.startswith("!delcmd"):

        if not admin:

            return {

                "response": f"@{username}, only the creator or mods can delete custom commands."

            }



        parts = original_message.split(" ", 1)



        if len(parts) < 2:

            return {

                "response": "Use !delcmd name. Example: !delcmd discord"

            }



        command_name = normalize_custom_command(parts[1])

        tenant_commands = _creator_commands_v1(resolved_creator_id)



        if command_name not in tenant_commands:

            return {

                "response": f"{command_name} is not a custom command."

            }



        del tenant_commands[command_name]



        return {

            "response": f"Custom command {command_name} deleted."

        }



    if lower_message == "!commands":

        return {

            "response": format_custom_commands(creator_id=resolved_creator_id)

        }



    if lower_message.startswith("!mode"):

        parts = original_message.split()



        if len(parts) == 1:

            return {

                "response": f"FoxBot mode is currently {bot_mode.upper()}. Available modes: hype, chill, pro"

            }



        if not admin:

            return {

                "response": f"@{username}, only the creator or mods can change FoxBot mode."

            }



        requested_mode = parts[1].strip().lower()

        allowed_modes = ["hype", "chill", "pro"]



        if requested_mode not in allowed_modes:

            return {

                "response": "Available FoxBot modes: hype, chill, pro"

            }



        bot_mode = requested_mode



        if bot_mode == "hype":

            return {

                "response": "FoxBot mode set to HYPE! Replies will bring more energy."

            }



        if bot_mode == "chill":

            return {

                "response": "FoxBot mode set to CHILL. Replies will be more relaxed."

            }



        return {

            "response": "FoxBot mode set to PRO. Replies will be cleaner and more professional."

        }



    if lower_message.startswith("!shoutout"):

        if not admin:

            return {

                "response": f"@{username}, only the creator or mods can use shoutouts."

            }



        target = original_message.replace("!shoutout", "", 1).strip()



        if not target:

            return {

                "response": "Use !shoutout followed by a username. Example: !shoutout avisi"

            }



        target = target.lstrip("@")



        return {

            "response": mode_style_response("shoutout", username=username, target=target)

        }



    if lower_message == "!socials":

        socials = os.getenv(

            "SOCIAL_LINKS",

            "Blaze: https://blaze.stream/crypt0k1ng96 | X: add your X link | YouTube: add your YouTube link"

        )

        return {

            "response": f"Follow the creator here: {socials}"

        }

    if lower_message == "!discord":
        discord_link = os.getenv("DISCORD_INVITE", "")
        if discord_link:
            return {
                "response": f"Join the community Discord: {discord_link}"
            }
        return {
            "response": "The community Discord opens soon. Type !socials to follow the creator and catch the invite."
        }

    if lower_message == "!love":
        return {
            "response": f"🦊💛 @{username} sends love to the stream! FoxBot appreciates you."
        }

    if lower_message == "!stats":

        user_data = _creator_viewer_stats_v1(resolved_creator_id).get(username.lower(), {"commands": 0})

        return {

            "response": f"@{username}, you have used {user_data.get('commands', 0)} FoxBot commands."

        }



    if lower_message == "!leaderboard":

        return {

            "response": format_leaderboard(creator_id=resolved_creator_id)

        }



    if lower_message == "!hugs":

        return {

            "response": mode_style_response("hug", username=username)

        }



    if lower_message.startswith("!ask"):

        question = original_message[4:].strip()



        if not question:

            return {

                "response": "Use !ask followed by a question."

            }



        return {

            "response": mode_style_response("ask", username=username, question=question)

        }



    if lower_message in _creator_commands_v1(resolved_creator_id):

        return {

            "response": _creator_commands_v1(resolved_creator_id)[lower_message]["response"]

        }



    return {

        "response": "Unknown command. Type !foxhelp"

    }





# ----------------------------

# Blaze OAuth
#
# /login/blaze + /oauth/blaze/callback were removed: they wrote an
# unkeyed OAuth token into the global bot_tokens dict with no check
# that the visitor was an authorized creator, letting any visitor who
# completed Blaze's consent screen clobber the token every channel's
# live chat sender was using. Bot-owner setup goes through
# /auth/blaze/login instead, which stays deliberately unlinked from
# any UI so it can't be triggered by an anonymous visitor.

# ----------------------------





@app.get("/me")

def get_my_profile():

    access_token = bot_tokens.get("accessToken")



    if not access_token:

        return {"error": "Not logged in yet. Visit /auth/blaze/login first."}



    response = requests.get(

        "https://api.blaze.stream/v1/users/profile",

        headers={

            "Authorization": f"Bearer {access_token}",

            "client-id": BLAZE_CLIENT_ID,

            "Accept": "application/json"

        }

    )



    return response.json()





# ----------------------------

# Blaze channel and chat helpers

# ----------------------------



@app.get("/blaze/find-channel")

def find_blaze_channel():

    client_id = os.getenv("BLAZE_CLIENT_ID")

    access_token = bot_tokens.get("accessToken")

    channel_slug = os.getenv("BLAZE_CHANNEL_SLUG")



    if not client_id:

        return {"success": False, "message": "Missing BLAZE_CLIENT_ID."}



    if not access_token:

        return {"success": False, "message": "Not logged in yet. Visit /auth/blaze/login first."}



    if not channel_slug:

        return {"success": False, "message": "Missing BLAZE_CHANNEL_SLUG."}



    response = requests.get(

        "https://api.blaze.stream/v1/channels",

        headers={

            "Authorization": f"Bearer {access_token}",

            "client-id": client_id,

            "Accept": "application/json"

        },

        params={

            "limit": 20,

            "type": "all",

            "slug[]": channel_slug

        }

    )



    try:

        data = response.json()

    except Exception:

        return {

            "status_code": response.status_code,

            "text": response.text

        }



    try:

        rows = data.get("data", {}).get("rows", [])

        if rows:

            proof_stats["channel_id"] = rows[0].get("id")

            proof_stats["channel_slug"] = rows[0].get("slug")

    except Exception:

        pass



    return data






@app.get("/blaze/send-test-message")

def send_test_blaze_message(request: Request):
    guard = _foxbot_require_admin_v1(request)
    if guard:
        return guard

    result = send_blaze_chat_message("FoxBot is officially connected to Blaze chat!")



    return {

        "test_message": "FoxBot is officially connected to Blaze chat!",

        "result": result

    }





@app.get("/blaze/run-command")

def run_command_in_blaze(message: str = "!foxhelp", username: str = "viewer"):

    if not bot_tokens.get("accessToken"):

        return {"success": False, "message": "Not logged in yet. Visit /auth/blaze/login first."}



    foxbot_result = chat(message=message, username=username)

    foxbot_reply = foxbot_result.get("response", "FoxBot had no response.")



    blaze_response = send_blaze_chat_message(foxbot_reply)



    proof_stats["commands_processed"] += 1

    proof_stats["last_command"] = message

    proof_stats["last_reply"] = foxbot_reply

    proof_stats["last_username"] = username

    proof_stats["last_message"] = message

    proof_stats["last_reply_at"] = time.time()



    return {

        "success": True,

        "command_received": message,

        "foxbot_reply": foxbot_reply,

        "blaze_response": blaze_response

    }





# ----------------------------

# Recent chat polling listener

# ----------------------------



def find_first_string(payload, possible_keys):

    if isinstance(payload, dict):

        for key in possible_keys:

            value = payload.get(key)



            if isinstance(value, str):

                return value



            if isinstance(value, dict) or isinstance(value, list):

                nested_text = find_first_string(value, possible_keys)

                if nested_text:

                    return nested_text



        for value in payload.values():

            nested_text = find_first_string(value, possible_keys)

            if nested_text:

                return nested_text



    if isinstance(payload, list):

        for item in payload:

            nested_text = find_first_string(item, possible_keys)

            if nested_text:

                return nested_text



    return None





def find_chat_message_text(payload):

    return find_first_string(payload, ["text", "content", "body", "message"])





def find_chat_username(payload):

    return find_first_string(payload, ["displayName", "username", "slug", "name"]) or "viewer"


def _foxbot_resolve_auto_event_username_v1(item, message_text):
    """Vote events carry the real voter identity nested under
    actionInfo.senderDisplayName (actionInfo.senderId as fallback), not any
    of the generic top-level/nested keys find_chat_username searches for
    (displayName/username/slug/name) -- confirmed against a real captured
    payload from viewer_fallback_debug_log. Scoped narrowly to messages that
    already match parse_auto_chat_event's own vote-keyword check, so this
    never touches follow/raid/boss/normal-chat rows -- those haven't been
    confirmed to share this shape, and guessing at their structure isn't
    part of this fix. A real chat message that merely mentions "vote" has
    no actionInfo at all, so it falls straight through to the unchanged
    find_chat_username(item) call below -- the direct-message path is
    untouched."""
    lower = str(message_text or "").lower()

    if "voted" in lower or "vote" in lower:
        action_info = item.get("actionInfo") if isinstance(item, dict) else None

        if isinstance(action_info, dict):
            sender_display_name = action_info.get("senderDisplayName")
            if isinstance(sender_display_name, str) and sender_display_name.strip():
                return sender_display_name.strip()

            sender_id = action_info.get("senderId")
            if sender_id:
                return str(sender_id).strip()

    return find_chat_username(item)


def _foxbot_item_has_vote_signal_v1(item):
    """True only when a chat-feed row carries Blaze's own structured vote
    marker: top-level type=="vote" AND a sibling actionInfo dict --
    confirmed present together on all 8 real captures from
    viewer_fallback_debug_log ("X is giving N Votes to Y" rows). Both
    required, not either/or: type is the authoritative "this is a vote"
    signal, actionInfo is what carries the data needed to attribute
    (senderDisplayName) and size (amount) the reward -- a type=="vote" row
    with no actionInfo can't be safely attributed or sized, so it doesn't
    count either.

    A plain chat message that merely contains "vote"/"voted" as text --
    whether from a bot (e.g. ScurvyBot) or a human typing "I voted!" --
    has neither field, since it isn't an actual Blaze vote action, and
    returns False. parse_auto_chat_event's vote branch gates on this
    instead of the old bare keyword match."""
    if not isinstance(item, dict):
        return False
    return item.get("type") == "vote" and isinstance(item.get("actionInfo"), dict)


# === TEMP DIAGNOSTIC — remove once a real payload has been captured ===
_FOXBOT_DEBUG_SENSITIVE_KEY_MARKERS = (
    "token", "secret", "password", "auth", "cookie", "session",
    "email", "phone", "ip", "key", "credential",
)


def _foxbot_redact_sensitive_debug_v1(value, _depth=0):
    """Recursively mask values whose key looks sensitive, keeping the
    overall shape intact so the payload structure is still inspectable.
    Depth-capped so a pathological/self-referential payload can't blow up
    this purely-diagnostic path."""
    if _depth > 8:
        return "[TRUNCATED]"

    if isinstance(value, dict):
        redacted = {}
        for key, inner in value.items():
            key_lower = str(key).lower()
            if any(marker in key_lower for marker in _FOXBOT_DEBUG_SENSITIVE_KEY_MARKERS):
                redacted[key] = "[REDACTED]"
            else:
                redacted[key] = _foxbot_redact_sensitive_debug_v1(inner, _depth + 1)
        return redacted

    if isinstance(value, list):
        return [_foxbot_redact_sensitive_debug_v1(item, _depth + 1) for item in value[:20]]

    return value


def _foxbot_capture_viewer_fallback_debug_v1(event_type, raw_item):
    """TEMP diagnostic only -- records a redacted copy of the raw chat item
    that produced a 'viewer' fallback name on a vote/follow auto-event, so
    the next live occurrence gives us a real payload shape to design the
    actual name-extraction fix against. No fallback/recognition behavior
    reads from this list. Remove this function, its call site, the
    viewer_fallback_debug_log global, and the persistence wiring for it
    once a real payload has been captured."""
    from datetime import datetime, timezone

    try:
        entry = {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "event_type": event_type,
            "raw_item": _foxbot_redact_sensitive_debug_v1(raw_item),
        }
        viewer_fallback_debug_log.append(entry)
        del viewer_fallback_debug_log[:-VIEWER_FALLBACK_DEBUG_LOG_CAP]
    except Exception:
        pass
# === End TEMP DIAGNOSTIC ===





def find_chat_message_id(payload):

    return find_first_string(payload, ["messageId", "id"]) or str(payload)[:200]




def _foxbot_parse_iso8601_v1(value):
    from datetime import datetime, timezone

    text = str(value or "").strip()
    if not text:
        return None

    if text.endswith("Z") or text.endswith("z"):
        text = text[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.timestamp()


def _foxbot_normalize_epoch_v1(value):
    try:
        epoch = float(value)
    except (TypeError, ValueError):
        return None

    # Blaze/most APIs send seconds (~1.7e9 today); some send milliseconds
    # (~1.7e12 today). Anything above 1e12 can only be milliseconds.
    if epoch > 1e12:
        epoch = epoch / 1000.0

    return epoch


def _foxbot_find_first_numeric_v1(payload, possible_keys):
    if isinstance(payload, dict):
        for key in possible_keys:
            value = payload.get(key)

            if isinstance(value, bool):
                continue

            if isinstance(value, (int, float)):
                return value

            if isinstance(value, str) and value.strip():
                try:
                    return float(value.strip())
                except ValueError:
                    pass

            if isinstance(value, (dict, list)):
                nested = _foxbot_find_first_numeric_v1(value, possible_keys)
                if nested is not None:
                    return nested

        for value in payload.values():
            nested = _foxbot_find_first_numeric_v1(value, possible_keys)
            if nested is not None:
                return nested

    if isinstance(payload, list):
        for item in payload:
            nested = _foxbot_find_first_numeric_v1(item, possible_keys)
            if nested is not None:
                return nested

    return None


def find_chat_message_created_at(payload):
    """Return a message's creation time as a UTC epoch float, or None.

    Pinned to Blaze's confirmed "createdAt" ISO8601 field (e.g.
    "2026-07-25T06:32:05.000Z"), with a few plausible key/format
    fallbacks kept defensive in case a row shape ever differs.
    """

    timestamp_keys = ["createdAt", "created_at", "timestamp", "sentAt", "sent_at"]

    raw_value = find_first_string(payload, timestamp_keys)
    if raw_value:
        parsed = _foxbot_parse_iso8601_v1(raw_value)
        if parsed is not None:
            return parsed

    numeric_value = _foxbot_find_first_numeric_v1(payload, timestamp_keys + ["ts", "time"])
    if numeric_value is not None:
        return _foxbot_normalize_epoch_v1(numeric_value)

    return None



def extract_rows_from_blaze_response(data):

    if isinstance(data, dict):

        if isinstance(data.get("data"), dict):

            if isinstance(data["data"].get("rows"), list):

                return data["data"]["rows"]



            if isinstance(data["data"].get("messages"), list):

                return data["data"]["messages"]



        if isinstance(data.get("rows"), list):

            return data["rows"]



        if isinstance(data.get("messages"), list):

            return data["messages"]



    return []










@app.get("/blaze/check-recent-messages")

def check_recent_blaze_messages():

    return get_recent_blaze_messages()





@app.get("/blaze/start-polling-listener")

def start_polling_listener():

    global polling_thread



    if polling_thread and polling_thread.is_alive():

        # Flip running back on so a stop followed by a quick start actually resumes

        # instead of letting the winding-down thread exit.

        polling_status["running"] = True

        proof_stats["listener_running"] = True

        return {

            "success": True,

            "message": "Polling listener is already running.",

            "status": polling_status

        }



    polling_status["running"] = True

    polling_thread = threading.Thread(target=blaze_polling_worker, daemon=True)

    polling_thread.start()

    _foxbot_events_v1.emit_event(
        _foxbot_events_v1.resolve_owner_handle(), "listener", detail={"state": "connected"}
    )



    return {

        "success": True,

        "message": "FoxBot polling listener started.",

        "status": polling_status

    }





@app.get("/blaze/stop-polling-listener")

def stop_polling_listener():

    polling_status["running"] = False

    polling_status["started_at"] = None

    proof_stats["listener_running"] = False

    _foxbot_events_v1.emit_event(
        _foxbot_events_v1.resolve_owner_handle(), "listener", detail={"state": "disconnected"}
    )



    return {

        "success": True,

        "message": "FoxBot polling listener stopped.",

        "status": polling_status

    }





@app.get("/blaze/polling-status")

def get_polling_status():

    return polling_status




@app.on_event("startup")

def foxbot_auto_start_listener_v1():

    """Start the chat listener automatically on boot when Blaze is configured.

    Means the dashboard comes up already listening after every Render deploy —

    no manual Start Listener click needed. Set FOXBOT_AUTO_START_LISTENER=false to opt out."""

    global polling_thread



    opt_out = (os.getenv("FOXBOT_AUTO_START_LISTENER", "true") or "").strip().lower()

    if opt_out in ["0", "false", "no", "off"]:

        return



    if not os.getenv("BLAZE_CLIENT_ID") or not os.getenv("BLAZE_CHANNEL_ID"):

        return



    access_token, _token_source = resolve_blaze_access_token()

    if not access_token:

        return



    if polling_thread and polling_thread.is_alive():

        return



    polling_status["running"] = True

    proof_stats["listener_running"] = True

    polling_thread = threading.Thread(target=blaze_polling_worker, daemon=True)

    polling_thread.start()





# ----------------------------

# Judge / proof endpoints

# ----------------------------



@app.get("/proof")

def proof_panel():

    proof_stats["blaze_connected"] = bool(bot_tokens.get("accessToken"))

    proof_stats["listener_running"] = polling_status.get("running", False)

    proof_stats["messages_checked"] = polling_status.get("checks", 0)

    proof_stats["messages_seen"] = polling_status.get("messages_seen", 0)

    proof_stats["channel_id"] = os.getenv("BLAZE_CHANNEL_ID")

    proof_stats["channel_slug"] = os.getenv("BLAZE_CHANNEL_SLUG")



    return {

        "project": "FoxBot AI Chatbot",

        "proof": proof_stats,

        "polling_status": {

            "running": polling_status.get("running"),

            "checks": polling_status.get("checks"),

            "messages_seen": polling_status.get("messages_seen"),

            "commands_processed": polling_status.get("commands_processed"),

            "last_error": polling_status.get("last_error")

        }

    }





@app.get("/project-status")

def project_status():

    return {

        "project": "FoxBot AI Chatbot",

        "status": "working",

        "live_app": "https://foxbot-ai-chatbot.onrender.com",

        "pages": {

            "homepage": "/",

            "dashboard": "/dashboard",

            "judges_page": "/judges",

            "project_status": "/project-status",

            "live_proof": "/proof"

        },

        "blaze_integration": {

            "oauth_login": True,

            "channel_lookup": True,

            "send_chat_messages": True,

            "read_recent_chat": True,

            "polling_listener": True,

            "automatic_command_replies": True,

            "live_proof_panel": True,

            "fox_spirit_ranks": True,

            "random_stream_events": True,

            "community_quests": True,

            "viewer_streaks": True,

            "support_rewards": True

        },

        "commands": [

            "!foxhelp",

            "!schedule",

            "!faq",

            "!giveaway",

            "!enter",

            "!entries",

            "!pickwinner",

            "!hugs",

            "!ask"

        ],

        "creator_tools": [

            "giveaway tracking",

            "duplicate entry protection",

            "random winner picker",

            "control dashboard",

            "live chat command listener",

            "live proof panel"

        ],

        "tech_stack": [

            "Python",

            "FastAPI",

            "Render",

            "Blaze OAuth",

            "Blaze Chat API",

            "HTML",

            "CSS",

            "JavaScript"

        ]

    }

@app.get("/blaze/judge-demo")

def judge_demo():

    if not bot_tokens.get("accessToken"):

        return {"success": False, "message": "Not logged in yet. Visit /auth/blaze/login first."}



    demo_steps = [

        "FoxBot Judge Demo starting now!",

        chat(message="!foxhelp", username="JudgeDemo").get("response"),

        chat(message="!giveaway", username="JudgeDemo").get("response"),

        chat(message="!enter", username="JudgeDemo").get("response"),

        chat(message="!entries", username="JudgeDemo").get("response"),

        chat(message="!pickwinner", username="JudgeDemo").get("response"),

        "FoxBot Judge Demo complete. Blaze OAuth, chat posting, commands, and giveaway tools are working."

    ]



    results = []



    for step in demo_steps:

        result = send_blaze_chat_message(step)

        results.append({

            "message_sent": step,

            "blaze_response": result

        })

        time.sleep(1)



    proof_stats["commands_processed"] += 5

    proof_stats["last_command"] = "judge-demo"

    proof_stats["last_reply"] = "Full judge demo completed."

    proof_stats["last_username"] = "JudgeDemo"

    proof_stats["last_message"] = "judge-demo"



    return {

        "success": True,

        "message": "Judge demo completed.",

        "steps_sent": len(results),

        "results": results

    }





features_html = """

<!DOCTYPE html>

<html lang="en">

<head>

    <meta charset="UTF-8">

    <meta name="viewport" content="width=device-width, initial-scale=1.0">

    <title>FoxBot Features</title>

    <style>

        body {

            margin: 0;

            font-family: Arial, sans-serif;

            background: linear-gradient(135deg, #0b1020, #111827, #1f2937);

            color: white;

            padding: 30px;

        }



        .page {

            max-width: 1050px;

            margin: 0 auto;

            background: rgba(17, 24, 39, 0.95);

            border: 1px solid rgba(255,255,255,0.08);

            border-radius: 24px;

            padding: 32px;

            box-shadow: 0 12px 40px rgba(0,0,0,0.35);

        }



        .brand {

            display: flex;

            align-items: center;

            gap: 18px;

            margin-bottom: 24px;

        }



        .brand img {

            width: 82px;

            height: 82px;

            border-radius: 20px;

            object-fit: cover;

            border: 2px solid rgba(249, 115, 22, 0.45);

        }



        h1 {

            margin: 0;

            font-size: 38px;

        }



        h2 {

            color: #fdba74;

            margin-top: 30px;

        }



        p, li {

            color: #d1d5db;

            line-height: 1.6;

            font-size: 16px;

        }



        .badge {

            display: inline-block;

            background: rgba(249, 115, 22, 0.16);

            color: #fdba74;

            border: 1px solid rgba(249, 115, 22, 0.3);

            padding: 8px 14px;

            border-radius: 999px;

            font-size: 14px;

            margin-top: 10px;

        }



        .grid {

            display: grid;

            grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));

            gap: 16px;

            margin-top: 22px;

        }



        .card {

            background: #0f172a;

            border: 1px solid rgba(255,255,255,0.08);

            border-radius: 18px;

            padding: 20px;

        }



        .card h3 {

            color: #fdba74;

            margin-top: 0;

        }



        .links {

            display: flex;

            flex-wrap: wrap;

            gap: 12px;

            margin-top: 26px;

        }



        .button {

            display: inline-block;

            text-decoration: none;

            background: linear-gradient(135deg, #f97316, #ea580c);

            color: white;

            padding: 14px 18px;

            border-radius: 14px;

            font-weight: bold;

        }



        .secondary {

            background: linear-gradient(135deg, #2563eb, #1d4ed8);

        }



        code {

            color: #93c5fd;

            background: #020617;

            padding: 2px 6px;

            border-radius: 6px;

        }

    </style>

</head>



<body>

    <div class="page">

        <div class="brand">

            <img src="/static/foxbot-logo.png" alt="FoxBot Logo">

            <div>

                <h1>FoxBot Features</h1>

                <div class="badge">A Blaze creator assistant built for real stream engagement</div>

            </div>

        </div>



        <p>

            FoxBot AI Chatbot is built to help Blaze creators automate chat engagement,

            run giveaways, answer repeated questions, and create more interactive live streams.

        </p>



        <div class="links">

            <a class="button" href="/dashboard">Open Dashboard</a>

            <a class="button secondary" href="/judges">Judges Page</a>

            <a class="button secondary" href="/features">Features</a>

            <a class="button secondary" href="/proof">Live Proof</a>

            <a class="button secondary" href="/">Demo Chat</a>

        </div>



        <h2>Who FoxBot Helps</h2>



        <div class="grid">

            <div class="card">

                <h3>For Creators</h3>

                <ul>

                    <li>Automates repeated chat replies</li>

                    <li>Runs giveaways during streams</li>

                    <li>Tracks entries and blocks duplicate entries</li>

                    <li>Protects admin-only commands</li>

                    <li>Posts directly into real Blaze chat</li>

                </ul>

            </div>



            <div class="card">

                <h3>For Viewers</h3>

                <ul>

                    <li>Simple commands like <code>!foxhelp</code> and <code>!enter</code></li>

                    <li>Fast answers to common questions</li>

                    <li>Interactive giveaway participation</li>

                    <li>More active and fun live chat</li>

                </ul>

            </div>



            <div class="card">

                <h3>For Judges</h3>

                <ul>

                    <li>Live Render deployment</li>

                    <li>Real Blaze OAuth connection</li>

                    <li>Real Blaze chat message posting</li>

                    <li>Live proof panel showing activity</li>

                    <li>One-click Judge Demo Mode</li>

                </ul>

            </div>

        </div>



        <h2>Live Features</h2>



        <div class="grid">

            <div class="card">

                <h3>Blaze OAuth</h3>

                <p>FoxBot connects to Blaze through OAuth so the creator can authorize the bot securely.</p>

            </div>



            <div class="card">

                <h3>Chat Commands</h3>

                <p>FoxBot supports public commands like <code>!foxhelp</code>, <code>!schedule</code>, <code>!faq</code>, <code>!enter</code>, and <code>!entries</code>.</p>

            </div>



            <div class="card">

                <h3>Protected Admin Commands</h3>

                <p>Commands like <code>!giveaway</code> and <code>!pickwinner</code> are protected for the creator or approved admins.</p>

            </div>



            <div class="card">

                <h3>Giveaway System</h3>

                <p>FoxBot can start giveaways, track entries, stop duplicate entries, show entries, and pick a random winner.</p>

            </div>



            <div class="card">

                <h3>Live Proof Panel</h3>

                <p>The dashboard shows Blaze connection status, listener status, messages checked, commands processed, last user, and last command.</p>

            </div>



            <div class="card">

                <h3>Judge Demo Mode</h3>

                <p>One endpoint runs a full automated demo by sending test messages, running commands, starting a giveaway, entering a user, and picking a winner.</p>

            </div>

        </div>



        <h2>Roadmap</h2>



        <div class="grid">

            <div class="card">

                <h3>Persistent Token Storage</h3>

                <p>Store refresh tokens securely so creators do not need to reconnect after Render restarts.</p>

            </div>



            <div class="card">

                <h3>Full AI Response Mode</h3>

                <p>Upgrade <code>!ask</code> from demo mode into a real AI assistant once billing is enabled.</p>

            </div>



            <div class="card">

                <h3>OBS Overlay</h3>

                <p>Add a browser-source overlay for live giveaways, latest entries, and winner announcements.</p>

            </div>



            <div class="card">

                <h3>Follower and Subscriber Shoutouts</h3>

                <p>Use Blaze activity data to celebrate new followers, subscribers, gifts, and other community events.</p>

            </div>



            <div class="card">

                <h3>True Socket.IO Events</h3>

                <p>Move from polling to full real-time events once the Socket.IO connection details are fully stable.</p>

            </div>



            <div class="card">

                <h3>Creator Settings UI</h3>

                <p>Let creators edit schedules, FAQs, prizes, social links, and bot personality directly from the dashboard.</p>

            </div>

        </div>

    </div>






</body>

</html>

"""





@app.get("/features", response_class=HTMLResponse)

def features_page():

    return features_html





giveaway_overlay_html = """

<!DOCTYPE html>

<html lang="en">

<head>

    <meta charset="UTF-8">

    <meta name="viewport" content="width=device-width, initial-scale=1.0">

    <title>FoxBot Giveaway Overlay</title>

    <style>

        body {

            margin: 0;

            background: transparent;

            font-family: Arial, sans-serif;

            color: white;

            overflow: hidden;

        }



        .overlay {

            width: 100vw;

            min-height: 100vh;

            display: flex;

            align-items: center;

            justify-content: center;

            padding: 30px;

        }



        .card {

            width: 720px;

            background: rgba(15, 23, 42, 0.92);

            border: 2px solid rgba(249, 115, 22, 0.65);

            border-radius: 30px;

            padding: 32px;

            box-shadow: 0 18px 60px rgba(0, 0, 0, 0.45);

            text-align: center;

        }



        .logo {

            width: 90px;

            height: 90px;

            border-radius: 22px;

            object-fit: cover;

            border: 2px solid rgba(249, 115, 22, 0.65);

            margin-bottom: 14px;

        }



        h1 {

            margin: 0;

            font-size: 46px;

            color: #fdba74;

        }



        .subtitle {

            margin-top: 8px;

            color: #cbd5e1;

            font-size: 20px;

        }



        .prize {

            margin-top: 24px;

            font-size: 28px;

            font-weight: bold;

        }



        .stats {

            display: grid;

            grid-template-columns: repeat(3, 1fr);

            gap: 14px;

            margin-top: 28px;

        }



        .stat {

            background: rgba(255,255,255,0.06);

            border-radius: 18px;

            padding: 18px;

        }



        .label {

            color: #94a3b8;

            font-size: 14px;

            text-transform: uppercase;

            letter-spacing: 1px;

            margin-bottom: 8px;

        }



        .value {

            font-size: 26px;

            font-weight: bold;

            color: white;

            word-break: break-word;

        }



        .winner {

            margin-top: 28px;

            padding: 20px;

            background: linear-gradient(135deg, rgba(249,115,22,0.22), rgba(234,88,12,0.18));

            border: 1px solid rgba(249,115,22,0.45);

            border-radius: 22px;

        }



        .winner .value {

            font-size: 34px;

            color: #fdba74;

        }



        .footer {

            margin-top: 22px;

            color: #94a3b8;

            font-size: 16px;

        }

    </style>

</head>

<body>

    <div class="overlay">

        <div class="card">

            <img src="/static/foxbot-logo.png" class="logo" alt="FoxBot Logo">

            <h1>FoxBot Giveaway</h1>

            <div class="subtitle">Type !enter in Blaze chat to join</div>



            <div class="prize" id="prize">Prize loading...</div>



            <div class="stats">

                <div class="stat">

                    <div class="label">Status</div>

                    <div class="value" id="status">Loading</div>

                </div>



                <div class="stat">

                    <div class="label">Entries</div>

                    <div class="value" id="entries">0</div>

                </div>



                <div class="stat">

                    <div class="label">Latest Entry</div>

                    <div class="value" id="latest">None</div>

                </div>

            </div>



            <div class="winner">

                <div class="label">Winner</div>

                <div class="value" id="winner">Not picked yet</div>

            </div>



            <div class="footer">Powered by FoxBot AI Chatbot on Blaze</div>

        </div>

    </div>



    <script>

        async function refreshOverlay() {

            try {

                const response = await fetch('/overlay/giveaway-data');

                const data = await response.json();



                document.getElementById("prize").textContent = "Prize: " + data.prize;

                document.getElementById("status").textContent = data.active ? "Live" : "Waiting";

                document.getElementById("entries").textContent = data.entry_count;

                document.getElementById("latest").textContent = data.latest_entry ? "@" + data.latest_entry : "None";

                document.getElementById("winner").textContent = data.winner ? "@" + data.winner : "Not picked yet";

            } catch (error) {

                document.getElementById("status").textContent = "Error";

            }

        }



        refreshOverlay();

        setInterval(refreshOverlay, 3000);

    </script>

</body>

</html>

"""





@app.get("/overlay/giveaway", response_class=HTMLResponse)

def giveaway_overlay_page():

    return giveaway_overlay_html





@app.get("/overlay/giveaway-data")

def giveaway_overlay_data():

    # Reads FOXBOT_STUDIO_GIVEAWAY_STATE_V3 -- the same store the REST
    # /api/studio/giveaways/* endpoints and chat()'s !enter/!giveaway
    # intercept already read/write.
    state = foxbot_studio_giveaway_state_v3()

    return {

        "active": bool(state.get("active", False)),

        "prize": state.get("prize") or os.getenv("GIVEAWAY_PRIZE", "a Blaze community prize"),

        "entry_count": len(giveaway_entries),

        "entries": giveaway_entries,

        "latest_entry": state.get("last_entry"),

        "winner": state.get("last_winner")

    }





@app.get("/viewer-stats")

def viewer_stats_endpoint(request: Request):

    # Bot Connection C2 Step 1, Tier 1: same resolution path as /foxcoins
    # and /api/studio/stats/live. blaze_id absent (Basic Auth, or no Blaze
    # session) falls back to tenant-zero, keeping this byte-identical to
    # today's _tenant_zero_viewer_stats() call until a second creator is
    # actually approved and mapped.
    resolved_creator_id = _foxbot_resolve_creator_id_v1(
        blaze_id=getattr(request.state, "blaze_id", None)
    )

    tenant_stats = _creator_viewer_stats_v1(resolved_creator_id)

    return {

        "viewer_count": len(tenant_stats),

        "leaderboard": sorted(

            tenant_stats.values(),

            key=lambda item: item.get("commands", 0),

            reverse=True

        )

    }





@app.get("/socials")

def socials_endpoint():

    return {

        "command": "!socials",

        "social_links": os.getenv(

            "SOCIAL_LINKS",

            "Blaze: https://blaze.stream/crypt0k1ng96 | X: add your X link | YouTube: add your YouTube link"

        )

    }





@app.get("/bot-mode")

def bot_mode_endpoint():

    return {

        "current_mode": bot_mode,

        "available_modes": ["hype", "chill", "pro"],

        "public_command": "!mode",

        "admin_commands": ["!mode hype", "!mode chill", "!mode pro"]

    }





@app.get("/custom-commands")

def custom_commands_endpoint(request: Request):

    # Bot Connection C2 Step 1, Tier 1: same resolution path as /foxcoins
    # and /api/studio/stats/live. blaze_id absent (Basic Auth, or no Blaze
    # session) falls back to tenant-zero, keeping this byte-identical to
    # today's _tenant_zero_commands() call until a second creator is
    # actually approved and mapped.
    resolved_creator_id = _foxbot_resolve_creator_id_v1(
        blaze_id=getattr(request.state, "blaze_id", None)
    )

    tenant_commands = _creator_commands_v1(resolved_creator_id)

    return {

        "count": len(tenant_commands),

        "commands": tenant_commands,

        "examples": [

            "!addcmd discord Join the Discord here: your-link",

            "!commands",

            "!discord",

            "!delcmd discord"

        ]

    }





@app.get("/stream-info")

def stream_info_endpoint():

    return {

        "game": stream_info.get("game"),

        "title": stream_info.get("title"),

        "lurker_count": len(stream_info.get("lurkers", {})),

        "lurkers": list(stream_info.get("lurkers", {}).values()),

        "commands": [

            "!game",

            "!setgame Off The Grid",

            "!title",

            "!settitle Playing Off The Grid with FoxBot live",

            "!lurk",

            "!unlurk",

            "!lurkers"

        ]

    }





judge_demo_html = """

<!DOCTYPE html>

<html lang="en">

<head>

    <meta charset="UTF-8">

    <meta name="viewport" content="width=device-width, initial-scale=1.0">

    <title>FoxBot Judge Demo</title>

    <style>

        body {

            margin: 0;

            font-family: Arial, sans-serif;

            background: radial-gradient(circle at top, #1f2937, #020617 70%);

            color: white;

        }



        .wrap {

            max-width: 1180px;

            margin: 0 auto;

            padding: 40px 22px;

        }



        .hero {

            background: rgba(15, 23, 42, 0.9);

            border: 1px solid rgba(249, 115, 22, 0.5);

            border-radius: 28px;

            padding: 34px;

            box-shadow: 0 20px 70px rgba(0,0,0,0.35);

        }



        .top {

            display: flex;

            gap: 20px;

            align-items: center;

            flex-wrap: wrap;

        }



        .logo {

            width: 92px;

            height: 92px;

            border-radius: 24px;

            object-fit: cover;

            border: 2px solid rgba(249, 115, 22, 0.7);

        }



        h1 {

            margin: 0;

            font-size: 46px;

            color: #fdba74;

        }



        .subtitle {

            margin-top: 8px;

            color: #cbd5e1;

            font-size: 19px;

            line-height: 1.5;

        }



        .nav {

            margin-top: 24px;

            display: flex;

            gap: 12px;

            flex-wrap: wrap;

        }



        .nav a {

            color: white;

            text-decoration: none;

            background: rgba(255,255,255,0.08);

            border: 1px solid rgba(255,255,255,0.12);

            border-radius: 999px;

            padding: 10px 14px;

        }



        .grid {

            display: grid;

            grid-template-columns: 1fr 1fr;

            gap: 18px;

            margin-top: 22px;

        }



        .panel {

            background: rgba(15, 23, 42, 0.78);

            border: 1px solid rgba(148, 163, 184, 0.22);

            border-radius: 22px;

            padding: 22px;

        }



        .panel h2 {

            margin: 0 0 14px;

            color: #fdba74;

        }



        .buttons {

            display: flex;

            gap: 10px;

            flex-wrap: wrap;

        }



        button {

            cursor: pointer;

            border: 0;

            background: linear-gradient(135deg, #f97316, #ea580c);

            color: white;

            font-weight: 800;

            border-radius: 14px;

            padding: 12px 14px;

            box-shadow: 0 8px 22px rgba(249,115,22,0.18);

        }



        button.secondary {

            background: rgba(255,255,255,0.1);

            border: 1px solid rgba(255,255,255,0.12);

        }



        .output {

            min-height: 170px;

            background: rgba(2, 6, 23, 0.78);

            border: 1px solid rgba(148, 163, 184, 0.22);

            border-radius: 18px;

            padding: 16px;

            white-space: pre-wrap;

            color: #e2e8f0;

            line-height: 1.5;

            overflow: auto;

        }



        .feature-list {

            display: grid;

            grid-template-columns: repeat(2, minmax(0, 1fr));

            gap: 10px;

        }



        .feature {

            background: rgba(255,255,255,0.06);

            border-radius: 14px;

            padding: 12px;

            color: #e2e8f0;

        }



        code {

            color: #fdba74;

            font-weight: bold;

        }



        @media (max-width: 800px) {

            .grid {

                grid-template-columns: 1fr;

            }



            h1 {

                font-size: 36px;

            }



            .feature-list {

                grid-template-columns: 1fr;

            }

        }

    </style>

</head>

<body>

    <div class="wrap">

        <section class="hero">

            <div class="top">

                <img src="/static/foxbot-logo.png" class="logo" alt="FoxBot Logo">

                <div>

                    <h1>FoxBot Judge Demo</h1>

                    <div class="subtitle">

                        A Blaze-connected AI creator chatbot built for live stream engagement, giveaways,

                        OBS overlays, viewer stats, custom commands, and creator moderation tools.

                    </div>

                </div>

            </div>



            <div class="nav">

                <a href="/">Home</a>

                <a href="/demo">Judge Demo</a>

                <a href="/smoke-test">Smoke Test</a>

                <a href="/goodnight">Goodnight</a>

                <a href="/dashboard">Dashboard</a>

                <a href="/demo">Demo</a>

                <a href="/economy">Economy</a>

                <a href="/features">Features</a>

                <a href="/judges">Judges</a>

                <a href="/proof">Live Proof</a>

                <a href="/overlay/giveaway">OBS Overlay</a>

            </div>

        </section>



        <div class="grid">

            <section class="panel">

                <h2>One-Click Command Tests</h2>

                <div class="buttons">

                    <button onclick="runCommand('!foxhelp')">!foxhelp</button>

                    <button onclick="runCommand('!goodnight')">!goodnight</button>

                    <button onclick="runCommand('!socials')">!socials</button>

                    <button onclick="runCommand('!mode')">!mode</button>

                    <button onclick="runCommand('!mode hype')">!mode hype</button>

                    <button onclick="runCommand('!giveaway')">!giveaway</button>

                    <button onclick="runCommand('!enter')">!enter</button>

                    <button onclick="runCommand('!entries')">!entries</button>

                    <button onclick="runCommand('!pickwinner')">!pickwinner</button>

                    <button onclick="runCommand('!leaderboard')">!leaderboard</button>

                    <button onclick="runCommand('!arcade')">!arcade</button>

                    <button onclick="runCommand('!startboss Cyber Fox Dragon')">start boss</button>

                    <button onclick="runCommand('!boss')">!boss</button>

                    <button onclick="runCommand('!attack')">!attack</button>

                    <button onclick="runCommand('!powerattack')">!powerattack</button>

                    <button onclick="runCommand('!bossleaderboard')">boss leaderboard</button>

                    <button onclick="runCommand('!foxhunt')">!foxhunt</button>

                    <button onclick="runCommand('!startboss 500 Cyber Fox Dragon')">start boss</button>

                    <button onclick="runCommand('!attack')">attack boss</button>

                    <button onclick="runCommand('!powerattack')">power attack</button>

                    <button onclick="runCommand('!daily')">!daily</button>

                    <button onclick="runCommand('!rank')">!rank</button>

                    <button onclick="runCommand('!ranks')">!ranks</button>

                    <button onclick="runCommand('!startevent goldenfox')">start event</button>

                    <button onclick="runCommand('!event')">claim event</button>

                    <button onclick="runCommand('!startquest foxhunt 3')">start quest</button>

                    <button onclick="runCommand('!quest')">quest status</button>

                    <button onclick="runCommand('!checkin')">check in</button>

                    <button onclick="runCommand('!streak')">streak</button>

                    <button onclick="runCommand('!support')">support rewards</button>

                    <button onclick="runCommand('!rewardconfig')">reward config</button>

                    <button onclick="runCommand('!balance')">!balance</button>

                    <button onclick="runCommand('!rank')">!rank</button>

                    <button onclick="runCommand('!ranks')">!ranks</button>

                    <button onclick="runCommand('!events')">!events</button>

                    <button onclick="runCommand('!quests')">!quests</button>

                    <button onclick="runCommand('!startquest foxhunt 3')">start foxhunt quest</button>

                    <button onclick="runCommand('!checkin')">!checkin</button>

                    <button onclick="runCommand('!streak')">!streak</button>

                    <button onclick="runCommand('!streaks')">!streaks</button>

                    <button onclick="runCommand('!quest')">!quest</button>

                    <button onclick="runCommand('!claimquest')">!claimquest</button>

                    <button onclick="runCommand('!startevent goldenfox')">start Golden Fox</button>

                    <button onclick="runCommand('!event')">!event</button>

                    <button onclick="runCommand('!shop')">!shop</button>

                    <button onclick="runCommand('!redeem hug')">redeem hug</button>

                    <button onclick="runCommand('!redeems')">!redeems</button>

                    <button onclick="runCommand('!redeem mysterybox')">mysterybox</button>

                    <button onclick="runCommand('!addreward hydrate 25 @{username} redeemed hydrate. Drink water!')">add hydrate reward</button>

                    <button onclick="runCommand('!coinleaderboard')">!coinleaderboard</button>

                    <button onclick="runCommand('!cooldowns')">!cooldowns</button>

                    <button onclick="runCommand('!setcooldown foxhunt 10')">set foxhunt cooldown</button>

                    <button onclick="runCommand('!clearcooldowns')">clear cooldowns</button>

                    <button onclick="runCommand('!givepoints avisi 100')">give points</button>

                    <button onclick="runCommand('!coinflip')">!coinflip</button>

                    <button onclick="runCommand('!roll 20')">!roll 20</button>

                    <button onclick="runCommand('!8ball Will FoxBot win?')">!8ball</button>

                    <button onclick="runCommand('!rps rock')">!rps</button>

                    <button onclick="runCommand('!hugs')">!hugs</button>

                    <button onclick="runCommand('!shoutout avisi')">!shoutout</button>

                    <button onclick="runCommand('!setgame Off The Grid')">!setgame</button>

                    <button onclick="runCommand('!game')">!game</button>

                    <button onclick="runCommand('!settitle Playing Off The Grid with FoxBot live')">!settitle</button>

                    <button onclick="runCommand('!title')">!title</button>

                    <button onclick="runCommand('!lurk')">!lurk</button>

                    <button onclick="runCommand('!lurkers')">!lurkers</button>

                    <button onclick="runCommand('!addcmd discord Join the Discord here: your-link')">add !discord</button>

                    <button onclick="runCommand('!commands')">!commands</button>

                    <button onclick="runCommand('!discord')">!discord</button>

                </div>

            </section>



            <section class="panel">

                <h2>Result</h2>

                <div id="output" class="output">Click a command button to test FoxBot.</div>

                <div class="buttons" style="margin-top: 14px;">

                    <button class="secondary" onclick="openEndpoint('/proof')">Open /proof</button>

                    <button class="secondary" onclick="openEndpoint('/ranks')">Open /ranks</button>

                    <button class="secondary" onclick="openEndpoint('/stream-event')">Open /stream-event</button>

                    <button class="secondary" onclick="openEndpoint('/community-quest')">Open /community-quest</button>

                    <button class="secondary" onclick="openEndpoint('/streaks')">Open /streaks</button>

                    <button class="secondary" onclick="openEndpoint('/support-rewards')">Open /support-rewards</button>

                    <button class="secondary" onclick="openEndpoint('/viewer-stats')">Open /viewer-stats</button>

                    <button class="secondary" onclick="openEndpoint('/stream-info')">Open /stream-info</button>

                    <button class="secondary" onclick="openEndpoint('/custom-commands')">Open /custom-commands</button>

                    <button class="secondary" onclick="openEndpoint('/bot-mode')">Open /bot-mode</button>

                    <button class="secondary" onclick="runBlazeDemo()">Run Blaze Demo</button>

                </div>

            </section>

        </div>



        <section class="panel" style="margin-top: 22px;">

            <h2>Finished Feature List</h2>

            <div class="feature-list">

                <div class="feature"><code>Blaze OAuth</code> ? login and connect FoxBot to a Blaze account.</div>

                <div class="feature"><code>Live Chat Listener</code> ? polls Blaze chat and replies to commands.</div>

                <div class="feature"><code>Giveaways</code> ? start, enter, count entries, and pick winners.</div>

                <div class="feature"><code>OBS Overlay</code> ? browser-source giveaway overlay for streams.</div>

                <div class="feature"><code>Leaderboard</code> ? tracks viewer command activity.</div>

                <div class="feature"><code>Socials</code> ? creator link command for viewers.</div>

                <div class="feature"><code>Shoutouts</code> ? admin-only shoutout command.</div>

                <div class="feature"><code>Personality Modes</code> ? hype, chill, and pro response styles.</div>

                <div class="feature"><code>Custom Commands</code> ? add and delete commands live from chat.</div>

                <div class="feature"><code>Stream Info</code> ? game, title, lurk, unlurk, and lurker count.</div>

            </div>

        </section>

    </div>



    <script>

        async function runCommand(command) {

            const output = document.getElementById("output");

            output.textContent = "Running " + command + "...";



            try {

                const response = await fetch("/chat?username=Ryan&message=" + encodeURIComponent(command));

                const data = await response.json();

                output.textContent = "Command: " + command + "\\n\\nResponse:\\n" + data.response;

            } catch (error) {

                output.textContent = "Error running command: " + error;

            }

        }



        function openEndpoint(path) {

            window.open(path, "_blank");

        }



        async function runBlazeDemo() {

            const output = document.getElementById("output");

            output.textContent = "Running Blaze judge demo... This requires Blaze login first.";



            try {

                const response = await fetch("/blaze/judge-demo");

                const data = await response.json();

                output.textContent = JSON.stringify(data, null, 2);

            } catch (error) {

                output.textContent = "Error running Blaze demo: " + error;

            }

        }

    </script>

</body>

</html>

"""





@app.get("/demo", response_class=HTMLResponse)

def judge_demo_page():

    return judge_demo_html





@app.get("/arcade-stats")

def arcade_stats_endpoint():

    return {

        "commands": [

            "!arcade",

            "!coinflip",

            "!roll",

            "!roll 20",

            "!8ball Will I win?",

            "!rps rock",

            "!rps paper",

            "!rps scissors"

        ],

        "stats": arcade_stats

    }





@app.get("/foxcoins")

def foxcoins_endpoint(request: Request):

    # Bot Connection C2 Step 1, Tier 1: same resolution path as
    # /api/studio/stats/live (the reference pattern) -- blaze_id absent
    # (Basic Auth, or no Blaze session) falls back to tenant-zero via
    # _foxbot_resolve_creator_id_v1, keeping this byte-identical to
    # today's _tenant_zero_economy() call for every caller until a
    # second creator is actually approved and mapped.
    resolved_creator_id = _foxbot_resolve_creator_id_v1(
        blaze_id=getattr(request.state, "blaze_id", None)
    )

    economy = _creator_economy_v1(resolved_creator_id)

    return {

        "currency_name": get_currency_name(),

        "balances": economy["balances"],

        "daily_claims": economy["daily_claims"],

        "recent_transactions": economy["transactions"][-10:],

        "reward_shop": reward_shop,

        "commands": [

            "!foxhunt",

            "!balance",

            "!points",

            "!daily",

            "!coinleaderboard",

            "!givepoints avisi 100",

            "!takepoints avisi 50"

        ]

    }





@app.get("/rewards")

def rewards_endpoint():

    return {

        "currency_name": get_currency_name(),

        "reward_count": len(reward_shop),

        "rewards": reward_shop,

        "redemptions_overlay": "/overlay/redemptions",

        "commands": [

            "!shop",

            "!redeem hug",

            "!redeem hype",

            "!redeem flex",

            "!redeem mysterybox",

            "!redeem sponsor",

            "!addreward hydrate 25 @{username} redeemed hydrate. Drink water!",

            "!delreward hydrate"

        ]

    }





@app.get("/redemptions")

def redemptions_endpoint():

    return {

        "count": len(redemption_queue),

        "latest": redemption_queue[0] if redemption_queue else None,

        "redemptions": redemption_queue,

        "commands": [

            "!shop",

            "!redeem hug",

            "!redeems",

            "!clearredeems"

        ]

    }





redemptions_overlay_html = """

<!DOCTYPE html>

<html lang="en">

<head>

    <meta charset="UTF-8">

    <meta name="viewport" content="width=device-width, initial-scale=1.0">

    <title>FoxBot Redemptions Overlay</title>

    <style>

        body {

            margin: 0;

            background: transparent;

            font-family: Arial, sans-serif;

            color: white;

            overflow: hidden;

        }



        .overlay {

            width: 100vw;

            min-height: 100vh;

            display: flex;

            align-items: flex-end;

            justify-content: center;

            padding: 30px;

            box-sizing: border-box;

        }



        .card {

            width: 760px;

            background: rgba(15, 23, 42, 0.94);

            border: 2px solid rgba(249, 115, 22, 0.65);

            border-radius: 28px;

            padding: 26px;

            box-shadow: 0 18px 60px rgba(0, 0, 0, 0.45);

        }



        .top {

            display: flex;

            align-items: center;

            gap: 14px;

            margin-bottom: 18px;

        }



        .logo {

            width: 64px;

            height: 64px;

            border-radius: 18px;

            object-fit: cover;

            border: 2px solid rgba(249, 115, 22, 0.65);

        }



        h1 {

            margin: 0;

            font-size: 34px;

            color: #fdba74;

        }



        .subtitle {

            color: #cbd5e1;

            margin-top: 4px;

            font-size: 16px;

        }



        .latest {

            background: linear-gradient(135deg, rgba(249,115,22,0.24), rgba(234,88,12,0.14));

            border: 1px solid rgba(249,115,22,0.45);

            border-radius: 20px;

            padding: 18px;

            margin-bottom: 16px;

        }



        .label {

            color: #94a3b8;

            font-size: 13px;

            text-transform: uppercase;

            letter-spacing: 1px;

            margin-bottom: 7px;

        }



        .message {

            font-size: 24px;

            font-weight: bold;

            line-height: 1.28;

        }



        .list {

            display: grid;

            gap: 8px;

        }



        .item {

            background: rgba(255,255,255,0.06);

            border-radius: 14px;

            padding: 10px 12px;

            color: #e2e8f0;

            font-size: 16px;

        }



        .empty {

            color: #cbd5e1;

            font-size: 18px;

            padding: 14px;

            background: rgba(255,255,255,0.06);

            border-radius: 14px;

        }

    </style>

</head>

<body>

    <div class="overlay">

        <div class="card">

            <div class="top">

                <img src="/static/foxbot-logo.png" class="logo" alt="FoxBot Logo">

                <div>

                    <h1>FoxBot Redemptions</h1>

                    <div class="subtitle">Earn FoxCoins, spend them in chat, show them on stream.</div>

                </div>

            </div>



            <div class="latest">

                <div class="label">Latest Redemption</div>

                <div id="latestMessage" class="message">Waiting for a redemption...</div>

            </div>



            <div class="label">Recent Queue</div>

            <div id="queue" class="list">

                <div class="empty">No redemptions yet. Type !shop then !redeem hug.</div>

            </div>

        </div>

    </div>



    <script>

        async function refreshRedemptions() {

            try {

                const response = await fetch('/redemptions');

                const data = await response.json();



                const latestMessage = document.getElementById("latestMessage");

                const queue = document.getElementById("queue");



                if (!data.latest) {

                    latestMessage.textContent = "Waiting for a redemption...";

                    queue.innerHTML = '<div class="empty">No redemptions yet. Type !shop then !redeem hug.</div>';

                    return;

                }



                latestMessage.textContent = data.latest.message;



                queue.innerHTML = "";



                data.redemptions.slice(0, 5).forEach(function(item) {

                    const div = document.createElement("div");

                    div.className = "item";

                    div.textContent = "@" + item.username + " redeemed " + item.reward + " (" + item.cost + " FoxCoins)";

                    queue.appendChild(div);

                });

            } catch (error) {

                document.getElementById("latestMessage").textContent = "Error loading redemptions.";

            }

        }



        refreshRedemptions();

        setInterval(refreshRedemptions, 3000);

    </script>

</body>

</html>

"""





@app.get("/overlay/redemptions", response_class=HTMLResponse)

def redemptions_overlay_page():

    return redemptions_overlay_html





# Save data when chat() is called directly by background listeners.

if "chat" in globals() and not globals().get("_foxbot_chat_save_wrapped", False):

    _foxbot_original_chat = chat



    def chat(*args, **kwargs):

        result = _foxbot_original_chat(*args, **kwargs)

        save_persistent_data()

        return result



    _foxbot_chat_save_wrapped = True





@app.get("/data-status")

def data_status_endpoint():

    exists = os.path.exists(DATA_FILE)



    return {

        "data_file": DATA_FILE,

        "exists": exists,

        "custom_command_count": len(_tenant_zero_commands()),

        "viewer_balance_count": len(_tenant_zero_economy().get("balances", {})),

        "reward_count": len(reward_shop),

        "redemption_count": len(redemption_queue),

        "bot_mode": bot_mode,

        "saved_now": save_persistent_data()

    }





@app.get("/save-data")

def save_data_endpoint():

    return {

        "saved": save_persistent_data(),

        "data_file": DATA_FILE

    }





@app.get("/cooldowns")

def cooldowns_endpoint():

    return {

        "cooldown_settings": cooldown_settings,

        "active_timers": len(cooldown_tracker),

        "commands": [

            "!cooldowns",

            "!setcooldown foxhunt 60",

            "!clearcooldowns"

        ]

    }





economy_dashboard_html = """

<!DOCTYPE html>

<html lang="en">

<head>

    <meta charset="UTF-8">

    <meta name="viewport" content="width=device-width, initial-scale=1.0">

    <title>FoxBot Economy Dashboard</title>

    <style>

        body {

            margin: 0;

            font-family: Arial, sans-serif;

            background: radial-gradient(circle at top, #1f2937, #020617 70%);

            color: white;

        }



        .wrap {

            max-width: 1180px;

            margin: 0 auto;

            padding: 40px 22px;

        }



        .hero {

            background: rgba(15, 23, 42, 0.92);

            border: 1px solid rgba(249, 115, 22, 0.5);

            border-radius: 28px;

            padding: 32px;

            box-shadow: 0 20px 70px rgba(0,0,0,0.35);

        }



        .top {

            display: flex;

            align-items: center;

            gap: 18px;

            flex-wrap: wrap;

        }



        .logo {

            width: 86px;

            height: 86px;

            border-radius: 22px;

            object-fit: cover;

            border: 2px solid rgba(249, 115, 22, 0.7);

        }



        h1 {

            margin: 0;

            color: #fdba74;

            font-size: 44px;

        }



        .subtitle {

            margin-top: 8px;

            color: #cbd5e1;

            font-size: 18px;

            line-height: 1.5;

        }



        .nav {

            margin-top: 24px;

            display: flex;

            gap: 10px;

            flex-wrap: wrap;

        }



        .nav a {

            color: white;

            text-decoration: none;

            background: rgba(255,255,255,0.08);

            border: 1px solid rgba(255,255,255,0.12);

            border-radius: 999px;

            padding: 10px 14px;

        }



        .grid {

            display: grid;

            grid-template-columns: 1fr 1fr;

            gap: 18px;

            margin-top: 22px;

        }



        .panel {

            background: rgba(15, 23, 42, 0.78);

            border: 1px solid rgba(148, 163, 184, 0.22);

            border-radius: 22px;

            padding: 22px;

        }



        h2 {

            margin: 0 0 14px;

            color: #fdba74;

        }



        .buttons {

            display: flex;

            gap: 10px;

            flex-wrap: wrap;

        }



        button {

            cursor: pointer;

            border: 0;

            background: linear-gradient(135deg, #f97316, #ea580c);

            color: white;

            font-weight: 800;

            border-radius: 14px;

            padding: 12px 14px;

            box-shadow: 0 8px 22px rgba(249,115,22,0.18);

        }



        button.secondary {

            background: rgba(255,255,255,0.1);

            border: 1px solid rgba(255,255,255,0.12);

        }



        .box {

            background: rgba(2, 6, 23, 0.78);

            border: 1px solid rgba(148, 163, 184, 0.22);

            border-radius: 18px;

            padding: 16px;

            white-space: pre-wrap;

            color: #e2e8f0;

            line-height: 1.5;

            overflow: auto;

            min-height: 160px;

            max-height: 420px;

        }



        .stat-grid {

            display: grid;

            grid-template-columns: repeat(3, 1fr);

            gap: 12px;

            margin-top: 18px;

        }



        .stat {

            background: rgba(255,255,255,0.06);

            border-radius: 16px;

            padding: 14px;

        }



        .label {

            color: #94a3b8;

            font-size: 12px;

            text-transform: uppercase;

            letter-spacing: 1px;

            margin-bottom: 8px;

        }



        .value {

            font-size: 24px;

            font-weight: bold;

            color: white;

        }



        code {

            color: #fdba74;

            font-weight: bold;

        }



        @media (max-width: 850px) {

            .grid, .stat-grid {

                grid-template-columns: 1fr;

            }



            h1 {

                font-size: 36px;

            }

        }

    </style>

</head>

<body>

    <div class="wrap">

        <section class="hero">

            <div class="top">

                <img src="/static/foxbot-logo.png" class="logo" alt="FoxBot Logo">

                <div>

                    <h1>FoxBot Economy Dashboard</h1>

                    <div class="subtitle">

                        Manage and preview the FoxCoins economy, reward shop, redemptions,

                        cooldowns, arcade stats, and saved bot data.

                    </div>

                </div>

            </div>



            <div class="nav">

                <a href="/">Home</a>

                <a href="/demo">Judge Demo</a>

                <a href="/dashboard">Dashboard</a>

                <a href="/features">Features</a>

                <a href="/overlay/giveaway">Giveaway Overlay</a>

                <a href="/overlay/redemptions">Redemptions Overlay</a>

                <a href="/overlay/boss">Boss Overlay</a>

                <a href="/proof">Proof</a>

            </div>



            <div class="stat-grid">

                <div class="stat">

                    <div class="label">Currency</div>

                    <div class="value" id="currencyName">Loading</div>

                </div>

                <div class="stat">

                    <div class="label">Balances</div>

                    <div class="value" id="balanceCount">0</div>

                </div>

                <div class="stat">

                    <div class="label">Rewards</div>

                    <div class="value" id="rewardCount">0</div>

                </div>

            </div>

        </section>



        <div class="grid">

            <section class="panel">

                <h2>Economy Test Buttons</h2>

                <div class="buttons">

                    <button onclick="runCommand('!daily')">!daily</button>

                    <button onclick="runCommand('!foxhunt')">!foxhunt</button>

                    <button onclick="runCommand('!balance')">!balance</button>

                    <button onclick="runCommand('!shop')">!shop</button>

                    <button onclick="runCommand('!redeem hug')">redeem hug</button>

                    <button onclick="runCommand('!redeem mysterybox')">mysterybox</button>

                    <button onclick="runCommand('!coinleaderboard')">leaderboard</button>

                    <button onclick="runCommand('!givepoints avisi 100')">give avisi 100</button>

                    <button onclick="runCommand('!addreward hydrate 25 @{username} redeemed hydrate. Drink water!')">add hydrate</button>

                    <button onclick="runCommand('!redeem hydrate')">redeem hydrate</button>

                    <button onclick="runCommand('!redeems')">!redeems</button>

                    <button onclick="runCommand('!cooldowns')">!cooldowns</button>

                    <button class="secondary" onclick="refreshAll()">Refresh Data</button>

                    <button class="secondary" onclick="openEndpoint('/save-data')">Save Data</button>

                </div>

            </section>



            <section class="panel">

                <h2>Command Result</h2>

                <div id="result" class="box">Click a test button to run a FoxBot economy command.</div>

            </section>



            <section class="panel">

                <h2>FoxCoins Data</h2>

                <div id="foxcoins" class="box">Loading...</div>

            </section>



            <section class="panel">

                <h2>Reward Shop</h2>

                <div id="rewards" class="box">Loading...</div>

            </section>



            <section class="panel">

                <h2>Recent Redemptions</h2>

                <div id="redemptions" class="box">Loading...</div>

            </section>



            <section class="panel">

                <h2>Cooldowns + Data Status</h2>

                <div id="status" class="box">Loading...</div>

            </section>



            <section class="panel">

                <h2>Ranks + Events + Quests</h2>

                <div id="progression" class="box">Loading...</div>

            </section>



            <section class="panel">

                <h2>Streaks + Support Rewards</h2>

                <div id="supportBox" class="box">Loading...</div>

            </section>

        </div>

    </div>



    <script>

        async function runCommand(command) {

            const result = document.getElementById("result");

            result.textContent = "Running " + command + "...";



            try {

                const response = await fetch("/chat?username=Ryan&message=" + encodeURIComponent(command));

                const data = await response.json();

                result.textContent = "Command: " + command + "\\n\\nResponse:\\n" + data.response;

                await refreshAll();

            } catch (error) {

                result.textContent = "Error: " + error;

            }

        }



        

async function callEndpoint(path) {

    const box = out();

    box.textContent = "Calling " + path + "...";



    try {

        const r = await fetch(path);

        const d = await r.json();

        box.textContent = JSON.stringify(d, null, 2);

        refreshAll();

    } catch(e) {

        box.textContent = "Error: " + e;

    }

}



async function sendBlazeCommand() {

    const box = out();

    const username = v("blazeUser") || "Ryan";

    const message = v("blazeMessage") || "!foxhelp";



    box.textContent = "Sending to Blaze...";



    try {

        const r = await fetch("/blaze/run-command?username=" + encodeURIComponent(username) + "&message=" + encodeURIComponent(message));

        const d = await r.json();

        box.textContent = JSON.stringify(d, null, 2);

    } catch(e) {

        box.textContent = "Error sending to Blaze: " + e;

    }

}



async function getJSON(path) {

            const response = await fetch(path);

            return await response.json();

        }



        function pretty(data) {

            return JSON.stringify(data, null, 2);

        }



        async function refreshAll() {

            try {

                const foxcoins = await getJSON("/foxcoins");

                const rewards = await getJSON("/rewards");

                const redemptions = await getJSON("/redemptions");

                const cooldowns = await getJSON("/cooldowns");

                const dataStatus = await getJSON("/data-status");

                const ranks = await getJSON("/ranks");

                const streamEvent = await getJSON("/stream-event");

                const communityQuest = await getJSON("/community-quest");

                const streaks = await getJSON("/streaks");

                const supportRewards = await getJSON("/support-rewards");



                document.getElementById("currencyName").textContent = foxcoins.currency_name || "FoxCoins";

                document.getElementById("balanceCount").textContent = Object.keys(foxcoins.balances || {}).length;

                document.getElementById("rewardCount").textContent = rewards.reward_count || 0;



                document.getElementById("foxcoins").textContent = pretty(foxcoins);

                document.getElementById("rewards").textContent = pretty(rewards);

                document.getElementById("redemptions").textContent = pretty(redemptions);

                document.getElementById("status").textContent =

                    "Cooldowns:\\n" + pretty(cooldowns) + "\\n\\nData Status:\\n" + pretty(dataStatus);

            } catch (error) {

                document.getElementById("status").textContent = "Error loading dashboard data: " + error;

            }

        }



        function openEndpoint(path) {

            window.open(path, "_blank");

        }



        refreshAll();

        setInterval(refreshAll, 5000);

    </script>

</body>

</html>

"""





@app.get("/economy", response_class=HTMLResponse)

def economy_dashboard_page():

    return economy_dashboard_html





@app.get("/boss")

def boss_endpoint():

    return {

        "boss_battle": boss_battle,

        "status": format_boss_status(),

        "leaderboard": format_boss_leaderboard(),

        "commands": [

            "!boss",

            "!bossstatus",

            "!startboss Cyber Fox Dragon",

            "!startboss 1000 Cyber Fox Dragon",

            "!attack",

            "!powerattack",

            "!bossleaderboard",

            "!endboss"

        ]

    }





boss_overlay_html = """

<!DOCTYPE html>

<html lang="en">

<head>

    <meta charset="UTF-8">

    <meta name="viewport" content="width=device-width, initial-scale=1.0">

    <title>FoxBot Boss Battle Overlay</title>

    <style>

        body {

            margin: 0;

            background: transparent;

            font-family: Arial, sans-serif;

            color: white;

            overflow: hidden;

        }



        .overlay {

            width: 100vw;

            min-height: 100vh;

            display: flex;

            align-items: flex-start;

            justify-content: center;

            padding: 28px;

            box-sizing: border-box;

        }



        .card {

            width: 900px;

            background: rgba(15, 23, 42, 0.94);

            border: 2px solid rgba(249, 115, 22, 0.7);

            border-radius: 28px;

            padding: 26px;

            box-shadow: 0 18px 60px rgba(0, 0, 0, 0.45);

        }



        .top {

            display: flex;

            align-items: center;

            gap: 16px;

            margin-bottom: 18px;

        }



        .logo {

            width: 72px;

            height: 72px;

            border-radius: 20px;

            object-fit: cover;

            border: 2px solid rgba(249, 115, 22, 0.7);

        }



        h1 {

            margin: 0;

            font-size: 38px;

            color: #fdba74;

        }



        .subtitle {

            color: #cbd5e1;

            margin-top: 4px;

            font-size: 17px;

        }



        .boss-name {

            font-size: 34px;

            font-weight: 900;

            margin-top: 10px;

            color: white;

        }



        .status {

            color: #cbd5e1;

            margin-top: 6px;

            font-size: 18px;

        }



        .hp-wrap {

            margin-top: 22px;

        }



        .hp-top {

            display: flex;

            justify-content: space-between;

            font-size: 18px;

            margin-bottom: 8px;

            color: #e2e8f0;

        }



        .hp-bar {

            width: 100%;

            height: 38px;

            background: rgba(255,255,255,0.1);

            border: 1px solid rgba(255,255,255,0.16);

            border-radius: 999px;

            overflow: hidden;

        }



        .hp-fill {

            height: 100%;

            width: 0%;

            background: linear-gradient(90deg, #ef4444, #f97316, #fdba74);

            border-radius: 999px;

            transition: width 0.5s ease;

        }



        .grid {

            display: grid;

            grid-template-columns: 1.1fr 0.9fr;

            gap: 16px;

            margin-top: 20px;

        }



        .panel {

            background: rgba(255,255,255,0.06);

            border: 1px solid rgba(255,255,255,0.1);

            border-radius: 20px;

            padding: 18px;

        }



        .label {

            color: #94a3b8;

            font-size: 13px;

            text-transform: uppercase;

            letter-spacing: 1px;

            margin-bottom: 10px;

        }



        .leaderboard {

            display: grid;

            gap: 8px;

        }



        .leader {

            display: flex;

            justify-content: space-between;

            gap: 14px;

            background: rgba(2, 6, 23, 0.4);

            border-radius: 12px;

            padding: 10px 12px;

            font-size: 17px;

        }



        .commands {

            font-size: 22px;

            font-weight: 900;

            line-height: 1.45;

        }



        .commands span {

            color: #fdba74;

        }



        .small {

            color: #cbd5e1;

            font-size: 16px;

            line-height: 1.45;

        }



        .defeated {

            font-size: 28px;

            font-weight: 900;

            color: #fdba74;

            margin-top: 4px;

        }



        .empty {

            color: #cbd5e1;

            font-size: 18px;

            padding: 12px;

            background: rgba(2, 6, 23, 0.35);

            border-radius: 12px;

        }



        @media (max-width: 900px) {

            .card {

                width: 100%;

            }



            .grid {

                grid-template-columns: 1fr;

            }



            h1 {

                font-size: 32px;

            }



            .boss-name {

                font-size: 28px;

            }

        }

    </style>

</head>

<body>

    <div class="overlay">

        <div class="card">

            <div class="top">

                <img src="/static/foxbot-logo.png" class="logo" alt="FoxBot Logo">

                <div>

                    <h1>FoxBot Boss Battle</h1>

                    <div class="subtitle">Chat fights together. Attack, earn FoxCoins, defeat the boss.</div>

                </div>

            </div>



            <div class="boss-name" id="bossName">Loading boss...</div>

            <div class="status" id="bossStatus">Checking battle status...</div>



            <div class="hp-wrap">

                <div class="hp-top">

                    <div>Boss HP</div>

                    <div id="hpText">0 / 0</div>

                </div>

                <div class="hp-bar">

                    <div class="hp-fill" id="hpFill"></div>

                </div>

            </div>



            <div class="grid">

                <section class="panel">

                    <div class="label">Top Damage</div>

                    <div id="leaderboard" class="leaderboard">

                        <div class="empty">No damage yet. Type !attack.</div>

                    </div>

                </section>



                <section class="panel">

                    <div class="label">Chat Commands</div>

                    <div class="commands">

                        Type <span>!attack</span><br>

                        or <span>!powerattack</span>

                    </div>

                    <div class="small" style="margin-top: 14px;">

                        Power attacks spend FoxCoins for bigger damage.

                    </div>



                    <div class="label" style="margin-top: 18px;">Bosses Defeated</div>

                    <div class="defeated" id="defeatedCount">0</div>

                </section>

            </div>

        </div>

    </div>



    <script>

        function titleCaseName(name) {

            if (!name) return "";

            return name;

        }



        async function refreshBoss() {

            try {

                const response = await fetch('/boss');

                const data = await response.json();

                const boss = data.boss_battle || {};



                const bossName = document.getElementById("bossName");

                const bossStatus = document.getElementById("bossStatus");

                const hpText = document.getElementById("hpText");

                const hpFill = document.getElementById("hpFill");

                const leaderboard = document.getElementById("leaderboard");

                const defeatedCount = document.getElementById("defeatedCount");



                const active = boss.active;

                const name = boss.name || "Cyber Fox Dragon";

                const hp = Number(boss.hp || 0);

                const maxHp = Number(boss.max_hp || 500);

                const defeated = Number(boss.defeated_count || 0);

                const damageLog = boss.damage_log || {};



                bossName.textContent = active ? name : "No Active Boss";

                bossStatus.textContent = active

                    ? "Boss is live. Chat can attack now."

                    : "Waiting for the next boss. Admins can type !startboss Cyber Fox Dragon.";



                hpText.textContent = active ? hp + " / " + maxHp : "0 / " + maxHp;



                let percent = active && maxHp > 0 ? Math.max(0, Math.min(100, (hp / maxHp) * 100)) : 0;

                hpFill.style.width = percent + "%";



                defeatedCount.textContent = defeated;



                const rows = Object.entries(damageLog)

                    .sort((a, b) => Number(b[1]) - Number(a[1]))

                    .slice(0, 5);



                leaderboard.innerHTML = "";



                if (rows.length === 0) {

                    leaderboard.innerHTML = '<div class="empty">No damage yet. Type !attack.</div>';

                    return;

                }



                rows.forEach(function(row, index) {

                    const div = document.createElement("div");

                    div.className = "leader";



                    const name = document.createElement("div");

                    name.textContent = (index + 1) + ". @" + row[0];



                    const damage = document.createElement("div");

                    damage.textContent = row[1] + " DMG";



                    div.appendChild(name);

                    div.appendChild(damage);

                    leaderboard.appendChild(div);

                });

            } catch (error) {

                document.getElementById("bossStatus").textContent = "Error loading boss battle.";

            }

        }



        refreshBoss();

        setInterval(refreshBoss, 3000);

    </script>

</body>

</html>

"""





@app.get("/overlay/boss", response_class=HTMLResponse)

def boss_overlay_page():

    return boss_overlay_html





smoke_test_html = """

<!DOCTYPE html>

<html lang="en">

<head>

    <meta charset="UTF-8">

    <meta name="viewport" content="width=device-width, initial-scale=1.0">

    <title>FoxBot Smoke Test</title>

    <style>

        body {

            margin: 0;

            font-family: Arial, sans-serif;

            background: radial-gradient(circle at top, #1f2937, #020617 70%);

            color: white;

        }



        .wrap {

            max-width: 1180px;

            margin: 0 auto;

            padding: 40px 22px;

        }



        .hero {

            background: rgba(15, 23, 42, 0.92);

            border: 1px solid rgba(249, 115, 22, 0.5);

            border-radius: 28px;

            padding: 32px;

            box-shadow: 0 20px 70px rgba(0,0,0,0.35);

        }



        .top {

            display: flex;

            align-items: center;

            gap: 18px;

            flex-wrap: wrap;

        }



        .logo {

            width: 86px;

            height: 86px;

            border-radius: 22px;

            object-fit: cover;

            border: 2px solid rgba(249, 115, 22, 0.7);

        }



        h1 {

            margin: 0;

            color: #fdba74;

            font-size: 44px;

        }



        .subtitle {

            margin-top: 8px;

            color: #cbd5e1;

            font-size: 18px;

            line-height: 1.5;

        }



        .nav {

            margin-top: 24px;

            display: flex;

            gap: 10px;

            flex-wrap: wrap;

        }



        .nav a {

            color: white;

            text-decoration: none;

            background: rgba(255,255,255,0.08);

            border: 1px solid rgba(255,255,255,0.12);

            border-radius: 999px;

            padding: 10px 14px;

        }



        .panel {

            margin-top: 22px;

            background: rgba(15, 23, 42, 0.78);

            border: 1px solid rgba(148, 163, 184, 0.22);

            border-radius: 22px;

            padding: 22px;

        }



        .buttons {

            display: flex;

            gap: 10px;

            flex-wrap: wrap;

        }



        button {

            cursor: pointer;

            border: 0;

            background: linear-gradient(135deg, #f97316, #ea580c);

            color: white;

            font-weight: 800;

            border-radius: 14px;

            padding: 12px 14px;

            box-shadow: 0 8px 22px rgba(249,115,22,0.18);

        }



        button.secondary {

            background: rgba(255,255,255,0.1);

            border: 1px solid rgba(255,255,255,0.12);

        }



        .summary {

            display: grid;

            grid-template-columns: repeat(4, 1fr);

            gap: 12px;

            margin-top: 18px;

        }



        .stat {

            background: rgba(255,255,255,0.06);

            border-radius: 16px;

            padding: 14px;

        }



        .label {

            color: #94a3b8;

            font-size: 12px;

            text-transform: uppercase;

            letter-spacing: 1px;

            margin-bottom: 8px;

        }



        .value {

            font-size: 26px;

            font-weight: bold;

        }



        .results {

            display: grid;

            gap: 10px;

            margin-top: 18px;

        }



        .row {

            background: rgba(2, 6, 23, 0.72);

            border: 1px solid rgba(148, 163, 184, 0.22);

            border-radius: 16px;

            padding: 14px;

        }



        .row-top {

            display: flex;

            justify-content: space-between;

            gap: 12px;

            flex-wrap: wrap;

            margin-bottom: 8px;

        }



        .cmd {

            color: #fdba74;

            font-weight: 900;

        }



        .ok {

            color: #86efac;

            font-weight: 900;

        }



        .fail {

            color: #fca5a5;

            font-weight: 900;

        }



        .pending {

            color: #fde68a;

            font-weight: 900;

        }



        .response {

            color: #e2e8f0;

            line-height: 1.45;

            white-space: pre-wrap;

            word-break: break-word;

        }



        code {

            color: #fdba74;

            font-weight: bold;

        }



        @media (max-width: 850px) {

            .summary {

                grid-template-columns: 1fr;

            }



            h1 {

                font-size: 36px;

            }

        }

    </style>

</head>

<body>

    <div class="wrap">

        <section class="hero">

            <div class="top">

                <img src="/static/foxbot-logo.png" class="logo" alt="FoxBot Logo">

                <div>

                    <h1>FoxBot Smoke Test</h1>

                    <div class="subtitle">

                        Run a fast health check before submitting, demoing, or going live.

                        A command passes if FoxBot returns a real response instead of an error or unknown command.

                    </div>

                </div>

            </div>



            <div class="nav">

                <a href="/">Home</a>

                <a href="/demo">Judge Demo</a>

                <a href="/dashboard">Dashboard</a>

                <a href="/economy">Economy</a>

                <a href="/overlay/giveaway">Giveaway Overlay</a>

                <a href="/overlay/redemptions">Redemptions Overlay</a>

                <a href="/overlay/boss">Boss Overlay</a>

                <a href="/proof">Proof</a>

            </div>



            <div class="summary">

                <div class="stat">

                    <div class="label">Total Tests</div>

                    <div class="value" id="totalCount">0</div>

                </div>

                <div class="stat">

                    <div class="label">Passed</div>

                    <div class="value" id="passCount">0</div>

                </div>

                <div class="stat">

                    <div class="label">Failed</div>

                    <div class="value" id="failCount">0</div>

                </div>

                <div class="stat">

                    <div class="label">Status</div>

                    <div class="value" id="overallStatus">Ready</div>

                </div>

            </div>

        </section>



        <section class="panel">

            <h2>Run Tests</h2>

            <div class="buttons">

                <button onclick="runAllTests()">Run Full Smoke Test</button>

                <button class="secondary" onclick="runCoreTests()">Core Only</button>

                <button class="secondary" onclick="runEconomyTests()">Economy Only</button>

                <button class="secondary" onclick="runBossTests()">Boss Only</button>

                <button class="secondary" onclick="clearResults()">Clear</button>

            </div>



            <div id="results" class="results"></div>

        </section>

    </div>



    <script>

        const coreTests = [

            "!foxhelp",

            "!socials",

            "!schedule",

            "!faq",

            "!arcade",

            "!coinflip",

            "!roll 20",

            "!8ball Will FoxBot win?",

            "!rps rock",

            "!leaderboard",

            "!stats",

            "!hugs"

        ];



        const economyTests = [

            "!daily",

            "!foxhunt",

            "!balance",

            "!shop",

            "!redeem hug",

            "!redeems",

            "!coinleaderboard",

            "!givepoints avisi 100"

        ];



        const bossTests = [

            "!startboss Cyber Fox Dragon",

            "!boss",

            "!attack",

            "!givepoints Ryan 100",

            "!powerattack",

            "!bossleaderboard"

        ];



        function allTests() {

            return [...coreTests, ...economyTests, ...bossTests];

        }



        function clearResults() {

            document.getElementById("results").innerHTML = "";

            updateSummary(0, 0, 0, "Ready");

        }



        function updateSummary(total, passed, failed, status) {

            document.getElementById("totalCount").textContent = total;

            document.getElementById("passCount").textContent = passed;

            document.getElementById("failCount").textContent = failed;

            document.getElementById("overallStatus").textContent = status;

        }



        function makeRow(command) {

            const row = document.createElement("div");

            row.className = "row";



            row.innerHTML = `

                <div class="row-top">

                    <div class="cmd">${command}</div>

                    <div class="pending">Testing...</div>

                </div>

                <div class="response">Waiting for response...</div>

            `;



            document.getElementById("results").appendChild(row);

            return row;

        }



        function isPassingResponse(text) {

            if (!text) return false;



            const lower = text.toLowerCase();



            if (lower.includes("unknown command")) return false;

            if (lower.includes("internal server error")) return false;

            if (lower.includes("traceback")) return false;



            return true;

        }



        async function runCommandTest(command) {

            const row = makeRow(command);



            try {

                const response = await fetch("/chat?username=Ryan&message=" + encodeURIComponent(command));

                const data = await response.json();

                const reply = data.response || JSON.stringify(data);



                const passed = isPassingResponse(reply);



                row.querySelector(".pending").className = passed ? "ok" : "fail";

                row.querySelector(".ok, .fail").textContent = passed ? "PASS" : "FAIL";

                row.querySelector(".response").textContent = reply;



                return passed;

            } catch (error) {

                row.querySelector(".pending").className = "fail";

                row.querySelector(".fail").textContent = "FAIL";

                row.querySelector(".response").textContent = "Error: " + error;

                return false;

            }

        }



        async function runTests(commands) {

            clearResults();



            let passed = 0;

            let failed = 0;



            updateSummary(commands.length, 0, 0, "Running");



            for (const command of commands) {

                const ok = await runCommandTest(command);



                if (ok) {

                    passed += 1;

                } else {

                    failed += 1;

                }



                updateSummary(commands.length, passed, failed, failed === 0 ? "Passing" : "Review");

                await new Promise(resolve => setTimeout(resolve, 250));

            }



            updateSummary(commands.length, passed, failed, failed === 0 ? "All Good" : "Fix Needed");

        }



        function runAllTests() {

            runTests(allTests());

        }



        function runCoreTests() {

            runTests(coreTests);

        }



        function runEconomyTests() {

            runTests(economyTests);

        }



        function runBossTests() {

            runTests(bossTests);

        }

    </script>

</body>

</html>

"""





@app.get("/smoke-test", response_class=HTMLResponse)

def smoke_test_page():

    return smoke_test_html





goodnight_html = """

<!DOCTYPE html>

<html lang="en">

<head>

    <meta charset="UTF-8">

    <meta name="viewport" content="width=device-width, initial-scale=1.0">

    <title>FoxBot Goodnight Button</title>

    <style>

        body {

            margin: 0;

            font-family: Arial, sans-serif;

            background: radial-gradient(circle at top, #1f2937, #020617 70%);

            color: white;

        }



        .wrap {

            max-width: 980px;

            margin: 0 auto;

            padding: 40px 22px;

        }



        .card {

            background: rgba(15, 23, 42, 0.92);

            border: 1px solid rgba(249, 115, 22, 0.5);

            border-radius: 28px;

            padding: 34px;

            box-shadow: 0 20px 70px rgba(0,0,0,0.35);

        }



        .top {

            display: flex;

            align-items: center;

            gap: 18px;

            flex-wrap: wrap;

        }



        .logo {

            width: 86px;

            height: 86px;

            border-radius: 22px;

            object-fit: cover;

            border: 2px solid rgba(249, 115, 22, 0.7);

        }



        h1 {

            margin: 0;

            color: #fdba74;

            font-size: 44px;

        }



        .subtitle {

            margin-top: 8px;

            color: #cbd5e1;

            font-size: 18px;

            line-height: 1.5;

        }



        .nav {

            margin-top: 24px;

            display: flex;

            gap: 10px;

            flex-wrap: wrap;

        }



        .nav a {

            color: white;

            text-decoration: none;

            background: rgba(255,255,255,0.08);

            border: 1px solid rgba(255,255,255,0.12);

            border-radius: 999px;

            padding: 10px 14px;

        }



        .buttons {

            display: flex;

            gap: 12px;

            flex-wrap: wrap;

            margin-top: 24px;

        }



        button {

            cursor: pointer;

            border: 0;

            background: linear-gradient(135deg, #f97316, #ea580c);

            color: white;

            font-weight: 900;

            border-radius: 16px;

            padding: 14px 18px;

            box-shadow: 0 8px 22px rgba(249,115,22,0.18);

            font-size: 16px;

        }



        button.secondary {

            background: rgba(255,255,255,0.1);

            border: 1px solid rgba(255,255,255,0.12);

        }



        .output {

            margin-top: 24px;

            background: rgba(2, 6, 23, 0.78);

            border: 1px solid rgba(148, 163, 184, 0.22);

            border-radius: 18px;

            padding: 18px;

            white-space: pre-wrap;

            color: #e2e8f0;

            line-height: 1.5;

            min-height: 130px;

        }



        code {

            color: #fdba74;

            font-weight: bold;

        }

    </style>

</head>

<body>

    <div class="wrap">

        <section class="card">

            <div class="top">

                <img src="/static/foxbot-logo.png" class="logo" alt="FoxBot Logo">

                <div>

                    <h1>FoxBot Goodnight Button</h1>

                    <div class="subtitle">

                        Use this at the end of stream to send a clean sign-off message.

                        Command: <code>!goodnight</code> or <code>!endstream</code>

                    </div>

                </div>

            </div>



            <div class="nav">

                <a href="/">Home</a>

                <a href="/dashboard">Dashboard</a>

                <a href="/demo">Judge Demo</a>

                <a href="/economy">Economy</a>

                <a href="/overlay/giveaway">Giveaway Overlay</a>

                <a href="/overlay/redemptions">Redemptions Overlay</a>

                <a href="/overlay/boss">Boss Overlay</a>

                <a href="/proof">Proof</a>

            </div>



            <div class="buttons">

                <button onclick="previewGoodnight()">Preview Goodnight Message</button>

                <button onclick="sendGoodnight()">Send Goodnight to Blaze</button>

                <button class="secondary" onclick="openPage('/overlay/redemptions')">Open Redemptions Overlay</button>

                <button class="secondary" onclick="openPage('/overlay/boss')">Open Boss Overlay</button>

                <button class="secondary" onclick="openPage('/proof')">Open Proof</button>

            </div>



            <div id="output" class="output">Click a button to preview or send your ending stream message.</div>

        </section>

    </div>



    <script>

        async function previewGoodnight() {

            const output = document.getElementById("output");

            output.textContent = "Previewing !goodnight...";



            try {

                const response = await fetch("/chat?username=Ryan&message=" + encodeURIComponent("!goodnight"));

                const data = await response.json();

                output.textContent = "Preview Response:\\n\\n" + data.response;

            } catch (error) {

                output.textContent = "Error previewing goodnight message: " + error;

            }

        }



        async function sendGoodnight() {

            const output = document.getElementById("output");

            output.textContent = "Sending !goodnight to Blaze... Make sure you are logged into Blaze first.";



            try {

                const response = await fetch("/blaze/run-command?username=Ryan&message=" + encodeURIComponent("!goodnight"));

                const data = await response.json();

                output.textContent = JSON.stringify(data, null, 2);

            } catch (error) {

                output.textContent = "Error sending to Blaze: " + error;

            }

        }



        function openPage(path) {

            window.open(path, "_blank");

        }

    </script>

</body>

</html>

"""





@app.get("/goodnight", response_class=HTMLResponse)

def goodnight_page():

    return goodnight_html





@app.get("/support-rewards")

def support_rewards_endpoint():

    return {

        "support_rewards": support_rewards,

        "commands": [

            "!support",

            "!rewardconfig",

            "!claimchat",

            "!claimvote 10",

            "!claimfollow",

            "!claimraid",

            "!claimtip 5",

            "!claimsub",

            "!claimgiftsub 3"

        ]

    }





@app.get("/ranks")

def ranks_endpoint():

    return {

        "currency_name": get_currency_name(),

        "ranks": fox_spirit_ranks,

        "commands": [

            "!rank",

            "!rank username",

            "!ranks"

        ]

    }





@app.get("/stream-event")

def stream_event_endpoint():

    return {

        "active_event": stream_event,

        "event_templates": stream_event_templates,

        "status": format_stream_event(),

        "commands": [

            "!events",

            "!event",

            "!startevent random",

            "!startevent goldenfox",

            "!startevent spiritstorm",

            "!startevent treasuredrop",

            "!startevent foxfrenzy",

            "!endevent"

        ]

    }





@app.get("/community-quest")

def community_quest_endpoint():

    return {

        "community_quest": community_quest,

        "status": format_quest_status(),

        "commands": [

            "!quest",

            "!quests",

            "!questprogress",

            "!startquest foxhunt 10",

            "!startquest boss 1",

            "!startquest redeem 5",

            "!startquest chat 25",

            "!startquest arcade 10",

            "!questadd 1",

            "!claimquest",

            "!endquest"

        ]

    }





@app.get("/streaks")

def streaks_endpoint():

    tenant_streaks = _tenant_zero_streaks()

    return {

        "today": today_string(),

        "viewer_count": len(tenant_streaks),

        "streaks": tenant_streaks,

        "leaderboard": format_streak_leaderboard(),

        "commands": [

            "!checkin",

            "!streak",

            "!streak username",

            "!streaks",

            "!resetstreak username"

        ]

    }





foxbot_admin_html = """

<!DOCTYPE html>

<html>

<head>

<meta charset="UTF-8">

<title>FoxBot Admin Hub</title>

<meta name="viewport" content="width=device-width, initial-scale=1.0">

<style>

:root {

    --bg: #050505;

    --panel: rgba(20, 20, 20, 0.92);

    --panel2: rgba(32, 32, 32, 0.92);

    --line: rgba(255, 255, 255, 0.10);

    --orange: #f97316;

    --orange2: #fb923c;

    --green: #84cc16;

    --text: #f8fafc;

    --muted: #a3a3a3;

}



* { box-sizing: border-box; }



body {

    margin: 0;

    color: var(--text);

    font-family: Arial, sans-serif;

    background:

        radial-gradient(circle at top left, rgba(249,115,22,0.20), transparent 34%),

        radial-gradient(circle at top right, rgba(132,204,22,0.16), transparent 34%),

        linear-gradient(180deg, #111, #050505 55%, #020202);

}



.app {

    display: grid;

    grid-template-columns: 240px 1fr;

    min-height: 100vh;

}



.side {

    background: rgba(10,10,10,0.88);

    border-right: 1px solid var(--line);

    padding: 18px 14px;

    position: sticky;

    top: 0;

    height: 100vh;

}



.logo {

    display: flex;

    align-items: center;

    gap: 10px;

    padding: 10px 12px 20px;

}



.logo-mark {

    width: 42px;

    height: 42px;

    border-radius: 14px;

    background: linear-gradient(135deg, var(--orange), var(--green));

    display: grid;

    place-items: center;

    font-size: 24px;

    box-shadow: 0 0 28px rgba(249,115,22,0.35);

}



.logo-title {

    font-size: 24px;

    font-weight: 1000;

    color: var(--orange);

    letter-spacing: 1px;

}



.logo-sub {

    font-size: 11px;

    color: var(--muted);

    text-transform: uppercase;

    letter-spacing: 1.5px;

}



.status {

    margin: 0 8px 18px;

    padding: 10px 12px;

    border: 1px solid rgba(132,204,22,0.25);

    background: rgba(132,204,22,0.08);

    border-radius: 14px;

    color: #bbf7d0;

    font-size: 13px;

}



.status span {

    display: inline-block;

    width: 8px;

    height: 8px;

    background: #22c55e;

    border-radius: 999px;

    margin-right: 7px;

    box-shadow: 0 0 14px #22c55e;

}



.nav button {

    width: 100%;

    border: 0;

    color: #bdbdbd;

    background: transparent;

    text-align: left;

    padding: 12px 13px;

    margin: 3px 0;

    border-radius: 12px;

    cursor: pointer;

    font-size: 14px;

    transition: 0.15s ease;

}



.nav button:hover,

.nav button.active {

    color: white;

    background: linear-gradient(90deg, rgba(249,115,22,0.22), rgba(132,204,22,0.10));

    box-shadow: inset 3px 0 0 var(--orange);

    font-weight: 800;

}



.main {

    padding: 28px;

}



.hero {

    border: 1px solid var(--line);

    background:

        linear-gradient(135deg, rgba(249,115,22,0.16), rgba(132,204,22,0.08)),

        rgba(20,20,20,0.72);

    border-radius: 24px;

    padding: 26px;

    margin-bottom: 20px;

    box-shadow: 0 20px 80px rgba(0,0,0,0.45);

}



.hero h1 {

    margin: 0;

    font-size: 42px;

    letter-spacing: -1px;

}



.hero p {

    margin: 8px 0 0;

    color: #d4d4d4;

    line-height: 1.45;

}



.quick-links {

    display: flex;

    flex-wrap: wrap;

    gap: 9px;

    margin-top: 18px;

}



.section {

    display: none;

}



.section.active {

    display: block;

}



.grid {

    display: grid;

    grid-template-columns: repeat(3, 1fr);

    gap: 14px;

    margin-bottom: 18px;

}



.stat {

    border: 1px solid var(--line);

    background: var(--panel);

    border-radius: 18px;

    padding: 18px;

}



.stat small {

    color: var(--muted);

    text-transform: uppercase;

    font-size: 11px;

    letter-spacing: 1.5px;

}



.big {

    font-size: 32px;

    font-weight: 1000;

    color: var(--orange);

    margin-top: 8px;

}



.card {

    border: 1px solid var(--line);

    background: var(--panel);

    border-radius: 20px;

    padding: 22px;

    margin-bottom: 18px;

    box-shadow: 0 16px 50px rgba(0,0,0,0.28);

}



.card h2 {

    margin: 0 0 8px;

    font-size: 24px;

}



.card p {

    margin: 0 0 16px;

    color: var(--muted);

    line-height: 1.45;

}



.row {

    display: flex;

    flex-wrap: wrap;

    gap: 10px;

    align-items: center;

}



input, select {

    background: #090909;

    border: 1px solid rgba(255,255,255,0.14);

    color: white;

    border-radius: 12px;

    padding: 12px 13px;

    min-width: 180px;

    outline: none;

}



input:focus, select:focus {

    border-color: var(--orange);

    box-shadow: 0 0 0 3px rgba(249,115,22,0.16);

}



button.action,

button.secondary {

    border: 0;

    border-radius: 12px;

    padding: 12px 14px;

    color: white;

    font-weight: 900;

    cursor: pointer;

    transition: 0.15s ease;

}



button.action {

    background: linear-gradient(135deg, var(--orange), #ea580c);

    box-shadow: 0 10px 28px rgba(249,115,22,0.22);

}



button.secondary {

    background: rgba(255,255,255,0.08);

    border: 1px solid rgba(255,255,255,0.12);

}



button.action:hover,

button.secondary:hover {

    transform: translateY(-1px);

    filter: brightness(1.1);

}



.out {

    background: #060606;

    border: 1px solid rgba(255,255,255,0.10);

    border-radius: 16px;

    padding: 15px;

    white-space: pre-wrap;

    min-height: 120px;

    max-height: 360px;

    overflow: auto;

    margin-top: 14px;

    color: #e5e7eb;

    line-height: 1.45;

}



.badge {

    display: inline-block;

    padding: 6px 9px;

    border-radius: 999px;

    font-size: 12px;

    font-weight: 900;

    background: rgba(249,115,22,0.14);

    color: #fed7aa;

    border: 1px solid rgba(249,115,22,0.25);

    margin-bottom: 12px;

}



@media(max-width: 980px) {

    .app { grid-template-columns: 1fr; }

    .side { height: auto; position: relative; }

    .grid { grid-template-columns: 1fr; }

    .hero h1 { font-size: 32px; }

}

</style>

</head>

<body>

<div class="app">

<aside class="side">

    <div class="logo">

        <div class="logo-mark">FB</div>

        <div>

            <div class="logo-title">FOXBOT</div>

            <div class="logo-sub">Admin Hub</div>

        </div>

    </div>



    <div class="status"><span></span>Bot online - tools active</div>



    <div class="nav">

        <button class="active" onclick="show('overview',this)">Overview</button>

        <button onclick="show('control',this)">Bot Control</button>

        <button onclick="show('chat',this)">Chat Test</button>

        <button onclick="show('economy',this)">FoxCoins</button>

        <button onclick="show('shop',this)">Reward Shop</button>

        <button onclick="show('giveaway',this)">Giveaways</button>

        <button onclick="show('boss',this)">Boss Battle</button>

        <button onclick="show('events',this)">Stream Events</button>

        <button onclick="show('quests',this)">Quests</button>

        <button onclick="show('streaks',this)">Streaks</button>

        <button onclick="show('support',this)">Support Rewards</button>

        <button onclick="show('recognitionTab',this)">Recognition</button>

        <button onclick="show('custom',this)">Custom Commands</button>

        <button onclick="show('overlays',this)">Overlays</button>

        <button onclick="show('data',this)">Diagnostics</button>

    </div>

</aside>



<main class="main">

    <div class="hero">

        <span class="badge">Blaze Creator Command Center</span>

        <h1>FoxBot Admin Hub</h1>

        <p>Control FoxCoins, giveaways, rewards, stream events, quests, streaks, boss battles, overlays, and live command tests from one clean dashboard.</p>

        <div class="quick-links">

            <button class="secondary" onclick="openPage('/demo')">Judge Demo</button>

            <button class="secondary" onclick="openPage('/smoke-test')">Smoke Test</button>

            <button class="secondary" onclick="openPage('/proof')">Proof</button>

            <button class="secondary" onclick="openPage('/economy')">Economy</button>

            <button class="secondary" onclick="refreshAll()">Refresh Stats</button>

        </div>

    </div>



    <section id="overview" class="section active">
        <!-- FoxBot HARD Embedded Control Dashboard v1 -->
        <div class="card" style="padding:0; overflow:hidden; margin-bottom:22px; border:1px solid rgba(255,255,255,.14);">
            <div style="padding:18px 20px; border-bottom:1px solid rgba(255,255,255,.12); display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap; background:rgba(15,23,42,.75);">
                <div>
                    <h2 style="margin:0; font-size:26px;">FoxBot Control Dashboard</h2>
                    <p style="margin:6px 0 0; color:var(--muted);">Live Blaze connection, listener, command testing, and chat controls inside the Dashboard tab.</p>
                </div>
                <div style="display:flex; gap:8px; flex-wrap:wrap;">
                    <button class="secondary" onclick="window.open('/foxbot-control','_blank')">Open Full Control</button>
                    <button class="secondary" onclick="document.getElementById('foxbotControlFrame').contentWindow.location.reload()">Refresh</button>
                </div>
            </div>
            <iframe
                id="foxbotControlFrame"
                src="/foxbot-control"
                style="width:100%; height:820px; border:0; background:#0b1020; display:block;"
                title="FoxBot Control Dashboard">
            </iframe>
        </div>
        <!-- End FoxBot HARD Embedded Control Dashboard v1 -->

        <!-- FoxBot Embedded Control Dashboard v1 -->
        <div class="card" style="padding:0; overflow:hidden;">
            <div style="padding:18px 20px; border-bottom:1px solid rgba(255,255,255,.12); display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap;">
                <div>
                    <h2 style="margin:0;">FoxBot Control Dashboard</h2>
                    <p style="margin:6px 0 0; color:var(--muted);">Live Blaze controls embedded directly into the Admin Hub Dashboard.</p>
                </div>
                <div style="display:flex; gap:8px; flex-wrap:wrap;">
                    <button class="secondary" onclick="window.open('/foxbot-control','_blank')">Open Full Control</button>
                    <button class="secondary" onclick="document.getElementById('foxbotControlFrame').contentWindow.location.reload()">Refresh Control</button>
                </div>
            </div>
            <iframe
                id="foxbotControlFrame"
                src="/foxbot-control"
                style="width:100%; height:760px; border:0; background:#0b1020; display:block;"
                title="FoxBot Control Dashboard">
            </iframe>
        </div>


        <div class="grid">

            <div class="stat"><small>FoxCoin Users</small><div id="balCount" class="big">-</div></div>

            <div class="stat"><small>Shop Rewards</small><div id="rewCount" class="big">-</div></div>

            <div class="stat"><small>Redemptions</small><div id="redCount" class="big">-</div></div>

        </div>

        <div class="card">

            <h2>Quick Command Doctor</h2>

            <p>Test your most important commands before going live.</p>

            <div class="row">

                <button class="action" onclick="runCommand('!foxhelp')">Test !foxhelp</button>

                <button class="secondary" onclick="runCommand('!rules')">Test !rules</button>

                <button class="secondary" onclick="runCommand('!daily')">Test !daily</button>

                <button class="secondary" onclick="runCommand('!boss')">Test !boss</button>

            </div>

            <div id="overviewOut" class="out">Ready.</div>

        </div>

    </section>



    

    <section id="control" class="section">

        <div class="card">

            <h2>Bot Control Panel</h2>

            <p>Control Blaze login, polling listener, proof checks, and live command sending from one place.</p>



            <div class="row">

                <button class="secondary" onclick="callEndpoint('/blaze/start-polling-listener')">Start Chat Listener</button>

                <button class="secondary" onclick="callEndpoint('/blaze/polling-status')">Listener Status</button>

                <button class="secondary" onclick="openPage('/proof')">Open Proof</button>

                <button class="secondary" onclick="openPage('/smoke-test')">Smoke Test</button>

            </div>



            <div style="height:14px"></div>



            <h2>Send Message / Command To Blaze</h2>

            <p>This sends through the Blaze command route if your Blaze login is active.</p>



            <input id="blazeUser" value="Ryan">

            <input id="blazeMessage" value="!foxhelp">

            <button class="action" onclick="sendBlazeCommand()">Send To Blaze</button>

            <button class="secondary" onclick="runCommand(v('blazeMessage'), v('blazeUser'))">Test Locally First</button>



            <div id="controlOut" class="out">Ready.</div>

        </div>

    </section>





    <section id="chat" class="section">

        <div class="card">

            <h2>Send Any FoxBot Command</h2>

            <p>Run any command as any username.</p>

            <input id="cmdInput" value="!foxhelp">

            <input id="userInput" value="Ryan">

            <button class="action" onclick="runInputCommand()">Run Command</button>

            <div id="chatOut" class="out">Ready.</div>

        </div>

    </section>



    <section id="economy" class="section">

        <div class="card">

            <h2>FoxCoins + Ranks</h2>

            <p>Manage viewer points and rank progression.</p>

            <input id="pointsUser" value="Ryan">

            <input id="pointsAmount" value="100">

            <button class="action" onclick="runCommand('!givepoints '+v('pointsUser')+' '+v('pointsAmount'))">Give Points</button>

            <button class="secondary" onclick="runCommand('!takepoints '+v('pointsUser')+' '+v('pointsAmount'))">Take Points</button>

            <button class="secondary" onclick="runCommand('!balance '+v('pointsUser'))">Balance</button>

            <button class="secondary" onclick="runCommand('!rank '+v('pointsUser'))">Rank</button>

            <button class="secondary" onclick="runCommand('!ranks')">All Ranks</button>

            <button class="secondary" onclick="runCommand('!foxhunt')">Foxhunt</button>

            <button class="secondary" onclick="openPage('/foxcoins')">FoxCoins JSON</button>

            <button class="secondary" onclick="openPage('/ranks')">Ranks JSON</button>

            <div id="economyOut" class="out">Ready.</div>

        </div>

    </section>



    <section id="shop" class="section">

        <div class="card">

            <h2>Reward Shop</h2>

            <p>Add, delete, test, and monitor reward redemptions.</p>

            <input id="rewardName" value="hydrate">

            <input id="rewardCost" value="25">

            <input id="rewardMsg" value="@{username} redeemed hydrate. Drink water!">

            <button class="action" onclick="runCommand('!addreward '+v('rewardName')+' '+v('rewardCost')+' '+v('rewardMsg'))">Add Reward</button>

            <button class="secondary" onclick="runCommand('!delreward '+v('rewardName'))">Delete</button>

            <button class="secondary" onclick="runCommand('!shop')">Show Shop</button>

            <button class="secondary" onclick="runCommand('!redeem '+v('rewardName'))">Redeem</button>

            <button class="secondary" onclick="runCommand('!clearredeems')">Clear Queue</button>

            <button class="secondary" onclick="openPage('/overlay/redemptions')">Redemption Overlay</button>

            <div id="shopOut" class="out">Ready.</div>

        </div>

    </section>



    <section id="giveaway" class="section">

        <div class="card">

            <h2>Giveaways</h2>

            <p>Start giveaways and test the giveaway link/rules.</p>

            <button class="action" onclick="runCommand('!giveaway')">Start Giveaway</button>

            <button class="secondary" onclick="runCommand('!enter')">Enter</button>

            <button class="secondary" onclick="runCommand('!entries')">Entries</button>

            <button class="secondary" onclick="runCommand('!pickwinner')">Pick Winner</button>

            <button class="secondary" onclick="runCommand('!rules')">Rules</button>

            <button class="secondary" onclick="runCommand('!giveawaylink')">Giveaway Link</button>

            <button class="secondary" onclick="openPage('/overlay/giveaway')">Overlay</button>

            <div id="giveawayOut" class="out">Ready.</div>

        </div>

    </section>



    <section id="boss" class="section">

        <div class="card">

            <h2>Boss Battle</h2>

            <p>Launch and manage live boss fights.</p>

            <input id="bossHp" value="500">

            <input id="bossName" value="Cyber Fox Dragon">

            <button class="action" onclick="runCommand('!startboss '+v('bossHp')+' '+v('bossName'))">Start Boss</button>

            <button class="secondary" onclick="runCommand('!attack')">Attack</button>

            <button class="secondary" onclick="runCommand('!powerattack')">Power Attack</button>

            <button class="secondary" onclick="runCommand('!bossleaderboard')">Leaderboard</button>

            <button class="secondary" onclick="runCommand('!endboss')">End Boss</button>

            <button class="secondary" onclick="openPage('/overlay/boss')">Boss Overlay</button>

            <div id="bossOut" class="out">Ready.</div>

        </div>

    </section>



    <section id="events" class="section">

        <div class="card">

            <h2>Stream Events</h2>

            <p>Trigger temporary events that boost rewards and hype.</p>

            <select id="eventName">

                <option value="goldenfox">Golden Fox</option>

                <option value="spiritstorm">Spirit Storm</option>

                <option value="treasuredrop">Treasure Drop</option>

                <option value="foxfrenzy">Fox Frenzy</option>

                <option value="random">Random</option>

            </select>

            <button class="action" onclick="runCommand('!startevent '+v('eventName'))">Start Event</button>

            <button class="secondary" onclick="runCommand('!event')">Claim/Check</button>

            <button class="secondary" onclick="runCommand('!events')">List Events</button>

            <button class="secondary" onclick="runCommand('!endevent')">End Event</button>

            <button class="secondary" onclick="openPage('/stream-event')">Event JSON</button>

            <div id="eventsOut" class="out">Ready.</div>

        </div>

    </section>



    <section id="quests" class="section">

        <div class="card">

            <h2>Community Quests</h2>

            <p>Start team goals that everyone contributes to.</p>

            <select id="questType">

                <option value="foxhunt">Foxhunt</option>

                <option value="boss">Boss</option>

                <option value="redeem">Redeem</option>

                <option value="chat">Chat</option>

                <option value="arcade">Arcade</option>

            </select>

            <input id="questGoal" value="3">

            <input id="questReward" value="100">

            <button class="action" onclick="runCommand('!startquest '+v('questType')+' '+v('questGoal')+' '+v('questReward'))">Start Quest</button>

            <button class="secondary" onclick="runCommand('!quest')">Status</button>

            <button class="secondary" onclick="runCommand('!questadd 1')">Add +1</button>

            <button class="secondary" onclick="runCommand('!claimquest')">Claim</button>

            <button class="secondary" onclick="runCommand('!endquest')">End</button>

            <button class="secondary" onclick="openPage('/community-quest')">Quest JSON</button>

            <div id="questsOut" class="out">Ready.</div>

        </div>

    </section>



    <section id="streaks" class="section">

        <div class="card">

            <h2>Viewer Streaks</h2>

            <p>Reward returning viewers and track loyalty.</p>

            <input id="streakUser" value="Ryan">

            <button class="action" onclick="runCommand('!checkin')">Check In</button>

            <button class="secondary" onclick="runCommand('!streak '+v('streakUser'))">Check Streak</button>

            <button class="secondary" onclick="runCommand('!streaks')">Leaderboard</button>

            <button class="secondary" onclick="runCommand('!resetstreak '+v('streakUser'))">Reset</button>

            <button class="secondary" onclick="openPage('/streaks')">Streaks JSON</button>

            <div id="streaksOut" class="out">Ready.</div>

        </div>

    </section>



    <section id="support" class="section">

        <div class="card">

            <h2>Support Rewards</h2>

            <p>Claim/test rewards for votes, subs, tips, follows, raids, and chat activity.</p>

            <input id="claimAmount" value="10">

            <button class="action" onclick="runCommand('!support')">Support Menu</button>

            <button class="secondary" onclick="runCommand('!rewardconfig')">Config</button>

            <button class="secondary" onclick="runCommand('!claimchat')">Chat</button>

            <button class="secondary" onclick="runCommand('!claimvote '+v('claimAmount'))">Votes</button>

            <button class="secondary" onclick="runCommand('!claimfollow')">Follow</button>

            <button class="secondary" onclick="runCommand('!claimraid')">Raid</button>

            <button class="secondary" onclick="runCommand('!claimtip 5')">Tip $5</button>

            <button class="secondary" onclick="runCommand('!claimsub')">Sub</button>

            <button class="secondary" onclick="runCommand('!claimgiftsub 3')">3 Gift Subs</button>

            <button class="secondary" onclick="openPage('/support-rewards')">Support JSON</button>

            <div id="supportOut" class="out">Ready.</div>

        </div>

    </section>



    <section id="custom" class="section">

        <div class="card">

            <h2>Custom Commands</h2>

            <p>Create and test custom chat commands.</p>

            <input id="customName" value="discord">

            <input id="customMsg" value="Join the FoxBot AI Discord!">

            <button class="action" onclick="runCommand('!addcmd '+v('customName')+' '+v('customMsg'))">Add</button>

            <button class="secondary" onclick="runCommand('!'+v('customName'))">Test</button>

            <button class="secondary" onclick="runCommand('!delcmd '+v('customName'))">Delete</button>

            <button class="secondary" onclick="runCommand('!commands')">List</button>

            <button class="secondary" onclick="openPage('/custom-commands')">Custom JSON</button>

            <div id="customOut" class="out">Ready.</div>

        </div>

    </section>



    

    <section id="recognitionTab" class="section">

        <div class="card">

            <h2>Recognition Engine</h2>

            <p>Thank voters, followers, subs, tips, raids, MVPs, OGs, and Blaze channels.</p>

            <input id="recUser" value="avisi">

            <input id="recAmount" value="10">

            <input id="recLink" value="https://blaze.stream/avisi">

            <button class="action" onclick="runCommand('!thankfollow '+v('recUser'))">Thank Follow</button>

            <button class="secondary" onclick="runCommand('!thanksub '+v('recUser'))">Thank Sub</button>

            <button class="secondary" onclick="runCommand('!thankgiftsub '+v('recUser')+' 3')">Thank 3 Gift Subs</button>

            <button class="secondary" onclick="runCommand('!thankvote '+v('recUser')+' '+v('recAmount'))">Thank Votes</button>

            <button class="secondary" onclick="runCommand('!thanktip '+v('recUser')+' 5')">Thank Tip</button>

            <button class="secondary" onclick="runCommand('!thankraid '+v('recUser'))">Thank Raid</button>

            <button class="secondary" onclick="runCommand('!mvp '+v('recUser'))">MVP</button>

            <button class="secondary" onclick="runCommand('!og '+v('recUser'))">OG</button>

            <button class="secondary" onclick="runCommand('!channel '+v('recUser')+' '+v('recLink'))">Channel Shoutout</button>

            <button class="secondary" onclick="runCommand('!recognitionlog')">Recognition Log</button>

            <button class="secondary" onclick="runCommand('!recognitionon')">Recognition ON</button>

            <button class="secondary" onclick="runCommand('!recognitionoff')">Recognition OFF</button>

            <button class="secondary" onclick="openPage('/recognition')">Recognition JSON</button>

            <div id="recognitionTabOut" class="out">Ready.</div>

        </div>

    </section>





    <section id="overlays" class="section">

        <div class="card">

            <h2>OBS Overlays</h2>

            <p>Open live stream overlays. Recommended OBS size: 1920x1080.</p>

            <button class="action" onclick="openPage('/overlay/giveaway')">Giveaway</button>

            <button class="secondary" onclick="openPage('/overlay/redemptions')">Redemptions</button>

            <button class="secondary" onclick="openPage('/overlay/boss')">Boss</button>

            <button class="secondary" onclick="openPage('/goodnight')">Goodnight</button>

            <div id="overlaysOut" class="out">Ready.</div>

        </div>

    </section>



    <section id="data" class="section">

        <div class="card">

            <h2>Diagnostics + Data</h2>

            <p>Check project health and save data.</p>

            <button class="action" onclick="openPage('/smoke-test')">Smoke Test</button>

            <button class="secondary" onclick="openPage('/proof')">Proof</button>

            <button class="secondary" onclick="openPage('/data-status')">Data Status</button>

            <button class="secondary" onclick="openPage('/save-data')">Save Data</button>

            <button class="secondary" onclick="openPage('/project-status')">Project Status</button>

            <button class="secondary" onclick="openPage('/cooldowns')">Cooldowns</button>

            <div id="dataOut" class="out">Ready.</div>

        </div>

    </section>

</main>

</div>



<script>

let active = "overview";



function show(id, btn) {

    active = id;

    document.querySelectorAll(".section").forEach(x => x.classList.remove("active"));

    document.getElementById(id).classList.add("active");

    document.querySelectorAll(".nav button").forEach(x => x.classList.remove("active"));

    btn.classList.add("active");

}



function v(id) {

    return document.getElementById(id).value.trim();

}



function openPage(path) {

    window.open(path, "_blank");

}



function out() {

    return document.getElementById(active + "Out") || document.getElementById("overviewOut");

}



async function runInputCommand() {

    await runCommand(v("cmdInput"), v("userInput") || "Ryan");

}



async function runCommand(cmd, user = "Ryan") {

    const box = out();

    box.textContent = "Running " + cmd + "...";



    try {

        const r = await fetch("/chat?username=" + encodeURIComponent(user) + "&message=" + encodeURIComponent(cmd));

        const d = await r.json();

        box.textContent = "Command: " + cmd + "\\n\\nResponse:\\n" + (d.response || JSON.stringify(d, null, 2));

        refreshAll();

    } catch(e) {

        box.textContent = "Error: " + e;

    }

}



async function getJSON(path) {

    const r = await fetch(path);

    return await r.json();

}



async function refreshAll() {

    try {

        const f = await getJSON("/foxcoins");

        const rw = await getJSON("/rewards");

        const rd = await getJSON("/redemptions");



        document.getElementById("balCount").textContent = Object.keys(f.balances || {}).length;

        document.getElementById("rewCount").textContent = rw.reward_count || 0;

        document.getElementById("redCount").textContent = rd.count || 0;

    } catch(e) {}

}



refreshAll();

setInterval(refreshAll, 5000);

</script>

</body>

</html>

"""





@app.get("/legacy-admin", response_class=HTMLResponse)

def foxbot_admin_page():

    return foxbot_admin_html





@app.get("/recognition")

def recognition_endpoint():

    return {

        "settings": recognition_settings,

        "recent_log": recognition_log[:10],

        "manual_commands": [

            "!recognition",

            "!recognitionon",

            "!recognitionoff",

            "!recognitionlog",

            "!thankfollow username",

            "!thanksub username",

            "!thankgiftsub username 3",

            "!thankvote username 10",

            "!thanktip username 5",

            "!thankraid username",

            "!mvp username",

            "!og username",

            "!channel username https://blaze.stream/username",

            "!so username"

        ],

        "auto_test_endpoints": [

            "/auto-event/follow?username=avisi",

            "/auto-event/sub?username=avisi",

            "/auto-event/giftsub?username=avisi&amount=3",

            "/auto-event/vote?username=avisi&amount=10",

            "/auto-event/tip?username=avisi&amount=5",

            "/auto-event/raid?username=avisi"

        ]

    }





@app.get("/auto-event/{event_type}")

def auto_event_endpoint(event_type: str, username: str, amount: float = 1):

    message = recognition_response(event_type.lower(), username, amount)

    return {

        "event_type": event_type,

        "username": username,

        "amount": amount,

        "message": message,

        "settings": recognition_settings

    }







# ==============================

# FoxBot Studio v2

# ==============================



from fastapi.responses import HTMLResponse

from fastapi.staticfiles import StaticFiles



try:

    app.mount("/static", StaticFiles(directory="static"), name="static")

except Exception:

    pass



@app.get("/legacy-admin", response_class=HTMLResponse)

async def foxbot_studio_admin():

    with open("templates/foxbot_studio.html", "r", encoding="utf-8") as f:

        return f.read()



@app.get("/api/studio/stats")

async def foxbot_studio_stats():

    return {

        "followersToday": 0,

        "votesToday": 0,

        "subsToday": 0,

        "tipsToday": "$0",

        "foxcoinsToday": 0,

        "recognitionQueue": 0,

        "bossHp": "100%",

        "currentEvent": "None"

    }



@app.post("/api/studio/action/{action}")

async def foxbot_studio_action(action: str):

    return {

        "ok": True,

        "action": action,

        "message": f"{action.replace('_', ' ').title()} triggered."

    }





# ==============================

# FoxBot Studio State v1

# ==============================



from datetime import datetime



STUDIO_STATE = {

    "botOnline": True,

    "recognitionEnabled": True,

    "followersToday": 0,

    "votesToday": 0,

    "subsToday": 0,

    "tipsToday": "$0",

    "tipsTotal": 0,

    "foxcoinsToday": 0,

    "recognitionQueue": 0,

    "bossHp": "100%",

    "currentEvent": "None",

    "activity": [

        {

            "time": datetime.now().strftime("%I:%M:%S %p"),

            "message": "🦊 FoxBot Studio online."

        }

    ]

}



def studio_log(message: str):

    STUDIO_STATE["activity"].insert(0, {

        "time": datetime.now().strftime("%I:%M:%S %p"),

        "message": message

    })

    STUDIO_STATE["activity"] = STUDIO_STATE["activity"][:25]



def add_foxcoins(amount: int):

    STUDIO_STATE["foxcoinsToday"] += amount



def recognition_test(event_type: str):

    STUDIO_STATE["recognitionQueue"] += 1



    if event_type == "follow":

        STUDIO_STATE["followersToday"] += 1

        add_foxcoins(50)

        studio_log("? Test follow detected — +50 FoxCoins reward triggered.")



    elif event_type == "vote":

        STUDIO_STATE["votesToday"] += 1

        add_foxcoins(25)

        studio_log("??? Test vote detected — +25 FoxCoins reward triggered.")



    elif event_type == "sub":

        STUDIO_STATE["subsToday"] += 1

        add_foxcoins(250)

        studio_log("?? Test sub detected — +250 FoxCoins reward triggered.")



    elif event_type == "tip":

        STUDIO_STATE["tipsTotal"] += 5

        STUDIO_STATE["tipsToday"] = f"${STUDIO_STATE['tipsTotal']}"

        add_foxcoins(500)

        studio_log("?? Test tip detected — $5 tip +500 FoxCoins reward triggered.")



    elif event_type == "raid":

        add_foxcoins(300)

        studio_log("?? Test raid detected — raid recognition triggered.")



    STUDIO_STATE["recognitionQueue"] = max(0, STUDIO_STATE["recognitionQueue"] - 1)



# === TEMP DIAGNOSTIC — remove once a real payload has been captured ===
@app.get("/api/studio/debug/viewer-fallback-captures")
async def foxbot_viewer_fallback_debug_captures_v1(request: Request):
    """Gated by the existing /api/studio/ Basic Auth prefix. Read-only view
    of viewer_fallback_debug_log -- see its definition for what this is."""
    guard = _foxbot_require_admin_v1(request)
    if guard:
        return guard
    return {
        "ok": True,
        "note": "TEMP diagnostic for the @viewer thank-you bug. Remove this route once a real payload has been captured.",
        "count": len(viewer_fallback_debug_log),
        "captures": viewer_fallback_debug_log,
    }
# === End TEMP DIAGNOSTIC ===


@app.get("/api/studio/stats/live")

async def foxbot_studio_stats_live(request: Request):

    # Derived at read-time from real sources -- deliberately not STUDIO_STATE,
    # which is only ever written by manual test buttons (recognition_test,
    # studio_recognition_response), never by real chat/recognition traffic.

    # Bot Connection Sub-phase D, stage 6: request.state.blaze_id is only
    # ever set by the auth-gate middleware, only on a successful
    # allowlist-approved Blaze session -- absent (Basic Auth, or no auth
    # yet reached this far) means None here, which
    # _foxbot_resolve_creator_id_v1 falls back to tenant-zero for. This is
    # the dashboard-request-driven resolution path stage 1's resolver was
    # built for: blaze_id given directly, already canonical, no join
    # lookup needed.
    resolved_creator_id = _foxbot_resolve_creator_id_v1(
        blaze_id=getattr(request.state, "blaze_id", None)
    )

    foxcoins_total = sum(int(v) for v in _creator_economy_v1(resolved_creator_id)["balances"].values())

    tenant_stats = _creator_viewer_stats_v1(resolved_creator_id)

    commands_total = sum(int(v.get("commands", 0)) for v in tenant_stats.values())

    viewers_total = len(tenant_stats)

    # A short name/"None", not format_stream_event()'s full sentence --
    # that reads fine as a chat reply but overflows a landing-page tile.
    current_event = stream_event.get("name") if stream_event.get("active") else "None"

    from services import blaze_native_connector as native

    native_connected = bool(native.STATE.get("connected"))
    native_started_at = native.STATE.get("started_at")

    # uptime_seconds prefers the native connector's started_at when it's the
    # one confirmed connected; otherwise fall back to the legacy polling
    # worker's own started_at (set when blaze_polling_worker actually begins
    # its loop). Left None (not 0, not a stale number) when neither is running.
    uptime_seconds = None
    if native_connected and native_started_at:
        uptime_seconds = max(0, int(time.time() - native_started_at))
    elif polling_status.get("running") and polling_status.get("started_at"):
        uptime_seconds = max(0, int(time.time() - polling_status["started_at"]))

    bot_online = bool(polling_status.get("running")) or native_connected

    follows_total = _foxbot_events_v1.count_events(
        _foxbot_events_v1.resolve_owner_handle(), "follow"
    )

    return {

        "ok": True,

        "foxcoins_total": foxcoins_total,

        "commands_total": commands_total,

        "viewers_total": viewers_total,

        "current_event": current_event,

        "bot_online": bot_online,

        "uptime_seconds": uptime_seconds,

        "follows_total": follows_total,

    }



@app.post("/api/studio/action/live/{action}")

async def foxbot_studio_action_live(action: str):

    if action == "start_bot":

        STUDIO_STATE["botOnline"] = True

        studio_log("?? Bot started.")



    elif action == "stop_bot":

        STUDIO_STATE["botOnline"] = False

        studio_log("?? Bot stopped.")



    elif action == "restart_bot":

        STUDIO_STATE["botOnline"] = True

        studio_log("?? Bot restarted.")



    elif action == "toggle_recognition":

        STUDIO_STATE["recognitionEnabled"] = not STUDIO_STATE["recognitionEnabled"]

        status = "enabled" if STUDIO_STATE["recognitionEnabled"] else "disabled"

        studio_log(f"?? Recognition Engine {status}.")



    elif action == "test_follow":

        recognition_test("follow")



    elif action == "test_vote":

        recognition_test("vote")



    elif action == "test_sub":

        recognition_test("sub")



    elif action == "test_tip":

        recognition_test("tip")



    elif action == "test_raid":

        recognition_test("raid")



    elif action == "treasure_drop":

        STUDIO_STATE["currentEvent"] = "Treasure Drop"

        add_foxcoins(1000)

        studio_log("?? Treasure Drop started.")



    elif action == "start_boss":

        STUDIO_STATE["bossHp"] = "100%"

        studio_log("?? Boss Battle started.")



    elif action == "smoke_test":

        studio_log("? Smoke test completed successfully.")



    elif action == "save_data":

        studio_log("?? Save data requested.")



    elif action == "load_data":

        studio_log("?? Load data requested.")



    elif action == "reconnect_blaze":

        studio_log("?? Blaze reconnect requested.")



    elif action == "restart_polling":

        studio_log("?? Polling listener restarted.")



    elif action == "clear_cache":

        studio_log("?? Cache cleared.")



    elif action == "backup":

        studio_log("??? Backup created.")



    else:

        studio_log(f"?? {action.replace('_', ' ').title()} triggered.")



    return {

        "ok": True,

        "action": action,

        "state": STUDIO_STATE,

        "message": f"{action.replace('_', ' ').title()} complete."

    }





# ==============================

# FoxBot Recognition Engine v1

# ==============================



RECOGNITION_HISTORY = []



SUPPORT_REWARDS = {

    "follow": 50,

    "vote": 25,

    "sub": 250,

    "gift_sub": 300,

    "tip": 500,

    "raid": 300

}



RECOGNITION_TEMPLATES = {

    "follow": "? Thanks {user} for following the Fox Spirit family! +{reward} FoxCoins",

    "vote": "??? Thanks {user} for voting! +{reward} FoxCoins",

    "sub": "?? Huge love to {user} for subscribing! +{reward} FoxCoins",

    "gift_sub": "?? {user} gifted a sub! Absolute legend! +{reward} FoxCoins",

    "tip": "?? {user} tipped {amount}! +{reward} FoxCoins",

    "raid": "?? {user} raided the stream! Welcome raiders! +{reward} FoxCoins"

}



def studio_recognition_response(event_type: str, user: str = "TestUser", amount: str = "$5"):

    reward = SUPPORT_REWARDS.get(event_type, 0)

    template = RECOGNITION_TEMPLATES.get(event_type, "?? Thanks {user}!")



    message = template.format(

        user=user,

        reward=reward,

        amount=amount

    )



    add_foxcoins(reward)



    if event_type == "follow":

        STUDIO_STATE["followersToday"] += 1

    elif event_type == "vote":

        STUDIO_STATE["votesToday"] += 1

    elif event_type == "sub":

        STUDIO_STATE["subsToday"] += 1

    elif event_type == "tip":

        numeric_tip = 5

        STUDIO_STATE["tipsTotal"] += numeric_tip

        STUDIO_STATE["tipsToday"] = f"${STUDIO_STATE['tipsTotal']}"



    entry = {

        "time": datetime.now().strftime("%I:%M:%S %p"),

        "event": event_type,

        "user": user,

        "reward": reward,

        "message": message

    }



    RECOGNITION_HISTORY.insert(0, entry)

    RECOGNITION_HISTORY[:] = RECOGNITION_HISTORY[:50]



    studio_log(message)



    return entry



@app.post("/api/recognition/event/{event_type}")

async def foxbot_recognition_event(event_type: str):

    if not STUDIO_STATE.get("recognitionEnabled", True):

        studio_log("?? Recognition event ignored because Recognition Engine is disabled.")

        return {

            "ok": False,

            "reason": "Recognition Engine disabled"

        }



    result = service_studio_recognition_response(event_type=event_type)

    return {

        "ok": True,

        "result": result

    }



@app.get("/api/recognition/history")

async def foxbot_recognition_history():

    return {

        "ok": True,

        "history": RECOGNITION_HISTORY

    }



@app.get("/api/recognition/config")

async def foxbot_recognition_config(request: Request):
    guard = _foxbot_require_admin_v1(request)
    if guard:
        return guard

    return {

        "ok": True,

        "enabled": STUDIO_STATE.get("recognitionEnabled", True),

        "rewards": SUPPORT_REWARDS,

        "templates": RECOGNITION_TEMPLATES

    }





# ==============================

# FoxBot Blaze Listener Scaffold v1

# ==============================



BLAZE_LISTENER_STATE = {

    "connected": False,

    "lastEvent": "None",

    "eventsReceived": 0,

    "mappedEvents": 0

}



BLAZE_EVENT_MAP = {

    "follow": "follow",

    "follower": "follow",

    "vote": "vote",

    "sub": "sub",

    "subscription": "sub",

    "gift_sub": "gift_sub",

    "giftsub": "gift_sub",

    "tip": "tip",

    "donation": "tip",

    "raid": "raid"

}



def process_blaze_event(raw_event: dict):

    event_name = str(raw_event.get("type", "")).lower().strip()

    user = raw_event.get("user", "BlazeUser")

    amount = raw_event.get("amount", "$5")



    BLAZE_LISTENER_STATE["eventsReceived"] += 1

    BLAZE_LISTENER_STATE["lastEvent"] = event_name or "unknown"



    mapped_event = BLAZE_EVENT_MAP.get(event_name)



    if not mapped_event:

        studio_log(f"?? Unmapped Blaze event received: {event_name}")

        return {

            "ok": False,

            "reason": "Unmapped event",

            "raw": raw_event

        }



    BLAZE_LISTENER_STATE["mappedEvents"] += 1



    result = service_studio_recognition_response(event_type=mapped_event, user=user, amount=amount)



    return {

        "ok": True,

        "mapped_event": mapped_event,

        "result": result

    }



@app.post("/api/blaze/event")

async def foxbot_blaze_event(raw_event: dict):

    return process_blaze_event(raw_event)



@app.get("/api/blaze/listener/status")

async def foxbot_blaze_listener_status():

    return {

        "ok": True,

        "listener": BLAZE_LISTENER_STATE,

        "event_map": BLAZE_EVENT_MAP

    }



@app.post("/api/blaze/listener/connect")

async def foxbot_blaze_listener_connect():

    BLAZE_LISTENER_STATE["connected"] = True

    studio_log("?? Blaze Listener connected.")

    return {

        "ok": True,

        "message": "Blaze Listener connected.",

        "listener": BLAZE_LISTENER_STATE

    }



@app.post("/api/blaze/listener/disconnect")

async def foxbot_blaze_listener_disconnect():

    BLAZE_LISTENER_STATE["connected"] = False

    studio_log("?? Blaze Listener disconnected.")

    return {

        "ok": True,

        "message": "Blaze Listener disconnected.",

        "listener": BLAZE_LISTENER_STATE

    }

















# ==================================

# Blaze Listener Service Test

# ==================================



@app.get("/api/blaze/service-test")

async def blaze_service_test():



    blaze_listener.connect()



    return {

        "ok": True,

        "listener": blaze_listener.status()

    }





# ==================================

# Blaze Listener Service Routes v1

# ==================================



@app.post("/api/blaze/service/connect")

async def blaze_service_connect(request: Request):
    guard = _foxbot_require_admin_v1(request)
    if guard:
        return guard

    listener = blaze_listener.connect()

    studio_log("🔌 Blaze Listener service connected.")



    return {

        "ok": True,

        "message": "Blaze Listener service connected.",

        "listener": listener

    }





@app.post("/api/blaze/service/disconnect")

async def blaze_service_disconnect(request: Request):
    guard = _foxbot_require_admin_v1(request)
    if guard:
        return guard

    listener = blaze_listener.disconnect()

    studio_log("🔌 Blaze Listener service disconnected.")



    return {

        "ok": True,

        "message": "Blaze Listener service disconnected.",

        "listener": listener

    }





@app.get("/api/blaze/service/status")

async def blaze_service_status(request: Request):
    guard = _foxbot_require_admin_v1(request)
    if guard:
        return guard

    return {

        "ok": True,

        "listener": blaze_listener.status()

    }





@app.post("/api/blaze/service/event")

async def blaze_service_event(raw_event: dict, request: Request):
    guard = _foxbot_require_admin_v1(request)
    if guard:
        return guard

    event_name = str(raw_event.get("type", "")).lower().strip()

    user = raw_event.get("user", "BlazeUser")

    amount = raw_event.get("amount", "$5")



    blaze_listener.increment_event(event_name)



    mapped_event = blaze_listener.map_event(event_name)



    if not mapped_event:

        studio_log(f"âš ï¸ Unmapped Blaze service event: {event_name}")

        return {

            "ok": False,

            "reason": "Unmapped event",

            "event": event_name,

            "raw": raw_event

        }



    BLAZE_LISTENER_STATE["mappedEvents"] += 1



    result = service_studio_recognition_response(

        event_type=mapped_event,

        user=user,

        amount=amount

    )



    return {

        "ok": True,

        "event": event_name,

        "mapped_event": mapped_event,

        "result": result

    }





# ==============================

# FoxBot Studio Clean Route

# ==============================



@app.get("/studio", response_class=HTMLResponse)

async def foxbot_studio_clean():

    with open("templates/foxbot_studio.html", "r", encoding="utf-8") as f:

        return f.read()



# ==============================

# FoxBot Studio v2 (Phase 1 — static shell)

# ==============================



@app.get("/studio-v2", response_class=HTMLResponse)

async def foxbot_studio_v2():

    with open("templates/foxbot_studio_v2.html", "r", encoding="utf-8") as f:

        return f.read()







# ==============================

# FoxBot Studio Primary Admin Route

# ==============================



@app.get("/favicon.ico", include_in_schema=False)

async def foxbot_favicon_v1():

    return FileResponse("static/foxbot-logo.png", media_type="image/png")


@app.get("/admin", response_class=HTMLResponse)

async def foxbot_studio_primary_admin():

    with open("templates/foxbot_studio.html", "r", encoding="utf-8") as f:

        return f.read()





# ==============================

# FoxBot Studio Activity Tools

# ==============================



@app.post("/api/studio/activity/clear")

async def foxbot_clear_activity():

    STUDIO_STATE["activity"] = []

    studio_log("🧹 Activity feed cleared.")

    return {

        "ok": True,

        "activity": STUDIO_STATE["activity"]

    }





# ==============================

# FoxBot Studio Activity System v2

# ==============================



def studio_event(event_type: str, title: str, detail: str = "", icon: str = "🦊"):

    item = {

        "time": datetime.now().strftime("%I:%M:%S %p"),

        "type": event_type,

        "title": title,

        "detail": detail,

        "icon": icon,

        "message": f"{icon} {title} {detail}".strip()

    }



    STUDIO_STATE["activity"].insert(0, item)

    STUDIO_STATE["activity"] = STUDIO_STATE["activity"][:40]



    return item





@app.post("/api/studio/activity/demo")

async def foxbot_demo_activity():

    samples = [

        ("follow", "Ryan followed", "+50 FoxCoins", "â­"),

        ("sub", "Mike subscribed", "Recognition sent", "🔥"),

        ("tip", "Sarah tipped $20", "+500 FoxCoins", "💰"),

        ("boss", "Boss Battle started", "Cyber Fox Dragon appeared", "👑"),

        ("event", "Treasure Drop started", "Viewers can claim rewards", "🎁"),

        ("quest", "Community Quest updated", "Progress 4/10", "🎯")

    ]



    event_type, title, detail, icon = random.choice(samples)

    item = studio_event(event_type, title, detail, icon)



    return {

        "ok": True,

        "event": item,

        "activity": STUDIO_STATE["activity"]

    }







# ==============================

# Blaze Event Bridge v1

# Receives Blaze-style event payloads and maps them into FoxBot recognition.

# ==============================



blaze_event_bridge_seen = set()



BLAZE_EVENT_BRIDGE_ALIASES = {

    "follow": "follow",

    "follower": "follow",

    "new_follow": "follow",

    "new_follower": "follow",

    "user_followed": "follow",



    "vote": "vote",

    "votes": "vote",

    "voted": "vote",

    "user_voted": "vote",



    "sub": "sub",

    "subscribe": "sub",

    "subscription": "sub",

    "new_sub": "sub",

    "new_subscription": "sub",

    "user_subscribed": "sub",



    "giftsub": "giftsub",

    "gift_sub": "giftsub",

    "gifted_sub": "giftsub",

    "gifted_subscription": "giftsub",



    "tip": "tip",

    "tips": "tip",

    "donation": "tip",

    "donate": "tip",

    "tipped": "tip",

    "user_tipped": "tip",



    "raid": "raid",

    "raided": "raid",

    "incoming_raid": "raid",



    "mvp": "mvp",

    "og": "og"

}





def normalize_blaze_event_type(raw_event_type):

    raw = str(raw_event_type or "").strip().lower().replace("-", "_").replace(" ", "_")



    if raw in BLAZE_EVENT_BRIDGE_ALIASES:

        return BLAZE_EVENT_BRIDGE_ALIASES[raw]



    for alias, mapped in BLAZE_EVENT_BRIDGE_ALIASES.items():

        if alias in raw:

            return mapped



    return None





def first_bridge_value(payload, keys, default=None):

    if not isinstance(payload, dict):

        return default



    for key in keys:

        if key in payload and payload.get(key) not in [None, ""]:

            return payload.get(key)



    data = payload.get("data")

    if isinstance(data, dict):

        for key in keys:

            if key in data and data.get(key) not in [None, ""]:

                return data.get(key)



    user = payload.get("user")

    if isinstance(user, dict):

        for key in keys:

            if key in user and user.get(key) not in [None, ""]:

                return user.get(key)



    return default





@app.post("/api/blaze/event-bridge")

async def blaze_event_bridge(payload: dict):

    event_id = first_bridge_value(

        payload,

        ["id", "event_id", "eventId", "message_id", "messageId", "transaction_id", "transactionId"],

        None

    )



    if event_id:

        event_id = str(event_id)

        if event_id in blaze_event_bridge_seen:

            return {

                "ok": True,

                "duplicate": True,

                "event_id": event_id,

                "message": "Duplicate Blaze event ignored."

            }



        blaze_event_bridge_seen.add(event_id)



        if len(blaze_event_bridge_seen) > 500:

            blaze_event_bridge_seen.clear()



    raw_event_type = first_bridge_value(

        payload,

        ["event_type", "eventType", "type", "event", "name", "action"],

        ""

    )



    mapped_event = normalize_blaze_event_type(raw_event_type)



    username = first_bridge_value(

        payload,

        ["username", "user_name", "userName", "display_name", "displayName", "viewer", "sender", "from"],

        "viewer"

    )



    amount = first_bridge_value(

        payload,

        ["amount", "value", "votes", "vote_count", "voteCount", "tip", "dollars", "count", "quantity"],

        1

    )



    post_to_chat = bool(first_bridge_value(payload, ["post_to_chat", "postToChat", "send_to_chat", "sendToChat"], False))



    if not mapped_event:

        return {

            "ok": False,

            "reason": "unmapped_event",

            "raw_event_type": raw_event_type,

            "username": username,

            "amount": amount,

            "supported_events": sorted(set(BLAZE_EVENT_BRIDGE_ALIASES.values()))

        }



    message = recognition_response(mapped_event, username, amount)



    posted_to_blaze = False

    post_error = None



    if post_to_chat:

        try:

            send_blaze_chat_message(message)

            posted_to_blaze = True

        except Exception as error:

            post_error = str(error)



    return {

        "ok": True,

        "event_id": event_id,

        "raw_event_type": raw_event_type,

        "mapped_event": mapped_event,

        "username": username,

        "amount": amount,

        "message": message,

        "posted_to_blaze": posted_to_blaze,

        "post_error": post_error

    }





@app.get("/api/blaze/event-bridge")

async def blaze_event_bridge_info():

    return {

        "ok": True,

        "route": "/api/blaze/event-bridge",

        "method": "POST",

        "supported_events": sorted(set(BLAZE_EVENT_BRIDGE_ALIASES.values())),

        "example_payload": {

            "event_type": "follow",

            "username": "FoxFan",

            "amount": 1,

            "post_to_chat": False

        }

    }





# ==============================

# Auto Chat Event Parser v1

# Detects Blaze-style system/chat messages and triggers recognition automatically.

# ==============================



auto_chat_event_seen = set()



def parse_auto_chat_event(message_text: str, username: str = "viewer", item: dict = None):

    text = str(message_text or "").strip()

    lower = text.lower()



    if not text:

        return None



    event_user = normalize_viewer_name(username or "viewer")

    amount = 1

    event_type = None



    words = text.replace("@", "").replace("!", "").split()


    if "followed" in lower or "new follower" in lower:

        event_type = "follow"



    elif "subscribed" in lower or "new sub" in lower or "new subscription" in lower:

        event_type = "sub"



    elif "gifted" in lower and ("sub" in lower or "subscription" in lower):

        event_type = "giftsub"

        for word in words:

            if word.isdigit():

                amount = int(word)

                break



    elif _foxbot_item_has_vote_signal_v1(item):

        event_type = "vote"

        action_info = (item or {}).get("actionInfo") or {}

        vote_amount = action_info.get("amount")

        if isinstance(vote_amount, (int, float)) and not isinstance(vote_amount, bool) and vote_amount > 0:

            amount = int(vote_amount)

        else:

            for word in words:

                if word.isdigit():

                    amount = int(word)

                    break



    elif "tipped" in lower or "tip" in lower or "donated" in lower:

        event_type = "tip"

        for word in words:

            cleaned = word.replace("$", "").replace(",", "")

            try:

                value = float(cleaned)

                if value > 0:

                    amount = value

                    break

            except Exception:

                pass



    elif "raided" in lower or "raid" in lower:

        event_type = "raid"



    if not event_type:

        return None



    return {

        "event_type": event_type,

        "username": event_user,

        "amount": amount,

        "raw_message": text

    }





def handle_auto_chat_event(message_id: str, message_text: str, username: str = "viewer", item: dict = None, creator_id: str = None):

    event = parse_auto_chat_event(message_text, username, item)



    if not event:

        return None



    if not automation_event_enabled(event.get("event_type")):

        return {

            "ok": False,

            "disabled": True,

            "event": event,

            "message": f"Automation disabled for {event.get('event_type')} events."

        }



    if not foxbot_automation_allowed(event.get("event_type"), event.get("username")):

        return {

            "ok": True,

            "duplicate": True,

            "cooldown": True,

            "event": event,

            "message": "Automation cooldown ignored repeat event."

        }



    dedupe_key = str(message_id or event.get("raw_message") or "").strip()



    if dedupe_key:

        if dedupe_key in auto_chat_event_seen:

            return {

                "ok": True,

                "duplicate": True,

                "event": event

            }



        auto_chat_event_seen.add(dedupe_key)



        if len(auto_chat_event_seen) > 500:

            auto_chat_event_seen.clear()



    message = recognition_response(

        event["event_type"],

        event["username"],

        event["amount"],

        creator_id=creator_id

    )



    return {

        "ok": True,

        "event": event,

        "message": message

    }





@app.get("/api/blaze/parse-auto-event")

def test_parse_auto_event(message: str = "BridgeFan followed", username: str = "BridgeFan"):

    event = parse_auto_chat_event(message, username)

    return {

        "ok": bool(event),

        "input": message,

        "username": username,

        "event": event

    }





@app.get("/api/blaze/test-auto-chat-event")

def test_auto_chat_event(message: str = "BridgeFan followed", username: str = "BridgeFan"):

    result = handle_auto_chat_event(

        message_id=f"manual-test-{message}-{username}",

        message_text=message,

        username=username

    )



    return {

        "ok": bool(result),

        "input": message,

        "username": username,

        "result": result

    }







# ==============================

# Automation Control Center v1 backend

# Controls live recognition automation and event type toggles.

# ==============================



AUTOMATION_EVENT_DEFAULTS = {

    "follow": True,

    "vote": True,

    "sub": True,

    "giftsub": True,

    "tip": True,

    "raid": True,

    "mvp": True,

    "og": True

}





def ensure_automation_settings():

    recognition_settings.setdefault("enabled", True)

    recognition_settings.setdefault("surprise_bonus_enabled", True)

    recognition_settings.setdefault("surprise_bonus_chance", 15)

    recognition_settings.setdefault("enabled_events", AUTOMATION_EVENT_DEFAULTS.copy())



    for event_type, enabled in AUTOMATION_EVENT_DEFAULTS.items():

        recognition_settings["enabled_events"].setdefault(event_type, enabled)



    return recognition_settings





def automation_event_enabled(event_type: str):

    settings = ensure_automation_settings()



    if not settings.get("enabled", True):

        return False



    enabled_events = settings.get("enabled_events", {})

    return bool(enabled_events.get(str(event_type or "").lower(), True))





@app.get("/api/automation/status")

def automation_control_status(request: Request):
    guard = _foxbot_require_admin_v1(request)
    if guard:
        return guard

    settings = ensure_automation_settings()



    return {

        "ok": True,

        "recognition": settings,

        "polling": polling_status,

        "recent_log": recognition_log[:10],

        "supported_events": list(AUTOMATION_EVENT_DEFAULTS.keys())

    }





@app.post("/api/automation/recognition/{state}")

def automation_control_recognition(state: str, request: Request):
    guard = _foxbot_require_admin_v1(request)
    if guard:
        return guard

    settings = ensure_automation_settings()

    enabled = str(state).lower() in ["on", "true", "1", "enabled", "enable"]



    settings["enabled"] = enabled



    return {

        "ok": True,

        "message": f"Recognition automation {'enabled' if enabled else 'disabled'}.",

        "recognition": settings

    }





@app.post("/api/automation/event/{event_type}/{state}")

def automation_control_event_toggle(event_type: str, state: str, request: Request):
    guard = _foxbot_require_admin_v1(request)
    if guard:
        return guard

    settings = ensure_automation_settings()



    clean_event = str(event_type or "").lower().strip()

    if clean_event not in AUTOMATION_EVENT_DEFAULTS:

        return {

            "ok": False,

            "reason": "unsupported_event",

            "event_type": clean_event,

            "supported_events": list(AUTOMATION_EVENT_DEFAULTS.keys())

        }



    enabled = str(state).lower() in ["on", "true", "1", "enabled", "enable"]

    settings["enabled_events"][clean_event] = enabled



    return {

        "ok": True,

        "message": f"{clean_event} automation {'enabled' if enabled else 'disabled'}.",

        "recognition": settings

    }







# ==============================

# FoxBot v1 Final Hardening

# Adds automation cooldowns, readiness status, and final health summary.

# ==============================



automation_recent_events = {}



def foxbot_automation_cooldown_key(event_type: str, username: str):

    return f"{str(event_type or '').lower()}:{normalize_viewer_name(username or 'viewer').lower()}"



def foxbot_automation_allowed(event_type: str, username: str, cooldown_seconds: int = 20):

    now = time.time()

    key = foxbot_automation_cooldown_key(event_type, username)

    last_seen = automation_recent_events.get(key, 0)



    if now - float(last_seen or 0) < cooldown_seconds:

        return False



    automation_recent_events[key] = now



    if len(automation_recent_events) > 500:

        automation_recent_events.clear()



    return True



@app.get("/api/foxbot/v1/status")

def foxbot_v1_status():

    return {

        "ok": True,

        "version": "foxbot-v1",

        "studio": {

            "admin": True,

            "automation_control_center": True,

            "event_bridge_panel": True,

            "action_feedback": True

        },

        "automation": {

            "recognition_enabled": recognition_settings.get("enabled", True),

            "surprise_bonus_enabled": recognition_settings.get("surprise_bonus_enabled", True),

            "polling_running": polling_status.get("running", False),

            "checks": polling_status.get("checks", 0),

            "messages_seen": polling_status.get("messages_seen", 0),

            "commands_processed": polling_status.get("commands_processed", 0),

            "last_error": polling_status.get("last_error"),

            "last_auto_event": polling_status.get("last_auto_event"),

            "last_reply": polling_status.get("last_reply")

        },

        "features": {

            "foxcoins": True,

            "rewards": True,

            "giveaways": True,

            "boss_battle": True,

            "community_quests": True,

            "stream_events": True,

            "streaks": True,

            "recognition": True,

            "blaze_event_bridge": True,

            "auto_chat_parser": True,

            "live_blaze_listener": True

        },

        "recent_recognition": recognition_log[:10]

    }


# === FoxBot Connected Creators Route Helpers v2 ===
# The original helper block was removed in "Remove duplicate connected
# creators routes" while the routes below survived, leaving them to raise
# NameError at request time. These adapters back the routes with the modern
# FoxBot Connect data layer defined later in this file
# (_foxbot_connect_*_v1: flat handle-keyed JSON, mirrored to Neon), so both
# code paths share one dataset. All lookups happen at call time, after the
# whole module has loaded.
from pathlib import Path as _FoxPath
from fastapi.responses import HTMLResponse as _FoxHTMLResponse

_FOXBOT_CONNECTED_TEMPLATE = _FoxPath(__file__).resolve().parent / "templates" / "connected_creators.html"


def _foxbot_load_connected_creators():
    return {"creators": _foxbot_connect_load_raw_v1()}


def _foxbot_save_connected_creators(data):
    creators = (data or {}).get("creators", {})
    if isinstance(creators, dict):
        _foxbot_connect_save_raw_v1(creators)


def _foxbot_creator_totals(creators):
    values = [c for c in creators.values() if isinstance(c, dict)]
    return {
        "creators": len(values),
        "messages": sum(int(c.get("messages", 0) or 0) for c in values),
        "stars": sum(int(c.get("stars", 0) or 0) for c in values),
        "foxcoins": sum(int(c.get("foxcoins", 0) or 0) for c in values),
    }


def foxbot_connect_creator(handle: str, source: str = "manual", verified_follow: bool = False):
    creator = _foxbot_connect_upsert_creator_v1(handle, source=source)
    if not creator:
        return {"ok": False, "message": "Missing Blaze handle."}
    if verified_follow:
        raw = _foxbot_connect_load_raw_v1()
        key = creator.get("handle")
        if key in raw and isinstance(raw[key], dict):
            raw[key]["follow_status"] = "verified"
            _foxbot_connect_save_raw_v1(raw)
            creator = raw[key]
    return {
        "ok": True,
        "creator": creator,
        "message": f"{creator.get('handle')} is now connected to FoxBot."
    }


def _foxbot_touch_creator(handle: str):
    """Return (raw, key) for an existing creator, connecting them first if needed."""
    handle = _foxbot_connect_clean_handle_v1(handle)
    if not handle:
        return None, None
    raw = _foxbot_connect_load_raw_v1()
    for key in raw:
        if str(key).lower() == handle.lower():
            return raw, key
    _foxbot_connect_upsert_creator_v1(handle, source="auto")
    raw = _foxbot_connect_load_raw_v1()
    return (raw, handle) if handle in raw else (None, None)


def foxbot_record_creator_message(handle: str, amount: int = 1):
    raw, key = _foxbot_touch_creator(handle)
    if not key:
        return {"ok": False, "message": "Missing Blaze handle."}
    raw[key]["messages"] = int(raw[key].get("messages", 0) or 0) + int(amount or 1)
    raw[key]["last_seen_at"] = _foxbot_connect_now_iso_v1()
    _foxbot_connect_save_raw_v1(raw)
    return {"ok": True, "creator": raw[key]}


def foxbot_award_creator_foxcoins(handle: str, amount: int = 25, reason: str = "reward"):
    raw, key = _foxbot_touch_creator(handle)
    if not key:
        return {"ok": False, "message": "Missing Blaze handle."}
    raw[key]["foxcoins"] = int(raw[key].get("foxcoins", 0) or 0) + int(amount or 0)
    raw[key]["last_reward_reason"] = str(reason)
    raw[key]["last_seen_at"] = _foxbot_connect_now_iso_v1()
    _foxbot_connect_save_raw_v1(raw)
    return {"ok": True, "creator": raw[key]}


def foxbot_connected_chat_reply(handle: str, message: str):
    result = _foxbot_connect_process_command_v1(handle, message)
    if isinstance(result, dict):
        return result.get("reply")
    return None
# === End FoxBot Connected Creators Route Helpers v2 ===









@app.get("/connected-creators")

async def foxbot_connected_creators_page():

    if not _FOXBOT_CONNECTED_TEMPLATE.exists():

        html = "<h1>FoxBot Connected Creators</h1><p>Template missing: templates/connected_creators.html</p>"

    else:

        html = _FOXBOT_CONNECTED_TEMPLATE.read_text(encoding="utf-8")

    return _FoxHTMLResponse(content=html)



@app.get("/api/connected-creators")

async def foxbot_connected_creators_api():

    data = _foxbot_load_connected_creators()

    creators = data.get("creators", {})

    creator_list = list(creators.values())

    creator_list.sort(key=lambda c: (str(c.get("status", "")) != "connected", str(c.get("handle", ""))))



    return {

        "ok": True,

        "creators": creator_list,

        "totals": _foxbot_creator_totals(creators)

    }



@app.post("/api/connected-creators/connect")

async def foxbot_connected_creators_connect(payload: _FoxDict[str, _FoxAny]):

    handle = payload.get("handle") or payload.get("username") or payload.get("creator")

    verified_follow = bool(payload.get("verified_follow", False))

    return foxbot_connect_creator(handle, source="studio", verified_follow=verified_follow)



@app.post("/api/connected-creators/demo")

async def foxbot_connected_creators_demo():

    demo_names = ["demo_creator", "der_bruder", "mistersupercool", "vroski55", "jt_squared2", "agent00zani", "hollowgames"]

    data = _foxbot_load_connected_creators()



    for i, handle in enumerate(demo_names):

        result = foxbot_connect_creator(handle, source="demo", verified_follow=True)

        handle = result.get("creator", {}).get("handle", handle)

        data = _foxbot_load_connected_creators()

        if handle in data["creators"]:

            data["creators"][handle]["messages"] = [173, 4, 3, 35, 1, 187, 1][i]

            data["creators"][handle]["stars"] = [3, 0, 0, 0, 1, 0, 0][i]

            data["creators"][handle]["foxcoins"] = [263, 0, 0, 2500, 427, 552, 0][i]

            if i != 0:

                data["creators"][handle]["commands"] = []

                data["creators"][handle]["status"] = "getting set up..."

            _foxbot_save_connected_creators(data)



    return {"ok": True, "message": "Demo Connected Creators added."}



@app.post("/api/connected-creators/{handle}/foxcoins")

async def foxbot_connected_creators_award(handle: str, payload: _FoxDict[str, _FoxAny], request: Request):
    guard = _foxbot_require_admin_v1(request)
    if guard:
        return guard

    amount = int(payload.get("amount", 25) or 25)

    reason = str(payload.get("reason", "studio_reward"))

    return foxbot_award_creator_foxcoins(handle, amount, reason)



@app.post("/api/connected-creators/{handle}/message")

async def foxbot_connected_creators_message(handle: str, payload: _FoxDict[str, _FoxAny], request: Request):
    guard = _foxbot_require_admin_v1(request)
    if guard:
        return guard

    amount = int(payload.get("amount", 1) or 1)

    return foxbot_record_creator_message(handle, amount)



@app.post("/api/connected-creators/chat-test")

async def foxbot_connected_creators_chat_test(payload: _FoxDict[str, _FoxAny]):

    handle = payload.get("handle") or payload.get("username")

    message = payload.get("message") or ""

    reply = foxbot_connected_chat_reply(handle, message)

    return {"ok": True, "reply": reply}



@app.get("/api/connected-creators/me")

async def foxbot_connected_creators_me(handle: str = ""):

    handle = _foxbot_connect_clean_handle_v1(handle)

    if not handle:

        return {"ok": False, "error": "Missing handle."}

    raw = _foxbot_connect_load_raw_v1()

    creators = []

    for key, info in raw.items():

        if isinstance(info, dict):

            item = dict(info)

            item.setdefault("handle", key)

            creators.append(item)

    me = next(

        (c for c in creators if str(c.get("handle", "")).lower() == handle.lower()),

        None

    )

    if not me:

        return {"ok": False, "error": "not_connected", "handle": handle}

    ranked = sorted(creators, key=lambda c: int(c.get("foxcoins", 0) or 0), reverse=True)

    rank = next(

        (i + 1 for i, c in enumerate(ranked) if str(c.get("handle", "")).lower() == handle.lower()),

        None

    )

    days_connected = None

    try:

        from datetime import datetime, timezone

        connected_at = str(me.get("connected_at") or "")

        if connected_at:

            connected_dt = datetime.fromisoformat(connected_at.replace("Z", "+00:00"))

            if connected_dt.tzinfo is None:

                connected_dt = connected_dt.replace(tzinfo=timezone.utc)

            days_connected = max(0, int((datetime.now(timezone.utc) - connected_dt).total_seconds() // 86400))

    except Exception:

        days_connected = None

    return {

        "ok": True,

        "creator": me,

        "rank": rank,

        "creator_count": len(creators),

        "days_connected": days_connected

    }



# ============================================================

# END FOXBOT CONNECTED CREATORS V1

# ============================================================



# === FoxBot Connect Public Route v2 ===

# Safe public route for FoxBot Connect / Connected Creators.

@app.middleware("http")

async def foxbot_connect_public_route_v2(request, call_next):

    path = request.url.path.rstrip("/") or "/"



    if path in ["/connected-creators", "/foxbot-connect", "/api/connected-creators"]:

        import json

        from pathlib import Path

        from fastapi.responses import HTMLResponse, JSONResponse



        data_path = _foxbot_storage_path_v1("connected_creators.json", "FOXBOT_CONNECTED_CREATORS_FILE")

        data_path.parent.mkdir(parents=True, exist_ok=True)



        if not data_path.exists():

            starter = {

                "crypt0k1ng96": {

                    "handle": "crypt0k1ng96",

                    "status": "connected",

                    "commands": ["!connect", "!profile", "!rank", "!socials"],

                    "foxcoins": 73,

                    "stars": 0,

                    "messages": 72,

                    "connected_at": ""

                }

            }

            data_path.write_text(json.dumps(starter, indent=2), encoding="utf-8")



        try:

            raw = json.loads(data_path.read_text(encoding="utf-8") or "{}")

        except Exception:

            raw = {}



        creators = []

        if isinstance(raw, dict) and isinstance(raw.get("creators"), list):

            creators = raw.get("creators", [])

        elif isinstance(raw, dict):

            for handle, info in raw.items():

                if isinstance(info, dict):

                    item = dict(info)

                    item.setdefault("handle", handle)

                    creators.append(item)

        elif isinstance(raw, list):

            creators = raw



        payload = {"ok": True, "count": len(creators), "creators": creators}



        if path == "/api/connected-creators":

            return JSONResponse(payload)



        cards = ""

        total_messages = sum(int(c.get("messages", 0) or 0) for c in creators)

        total_foxcoins = sum(int(c.get("foxcoins", 0) or 0) for c in creators)

        for c in creators:

            handle = c.get("handle", "unknown")

            display_name = c.get("display_name") or handle

            status = c.get("status", "connected")

            status_class = "live" if str(status).lower() == "connected" else "pending"

            messages = c.get("messages", 0)

            stars = c.get("stars", 0)

            foxcoins = c.get("foxcoins", 0)

            initial = (str(handle)[:1] or "?").upper()

            command_tags = " ".join(f"<span class='tag'>{cmd}</span>" for cmd in c.get("commands", []))

            cards += f"""

            <div class='card' data-handle='{handle}'>

                <div class='card-top'>

                    <div class='avatar'>{initial}</div>

                    <div>

                        <div class='handle'>@{handle}</div>

                        <div class='display-name'>{display_name}</div>

                    </div>

                    <span class='pill {status_class}'>{status}</span>

                </div>

                <div class='stats'>

                    <div class='stat'><b>{messages}</b><span>messages</span></div>

                    <div class='stat'><b>{stars}</b><span>stars</span></div>

                    <div class='stat'><b>{foxcoins}</b><span>foxcoins</span></div>

                </div>

                <div class='commands'>{command_tags}</div>

            </div>

            """



        if not cards:

            cards = "<div class='empty'>🦊 No connected creators yet.<br>Follow FoxBot on Blaze and type <b>!connect</b> in chat — or use the button above.</div>"



        html = f"""

        <!doctype html>

        <html lang='en'>

        <head>

            <meta charset='utf-8'>

            <meta name='viewport' content='width=device-width, initial-scale=1'>

            <title>Connected Creators | FoxBot AI</title>

            <meta name='description' content='Creators connected to FoxBot AI on Blaze. Join them in minutes.'>

            <link rel='icon' type='image/png' href='/static/foxbot-logo.png'>

            <style>

                :root {{

                    --bg: #050807;

                    --panel: rgba(255,255,255,.05);

                    --border: rgba(255,255,255,.12);

                    --text: #f4f2ee;

                    --muted: rgba(244,242,238,.65);

                    --accent: #f97316;

                    --accent-soft: #ff9b3d;

                    --green: #4ade80;

                }}

                * {{ box-sizing: border-box; }}

                body {{

                    margin: 0;

                    min-height: 100vh;

                    font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;

                    background:

                        radial-gradient(1000px 500px at 85% -10%, rgba(249,115,22,.18), transparent 60%),

                        radial-gradient(800px 420px at -10% 20%, rgba(249,115,22,.10), transparent 55%),

                        var(--bg);

                    color: var(--text);

                }}

                .site-header {{

                    display: flex; align-items: center; gap: 18px;

                    max-width: 1100px; margin: 0 auto; padding: 18px 24px;

                }}

                .brand {{ display: flex; align-items: center; gap: 10px; color: var(--text); text-decoration: none; font-size: 19px; }}

                .brand img {{ width: 34px; height: 34px; border-radius: 9px; }}

                .brand strong {{ color: var(--accent-soft); }}

                .site-header nav {{ margin-left: auto; display: flex; gap: 18px; }}

                .site-header nav a {{ color: var(--muted); text-decoration: none; font-size: 14px; font-weight: 600; }}

                .site-header nav a:hover {{ color: var(--text); }}

                .wrap {{ max-width: 1100px; margin: 0 auto; padding: 12px 24px 64px; }}

                .hero {{

                    position: relative; overflow: hidden;

                    border: 1px solid var(--border);

                    background: linear-gradient(140deg, rgba(249,115,22,.14), rgba(255,255,255,.04) 45%);

                    border-radius: 26px;

                    padding: 40px 36px;

                    margin-bottom: 22px;

                }}

                h1 {{ margin: 0 0 10px; font-size: clamp(32px, 5vw, 46px); letter-spacing: -.5px; }}

                h1 span {{

                    background: linear-gradient(90deg, var(--accent), #ffb46b);

                    -webkit-background-clip: text; background-clip: text; color: transparent;

                }}

                .sub {{ color: var(--muted); font-size: 17px; max-width: 560px; line-height: 1.6; }}

                .hero-actions {{ margin-top: 22px; display: flex; gap: 12px; flex-wrap: wrap; }}

                .button {{

                    display: inline-block; padding: 12px 26px; border-radius: 12px;

                    background: linear-gradient(180deg, #ff8a2a, var(--accent));

                    color: #0b0b0f; font-weight: 700; text-decoration: none;

                    box-shadow: 0 8px 24px rgba(249,115,22,.35);

                    transition: transform .15s ease, box-shadow .15s ease;

                }}

                .button:hover {{ transform: translateY(-2px); box-shadow: 0 12px 30px rgba(249,115,22,.45); }}

                .button.secondary {{

                    background: rgba(255,255,255,.07); color: var(--text);

                    border: 1px solid var(--border); box-shadow: none;

                }}

                .totals {{

                    display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));

                    gap: 14px; margin-bottom: 26px;

                }}

                .total {{

                    border: 1px solid var(--border); background: var(--panel);

                    border-radius: 18px; padding: 18px 20px; text-align: center;

                }}

                .total b {{ display: block; font-size: 30px; color: var(--accent-soft); }}

                .total span {{ color: var(--muted); font-size: 13px; text-transform: uppercase; letter-spacing: 1px; }}

                .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 16px; }}

                .card {{

                    border: 1px solid var(--border); background: var(--panel);

                    border-radius: 20px; padding: 22px;

                    transition: transform .15s ease, border-color .15s ease, background .15s ease;

                }}

                .card:hover {{ transform: translateY(-3px); border-color: rgba(249,115,22,.5); background: rgba(255,255,255,.07); }}

                .card-top {{ display: flex; align-items: center; gap: 13px; }}

                .avatar {{

                    width: 46px; height: 46px; flex: none; border-radius: 50%;

                    display: flex; align-items: center; justify-content: center;

                    font-weight: 800; font-size: 20px; color: #0b0b0f;

                    background: linear-gradient(140deg, #ffb46b, var(--accent));

                }}

                .handle {{ font-size: 19px; font-weight: 800; color: var(--accent-soft); }}

                .display-name {{ color: var(--muted); font-size: 13px; }}

                .pill {{

                    margin-left: auto; padding: 4px 12px; border-radius: 999px;

                    font-size: 12px; font-weight: 700; text-transform: capitalize;

                }}

                .pill.live {{ color: var(--green); background: rgba(74,222,128,.12); border: 1px solid rgba(74,222,128,.35); }}

                .pill.pending {{ color: var(--accent-soft); background: rgba(249,115,22,.12); border: 1px solid rgba(249,115,22,.35); }}

                .stats {{ display: flex; gap: 10px; margin: 18px 0 14px; }}

                .stat {{

                    flex: 1; text-align: center; padding: 10px 6px;

                    background: rgba(0,0,0,.25); border-radius: 12px;

                }}

                .stat b {{ display: block; font-size: 18px; }}

                .stat span {{ color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: .5px; }}

                .commands {{ display: flex; flex-wrap: wrap; gap: 6px; }}

                .tag {{

                    font-size: 12px; font-weight: 600; color: var(--muted);

                    padding: 3px 10px; border-radius: 8px;

                    background: rgba(255,255,255,.06); border: 1px solid var(--border);

                }}

                .empty {{

                    grid-column: 1 / -1; text-align: center; color: var(--muted);

                    border: 1px dashed var(--border); border-radius: 20px;

                    padding: 48px 24px; font-size: 17px; line-height: 1.8;

                }}

                .claim-row {{

                    margin-top: 22px; padding-top: 18px;

                    border-top: 1px dashed var(--border);

                    display: flex; align-items: center; gap: 10px; flex-wrap: wrap;

                }}

                .claim-label {{ color: var(--muted); font-size: 14px; font-weight: 600; }}

                .claim-row input {{

                    padding: 10px 14px; border-radius: 12px; min-width: 210px;

                    background: rgba(0,0,0,.35); color: var(--text);

                    border: 1px solid var(--border); font-size: 14px; outline: none;

                }}

                .claim-row input:focus {{ border-color: rgba(249,115,22,.6); }}

                .claim-row .button {{ padding: 10px 18px; cursor: pointer; font-size: 14px; }}

                .claim-msg {{ color: var(--accent-soft); font-size: 13px; }}

                .me-top {{ display: flex; align-items: center; gap: 20px; flex-wrap: wrap; }}

                .me-avatar {{

                    width: 84px; height: 84px; flex: none; border-radius: 50%;

                    display: flex; align-items: center; justify-content: center;

                    font-weight: 800; font-size: 38px; color: #0b0b0f;

                    background: linear-gradient(140deg, #ffb46b, var(--accent));

                    box-shadow: 0 10px 30px rgba(249,115,22,.35);

                }}

                .me-greeting {{ color: var(--muted); font-size: 15px; letter-spacing: .3px; }}

                .me-name {{ margin: 2px 0 0; }}

                .me-meta {{ color: var(--muted); font-size: 14px; margin-top: 6px; line-height: 1.6; }}

                .me-pill {{ margin-left: auto; align-self: flex-start; }}

                .me-stats {{

                    display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));

                    gap: 12px; margin: 22px 0 4px;

                }}

                .me-stat {{

                    text-align: center; padding: 14px 8px;

                    background: rgba(0,0,0,.28); border: 1px solid var(--border); border-radius: 14px;

                }}

                .me-stat b {{ display: block; font-size: 24px; color: var(--accent-soft); }}

                .me-stat span {{ color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: .8px; }}

                .me-badges {{ display: flex; flex-wrap: wrap; gap: 6px; margin-top: 14px; }}

                .linklike {{

                    background: none; border: none; cursor: pointer;

                    color: var(--muted); font-size: 14px; text-decoration: underline;

                    padding: 12px 6px; font-family: inherit;

                }}

                .linklike:hover {{ color: var(--text); }}

                .card.is-me {{

                    border-color: rgba(249,115,22,.75);

                    box-shadow: 0 0 0 1px rgba(249,115,22,.5), 0 10px 30px rgba(249,115,22,.2);

                }}

                .you-pill {{

                    display: inline-block; vertical-align: middle; white-space: nowrap;

                    margin-left: 8px; padding: 2px 9px; border-radius: 999px;

                    font-size: 11px; font-weight: 700;

                    color: #0b0b0f; background: linear-gradient(180deg, #ff8a2a, var(--accent));

                }}

                footer {{ text-align: center; color: var(--muted); font-size: 13px; padding: 26px 0 12px; }}

                footer a {{ color: var(--accent-soft); text-decoration: none; }}

            </style>

        </head>

        <body>

            <header class='site-header'>

                <a href='/' class='brand'><img src='/static/foxbot-logo.png' alt='FoxBot AI'><span><strong>FoxBot</strong> AI</span></a>

                <nav>

                    <a href='/'>Home</a>

                    <a href='/get-started'>Get Started</a>

                    <a href='/demo-chat'>Live Demo</a>

                    <a href='/admin'>Studio</a>

                    <a href='/studio-v2'>Studio v2</a>

                </nav>

            </header>

            <div class='wrap'>

                <div class='hero'>

                    <div id='heroDefault'>

                        <h1>🦊 Connected <span>Creators</span></h1>

                        <div class='sub'>Blaze creators running their streams with FoxBot AI. Follow the FoxBot Blaze profile and type <b>!connect</b> in chat — or connect right here.</div>

                        <div class='hero-actions'>

                            <a href='/get-started' class='button'>Get Started — connect your channel</a>

                            <a href='/demo-chat' class='button secondary'>Try the live demo</a>

                        </div>

                        <div class='claim-row'>

                            <span class='claim-label'>Already in the pack?</span>

                            <input id='claimInput' placeholder='your Blaze handle' autocomplete='off' />

                            <button id='claimBtn' class='button secondary'>Open my den</button>

                            <span id='claimMsg' class='claim-msg'></span>

                        </div>

                    </div>

                    <div id='heroPersonal' hidden>

                        <div class='me-top'>

                            <div class='me-avatar' id='meAvatar'>?</div>

                            <div class='me-id'>

                                <div class='me-greeting' id='meGreeting'>Welcome back</div>

                                <h1 class='me-name'>@<span id='meName'>creator</span></h1>

                                <div class='me-meta' id='meMeta'></div>

                            </div>

                            <span class='pill live me-pill'>connected</span>

                        </div>

                        <div class='me-stats'>

                            <div class='me-stat'><b id='meFoxcoins'>0</b><span>FoxCoins</span></div>

                            <div class='me-stat'><b id='meMessages'>0</b><span>Messages</span></div>

                            <div class='me-stat'><b id='meStars'>0</b><span>Stars</span></div>

                            <div class='me-stat'><b id='meRank'>—</b><span>Pack rank</span></div>

                        </div>

                        <div class='me-badges' id='meBadges'></div>

                        <div class='hero-actions'>

                            <a href='/admin' class='button'>Open FoxBot Studio</a>

                            <a href='/studio-v2' class='button secondary'>Open Studio v2</a>

                            <a href='/demo-chat' class='button secondary'>Try the live demo</a>

                            <button id='meSignout' class='linklike'>Not you? Switch creator</button>

                        </div>

                    </div>

                </div>

                <div class='totals'>

                    <div class='total'><b>{len(creators)}</b><span>Creators</span></div>

                    <div class='total'><b>{total_messages}</b><span>Messages</span></div>

                    <div class='total'><b>{total_foxcoins}</b><span>FoxCoins earned</span></div>

                </div>

                <div class='grid'>{cards}</div>

                <footer>Powered by <a href='/'>FoxBot AI</a> — the creator command center for Blaze.</footer>

            </div>

            <script src='/static/js/connect-personal.js'></script>

        </body>

        </html>

        """

        return HTMLResponse(html)



    return await call_next(request)

# === End FoxBot Connect Public Route v2 ===



# === FoxBot Connect Command Engine v1 ===

# Handles Blaze chat commands like !connect, !profile, !rank, and !disconnect.

def _foxbot_connect_now_iso_v1():

    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()





def _foxbot_connect_clean_handle_v1(value):

    value = str(value or "").strip()

    value = value.replace("@", "").strip()

    value = "".join(ch for ch in value if ch.isalnum() or ch in ["_", "-", "."])

    return value[:64]





def _foxbot_connect_data_path_v1():

    from pathlib import Path

    path = _foxbot_storage_path_v1("connected_creators.json", "FOXBOT_CONNECTED_CREATORS_FILE")

    path.parent.mkdir(parents=True, exist_ok=True)

    return path





def _foxbot_connect_load_raw_v1():

    import json



    path = _foxbot_connect_data_path_v1()



    if not path.exists():

        starter = {

            "crypt0k1ng96": {

                "handle": "crypt0k1ng96",

                "status": "connected",

                "commands": ["!connect", "!profile", "!rank", "!socials"],

                "foxcoins": 73,

                "stars": 0,

                "messages": 72,

                "connected_at": _foxbot_connect_now_iso_v1(),

                "verification_method": "starter"

            }

        }

        path.write_text(json.dumps(starter, indent=2), encoding="utf-8")

        return starter



    try:

        raw = json.loads(path.read_text(encoding="utf-8") or "{}")

        return raw if isinstance(raw, dict) else {}

    except Exception:

        return {}





def _foxbot_connect_save_raw_v1(raw):

    import json



    path = _foxbot_connect_data_path_v1()

    path.write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")





def _foxbot_connect_get_creator_v1(handle):

    raw = _foxbot_connect_load_raw_v1()

    handle = _foxbot_connect_clean_handle_v1(handle)



    if not handle:

        return None



    existing = raw.get(handle)



    if isinstance(existing, dict):

        existing.setdefault("handle", handle)

        return existing



    lower = handle.lower()

    for key, value in raw.items():

        if str(key).lower() == lower and isinstance(value, dict):

            value.setdefault("handle", key)

            return value



    return None





def _foxbot_connect_upsert_creator_v1(handle, display_name=None, source="blaze_chat_command"):

    raw = _foxbot_connect_load_raw_v1()

    handle = _foxbot_connect_clean_handle_v1(handle)



    if not handle:

        return None



    existing_key = handle

    for key in list(raw.keys()):

        if str(key).lower() == handle.lower():

            existing_key = key

            break



    creator = raw.get(existing_key)

    if not isinstance(creator, dict):

        creator = {}



    creator.setdefault("handle", handle)

    creator["handle"] = creator.get("handle") or handle



    if display_name:

        creator["display_name"] = str(display_name).strip()[:80]



    creator["status"] = "connected"

    creator["verification_method"] = source

    creator["follow_status"] = creator.get("follow_status", "pending_public_sync")

    creator["connected_at"] = creator.get("connected_at") or _foxbot_connect_now_iso_v1()

    creator["last_seen_at"] = _foxbot_connect_now_iso_v1()

    creator["messages"] = int(creator.get("messages", 0) or 0) + 1

    creator["foxcoins"] = int(creator.get("foxcoins", 0) or 0) + 25

    creator["stars"] = int(creator.get("stars", 0) or 0)

    creator["commands"] = creator.get("commands") or ["!connect", "!profile", "!rank", "!socials"]

    creator["badges"] = creator.get("badges") or ["FoxBot Connected"]



    raw[existing_key] = creator

    _foxbot_connect_save_raw_v1(raw)

    return creator


def _foxbot_connect_set_blaze_id_v1(handle, blaze_id, display_name=None):
    """Bot Connection Sub-phase D, stage 1: writes the blaze_id join field
    onto a connected_creators.json record -- the write side of the
    creator_handle <-> blaze_id join Decision 1 requires
    (docs/bot-connection-track-scoping.md). Separate from
    _foxbot_connect_upsert_creator_v1 on purpose: that function's
    messages/foxcoins/badges bookkeeping is specific to a real chat
    interaction (each call means "a chat message just happened"), and a
    dashboard login isn't one -- calling it here would silently grant
    +25 FoxCoins and increment a message count on every login. This
    function only ever touches handle/display_name/blaze_id/
    last_seen_at, leaving chat-progression fields alone (unset if this
    creates a brand-new record, until/unless a real chat interaction
    later initializes them via the other function).

    Currently called from exactly one place: foxbot_dashboard_callback_v1,
    right after a successful (approved) dashboard login, using the
    blaze_id Blaze's own /v1/users/profile just verified -- never a
    caller-supplied value, same discipline as Sub-phase B's identity
    invariant."""
    handle = _foxbot_connect_clean_handle_v1(handle)

    if not handle or not blaze_id:
        return None

    raw = _foxbot_connect_load_raw_v1()

    existing_key = handle
    for key in list(raw.keys()):
        if str(key).lower() == handle.lower():
            existing_key = key
            break

    creator = raw.get(existing_key)
    if not isinstance(creator, dict):
        creator = {}

    creator.setdefault("handle", handle)
    creator["handle"] = creator.get("handle") or handle

    if display_name:
        creator["display_name"] = creator.get("display_name") or str(display_name).strip()[:80]

    creator["blaze_id"] = str(blaze_id).strip()
    creator["last_seen_at"] = _foxbot_connect_now_iso_v1()

    raw[existing_key] = creator

    _foxbot_connect_save_raw_v1(raw)

    return creator


def _foxbot_connect_clear_blaze_id_v1(blaze_id):
    """Bot Connection Sub-phase E, stage 4: the symmetric inverse of
    _foxbot_connect_set_blaze_id_v1 above -- unsets the blaze_id join
    field on every connected_creators.json record that carries it,
    without deleting the record itself (a creator's handle/messages/
    foxcoins/badges history isn't tied to whether a bot-connect OAuth
    slot currently exists; revoking the bot connection shouldn't erase
    it). Returns the number of records updated, 0 if none matched.

    blaze_id here is caller-supplied by design -- unlike Sub-phase B/D's
    write-side invariant (identity must come from Blaze's own
    verification, never a caller value), this is a targeted ADMIN
    cleanup operation, not a registration. The route calling this
    (foxbot_bot_connect_revoke_v1) is gated by the existing studio admin
    auth middleware; an admin choosing which slot to remove is the
    correct shape for a revoke, the mirror image of a creator's own
    OAuth completion being what's trusted for a register."""
    if not blaze_id:
        return 0

    raw = _foxbot_connect_load_raw_v1()
    blaze_id = str(blaze_id).strip()
    updated = 0

    for key, creator in raw.items():
        if isinstance(creator, dict) and str(creator.get("blaze_id") or "").strip() == blaze_id:
            creator.pop("blaze_id", None)
            updated += 1

    if updated:
        _foxbot_connect_save_raw_v1(raw)

    return updated


def _foxbot_resolve_blaze_id_for_handle_v1(creator_handle):
    """Bot Connection Sub-phase D, stage 1: the read side of the join --
    given a chat-side creator_handle, returns the mapped blaze_id from
    connected_creators.json, or None if this handle has never completed
    a dashboard login (or bot-connect OAuth, once Sub-phase E exists).
    None is a legitimate, expected result -- callers (see
    _foxbot_resolve_creator_id_v1) fall back to tenant-zero for it, they
    don't treat it as an error."""
    handle = _foxbot_connect_clean_handle_v1(creator_handle)

    if not handle:
        return None

    raw = _foxbot_connect_load_raw_v1()

    for key, creator in raw.items():
        if str(key).lower() == handle.lower() and isinstance(creator, dict):
            blaze_id = creator.get("blaze_id")
            if blaze_id:
                return str(blaze_id).strip()

    return None








@app.middleware("http")

async def foxbot_connect_command_engine_v1(request, call_next):

    path = request.url.path.rstrip("/") or "/"



    if path in ["/api/foxbot-connect/command", "/api/blaze-command", "/api/connect-command"]:

        from fastapi.responses import JSONResponse



        if request.method.upper() != "POST":

            return JSONResponse({

                "ok": False,

                "error": "Use POST with JSON: {handle, message, display_name}"

            }, status_code=405)



        try:

            body = await request.json()

        except Exception:

            body = {}



        handle = (

            body.get("handle")

            or body.get("username")

            or body.get("user")

            or body.get("name")

            or ""

        )



        display_name = body.get("display_name") or body.get("displayName") or handle

        message = body.get("message") or body.get("text") or body.get("content") or ""



        result = _foxbot_connect_process_command_v1(

            handle=handle,

            message=message,

            display_name=display_name

        )



        return JSONResponse(result)



    if path.startswith("/api/foxbot-connect/profile/"):

        from fastapi.responses import JSONResponse



        handle = path.split("/api/foxbot-connect/profile/", 1)[-1]

        creator = _foxbot_connect_get_creator_v1(handle)



        if not creator:

            return JSONResponse({

                "ok": False,

                "found": False,

                "handle": handle,

                "error": "Creator not connected yet."

            }, status_code=404)



        return JSONResponse({

            "ok": True,

            "found": True,

            "creator": creator

        })



    return await call_next(request)

# === End FoxBot Connect Command Engine v1 ===



# === Blaze Chat Bridge v1 ===

# Public bridge for Blaze chat/webhook/listener messages.

# External listener can POST: { "username": "viewer", "message": "!connect" }

@app.post("/api/blaze/chat")

async def blaze_chat_bridge_v1(payload: dict):

    username = (

        payload.get("username")

        or payload.get("handle")

        or payload.get("user")

        or payload.get("viewer")

        or payload.get("display_name")

        or "viewer"

    )



    message = (

        payload.get("message")

        or payload.get("text")

        or payload.get("content")

        or ""

    )



    username = str(username).replace("@", "").strip() or "viewer"

    message = str(message).strip()



    if not message:

        return {

            "ok": False,

            "handled": False,

            "error": "Missing message"

        }

    # Public, unauthenticated route (docs/FOXBOT_CONNECT_INTEGRATION.md) --
    # username is caller-supplied and must never be able to buy admin
    # authority through chat()'s privileged commands. allow_admin=False
    # forces admin=False in chat() regardless of what username is passed;
    # ordinary viewer commands (!connect, !profile, !rank, etc.) are
    # unaffected, since those don't check the admin flag at all.
    result = chat(message=message, username=username, allow_admin=False)



    reply = ""

    if isinstance(result, dict):

        reply = result.get("response") or result.get("reply") or ""



    return {

        "ok": True,

        "handled": bool(reply),

        "username": username,

        "message": message,

        "reply": reply,

        "chat_result": result

    }





@app.get("/api/blaze/chat/test")

def blaze_chat_bridge_test_v1(message: str = "!connect", username: str = "testviewer"):

    # Public, unauthenticated route -- see blaze_chat_bridge_v1 above.
    result = chat(message=message, username=username, allow_admin=False)



    reply = ""

    if isinstance(result, dict):

        reply = result.get("response") or result.get("reply") or ""



    return {

        "ok": True,

        "username": username,

        "message": message,

        "reply": reply,

        "chat_result": result

    }

# === End Blaze Chat Bridge v1 ===



# === FoxBot Connect Test Panel v1 ===

@app.get("/foxbot-connect-test")

@app.get("/connect-test")

def foxbot_connect_test_panel_v1():

    from fastapi.responses import HTMLResponse



    html = """

    <!doctype html>

    <html>

    <head>

        <meta charset="utf-8">

        <meta name="viewport" content="width=device-width, initial-scale=1">

        <title>FoxBot Connect Test Panel</title>

        <style>

            body {

                margin: 0;

                min-height: 100vh;

                font-family: Arial, sans-serif;

                background:

                    radial-gradient(circle at top left, rgba(255,122,24,.24), transparent 35%),

                    radial-gradient(circle at bottom right, rgba(57,255,136,.12), transparent 35%),

                    #050807;

                color: white;

                padding: 32px;

            }

            .wrap { max-width: 980px; margin: 0 auto; }

            .hero, .panel, .result {

                border: 1px solid rgba(255,255,255,.14);

                background: rgba(255,255,255,.055);

                border-radius: 24px;

                padding: 24px;

                margin-bottom: 18px;

                box-shadow: 0 22px 70px rgba(0,0,0,.28);

            }

            h1 { margin: 0 0 8px; font-size: 42px; }

            .sub { opacity: .78; line-height: 1.6; }

            label { display: block; margin: 14px 0 8px; font-weight: 700; }

            input {

                width: 100%;

                box-sizing: border-box;

                border: 1px solid rgba(255,255,255,.16);

                background: rgba(0,0,0,.25);

                color: white;

                border-radius: 14px;

                padding: 14px;

                font-size: 16px;

                outline: none;

            }

            .buttons { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 16px; }

            button, a.button {

                border: 0;

                border-radius: 999px;

                padding: 12px 16px;

                font-weight: 800;

                cursor: pointer;

                color: #07100b;

                background: #ff9b3d;

                text-decoration: none;

                display: inline-block;

            }

            button.secondary, a.secondary { background: #39ff88; }

            pre {

                white-space: pre-wrap;

                word-break: break-word;

                background: rgba(0,0,0,.28);

                border-radius: 16px;

                padding: 16px;

                border: 1px solid rgba(255,255,255,.1);

                min-height: 120px;

            }

            .reply {

                font-size: 20px;

                font-weight: 800;

                color: #39ff88;

                margin-top: 12px;

            }

        </style>

    </head>

    <body>

        <div class="wrap">

            <div class="hero">

                <h1>🦊 FoxBot Connect Test Panel</h1>

                <div class="sub">

                    Test Blaze chat commands through the live FoxBot bridge.

                    This uses <b>POST /api/blaze/chat</b>, the same endpoint the real Blaze connector should call.

                </div>

                <div class="buttons">

                    <a class="button secondary" href="/connected-creators">Connected Creators</a>

                    <a class="button secondary" href="/admin">FoxBot Studio</a>

                    <a class="button secondary" href="/studio-v2">Studio v2</a>

                </div>

            </div>



            <div class="panel">

                <label>Username / Blaze Handle</label>

                <input id="username" value="testviewer">



                <label>Message</label>

                <input id="message" value="!connect">



                <div class="buttons">

                    <button onclick="sendCommand('!connect')">!connect</button>

                    <button onclick="sendCommand('!profile')">!profile</button>

                    <button onclick="sendCommand('!rank')">!rank</button>

                    <button onclick="sendCommand('!disconnect')">!disconnect</button>

                    <button class="secondary" onclick="sendCustom()">Send Custom</button>

                </div>

            </div>



            <div class="result">

                <h2>FoxBot Reply</h2>

                <div id="reply" class="reply">Waiting for test...</div>

                <h3>Raw Response</h3>

                <pre id="raw">{}</pre>

            </div>

        </div>



        <script>

            async function callBridge(username, message) {

                const replyEl = document.getElementById("reply");

                const rawEl = document.getElementById("raw");



                replyEl.textContent = "Sending...";

                rawEl.textContent = "{}";



                try {

                    const res = await fetch("/api/blaze/chat", {

                        method: "POST",

                        headers: { "Content-Type": "application/json" },

                        body: JSON.stringify({ username, message })

                    });



                    const data = await res.json();

                    replyEl.textContent = data.reply || data.error || "No reply returned.";

                    rawEl.textContent = JSON.stringify(data, null, 2);

                } catch (err) {

                    replyEl.textContent = "Error calling bridge.";

                    rawEl.textContent = String(err);

                }

            }



            function sendCommand(command) {

                const username = document.getElementById("username").value || "testviewer";

                document.getElementById("message").value = command;

                callBridge(username, command);

            }



            function sendCustom() {

                const username = document.getElementById("username").value || "testviewer";

                const message = document.getElementById("message").value || "!connect";

                callBridge(username, message);

            }

        </script>

    </body>

    </html>

    """



    return HTMLResponse(html)

# === End FoxBot Connect Test Panel v1 ===



# === FoxBot Native Blaze Compatibility Routes v1 ===

# Compatibility layer inspired by BLAZEIAN-style Blaze eventsub/chat flow.

@app.get("/api/blaze/native/status")

def foxbot_blaze_native_status_v1():

    from services.blaze_native_connector import config_status

    return config_status()





@app.post("/api/blaze/native/start")

def foxbot_blaze_native_start_v1(request: Request):
    guard = _foxbot_require_admin_v1(request)
    if guard:
        return guard

    from services.blaze_native_connector import start_listener



    def handle_native_event(message):

        try:

            foxbot_blaze_native_event_ingest_sync_v1(message)

        except Exception:

            pass



    return start_listener(handle_native_event)





@app.post("/api/blaze/native/stop")

def foxbot_blaze_native_stop_v1(request: Request):
    guard = _foxbot_require_admin_v1(request)
    if guard:
        return guard

    from services.blaze_native_connector import stop_listener

    return stop_listener()





@app.post("/api/blaze/native/send")

async def foxbot_blaze_native_send_v1(payload: dict, request: Request):
    guard = _foxbot_require_admin_v1(request)
    if guard:
        return guard

    from services.blaze_native_connector import send_blaze_chat



    channel_id = payload.get("channel_id") or payload.get("channelId") or payload.get("channel") or ""

    message = payload.get("message") or payload.get("text") or ""



    return send_blaze_chat(channel_id, message)





def foxbot_blaze_native_event_ingest_sync_v1(payload: dict):

    from services.blaze_native_connector import parse_blaze_event, send_blaze_chat

    import os



    parsed = parse_blaze_event(payload)

    reply = ""

    chat_result = None

    send_result = None



    if parsed.get("kind") == "chat":

        username = parsed.get("username") or "viewer"

        message = parsed.get("message") or ""



        if message:

            chat_result = chat(message=message, username=username)



            if isinstance(chat_result, dict):

                reply = chat_result.get("response") or chat_result.get("reply") or ""



            auto_send = os.getenv("FOXBOT_BLAZE_AUTO_SEND", "false").lower() == "true"



            if reply and auto_send:

                send_result = send_blaze_chat(parsed.get("channel_id"), reply)



    elif parsed.get("kind") == "follow":

        username = parsed.get("username") or ""

        if username:

            try:

                _foxbot_connect_mark_follow_v1(username, "verified_public_follow")

                reply = f"🦊 @{username} followed FoxBot! Type !connect to activate your FoxBot profile."

            except Exception:

                reply = ""



    return {

        "ok": True,

        "parsed": parsed,

        "reply": reply,

        "chat_result": chat_result,

        "send_result": send_result,

    }





@app.post("/api/blaze/native/event")

async def foxbot_blaze_native_event_ingest_v1(payload: dict):

    return foxbot_blaze_native_event_ingest_sync_v1(payload)





@app.get("/api/foxbot-connect/instructions")

def foxbot_connect_public_instructions_v1():

    import os

    handle = os.getenv("FOXBOT_BLAZE_PROFILE_HANDLE", "@FoxBotStudio").strip()

    if handle and not handle.startswith("@"):

        handle = "@" + handle



    return {

        "ok": True,

        "bot_profile_handle": handle or "@FoxBotStudio",

        "steps": [

            f"Follow {handle or '@FoxBotStudio'} on Blaze.",

            "Join a supported creator chat.",

            "Type !connect.",

            "Use !profile or !rank to check FoxCoins and status.",

        ],

        "safety": "FoxBot will never ask for Blaze passwords, private keys, wallet seed phrases, or login codes.",

        "test_panel": "/foxbot-connect-test",

        "connected_creators": "/connected-creators",

    }





def _foxbot_connect_mark_follow_v1(handle, follow_status="verified_public_follow"):

    import json

    from datetime import datetime, timezone

    from pathlib import Path



    clean = str(handle or "").replace("@", "").strip()

    if not clean:

        return None



    path = _foxbot_storage_path_v1("connected_creators.json", "FOXBOT_CONNECTED_CREATORS_FILE")

    path.parent.mkdir(parents=True, exist_ok=True)



    try:

        raw = json.loads(path.read_text(encoding="utf-8") or "{}") if path.exists() else {}

    except Exception:

        raw = {}



    key = clean

    for existing in list(raw.keys()):

        if str(existing).lower() == clean.lower():

            key = existing

            break



    creator = raw.get(key) if isinstance(raw.get(key), dict) else {}

    creator.setdefault("handle", clean)

    creator["follow_status"] = follow_status

    creator["follow_verified_at"] = datetime.now(timezone.utc).isoformat()

    creator.setdefault("status", "connected")

    creator.setdefault("commands", ["!connect", "!profile", "!rank", "!socials"])

    creator.setdefault("badges", ["FoxBot Connected"])

    if "FoxBot Follower" not in creator["badges"]:

        creator["badges"].append("FoxBot Follower")



    raw[key] = creator

    path.write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")



    return creator





@app.post("/api/foxbot-connect/verify-follow")

async def foxbot_connect_verify_follow_v1(payload: dict):

    handle = payload.get("handle") or payload.get("username") or payload.get("user") or ""

    follow_status = payload.get("follow_status") or "verified_manual"



    creator = _foxbot_connect_mark_follow_v1(handle, follow_status)



    if not creator:

        return {

            "ok": False,

            "error": "Missing handle"

        }



    return {

        "ok": True,

        "creator": creator

    }





@app.get("/foxbot-connect-start")

def foxbot_connect_start_page_v1():

    import os

    from fastapi.responses import HTMLResponse



    handle = os.getenv("FOXBOT_BLAZE_PROFILE_HANDLE", "@FoxBotStudio").strip()

    if handle and not handle.startswith("@"):

        handle = "@" + handle



    html = f"""

    <!doctype html>

    <html>

    <head>

        <meta charset="utf-8">

        <meta name="viewport" content="width=device-width, initial-scale=1">

        <title>Start Using FoxBot Connect</title>

        <style>

            body {{

                margin: 0;

                min-height: 100vh;

                font-family: Arial, sans-serif;

                background:

                    radial-gradient(circle at top left, rgba(255,122,24,.25), transparent 35%),

                    radial-gradient(circle at bottom right, rgba(57,255,136,.12), transparent 35%),

                    #050807;

                color: white;

                padding: 32px;

            }}

            .wrap {{ max-width: 980px; margin: 0 auto; }}

            .card {{

                border: 1px solid rgba(255,255,255,.14);

                background: rgba(255,255,255,.055);

                border-radius: 24px;

                padding: 28px;

                margin-bottom: 18px;

            }}

            h1 {{ margin: 0 0 12px; font-size: 42px; }}

            .handle {{ color: #ff9b3d; font-size: 34px; font-weight: 900; }}

            li {{ margin: 12px 0; font-size: 19px; }}

            .safe {{ color: #39ff88; font-weight: 800; }}

            a {{ color: #39ff88; }}

        </style>

    </head>

    <body>

        <div class="wrap">

            <div class="card">

                <h1>🦊 Start Using FoxBot Connect</h1>

                <p class="handle">{handle}</p>

                <ol>

                    <li>Follow the FoxBot Blaze profile: <b>{handle}</b></li>

                    <li>Join a supported creator's Blaze chat.</li>

                    <li>Type <b>!connect</b>.</li>

                    <li>Use <b>!profile</b> or <b>!rank</b> to check your FoxCoins and status.</li>

                </ol>

                <p class="safe">FoxBot will never ask for passwords, private keys, seed phrases, or login codes.</p>

                <p><a href="/connected-creators">View Connected Creators</a> · <a href="/foxbot-connect-test">Test Panel</a></p>

            </div>

        </div>

    </body>

    </html>

    """



    return HTMLResponse(html)

# === End FoxBot Native Blaze Compatibility Routes v1 ===



# === FoxBot Blaze OAuth Routes v1 ===

# FoxBot-only Blaze OAuth setup for @foxbotai.

_FOXBOT_BLAZE_OAUTH_PENDING = {}



def _foxbot_blaze_oauth_post_json_v1(url, payload, timeout=15):

    import json

    import urllib.error

    import urllib.request



    data = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(

        url,

        data=data,

        headers={

            "content-type": "application/json",

            "accept": "application/json",

            "origin": "https://blaze.stream",

            "user-agent": "FoxBotAI/1.0"

        },

        method="POST"

    )



    try:

        with urllib.request.urlopen(req, timeout=timeout) as res:

            raw = res.read().decode("utf-8", errors="replace")

            return json.loads(raw or "{}")

    except urllib.error.HTTPError as e:

        details = ""

        try:

            details = e.read().decode("utf-8", errors="replace")

        except Exception:

            details = ""



        raise RuntimeError(

            f"Blaze OAuth HTTP {e.code} {e.reason}. Response body: {details}"

        )





def _foxbot_blaze_oauth_mask_v1(value):

    value = str(value or "")

    if len(value) <= 12:

        return "***" if value else ""

    return value[:6] + "..." + value[-6:]





class FoxBotBlazeIdentityMismatch(Exception):
    pass


def _foxbot_blaze_bot_expected_id_v1():
    """The tenant-zero bot's configured Blaze identity (BLAZE_BOT_USER_ID,
    falling back to FOXBOT_BLAZE_USER_ID), or "" if unset. Shared by
    _foxbot_blaze_oauth_verify_identity_v1's bootstrap check and
    _foxbot_blaze_oauth_save_tokens_v1's per-slot authorization decision
    so both read the exact same value -- pulled out during Bot Connection
    Sub-phase B so the two can't drift apart."""
    import os
    return (
        os.getenv("BLAZE_BOT_USER_ID", "")
        or os.getenv("FOXBOT_BLAZE_USER_ID", "")
    ).strip()


def _foxbot_blaze_oauth_verify_identity_v1(tokens):
    """Returns the Blaze-verified actual_id for these tokens -- the userId
    Blaze's own /v1/users/profile reports for the access_token just
    obtained via OAuth -- or None if it can't be determined (no
    BLAZE_CLIENT_ID configured, or no access_token in tokens; the
    existing missing-config error paths handle that downstream, same as
    before this function returned anything).

    Still raises FoxBotBlazeIdentityMismatch for the tenant-zero bootstrap
    race specifically (BLAZE_BOT_USER_ID/FOXBOT_BLAZE_USER_ID unset AND
    tokens already saved) -- a safety check on whether this save is even
    eligible to become the tenant-zero identity while the env var is
    unconfigured. Unrelated to, and unaffected by, Sub-phase B's per-slot
    authorization decision, which now lives in
    _foxbot_blaze_oauth_save_tokens_v1 instead of here.

    Bot Connection Sub-phase B invariant, load-bearing -- read this before
    touching this function or adding a new caller: actual_id is the ONLY
    value any code may ever use as a by_creator token-slot key. It comes
    exclusively from this Blaze API call -- never from a request
    parameter, cookie, or any other caller-supplied value. Any future call
    site (including whatever route Sub-phase E builds for per-creator
    bot-connect) that accepts a creator_id from outside this function
    reopens the exact token-clobbering vulnerability this whole gate
    exists to prevent. If you need to know "who is this," call this
    function -- do not thread a creator_id through from elsewhere.
    """
    import os

    access_token = (tokens or {}).get("accessToken") or (tokens or {}).get("access_token") or ""
    client_id = os.getenv("BLAZE_CLIENT_ID", "").strip()

    if not access_token or not client_id:
        # Nothing to check the new token against -- let it through here and
        # let the existing missing-config error paths handle it.
        return None

    res = _foxbot_blaze_http_json_v1(
        "GET",
        "https://api.blaze.stream/v1/users/profile",
        None,
        {
            "authorization": f"Bearer {access_token}",
            "client-id": client_id,
            "accept": "application/json",
            "user-agent": "FoxBotAI/1.0"
        }
    )

    data = ((res.get("body") or {}).get("data") or {}) if res.get("ok") else {}
    actual_id = data.get("userId")
    expected_id = _foxbot_blaze_bot_expected_id_v1()

    if not expected_id:
        # Bootstrap case: on a fresh deploy BLAZE_BOT_USER_ID/FOXBOT_BLAZE_USER_ID
        # are not set yet, so there is nothing to gate against and this save is
        # the only way to ever discover the bot's real Blaze userId. This must
        # only fire once -- if it fired on every call, leaving the env var unset
        # would make the gate a permanent no-op that lets any Blaze account
        # keep overwriting the saved tokens forever. So only allow it through
        # when nothing has been saved yet.
        #
        # Render's disk is ephemeral, so the local token file is gone after
        # every redeploy even though the Neon row survives -- checking the
        # file here would read already_saved=False on a fresh instance and
        # reopen the gate on each deploy. Ask Postgres directly instead of
        # going through storage_path()/_StateBackedPath: those only mirror
        # writes and hydrate the file opportunistically (once per process,
        # skipped for good on any transient DB error), so they cannot be
        # trusted for this check.
        import json
        from services.postgres_state import is_configured as _pg_is_configured
        from services.postgres_state import load_json_state_strict as _pg_load_json_state_strict

        already_saved = False
        if _pg_is_configured():
            # load_json_state_strict raises on a query failure instead of
            # returning None like load_json_state does -- None here means the
            # row is genuinely absent. A failure can't prove that, and since
            # this gate exists to stop token clobbering, treat "can't tell"
            # the same as "already saved": refuse rather than bootstrap.
            try:
                stored = _pg_load_json_state_strict("blaze_oauth_tokens")
            except Exception as error:
                raise FoxBotBlazeIdentityMismatch(
                    f"Could not confirm from Postgres whether FoxBot tokens are already "
                    f"saved ({error}); refusing to save Blaze account {actual_id!r}'s "
                    f"tokens without BLAZE_BOT_USER_ID/FOXBOT_BLAZE_USER_ID configured."
                )
            already_saved = bool(stored and (stored.get("accessToken") or stored.get("access_token")))
        else:
            path = _foxbot_storage_path_v1("blaze_oauth_tokens.json", "FOXBOT_OAUTH_TOKEN_FILE")
            if path.exists():
                try:
                    existing = json.loads(path.read_text(encoding="utf-8") or "{}")
                    already_saved = bool(existing.get("accessToken") or existing.get("access_token"))
                except Exception:
                    already_saved = False

        if already_saved:
            raise FoxBotBlazeIdentityMismatch(
                f"BLAZE_BOT_USER_ID/FOXBOT_BLAZE_USER_ID is not set, and tokens are already "
                f"saved -- refusing to let Blaze account {actual_id!r} overwrite them. Set "
                f"BLAZE_BOT_USER_ID to the id printed by the first successful login to lock "
                f"this down."
            )

        print(
            f"[FoxBot Blaze OAuth] No BLAZE_BOT_USER_ID/FOXBOT_BLAZE_USER_ID configured. "
            f"Blaze account {actual_id!r} just completed OAuth and its tokens were saved. "
            f"Set BLAZE_BOT_USER_ID={actual_id} in Render to stop future logins from any "
            f"other Blaze account from overwriting these tokens."
        )
        return actual_id

    # Sub-phase B: no more comparison/raise here -- this function's job is
    # purely "who does Blaze say this is." The accept/reject decision
    # (Sub-phase B.1: reject anyone but tenant-zero; B.2: register a new
    # slot instead) now lives in _foxbot_blaze_oauth_save_tokens_v1, the
    # one place actual_id gets used to decide what to write.
    return actual_id


def _foxbot_blaze_oauth_save_tokens_v1(tokens):

    import json

    from datetime import datetime, timezone

    from pathlib import Path



    actual_id = _foxbot_blaze_oauth_verify_identity_v1(tokens)

    # Bot Connection Sub-phase B: the per-slot authorization decision.
    # actual_id came ONLY from Blaze's own verification above. This
    # function has no creator_id parameter -- nothing else it could read
    # a slot key from -- so this decision is structurally incapable of
    # targeting any slot other than the one Blaze just proved this
    # request's identity to be (see the invariant documented on
    # _foxbot_blaze_oauth_verify_identity_v1).
    expected_id = _foxbot_blaze_bot_expected_id_v1()

    if actual_id and expected_id and str(actual_id).strip() != expected_id:
        # Sub-phase B.2: new-slot registration, not rejection. Self-
        # service, no allowlist (Decision 2,
        # docs/bot-connection-track-scoping.md) -- completing OAuth and
        # having Blaze verify the identity above IS the authorization;
        # there's no separate approval step.
        #
        # Isolation guarantee: this branch reads and writes ONLY
        # by_creator[actual_id]. It never opens the flat top-level keys
        # and never touches by_creator[tenant-zero] -- a second
        # creator's save cannot affect tenant-zero's tokens even by
        # accident, because this code has no reference to them at all.
        # (Today, nothing live can reach this branch: every live caller
        # of this function -- the /auth/blaze/callback login route and
        # the refresh flow, which only ever refreshes tenant-zero's own
        # flat-key refresh token -- always verifies back to tenant-zero's
        # own identity. Sub-phase E is what will eventually add a route
        # where actual_id could genuinely differ.)
        path = _foxbot_storage_path_v1("blaze_oauth_tokens.json", "FOXBOT_OAUTH_TOKEN_FILE")
        path.parent.mkdir(parents=True, exist_ok=True)

        existing = {}
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8") or "{}")
            except Exception:
                existing = {}

        by_creator = existing.setdefault("by_creator", {})
        slot = dict(by_creator.get(actual_id) or {})
        slot.update(tokens or {})
        slot["saved_at"] = datetime.now(timezone.utc).isoformat()
        by_creator[actual_id] = slot

        path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
        return slot

    # tenant-zero path: actual_id is None (config missing), matches
    # expected_id, or expected_id isn't configured yet (bootstrap case).
    # Flat keys + by_creator[tenant-zero] mirror, unchanged from before
    # Sub-phase B.



    path = _foxbot_storage_path_v1("blaze_oauth_tokens.json", "FOXBOT_OAUTH_TOKEN_FILE")

    path.parent.mkdir(parents=True, exist_ok=True)



    existing = {}

    if path.exists():

        try:

            existing = json.loads(path.read_text(encoding="utf-8") or "{}")

        except Exception:

            existing = {}



    merged = dict(existing)

    merged.update(tokens or {})

    merged["saved_at"] = datetime.now(timezone.utc).isoformat()

    _foxbot_blaze_oauth_sync_tenant_zero_slot_v1(merged, _tenant_zero_id())



    path.write_text(json.dumps(merged, indent=2), encoding="utf-8")

    return merged





@app.get("/auth/blaze/login")

def foxbot_blaze_oauth_login_v1():

    import os

    import time

    from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse



    client_id = os.getenv("BLAZE_CLIENT_ID", "").strip()

    client_secret = os.getenv("BLAZE_CLIENT_SECRET", "").strip()

    redirect_uri = os.getenv(

        "BLAZE_REDIRECT_URI",

        "https://foxbot-ai-chatbot.onrender.com/auth/blaze/callback"

    ).strip()



    if not client_id or not client_secret:

        return HTMLResponse(

            "<h1>FoxBot Blaze OAuth Missing Config</h1>"

            "<p>Add BLAZE_CLIENT_ID, BLAZE_CLIENT_SECRET, and BLAZE_REDIRECT_URI in Render first.</p>",

            status_code=500

        )



    scopes = ["users.read", "offline.access", "channel.moderate", "users.bot"]



    try:

        data = _foxbot_blaze_oauth_post_json_v1(

            "https://blaze.stream/bapi/oauth2/generate-auth-url",

            {

                "clientId": client_id,

                "clientSecret": client_secret,

                "redirectUri": redirect_uri,

                "scopes": scopes

            }

        )

    except Exception as e:

        return HTMLResponse(

            f"<h1>FoxBot Blaze OAuth Error</h1><p>Could not generate auth URL.</p><pre>{e}</pre>",

            status_code=500

        )



    state = data.get("state")

    code_verifier = data.get("codeVerifier")

    url = data.get("url")



    if not state or not code_verifier or not url:

        return HTMLResponse(

            f"<h1>FoxBot Blaze OAuth Error</h1><p>Blaze did not return state/codeVerifier/url.</p><pre>{data}</pre>",

            status_code=500

        )



    _foxbot_oauth_set_pending_v2(state, {

        "codeVerifier": code_verifier,

        "redirectUri": redirect_uri,

        "created_at": time.time()

    })



    response = RedirectResponse(url)



    # FoxBot OAuth cookie state v4

    # Keeps the exact state/codeVerifier paired through the browser redirect.

    try:

        response.set_cookie(

            "foxbot_oauth_state",

            state,

            max_age=900,

            httponly=True,

            secure=True,

            samesite="lax"

        )

        response.set_cookie(

            "foxbot_oauth_verifier",

            code_verifier,

            max_age=900,

            httponly=True,

            secure=True,

            samesite="lax"

        )

        response.set_cookie(

            "foxbot_oauth_redirect",

            redirect_uri,

            max_age=900,

            httponly=True,

            secure=True,

            samesite="lax"

        )

    except Exception:

        pass



    return response





@app.get("/auth/blaze/callback")

def foxbot_blaze_oauth_callback_v1(request: Request, code: str = "", state: str = ""):

    import json

    import os

    from fastapi.responses import HTMLResponse

    # Dashboard-login multiplex: STUDIO_DASHBOARD_REDIRECT_URI can be
    # pointed at this route -- the one redirect URI Blaze actually honors
    # (the other two, /auth/dashboard/callback and /auth/bot-connect/callback,
    # hit "invalid redirect_uri" even though they're registered in Blaze's
    # console -- see docs/blaze-dashboard-auth-plan.md). Checked FIRST, before
    # any tenant-zero state (the pending-lookup below) is touched, so a
    # request with no valid foxbot_dashboard_oauth_state cookie falls straight
    # through to the unmodified tenant-zero logic below -- byte-identical to
    # before this branch existed. This branch never calls
    # _foxbot_blaze_oauth_save_tokens_v1 and never touches
    # blaze_oauth_tokens.json or by_creator state -- it's pure delegation to
    # the same identity-only handler /auth/dashboard/callback already uses
    # (_foxbot_dashboard_oauth_callback_handle_v1, app.py, dashboard login
    # section below).
    dashboard_cookie_state = request.cookies.get("foxbot_dashboard_oauth_state")
    if dashboard_cookie_state and dashboard_cookie_state == state:
        return _foxbot_dashboard_oauth_callback_handle_v1(request, code, state)

    if not code:

        return HTMLResponse("<h1>FoxBot Blaze OAuth Error</h1><p>No code received.</p>", status_code=400)



    pending = _foxbot_oauth_get_pending_v2(state) if "_foxbot_oauth_get_pending_v2" in globals() else _FOXBOT_BLAZE_OAUTH_PENDING.get(state)



    if not pending:

        try:

            cookie_state = request.cookies.get("foxbot_oauth_state")

            cookie_verifier = request.cookies.get("foxbot_oauth_verifier")

            cookie_redirect = request.cookies.get("foxbot_oauth_redirect")



            if cookie_state == state and cookie_verifier:

                pending = {

                    "codeVerifier": cookie_verifier,

                    "redirectUri": cookie_redirect

                }

        except Exception:

            pass



    if not pending:

        return HTMLResponse(

            "<h1>FoxBot Blaze OAuth Error</h1>"

            "<p>OAuth state was not found. Open /auth/blaze/login again and complete login in the same browser session.</p>",

            status_code=400

        )



    client_id = os.getenv("BLAZE_CLIENT_ID", "").strip()

    client_secret = os.getenv("BLAZE_CLIENT_SECRET", "").strip()

    redirect_uri = pending.get("redirectUri") or os.getenv(

        "BLAZE_REDIRECT_URI",

        "https://foxbot-ai-chatbot.onrender.com/auth/blaze/callback"

    ).strip()



    try:

        # Exact Blaze/BLAZEIAN-style token exchange.

        # Do not try multiple formats here because the authorization code is one-time use.

        tokens = _foxbot_blaze_oauth_post_json_v1(

            "https://blaze.stream/bapi/oauth2/token",

            {

                "clientId": client_id,

                "clientSecret": client_secret,

                "code": code,

                "codeVerifier": pending.get("codeVerifier"),

                "redirectUri": redirect_uri,

                "grantType": "authorization_code"

            }

        )

        tokens["exchange_style"] = "json_camel_grant_cookie_state"

    except Exception as e:

        return HTMLResponse(

            f"<h1>FoxBot Blaze OAuth Token Error</h1><p>Could not exchange code for token.</p><pre>{e}</pre>",

            status_code=500

        )



    _foxbot_oauth_pop_pending_v2(state)

    try:

        saved = _foxbot_blaze_oauth_save_tokens_v1(tokens)

    except FoxBotBlazeIdentityMismatch as e:

        return HTMLResponse(
            f"<h1>FoxBot Blaze OAuth Rejected</h1>"
            f"<p>This Blaze account is not the configured FoxBot bot account, so its "
            f"tokens were not saved.</p><pre>{e}</pre>",
            status_code=403
        )



    access_token = saved.get("accessToken") or saved.get("access_token") or ""

    refresh_token = saved.get("refreshToken") or saved.get("refresh_token") or ""



    env_text = (

        "BLAZE_ACCESS_TOKEN=" + access_token + "\n"

        "BLAZE_REFRESH_TOKEN=" + refresh_token

    )



    # Personal touch: greet the person who just logged in by their Blaze name

    # and register them as a connected creator so their den is ready.

    profile_name = ""

    try:

        import requests as _fox_requests

        prof_res = _fox_requests.get(

            "https://api.blaze.stream/v1/users/profile",

            headers={

                "Authorization": f"Bearer {access_token}",

                "client-id": client_id,

                "Accept": "application/json"

            },

            timeout=15

        )

        prof = prof_res.json() if prof_res.ok else {}

        node = prof.get("data") if isinstance(prof.get("data"), dict) else prof

        if isinstance(node, dict):

            for key in ("username", "handle", "slug", "displayName", "display_name", "name"):

                value = node.get(key)

                if value:

                    profile_name = str(value).strip().lstrip("@")[:40]

                    break

    except Exception:

        profile_name = ""



    if profile_name:

        try:

            _foxbot_connect_upsert_creator_v1(profile_name, display_name=profile_name, source="blaze_oauth")

        except Exception:

            pass



    hour = __import__("datetime").datetime.now().hour

    if hour < 5:

        day_greeting = "Burning the midnight oil"

    elif hour < 12:

        day_greeting = "Good morning"

    elif hour < 18:

        day_greeting = "Good afternoon"

    else:

        day_greeting = "Good evening"



    if profile_name:

        headline = f"{day_greeting}, @{profile_name}! 🦊"

        subline = "You're logged in with Blaze and FoxBot just rolled out the welcome mat. Your den is ready."

        den_link = f"/connected-creators?me={profile_name}&connected=1"

    else:

        headline = "You're connected! 🦊"

        subline = "Blaze login complete — FoxBot is now linked to your account."

        den_link = "/connected-creators"



    html = f"""

    <!doctype html>

    <html>

    <head>

        <title>Welcome to FoxBot</title>

        <meta name="viewport" content="width=device-width, initial-scale=1">

        <link rel="icon" type="image/png" href="/static/foxbot-logo.png">

        <style>

            body {{

                font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;

                background:

                    radial-gradient(1000px 500px at 85% -10%, rgba(249,115,22,.18), transparent 60%),

                    #050807;

                color: #f4f2ee; padding: 32px; margin: 0; min-height: 100vh;

            }}

            .card {{

                max-width: 720px; margin: 40px auto 0;

                border: 1px solid rgba(255,255,255,.14); background: rgba(255,255,255,.06);

                border-radius: 24px; padding: 36px; text-align: center;

            }}

            .fox-avatar {{

                width: 88px; height: 88px; border-radius: 50%; margin: 0 auto 18px;

                display: flex; align-items: center; justify-content: center;

                font-size: 40px; font-weight: 800; color: #0b0b0f;

                background: linear-gradient(140deg, #ffb46b, #f97316);

                box-shadow: 0 10px 30px rgba(249,115,22,.4);

            }}

            h1 {{ margin: 0 0 10px; font-size: clamp(26px, 5vw, 38px); }}

            .sub {{ color: rgba(244,242,238,.7); font-size: 16px; line-height: 1.7; max-width: 480px; margin: 0 auto; }}

            .actions {{ margin-top: 26px; display: flex; gap: 12px; justify-content: center; flex-wrap: wrap; }}

            .button {{

                display: inline-block; padding: 13px 28px; border-radius: 12px;

                background: linear-gradient(180deg, #ff8a2a, #f97316);

                color: #0b0b0f; font-weight: 700; text-decoration: none;

                box-shadow: 0 8px 24px rgba(249,115,22,.35);

            }}

            .button.secondary {{

                background: rgba(255,255,255,.07); color: #f4f2ee;

                border: 1px solid rgba(255,255,255,.14); box-shadow: none;

            }}

            details {{

                max-width: 720px; margin: 22px auto 40px; text-align: left;

                border: 1px solid rgba(255,255,255,.1); border-radius: 16px;

                padding: 16px 20px; background: rgba(255,255,255,.03);

                color: rgba(244,242,238,.8);

            }}

            summary {{ cursor: pointer; font-weight: 600; color: rgba(244,242,238,.65); }}

            textarea {{

                width: 100%; min-height: 120px; margin-top: 12px; box-sizing: border-box;

                background: #111; color: #39ff88; border: 1px solid #333; border-radius: 12px; padding: 14px;

            }}

            code {{ color: #ff9b3d; }}

            details a {{ color: #39ff88; }}

        </style>

    </head>

    <body>

        <div class="card">

            <div class="fox-avatar">{(profile_name[:1] or "🦊").upper() if profile_name else "🦊"}</div>

            <h1>{headline}</h1>

            <p class="sub">{subline}</p>

            <div class="actions">

                <a class="button" href="{den_link}">Open my creator den</a>

                <a class="button secondary" href="/demo-chat">Try the live demo</a>

            </div>

        </div>

        <details>

            <summary>Bot owner setup — OAuth tokens (keep private)</summary>

            <p><b>Access token:</b> {_foxbot_blaze_oauth_mask_v1(access_token)}<br>

            <b>Refresh token:</b> {_foxbot_blaze_oauth_mask_v1(refresh_token)}</p>

            <p>Add these to <b>Render → foxbot-ai-chatbot → Environment</b>. Do not share them publicly.</p>

            <textarea readonly>{env_text}</textarea>

            <ol>

                <li>Add the two variables above to Render.</li>

                <li>Keep <code>FOXBOT_BLAZE_AUTO_SEND=false</code> for now.</li>

                <li>Redeploy.</li>

                <li>Open <a href="/api/blaze/native/status">/api/blaze/native/status</a>.</li>

            </ol>

        </details>

    </body>

    </html>

    """



    return HTMLResponse(html)


# === FoxBot Studio Dashboard Login (separate from bot OAuth) ===
# A distinct, parallel OAuth flow so approved people can log into the
# studio dashboard with their own Blaze identity, instead of everyone
# sharing STUDIO_ADMIN_USER/PASSWORD. Deliberately independent of
# /auth/blaze/login + /auth/blaze/callback above: different routes,
# different cookie names, minimal users.read scope only (no
# offline.access/channel.moderate/users.bot), and it never touches
# blaze_oauth_tokens.json, BLAZE_BOT_USER_ID, or any bot-identity state.
# The access token is used once to read the visitor's profile, then
# discarded -- nothing long-lived is stored per person.
#
# Env vars this flow reads:
#   STUDIO_APPROVED_BLAZE_USER_IDS  comma-separated allowlist of Blaze
#                                    userIds permitted to get a session.
#   STUDIO_SESSION_SECRET           HMAC key signing the session cookie.
#   STUDIO_AUTH_MODE                basic|blaze|both (default "both").
#                                    Basic Auth stays fully intact in
#                                    "both" -- see foxbot_studio_admin_auth_gate_v1.

_FOXBOT_DASHBOARD_SESSION_MAX_AGE = 24 * 60 * 60  # 24h


def _foxbot_dashboard_session_sign_v1(blaze_id, display_name) -> str:
    import base64
    import hashlib
    import hmac
    import json
    import time

    secret = os.getenv("STUDIO_SESSION_SECRET", "").strip()
    if not secret:
        raise RuntimeError("STUDIO_SESSION_SECRET is not configured.")

    payload = json.dumps({
        "blaze_id": str(blaze_id),
        "display_name": str(display_name or ""),
        "issued_at": time.time(),
    }).encode("utf-8")

    payload_b64 = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    signature = hmac.new(secret.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{signature}"


def _foxbot_dashboard_session_verify_v1(token):
    import base64
    import hashlib
    import hmac
    import json
    import time

    secret = os.getenv("STUDIO_SESSION_SECRET", "").strip()
    if not secret or not token or "." not in token:
        return None

    payload_b64, _, signature = token.rpartition(".")
    expected = hmac.new(secret.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(signature, expected):
        return None

    try:
        padding = "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64 + padding).decode("utf-8"))
    except Exception:
        return None

    issued_at = payload.get("issued_at")
    if not isinstance(issued_at, (int, float)) or time.time() - issued_at > _FOXBOT_DASHBOARD_SESSION_MAX_AGE:
        return None

    if not payload.get("blaze_id"):
        return None

    return payload


def _foxbot_dashboard_user_is_approved_v1(blaze_id) -> bool:
    if not blaze_id:
        return False

    approved = [
        x.strip() for x in os.getenv("STUDIO_APPROVED_BLAZE_USER_IDS", "").split(",") if x.strip()
    ]
    return str(blaze_id).strip() in approved


@app.get("/auth/dashboard/login")
def foxbot_dashboard_login_v1():
    from fastapi.responses import HTMLResponse, RedirectResponse

    client_id = os.getenv("BLAZE_CLIENT_ID", "").strip()
    client_secret = os.getenv("BLAZE_CLIENT_SECRET", "").strip()
    redirect_uri = os.getenv(
        "STUDIO_DASHBOARD_REDIRECT_URI",
        "https://foxbot-ai-chatbot.onrender.com/auth/dashboard/callback"
    ).strip()

    if not client_id or not client_secret:
        return HTMLResponse(
            "<h1>FoxBot Dashboard Login Missing Config</h1>"
            "<p>Add BLAZE_CLIENT_ID and BLAZE_CLIENT_SECRET in Render first.</p>",
            status_code=500
        )

    if not os.getenv("STUDIO_SESSION_SECRET", "").strip():
        return HTMLResponse(
            "<h1>FoxBot Dashboard Login Missing Config</h1>"
            "<p>Add STUDIO_SESSION_SECRET in Render first.</p>",
            status_code=500
        )

    try:
        data = _foxbot_blaze_oauth_post_json_v1(
            "https://blaze.stream/bapi/oauth2/generate-auth-url",
            {
                "clientId": client_id,
                "clientSecret": client_secret,
                "redirectUri": redirect_uri,
                "scopes": ["users.read"]
            }
        )
    except Exception as e:
        return HTMLResponse(
            f"<h1>FoxBot Dashboard Login Error</h1><p>Could not generate auth URL.</p><pre>{e}</pre>",
            status_code=500
        )

    state = data.get("state")
    code_verifier = data.get("codeVerifier")
    url = data.get("url")

    if not state or not code_verifier or not url:
        return HTMLResponse(
            f"<h1>FoxBot Dashboard Login Error</h1><p>Blaze did not return state/codeVerifier/url.</p><pre>{data}</pre>",
            status_code=500
        )

    response = RedirectResponse(url)

    # Distinct cookie names from the bot flow's foxbot_oauth_state /
    # foxbot_oauth_verifier / foxbot_oauth_redirect -- a concurrent bot
    # re-auth and a dashboard login in the same browser must not collide.
    try:
        response.set_cookie(
            "foxbot_dashboard_oauth_state", state,
            max_age=900, httponly=True, secure=True, samesite="lax"
        )
        response.set_cookie(
            "foxbot_dashboard_oauth_verifier", code_verifier,
            max_age=900, httponly=True, secure=True, samesite="lax"
        )
        response.set_cookie(
            "foxbot_dashboard_oauth_redirect", redirect_uri,
            max_age=900, httponly=True, secure=True, samesite="lax"
        )
    except Exception:
        pass

    return response


def _foxbot_dashboard_oauth_callback_handle_v1(request: Request, code: str = "", state: str = ""):
    """Shared body for the dashboard-login OAuth callback. Reused by both
    /auth/dashboard/callback (foxbot_dashboard_callback_v1, below) and the
    dashboard branch multiplexed through /auth/blaze/callback
    (foxbot_blaze_oauth_callback_v1, app.py:19598) -- STUDIO_DASHBOARD_REDIRECT_URI
    can point at either path, so both routes must run this exact same logic.
    Identity-only, unchanged from before this was factored out: never calls
    _foxbot_blaze_oauth_save_tokens_v1, never touches blaze_oauth_tokens.json
    or by_creator state.
    """
    from fastapi.responses import HTMLResponse, RedirectResponse

    if not code:
        return HTMLResponse("<h1>FoxBot Dashboard Login Error</h1><p>No code received.</p>", status_code=400)

    cookie_state = request.cookies.get("foxbot_dashboard_oauth_state")
    cookie_verifier = request.cookies.get("foxbot_dashboard_oauth_verifier")
    cookie_redirect = request.cookies.get("foxbot_dashboard_oauth_redirect")

    if not cookie_state or cookie_state != state or not cookie_verifier:
        return HTMLResponse(
            "<h1>FoxBot Dashboard Login Error</h1>"
            "<p>Login state was not found or didn't match. Open /auth/dashboard/login again "
            "and complete login in the same browser session.</p>",
            status_code=400
        )

    client_id = os.getenv("BLAZE_CLIENT_ID", "").strip()
    client_secret = os.getenv("BLAZE_CLIENT_SECRET", "").strip()
    redirect_uri = cookie_redirect or os.getenv(
        "STUDIO_DASHBOARD_REDIRECT_URI",
        "https://foxbot-ai-chatbot.onrender.com/auth/dashboard/callback"
    ).strip()

    try:
        tokens, _style = _foxbot_blaze_exchange_code_v3(
            client_id, client_secret, code, cookie_verifier, redirect_uri
        )
    except Exception as e:
        return HTMLResponse(
            f"<h1>FoxBot Dashboard Login Error</h1><p>Could not exchange code for token.</p><pre>{e}</pre>",
            status_code=500
        )

    access_token = (tokens or {}).get("accessToken") or (tokens or {}).get("access_token") or ""
    if not access_token:
        return HTMLResponse(
            "<h1>FoxBot Dashboard Login Error</h1><p>No access token returned.</p>",
            status_code=500
        )

    # Identity-only: read the profile once, then let access_token/tokens
    # fall out of scope. Nothing per-person is saved to disk or Postgres --
    # this flow never calls _foxbot_blaze_oauth_save_tokens_v1.
    profile = _foxbot_blaze_http_json_v1(
        "GET",
        "https://api.blaze.stream/v1/users/profile",
        None,
        {
            "authorization": f"Bearer {access_token}",
            "client-id": client_id,
            "accept": "application/json",
            "user-agent": "FoxBotAI/1.0"
        }
    )

    node = ((profile.get("body") or {}).get("data") or {}) if profile.get("ok") else {}
    blaze_id = node.get("userId")

    display_name = ""
    for key in ("username", "handle", "slug", "displayName", "display_name", "name"):
        value = node.get(key)
        if value:
            display_name = str(value).strip().lstrip("@")[:40]
            break

    if not blaze_id:
        return HTMLResponse(
            "<h1>FoxBot Dashboard Login Error</h1>"
            "<p>Could not read a Blaze user ID from your profile. Try again.</p>",
            status_code=502
        )

    # Always logged, regardless of approval -- this is the bootstrap path:
    # the very first approved user has no way to know their own Blaze
    # userId in advance, so it has to surface here before the allowlist
    # can ever contain it. Mirrors the existing BLAZE_BOT_USER_ID bootstrap
    # print in _foxbot_blaze_oauth_verify_identity_v1.
    print(f"[FoxBot Dashboard Login] Blaze account userId={blaze_id!r} name={display_name!r} attempted dashboard login.")

    if not _foxbot_dashboard_user_is_approved_v1(blaze_id):
        return HTMLResponse(
            f"<h1>Not yet approved</h1>"
            f"<p>Signed in as Blaze account <code>{blaze_id}</code>"
            f"{' (@' + display_name + ')' if display_name else ''}, "
            f"but this account is not on the dashboard allowlist yet.</p>"
            f"<p>To approve it, add this to Render's environment and restart:</p>"
            f"<textarea readonly style='width:100%;height:3em;'>STUDIO_APPROVED_BLAZE_USER_IDS={blaze_id}</textarea>"
            f"<p>If others are already approved, add a comma and this ID to the existing value "
            f"instead of replacing it.</p>",
            status_code=403
        )

    # Bot Connection Sub-phase D, stage 1: the join's write side. Only on
    # a successful (approved) login -- blaze_id here came exclusively
    # from Blaze's own /v1/users/profile response above, never from a
    # caller-supplied value. Best-effort: a failure here must not block
    # login (the session is the important part; the join can catch up
    # on a later login if this write has a transient problem).
    if display_name:
        try:
            _foxbot_connect_set_blaze_id_v1(display_name, blaze_id, display_name=display_name)
        except Exception as e:
            print(f"[FoxBot Dashboard Login] could not write blaze_id join for handle {display_name!r}: {e}")

    session_token = _foxbot_dashboard_session_sign_v1(blaze_id, display_name)

    response = RedirectResponse("/studio-v2")
    response.set_cookie(
        "foxbot_dashboard_session", session_token,
        max_age=_FOXBOT_DASHBOARD_SESSION_MAX_AGE,
        httponly=True, secure=True, samesite="lax"
    )
    # Clear the short-lived PKCE cookies now that login is complete.
    response.delete_cookie("foxbot_dashboard_oauth_state")
    response.delete_cookie("foxbot_dashboard_oauth_verifier")
    response.delete_cookie("foxbot_dashboard_oauth_redirect")

    return response


@app.get("/auth/dashboard/callback")
def foxbot_dashboard_callback_v1(request: Request, code: str = "", state: str = ""):
    return _foxbot_dashboard_oauth_callback_handle_v1(request, code, state)

# === End FoxBot Studio Dashboard Login ===



@app.get("/api/blaze/oauth/status")

def foxbot_blaze_oauth_status_v1(request: Request):
    guard = _foxbot_require_admin_v1(request)
    if guard:
        return guard

    import os

    import json

    from pathlib import Path



    token_path = _foxbot_storage_path_v1("blaze_oauth_tokens.json", "FOXBOT_OAUTH_TOKEN_FILE")

    saved = {}



    if token_path.exists():

        try:

            saved = json.loads(token_path.read_text(encoding="utf-8") or "{}")

        except Exception:

            saved = {}



    access = os.getenv("BLAZE_ACCESS_TOKEN") or saved.get("accessToken") or saved.get("access_token") or ""

    refresh = os.getenv("BLAZE_REFRESH_TOKEN") or saved.get("refreshToken") or saved.get("refresh_token") or ""



    return {

        "ok": True,

        "has_client_id": bool(os.getenv("BLAZE_CLIENT_ID")),

        "has_client_secret": bool(os.getenv("BLAZE_CLIENT_SECRET")),

        "redirect_uri": os.getenv("BLAZE_REDIRECT_URI", "https://foxbot-ai-chatbot.onrender.com/auth/blaze/callback"),

        "has_access_token": bool(access),

        "has_refresh_token": bool(refresh),

        "access_token_masked": _foxbot_blaze_oauth_mask_v1(access),

        "refresh_token_masked": _foxbot_blaze_oauth_mask_v1(refresh),

        "saved_token_file_exists": token_path.exists(),

    }





@app.post("/api/blaze/oauth/refresh")

def foxbot_blaze_oauth_refresh_v1(request: Request):
    guard = _foxbot_require_admin_v1(request)
    if guard:
        return guard

    import os

    import json

    from pathlib import Path



    client_id = os.getenv("BLAZE_CLIENT_ID", "").strip()

    client_secret = os.getenv("BLAZE_CLIENT_SECRET", "").strip()



    token_path = _foxbot_storage_path_v1("blaze_oauth_tokens.json", "FOXBOT_OAUTH_TOKEN_FILE")

    saved = {}



    if token_path.exists():

        try:

            saved = json.loads(token_path.read_text(encoding="utf-8") or "{}")

        except Exception:

            saved = {}



    refresh_token = saved.get("refreshToken") or saved.get("refresh_token") or os.getenv("BLAZE_REFRESH_TOKEN") or ""



    if not client_id or not client_secret or not refresh_token:

        return {

            "ok": False,

            "error": "Missing BLAZE_CLIENT_ID, BLAZE_CLIENT_SECRET, or BLAZE_REFRESH_TOKEN."

        }



    try:

        tokens = _foxbot_blaze_oauth_post_json_v1(

            "https://blaze.stream/bapi/oauth2/refresh",

            {

                "clientId": client_id,

                "clientSecret": client_secret,

                "refreshToken": refresh_token

            }

        )

    except Exception as e:

        return {

            "ok": False,

            "error": str(e)

        }



    try:

        saved = _foxbot_blaze_oauth_save_tokens_v1(tokens)

    except FoxBotBlazeIdentityMismatch as e:

        return {

            "ok": False,

            "error": str(e)

        }



    return {

        "ok": True,

        "has_access_token": bool(saved.get("accessToken") or saved.get("access_token")),

        "has_refresh_token": bool(saved.get("refreshToken") or saved.get("refresh_token")),

        "access_token_masked": _foxbot_blaze_oauth_mask_v1(saved.get("accessToken") or saved.get("access_token")),

        "refresh_token_masked": _foxbot_blaze_oauth_mask_v1(saved.get("refreshToken") or saved.get("refresh_token")),

    }

# === End FoxBot Blaze OAuth Routes v1 ===


# === FoxBot Bot Connection Routes v1 (Sub-phase E) ===
# Per-creator bot-connect OAuth: lets a SECOND creator register their own
# Blaze bot identity into a by_creator token slot, using the same posting
# scopes tenant-zero's own /auth/blaze/login already uses. Reuses proven
# pieces rather than reinventing them:
#   - _foxbot_blaze_exchange_code_v3     code -> tokens (Stage 3)
#   - _foxbot_blaze_oauth_save_tokens_v1 Sub-phase B.2's self-service
#     by_creator[actual_id] write, UNCHANGED -- still takes exactly one
#     argument, slot key still derived only from Blaze's own /v1/users/
#     profile response, never from anything this flow hands it (Stage 3)
#   - _foxbot_connect_set_blaze_id_v1    Sub-phase D's join write (Stage 3)
#
# Flag-gated behind FOXBOT_BOT_CONNECT_ENABLED (default OFF/unset). Both
# routes exist even while OFF -- on purpose, so the redirect URI can be
# registered in Blaze's console ahead of time -- but do nothing until the
# flag is on. Neither route sits on tenant-zero's own OAuth path
# (/auth/blaze/login + /auth/blaze/callback, above), so tenant-zero's
# flow is unaffected regardless of this flag's value.
#
# Stage 1 (this): flag + route scaffolding only. No Blaze HTTP calls, no
# cookies, no token writes yet -- those land in Stages 2-3.

def _foxbot_bot_connect_enabled_v1() -> bool:
    return os.getenv("FOXBOT_BOT_CONNECT_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")


def _foxbot_bot_connect_disabled_response_v1():
    return HTMLResponse(
        "<h1>Bot Connection Not Enabled</h1>"
        "<p>This feature is not turned on for this deployment yet.</p>",
        status_code=404
    )


@app.get("/auth/bot-connect/login")
def foxbot_bot_connect_login_v1():
    if not _foxbot_bot_connect_enabled_v1():
        return _foxbot_bot_connect_disabled_response_v1()

    client_id = os.getenv("BLAZE_CLIENT_ID", "").strip()
    client_secret = os.getenv("BLAZE_CLIENT_SECRET", "").strip()
    redirect_uri = os.getenv(
        "FOXBOT_BOT_CONNECT_REDIRECT_URI",
        "https://foxbot-ai-chatbot.onrender.com/auth/bot-connect/callback"
    ).strip()

    if not client_id or not client_secret:
        return HTMLResponse(
            "<h1>Bot Connect Missing Config</h1>"
            "<p>Add BLAZE_CLIENT_ID and BLAZE_CLIENT_SECRET in Render first.</p>",
            status_code=500
        )

    # Same posting scopes as tenant-zero's own /auth/blaze/login
    # (app.py:19423) -- a second creator's bot needs to be able to do
    # everything tenant-zero's bot can do on their own channel.
    scopes = ["users.read", "offline.access", "channel.moderate", "users.bot"]

    try:
        data = _foxbot_blaze_oauth_post_json_v1(
            "https://blaze.stream/bapi/oauth2/generate-auth-url",
            {
                "clientId": client_id,
                "clientSecret": client_secret,
                "redirectUri": redirect_uri,
                "scopes": scopes
            }
        )
    except Exception as e:
        return HTMLResponse(
            f"<h1>Bot Connect Error</h1><p>Could not generate auth URL.</p><pre>{e}</pre>",
            status_code=500
        )

    state = data.get("state")
    code_verifier = data.get("codeVerifier")
    url = data.get("url")

    if not state or not code_verifier or not url:
        return HTMLResponse(
            f"<h1>Bot Connect Error</h1><p>Blaze did not return state/codeVerifier/url.</p><pre>{data}</pre>",
            status_code=500
        )

    response = RedirectResponse(url)

    # Distinct cookie names from BOTH other flows -- foxbot_oauth_* (the
    # tenant-zero bot flow) and foxbot_dashboard_oauth_* (dashboard
    # login) -- so a browser mid-way through any one of the three never
    # cross-contaminates another. A creator with an active dashboard
    # session starting a bot-connect flow in the same browser is exactly
    # the case this guards against.
    try:
        response.set_cookie(
            "foxbot_botconnect_oauth_state", state,
            max_age=900, httponly=True, secure=True, samesite="lax"
        )
        response.set_cookie(
            "foxbot_botconnect_oauth_verifier", code_verifier,
            max_age=900, httponly=True, secure=True, samesite="lax"
        )
        response.set_cookie(
            "foxbot_botconnect_oauth_redirect", redirect_uri,
            max_age=900, httponly=True, secure=True, samesite="lax"
        )
    except Exception:
        pass

    return response


@app.get("/auth/bot-connect/callback")
def foxbot_bot_connect_callback_v1(request: Request, code: str = "", state: str = ""):
    if not _foxbot_bot_connect_enabled_v1():
        return _foxbot_bot_connect_disabled_response_v1()

    if not code:
        return HTMLResponse("<h1>Bot Connect Error</h1><p>No code received.</p>", status_code=400)

    # CSRF/state check, same cookie-comparison pattern as
    # foxbot_dashboard_callback_v1 (app.py:20144) -- rejects here, before
    # any exchange is attempted, if the state param wasn't issued by our
    # own /auth/bot-connect/login for this browser.
    cookie_state = request.cookies.get("foxbot_botconnect_oauth_state")
    cookie_verifier = request.cookies.get("foxbot_botconnect_oauth_verifier")
    cookie_redirect = request.cookies.get("foxbot_botconnect_oauth_redirect")

    if not cookie_state or cookie_state != state or not cookie_verifier:
        return HTMLResponse(
            "<h1>Bot Connect Error</h1>"
            "<p>Login state was not found or didn't match. Open /auth/bot-connect/login again "
            "and complete login in the same browser session.</p>",
            status_code=400
        )

    client_id = os.getenv("BLAZE_CLIENT_ID", "").strip()
    client_secret = os.getenv("BLAZE_CLIENT_SECRET", "").strip()
    redirect_uri = cookie_redirect or os.getenv(
        "FOXBOT_BOT_CONNECT_REDIRECT_URI",
        "https://foxbot-ai-chatbot.onrender.com/auth/bot-connect/callback"
    ).strip()

    try:
        tokens, _style = _foxbot_blaze_exchange_code_v3(
            client_id, client_secret, code, cookie_verifier, redirect_uri
        )
    except Exception as e:
        return HTMLResponse(
            f"<h1>Bot Connect Error</h1><p>Could not exchange code for token.</p><pre>{e}</pre>",
            status_code=500
        )

    access_token = (tokens or {}).get("accessToken") or (tokens or {}).get("access_token") or ""
    if not access_token:
        return HTMLResponse("<h1>Bot Connect Error</h1><p>No access token returned.</p>", status_code=500)

    # THE SACRED INVARIANT. actual_id is obtained by calling the exact
    # same function _foxbot_blaze_oauth_save_tokens_v1 calls internally
    # (_foxbot_blaze_oauth_verify_identity_v1, app.py:19157) on the exact
    # same tokens -- never parsed from `code`, `state`, a cookie, or any
    # other value this request carries. That function's own docstring is
    # explicit about this: "actual_id is the ONLY value any code may
    # ever use as a by_creator token-slot key... do not thread a
    # creator_id through from elsewhere." Getting it here the same way
    # the save primitive gets it internally is what guarantees this
    # join write below and that save's slot write can never disagree --
    # both are the same deterministic function of the same
    # Blaze-verified access token, not two independently-parsed values
    # that merely happen to usually match.
    try:
        actual_id = _foxbot_blaze_oauth_verify_identity_v1(tokens)
    except FoxBotBlazeIdentityMismatch as e:
        return HTMLResponse(f"<h1>Bot Connect Rejected</h1><p>{e}</p>", status_code=403)

    if not actual_id:
        return HTMLResponse(
            "<h1>Bot Connect Error</h1><p>Could not verify a Blaze account for this login.</p>",
            status_code=502
        )

    # B's primitive, called COMPLETELY UNCHANGED -- single argument, no
    # creator_id passed in from here. It re-derives actual_id internally
    # via its own call to _foxbot_blaze_oauth_verify_identity_v1 and
    # writes by_creator[that id] via Sub-phase B.2's self-service branch
    # (app.py:19301). This callback has no parameter, and B's primitive
    # has no parameter slot, through which anything request-supplied
    # could reach the write destination.
    try:
        saved = _foxbot_blaze_oauth_save_tokens_v1(tokens)
    except FoxBotBlazeIdentityMismatch as e:
        return HTMLResponse(f"<h1>Bot Connect Rejected</h1><p>{e}</p>", status_code=403)

    # Display name for the join's `handle` field only -- cosmetic, not
    # security-relevant (unlike actual_id above, nothing downstream uses
    # this as a token-slot key). Best-effort, mirrors the dashboard
    # login's own profile read (app.py:20189): a failure here skips the
    # join but must not undo the token save that already succeeded.
    display_name = ""
    try:
        profile = _foxbot_blaze_http_json_v1(
            "GET",
            "https://api.blaze.stream/v1/users/profile",
            None,
            {
                "authorization": f"Bearer {access_token}",
                "client-id": client_id,
                "accept": "application/json",
                "user-agent": "FoxBotAI/1.0"
            }
        )
        node = ((profile.get("body") or {}).get("data") or {}) if profile.get("ok") else {}
        for key in ("username", "handle", "slug", "displayName", "display_name", "name"):
            value = node.get(key)
            if value:
                display_name = str(value).strip().lstrip("@")[:40]
                break
    except Exception:
        pass

    # Sub-phase D's join, write side -- actual_id here is the SAME value
    # verified above and handed to the save primitive, never a
    # separately-parsed one, so the join and the token slot cannot
    # disagree on who this creator is.
    if display_name:
        try:
            _foxbot_connect_set_blaze_id_v1(display_name, actual_id, display_name=display_name)
        except Exception as e:
            print(f"[FoxBot Bot Connect] could not write blaze_id join for handle {display_name!r}: {e}")

    return HTMLResponse(
        f"<h1>Bot Connected</h1>"
        f"<p>Blaze account <code>{actual_id}</code>"
        f"{' (@' + display_name + ')' if display_name else ''} is now connected.</p>"
        f"<p>Access token: {bool(saved.get('accessToken') or saved.get('access_token'))}, "
        f"refresh token: {bool(saved.get('refreshToken') or saved.get('refresh_token'))}.</p>",
        status_code=200
    )


@app.post("/api/blaze/oauth/bot-connect/revoke")
def foxbot_bot_connect_revoke_v1(request: Request, creator_id: str = ""):
    """Bot Connection Sub-phase E, stage 4: B.2's structural inverse.

    ADMIN-gated, not self-service. This path (/api/blaze/oauth/...)
    already matches FOXBOT_ADMIN_GATED_PREFIXES (app.py:994), so the
    existing studio admin auth middleware (foxbot_studio_admin_auth_gate_v1,
    app.py:1035) already requires Basic Auth or an approved Blaze
    dashboard session before this function body ever runs. Bot Connection
    C2, Step 2 additionally requires that session be full admin (not a
    scoped, non-tenant-zero creator) -- see the _foxbot_require_admin_v1
    call below, same as every other /api/blaze/oauth/* route in this file.

    Deliberately NOT gated behind FOXBOT_BOT_CONNECT_ENABLED -- this is
    a standalone cleanup lever an admin may need even with the feature
    flag off (e.g. to purge a slot registered while the flag was
    briefly on).

    Unlike registration (Stage 3), where the slot key MUST come only
    from Blaze's own OAuth verification, revocation is an admin
    operation targeting an EXISTING slot the admin selects -- creator_id
    here is caller-supplied by design, the mirror image of Stage 3's
    invariant, not a violation of it. What stays true in both
    directions: this function only ever reads/writes
    by_creator[creator_id] -- it has no code path that touches the flat
    top-level token keys (tenant-zero's live credentials), regardless of
    what creator_id is passed.

    Refuses outright to touch tenant-zero's own id (checked against
    BOTH _tenant_zero_id() and the configured bot identity from
    _foxbot_blaze_bot_expected_id_v1(), since either could in principle
    be handed here) -- revoking the live bot's own connection isn't
    this lever's job and isn't allowed to be one accidental admin click.
    """
    guard = _foxbot_require_admin_v1(request)
    if guard:
        return guard

    import json

    creator_id = (creator_id or "").strip()

    if not creator_id:
        from fastapi.responses import JSONResponse
        return JSONResponse({"ok": False, "error": "creator_id is required."}, status_code=400)

    protected_ids = {x for x in (_tenant_zero_id(), _foxbot_blaze_bot_expected_id_v1()) if x}
    if creator_id in protected_ids:
        from fastapi.responses import JSONResponse
        return JSONResponse(
            {
                "ok": False,
                "error": "Refusing to revoke tenant-zero's own bot connection through this endpoint.",
            },
            status_code=400
        )

    # Token slot removal. ONLY by_creator[creator_id] is ever read or
    # written below -- the flat top-level keys are never touched by this
    # function, by construction, no matter what creator_id was passed.
    token_path = _foxbot_storage_path_v1("blaze_oauth_tokens.json", "FOXBOT_OAUTH_TOKEN_FILE")
    removed_token_slot = False

    if token_path.exists():
        try:
            existing = json.loads(token_path.read_text(encoding="utf-8") or "{}")
        except Exception:
            existing = {}

        by_creator = existing.get("by_creator") or {}
        if creator_id in by_creator:
            del by_creator[creator_id]
            existing["by_creator"] = by_creator
            token_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
            removed_token_slot = True

    # Join removal -- symmetric with Stage 3's join write, so no
    # connected_creators.json record is left pointing at a blaze_id that
    # no longer has a token slot.
    removed_join_entries = _foxbot_connect_clear_blaze_id_v1(creator_id)

    return {
        "ok": True,
        "creator_id": creator_id,
        "removed_token_slot": removed_token_slot,
        "removed_join_entries": removed_join_entries,
    }

# === End FoxBot Bot Connection Routes v1 (Sub-phase E) ===


# === FoxBot Blaze OAuth Scheduled Refresh v1 ===
# Calls the existing, identity-locked foxbot_blaze_oauth_refresh_v1() on a
# timer so the bot's Blaze access token gets renewed automatically instead
# of expiring and needing a manual /auth/blaze/login redo. Deliberately does
# not touch foxbot_blaze_oauth_refresh_v1, _foxbot_blaze_oauth_save_tokens_v1,
# or _foxbot_blaze_oauth_verify_identity_v1 -- this only calls that existing
# logic on a schedule.
#
# Bot Connection Sub-phase C: the loop also refreshes every OTHER creator
# in by_creator (via _foxbot_blaze_oauth_refresh_creator_v1), independently,
# with its own per-creator status. Tenant-zero's own refresh mechanism
# above is completely unchanged by this -- the per-creator loop explicitly
# excludes tenant-zero's id (see _foxbot_blaze_oauth_refresh_worker_v1) so
# it is never refreshed twice. Dormant in practice until Sub-phase E ships
# a route that can add a second creator to by_creator.
blaze_oauth_refresh_status = {
    "running": False,
    "cycles": 0,
    "last_attempt_at": None,
    "last_ok": None,
    "last_error": None,
    "per_creator": {},
}


def _foxbot_blaze_oauth_log_raw_fields_v1():
    """Read back the just-saved token file and print whatever fields Blaze's
    response actually contained (masking only the token values themselves),
    so the real expiresIn/tokenType/etc. show up once in the Render log
    stream instead of staying unknown forever. Read-only -- writes nothing."""

    import json

    token_path = _foxbot_storage_path_v1("blaze_oauth_tokens.json", "FOXBOT_OAUTH_TOKEN_FILE")

    if not token_path.exists():
        return

    try:
        saved = json.loads(token_path.read_text(encoding="utf-8") or "{}")
    except Exception as e:
        print(f"[FoxBot OAuth Refresh] could not read back token file for logging: {e}")
        return

    masked = dict(saved)
    for key in ("accessToken", "access_token", "refreshToken", "refresh_token"):
        if key in masked:
            masked[key] = _foxbot_blaze_oauth_mask_v1(masked.get(key))

    print(f"[FoxBot OAuth Refresh] saved token fields (masked tokens only): {masked}")


def _foxbot_blaze_oauth_refresh_creator_v1(creator_id, creator_slot):
    """Bot Connection Sub-phase C, stage 1: refresh ONE creator's tokens
    using their OWN refresh token from their by_creator slot -- never the
    flat keys, and never the BLAZE_REFRESH_TOKEN env var fallback (that's
    tenant-zero's bootstrap-only fallback; a per-creator slot must never
    fall back to a shared/global credential belonging to a different
    identity).

    Reuses _foxbot_blaze_oauth_save_tokens_v1 unchanged: it already
    derives the verified identity fresh from Blaze on every save and
    routes the result correctly (flat keys if that identity is
    tenant-zero, by_creator[that identity] otherwise) -- Sub-phase B's
    gate already does the routing this function would otherwise have to
    duplicate.

    creator_id/creator_slot here are READ inputs only -- which slot's
    refresh token to send to Blaze, and a label for the caller's status
    tracking. They do not determine where the result gets WRITTEN: that
    write destination is still derived exclusively inside
    _foxbot_blaze_oauth_save_tokens_v1/_foxbot_blaze_oauth_verify_identity_v1
    from Blaze's own fresh /v1/users/profile response, per the Sub-phase B
    invariant documented there. Even a wrong/stale creator_id passed in
    here can't misdirect the write -- the save path re-verifies for
    itself and only ever writes to whatever identity Blaze just proved.

    Returns {"ok": bool, "error": str} on any expected failure (missing
    config, missing/expired refresh token, Blaze API error, identity
    mismatch) -- same return shape as the existing
    foxbot_blaze_oauth_refresh_v1(), so the per-creator refresh loop
    (Sub-phase C stage 2) can record it without its own error parsing.
    Genuinely unexpected exceptions propagate to the caller, which
    isolates them per creator.
    """
    client_id = os.getenv("BLAZE_CLIENT_ID", "").strip()
    client_secret = os.getenv("BLAZE_CLIENT_SECRET", "").strip()

    refresh_token = (
        (creator_slot or {}).get("refreshToken")
        or (creator_slot or {}).get("refresh_token")
        or ""
    )

    if not client_id or not client_secret or not refresh_token:
        return {
            "ok": False,
            "error": f"Missing BLAZE_CLIENT_ID, BLAZE_CLIENT_SECRET, or {creator_id!r}'s own refreshToken.",
        }

    try:
        tokens = _foxbot_blaze_oauth_post_json_v1(
            "https://blaze.stream/bapi/oauth2/refresh",
            {
                "clientId": client_id,
                "clientSecret": client_secret,
                "refreshToken": refresh_token,
            },
        )
    except Exception as e:
        return {"ok": False, "error": str(e)}

    try:
        saved = _foxbot_blaze_oauth_save_tokens_v1(tokens)
    except FoxBotBlazeIdentityMismatch as e:
        return {"ok": False, "error": str(e)}

    return {
        "ok": True,
        "has_access_token": bool(saved.get("accessToken") or saved.get("access_token")),
        "has_refresh_token": bool(saved.get("refreshToken") or saved.get("refresh_token")),
    }


def _foxbot_blaze_oauth_refresh_creator_status_v1(creator_id):
    """The per_creator status entry for one creator, creating it with the
    same shape as the top-level fields on first touch."""
    return blaze_oauth_refresh_status["per_creator"].setdefault(
        creator_id,
        {"cycles": 0, "last_attempt_at": None, "last_ok": None, "last_error": None},
    )


def _foxbot_blaze_oauth_refresh_worker_v1():
    blaze_oauth_refresh_status["running"] = True

    try:
        interval = float(os.getenv("FOXBOT_BLAZE_TOKEN_REFRESH_INTERVAL_SECONDS", "3600") or "3600")
    except (TypeError, ValueError):
        interval = 3600.0
    interval = max(60.0, interval)

    while blaze_oauth_refresh_status["running"]:
        blaze_oauth_refresh_status["last_attempt_at"] = time.time()
        blaze_oauth_refresh_status["cycles"] += 1

        # --- Tenant-zero: UNCHANGED flat-path refresh. Same call, same
        # try/except shape as before Sub-phase C -- zero new code in this
        # block. Also mirrored into per_creator[tz_id] so the status
        # endpoint gives one consistent view of every creator, even
        # though tenant-zero's actual refresh mechanism stays untouched.
        tz_id = _tenant_zero_id()
        tz_status = _foxbot_blaze_oauth_refresh_creator_status_v1(tz_id)
        tz_status["last_attempt_at"] = time.time()
        tz_status["cycles"] += 1

        try:
            result = foxbot_blaze_oauth_refresh_v1()
            ok = bool(result.get("ok"))
            blaze_oauth_refresh_status["last_ok"] = ok
            tz_status["last_ok"] = ok

            if ok:
                blaze_oauth_refresh_status["last_error"] = None
                tz_status["last_error"] = None
                print("[FoxBot OAuth Refresh] scheduled refresh succeeded.")
                _foxbot_blaze_oauth_log_raw_fields_v1()
            else:
                blaze_oauth_refresh_status["last_error"] = result.get("error")
                tz_status["last_error"] = result.get("error")
                print(f"[FoxBot OAuth Refresh] scheduled refresh did not run: {result.get('error')}")
        except Exception as e:
            blaze_oauth_refresh_status["last_ok"] = False
            blaze_oauth_refresh_status["last_error"] = str(e)
            tz_status["last_ok"] = False
            tz_status["last_error"] = str(e)
            print(f"[FoxBot OAuth Refresh] scheduled refresh crashed: {e}")

        # --- Every OTHER creator in by_creator: Bot Connection Sub-phase
        # C. Explicitly skips tz_id -- keyed on _tenant_zero_id(), the
        # same function that writes by_creator[tz_id] (Sub-phase A's
        # mirror sync), so the exclusion always matches what's actually
        # stored there regardless of whether BLAZE_BOT_USER_ID/
        # FOXBOT_BLAZE_USER_ID (the identity-lock's own env vars) ever
        # differs from FOXBOT_TENANT_ZERO_CREATOR_ID. Tenant-zero is
        # never refreshed here -- it was already handled above via the
        # unchanged flat path. Dormant today: by_creator only ever
        # contains tz_id until Sub-phase E ships a route that can add
        # another creator.
        import json

        try:
            token_path = _foxbot_storage_path_v1("blaze_oauth_tokens.json", "FOXBOT_OAUTH_TOKEN_FILE")
            existing = json.loads(token_path.read_text(encoding="utf-8") or "{}") if token_path.exists() else {}
        except Exception as e:
            existing = {}
            print(f"[FoxBot OAuth Refresh] could not read token file for per-creator refresh: {e}")

        by_creator = existing.get("by_creator") if isinstance(existing, dict) else None

        for creator_id, creator_slot in (by_creator or {}).items():
            if creator_id == tz_id:
                continue

            creator_status = _foxbot_blaze_oauth_refresh_creator_status_v1(creator_id)
            creator_status["last_attempt_at"] = time.time()
            creator_status["cycles"] += 1

            try:
                result = _foxbot_blaze_oauth_refresh_creator_v1(creator_id, creator_slot)
                creator_status["last_ok"] = bool(result.get("ok"))
                creator_status["last_error"] = None if result.get("ok") else result.get("error")
                if result.get("ok"):
                    print(f"[FoxBot OAuth Refresh] scheduled refresh succeeded for creator {creator_id!r}.")
                else:
                    print(f"[FoxBot OAuth Refresh] scheduled refresh did not run for creator {creator_id!r}: {result.get('error')}")
            except Exception as e:
                # Isolation: caught here, per creator_id -- one creator's
                # exception must never stop the loop from reaching the
                # next creator, and is only ever recorded against this
                # creator's own status entry.
                creator_status["last_ok"] = False
                creator_status["last_error"] = str(e)
                print(f"[FoxBot OAuth Refresh] scheduled refresh crashed for creator {creator_id!r}: {e}")

        time.sleep(interval)

    blaze_oauth_refresh_status["running"] = False


def _foxbot_blaze_oauth_startup_sync_tenant_zero_slot_v1():
    """One-time startup sync so by_creator[tenant-zero] is populated right
    after deploy instead of waiting for the next login/refresh event.
    Bot Connection Sub-phase A, storage-shape only -- doesn't start or
    change anything else; a no-op if no tokens are saved yet."""
    import json

    path = _foxbot_storage_path_v1("blaze_oauth_tokens.json", "FOXBOT_OAUTH_TOKEN_FILE")
    if not path.exists():
        return

    try:
        existing = json.loads(path.read_text(encoding="utf-8") or "{}")
    except Exception:
        return

    if not isinstance(existing, dict):
        return

    if not (existing.get("accessToken") or existing.get("access_token")):
        return

    _foxbot_blaze_oauth_sync_tenant_zero_slot_v1(existing, _tenant_zero_id())
    path.write_text(json.dumps(existing, indent=2), encoding="utf-8")


@app.on_event("startup")
def foxbot_auto_start_oauth_refresh_v1():
    """Start the scheduled Blaze OAuth refresh loop automatically on boot.
    Set FOXBOT_AUTO_REFRESH_BLAZE_TOKEN=false to opt out."""
    global _blaze_oauth_refresh_thread

    _foxbot_blaze_oauth_startup_sync_tenant_zero_slot_v1()

    opt_out = (os.getenv("FOXBOT_AUTO_REFRESH_BLAZE_TOKEN", "true") or "").strip().lower()
    if opt_out in ["0", "false", "no", "off"]:
        return

    if not os.getenv("BLAZE_CLIENT_ID") or not os.getenv("BLAZE_CLIENT_SECRET"):
        return

    if _blaze_oauth_refresh_thread and _blaze_oauth_refresh_thread.is_alive():
        return

    _blaze_oauth_refresh_thread = threading.Thread(
        target=_foxbot_blaze_oauth_refresh_worker_v1, daemon=True
    )
    _blaze_oauth_refresh_thread.start()


@app.get("/api/blaze/oauth/refresh-status")
def foxbot_blaze_oauth_refresh_status_v1(request: Request):
    """Read-only view of the scheduled refresh loop's own in-process state
    (cycles/last_attempt_at/last_ok/last_error) -- gated by the existing
    /api/blaze/oauth/ Basic Auth prefix, same as /api/blaze/oauth/status.
    Does not touch the worker, the refresh endpoint, or the identity lock;
    it only reads blaze_oauth_refresh_status."""
    guard = _foxbot_require_admin_v1(request)
    if guard:
        return guard

    return {
        "ok": True,
        "thread_alive": bool(_blaze_oauth_refresh_thread and _blaze_oauth_refresh_thread.is_alive()),
        "refresh_status": blaze_oauth_refresh_status,
    }
# === End FoxBot Blaze OAuth Scheduled Refresh v1 ===


# === FoxBot Blaze OAuth Debug Routes v1 ===

def _foxbot_blaze_oauth_generate_auth_debug_v1(scopes, redirect_uri=None):

    import json

    import os

    import urllib.error

    import urllib.request



    client_id = os.getenv("BLAZE_CLIENT_ID", "").strip()

    client_secret = os.getenv("BLAZE_CLIENT_SECRET", "").strip()

    if redirect_uri is None:
        redirect_uri = os.getenv(
            "BLAZE_REDIRECT_URI",
            "https://foxbot-ai-chatbot.onrender.com/auth/blaze/callback"
        ).strip()



    payload = {

        "clientId": client_id,

        "clientSecret": client_secret,

        "redirectUri": redirect_uri,

        "scopes": scopes,

    }



    safe_payload = dict(payload)

    if safe_payload.get("clientSecret"):

        safe_payload["clientSecret"] = safe_payload["clientSecret"][:4] + "...MASKED"



    data = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(

        "https://blaze.stream/bapi/oauth2/generate-auth-url",

        data=data,

        headers={

            "content-type": "application/json",

            "accept": "application/json",

            "origin": "https://blaze.stream",

            "user-agent": "FoxBotAI/1.0",

        },

        method="POST",

    )



    try:

        with urllib.request.urlopen(req, timeout=15) as res:

            raw = res.read().decode("utf-8", errors="replace")

            try:

                body = json.loads(raw or "{}")

            except Exception:

                body = {"raw": raw}



            return {

                "ok": True,

                "status": res.status,

                "body": body,

                "safe_payload": safe_payload,

            }

    except urllib.error.HTTPError as e:

        details = ""

        try:

            details = e.read().decode("utf-8", errors="replace")

        except Exception:

            pass



        return {

            "ok": False,

            "status": e.code,

            "reason": e.reason,

            "details": details,

            "safe_payload": safe_payload,

        }

    except Exception as e:

        return {

            "ok": False,

            "error": str(e),

            "safe_payload": safe_payload,

        }





@app.get("/api/blaze/oauth/debug")

def foxbot_blaze_oauth_debug_v1(request: Request):
    guard = _foxbot_require_admin_v1(request)
    if guard:
        return guard

    full = _foxbot_blaze_oauth_generate_auth_debug_v1([

        "users.read",

        "offline.access",

        "channel.moderate",

        "users.bot"

    ])



    basic = _foxbot_blaze_oauth_generate_auth_debug_v1([

        "users.read",

        "offline.access"

    ])



    return {

        "ok": True,

        "full_scope_test": full,

        "basic_scope_test": basic,

        "what_to_check": [

            "BLAZE_CLIENT_ID must match the FoxBot AI app in Blaze Developers.",

            "BLAZE_CLIENT_SECRET must match the same app.",

            "BLAZE_REDIRECT_URI must exactly match https://foxbot-ai-chatbot.onrender.com/auth/blaze/callback.",

            "If basic works but full fails, Blaze is rejecting channel.moderate or users.bot scope."

        ]

    }


@app.get("/api/blaze/oauth/bot-connect/debug")
def foxbot_blaze_oauth_bot_connect_debug_v1(request: Request):
    """Mirrors foxbot_blaze_oauth_debug_v1 above, but exercises the
    /auth/bot-connect/login redirect_uri instead of the tenant-zero
    /auth/blaze/callback one -- so the two can be diffed directly. Reads
    FOXBOT_BOT_CONNECT_REDIRECT_URI the exact same way
    foxbot_bot_connect_login_v1 (app.py:20522) does, so if that env var
    is set to something unexpected in Render, this shows the same value
    the real login route would actually send to Blaze.
    """
    guard = _foxbot_require_admin_v1(request)
    if guard:
        return guard

    bot_connect_redirect_uri = os.getenv(
        "FOXBOT_BOT_CONNECT_REDIRECT_URI",
        "https://foxbot-ai-chatbot.onrender.com/auth/bot-connect/callback"
    ).strip()

    full = _foxbot_blaze_oauth_generate_auth_debug_v1(
        ["users.read", "offline.access", "channel.moderate", "users.bot"],
        redirect_uri=bot_connect_redirect_uri
    )

    basic = _foxbot_blaze_oauth_generate_auth_debug_v1(
        ["users.read", "offline.access"],
        redirect_uri=bot_connect_redirect_uri
    )

    return {
        "ok": True,
        "redirect_uri_used": bot_connect_redirect_uri,
        "full_scope_test": full,
        "basic_scope_test": basic,
        "what_to_check": [
            "redirect_uri_used above is EXACTLY what /auth/bot-connect/login sends -- "
            "compare it byte-for-byte against what /api/blaze/oauth/debug uses for the "
            "working blaze flow, and against what's registered in Blaze Developers.",
            "If FOXBOT_BOT_CONNECT_REDIRECT_URI is unset in Render, this falls back to "
            "https://foxbot-ai-chatbot.onrender.com/auth/bot-connect/callback -- that exact "
            "URI must be registered as an additional redirect URI on the FoxBot AI app in Blaze.",
            "If basic_scope_test succeeds but full_scope_test fails, Blaze is rejecting "
            "channel.moderate or users.bot for this redirect_uri specifically.",
            "If both fail here but /api/blaze/oauth/debug succeeds, the bot-connect "
            "redirect_uri itself is the problem (not registered, or a mismatch)."
        ]
    }


@app.get("/api/blaze/oauth/dashboard/debug")
def foxbot_blaze_oauth_dashboard_debug_v1(request: Request):
    """Mirrors foxbot_blaze_oauth_bot_connect_debug_v1 above, but exercises the
    /auth/dashboard/login redirect_uri -- so all three (tenant-zero, dashboard,
    bot-connect) can be diffed side by side. Reads STUDIO_DASHBOARD_REDIRECT_URI
    the exact same way foxbot_dashboard_login_v1 (app.py:20109) does, and
    requests only users.read since that's the only scope the real dashboard
    login route ever sends.
    """
    guard = _foxbot_require_admin_v1(request)
    if guard:
        return guard

    dashboard_redirect_uri = os.getenv(
        "STUDIO_DASHBOARD_REDIRECT_URI",
        "https://foxbot-ai-chatbot.onrender.com/auth/dashboard/callback"
    ).strip()

    read_scope_test = _foxbot_blaze_oauth_generate_auth_debug_v1(
        ["users.read"],
        redirect_uri=dashboard_redirect_uri
    )

    return {
        "ok": True,
        "redirect_uri_used": dashboard_redirect_uri,
        "read_scope_test": read_scope_test,
        "what_to_check": [
            "redirect_uri_used above is EXACTLY what /auth/dashboard/login sends -- "
            "compare it byte-for-byte against /api/blaze/oauth/debug (tenant-zero, working) "
            "and /api/blaze/oauth/bot-connect/debug, and against what's registered in Blaze Developers.",
            "If STUDIO_DASHBOARD_REDIRECT_URI is unset in Render, this falls back to "
            "https://foxbot-ai-chatbot.onrender.com/auth/dashboard/callback -- that exact "
            "URI must be registered as an additional redirect URI on the FoxBot AI app in Blaze.",
            "If this fails the same way bot-connect/debug does, while /api/blaze/oauth/debug "
            "(the original, single registered URI) succeeds, that points at Blaze not "
            "actually persisting/enforcing any redirect URI beyond the first one saved on "
            "this client ID -- not a typo in this codebase."
        ]
    }

# === End FoxBot Blaze OAuth Debug Routes v1 ===



# === FoxBot Blaze Native Diagnostics Route v1 ===

@app.get("/api/blaze/native/diagnostics")

def foxbot_blaze_native_diagnostics_route_v1(request: Request):
    guard = _foxbot_require_admin_v1(request)
    if guard:
        return guard

    from services import blaze_native_connector as native

    legacy_listener = {
        "running": polling_status.get("running", False),
        "messages_seen": polling_status.get("messages_seen", proof_stats.get("messages_seen", 0)),
        "commands_processed": polling_status.get("commands_processed", proof_stats.get("commands_processed", 0)),
        "last_command": proof_stats.get("last_command"),
        "last_reply": proof_stats.get("last_reply"),
        "last_username": proof_stats.get("last_username"),
        "last_reply_at": proof_stats.get("last_reply_at"),
    }

    if hasattr(native, "blaze_native_diagnostics_v1"):

        result = native.blaze_native_diagnostics_v1()

        result["legacy_listener"] = legacy_listener

        return result



    return {

        "ok": False,

        "error": "blaze_native_diagnostics_v1 is not installed"

    }

# === End FoxBot Blaze Native Diagnostics Route v1 ===



# === FoxBot OAuth Clean State v2 ===

def _foxbot_oauth_pending_file_v2():

    from pathlib import Path

    path = Path("data") / "blaze_oauth_pending.json"

    path.parent.mkdir(parents=True, exist_ok=True)

    return path





def _foxbot_oauth_read_pending_v2():

    import json

    path = _foxbot_oauth_pending_file_v2()

    if not path.exists():

        return {}

    try:

        return json.loads(path.read_text(encoding="utf-8") or "{}")

    except Exception:

        return {}





def _foxbot_oauth_write_pending_v2(data):

    import json

    path = _foxbot_oauth_pending_file_v2()

    path.write_text(json.dumps(data or {}, indent=2), encoding="utf-8")





def _foxbot_oauth_prune_pending_v2(data):

    import time

    now = time.time()

    clean = {}

    for key, value in (data or {}).items():

        try:

            age = now - float(value.get("created_at", 0))

            if age <= 900:

                clean[key] = value

        except Exception:

            pass

    return clean





def _foxbot_oauth_set_pending_v2(state, payload):

    data = _foxbot_oauth_prune_pending_v2(_foxbot_oauth_read_pending_v2())

    data[state] = payload

    _foxbot_oauth_write_pending_v2(data)

    try:

        _FOXBOT_BLAZE_OAUTH_PENDING[state] = payload

    except Exception:

        pass





def _foxbot_oauth_get_pending_v2(state):

    if not state:

        return None



    try:

        found = _FOXBOT_BLAZE_OAUTH_PENDING.get(state)

        if found:

            return found

    except Exception:

        pass



    return _foxbot_oauth_read_pending_v2().get(state)





def _foxbot_oauth_pop_pending_v2(state):

    try:

        _foxbot_oauth_pop_pending_v2(state)

    except Exception:

        pass



    data = _foxbot_oauth_read_pending_v2()

    if state in data:

        data.pop(state, None)

        _foxbot_oauth_write_pending_v2(data)





@app.post("/api/blaze/oauth/reset")

def foxbot_blaze_oauth_reset_v2(request: Request):
    guard = _foxbot_require_admin_v1(request)
    if guard:
        return guard

    try:

        _FOXBOT_BLAZE_OAUTH_PENDING.clear()

    except Exception:

        pass



    _foxbot_oauth_write_pending_v2({})



    return {

        "ok": True,

        "message": "OAuth pending login state cleared. Open /auth/blaze/login next."

    }


# === End FoxBot OAuth Clean State v2 ===



# === FoxBot OAuth Exchange Fix v3 ===

def _foxbot_blaze_oauth_post_form_v3(url, payload, timeout=15):

    import json

    import urllib.error

    import urllib.parse

    import urllib.request



    data = urllib.parse.urlencode(payload).encode("utf-8")

    req = urllib.request.Request(

        url,

        data=data,

        headers={

            "content-type": "application/x-www-form-urlencoded",

            "accept": "application/json",

            "origin": "https://blaze.stream",

            "user-agent": "FoxBotAI/1.0"

        },

        method="POST"

    )



    try:

        with urllib.request.urlopen(req, timeout=timeout) as res:

            raw = res.read().decode("utf-8", errors="replace")

            return json.loads(raw or "{}")

    except urllib.error.HTTPError as e:

        details = ""

        try:

            details = e.read().decode("utf-8", errors="replace")

        except Exception:

            pass

        raise RuntimeError(f"FORM exchange HTTP {e.code} {e.reason}. Response body: {details}")





def _foxbot_blaze_exchange_code_v3(client_id, client_secret, code, code_verifier, redirect_uri):

    attempts = []



    # Attempt 1: standard PKCE form style

    try:

        tokens = _foxbot_blaze_oauth_post_form_v3(

            "https://blaze.stream/bapi/oauth2/token",

            {

                "client_id": client_id,

                "client_secret": client_secret,

                "grant_type": "authorization_code",

                "code": code,

                "redirect_uri": redirect_uri,

                "code_verifier": code_verifier

            }

        )

        return tokens, "form_snake_pkce"

    except Exception as e:

        attempts.append({"style": "form_snake_pkce", "error": str(e)})



    # Attempt 2: Blaze camelCase JSON, no extra grant fields

    try:

        tokens = _foxbot_blaze_oauth_post_json_v1(

            "https://blaze.stream/bapi/oauth2/token",

            {

                "clientId": client_id,

                "clientSecret": client_secret,

                "code": code,

                "codeVerifier": code_verifier,

                "redirectUri": redirect_uri

            }

        )

        return tokens, "json_camel_minimal"

    except Exception as e:

        attempts.append({"style": "json_camel_minimal", "error": str(e)})



    # Attempt 3: original Blaze camelCase JSON

    try:

        tokens = _foxbot_blaze_oauth_post_json_v1(

            "https://blaze.stream/bapi/oauth2/token",

            {

                "clientId": client_id,

                "clientSecret": client_secret,

                "code": code,

                "codeVerifier": code_verifier,

                "redirectUri": redirect_uri,

                "grantType": "authorization_code"

            }

        )

        return tokens, "json_camel_grant"

    except Exception as e:

        attempts.append({"style": "json_camel_grant", "error": str(e)})



    raise RuntimeError("All token exchange styles failed: " + str(attempts))

# === End FoxBot OAuth Exchange Fix v3 ===

# === FoxBot Blaze Live Safety Test Route v1 ===
@app.get("/api/blaze/native/safety-test")
def foxbot_blaze_native_safety_test_route_v1(
    request: Request,
    username: str = "viewer",
    message: str = "!connect"
):
    guard = _foxbot_require_admin_v1(request)
    if guard:
        return guard

    from services import blaze_native_connector as native

    fake_event = {
        "metadata": {
            "messageType": "notification",
            "subscriptionType": "channel.chat.message"
        },
        "payload": {
            "channelId": os.getenv("BLAZE_CHANNEL_ID", ""),
            "sender": {
                "username": username,
                "displayName": username
            },
            "messageId": "safety-test",
            "message": message
        }
    }

    if hasattr(native, "_foxbot_safe_live_reply_preview_v1"):
        return native._foxbot_safe_live_reply_preview_v1(fake_event)

    return {
        "ok": False,
        "error": "safety helper not installed"
    }
# === End FoxBot Blaze Live Safety Test Route v1 ===

# === FoxBot Blaze App Token Send Test v1 ===
def _foxbot_blaze_http_json_v1(method, url, payload=None, headers=None, timeout=15):
    import json
    import urllib.error
    import urllib.request

    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=data,
        headers=headers or {},
        method=method
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            raw = res.read().decode("utf-8", errors="replace")
            try:
                body = json.loads(raw or "{}")
            except Exception:
                body = {"raw": raw}
            return {"ok": True, "status": res.status, "body": body}
    except urllib.error.HTTPError as e:
        raw = ""
        try:
            raw = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        try:
            body = json.loads(raw or "{}")
        except Exception:
            body = {"raw": raw}
        return {
            "ok": False,
            "status": e.code,
            "reason": e.reason,
            "body": body
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _foxbot_mask_token_v1(value):
    value = str(value or "")
    if len(value) <= 12:
        return "***" if value else ""
    return value[:6] + "..." + value[-6:]


def _foxbot_blaze_app_token_v1():
    import os

    client_id = os.getenv("BLAZE_CLIENT_ID", "").strip()
    client_secret = os.getenv("BLAZE_CLIENT_SECRET", "").strip()

    if not client_id or not client_secret:
        return {"ok": False, "error": "Missing BLAZE_CLIENT_ID or BLAZE_CLIENT_SECRET"}

    res = _foxbot_blaze_http_json_v1(
        "POST",
        "https://blaze.stream/bapi/oauth2/token",
        {
            "clientId": client_id,
            "clientSecret": client_secret,
            "grantType": "client_credentials"
        },
        {
            "content-type": "application/json",
            "accept": "application/json",
            "origin": "https://blaze.stream",
            "user-agent": "FoxBotAI/1.0"
        }
    )

    body = res.get("body") or {}
    token = body.get("accessToken") or body.get("access_token") or ""

    return {
        "ok": bool(res.get("ok") and token),
        "status": res.get("status"),
        "reason": res.get("reason"),
        "error": res.get("error"),
        "body": body if not token else {"success": body.get("success"), "has_accessToken": True},
        "access_token": token,
        "access_token_masked": _foxbot_mask_token_v1(token)
    }


def _foxbot_blaze_profile_user_id_v1():
    import os

    env_user_id = (
        os.getenv("BLAZE_BOT_USER_ID", "")
        or os.getenv("FOXBOT_BLAZE_USER_ID", "")
        or ""
    ).strip()

    if env_user_id:
        return {"ok": True, "user_id": env_user_id, "source": "env"}

    token = os.getenv("BLAZE_ACCESS_TOKEN", "").strip()
    client_id = os.getenv("BLAZE_CLIENT_ID", "").strip()

    if not token or not client_id:
        return {"ok": False, "error": "Missing BLAZE_ACCESS_TOKEN or BLAZE_CLIENT_ID"}

    res = _foxbot_blaze_http_json_v1(
        "GET",
        "https://api.blaze.stream/v1/users/profile",
        None,
        {
            "authorization": f"Bearer {token}",
            "client-id": client_id,
            "accept": "application/json",
            "user-agent": "FoxBotAI/1.0"
        }
    )

    try:
        user_id = ((res.get("body") or {}).get("data") or {}).get("userId") or ""
    except Exception:
        user_id = ""

    return {
        "ok": bool(res.get("ok") and user_id),
        "status": res.get("status"),
        "reason": res.get("reason"),
        "body": res.get("body"),
        "user_id": user_id,
        "source": "profile"
    }


def _foxbot_blaze_send_app_token_v1(message, channel_id=None):
    import os

    channel_id = (channel_id or os.getenv("BLAZE_CHANNEL_ID", "")).strip()
    client_id = os.getenv("BLAZE_CLIENT_ID", "").strip()

    if not channel_id:
        return {"ok": False, "sent": False, "error": "Missing BLAZE_CHANNEL_ID"}

    if not client_id:
        return {"ok": False, "sent": False, "error": "Missing BLAZE_CLIENT_ID"}

    message = str(message or "").strip()
    if not message:
        return {"ok": False, "sent": False, "error": "Empty message"}

    app = _foxbot_blaze_app_token_v1()
    profile = _foxbot_blaze_profile_user_id_v1()

    results = []

    if app.get("ok") and profile.get("ok"):
        app_token = app.get("access_token")
        sender_id = profile.get("user_id")

        res = _foxbot_blaze_http_json_v1(
            "POST",
            "https://api.blaze.stream/v1/chats/messages",
            {
                "channelId": channel_id,
                "message": message,
                "senderId": sender_id
            },
            {
                "authorization": f"Bearer {app_token}",
                "client-id": client_id,
                "content-type": "application/json",
                "accept": "application/json",
                "origin": "https://blaze.stream",
                "user-agent": "FoxBotAI/1.0"
            }
        )

        results.append({
            "mode": "app_token_sender_id",
            "ok": bool(res.get("ok")),
            "status": res.get("status"),
            "reason": res.get("reason"),
            "body": res.get("body"),
            "sender_id": sender_id,
        })

        if res.get("ok"):
            return {
                "ok": True,
                "sent": True,
                "channel_id": channel_id,
                "mode": "app_token_sender_id",
                "results": results
            }
    else:
        results.append({
            "mode": "app_token_sender_id",
            "ok": False,
            "app_token_ok": app.get("ok"),
            "profile_ok": profile.get("ok"),
            "app_status": app.get("status"),
            "profile_status": profile.get("status"),
            "app_body": app.get("body"),
            "profile_body": profile.get("body"),
        })

    # Fallback: current user token mode, but with full response body.
    user_token = os.getenv("BLAZE_ACCESS_TOKEN", "").strip()
    if user_token:
        res = _foxbot_blaze_http_json_v1(
            "POST",
            "https://api.blaze.stream/v1/chats/messages",
            {
                "channelId": channel_id,
                "message": message
            },
            {
                "authorization": f"Bearer {user_token}",
                "client-id": client_id,
                "content-type": "application/json",
                "accept": "application/json",
                "origin": "https://blaze.stream",
                "user-agent": "FoxBotAI/1.0"
            }
        )

        results.append({
            "mode": "user_token",
            "ok": bool(res.get("ok")),
            "status": res.get("status"),
            "reason": res.get("reason"),
            "body": res.get("body"),
        })

        if res.get("ok"):
            return {
                "ok": True,
                "sent": True,
                "channel_id": channel_id,
                "mode": "user_token",
                "results": results
            }

    return {
        "ok": False,
        "sent": False,
        "channel_id": channel_id,
        "results": results
    }


@app.get("/api/blaze/native/send-app-test")
def foxbot_blaze_native_send_app_test_v1(request: Request, message: str = "FoxBot app-token send test - safe mode still OFF."):
    guard = _foxbot_require_admin_v1(request)
    if guard:
        return guard

    return _foxbot_blaze_send_app_token_v1(message)
# === End FoxBot Blaze App Token Send Test v1 ===

# === FoxBot Blaze Live Reply Test Route v2 ===
@app.get("/api/blaze/native/live-reply-test")
def foxbot_blaze_native_live_reply_test_v2(
    request: Request,
    username: str = "crypt0k1ng96",
    message: str = "!connect"
):
    guard = _foxbot_require_admin_v1(request)
    if guard:
        return guard

    from services import blaze_native_connector as native

    fake_event = {
        "metadata": {
            "messageType": "notification",
            "subscriptionType": "channel.chat.message"
        },
        "payload": {
            "channelId": "test",
            "sender": {
                "username": username,
                "displayName": username
            },
            "messageId": "live-reply-test",
            "message": message
        }
    }

    if hasattr(native, "_foxbot_maybe_live_reply_v2"):
        return native._foxbot_maybe_live_reply_v2(fake_event)

    return {
        "ok": False,
        "error": "live reply helper not installed"
    }
# === End FoxBot Blaze Live Reply Test Route v2 ===

# === FoxBot Blaze Event Thanks Test Route v1 ===
@app.get("/api/blaze/native/event-thanks-test")
def foxbot_blaze_native_event_thanks_test_v1(
    request: Request,
    event_type: str = "channel.follow",
    username: str = "crypt0k1ng96",
    send: bool = False,
    amount: str = ""
):
    guard = _foxbot_require_admin_v1(request)
    if guard:
        return guard

    import time
    from services import blaze_native_connector as native

    event_type = str(event_type or "channel.follow").strip()

    payload = {
        "eventId": f"event-thanks-test-{event_type}-{username}-{time.time()}",
        "channelId": "test",
        "sender": {
            "username": username,
            "displayName": username
        },
        "user": {
            "username": username,
            "displayName": username
        },
        "follower": {
            "username": username,
            "displayName": username
        },
        "subscriber": {
            "username": username,
            "displayName": username
        },
        "gifter": {
            "username": username,
            "displayName": username
        },
        "raider": {
            "username": username,
            "displayName": username
        },
        "amount": amount,
        "count": amount,
        "createdAt": str(time.time())
    }

    fake_event = {
        "metadata": {
            "messageType": "notification",
            "subscriptionType": event_type
        },
        "payload": payload
    }

    if not hasattr(native, "_foxbot_event_thank_you_reply_v1"):
        return {
            "ok": False,
            "error": "event thank-you helper not installed"
        }

    preview_reply = native._foxbot_event_thank_you_reply_v1(event_type, payload)

    if not send:
        return {
            "ok": bool(preview_reply),
            "dry_run": True,
            "sent": False,
            "event_type": event_type,
            "username": username,
            "amount": amount,
            "reply": preview_reply,
            "note": "Add &send=true to send this test message into live Blaze chat."
        }

    if hasattr(native, "_foxbot_maybe_event_thank_you_v1"):
        return native._foxbot_maybe_event_thank_you_v1(fake_event)

    return {
        "ok": False,
        "error": "event thank-you sender not installed"
    }
# === End FoxBot Blaze Event Thanks Test Route v1 ===

# === FoxBot Live Control Routes v1 ===
@app.get("/api/blaze/native/live-control")
def foxbot_live_control_api_status_v1(request: Request):
    guard = _foxbot_require_admin_v1(request)
    if guard:
        return guard

    from services import blaze_native_connector as native

    if hasattr(native, "foxbot_live_control_status_v1"):
        return native.foxbot_live_control_status_v1()

    return {"ok": False, "error": "live control status helper missing"}


@app.post("/api/blaze/native/live-control/on")
def foxbot_live_control_api_on_v1(request: Request):
    guard = _foxbot_require_admin_v1(request)
    if guard:
        return guard

    from services import blaze_native_connector as native

    if hasattr(native, "foxbot_live_control_set_v1"):
        return native.foxbot_live_control_set_v1(True, "api on")

    return {"ok": False, "error": "live control setter missing"}


@app.post("/api/blaze/native/live-control/off")
def foxbot_live_control_api_off_v1(request: Request):
    guard = _foxbot_require_admin_v1(request)
    if guard:
        return guard

    from services import blaze_native_connector as native

    if hasattr(native, "foxbot_live_control_set_v1"):
        return native.foxbot_live_control_set_v1(False, "api emergency off")

    return {"ok": False, "error": "live control setter missing"}


@app.post("/api/blaze/native/live-control/env")
def foxbot_live_control_api_env_v1(request: Request):
    guard = _foxbot_require_admin_v1(request)
    if guard:
        return guard

    from services import blaze_native_connector as native

    if hasattr(native, "foxbot_live_control_set_v1"):
        return native.foxbot_live_control_set_v1(None, "api env default")

    return {"ok": False, "error": "live control setter missing"}


@app.get("/foxbot-live-control")
def foxbot_live_control_page_v1():
    from fastapi.responses import HTMLResponse
    return HTMLResponse(content="""
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>FoxBot Live Control</title>
  <style>
    body {
      margin: 0;
      background: #070b08;
      color: #f5f5f5;
      font-family: Arial, sans-serif;
    }
    .wrap {
      max-width: 1100px;
      margin: 0 auto;
      padding: 28px;
    }
    .hero {
      border: 1px solid rgba(255,122,24,.35);
      background:
        radial-gradient(circle at top right, rgba(255,122,24,.22), transparent 32%),
        radial-gradient(circle at bottom left, rgba(57,255,136,.12), transparent 32%),
        #0b100c;
      border-radius: 22px;
      padding: 24px;
      box-shadow: 0 20px 60px rgba(0,0,0,.35);
    }
    h1 {
      margin: 0 0 8px;
      font-size: 34px;
    }
    .sub {
      color: #b8c8bc;
      margin-bottom: 22px;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 14px;
      margin-top: 18px;
    }
    .card {
      border: 1px solid rgba(255,255,255,.12);
      background: rgba(255,255,255,.045);
      border-radius: 16px;
      padding: 16px;
    }
    .label {
      color: #9fb0a3;
      font-size: 13px;
      text-transform: uppercase;
      letter-spacing: .08em;
    }
    .value {
      font-size: 24px;
      font-weight: 800;
      margin-top: 8px;
    }
    .buttons {
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      margin: 22px 0;
    }
    button {
      border: 0;
      border-radius: 14px;
      padding: 14px 18px;
      font-size: 16px;
      font-weight: 800;
      cursor: pointer;
    }
    .on { background: #39ff88; color: #041006; }
    .off { background: #ff3b3b; color: white; }
    .env { background: #ffae42; color: #140b00; }
    .refresh { background: #ffffff; color: #050505; }
    pre {
      white-space: pre-wrap;
      word-break: break-word;
      background: #020402;
      border: 1px solid rgba(255,255,255,.1);
      border-radius: 16px;
      padding: 16px;
      color: #c7ffd9;
      max-height: 460px;
      overflow: auto;
    }
    .good { color: #39ff88; }
    .bad { color: #ff5b5b; }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="hero">
      <h1>🦊 FoxBot Live Control</h1>
      <div class="sub">Emergency controls for Blaze auto-replies.</div>

      <div class="buttons">
        <button class="on" onclick="post('/api/blaze/native/live-control/on')">Turn Live Replies ON</button>
        <button class="off" onclick="post('/api/blaze/native/live-control/off')">EMERGENCY OFF</button>
        <button class="env" onclick="post('/api/blaze/native/live-control/env')">Use Render Env Default</button>
        <button class="refresh" onclick="loadStatus()">Refresh Status</button>
      </div>

      <div class="grid">
        <div class="card">
          <div class="label">Auto Send</div>
          <div class="value" id="auto">Loading...</div>
        </div>
        <div class="card">
          <div class="label">Listener</div>
          <div class="value" id="listener">Loading...</div>
        </div>
        <div class="card">
          <div class="label">Replies Sent</div>
          <div class="value" id="replies">Loading...</div>
        </div>
      </div>

      <h2>Full Status</h2>
      <pre id="status">Loading...</pre>
    </div>
  </div>

<script>
async function loadStatus() {
  const res = await fetch('/api/blaze/native/live-control');
  const data = await res.json();

  document.getElementById('auto').innerHTML = data.auto_send_effective
    ? '<span class="good">ON</span>'
    : '<span class="bad">OFF</span>';

  document.getElementById('listener').innerHTML = data.running && data.connected
    ? '<span class="good">CONNECTED</span>'
    : '<span class="bad">CHECK</span>';

  document.getElementById('replies').textContent = data.replies_sent ?? 0;
  document.getElementById('status').textContent = JSON.stringify(data, null, 2);
}

async function post(url) {
  const res = await fetch(url, { method: 'POST' });
  const data = await res.json();
  document.getElementById('status').textContent = JSON.stringify(data, null, 2);
  await loadStatus();
}

loadStatus();
setInterval(loadStatus, 10000);
</script>
</body>
</html>
""")
# === End FoxBot Live Control Routes v1 ===

# === FoxBot Control Dashboard v2 ===
@app.get("/foxbot-control")
def foxbot_control_dashboard_v2():
    from fastapi.responses import HTMLResponse

    return HTMLResponse(content="""
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>FoxBot Control Dashboard</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    :root {
      --bg: #050805;
      --panel: #0b110c;
      --panel2: rgba(255,255,255,.045);
      --border: rgba(255,255,255,.12);
      --orange: #ff7a18;
      --green: #39ff88;
      --red: #ff3b3b;
      --muted: #a9b9ae;
      --text: #f5fff7;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background:
        radial-gradient(circle at top right, rgba(255,122,24,.14), transparent 28%),
        radial-gradient(circle at bottom left, rgba(57,255,136,.10), transparent 32%),
        var(--bg);
      color: var(--text);
      font-family: Arial, sans-serif;
    }
    .wrap {
      max-width: 1240px;
      margin: 0 auto;
      padding: 28px;
    }
    .hero {
      border: 1px solid rgba(255,122,24,.35);
      background: linear-gradient(135deg, rgba(255,122,24,.10), rgba(57,255,136,.05)), var(--panel);
      border-radius: 24px;
      padding: 24px;
      box-shadow: 0 24px 80px rgba(0,0,0,.38);
    }
    h1 { margin: 0; font-size: 36px; letter-spacing: -.04em; }
    h2 { margin: 24px 0 12px; }
    .sub { color: var(--muted); margin-top: 8px; }
    .topbar {
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: center;
      flex-wrap: wrap;
    }
    .pill {
      border: 1px solid var(--border);
      background: rgba(255,255,255,.05);
      border-radius: 999px;
      padding: 9px 13px;
      color: var(--muted);
      font-weight: 700;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 14px;
      margin-top: 22px;
    }
    .card {
      border: 1px solid var(--border);
      background: var(--panel2);
      border-radius: 18px;
      padding: 16px;
      min-height: 100px;
    }
    .label {
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: .08em;
    }
    .value {
      font-size: 25px;
      font-weight: 900;
      margin-top: 9px;
    }
    .good { color: var(--green); }
    .bad { color: var(--red); }
    .warn { color: var(--orange); }
    .buttons {
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      margin: 22px 0 8px;
    }
    button {
      border: 0;
      border-radius: 14px;
      padding: 14px 17px;
      font-size: 15px;
      font-weight: 900;
      cursor: pointer;
      transition: transform .08s ease, opacity .08s ease;
    }
    button:hover { transform: translateY(-1px); }
    button:active { transform: translateY(1px); opacity: .86; }
    .on { background: var(--green); color: #031006; }
    .off { background: var(--red); color: white; }
    .env { background: var(--orange); color: #160b00; }
    .white { background: white; color: #050505; }
    .dark { background: #182019; color: #e9fff0; border: 1px solid var(--border); }
    .split {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 14px;
    }
    pre {
      white-space: pre-wrap;
      word-break: break-word;
      background: #020402;
      border: 1px solid rgba(255,255,255,.10);
      border-radius: 16px;
      padding: 16px;
      color: #c7ffd9;
      max-height: 430px;
      overflow: auto;
      font-size: 13px;
      line-height: 1.35;
    }
    .small {
      color: var(--muted);
      font-size: 13px;
      line-height: 1.45;
    }
    @media (max-width: 900px) {
      .grid { grid-template-columns: repeat(2, 1fr); }
      .split { grid-template-columns: 1fr; }
    }
    @media (max-width: 560px) {
      .grid { grid-template-columns: 1fr; }
      .wrap { padding: 16px; }
      h1 { font-size: 30px; }
    }
    .kv {
      background: var(--panel2);
      border: 1px solid var(--border);
      border-radius: 14px;
      overflow: hidden;
    }
    .kv-row {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 14px;
      padding: 10px 14px;
      border-bottom: 1px solid var(--border);
      font-size: 14px;
    }
    .kv-row:last-child { border-bottom: 0; }
    .kv-row .k { color: rgba(255,255,255,.6); }
    .kv-row .v { font-weight: 800; text-align: right; word-break: break-word; }
    details.raw { margin-top: 10px; }
    details.raw summary {
      cursor: pointer;
      color: rgba(255,255,255,.55);
      font-size: 12px;
      letter-spacing: .5px;
      text-transform: uppercase;
    }
    details.raw pre { margin-top: 8px; }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="hero">
      <div class="topbar">
        <div>
          <h1>🦊 FoxBot Control Dashboard</h1>
          <div class="sub">Live Blaze controls for @foxbotai and crypt0k1ng96.</div>
        </div>
        <div class="pill" id="updated">Loading...</div>
      </div>

      <div class="grid">
        <div class="card">
          <div class="label">Live Replies</div>
          <div class="value" id="auto">Loading...</div>
          <div class="small" id="autoSource"></div>
        </div>
        <div class="card">
          <div class="label">Listener</div>
          <div class="value" id="listener">Loading...</div>
          <div class="small" id="session"></div>
        </div>
        <div class="card">
          <div class="label">Chat Events</div>
          <div class="value" id="chatEvents">0</div>
          <div class="small">Live chat messages received</div>
        </div>
        <div class="card">
          <div class="label">Replies Sent</div>
          <div class="value" id="replies">0</div>
          <div class="small">Commands + events + shoutouts</div>
        </div>
      </div>

      <h2>Controls</h2>
      <div class="buttons">
        <button class="on" onclick="post('/api/blaze/native/live-control/on')">Live Replies ON</button>
        <button class="off" onclick="post('/api/blaze/native/live-control/off')">Emergency OFF</button>
        <button class="env" onclick="post('/api/blaze/native/live-control/env')">Use Render Env</button>
        <button class="white" onclick="post('/api/blaze/native/start')">Start Listener</button>
        <button class="dark" onclick="post('/api/blaze/native/stop')">Stop Listener</button>
        <button class="white" onclick="restartListener()">Restart Listener</button>
        <button class="dark" onclick="loadAll()">Refresh</button>
      </div>

      <div class="small">
        Emergency OFF overrides Render immediately until the app restarts or you press Live Replies ON / Use Render Env.
      </div>

      <div class="split">
        <div>
          <h2>Last Reply / Event</h2>
          <div id="replyRows" class="kv"></div>
        </div>
        <div>
          <h2>Live Control Status</h2>
          <div id="statusRows" class="kv"></div>
          <details class="raw">
            <summary>Raw live-control JSON</summary>
            <pre id="statusBox">Loading...</pre>
          </details>
        </div>
      </div>

      <h2>Native Listener</h2>
      <div id="nativeRows" class="kv"></div>
      <details class="raw">
        <summary>Raw listener JSON</summary>
        <pre id="nativeBox">Loading...</pre>
      </details>
    </div>
  </div>

<script>
function pretty(obj) {
  return JSON.stringify(obj, null, 2);
}

function setHtml(id, value) {
  document.getElementById(id).innerHTML = value;
}

function setText(id, value) {
  document.getElementById(id).textContent = value;
}

function escapeHtml(value) {
  return String(value).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function renderRows(id, rows) {
  const el = document.getElementById(id);
  if (!el) return;
  el.innerHTML = rows.map(([label, val]) => {
    const [text, cls] = Array.isArray(val) ? val : [val, ''];
    return '<div class="kv-row"><span class="k">' + escapeHtml(label) +
           '</span><span class="v ' + (cls || '') + '">' + escapeHtml(text) + '</span></div>';
  }).join('');
}

function describeAttempt(attempt) {
  if (!attempt) return ['none yet', ''];
  if (typeof attempt === 'string') return [attempt, ''];
  const ok = attempt.ok ?? attempt.sent ?? attempt.success;
  const what = attempt.command || attempt.type || attempt.reason || attempt.message || 'attempt';
  const when = attempt.at || attempt.time || attempt.timestamp || '';
  const text = String(what).slice(0, 48) + (when ? ' @ ' + when : '');
  if (ok === true) return [text, 'good'];
  if (ok === false) return [text, 'bad'];
  return [text, ''];
}

async function getJson(url) {
  const res = await fetch(url);
  return await res.json();
}

async function post(url) {
  setText('updated', 'Working...');
  const res = await fetch(url, { method: 'POST' });
  const data = await res.json();
  setText('statusBox', pretty(data));
  await new Promise(r => setTimeout(r, 700));
  await loadAll();
}

async function restartListener() {
  setText('updated', 'Restarting listener...');
  await fetch('/api/blaze/native/stop', { method: 'POST' });
  await new Promise(r => setTimeout(r, 3000));
  await fetch('/api/blaze/native/start', { method: 'POST' });
  await new Promise(r => setTimeout(r, 1200));
  await loadAll();
}

async function loadAll() {
  try {
    const live = await getJson('/api/blaze/native/live-control');
    const native = await getJson('/api/blaze/native/status');

    const auto = live.auto_send_effective;
    setHtml('auto', auto ? '<span class="good">ON</span>' : '<span class="bad">OFF</span>');
    setText('autoSource', 'source: ' + (live.source || 'unknown'));

    const running = native?.state?.running;
    const connected = native?.state?.connected;

    setHtml('listener', running && connected ? '<span class="good">CONNECTED</span>' : '<span class="bad">CHECK</span>');
    setText('session', native?.state?.session_id ? ('session: ' + native.state.session_id) : 'no session');

    setText('chatEvents', native?.state?.chat_messages_received ?? live.chat_messages_received ?? 0);
    setText('replies', native?.state?.replies_sent ?? live.replies_sent ?? 0);

    const lastReply = live.last_reply_attempt || native?.state?.last_reply_attempt || null;
    const lastEvent = live.last_event_reply_attempt || native?.state?.last_event_reply_attempt || null;
    const lastShout = live.last_role_shoutout_attempt || native?.state?.last_role_shoutout_attempt || null;
    const lastErr = live.last_error || native?.state?.last_error || null;

    renderRows('replyRows', [
      ['Last command reply', describeAttempt(lastReply)],
      ['Last event reply', describeAttempt(lastEvent)],
      ['Last role shoutout', describeAttempt(lastShout)],
      ['Last error', lastErr ? [String(lastErr), 'bad'] : ['none', 'good']]
    ]);

    renderRows('statusRows', [
      ['Auto-send', auto ? ['ON', 'good'] : ['OFF', 'bad']],
      ['Source', String(live.source || 'unknown')],
      ['Override reason', String(live.override?.reason || '-')],
      ['Override updated', String(live.override?.updated_at || 'never')]
    ]);

    renderRows('nativeRows', [
      ['Running', running ? ['yes', 'good'] : ['no', 'bad']],
      ['Connected', connected ? ['yes', 'good'] : ['no', 'bad']],
      ['Session', String(native?.state?.session_id || 'none')],
      ['Events received', String(native?.state?.events_received ?? 0)],
      ['Chat messages', String(native?.state?.chat_messages_received ?? 0)],
      ['Replies sent', String(native?.state?.replies_sent ?? 0)],
      ['Started at', String(native?.state?.started_at || '-')],
      ['Disconnect reason', String(native?.state?.disconnect_reason || '-')]
    ]);

    setText('statusBox', pretty(live));
    setText('nativeBox', pretty(native));
    setText('updated', 'Updated: ' + new Date().toLocaleTimeString());
  } catch (err) {
    setHtml('listener', '<span class="bad">ERROR</span>');
    setText('statusBox', String(err));
    setText('updated', 'Error loading status');
  }
}

loadAll();
setInterval(loadAll, 10000);
</script>
</body>
</html>
""")
# === End FoxBot Control Dashboard v2 ===

# === FoxBot Admin Command Send v1 ===
@app.post("/api/foxbot/admin-command")
async def foxbot_admin_command_send_v1(payload: dict):
    username = str(payload.get("username") or "crypt0k1ng96").strip()
    message = str(payload.get("message") or "").strip()
    send_to_blaze = bool(payload.get("send_to_blaze", True))

    if not message:
        return {"ok": False, "error": "Missing message"}

    try:
        result = chat(message=message, username=username)
    except Exception as e:
        return {
            "ok": False,
            "username": username,
            "message": message,
            "error": f"Command engine failed: {e}"
        }

    reply = ""

    if isinstance(result, dict):
        reply = result.get("response") or result.get("reply") or result.get("message") or ""
    elif isinstance(result, str):
        reply = result

    reply = str(reply or "").strip()

    send_result = None

    if send_to_blaze and reply:
        try:
            from services import blaze_native_connector as native

            if hasattr(native, "_foxbot_live_send_chat_v2"):
                send_result = native._foxbot_live_send_chat_v2(reply)
            else:
                send_result = {
                    "ok": False,
                    "sent": False,
                    "error": "native._foxbot_live_send_chat_v2 missing"
                }
        except Exception as e:
            send_result = {
                "ok": False,
                "sent": False,
                "error": str(e)
            }

    return {
        "ok": True,
        "username": username,
        "message": message,
        "command_result": result,
        "reply": reply,
        "send_to_blaze": send_to_blaze,
        "send_result": send_result
    }
# === End FoxBot Admin Command Send v1 ===

# === FoxBot Rewards Fun Emoji Skin v1 ===
def foxbot_rewards_v21_fun_icon_v1(reward):
    reward_id = str((reward or {}).get("id") or "").lower()

    icons = {
        "hug": "\U0001F917\U0001F49B",
        "hype": "\U0001F525\u26A1",
        "flex": "\U0001F4AA\U0001F624",
        "hydrate": "\U0001F4A7\U0001F9CA",
        "stretch": "\U0001F9D8\u2728",
        "foxfact": "\U0001F98A\U0001F4DC",
        "clipit": "\U0001F3AC\u2702\uFE0F",
        "lurklove": "\U0001F440\U0001F49C",

        "shoutout": "\U0001F4E3\U0001F31F",
        "socialsplug": "\U0001F517\U0001F680",
        "nickname": "\U0001F3F7\uFE0F\U0001F602",
        "poll": "\U0001F5F3\uFE0F\U0001F9E0",
        "mvp": "\U0001F451\U0001F525",
        "og": "\U0001F6E1\uFE0F\U0001F98A",
        "raidcaptain": "\U0001F6A9\u2694\uFE0F",
        "vipwall": "\u2B50\U0001F3C6",

        "loadout": "\U0001F3AF\U0001F52B",
        "dropzone": "\U0001FA82\U0001F4CD",
        "challenge": "\u26A1\U0001F3B2",
        "gamemode": "\U0001F579\uFE0F\U0001F3AE",
        "soundalert": "\U0001F50A\U0001F923",
        "nextmatchvote": "\U0001F9ED\u2705",
        "choosegame": "\U0001F3AE\U0001F440",
        "streamtitle": "\u270D\uFE0F\U0001F9E0",

        "goldenfox": "\U0001F98A\U0001F3C6\u2728",
        "treasurecall": "\U0001F4B0\U0001F5FA\uFE0F",
        "bossboost": "\u2694\uFE0F\U0001F479",
        "doublepoints": "\u2716\uFE0F\U00000032\uFE0F\u20E3\u2728",
        "giveawayboost": "\U0001F39F\uFE0F\U0001F381",
        "mysterybox": "\U0001F381\u2753",
        "customcommand": "\U0001F916\U0001F6E0\uFE0F",
        "squadpriority": "\U0001F465\u2B50",

        "sponsor": "\U0001F48E\U0001F4E2",
        "producer": "\U0001F3A5\U0001F451",
        "streamsegment": "\U0001F3A4\U0001F3AC",
        "vipnight": "\U0001F319\U0001F31F",
        "foxlegend": "\U0001F3C6\U0001F98A\U0001F525",
    }

    return icons.get(reward_id, str((reward or {}).get("emoji") or "\U0001F381") + "\u2728")


def foxbot_rewards_v2_shop_text(page="main"):
    page = str(page or "main").strip().lower()

    aliases = {
        "low": "cheap",
        "starter": "cheap",
        "basic": "cheap",
        "recognition": "social",
        "status": "social",
        "stream": "control",
        "influence": "control",
        "high": "premium",
        "expensive": "elite",
        "legend": "elite",
        "full": "all"
    }

    page = aliases.get(page, page)

    labels = {
        "cheap": "\U0001FA99 Starter Fun Rewards",
        "social": "\U0001F31F Recognition Rewards",
        "control": "\U0001F3AE Control The Stream",
        "premium": "\U0001F48E Premium Chaos Rewards",
        "elite": "\U0001F3C6 Elite Fox Legend Rewards",
        "all": "\U0001F308 All FoxBot Rewards"
    }

    if page in {"main", "menu"}:
        return (
            "\U0001F98A\u2728 FoxBot Rewards 2.1 \u2728\U0001F98A | "
            "\U0001FA99 !shop cheap | "
            "\U0001F31F !shop social | "
            "\U0001F3AE !shop control | "
            "\U0001F48E !shop premium | "
            "\U0001F3C6 !shop elite | "
            "\U0001F308 !shop all | "
            "\U0001F381 Redeem: !redeem rewardname"
        )

    if page == "all":
        items = FOXBOT_REWARDS_V2
    else:
        items = [r for r in FOXBOT_REWARDS_V2 if r.get("category") == page]

    if not items:
        return (
            "\U0001F98A\u2753 Reward page not found. Try: "
            "!shop cheap, !shop social, !shop control, !shop premium, !shop elite, or !shop all."
        )

    parts = []
    for reward in items:
        icon = foxbot_rewards_v21_fun_icon_v1(reward)
        parts.append(f"{icon} {reward['id']} {reward['cost']}")

    label = labels.get(page, page.title())

    return (
        f"{label}: "
        + " | ".join(parts)
        + " | \U0001F381 Redeem: !redeem name"
    )


def foxbot_rewards_v2_redeem_text(username, reward_name):
    username = str(username or "viewer").strip().lstrip("@")
    reward = foxbot_rewards_v2_find(reward_name)

    if not reward:
        options = ", ".join([r["id"] for r in FOXBOT_REWARDS_V2[:10]])
        return (
            f"\U0001F98A\u2753 @{username}, I could not find that reward. "
            f"Try: {options}. Use !shop to see categories."
        )

    path = foxbot_rewards_v2_redemptions_path()
    redemptions = foxbot_rewards_v2_read_json(path, [])

    icon = foxbot_rewards_v21_fun_icon_v1(reward)

    record = {
        "id": f"redemption-{int(_foxbot_rewards_time.time())}-{len(redemptions) + 1}",
        "username": username,
        "reward_id": reward["id"],
        "reward_name": reward["name"],
        "emoji": reward.get("emoji", ""),
        "fun_icon": icon,
        "cost": reward["cost"],
        "category": reward.get("category", "uncategorized"),
        "description": reward["description"],
        "fulfillment": reward.get("fulfillment", "streamer_review"),
        "status": "pending",
        "created_at": int(_foxbot_rewards_time.time())
    }

    redemptions.append(record)
    foxbot_rewards_v2_write_json(path, redemptions)

    return (
        f"{icon} REDEEMED! @{username} claimed {reward['name']} for {reward['cost']} FoxCoins! "
        f"\u2728 Status: pending streamer approval \U0001F9E1 | {reward['description']}"
    )


def foxbot_rewards_v2_queue_text():
    path = foxbot_rewards_v2_redemptions_path()
    redemptions = foxbot_rewards_v2_read_json(path, [])
    pending = [r for r in redemptions if r.get("status") == "pending"]

    if not pending:
        return "\U0001F98A\u2728 Reward queue is empty. Chat can redeem with !redeem rewardname."

    latest = pending[-5:]
    parts = []

    for item in latest:
        icon = item.get("fun_icon") or item.get("emoji") or "\U0001F381"
        parts.append(f"{icon} @{item.get('username')} - {item.get('reward_name')}")

    return "\U0001F381\u2728 Pending Reward Queue: " + " | ".join(parts)
# === End FoxBot Rewards Fun Emoji Skin v1 ===

# === FoxBot Safe Rewards 2.1 Admin Command Hook v1 ===
import time as _foxbot_safe_rewards_time
import json as _foxbot_safe_rewards_json
from pathlib import Path as _foxbot_safe_rewards_Path

FOXBOT_SAFE_REWARDS_21 = [
    {"id":"hug","emoji":"\U0001F917\U0001F49B","cost":10,"category":"cheap","name":"Fox Hug","description":"FoxBot sends a wholesome hug in chat.","aliases":["hug","foxhug"]},
    {"id":"hype","emoji":"\U0001F525\u26A1","cost":25,"category":"cheap","name":"Hype Blast","description":"FoxBot fires up chat.","aliases":["hype","fire"]},
    {"id":"flex","emoji":"\U0001F4AA\U0001F624","cost":50,"category":"cheap","name":"Flex Moment","description":"Viewer gets a flex shoutout.","aliases":["flex"]},
    {"id":"hydrate","emoji":"\U0001F4A7\U0001F9CA","cost":50,"category":"cheap","name":"Hydration Check","description":"Chat reminds streamer to drink water.","aliases":["hydrate","water"]},
    {"id":"stretch","emoji":"\U0001F9D8\u2728","cost":75,"category":"cheap","name":"Stretch Break","description":"Quick stretch break.","aliases":["stretch","break"]},
    {"id":"foxfact","emoji":"\U0001F98A\U0001F4DC","cost":75,"category":"cheap","name":"Fox Fact","description":"Fox Spirit fact or lore line.","aliases":["foxfact","fact","lore"]},
    {"id":"clipit","emoji":"\U0001F3AC\u2702\uFE0F","cost":100,"category":"cheap","name":"Clip This Moment","description":"Marks a clip-worthy moment.","aliases":["clipit","clip"]},
    {"id":"lurklove","emoji":"\U0001F440\U0001F49C","cost":100,"category":"cheap","name":"Lurker Love","description":"Love for quiet viewers.","aliases":["lurk","lurklove"]},

    {"id":"shoutout","emoji":"\U0001F4E3\U0001F31F","cost":500,"category":"social","name":"Stream Shoutout","description":"Full viewer shoutout.","aliases":["shoutout","so"]},
    {"id":"socialsplug","emoji":"\U0001F517\U0001F680","cost":650,"category":"social","name":"Socials Plug","description":"Viewer gets a short socials plug.","aliases":["plug","socials"]},
    {"id":"nickname","emoji":"\U0001F3F7\uFE0F\U0001F602","cost":800,"category":"social","name":"Temporary Nickname","description":"Temporary stream nickname.","aliases":["nickname","nick"]},
    {"id":"poll","emoji":"\U0001F5F3\uFE0F\U0001F9E0","cost":900,"category":"social","name":"Community Poll","description":"Viewer starts a poll idea.","aliases":["poll","vote"]},
    {"id":"mvp","emoji":"\U0001F451\U0001F525","cost":1000,"category":"social","name":"MVP Spotlight","description":"MVP spotlight message.","aliases":["mvp","spotlight"]},
    {"id":"og","emoji":"\U0001F6E1\uFE0F\U0001F98A","cost":1000,"category":"social","name":"OG Spirit Shoutout","description":"OG recognition message.","aliases":["og","ogspirit"]},
    {"id":"raidcaptain","emoji":"\U0001F6A9\u2694\uFE0F","cost":1200,"category":"social","name":"Raid Captain","description":"Raid Captain callout.","aliases":["raid","raidcaptain"]},
    {"id":"vipwall","emoji":"\u2B50\U0001F3C6","cost":1500,"category":"social","name":"VIP Wall Mention","description":"VIP wall style mention.","aliases":["vip","vipwall"]},

    {"id":"loadout","emoji":"\U0001F3AF\U0001F52B","cost":750,"category":"control","name":"Choose My Loadout","description":"Viewer suggests a loadout/build.","aliases":["loadout","weapon","build"]},
    {"id":"dropzone","emoji":"\U0001FA82\U0001F4CD","cost":850,"category":"control","name":"Choose Drop Zone","description":"Viewer chooses the next drop/start spot.","aliases":["dropzone","drop"]},
    {"id":"challenge","emoji":"\u26A1\U0001F3B2","cost":1000,"category":"control","name":"Streamer Challenge","description":"Viewer gives a safe challenge.","aliases":["challenge","mission"]},
    {"id":"gamemode","emoji":"\U0001F579\uFE0F\U0001F3AE","cost":1250,"category":"control","name":"Choose Game Mode","description":"Viewer votes for next mode.","aliases":["gamemode","mode"]},
    {"id":"soundalert","emoji":"\U0001F50A\U0001F923","cost":1250,"category":"control","name":"Sound Alert Moment","description":"Streamer-approved sound moment.","aliases":["sound","soundalert"]},
    {"id":"choosegame","emoji":"\U0001F3AE\U0001F440","cost":3000,"category":"control","name":"Choose The Game","description":"Viewer suggests next game/segment.","aliases":["choosegame","game"]},

    {"id":"goldenfox","emoji":"\U0001F98A\U0001F3C6\u2728","cost":2500,"category":"premium","name":"Golden Fox Callout","description":"Premium Fox Spirit shoutout.","aliases":["goldenfox","fox"]},
    {"id":"treasurecall","emoji":"\U0001F4B0\U0001F5FA\uFE0F","cost":3000,"category":"premium","name":"Treasure Drop Call","description":"Treasure Drop style moment.","aliases":["treasure","treasurecall"]},
    {"id":"bossboost","emoji":"\u2694\uFE0F\U0001F479","cost":3500,"category":"premium","name":"Boss Battle Boost","description":"Special boss battle boost moment.","aliases":["boss","bossboost"]},
    {"id":"doublepoints","emoji":"\u2716\uFE0F\u0032\uFE0F\u20E3\u2728","cost":4000,"category":"premium","name":"Double Points Minute","description":"Streamer-approved double points moment.","aliases":["double","doublepoints","2x"]},
    {"id":"giveawayboost","emoji":"\U0001F39F\uFE0F\U0001F381","cost":4500,"category":"premium","name":"Giveaway Entry Boost","description":"Bonus giveaway entry request.","aliases":["ticket","giveawayboost"]},
    {"id":"mysterybox","emoji":"\U0001F381\u2753","cost":5000,"category":"premium","name":"Mystery Box","description":"Random fun reward chosen by streamer.","aliases":["box","mysterybox"]},

    {"id":"sponsor","emoji":"\U0001F48E\U0001F4E2","cost":10000,"category":"elite","name":"Sponsor Spotlight","description":"Premium sponsor-style spotlight.","aliases":["sponsor","gem"]},
    {"id":"producer","emoji":"\U0001F3A5\U0001F451","cost":15000,"category":"elite","name":"Stream Producer Credit","description":"Viewer gets producer/supporter credit.","aliases":["producer","credit"]},
    {"id":"streamsegment","emoji":"\U0001F3A4\U0001F3AC","cost":20000,"category":"elite","name":"Viewer Stream Segment","description":"Viewer helps create/name a segment.","aliases":["segment","streamsegment"]},
    {"id":"vipnight","emoji":"\U0001F319\U0001F31F","cost":25000,"category":"elite","name":"VIP Community Night Vote","description":"Major vote toward future community night.","aliases":["vipnight","night"]},
    {"id":"foxlegend","emoji":"\U0001F3C6\U0001F98A\U0001F525","cost":50000,"category":"elite","name":"Fox Legend Status","description":"Ultimate long-term flex reward.","aliases":["legend","foxlegend","goat"]}
]

def foxbot_safe_rewards21_shop_text_v1(page="main"):
    page = str(page or "main").strip().lower()
    aliases = {
        "low":"cheap", "starter":"cheap", "basic":"cheap",
        "recognition":"social", "status":"social",
        "stream":"control", "influence":"control",
        "high":"premium", "legend":"elite", "full":"all"
    }
    page = aliases.get(page, page)

    labels = {
        "cheap":"\U0001FA99 Starter Fun Rewards",
        "social":"\U0001F31F Recognition Rewards",
        "control":"\U0001F3AE Control The Stream",
        "premium":"\U0001F48E Premium Chaos Rewards",
        "elite":"\U0001F3C6 Elite Fox Legend Rewards",
        "all":"\U0001F308 All FoxBot Rewards"
    }

    if page in {"main", "menu"}:
        return (
            "\U0001F98A\u2728 FoxBot Rewards 2.1 \u2728\U0001F98A | "
            "\U0001FA99 !shop cheap | \U0001F31F !shop social | \U0001F3AE !shop control | "
            "\U0001F48E !shop premium | \U0001F3C6 !shop elite | \U0001F308 !shop all | "
            "\U0001F381 Redeem: !redeem rewardname"
        )

    items = FOXBOT_SAFE_REWARDS_21 if page == "all" else [r for r in FOXBOT_SAFE_REWARDS_21 if r.get("category") == page]

    if not items:
        return "\U0001F98A\u2753 Page not found. Try !shop cheap, social, control, premium, elite, or all."

    parts = [f"{r['emoji']} {r['id']} {r['cost']}" for r in items]
    return f"{labels.get(page, page.title())}: " + " | ".join(parts) + " | \U0001F381 Redeem: !redeem name"

def foxbot_safe_rewards21_find_v1(name):
    key = str(name or "").strip().lower().replace(" ", "")
    for r in FOXBOT_SAFE_REWARDS_21:
        if key == r["id"]:
            return r
        if key == r["name"].lower().replace(" ", ""):
            return r
        if key in [str(a).lower().replace(" ", "") for a in r.get("aliases", [])]:
            return r
    return None

# === End FoxBot Safe Rewards 2.1 Admin Command Hook v1 ===


# === FoxBot Rewards v2 Compat Aliases v1 ===
# The "Rewards Fun Emoji Skin" fallback functions above reference v2 helper
# names that were never defined anywhere, so the fallback path raised
# NameError when invoked. Alias them to the Safe Rewards 2.1 implementations
# so both paths share one catalog and one redemption file.
import time as _foxbot_rewards_time

FOXBOT_REWARDS_V2 = FOXBOT_SAFE_REWARDS_21
foxbot_rewards_v2_find = foxbot_safe_rewards21_find_v1


def foxbot_rewards_v2_redemptions_path():
    data_dir = _foxbot_safe_rewards_Path("data")
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "foxbot_safe_rewards21_redemptions.json"


def foxbot_rewards_v2_read_json(path, default):
    try:
        if path.exists():
            return _foxbot_safe_rewards_json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def foxbot_rewards_v2_write_json(path, data):
    path.write_text(
        _foxbot_safe_rewards_json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
# === End FoxBot Rewards v2 Compat Aliases v1 ===



# === FoxBot Studio Giveaway Live API v3 ===
def foxbot_studio_giveaway_state_v3():
    return globals().setdefault("FOXBOT_STUDIO_GIVEAWAY_STATE_V3", {
        "active": False,
        "prize": "Weekly Giveaway",
        "rules": "Type !enter to join.",
        "last_winner": None,
        "last_entry": None,
        "last_action": "Waiting"
    })


def foxbot_studio_giveaway_entries_v3():
    entries = globals().setdefault("giveaway_entries", [])
    if entries is None:
        globals()["giveaway_entries"] = []
        entries = globals()["giveaway_entries"]
    return entries


def foxbot_studio_giveaway_send_v3(message: str):
    message = str(message or "").strip()
    if not message:
        return {"ok": False, "success": False, "message": "No message provided."}

    errors = []

    try:
        result = send_blaze_chat_message(message)
        if isinstance(result, dict):
            if result.get("success") or result.get("ok"):
                return result
            errors.append({"send_blaze_chat_message": result})
        elif result:
            return {"ok": True, "success": True, "message": "Sent through send_blaze_chat_message.", "result": str(result)}
    except Exception as e:
        errors.append({"send_blaze_chat_message_error": str(e)})

    try:
        sender = globals().get("_foxbot_blaze_send_app_token_v1")
        if sender:
            result = sender(message)
            if isinstance(result, dict):
                if result.get("success") or result.get("ok"):
                    return result
                errors.append({"app_token_sender": result})
            elif result:
                return {"ok": True, "success": True, "message": "Sent through app token sender.", "result": str(result)}
    except Exception as e:
        errors.append({"app_token_sender_error": str(e)})

    try:
        from services.blaze_native_connector import send_blaze_chat
        result = send_blaze_chat(message)
        if isinstance(result, dict):
            if result.get("success") or result.get("ok"):
                return result
            errors.append({"native_send": result})
        elif result:
            return {"ok": True, "success": True, "message": "Sent through native connector.", "result": str(result)}
    except Exception as e:
        errors.append({"native_send_error": str(e)})

    return {
        "ok": False,
        "success": False,
        "message": "Could not send to Blaze chat.",
        "errors": errors[-6:]
    }


@app.get("/api/studio/giveaways/status")
def foxbot_studio_giveaway_status_v3():
    state = foxbot_studio_giveaway_state_v3()
    entries = foxbot_studio_giveaway_entries_v3()

    return {
        "ok": True,
        "state": state,
        "count": len(entries),
        "entries": list(entries)[-500:]
    }


@app.post("/api/studio/giveaways/start")
async def foxbot_studio_giveaway_start_v3(payload: dict, request: Request):
    guard = _foxbot_require_admin_v1(request)
    if guard:
        return guard

    state = foxbot_studio_giveaway_state_v3()
    entries = foxbot_studio_giveaway_entries_v3()

    prize = str(payload.get("prize", "Weekly Giveaway")).strip() or "Weekly Giveaway"
    rules = str(payload.get("rules", "Type !enter to join. One entry per viewer.")).strip()
    clear_old = bool(payload.get("clear_old", True))

    if clear_old:
        entries.clear()

    state["active"] = True
    state["prize"] = prize
    state["rules"] = rules
    state["last_winner"] = None
    state["last_entry"] = None
    state["last_action"] = "Started"

    msg = f"GIVEAWAY LIVE: {prize} | Type !enter to join! {rules}"
    sent = foxbot_studio_giveaway_send_v3(msg)

    return {"ok": True, "message": "Giveaway started.", "state": state, "count": len(entries), "sent_to_blaze": sent}


@app.post("/api/studio/giveaways/announce")
async def foxbot_studio_giveaway_announce_v3(request: Request, payload: dict = None):
    guard = _foxbot_require_admin_v1(request)
    if guard:
        return guard

    state = foxbot_studio_giveaway_state_v3()
    entries = foxbot_studio_giveaway_entries_v3()

    msg = f"GIVEAWAY LIVE: {state.get('prize','Weekly Giveaway')} | Type !enter to join! {state.get('rules','')} | Entries: {len(entries)}"
    sent = foxbot_studio_giveaway_send_v3(msg)
    state["last_action"] = "Announced"

    return {"ok": True, "message": msg, "sent_to_blaze": sent}


@app.post("/api/studio/giveaways/close")
async def foxbot_studio_giveaway_close_v3(request: Request, payload: dict = None):
    guard = _foxbot_require_admin_v1(request)
    if guard:
        return guard

    state = foxbot_studio_giveaway_state_v3()
    entries = foxbot_studio_giveaway_entries_v3()

    state["active"] = False
    state["last_action"] = "Closed"

    msg = f"Giveaway entries closed. Total entries: {len(entries)}"
    sent = foxbot_studio_giveaway_send_v3(msg)

    return {"ok": True, "message": "Giveaway closed.", "state": state, "count": len(entries), "sent_to_blaze": sent}


@app.post("/api/studio/giveaways/add")
async def foxbot_studio_giveaway_add_v3(payload: dict, request: Request):
    guard = _foxbot_require_admin_v1(request)
    if guard:
        return guard

    state = foxbot_studio_giveaway_state_v3()
    entries = foxbot_studio_giveaway_entries_v3()

    username = str(payload.get("username", "")).strip().lstrip("@")
    if not username:
        return {"ok": False, "error": "Username is required."}

    existing = [str(x).lower() for x in entries]
    if username.lower() in existing:
        return {"ok": False, "message": f"{username} is already entered.", "count": len(entries), "entries": entries}

    entries.append(username)
    state["last_entry"] = username
    state["last_action"] = f"Added {username}"

    return {"ok": True, "message": f"{username} added.", "count": len(entries), "entries": entries}


@app.post("/api/studio/giveaways/remove")
async def foxbot_studio_giveaway_remove_v3(payload: dict, request: Request):
    guard = _foxbot_require_admin_v1(request)
    if guard:
        return guard

    entries = foxbot_studio_giveaway_entries_v3()
    username = str(payload.get("username", "")).strip().lstrip("@")

    before = len(entries)
    entries[:] = [x for x in entries if str(x).lower() != username.lower()]

    return {"ok": True, "message": f"Removed {before - len(entries)} entries for {username}.", "count": len(entries), "entries": entries}


@app.post("/api/studio/giveaways/pick")
async def foxbot_studio_giveaway_pick_v3(payload: dict, request: Request):
    guard = _foxbot_require_admin_v1(request)
    if guard:
        return guard

    import random

    state = foxbot_studio_giveaway_state_v3()
    entries = foxbot_studio_giveaway_entries_v3()

    if not entries:
        return {"ok": False, "error": "No entries yet."}

    winner = random.choice(entries)
    state["last_winner"] = winner
    state["last_action"] = "Picked winner"

    announce = bool(payload.get("announce", True))
    sent = None

    if announce:
        msg = f"WINNER: @{winner} won {state.get('prize', 'the giveaway')}! Congratulations!"
        sent = foxbot_studio_giveaway_send_v3(msg)

    return {"ok": True, "winner": winner, "state": state, "sent_to_blaze": sent}


@app.post("/api/studio/giveaways/clear")
async def foxbot_studio_giveaway_clear_v3(request: Request, payload: dict = None):
    guard = _foxbot_require_admin_v1(request)
    if guard:
        return guard

    state = foxbot_studio_giveaway_state_v3()
    entries = foxbot_studio_giveaway_entries_v3()

    entries.clear()
    state["last_winner"] = None
    state["last_entry"] = None
    state["last_action"] = "Cleared"

    return {"ok": True, "message": "Entries cleared.", "state": state, "count": len(entries)}


@app.post("/api/studio/giveaways/send")
async def foxbot_studio_giveaway_send_route_v3(payload: dict, request: Request):
    guard = _foxbot_require_admin_v1(request)
    if guard:
        return guard

    message = str(payload.get("message", "")).strip()
    sent = foxbot_studio_giveaway_send_v3(message)
    return {"ok": bool(sent.get("ok") or sent.get("success")), "message": message, "sent_to_blaze": sent}


@app.get("/overlay/studio-giveaway")
def foxbot_studio_giveaway_overlay_v3():
    from fastapi.responses import HTMLResponse

    html = """
<!doctype html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
body{margin:0;background:transparent;color:white;font-family:Arial,sans-serif;overflow:hidden}
.box{width:760px;padding:28px;border-radius:28px;background:linear-gradient(135deg,rgba(249,115,22,.92),rgba(10,16,12,.92));border:2px solid rgba(255,255,255,.18);box-shadow:0 22px 60px rgba(0,0,0,.45)}
.kicker{font-size:20px;font-weight:900;letter-spacing:.12em;text-transform:uppercase;color:#fff7ed}
.title{font-size:46px;font-weight:1000;line-height:1.05;margin:10px 0}
.row{display:flex;gap:18px;margin-top:18px}
.stat{flex:1;padding:14px;border-radius:18px;background:rgba(0,0,0,.28)}
.label{font-size:15px;color:#fed7aa}
.value{font-size:30px;font-weight:1000}
</style>
</head>
<body>
<div class="box">
  <div class="kicker">FoxBot Giveaway</div>
  <div id="prize" class="title">Waiting for giveaway...</div>
  <div id="rules">Type !enter to join.</div>
  <div class="row">
    <div class="stat"><div class="label">Status</div><div id="status" class="value">Waiting</div></div>
    <div class="stat"><div class="label">Entries</div><div id="entries" class="value">0</div></div>
    <div class="stat"><div class="label">Winner</div><div id="winner" class="value">None</div></div>
  </div>
</div>
<script>
async function refresh(){
  const res = await fetch('/api/studio/giveaways/status');
  const data = await res.json();
  const s = data.state || {};
  document.getElementById('prize').textContent = s.prize || 'Weekly Giveaway';
  document.getElementById('rules').textContent = s.rules || 'Type !enter to join.';
  document.getElementById('status').textContent = s.active ? 'LIVE' : 'Closed';
  document.getElementById('entries').textContent = data.count || 0;
  document.getElementById('winner').textContent = s.last_winner || 'None';
}
refresh();
setInterval(refresh, 3000);
</script>
</body>
</html>
"""
    return HTMLResponse(html)

# === End FoxBot Studio Giveaway Live API v3 ===











# === FoxBot Creator Onboarding v1 ===
@app.get("/get-started", response_class=HTMLResponse)
def foxbot_creator_onboarding_v1():
    return FileResponse(
        "templates/foxbot_onboarding.html",
        media_type="text/html",
    )
# === End FoxBot Creator Onboarding v1 ===

# === FoxBot Blaze Creator Access v1 ===
from services import creator_access as _foxbot_creator_access_v1


def _foxbot_connect_process_command_v1(handle, message, display_name=None):
    handle = _foxbot_connect_clean_handle_v1(handle)
    message = str(message or "").strip()

    if not handle:
        return {
            "ok": False,
            "handled": False,
            "error": "Missing Blaze handle."
        }

    if not message.startswith("!"):
        return {
            "ok": True,
            "handled": False,
            "reply": None
        }

    command = message.split()[0].lower()

    if command == "!connect":
        creator = _foxbot_connect_upsert_creator_v1(handle, display_name=display_name)
        return {
            "ok": True,
            "handled": True,
            "command": "!connect",
            "creator": creator,
            "reply": f"🦊 @{handle} is now connected to FoxBot Connect! +25 FoxCoins. Use !profile to view your FoxBot profile."
        }

    if command in ["!profile", "!rank"]:
        creator = _foxbot_connect_get_creator_v1(handle)

        if not creator:
            return {
                "ok": True,
                "handled": True,
                "command": command,
                "reply": f"🦊 @{handle}, you are not connected yet. Follow the FoxBot Blaze profile and type !connect."
            }

        foxcoins = creator.get("foxcoins", 0)
        messages = creator.get("messages", 0)
        stars = creator.get("stars", 0)
        status = creator.get("status", "connected")

        return {
            "ok": True,
            "handled": True,
            "command": command,
            "creator": creator,
            "reply": f"🦊 @{handle} FoxBot Profile | Status: {status} | FoxCoins: {foxcoins} | Messages: {messages} | Stars: {stars}"
        }

    if command == "!disconnect":
        raw = _foxbot_connect_load_raw_v1()
        creator = _foxbot_connect_get_creator_v1(handle)

        if not creator:
            return {
                "ok": True,
                "handled": True,
                "command": "!disconnect",
                "reply": f"@{handle}, you were not connected yet."
            }

        key_to_update = handle
        for key in raw.keys():
            if str(key).lower() == handle.lower():
                key_to_update = key
                break

        raw[key_to_update]["status"] = "disconnected"
        raw[key_to_update]["disconnected_at"] = _foxbot_connect_now_iso_v1()
        _foxbot_connect_save_raw_v1(raw)

        return {
            "ok": True,
            "handled": True,
            "command": "!disconnect",
            "reply": f"🦊 @{handle} has been disconnected from FoxBot Connect."
        }

    return {
        "ok": True,
        "handled": False,
        "command": command,
        "reply": None
    }


_foxbot_connect_process_command_without_access_v1 = _foxbot_connect_process_command_v1


def _foxbot_connect_process_command_v1(handle, message, display_name=None):
    """Extend FoxBot Connect with trial and subscription access commands."""
    clean_handle = _foxbot_creator_access_v1.clean_handle(handle)
    clean_message = str(message or "").strip()
    command = clean_message.split()[0].lower() if clean_message.startswith("!") else ""

    if command == "!join":
        existing = _foxbot_connect_get_creator_v1(clean_handle)
        if not existing:
            _foxbot_connect_process_command_without_access_v1(
                clean_handle,
                "!connect",
                display_name=display_name,
            )

        access = _foxbot_creator_access_v1.start_trial(clean_handle, display_name)
        if access.get("started"):
            reply = (
                f"@{clean_handle}, your FoxBot 7-day trial is active. "
                "Use !access anytime to check your status."
            )
        else:
            reply = (
                f"@{clean_handle}, FoxBot access is {access.get('status')}. "
                f"Days remaining: {access.get('remaining_days', 0)}."
            )

        return {
            "ok": True,
            "handled": True,
            "command": "!join",
            "access": access,
            "reply": reply,
        }

    if command == "!access":
        access = _foxbot_creator_access_v1.get_access(clean_handle)
        if access.get("status") == "not_started":
            reply = f"@{clean_handle}, type !join to start your free 7-day FoxBot trial."
        else:
            reply = (
                f"@{clean_handle} FoxBot Access | Status: {access.get('status')} | "
                f"Days remaining: {access.get('remaining_days', 0)} | "
                f"Verification: {access.get('verification_status')}"
            )
        return {
            "ok": True,
            "handled": True,
            "command": "!access",
            "access": access,
            "reply": reply,
        }

    if command == "!verify":
        access = _foxbot_creator_access_v1.request_verification(clean_handle)
        if access.get("ok"):
            reply = (
                f"@{clean_handle}, your FoxBot subscription verification request is pending. "
                "Use !access to check its status."
            )
        else:
            reply = f"@{clean_handle}, type !join before requesting verification."
        return {
            "ok": True,
            "handled": True,
            "command": "!verify",
            "access": access,
            "reply": reply,
        }

    result = _foxbot_connect_process_command_without_access_v1(
        clean_handle,
        clean_message,
        display_name=display_name,
    )

    if command in {"!profile", "!rank"} and result.get("handled"):
        access = _foxbot_creator_access_v1.get_access(clean_handle)
        if access.get("status") != "not_started":
            result["access"] = access
            result["reply"] = (
                str(result.get("reply") or "")
                + f" | Access: {access.get('status')} ({access.get('remaining_days', 0)} days)"
            )

    return result


@app.get("/api/foxbot/access")
def foxbot_creator_access_list_v1():
    return {
        "ok": True,
        "trial_days": _foxbot_creator_access_v1.TRIAL_DAYS,
        "creators": _foxbot_creator_access_v1.list_access(),
    }


@app.get("/api/foxbot/access/{handle}")
def foxbot_creator_access_status_v1(handle: str):
    return {
        "ok": True,
        "access": _foxbot_creator_access_v1.get_access(handle),
    }
# === End FoxBot Blaze Creator Access v1 ===

# === FoxBot Blaze Multi-Channel Listener v1 ===
from services import blaze_multichannel as _foxbot_multichannel_service_v1

_FOXBOT_MULTICHANNEL_STATE_V1 = {
    "running": False,
    "cycles": 0,
    "target_count": 0,
    "active_creator_count": 0,
    "channels_checked": 0,
    "messages_seen": 0,
    "commands_processed": 0,
    "unresolved": [],
    "targets": [],
    "last_error": None,
    "last_cycle_at": None,
}
_FOXBOT_MULTICHANNEL_INITIALIZED_V1 = set()




def blaze_polling_worker():
    """Poll owner plus every creator channel with current FoxBot access."""
    from datetime import datetime, timezone
    import time

    polling_status["running"] = True
    polling_status["started_at"] = time.time()
    polling_status["last_error"] = None
    proof_stats["listener_running"] = True
    _FOXBOT_MULTICHANNEL_STATE_V1["running"] = True
    _FOXBOT_MULTICHANNEL_STATE_V1["last_error"] = None

    while polling_status["running"]:
        cycle_messages = 0
        cycle_processed = 0
        channels_checked = 0

        try:
            target_result = _foxbot_multichannel_targets_v1()
            targets = target_result.get("targets", [])
            _FOXBOT_MULTICHANNEL_STATE_V1["targets"] = targets
            _FOXBOT_MULTICHANNEL_STATE_V1["unresolved"] = target_result.get("unresolved", [])
            _FOXBOT_MULTICHANNEL_STATE_V1["target_count"] = len(targets)
            _FOXBOT_MULTICHANNEL_STATE_V1["active_creator_count"] = target_result.get(
                "active_creator_count", 0
            )

            for target in targets:
                if not polling_status["running"]:
                    break

                channel_id = target.get("channel_id")
                data = get_recent_blaze_messages(channel_id=channel_id)
                polling_status["checks"] += 1
                polling_status["last_response"] = data
                channels_checked += 1

                if isinstance(data, dict) and data.get("success") is False:
                    _FOXBOT_MULTICHANNEL_STATE_V1["last_error"] = (
                        data.get("error") or data.get("message") or "Blaze message fetch failed."
                    )
                    continue

                rows = extract_rows_from_blaze_response(data)
                cycle_messages += len(rows)
                cycle_processed += _foxbot_process_channel_rows_v1(target, rows)

            polling_status["messages_seen"] = cycle_messages
            polling_status["commands_processed"] += cycle_processed
            proof_stats["blaze_connected"] = bool(
                bot_tokens.get("accessToken") or os.getenv("BLAZE_ACCESS_TOKEN")
            )
            proof_stats["listener_running"] = polling_status["running"]
            proof_stats["messages_checked"] = polling_status["checks"]
            proof_stats["messages_seen"] = cycle_messages
            proof_stats["commands_processed"] += cycle_processed

            _FOXBOT_MULTICHANNEL_STATE_V1["cycles"] += 1
            _FOXBOT_MULTICHANNEL_STATE_V1["channels_checked"] = channels_checked
            _FOXBOT_MULTICHANNEL_STATE_V1["messages_seen"] = cycle_messages
            _FOXBOT_MULTICHANNEL_STATE_V1["commands_processed"] += cycle_processed
            _FOXBOT_MULTICHANNEL_STATE_V1["last_cycle_at"] = datetime.now(
                timezone.utc
            ).isoformat()

        except Exception as error:
            polling_status["last_error"] = str(error)
            _FOXBOT_MULTICHANNEL_STATE_V1["last_error"] = str(error)

        try:
            interval = float(os.getenv("FOXBOT_MULTI_CHANNEL_POLL_SECONDS", "5") or "5")
        except Exception:
            interval = 5.0
        time.sleep(max(2.0, min(interval, 60.0)))

    proof_stats["listener_running"] = False
    _FOXBOT_MULTICHANNEL_STATE_V1["running"] = False


@app.get("/api/foxbot/multichannel/status")
def foxbot_multichannel_status_v1():
    return {"ok": True, **_FOXBOT_MULTICHANNEL_STATE_V1}


@app.get("/api/foxbot/multichannel/targets")
def foxbot_multichannel_targets_v1():
    return _foxbot_multichannel_targets_v1()


# === End FoxBot Blaze Multi-Channel Listener v1 ===

# === FoxBot OAuth Token Priority Fix v1 ===
def send_blaze_chat_message(text: str, channel_id=None):
    """Send with the newest OAuth callback token."""
    import requests

    client_id = str(os.getenv("BLAZE_CLIENT_ID") or "").strip()
    target_channel_id = str(channel_id or os.getenv("BLAZE_CHANNEL_ID") or "").strip()
    access_token, token_source = resolve_blaze_access_token()

    if not client_id or not target_channel_id or not access_token:
        return {
            "success": False,
            "message": "Missing Blaze client ID, target channel ID, or access token.",
            "channel_id": target_channel_id or None,
            "token_source": token_source,
        }

    try:
        response = requests.post(
            "https://api.blaze.stream/v1/chats/messages",
            headers={
                "Authorization": f"Bearer {access_token}",
                "client-id": client_id,
                "Accept": "application/json",
                "content-type": "application/json",
            },
            json={"channelId": target_channel_id, "message": str(text)},
            timeout=20,
        )
        try:
            payload = response.json()
        except Exception:
            payload = {
                "status_code": response.status_code,
                "text": response.text[:500],
            }
        if isinstance(payload, dict):
            payload.setdefault("success", response.ok)
            payload.setdefault("channel_id", target_channel_id)
            payload.setdefault("token_source", token_source)
        return payload
    except Exception as error:
        return {
            "success": False,
            "channel_id": target_channel_id,
            "token_source": token_source,
            "error": str(error),
        }


def get_recent_blaze_messages(channel_id=None):
    """Read chat with the newest OAuth callback token."""
    import requests

    client_id = str(os.getenv("BLAZE_CLIENT_ID") or "").strip()
    target_channel_id = str(channel_id or os.getenv("BLAZE_CHANNEL_ID") or "").strip()
    access_token, token_source = resolve_blaze_access_token()

    if not client_id or not target_channel_id or not access_token:
        return {
            "success": False,
            "message": "Missing Blaze client ID, target channel ID, or access token.",
            "channel_id": target_channel_id or None,
            "token_source": token_source,
        }

    try:
        response = requests.get(
            "https://api.blaze.stream/v1/chats/messages",
            headers={
                "Authorization": f"Bearer {access_token}",
                "client-id": client_id,
                "Accept": "application/json",
            },
            params={"channelId": target_channel_id, "limit": 20},
            timeout=20,
        )
        try:
            payload = response.json()
        except Exception:
            payload = {
                "success": False,
                "status_code": response.status_code,
                "text": response.text[:500],
            }
        if isinstance(payload, dict):
            payload.setdefault("success", response.ok)
            payload.setdefault("channel_id", target_channel_id)
            payload.setdefault("token_source", token_source)
        return payload
    except Exception as error:
        return {
            "success": False,
            "channel_id": target_channel_id,
            "token_source": token_source,
            "error": str(error),
        }


@app.get("/api/foxbot/token-source")
def foxbot_token_source_v2():
    from pathlib import Path

    token, source = resolve_blaze_access_token()
    return {
        "ok": True,
        "has_token": bool(token),
        "source": source,
        "saved_oauth_file_exists": (_foxbot_storage_path_v1("blaze_oauth_tokens.json", "FOXBOT_OAUTH_TOKEN_FILE")).exists(),
    }


@app.get("/api/foxbot/sender-identity")
def foxbot_sender_identity_v1():
    import requests as _requests

    token, source = _foxbot_current_access_token_v2()
    if not token:
        return {"ok": False, "error": "No access token available.", "token_source": source}

    try:
        response = _requests.get(
            "https://api.blaze.stream/v1/users/profile",
            headers={
                "Authorization": f"Bearer {token}",
                "client-id": str(os.getenv("BLAZE_CLIENT_ID") or "").strip(),
                "Accept": "application/json",
            },
            timeout=20,
        )
        try:
            body = response.json()
        except Exception:
            body = {}
    except Exception as error:
        return {"ok": False, "error": str(error), "token_source": source}

    data = body.get("data") if isinstance(body, dict) and isinstance(body.get("data"), dict) else body
    if not isinstance(data, dict):
        data = {}
    return {
        "ok": response.ok,
        "username": data.get("username") or data.get("handle") or data.get("slug"),
        "user_id": data.get("id") or data.get("userId"),
        "token_source": source,
    }


# === End FoxBot OAuth Token Priority Fix v1 ===

# === FoxBot Blaze Subscription Access v1 ===
FOXBOT_SUBSCRIPTION_PRICE_USD_V1 = 5
FOXBOT_SUBSCRIPTION_PROFILE_V1 = "https://blaze.stream/foxbotai"


def _foxbot_item_has_subscriber_role_v1(payload):
    """Detect Blaze subscriber role data in a polling message payload."""
    if isinstance(payload, dict):
        for key, value in payload.items():
            normalized_key = str(key or "").strip().lower()
            if normalized_key in {"issubscriber", "is_subscriber"} and value is True:
                return True
            if normalized_key in {"roles", "badges"} and isinstance(value, list):
                roles = {str(role or "").strip().lower() for role in value}
                if roles.intersection({"subscriber", "sub"}):
                    return True
            if _foxbot_item_has_subscriber_role_v1(value):
                return True
    elif isinstance(payload, list):
        return any(_foxbot_item_has_subscriber_role_v1(item) for item in payload)
    return False


def _foxbot_multichannel_targets_v1():
    try:
        limit = int(os.getenv("FOXBOT_MULTI_CHANNEL_LIMIT", "25") or "25")
    except Exception:
        limit = 25

    access_token, _ = resolve_blaze_access_token()
    return _foxbot_multichannel_service_v1.build_targets(
        client_id=os.getenv("BLAZE_CLIENT_ID", ""),
        access_token=access_token,
        default_channel_id=os.getenv("BLAZE_CHANNEL_ID", ""),
        default_channel_slug=os.getenv("BLAZE_CHANNEL_SLUG", ""),
        limit=limit,
        subscription_channel_id=os.getenv("FOXBOT_SUBSCRIPTION_CHANNEL_ID", ""),
        subscription_channel_slug=os.getenv(
            "FOXBOT_SUBSCRIPTION_CHANNEL_SLUG",
            "foxbotai",
        ),
    )


def _foxbot_process_channel_rows_v1(target, rows):
    channel_id = str(target.get("channel_id") or "").strip()
    channel_slug = str(target.get("channel_slug") or "").strip()
    channel_key = channel_id or channel_slug
    is_subscription_channel = bool(target.get("is_subscription_channel"))
    creator_handle = str(target.get("handle") or "").strip() or _foxbot_events_v1.resolve_owner_handle()

    # Bot Connection Sub-phase D, stage 5: resolved ONCE per channel, right
    # here -- creator_handle above is already scoped to THIS channel (this
    # function runs once per target/channel per poll cycle, called from a
    # per-target loop), so resolving here, not inside handle_auto_chat_event,
    # guarantees an event on channel X's row always resolves to X's
    # identity, never a different channel's or a stale shared value. Falls
    # back to tenant-zero automatically for as long as creator_handle has
    # no blaze_id mapping (today's real state for every channel), so the
    # auto-recognition path stays byte-identical until a real join exists.
    resolved_creator_id = _foxbot_resolve_creator_id_v1(creator_handle=creator_handle)

    # On first discovery of a channel, seed only messages older than the
    # discovery moment (minus a small clock-skew grace window) so a redeploy
    # never replays the backlog. Messages at/after the cutoff are left
    # unseeded and fall through into the normal loop below, so a creator's
    # very first message right after connecting still gets a reply instead
    # of being silently absorbed into the seed set.
    if channel_key not in _FOXBOT_MULTICHANNEL_INITIALIZED_V1:
        try:
            grace_seconds = float(os.getenv("FOXBOT_DISCOVERY_GRACE_SECONDS", "15") or "15")
        except (TypeError, ValueError):
            grace_seconds = 15.0
        discovery_cutoff = time.time() - max(0.0, grace_seconds)

        for item in rows:
            message_id = find_chat_message_id(item)
            if not message_id:
                continue
            created_at = find_chat_message_created_at(item)
            if created_at is None or created_at < discovery_cutoff:
                processed_polling_messages.add(f"{channel_key}:{message_id}")
        _FOXBOT_MULTICHANNEL_INITIALIZED_V1.add(channel_key)

    processed_count = 0
    bot_handle = str(os.getenv("FOXBOT_BLAZE_PROFILE_HANDLE", "foxbotai"))
    bot_handle = bot_handle.strip().lower().lstrip("@")
    # Known bot accounts to exclude from command dispatch and auto-recognition,
    # independent of FOXBOT_BLAZE_PROFILE_HANDLE. blazeian_bot_ai was observed
    # live posting vote-shaped chat text that triggered FoxCoin awards before
    # the identity fix; foxbotai is listed defensively even though bot_handle
    # already covers it, so this still holds if that env var is ever misconfigured.
    known_bot_handles = {"foxbotai", "blazeian_bot_ai"}
    subscription_commands = {
        "!join",
        "!connect",
        "!verify",
        "!access",
        "!profile",
        "!rank",
    }

    for item in reversed(rows):
        message_id = find_chat_message_id(item)
        message_text = find_chat_message_text(item)
        username = _foxbot_resolve_auto_event_username_v1(item, message_text)
        message_key = f"{channel_key}:{message_id}"

        polling_status["last_message"] = item
        if not message_id or message_key in processed_polling_messages:
            continue
        processed_polling_messages.add(message_key)

        if not message_text:
            continue
        clean_username = str(username or "").strip().lstrip("@")
        if clean_username.lower() == bot_handle or clean_username.lower() in known_bot_handles:
            continue

        command = str(message_text).strip().split()[0].lower()

        if is_subscription_channel:
            if command not in subscription_commands:
                access = _foxbot_creator_access_v1.get_access(clean_username)

                if not access.get("has_access"):
                    send_blaze_chat_message(
                        f"@{clean_username}, start your free 7-day FoxBot trial by typing !join.",
                        channel_id=channel_id,
                    )
                    processed_count += 1
                    continue

            if command == "!verify":
                # Preserve the exact subscription-channel payload for diagnosis.
                polling_status["last_subscription_verify_payload"] = item
                polling_status["last_subscription_verify_channel"] = {
                    "channel_id": channel_id,
                    "channel_slug": channel_slug,
                    "is_subscription_channel": is_subscription_channel,
                    "username": clean_username,
                }
                polling_status["last_subscription_verify_detected"] = (
                    _foxbot_item_has_subscriber_role_v1(item)
                )

                _foxbot_events_v1.emit_event(
                    creator_handle, "command", actor=clean_username, detail={"command": command}
                )

                if _foxbot_item_has_subscriber_role_v1(item):
                    access = _foxbot_creator_access_v1.verify_current_subscription(
                        clean_username
                    )
                    foxbot_reply = (
                        f"@{clean_username}, your FoxBot subscription is verified. "
                        "Creator access is active."
                    )
                else:
                    _foxbot_creator_access_v1.request_verification(clean_username)
                    access = _foxbot_creator_access_v1.get_access(clean_username)
                    foxbot_reply = (
                        f"@{clean_username}, FoxBot could not detect an active subscription. "
                        "Subscribe at blaze.stream/foxbotai, then type !verify again here."
                    )

                send_blaze_chat_message(foxbot_reply, channel_id=channel_id)
                _foxbot_events_v1.emit_event(
                    creator_handle, "bot_reply", detail={"in_reply_to": command, "viewer": clean_username}
                )
                polling_status["last_reply"] = foxbot_reply
                polling_status["last_subscription_verification"] = {
                    "handle": clean_username,
                    "verified": access.get("verification_status") == "verified",
                    "status": access.get("status"),
                }
                processed_count += 1
                continue

            _foxbot_events_v1.emit_event(
                creator_handle, "command", actor=clean_username, detail={"command": command}
            )
            foxbot_result = chat(message=message_text, username=clean_username, creator_handle=creator_handle)
            foxbot_reply = foxbot_result.get("response", "FoxBot had no response.")
            if foxbot_reply:
                send_blaze_chat_message(foxbot_reply, channel_id=channel_id)
                _foxbot_events_v1.emit_event(
                    creator_handle, "bot_reply", detail={"in_reply_to": command, "viewer": clean_username}
                )
                polling_status["last_reply"] = foxbot_reply
            processed_count += 1
            continue

        auto_event_result = None
        try:
            auto_event_result = handle_auto_chat_event(
                message_key,
                message_text,
                clean_username,
                item,
                creator_id=resolved_creator_id,
            )
        except Exception as auto_event_error:
            polling_status["last_auto_event_error"] = str(auto_event_error)

        if auto_event_result and auto_event_result.get("ok") and not auto_event_result.get("duplicate"):
            foxbot_reply = auto_event_result.get("message")
            polling_status["last_auto_event"] = auto_event_result

            auto_event = auto_event_result.get("event") or {}

            # === TEMP DIAGNOSTIC — remove once a real payload has been captured ===
            if clean_username == "viewer" and auto_event.get("event_type") in ("vote", "follow"):
                _foxbot_capture_viewer_fallback_debug_v1(auto_event.get("event_type"), item)
            # === End TEMP DIAGNOSTIC ===

            if auto_event.get("event_type") == "follow":
                _foxbot_events_v1.emit_event(
                    creator_handle, "follow", actor=auto_event.get("username"), detail={}
                )

            if foxbot_reply:
                send_blaze_chat_message(foxbot_reply, channel_id=channel_id)
                _foxbot_events_v1.emit_event(
                    creator_handle,
                    "bot_reply",
                    detail={"in_reply_to": auto_event.get("event_type"), "viewer": clean_username},
                )
                processed_count += 1
                proof_stats["last_command"] = message_text
                proof_stats["last_reply"] = foxbot_reply
                proof_stats["last_username"] = clean_username
                proof_stats["last_message"] = message_text
                proof_stats["last_reply_at"] = time.time()
                polling_status["last_reply"] = foxbot_reply
            continue

        if not str(message_text).startswith("!"):
            continue

        _foxbot_events_v1.emit_event(
            creator_handle, "command", actor=clean_username, detail={"command": command}
        )
        foxbot_result = chat(
            message=message_text,
            username=clean_username,
            creator_handle=creator_handle,
        )
        foxbot_reply = foxbot_result.get("response", "FoxBot had no response.")

        if foxbot_reply:
            send_result = send_blaze_chat_message(
                foxbot_reply,
                channel_id=channel_id,
            )

            polling_status["last_multichannel_send"] = {
                "channel_id": channel_id,
                "channel_slug": channel_slug,
                "creator_handle": creator_handle,
                "viewer": clean_username,
                "command": command,
                "reply": foxbot_reply,
                "send_result": send_result,
            }

            send_success = bool(
                isinstance(send_result, dict)
                and send_result.get("success")
            )
            send_message = (
                str(send_result.get("message") or "")
                if isinstance(send_result, dict)
                else "Unknown Blaze send response."
            )
            follower_required = (
                not send_success
                and "only followers can send messages" in send_message.lower()
            )

            if follower_required:
                polling_status["multichannel_connection_health"] = {
                    "chat_ready": False,
                    "setup_issue": "foxbot_not_following_creator",
                    "action_required": (
                        f"Follow @{channel_slug} from the FoxBot Blaze account, "
                        "then test !foxhelp again."
                    ),
                    "channel_id": channel_id,
                    "channel_slug": channel_slug,
                    "creator_handle": creator_handle,
                    "last_error": send_message,
                    "checked_at": time.time(),
                }
            elif send_success:
                polling_status["multichannel_connection_health"] = {
                    "chat_ready": True,
                    "setup_issue": None,
                    "action_required": None,
                    "channel_id": channel_id,
                    "channel_slug": channel_slug,
                    "creator_handle": creator_handle,
                    "last_error": None,
                    "checked_at": time.time(),
                }
            else:
                polling_status["multichannel_connection_health"] = {
                    "chat_ready": False,
                    "setup_issue": "blaze_send_failed",
                    "action_required": "Review the latest Blaze send response.",
                    "channel_id": channel_id,
                    "channel_slug": channel_slug,
                    "creator_handle": creator_handle,
                    "last_error": send_message,
                    "checked_at": time.time(),
                }

            _foxbot_events_v1.emit_event(
                creator_handle, "bot_reply", detail={"in_reply_to": command, "viewer": clean_username}
            )
            proof_stats["last_command"] = message_text
            proof_stats["last_reply"] = foxbot_reply
            proof_stats["last_username"] = clean_username
            proof_stats["last_message"] = message_text
            proof_stats["last_reply_at"] = time.time()
            polling_status["last_reply"] = foxbot_reply

        processed_count += 1

    return processed_count


@app.get("/api/foxbot/subscription/config")
def foxbot_subscription_config_v1():
    return {
        "ok": True,
        "trial_days": _foxbot_creator_access_v1.TRIAL_DAYS,
        "price_usd_monthly": FOXBOT_SUBSCRIPTION_PRICE_USD_V1,
        "blaze_profile": FOXBOT_SUBSCRIPTION_PROFILE_V1,
        "join_command": "!join",
        "verify_command": "!verify",
    }


# === End FoxBot Blaze Subscription Access v1 ===

# === FoxBot Persistent Storage v1 ===
@app.get("/api/foxbot/storage/status")
def foxbot_storage_status_v1():
    from services.storage_paths import storage_status

    return storage_status()


# === End FoxBot Persistent Storage v1 ===

# === FoxBot Studio v2 Read Endpoints v1 ===

@app.get("/api/foxbot/events")
def foxbot_events_read_v1(creator_handle: str = "", limit: int = 20):
    from datetime import timezone

    handle = str(creator_handle or "").strip() or _foxbot_events_v1.resolve_owner_handle()
    rows = _foxbot_events_v1.fetch_events(handle, limit)

    if rows is None:
        return {"ok": False, "error": "events unavailable"}

    now = datetime.now(timezone.utc)
    events = []
    for kind, actor, detail, created_at in rows:
        age_seconds = int((now - created_at).total_seconds()) if created_at else None
        events.append({
            "kind": kind,
            "actor": actor,
            "detail": detail,
            "created_at": created_at.isoformat() if created_at else None,
            "age_seconds": age_seconds,
        })

    return {"ok": True, "creator_handle": handle, "events": events}


@app.get("/api/foxbot/onboarding")
def foxbot_onboarding_read_v1(creator_handle: str = ""):
    handle = str(creator_handle or "").strip() or _foxbot_events_v1.resolve_owner_handle()

    registered = _foxbot_creator_access_v1.is_registered(handle)

    posted = _foxbot_events_v1.event_exists(handle, "bot_reply")
    giveaway_done = _foxbot_events_v1.event_exists(handle, "giveaway_complete")
    dismissal = _foxbot_events_v1.fetch_onboarding_dismissal(handle)

    if posted is None or giveaway_done is None or dismissal is None:
        return {"ok": False, "error": "onboarding data unavailable"}

    # Bot Connection Sub-phase D, stage 2: the first call site migrated
    # off the tenant-zero-only helper. `handle` is already resolved above
    # (line 23768) and used by every other check in this function --
    # this was the one place that ignored it. Falls back to
    # tenant-zero automatically via _foxbot_resolve_creator_id_v1 for as
    # long as `handle` has no blaze_id mapping (today's real state for
    # everyone), so this is byte-identical to the old
    # _tenant_zero_commands() call until a real join exists for this
    # handle.
    command_added = bool(_creator_commands_v1(_foxbot_resolve_creator_id_v1(creator_handle=handle)))
    reward_added = bool(set(reward_shop.keys()) - {"hug", "hype", "flex", "mysterybox", "sponsor"})

    items = [
        {"key": "register", "label": "Register your channel", "done": registered},
        {"key": "posted", "label": "FoxBot posted in your chat", "done": bool(posted)},
        {"key": "command", "label": "Added a custom command", "done": command_added},
        {"key": "reward", "label": "Set up a reward", "done": reward_added},
        {"key": "giveaway", "label": "Run your first giveaway", "done": bool(giveaway_done)},
    ]
    completed = sum(1 for item in items if item["done"])

    return {
        "ok": True,
        "creator_handle": handle,
        "dismissed": dismissal["dismissed"],
        "completed": completed,
        "total": len(items),
        "items": items,
    }


@app.post("/api/foxbot/onboarding/dismiss")
def foxbot_onboarding_dismiss_v1(creator_handle: str = ""):
    handle = str(creator_handle or "").strip() or _foxbot_events_v1.resolve_owner_handle()

    if not _foxbot_creator_access_v1.is_registered(handle):
        return {"ok": False, "error": "register your channel before dismissing the checklist"}

    if _foxbot_events_v1.set_onboarding_dismissed(handle) is None:
        return {"ok": False, "error": "dismiss write failed"}

    return {"ok": True, "creator_handle": handle, "dismissed": True}

# === End FoxBot Studio v2 Read Endpoints v1 ===