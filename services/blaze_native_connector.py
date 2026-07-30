import json
import os
import threading
import time
import urllib.error
import urllib.request

from services.blaze_tokens import resolve_blaze_access_token, resolve_blaze_refresh_token
from services.storage_paths import storage_path
from services import foxbot_events as _foxbot_events_v1

STATE = {
    "running": False,
    "connected": False,
    "session_id": None,
    "last_event": None,
    "last_error": None,
    "events_received": 0,
    "chat_messages_received": 0,
    "replies_sent": 0,
    "started_at": None,
    "stopped_at": None,
    "disconnect_reason": None,
    "subscriptions": [],
    "logs": [],
}

_socket = None
_thread = None
_event_handler = None

EVENT_TYPES = [
    "channel.chat.message",
    "channel.follow",
    "channel.vote",
    "channel.subscribe",
    "channel.subscription.gift",
    "channel.raid",
    "stream.online",
    "stream.offline",
]

def add_log(message):
    try:
        entry = {
            "ts": time.time(),
            "message": str(message)
        }
        STATE.setdefault("logs", []).append(entry)
        STATE["logs"] = STATE["logs"][-80:]
    except Exception:
        pass


def env(name, default=""):
    if name == "BLAZE_ACCESS_TOKEN":
        token, _source = resolve_blaze_access_token()
        return token or default

    if name == "BLAZE_REFRESH_TOKEN":
        return resolve_blaze_refresh_token() or default

    return os.getenv(name) or default

def bot_profile_handle():
    handle = env("FOXBOT_BLAZE_PROFILE_HANDLE", "@FoxBotStudio").strip()
    if handle and not handle.startswith("@"):
        handle = "@" + handle
    return handle or "@FoxBotStudio"

def config_status():
    required_for_live = [
        "BLAZE_CLIENT_ID",
        "BLAZE_ACCESS_TOKEN",
        "BLAZE_CHANNEL_ID",
    ]

    optional = [
        "BLAZE_BOT_USER_ID",
        "BLAZE_APP_ACCESS_TOKEN",
        "BLAZE_REFRESH_TOKEN",
        "BLAZE_SESSION_TOKEN",
        "BLAZE_VISITOR_ID",
        "FOXBOT_BLAZE_PROFILE_HANDLE",
    ]

    return {
        "ok": True,
        "bot_profile_handle": bot_profile_handle(),
        "native_enabled": env("FOXBOT_BLAZE_NATIVE_ENABLED", "false").lower() == "true",
        "auto_send_replies": env("FOXBOT_BLAZE_AUTO_SEND", "false").lower() == "true",
        "missing_required_for_live": [k for k in required_for_live if not env(k)],
        "present_optional": [k for k in optional if env(k)],
        "state": STATE,
    }

def split_message(text, max_len=480):
    text = str(text or "").strip()
    if len(text) <= max_len:
        return [text] if text else []

    chunks = []
    rest = text

    while len(rest) > max_len:
        cut = rest.rfind("\n", 0, max_len)
        if cut < int(max_len * 0.5):
            cut = rest.rfind(" ", 0, max_len)
        if cut < int(max_len * 0.5):
            cut = max_len

        chunks.append(rest[:cut].strip())
        rest = rest[cut:].strip()

    if rest:
        chunks.append(rest)

    return chunks

def _post_json(url, payload, headers, timeout=10):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method="POST"
    )

    with urllib.request.urlopen(req, timeout=timeout) as res:
        raw = res.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(raw) if raw else {}
        except Exception:
            body = {"raw": raw}
        return {"ok": True, "status": res.status, "body": body}

def send_blaze_chat(channel_id, message):
    channel_id = str(channel_id or env("BLAZE_CHANNEL_ID", "")).strip()
    message = str(message or "").strip()

    if not channel_id:
        return {"ok": False, "sent": False, "error": "Missing channel_id or BLAZE_CHANNEL_ID"}

    if not message:
        return {"ok": False, "sent": False, "error": "Missing message"}

    client_id = env("BLAZE_CLIENT_ID", "")
    access_token = env("BLAZE_ACCESS_TOKEN", "")
    app_token = env("BLAZE_APP_ACCESS_TOKEN", "")
    bot_user_id = env("BLAZE_BOT_USER_ID", "") or env("BLAZE_CHANNEL_ID", "")

    if not client_id:
        return {"ok": False, "sent": False, "error": "Missing BLAZE_CLIENT_ID"}

    api_base = env("BLAZE_API_BASE", "https://api.blaze.stream").rstrip("/")
    url = f"{api_base}/v1/chats/messages"

    results = []

    for part in split_message(message):
        sent = False

        if app_token and bot_user_id:
            try:
                result = _post_json(
                    url,
                    {"channelId": channel_id, "message": part, "senderId": bot_user_id},
                    {
                        "authorization": f"Bearer {app_token}",
                        "client-id": client_id,
                        "content-type": "application/json",
                    }
                )
                results.append({"mode": "app_token", "message": part, "result": result})
                STATE["replies_sent"] += 1
                sent = True
            except urllib.error.HTTPError as e:
                results.append({"mode": "app_token", "error": f"{e.code} {e.reason}"})
            except Exception as e:
                results.append({"mode": "app_token", "error": str(e)})

        if not sent:
            if not access_token:
                results.append({"mode": "user_token", "error": "Missing BLAZE_ACCESS_TOKEN"})
                continue

            try:
                result = _post_json(
                    url,
                    {"channelId": channel_id, "message": part},
                    {
                        "authorization": f"Bearer {access_token}",
                        "client-id": client_id,
                        "content-type": "application/json",
                    }
                )
                results.append({"mode": "user_token", "message": part, "result": result})
                STATE["replies_sent"] += 1
            except urllib.error.HTTPError as e:
                details = ""
                try:
                    details = e.read().decode("utf-8", errors="replace")
                except Exception:
                    pass
                results.append({"mode": "user_token", "error": f"{e.code} {e.reason}", "details": details})
            except Exception as e:
                results.append({"mode": "user_token", "error": str(e)})

    ok = any("result" in r for r in results)

    return {
        "ok": ok,
        "sent": ok,
        "channel_id": channel_id,
        "parts": len(split_message(message)),
        "results": results,
    }

def parse_blaze_event(message):
    metadata = message.get("metadata") or {}
    payload = message.get("payload") or message.get("data") or message

    event_type = (
        metadata.get("subscriptionType")
        or metadata.get("type")
        or message.get("type")
        or payload.get("type")
        or ""
    )

    channel_id = (
        payload.get("channelId")
        or payload.get("channel_id")
        or (payload.get("condition") or {}).get("channelId")
        or message.get("channelId")
        or env("BLAZE_CHANNEL_ID", "")
    )

    parsed = {
        "raw_type": event_type,
        "kind": "unknown",
        "channel_id": channel_id,
        "username": None,
        "message": None,
        "amount": None,
        "raw": message,
    }

    if metadata.get("messageType") == "session_welcome":
        parsed["kind"] = "session_welcome"
        parsed["session_id"] = (payload or {}).get("sessionId") or (payload or {}).get("session_id")
        return parsed

    if event_type == "channel.chat.message" or message.get("message") or message.get("text"):
        sender = payload.get("sender") or payload.get("user") or {}
        parsed["kind"] = "chat"
        parsed["username"] = (
            sender.get("username")
            or sender.get("displayName")
            or payload.get("username")
            or payload.get("user")
            or message.get("username")
            or message.get("user")
            or "viewer"
        )

        raw_message = payload.get("message") or message.get("message") or message.get("text") or ""
        if isinstance(raw_message, dict):
            raw_message = raw_message.get("text") or raw_message.get("content") or ""

        parsed["message"] = str(raw_message).strip()
        return parsed

    if event_type == "channel.follow":
        follower = payload.get("follower") or payload.get("user") or {}
        parsed["kind"] = "follow"
        parsed["username"] = follower.get("username") or follower.get("displayName") or payload.get("username")
        return parsed

    if event_type == "channel.vote":
        voter = payload.get("voter") or payload.get("user") or {}
        parsed["kind"] = "vote"
        parsed["username"] = voter.get("username") or voter.get("displayName") or payload.get("username")
        parsed["amount"] = payload.get("amount") or payload.get("votes") or payload.get("value")
        return parsed

    if event_type == "channel.subscribe":
        subber = payload.get("subscriber") or payload.get("user") or {}
        parsed["kind"] = "subscribe"
        parsed["username"] = subber.get("username") or subber.get("displayName") or payload.get("username")
        return parsed

    if event_type == "channel.subscription.gift":
        sender = payload.get("sender") or payload.get("gifter") or payload.get("user") or {}
        parsed["kind"] = "gift_sub"
        parsed["username"] = sender.get("username") or sender.get("displayName") or payload.get("username")
        parsed["amount"] = payload.get("count") or payload.get("amount") or 1
        return parsed

    if event_type == "channel.raid":
        raider = payload.get("raider") or payload.get("user") or {}
        parsed["kind"] = "raid"
        parsed["username"] = raider.get("username") or raider.get("displayName") or payload.get("username")
        return parsed

    if event_type == "stream.online":
        parsed["kind"] = "stream_online"
        return parsed

    if event_type == "stream.offline":
        parsed["kind"] = "stream_offline"
        return parsed

    return parsed

def subscribe_event(event_type, channel_id):
    session_id = STATE.get("session_id")
    access_token = env("BLAZE_ACCESS_TOKEN", "")
    client_id = env("BLAZE_CLIENT_ID", "")

    if not session_id:
        return {"ok": False, "error": "No websocket session_id yet"}

    if not channel_id:
        return {"ok": False, "error": "Missing channel_id"}

    if not access_token or not client_id:
        return {"ok": False, "error": "Missing BLAZE_ACCESS_TOKEN or BLAZE_CLIENT_ID"}

    api_base = env("BLAZE_API_BASE", "https://api.blaze.stream").rstrip("/")
    url = f"{api_base}/v1/events/subscriptions"

    return _post_json(
        url,
        {
            "type": event_type,
            "version": "1",
            "sessionId": session_id,
            "condition": {"channelId": channel_id},
        },
        {
            "authorization": f"Bearer {access_token}",
            "client-id": client_id,
            "content-type": "application/json",
        }
    )

def subscribe_default_events():
    channel_id = env("BLAZE_CHANNEL_ID", "")
    results = []

    for event_type in EVENT_TYPES:
        try:
            results.append({"type": event_type, "result": subscribe_event(event_type, channel_id)})
            time.sleep(0.4)
        except Exception as e:
            results.append({"type": event_type, "error": str(e)})

    return results

def start_listener(event_handler=None):
    global _socket, _thread, _event_handler

    _event_handler = event_handler

    if STATE["running"]:
        return {"ok": True, "already_running": True, "state": STATE}

    status = config_status()
    if status["missing_required_for_live"]:
        STATE["last_error"] = "Missing required env vars: " + ", ".join(status["missing_required_for_live"])
        return {"ok": False, "error": STATE["last_error"], "state": STATE}

    try:
        import socketio
    except Exception:
        STATE["last_error"] = "Missing python-socketio client dependency. Add python-socketio[client] to requirements."
        return {"ok": False, "error": STATE["last_error"], "state": STATE}

    sio = socketio.Client(reconnection=True, logger=False, engineio_logger=False)
    _socket = sio

    @sio.event
    def connect():
        import threading

        STATE["connected"] = True
        STATE["last_error"] = None
        STATE["disconnect_reason"] = None

        socket_sid = None
        try:
            socket_sid = sio.get_sid("/")
        except Exception:
            socket_sid = getattr(sio, "sid", None)

        STATE["socket_sid"] = socket_sid
        add_log(f"socket connected; socket_sid={socket_sid}")

        def _fallback_subscribe_with_socket_sid():
            try:
                if not STATE.get("connected"):
                    add_log("socket sid fallback skipped: socket not connected")
                    return

                existing_session_id = STATE.get("session_id")
                current_socket_sid = STATE.get("socket_sid")

                try:
                    current_socket_sid = current_socket_sid or sio.get_sid("/")
                except Exception:
                    current_socket_sid = current_socket_sid or getattr(sio, "sid", None)

                if existing_session_id and current_socket_sid and existing_session_id == current_socket_sid:
                    add_log("socket sid fallback skipped: session_id already matches current socket")
                    return

                if existing_session_id and current_socket_sid and existing_session_id != current_socket_sid:
                    add_log(f"clearing stale session_id before fallback: {existing_session_id} -> {current_socket_sid}")
                    STATE["session_id"] = None
                    STATE["subscriptions"] = []

                sid = STATE.get("socket_sid")
                try:
                    sid = sid or sio.get_sid("/")
                except Exception:
                    sid = sid or getattr(sio, "sid", None)

                add_log(f"no session_welcome yet; trying socket_sid as session_id: {sid}")

                if not sid:
                    STATE["last_error"] = "socket sid fallback failed: no socket sid available"
                    add_log(STATE["last_error"])
                    return

                STATE["session_id"] = sid

                try:
                    STATE["subscriptions"] = subscribe_default_events()
                    add_log(f"socket sid fallback subscription attempts: {STATE['subscriptions']}")
                except Exception as e:
                    STATE["last_error"] = f"socket sid fallback subscribe failed: {e}"
                    add_log(STATE["last_error"])

            except Exception as e:
                STATE["last_error"] = f"socket sid fallback crashed: {e}"
                add_log(STATE["last_error"])

        threading.Timer(2.0, _fallback_subscribe_with_socket_sid).start()

    @sio.event
    def disconnect(reason=None):
        STATE["connected"] = False
        STATE["disconnect_reason"] = str(reason)
        add_log(f"socket disconnected: {reason}")

    @sio.event
    def connect_error(data):
        STATE["last_error"] = f"connect_error: {data}"
        add_log(STATE["last_error"])

    @sio.on("eventsub")
    def on_eventsub(message, *extra):
        STATE["events_received"] += 1
        STATE["last_event"] = message

        parsed = parse_blaze_event(message)
        if extra:
            add_log(f"eventsub extra args: {extra}")
        add_log(f"eventsub received: {parsed.get('kind')} / {parsed.get('raw_type')}")
        if "_foxbot_maybe_event_thank_you_v1" in globals():
            event_reply_result = _foxbot_maybe_event_thank_you_v1(message)
            if event_reply_result.get("ok") or event_reply_result.get("sent"):
                STATE["last_event_reply_attempt"] = event_reply_result
                add_log(f"event thank-you hook result: {event_reply_result}")

        if parsed.get("kind") == "session_welcome":
            STATE["session_id"] = parsed.get("session_id")
            add_log(f"session_welcome received: {STATE.get('session_id')}")
            if STATE["session_id"]:
                try:
                    STATE["subscriptions"] = subscribe_default_events()
                    add_log(f"subscription attempts: {STATE['subscriptions']}")
                except Exception as e:
                    STATE["last_error"] = f"subscribe failed: {e}"
                    add_log(STATE["last_error"])
            return

        if parsed.get("kind") == "chat":
            STATE["chat_messages_received"] += 1
            if "_foxbot_maybe_role_shoutout_v1" in globals():
                role_result = _foxbot_maybe_role_shoutout_v1(message)
                if role_result.get("ok") or role_result.get("sent"):
                    STATE["last_role_shoutout_attempt"] = role_result
                    add_log(f"role shoutout hook result: {role_result}")
            try:
                reply_result = _foxbot_maybe_live_reply_v2(message)
                STATE["last_reply_attempt"] = reply_result
                add_log(f"live reply hook result: {reply_result}")
            except Exception as e:
                STATE["last_error"] = f"live reply hook failed: {e}"
                add_log(STATE["last_error"])

        if _event_handler:
            try:
                _event_handler(message)
            except Exception as e:
                STATE["last_error"] = f"event handler failed: {e}"
                add_log(STATE["last_error"])

    def run():
        STATE["running"] = True
        _foxbot_events_v1.emit_event(
            _foxbot_events_v1.resolve_owner_handle(), "listener", detail={"state": "connected"}
        )
        STATE["started_at"] = time.time()
        STATE["stopped_at"] = None

        # FoxBot stale session cleanup v1
        STATE["session_id"] = None
        STATE["socket_sid"] = None
        STATE["subscriptions"] = []
        STATE["last_event"] = None
        STATE["disconnect_reason"] = None

        add_log("listener thread starting; cleared stale session state")

        try:
            sio.connect(
                env("BLAZE_WS_URL", "https://blaze.stream"),
                socketio_path=env("BLAZE_WS_PATH", "ws"),
                transports=["websocket"],
                wait_timeout=20,
                headers={
                    "Origin": "https://blaze.stream",
                    "User-Agent": "FoxBotAI/1.0",
                },
            )
            add_log("sio.connect returned; waiting for events")
            sio.wait()
            if not STATE.get("last_error"):
                STATE["last_error"] = "socket wait ended without exception"
                add_log(STATE["last_error"])
        except Exception as e:
            STATE["last_error"] = str(e)
            add_log(f"listener exception: {e}")
        finally:
            STATE["running"] = False
            STATE["connected"] = False
            STATE["stopped_at"] = time.time()
            add_log("listener thread stopped")
            _foxbot_events_v1.emit_event(
                _foxbot_events_v1.resolve_owner_handle(),
                "listener",
                detail={"state": "disconnected", "reason": STATE.get("last_error")},
            )

    _thread = threading.Thread(target=run, daemon=True)
    _thread.start()

    return {"ok": True, "started": True, "state": STATE}

def stop_listener():
    global _socket

    if _socket:
        try:
            _socket.disconnect()
        except Exception:
            pass

    STATE["running"] = False
    STATE["connected"] = False
    _foxbot_events_v1.emit_event(
        _foxbot_events_v1.resolve_owner_handle(), "listener", detail={"state": "disconnected"}
    )

    return {"ok": True, "stopped": True, "state": STATE}

# === FoxBot Safe Blaze Diagnostics Override v1 ===
def _fb_json_http_diag(method, url, payload=None, headers=None, timeout=15):
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


def _fb_blaze_headers_diag(token=None):
    token = token or env("BLAZE_ACCESS_TOKEN", "")
    return {
        "authorization": f"Bearer {token}",
        "client-id": env("BLAZE_CLIENT_ID", ""),
        "content-type": "application/json",
        "accept": "application/json",
        "origin": "https://blaze.stream",
        "user-agent": "FoxBotAI/1.0"
    }


def _fb_save_tokens_diag(tokens):
    import json
    from datetime import datetime, timezone

    if not isinstance(tokens, dict):
        return {}

    path = storage_path("blaze_oauth_tokens.json", "FOXBOT_OAUTH_TOKEN_FILE")

    existing = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8") or "{}")
        except Exception:
            existing = {}

    existing.update(tokens)
    existing["saved_at"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    return existing


def _fb_refresh_token_diag():
    payload = {
        "clientId": env("BLAZE_CLIENT_ID", ""),
        "clientSecret": env("BLAZE_CLIENT_SECRET", ""),
        "refreshToken": env("BLAZE_REFRESH_TOKEN", "")
    }

    if not payload["clientId"] or not payload["clientSecret"] or not payload["refreshToken"]:
        return {"ok": False, "error": "Missing client ID, client secret, or refresh token."}

    res = _fb_json_http_diag(
        "POST",
        "https://blaze.stream/bapi/oauth2/refresh",
        payload,
        {
            "content-type": "application/json",
            "accept": "application/json",
            "origin": "https://blaze.stream",
            "user-agent": "FoxBotAI/1.0"
        }
    )

    if res.get("ok"):
        body = res.get("body") or {}
        _fb_save_tokens_diag(body)
        access = body.get("accessToken") or body.get("access_token") or env("BLAZE_ACCESS_TOKEN", "")
        return {
            "ok": True,
            "has_access_token": bool(access),
            "accessToken": access
        }

    return res


def _fb_resolve_channel_diag():
    import urllib.parse

    slug = (
        env("BLAZE_CHANNEL_SLUG", "")
        or env("FOXBOT_BLAZE_PROFILE_HANDLE", "")
        or ""
    ).strip().lstrip("@")

    if not slug:
        return {"ok": False, "error": "No BLAZE_CHANNEL_SLUG or FOXBOT_BLAZE_PROFILE_HANDLE set."}

    url = "https://api.blaze.stream/v1/channels?slug[]=" + urllib.parse.quote(slug) + "&type=all"
    res = _fb_json_http_diag("GET", url, None, _fb_blaze_headers_diag())
    res["slug"] = slug

    try:
        rows = (((res.get("body") or {}).get("data") or {}).get("rows") or [])
        res["first_channel_id"] = rows[0].get("id") if rows else None
        res["first_channel_user_id"] = rows[0].get("userId") if rows else None
        res["row_count"] = len(rows)
    except Exception as e:
        res["parse_error"] = str(e)

    return res


def blaze_native_diagnostics_v1():
    profile = _fb_json_http_diag(
        "GET",
        "https://api.blaze.stream/v1/users/profile",
        None,
        _fb_blaze_headers_diag()
    )

    return {
        "ok": True,
        "configured_channel_id": env("BLAZE_CHANNEL_ID", ""),
        "configured_channel_slug": env("BLAZE_CHANNEL_SLUG", ""),
        "bot_profile_handle": env("FOXBOT_BLAZE_PROFILE_HANDLE", ""),
        "has_access_token": bool(env("BLAZE_ACCESS_TOKEN", "")),
        "has_refresh_token": bool(env("BLAZE_REFRESH_TOKEN", "")),
        "profile": profile,
        "channel_by_slug": _fb_resolve_channel_diag(),
        "state": STATE
    }


def subscribe_default_events():
    session_id = STATE.get("session_id") or STATE.get("socket_sid")
    channel_id = env("BLAZE_CHANNEL_ID", "").strip()

    if not session_id:
        return [{"ok": False, "error": "Missing session_id"}]

    if not channel_id:
        resolved = _fb_resolve_channel_diag()
        channel_id = str(resolved.get("first_channel_id") or "").strip()
        add_log(f"Resolved channel_id from slug: {channel_id}")

    if not channel_id:
        return [{"ok": False, "error": "Missing BLAZE_CHANNEL_ID and could not resolve channel."}]

    refreshed = _fb_refresh_token_diag()
    token = env("BLAZE_ACCESS_TOKEN", "")

    if refreshed.get("ok") and refreshed.get("accessToken"):
        token = refreshed.get("accessToken")
        add_log("Refreshed token before subscribe.")
    else:
        add_log(f"Token refresh before subscribe failed or skipped: {refreshed}")

    event_types = [
        "channel.chat.message",
        "channel.follow",
        "channel.vote",
        "channel.subscribe",
        "channel.subscription.gift",
        "channel.raid",
        "stream.online",
        "stream.offline"
    ]

    results = []

    for event_type in event_types:
        payload = {
            "type": event_type,
            "version": "1",
            "sessionId": session_id,
            "condition": {
                "channelId": channel_id
            }
        }

        res = _fb_json_http_diag(
            "POST",
            "https://api.blaze.stream/v1/events/subscriptions",
            payload,
            _fb_blaze_headers_diag(token)
        )

        results.append({
            "type": event_type,
            "ok": bool(res.get("ok")),
            "status": res.get("status"),
            "reason": res.get("reason"),
            "body": res.get("body"),
            "error": res.get("error"),
            "channelId": channel_id,
            "sessionId": session_id
        })

    return results
# === End FoxBot Safe Blaze Diagnostics Override v1 ===

# === FoxBot Blaze Live Reply Safety v1 ===
_LIVE_REPLY_LAST_SEEN = {}
_LIVE_REPLY_LAST_SENT = {}

def _foxbot_bool_env_v1(name, default=False):
    if str(name or "").strip() == "FOXBOT_BLAZE_AUTO_SEND":
        try:
            override = LIVE_CONTROL_OVERRIDE.get("auto_send") if "LIVE_CONTROL_OVERRIDE" in globals() else None
            if override is not None:
                return bool(override)
        except Exception:
            pass

    value = str(env(name, "")).strip().lower()
    if not value:
        return default
    return value in ("1", "true", "yes", "on")

def _foxbot_extract_chat_v1(message):
    if not isinstance(message, dict):
        return None

    metadata = message.get("metadata") or {}
    payload = message.get("payload") or {}
    sender = payload.get("sender") or {}

    subscription_type = metadata.get("subscriptionType") or metadata.get("subscription_type")
    message_type = metadata.get("messageType") or metadata.get("message_type")

    is_chat = (
        subscription_type == "channel.chat.message"
        or payload.get("message") is not None
        or message_type == "notification"
    )

    if not is_chat:
        return None

    username = str(sender.get("username") or sender.get("displayName") or "viewer").strip().lstrip("@")
    display_name = str(sender.get("displayName") or username or "viewer").strip()
    body = str(payload.get("message") or payload.get("text") or "").strip()
    message_id = str(payload.get("messageId") or payload.get("id") or "").strip()

    if not body:
        return None

    return {
        "username": username,
        "display_name": display_name,
        "message": body,
        "message_id": message_id,
        "sender": sender,
        "payload": payload,
    }

def _foxbot_should_ignore_chat_v1(chat):
    import time

    username = str((chat or {}).get("username") or "").strip().lower().lstrip("@")
    bot_handle = str(env("FOXBOT_BLAZE_PROFILE_HANDLE", "@foxbotai")).strip().lower().lstrip("@")
    body = str((chat or {}).get("message") or "").strip()
    message_id = str((chat or {}).get("message_id") or "").strip()

    if not body:
        return True, "empty message"

    if username == bot_handle:
        return True, "ignored bot self-message"

    allowed_prefixes = tuple(x.strip() for x in env("FOXBOT_LIVE_COMMAND_PREFIXES", "!,/").split(",") if x.strip())
    if not body.startswith(allowed_prefixes):
        return True, "not a command"

    allowed_commands = set(
        x.strip().lower()
        for x in env("FOXBOT_LIVE_ALLOWED_COMMANDS", "!connect,!profile,!rank,!disconnect,!foxhelp").split(",")
        if x.strip()
    )

    first_word = body.split()[0].lower()
    if first_word not in allowed_commands:
        return True, f"command not allowed: {first_word}"

    if message_id:
        if message_id in _LIVE_REPLY_LAST_SEEN:
            return True, "duplicate message id"
        _LIVE_REPLY_LAST_SEEN[message_id] = time.time()

    now = time.time()
    cooldown = float(env("FOXBOT_LIVE_REPLY_COOLDOWN_SECONDS", "8") or "8")
    key = f"{username}:{first_word}"
    last = _LIVE_REPLY_LAST_SENT.get(key, 0)

    if now - last < cooldown:
        return True, f"cooldown active for {key}"

    _LIVE_REPLY_LAST_SENT[key] = now

    return False, "ok"

def _foxbot_safe_live_reply_preview_v1(message):
    chat = _foxbot_extract_chat_v1(message)
    if not chat:
        return {"ok": False, "reason": "not chat"}

    ignore, reason = _foxbot_should_ignore_chat_v1(chat)

    return {
        "ok": not ignore,
        "reason": reason,
        "username": chat.get("username"),
        "message": chat.get("message"),
        "auto_send_enabled": _foxbot_bool_env_v1("FOXBOT_BLAZE_AUTO_SEND", False),
    }
# === End FoxBot Blaze Live Reply Safety v1 ===

# === FoxBot Blaze Live Auto Reply v2 ===
def _foxbot_live_http_json_v2(method, url, payload=None, headers=None, timeout=15):
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


def _foxbot_live_app_token_v2():
    client_id = env("BLAZE_CLIENT_ID", "").strip()
    client_secret = env("BLAZE_CLIENT_SECRET", "").strip()

    if not client_id or not client_secret:
        return {"ok": False, "error": "Missing BLAZE_CLIENT_ID or BLAZE_CLIENT_SECRET"}

    res = _foxbot_live_http_json_v2(
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
        "access_token": token,
        "status": res.get("status"),
        "reason": res.get("reason"),
        "body": {"has_accessToken": bool(token), "success": body.get("success")}
    }


def _foxbot_live_profile_user_id_v2():
    user_id = (env("BLAZE_BOT_USER_ID", "") or env("FOXBOT_BLAZE_USER_ID", "")).strip()
    if user_id:
        return {"ok": True, "user_id": user_id, "source": "env"}

    token = env("BLAZE_ACCESS_TOKEN", "").strip()
    client_id = env("BLAZE_CLIENT_ID", "").strip()

    if not token or not client_id:
        return {"ok": False, "error": "Missing BLAZE_ACCESS_TOKEN or BLAZE_CLIENT_ID"}

    res = _foxbot_live_http_json_v2(
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
        "user_id": user_id,
        "source": "profile",
        "status": res.get("status"),
        "body": res.get("body")
    }



# === FoxBot Native Outgoing Shop Emoji Fix v1 ===
def _foxbot_fix_outgoing_shop_text_v1(text):
    s = str(text or "")

    if "FoxBot Reward Shop" in s:
        return (
            "FoxBot Reward Shop: "
            "hug \U0001F917 10 FoxCoins | "
            "hype \U0001F525 25 FoxCoins | "
            "flex \U0001F4AA 50 FoxCoins | "
            "mysterybox \U0001F381 75 FoxCoins | "
            "sponsor \U0001F48E 150 FoxCoins | "
            "Use !redeem rewardname"
        )

    return s
# === End FoxBot Native Outgoing Shop Emoji Fix v1 ===

def _foxbot_live_send_chat_v2(message):
    message = _foxbot_fix_outgoing_shop_text_v1(message)
    channel_id = env("BLAZE_CHANNEL_ID", "").strip()
    client_id = env("BLAZE_CLIENT_ID", "").strip()

    if not channel_id:
        return {"ok": False, "sent": False, "error": "Missing BLAZE_CHANNEL_ID"}

    if not client_id:
        return {"ok": False, "sent": False, "error": "Missing BLAZE_CLIENT_ID"}

    message = str(message or "").strip()
    if not message:
        return {"ok": False, "sent": False, "error": "Empty message"}

    if len(message) > 450:
        message = message[:447] + "..."

    app = _foxbot_live_app_token_v2()
    profile = _foxbot_live_profile_user_id_v2()

    if not app.get("ok") or not profile.get("ok"):
        return {
            "ok": False,
            "sent": False,
            "error": "Could not get app token or bot profile user id",
            "app": app,
            "profile": profile
        }

    res = _foxbot_live_http_json_v2(
        "POST",
        "https://api.blaze.stream/v1/chats/messages",
        {
            "channelId": channel_id,
            "message": message,
            "senderId": profile.get("user_id")
        },
        {
            "authorization": f"Bearer {app.get('access_token')}",
            "client-id": client_id,
            "content-type": "application/json",
            "accept": "application/json",
            "origin": "https://blaze.stream",
            "user-agent": "FoxBotAI/1.0"
        }
    )

    return {
        "ok": bool(res.get("ok")),
        "sent": bool(res.get("ok")),
        "status": res.get("status"),
        "reason": res.get("reason"),
        "body": res.get("body"),
        "mode": "app_token_sender_id"
    }


def _foxbot_live_command_reply_v2(chat):
    username = str((chat or {}).get("username") or "viewer").strip().lstrip("@")
    message = str((chat or {}).get("message") or "").strip()
    command = message.split()[0].lower() if message else ""

    # Channel owner bypass: the owner should not need to pass the follower gate.
    try:
        owner_reply = _foxbot_owner_direct_reply_v1(chat)
        if owner_reply:
            return owner_reply
    except Exception as e:
        add_log(f"owner bypass failed: {e}")

    # Try the real FoxBot Connect command engine first.
    try:
        import sys
        app_mod = sys.modules.get("app")

        fn = getattr(app_mod, "_foxbot_connect_process_command_v1", None) if app_mod else None

        if fn:
            attempts = [
                (message, username),
                (username, message),
                (message, username, "blaze"),
                (username, message, "blaze")
            ]

            for args in attempts:
                try:
                    result = fn(*args)

                    if isinstance(result, dict):
                        reply = (
                            result.get("reply")
                            or result.get("message")
                            or result.get("response")
                            or ""
                        )
                        if reply:
                            return str(reply)

                    if isinstance(result, str) and result.strip():
                        return result.strip()

                except TypeError:
                    continue
                except Exception as e:
                    add_log(f"command engine attempt failed: {e}")

    except Exception as e:
        add_log(f"command engine import failed: {e}")

    # Safe fallback replies.
    if command == "!connect":
        return f"🦊 @{username} you are connected to FoxBot! Try !profile or !rank next."

    if command == "!profile":
        return f"🦊 @{username} FoxBot profile is active. More profile stats are coming soon."

    if command == "!rank":
        return f"🏆 @{username} FoxBot rank tracking is live. Keep showing up in chat!"

    if command == "!disconnect":
        return f"🦊 @{username} FoxBot disconnect request received."

    if command == "!foxhelp":
        return "🦊 FoxBot commands: !connect, !profile, !rank, !disconnect, !foxhelp"

    return ""


def _foxbot_maybe_live_reply_v2(message):
    try:
        chat = _foxbot_extract_chat_v1(message) if "_foxbot_extract_chat_v1" in globals() else None

        if not chat:
            return {"ok": False, "reason": "not chat"}

        ignore, reason = _foxbot_should_ignore_chat_v1(chat)

        preview = {
            "ok": not ignore,
            "reason": reason,
            "username": chat.get("username"),
            "message": chat.get("message"),
            "auto_send_enabled": _foxbot_bool_env_v1("FOXBOT_BLAZE_AUTO_SEND", False)
        }

        STATE["last_reply_preview"] = preview

        if ignore:
            add_log(f"live reply skipped: {reason}")
            return preview

        chat_message = str(chat.get("message") or "").strip()
        if chat_message.startswith("!"):
            _foxbot_events_v1.emit_event(
                _foxbot_events_v1.resolve_owner_handle(),
                "command",
                actor=chat.get("username"),
                detail={"command": chat_message.split()[0].lower()},
            )

        reply = _foxbot_live_command_reply_v2(chat)
        preview["reply"] = reply

        if not reply:
            preview["ok"] = False
            preview["reason"] = "no reply generated"
            add_log("live reply skipped: no reply generated")
            return preview

        if not _foxbot_bool_env_v1("FOXBOT_BLAZE_AUTO_SEND", False):
            preview["sent"] = False
            preview["reason"] = "auto-send disabled dry run"
            add_log(f"live reply dry run: {reply}")
            return preview

        send_result = _foxbot_live_send_chat_v2(reply)
        preview["send_result"] = send_result
        preview["sent"] = bool(send_result.get("sent"))

        STATE["last_reply_attempt"] = preview

        if send_result.get("sent"):
            STATE["replies_sent"] = int(STATE.get("replies_sent") or 0) + 1
            add_log(f"live reply sent: {reply}")
            _foxbot_events_v1.emit_event(
                _foxbot_events_v1.resolve_owner_handle(),
                "bot_reply",
                detail={"in_reply_to": chat_message, "viewer": chat.get("username")},
            )
        else:
            STATE["last_error"] = f"live reply send failed: {send_result}"
            add_log(STATE["last_error"])

        return preview

    except Exception as e:
        STATE["last_error"] = f"live reply handler failed: {e}"
        add_log(STATE["last_error"])
        return {"ok": False, "error": str(e)}
# === End FoxBot Blaze Live Auto Reply v2 ===

# === FoxBot Blaze Owner Bypass v1 ===
def _foxbot_owner_handles_v1():
    raw = ",".join([
        env("BLAZE_CHANNEL_SLUG", ""),
        env("CREATOR_NAME", ""),
        env("FOXBOT_OWNER_HANDLES", ""),
        env("ADMIN_USERNAMES", ""),
        "crypt0k1ng96"
    ])

    handles = set()

    for item in raw.replace(";", ",").split(","):
        item = str(item or "").strip().lower().lstrip("@")
        if item:
            handles.add(item)

    return handles


def _foxbot_is_channel_owner_v1(chat):
    try:
        sender = (chat or {}).get("sender") or {}
        if sender.get("isOwner") is True:
            return True
    except Exception:
        pass

    username = str((chat or {}).get("username") or "").strip().lower().lstrip("@")
    return username in _foxbot_owner_handles_v1()


def _foxbot_owner_direct_reply_v1(chat):
    username = str((chat or {}).get("username") or "creator").strip().lstrip("@")
    message = str((chat or {}).get("message") or "").strip()
    command = message.split()[0].lower() if message else ""

    if not _foxbot_is_channel_owner_v1(chat):
        return ""

    if command == "!connect":
        return f"🦊 @{username} is connected as the FoxBot channel owner! Owner access unlocked. Use !profile next."

    if command == "!profile":
        return f"🦊 @{username} FoxBot Owner Profile | Status: connected | Role: Channel Owner | Commands: !connect, !profile, !rank, !foxhelp"

    if command == "!rank":
        return f"🏆 @{username} FoxBot Rank | Channel Owner | Top Fox unlocked."

    if command == "!foxhelp":
        return "🦊 FoxBot commands: !connect, !profile, !rank, !disconnect, !foxhelp"

    return ""
# === End FoxBot Blaze Owner Bypass v1 ===

# === FoxBot Blaze Event Thank Yous v1 ===
_EVENT_THANK_YOU_SEEN = {}
_EVENT_THANK_YOU_COOLDOWNS = {}

def _foxbot_event_actor_name_v1(payload):
    if not isinstance(payload, dict):
        return "viewer"

    candidates = [
        payload.get("sender"),
        payload.get("user"),
        payload.get("viewer"),
        payload.get("follower"),
        payload.get("subscriber"),
        payload.get("gifter"),
        payload.get("raider"),
        payload.get("from"),
    ]

    for obj in candidates:
        if isinstance(obj, dict):
            name = (
                obj.get("username")
                or obj.get("displayName")
                or obj.get("display_name")
                or obj.get("name")
            )
            if name:
                return str(name).strip().lstrip("@")

    for key in (
        "username",
        "displayName",
        "display_name",
        "viewerUsername",
        "followerUsername",
        "subscriberUsername",
        "gifterUsername",
        "raiderUsername",
    ):
        value = payload.get(key)
        if value:
            return str(value).strip().lstrip("@")

    return "viewer"


def _foxbot_event_amount_v1(payload):
    if not isinstance(payload, dict):
        return ""

    for key in ("amount", "count", "quantity", "giftCount", "viewerCount", "raidCount"):
        value = payload.get(key)
        if value not in (None, "", 0):
            return str(value).strip()

    return ""


def _foxbot_event_id_v1(event_type, payload):
    if not isinstance(payload, dict):
        return ""

    for key in (
        "eventId",
        "id",
        "messageId",
        "followId",
        "subscriptionId",
        "voteId",
        "raidId",
        "createdAt",
    ):
        value = payload.get(key)
        if value:
            return f"{event_type}:{value}"

    actor = _foxbot_event_actor_name_v1(payload)
    return f"{event_type}:{actor}:{payload.get('createdAt', '')}"


def _foxbot_event_thank_you_reply_v1(event_type, payload):
    username = _foxbot_event_actor_name_v1(payload)
    amount = _foxbot_event_amount_v1(payload)

    event_type = str(event_type or "").strip().lower()

    if event_type == "channel.follow":
        return f"🦊 Welcome @{username}! Thanks for following the FoxBot-powered stream!"

    if event_type == "channel.vote":
        return f"🔥 Thanks for the vote, @{username}! FoxBot sees you."

    if event_type == "channel.subscribe":
        return f"🚀 Massive thanks for subscribing, @{username}! You just powered up the Fox Den."

    if event_type == "channel.subscription.gift":
        if amount:
            return f"🎁 @{username} just gifted {amount} sub(s)! Huge Fox Den energy!"
        return f"🎁 @{username} just gifted a sub! Huge Fox Den energy!"

    if event_type == "channel.raid":
        if amount:
            return f"⚔️ @{username} just raided with {amount} viewer(s)! Welcome raiders!"
        return f"⚔️ @{username} just raided the stream! Welcome raiders!"

    if event_type in ("channel.tip", "channel.tipping", "channel.donation"):
        if amount:
            return f"💰 Huge thanks @{username} for the {amount} tip! FoxBot appreciates you!"
        return f"💰 Huge thanks for the tip, @{username}! FoxBot appreciates you!"

    return ""


def _foxbot_maybe_event_thank_you_v1(message):
    import time

    try:
        if not isinstance(message, dict):
            return {"ok": False, "reason": "message not dict"}

        metadata = message.get("metadata") or {}
        payload = message.get("payload") or {}

        event_type = (
            metadata.get("subscriptionType")
            or metadata.get("subscription_type")
            or payload.get("type")
            or ""
        )

        event_type = str(event_type or "").strip().lower()

        supported = {
            "channel.follow",
            "channel.vote",
            "channel.subscribe",
            "channel.subscription.gift",
            "channel.raid",
            "channel.tip",
            "channel.tipping",
            "channel.donation",
        }

        if event_type not in supported:
            return {"ok": False, "reason": f"unsupported event: {event_type}"}

        actor = _foxbot_event_actor_name_v1(payload).lower().lstrip("@")
        bot_handle = str(env("FOXBOT_BLAZE_PROFILE_HANDLE", "@foxbotai")).strip().lower().lstrip("@")

        if actor == bot_handle:
            return {"ok": False, "reason": "ignored bot self-event"}

        event_id = _foxbot_event_id_v1(event_type, payload)
        now = time.time()

        if event_id:
            if event_id in _EVENT_THANK_YOU_SEEN:
                return {"ok": False, "reason": "duplicate event"}
            _EVENT_THANK_YOU_SEEN[event_id] = now

        cooldown_seconds = float(env("FOXBOT_EVENT_REPLY_COOLDOWN_SECONDS", "20") or "20")
        cooldown_key = f"{event_type}:{actor}"
        last_sent = _EVENT_THANK_YOU_COOLDOWNS.get(cooldown_key, 0)

        if now - last_sent < cooldown_seconds:
            return {"ok": False, "reason": f"cooldown active: {cooldown_key}"}

        reply = _foxbot_event_thank_you_reply_v1(event_type, payload)

        if not reply:
            return {"ok": False, "reason": "no reply generated"}

        result = {
            "ok": True,
            "event_type": event_type,
            "actor": actor,
            "reply": reply,
            "auto_send_enabled": _foxbot_bool_env_v1("FOXBOT_BLAZE_AUTO_SEND", False),
        }

        STATE["last_event_reply_preview"] = result

        if not _foxbot_bool_env_v1("FOXBOT_BLAZE_AUTO_SEND", False):
            result["sent"] = False
            result["reason"] = "auto-send disabled dry run"
            add_log(f"event thank-you dry run: {reply}")
            return result

        send_result = _foxbot_live_send_chat_v2(reply)
        result["send_result"] = send_result
        result["sent"] = bool(send_result.get("sent"))

        STATE["last_event_reply_attempt"] = result

        if send_result.get("sent"):
            _EVENT_THANK_YOU_COOLDOWNS[cooldown_key] = now
            STATE["replies_sent"] = int(STATE.get("replies_sent") or 0) + 1
            add_log(f"event thank-you sent: {event_type} / {actor}")
        else:
            STATE["last_error"] = f"event thank-you send failed: {send_result}"
            add_log(STATE["last_error"])

        return result

    except Exception as e:
        STATE["last_error"] = f"event thank-you failed: {e}"
        add_log(STATE["last_error"])
        return {"ok": False, "error": str(e)}
# === End FoxBot Blaze Event Thank Yous v1 ===

# === FoxBot Blaze Role Shoutouts v1 ===
_ROLE_SHOUTOUT_COOLDOWNS = {}

def _foxbot_chat_roles_v1(chat):
    sender = (chat or {}).get("sender") or {}
    roles = sender.get("roles") or []

    normalized = set()
    for role in roles:
        role = str(role or "").strip().lower()
        if role:
            normalized.add(role)

    if sender.get("isSubscriber") is True:
        normalized.add("subscriber")

    if sender.get("isOwner") is True:
        normalized.add("owner")

    if sender.get("isFollower") is True:
        normalized.add("follower")

    return normalized


def _foxbot_role_shoutout_reply_v1(chat):
    username = str((chat or {}).get("username") or "viewer").strip().lstrip("@")
    roles = _foxbot_chat_roles_v1(chat)

    if "owner" in roles:
        return f"👑 The channel owner @{username} is in the Fox Den!"

    if "moderator" in roles or "mod" in roles:
        return f"🛡️ Mod check! @{username} is helping keep the Fox Den locked in."

    if "og" in roles:
        return f"🔥 OG spotted! @{username} has been with the Fox Den from the jump."

    if "vip" in roles:
        return f"⭐ VIP in chat! Much love to @{username}."

    if "subscriber" in roles or "sub" in roles:
        return f"🚀 Subscriber spotted! Thanks for powering up the stream, @{username}."

    return ""


def _foxbot_maybe_role_shoutout_v1(message):
    import time

    try:
        chat = _foxbot_extract_chat_v1(message) if "_foxbot_extract_chat_v1" in globals() else None

        if not chat:
            return {"ok": False, "reason": "not chat"}

        username = str(chat.get("username") or "").strip().lower().lstrip("@")
        bot_handle = str(env("FOXBOT_BLAZE_PROFILE_HANDLE", "@foxbotai")).strip().lower().lstrip("@")
        body = str(chat.get("message") or "").strip()

        if not body:
            return {"ok": False, "reason": "empty message"}

        if username == bot_handle:
            return {"ok": False, "reason": "ignored bot self-message"}

        # Commands already get command replies. Do not double-send role shoutouts on commands.
        if body.startswith(("!", "/")):
            return {"ok": False, "reason": "skipped command message"}

        reply = _foxbot_role_shoutout_reply_v1(chat)
        if not reply:
            return {"ok": False, "reason": "no shoutout role"}

        now = time.time()
        cooldown_seconds = float(env("FOXBOT_ROLE_SHOUTOUT_COOLDOWN_SECONDS", "1800") or "1800")
        cooldown_key = username

        last = _ROLE_SHOUTOUT_COOLDOWNS.get(cooldown_key, 0)
        if now - last < cooldown_seconds:
            return {"ok": False, "reason": f"role shoutout cooldown active for {username}"}

        result = {
            "ok": True,
            "username": username,
            "reply": reply,
            "auto_send_enabled": _foxbot_bool_env_v1("FOXBOT_BLAZE_AUTO_SEND", False),
        }

        STATE["last_role_shoutout_preview"] = result

        if not _foxbot_bool_env_v1("FOXBOT_BLAZE_AUTO_SEND", False):
            result["sent"] = False
            result["reason"] = "auto-send disabled dry run"
            add_log(f"role shoutout dry run: {reply}")
            return result

        send_result = _foxbot_live_send_chat_v2(reply)
        result["send_result"] = send_result
        result["sent"] = bool(send_result.get("sent"))

        STATE["last_role_shoutout_attempt"] = result

        if send_result.get("sent"):
            _ROLE_SHOUTOUT_COOLDOWNS[cooldown_key] = now
            STATE["replies_sent"] = int(STATE.get("replies_sent") or 0) + 1
            add_log(f"role shoutout sent: {username}")
        else:
            STATE["last_error"] = f"role shoutout send failed: {send_result}"
            add_log(STATE["last_error"])

        return result

    except Exception as e:
        STATE["last_error"] = f"role shoutout failed: {e}"
        add_log(STATE["last_error"])
        return {"ok": False, "error": str(e)}
# === End FoxBot Blaze Role Shoutouts v1 ===

# === FoxBot Live Control Override v1 ===
LIVE_CONTROL_OVERRIDE = {
    "auto_send": None,
    "reason": "env default",
    "updated_at": None,
}

def foxbot_live_control_set_v1(auto_send=None, reason="manual"):
    import time

    if auto_send is None:
        LIVE_CONTROL_OVERRIDE["auto_send"] = None
        LIVE_CONTROL_OVERRIDE["reason"] = "env default"
    else:
        LIVE_CONTROL_OVERRIDE["auto_send"] = bool(auto_send)
        LIVE_CONTROL_OVERRIDE["reason"] = str(reason or "manual")

    LIVE_CONTROL_OVERRIDE["updated_at"] = time.time()
    add_log(f"live control override updated: {LIVE_CONTROL_OVERRIDE}")

    return foxbot_live_control_status_v1()


def foxbot_live_control_status_v1():
    env_value = env("FOXBOT_BLAZE_AUTO_SEND", "")
    override = LIVE_CONTROL_OVERRIDE.get("auto_send")

    if override is None:
        effective = _foxbot_bool_env_v1("FOXBOT_BLAZE_AUTO_SEND", False) if "_foxbot_bool_env_v1" in globals() else False
        source = "env"
    else:
        effective = bool(override)
        source = "runtime_override"

    return {
        "ok": True,
        "auto_send_effective": effective,
        "source": source,
        "env_value": env_value,
        "override": LIVE_CONTROL_OVERRIDE,
        "running": STATE.get("running"),
        "connected": STATE.get("connected"),
        "session_id": STATE.get("session_id"),
        "events_received": STATE.get("events_received"),
        "chat_messages_received": STATE.get("chat_messages_received"),
        "replies_sent": STATE.get("replies_sent"),
        "last_error": STATE.get("last_error"),
        "last_reply_attempt": STATE.get("last_reply_attempt"),
        "last_event_reply_attempt": STATE.get("last_event_reply_attempt"),
        "last_role_shoutout_attempt": STATE.get("last_role_shoutout_attempt"),
    }
# === End FoxBot Live Control Override v1 ===

