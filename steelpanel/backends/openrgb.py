"""Keyboard (and mousepad) lighting backend using the OpenRGB SDK server.

There is no maintained Linux library for SteelSeries Apex keyboard settings,
but OpenRGB (https://openrgb.org) drives their lighting well. It runs as a
local server (``openrgb --server``) that we talk to over TCP.

Mice are skipped here because rivalcfg covers them with more features.
"""

from __future__ import annotations

import copy
import os
from typing import Any, Callable

from .. import model
from ..model import Backend, Device, DiscoveryResult, Setting

try:
    from openrgb import OpenRGBClient
    from openrgb.utils import DeviceType, ModeColors, ModeFlags, RGBColor
except ImportError:  # pragma: no cover - reported at discovery time
    OpenRGBClient = None

DEFAULT_HOST = os.environ.get("STEELPANEL_OPENRGB_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.environ.get("STEELPANEL_OPENRGB_PORT", "6742"))


def _scale(percent: int, lo: int, hi: int) -> int:
    """Map 0-100% onto a mode's native [lo, hi] range (which may be inverted)."""
    return round(lo + (hi - lo) * max(0, min(100, percent)) / 100)


def _percent(value: int | None, lo: int | None, hi: int | None) -> int | None:
    if value is None or lo is None or hi is None or lo == hi:
        return None
    return round(100 * (value - lo) / (hi - lo))


def _hex(color) -> str:
    return f"#{color.red:02x}{color.green:02x}{color.blue:02x}"


def is_steelseries(dev) -> bool:
    vendor = (getattr(dev.metadata, "vendor", None) or "").lower()
    return "steelseries" in vendor or "steelseries" in dev.name.lower()


class OpenRGBDevice(Device):
    def __init__(self, dev):
        self._dev = dev
        self.name = dev.name
        self.kind = "keyboard" if dev.type == DeviceType.KEYBOARD else dev.type.name.lower()
        serial = (dev.metadata.serial or dev.metadata.location or "").strip()
        self.id = f"{self.kind}:{dev.name}" + (f":{serial}" if serial else "")

    def _active_mode(self):
        return self._dev.modes[self._dev.active_mode]

    def settings(self) -> list[Setting]:
        dev = self._dev
        active = self._active_mode()
        result = [
            Setting(
                key="mode", label="Effect", kind=model.CHOICE, group=model.LIGHTING,
                choices=[m.name for m in dev.modes], default=active.name,
                description="Lighting effect (provided by OpenRGB)",
            ),
        ]
        if any(ModeFlags.HAS_BRIGHTNESS in m.flags for m in dev.modes):
            result.append(Setting(
                key="brightness", label="Brightness (%)", kind=model.RANGE,
                group=model.LIGHTING, minimum=0, maximum=100, step=5,
                default=_percent(active.brightness, active.brightness_min, active.brightness_max) or 100,
                description="Applies to effects that support brightness",
            ))
        if any(ModeFlags.HAS_SPEED in m.flags for m in dev.modes):
            result.append(Setting(
                key="speed", label="Effect speed (%)", kind=model.RANGE,
                group=model.LIGHTING, minimum=0, maximum=100, step=5,
                default=_percent(active.speed, active.speed_min, active.speed_max) or 50,
                description="Applies to animated effects",
            ))
        current = dev.colors[0] if dev.colors else None
        if active.colors:
            current = active.colors[0]
        result.append(Setting(
            key="color", label="Color", kind=model.COLOR, group=model.LIGHTING,
            default=_hex(current) if current else "#ff0000",
            description="Color of the whole device (static / single-color effects)",
        ))
        if len(dev.zones) > 1:
            for i, zone in enumerate(dev.zones):
                result.append(Setting(
                    key=f"zone{i}", label=f"Zone: {zone.name}", kind=model.COLOR,
                    group=model.LIGHTING,
                    default=_hex(zone.colors[0]) if zone.colors else "#ff0000",
                    description="Needs a per-key effect such as Direct",
                ))
        return result

    def apply(self, key: str, value: Any) -> None:
        dev = self._dev
        if key == "mode":
            dev.set_mode(value)
        elif key in ("brightness", "speed"):
            mode = copy.deepcopy(self._active_mode())
            flag = ModeFlags.HAS_BRIGHTNESS if key == "brightness" else ModeFlags.HAS_SPEED
            if flag not in mode.flags:
                raise ValueError(f"The '{mode.name}' effect has no {key} control")
            lo, hi = getattr(mode, f"{key}_min"), getattr(mode, f"{key}_max")
            setattr(mode, key, _scale(int(value), lo, hi))
            dev.set_mode(mode)
        elif key == "color":
            mode = self._active_mode()
            if mode.color_mode not in (ModeColors.PER_LED, ModeColors.MODE_SPECIFIC):
                raise ValueError(f"The '{mode.name}' effect does not use a color")
            if mode.color_mode == ModeColors.MODE_SPECIFIC:
                # Fill every color slot so multi-color effects stay uniform.
                dev.set_colors([RGBColor.fromHEX(value)] * len(mode.colors or [None]))
            else:
                dev.set_color(RGBColor.fromHEX(value))
        elif key.startswith("zone"):
            if self._active_mode().color_mode != ModeColors.PER_LED:
                raise ValueError("Zone colors need a per-key effect such as 'Direct'")
            dev.zones[int(key[4:])].set_color(RGBColor.fromHEX(value))
        else:
            raise KeyError(key)

    def info(self) -> dict[str, str]:
        meta = self._dev.metadata
        lines = {"Via": "OpenRGB"}
        if meta.version:
            lines["Firmware"] = meta.version
        if meta.location:
            lines["Location"] = meta.location
        return lines


class OpenRGBBackend(Backend):
    name = "OpenRGB"

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        client_factory: Callable[..., Any] | None = None,
    ):
        self.host, self.port = host, port
        self._client_factory = client_factory or OpenRGBClient
        self._client = None

    def discover(self) -> DiscoveryResult:
        result = DiscoveryResult()
        if self._client_factory is None:
            result.problems.append("openrgb-python is not installed: keyboard support disabled.")
            return result
        try:
            if self._client is None:
                self._client = self._client_factory(self.host, self.port, "SteelPanel")
            else:
                self._client.update()
        except (OSError, TimeoutError) as exc:
            self._client = None
            result.problems.append(
                f"OpenRGB server not reachable at {self.host}:{self.port} ({exc}). "
                "Keyboard lighting needs OpenRGB running with: openrgb --server"
            )
            return result
        for dev in self._client.devices:
            if dev.type == DeviceType.MOUSE or not is_steelseries(dev):
                continue
            result.devices.append(OpenRGBDevice(dev))
        return result
