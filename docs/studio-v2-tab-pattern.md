# Studio v2 — Reusable Tab Pattern

Established while building the Analytics tab (the pilot), after an earlier
attempt with Bot Control was abandoned mid-plan because one of its actions
(the native-connector live-reply toggle) turned out to have no equivalent
on the live system, meaning that tab needs new backend work before it can
migrate honestly. Analytics has none of that — every endpoint it reads
already works — which is what makes it safe as the template.

Follow this pattern for every subsequent tab. Don't reinvent the
structure per tab; copy this.

## 1. Page structure

Each tab is one `<section class="page" id="page-<name>" hidden>`, a
sibling of every other page inside `<main class="main">`. Exactly one
`.page` is visible at a time — the active one has no `hidden` attribute
(Overview, the first tab, ships as `class="page active" id="page-overview"`
with no `hidden`, since it's the default view).

```html
<section class="page" id="page-analytics" hidden>
  <div class="page-head">
    <h1 class="page-title">Analytics</h1>
    <p class="page-sub">One sentence describing what's here.</p>
  </div>
  <!-- cards go here -->
</section>
```

Sidebar wiring: give the nav button a `data-target` matching the page's
`id`:

```html
<button class="item" data-target="page-analytics">Analytics</button>
```

Switching is handled once, generically, for every tab —
`templates/foxbot_studio_v2.html`'s bottom `<script>`:

```js
function showPage(id) {
  var target = document.getElementById(id);
  if (!target || !target.classList.contains('page')) return false;
  document.querySelectorAll('.page').forEach(function (p) { p.hidden = (p !== target); });
  return true;
}
```

Nav buttons with no `data-target` (tabs not built yet) just take the
active-highlight and leave whatever page is currently shown untouched —
they never relabel a real page's title over content that hasn't actually
changed. Don't add per-tab title-rewriting logic; `showPage()` is the
only routing mechanism, reused verbatim by every tab.

## 2. One card per data source

This is the load-bearing rule. Each `<section class="card">` maps to
**exactly one** backend endpoint. Never blend two fetches into one card
— if you do, a failure in one silently blanks data that was actually
fine, and a reader can't tell which source broke.

Analytics is four cards, four endpoints, no exceptions:

| Card | Endpoint | Notes |
|---|---|---|
| Today | `GET /api/studio/stats/live` | Real `STUDIO_STATE` counters only — `botOnline`/`recognitionEnabled` deliberately excluded (see §4) |
| Top Viewers | `GET /viewer-stats` | Pre-sorted leaderboard, no client-side re-sort needed |
| Arcade | `GET /arcade-stats` | Nested under `data.stats` |
| Economy | `GET /foxcoins` | Two lists (balances, transactions) from one endpoint — still one card, since it's one fetch |

Note Economy is one card with two lists, not two cards — the rule is
**one fetch per card**, not one visual list per card. If a page needs
data from two independent fetches side by side, that's two cards.

## 3. Fetch + render contract (honest-failure discipline)

Every poll function follows this exact shape — copy it, don't
paraphrase it:

```js
async function poll<X>() {
  try {
    var res = await fetch('/endpoint', { cache: 'no-store' });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    var data = await res.json();
    // render real data into the DOM
  } catch (err) {
    // render an explicit, visible error state
  }
}
```

Three outcomes only, ever:

1. **Real data** — rendered as-is from the response, no synthesis.
2. **Explicit error** — fetch failure, non-200, or `ok:false` in the
   body. Never leave a stale/frozen "last good" value on screen; never
   silently show nothing. `.dismiss-error`/`.feed-error` (already
   defined) are the two existing visible-error styles — reuse them,
   don't invent a third.
3. **Genuine empty** — the request succeeded and the data is real but
   empty (e.g. no transactions yet). This is not an error. Say so
   plainly ("No viewer activity yet.") — distinct wording from the
   error case, so a reader can tell "nothing has happened" from
   "something's broken."

KPI-shaped values (single numbers) reuse the existing `.stats`/`.stat`/
`.stat-label`/`.stat-num` tile grid. Variable-length lists (leaderboards,
transaction logs, feeds) reuse the existing `.feed-item`/`.feed-time`/
`.feed-body` row pattern from the Overview feed. Don't invent a new
visual vocabulary per tab — every tab draws from the same small set of
primitives already defined in the page's `<style>` block.

Poll cadence: match the data's actual volatility, not a fixed default.
Overview's vitals (live bot status) poll every 10s; Analytics (numbers
that change on human timescales — a follow, a redemption) polls every
20s. Pick deliberately, don't copy the number without thinking about it.

## 4. Never display state that isn't honestly derived

Before wiring a field into a card, trace where it's written, not just
what it's named. Two prior violations of this rule in the old admin
dashboard shaped this project:

- The vitals strip originally would have read the native connector's
  `chat_messages_received` counter, which stays flat even when the bot
  is genuinely alive via the legacy polling worker — the wrong "is it
  alive" signal. It now reads `legacy_listener` from
  `/api/blaze/native/diagnostics` instead.
- `STUDIO_STATE.botOnline`/`.recognitionEnabled` (available from the
  same `/api/studio/stats/live` endpoint Analytics's "Today" card uses)
  are bare booleans flipped only by manual admin actions, with zero
  automatic tie to real listener state, legacy or native. Analytics
  reads six other fields from that same endpoint but explicitly omits
  these two — showing either as a "status" would fake a health check
  that was never actually performed.

When a new tab is about to show anything that looks like a live/health
signal, grep for where the backing value actually gets written before
trusting the field name. If nothing writes it from real activity, don't
show it, no matter how tempting the field looks sitting right there in
the response.

## 5. Action buttons (not exercised by Analytics — for future tabs)

Analytics is read-only, so this half of the pattern isn't demonstrated
here, but is defined for whenever a tab needs it:

```js
function bindAction(buttonId, errorEl, run) {
  var btn = document.getElementById(buttonId);
  if (!btn) return;
  btn.addEventListener('click', async function () {
    btn.disabled = true;
    errorEl.hidden = true;
    try {
      await run();
    } catch (err) {
      errorEl.textContent = 'Action failed — try again.';
      errorEl.hidden = false;
    } finally {
      btn.disabled = false;
    }
  });
}
```

Before wiring any action button, confirm — by tracing the endpoint to
the actual global/state it mutates, not by endpoint naming — that it
controls the **same system** the tab's own status display reads from.
This is exactly where Bot Control's pilot attempt broke down: several
of its actions (the native connector's Live-Control on/off/env toggle)
have no equivalent on the legacy polling worker, which is the system
actually serving chat. Displaying a real listener status while wiring
its control buttons to a different, dormant system would report success
on a click that did nothing real. If an action doesn't have a live
equivalent, don't fabricate one — cut it from the tab and scope the
missing backend work separately, the way Bot Control's toggle gap is
now tracked as its own task rather than bundled into a tab migration.

## 6. Checklist for the next tab

- [ ] One `.page` section, `data-target` wired on its nav button.
- [ ] One card per endpoint — no blended fetches.
- [ ] Every poll function: real data / explicit error / genuine empty, no fourth state.
- [ ] Reused `.stats`/`.stat` or `.feed-item` markup — no new visual pattern invented.
- [ ] Every displayed "status"-looking field traced to confirm it's actually derived from real activity, not a manual toggle or a dormant system's counter.
- [ ] If the tab has action buttons: each one traced to the same system its own display reads from, verified before wiring, not assumed from the endpoint name.
