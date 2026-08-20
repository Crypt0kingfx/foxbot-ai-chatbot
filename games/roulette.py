"""Roulette (Casino Phase 6): European single-zero wheel, full bet table,
plugged into the same play_round() that games/coinflip.py proved out.

Round-model note: play_round()'s `metadata` column is already an arbitrary
JSONB blob, and _decide_outcome() only ever invokes `resolve` when
`outcome IS NULL` -- a resumed/retried round always reads its persisted
outcome/payout/metadata back from the row rather than recomputing them.
Coinflip's {"pick", "roll", "won"} metadata already exercises this; this
module just puts a richer dict ({"bet_type", "selection", "pocket",
"color", "won"}) through the exact same, unmodified path. No changes to
services/casino_rounds.py or games/coinflip.py were needed or made.

0 is green and loses every bet type EXCEPT a straight-up bet on 0 itself
-- that's where the house edge lives on a single-zero wheel, not from 0
being unbettable. _wins() checks "number" before the zero short-circuit
so a `!roulette number 0` bet can actually win, at true single-number
odds (1-in-37), same as every other straight-up number.

Payout convention matches coinflip: the returned `payout` is the TOTAL
credited amount (stake + profit), not just profit -- wager*36 on a 35:1
straight-up win, wager*3 on a 2:1 dozen/column win, wager*2 on a 1:1
even-money win.
"""

from __future__ import annotations

from services import casino_rng
from services import casino_rounds

GAME_ID = "roulette"

BET_TYPES = ("number", "red", "black", "even", "odd", "high", "low", "dozen", "column")

# Standard European/American felt layout -- fixed by convention, not by
# wheel pocket order. 18 red + 18 black + 0 (green) = 37 pockets.
RED_NUMBERS = frozenset({1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36})
BLACK_NUMBERS = frozenset(set(range(1, 37)) - RED_NUMBERS)

COLUMNS = {
    1: frozenset(n for n in range(1, 37) if n % 3 == 1),
    2: frozenset(n for n in range(1, 37) if n % 3 == 2),
    3: frozenset(n for n in range(1, 37) if n % 3 == 0),
}

# Total return multiplier (stake included), i.e. "X:1" pays wager*(X+1).
PAYOUT_MULTIPLIER = {
    "number": 36, "red": 2, "black": 2, "even": 2, "odd": 2,
    "high": 2, "low": 2, "dozen": 3, "column": 3,
}


def pocket_color(pocket: int) -> str:
    if pocket == 0:
        return "green"
    return "red" if pocket in RED_NUMBERS else "black"


def _wins(bet_type: str, selection: int | None, pocket: int) -> bool:
    if bet_type == "number":
        return pocket == selection
    if pocket == 0:
        return False  # green loses every non-number bet
    if bet_type == "red":
        return pocket in RED_NUMBERS
    if bet_type == "black":
        return pocket in BLACK_NUMBERS
    if bet_type == "even":
        return pocket % 2 == 0
    if bet_type == "odd":
        return pocket % 2 == 1
    if bet_type == "high":
        return 19 <= pocket <= 36
    if bet_type == "low":
        return 1 <= pocket <= 18
    if bet_type == "dozen":
        return (selection - 1) * 12 + 1 <= pocket <= selection * 12
    if bet_type == "column":
        return pocket in COLUMNS[selection]
    raise ValueError(f"unknown bet_type {bet_type!r}")


def _resolve(bet_type: str, selection: int | None, wager: int):
    pocket = casino_rng.roll(0, 36)
    won = _wins(bet_type, selection, pocket)
    payout = wager * PAYOUT_MULTIPLIER[bet_type] if won else 0
    outcome = "win" if won else "loss"
    metadata = {
        "bet_type": bet_type, "selection": selection,
        "pocket": pocket, "color": pocket_color(pocket), "won": won,
    }
    return outcome, payout, metadata


def play_roulette(
    creator_id: str,
    user_id: str,
    bet_type: str,
    selection: int | None,
    wager: int,
    round_id: str,
    *,
    display_name: str | None = None,
    timeout: int = casino_rounds.DEFAULT_CONNECT_TIMEOUT_SECONDS,
) -> casino_rounds.RoundResult:
    bet_type = str(bet_type or "").strip().lower()
    if bet_type not in BET_TYPES:
        raise ValueError(f"bet_type must be one of {BET_TYPES!r}, got {bet_type!r}.")

    if bet_type == "number":
        if not isinstance(selection, int) or isinstance(selection, bool) or not (0 <= selection <= 36):
            raise ValueError("number bet needs an integer selection in 0..36.")
    elif bet_type in ("dozen", "column"):
        if selection not in (1, 2, 3):
            raise ValueError(f"{bet_type} bet needs a selection in 1..3.")
    else:
        selection = None  # red/black/even/odd/high/low take no selection

    return casino_rounds.play_round(
        creator_id, user_id, GAME_ID, wager, round_id,
        resolve=lambda: _resolve(bet_type, selection, wager),
        display_name=display_name, timeout=timeout,
    )
