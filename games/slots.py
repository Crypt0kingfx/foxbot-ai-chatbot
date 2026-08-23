"""Slots (Casino Phase 10): 3-reel, 5-symbol slot machine on the same
play_round() lifecycle every other game uses. Three independent weighted
draws (one per reel), evaluated against a paytable, exactly analogous to
roulette's single spin evaluated against a bet -- here there's no bet
selection at all (!slots <bet> is single-shot, like coinflip), the
paytable evaluates whatever the three reels land on.

SYMBOL WEIGHTS. Five symbols, weighted out of 31 total (doubling per
tier, rarest first):
    fox (jackpot)  weight 1   p = 1/31  ~= 3.23% per reel
    seven          weight 2   p = 2/31  ~= 6.45% per reel
    diamond        weight 4   p = 4/31  ~= 12.90% per reel
    rocket         weight 8   p = 8/31  ~= 25.81% per reel
    purple         weight 16  p = 16/31 ~= 51.61% per reel

PAYTABLE (total-return multiplier). Only fox and seven pay on a PAIR
(exactly two of three reels matching) -- an early design with "any pair
pays" blew RTP past 180%, since a pair of the common filler symbols
(diamond/rocket/purple) happens far too often (~58% of all spins) to
pay anything without wrecking the edge; real slot paytables have the
same shape, only premium symbols reward a near-miss:
    triple fox     (jackpot)  2650x
    triple seven              245x
    triple diamond             50x
    triple rocket                8x
    triple purple                2x
    pair fox                    38x
    pair seven                  15x
    pair diamond/rocket/purple,
    or no match at all           0

Exact RTP (computed from the weights+paytable above via exact fractions,
not simulated -- see games/slots_math_notes in the repo history / the
casino design conversation for the derivation): 96.465%, a 3.53% house
edge. Confirmed independently by a 20-million-spin simulation in
tests/test_slots.py converging to within ~0.2 percentage points -- the
jackpot's 2650x multiplier makes this a high-variance game, so a much
smaller sample (e.g. 2M spins) can show noticeably more spread purely
from jackpot-hit variance; that's expected statistical behavior, not a
sign the math is wrong, and the test suite documents why the larger
sample is used.

Anti-exploit: the three reels are drawn once via casino_rng.choice()
inside resolve(), which casino_rounds.play_round() guarantees is called
AT MOST ONCE PER round_id, ever. A resumed/retried round reuses the
persisted reels from metadata -- never a fresh spin.
"""

from __future__ import annotations

from services import casino_rng
from services import casino_rounds

GAME_ID = "slots"

SYMBOLS = ("fox", "seven", "diamond", "rocket", "purple")
SYMBOL_EMOJI = {"fox": "🦊", "seven": "7️⃣", "diamond": "💎", "rocket": "🚀", "purple": "💜"}

# Relative weight per reel -- doubling per tier, rarest (fox) first.
WEIGHTS = {"fox": 1, "seven": 2, "diamond": 4, "rocket": 8, "purple": 16}

# Flat weighted pool for casino_rng.choice() -- same choice()-based draw
# mechanism coinflip's SIDES already uses, weighting via repetition
# rather than a separate weighted-choice primitive in casino_rng.py
# (keeps services/casino_rng.py at zero diff, same as every other game).
_WEIGHTED_SYMBOLS = tuple(symbol for symbol in SYMBOLS for _ in range(WEIGHTS[symbol]))

TRIPLE_PAYOUT = {"fox": 2650, "seven": 245, "diamond": 50, "rocket": 8, "purple": 2}
# Only fox/seven pairs pay -- diamond/rocket/purple pairs are absent
# here on purpose, meaning .get(symbol, 0) below returns 0 for them.
PAIR_PAYOUT = {"fox": 38, "seven": 15}


def format_symbol(symbol: str) -> str:
    return SYMBOL_EMOJI.get(symbol, "❓")


def format_reels(reels) -> str:
    return " ".join(format_symbol(s) for s in reels)


def _spin_reels() -> tuple[str, str, str]:
    return (
        casino_rng.choice(_WEIGHTED_SYMBOLS),
        casino_rng.choice(_WEIGHTED_SYMBOLS),
        casino_rng.choice(_WEIGHTED_SYMBOLS),
    )


def _resolve(wager: int):
    r1, r2, r3 = _spin_reels()

    if r1 == r2 == r3:
        multiplier = TRIPLE_PAYOUT[r1]
        combo = f"triple_{r1}"
    elif r1 == r2 or r2 == r3 or r1 == r3:
        pair_symbol = r1 if (r1 == r2 or r1 == r3) else r2
        multiplier = PAIR_PAYOUT.get(pair_symbol, 0)
        combo = f"pair_{pair_symbol}" if multiplier > 0 else "no_win"
    else:
        multiplier = 0
        combo = "no_win"

    payout = wager * multiplier
    outcome = "win" if payout > 0 else "loss"
    metadata = {"reels": [r1, r2, r3], "combo": combo, "won": payout > 0}
    return outcome, payout, metadata


def play_slots(
    creator_id: str,
    user_id: str,
    wager: int,
    round_id: str,
    *,
    display_name: str | None = None,
    timeout: int = casino_rounds.DEFAULT_CONNECT_TIMEOUT_SECONDS,
) -> casino_rounds.RoundResult:
    return casino_rounds.play_round(
        creator_id, user_id, GAME_ID, wager, round_id,
        resolve=lambda: _resolve(wager),
        display_name=display_name, timeout=timeout,
    )
