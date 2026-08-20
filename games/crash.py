"""Crash, auto-cashout v1 (Casino Phase 7): provably-fair crash-point
distribution, plugged into the same play_round() coinflip/roulette both
use. See games/roulette.py's docstring for why this is safe -- the same
argument applies here: play_round()'s JSONB metadata column already
supports arbitrary structured outcomes, and a resumed round always
reuses the persisted outcome/payout/metadata rather than recomputing
them. No changes to services/casino_rounds.py, games/coinflip.py, or
games/roulette.py were needed or made to build this.

DISTRIBUTION. crash_point = max(1.00, (1 - house_edge) / (1 - r)), where
r is uniform on [0, 1) drawn from the CSPRNG. For any target t > 1:
    P(crash_point >= t) = (1 - house_edge) / t
so expected payout per unit wagered = P(win) * t = 1 - house_edge --
the RTP is exactly (1 - house_edge) for every target, which is what
makes this the standard "provably fair crash" formula (see
tests/test_crash.py's RTP simulation for the empirical proof).

RNG NOTE: services/casino_rng.py only exposes roll()/choice() -- no
uniform-float primitive, and none is added here, since extending the
RNGProvider ABC with a new abstract method would break every existing
subclass (including the _FixedChoiceProvider/_FixedRollProvider test
fixtures already committed for coinflip/roulette) without reimplementing
it. Instead, r is derived entirely from roll(): a 53-bit integer (the
same precision as a double's mantissa) divided down into [0, 1). Zero
changes to services/casino_rng.py.

PAYOUT PRECISION: target is accepted at exactly 2 decimal places and
parsed via Decimal from the raw input, never float() -- this makes
target_cents an exact integer, so payout = (bet * target_cents) // 100
is an exact integer floor of bet * target with zero binary-float
rounding risk. crash_point itself stays a float (it is inherently
continuous; the win/loss comparison against target doesn't need
cent-exactness), but money math never touches a float.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from services import casino_rng
from services import casino_rounds

GAME_ID = "crash"

HOUSE_EDGE = 0.03

# 2**53 matches a double's mantissa precision -- the standard technique
# for deriving a uniform float in [0, 1) from an integer CSPRNG draw.
RESOLUTION = 2 ** 53

MIN_TARGET = Decimal("1.00")  # strict lower bound: target must be > this
MAX_TARGET = Decimal("100.00")
TARGET_DECIMALS = -2  # Decimal.as_tuple().exponent must be >= this (<=2 dp)


def _draw_crash_point() -> float:
    n = casino_rng.roll(0, RESOLUTION - 1)
    r = n / RESOLUTION  # in [0, 1) -- 1-r is always > 0, no divide-by-zero
    raw = (1.0 - HOUSE_EDGE) / (1.0 - r)
    return max(1.0, raw)


def _parse_target(target) -> tuple[float, int]:
    """Returns (target_float, target_cents). Raises ValueError on any
    invalid input -- non-numeric, <= 1.00, > MAX_TARGET, or more than 2
    decimal places."""
    if isinstance(target, Decimal):
        value = target
    else:
        try:
            value = Decimal(str(target).strip())
        except (InvalidOperation, ValueError):
            raise ValueError(f"target must be a number, got {target!r}.")

    # Decimal("NaN")/Decimal("inf") parse successfully as special values --
    # as_tuple().exponent is the STRING 'n'/'F' for those, not an int, so
    # the exponent check below must reject them first or it would raise
    # TypeError instead of a graceful ValueError.
    if not value.is_finite():
        raise ValueError(f"target must be a finite number, got {target!r}.")

    if value.as_tuple().exponent < TARGET_DECIMALS:
        raise ValueError("target must have at most 2 decimal places.")
    if value <= MIN_TARGET:
        raise ValueError(f"target must be greater than {MIN_TARGET}.")
    if value > MAX_TARGET:
        raise ValueError(f"target must be at most {MAX_TARGET}.")

    target_cents = int(value * 100)
    return float(value), target_cents


def _resolve(bet: int, target_float: float, target_cents: int):
    crash_point = _draw_crash_point()
    won = crash_point >= target_float
    payout = (bet * target_cents) // 100 if won else 0
    outcome = "win" if won else "loss"
    metadata = {
        "crash_point": crash_point, "target": target_float,
        "bet": bet, "payout": payout, "won": won,
    }
    return outcome, payout, metadata


def play_crash(
    creator_id: str,
    user_id: str,
    bet: int,
    target,
    round_id: str,
    *,
    display_name: str | None = None,
    timeout: int = casino_rounds.DEFAULT_CONNECT_TIMEOUT_SECONDS,
) -> casino_rounds.RoundResult:
    # target is validated (and converted to an exact integer-cents form)
    # BEFORE play_round/any DB call -- bad input never touches Postgres,
    # same "no round created" guarantee casino_ledger.debit() already
    # gives play_round() for bad wagers.
    target_float, target_cents = _parse_target(target)

    return casino_rounds.play_round(
        creator_id, user_id, GAME_ID, bet, round_id,
        resolve=lambda: _resolve(bet, target_float, target_cents),
        display_name=display_name, timeout=timeout,
    )
