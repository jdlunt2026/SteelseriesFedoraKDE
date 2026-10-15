from types import SimpleNamespace

import pytest

evdev = pytest.importorskip("evdev")
from evdev import ecodes as e

from steelpanel import remap
from steelpanel.remap import Remapper

VID, WIRELESS, WIRED = 0x1038, 0x1858, 0x185A


class FakeDev:
    def __init__(self, path, name, product, caps):
        self.path, self.name = path, name
        self.info = SimpleNamespace(vendor=VID, product=product)
        self._caps = caps
        self.queue, self.grabbed, self.closed = [], False, False

    def capabilities(self):
        return self._caps

    def read(self):
        if self.closed:
            raise OSError(19, "No such device")
        events, self.queue = self.queue, []
        return events

    def grab(self):
        self.grabbed = True

    def ungrab(self):
        self.grabbed = False

    def close(self):
        self.closed = True


POINTER = {e.EV_KEY: [e.BTN_LEFT, e.BTN_RIGHT, e.BTN_MIDDLE, e.BTN_SIDE, e.BTN_EXTRA],
           e.EV_REL: [e.REL_X, e.REL_Y, e.REL_WHEEL], e.EV_MSC: [e.MSC_SCAN]}
SIDE_KEYS = {e.EV_KEY: [getattr(e, f"KEY_{k}") for k in "1234567890"] + [e.KEY_MINUS, e.KEY_EQUAL],
             e.EV_MSC: [e.MSC_SCAN]}


class FakeBackend:
    """Returns fresh handles on every scan, like the real one."""

    def __init__(self, specs):
        self.specs = specs  # path -> (name, product, caps)
        self.opened: list[FakeDev] = []
        self.uinputs = []

    def list_devices(self, vendor, products):
        devs = [FakeDev(p, n, prod, caps) for p, (n, prod, caps) in self.specs.items()
                if prod in products]
        self.opened += devs
        return devs

    def make_uinput(self, caps, name, vendor, product):
        ui = SimpleNamespace(caps=caps, name=name, written=[], closed=False)
        ui.write = lambda t, c, v: ui.written.append((t, c, v))
        ui.close = lambda: setattr(ui, "closed", True)
        self.uinputs.append(ui)
        return ui

    def wait(self, devices, timeout):
        return [d for d in devices if d.queue]


def ev(t, c, v):
    return SimpleNamespace(type=t, code=c, value=v)


@pytest.fixture
def aerox():
    backend = FakeBackend({
        "/dev/input/event5": ("SteelSeries Aerox 9 Wireless", WIRELESS, POINTER),
        "/dev/input/event6": ("SteelSeries Aerox 9 Wireless Keyboard", WIRELESS, SIDE_KEYS),
    })
    return Remapper(VID, [WIRELESS, WIRED], "Aerox 9", backend=backend), backend


def src(r, path):
    return r._sources[path].dev


def test_only_device_with_mapped_button_is_grabbed(aerox):
    r, b = aerox
    r._mapping = {e.KEY_1: e.KEY_F13}
    r.reconcile()
    assert set(r._sources) == {"/dev/input/event6"}, "pointer device untouched"
    assert src(r, "/dev/input/event6").grabbed
    # every handle opened during the scan but not used is closed
    assert all(d.closed for d in b.opened if d.path == "/dev/input/event5")


def test_events_translated_and_forwarded(aerox):
    r, b = aerox
    r._mapping = {e.KEY_1: e.KEY_F13}
    r.reconcile()
    kb = src(r, "/dev/input/event6")
    kb.queue = [ev(e.EV_MSC, e.MSC_SCAN, 30), ev(e.EV_KEY, e.KEY_1, 1), ev(e.EV_SYN, 0, 0),
                ev(e.EV_KEY, e.KEY_2, 1), ev(e.EV_KEY, e.KEY_1, 0), ev(e.EV_SYN, 0, 0)]
    r.process(kb)
    ui = b.uinputs[-1]
    assert ui.written == [(e.EV_MSC, e.MSC_SCAN, 30), (e.EV_KEY, e.KEY_F13, 1), (e.EV_SYN, 0, 0),
                          (e.EV_KEY, e.KEY_2, 1), (e.EV_KEY, e.KEY_F13, 0), (e.EV_SYN, 0, 0)]
    assert e.KEY_F13 in ui.caps[e.EV_KEY]


def test_mouse_button_target_gets_pointer_axes(aerox):
    r, b = aerox
    r._mapping = {e.KEY_3: e.BTN_MIDDLE}
    r.reconcile()
    caps = b.uinputs[-1].caps
    assert e.BTN_MIDDLE in caps[e.EV_KEY] and e.REL_X in caps[e.EV_REL]


def test_remapping_pointer_button_grabs_pointer(aerox):
    r, b = aerox
    r._mapping = {e.BTN_SIDE: e.KEY_F14}
    r.reconcile()
    assert set(r._sources) == {"/dev/input/event5"}
    ptr = src(r, "/dev/input/event5")
    ptr.queue = [ev(e.EV_REL, e.REL_X, 5), ev(e.EV_KEY, e.BTN_SIDE, 1), ev(e.EV_SYN, 0, 0)]
    r.process(ptr)
    assert b.uinputs[-1].written == [(e.EV_REL, e.REL_X, 5), (e.EV_KEY, e.KEY_F14, 1), (e.EV_SYN, 0, 0)]


def test_capture_ignores_left_button_and_does_not_grab(aerox):
    r, b = aerox
    got = []
    r._capture = got.append
    r.reconcile()
    assert len(r._sources) == 2 and not any(s.grabbed for s in r._sources.values())
    ptr = src(r, "/dev/input/event5")
    ptr.queue = [ev(e.EV_KEY, e.BTN_LEFT, 1), ev(e.EV_KEY, e.BTN_EXTRA, 1)]
    r.process(ptr)
    assert got == ["BTN_EXTRA"] and r._capture is None
    assert b.uinputs == []  # nothing grabbed -> no virtual device


def test_set_mapping_validates_and_skips_protected():
    r = Remapper(VID, [WIRELESS], "x", backend=FakeBackend({}))
    r.start = lambda: None
    r.set_mapping({"BTN_LEFT": "KEY_A", "KEY_1": "KEY_1", "KEY_2": "KEY_F13"})
    assert r._mapping == {e.KEY_2: e.KEY_F13}
    with pytest.raises(KeyError):
        r.set_mapping({"KEY_1": "KEY_NOPE"})


def test_unplug_releases_and_rescans(aerox):
    r, b = aerox
    r._mapping = {e.KEY_1: e.KEY_F13}
    r.reconcile()
    kb = src(r, "/dev/input/event6")
    kb.closed = True  # read() now raises ENODEV
    r.process(kb)
    assert "/dev/input/event6" not in r._sources
    r.reconcile()
    assert src(r, "/dev/input/event6").grabbed  # picked up again


def test_close_all_ungrabs(aerox):
    r, b = aerox
    r._mapping = {e.KEY_1: e.KEY_F13}
    r.reconcile()
    kb = src(r, "/dev/input/event6")
    ui = b.uinputs[-1]
    r.stop()
    assert not kb.grabbed and kb.closed and ui.closed


def test_thread_end_to_end(aerox):
    r, b = aerox
    r.set_mapping({"KEY_1": "KEY_F13"})
    import time
    for _ in range(100):
        if r._sources:
            break
        time.sleep(0.01)
    kb = src(r, "/dev/input/event6")
    kb.queue = [ev(e.EV_KEY, e.KEY_1, 1)]
    for _ in range(100):
        if b.uinputs and b.uinputs[-1].written:
            break
        time.sleep(0.01)
    assert b.uinputs[-1].written == [(e.EV_KEY, e.KEY_F13, 1)]
    r.set_mapping({})  # idle -> thread exits and releases the mouse
    r._thread.join(timeout=2)
    assert kb.closed and not kb.grabbed


def test_key_names():
    names = remap.all_key_names()
    assert names[0] == "BTN_MIDDLE" and "KEY_F13" in names and "KEY_RESERVED" not in names
    assert remap.pretty("KEY_F13") == "F13" and remap.pretty("BTN_EXTRA") == "Forward (mouse 5)"
