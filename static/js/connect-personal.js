// FoxBot Connect — personal creator experience.
// Remembers which creator is browsing (localStorage / ?me= from OAuth login)
// and turns the public Connected Creators hub into their personal den.
(function () {
  var STORAGE_KEY = "foxbot_me";

  var heroDefault = document.getElementById("heroDefault");
  var heroPersonal = document.getElementById("heroPersonal");
  if (!heroDefault || !heroPersonal) return;

  var params = new URLSearchParams(window.location.search);
  var justConnected = params.get("connected") === "1";

  var fromLogin = cleanHandle(params.get("me") || "");
  if (fromLogin) {
    try { localStorage.setItem(STORAGE_KEY, fromLogin); } catch (e) {}
    // Drop ?me= from the address bar so the link stays shareable.
    try {
      window.history.replaceState({}, "", window.location.pathname);
    } catch (e) {}
  }

  var me = "";
  try { me = cleanHandle(localStorage.getItem(STORAGE_KEY) || ""); } catch (e) {}

  if (me) loadProfile(me, false);

  var claimBtn = document.getElementById("claimBtn");
  var claimInput = document.getElementById("claimInput");
  if (claimBtn && claimInput) {
    claimBtn.addEventListener("click", function () {
      claimFromInput();
    });
    claimInput.addEventListener("keydown", function (event) {
      if (event.key === "Enter") claimFromInput();
    });
  }

  var signoutBtn = document.getElementById("meSignout");
  if (signoutBtn) {
    signoutBtn.addEventListener("click", function () {
      try { localStorage.removeItem(STORAGE_KEY); } catch (e) {}
      window.location.href = window.location.pathname;
    });
  }

  function cleanHandle(value) {
    return String(value || "").trim().replace(/^@+/, "").toLowerCase().slice(0, 40);
  }

  function claimFromInput() {
    var handle = cleanHandle(claimInput.value);
    var msg = document.getElementById("claimMsg");
    if (!handle) {
      if (msg) msg.textContent = "Type your Blaze handle first.";
      return;
    }
    if (msg) msg.textContent = "Sniffing around for @" + handle + "…";
    loadProfile(handle, true);
  }

  function loadProfile(handle, save) {
    fetch("/api/connected-creators/me?handle=" + encodeURIComponent(handle))
      .then(function (res) { return res.json(); })
      .then(function (data) {
        var msg = document.getElementById("claimMsg");
        if (!data || !data.ok || !data.creator) {
          if (msg) {
            msg.textContent =
              "That handle isn't connected yet — follow FoxBot on Blaze and type !connect in chat first.";
          }
          if (!save) {
            // Stored identity no longer exists; forget it quietly.
            try { localStorage.removeItem(STORAGE_KEY); } catch (e) {}
          }
          return;
        }
        if (save) {
          try { localStorage.setItem(STORAGE_KEY, handle); } catch (e) {}
        }
        renderPersonal(data);
      })
      .catch(function () {
        var msg = document.getElementById("claimMsg");
        if (msg) msg.textContent = "Could not reach FoxBot right now. Try again in a moment.";
      });
  }

  function greetingForHour(hour) {
    if (hour < 5) return "Burning the midnight oil";
    if (hour < 12) return "Good morning";
    if (hour < 18) return "Good afternoon";
    return "Good evening";
  }

  function relativeTime(iso) {
    if (!iso) return "";
    var then = new Date(iso);
    if (isNaN(then.getTime())) return "";
    var seconds = Math.max(0, (Date.now() - then.getTime()) / 1000);
    if (seconds < 90) return "just now";
    var minutes = Math.round(seconds / 60);
    if (minutes < 60) return minutes + " min ago";
    var hours = Math.round(minutes / 60);
    if (hours < 48) return hours + "h ago";
    var days = Math.round(hours / 24);
    return days + " days ago";
  }

  function renderPersonal(data) {
    var creator = data.creator;
    var handle = String(creator.handle || "creator");
    var displayName = String(creator.display_name || "");

    setText("meName", handle);
    setText("meAvatar", (handle.charAt(0) || "?").toUpperCase());

    var greeting = greetingForHour(new Date().getHours());
    if (justConnected) {
      setText("meGreeting", "You're in! Welcome to the Fox Spirits pack 🎉");
    } else {
      setText("meGreeting", greeting + ", welcome back to your den 🦊");
    }

    var metaParts = [];
    if (displayName && displayName.toLowerCase() !== handle.toLowerCase()) {
      metaParts.push(displayName);
    }
    if (data.rank) {
      metaParts.push("Rank #" + data.rank + " of " + (data.creator_count || "?") + " creators");
    }
    if (typeof data.days_connected === "number") {
      metaParts.push(
        data.days_connected < 1
          ? "Joined the pack today"
          : "In the pack for " + data.days_connected + " day" + (data.days_connected === 1 ? "" : "s")
      );
    }
    var seen = relativeTime(creator.last_seen_at);
    if (seen) metaParts.push("Last seen " + seen);
    setText("meMeta", metaParts.join(" · "));

    setText("meFoxcoins", String(creator.foxcoins || 0));
    setText("meMessages", String(creator.messages || 0));
    setText("meStars", String(creator.stars || 0));
    setText("meRank", data.rank ? "#" + data.rank : "—");

    var badgesEl = document.getElementById("meBadges");
    if (badgesEl) {
      badgesEl.innerHTML = "";
      (creator.badges || []).concat(creator.commands || []).forEach(function (label) {
        var tag = document.createElement("span");
        tag.className = "tag";
        tag.textContent = String(label);
        badgesEl.appendChild(tag);
      });
    }

    heroDefault.hidden = true;
    heroPersonal.hidden = false;

    highlightMyCard(handle);
  }

  function highlightMyCard(handle) {
    var grid = document.querySelector(".grid");
    if (!grid) return;
    var cards = grid.querySelectorAll(".card[data-handle]");
    for (var i = 0; i < cards.length; i++) {
      var card = cards[i];
      var isMe = String(card.getAttribute("data-handle") || "").toLowerCase() === handle.toLowerCase();
      card.classList.toggle("is-me", isMe);
      var existing = card.querySelector(".you-pill");
      if (existing) existing.remove();
      if (isMe) {
        var handleEl = card.querySelector(".handle");
        if (handleEl) {
          var pill = document.createElement("span");
          pill.className = "you-pill";
          pill.textContent = "That's you";
          handleEl.appendChild(pill);
        }
        grid.prepend(card);
      }
    }
  }

  function setText(id, text) {
    var el = document.getElementById(id);
    if (el) el.textContent = text;
  }
})();
