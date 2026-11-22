"""The TTLock sidebar panel: one page to see and change every lock's credentials."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import callback
from homeassistant.setup import async_when_setup

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

PANEL_URL_PATH = "btr-ttlock-ble"
PANEL_ELEMENT = "btr-ttlock-ble-panel"
_STATIC_URL = f"/{DOMAIN}_static"
_FRONTEND_DIR = Path(__file__).parent / "frontend"


@callback
def async_setup_panel(hass: HomeAssistant, version: str) -> None:
    """
    Add the panel to the sidebar once the frontend is up.

    This integration can finish setting up before the frontend does, and
    a headless install or a test run never loads it at all; waiting for
    it covers both.
    """

    async def _register(hass: HomeAssistant, _component: str) -> None:
        await _async_register_panel(hass, version)

    async_when_setup(hass, "frontend", _register)


async def _async_register_panel(hass: HomeAssistant, version: str) -> None:
    """Serve the panel's script and add the panel to the sidebar, once."""
    if PANEL_URL_PATH in hass.data.get(frontend.DATA_PANELS, {}):
        return
    await hass.http.async_register_static_paths(
        [StaticPathConfig(_STATIC_URL, str(_FRONTEND_DIR), cache_headers=False)]
    )
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_URL_PATH,
        webcomponent_name=PANEL_ELEMENT,
        sidebar_title="BTR TTLock",
        sidebar_icon="mdi:lock-smart",
        # The version busts the browser's cache of the script on upgrade.
        module_url=f"{_STATIC_URL}/{PANEL_ELEMENT}.js?v={version}",
        require_admin=True,
    )
