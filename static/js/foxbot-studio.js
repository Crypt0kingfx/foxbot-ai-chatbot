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

function previewStreakOverlay(mode) {
  const preview = document.getElementById("streakOverlayPreview");
  if (!preview) return;

  const data = {
    leaderboard: {
      icon: "🏆",
      title: "Top Streak Leaderboard",
      message: "MVP Viewer leads the pack with a 30-day streak."
    },
    daily_checkin: {
      icon: "🔥",
      title: "Daily Check-In Claimed",
      message: "A loyal viewer checked in and extended their streak."
    },
    award_bonus: {
      icon: "💰",
      title: "Streak Bonus Awarded",
      message: "FoxCoins have been awarded for consistent support."
    },
    repair_streak: {
      icon: "🛠️",
      title: "Streak Repaired",
      message: "Admin protection restored a viewer streak."
    },
    reset_streak: {
      icon: "⚠️",
      title: "Streak Reset Tool",
      message: "Use carefully. This is for admin corrections only."
    },
    boost_mvp: {
      icon: "👑",
      title: "MVP Streak Boost",
      message: "MVP multiplier activated for a loyal supporter."
    },
    boost_og: {
      icon: "🦊",
      title: "OG Streak Boost",
      message: "OG community boost activated."
    },
    reset: {
      icon: "🔥",
      title: "Streak System Ready",
      message: "Viewer check-ins and streak rewards will appear here."
    }
  };

  const item = data[mode] || data.reset;

  preview.innerHTML = `
    <div class="preview-orb">${item.icon}</div>
    <h3>${item.title}</h3>
    <p>${item.message}</p>
  `;
}

function triggerStreakAction(action) {
  previewStreakOverlay(action);

  if (typeof studioAction === "function") {
    studioAction(action);
  }

  const currentEvent = document.getElementById("currentEvent");
  if (currentEvent) {
    currentEvent.textContent = "Streak: " + action.replaceAll("_", " ");
  }
}

function previewRewardOverlay(mode) {
  const preview = document.getElementById("rewardOverlayPreview");
  if (!preview) return;

  const data = {
    redemption: {
      icon: "🎁",
      title: "Reward Redeemed",
      message: "A viewer spent FoxCoins and triggered a reward."
    },
    bonus_drop: {
      icon: "💰",
      title: "Bonus Drop Activated",
      message: "FoxBot dropped bonus FoxCoins for the community."
    },
    mvp_bonus: {
      icon: "👑",
      title: "MVP Bonus Awarded",
      message: "A top supporter earned a special MVP reward."
    },
    og_bonus: {
      icon: "🦊",
      title: "OG Bonus Awarded",
      message: "An original Fox Spirit earned an OG loyalty reward."
    },
    streak_reward: {
      icon: "🔥",
      title: "Streak Reward Paid",
      message: "A loyal viewer earned FoxCoins for showing up."
    },
    event_reward: {
      icon: "⚡",
      title: "Stream Event Reward",
      message: "A live event paid out bonus FoxCoins."
    },
    giveaway_entry: {
      icon: "🎟️",
      title: "Giveaway Entry Added",
      message: "A viewer earned an extra giveaway entry."
    },
    clear_queue: {
      icon: "🧹",
      title: "Reward Queue Cleared",
      message: "Pending redemptions have been cleared by admin."
    },
    reset: {
      icon: "🎁",
      title: "Rewards System Ready",
      message: "FoxCoins payouts and redemptions will appear here."
    }
  };

  const item = data[mode] || data.reset;

  preview.innerHTML = `
    <div class="preview-orb">${item.icon}</div>
    <h3>${item.title}</h3>
    <p>${item.message}</p>
  `;
}

function triggerRewardAction(action) {
  previewRewardOverlay(action);

  if (typeof studioAction === "function") {
    studioAction(action);
  }

  const currentEvent = document.getElementById("currentEvent");
  if (currentEvent) {
    currentEvent.textContent = "Reward: " + action.replaceAll("_", " ");
  }
}

function getOverlayAbsoluteUrl(path) {
  return window.location.origin + path;
}

function openOverlayPage(path) {
  window.open(path, "_blank", "noopener,noreferrer");
}

async function copyOverlayUrl(path) {
  const url = getOverlayAbsoluteUrl(path);

  try {
    await navigator.clipboard.writeText(url);
    previewOverlayCard("copied", url);
  } catch (error) {
    previewOverlayCard("copy_failed", url);
  }
}

function previewOverlayCard(type, url) {
  const preview = document.getElementById("overlayPreviewPanel");
  if (!preview) return;

  const data = {
    giveaway: {
      icon: "🎁",
      title: "Giveaway Overlay",
      message: "Shows prize, latest entry, total entries, and winner."
    },
    redemptions: {
      icon: "💎",
      title: "Redemptions Overlay",
      message: "Shows recent FoxCoins reward redemptions."
    },
    boss: {
      icon: "👑",
      title: "Boss Battle Overlay",
      message: "Shows boss HP, battle state, damage board, and defeated count."
    },
    events: {
      icon: "⚡",
      title: "Stream Events Overlay",
      message: "Future overlay for Golden Fox, Spirit Storm, Treasure Drop, and Fox Frenzy."
    },
    streaks: {
      icon: "🔥",
      title: "Streak Overlay",
      message: "Future overlay for check-ins, streak leaders, MVPs, and OGs."
    },
    activity: {
      icon: "📡",
      title: "Activity Feed Overlay",
      message: "Future overlay for follows, subs, tips, votes, raids, and shoutouts."
    },
    copied: {
      icon: "✅",
      title: "OBS URL Copied",
      message: url || "Overlay URL copied to clipboard."
    },
    copy_failed: {
      icon: "⚠️",
      title: "Copy Failed",
      message: url || "Copy failed. Manually copy the overlay path."
    },
    reset: {
      icon: "🎬",
      title: "OBS Overlay Manager Ready",
      message: "Select an overlay preview or copy a browser-source URL."
    }
  };

  const item = data[type] || data.reset;

  preview.innerHTML = `
    <div class="preview-orb">${item.icon}</div>
    <h3>${item.title}</h3>
    <p>${item.message}</p>
  `;
}
