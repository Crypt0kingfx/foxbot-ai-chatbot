"""Prize Wheels v1: the Exercise ("Pushup") Wheel and the Sub Prize Wheel.

Two independent wheels per creator, keyed by creator_HANDLE -- same
reasoning as services/tts_config.py: every real consumer only has a
handle in scope (the polling loop's per-target handle, the anonymous
/overlay/wheel OBS source's ?handle= param, and the Studio session via
_foxbot_resolve_event_handle_v1).

  - exercise wheel: queued when a single vote event is >= the creator's
    vote_threshold (default 50). Every segment is something the STREAMER
    does on stream (pushups, planks, ...), so its result lands as "owed"
    until the creator ticks it off in Studio.
  - sub wheel: queued once per subscribed / gift_sent event. Segments are
    prizes for the viewer: FoxCoins (auto-credited by app.py), streamer
    tasks, manually-delivered rewards (votes, a gifted sub, a guide),
    a respin, or a trip to the exercise wheel.

Spins are a QUEUE, not an instant result: a trigger writes a 'queued'
row, and nothing is decided until resolve_spin() runs -- either from the
creator's own Spin button in Studio V2, or immediately when the creator
has auto_spin turned on. The outcome is drawn server-side through
services/casino_rng (OS CSPRNG, same source of truth as the casino); the
overlay only ever animates to a result that already exists.

Same fail-closed contract as tts_config/casino_config: Postgres only, no
local-file fallback, and wheels are OPT-IN (enabled defaults False) --
bot-connect creators must never suddenly start getting "spin the pushup
wheel" messages in their chat because this shipped.

Mission Wheel (third wheel, "mission"): same engine -- same tables, same
queue_spin / resolve_spin / weighted casino_rng draw -- with its own
segment shape (title, short_label, description, duration_minutes,
weight 1-10) and its own settings columns on wheel_config. It is ON by
default only for the owner (tenant-zero) handle; every other creator
gets it off. A mission spin's lifecycle maps onto the existing columns:
queued = pending, spun+owed = playing (or done once a timed mission's
clock has run out), done = done. See mission_state().
"""

from __future__ import annotations

import copy
import os
import re
import threading
from dataclasses import dataclass, field
from typing import Any

from services import casino_rng


TABLE_CONFIG = "wheel_config"
TABLE_SPINS = "wheel_spins"

WHEEL_EXERCISE = "exercise"
WHEEL_SUB = "sub"
# The two original prize wheels. Deliberately does NOT include the mission
# wheel: everything that iterates WHEELS (the !wheel help, /overlay/wheel,
# /api/studio/wheels) is about those two, and the mission wheel has its own
# admin-gated routes and overlay.
WHEELS = (WHEEL_EXERCISE, WHEEL_SUB)
WHEEL_MISSION = "mission"
# Every wheel the engine (queue_spin / resolve_spin / active_segments) knows.
ALL_WHEELS = WHEELS + (WHEEL_MISSION,)

WHEEL_TITLES = {
    WHEEL_EXERCISE: "Pushup Wheel",
    WHEEL_SUB: "Sub Prize Wheel",
    WHEEL_MISSION: "GTA 6 Mission Wheel",
}

# Prize types. What app.py does with each once a spin resolves:
#   foxcoins      -> credit `amount` FoxCoins to the viewer (auto)
#   task          -> streamer does it on stream (owed until marked done)
#   reward        -> streamer delivers something by hand (owed)
#   respin        -> queue another spin on the SAME wheel for the viewer
#   exercise_spin -> queue a spin on the exercise wheel for the viewer
#   nothing       -> a dud / free pass (auto)
PRIZE_TYPES = ("foxcoins", "task", "reward", "respin", "exercise_spin", "nothing")
AUTO_FULFILLED_TYPES = ("foxcoins", "respin", "exercise_spin", "nothing")

FULFILL_AUTO = "auto"
FULFILL_OWED = "owed"
FULFILL_DONE = "done"

STATUS_QUEUED = "queued"
STATUS_SPUN = "spun"
STATUS_CANCELLED = "cancelled"

DEFAULT_ENABLED = False
DEFAULT_VOTE_THRESHOLD = 50
DEFAULT_AUTO_SPIN = False
DEFAULT_ANNOUNCE_IN_CHAT = True

MIN_VOTE_THRESHOLD = 1
MAX_VOTE_THRESHOLD = 100000
MIN_SEGMENTS = 2
MAX_SEGMENTS = 24
MAX_LABEL_CHARS = 40
MAX_WEIGHT = 1000
MAX_FOXCOIN_PRIZE = 100000

# A respin can land on another respin. Probability already decays fast,
# but auto_spin makes the chain run with no human in the loop, so it's
# capped structurally rather than trusted to luck.
MAX_CHAIN_DEPTH = 3

# Mission Wheel limits/defaults.
MISSION_PRIZE_TYPE = "mission"   # never in PRIZE_TYPES: not selectable on the other wheels
DEFAULT_MISSION_VOTE_THRESHOLD = 30
DEFAULT_MISSION_MAX_SPINS = 3
MIN_MISSION_MAX_SPINS = 1
MAX_MISSION_MAX_SPINS = 10
MAX_MISSION_TITLE_CHARS = 60
MAX_MISSION_SHORT_LABEL_CHARS = 14
MAX_MISSION_DESCRIPTION_CHARS = 200
MIN_MISSION_WEIGHT = 1
MAX_MISSION_WEIGHT = 10
MAX_MISSION_DURATION_MINUTES = 240
# A timed mission's clock starts when the overlay lands, not when the
# server draws it: the overlay's spin animation takes ~8s (700ms intro +
# 7.2s spin). app.py's reveal delay for the chat line uses the same number.
MISSION_START_DELAY_SECONDS = 9

DEFAULT_CONNECT_TIMEOUT_SECONDS = 10

_schema_lock = threading.Lock()
_schema_ready = False


def _seg(seg_id, label, prize_type, weight, amount=0):
    return {"id": seg_id, "label": label, "type": prize_type, "amount": amount, "weight": weight, "enabled": True}


DEFAULT_SEGMENTS = {
    WHEEL_EXERCISE: [
        _seg("ex-10-pushups", "10 Pushups", "task", 12),
        _seg("ex-25-squats", "25 Squats", "task", 10),
        _seg("ex-30s-plank", "30s Plank", "task", 10),
        _seg("ex-30-jacks", "30 Jumping Jacks", "task", 10),
        _seg("ex-20-pushups", "20 Pushups", "task", 8),
        _seg("ex-20-situps", "20 Sit-ups", "task", 8),
        _seg("ex-20-lunges", "20 Lunges", "task", 8),
        _seg("ex-45s-wallsit", "45s Wall Sit", "task", 7),
        _seg("ex-15-burpees", "15 Burpees", "task", 6),
        _seg("ex-60s-plank", "60s Plank", "task", 5),
        _seg("ex-chat-picks", "Chat Picks the Workout", "task", 5),
        _seg("ex-free-pass", "Free Pass!", "nothing", 5),
        _seg("ex-spin-twice", "Spin Twice", "respin", 4),
        _seg("ex-50-pushups", "50 PUSHUPS", "task", 2),
    ],
    WHEEL_SUB: [
        _seg("sub-500", "500 FoxCoins", "foxcoins", 20, 500),
        _seg("sub-1000", "1,000 FoxCoins", "foxcoins", 14, 1000),
        _seg("sub-streamer-pushups", "Streamer Does 20 Pushups", "task", 8),
        _seg("sub-2500", "2,500 FoxCoins", "foxcoins", 7, 2500),
        _seg("sub-pick-mission", "You Pick the Next Mission", "task", 7),
        _seg("sub-pushup-wheel", "Spin the Pushup Wheel", "exercise_spin", 6),
        _seg("sub-name-ingame", "Name Something In-Game", "task", 6),
        _seg("sub-x-shoutout", "Shoutout on X", "task", 6),
        _seg("sub-accent", "10-Min Accent Challenge", "task", 5),
        _seg("sub-discord-role", "Custom Discord Role", "reward", 5),
        _seg("sub-spin-again", "Spin Again!", "respin", 5),
        _seg("sub-money-guide", "Free GTA6 Money Guide", "reward", 3),
        _seg("sub-10-votes", "10 Votes From CryptoKing", "reward", 3),
        _seg("sub-5000", "5,000 FoxCoin JACKPOT", "foxcoins", 2, 5000),
        _seg("sub-gift-sub", "Gift a Sub to Chat", "reward", 1),
    ],
}


def _mission(seg_id, title, short_label, description, duration_minutes=None, weight=5):
    return {
        "id": seg_id, "title": title, "short_label": short_label, "label": short_label,
        "description": description, "duration_minutes": duration_minutes,
        "type": MISSION_PRIZE_TYPE, "amount": 0, "weight": weight, "enabled": True,
    }


# Starter missions so the wheel isn't empty -- the streamer edits these in
# Studio. 23 of them, inside MAX_SEGMENTS (24).
DEFAULT_SEGMENTS[WHEEL_MISSION] = [
    _mission("no-hud-drive", "No HUD drive", "No HUD", "Drive with the HUD and minimap turned off."),
    _mission("first-person-only", "First-person only", "1st Person", "First-person camera only.", 10),
    _mission("obey-traffic-laws", "Obey traffic laws", "Obey Traffic", "Stop at reds, stay in lane, no speeding.", 5),
    _mission("airboat-swamp-lap", "Airboat swamp lap", "Airboat Lap", "Take an airboat for a full lap of the swamp."),
    _mission("jet-ski-3-jumps", "Jet ski 3 jumps", "Jet Ski x3", "Land 3 jumps on a jet ski."),
    _mission("street-race-top-3", "Street race top 3", "Race Top 3", "Finish a street race in the top 3."),
    _mission("pool-game", "Pool game", "Pool Game", "Play a full game of pool."),
    _mission("fists-only", "Fists only", "Fists Only", "No weapons -- fists only.", 10),
    _mission("basketball-5-shots", "Basketball 5 shots", "Hoops x5", "Sink 5 basketball shots."),
    _mission("mini-golf-round", "Mini golf round", "Mini Golf", "Play a full round of mini golf."),
    _mission("catch-a-fish", "Catch a fish", "Catch a Fish", "Go fishing and land one."),
    _mission("scuba-dive", "Scuba dive", "Scuba Dive", "Gear up and go scuba diving."),
    _mission("skydive-highest-point", "Skydive from the highest point", "Skydive", "Get to the highest point on the map and jump."),
    _mission("gym-plus-pushups", "In-game gym + 10 real pushups", "Gym + Pushups", "Hit the in-game gym, then do 10 real pushups."),
    _mission("motocross-run", "Motocross run", "Motocross", "Do a full motocross run."),
    _mission("chat-picks-my-car", "Chat picks my car", "Chat's Car", "Chat picks the car -- drive it."),
    _mission("hanks-waffles", "Hank's Waffles with no wanted level", "Hank's Waffles", "Get to Hank's Waffles without picking up a wanted level."),
    _mission("scooters-only", "Scooters only", "Scooters Only", "Scooters only, nothing else.", 10),
    _mission("binocular-stakeout", "Binocular stakeout", "Stakeout", "Find a spot and run a binocular stakeout."),
    _mission("five-star-wanted", "5-star wanted for 3 min", "5-Star Wanted", "Survive a 5-star wanted level for 3 minutes.", 3),
    _mission("swap-jason-lucia", "Swap Jason/Lucia", "Swap J/L", "Switch to the other character.", 15),
    _mission("car-meet", "Car meet", "Car Meet", "Roll up to a car meet."),
    _mission("viewers-choice", "Viewer's Choice", "Viewer Choice", "The viewer who spun picks the mission.", None, 1),
]


class WheelsUnavailable(Exception):
    """DATABASE_URL isn't configured -- no local-file fallback by design."""


@dataclass(frozen=True)
class WheelConfig:
    creator_handle: str
    exercise_enabled: bool
    sub_enabled: bool
    vote_threshold: int
    auto_spin: bool
    announce_in_chat: bool
    segments: dict = field(default_factory=dict)
    mission_enabled: bool = False
    mission_vote_threshold: int = DEFAULT_MISSION_VOTE_THRESHOLD
    mission_max_spins: int = DEFAULT_MISSION_MAX_SPINS

    def wheel_enabled(self, wheel: str) -> bool:
        if wheel == WHEEL_EXERCISE:
            return self.exercise_enabled
        if wheel == WHEEL_SUB:
            return self.sub_enabled
        if wheel == WHEEL_MISSION:
            return self.mission_enabled
        return False


def database_url() -> str:
    return str(os.getenv("DATABASE_URL") or "").strip()


def is_available() -> bool:
    return bool(database_url())


def _require_available() -> None:
    if not is_available():
        raise WheelsUnavailable("Prize wheels require DATABASE_URL (Postgres) -- there is no local-file fallback by design.")


def _connect(timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS):
    import psycopg

    return psycopg.connect(database_url(), connect_timeout=timeout)


def _clean_handle(creator_handle: str) -> str:
    from services import creator_access

    handle = creator_access.clean_handle(creator_handle)
    if not handle:
        raise ValueError("creator_handle is required.")
    return handle


def _ensure_schema(connection) -> None:
    """Same race-tolerant, commit-before-flag pattern as
    services/tts_config.py's _ensure_schema -- see that docstring for why
    both the duplicate-object catch and the explicit commit() matter."""
    global _schema_ready
    if _schema_ready:
        return

    with _schema_lock:
        if _schema_ready:
            return

        import psycopg

        statements = (
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE_CONFIG} (
                creator_handle    TEXT PRIMARY KEY,
                exercise_enabled  BOOLEAN NOT NULL DEFAULT {str(DEFAULT_ENABLED).upper()},
                sub_enabled       BOOLEAN NOT NULL DEFAULT {str(DEFAULT_ENABLED).upper()},
                vote_threshold    INT NOT NULL DEFAULT {DEFAULT_VOTE_THRESHOLD},
                auto_spin         BOOLEAN NOT NULL DEFAULT {str(DEFAULT_AUTO_SPIN).upper()},
                announce_in_chat  BOOLEAN NOT NULL DEFAULT {str(DEFAULT_ANNOUNCE_IN_CHAT).upper()},
                exercise_segments JSONB,
                sub_segments      JSONB,
                updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE_SPINS} (
                id             BIGSERIAL PRIMARY KEY,
                creator_handle TEXT NOT NULL,
                wheel          TEXT NOT NULL,
                viewer         TEXT NOT NULL,
                trigger_kind   TEXT NOT NULL,
                trigger_amount INT NOT NULL DEFAULT 0,
                dedupe_key     TEXT NOT NULL,
                channel_id     TEXT,
                creator_id     TEXT,
                parent_spin_id BIGINT,
                chain_depth    INT NOT NULL DEFAULT 0,
                status         TEXT NOT NULL DEFAULT '{STATUS_QUEUED}',
                segments       JSONB,
                segment_index  INT,
                prize          JSONB,
                fulfillment    TEXT,
                created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                spun_at        TIMESTAMPTZ,
                fulfilled_at   TIMESTAMPTZ,
                UNIQUE (creator_handle, dedupe_key)
            )
            """,
            f"""
            CREATE INDEX IF NOT EXISTS idx_{TABLE_SPINS}_handle_status
                ON {TABLE_SPINS} (creator_handle, status, id)
            """,
            # Mission Wheel -- additive only. mission_enabled is nullable on
            # purpose: NULL means "never set", which resolves to on for the
            # owner handle and off for everyone else (_mission_default_enabled).
            f"ALTER TABLE {TABLE_CONFIG} ADD COLUMN IF NOT EXISTS mission_enabled BOOLEAN",
            f"ALTER TABLE {TABLE_CONFIG} ADD COLUMN IF NOT EXISTS mission_vote_threshold INT NOT NULL DEFAULT {DEFAULT_MISSION_VOTE_THRESHOLD}",
            f"ALTER TABLE {TABLE_CONFIG} ADD COLUMN IF NOT EXISTS mission_max_spins INT NOT NULL DEFAULT {DEFAULT_MISSION_MAX_SPINS}",
            f"ALTER TABLE {TABLE_CONFIG} ADD COLUMN IF NOT EXISTS mission_segments JSONB",
        )
        for statement in statements:
            try:
                connection.execute(statement)
            except (psycopg.errors.DuplicateTable, psycopg.errors.UniqueViolation, psycopg.errors.DuplicateObject,
                    psycopg.errors.DuplicateColumn):
                connection.rollback()

        connection.commit()
        _schema_ready = True


# ---------------------------------------------------------------- segments


def default_segments(wheel: str) -> list[dict]:
    return copy.deepcopy(DEFAULT_SEGMENTS[wheel])


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")[:40] or "segment"


def validate_segments(wheel: str, segments: Any) -> list[dict]:
    """Normalizes a creator-submitted segment list or raises ValueError.
    Labels are rendered on the public overlay and posted to chat, so they
    are trimmed, length-capped and stripped of control characters here --
    the one place segment text enters the system."""
    if wheel == WHEEL_MISSION:
        return validate_mission_segments(segments)
    if wheel not in WHEELS:
        raise ValueError("unknown wheel.")
    if not isinstance(segments, list):
        raise ValueError("segments must be a list.")
    if not (MIN_SEGMENTS <= len(segments) <= MAX_SEGMENTS):
        raise ValueError(f"a wheel needs between {MIN_SEGMENTS} and {MAX_SEGMENTS} segments.")

    cleaned = []
    seen_ids = set()
    for index, raw in enumerate(segments):
        if not isinstance(raw, dict):
            raise ValueError(f"segment {index + 1} is not an object.")

        label = re.sub(r"[\x00-\x1f\x7f]", "", str(raw.get("label") or "")).strip()[:MAX_LABEL_CHARS]
        if not label:
            raise ValueError(f"segment {index + 1} needs a label.")

        prize_type = str(raw.get("type") or "task").strip().lower()
        if prize_type not in PRIZE_TYPES:
            raise ValueError(f"segment {index + 1} ({label}): unknown prize type {prize_type!r}.")
        if wheel == WHEEL_EXERCISE and prize_type == "exercise_spin":
            raise ValueError(f"segment {index + 1} ({label}): the exercise wheel can't send spins to itself -- use respin.")

        try:
            weight = int(raw.get("weight", 1))
            amount = int(raw.get("amount") or 0)
        except (TypeError, ValueError):
            raise ValueError(f"segment {index + 1} ({label}): weight and amount must be whole numbers.")
        if not (0 <= weight <= MAX_WEIGHT):
            raise ValueError(f"segment {index + 1} ({label}): weight must be between 0 and {MAX_WEIGHT}.")
        if prize_type == "foxcoins":
            if not (1 <= amount <= MAX_FOXCOIN_PRIZE):
                raise ValueError(f"segment {index + 1} ({label}): FoxCoin amount must be between 1 and {MAX_FOXCOIN_PRIZE}.")
        else:
            amount = 0

        seg_id = _slug(raw.get("id") or label)
        base_id, suffix = seg_id, 2
        while seg_id in seen_ids:
            seg_id = f"{base_id}-{suffix}"
            suffix += 1
        seen_ids.add(seg_id)

        cleaned.append({
            "id": seg_id,
            "label": label,
            "type": prize_type,
            "amount": amount,
            "weight": weight,
            "enabled": bool(raw.get("enabled", True)),
        })

    if not any(seg["enabled"] and seg["weight"] > 0 for seg in cleaned):
        raise ValueError("at least one enabled segment needs a weight above 0.")
    if sum(1 for seg in cleaned if seg["enabled"]) < MIN_SEGMENTS:
        raise ValueError(f"at least {MIN_SEGMENTS} segments must be enabled.")
    return cleaned


def _clean_text(value: Any, limit: int) -> str:
    return re.sub(r"[\x00-\x1f\x7f]", "", str(value or "")).strip()[:limit]


def validate_mission_segments(segments: Any) -> list[dict]:
    """Normalizes a creator-submitted mission list or raises ValueError.
    Same role as validate_segments: title/short_label/description reach the
    public overlay and chat, so they're cleaned and capped here. Each
    mission also carries label (= short_label), type and amount so the
    shared engine and overlay code can treat it like any other segment."""
    if not isinstance(segments, list):
        raise ValueError("missions must be a list.")
    if not (MIN_SEGMENTS <= len(segments) <= MAX_SEGMENTS):
        raise ValueError(f"the mission wheel needs between {MIN_SEGMENTS} and {MAX_SEGMENTS} missions.")

    cleaned = []
    seen_ids = set()
    for index, raw in enumerate(segments):
        if not isinstance(raw, dict):
            raise ValueError(f"mission {index + 1} is not an object.")

        title = _clean_text(raw.get("title"), MAX_MISSION_TITLE_CHARS)
        if not title:
            raise ValueError(f"mission {index + 1} needs a title.")
        raw_short = _clean_text(raw.get("short_label"), 1000)
        if len(raw_short) > MAX_MISSION_SHORT_LABEL_CHARS:
            raise ValueError(f"mission {index + 1} ({title}): short label must be {MAX_MISSION_SHORT_LABEL_CHARS} characters or fewer.")
        short_label = raw_short or title[:MAX_MISSION_SHORT_LABEL_CHARS].strip()
        description = _clean_text(raw.get("description"), MAX_MISSION_DESCRIPTION_CHARS)

        duration = raw.get("duration_minutes")
        if duration in (None, ""):
            duration = None
        else:
            try:
                duration = int(duration)
            except (TypeError, ValueError):
                raise ValueError(f"mission {index + 1} ({title}): duration must be whole minutes, or blank.")
            if not (1 <= duration <= MAX_MISSION_DURATION_MINUTES):
                raise ValueError(f"mission {index + 1} ({title}): duration must be between 1 and {MAX_MISSION_DURATION_MINUTES} minutes, or blank.")

        try:
            weight = int(raw.get("weight", 5))
        except (TypeError, ValueError):
            raise ValueError(f"mission {index + 1} ({title}): weight must be a whole number.")
        if not (MIN_MISSION_WEIGHT <= weight <= MAX_MISSION_WEIGHT):
            raise ValueError(f"mission {index + 1} ({title}): weight must be between {MIN_MISSION_WEIGHT} and {MAX_MISSION_WEIGHT}.")

        seg_id = _slug(raw.get("id") or title)
        base_id, suffix = seg_id, 2
        while seg_id in seen_ids:
            seg_id = f"{base_id}-{suffix}"
            suffix += 1
        seen_ids.add(seg_id)

        cleaned.append({
            "id": seg_id,
            "title": title,
            "short_label": short_label,
            "label": short_label,
            "description": description,
            "duration_minutes": duration,
            "type": MISSION_PRIZE_TYPE,
            "amount": 0,
            "weight": weight,
            "enabled": bool(raw.get("enabled", True)),
        })

    if sum(1 for seg in cleaned if seg["enabled"]) < MIN_SEGMENTS:
        raise ValueError(f"at least {MIN_SEGMENTS} missions must be enabled.")
    return cleaned


def active_segments(config: WheelConfig, wheel: str) -> list[dict]:
    """The segments actually drawn on the wheel: enabled ones, in order.
    Zero-weight segments stay visible (a creator may want a 'tease'
    jackpot slice) -- they just can never be picked."""
    return [seg for seg in config.segments.get(wheel) or default_segments(wheel) if seg.get("enabled", True)]


def pick_segment_index(segments: list[dict]) -> int:
    """Weighted draw through casino_rng (CSPRNG). Returns an index into
    `segments`."""
    total = sum(max(0, int(seg.get("weight") or 0)) for seg in segments)
    if total <= 0:
        raise ValueError("no segment has a positive weight.")
    ticket = casino_rng.roll(1, total)
    running = 0
    for index, seg in enumerate(segments):
        running += max(0, int(seg.get("weight") or 0))
        if ticket <= running:
            return index
    return len(segments) - 1  # unreachable; defensive


# ------------------------------------------------------------------ config


_CONFIG_COLUMNS = (
    "exercise_enabled, sub_enabled, vote_threshold, auto_spin, announce_in_chat, "
    "exercise_segments, sub_segments, "
    "mission_enabled, mission_vote_threshold, mission_max_spins, mission_segments"
)


def _mission_default_enabled(handle: str) -> bool:
    """The Mission Wheel defaults ON for the owner (tenant-zero) handle and
    OFF for every other creator -- same reason the other wheels are opt-in:
    a bot-connect creator must never suddenly get mission spam in chat."""
    from services import foxbot_events

    return handle == foxbot_events.resolve_owner_handle()


def _row_to_config(handle: str, row) -> WheelConfig:
    if row is None:
        return WheelConfig(
            handle, DEFAULT_ENABLED, DEFAULT_ENABLED, DEFAULT_VOTE_THRESHOLD,
            DEFAULT_AUTO_SPIN, DEFAULT_ANNOUNCE_IN_CHAT,
            {wheel: default_segments(wheel) for wheel in ALL_WHEELS},
            _mission_default_enabled(handle), DEFAULT_MISSION_VOTE_THRESHOLD, DEFAULT_MISSION_MAX_SPINS,
        )
    return WheelConfig(
        handle, bool(row[0]), bool(row[1]), int(row[2]), bool(row[3]), bool(row[4]),
        {
            WHEEL_EXERCISE: row[5] if isinstance(row[5], list) and row[5] else default_segments(WHEEL_EXERCISE),
            WHEEL_SUB: row[6] if isinstance(row[6], list) and row[6] else default_segments(WHEEL_SUB),
            WHEEL_MISSION: row[10] if isinstance(row[10], list) and row[10] else default_segments(WHEEL_MISSION),
        },
        _mission_default_enabled(handle) if row[7] is None else bool(row[7]),
        int(row[8]),
        int(row[9]),
    )


def get_config(creator_handle: str, timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS) -> WheelConfig:
    _require_available()
    handle = _clean_handle(creator_handle)

    with _connect(timeout=timeout) as connection:
        _ensure_schema(connection)
        row = connection.execute(
            f"SELECT {_CONFIG_COLUMNS} FROM {TABLE_CONFIG} WHERE creator_handle = %s",
            (handle,),
        ).fetchone()
    return _row_to_config(handle, row)


def set_config(
    creator_handle: str,
    *,
    exercise_enabled: bool | None = None,
    sub_enabled: bool | None = None,
    vote_threshold: int | None = None,
    auto_spin: bool | None = None,
    announce_in_chat: bool | None = None,
    exercise_segments: list | None = None,
    sub_segments: list | None = None,
    reset_segments: str | None = None,
    mission_enabled: bool | None = None,
    mission_vote_threshold: int | None = None,
    mission_max_spins: int | None = None,
    mission_segments: list | None = None,
    timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS,
) -> WheelConfig:
    """Partial update: only the kwargs actually given change; everything
    else keeps its stored value (same contract as tts_config.set_config).
    reset_segments="exercise"|"sub"|"mission" puts that wheel back on the
    built-in defaults (stored as NULL, so future default tweaks reach it too)."""
    _require_available()
    handle = _clean_handle(creator_handle)

    if vote_threshold is not None:
        vote_threshold = int(vote_threshold)
        if not (MIN_VOTE_THRESHOLD <= vote_threshold <= MAX_VOTE_THRESHOLD):
            raise ValueError(f"vote_threshold must be between {MIN_VOTE_THRESHOLD} and {MAX_VOTE_THRESHOLD}.")
    if exercise_segments is not None:
        exercise_segments = validate_segments(WHEEL_EXERCISE, exercise_segments)
    if sub_segments is not None:
        sub_segments = validate_segments(WHEEL_SUB, sub_segments)
    if reset_segments is not None and reset_segments not in ALL_WHEELS:
        raise ValueError("reset_segments must be 'exercise', 'sub' or 'mission'.")
    if mission_vote_threshold is not None:
        mission_vote_threshold = int(mission_vote_threshold)
        if not (MIN_VOTE_THRESHOLD <= mission_vote_threshold <= MAX_VOTE_THRESHOLD):
            raise ValueError(f"mission vote threshold must be between {MIN_VOTE_THRESHOLD} and {MAX_VOTE_THRESHOLD}.")
    if mission_max_spins is not None:
        mission_max_spins = int(mission_max_spins)
        if not (MIN_MISSION_MAX_SPINS <= mission_max_spins <= MAX_MISSION_MAX_SPINS):
            raise ValueError(f"max spins per event must be between {MIN_MISSION_MAX_SPINS} and {MAX_MISSION_MAX_SPINS}.")
    if mission_segments is not None:
        mission_segments = validate_mission_segments(mission_segments)

    from psycopg.types.json import Jsonb

    with _connect(timeout=timeout) as connection:
        _ensure_schema(connection)
        connection.execute(
            f"INSERT INTO {TABLE_CONFIG} (creator_handle) VALUES (%s) ON CONFLICT (creator_handle) DO NOTHING",
            (handle,),
        )

        assignments, params = [], []
        for column, value in (
            ("exercise_enabled", exercise_enabled),
            ("sub_enabled", sub_enabled),
            ("vote_threshold", vote_threshold),
            ("auto_spin", auto_spin),
            ("announce_in_chat", announce_in_chat),
            ("mission_enabled", mission_enabled),
        ):
            if value is not None:
                assignments.append(f"{column} = %s")
                params.append(bool(value) if column != "vote_threshold" else value)
        for column, value in (
            ("mission_vote_threshold", mission_vote_threshold),
            ("mission_max_spins", mission_max_spins),
        ):
            if value is not None:
                assignments.append(f"{column} = %s")
                params.append(value)
        if mission_segments is not None:
            assignments.append("mission_segments = %s")
            params.append(Jsonb(mission_segments))
        if exercise_segments is not None:
            assignments.append("exercise_segments = %s")
            params.append(Jsonb(exercise_segments))
        if sub_segments is not None:
            assignments.append("sub_segments = %s")
            params.append(Jsonb(sub_segments))
        if reset_segments is not None:
            assignments.append(f"{reset_segments}_segments = NULL")

        if assignments:
            assignments.append("updated_at = NOW()")
            connection.execute(
                f"UPDATE {TABLE_CONFIG} SET {', '.join(assignments)} WHERE creator_handle = %s",
                (*params, handle),
            )

        row = connection.execute(
            f"SELECT {_CONFIG_COLUMNS} FROM {TABLE_CONFIG} WHERE creator_handle = %s",
            (handle,),
        ).fetchone()
    return _row_to_config(handle, row)


# ------------------------------------------------------------------- spins

_SPIN_COLUMNS = (
    "id, creator_handle, wheel, viewer, trigger_kind, trigger_amount, channel_id, creator_id, "
    "parent_spin_id, chain_depth, status, segments, segment_index, prize, fulfillment, "
    "created_at, spun_at, fulfilled_at"
)


def _row_to_spin(row) -> dict | None:
    if row is None:
        return None
    (spin_id, handle, wheel, viewer, trigger_kind, trigger_amount, channel_id, creator_id,
     parent_spin_id, chain_depth, status, segments, segment_index, prize, fulfillment,
     created_at, spun_at, fulfilled_at) = row
    return {
        "id": int(spin_id),
        "creator_handle": handle,
        "wheel": wheel,
        "wheel_title": WHEEL_TITLES.get(wheel, wheel),
        "viewer": viewer,
        "trigger_kind": trigger_kind,
        "trigger_amount": int(trigger_amount or 0),
        "channel_id": channel_id,
        "creator_id": creator_id,
        "parent_spin_id": int(parent_spin_id) if parent_spin_id is not None else None,
        "chain_depth": int(chain_depth or 0),
        "status": status,
        "segments": segments,
        "segment_index": segment_index,
        "prize": prize,
        "fulfillment": fulfillment,
        "created_at": created_at.isoformat() if created_at else None,
        "spun_at": spun_at.isoformat() if spun_at else None,
        "fulfilled_at": fulfilled_at.isoformat() if fulfilled_at else None,
    }


def queue_spin(
    creator_handle: str,
    wheel: str,
    viewer: str,
    *,
    trigger_kind: str,
    trigger_amount: int = 0,
    dedupe_key: str,
    channel_id: str | None = None,
    creator_id: str | None = None,
    parent_spin_id: int | None = None,
    chain_depth: int = 0,
    timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS,
) -> dict | None:
    """Adds one queued spin. Returns the new spin, or None when this
    dedupe_key was already queued for this creator (a replayed chat row, a
    restart re-reading recent messages) -- the UNIQUE constraint, not an
    in-memory set, is what makes that durable across restarts."""
    _require_available()
    handle = _clean_handle(creator_handle)
    if wheel not in ALL_WHEELS:
        raise ValueError("unknown wheel.")
    key = str(dedupe_key or "").strip()
    if not key:
        raise ValueError("dedupe_key is required.")
    name = str(viewer or "").strip().lstrip("@")[:64] or "viewer"

    with _connect(timeout=timeout) as connection:
        _ensure_schema(connection)
        row = connection.execute(
            f"""
            INSERT INTO {TABLE_SPINS}
                (creator_handle, wheel, viewer, trigger_kind, trigger_amount, dedupe_key,
                 channel_id, creator_id, parent_spin_id, chain_depth)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (creator_handle, dedupe_key) DO NOTHING
            RETURNING {_SPIN_COLUMNS}
            """,
            (handle, wheel, name, str(trigger_kind or "manual")[:32], int(trigger_amount or 0), key[:300],
             channel_id or None, creator_id or None, parent_spin_id, int(chain_depth or 0)),
        ).fetchone()
    return _row_to_spin(row)


def resolve_spin(creator_handle: str, spin_id: int, timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS) -> dict | None:
    """Draws the result for one queued spin. Row-locked (SELECT ... FOR
    UPDATE) so two clicks, or an auto-spin racing a manual click, can
    only ever resolve it once: the loser gets the already-decided spin
    back with replayed=True and must not pay anything out again.
    Returns None if the spin doesn't exist for this creator or was
    cancelled."""
    _require_available()
    handle = _clean_handle(creator_handle)
    config = get_config(handle, timeout=timeout)

    from psycopg.types.json import Jsonb

    with _connect(timeout=timeout) as connection:
        _ensure_schema(connection)
        row = connection.execute(
            f"SELECT {_SPIN_COLUMNS} FROM {TABLE_SPINS} WHERE creator_handle = %s AND id = %s FOR UPDATE",
            (handle, int(spin_id)),
        ).fetchone()
        spin = _row_to_spin(row)
        if spin is None or spin["status"] == STATUS_CANCELLED:
            return None
        if spin["status"] == STATUS_SPUN:
            spin["replayed"] = True
            return spin

        segments = active_segments(config, spin["wheel"])
        index = pick_segment_index(segments)
        prize = dict(segments[index])
        fulfillment = FULFILL_AUTO if prize["type"] in AUTO_FULFILLED_TYPES else FULFILL_OWED

        row = connection.execute(
            f"""
            UPDATE {TABLE_SPINS}
               SET status = %s, segments = %s, segment_index = %s, prize = %s, fulfillment = %s,
                   spun_at = NOW(),
                   fulfilled_at = CASE WHEN %s = '{FULFILL_AUTO}' THEN NOW() ELSE NULL END
             WHERE id = %s
            RETURNING {_SPIN_COLUMNS}
            """,
            (STATUS_SPUN, Jsonb(segments), index, Jsonb(prize), fulfillment, fulfillment, spin["id"]),
        ).fetchone()

    spin = _row_to_spin(row)
    spin["replayed"] = False
    return spin


def get_spin(creator_handle: str, spin_id: int, timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS) -> dict | None:
    _require_available()
    handle = _clean_handle(creator_handle)
    with _connect(timeout=timeout) as connection:
        _ensure_schema(connection)
        row = connection.execute(
            f"SELECT {_SPIN_COLUMNS} FROM {TABLE_SPINS} WHERE creator_handle = %s AND id = %s",
            (handle, int(spin_id)),
        ).fetchone()
    return _row_to_spin(row)


def list_spins(
    creator_handle: str,
    *,
    status: str | None = None,
    fulfillment: str | None = None,
    limit: int = 25,
    oldest_first: bool = False,
    wheels: tuple | list | None = None,
    timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS,
) -> list[dict]:
    """wheels=None lists every wheel's spins; pass a tuple to restrict it
    (the Prize Wheels routes pass WHEELS, the Mission Wheel routes pass
    (WHEEL_MISSION,), so neither queue shows or spins the other's rows)."""
    _require_available()
    handle = _clean_handle(creator_handle)
    clauses, params = ["creator_handle = %s"], [handle]
    if wheels is not None:
        clauses.append("wheel = ANY(%s)")
        params.append(list(wheels))
    if status:
        clauses.append("status = %s")
        params.append(status)
    if fulfillment:
        clauses.append("fulfillment = %s")
        params.append(fulfillment)
    capped = max(1, min(int(limit or 25), 200))
    order = "ASC" if oldest_first else "DESC"

    with _connect(timeout=timeout) as connection:
        _ensure_schema(connection)
        rows = connection.execute(
            f"SELECT {_SPIN_COLUMNS} FROM {TABLE_SPINS} WHERE {' AND '.join(clauses)} ORDER BY id {order} LIMIT %s",
            (*params, capped),
        ).fetchall()
    return [_row_to_spin(row) for row in rows]


def latest_spun(creator_handle: str, max_age_seconds: int = 120, wheels: tuple | list | None = None,
                timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS) -> list[dict]:
    """Recently-resolved spins, oldest first -- what the OBS overlay polls.
    The age window means a freshly-opened overlay never replays last
    night's spins; the overlay's own last-seen id keeps it from
    re-animating one it already showed."""
    _require_available()
    handle = _clean_handle(creator_handle)
    with _connect(timeout=timeout) as connection:
        _ensure_schema(connection)
        rows = connection.execute(
            f"""
            SELECT {_SPIN_COLUMNS} FROM {TABLE_SPINS}
             WHERE creator_handle = %s AND status = %s
               AND spun_at > NOW() - make_interval(secs => %s)
               AND (%s::text[] IS NULL OR wheel = ANY(%s::text[]))
             ORDER BY spun_at ASC, id ASC
             LIMIT 20
            """,
            (handle, STATUS_SPUN, int(max_age_seconds),
             list(wheels) if wheels is not None else None, list(wheels) if wheels is not None else None),
        ).fetchall()
    return [_row_to_spin(row) for row in rows]


def set_fulfilled(creator_handle: str, spin_id: int, done: bool = True, timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS) -> dict | None:
    """Ticks an owed prize/task off (or back on, done=False). Only ever
    moves between owed<->done; auto-fulfilled spins are left alone."""
    _require_available()
    handle = _clean_handle(creator_handle)
    target, source = (FULFILL_DONE, FULFILL_OWED) if done else (FULFILL_OWED, FULFILL_DONE)
    with _connect(timeout=timeout) as connection:
        _ensure_schema(connection)
        row = connection.execute(
            f"""
            UPDATE {TABLE_SPINS}
               SET fulfillment = %s, fulfilled_at = CASE WHEN %s = '{FULFILL_DONE}' THEN NOW() ELSE NULL END
             WHERE creator_handle = %s AND id = %s AND status = %s AND fulfillment = %s
            RETURNING {_SPIN_COLUMNS}
            """,
            (target, target, handle, int(spin_id), STATUS_SPUN, source),
        ).fetchone()
    return _row_to_spin(row)


def cancel_spin(creator_handle: str, spin_id: int, timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS) -> dict | None:
    _require_available()
    handle = _clean_handle(creator_handle)
    with _connect(timeout=timeout) as connection:
        _ensure_schema(connection)
        row = connection.execute(
            f"""
            UPDATE {TABLE_SPINS} SET status = %s
             WHERE creator_handle = %s AND id = %s AND status = %s
            RETURNING {_SPIN_COLUMNS}
            """,
            (STATUS_CANCELLED, handle, int(spin_id), STATUS_QUEUED),
        ).fetchone()
    return _row_to_spin(row)


def mark_owed(creator_handle: str, spin_id: int, timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS) -> dict | None:
    """Moves an auto-fulfilled spin back to 'owed' -- used when the
    automatic part couldn't happen (an unattributed viewer, a failed
    FoxCoin credit), so the prize shows up in Studio instead of vanishing."""
    _require_available()
    handle = _clean_handle(creator_handle)
    with _connect(timeout=timeout) as connection:
        _ensure_schema(connection)
        row = connection.execute(
            f"""
            UPDATE {TABLE_SPINS} SET fulfillment = %s, fulfilled_at = NULL
             WHERE creator_handle = %s AND id = %s AND status = %s AND fulfillment = %s
            RETURNING {_SPIN_COLUMNS}
            """,
            (FULFILL_OWED, handle, int(spin_id), STATUS_SPUN, FULFILL_AUTO),
        ).fetchone()
    return _row_to_spin(row)


def close_test_spin(creator_handle: str, spin_id: int, timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS) -> dict | None:
    """Test spins (Studio's overlay preview) never owe anything -- mark
    them done so they can't clutter the owed list. Only touches rows whose
    trigger_kind is 'test'."""
    _require_available()
    handle = _clean_handle(creator_handle)
    with _connect(timeout=timeout) as connection:
        _ensure_schema(connection)
        row = connection.execute(
            f"""
            UPDATE {TABLE_SPINS} SET fulfillment = %s, fulfilled_at = NOW()
             WHERE creator_handle = %s AND id = %s AND trigger_kind = 'test' AND status = %s
            RETURNING {_SPIN_COLUMNS}
            """,
            (FULFILL_DONE, handle, int(spin_id), STATUS_SPUN),
        ).fetchone()
    return _row_to_spin(row)


# ---------------------------------------------------------- mission state

MISSION_PENDING = "pending"
MISSION_PLAYING = "playing"
MISSION_DONE = "done"


def mission_ends_at(spin: dict):
    """When a timed mission's clock runs out (aware datetime), or None for
    an untimed mission / an unresolved spin. The clock starts when the
    overlay lands (MISSION_START_DELAY_SECONDS after spun_at)."""
    from datetime import datetime, timedelta

    prize = (spin or {}).get("prize") or {}
    duration = prize.get("duration_minutes")
    spun_at = (spin or {}).get("spun_at")
    if not duration or not spun_at:
        return None
    started = datetime.fromisoformat(spun_at) + timedelta(seconds=MISSION_START_DELAY_SECONDS)
    return started + timedelta(minutes=int(duration))


def mission_state(spin: dict, now=None) -> str:
    """pending / playing / done for the Studio queue view, derived from
    the existing columns -- no extra writes: queued -> pending; spun and
    still owed -> playing, unless it's a timed mission whose clock already
    ran out (then done); marked done (or a closed test spin) -> done."""
    from datetime import datetime, timezone

    if spin.get("status") == STATUS_QUEUED:
        return MISSION_PENDING
    if spin.get("status") != STATUS_SPUN or spin.get("fulfillment") != FULFILL_OWED:
        return MISSION_DONE
    ends_at = mission_ends_at(spin)
    if ends_at is not None and ends_at <= (now or datetime.now(timezone.utc)):
        return MISSION_DONE
    return MISSION_PLAYING
