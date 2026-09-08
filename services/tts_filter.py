"""Profanity screening for text about to be spoken by the TTS overlay.

Deliberately NOT a hand-rolled wordlist: a bespoke list is incomplete on
day one and needs constant upkeep against leetspeak/spacing workarounds
established libraries already handle. Uses better-profanity (maintained,
MIT-licensed, pure-Python, no network calls -- consistent with the rest of
this feature being fully free/local, no external TTS or moderation
service).

Fails CLOSED on any load error: if the censor wordlist can't be imported
or loaded for any reason, is_clean() returns False (block) rather than
speaking unscreened text -- same "under-deliver rather than risk it"
discipline as services/foxbot_events.py's emit_event().
"""

from __future__ import annotations

import threading


_lock = threading.Lock()
_loaded = False
_load_failed = False


def _ensure_loaded() -> None:
    global _loaded, _load_failed
    if _loaded or _load_failed:
        return

    with _lock:
        if _loaded or _load_failed:
            return
        try:
            from better_profanity import profanity

            profanity.load_censor_words()
            _loaded = True
        except Exception:
            _load_failed = True


def is_clean(text: str) -> bool:
    """True only if `text` was checked against the wordlist and found
    clean. Returns False (not clean) both when profanity is found AND
    when the check itself couldn't run -- callers must treat both cases
    identically: don't speak it."""
    _ensure_loaded()
    if _load_failed:
        return False

    try:
        from better_profanity import profanity

        return not profanity.contains_profanity(str(text or ""))
    except Exception:
        return False
