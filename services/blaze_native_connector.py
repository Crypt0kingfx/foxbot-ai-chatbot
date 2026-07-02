import json
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

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

def _saved_oauth_tokens():
    try:
        path = Path("data") / "blaze_oauth_tokens.json"
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8") or "{}")
    except Exception:
        return {}


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
    value = os.getenv(name)
    if value:
        return value

    tokens = _saved_oauth_tokens()

    if name == "BLAZE_ACCESS_TOKEN":
        return tokens.get("accessToken") or tokens.get("access_token") or default

    if name == "BLAZE_REFRESH_TOKEN":
        return tokens.get("refreshToken") or tokens.get("refresh_token") or default

    return default

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

        if _event_handler:
            try:
                _event_handler(message)
            except Exception as e:
                STATE["last_error"] = f"event handler failed: {e}"
                add_log(STATE["last_error"])

    def run():
        STATE["running"] = True
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
    from pathlib import Path
    from datetime import datetime, timezone

    if not isinstance(tokens, dict):
        return {}

    path = Path("data") / "blaze_oauth_tokens.json"
    path.parent.mkdir(parents=True, exist_ok=True)

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

