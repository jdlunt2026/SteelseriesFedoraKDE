"""Mouse backend built on rivalcfg (https://github.com/flozz/rivalcfg).

rivalcfg describes every supported mouse as a data profile (settings, value
types, ranges, choices). We translate those profiles into generic
:class:`~steelpanel.model.Setting` specs so new mice supported by rivalcfg
show up in the GUI without any code change here.
"""

from __future__ import annotations

import re
from typing import Any

from .. import model, remap
from ..model import Backend, Device, DiscoveryResult, Setting

try:
    import rivalcfg
    from rivalcfg import color_helpers, devices as rival_devices
    from rivalcfg.mouse import get_mouse
except ImportError:  # pragma: no cover - reported at discovery time
    rivalcfg = None

#: Actions offered in the button mapping editor. Any rivalcfg-accepted value
#: (keyboard keys like "F13", multimedia keys like "PlayPause") can also be
#: typed in by hand.
BUTTON_ACTIONS = [
    "default", "disabled", "dpi",
    "button1", "button2", "button3", "button4", "button5",
    "scrollup", "scrolldown",
    "PlayPause", "Next", "Previous", "VolumeUp", "VolumeDown", "Mute",
]

_LIGHTING_HINTS = ("color", "light", "led", "rainbow", "reactive", "gradient")
_PERFORMANCE_HINTS = ("sensitivity", "dpi", "polling", "angle", "lift", "acceleration")
_POWER_HINTS = ("sleep", "dim", "timer", "battery")
# rivalcfg puts units in descriptions, e.g. "... (minutes, 0 = disable)".
_UNITS = {"minutes": "min", "seconds": "s", "ms": "ms", "Hz": "Hz", "%": "%"}

# Wireless mice have one rivalcfg profile per connection, e.g.
# "SteelSeries Aerox 9 Wireless (wired mode)" and "... (2.4 GHz wireless mode)".
_MODE_SUFFIX = re.compile(r"\s*\(([^()]*\bmode)\)\s*$")


def split_connection(model_name: str) -> tuple[str, str | None]:
    """Split a rivalcfg model name into (device name, connection mode)."""
    m = _MODE_SUFFIX.search(model_name)
    if not m:
        return model_name, None
    mode = m.group(1).replace("\xa0", " ")
    return model_name[: m.start()], mode[: -len(" mode")].strip()


def _group_for(key: str) -> str:
    if key == "buttons_mapping":
        return model.MAPPING
    if any(h in key for h in _POWER_HINTS):
        return model.POWER
    if any(h in key for h in _LIGHTING_HINTS):
        return model.LIGHTING
    if any(h in key for h in _PERFORMANCE_HINTS):
        return model.PERFORMANCE
    return model.GENERAL


def to_hex(color: Any) -> str | None:
    """Best-effort conversion of a rivalcfg color value to "#rrggbb"."""
    if color is None:
        return None
    if isinstance(color, (tuple, list)) and len(color) == 3:
        r, g, b = color
        return f"#{r:02x}{g:02x}{b:02x}"
    if isinstance(color, str):
        if color.lower() in ("off", "disable", "disabled", "none"):
            return None
        if color.startswith("rgbgradient("):
            # Use the first color stop of a gradient as a representative.
            stops = re.findall(r"%\s*:\s*([#\w]+)", color)
            return to_hex(stops[0]) if stops else None
        try:
            return to_hex(color_helpers.parse_color_string(color))
        except ValueError:
            return None
    return None


def family_product_ids(vendor_id: int, name: str) -> list[int]:
    """USB product ids of every connection mode of the mouse called `name`."""
    return [pid for (vid, pid), prof in rival_devices.PROFILES.items()
            if vid == vendor_id and split_connection(prof["name"])[0] == name]


def parse_buttons(value: str | dict | None, names: list[str]) -> dict[str, str]:
    """Parse rivalcfg's ``buttons(a=b; c=d)`` syntax into a dict."""
    result = {n: "default" for n in names}
    if isinstance(value, dict):
        result.update(value)
        return result
    if not value or value == "default":
        return result
    body = value.strip()
    if body.startswith("buttons(") and body.endswith(")"):
        body = body[len("buttons("):-1]
    lookup = {n.lower(): n for n in names}
    for part in body.split(";"):
        if "=" not in part:
            continue
        k, v = (s.strip() for s in part.split("=", 1))
        if k.lower() in lookup:
            result[lookup[k.lower()]] = v
    return result


def format_buttons(mapping: dict[str, str]) -> str:
    parts = [f"{k}={v}" for k, v in mapping.items() if v and v != "default"]
    return "buttons(%s)" % "; ".join(parts) if parts else "default"


def settings_from_profile(profile: dict) -> list[Setting]:
    """Translate a rivalcfg device profile into generic setting specs."""
    result = []
    for key, info in profile["settings"].items():
        vt = info.get("value_type")
        label = info.get("label", key).strip()
        label = label[:1].upper() + label[1:]
        common = dict(
            key=key,
            label=label,
            group=_group_for(key),
            description=info.get("description", ""),
            default=info.get("default"),
        )
        if vt == "choice":
            s = Setting(kind=model.CHOICE, choices=list(info["choices"].keys()), **common)
        elif vt in ("range", "range_choice"):
            lo, hi, step = info["input_range"]
            desc = common["description"]
            unit = next((u for word, u in _UNITS.items() if f"({word}" in desc), "")
            s = Setting(
                kind=model.RANGE, minimum=lo, maximum=hi, step=step, unit=unit,
                minimum_label="Never" if lo == 0 and "0 = disable" in desc else "",
                **common,
            )
        elif vt in ("multidpi_range", "multidpi_range_choice", "multidpi_range_choice_xy"):
            lo, hi, step = info["input_range"]
            default = info.get("default")
            if isinstance(default, str):
                default = [int(d.split(":")[0]) for d in default.replace(" ", "").split(",")]
            common["default"] = default
            s = Setting(
                kind=model.DPI, minimum=lo, maximum=hi, step=step,
                max_presets=info.get("max_preset_count", 1), **common,
            )
        elif vt in ("rgbcolor", "rgbgradient", "rgbgradientv2", "reactive_rgbcolor"):
            common["default"] = to_hex(info.get("default"))
            s = Setting(kind=model.COLOR, allow_off=vt == "reactive_rgbcolor", **common)
        elif vt == "buttons":
            names = list(info["buttons"].keys())
            common["default"] = {n: "default" for n in names}
            s = Setting(kind=model.BUTTONS, buttons=names, choices=BUTTON_ACTIONS, **common)
        elif vt == "none":
            s = Setting(kind=model.ACTION, **common)
        else:
            continue  # unknown handler: skip rather than break the whole device
        result.append(s)
    return result


class RivalMouse(Device):
    kind = "mouse"

    def __init__(self, mouse):
        self._mouse = mouse
        self.name, self.connection = split_connection(mouse.name)
        # Keyed by name, not USB id, so a wireless mouse keeps one set of
        # profile values whether it is used over the cable or the dongle.
        self.id = f"mouse:{self.name}"
        self.supports_onboard_save = "save_command" in mouse.mouse_profile
        self._settings = settings_from_profile(mouse.mouse_profile)
        # Software input filtering (see steelpanel/remap.py): angle snapping
        # for every mouse, button remapping where rivalcfg can't do it onboard.
        self._remapper = remap.Remapper(
            mouse.vendor_id, family_product_ids(mouse.vendor_id, self.name), self.name)
        self._settings.append(Setting(
            key="angle_snapping", label="Angle snapping", kind=model.RANGE,
            group=model.PERFORMANCE, default=0, minimum=0, maximum=30, unit="°",
            minimum_label="Off",
            description="Straightens nearly horizontal and vertical movement while "
                        "SteelPanel is running (degrees of tolerance; about 10 is a good start)",
        ))
        self.has_software_buttons = "buttons_mapping" not in mouse.mouse_profile["settings"]
        if self.has_software_buttons:
            self._settings.append(Setting(
                key="software_buttons", label="Button remapping", kind=model.KEYMAP,
                group=model.MAPPING, default={},
                description="Remaps buttons while SteelPanel is running",
            ))
        self._by_key = {s.key: s for s in self._settings}

    def settings(self) -> list[Setting]:
        return self._settings

    def apply(self, key: str, value: Any) -> None:
        spec = self._by_key[key]
        if spec.kind == model.KEYMAP:
            self._remapper.set_mapping(value or {})
            return
        if key == "angle_snapping":
            self._remapper.set_angle_snapping(value or 0)
            return
        setter = getattr(self._mouse, f"set_{key}")
        if spec.kind == model.ACTION:
            setter()
        elif spec.kind == model.BUTTONS:
            setter(format_buttons(value) if isinstance(value, dict) else value)
        elif spec.kind == model.COLOR and value is None:
            setter("off")
        else:
            setter(value)

    def save_onboard(self) -> None:
        if not self.supports_onboard_save:
            super().save_onboard()
        self._mouse.save()

    def info(self) -> dict[str, str]:
        lines = {"Device": f"{self._mouse.vendor_id:04x}:{self._mouse.product_id:04x}"}
        if self.connection:
            lines["Connection"] = self.connection
        try:
            fw = self._mouse.firmware_version
            if fw and fw.strip("0.") != "":  # all-zero means unknown
                lines["Firmware"] = fw
        except Exception:
            pass
        try:
            battery = self._mouse.battery
            if battery.get("level") is not None:
                charging = " (charging)" if battery.get("is_charging") else ""
                lines["Battery"] = f"{battery['level']}%{charging}"
        except Exception:
            pass
        return lines

    def capture_button(self, callback) -> None:
        if not self.has_software_buttons:
            super().capture_button(callback)
        self._remapper.capture(callback)

    def remap_status(self) -> str:
        return self._remapper.status

    def close(self) -> None:
        self._remapper.stop()
        try:
            self._mouse.close()
        except Exception:
            pass


class RivalBackend(Backend):
    name = "rivalcfg"

    def discover(self) -> DiscoveryResult:
        result = DiscoveryResult()
        if rivalcfg is None:
            result.problems.append("rivalcfg is not installed: mouse support disabled.")
            return result
        plugged = {}
        for dev in rival_devices.list_plugged_devices():
            plugged.setdefault((dev["vendor_id"], dev["product_id"]), dev)
        # With the cable plugged in, a wireless mouse shows up both through its
        # cable and its dongle: keep the cable, which is the more reliable link.
        def wired_first(ids):
            _, conn = split_connection(rival_devices.get_profile(*ids)["name"])
            return 0 if conn in (None, "wired") else 1

        seen_names = set()
        for ids in sorted(plugged, key=wired_first):
            dev = plugged[ids]
            name, _ = split_connection(rival_devices.get_profile(*ids)["name"])
            if name in seen_names:
                continue
            seen_names.add(name)
            try:
                result.devices.append(RivalMouse(get_mouse(*ids)))
            except (IOError, OSError) as exc:
                result.problems.append(
                    f"{dev['name']}: cannot open device ({exc}). "
                    "Install the udev rules: sudo steelpanel --install-udev"
                )
            except Exception as exc:
                result.problems.append(f"{dev['name']}: {exc}")
        return result
