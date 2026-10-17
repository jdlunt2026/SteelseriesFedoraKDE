"""Start SteelPanel (in the tray) at login, via an XDG autostart entry.

KDE Plasma, GNOME and other desktops run the ``.desktop`` files in
``~/.config/autostart`` when you log in.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

FILE_NAME = "steelpanel-autostart.desktop"
#: Entries other tools may have made for SteelPanel, e.g. KDE's
#: System Settings > Autostart copies the app launcher's file.
OTHER_NAMES = ("steelpanel.desktop",)


def autostart_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "autostart"


def _command() -> str:
    """Absolute path of the steelpanel command, so the login session's PATH
    doesn't matter."""
    beside_python = Path(sys.executable).with_name("steelpanel")
    if beside_python.exists():  # installed in a venv by install.sh
        return str(beside_python)
    return shutil.which("steelpanel") or "steelpanel"


def entry_text() -> str:
    return "\n".join([
        "[Desktop Entry]",
        "Type=Application",
        "Name=SteelPanel (tray)",
        "Comment=Apply the active SteelSeries profile at login",
        f"Exec={_command()} --hidden",
        "Icon=input-mouse",
        "Terminal=false",
        "X-GNOME-Autostart-enabled=true",
        "",
    ])


def _is_active(path: Path) -> bool:
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return False
    keys = {line.split("=", 1)[0].strip(): line.split("=", 1)[1].strip()
            for line in lines if "=" in line}
    return (keys.get("Hidden", "false").lower() != "true"
            and keys.get("X-GNOME-Autostart-enabled", "true").lower() != "false"
            and "steelpanel" in keys.get("Exec", ""))


def is_enabled() -> bool:
    d = autostart_dir()
    return any(_is_active(d / name) for name in (FILE_NAME, *OTHER_NAMES))


def set_enabled(enabled: bool) -> None:
    d = autostart_dir()
    if enabled:
        d.mkdir(parents=True, exist_ok=True)
        (d / FILE_NAME).write_text(entry_text())
    else:
        for name in (FILE_NAME, *OTHER_NAMES):
            path = d / name
            if path.exists() and _is_active(path):
                path.unlink()
