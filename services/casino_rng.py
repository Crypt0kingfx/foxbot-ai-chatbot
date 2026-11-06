"""Server-authoritative, cryptographically secure randomness for casino
games (Casino Phase 4). Every draw comes from the `secrets` module -- OS
CSPRNG (os.urandom-backed) -- never `random`'s Mersenne Twister, never a
timestamp, never anything a client submits or influences. Games call
roll()/choice() through this module; nothing in this codebase calls
`secrets` directly for game outcomes, and no game function anywhere
accepts an outcome/result parameter from a caller -- the draw happens
here, server-side, or it doesn't happen.

Behind RNGProvider so a future provably-fair scheme (server-seed +
client-seed + nonce, HMAC-SHA256, revealed after the bet) can slot in via
set_provider() without touching any game code -- games only ever see
roll()/choice(), never which provider is behind them.
"""

from __future__ import annotations

import secrets
from abc import ABC, abstractmethod


class RNGProvider(ABC):
    @abstractmethod
    def roll(self, minimum: int, maximum: int) -> int:
        """Uniformly random int in [minimum, maximum], inclusive."""

    @abstractmethod
    def choice(self, seq):
        """Uniformly random element of seq."""

    def bits(self, k: int) -> int:
        """k uniformly random bits as an int in [0, 2**k). Not abstract, so
        existing providers (including test doubles) keep working: the
        default builds the value from k fair roll(0, 1) draws."""
        if k <= 0:
            raise ValueError("k must be positive.")
        value = 0
        for i in range(k):
            value |= self.roll(0, 1) << i
        return value


class SecureRandomProvider(RNGProvider):
    def bits(self, k: int) -> int:
        if k <= 0:
            raise ValueError("k must be positive.")
        return secrets.randbits(k)

    def roll(self, minimum: int, maximum: int) -> int:
        if maximum < minimum:
            raise ValueError("maximum must be >= minimum.")
        span = maximum - minimum + 1
        return minimum + secrets.randbelow(span)

    def choice(self, seq):
        if not seq:
            raise ValueError("seq must be non-empty.")
        return secrets.choice(seq)


_provider: RNGProvider = SecureRandomProvider()


def roll(minimum: int, maximum: int) -> int:
    return _provider.roll(minimum, maximum)


def choice(seq):
    return _provider.choice(seq)


def bits(k: int) -> int:
    return _provider.bits(k)


def set_provider(provider: RNGProvider) -> None:
    """Swap the active RNG implementation, e.g. for a future provably-fair
    provider. Global by design -- there is exactly one RNG source of truth
    per process, the same way there is exactly one DATABASE_URL."""
    global _provider
    _provider = provider


def get_provider() -> RNGProvider:
    return _provider
