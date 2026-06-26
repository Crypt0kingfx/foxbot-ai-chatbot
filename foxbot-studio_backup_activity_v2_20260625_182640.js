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
    addFeed(`🔌 Blaze Service: ${data.message || data.mapped_event || action}`);
    loadStudioStats();
  } catch (err) {
    addFeed(`⚠️ Blaze Service failed: ${action}`);
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
    addFeed("⚠️ Failed to clear activity feed.");
  }
}
