"""Minimal stand-in for openrgb-python's client/devices."""

from types import SimpleNamespace

from openrgb.utils import DeviceType, ModeColors, ModeData, ModeFlags, RGBColor


def mode(i, name, flags=ModeFlags(0), color_mode=ModeColors.NONE, ncolors=0, **kw):
    return ModeData(
        id=i, name=name, value=i, flags=flags,
        speed_min=kw.get("speed_min"), speed_max=kw.get("speed_max"),
        brightness_min=kw.get("brightness_min"), brightness_max=kw.get("brightness_max"),
        colors_min=ncolors or None, colors_max=ncolors or None,
        speed=kw.get("speed"), brightness=kw.get("brightness"),
        direction=None, color_mode=color_mode,
        colors=[RGBColor(0, 0, 255)] * ncolors if ncolors else None,
    )


class FakeZone:
    def __init__(self, name):
        self.name, self.colors, self.calls = name, [RGBColor(0, 0, 0)], []

    def set_color(self, color, fast=False):
        self.calls.append(color)


class FakeDevice:
    def __init__(self, name="SteelSeries Apex Pro TKL", type_=DeviceType.KEYBOARD, vendor="SteelSeries"):
        self.name, self.type = name, type_
        self.metadata = SimpleNamespace(vendor=vendor, serial="", location="HID: /dev/hidraw3",
                                        version="1.2")
        self.modes = [
            mode(0, "Direct", ModeFlags.HAS_PER_LED_COLOR, ModeColors.PER_LED),
            mode(1, "Static", ModeFlags.HAS_MODE_SPECIFIC_COLOR | ModeFlags.HAS_BRIGHTNESS,
                 ModeColors.MODE_SPECIFIC, ncolors=1, brightness_min=0, brightness_max=255, brightness=255),
            mode(2, "Rainbow Wave", ModeFlags.HAS_SPEED, ModeColors.NONE,
                 speed_min=100, speed_max=0, speed=50),
        ]
        self.active_mode = 1
        self.colors = [RGBColor(255, 0, 0)]
        self.zones = [FakeZone("Keyboard"), FakeZone("Logo")]
        self.calls = []

    def set_mode(self, m):
        if isinstance(m, str):
            m = next(x for x in self.modes if x.name.lower() == m.lower())
        self.active_mode = m.id
        self.modes[m.id] = m
        self.calls.append(("mode", m))

    def set_color(self, c, fast=False):
        self.calls.append(("color", c))

    def set_colors(self, cs, fast=False):
        self.calls.append(("colors", cs))


class FakeClient:
    def __init__(self, *devices):
        self.devices = list(devices)
        self.updates = 0

    def update(self):
        self.updates += 1
