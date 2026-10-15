"""Mouse backend built on rivalcfg (https://github.com/flozz/rivalcfg).

rivalcfg describes every supported mouse as a data profile (settings, value
types, ranges, choices). We translate those profiles into generic
:class:`~steelpanel.model.Setting` specs so new mice supported by rivalcfg
show up in the GUI without any code change here.
"""

from __future__ import annotations

import re
from typing import Any

from .. import model
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


def _group_for(key: str) -> str:
    if key == "buttons_mapping":
        return model.MAPPING
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
            s = Setting(kind=model.RANGE, minimum=lo, maximum=hi, step=step, **common)
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
        self.name = mouse.name
        self.id = f"mouse:{mouse.vendor_id:04x}:{mouse.product_id:04x}"
        self.supports_onboard_save = "save_command" in mouse.mouse_profile
        self._settings = settings_from_profile(mouse.mouse_profile)
        self._by_key = {s.key: s for s in self._settings}

    def settings(self) -> list[Setting]:
        return self._settings

    def apply(self, key: str, value: Any) -> None:
        spec = self._by_key[key]
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

    def close(self) -> None:
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
        seen = set()
        for dev in rival_devices.list_plugged_devices():
            ids = (dev["vendor_id"], dev["product_id"])
            if ids in seen:
                continue
            seen.add(ids)
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
