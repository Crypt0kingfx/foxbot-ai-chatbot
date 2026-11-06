// Runs static/foxbot-plinko-overlay.html's real script in Node against a
// stubbed DOM/canvas/fetch with a controlled clock, then prints a JSON
// report. Driven by tests/test_plinko_overlay_js.py; no browser needed.
//
//   node plinko_overlay_harness.js <overlay.html> <drops.json>
'use strict';
const fs = require('fs');
const vm = require('vm');

const html = fs.readFileSync(process.argv[2], 'utf8');
const drops = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];

const BALL_GLOW = 1.6; // overlay draws the ball glow at ballR * 1.6
const elements = {};
function el(id) {
  if (!elements[id]) {
    const classes = new Set();
    elements[id] = {
      id, textContent: '', style: {}, hidden: false, width: 0, height: 0,
      classList: {
        add: (c) => classes.add(c), remove: (c) => classes.delete(c),
        toggle: (c, on) => (on === undefined ? (classes.has(c) ? classes.delete(c) : classes.add(c)) : on ? classes.add(c) : classes.delete(c)),
        contains: (c) => classes.has(c),
      },
      getContext: () => ctx,
    };
  }
  return elements[id];
}

const arcs = [];
const fills = [];
const noop = () => {};
const ctx = new Proxy({}, {
  get(target, prop) {
    if (prop === 'arc') return (x, y, r) => arcs.push({ x, y, r });
    if (prop === 'fillText') return (text, x, y) => fills.push({ text, x, y });
    if (prop === 'createRadialGradient') return () => ({ addColorStop: noop });
    if (prop in target) return target[prop];
    return noop;
  },
  set(target, prop, value) { target[prop] = value; return true; },
});

let rafCallback = null;
const timers = [];
const fetchLog = [];
let speechTouched = false;

const sandbox = {
  console,
  URLSearchParams,
  Number, Math, JSON, Array, String, Date, Promise, parseInt, parseFloat, isNaN,
  window: {
    location: { search: '?handle=demo&size=640&hold=4' },
    devicePixelRatio: 1,
    requestAnimationFrame: (cb) => { rafCallback = cb; },
    get speechSynthesis() { speechTouched = true; return undefined; },
  },
  document: {
    hidden: false,
    getElementById: el,
    documentElement: { style: { setProperty: noop } },
    addEventListener: noop,
  },
  setTimeout: (fn, ms) => { timers.push({ fn, ms }); return timers.length; },
  clearTimeout: noop,
  fetch: async (url) => {
    fetchLog.push(url);
    const since = Number(new URL('http://x' + url).searchParams.get('since'));
    const multipliers = ['10x', '3x', '1.4x', '0.6x', '0.4x', '0.6x', '1.4x', '3x', '10x'];
    const body = since < 0
      ? { ok: true, multipliers, drops: [], latest_id: 0 }
      : { ok: true, multipliers, drops: drops.filter((d) => d.id > since), latest_id: Math.max(since, ...drops.map((d) => d.id)) };
    return { json: async () => body };
  },
};
sandbox.window.window = sandbox.window;
vm.createContext(sandbox);

(async () => {
  vm.runInContext(script, sandbox);
  const flush = () => new Promise((r) => setImmediate(r));
  await flush();
  // first poll (since=-1) done; run its scheduled re-poll to fetch the drops
  const next = timers.shift();
  next.fn();
  await flush(); await flush();

  // Geometry the overlay uses (board width 640).
  const W = 640, spacing = W / 10.5, cx = W / 2;
  const slotX = (k) => cx + (k - 4) * spacing;

  // The ball is the arc drawn with exactly ballR * 1.6 (peg glows are pegR * 3).
  const ballGlow = Math.max(5, spacing * 0.17) * BALL_GLOW;
  const landings = [];
  const trail = [];
  let now = 1000;
  let lastBall = null;
  for (let frame = 0; frame < 3000 && landings.length < drops.length; frame++) {
    arcs.length = 0;
    const cb = rafCallback; rafCallback = null;
    cb(now);
    const ball = arcs.find((a) => Math.abs(a.r - ballGlow) < 1e-6);
    if (ball) { lastBall = ball; trail.push(ball.x); }
    if (el('banner').classList.contains('show')) {
      landings.push({
        frame, viewer: el('bannerName').textContent, multiplier: el('bannerMult').textContent,
        pay: el('bannerPay').textContent, lastBallX: lastBall ? lastBall.x : null,
        distinctXs: new Set(trail.map((x) => x.toFixed(1))).size,
      });
      el('banner').classList.remove('show');
      lastBall = null;
      trail.length = 0;
    }
    now += 16;
  }

  console.log(JSON.stringify({
    landings,
    expectedSlotX: drops.map((d) => slotX(d.slot)),
    fetches: fetchLog,
    speechTouched,
    slotLabels: fills.map((f) => f.text).slice(0, 9),
  }));
})().catch((e) => { console.error(e); process.exit(1); });
