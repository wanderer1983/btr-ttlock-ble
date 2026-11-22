"""Pairing a lock in setting mode from the config flow, and removing a paired lock."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ttlock_ble.const import DOMAIN
from custom_components.ttlock_ble.pairing import (
    TtlockBlePairing,
    async_get_pairing_store,
    async_pair_lock,
    async_pairable_locks,
)
from custom_components.ttlock_ble.sdk import (
    LockVersion,
    PairingResult,
    TTLockError,
)

MAC = "AA:BB:CC:DD:EE:FF"
TAIL = bytes.fromhex("ffeeddccbbaa")
NAME = "P3_ddeeff"
V3_COMPANY_ID = 0x0305
SETTING_MODE = 0x04
AES_KEY = bytes(range(16))
VERSION = LockVersion(protocolType=5, protocolVersion=3, scene=2, groupId=1, orgId=1)


def _info(
    *, flags: int = SETTING_MODE, address: str = MAC, name: str = NAME
) -> MagicMock:
    info = MagicMock(name="BluetoothServiceInfoBleak")
    info.address = address
    info.name = name
    info.rssi = -60
    info.manufacturer_data = {V3_COMPANY_ID: bytes([2, flags, 70, 0, 0, 0, 0]) + TAIL}
    return info


def _result(code: str = "1234567") -> PairingResult:
    return PairingResult(
        aes_key=AES_KEY,
        admin_ps="11223344",
        unlock_key="55667788",
        admin_keypad_code=code,
        features=None,
    )


def _pairing(code: str = "1234567") -> TtlockBlePairing:
    return TtlockBlePairing(
        key=_result(code).to_virtual_key(
            lock_mac=MAC, name="Garage", lock_version=VERSION
        ),
        admin_keypad_code=code,
    )


@pytest.fixture
def lock_in_range():
    """Make HA's bluetooth stack report one lock in setting mode."""
    info = _info()
    with (
        patch(
            "custom_components.ttlock_ble.pairing.async_last_service_info",
            return_value=info,
        ),
        patch(
            "custom_components.ttlock_ble.pairing.async_discovered_service_info",
            return_value=[
                info,
                _info(flags=0, address="11:22:33:44:55:66", name="paired"),
            ],
        ),
        patch(
            "custom_components.ttlock_ble.pairing.async_ble_device_from_address",
            return_value=MagicMock(address=MAC),
        ),
    ):
        yield info


@pytest.fixture
def pairing_client():
    client = MagicMock(name="TTLockClient")
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.initialize = AsyncMock(return_value=_result())
    with patch(
        "custom_components.ttlock_ble.pairing.TTLockClient.for_pairing",
        return_value=client,
    ) as for_pairing:
        client.for_pairing = for_pairing
        yield client


async def _finish(hass, result):
    """Drive the flow through the pairing progress step, however fast it was."""
    while result["type"] == FlowResultType.SHOW_PROGRESS:
        await hass.async_block_till_done()
        result = await hass.config_entries.flow.async_configure(result["flow_id"])
    return result


# --- discovery helpers --------------------------------------------------


async def test_pairable_lists_setting_mode_locks_only(hass, lock_in_range) -> None:
    locks = async_pairable_locks(hass, set())
    assert [lock.address for lock in locks] == [MAC]
    assert async_pairable_locks(hass, {dr.format_mac(MAC)}) == []


async def test_pair_lock_stores_the_keys_before_returning(
    hass, enable_bluetooth, lock_in_range, pairing_client
) -> None:
    pairing = await async_pair_lock(hass, MAC, "Garage")
    assert pairing.key.lockMac == MAC
    assert pairing.key.adminPs == "11223344"
    assert pairing.key.lockVersion == VERSION
    store = await async_get_pairing_store(hass)
    assert store.get(MAC) == pairing
    pairing_client.disconnect.assert_awaited_once()
    # A second attempt reuses the keys instead of pairing again.
    again = await async_pair_lock(hass, MAC, "Garage")
    assert again == pairing
    pairing_client.initialize.assert_awaited_once()


async def test_pair_lock_refuses_a_lock_not_in_setting_mode(
    hass, enable_bluetooth
) -> None:
    with (
        patch(
            "custom_components.ttlock_ble.pairing.async_last_service_info",
            return_value=_info(flags=0),
        ),
        pytest.raises(TTLockError, match="not advertising"),
    ):
        await async_pair_lock(hass, MAC, "Garage")


async def test_pair_lock_wraps_unexpected_errors(
    hass, enable_bluetooth, lock_in_range, pairing_client
) -> None:
    pairing_client.initialize.side_effect = RuntimeError("link dropped")
    with pytest.raises(TTLockError, match="link dropped"):
        await async_pair_lock(hass, MAC, "Garage")
    store = await async_get_pairing_store(hass)
    assert store.get(MAC) is None


# --- config flow ---------------------------------------------------------


async def test_user_menu_offers_pairing(hass, enable_custom_integrations) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["menu_options"] == ["pair", "cloud", "manual"]


async def test_discovery_of_a_lock_in_setting_mode_offers_pairing(
    hass, enable_custom_integrations, lock_in_range
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_BLUETOOTH}, data=_info()
    )
    assert result["menu_options"] == ["pair", "cloud", "manual"]


async def test_pair_from_discovery_creates_the_entry(
    hass, enable_custom_integrations, lock_in_range, pairing_client
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_BLUETOOTH}, data=_info()
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "pair"}
    )
    assert result["step_id"] == "pair"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Garage"}
    )
    result = await _finish(hass, result)
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Garage"
    assert result["data"]["paired_locally"] is True
    assert result["data"]["admin_keypad_code"] == "1234567"
    (key,) = result["data"]["keys"]
    assert key["lockMac"] == MAC
    assert key["aesKeyStr"] == AES_KEY.hex()
    assert key["userType"] == "110301"
    store = await async_get_pairing_store(hass)
    assert store.get(MAC) is None


async def test_pair_from_the_menu_lists_locks_in_range(
    hass, enable_custom_integrations, lock_in_range, pairing_client
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "pair"}
    )
    assert result["step_id"] == "pair"
    assert result["errors"] == {}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"lock": MAC, "name": ""}
    )
    result = await _finish(hass, result)
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == NAME


async def test_pair_with_no_lock_in_range_says_so(
    hass, enable_custom_integrations
) -> None:
    with patch(
        "custom_components.ttlock_ble.pairing.async_discovered_service_info",
        return_value=[],
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "pair"}
        )
        assert result["errors"] == {"base": "no_lock_in_pairing_mode"}
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["errors"] == {"base": "no_lock_in_pairing_mode"}


async def test_failed_pairing_aborts_with_the_reason(
    hass, enable_custom_integrations, lock_in_range, pairing_client
) -> None:
    pairing_client.initialize.side_effect = TTLockError("lock refused the new admin")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_BLUETOOTH}, data=_info()
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "pair"}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    result = await _finish(hass, result)
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "pairing_failed"
    assert "refused the new admin" in result["description_placeholders"]["error"]


async def test_a_pairing_left_unfinished_is_offered_again(
    hass, enable_custom_integrations, pairing_client
) -> None:
    store = await async_get_pairing_store(hass)
    await store.async_put(_pairing())
    with patch(
        "custom_components.ttlock_ble.pairing.async_discovered_service_info",
        return_value=[],
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "pair"}
        )
        assert result["errors"] == {}
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"lock": MAC}
        )
        result = await _finish(hass, result)
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Garage"
    pairing_client.initialize.assert_not_awaited()


async def test_pair_aborts_for_a_lock_already_set_up(
    hass, enable_custom_integrations, lock_in_range, sample_stored_key
) -> None:
    MockConfigEntry(
        domain=DOMAIN,
        data={"keys": [{**sample_stored_key, "lockMac": MAC}]},
        unique_id="other",
    ).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "pair"}
    )
    assert result["errors"] == {"base": "no_lock_in_pairing_mode"}


# --- removal -------------------------------------------------------------


@pytest.fixture
async def local_entry(
    hass,
    enable_bluetooth,
    enable_custom_integrations,
    sample_stored_key,
    mock_ttlock_connection,
):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "keys": [sample_stored_key],
            "paired_locally": True,
            "admin_keypad_code": "1234567",
        },
        unique_id=dr.format_mac(sample_stored_key["lockMac"]),
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _device_id(hass, entry, mac) -> str:
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, dr.format_mac(mac)), entry.entry_id
    )
    assert device is not None
    return device.id


async def _ws(hass_ws_client, hass, **msg):
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, **msg})
    return await ws.receive_json()


async def test_locks_report_a_local_pairing(
    hass, hass_ws_client, local_entry, sample_stored_key
) -> None:
    reply = await _ws(hass_ws_client, hass, type=f"{DOMAIN}/locks")
    (lock,) = reply["result"]["locks"]
    assert lock["removable"] is True
    assert lock["paired_locally"] is True
    assert lock["admin_keypad_code"] == "1234567"


async def test_remove_with_reset_resets_then_removes(
    hass, hass_ws_client, local_entry, sample_stored_key, mock_ttlock_connection
) -> None:
    client = MagicMock(factory_reset=AsyncMock())

    async def _manage(_action, operation):
        return await operation(client)

    mock_ttlock_connection.async_manage = AsyncMock(side_effect=_manage)
    device_id = _device_id(hass, local_entry, sample_stored_key["lockMac"])
    reply = await _ws(
        hass_ws_client,
        hass,
        type=f"{DOMAIN}/remove_lock",
        device_id=device_id,
        reset=True,
    )
    assert reply["success"], reply
    client.factory_reset.assert_awaited_once()
    assert hass.config_entries.async_get_entry(local_entry.entry_id) is None


async def test_failed_reset_keeps_the_entry(
    hass, hass_ws_client, local_entry, sample_stored_key, mock_ttlock_connection
) -> None:
    mock_ttlock_connection.async_manage = AsyncMock(side_effect=TTLockError("asleep"))
    device_id = _device_id(hass, local_entry, sample_stored_key["lockMac"])
    reply = await _ws(
        hass_ws_client,
        hass,
        type=f"{DOMAIN}/remove_lock",
        device_id=device_id,
        reset=True,
    )
    assert reply["error"]["code"] == "reset_failed"
    assert hass.config_entries.async_get_entry(local_entry.entry_id) is not None


async def test_remove_without_reset_only_removes(
    hass, hass_ws_client, local_entry, sample_stored_key, mock_ttlock_connection
) -> None:
    mock_ttlock_connection.async_manage = AsyncMock()
    device_id = _device_id(hass, local_entry, sample_stored_key["lockMac"])
    reply = await _ws(
        hass_ws_client,
        hass,
        type=f"{DOMAIN}/remove_lock",
        device_id=device_id,
        reset=False,
    )
    assert reply["success"], reply
    mock_ttlock_connection.async_manage.assert_not_awaited()
    assert hass.config_entries.async_get_entry(local_entry.entry_id) is None


async def test_an_account_lock_cannot_be_removed_alone(
    hass, hass_ws_client, setup_integration, sample_stored_key
) -> None:
    device_id = _device_id(hass, setup_integration, sample_stored_key["lockMac"])
    reply = await _ws(
        hass_ws_client,
        hass,
        type=f"{DOMAIN}/remove_lock",
        device_id=device_id,
        reset=True,
    )
    assert reply["error"]["code"] == "account_lock"
