from types import SimpleNamespace

import pytest

evdev = pytest.importorskip("evdev")
from evdev import ecodes as e

from steelpanel import remap
from steelpanel.remap import AngleSnapper, Remapper

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


# -- angle snapping ---------------------------------------------------------


def stroke(snapper, moves, dt=0.001):
    return [snapper.filter(dx, dy, i * dt) for i, (dx, dy) in enumerate(moves)]


def test_snapper_straightens_near_horizontal_and_vertical():
    # ~6 degrees off horizontal, as 1000 Hz reports: mostly (1, 0), every
    # tenth report (1, 1).
    moves = [(1, 1 if i % 10 == 9 else 0) for i in range(50)]
    assert all(dy == 0 for _, dy in stroke(AngleSnapper(10), moves))
    vertical = [(1 if i % 10 == 9 else 0, -2) for i in range(50)]
    assert all(dx == 0 for dx, _ in stroke(AngleSnapper(10), vertical))


def test_snapper_passes_diagonals_and_respects_angle():
    diagonal = [(2, 1)] * 30  # ~27 degrees
    assert stroke(AngleSnapper(10), diagonal)[-1] == (2, 1)
    assert stroke(AngleSnapper(30), diagonal)[-1] == (2, 0)


def test_snapper_new_stroke_starts_fresh():
    s = AngleSnapper(10)
    stroke(s, [(5, 0)] * 20)
    # After a pause, a vertical move must not be judged by the old direction.
    assert s.filter(0, 3, 1.0) == (0, 3)
    assert s.filter(0, 0, 1.001) == (0, 0)


def test_snapping_grabs_only_the_pointer(aerox):
    r, b = aerox
    r._snap_degrees = 10.0
    r.reconcile()
    assert set(r._sources) == {"/dev/input/event5"}
    assert src(r, "/dev/input/event5").grabbed
    caps = b.uinputs[-1].caps
    assert {e.REL_X, e.REL_Y} <= set(caps[e.EV_REL]) and e.BTN_LEFT in caps[e.EV_KEY]


def test_snapping_filters_frames_and_forwards_the_rest(aerox):
    r, b = aerox
    r._snap_degrees = 10.0
    r.reconcile()
    ptr = src(r, "/dev/input/event5")
    ptr.queue = [ev(e.EV_REL, e.REL_X, 8), ev(e.EV_REL, e.REL_Y, 1), ev(e.EV_KEY, e.BTN_LEFT, 1),
                 ev(e.EV_SYN, e.SYN_REPORT, 0),
                 ev(e.EV_REL, e.REL_WHEEL, -1), ev(e.EV_SYN, e.SYN_REPORT, 0)]
    r.process(ptr)
    assert b.uinputs[-1].written == [
        (e.EV_KEY, e.BTN_LEFT, 1), (e.EV_REL, e.REL_X, 8), (e.EV_SYN, e.SYN_REPORT, 0),
        (e.EV_REL, e.REL_WHEEL, -1), (e.EV_SYN, e.SYN_REPORT, 0)]


def test_snapping_and_remapping_share_one_virtual_device(aerox):
    r, b = aerox
    r._snap_degrees = 10.0
    r._mapping = {e.KEY_1: e.KEY_F13}
    r.reconcile()
    assert all(s.grabbed for s in r._sources.values()) and len(r._sources) == 2
    assert len(b.uinputs) == 1
    caps = b.uinputs[-1].caps
    assert e.KEY_F13 in caps[e.EV_KEY] and e.REL_X in caps[e.EV_REL]


def test_set_angle_snapping_starts_and_stops_thread(aerox):
    r, b = aerox
    r.set_angle_snapping(10)
    import time
    for _ in range(100):
        if r._sources:
            break
        time.sleep(0.01)
    ptr = src(r, "/dev/input/event5")
    assert ptr.grabbed
    r.set_angle_snapping(0)  # off -> thread exits and releases the mouse
    r._thread.join(timeout=2)
    assert ptr.closed and not ptr.grabbed


def test_snapper_stays_snapped_through_a_small_wobble():
    s = AngleSnapper(10)
    stroke(s, [(2, 0)] * 40)
    # ~12 degrees for a while: above the angle, below the 15 degree release.
    wobble = [s.filter(5, 1, 0.04 + i * 0.001) for i in range(40)]
    assert all(dy == 0 for _, dy in wobble)
    # A real turn (45 degrees) is let through.
    turn = [s.filter(2, 2, 0.08 + i * 0.001) for i in range(40)]
    assert turn[-1] == (2, 2)


# -- click macros ------------------------------------------------------------


def test_triple_click_macro(aerox, monkeypatch):
    monkeypatch.setattr(remap, "CLICK_HOLD", 0)
    monkeypatch.setattr(remap, "CLICK_GAP", 0)
    r, b = aerox
    r.start = lambda: None
    r.set_mapping({"KEY_1": "MACRO_TRIPLE_CLICK"})
    assert r._mapping == {e.KEY_1: (e.BTN_LEFT, 3)}
    r.reconcile()
    ui = b.uinputs[-1]
    assert e.BTN_LEFT in ui.caps[e.EV_KEY] and e.REL_X in ui.caps[e.EV_REL]
    kb = src(r, "/dev/input/event6")
    kb.queue = [ev(e.EV_KEY, e.KEY_1, 1), ev(e.EV_SYN, 0, 0),
                ev(e.EV_KEY, e.KEY_1, 2), ev(e.EV_KEY, e.KEY_1, 0), ev(e.EV_SYN, 0, 0)]
    r.process(kb)
    import time
    for _ in range(200):
        clicks = [w for w in ui.written if w[0] == e.EV_KEY]
        if len(clicks) == 6:
            break
        time.sleep(0.005)
    # press/release three times; the source key itself (press, repeat,
    # release) is never forwarded
    assert clicks == [(e.EV_KEY, e.BTN_LEFT, v) for v in (1, 0) * 3]
    assert all(w[1] != e.KEY_1 for w in ui.written)


def test_macro_names():
    names = remap.all_key_names()
    assert names.index("MACRO_TRIPLE_CLICK") < names.index("KEY_F13")
    assert remap.pretty("MACRO_TRIPLE_CLICK") == "Triple left click"
