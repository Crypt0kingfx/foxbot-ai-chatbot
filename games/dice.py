"""Dice (Casino Phase 10): single-die high/low and exact-number bets, on
the same play_round() lifecycle coinflip/roulette/crash/blackjack all
use. One RNG draw (a single d6 roll), evaluated against whichever
prediction was made -- same "one draw, multiple bet shapes" pattern
roulette already established for red/black/number/dozen/column.

PAYOUT DESIGN. Both bet types are solved to exactly 97% RTP (3% house
edge), the same edge crash already uses (services/casino_rng.py's
HOUSE_EDGE convention) -- one consistent house-edge story across the
casino rather than a different number picked per game:
  - high (4,5,6) / low (1,2,3): p=1/2, multiplier=194% -> RTP = 0.5*1.94 = 0.97
  - exact number (1-6):          p=1/6, multiplier=582% -> RTP = (1/6)*5.82 = 0.97
Multipliers are expressed as integer percent (194, 582) and applied via
floor division -- payout = (wager * multiplier_pct) // 100. At small
wagers this floors the *effective* multiplier slightly below the
theoretical one (e.g. wager=10 -> payout=19, an effective 1.90x rather
than 1.94x), the same "always floor, never round up" convention
blackjack's 3:2 payout already has on odd bets -- not a bug, and it
converges to the true 97% RTP as wager grows (confirmed by simulation
at multiple wager sizes in tests/test_dice.py).
"""

from __future__ import annotations

from services import casino_rng
from services import casino_rounds

GAME_ID = "dice"

PREDICTIONS = ("high", "low", "1", "2", "3", "4", "5", "6")
HIGH_FACES = (4, 5, 6)
LOW_FACES = (1, 2, 3)

# Total-return multiplier, expressed as integer percent -- both solved
# for exactly 97% RTP against their respective win probability (see
# module docstring).
PAYOUT_PERCENT_EVEN_MONEY = 194  # high or low, p=1/2
PAYOUT_PERCENT_EXACT_NUMBER = 582  # exact digit, p=1/6


def _wins(prediction: str, roll: int) -> bool:
    if prediction == "high":
        return roll in HIGH_FACES
    if prediction == "low":
        return roll in LOW_FACES
    return roll == int(prediction)


def _payout_percent(prediction: str) -> int:
    return PAYOUT_PERCENT_EVEN_MONEY if prediction in ("high", "low") else PAYOUT_PERCENT_EXACT_NUMBER


def _resolve(prediction: str, wager: int):
    roll = casino_rng.roll(1, 6)
    won = _wins(prediction, roll)
    payout = (wager * _payout_percent(prediction)) // 100 if won else 0
    outcome = "win" if won else "loss"
    metadata = {"prediction": prediction, "roll": roll, "won": won}
    return outcome, payout, metadata


def play_dice(
    creator_id: str,
    user_id: str,
    prediction: str,
    wager: int,
    round_id: str,
    *,
    display_name: str | None = None,
    timeout: int = casino_rounds.DEFAULT_CONNECT_TIMEOUT_SECONDS,
) -> casino_rounds.RoundResult:
    prediction = str(prediction or "").strip().lower()
    if prediction not in PREDICTIONS:
        raise ValueError(f"prediction must be one of {PREDICTIONS!r}, got {prediction!r}.")

    return casino_rounds.play_round(
        creator_id, user_id, GAME_ID, wager, round_id,
        resolve=lambda: _resolve(prediction, wager),
        display_name=display_name, timeout=timeout,
    )
