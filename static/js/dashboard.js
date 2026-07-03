FoxBotStudio.register("dashboard", () => {
    console.log("Dashboard Loaded");

    // FoxBot Main Dashboard Live Control Card v1
    const cardId = "foxbot-live-main-card";

    function findDashboardMount() {
        return (
            document.querySelector("#dashboard") ||
            document.querySelector("[data-page='dashboard']") ||
            document.querySelector(".dashboard") ||
            document.querySelector(".content") ||
            document.querySelector("main") ||
            document.body
        );
    }

    function ensureCard() {
        if (document.getElementById(cardId)) return;

        const mount = findDashboardMount();

        const card = document.createElement("section");
        card.id = cardId;
        card.innerHTML = `
            <div style="
                border:1px solid rgba(255,122,24,.35);
                background:
                    radial-gradient(circle at top right, rgba(255,122,24,.18), transparent 30%),
                    radial-gradient(circle at bottom left, rgba(57,255,136,.10), transparent 32%),
                    #07100a;
                color:#f5fff7;
                border-radius:20px;
                padding:18px;
                margin:16px 0;
                box-shadow:0 18px 50px rgba(0,0,0,.28);
                font-family:Arial,sans-serif;
            ">
                <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:14px;flex-wrap:wrap;">
                    <div>
                        <h2 style="margin:0 0 6px;font-size:24px;">🦊 FoxBot Live Blaze Control</h2>
                        <div style="color:#a9b9ae;font-size:14px;">Quick controls for @foxbotai live replies and listener status.</div>
                    </div>
                    <a href="/foxbot-control" style="
                        color:#050505;
                        background:#fff;
                        text-decoration:none;
                        padding:10px 13px;
                        border-radius:12px;
                        font-weight:800;
                    ">Open Full Control</a>
                </div>

                <div style="
                    display:grid;
                    grid-template-columns:repeat(4,minmax(130px,1fr));
                    gap:12px;
                    margin-top:16px;
                ">
                    <div style="border:1px solid rgba(255,255,255,.12);background:rgba(255,255,255,.045);border-radius:14px;padding:12px;">
                        <div style="color:#a9b9ae;font-size:12px;text-transform:uppercase;letter-spacing:.08em;">Auto Replies</div>
                        <div id="fb-live-auto" style="font-size:22px;font-weight:900;margin-top:6px;">Loading...</div>
                    </div>
                    <div style="border:1px solid rgba(255,255,255,.12);background:rgba(255,255,255,.045);border-radius:14px;padding:12px;">
                        <div style="color:#a9b9ae;font-size:12px;text-transform:uppercase;letter-spacing:.08em;">Listener</div>
                        <div id="fb-live-listener" style="font-size:22px;font-weight:900;margin-top:6px;">Loading...</div>
                    </div>
                    <div style="border:1px solid rgba(255,255,255,.12);background:rgba(255,255,255,.045);border-radius:14px;padding:12px;">
                        <div style="color:#a9b9ae;font-size:12px;text-transform:uppercase;letter-spacing:.08em;">Chat Events</div>
                        <div id="fb-live-events" style="font-size:22px;font-weight:900;margin-top:6px;">0</div>
                    </div>
                    <div style="border:1px solid rgba(255,255,255,.12);background:rgba(255,255,255,.045);border-radius:14px;padding:12px;">
                        <div style="color:#a9b9ae;font-size:12px;text-transform:uppercase;letter-spacing:.08em;">Replies Sent</div>
                        <div id="fb-live-replies" style="font-size:22px;font-weight:900;margin-top:6px;">0</div>
                    </div>
                </div>

                <div style="display:flex;flex-wrap:wrap;gap:10px;margin-top:16px;">
                    <button id="fb-live-on" style="border:0;border-radius:12px;padding:11px 14px;font-weight:900;background:#39ff88;color:#031006;cursor:pointer;">Live Replies ON</button>
                    <button id="fb-live-off" style="border:0;border-radius:12px;padding:11px 14px;font-weight:900;background:#ff3b3b;color:#fff;cursor:pointer;">Emergency OFF</button>
                    <button id="fb-live-restart" style="border:0;border-radius:12px;padding:11px 14px;font-weight:900;background:#fff;color:#050505;cursor:pointer;">Restart Listener</button>
                    <button id="fb-live-refresh" style="border:1px solid rgba(255,255,255,.18);border-radius:12px;padding:11px 14px;font-weight:900;background:#151d16;color:#f5fff7;cursor:pointer;">Refresh</button>
                </div>

                <pre id="fb-live-status" style="
                    white-space:pre-wrap;
                    word-break:break-word;
                    background:#020402;
                    border:1px solid rgba(255,255,255,.1);
                    color:#c7ffd9;
                    border-radius:14px;
                    padding:12px;
                    margin-top:14px;
                    max-height:220px;
                    overflow:auto;
                    font-size:12px;
                ">Loading FoxBot status...</pre>
            </div>
        `;

        mount.prepend(card);

        document.getElementById("fb-live-on")?.addEventListener("click", () => postControl("/api/blaze/native/live-control/on"));
        document.getElementById("fb-live-off")?.addEventListener("click", () => postControl("/api/blaze/native/live-control/off"));
        document.getElementById("fb-live-refresh")?.addEventListener("click", loadFoxBotLiveStatus);
        document.getElementById("fb-live-restart")?.addEventListener("click", restartFoxBotListener);

        loadFoxBotLiveStatus();
        setInterval(loadFoxBotLiveStatus, 10000);
    }

    async function getJson(url) {
        const res = await fetch(url);
        return await res.json();
    }

    async function postControl(url) {
        setStatusText("Working...");
        await fetch(url, { method: "POST" });
        await new Promise(resolve => setTimeout(resolve, 700));
        await loadFoxBotLiveStatus();
    }

    async function restartFoxBotListener() {
        setStatusText("Restarting listener...");
        await fetch("/api/blaze/native/stop", { method: "POST" });
        await new Promise(resolve => setTimeout(resolve, 3000));
        await fetch("/api/blaze/native/start", { method: "POST" });
        await new Promise(resolve => setTimeout(resolve, 1000));
        await loadFoxBotLiveStatus();
    }

    function setStatusText(text) {
        const el = document.getElementById("fb-live-status");
        if (el) el.textContent = text;
    }

    function setHtml(id, html) {
        const el = document.getElementById(id);
        if (el) el.innerHTML = html;
    }

    function setText(id, text) {
        const el = document.getElementById(id);
        if (el) el.textContent = text;
    }

    async function loadFoxBotLiveStatus() {
        try {
            const live = await getJson("/api/blaze/native/live-control");
            const native = await getJson("/api/blaze/native/status");

            const autoOn = !!live.auto_send_effective;
            const running = !!native?.state?.running;
            const connected = !!native?.state?.connected;

            setHtml("fb-live-auto", autoOn ? `<span style="color:#39ff88;">ON</span>` : `<span style="color:#ff5b5b;">OFF</span>`);
            setHtml("fb-live-listener", running && connected ? `<span style="color:#39ff88;">CONNECTED</span>` : `<span style="color:#ff5b5b;">CHECK</span>`);
            setText("fb-live-events", native?.state?.chat_messages_received ?? live.chat_messages_received ?? 0);
            setText("fb-live-replies", native?.state?.replies_sent ?? live.replies_sent ?? 0);

            const compact = {
                auto_send: live.auto_send_effective,
                source: live.source,
                running: native?.state?.running,
                connected: native?.state?.connected,
                session_id: native?.state?.session_id,
                events_received: native?.state?.events_received,
                chat_messages_received: native?.state?.chat_messages_received,
                replies_sent: native?.state?.replies_sent,
                last_error: native?.state?.last_error,
                last_reply_attempt: native?.state?.last_reply_attempt || live.last_reply_attempt || null,
                last_event_reply_attempt: native?.state?.last_event_reply_attempt || live.last_event_reply_attempt || null,
                last_role_shoutout_attempt: native?.state?.last_role_shoutout_attempt || live.last_role_shoutout_attempt || null
            };

            setStatusText(JSON.stringify(compact, null, 2));
        } catch (err) {
            setHtml("fb-live-listener", `<span style="color:#ff5b5b;">ERROR</span>`);
            setStatusText(String(err));
        }
    }

    ensureCard();
});
