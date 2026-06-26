function showSection(id) {
  document.querySelectorAll(".section").forEach(s => s.classList.remove("active"));
  document.querySelectorAll(".nav button").forEach(b => b.classList.remove("active"));

  const section = document.getElementById(id);
  const button = document.querySelector(`[data-target="${id}"]`);

  if (section) section.classList.add("active");
  if (button) button.classList.add("active");
}

async function studioAction(action) {
  try {
    const res = await fetch(`/api/studio/action/${action}`, { method: "POST" });
    const data = await res.json();
    addFeed(`?? ${data.message || action + " triggered"}`);
  } catch (err) {
    addFeed(`?? Failed: ${action}`);
  }
}

function addFeed(text) {
  const feed = document.getElementById("activityFeed");
  if (!feed) return;

  const item = document.createElement("div");
  item.className = "feed-item";
  item.innerHTML = `<strong>${new Date().toLocaleTimeString()}</strong><br>${text}`;
  feed.prepend(item);
}

async function loadStudioStats() {
  try {
    const res = await fetch("/api/studio/stats");
    const data = await res.json();

    for (const [key, value] of Object.entries(data)) {
      const el = document.getElementById(key);
      if (el) el.textContent = value;
    }
  } catch (err) {
    console.log("Stats unavailable");
  }
}

document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll(".nav button").forEach(btn => {
    btn.addEventListener("click", () => showSection(btn.dataset.target));
  });

  loadStudioStats();
  setInterval(loadStudioStats, 5000);
});

async function blazeService(action) {
  let url = "/api/blaze/service/status";
  let options = { method: "GET" };

  if (action === "connect") {
    url = "/api/blaze/service/connect";
    options = { method: "POST" };
  }

  if (action === "disconnect") {
    url = "/api/blaze/service/disconnect";
    options = { method: "POST" };
  }

  if (action === "test_follow") {
    url = "/api/blaze/service/event";
    options = {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ type: "follow", user: "Ryan" })
    };
  }

  try {
    const res = await fetch(url, options);
    const data = await res.json();
    addFeed(`ðŸ”Œ Blaze Service: ${data.message || data.mapped_event || action}`);
    loadStudioStats();
  } catch (err) {
    addFeed(`âš ï¸ Blaze Service failed: ${action}`);
  }
}

function toggleSidebar() {
  const studio = document.querySelector(".studio");
  if (!studio) return;

  studio.classList.toggle("sidebar-collapsed");

  const collapsed = studio.classList.contains("sidebar-collapsed");
  localStorage.setItem("foxbotSidebarCollapsed", collapsed ? "1" : "0");
}

document.addEventListener("DOMContentLoaded", () => {
  const studio = document.querySelector(".studio");
  if (studio && localStorage.getItem("foxbotSidebarCollapsed") === "1") {
    studio.classList.add("sidebar-collapsed");
  }
});

async function clearActivity() {
  try {
    await fetch("/api/studio/activity/clear", { method: "POST" });
    loadStudioStats();
  } catch (err) {
    addFeed("âš ï¸ Failed to clear activity feed.");
  }
}

function renderActivityItem(item) {
  const icon = item.icon || "ðŸ¦Š";
  const title = item.title || item.message || "FoxBot event";
  const detail = item.detail || "";
  const time = item.time || "Now";
  const type = item.type || "default";

  return `
    <div class="feed-item feed-${type}">
      <div class="feed-icon">${icon}</div>
      <div class="feed-content">
        <strong>${time}</strong>
        <div class="feed-title">${title}</div>
        ${detail ? `<div class="feed-detail">${detail}</div>` : ""}
      </div>
    </div>
  `;
}

async function demoActivity() {
  try {
    await fetch("/api/studio/activity/demo", { method: "POST" });
    loadStudioStats();
  } catch (err) {
    addFeed("âš ï¸ Failed to create demo activity.");
  }
}



function getStreamEventLabel(eventKey) {
  const labels = {
    golden_fox: "Golden Fox",
    spirit_storm: "Spirit Storm",
    treasure_drop: "Treasure Drop",
    fox_frenzy: "Fox Frenzy",
    random: "Random Event",
    reset: "FoxBot Stream Event Ready"
  };
  return labels[eventKey] || "Stream Event";
}

function getStreamEventIcon(eventKey) {
  const icons = {
    golden_fox: "🦊",
    spirit_storm: "🌩️",
    treasure_drop: "💰",
    fox_frenzy: "🔥",
    random: "🎲",
    reset: "🦊"
  };
  return icons[eventKey] || "⚡";
}

function getStreamEventMessage(eventKey) {
  const messages = {
    golden_fox: "A rare Golden Fox has appeared! Viewers earn bonus FoxCoins.",
    spirit_storm: "Spirit Storm is live! Active chatters get rewarded.",
    treasure_drop: "Treasure Drop activated! Bonus rewards are falling.",
    fox_frenzy: "Fox Frenzy has started! Huge hype rewards are active.",
    random: "FoxBot rolled a weighted random stream event.",
    reset: "Select an event preview or trigger one live."
  };
  return messages[eventKey] || "FoxBot stream event ready.";
}

function previewStreamEvent(eventKey) {
  const preview = document.getElementById("streamEventPreview");
  if (!preview) return;

  const label = getStreamEventLabel(eventKey);
  const icon = getStreamEventIcon(eventKey);
  const message = getStreamEventMessage(eventKey);

  preview.innerHTML = `
    <div class="preview-orb">${icon}</div>
    <h3>${label}</h3>
    <p>${message}</p>
  `;
}

function triggerStreamEvent(eventKey) {
  previewStreamEvent(eventKey);

  if (typeof studioAction === "function") {
    studioAction(eventKey === "random" ? "random_event" : eventKey);
  }

  const currentEvent = document.getElementById("currentEvent");
  if (currentEvent) {
    currentEvent.textContent = getStreamEventLabel(eventKey);
  }
}
