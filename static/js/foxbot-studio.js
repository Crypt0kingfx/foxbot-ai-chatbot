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

function setAnalyticsText(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value;
}

function setAnalyticsBar(id, value, max) {
  const el = document.getElementById(id);
  if (!el) return;

  const safeValue = Number(value || 0);
  const safeMax = Math.max(Number(max || 1), 1);
  const width = Math.max(5, Math.min(100, Math.round((safeValue / safeMax) * 100)));

  el.style.width = width + "%";
}

function renderAnalyticsList(id, rows, labelKey, valueKey, emptyText) {
  const list = document.getElementById(id);
  if (!list) return;

  if (!rows || rows.length === 0) {
    list.innerHTML = `<div class="analytics-empty">${emptyText}</div>`;
    return;
  }

  list.innerHTML = rows.map((row, index) => {
    const label = row[labelKey] || row.display_name || row.name || row.command || ("Item " + (index + 1));
    const value = row[valueKey] || row.commands || row.value || 0;

    return `
      <div class="analytics-list-item">
        <span>#${index + 1} ${label}</span>
        <strong>${value}</strong>
      </div>
    `;
  }).join("");
}

async function refreshAnalyticsCenter() {
  try {
    const [studioRes, viewerRes, foxcoinsRes, arcadeRes] = await Promise.all([
      fetch("/api/studio/stats/live"),
      fetch("/viewer-stats"),
      fetch("/foxcoins"),
      fetch("/arcade-stats")
    ]);

    const studio = await studioRes.json();
    const viewers = await viewerRes.json();
    const foxcoins = await foxcoinsRes.json();
    const arcade = await arcadeRes.json();

    const followers = Number(studio.followersToday || 0);
    const subs = Number(studio.subsToday || 0);
    const votes = Number(studio.votesToday || 0);
    const foxcoinsToday = Number(studio.foxcoinsToday || 0);

    setAnalyticsText("analyticsFollowersToday", followers);
    setAnalyticsText("analyticsSubsToday", subs);
    setAnalyticsText("analyticsVotesToday", votes);
    setAnalyticsText("analyticsTipsToday", studio.tipsToday || "$0");
    setAnalyticsText("analyticsFoxCoinsToday", foxcoinsToday);
    setAnalyticsText("analyticsViewerCount", viewers.viewer_count || 0);

    const arcadeStats = arcade.stats || {};
    setAnalyticsText("analyticsArcadePlays", arcadeStats.plays || 0);

    const balances = foxcoins.balances || {};
    const transactions = foxcoins.transactions || [];
    const dailyClaims = foxcoins.daily_claims || {};

    setAnalyticsText("analyticsBalanceCount", Object.keys(balances).length);
    setAnalyticsText("analyticsCurrencyName", foxcoins.currency_name || "FoxCoins");
    setAnalyticsText("analyticsEconomyBalances", Object.keys(balances).length);
    setAnalyticsText("analyticsTransactions", transactions.length || 0);
    setAnalyticsText("analyticsDailyClaims", Object.keys(dailyClaims).length);

    setAnalyticsText("barFollowersValue", followers);
    setAnalyticsText("barSubsValue", subs);
    setAnalyticsText("barVotesValue", votes);
    setAnalyticsText("barFoxCoinsValue", foxcoinsToday);

    const maxEngagement = Math.max(followers, subs, votes, 1);
    setAnalyticsBar("barFollowers", followers, maxEngagement);
    setAnalyticsBar("barSubs", subs, maxEngagement);
    setAnalyticsBar("barVotes", votes, maxEngagement);
    setAnalyticsBar("barFoxCoins", foxcoinsToday, Math.max(foxcoinsToday, 1000));

    renderAnalyticsList(
      "analyticsLeaderboard",
      (viewers.leaderboard || []).slice(0, 5),
      "display_name",
      "commands",
      "No viewer command activity yet."
    );

    const arcadeRows = Object.entries(arcadeStats)
      .filter(([key]) => key !== "plays")
      .map(([key, value]) => ({ command: key, value }))
      .sort((a, b) => Number(b.value || 0) - Number(a.value || 0))
      .slice(0, 6);

    renderAnalyticsList(
      "analyticsArcadeList",
      arcadeRows,
      "command",
      "value",
      "No arcade activity yet."
    );

    previewAnalyticsReport("refreshed");
  } catch (error) {
    previewAnalyticsReport("error");
  }
}

function previewAnalyticsReport(mode) {
  const preview = document.getElementById("analyticsReportPreview");
  if (!preview) return;

  const data = {
    summary: {
      icon: "📊",
      title: "Stream Summary Preview",
      message: "FoxBot will summarize growth, rewards, activity, and community engagement."
    },
    growth: {
      icon: "📈",
      title: "Growth Report",
      message: "Tracks followers, subs, votes, tips, and overall support trends."
    },
    rewards: {
      icon: "💰",
      title: "Rewards Report",
      message: "Tracks FoxCoins generated, redemptions, streak bonuses, and event payouts."
    },
    refreshed: {
      icon: "✅",
      title: "Analytics Refreshed",
      message: "Live Studio, viewer, FoxCoins, and arcade data were loaded."
    },
    error: {
      icon: "⚠️",
      title: "Analytics Load Error",
      message: "One or more analytics endpoints did not respond."
    },
    reset: {
      icon: "📊",
      title: "Analytics Ready",
      message: "Refresh analytics or preview a stream summary."
    }
  };

  const item = data[mode] || data.summary;

  preview.innerHTML = `
    <div class="preview-orb">${item.icon}</div>
    <h3>${item.title}</h3>
    <p>${item.message}</p>
  `;
}

document.addEventListener("DOMContentLoaded", () => {
  setTimeout(refreshAnalyticsCenter, 600);
});

function getFoxAITone() {
  const el = document.getElementById("aiTone");
  return el ? el.value : "hype";
}

function getFoxAIOutputType() {
  const el = document.getElementById("aiOutputType");
  return el ? el.value : "stream";
}

function setFoxAIPrompt(text) {
  const input = document.getElementById("aiPrompt");
  if (input) input.value = text;
  generateFoxAI("stream_plan");
}

function writeFoxAIOutput(text) {
  const output = document.getElementById("aiOutput");
  if (!output) return;

  output.classList.add("generated");
  output.textContent = text;
}

function getFoxAIPrompt() {
  const input = document.getElementById("aiPrompt");
  return input && input.value.trim()
    ? input.value.trim()
    : "Make tonight's Blaze stream more exciting with FoxBot quests, rewards, events, and hype moments.";
}

function generateFoxAI(type) {
  const prompt = getFoxAIPrompt();
  const tone = getFoxAITone();
  const outputType = getFoxAIOutputType();

  const toneLine = `Tone: ${tone.toUpperCase()} | Output: ${outputType.toUpperCase()}`;
  const base = `Prompt: ${prompt}`;

  const templates = {
    stream_plan:
`${toneLine}

FOX AI STREAM PLAN
${base}

1. Opening Hook:
Welcome everyone in with Fox Spirit energy, remind chat to use !help, and tease a reward drop.

2. Engagement Loop:
Run !daily, !checkin, !quest, and !boss during key moments to keep chat active.

3. Reward Moment:
Trigger a FoxCoins bonus or Treasure Drop when chat gets active.

4. OBS Moment:
Use an overlay alert: "The Fox Spirit is watching... chat has activated bonus rewards."

5. Closing:
Thank MVPs, OGs, voters, subs, tippers, raiders, and remind everyone to follow for the next hunt.`,

    quest:
`${toneLine}

COMMUNITY QUEST IDEA
${base}

Quest Name: Fox Spirit Hunt
Command: !startquest chat 25
Goal: 25 chat interactions
Reward: 100 FoxCoins
Chat Message:
"Community Quest Started: Fox Spirit Hunt. Help the stream reach 25 chat actions and everyone can claim bonus FoxCoins!"`,

    reward:
`${toneLine}

REWARD SHOP IDEA
${base}

Reward Name: foxboost
Cost: 75 FoxCoins
Command Idea:
!addreward foxboost 75 @{username} activated Fox Boost! Chat gets extra hype and the Fox Spirit approves.

Use Case:
Great for viewers who want to trigger a fun stream moment without interrupting gameplay.`,

    event:
`${toneLine}

STREAM EVENT IDEA
${base}

Event Name: Cyber Fox Frenzy
Trigger: Mid-stream hype moment, raid, sub train, or big match win.
Viewer Action: Type !event to claim.
Reward: 50 FoxCoins
Overlay Text:
"CYBER FOX FRENZY ACTIVE — rewards boosted, chat energy unlocked."`,

    giveaway:
`${toneLine}

GIVEAWAY IDEA
${base}

Giveaway Name: Fox Spirit Drop
Entry Command: !enter
Bonus Entry Ideas:
- Follow the channel
- Vote/support the creator
- Stay active in chat
- Sub or gift sub for extra hype

Announcement:
"Fox Spirit Drop is live! Type !enter and stay active. Winner gets chosen before stream ends."`,

    shoutout:
`${toneLine}

SHOUTOUT MESSAGE
${base}

"HUGE Fox Spirit shoutout to @{viewer}! Thanks for showing love, supporting the stream, and keeping the Blaze community alive. Everyone go show them some love!"`,

    overlay:
`${toneLine}

OBS OVERLAY TEXT
${base}

Main Alert:
"FOX SPIRIT EVENT ACTIVATED"

Subtext:
"Chat rewards are live. Type !help, !daily, !quest, or !boss."

Lower Third:
"Powered by FoxBot Studio"`,

    commands:
`${toneLine}

COMMAND IDEAS
${base}

!foxboost — viewer spends FoxCoins to trigger hype.
!raidlove — welcome raiders with a special message.
!clipthat — asks chat to clip a huge moment.
!mvp — celebrates top viewer of the stream.
!nextdrop — shows the next giveaway or reward event.`,

    moderation:
`${toneLine}

MODERATION HELPER
${base}

Soft Warning:
"Keep it respectful, Fox Fam. We are here for good energy and a fun stream."

Spam Warning:
"Please slow down on spam so everyone can enjoy chat."

Final Warning:
"Last warning — keep the chat clean or mods may step in."`
  };

  writeFoxAIOutput(templates[type] || templates.stream_plan);
}

async function askFoxAIDemo() {
  const prompt = getFoxAIPrompt();

  try {
    const response = await fetch(`/chat?message=${encodeURIComponent("!ask " + prompt)}&username=${encodeURIComponent("Ryan")}`);
    const data = await response.json();
    writeFoxAIOutput(`FOX AI DEMO RESPONSE\n\n${data.response || "No response returned."}`);
  } catch (error) {
    writeFoxAIOutput("Fox AI demo request failed. The local generators still work.");
  }
}

async function runFoxAICommand(command) {
  try {
    const response = await fetch(`/chat?message=${encodeURIComponent(command)}&username=${encodeURIComponent("Ryan")}`);
    const data = await response.json();
    writeFoxAIOutput(`COMMAND TEST: ${command}\n\n${data.response || "No response returned."}`);
  } catch (error) {
    writeFoxAIOutput(`Command test failed: ${command}`);
  }
}

async function copyFoxAIOutput() {
  const output = document.getElementById("aiOutput");
  if (!output) return;

  const text = output.innerText || output.textContent || "";

  try {
    await navigator.clipboard.writeText(text);
    addFeed("🧠 Fox AI output copied.");
  } catch (error) {
    addFeed("⚠️ Could not copy Fox AI output.");
  }
}

function getFoxAITone() {
  const el = document.getElementById("aiTone");
  return el ? el.value : "hype";
}

function getFoxAIOutputType() {
  const el = document.getElementById("aiOutputType");
  return el ? el.value : "stream";
}

function setFoxAIPrompt(text) {
  const input = document.getElementById("aiPrompt");
  if (input) input.value = text;
  generateFoxAI("stream_plan");
}

function writeFoxAIOutput(text) {
  const output = document.getElementById("aiOutput");
  if (!output) return;

  output.classList.add("generated");
  output.textContent = text;
}

function getFoxAIPrompt() {
  const input = document.getElementById("aiPrompt");
  return input && input.value.trim()
    ? input.value.trim()
    : "Make tonight's Blaze stream more exciting with FoxBot quests, rewards, events, and hype moments.";
}

function generateFoxAI(type) {
  const prompt = getFoxAIPrompt();
  const tone = getFoxAITone();
  const outputType = getFoxAIOutputType();

  const toneLine = `Tone: ${tone.toUpperCase()} | Output: ${outputType.toUpperCase()}`;
  const base = `Prompt: ${prompt}`;

  const templates = {
    stream_plan:
`${toneLine}

FOX AI STREAM PLAN
${base}

1. Opening Hook:
Welcome everyone in with Fox Spirit energy, remind chat to use !help, and tease a reward drop.

2. Engagement Loop:
Run !daily, !checkin, !quest, and !boss during key moments to keep chat active.

3. Reward Moment:
Trigger a FoxCoins bonus or Treasure Drop when chat gets active.

4. OBS Moment:
Use an overlay alert: "The Fox Spirit is watching... chat has activated bonus rewards."

5. Closing:
Thank MVPs, OGs, voters, subs, tippers, raiders, and remind everyone to follow for the next hunt.`,

    quest:
`${toneLine}

COMMUNITY QUEST IDEA
${base}

Quest Name: Fox Spirit Hunt
Command: !startquest chat 25
Goal: 25 chat interactions
Reward: 100 FoxCoins
Chat Message:
"Community Quest Started: Fox Spirit Hunt. Help the stream reach 25 chat actions and everyone can claim bonus FoxCoins!"`,

    reward:
`${toneLine}

REWARD SHOP IDEA
${base}

Reward Name: foxboost
Cost: 75 FoxCoins
Command Idea:
!addreward foxboost 75 @{username} activated Fox Boost! Chat gets extra hype and the Fox Spirit approves.

Use Case:
Great for viewers who want to trigger a fun stream moment without interrupting gameplay.`,

    event:
`${toneLine}

STREAM EVENT IDEA
${base}

Event Name: Cyber Fox Frenzy
Trigger: Mid-stream hype moment, raid, sub train, or big match win.
Viewer Action: Type !event to claim.
Reward: 50 FoxCoins
Overlay Text:
"CYBER FOX FRENZY ACTIVE — rewards boosted, chat energy unlocked."`,

    giveaway:
`${toneLine}

GIVEAWAY IDEA
${base}

Giveaway Name: Fox Spirit Drop
Entry Command: !enter
Bonus Entry Ideas:
- Follow the channel
- Vote/support the creator
- Stay active in chat
- Sub or gift sub for extra hype

Announcement:
"Fox Spirit Drop is live! Type !enter and stay active. Winner gets chosen before stream ends."`,

    shoutout:
`${toneLine}

SHOUTOUT MESSAGE
${base}

"HUGE Fox Spirit shoutout to @{viewer}! Thanks for showing love, supporting the stream, and keeping the Blaze community alive. Everyone go show them some love!"`,

    overlay:
`${toneLine}

OBS OVERLAY TEXT
${base}

Main Alert:
"FOX SPIRIT EVENT ACTIVATED"

Subtext:
"Chat rewards are live. Type !help, !daily, !quest, or !boss."

Lower Third:
"Powered by FoxBot Studio"`,

    commands:
`${toneLine}

COMMAND IDEAS
${base}

!foxboost — viewer spends FoxCoins to trigger hype.
!raidlove — welcome raiders with a special message.
!clipthat — asks chat to clip a huge moment.
!mvp — celebrates top viewer of the stream.
!nextdrop — shows the next giveaway or reward event.`,

    moderation:
`${toneLine}

MODERATION HELPER
${base}

Soft Warning:
"Keep it respectful, Fox Fam. We are here for good energy and a fun stream."

Spam Warning:
"Please slow down on spam so everyone can enjoy chat."

Final Warning:
"Last warning — keep the chat clean or mods may step in."`
  };

  writeFoxAIOutput(templates[type] || templates.stream_plan);
}

async function askFoxAIDemo() {
  const prompt = getFoxAIPrompt();

  try {
    const response = await fetch(`/chat?message=${encodeURIComponent("!ask " + prompt)}&username=${encodeURIComponent("Ryan")}`);
    const data = await response.json();
    writeFoxAIOutput(`FOX AI DEMO RESPONSE\n\n${data.response || "No response returned."}`);
  } catch (error) {
    writeFoxAIOutput("Fox AI demo request failed. The local generators still work.");
  }
}

async function runFoxAICommand(command) {
  try {
    const response = await fetch(`/chat?message=${encodeURIComponent(command)}&username=${encodeURIComponent("Ryan")}`);
    const data = await response.json();
    writeFoxAIOutput(`COMMAND TEST: ${command}\n\n${data.response || "No response returned."}`);
  } catch (error) {
    writeFoxAIOutput(`Command test failed: ${command}`);
  }
}

async function copyFoxAIOutput() {
  const output = document.getElementById("aiOutput");
  if (!output) return;

  const text = output.innerText || output.textContent || "";

  try {
    await navigator.clipboard.writeText(text);
    addFeed("🧠 Fox AI output copied.");
  } catch (error) {
    addFeed("⚠️ Could not copy Fox AI output.");
  }
}

function openDiagnosticsUrl(path) {
  window.open(path, "_blank", "noopener,noreferrer");
}

function writeDiagnosticsOutput(title, data) {
  const output = document.getElementById("diagnosticsOutput");
  if (!output) return;

  const body = typeof data === "string" ? data : JSON.stringify(data, null, 2);
  output.textContent = `${title}\n\n${body}`;
}

function clearDiagnosticsOutput() {
  writeDiagnosticsOutput("Diagnostics ready.", "Run a health check.");
}

async function fetchDiagnosticsJSON(path) {
  const response = await fetch(path);
  const text = await response.text();

  try {
    return JSON.parse(text);
  } catch (error) {
    return {
      ok: response.ok,
      status: response.status,
      text
    };
  }
}

async function runDiagnosticsCheck(path) {
  writeDiagnosticsOutput(`Checking ${path}...`, "Loading...");

  try {
    const data = await fetchDiagnosticsJSON(path);
    writeDiagnosticsOutput(`Result: ${path}`, data);
    refreshDiagnosticsCenter();
  } catch (error) {
    writeDiagnosticsOutput(`Error: ${path}`, String(error));
  }
}

async function runDiagnosticsCommand(command) {
  const path = `/chat?message=${encodeURIComponent(command)}&username=${encodeURIComponent("Ryan")}`;
  writeDiagnosticsOutput(`Testing command ${command}...`, "Loading...");

  try {
    const data = await fetchDiagnosticsJSON(path);
    writeDiagnosticsOutput(`Command Result: ${command}`, data);
    refreshDiagnosticsCenter();
  } catch (error) {
    writeDiagnosticsOutput(`Command Error: ${command}`, String(error));
  }
}

async function refreshDiagnosticsCenter() {
  try {
    const proof = await fetchDiagnosticsJSON("/proof");
    const proofData = proof.proof || {};

    const connected = proofData.blaze_connected ? "Yes" : "No";
    const listener = proofData.listener_running ? "Running" : "Stopped";

    const connectedEl = document.getElementById("diagBlazeConnected");
    const listenerEl = document.getElementById("diagListenerStatus");
    const checkedEl = document.getElementById("diagMessagesChecked");
    const commandsEl = document.getElementById("diagCommandsProcessed");

    if (connectedEl) connectedEl.textContent = connected;
    if (listenerEl) listenerEl.textContent = listener;
    if (checkedEl) checkedEl.textContent = proofData.messages_checked ?? 0;
    if (commandsEl) commandsEl.textContent = proofData.commands_processed ?? 0;

    writeDiagnosticsOutput("Health Check: /proof", proof);
  } catch (error) {
    writeDiagnosticsOutput("Diagnostics health check failed.", String(error));
  }
}

async function runDiagnosticsSuite() {
  const endpoints = [
    "/proof",
    "/project-status",
    "/data-status",
    "/api/studio/stats/live",
    "/blaze/polling-status",
    "/foxcoins",
    "/rewards",
    "/redemptions"
  ];

  const results = [];

  writeDiagnosticsOutput("Running Diagnostics Mini Suite...", "Checking endpoints...");

  for (const endpoint of endpoints) {
    try {
      const data = await fetchDiagnosticsJSON(endpoint);
      results.push({
        endpoint,
        ok: true,
        status: "PASS",
        keys: data && typeof data === "object" ? Object.keys(data).slice(0, 8) : []
      });
    } catch (error) {
      results.push({
        endpoint,
        ok: false,
        status: "FAIL",
        error: String(error)
      });
    }
  }

  writeDiagnosticsOutput("Diagnostics Mini Suite Results", results);
  refreshDiagnosticsCenter();
}

document.addEventListener("DOMContentLoaded", () => {
  setTimeout(refreshDiagnosticsCenter, 900);
});
