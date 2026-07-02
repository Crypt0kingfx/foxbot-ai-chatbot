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
        STATE["connected"] = True
        STATE["last_error"] = None

    @sio.event
    def disconnect():
        STATE["connected"] = False

    @sio.event
    def connect_error(data):
        STATE["last_error"] = f"connect_error: {data}"

    @sio.on("eventsub")
    def on_eventsub(message):
        STATE["events_received"] += 1
        STATE["last_event"] = message

        parsed = parse_blaze_event(message)

        if parsed.get("kind") == "chat":
            STATE["chat_messages_received"] += 1

        if parsed.get("raw_type") == "session_welcome":
            payload = message.get("payload") or {}
            STATE["session_id"] = payload.get("sessionId")
            if STATE["session_id"]:
                try:
                    STATE["subscriptions"] = subscribe_default_events()
                except Exception as e:
                    STATE["last_error"] = f"subscribe failed: {e}"

        if _event_handler:
            try:
                _event_handler(message)
            except Exception as e:
                STATE["last_error"] = f"event handler failed: {e}"

    def run():
        STATE["running"] = True
        STATE["started_at"] = time.time()
        try:
            sio.connect(
                env("BLAZE_WS_URL", "https://blaze.stream"),
                socketio_path=env("BLAZE_WS_PATH", "ws"),
                transports=["websocket"],
                wait_timeout=15,
            )
            sio.wait()
        except Exception as e:
            STATE["last_error"] = str(e)
        finally:
            STATE["running"] = False
            STATE["connected"] = False

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
