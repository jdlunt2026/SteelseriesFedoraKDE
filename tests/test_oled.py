from datetime import datetime

import pytest

pytest.importorskip("PySide6")

from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from steelpanel import model, oled
from steelpanel.backends.screen import KeyboardScreen, ScreenBackend

W, H = oled.WIDTH, oled.HEIGHT


@pytest.fixture(autouse=True)
def app():
    return QApplication.instance() or QApplication([])


def frame_with(*pixels):
    f = bytearray(W * H)
    for x, y in pixels:
        f[y * W + x] = 1
    return bytes(f)


def lit(frame):
    return sum(frame)


# -- encoding ----------------------------------------------------------------


def test_apex_pro_gen3_report_matches_gg_framing():
    (report,) = oled.encode(oled.MODELS[0x1640], frame_with((0, 0), (5, 9), (127, 39)))
    # Report ID 0 + 642 bytes of data, as GG sends on interface 1.
    assert len(report) == 643 and report[0] == 0x00 and report[1] == 0x61
    data = report[2:642]
    assert data[0] == 0b1                       # (0, 0): page 0, bit 0
    assert data[128 + 5] == 0b10                # (5, 9): page 1, bit 1
    assert data[4 * 128 + 127] == 0b10000000    # (127, 39): page 4, bit 7
    assert sum(1 for b in data if b) == 3 and report[642] == 0


def test_legacy_rows_layout():
    (report,) = oled.encode(oled.MODELS[0x1614], frame_with((1, 0), (8, 2)))
    assert len(report) == 642 and report[0] == 0x61
    assert report[1] == 0b01000000              # (1, 0): MSB first
    assert report[1 + 2 * 16 + 1] == 0b10000000  # (8, 2)


def test_chunked_layout_splits_pages():
    frame = frame_with((0, 0), (5, 9), (127, 39))
    reports = oled.encode(oled.MODELS[0x1644], frame)
    assert len(reports) == 8 and all(len(r) == 641 for r in reports)
    assert [r[0] for r in reports] == [0x4C] * 8 and reports[1][1] == 0x01
    assert [r[2] | r[3] << 8 for r in reports] == list(range(0, 640, 80))
    joined = b"".join(r[6:86] for r in reports)
    (single,) = oled.encode(oled.MODELS[0x1640], frame)
    assert joined == single[2:642]


def test_encode_rejects_wrong_size():
    with pytest.raises(ValueError):
        oled.encode(oled.MODELS[0x1640], b"\x00" * 10)


# -- rendering ---------------------------------------------------------------


def test_render_text_and_clock():
    assert lit(oled.render_text("Hello")) > 50
    assert oled.render_text("") == oled.BLANK
    a = oled.render_clock(datetime(2026, 1, 1, 12, 34))
    b = oled.render_clock(datetime(2026, 1, 1, 12, 35))
    assert lit(a) > 100 and a != b
    assert oled.render_clock(datetime(2026, 1, 1, 9, 5), "12-hour", show_date=False) != a


def test_long_text_still_fits():
    frame = oled.render_text("The quick brown fox jumps over the lazy dog")
    assert lit(frame) > 50


def test_render_image_dithers_gray(tmp_path):
    img = QImage(256, 80, QImage.Format_RGB32)
    img.fill(QColor(128, 128, 128))
    path = tmp_path / "gray.png"
    img.save(str(path))
    frame = oled.render_image(str(path))
    assert 0.3 < lit(frame) / (W * H) < 0.7  # mid gray -> about half the pixels
    assert lit(oled.render_image(str(tmp_path / "missing.png"))) > 0  # error text


def test_render_meters_fill_with_value():
    empty = oled.render_meters([("CPU", 0.0)])
    full = oled.render_meters([("CPU", 1.0)])
    assert lit(full) > lit(empty) + 100


def test_system_stats_in_range():
    stats = oled.SystemStats()
    assert 0.0 <= stats.cpu() <= 1.0 and 0.0 <= stats.memory() <= 1.0


# -- runner and device -------------------------------------------------------


class FakeScreen:
    def __init__(self, product_id=0x1640, fail=False):
        self.model = oled.MODELS[product_id]
        self.product_id = product_id
        self.frames, self.closed, self.fail = [], False, fail

    def show(self, frame):
        if self.fail:
            raise OSError(13, "Permission denied")
        self.frames.append(frame)

    def close(self):
        self.closed = True


def test_runner_sends_only_changes():
    screen = FakeScreen()
    now = [datetime(2026, 1, 1, 12, 0)]
    r = oled.ScreenRunner(screen, clock=lambda: now[0])
    r._config["mode"] = "Clock"
    assert r.tick() and r.tick()
    assert len(screen.frames) == 1
    now[0] = datetime(2026, 1, 1, 12, 1)
    r.tick()
    assert len(screen.frames) == 2
    r._config["mode"] = "Keyboard default"
    assert not r.tick() and len(screen.frames) == 2


def test_runner_reports_permission_problem():
    r = oled.ScreenRunner(FakeScreen(fail=True))
    r._config.update(mode="Text", text="hi")
    r.tick()
    assert "install-udev" in r.status


def test_runner_thread_starts_and_stops():
    screen = FakeScreen()
    r = oled.ScreenRunner(screen, interval=0.01)
    r.configure(text="Hello", mode="Text")
    import time
    for _ in range(200):
        if screen.frames:
            break
        time.sleep(0.01)
    assert screen.frames and lit(screen.frames[-1]) > 0
    r.stop()
    assert screen.closed and r._thread is None
    assert screen.frames[-1] == oled.BLANK  # cleared on exit


def test_keyboard_screen_device():
    screen = FakeScreen()
    dev = KeyboardScreen(screen)
    specs = {s.key: s for s in dev.settings()}
    assert dev.settings()[0].key == "mode"
    assert specs["mode"].default == "Keyboard default" and specs["mode"].group == model.SCREEN
    assert specs["text"].kind == model.TEXT and specs["image"].kind == model.FILE
    with pytest.raises(ValueError):
        dev.apply("mode", "Fireworks")
    dev._runner.configure = lambda **kw: calls.append(kw)
    calls = []
    dev.apply("text", "Hi")
    dev.apply("mode", "Text")
    assert calls == [{"text": "Hi"}, {"mode": "Text"}]
    assert "Support" not in dev.info()  # confirmed on real hardware
    assert KeyboardScreen(FakeScreen(0x1614)).info()["Support"] == "experimental"


def test_backend_finds_one_device_per_keyboard(monkeypatch):
    monkeypatch.setattr(oled, "find_screens", lambda: [(0x1640, b"/dev/hidraw3"), (0x1640, b"/dev/hidraw9")])
    found = ScreenBackend().discover()
    assert [d.name for d in found.devices] == ["SteelSeries Apex Pro Gen 3"]
    assert found.devices[0].id == "screen:Apex Pro Gen 3"


def test_switching_to_keyboard_default_clears_screen():
    screen = FakeScreen()
    r = oled.ScreenRunner(screen, interval=0.01)
    r.configure(text="Hello", mode="Text")
    import time
    for _ in range(200):
        if screen.frames:
            break
        time.sleep(0.01)
    r.configure(mode="Keyboard default")
    r._thread.join(timeout=2)
    assert screen.frames[-1] == oled.BLANK and not screen.closed
