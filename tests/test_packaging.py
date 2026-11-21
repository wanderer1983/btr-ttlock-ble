"""The SDK ships vendored inside the integration, never as a pip requirement."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_INTEGRATION = _ROOT / "custom_components" / "ttlock_ble"
_SDK = "ttlock-ble"


def _names(requirements: list[str]) -> set[str]:
    return {r.partition("==")[0].partition("@")[0].strip() for r in requirements}


def test_sdk_is_vendored_not_pinned() -> None:
    """Home Assistant must not install upstream's SDK next to the vendored fork.

    Both would import as different modules, and the upstream release lacks
    the credential-management commands this fork calls.
    """
    manifest = json.loads((_INTEGRATION / "manifest.json").read_text())
    pyproject = tomllib.loads((_ROOT / "pyproject.toml").read_text())

    assert _SDK not in _names(manifest["requirements"])
    assert _SDK not in _names(pyproject["dependency-groups"]["dev"])
    assert (_INTEGRATION / "sdk" / "__init__.py").is_file()
    assert (
        (_INTEGRATION / "sdk" / "VENDORED_FROM")
        .read_text()
        .startswith("wanderer/ttlock-ble ")
    )


def test_vendored_sdk_leaves_out_the_cli() -> None:
    """The CLI needs typer and python-dotenv, which Home Assistant does not ship."""
    assert not (_INTEGRATION / "sdk" / "cli.py").exists()
