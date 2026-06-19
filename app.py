import os
import random
import threading
import time

import requests
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()

app = FastAPI()

app.mount("/static", StaticFiles(directory="static", check_dir=False), name="static")

giveaway_entries = []

BLAZE_CLIENT_ID = os.environ.get("BLAZE_CLIENT_ID", "")
BLAZE_CLIENT_SECRET = os.environ.get("BLAZE_CLIENT_SECRET", "")
BLAZE_REDIRECT_URI = "https://foxbot-ai-chatbot.onrender.com/oauth/blaze/callback"

oauth_session = {}
bot_tokens = {}

polling_thread = None

polling_status = {
    "running": False,
    "checks": 0,
    "messages_seen": 0,
    "commands_processed": 0,
    "last_error": None,
    "last_response": None,
    "last_message": None
}

processed_polling_messages = set()


html_content = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>FoxBot AI Chatbot</title>

    <style>
        * {
            box-sizing: border-box;
        }

        body {
            margin: 0;
            font-family: Arial, sans-serif;
            background: linear-gradient(135deg, #0b1020, #111827, #1f2937);
            color: white;
        }

        .page {
            min-height: 100vh;
            padding: 24px;
        }

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

        .sidebar {
            padding: 22px;
        }

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

        .brand h1 {
            margin: 0;
            font-size: 28px;
            line-height: 1;
        }

        .brand p {
            margin: 6px 0 0;
            color: #cbd5e1;
            font-size: 14px;
        }

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

        .info-card strong {
            display: block;
            margin-bottom: 6px;
            color: #ffffff;
        }

        .info-card span {
            color: #cbd5e1;
            font-size: 14px;
            line-height: 1.4;
        }

        .command-list {
            display: grid;
            gap: 10px;
        }

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

        .title-block h2 {
            margin: 0;
            font-size: 30px;
        }

        .title-block p {
            margin: 8px 0 0;
            color: #cbd5e1;
        }

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

        .quick-buttons {
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
        }

        .quick-buttons button,
        .send-row button {
            background: linear-gradient(135deg, #f97316, #ea580c);
            color: white;
            border: none;
            border-radius: 12px;
            padding: 12px 14px;
            cursor: pointer;
            font-size: 14px;
            font-weight: bold;
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

        .bot {
            background: #1f2937;
            color: #f8fafc;
            margin-right: auto;
        }

        .user {
            background: linear-gradient(135deg, #2563eb, #1d4ed8);
            color: white;
            margin-left: auto;
            text-align: right;
        }

        .send-row {
            display: flex;
            gap: 12px;
            margin-top: 16px;
        }

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

        .footer-note {
            margin-top: 14px;
            color: #94a3b8;
            font-size: 13px;
            text-align: center;
        }

        @media (max-width: 920px) {
            .app-shell {
                grid-template-columns: 1fr;
            }

            .main {
                min-height: auto;
            }

            .message {
                max-width: 90%;
            }
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

                <div class="info-card">
                    <strong>Community Assistant</strong>
                    <span>Helps creators manage chat, answer common questions, and improve engagement.</span>
                </div>

                <div class="info-card">
                    <strong>Giveaway System</strong>
                    <span>Starts giveaways, tracks entries, blocks duplicate signups, and picks a winner.</span>
                </div>

                <div class="info-card">
                    <strong>Blaze Integration</strong>
                    <span>Connects with Blaze OAuth, sends chat messages, and can poll live chat for commands.</span>
                </div>

                <div class="section-title">Commands</div>

                <div class="command-list">
                    <div class="command-chip">!help</div>
                    <div class="command-chip">!schedule</div>
                    <div class="command-chip">!faq</div>
                    <div class="command-chip">!giveaway</div>
                    <div class="command-chip">!enter</div>
                    <div class="command-chip">!entries</div>
                    <div class="command-chip">!pickwinner</div>
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
                        <button onclick="sendQuickMessage('!help')">!help</button>
                        <button onclick="sendQuickMessage('!schedule')">!schedule</button>
                        <button onclick="sendQuickMessage('!faq')">!faq</button>
                        <button onclick="sendQuickMessage('!giveaway')">!giveaway</button>
                        <button onclick="sendQuickMessage('!enter')">!enter</button>
                        <button onclick="sendQuickMessage('!entries')">!entries</button>
                        <button onclick="sendQuickMessage('!pickwinner')">!pickwinner</button>
                        <button onclick="sendQuickMessage('!ask What does FoxBot do?')">!ask demo</button>
                    </div>
                </div>

                <div class="chat-box" id="chatBox">
                    <div class="message bot">Welcome to FoxBot. Try !help to see commands.</div>
                    <div class="message bot">FoxBot now supports Blaze OAuth, real Blaze chat posting, and polling-based command detection.</div>
                </div>

                <div class="send-row">
                    <input id="messageInput" type="text" placeholder="Type a command or message...">
                    <button onclick="sendMessage()">Send</button>
                </div>

                <div class="footer-note">
                    FoxBot AI Chatbot for the Blaze Builder Challenge
                </div>
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


@app.get("/", response_class=HTMLResponse)
def home():
    return html_content


@app.get("/chat")
def chat(message: str = "", username: str = "viewer"):
    global giveaway_entries

    original_message = message.strip()
    lower_message = original_message.lower()
    username = username.strip() or "viewer"

    if lower_message == "!help":
        return {
            "response": "Commands: !help, !schedule, !faq, !giveaway, !enter, !entries, !pickwinner, !ask"
        }

    if lower_message == "!schedule":
        return {
            "response": "Wednesday 10AM PST | Friday 1PM PST | Sunday 2PM PST"
        }

    if lower_message == "!faq":
        return {
            "response": "FoxBot is your Blaze AI chatbot for creators and viewers."
        }

    if lower_message == "!giveaway":
        giveaway_entries = []
        return {
            "response": "Giveaway started. Type !enter to join."
        }

    if lower_message == "!enter":
        existing_names = [name.lower() for name in giveaway_entries]

        if username.lower() in existing_names:
            return {
                "response": f"@{username}, you are already entered."
            }

        giveaway_entries.append(username)

        return {
            "response": f"@{username}, you are entered into the giveaway!"
        }

    if lower_message == "!entries":
        if giveaway_entries:
            return {
                "response": f"Current entries: {len(giveaway_entries)} | Names: {', '.join(giveaway_entries)}"
            }

        return {
            "response": "Current entries: 0 | Names: No entries yet"
        }

    if lower_message == "!pickwinner":
        if not giveaway_entries:
            return {
                "response": "No giveaway entries yet."
            }

        winner = random.choice(giveaway_entries)

        return {
            "response": f"Winner selected: @{winner}!"
        }

    if lower_message.startswith("!ask"):
        question = original_message[4:].strip()

        if not question:
            return {
                "response": "Use !ask followed by a question."
            }

        return {
            "response": f"Demo mode: FoxBot would answer this question about '{question}' using AI once API billing is active."
        }

    return {
        "response": "Unknown command. Type !help"
    }


@app.get("/login/blaze")
def login_blaze():
    response = requests.post(
        "https://blaze.stream/bapi/oauth2/generate-auth-url",
        json={
            "clientId": BLAZE_CLIENT_ID,
            "clientSecret": BLAZE_CLIENT_SECRET,
            "redirectUri": BLAZE_REDIRECT_URI,
            "scopes": ["users.read", "offline.access", "channel.moderate", "users.bot"]
        }
    )

    data = response.json()

    oauth_session["state"] = data.get("state")
    oauth_session["codeVerifier"] = data.get("codeVerifier")

    return RedirectResponse(data.get("url"))


@app.get("/oauth/blaze/callback")
def blaze_oauth_callback(code: str = "", state: str = ""):
    if not code:
        return {"error": "Missing code from Blaze callback."}

    if state != oauth_session.get("state"):
        return {"error": "State did not match. Please try logging in again."}

    token_response = requests.post(
        "https://blaze.stream/bapi/oauth2/token",
        json={
            "clientId": BLAZE_CLIENT_ID,
            "clientSecret": BLAZE_CLIENT_SECRET,
            "code": code,
            "codeVerifier": oauth_session.get("codeVerifier"),
            "redirectUri": BLAZE_REDIRECT_URI,
            "grantType": "authorization_code"
        }
    )

    token_data = token_response.json()

    bot_tokens["accessToken"] = token_data.get("accessToken")
    bot_tokens["refreshToken"] = token_data.get("refreshToken")

    return {
        "message": "Blaze login successful! FoxBot is now connected to your account.",
        "scopes": token_data.get("scopes")
    }


@app.get("/me")
def get_my_profile():
    access_token = bot_tokens.get("accessToken")

    if not access_token:
        return {"error": "Not logged in yet. Visit /login/blaze first."}

    response = requests.get(
        "https://api.blaze.stream/v1/users/profile",
        headers={
            "Authorization": f"Bearer {access_token}",
            "client-id": BLAZE_CLIENT_ID,
            "Accept": "application/json"
        }
    )

    return response.json()


@app.get("/blaze/find-channel")
def find_blaze_channel():
    client_id = os.getenv("BLAZE_CLIENT_ID")
    access_token = bot_tokens.get("accessToken")
    channel_slug = os.getenv("BLAZE_CHANNEL_SLUG")

    if not client_id:
        return {"success": False, "message": "Missing BLAZE_CLIENT_ID."}

    if not access_token:
        return {"success": False, "message": "Not logged in yet. Visit /login/blaze first."}

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
        return response.json()
    except Exception:
        return {
            "status_code": response.status_code,
            "text": response.text
        }


def send_blaze_chat_message(text: str):
    client_id = os.getenv("BLAZE_CLIENT_ID")
    channel_id = os.getenv("BLAZE_CHANNEL_ID")
    access_token = bot_tokens.get("accessToken")

    if not client_id or not channel_id or not access_token:
        return {
            "success": False,
            "message": "Missing BLAZE_CLIENT_ID, BLAZE_CHANNEL_ID, or access token."
        }

    response = requests.post(
        "https://api.blaze.stream/v1/chats/messages",
        headers={
            "Authorization": f"Bearer {access_token}",
            "client-id": client_id,
            "Accept": "application/json",
            "content-type": "application/json"
        },
        json={
            "channelId": channel_id,
            "message": text
        }
    )

    try:
        return response.json()
    except Exception:
        return {
            "status_code": response.status_code,
            "text": response.text
        }


@app.get("/blaze/send-test-message")
def send_test_blaze_message():
    result = send_blaze_chat_message("FoxBot is officially connected to Blaze chat!")

    return {
        "test_message": "FoxBot is officially connected to Blaze chat!",
        "result": result
    }


@app.get("/blaze/run-command")
def run_command_in_blaze(message: str = "!help", username: str = "viewer"):
    if not bot_tokens.get("accessToken"):
        return {"success": False, "message": "Not logged in yet. Visit /login/blaze first."}

    foxbot_result = chat(message=message, username=username)
    foxbot_reply = foxbot_result.get("response", "FoxBot had no response.")

    blaze_response = send_blaze_chat_message(foxbot_reply)

    return {
        "success": True,
        "command_received": message,
        "foxbot_reply": foxbot_reply,
        "blaze_response": blaze_response
    }


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


def find_chat_message_id(payload):
    return find_first_string(payload, ["messageId", "id"]) or str(payload)[:200]


def get_recent_blaze_messages():
    client_id = os.getenv("BLAZE_CLIENT_ID")
    channel_id = os.getenv("BLAZE_CHANNEL_ID")
    access_token = bot_tokens.get("accessToken")

    if not client_id or not channel_id or not access_token:
        return {
            "success": False,
            "message": "Missing BLAZE_CLIENT_ID, BLAZE_CHANNEL_ID, or access token. Visit /login/blaze first."
        }

    response = requests.get(
        "https://api.blaze.stream/v1/chats/messages",
        headers={
            "Authorization": f"Bearer {access_token}",
            "client-id": client_id,
            "Accept": "application/json"
        },
        params={
            "channelId": channel_id,
            "limit": 20
        }
    )

    try:
        return response.json()
    except Exception:
        return {
            "success": False,
            "status_code": response.status_code,
            "text": response.text
        }


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


def blaze_polling_worker():
    polling_status["running"] = True
    polling_status["last_error"] = None

    while polling_status["running"]:
        try:
            data = get_recent_blaze_messages()
            polling_status["checks"] += 1
            polling_status["last_response"] = data

            rows = extract_rows_from_blaze_response(data)
            polling_status["messages_seen"] = len(rows)

            for item in reversed(rows):
                message_id = find_chat_message_id(item)
                message_text = find_chat_message_text(item)
                username = find_chat_username(item)

                polling_status["last_message"] = item

                if not message_id or message_id in processed_polling_messages:
                    continue

                processed_polling_messages.add(message_id)

                if not message_text:
                    continue

                if not message_text.startswith("!"):
                    continue

                foxbot_result = chat(message=message_text, username=username)
                foxbot_reply = foxbot_result.get("response", "FoxBot had no response.")

                send_blaze_chat_message(foxbot_reply)
                polling_status["commands_processed"] += 1

            time.sleep(5)

        except Exception as error:
            polling_status["last_error"] = str(error)
            time.sleep(5)


@app.get("/blaze/check-recent-messages")
def check_recent_blaze_messages():
    return get_recent_blaze_messages()


@app.get("/blaze/start-polling-listener")
def start_polling_listener():
    global polling_thread

    if polling_thread and polling_thread.is_alive():
        return {
            "success": True,
            "message": "Polling listener is already running.",
            "status": polling_status
        }

    polling_thread = threading.Thread(target=blaze_polling_worker, daemon=True)
    polling_thread.start()

    return {
        "success": True,
        "message": "FoxBot polling listener started.",
        "status": polling_status
    }


@app.get("/blaze/stop-polling-listener")
def stop_polling_listener():
    polling_status["running"] = False

    return {
        "success": True,
        "message": "FoxBot polling listener stopped.",
        "status": polling_status
    }


@app.get("/blaze/polling-status")
def get_polling_status():
    return polling_status