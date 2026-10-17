"""OLED screens of Apex keyboards, driven directly over USB HID.

The screen shows up as its own device, independent of OpenRGB (which only
handles the keyboard's lighting). See steelpanel/oled.py for the protocol.
"""

from __future__ import annotations

from typing import Any

from .. import model, oled
from ..model import Backend, Device, DiscoveryResult, Setting


class KeyboardScreen(Device):
    kind = "keyboard screen"

    def __init__(self, screen, runner: oled.ScreenRunner | None = None):
        self._screen = screen
        self._runner = runner or oled.ScreenRunner(screen)
        self.name = f"SteelSeries {screen.model.name}"
        self.id = f"screen:{screen.model.name}"
        self._settings = [
            Setting(key="mode", label="Show", kind=model.CHOICE, group=model.SCREEN,
                    default="Keyboard default", choices=list(oled.MODES),
                    description="Anything but 'Keyboard default' is shown while SteelPanel runs"),
            Setting(key="clock_format", label="Clock format", kind=model.CHOICE,
                    group=model.SCREEN, default="24-hour", choices=list(oled.CLOCK_FORMATS)),
            Setting(key="show_date", label="Show date under the clock", kind=model.CHOICE,
                    group=model.SCREEN, default="Yes", choices=["Yes", "No"]),
            Setting(key="text", label="Text", kind=model.TEXT, group=model.SCREEN, default="",
                    description="Shown in the largest size that fits; long text wraps"),
            Setting(key="image", label="Image", kind=model.FILE, group=model.SCREEN, default="",
                    description="Scaled to 128x40 and converted to black and white"),
        ]
        self._by_key = {s.key: s for s in self._settings}

    def settings(self) -> list[Setting]:
        return self._settings

    def apply(self, key: str, value: Any) -> None:
        spec = self._by_key[key]
        if spec.kind == model.CHOICE and value not in spec.choices:
            raise ValueError(f"unknown choice {value!r}")
        self._runner.configure(**{key: value if value is not None else spec.default})

    def info(self) -> dict[str, str]:
        lines = {"Device": f"{oled.VENDOR:04x}:{self._screen.product_id:04x}"}
        if not self._screen.model.verified:
            lines["Support"] = "experimental"
        if self._runner.status:
            lines["Problem"] = self._runner.status
        return lines

    def close(self) -> None:
        self._runner.stop()


class ScreenBackend(Backend):
    name = "keyboard screens"

    def discover(self) -> DiscoveryResult:
        result = DiscoveryResult()
        if oled.hid is None:
            result.problems.append("hidapi is not installed: keyboard screens disabled.")
            return result
        seen = set()
        for product_id, path in oled.find_screens():
            if product_id in seen:
                continue
            seen.add(product_id)
            result.devices.append(KeyboardScreen(oled.HidScreen(product_id, path)))
        return result
