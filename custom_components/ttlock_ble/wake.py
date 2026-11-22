"""
When a lock was last heard, and waiting for it to wake.

A lock can only be connected to while it advertises. Door locks keep
advertising while idle, but a padlock goes silent between touches: no
proxy hears it, and a connect attempt just runs into the BLE timeouts.
Everything that needs a session asks here first, so a sleeping padlock
is reported as asleep at once, and the panel can wait for the touch
that wakes it before it starts a session.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.components.bluetooth import (
    MONOTONIC_TIME,
    BluetoothCallbackMatcher,
    BluetoothScanningMode,
    async_last_service_info,
    async_register_callback,
    async_scanner_by_source,
)
from homeassistant.core import callback

from .sdk import TTLockError

if TYPE_CHECKING:
    from homeassistant.components.bluetooth import (
        BluetoothChange,
        BluetoothServiceInfoBleak,
    )
    from homeassistant.core import HomeAssistant

# A lock unheard for this long is taken to be asleep. Awake locks
# advertise several times a second, so this is generous; it only has to
# be shorter than a connect attempt that is bound to fail.
ASLEEP_AFTER_SECONDS = 20.0

# An advertisement at most this old counts as the lock being awake now,
# so a wait for it returns at once.
AWAKE_NOW_SECONDS = 3.0


class TtlockBleLockAsleepError(TTLockError):
    """The lock has not advertised lately, so no session can be opened."""


@dataclass(frozen=True)
class TtlockBleHeard:
    """The last advertisement HA's bluetooth stack has from one lock."""

    seconds_ago: float
    rssi: int
    source: str
    source_name: str

    @property
    def asleep(self) -> bool:
        """Report whether the lock has been quiet for too long to connect."""
        return self.seconds_ago > ASLEEP_AFTER_SECONDS


@callback
def async_last_heard(hass: HomeAssistant, mac: str) -> TtlockBleHeard | None:
    """Return the last advertisement from `mac`, or `None` if never heard."""
    if "bluetooth" not in hass.config.components:
        return None
    info = async_last_service_info(hass, mac, connectable=False)
    if info is None:
        return None
    scanner = async_scanner_by_source(hass, info.source)
    return TtlockBleHeard(
        seconds_ago=max(0.0, MONOTONIC_TIME() - info.time),
        rssi=info.rssi,
        source=info.source,
        source_name=(scanner.name if scanner else None) or info.source,
    )


async def async_wait_until_heard(
    hass: HomeAssistant, mac: str, wait_seconds: float
) -> bool:
    """
    Wait for `mac` to advertise, up to `wait_seconds` seconds.

    Returns at once when the lock was heard just now. Reports whether it
    was heard before the wait ran out.
    """
    heard = async_last_heard(hass, mac)
    if heard is not None and heard.seconds_ago <= AWAKE_NOW_SECONDS:
        return True
    woke: asyncio.Future[None] = hass.loop.create_future()

    @callback
    def _on_advertisement(
        service_info: BluetoothServiceInfoBleak, _change: BluetoothChange
    ) -> None:
        # Registering replays the last advertisement already held, which
        # may be hours old; only a fresh one means the lock woke.
        fresh = MONOTONIC_TIME() - service_info.time <= AWAKE_NOW_SECONDS
        if fresh and not woke.done():
            woke.set_result(None)

    unsubscribe = async_register_callback(
        hass,
        _on_advertisement,
        BluetoothCallbackMatcher(address=mac, connectable=False),
        BluetoothScanningMode.ACTIVE,
    )
    try:
        async with asyncio.timeout(wait_seconds):
            await woke
    except TimeoutError:
        return False
    finally:
        unsubscribe()
    return True
