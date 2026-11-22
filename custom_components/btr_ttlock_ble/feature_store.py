"""Persisted feature values read from locks whose key carried none."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers.singleton import singleton
from homeassistant.helpers.storage import Store
from homeassistant.util.hass_dict import HassKey

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .sdk import VirtualKey

STORAGE_VERSION = 1
STORAGE_KEY = f"{DOMAIN}.features"


class TtlockBleFeatureStore:
    """
    The feature value each lock reported about itself, as the cloud's hex string.

    A key from a TTLock account carries the lock's features; a key
    entered by hand or kept by a local bridge often does not, and then
    every kind of credential is assumed possible. Asking the lock once
    settles it, and keeping the answer means it is not asked again.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        """Bind the store to HA's storage helper."""
        self._store: Store[dict[str, str]] = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._features: dict[str, str] = {}

    async def async_load(self) -> None:
        """Read the persisted values, tolerating a missing file."""
        self._features = dict(await self._store.async_load() or {})

    def apply(self, key: VirtualKey) -> None:
        """Give `key` the feature value its lock reported, if it has none."""
        if key.feature_mask == 0 and (value := self._features.get(key.lockMac)):
            key.featureValue = value

    async def async_remember(self, mac: str, feature_value: str) -> None:
        """Persist the feature value `mac` reported."""
        self._features[mac] = feature_value
        await self._store.async_save(self._features)

    async def async_forget(self, mac: str) -> None:
        """Drop the feature value kept for `mac`."""
        if self._features.pop(mac, None) is not None:
            await self._store.async_save(self._features)


STORE_KEY: HassKey[TtlockBleFeatureStore] = HassKey(f"{DOMAIN}_feature_store")


@singleton(STORE_KEY, async_=True)
async def async_get_feature_store(hass: HomeAssistant) -> TtlockBleFeatureStore:
    """Return the one store this Home Assistant instance shares."""
    store = TtlockBleFeatureStore(hass)
    await store.async_load()
    return store
