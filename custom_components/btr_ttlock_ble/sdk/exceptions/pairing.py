"""PairingError: a pairing that did not complete, and what it got as far as."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .ttlock import TTLockError

if TYPE_CHECKING:
    from ..models import PairingResult


class PairingError(TTLockError):
    """Raised by `TTLockClient.initialize` when pairing stops part-way.

    `partial` is `None` when nothing was sent that changes the lock: it
    is still unpaired, and pairing can simply run again. Otherwise the
    admin password and unlock key were sent, so the lock may already
    answer to them, and `partial` holds the keys it would answer to
    (without features or a keypad admin code).
    """

    def __init__(self, message: str, *, partial: PairingResult | None) -> None:
        """Record the message and the keys the lock may already hold."""
        super().__init__(message)
        self.partial = partial
