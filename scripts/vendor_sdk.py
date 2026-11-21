"""
Copy the ttlock-ble SDK into the integration as `custom_components/ttlock_ble/sdk`.

This fork ships the SDK inside the integration instead of pinning it from
PyPI: the credential-management commands it relies on exist only in the
`wanderer/ttlock-ble` fork, which Home Assistant cannot install by itself.

Usage: python scripts/vendor_sdk.py <path to a ttlock-ble checkout>

The CLI module is left out - it needs typer and python-dotenv, which Home
Assistant does not ship, and nothing in the integration uses it.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

_EXCLUDED = {"cli.py"}


def main() -> None:
    """Replace the vendored tree with the given checkout's `src/ttlock_ble`."""
    source_root = Path(sys.argv[1]).resolve()
    source = source_root / "src" / "ttlock_ble"
    target = (
        Path(__file__).resolve().parent.parent
        / "custom_components"
        / "ttlock_ble"
        / "sdk"
    )
    if not (source / "__init__.py").is_file():
        sys.exit(f"{source} is not a ttlock_ble package")
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(
        source,
        target,
        ignore=lambda _dir, names: [
            n for n in names if n in _EXCLUDED or n == "__pycache__"
        ],
    )
    revision = subprocess.run(
        ["git", "-C", str(source_root), "rev-parse", "HEAD"],  # noqa: S607 -- developer tool
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    (target / "VENDORED_FROM").write_text(f"wanderer/ttlock-ble {revision}\n")
    print(f"Vendored {source} at {revision} into {target}")


if __name__ == "__main__":
    main()
