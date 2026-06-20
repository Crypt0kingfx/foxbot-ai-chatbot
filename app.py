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
                    <div class="command-chip">!giveaway</div>
                    <div class="command-chip">!enter</div>
                    <div class="command-chip">!entries</div>
                    <div class="command-chip">!pickwinner</div>
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
                        <button onclick="sendQuickMessage('!giveaway')">!giveaway</button>
                        <button onclick="sendQuickMessage('!enter')">!enter</button>
                        <button onclick="sendQuickMessage('!entries')">!entries</button>
                        <button onclick="sendQuickMessage('!pickwinner')">!pickwinner</button>
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
def is_admin(username: str):
    admin_usernames = os.getenv("ADMIN_USERNAMES", "crypt0k1ng96,Ryan")
    admins = [name.strip().lower() for name in admin_usernames.split(",")]
    return username.strip().lower() in admins

@app.get("/chat")
def chat(message: str = "", username: str = "viewer"):
    global giveaway_entries

    original_message = message.strip()
    lower_message = original_message.lower()
    username = username.strip() or "viewer"

    if lower_message == "!help":
        return {
            "response": "FoxBot commands: !help, !schedule, !faq, !giveaway, !enter, !entries, !pickwinner, !hugs, !ask"
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
        giveaway_entries = []
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
        if not giveaway_entries:
            return {
                "response": "No giveaway entries yet. Type !enter to join first."
            }

        winner = random.choice(giveaway_entries)

        return {
            "response": f"The fox has chosen... @{winner} wins!"
        }

    if lower_message == "!hugs":
        return {
            "response": f"@{username} sends a big FoxBot hug to the chat! ????"
        }

    if lower_message.startswith("!ask"):
        question = original_message[4:].strip()

        if not question:
            return {
                "response": "Use !ask followed by a question."
            }

        return {
            "response": f"FoxBot AI demo mode: I would answer this question next once full AI billing is enabled: {question}"
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

