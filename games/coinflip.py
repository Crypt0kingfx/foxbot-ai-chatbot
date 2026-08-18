"""Coinflip -- the first casino game (Casino Phase 4), deliberately trivial.
The point of this module is proving services/casino_rounds.py's round
model end-to-end, not game complexity; slots/blackjack/roulette later
plug into the exact same play_round() this module calls.

Paytable: wager N, pick heads or tails, win pays 2N (0% house edge for
now -- trivial to change later, not the point of this phase). The RNG
draw happens entirely inside _resolve() below, via services/casino_rng.py
(secrets-backed CSPRNG). play_coinflip()'s signature has no outcome/
result/roll parameter -- there is no code path by which a caller can
submit or influence which side the coin lands on.
"""

from __future__ import annotations

from services import casino_rng
from services import casino_rounds

GAME_ID = "coinflip"
SIDES = ("heads", "tails")


def _resolve(pick: str, wager: int):
    roll = casino_rng.choice(SIDES)
    won = roll == pick
    payout = wager * 2 if won else 0
    outcome = "win" if won else "loss"
    metadata = {"pick": pick, "roll": roll, "won": won}
    return outcome, payout, metadata


def play_coinflip(
    creator_id: str,
    user_id: str,
    pick: str,
    wager: int,
    round_id: str,
    *,
    display_name: str | None = None,
    timeout: int = casino_rounds.DEFAULT_CONNECT_TIMEOUT_SECONDS,
) -> casino_rounds.RoundResult:
    pick = str(pick or "").strip().lower()
    if pick not in SIDES:
        raise ValueError(f"pick must be one of {SIDES!r}, got {pick!r}.")

    return casino_rounds.play_round(
        creator_id, user_id, GAME_ID, wager, round_id,
        resolve=lambda: _resolve(pick, wager),
        display_name=display_name, timeout=timeout,
    )
