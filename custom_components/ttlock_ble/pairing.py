"""
Pairing a lock in setting mode with this Home Assistant.

A lock fresh from the box, or reset, advertises that it is in setting
mode and answers anyone under a default key. Pairing makes this Home
Assistant its admin: the lock hands over its AES key and accepts an
admin password and unlock key chosen here. Those keys exist nowhere
else - not in a TTLock account, not in an app - so they are written to
storage the moment the lock accepts them, before any config entry
exists. A pairing whose flow was closed half-way, or whose entry could
not be created, is picked up again from there instead of leaving a lock
that only a hardware reset can recover.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.components.bluetooth import (
    async_ble_device_from_address,
    async_discovered_service_info,
    async_last_service_info,
)
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.singleton import singleton
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from homeassistant.util.hass_dict import HassKey

from .advertisement import decode_lock_advertisement
from .const import DOMAIN, LOGGER
from .sdk import LockVersion, PairingError, TTLockClient, TTLockError, VirtualKey

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .sdk import LockAdvertisement, PairingResult

# Every lock seen so far, paired by the app or by a local bridge, frames
# its commands with these two; the advertisement does not carry them.
PAIRING_GROUP_ID = 1
PAIRING_ORG_ID = 1
# The scene a lock in setting mode is addressed with. kind3r's SDK, which
# paired locks advertising other scenes this way, frames every V3 lock
# with the generic header (5, 3, 1, 1, 1); a padlock advertising scene 8
# left a pairing framed with its own scene unanswered.
PAIRING_SCENE = 1
# Sessions opened for one pairing before giving up.
PAIRING_ATTEMPTS = 3

STORAGE_VERSION = 1
STORAGE_KEY = f"{DOMAIN}.pairings"


@dataclass(frozen=True)
class TtlockBlePairable:
    """A lock in range that advertises it is in setting mode."""

    address: str
    name: str
    rssi: int


@dataclass(frozen=True)
class TtlockBlePairing:
    """What pairing produced: the key the entry stores, and the keypad admin code."""

    key: VirtualKey
    admin_keypad_code: str


class TtlockBlePairingStore:
    """Pairings that have no config entry yet, keyed by formatted MAC."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Bind the store to HA's storage helper."""
        self._store: Store[dict[str, dict[str, object]]] = Store(
            hass, STORAGE_VERSION, STORAGE_KEY, private=True
        )
        self._pending: dict[str, dict[str, object]] = {}

    async def async_load(self) -> None:
        """Read the pending pairings, tolerating a missing file."""
        self._pending = await self._store.async_load() or {}

    def get(self, address: str) -> TtlockBlePairing | None:
        """Return the pending pairing for `address`, if one is waiting."""
        stored = self._pending.get(format_mac(address))
        if stored is None:
            return None
        key = VirtualKey.from_dict(dict(stored["key"]))  # type: ignore[call-overload]
        return TtlockBlePairing(
            key=key, admin_keypad_code=str(stored["admin_keypad_code"])
        )

    def all(self) -> list[TtlockBlePairing]:
        """Return every pairing still waiting for its config entry."""
        pairings = (self.get(mac) for mac in self._pending)
        return [pairing for pairing in pairings if pairing is not None]

    async def async_put(self, pairing: TtlockBlePairing) -> None:
        """Keep a pairing until its entry exists, written to disk at once."""
        self._pending[format_mac(pairing.key.lockMac)] = {
            "key": pairing.key.to_dict(),
            "admin_keypad_code": pairing.admin_keypad_code,
        }
        await self._store.async_save(self._pending)

    async def async_discard(self, address: str) -> None:
        """Forget a pairing once its config entry holds the keys."""
        if self._pending.pop(format_mac(address), None) is not None:
            await self._store.async_save(self._pending)


STORE_KEY: HassKey[TtlockBlePairingStore] = HassKey(f"{DOMAIN}_pairing_store")


@singleton(STORE_KEY, async_=True)
async def async_get_pairing_store(hass: HomeAssistant) -> TtlockBlePairingStore:
    """Return the one pairing store this Home Assistant instance shares."""
    store = TtlockBlePairingStore(hass)
    await store.async_load()
    return store


def _setting_mode_advertisement(
    hass: HomeAssistant, address: str
) -> LockAdvertisement | None:
    info = async_last_service_info(hass, address, connectable=False)
    if info is None:
        return None
    advertisement = decode_lock_advertisement(address, info.manufacturer_data)
    if advertisement is None or not advertisement.is_setting_mode:
        return None
    return advertisement


def async_pairable_locks(
    hass: HomeAssistant, exclude: set[str]
) -> list[TtlockBlePairable]:
    """List the locks in range that are in setting mode, minus `exclude` (MACs)."""
    found: dict[str, TtlockBlePairable] = {}
    for info in async_discovered_service_info(hass, connectable=False):
        mac = format_mac(info.address)
        if mac in exclude or mac in found:
            continue
        advertisement = decode_lock_advertisement(info.address, info.manufacturer_data)
        if advertisement is None or not advertisement.is_setting_mode:
            continue
        found[mac] = TtlockBlePairable(
            address=info.address, name=info.name or info.address, rssi=info.rssi
        )
    return sorted(found.values(), key=lambda lock: -lock.rssi)


async def async_pair_lock(
    hass: HomeAssistant, address: str, name: str
) -> TtlockBlePairing:
    """
    Pair the lock at `address`, or return the pairing already made with it.

    A pairing kept from earlier is used once the lock has left setting
    mode, which is what proves it took. A lock still in setting mode is
    paired afresh, a few times over: a padlock at the edge of a proxy's
    range often drops the first session. A session that dropped after
    the lock was sent its admin keeps those keys, so the next attempt
    can finish with them.

    Raises `TTLockError` when the lock is not in setting mode, cannot be
    reached, or refuses the pairing.
    """
    store = await async_get_pairing_store(hass)
    pending = store.get(address)
    advertisement = _setting_mode_advertisement(hass, address)
    if pending is not None and advertisement is None:
        LOGGER.info("Using the pairing already made with %s", address)
        return pending
    if pending is not None:
        await store.async_discard(address)
    if advertisement is None:
        msg = f"{address} is not advertising that it is in pairing mode"
        raise TTLockError(msg)
    lock_version = LockVersion(
        protocolType=advertisement.protocol_type,
        protocolVersion=advertisement.protocol_version,
        scene=advertisement.scene,
        groupId=PAIRING_GROUP_ID,
        orgId=PAIRING_ORG_ID,
    )
    pairing_version = LockVersion(
        protocolType=advertisement.protocol_type,
        protocolVersion=advertisement.protocol_version,
        scene=PAIRING_SCENE,
        groupId=PAIRING_GROUP_ID,
        orgId=PAIRING_ORG_ID,
    )
    last_error: Exception | None = None
    for attempt in range(1, PAIRING_ATTEMPTS + 1):
        try:
            result = await _async_pair_once(hass, address, pairing_version)
        except PairingError as error:
            if error.partial is None:
                last_error = error
                LOGGER.info("Pairing %s, attempt %d: %s", address, attempt, error)
                continue
            pairing = _pairing_from(error.partial, address, name, lock_version)
            await store.async_put(pairing)
            msg = (
                "the lock dropped the link after accepting Home Assistant as its "
                f"admin ({error}); wake it and pair again to finish"
            )
            raise TTLockError(msg) from error
        except TTLockError as error:
            last_error = error
            LOGGER.info("Pairing %s, attempt %d: %s", address, attempt, error)
            continue
        pairing = _pairing_from(result, address, name, lock_version)
        await store.async_put(pairing)
        LOGGER.info("Paired %s (%s) with Home Assistant", name, address)
        return pairing
    msg = f"no answer after {PAIRING_ATTEMPTS} attempts: {last_error}"
    raise TTLockError(msg) from last_error


def _pairing_from(
    result: PairingResult, address: str, name: str, lock_version: LockVersion
) -> TtlockBlePairing:
    return TtlockBlePairing(
        key=result.to_virtual_key(
            lock_mac=address.upper(), name=name, lock_version=lock_version
        ),
        admin_keypad_code=result.admin_keypad_code,
    )


async def _async_pair_once(
    hass: HomeAssistant, address: str, pairing_version: LockVersion
) -> PairingResult:
    """Open one session with the lock and run the pairing over it."""
    device = async_ble_device_from_address(hass, address, connectable=True)
    if device is None:
        msg = f"{address} is not reachable through any Bluetooth adapter or proxy"
        raise TTLockError(msg)
    client = TTLockClient.for_pairing(device, pairing_version)
    try:
        await client.connect()
        return await client.initialize(dt_util.now().replace(tzinfo=None))
    except TTLockError:
        raise
    except Exception as error:
        msg = f"pairing {address} failed: {error}"
        raise TTLockError(msg) from error
    finally:
        try:
            await client.disconnect()
        except Exception:  # noqa: BLE001 -- a failed disconnect must not hide the result
            LOGGER.debug("Disconnect after pairing %s failed", address, exc_info=True)
