import os
import json
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

# ----------------------------
# App state
# ----------------------------

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

giveaway_overlay = {
    "active": False,
    "latest_entry": None,
    "winner": None
}

viewer_stats = {}

bot_mode = os.getenv("FOXBOT_MODE", "hype").lower()

custom_commands = {}

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

foxcoin_economy = {
    "currency_name": os.getenv("POINTS_NAME", "FoxCoins"),
    "balances": {},
    "daily_claims": {},
    "transactions": []
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

fox_spirit_ranks = [
    {"name": "Fox Pup", "minimum": 0},
    {"name": "Fox Hunter", "minimum": 250},
    {"name": "Fox Warrior", "minimum": 750},
    {"name": "Fox Elder", "minimum": 1500},
    {"name": "Fox Spirit", "minimum": 3000},
    {"name": "Fox King", "minimum": 5000}
]

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
        "fox_spirit_ranks": globals().get("fox_spirit_ranks", []),
        "reward_shop": globals().get("reward_shop", {}),
        "redemption_queue": globals().get("redemption_queue", []),
        "cooldown_settings": globals().get("cooldown_settings", {}),
        "boss_battle": globals().get("boss_battle", {})
    }


def apply_persistent_snapshot(data):
    global bot_mode
    global custom_commands
    global stream_info
    global arcade_stats
    global foxcoin_economy
    global support_rewards
    global fox_spirit_ranks
    global reward_shop
    global redemption_queue
    global cooldown_settings
    global cooldown_tracker
    global boss_battle

    if not isinstance(data, dict):
        return False

    if isinstance(data.get("bot_mode"), str):
        bot_mode = data["bot_mode"].lower()

    if isinstance(data.get("custom_commands"), dict):
        custom_commands = data["custom_commands"]

    if isinstance(data.get("stream_info"), dict):
        stream_info = data["stream_info"]
        stream_info.setdefault("game", os.getenv("STREAM_GAME", "Off The Grid"))
        stream_info.setdefault("title", os.getenv("STREAM_TITLE", "FoxBot is live on Blaze!"))
        stream_info.setdefault("lurkers", {})

    if isinstance(data.get("arcade_stats"), dict):
        arcade_stats.update(data["arcade_stats"])

    if isinstance(data.get("fox_spirit_ranks"), list):
        fox_spirit_ranks = data["fox_spirit_ranks"]

    if isinstance(data.get("support_rewards"), dict):
        support_rewards.update(data["support_rewards"])

    if isinstance(data.get("foxcoin_economy"), dict):
        foxcoin_economy.update(data["foxcoin_economy"])
        foxcoin_economy.setdefault("currency_name", os.getenv("POINTS_NAME", "FoxCoins"))
        foxcoin_economy.setdefault("balances", {})
        foxcoin_economy.setdefault("daily_claims", {})
        foxcoin_economy.setdefault("transactions", [])

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

    return True


def save_persistent_data():
    try:
        data = get_persistent_snapshot()

        temp_file = DATA_FILE + ".tmp"

        with open(temp_file, "w", encoding="utf-8") as file:
            json.dump(data, file, indent=2)

        os.replace(temp_file, DATA_FILE)

        return True
    except Exception as exc:
        print(f"FoxBot data save failed: {exc}")
        return False


def load_persistent_data():
    if not os.path.exists(DATA_FILE):
        print("FoxBot data file not found. Starting fresh.")
        return False

    try:
        with open(DATA_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)

        loaded = apply_persistent_snapshot(data)

        if loaded:
            print(f"FoxBot data loaded from {DATA_FILE}")

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
    "last_message": None
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
                    <div class="command-chip">!help</div>
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
                        <button onclick="sendQuickMessage('!help')">!help</button>
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
                    <div class="message bot">Welcome to FoxBot. Try !help to see commands.</div>
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
            <a class="button" href="/login/blaze">Login with Blaze</a>
            <button onclick="callEndpoint('/blaze/start-polling-listener')">Start Listener</button>
            <button class="danger" onclick="callEndpoint('/blaze/stop-polling-listener')">Stop Listener</button>
            <button class="secondary" onclick="callEndpoint('/blaze/polling-status')">Check Status</button>
            <button class="secondary" onclick="callEndpoint('/blaze/check-recent-messages')">Check Recent Chat</button>
            <button onclick="callEndpoint('/blaze/send-test-message')">Send Test Message</button>
            <button onclick="callEndpoint('/blaze/run-command?message=!help&username=Ryan')">Run !help</button>
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
            <li><code>!help</code> — shows available commands</li>
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
            <li>Type <code>!help</code> in Blaze chat.</li>
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
        parts.append(f"{index}. {viewer} ? {damage} damage")

    return "Boss damage leaderboard: " + " | ".join(parts)


def add_boss_damage(username: str, damage: int):
    clean_name = normalize_viewer_name(username)
    key = clean_name.lower()

    damage_log = boss_battle.setdefault("damage_log", {})
    current_damage = int(damage_log.get(key, 0))
    damage_log[key] = current_damage + int(damage)

    boss_battle["hp"] = max(0, int(boss_battle.get("hp", 0)) - int(damage))

    return damage_log[key]


def finish_boss_if_defeated():
    if int(boss_battle.get("hp", 0)) > 0:
        return None

    damage_log = boss_battle.get("damage_log", {})

    if damage_log:
        top_player = max(damage_log.items(), key=lambda item: int(item[1]))[0]
    else:
        top_player = "unknown"

    boss_battle["active"] = False
    boss_battle["defeated_count"] = int(boss_battle.get("defeated_count", 0)) + 1
    boss_battle["last_winner"] = top_player

    bonus = 100
    currency = get_currency_name()

    if top_player != "unknown":
        add_points(top_player, bonus, "boss battle mvp")

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


def get_balance(name: str):
    key = viewer_key(name)
    return int(foxcoin_economy["balances"].get(key, 0))


def add_points(name: str, amount: int, reason: str = "activity"):
    clean_name = normalize_viewer_name(name)
    key = viewer_key(clean_name)

    current = int(foxcoin_economy["balances"].get(key, 0))
    new_balance = max(0, current + int(amount))
    foxcoin_economy["balances"][key] = new_balance

    foxcoin_economy["transactions"].append({
        "viewer": clean_name,
        "amount": int(amount),
        "reason": reason,
        "balance": new_balance
    })

    # Keep transaction history small
    foxcoin_economy["transactions"] = foxcoin_economy["transactions"][-50:]

    return new_balance


def add_redemption(username: str, reward_name: str, message: str, cost: int):
    redemption = {
        "username": normalize_viewer_name(username),
        "reward": reward_name,
        "message": message,
        "cost": int(cost)
    }

    redemption_queue.insert(0, redemption)

    # Keep the latest 10 redemptions
    del redemption_queue[10:]

    return redemption


def format_redemptions(limit: int = 5):
    if not redemption_queue:
        return "No active redemptions yet. Use !shop and !redeem to spend FoxCoins."

    parts = []

    for item in redemption_queue[:limit]:
        parts.append(f"@{item['username']} redeemed {item['reward']}")

    return "Recent redemptions: " + " | ".join(parts)


def format_reward_shop():
    currency = get_currency_name()

    if not reward_shop:
        return f"The reward shop is empty. Admins can add rewards with !addreward name cost message"

    parts = []

    for reward_name, reward_data in sorted(reward_shop.items(), key=lambda item: item[1].get("cost", 0)):
        cost = reward_data.get("cost", 0)
        parts.append(f"{reward_name} ? {cost} {currency}")

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


def format_coin_leaderboard(limit: int = 5):
    currency = get_currency_name()

    if not foxcoin_economy["balances"]:
        return f"No {currency} balances yet. Type !daily or !foxhunt to earn some."

    sorted_balances = sorted(
        foxcoin_economy["balances"].items(),
        key=lambda item: item[1],
        reverse=True
    )

    parts = []
    for index, (name, balance) in enumerate(sorted_balances[:limit], start=1):
        parts.append(f"{index}. {name} ? {balance} {currency}")

    return f"{currency} leaderboard: " + " | ".join(parts)


def normalize_custom_command(command_name: str):
    cleaned = command_name.strip().lower()

    if not cleaned:
        return ""

    if not cleaned.startswith("!"):
        cleaned = "!" + cleaned

    return cleaned


def format_custom_commands():
    if not custom_commands:
        return "No custom commands yet. Admins can add one with !addcmd name response"

    command_names = sorted(custom_commands.keys())
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


def track_viewer_command(username: str, command: str):
    clean_name = username.strip() or "viewer"
    clean_key = clean_name.lower()

    if clean_key not in viewer_stats:
        viewer_stats[clean_key] = {
            "display_name": clean_name,
            "commands": 0,
            "last_command": None
        }

    viewer_stats[clean_key]["commands"] += 1
    viewer_stats[clean_key]["last_command"] = command


def format_leaderboard(limit: int = 5):
    if not viewer_stats:
        return "FoxBot leaderboard is empty. Type !help to get started."

    sorted_users = sorted(
        viewer_stats.values(),
        key=lambda item: item.get("commands", 0),
        reverse=True
    )

    top_users = sorted_users[:limit]

    parts = []
    for index, user in enumerate(top_users, start=1):
        parts.append(f"{index}. {user['display_name']} ? {user['commands']} commands")

    return "FoxBot leaderboard: " + " | ".join(parts)


def is_admin(username: str):
    admin_usernames = os.getenv("ADMIN_USERNAMES", "crypt0k1ng96,Ryan")
    admins = [name.strip().lower() for name in admin_usernames.split(",")]
    return username.strip().lower() in admins


@app.get("/chat")
def chat(message: str = "", username: str = "viewer"):
    global giveaway_entries
    global giveaway_overlay
    global bot_mode
    global custom_commands
    global stream_info
    global arcade_stats
    global foxcoin_economy
    global support_rewards
    global fox_spirit_ranks
    global reward_shop
    global redemption_queue
    global cooldown_settings
    global cooldown_tracker
    global boss_battle

    original_message = message.strip()
    lower_message = original_message.lower()
    username = username.strip() or "viewer"
    admin = is_admin(username)

    cooldown_message = check_command_cooldown(username, lower_message, admin)
    if cooldown_message:
        return {
            "response": cooldown_message
        }

    if lower_message.startswith("!"):
        track_viewer_command(username, lower_message)

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

    if lower_message == "!rules":
        return {
            "response": "BLAZE COMMUNITY SPIN RULES | $25 USDC Giveaway | +100 Votes Sponsored by Fox Spirits | Tag 3 Friends | Like + Repost | Be Active in Fox Spirits Discord | Up to 1.50x Multiplier | 1-5 Gifted Subs = Bonus Entries | Sunday 5 PM PST | Enter here: https://x.com/Pardon_my_trade/status/2069089169738289206?s=20"
        }

    if lower_message == "!giveawaylink":
        return {
            "response": "$25 USDC Giveaway + 100 Votes Sponsored by Fox Spirits | Enter here: https://x.com/Pardon_my_trade/status/2069089169738289206?s=20"
        }

    if lower_message == "!help":
        if admin:
            return {
                "response": "FoxBot help: !daily, !foxhunt, !balance, !shop, !redeem, !boss, !attack, !arcade, !socials, !leaderboard | Admin: !giveaway, !pickwinner, !goodnight, !endstream, !startboss, !givepoints, !addreward"
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

    if lower_message == "!giveaway":
        if not admin:
            return {
                "response": f"@{username}, only the creator or mods can start giveaways."
            }

        giveaway_entries = []
        giveaway_overlay["active"] = True
        giveaway_overlay["latest_entry"] = None
        giveaway_overlay["winner"] = None

        prize = os.getenv("GIVEAWAY_PRIZE", "a Blaze community prize")
        return {
            "response": f"FoxBot giveaway started for {prize}! Type !enter to join."
        }

    if lower_message == "!enter":
        existing_names = [name.lower() for name in giveaway_entries]

        if username.lower() in existing_names:
            return {
                "response": f"@{username}, you are already entered."
            }

        giveaway_entries.append(username)
        giveaway_overlay["latest_entry"] = username
        giveaway_overlay["active"] = True

        return {
            "response": f"@{username}, you are entered into the giveaway!"
        }

    if lower_message == "!entries":
        if giveaway_entries:
            return {
                "response": f"Current giveaway entries: {len(giveaway_entries)} | Names: {', '.join(giveaway_entries)}"
            }

        return {
            "response": "Current giveaway entries: 0 | Names: No entries yet"
        }

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
        giveaway_overlay["winner"] = winner

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
        currency = get_currency_name()
        boss_name = boss_battle.get("name", "Boss")

        total_damage = add_boss_damage(username, damage)
        new_balance = add_points(username, reward, "boss attack")
        boss_hp = int(boss_battle.get("hp", 0))
        defeat_message = finish_boss_if_defeated()

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
        balance = get_balance(username)

        if balance < power_cost:
            return {
                "response": f"@{username}, power attack costs {power_cost} {currency}. Your balance: {balance} {currency}."
            }

        add_points(username, -power_cost, "power attack cost")

        damage = random.randint(50, 110)
        reward = random.randint(15, 35)
        boss_name = boss_battle.get("name", "Boss")

        total_damage = add_boss_damage(username, damage)
        new_balance = add_points(username, reward, "boss power attack reward")
        boss_hp = int(boss_battle.get("hp", 0))
        defeat_message = finish_boss_if_defeated()

        response = f"@{username} used POWER ATTACK on {boss_name} for {damage} damage! Cost: {power_cost} {currency}. Reward: {reward} {currency}. Boss HP: {boss_hp}/{boss_battle.get('max_hp', 500)}. Your total boss damage: {total_damage}. Balance: {new_balance} {currency}."

        if defeat_message:
            response += " " + defeat_message

        return {
            "response": response
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

        balance = get_balance(target)
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
        currency = get_currency_name()
        new_balance = add_points(username, reward, "chat activity")
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
        new_balance = add_points(username, reward, f"claimed {amount} vote tokens")
        return {
            "response": f"@{username} claimed {amount} vote tokens and earned {reward} {currency}. Balance: {new_balance} {currency}."
        }

    if lower_message == "!claimfollow":
        reward = int(support_rewards.get("follow", 100))
        currency = get_currency_name()
        new_balance = add_points(username, reward, "follow reward")
        return {
            "response": f"@{username} earned {reward} {currency} for following. Balance: {new_balance} {currency}."
        }

    if lower_message == "!claimraid":
        reward = int(support_rewards.get("raid", 250))
        currency = get_currency_name()
        new_balance = add_points(username, reward, "raid reward")
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
        new_balance = add_points(username, reward, f"tip reward ${dollars}")
        return {
            "response": f"@{username} earned {reward} {currency} for a ${dollars:g} tip. Balance: {new_balance} {currency}."
        }

    if lower_message == "!claimsub":
        reward = int(support_rewards.get("new_sub", 500))
        currency = get_currency_name()
        new_balance = add_points(username, reward, "subscription reward")
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
        new_balance = add_points(username, reward, f"gift sub reward x{amount}")
        return {
            "response": f"@{username} claimed {amount} gifted sub rewards and earned {reward} {currency}. Balance: {new_balance} {currency}."
        }

    if lower_message in ["!balance", "!points", "!foxcoins"]:
        balance = get_balance(username)
        currency = get_currency_name()

        return {
            "response": f"@{username}, you have {balance} {currency}."
        }

    if lower_message.startswith("!balance ") or lower_message.startswith("!points ") or lower_message.startswith("!foxcoins "):
        parts = original_message.split()

        if len(parts) >= 2:
            target = normalize_viewer_name(parts[1])
            balance = get_balance(target)
            currency = get_currency_name()

            return {
                "response": f"@{target} has {balance} {currency}."
            }

    if lower_message == "!daily":
        key = viewer_key(username)
        currency = get_currency_name()

        if foxcoin_economy["daily_claims"].get(key):
            return {
                "response": f"@{username}, you already claimed your daily {currency} this session."
            }

        reward = 25
        new_balance = add_points(username, reward, "daily")
        foxcoin_economy["daily_claims"][key] = True

        return {
            "response": f"@{username} claimed {reward} {currency}! New balance: {new_balance} {currency}."
        }

    if lower_message == "!coinleaderboard":
        return {
            "response": format_coin_leaderboard()
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

        new_balance = add_points(target, amount, f"given by {username}")
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

        new_balance = add_points(target, -amount, f"removed by {username}")
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
        balance = get_balance(username)

        if balance < cost:
            return {
                "response": f"@{username}, you need {cost} {currency} to redeem {reward_name}. Your balance: {balance} {currency}."
            }

        new_balance = add_points(username, -cost, f"redeemed {reward_name}")
        response_template = reward.get("response", "@{username} redeemed a reward!")

        if reward_name == "mysterybox":
            mystery_roll = random.randint(1, 100)

            if mystery_roll <= 10:
                bonus = 150
                new_balance = add_points(username, bonus, "mysterybox jackpot")
                redeem_message = f"@{username} opened a mystery box and hit the JACKPOT! +{bonus} {currency}. Balance: {new_balance} {currency}."
                add_redemption(username, reward_name, redeem_message, cost)
                return {
                    "response": redeem_message
                }

            if mystery_roll <= 35:
                bonus = 50
                new_balance = add_points(username, bonus, "mysterybox prize")
                redeem_message = f"@{username} opened a mystery box and found {bonus} {currency}! Balance: {new_balance} {currency}."
                add_redemption(username, reward_name, redeem_message, cost)
                return {
                    "response": redeem_message
                }

            if mystery_roll <= 70:
                redeem_message = f"@{username} opened a mystery box and found bonus hype for the chat! Balance: {new_balance} {currency}."
                add_redemption(username, reward_name, redeem_message, cost)
                return {
                    "response": redeem_message
                }

            redeem_message = f"@{username} opened a mystery box... and the fox ran away with the loot. Balance: {new_balance} {currency}."
            add_redemption(username, reward_name, redeem_message, cost)
            return {
                "response": redeem_message
            }

        redeem_message = format_reward_response(response_template, username, cost, new_balance) + f" Balance: {new_balance} {currency}."
        add_redemption(username, reward_name, redeem_message, cost)

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
        new_balance = add_points(username, reward, "foxhunt")

        return {
            "response": f"@{username} went on a fox hunt and {event}! +{reward} {currency}. Balance: {new_balance} {currency}."
        }

    if lower_message == "!arcade":
        return {
            "response": "FoxBot Arcade commands: !foxhunt, !coinflip, !roll, !roll 20, !8ball your question, !rps rock/paper/scissors"
        }

    if lower_message == "!coinflip":
        arcade_stats["plays"] += 1
        arcade_stats["coinflip"] += 1

        result = random.choice(["Heads", "Tails"])

        return {
            "response": f"FoxBot flips a coin... {result}!"
        }

    if lower_message.startswith("!roll"):
        arcade_stats["plays"] += 1
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
            "!help", "!schedule", "!faq", "!socials", "!mode",
            "!giveaway", "!enter", "!entries", "!pickwinner",
            "!stats", "!leaderboard", "!hugs", "!ask", "!arcade", "!goodnight", "!endstream", "!boss", "!bossstatus", "!startboss", "!endboss", "!attack", "!powerattack", "!bossleaderboard", "!foxhunt", "!coinflip", "!roll", "!8ball", "!rps", "!balance", "!points", "!foxcoins", "!rank", "!ranks", "!daily", "!shop", "!redeem", "!redeems", "!clearredeems", "!cooldowns", "!setcooldown", "!clearcooldowns", "!addreward", "!delreward", "!coinleaderboard", "!givepoints", "!takepoints",
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

        custom_commands[command_name] = {
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

        if command_name not in custom_commands:
            return {
                "response": f"{command_name} is not a custom command."
            }

        del custom_commands[command_name]

        return {
            "response": f"Custom command {command_name} deleted."
        }

    if lower_message == "!commands":
        return {
            "response": format_custom_commands()
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

    if lower_message == "!stats":
        user_data = viewer_stats.get(username.lower(), {"commands": 0})
        return {
            "response": f"@{username}, you have used {user_data.get('commands', 0)} FoxBot commands."
        }

    if lower_message == "!leaderboard":
        return {
            "response": format_leaderboard()
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

    if lower_message in custom_commands:
        return {
            "response": custom_commands[lower_message]["response"]
        }

    return {
        "response": "Unknown command. Type !help"
    }


# ----------------------------
# Blaze OAuth
# ----------------------------

@app.get("/login/blaze")
def login_blaze():
    if not BLAZE_CLIENT_ID or not BLAZE_CLIENT_SECRET:
        return {
            "success": False,
            "message": "Missing BLAZE_CLIENT_ID or BLAZE_CLIENT_SECRET in Render environment variables."
        }

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

    if not data.get("url"):
        return {
            "success": False,
            "message": "Blaze did not return a login URL.",
            "response": data
        }

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

    proof_stats["blaze_connected"] = bool(bot_tokens.get("accessToken"))
    proof_stats["channel_id"] = os.getenv("BLAZE_CHANNEL_ID")
    proof_stats["channel_slug"] = os.getenv("BLAZE_CHANNEL_SLUG")

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

    proof_stats["commands_processed"] += 1
    proof_stats["last_command"] = message
    proof_stats["last_reply"] = foxbot_reply
    proof_stats["last_username"] = username
    proof_stats["last_message"] = message

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
    proof_stats["listener_running"] = True

    while polling_status["running"]:
        try:
            data = get_recent_blaze_messages()
            polling_status["checks"] += 1
            polling_status["last_response"] = data

            rows = extract_rows_from_blaze_response(data)
            polling_status["messages_seen"] = len(rows)

            proof_stats["blaze_connected"] = bool(bot_tokens.get("accessToken"))
            proof_stats["listener_running"] = polling_status["running"]
            proof_stats["messages_checked"] = polling_status["checks"]
            proof_stats["messages_seen"] = len(rows)

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

                proof_stats["commands_processed"] += 1
                proof_stats["last_command"] = message_text
                proof_stats["last_reply"] = foxbot_reply
                proof_stats["last_username"] = username
                proof_stats["last_message"] = message_text

            time.sleep(5)

        except Exception as error:
            polling_status["last_error"] = str(error)
            time.sleep(5)

    proof_stats["listener_running"] = False


@app.get("/blaze/check-recent-messages")
def check_recent_blaze_messages():
    return get_recent_blaze_messages()


@app.get("/blaze/start-polling-listener")
def start_polling_listener():
    global polling_thread

    if polling_thread and polling_thread.is_alive():
        proof_stats["listener_running"] = True
        return {
            "success": True,
            "message": "Polling listener is already running.",
            "status": polling_status
        }

    polling_status["running"] = True
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
    proof_stats["listener_running"] = False

    return {
        "success": True,
        "message": "FoxBot polling listener stopped.",
        "status": polling_status
    }


@app.get("/blaze/polling-status")
def get_polling_status():
    return polling_status


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
            "live_proof_panel": True
        },
        "commands": [
            "!help",
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
        return {"success": False, "message": "Not logged in yet. Visit /login/blaze first."}

    demo_steps = [
        "FoxBot Judge Demo starting now!",
        chat(message="!help", username="JudgeDemo").get("response"),
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
                    <li>Simple commands like <code>!help</code> and <code>!enter</code></li>
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
                <p>FoxBot supports public commands like <code>!help</code>, <code>!schedule</code>, <code>!faq</code>, <code>!enter</code>, and <code>!entries</code>.</p>
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
    return {
        "active": giveaway_overlay.get("active", False),
        "prize": os.getenv("GIVEAWAY_PRIZE", "a Blaze community prize"),
        "entry_count": len(giveaway_entries),
        "entries": giveaway_entries,
        "latest_entry": giveaway_overlay.get("latest_entry"),
        "winner": giveaway_overlay.get("winner")
    }


@app.get("/viewer-stats")
def viewer_stats_endpoint():
    return {
        "viewer_count": len(viewer_stats),
        "leaderboard": sorted(
            viewer_stats.values(),
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
def custom_commands_endpoint():
    return {
        "count": len(custom_commands),
        "commands": custom_commands,
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
                    <button onclick="runCommand('!help')">!help</button>
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
                    <button onclick="runCommand('!balance')">!balance</button>
                    <button onclick="runCommand('!rank')">!rank</button>
                    <button onclick="runCommand('!ranks')">!ranks</button>
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
def foxcoins_endpoint():
    return {
        "currency_name": get_currency_name(),
        "balances": foxcoin_economy["balances"],
        "daily_claims": foxcoin_economy["daily_claims"],
        "recent_transactions": foxcoin_economy["transactions"][-10:],
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
        "custom_command_count": len(custom_commands),
        "viewer_balance_count": len(foxcoin_economy.get("balances", {})),
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
            "!help",
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

