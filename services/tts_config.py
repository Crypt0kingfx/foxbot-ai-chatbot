"""Per-creator text-to-speech configuration for the Casino Stream Overlay's
TTS sibling (/overlay/tts): which voice/volume the browser's own
SpeechSynthesis API should use, and the guardrails that decide whether a
given casino_win is worth interrupting the stream's audio for.

Keyed by creator_HANDLE, not creator_id -- deliberately different from
services/casino_config.py's creator_id keying. Both real consumers of this
config only ever have a handle in scope: the emit hook
(app.py's _foxbot_casino_emit_win_v1) receives creator_handle, not
creator_id, and the public /overlay/tts-data endpoint is an anonymous OBS
browser source scoped by the SAME ?handle= query param /overlay/casino-data
already uses (no session to resolve a creator_id from at all). Introducing
a creator_id lookup here would mean resolving handle->id at both call
sites for no benefit.

Same fail-closed contract as casino_config.py: no local-file fallback, and
a creator with no row yet gets sane defaults (TTS is opt-in like
casino_enabled, so `enabled` defaults to False -- everything else works
out of the box once a creator turns it on).
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass


TABLE_CONFIG = "tts_config"

_schema_lock = threading.Lock()
_schema_ready = False

DEFAULT_CONNECT_TIMEOUT_SECONDS = 10

# Opt-in, not opt-out -- same Casino-Phase-5-style go-live gate as
# casino_config.DEFAULT_CASINO_ENABLED. A creator's stream shouldn't
# suddenly start talking because a column defaulted to True.
DEFAULT_TTS_ENABLED = False

# '' means "browser default voice" -- speak-time code must fall back to
# the overlay page's default voice when this name isn't found in that
# page's own live getVoices() list (a different machine, a removed voice
# pack, or simply never configured yet).
DEFAULT_VOICE_NAME = ""

DEFAULT_VOLUME = 80
DEFAULT_CHAR_LIMIT = 200
DEFAULT_MIN_PAYOUT = 100
DEFAULT_COOLDOWN_SECONDS = 15

MIN_VOLUME = 0
MAX_VOLUME = 100
MIN_CHAR_LIMIT = 20
MAX_CHAR_LIMIT = 500
MIN_COOLDOWN_SECONDS = 5
MAX_COOLDOWN_SECONDS = 300

# Chat-readout sibling of the win-announcement defaults above -- independent
# toggle (read_chat_enabled), independently tuned: chat is meant to feel
# closer to real-time than the rare, deliberately-slow win cooldown, and a
# short message like "lol" shouldn't trigger TTS at all (no min-length
# guard exists for wins since _foxbot_tts_build_line_v1's output is always
# a full server-generated sentence, never a bare user string).
DEFAULT_READ_CHAT_ENABLED = False
DEFAULT_CHAT_COOLDOWN_SECONDS = 3
DEFAULT_CHAT_MIN_CHARS = 4

MIN_CHAT_COOLDOWN_SECONDS = 1
MAX_CHAT_COOLDOWN_SECONDS = 60
MIN_CHAT_MIN_CHARS = 1
MAX_CHAT_MIN_CHARS = 50


class TtsConfigUnavailable(Exception):
    """Raised when DATABASE_URL isn't configured -- see module docstring
    for why there is no local-file fallback here."""


@dataclass(frozen=True)
class TtsConfig:
    creator_handle: str
    read_wins_enabled: bool
    voice_name: str
    volume: int
    char_limit: int
    min_payout: int
    cooldown_seconds: int
    read_chat_enabled: bool
    chat_cooldown_seconds: int
    chat_min_chars: int
    # Server-owned "already spoken" cursors (foxbot_events.id high-water
    # marks) for each independent TTS stream -- see ack_event() below.
    # -1 means "never bootstrapped for this stream"; the overlay data
    # endpoint treats that as a one-time silent catch-up to "now" (via
    # mark_stream_bootstrapped(), not ack_event() -- see that function's
    # docstring for why they're different), never a backlog replay. 0 is
    # a legitimate ongoing value after that: "bootstrapped, but nothing
    # has happened for this stream yet". NOT settable via set_config():
    # only ack_event() (called from the overlay's own POST
    # /overlay/tts-ack, after it has actually spoken or explicitly
    # skipped a line) and mark_stream_bootstrapped() may advance these.
    last_acked_win_event_id: int
    last_acked_chat_event_id: int


def database_url() -> str:
    return str(os.getenv("DATABASE_URL") or "").strip()


def is_available() -> bool:
    return bool(database_url())


def _require_available() -> None:
    if not is_available():
        raise TtsConfigUnavailable(
            "TTS config requires DATABASE_URL (Postgres) -- there is no "
            "local-file fallback by design."
        )


def _connect(timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS):
    import psycopg

    return psycopg.connect(database_url(), connect_timeout=timeout)


def _ensure_schema(connection) -> None:
    """CREATE TABLE IF NOT EXISTS is NOT safe against two independent
    connections/processes both calling this for the very first time
    concurrently, before the table exists: both see "doesn't exist yet"
    (Postgres visibility rules -- an uncommitted DDL from another
    session is invisible to this session's own existence check) and both
    attempt the CREATE; only one can win at commit time. Confirmed by a
    direct reproduction (10 parallel first-time callers against a freshly
    dropped table): most raised psycopg.errors.UniqueViolation on
    pg_type_typname_nsp_index, the underlying catalog collision. The
    module-level _schema_lock only serializes callers WITHIN this one
    process -- it cannot prevent a race against a different process (a
    second test run, a live app server) hitting the same fresh database
    at the same moment.

    Losing the race must not surface as an error to a caller who did
    nothing wrong: the table exists either way once the dust settles, so
    catch the specific duplicate-object errors and roll back this
    connection's now-aborted transaction (a failed statement poisons the
    rest of the transaction until rolled back) so the caller's own query
    right after this can proceed normally.
    """
    global _schema_ready
    if _schema_ready:
        return

    with _schema_lock:
        if _schema_ready:
            return

        import psycopg

        try:
            connection.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {TABLE_CONFIG} (
                    creator_handle   TEXT PRIMARY KEY,
                    enabled          BOOLEAN NOT NULL DEFAULT {str(DEFAULT_TTS_ENABLED).upper()},
                    voice_name       TEXT NOT NULL DEFAULT '',
                    volume           INT NOT NULL DEFAULT {DEFAULT_VOLUME},
                    char_limit       INT NOT NULL DEFAULT {DEFAULT_CHAR_LIMIT},
                    min_payout       INT NOT NULL DEFAULT {DEFAULT_MIN_PAYOUT},
                    cooldown_seconds INT NOT NULL DEFAULT {DEFAULT_COOLDOWN_SECONDS},
                    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
        except (psycopg.errors.DuplicateTable, psycopg.errors.UniqueViolation):
            connection.rollback()

        # Additive migration for an already-deployed table (win-TTS shipped
        # before this chat-TTS/cursor work): ADD COLUMN IF NOT EXISTS is
        # idempotent and safe to re-run every process start, unlike the
        # CREATE TABLE above which only ever matters on a truly fresh
        # database. `enabled` (the original column) is deliberately left
        # in place and untouched -- it IS read_wins_enabled's storage,
        # just under its original name, so no backfill/rename is needed
        # and no risk of resetting a creator's existing win-TTS choice.
        for statement in (
            f"ALTER TABLE {TABLE_CONFIG} ADD COLUMN IF NOT EXISTS "
            f"read_chat_enabled BOOLEAN NOT NULL DEFAULT {str(DEFAULT_READ_CHAT_ENABLED).upper()}",
            f"ALTER TABLE {TABLE_CONFIG} ADD COLUMN IF NOT EXISTS "
            f"chat_cooldown_seconds INT NOT NULL DEFAULT {DEFAULT_CHAT_COOLDOWN_SECONDS}",
            f"ALTER TABLE {TABLE_CONFIG} ADD COLUMN IF NOT EXISTS "
            f"chat_min_chars INT NOT NULL DEFAULT {DEFAULT_CHAT_MIN_CHARS}",
            # -1, not 0: a real foxbot_events id is never 0 (BIGSERIAL
            # starts at 1), so -1 is the only value that can unambiguously
            # mean "this stream has never been bootstrapped" -- see
            # /overlay/tts-data's own comment on why 0 can't do that job
            # (0 is also the legitimate, ongoing state of "bootstrapped,
            # but nothing has happened for this stream yet").
            f"ALTER TABLE {TABLE_CONFIG} ADD COLUMN IF NOT EXISTS "
            f"last_acked_win_event_id BIGINT NOT NULL DEFAULT -1",
            f"ALTER TABLE {TABLE_CONFIG} ADD COLUMN IF NOT EXISTS "
            f"last_acked_chat_event_id BIGINT NOT NULL DEFAULT -1",
        ):
            try:
                connection.execute(statement)
            except (psycopg.errors.DuplicateColumn, psycopg.errors.UniqueViolation):
                connection.rollback()

        # Same fix as services/foxbot_events.py's _ensure_schema (found
        # while building this file's own ack_event() cursor work, and
        # applied here too since every write path below relies on it):
        # _schema_ready is shared across every connection in this process,
        # but each connection has its own transaction. Without this
        # commit, a DIFFERENT connection could see the flag turn true and
        # skip its own CREATE/ALTER TABLE entirely, then query a table
        # that -- from ITS transaction's snapshot -- doesn't exist yet,
        # because this connection's DDL was never actually committed.
        connection.commit()
        _schema_ready = True


def get_config(creator_handle: str, timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS) -> TtsConfig:
    _require_available()

    from services import creator_access

    handle = creator_access.clean_handle(creator_handle)
    if not handle:
        raise ValueError("creator_handle is required.")

    with _connect(timeout=timeout) as connection:
        _ensure_schema(connection)
        row = connection.execute(
            f"""
            SELECT enabled, voice_name, volume, char_limit, min_payout, cooldown_seconds,
                   read_chat_enabled, chat_cooldown_seconds, chat_min_chars,
                   last_acked_win_event_id, last_acked_chat_event_id
            FROM {TABLE_CONFIG} WHERE creator_handle = %s
            """,
            (handle,),
        ).fetchone()

    if row is None:
        return TtsConfig(
            handle, DEFAULT_TTS_ENABLED, DEFAULT_VOICE_NAME, DEFAULT_VOLUME,
            DEFAULT_CHAR_LIMIT, DEFAULT_MIN_PAYOUT, DEFAULT_COOLDOWN_SECONDS,
            DEFAULT_READ_CHAT_ENABLED, DEFAULT_CHAT_COOLDOWN_SECONDS, DEFAULT_CHAT_MIN_CHARS,
            -1, -1,
        )
    return TtsConfig(
        handle, bool(row[0]), str(row[1] or ""), int(row[2]), int(row[3]), int(row[4]), int(row[5]),
        bool(row[6]), int(row[7]), int(row[8]), int(row[9]), int(row[10]),
    )


def set_config(
    creator_handle: str,
    *,
    read_wins_enabled: bool | None = None,
    voice_name: str | None = None,
    volume: int | None = None,
    char_limit: int | None = None,
    min_payout: int | None = None,
    cooldown_seconds: int | None = None,
    read_chat_enabled: bool | None = None,
    chat_cooldown_seconds: int | None = None,
    chat_min_chars: int | None = None,
    timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS,
) -> TtsConfig:
    _require_available()

    from services import creator_access

    handle = creator_access.clean_handle(creator_handle)
    if not handle:
        raise ValueError("creator_handle is required.")

    is_enabled = DEFAULT_TTS_ENABLED if read_wins_enabled is None else bool(read_wins_enabled)
    name = DEFAULT_VOICE_NAME if voice_name is None else str(voice_name).strip()[:200]
    vol = DEFAULT_VOLUME if volume is None else int(volume)
    limit = DEFAULT_CHAR_LIMIT if char_limit is None else int(char_limit)
    floor = DEFAULT_MIN_PAYOUT if min_payout is None else int(min_payout)
    cooldown = DEFAULT_COOLDOWN_SECONDS if cooldown_seconds is None else int(cooldown_seconds)
    chat_enabled = DEFAULT_READ_CHAT_ENABLED if read_chat_enabled is None else bool(read_chat_enabled)
    chat_cooldown = DEFAULT_CHAT_COOLDOWN_SECONDS if chat_cooldown_seconds is None else int(chat_cooldown_seconds)
    chat_min = DEFAULT_CHAT_MIN_CHARS if chat_min_chars is None else int(chat_min_chars)

    with _connect(timeout=timeout) as connection:
        _ensure_schema(connection)
        current = connection.execute(
            f"""
            SELECT enabled, voice_name, volume, char_limit, min_payout, cooldown_seconds,
                   read_chat_enabled, chat_cooldown_seconds, chat_min_chars
            FROM {TABLE_CONFIG} WHERE creator_handle = %s
            """,
            (handle,),
        ).fetchone()

        # Partial updates (only some kwargs given) must not silently reset
        # the other fields back to module defaults -- same contract as
        # casino_config.set_config(). last_acked_*_event_id is deliberately
        # NOT among these fields: this function never touches those columns
        # at all (see the INSERT/UPDATE below), so a settings save can never
        # reset the overlay's playback cursor.
        if current is not None:
            if read_wins_enabled is None:
                is_enabled = bool(current[0])
            if voice_name is None:
                name = str(current[1] or "")
            if volume is None:
                vol = int(current[2])
            if char_limit is None:
                limit = int(current[3])
            if min_payout is None:
                floor = int(current[4])
            if cooldown_seconds is None:
                cooldown = int(current[5])
            if read_chat_enabled is None:
                chat_enabled = bool(current[6])
            if chat_cooldown_seconds is None:
                chat_cooldown = int(current[7])
            if chat_min_chars is None:
                chat_min = int(current[8])

        if not (MIN_VOLUME <= vol <= MAX_VOLUME):
            raise ValueError(f"volume must be between {MIN_VOLUME} and {MAX_VOLUME}.")
        if not (MIN_CHAR_LIMIT <= limit <= MAX_CHAR_LIMIT):
            raise ValueError(f"char_limit must be between {MIN_CHAR_LIMIT} and {MAX_CHAR_LIMIT}.")
        if floor < 0:
            raise ValueError("min_payout must be zero or a positive integer.")
        if not (MIN_COOLDOWN_SECONDS <= cooldown <= MAX_COOLDOWN_SECONDS):
            raise ValueError(f"cooldown_seconds must be between {MIN_COOLDOWN_SECONDS} and {MAX_COOLDOWN_SECONDS}.")
        if not (MIN_CHAT_COOLDOWN_SECONDS <= chat_cooldown <= MAX_CHAT_COOLDOWN_SECONDS):
            raise ValueError(
                f"chat_cooldown_seconds must be between {MIN_CHAT_COOLDOWN_SECONDS} and {MAX_CHAT_COOLDOWN_SECONDS}."
            )
        if not (MIN_CHAT_MIN_CHARS <= chat_min <= MAX_CHAT_MIN_CHARS):
            raise ValueError(f"chat_min_chars must be between {MIN_CHAT_MIN_CHARS} and {MAX_CHAT_MIN_CHARS}.")

        connection.execute(
            f"""
            INSERT INTO {TABLE_CONFIG}
                (creator_handle, enabled, voice_name, volume, char_limit, min_payout, cooldown_seconds,
                 read_chat_enabled, chat_cooldown_seconds, chat_min_chars, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (creator_handle) DO UPDATE
                SET enabled = EXCLUDED.enabled,
                    voice_name = EXCLUDED.voice_name,
                    volume = EXCLUDED.volume,
                    char_limit = EXCLUDED.char_limit,
                    min_payout = EXCLUDED.min_payout,
                    cooldown_seconds = EXCLUDED.cooldown_seconds,
                    read_chat_enabled = EXCLUDED.read_chat_enabled,
                    chat_cooldown_seconds = EXCLUDED.chat_cooldown_seconds,
                    chat_min_chars = EXCLUDED.chat_min_chars,
                    updated_at = NOW()
            """,
            (handle, is_enabled, name, vol, limit, floor, cooldown, chat_enabled, chat_cooldown, chat_min),
        )

        cursor_row = connection.execute(
            f"SELECT last_acked_win_event_id, last_acked_chat_event_id FROM {TABLE_CONFIG} WHERE creator_handle = %s",
            (handle,),
        ).fetchone()

    last_win, last_chat = (int(cursor_row[0]), int(cursor_row[1])) if cursor_row else (0, 0)
    return TtsConfig(
        handle, is_enabled, name, vol, limit, floor, cooldown,
        chat_enabled, chat_cooldown, chat_min, last_win, last_chat,
    )


_ACK_STREAM_KIND = {"win": "tts_message", "chat": "tts_chat_message"}
_ACK_STREAM_COLUMN = {"win": "last_acked_win_event_id", "chat": "last_acked_chat_event_id"}


def ack_event(
    creator_handle: str, stream: str, event_id: int, timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS
) -> int | None:
    """Advances the server-owned "already handled" cursor for one TTS
    stream (`stream` is "win" or "chat") forward to `event_id` -- the
    mechanism that replaces the old client-side "seen" Set the overlay
    used to keep in a page-local JS variable. Moving this to the server
    fixes two real problems with that approach: a reload/crash of the
    overlay page no longer permanently loses track of what was already
    spoken (the cursor survives in Postgres, not in a tab that can vanish
    mid-queue), and two simultaneous overlay instances for the same
    creator (an OBS source plus a preview tab, say) no longer both
    independently speak every line -- whichever instance acks first
    "wins" that line for both.

    Callers (see /overlay/tts-ack in app.py) must only call this AFTER
    actually finishing with a line -- either the browser's speechSynthesis
    genuinely finished/errored on it, or the overlay's own max-queue-depth
    logic explicitly dropped it -- never merely because it was fetched.
    Fetching and speaking are different moments; conflating them is
    exactly the bug this replaces (a message fetched but not yet spoken
    when the page reloads would otherwise be lost forever).

    `event_id` is clamped to that creator's actual latest event of the
    given stream before being applied, and the update is a GREATEST(...)
    (monotonic, never moves backward). Both guardrails matter because
    /overlay/tts-ack is unauthenticated by necessity (same anonymous-OBS-
    browser-source trust model as /overlay/tts-data and
    /overlay/casino-data): the clamp means a forged/oversized event_id
    can never skip further ahead than events that genuinely exist for
    that creator -- bounded exactly like a normal fast-forward, never an
    arbitrary future skip that could silence a creator's overlay
    indefinitely. Returns the clamped value actually applied, or None if
    unavailable/invalid -- the route treats both as best-effort.
    """
    if not is_available():
        return None
    if stream not in _ACK_STREAM_COLUMN:
        return None

    from services import creator_access

    handle = creator_access.clean_handle(creator_handle)
    if not handle:
        return None

    try:
        event_id = int(event_id)
    except (TypeError, ValueError):
        return None
    if event_id <= 0:
        return None

    column = _ACK_STREAM_COLUMN[stream]
    kind = _ACK_STREAM_KIND[stream]

    with _connect(timeout=timeout) as connection:
        _ensure_schema(connection)
        row = connection.execute(
            "SELECT COALESCE(MAX(id), 0) FROM foxbot_events WHERE creator_handle = %s AND kind = %s",
            (handle, kind),
        ).fetchone()
        real_max = int(row[0]) if row else 0
        clamped = min(event_id, real_max)
        if clamped <= 0:
            return None

        connection.execute(
            f"""
            INSERT INTO {TABLE_CONFIG} (creator_handle, {column}, updated_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (creator_handle) DO UPDATE
                SET {column} = GREATEST({TABLE_CONFIG}.{column}, EXCLUDED.{column}),
                    updated_at = NOW()
            """,
            (handle, clamped),
        )

    return clamped


def mark_stream_bootstrapped(
    creator_handle: str, stream: str, max_id: int, timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS
) -> int | None:
    """Records that a TTS stream's cursor has completed its ONE-TIME
    bootstrap (the "-1 -> real position" transition), even when max_id is
    legitimately 0 -- unlike ack_event() above, which intentionally
    refuses to persist a non-positive value (a real ack always references
    a genuine, already-spoken event with id >= 1, so 0/negative is always
    invalid input there). Bootstrapping is different: /overlay/tts-data's
    silent-catch-up path (see that route's own comment) must be able to
    move a stream from "-1, never touched" to "0, caught up, nothing has
    happened yet" just as validly as to "47, caught up to the 47th
    event" -- both are real, intentional bootstrap outcomes, not a no-op.

    Only ever called internally, from that one bootstrap code path --
    never exposed to the public, unauthenticated /overlay/tts-ack route
    (ack_event is what that route calls). Still clamps to the creator's
    actual latest event for the stream and is monotonic (GREATEST), same
    safety properties as ack_event, for the same reasons.
    """
    if not is_available():
        return None
    if stream not in _ACK_STREAM_COLUMN:
        return None

    from services import creator_access

    handle = creator_access.clean_handle(creator_handle)
    if not handle:
        return None

    try:
        max_id = int(max_id)
    except (TypeError, ValueError):
        return None
    if max_id < 0:
        return None

    column = _ACK_STREAM_COLUMN[stream]
    kind = _ACK_STREAM_KIND[stream]

    with _connect(timeout=timeout) as connection:
        _ensure_schema(connection)
        row = connection.execute(
            "SELECT COALESCE(MAX(id), 0) FROM foxbot_events WHERE creator_handle = %s AND kind = %s",
            (handle, kind),
        ).fetchone()
        real_max = int(row[0]) if row else 0
        clamped = min(max_id, real_max)

        connection.execute(
            f"""
            INSERT INTO {TABLE_CONFIG} (creator_handle, {column}, updated_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (creator_handle) DO UPDATE
                SET {column} = GREATEST({TABLE_CONFIG}.{column}, EXCLUDED.{column}),
                    updated_at = NOW()
            """,
            (handle, clamped),
        )

    return clamped
