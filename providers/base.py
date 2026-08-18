"""Settlement provider contract -- the currency-specific seam between the
currency-agnostic casino ledger (services/casino_ledger.py) and whatever
external balance a currency's real value actually lives in (the FoxBot
FoxCoin economy for PROMO, a real-money processor for BLAZE later).

The ledger and games never talk to FoxCoin/Blaze directly; they only ever
see PROMO/BLAZE balances inside casino_ledger.py. Only a provider knows how
to move value between the ledger and its backing currency, via deposit()
(external -> ledger) and withdraw() (ledger -> external). A provider that
is one-way by design (see providers/promo.py) must raise from withdraw()
rather than merely leaving it unimplemented, so "no path back" is an
enforced contract, not an accident of what's been built so far.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class SettlementProvider(ABC):
    # The casino_ledger currency this provider settles, e.g. "PROMO".
    currency: str

    @abstractmethod
    def deposit(self, creator_id: str, user_id: str, amount: int, *, idempotency_key: str, **kwargs) -> dict:
        """Move `amount` of external currency into the casino ledger's
        balance for (creator_id, user_id). Must be safe to call twice with
        the same idempotency_key -- a replay returns the original result
        rather than moving value again."""

    @abstractmethod
    def withdraw(self, creator_id: str, user_id: str, amount: int, *, idempotency_key: str, **kwargs) -> dict:
        """Move `amount` out of the casino ledger's balance back into the
        external currency. Providers that don't support this must raise a
        specific exception explaining why, not silently no-op."""
