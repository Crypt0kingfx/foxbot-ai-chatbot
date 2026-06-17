from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
import random

app = FastAPI()

# Logo should be here:
# C:\Users\crypt\Desktop\foxbot\foxbot\static\foxbot-logo.png
app.mount("/static", StaticFiles(directory="static", check_dir=False), name="static")

giveaway_entries = []

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

        .quick-buttons button:hover,
        .send-row button:hover {
            opacity: 0.92;
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
                    <strong>AI Demo Mode</strong>
                    <span>The !ask command shows how FoxBot will respond once live AI billing is enabled.</span>
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
                    <div class="status">● Live Local Demo</div>
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
                    <div class="message bot">This version includes giveaway memory, winner selection, and AI demo mode for your Blaze competition build.</div>
                </div>

                <div class="send-row">
                    <input id="messageInput" type="text" placeholder="Type a command or message...">
                    <button onclick="sendMessage()">Send</button>
                </div>

                <div class="footer-note">
                    Demo running locally on your machine
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
    username = username.strip()

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
            "response": f"🎉 Winner selected: @{winner}!"
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
    }@app.get("/oauth/blaze/callback")
def blaze_oauth_callback(code: str = "", state: str = ""):
    return {
        "message": "Blaze OAuth callback received.",
        "code_received": bool(code),
        "state": state
    }