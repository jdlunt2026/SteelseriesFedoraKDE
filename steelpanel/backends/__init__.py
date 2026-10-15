from __future__ import annotations

from ..model import Backend, DiscoveryResult
from .openrgb import OpenRGBBackend
from .rival import RivalBackend


def default_backends() -> list[Backend]:
    return [RivalBackend(), OpenRGBBackend()]


def discover_all(backends: list[Backend]) -> DiscoveryResult:
    merged = DiscoveryResult()
    for backend in backends:
        try:
            found = backend.discover()
        except Exception as exc:  # one broken backend must not hide the others
            merged.problems.append(f"{backend.name}: {exc}")
            continue
        merged.devices.extend(found.devices)
        merged.problems.extend(found.problems)
    return merged
