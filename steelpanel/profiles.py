"""Named profiles: {profile name: {device id: {setting key: value}}}.

Stored as JSON in ``$XDG_CONFIG_HOME/steelpanel/profiles.json``.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any, Iterable

from .model import Device

DEFAULT_PROFILE = "Default"


def default_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "steelpanel" / "profiles.json"


class ProfileStore:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else default_path()
        self._data: dict[str, Any] = {"active": DEFAULT_PROFILE, "profiles": {DEFAULT_PROFILE: {}}}
        if self.path.exists():
            with open(self.path) as f:
                loaded = json.load(f)
            if isinstance(loaded.get("profiles"), dict) and loaded["profiles"]:
                self._data = loaded
        if self.active not in self._data["profiles"]:
            self._data["active"] = next(iter(self._data["profiles"]))

    # -- queries -------------------------------------------------------------
    def names(self) -> list[str]:
        return list(self._data["profiles"])

    @property
    def active(self) -> str:
        return self._data["active"]

    @active.setter
    def active(self, name: str) -> None:
        if name not in self._data["profiles"]:
            raise KeyError(name)
        self._data["active"] = name

    def get(self, name: str) -> dict[str, dict[str, Any]]:
        return self._data["profiles"][name]

    def device_values(self, name: str, device_id: str) -> dict[str, Any]:
        return self.get(name).get(device_id, {})

    # -- edits ---------------------------------------------------------------
    def set_value(self, name: str, device_id: str, key: str, value: Any) -> None:
        self.get(name).setdefault(device_id, {})[key] = value

    def create(self, name: str, copy_from: str | None = None) -> None:
        name = name.strip()
        if not name:
            raise ValueError("Profile name cannot be empty")
        if name in self._data["profiles"]:
            raise ValueError(f"Profile '{name}' already exists")
        source = self.get(copy_from) if copy_from else {}
        self._data["profiles"][name] = copy.deepcopy(source)

    def rename(self, old: str, new: str) -> None:
        self.create(new, copy_from=old)
        del self._data["profiles"][old]
        if self.active == old:
            self._data["active"] = new

    def delete(self, name: str) -> None:
        if len(self._data["profiles"]) == 1:
            raise ValueError("Cannot delete the last profile")
        del self._data["profiles"][name]
        if self.active == name:
            self._data["active"] = next(iter(self._data["profiles"]))

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        with open(tmp, "w") as f:
            json.dump(self._data, f, indent=2)
        os.replace(tmp, self.path)  # atomic: never leaves a half-written file


def apply_profile(store: ProfileStore, name: str, devices: Iterable[Device]) -> list[str]:
    """Push every saved value of profile `name` to the matching devices.

    Values are applied in the device's own setting order (so e.g. a keyboard
    effect is selected before its color). Returns a list of error messages.
    """
    errors = []
    for device in devices:
        values = store.device_values(name, device.id)
        for spec in device.settings():
            if spec.key not in values:
                continue
            try:
                device.apply(spec.key, values[spec.key])
            except Exception as exc:
                errors.append(f"{device.name} / {spec.label}: {exc}")
    return errors
