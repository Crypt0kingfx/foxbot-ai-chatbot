"""Blackjack, interactive v1 (Casino Phase 8): the classic, built on the
same primitives coinflip/roulette/crash proved, but NOT on play_round()
itself. play_round()'s contract is one atomic sequence -- debit, call
resolve() exactly once, settle -- with no way to pause between "debit"
and "settle" across separate chat messages. Blackjack needs the round to
stay OPEN across !hit calls, each mutating persisted state without
settling. So this module gives blackjack its own three-phase lifecycle
(deal/hit/stand) that reuses services/casino_rounds.py's TABLE, schema,
connection helper, state constants, RoundResult, and exception types via
import -- casino_rounds.py itself has ZERO changes, same guarantee
coinflip/roulette/crash already have.

STRUCTURED OUTCOME, THE MULTI-STEP VERSION. Every other game persists
its outcome once, atomically, before any dependent action (payout).
Blackjack generalizes this to a SEQUENCE of individually-idempotent
decisions on top of one immutable, pre-committed source of randomness:

  - DECK INTEGRITY: a full 52-card deck is Fisher-Yates shuffled via
    casino_rng.roll() alone (zero changes to services/casino_rng.py,
    same principle as crash's float derivation) and persisted WHOLE into
    metadata["deck"] at deal time, together with a metadata["deck_cursor"]
    marking how many cards have been dealt. The deck is written exactly
    once and never touched again. Every draw -- the initial 4 cards,
    every hit, every dealer catch-up card -- reads deck[deck_cursor] and
    advances the cursor. A crash/resume mid-hand reads the SAME deck
    back and continues from the SAME cursor: no card already shown can
    ever change, and no future card can be rerolled by retrying.

  - PER-HIT IDEMPOTENCY (the genuinely new piece vs. the other games):
    a single-shot game only needs one "already decided?" check. Blackjack
    needs one PER HIT, because each !hit is a separate chat message with
    its own dedupe_key, and a network-retry of the SAME message must
    return the SAME card, never draw a new one. metadata["hit_log"] maps
    each hit's dedupe_key to the card it drew; hit() checks this BEFORE
    touching the deck cursor.

  - LAZY TIMEOUT, NO BACKGROUND JOB: metadata["deal_timestamp"] (a plain
    time.time() float) is stamped at deal. There is no scheduler --
    staleness (now - deal_timestamp > TIMEOUT_SECONDS) is checked lazily,
    inline, whenever a hand is next touched: by hit() before drawing, and
    by deal()'s active-hand-slot reservation before rejecting a second
    deal. A stale hand is auto-stood (dealer plays out, hand settles)
    using the exact same code path an explicit !stand uses -- elapsed
    time doesn't change what "stand" does, it only changes what TRIGGERS
    it. Explicit !stand never needs to check staleness at all.

ONE ACTIVE HAND PER USER: enforced by a new table, casino_active_hands,
keyed by (creator_id, user_id, game_id) -- the ONE net-new schema object
in this feature. deal() reserves a slot via INSERT ... ON CONFLICT DO
NOTHING RETURNING, which is race-free by construction: two concurrent
deal attempts can't both win. If reservation fails because the slot
already holds a DIFFERENT, non-stale round, deal() rejects before ever
debiting -- no phantom-hand bug where a rejected deal still takes money.
If reservation succeeds but the wager debit then fails (InsufficientFunds),
the reservation is rolled back so the player isn't left blocked from ever
dealing again by a hand that never actually happened.

Dealer plays stand-on-all-17s (S17, including soft 17) -- simpler to
implement correctly than distinguishing soft/hard at the boundary, and
player-friendly. Payouts, integer math throughout: natural blackjack
(21 on the first two cards) pays 3:2 as bet + (bet*3)//2 -- floor,
never a float, same "always round down, never up" convention crash
established for its integer-cent math. Regular win pays 1:1 (bet*2
total return). Push returns the stake (payout=bet). Bust/loss pays 0.
"""

from __future__ import annotations

import threading
import time

from services import casino_config
from services import casino_ledger as cl
from services import casino_rng
from services import casino_rounds as cr

GAME_ID = "blackjack"
CURRENCY_PROMO = cr.CURRENCY_PROMO

TABLE_ACTIVE_HANDS = "casino_active_hands"

# Lazy timeout: how long an open hand can sit untouched before the NEXT
# interaction (a hit, or a competing deal attempt) auto-stands it. No
# background job checks this -- see module docstring.
TIMEOUT_SECONDS = 600  # 10 minutes

RANKS = "23456789TJQKA"
SUITS = "SHDC"

RANK_DISPLAY = {"T": "10"}
SUIT_SYMBOL = {"S": "♠", "H": "♥", "D": "♦", "C": "♣"}

_schema_lock = threading.Lock()
_schema_ready = False


class HandInProgress(Exception):
    """deal() was called while this user already has a different, non-stale
    open hand for this creator -- finish it with !hit/!stand first."""


class NoActiveHand(Exception):
    """hit()/stand() was called with a round_id that isn't an open hand
    for this creator/user/game."""


def _ensure_schema(connection) -> None:
    global _schema_ready
    if _schema_ready:
        return
    with _schema_lock:
        if _schema_ready:
            return
        cr._ensure_schema(connection)
        connection.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE_ACTIVE_HANDS} (
                creator_id TEXT NOT NULL,
                user_id    TEXT NOT NULL,
                game_id    TEXT NOT NULL,
                round_id   TEXT NOT NULL,
                PRIMARY KEY (creator_id, user_id, game_id)
            )
            """
        )
        _schema_ready = True


# ----------------------------------------------------------------------
# Deck / hand-value helpers -- pure functions, no DB.
# ----------------------------------------------------------------------

def _build_shuffled_deck() -> list[str]:
    deck = [rank + suit for suit in SUITS for rank in RANKS]
    for i in range(len(deck) - 1, 0, -1):
        j = casino_rng.roll(0, i)
        deck[i], deck[j] = deck[j], deck[i]
    return deck


def _draw(metadata: dict) -> str:
    cursor = metadata["deck_cursor"]
    card = metadata["deck"][cursor]
    metadata["deck_cursor"] = cursor + 1
    return card


def _card_rank_value(card: str) -> int:
    rank = card[0]
    if rank in ("T", "J", "Q", "K"):
        return 10
    if rank == "A":
        return 11
    return int(rank)


def hand_value(cards) -> int:
    total = sum(_card_rank_value(c) for c in cards)
    aces = sum(1 for c in cards if c[0] == "A")
    while total > 21 and aces > 0:
        total -= 10
        aces -= 1
    return total


def is_bust(cards) -> bool:
    return hand_value(cards) > 21


def is_natural_blackjack(cards) -> bool:
    return len(cards) == 2 and hand_value(cards) == 21


def format_card(card: str) -> str:
    rank, suit = card[0], card[1]
    return f"{RANK_DISPLAY.get(rank, rank)}{SUIT_SYMBOL[suit]}"


def format_hand(cards) -> str:
    return " ".join(format_card(c) for c in cards)


def _is_stale(metadata: dict) -> bool:
    deal_timestamp = metadata.get("deal_timestamp")
    if deal_timestamp is None:
        return False
    return (time.time() - deal_timestamp) > TIMEOUT_SECONDS


# ----------------------------------------------------------------------
# Settlement core -- shared by explicit !stand AND the lazy-timeout
# auto-stand path. Caller already holds SELECT ... FOR UPDATE on the
# round row via `connection`.
# ----------------------------------------------------------------------

def _play_dealer_and_settle(connection, round_id, creator_id, user_id, metadata, bet,
                             display_name, timeout, *, reason: str):
    from psycopg.types.json import Jsonb

    dealer_cards = metadata["dealer_cards"]
    while hand_value(dealer_cards) < 17:
        dealer_cards.append(_draw(metadata))
    metadata["dealer_cards"] = dealer_cards

    player_total = hand_value(metadata["player_cards"])
    dealer_total = hand_value(dealer_cards)

    if dealer_total > 21 or player_total > dealer_total:
        outcome, payout = "win", bet * 2
    elif player_total < dealer_total:
        outcome, payout = "loss", 0
    else:
        outcome, payout = "push", bet

    metadata["phase"] = "settled"
    metadata["settled_reason"] = reason

    if payout > 0:
        cl.credit(
            creator_id, user_id, CURRENCY_PROMO, payout, cl.PROMO_PAYOUT,
            round_id=round_id, idempotency_key=f"{round_id}-payout",
            display_name=display_name, timeout=timeout,
        )

    connection.execute(
        f"UPDATE {cr.TABLE_ROUNDS} SET outcome = %s, payout = %s, metadata = %s, "
        f"state = %s, updated_at = NOW() WHERE round_id = %s",
        (outcome, payout, Jsonb(metadata), cr.STATE_SETTLED, round_id),
    )
    connection.execute(
        f"DELETE FROM {TABLE_ACTIVE_HANDS} WHERE creator_id = %s AND user_id = %s "
        f"AND game_id = %s AND round_id = %s",
        (creator_id, user_id, GAME_ID, round_id),
    )
    return outcome, payout, metadata


# ----------------------------------------------------------------------
# Active-hand slot reservation -- the one-active-hand-per-user guarantee.
# ----------------------------------------------------------------------

def _reserve_active_hand_slot(creator_id, user_id, round_id, display_name, timeout) -> None:
    for _attempt in range(2):
        with cr._connect(timeout=timeout) as connection:
            cr._ensure_schema(connection)
            _ensure_schema(connection)

            reserved = connection.execute(
                f"""
                INSERT INTO {TABLE_ACTIVE_HANDS} (creator_id, user_id, game_id, round_id)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (creator_id, user_id, game_id) DO NOTHING
                RETURNING round_id
                """,
                (creator_id, user_id, GAME_ID, round_id),
            ).fetchone()

            if reserved is not None:
                return  # won the slot -- proceed to debit/deal

            existing_round_id = connection.execute(
                f"SELECT round_id FROM {TABLE_ACTIVE_HANDS} "
                f"WHERE creator_id = %s AND user_id = %s AND game_id = %s",
                (creator_id, user_id, GAME_ID),
            ).fetchone()[0]

            if existing_round_id == round_id:
                return  # our own round already holds the slot -- idempotent retry

            existing_row = connection.execute(
                f"SELECT {cr._ROUND_COLUMNS} FROM {cr.TABLE_ROUNDS} WHERE round_id = %s FOR UPDATE",
                (existing_round_id,),
            ).fetchone()
            existing = cr._row_to_dict(existing_row)

            if existing["state"] != cr.STATE_FUNDED:
                # Defensive: settlement always deletes its own active_hands
                # row, so this shouldn't happen -- clear stale bookkeeping
                # and retry rather than trust a dangling pointer.
                connection.execute(
                    f"DELETE FROM {TABLE_ACTIVE_HANDS} WHERE creator_id = %s AND user_id = %s AND game_id = %s",
                    (creator_id, user_id, GAME_ID),
                )
                continue

            if not _is_stale(existing["metadata"]):
                raise HandInProgress(
                    f"{user_id!r} already has an open blackjack hand ({existing_round_id!r}) "
                    f"for creator {creator_id!r}; finish it with !hit or !stand first."
                )

            # Stale -- auto-stand it now (frees the slot), then retry.
            _play_dealer_and_settle(
                connection, existing_round_id, creator_id, user_id,
                existing["metadata"], existing["wager"], display_name, timeout, reason="timeout",
            )

    raise HandInProgress(f"could not reserve a blackjack hand slot for {user_id!r}.")


def _release_active_hand_slot(creator_id, user_id, round_id, timeout) -> None:
    """Cleanup for a reserved-but-never-dealt slot -- e.g. the wager debit
    failed (InsufficientFunds) after the reservation succeeded. Without
    this, a rejected deal would leave a phantom open hand blocking the
    player from ever dealing again."""
    with cr._connect(timeout=timeout) as connection:
        cr._ensure_schema(connection)
        _ensure_schema(connection)
        connection.execute(
            f"DELETE FROM {TABLE_ACTIVE_HANDS} WHERE creator_id = %s AND user_id = %s "
            f"AND game_id = %s AND round_id = %s",
            (creator_id, user_id, GAME_ID, round_id),
        )


# ----------------------------------------------------------------------
# Public lifecycle: deal / hit / stand / get_active_round_id.
# ----------------------------------------------------------------------

def get_active_round_id(creator_id: str, user_id: str, timeout: int = cr.DEFAULT_CONNECT_TIMEOUT_SECONDS) -> str | None:
    creator_id = str(creator_id or "").strip()
    user_id = str(user_id or "").strip()
    with cr._connect(timeout=timeout) as connection:
        cr._ensure_schema(connection)
        _ensure_schema(connection)
        row = connection.execute(
            f"SELECT round_id FROM {TABLE_ACTIVE_HANDS} WHERE creator_id = %s AND user_id = %s AND game_id = %s",
            (creator_id, user_id, GAME_ID),
        ).fetchone()
    return row[0] if row else None


def deal(
    creator_id: str,
    user_id: str,
    bet: int,
    round_id: str,
    *,
    display_name: str | None = None,
    timeout: int = cr.DEFAULT_CONNECT_TIMEOUT_SECONDS,
) -> cr.RoundResult:
    creator_id = str(creator_id or "").strip()
    user_id = str(user_id or "").strip()
    if not creator_id or not user_id:
        raise ValueError("creator_id and user_id are required.")
    if not round_id:
        raise ValueError("round_id is required.")
    if not isinstance(bet, int) or isinstance(bet, bool) or bet <= 0:
        raise ValueError("bet must be a positive integer.")

    game_conf = casino_config.get_game_config(creator_id, GAME_ID, timeout=timeout)
    if not game_conf.enabled:
        raise cr.GameDisabled(f"{GAME_ID!r} is disabled for creator {creator_id!r}.")
    if bet < game_conf.min_bet or bet > game_conf.max_bet:
        raise cr.BetOutOfRange(f"bet {bet} outside [{game_conf.min_bet}, {game_conf.max_bet}] for {GAME_ID!r}.")

    _reserve_active_hand_slot(creator_id, user_id, round_id, display_name, timeout)

    try:
        cl.debit(
            creator_id, user_id, CURRENCY_PROMO, bet, cl.PROMO_WAGER,
            round_id=round_id, idempotency_key=f"{round_id}-wager",
            display_name=display_name, timeout=timeout,
        )
    except Exception:
        _release_active_hand_slot(creator_id, user_id, round_id, timeout)
        raise

    with cr._connect(timeout=timeout) as connection:
        cr._ensure_schema(connection)
        round_ = cr._claim_round(connection, round_id, creator_id, user_id, GAME_ID, bet)

    if (round_["creator_id"] != creator_id or round_["user_id"] != user_id
            or round_["game_id"] != GAME_ID or round_["wager"] != bet):
        raise cr.RoundMismatch(f"round_id {round_id!r} was already used for a different hand.")

    if round_["state"] != cr.STATE_FUNDED or round_["metadata"]:
        # Either already terminal, or already dealt (FUNDED, mid-hand) by
        # an earlier attempt at this exact round_id -- replay.
        return cr.RoundResult(
            round_id=round_id, state=round_["state"], outcome=round_["outcome"],
            wager=round_["wager"], payout=round_["payout"] or 0,
            balance_after=cl.get_balance(creator_id, user_id, CURRENCY_PROMO),
            metadata=round_["metadata"], replayed=True,
        )

    return _deal_cards_locked(round_id, creator_id, user_id, bet, display_name, timeout)


def _deal_cards_locked(round_id, creator_id, user_id, bet, display_name, timeout) -> cr.RoundResult:
    from psycopg.types.json import Jsonb

    with cr._connect(timeout=timeout) as connection:
        cr._ensure_schema(connection)
        _ensure_schema(connection)

        row = connection.execute(
            f"SELECT outcome, payout, metadata FROM {cr.TABLE_ROUNDS} WHERE round_id = %s FOR UPDATE",
            (round_id,),
        ).fetchone()
        outcome, payout, metadata = row[0], row[1], (row[2] or {})

        if metadata:
            state = cr.STATE_SETTLED if outcome is not None else cr.STATE_FUNDED
            return cr.RoundResult(
                round_id=round_id, state=state, outcome=outcome, wager=bet,
                payout=payout or 0, balance_after=cl.get_balance(creator_id, user_id, CURRENCY_PROMO),
                metadata=metadata, replayed=True,
            )

        deck = _build_shuffled_deck()
        player_cards = [deck[0], deck[2]]
        dealer_cards = [deck[1], deck[3]]
        metadata = {
            "phase": "player_turn", "deck": deck, "deck_cursor": 4,
            "player_cards": player_cards, "dealer_cards": dealer_cards,
            "bet": bet, "deal_timestamp": time.time(), "hit_log": {},
            "settled_reason": None,
        }

        player_bj = is_natural_blackjack(player_cards)
        dealer_bj = is_natural_blackjack(dealer_cards)

        if player_bj or dealer_bj:
            if player_bj and dealer_bj:
                outcome, payout = "push", bet
            elif player_bj:
                outcome, payout = "blackjack", bet + (bet * 3) // 2
            else:
                outcome, payout = "loss", 0
            metadata["phase"] = "settled"
            metadata["settled_reason"] = "natural"
            state = cr.STATE_SETTLED
            if payout > 0:
                cl.credit(
                    creator_id, user_id, CURRENCY_PROMO, payout, cl.PROMO_PAYOUT,
                    round_id=round_id, idempotency_key=f"{round_id}-payout",
                    display_name=display_name, timeout=timeout,
                )
            connection.execute(
                f"DELETE FROM {TABLE_ACTIVE_HANDS} WHERE creator_id = %s AND user_id = %s "
                f"AND game_id = %s AND round_id = %s",
                (creator_id, user_id, GAME_ID, round_id),
            )
        else:
            outcome, payout, state = None, 0, cr.STATE_FUNDED
            # active_hands row already reserved by _reserve_active_hand_slot.

        connection.execute(
            f"UPDATE {cr.TABLE_ROUNDS} SET outcome = %s, payout = %s, metadata = %s, "
            f"state = %s, updated_at = NOW() WHERE round_id = %s",
            (outcome, payout, Jsonb(metadata), state, round_id),
        )

    return cr.RoundResult(
        round_id=round_id, state=state, outcome=outcome, wager=bet,
        payout=payout or 0, balance_after=cl.get_balance(creator_id, user_id, CURRENCY_PROMO),
        metadata=metadata, replayed=False,
    )


def hit(
    creator_id: str,
    user_id: str,
    round_id: str,
    action_key: str,
    *,
    display_name: str | None = None,
    timeout: int = cr.DEFAULT_CONNECT_TIMEOUT_SECONDS,
) -> cr.RoundResult:
    creator_id = str(creator_id or "").strip()
    user_id = str(user_id or "").strip()
    round_id = str(round_id or "").strip()
    action_key = str(action_key or "").strip()
    if not creator_id or not user_id or not round_id or not action_key:
        raise ValueError("creator_id, user_id, round_id, and action_key are all required.")

    from psycopg.types.json import Jsonb

    with cr._connect(timeout=timeout) as connection:
        cr._ensure_schema(connection)
        _ensure_schema(connection)

        row = connection.execute(
            f"SELECT {cr._ROUND_COLUMNS} FROM {cr.TABLE_ROUNDS} "
            f"WHERE round_id = %s AND creator_id = %s AND user_id = %s AND game_id = %s FOR UPDATE",
            (round_id, creator_id, user_id, GAME_ID),
        ).fetchone()
        if row is None:
            raise NoActiveHand(f"no blackjack hand {round_id!r} for {user_id!r}.")
        round_ = cr._row_to_dict(row)

        if round_["state"] != cr.STATE_FUNDED:
            return cr.RoundResult(
                round_id=round_id, state=round_["state"], outcome=round_["outcome"],
                wager=round_["wager"], payout=round_["payout"] or 0,
                balance_after=cl.get_balance(creator_id, user_id, CURRENCY_PROMO),
                metadata=round_["metadata"], replayed=True,
            )

        metadata = round_["metadata"]

        if _is_stale(metadata):
            outcome, payout, metadata = _play_dealer_and_settle(
                connection, round_id, creator_id, user_id, metadata, round_["wager"],
                display_name, timeout, reason="timeout",
            )
            return cr.RoundResult(
                round_id=round_id, state=cr.STATE_SETTLED, outcome=outcome,
                wager=round_["wager"], payout=payout,
                balance_after=cl.get_balance(creator_id, user_id, CURRENCY_PROMO),
                metadata=metadata, replayed=False,
            )

        hit_log = metadata.setdefault("hit_log", {})
        if action_key in hit_log:
            return cr.RoundResult(
                round_id=round_id, state=round_["state"], outcome=None,
                wager=round_["wager"], payout=0,
                balance_after=cl.get_balance(creator_id, user_id, CURRENCY_PROMO),
                metadata=metadata, replayed=True,
            )

        card = _draw(metadata)
        metadata["player_cards"].append(card)
        hit_log[action_key] = {"card": card}
        busted = is_bust(metadata["player_cards"])

        if busted:
            metadata["phase"] = "settled"
            metadata["settled_reason"] = "bust"
            connection.execute(
                f"UPDATE {cr.TABLE_ROUNDS} SET outcome = %s, payout = %s, metadata = %s, "
                f"state = %s, updated_at = NOW() WHERE round_id = %s",
                ("loss", 0, Jsonb(metadata), cr.STATE_SETTLED, round_id),
            )
            connection.execute(
                f"DELETE FROM {TABLE_ACTIVE_HANDS} WHERE creator_id = %s AND user_id = %s "
                f"AND game_id = %s AND round_id = %s",
                (creator_id, user_id, GAME_ID, round_id),
            )
            return cr.RoundResult(
                round_id=round_id, state=cr.STATE_SETTLED, outcome="loss",
                wager=round_["wager"], payout=0,
                balance_after=cl.get_balance(creator_id, user_id, CURRENCY_PROMO),
                metadata=metadata, replayed=False,
            )

        connection.execute(
            f"UPDATE {cr.TABLE_ROUNDS} SET metadata = %s, updated_at = NOW() WHERE round_id = %s",
            (Jsonb(metadata), round_id),
        )
        return cr.RoundResult(
            round_id=round_id, state=cr.STATE_FUNDED, outcome=None,
            wager=round_["wager"], payout=0,
            balance_after=cl.get_balance(creator_id, user_id, CURRENCY_PROMO),
            metadata=metadata, replayed=False,
        )


def stand(
    creator_id: str,
    user_id: str,
    round_id: str,
    *,
    display_name: str | None = None,
    timeout: int = cr.DEFAULT_CONNECT_TIMEOUT_SECONDS,
) -> cr.RoundResult:
    creator_id = str(creator_id or "").strip()
    user_id = str(user_id or "").strip()
    round_id = str(round_id or "").strip()
    if not creator_id or not user_id or not round_id:
        raise ValueError("creator_id, user_id, and round_id are all required.")

    with cr._connect(timeout=timeout) as connection:
        cr._ensure_schema(connection)
        _ensure_schema(connection)

        row = connection.execute(
            f"SELECT {cr._ROUND_COLUMNS} FROM {cr.TABLE_ROUNDS} "
            f"WHERE round_id = %s AND creator_id = %s AND user_id = %s AND game_id = %s FOR UPDATE",
            (round_id, creator_id, user_id, GAME_ID),
        ).fetchone()
        if row is None:
            raise NoActiveHand(f"no blackjack hand {round_id!r} for {user_id!r}.")
        round_ = cr._row_to_dict(row)

        if round_["state"] != cr.STATE_FUNDED:
            return cr.RoundResult(
                round_id=round_id, state=round_["state"], outcome=round_["outcome"],
                wager=round_["wager"], payout=round_["payout"] or 0,
                balance_after=cl.get_balance(creator_id, user_id, CURRENCY_PROMO),
                metadata=round_["metadata"], replayed=True,
            )

        outcome, payout, metadata = _play_dealer_and_settle(
            connection, round_id, creator_id, user_id, round_["metadata"], round_["wager"],
            display_name, timeout, reason="stand",
        )

    return cr.RoundResult(
        round_id=round_id, state=cr.STATE_SETTLED, outcome=outcome,
        wager=round_["wager"], payout=payout,
        balance_after=cl.get_balance(creator_id, user_id, CURRENCY_PROMO),
        metadata=metadata, replayed=False,
    )
