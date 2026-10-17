import os

# Use rivalcfg's fake HID device so tests never touch real hardware.
os.environ["RIVALCFG_DRY"] = "1"
os.environ["RIVALCFG_DEBUG_NO_COMMAND_DELAY"] = "1"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    # Never read or write the real ~/.config (autostart entries, profiles).
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
