"""Software button remapping through the Linux input layer (evdev/uinput).

Used for mice whose onboard button mapping rivalcfg can't program (e.g. the
Aerox 9 Wireless). How it works:

* The mouse exposes several kernel input devices (/dev/input/event*): one for
  the pointer and usually one or more "keyboard" interfaces for extra buttons.
* Only the input devices that emit a remapped button are *grabbed*
  (exclusive access). Their events are re-emitted through one virtual
  "SteelPanel" device, with remapped buttons translated. If no remapped
  button lives on the pointer device, pointer movement never goes through us.
* This happens below the display server, so it works the same on Wayland and
  X11. It only lasts while SteelPanel runs; closing it releases the grab and
  the mouse behaves normally again.

Mappings are stored by evdev code names, e.g. ``{"KEY_1": "KEY_F13"}``.
"""

from __future__ import annotations

import logging
import select
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable

log = logging.getLogger(__name__)

try:
    import evdev
    from evdev import ecodes
except ImportError:  # pragma: no cover - reported to the user by Remapper.status
    evdev = None
    ecodes = None

VIRTUAL_NAME = "SteelPanel virtual"
#: Never remap/capture the left button: it would make the mouse unusable
#: (and clicking "Cancel" in the capture dialog would capture it).
PROTECTED = {"BTN_LEFT"}

# Friendly names for the targets people use most.
FRIENDLY = {
    "BTN_LEFT": "Left click", "BTN_RIGHT": "Right click", "BTN_MIDDLE": "Middle click",
    "BTN_SIDE": "Back (mouse 4)", "BTN_EXTRA": "Forward (mouse 5)",
    "BTN_FORWARD": "Forward", "BTN_BACK": "Back", "BTN_TASK": "Task button",
    "KEY_LEFTCTRL": "Left Ctrl", "KEY_LEFTSHIFT": "Left Shift", "KEY_LEFTALT": "Left Alt",
    "KEY_LEFTMETA": "Meta (Super)", "KEY_RIGHTCTRL": "Right Ctrl", "KEY_RIGHTALT": "AltGr",
    "KEY_PLAYPAUSE": "Play / Pause", "KEY_NEXTSONG": "Next track",
    "KEY_PREVIOUSSONG": "Previous track", "KEY_VOLUMEUP": "Volume up",
    "KEY_VOLUMEDOWN": "Volume down", "KEY_MUTE": "Mute", "KEY_ESC": "Escape",
    "KEY_ENTER": "Enter", "KEY_SPACE": "Space", "KEY_TAB": "Tab",
    "KEY_BACKSPACE": "Backspace", "KEY_DELETE": "Delete",
    "KEY_PAGEUP": "Page Up", "KEY_PAGEDOWN": "Page Down",
    "KEY_COPY": "Copy", "KEY_PASTE": "Paste", "KEY_CUT": "Cut", "KEY_UNDO": "Undo",
}

#: Shown first in the target picker.
COMMON_TARGETS = [
    "BTN_MIDDLE", "BTN_RIGHT", "BTN_SIDE", "BTN_EXTRA",
    *[f"KEY_F{i}" for i in range(13, 25)],
    "KEY_LEFTCTRL", "KEY_LEFTSHIFT", "KEY_LEFTALT", "KEY_LEFTMETA",
    "KEY_PLAYPAUSE", "KEY_NEXTSONG", "KEY_PREVIOUSSONG",
    "KEY_VOLUMEUP", "KEY_VOLUMEDOWN", "KEY_MUTE",
    "KEY_ESC", "KEY_ENTER", "KEY_SPACE", "KEY_TAB", "KEY_PAGEUP", "KEY_PAGEDOWN",
]


def available() -> bool:
    return evdev is not None


def pretty(name: str) -> str:
    if name in FRIENDLY:
        return FRIENDLY[name]
    for prefix in ("KEY_", "BTN_"):
        if name.startswith(prefix):
            rest = name[len(prefix):]
            return rest if len(rest) <= 3 else rest.capitalize()
    return name


def _first_name(names) -> str:
    return names[0] if isinstance(names, (list, tuple)) else names


def all_key_names() -> list[str]:
    """Every remappable target, common ones first."""
    if ecodes is None:
        return list(COMMON_TARGETS)
    names = {_first_name(n) for n in ecodes.KEY.values()} | {_first_name(n) for n in ecodes.BTN.values()}
    names = {n for n in names if n.startswith(("KEY_", "BTN_")) and n != "KEY_RESERVED"}
    rest = sorted(names - set(COMMON_TARGETS))
    return [n for n in COMMON_TARGETS if n in names] + rest


def code_of(name: str) -> int:
    return ecodes.ecodes[name]


def name_of(code: int) -> str:
    return _first_name(ecodes.KEY.get(code) or ecodes.BTN.get(code) or f"KEY_{code}")


# --------------------------------------------------------------------------
# Thin layer over python-evdev, replaceable in tests.


class EvdevBackend:
    def list_devices(self, vendor: int, products: set[int]):
        """Open the input devices of the given USB ids (fresh handles each
        call; the caller closes them). Everything else is closed at once."""
        devices = []
        for path in evdev.list_devices():
            try:
                dev = evdev.InputDevice(path)
            except PermissionError:
                if _sysfs_ids(path) in {(vendor, p) for p in products}:
                    raise  # it's our mouse: report the missing udev rule
                continue
            except OSError:
                continue  # vanished
            if dev.info.vendor == vendor and dev.info.product in products:
                devices.append(dev)
            else:
                dev.close()
        return devices

    def make_uinput(self, capabilities: dict, name: str, vendor: int, product: int):
        return evdev.UInput(capabilities, name=name, vendor=vendor, product=product)

    def wait(self, devices, timeout: float):
        if not devices:
            time.sleep(timeout)
            return []
        readable, _, _ = select.select(devices, [], [], timeout)
        return readable


def _sysfs_ids(path: str) -> tuple[int, int] | None:
    """USB ids of /dev/input/eventN without opening it (no permission needed)."""
    base = f"/sys/class/input/{path.rsplit('/', 1)[-1]}/device/id"
    try:
        with open(f"{base}/vendor") as v, open(f"{base}/product") as p:
            return int(v.read(), 16), int(p.read(), 16)
    except (OSError, ValueError):
        return None


@dataclass
class _Source:
    dev: object
    grabbed: bool = False
    keys: set[int] = field(default_factory=set)


class Remapper:
    """Remaps buttons of every input device belonging to one physical mouse.

    Thread model: :meth:`start` runs :meth:`_loop` in a daemon thread. The
    public setters only update state under a lock and flag a reconfigure;
    all device I/O happens on the remapper thread.
    """

    def __init__(self, vendor: int, products: Iterable[int], label: str,
                 backend=None, rescan_interval: float = 3.0):
        self.vendor = vendor
        self.products = set(products)
        self.label = label
        self.backend = backend or (EvdevBackend() if available() else None)
        self.rescan_interval = rescan_interval
        self.status = "" if self.backend else (
            "Button remapping needs python-evdev: sudo dnf install python3-evdev, "
            "then re-run ./install.sh")
        self._lock = threading.Lock()
        self._mapping: dict[int, int] = {}
        self._capture: Callable[[str], None] | None = None
        self._dirty = True
        self._sources: dict[str, _Source] = {}
        self._uinput = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._last_scan = 0.0

    # -- public API (any thread) ---------------------------------------------
    def set_mapping(self, mapping: dict[str, str]) -> None:
        if self.backend is None:
            raise RuntimeError(self.status)
        codes = {}
        for src, dst in (mapping or {}).items():
            if src in PROTECTED or not dst or src == dst:
                continue
            codes[code_of(src)] = code_of(dst)  # KeyError on unknown names
        with self._lock:
            self._mapping = codes
            self._dirty = True
        if codes:
            self.start()

    def capture(self, callback: Callable[[str], None] | None) -> None:
        """Call `callback(name)` (from the remapper thread) on the next press
        of any button of this mouse, except the left button. None cancels."""
        if self.backend is None:
            raise RuntimeError(self.status)
        with self._lock:
            self._capture = callback
            self._dirty = True
        if callback:
            self.start()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name=f"remap:{self.label}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)
        self._thread = None
        self._close_all()

    # -- remapper thread -----------------------------------------------------
    def _loop(self) -> None:
        try:
            while not self._stop.is_set():
                self.reconcile()
                with self._lock:
                    idle = not self._mapping and self._capture is None
                if idle:
                    self._close_all()
                    return  # nothing to do; set_mapping/capture restarts us
                devs = [s.dev for s in self._sources.values()]
                for dev in self.backend.wait(devs, 0.25):
                    self.process(dev)
        except Exception as exc:  # never leave the mouse grabbed on a crash
            log.exception("remapper crashed")
            self.status = f"Button remapping stopped: {exc}"
            self._close_all()

    def _matches(self, dev) -> bool:
        info = dev.info
        return (info.vendor == self.vendor and info.product in self.products
                and not dev.name.startswith(VIRTUAL_NAME))

    def reconcile(self, force_scan: bool = False) -> None:
        """Open/grab exactly the input devices the current mapping needs."""
        now = time.monotonic()
        with self._lock:
            dirty, self._dirty = self._dirty, False
            mapping = dict(self._mapping)
            capturing = self._capture is not None
        if not (dirty or force_scan or now - self._last_scan > self.rescan_interval):
            return
        self._last_scan = now

        try:
            listed = self.backend.list_devices(self.vendor, self.products)
        except PermissionError:
            self._close_all()
            self.status = ("No permission for the mouse's input devices. Run "
                           "'sudo steelpanel --install-udev' again, then replug the mouse.")
            return
        found = {}
        for d in listed:
            if self._matches(d):
                found[d.path] = d
            else:
                d.close()
        wanted: dict[str, bool] = {}  # path -> needs grab
        for path, dev in found.items():
            keys = set(dev.capabilities().get(ecodes.EV_KEY, []))
            grab = bool(keys & mapping.keys())
            if grab or capturing:
                wanted[path] = grab
        # Close devices that went away or whose role changed.
        changed = dirty or set(wanted) != set(self._sources) or any(
            self._sources[p].grabbed != g for p, g in wanted.items() if p in self._sources)
        if not changed:
            for d in found.values():  # fresh handles; keep the ones in use
                d.close()
            return
        self._close_all()
        for path, dev in found.items():
            if path not in wanted:
                dev.close()
                continue
            self._sources[path] = _Source(dev=dev, keys=set(dev.capabilities().get(ecodes.EV_KEY, [])))
        try:
            self._open_virtual(mapping)
            for src in self._sources.values():
                if wanted[src.dev.path]:
                    src.dev.grab()
                    src.grabbed = True
        except PermissionError:
            self._close_all()
            self.status = ("No permission for the mouse's input devices. Run "
                           "'sudo steelpanel --install-udev' again, then replug the mouse.")
            return
        except OSError as exc:
            self._close_all()
            self.status = f"Could not set up button remapping: {exc}"
            return
        if not found and (mapping or capturing):
            self.status = f"Waiting for {self.label}… (no input device found)"
        else:
            self.status = ""

    def _open_virtual(self, mapping: dict[int, int]) -> None:
        sources = list(self._sources.values())
        if not mapping or not any(s.keys & mapping.keys() for s in sources):
            return
        caps: dict[int, set] = {ecodes.EV_KEY: set(), ecodes.EV_REL: set(), ecodes.EV_MSC: set()}
        for src in sources:
            if not src.keys & mapping.keys():
                continue
            for etype, codes in src.dev.capabilities().items():
                if etype in caps:
                    caps[etype].update(c for c in codes if isinstance(c, int))
        caps[ecodes.EV_KEY].update(mapping.values())
        if any(code >= ecodes.BTN_MISC and code < ecodes.KEY_OK for code in caps[ecodes.EV_KEY]):
            # Mouse buttons on a device without axes are ignored by libinput.
            caps[ecodes.EV_REL].update({ecodes.REL_X, ecodes.REL_Y, ecodes.REL_WHEEL})
        caps = {t: sorted(c) for t, c in caps.items() if c}
        product = next(iter(self.products))
        self._uinput = self.backend.make_uinput(caps, f"{VIRTUAL_NAME} {self.label}", self.vendor, product)

    def process(self, dev) -> None:
        """Read pending events from `dev` and forward/translate them."""
        src = self._sources.get(dev.path)
        if src is None:
            return
        try:
            events = list(dev.read())
        except (BlockingIOError, InterruptedError):
            return
        except OSError:  # unplugged / mouse switched connection
            self._sources.pop(dev.path, None)
            self._dirty = True
            return
        with self._lock:
            mapping = self._mapping
            capture = self._capture
        for ev in events:
            if capture and ev.type == ecodes.EV_KEY and ev.value == 1:
                name = name_of(ev.code)
                if name not in PROTECTED:
                    with self._lock:
                        self._capture = None
                        self._dirty = True
                    capture(name)
                    capture = None
            if src.grabbed and self._uinput is not None:
                code = mapping.get(ev.code, ev.code) if ev.type == ecodes.EV_KEY else ev.code
                self._uinput.write(ev.type, code, ev.value)

    def _close_all(self) -> None:
        for src in self._sources.values():
            try:
                if src.grabbed:
                    src.dev.ungrab()
            except OSError:
                pass
            try:
                src.dev.close()
            except OSError:
                pass
        self._sources.clear()
        if self._uinput is not None:
            try:
                self._uinput.close()
            except OSError:
                pass
            self._uinput = None
