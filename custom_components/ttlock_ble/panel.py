"""The TTLock sidebar panel: one page to see and change every lock's credentials."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import StaticPathConfig

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

PANEL_URL_PATH = "ttlock-ble"
PANEL_ELEMENT = "ttlock-ble-panel"
_STATIC_URL = f"/{DOMAIN}_static"
_FRONTEND_DIR = Path(__file__).parent / "frontend"


async def async_setup_panel(hass: HomeAssistant, version: str) -> None:
    """
    Serve the panel's script and add the panel to the sidebar, once.

    Skipped where the frontend is not loaded (a headless install, or a
    test run): there is no sidebar to add it to.
    """
    if "frontend" not in hass.config.components:
        return
    if PANEL_URL_PATH in hass.data.get(frontend.DATA_PANELS, {}):
        return
    await hass.http.async_register_static_paths(
        [StaticPathConfig(_STATIC_URL, str(_FRONTEND_DIR), cache_headers=False)]
    )
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_URL_PATH,
        webcomponent_name=PANEL_ELEMENT,
        sidebar_title="TTLock",
        sidebar_icon="mdi:lock-smart",
        # The version busts the browser's cache of the script on upgrade.
        module_url=f"{_STATIC_URL}/{PANEL_ELEMENT}.js?v={version}",
        require_admin=True,
    )
