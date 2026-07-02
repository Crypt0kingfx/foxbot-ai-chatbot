const grid = document.getElementById("creatorGrid");
const refreshBtn = document.getElementById("refreshBtn");
const demoBtn = document.getElementById("demoBtn");
const connectBtn = document.getElementById("connectBtn");
const handleInput = document.getElementById("handleInput");
const connectMsg = document.getElementById("connectMsg");

function safeText(value) {
  return String(value ?? "").replace(/[&<>"']/g, c => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;"
  }[c]));
}

function creatorCard(c) {
  const commands = (c.commands || []).map(cmd => `<span class="command">${safeText(cmd)}</span>`).join("");
  const badges = (c.badges || []).map(b => safeText(b)).join(" ");
  const url = c.channel_url || `https://blaze.stream/${encodeURIComponent(c.handle || "")}`;

  return `
    <article class="creator-card ${c.status === "connected" ? "connected" : ""}">
      <div class="creator-top">
        <h3 class="creator-name">${safeText(c.handle)}</h3>
        <div class="badges">${badges || "GB"}</div>
      </div>

      <div class="creator-metrics">
        <span>💬 ${Number(c.messages || 0)}</span>
        <span>⭐ ${Number(c.stars || 0)}</span>
        <span>💼 ${Number(c.foxcoins || 0)}</span>
      </div>

      <div class="commands">${commands || `<span class="status">getting set up...</span>`}</div>

      <span class="status">${safeText(c.status || "setting up...")}</span>
      <br>
      <a class="visit" href="${safeText(url)}" target="_blank" rel="noreferrer">visit channel →</a>
    </article>
  `;
}

async function loadCreators() {
  grid.innerHTML = `<p class="sub">Loading connected creators...</p>`;

  const res = await fetch("/api/connected-creators");
  const data = await res.json();

  const creators = data.creators || [];
  grid.innerHTML = creators.map(creatorCard).join("") || `<p class="sub">No creators connected yet.</p>`;

  document.getElementById("totalCreators").textContent = data.totals?.creators || 0;
  document.getElementById("totalMessages").textContent = data.totals?.messages || 0;
  document.getElementById("totalFoxcoins").textContent = data.totals?.foxcoins || 0;
  document.getElementById("totalStars").textContent = data.totals?.stars || 0;
}

async function connectCreator() {
  const handle = (handleInput.value || "").trim().replace(/^@/, "");
  if (!handle) {
    connectMsg.textContent = "Enter a Blaze handle first.";
    return;
  }

  connectMsg.textContent = "Connecting creator...";

  const res = await fetch("/api/connected-creators/connect", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({ handle })
  });

  const data = await res.json();
  connectMsg.textContent = data.message || "Creator connected.";
  handleInput.value = "";
  await loadCreators();
}

async function addDemo() {
  const res = await fetch("/api/connected-creators/demo", { method: "POST" });
  const data = await res.json();
  connectMsg.textContent = data.message || "Demo creator added.";
  await loadCreators();
}

refreshBtn?.addEventListener("click", loadCreators);
connectBtn?.addEventListener("click", connectCreator);
demoBtn?.addEventListener("click", addDemo);
handleInput?.addEventListener("keydown", e => {
  if (e.key === "Enter") connectCreator();
});

loadCreators().catch(err => {
  grid.innerHTML = `<p class="sub">Could not load creators: ${safeText(err.message)}</p>`;
});
