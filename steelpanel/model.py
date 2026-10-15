"""Backend-agnostic description of devices and their settings.

The GUI never talks to rivalcfg or OpenRGB directly. Each backend turns its
devices into :class:`Device` objects exposing a list of :class:`Setting`
specs, and the GUI builds its controls from those specs.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

# Setting kinds understood by the GUI.
CHOICE = "choice"    # one of `choices`
RANGE = "range"      # int in [minimum, maximum] by `step`
COLOR = "color"      # "#rrggbb" (or None when `allow_off`)
DPI = "dpi"          # list of 1..max_presets ints in [minimum, maximum]
BUTTONS = "buttons"  # dict of button name -> action string
ACTION = "action"    # no value, just triggers a command

# Groups (one tab each in the GUI).
PERFORMANCE = "Performance"
LIGHTING = "Lighting"
MAPPING = "Buttons"
GENERAL = "General"


@dataclass
class Setting:
    key: str
    label: str
    kind: str
    group: str = GENERAL
    default: Any = None
    description: str = ""
    choices: list = field(default_factory=list)
    minimum: int = 0
    maximum: int = 100
    step: int = 1
    max_presets: int = 1
    allow_off: bool = False
    buttons: list[str] = field(default_factory=list)


class Device(ABC):
    #: Stable identifier used as the key in saved profiles.
    id: str
    name: str
    #: "mouse", "keyboard", ...
    kind: str
    #: True when settings can be persisted to the device's onboard memory.
    supports_onboard_save: bool = False

    @abstractmethod
    def settings(self) -> list[Setting]:
        """Settings this device exposes."""

    @abstractmethod
    def apply(self, key: str, value: Any) -> None:
        """Send one setting to the hardware. Raises on failure."""

    def save_onboard(self) -> None:
        raise NotImplementedError(f"{self.name} has no onboard memory")

    def info(self) -> dict[str, str]:
        """Read-only status lines (battery, firmware, ...)."""
        return {}

    def close(self) -> None:
        pass


@dataclass
class DiscoveryResult:
    devices: list[Device] = field(default_factory=list)
    #: Human-readable problems (missing permissions, server not running...).
    problems: list[str] = field(default_factory=list)


class Backend(ABC):
    name: str

    @abstractmethod
    def discover(self) -> DiscoveryResult:
        ...
