"""The sidebar panel, its websocket commands, and the asleep-lock check."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components import frontend
from homeassistant.const import EVENT_COMPONENT_LOADED
from homeassistant.helpers import device_registry as dr

from custom_components.ttlock_ble.connection import TtlockBleConnection
from custom_components.ttlock_ble.const import DOMAIN
from custom_components.ttlock_ble.credential_store import async_get_credential_store
from custom_components.ttlock_ble.credential_type import TtlockBleCredentialType
from custom_components.ttlock_ble.data import TtlockBleKnownCredential
from custom_components.ttlock_ble.panel import PANEL_URL_PATH, async_setup_panel
from custom_components.ttlock_ble.sdk import KeyboardPwdType, PasscodeEntry
from custom_components.ttlock_ble.wake import (
    TtlockBleHeard,
    TtlockBleLockAsleepError,
    async_last_heard,
    async_wait_until_heard,
)


@pytest.fixture
def client(mock_ttlock_connection) -> MagicMock:
    fake = MagicMock(name="TTLockClient")
    fake.delete_passcode = AsyncMock(return_value=None)
    fake.get_passcodes = AsyncMock(return_value=[])

    async def _manage(_action, operation):
        return await operation(fake)

    mock_ttlock_connection.async_manage = AsyncMock(side_effect=_manage)
    mock_ttlock_connection.is_connected = False
    return fake


@pytest.fixture
def device_id(hass, setup_integration, sample_virtual_key) -> str:
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, dr.format_mac(sample_virtual_key.lockMac)), setup_integration.entry_id
    )
    assert device is not None
    return device.id


async def _ws(hass_ws_client, hass, **msg):
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, **msg})
    return await ws.receive_json()


async def test_panel_waits_for_the_frontend(hass) -> None:
    with patch("custom_components.ttlock_ble.panel.panel_custom") as panel_custom:
        async_setup_panel(hass, "1")
        await hass.async_block_till_done()
    panel_custom.async_register_panel.assert_not_called()


async def test_panel_is_registered_for_admins_with_a_versioned_script(hass) -> None:
    hass.http = MagicMock(async_register_static_paths=AsyncMock())
    with patch(
        "custom_components.ttlock_ble.panel.panel_custom.async_register_panel",
        AsyncMock(),
    ) as register:
        async_setup_panel(hass, "9.9")
        hass.config.components.add("frontend")
        hass.bus.async_fire(EVENT_COMPONENT_LOADED, {"component": "frontend"})
        await hass.async_block_till_done()
        # A second call (another entry's setup, a reload) adds nothing.
        hass.data.setdefault(frontend.DATA_PANELS, {})[PANEL_URL_PATH] = object()
        async_setup_panel(hass, "9.9")
        await hass.async_block_till_done()
    register.assert_awaited_once()
    kwargs = register.await_args.kwargs
    assert kwargs["frontend_url_path"] == PANEL_URL_PATH
    assert kwargs["require_admin"] is True
    assert kwargs["module_url"].endswith("ttlock-ble-panel.js?v=9.9")
    (static,) = hass.http.async_register_static_paths.await_args.args[0]
    assert (Path(static.path) / "ttlock-ble-panel.js").is_file()


async def test_locks_lists_credentials_and_names(
    hass, hass_ws_client, setup_integration, sample_virtual_key, device_id, client
) -> None:
    mac = sample_virtual_key.lockMac
    store = await async_get_credential_store(hass)
    passcode = TtlockBleKnownCredential.from_passcode(mac, "123456")
    store.async_replace(mac, TtlockBleCredentialType.PASSCODE, [passcode])
    store.async_set_name(mac, TtlockBleCredentialType.PASSCODE, passcode.key, "Ana")

    reply = await _ws(hass_ws_client, hass, type=f"{DOMAIN}/locks")

    assert reply["success"], reply
    (lock,) = reply["result"]["locks"]
    assert lock["device_id"] == device_id
    assert lock["mac"] == mac
    assert lock["locked"] is True
    assert lock["battery"] == 80
    assert lock["credentials"]["passcode"] == [
        {"key": passcode.key, "label": "1•••56", "name": "Ana"}
    ]
    # The code itself never leaves the integration.
    assert "123456" not in str(reply)


async def test_rename_credential(
    hass, hass_ws_client, setup_integration, sample_virtual_key, device_id, client
) -> None:
    mac = sample_virtual_key.lockMac
    store = await async_get_credential_store(hass)
    card = TtlockBleKnownCredential(TtlockBleCredentialType.CARD, "42", "42")
    store.async_replace(mac, TtlockBleCredentialType.CARD, [card])

    reply = await _ws(
        hass_ws_client,
        hass,
        type=f"{DOMAIN}/rename_credential",
        device_id=device_id,
        credential_type="card",
        key="42",
        name="Blue tag",
    )

    assert reply["success"], reply
    assert store.name_of(mac, TtlockBleCredentialType.CARD, "42") == "Blue tag"


async def test_rename_unknown_credential_is_an_error(
    hass, hass_ws_client, setup_integration, device_id, client
) -> None:
    reply = await _ws(
        hass_ws_client,
        hass,
        type=f"{DOMAIN}/rename_credential",
        device_id=device_id,
        credential_type="card",
        key="999",
        name="x",
    )
    assert reply["error"]["code"] == "not_found"


async def test_unknown_device_is_an_error(
    hass, hass_ws_client, setup_integration
) -> None:
    reply = await _ws(
        hass_ws_client, hass, type=f"{DOMAIN}/wait_awake", device_id="nope", timeout=1
    )
    assert reply["error"]["code"] == "invalid_lock"


async def test_wait_awake_reports_the_wait(
    hass, hass_ws_client, setup_integration, device_id, client
) -> None:
    with patch(
        "custom_components.ttlock_ble.websocket.async_wait_until_heard",
        AsyncMock(return_value=True),
    ) as wait:
        reply = await _ws(
            hass_ws_client,
            hass,
            type=f"{DOMAIN}/wait_awake",
            device_id=device_id,
            timeout=5,
        )
    assert reply["result"] == {"awake": True}
    assert wait.await_args.args[2] == 5


async def test_delete_passcode_by_key(
    hass, hass_ws_client, setup_integration, sample_virtual_key, device_id, client
) -> None:
    mac = sample_virtual_key.lockMac
    store = await async_get_credential_store(hass)
    passcode = TtlockBleKnownCredential.from_passcode(mac, "246810")
    store.async_replace(mac, TtlockBleCredentialType.PASSCODE, [passcode])
    client.get_passcodes.return_value = [
        PasscodeEntry(
            passcode="111111",
            pwd_type=KeyboardPwdType.PERMANENT,
            start_date=None,
            end_date=None,
            cyclic_schedule=None,
        ),
        PasscodeEntry(
            passcode="246810",
            pwd_type=KeyboardPwdType.PERIOD,
            start_date=None,
            end_date=None,
            cyclic_schedule=None,
        ),
    ]

    reply = await _ws(
        hass_ws_client,
        hass,
        type=f"{DOMAIN}/delete_passcode",
        device_id=device_id,
        key=passcode.key,
    )

    assert reply["success"], reply
    client.delete_passcode.assert_awaited_once_with(
        "246810", pwd_type=KeyboardPwdType.PERIOD
    )
    assert store.known(mac, TtlockBleCredentialType.PASSCODE) == []


async def test_delete_passcode_not_listed(
    hass, hass_ws_client, setup_integration, device_id, client
) -> None:
    reply = await _ws(
        hass_ws_client,
        hass,
        type=f"{DOMAIN}/delete_passcode",
        device_id=device_id,
        key="0" * 32,
    )
    assert reply["error"]["code"] == "delete_failed"
    client.delete_passcode.assert_not_awaited()


async def test_refresh_refuses_a_sleeping_lock(
    hass, hass_ws_client, setup_integration, device_id, client
) -> None:
    heard = TtlockBleHeard(seconds_ago=600, rssi=-70, source="aa", source_name="proxy1")
    with patch(
        "custom_components.ttlock_ble.websocket.async_last_heard", return_value=heard
    ):
        reply = await _ws(
            hass_ws_client,
            hass,
            type=f"{DOMAIN}/refresh_credentials",
            device_id=device_id,
        )
    assert reply["error"]["code"] == "lock_asleep"


async def test_refresh_reads_the_lists(
    hass, hass_ws_client, setup_integration, device_id, client
) -> None:
    coordinator = setup_integration.runtime_data.coordinator
    with patch.object(
        coordinator, "async_refresh_credentials", AsyncMock(return_value=True)
    ) as refresh:
        reply = await _ws(
            hass_ws_client,
            hass,
            type=f"{DOMAIN}/refresh_credentials",
            device_id=device_id,
        )
    assert reply["success"], reply
    refresh.assert_awaited_once()


def test_heard_is_asleep_after_the_threshold() -> None:
    assert TtlockBleHeard(25, -70, "a", "p").asleep
    assert not TtlockBleHeard(2, -70, "a", "p").asleep


async def test_last_heard_none_without_bluetooth(hass) -> None:
    assert async_last_heard(hass, "AA:BB:CC:DD:EE:FF") is None


async def test_wait_until_heard_times_out(hass, enable_bluetooth) -> None:
    assert not await async_wait_until_heard(hass, "AA:BB:CC:DD:EE:FF", 0.05)


async def test_wait_until_heard_returns_at_once_when_just_heard(
    hass, enable_bluetooth
) -> None:
    heard = TtlockBleHeard(1, -70, "a", "p")
    with patch(
        "custom_components.ttlock_ble.wake.async_last_heard", return_value=heard
    ):
        assert await async_wait_until_heard(hass, "AA:BB:CC:DD:EE:FF", 0.05)


async def test_manage_refuses_a_sleeping_lock_without_connecting(
    hass, sample_virtual_key, mock_ble_resolver, mock_ttlock_client
) -> None:
    heard = TtlockBleHeard(seconds_ago=90, rssi=-70, source="aa", source_name="proxy1")
    conn = TtlockBleConnection(hass, sample_virtual_key)
    with (
        patch(
            "custom_components.ttlock_ble.connection.async_last_heard",
            return_value=heard,
        ),
        pytest.raises(TtlockBleLockAsleepError, match="asleep"),
    ):
        await conn.async_unlock()
    mock_ttlock_client.connect.assert_not_awaited()


async def test_manage_connects_to_a_lock_heard_just_now(
    hass, sample_virtual_key, mock_ble_resolver, mock_ttlock_client
) -> None:
    heard = TtlockBleHeard(seconds_ago=1, rssi=-70, source="aa", source_name="proxy1")
    conn = TtlockBleConnection(hass, sample_virtual_key)
    with patch(
        "custom_components.ttlock_ble.connection.async_last_heard", return_value=heard
    ):
        await conn.async_unlock()
    mock_ttlock_client.unlock.assert_awaited_once()
