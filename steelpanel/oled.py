"""OLED screens of SteelSeries Apex keyboards.

The screen is 128x40 pixels, one bit per pixel, written with HID feature
reports. Three report formats exist; the byte layouts follow apex-tux
(https://github.com/not-jan/apex-tux, public domain), and the Apex Pro Gen 3
framing was checked against a USB capture of SteelSeries GG talking to that
keyboard (feature reports with report ID 0 and 642 bytes of data on USB
interface 1).

* ``page``: Apex Pro Gen 3. One report: 0x61, then 5 "pages" of 128 bytes,
  each byte holding 8 vertical pixels (bit 0 at the top), like an SSD1306.
* ``rows``: older Apex Pro / Apex 7 / Apex 5. One report: 0x61, then 40 rows
  of 16 bytes, most significant bit first.
* ``chunked``: Apex Pro TKL Wireless Gen 3. The page layout split over 8
  reports of 80 bytes each, with an offset header.

Frames are rendered with Qt (fonts, images) and stored as a flat
``bytes`` of WIDTH*HEIGHT pixels, 1 = lit, row by row.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

log = logging.getLogger(__name__)

WIDTH, HEIGHT = 128, 40
VENDOR = 0x1038

try:
    import hid
except ImportError:  # pragma: no cover - comes with rivalcfg
    hid = None


@dataclass(frozen=True)
class ScreenModel:
    name: str
    interface: int
    layout: str  # "page", "rows" or "chunked"
    #: Byte prepended to every report as its HID report ID. 0x00 means the
    #: interface has no report IDs (the kernel then sends the rest as-is).
    #: None sends the report's own first byte as the ID, as apex-tux does.
    report_id: int | None = None
    #: Command byte of ``chunked`` reports.
    command: int = 0x0C
    #: Seen working on real hardware with SteelPanel.
    verified: bool = False


MODELS: dict[int, ScreenModel] = {
    0x1640: ScreenModel("Apex Pro Gen 3", interface=1, layout="page", report_id=0x00,
                     verified=True),
    0x1610: ScreenModel("Apex Pro", interface=1, layout="rows"),
    0x1612: ScreenModel("Apex 7", interface=1, layout="rows"),
    0x1614: ScreenModel("Apex Pro TKL", interface=1, layout="rows"),
    0x1618: ScreenModel("Apex 7 TKL", interface=1, layout="rows"),
    0x161C: ScreenModel("Apex 5", interface=1, layout="rows"),
    0x1646: ScreenModel("Apex Pro TKL Wireless Gen 3", interface=3, layout="chunked"),
    0x1644: ScreenModel("Apex Pro TKL Wireless Gen 3", interface=3, layout="chunked",
                        command=0x4C),
}

BLANK = bytes(WIDTH * HEIGHT)


# --------------------------------------------------------------------------
# Report encoding


def _pages(frame: bytes) -> bytearray:
    """Row-major pixels -> 5 pages of 128 column bytes, bit 0 at the top."""
    out = bytearray(WIDTH * HEIGHT // 8)
    for y in range(HEIGHT):
        page, mask = y // 8, 1 << (y % 8)
        row = frame[y * WIDTH:(y + 1) * WIDTH]
        base = page * WIDTH
        for x, lit in enumerate(row):
            if lit:
                out[base + x] |= mask
    return out


def _rows(frame: bytes) -> bytearray:
    """Row-major pixels -> 16 bytes per row, most significant bit first."""
    out = bytearray(WIDTH * HEIGHT // 8)
    for i, lit in enumerate(frame):
        if lit:
            out[i // 8] |= 0x80 >> (i % 8)
    return out


def encode(model: ScreenModel, frame: bytes) -> list[bytes]:
    """The feature reports (as passed to hidapi) that show `frame`."""
    if len(frame) != WIDTH * HEIGHT:
        raise ValueError(f"frame must be {WIDTH}x{HEIGHT} pixels")
    if model.layout == "chunked":
        data = _pages(frame)
        reports = []
        for i in range(8):
            offset = i * 80
            body = bytearray(641)
            body[0:6] = bytes([model.command, 0x01, offset & 0xFF, offset >> 8, 80, 0])
            body[6:86] = data[offset:offset + 80]
            reports.append(body)
    else:
        data = _pages(frame) if model.layout == "page" else _rows(frame)
        reports = [bytes([0x61]) + bytes(data) + b"\x00"]
    if model.report_id is not None:
        reports = [bytes([model.report_id]) + bytes(r) for r in reports]
    return [bytes(r) for r in reports]


# --------------------------------------------------------------------------
# Hardware access


def find_screens() -> list[tuple[int, bytes]]:
    """(product id, hidraw path) of every plugged keyboard with a screen."""
    if hid is None:
        return []
    found = []
    for info in hid.enumerate(VENDOR, 0):
        model = MODELS.get(info["product_id"])
        if model and info.get("interface_number") == model.interface:
            found.append((info["product_id"], info["path"]))
    return found


class HidScreen:
    """Writes frames to one physical screen."""

    def __init__(self, product_id: int, path: bytes):
        self.model = MODELS[product_id]
        self.product_id = product_id
        self.path = path
        self._dev = None

    def show(self, frame: bytes) -> None:
        if self._dev is None:
            dev = hid.device()
            dev.open_path(self.path)  # OSError without the udev rule
            self._dev = dev
        try:
            for report in encode(self.model, frame):
                if self._dev.send_feature_report(report) < 0:
                    raise OSError("the keyboard rejected the screen update")
        except Exception:
            self.close()  # reopen next time (e.g. after replugging)
            raise

    def close(self) -> None:
        if self._dev is not None:
            try:
                self._dev.close()
            except Exception:
                pass
            self._dev = None


# --------------------------------------------------------------------------
# Rendering (needs a QGuiApplication for fonts)


def _qt():
    from PySide6 import QtCore, QtGui
    return QtCore, QtGui


def _canvas():
    QtCore, QtGui = _qt()
    img = QtGui.QImage(WIDTH, HEIGHT, QtGui.QImage.Format_Grayscale8)
    img.fill(0)
    painter = QtGui.QPainter(img)
    painter.setRenderHint(QtGui.QPainter.Antialiasing, False)
    painter.setRenderHint(QtGui.QPainter.TextAntialiasing, False)
    painter.setPen(QtGui.QColor(255, 255, 255))
    return img, painter


def _font(px: int, bold: bool = False):
    _, QtGui = _qt()
    font = QtGui.QFont("Sans")
    font.setPixelSize(px)
    font.setBold(bold)
    font.setStyleStrategy(QtGui.QFont.NoAntialias)
    return font


def _to_frame(img, dither: bool = False) -> bytes:
    """8-bit grayscale QImage of WIDTH x HEIGHT -> frame."""
    px = [[img.pixelColor(x, y).value() for x in range(WIDTH)] for y in range(HEIGHT)]
    if not dither:
        return bytes(1 if v >= 128 else 0 for row in px for v in row)
    err = [[float(v) for v in row] for row in px]
    out = bytearray(WIDTH * HEIGHT)
    for y in range(HEIGHT):  # Floyd-Steinberg
        for x in range(WIDTH):
            old = err[y][x]
            new = 255.0 if old >= 128 else 0.0
            out[y * WIDTH + x] = 1 if new else 0
            e = old - new
            if x + 1 < WIDTH:
                err[y][x + 1] += e * 7 / 16
            if y + 1 < HEIGHT:
                if x > 0:
                    err[y + 1][x - 1] += e * 3 / 16
                err[y + 1][x] += e * 5 / 16
                if x + 1 < WIDTH:
                    err[y + 1][x + 1] += e * 1 / 16
    return bytes(out)


def _fit_text(painter, text: str, rect, max_px: int, bold: bool = False, wrap: bool = True):
    """Draw `text` in the largest font size that fits `rect`."""
    QtCore, QtGui = _qt()
    flags = QtCore.Qt.AlignCenter | (QtCore.Qt.TextWordWrap if wrap else 0)
    for px in range(max_px, 7, -1):
        font = _font(px, bold)
        bounds = QtGui.QFontMetrics(font).boundingRect(rect, flags, text)
        if bounds.width() <= rect.width() and bounds.height() <= rect.height():
            break
    painter.setFont(font)
    painter.drawText(rect, flags, text)


def render_text(text: str) -> bytes:
    QtCore, _ = _qt()
    img, painter = _canvas()
    _fit_text(painter, text or " ", QtCore.QRect(0, 0, WIDTH, HEIGHT), 30)
    painter.end()
    return _to_frame(img)


CLOCK_FORMATS = {"24-hour": "%H:%M", "24-hour with seconds": "%H:%M:%S", "12-hour": "%I:%M %p"}


def render_clock(now: datetime, fmt: str = "24-hour", show_date: bool = True) -> bytes:
    QtCore, _ = _qt()
    img, painter = _canvas()
    text = now.strftime(CLOCK_FORMATS.get(fmt, "%H:%M"))
    if fmt == "12-hour":
        text = text.lstrip("0")
    if show_date:
        _fit_text(painter, text, QtCore.QRect(0, 0, WIDTH, 28), 28, bold=True, wrap=False)
        painter.setFont(_font(10))
        painter.drawText(QtCore.QRect(0, 28, WIDTH, 12), QtCore.Qt.AlignCenter,
                         now.strftime("%a %d %b"))
    else:
        _fit_text(painter, text, QtCore.QRect(0, 0, WIDTH, HEIGHT), 34, bold=True, wrap=False)
    painter.end()
    return _to_frame(img)


def render_image(path: str) -> bytes:
    """Scale an image file to fit the screen (black bars), dithered."""
    QtCore, QtGui = _qt()
    src = QtGui.QImage(path)
    if src.isNull():
        return render_text("Can't open image")
    scaled = src.convertToFormat(QtGui.QImage.Format_Grayscale8).scaled(
        WIDTH, HEIGHT, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
    img, painter = _canvas()
    painter.drawImage((WIDTH - scaled.width()) // 2, (HEIGHT - scaled.height()) // 2, scaled)
    painter.end()
    return _to_frame(img, dither=True)


def render_meters(rows: list[tuple[str, float]]) -> bytes:
    """Labelled bars, e.g. [("CPU", 0.23), ("RAM", 0.41)]."""
    QtCore, QtGui = _qt()
    img, painter = _canvas()
    painter.setFont(_font(11, bold=True))
    h = HEIGHT // max(1, len(rows))
    for i, (label, fraction) in enumerate(rows):
        y = i * h
        fraction = max(0.0, min(1.0, fraction))
        painter.drawText(QtCore.QRect(0, y, 30, h), QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft, label)
        painter.drawText(QtCore.QRect(96, y, 32, h), QtCore.Qt.AlignVCenter | QtCore.Qt.AlignRight,
                         f"{round(fraction * 100)}%")
        bar = QtCore.QRect(31, y + h // 2 - 4, 62, 8)
        painter.drawRect(bar.adjusted(0, 0, -1, -1))
        painter.fillRect(bar.adjusted(2, 2, -2, -2).adjusted(0, 0, -round(58 * (1 - fraction)), 0),
                         QtGui.QColor(255, 255, 255))
    painter.end()
    return _to_frame(img)


def render_test_pattern() -> bytes:
    """Border, corner marks and text: shows orientation and edge pixels."""
    QtCore, QtGui = _qt()
    img, painter = _canvas()
    painter.drawRect(0, 0, WIDTH - 1, HEIGHT - 1)
    painter.fillRect(0, 0, 6, 6, QtGui.QColor(255, 255, 255))  # top-left
    painter.setFont(_font(14, bold=True))
    painter.drawText(QtCore.QRect(0, 2, WIDTH, 22), QtCore.Qt.AlignCenter, "SteelPanel")
    painter.setFont(_font(10))
    painter.drawText(QtCore.QRect(0, 22, WIDTH, 16), QtCore.Qt.AlignCenter, "screen test")
    painter.end()
    return _to_frame(img)


class SystemStats:
    """CPU and memory use from /proc."""

    def __init__(self):
        self._prev = self._cpu_times()

    @staticmethod
    def _cpu_times() -> tuple[int, int] | None:
        try:
            with open("/proc/stat") as f:
                fields = [int(v) for v in f.readline().split()[1:]]
        except (OSError, ValueError):
            return None
        idle = fields[3] + (fields[4] if len(fields) > 4 else 0)
        return idle, sum(fields)

    def cpu(self) -> float:
        now, prev = self._cpu_times(), self._prev
        self._prev = now
        if not now or not prev or now[1] == prev[1]:
            return 0.0
        return 1 - (now[0] - prev[0]) / (now[1] - prev[1])

    @staticmethod
    def memory() -> float:
        try:
            info = {}
            with open("/proc/meminfo") as f:
                for line in f:
                    key, value = line.split(":", 1)
                    info[key] = int(value.split()[0])
            return 1 - info["MemAvailable"] / info["MemTotal"]
        except (OSError, KeyError, ValueError, ZeroDivisionError):
            return 0.0


# --------------------------------------------------------------------------
# What to show, refreshed in the background

MODES = ["Keyboard default", "Clock", "Text", "Image", "System monitor"]


class ScreenRunner:
    """Keeps the screen showing the configured content.

    Renders once a second (only sending frames that changed) in a daemon
    thread while a mode other than "Keyboard default" is selected.
    """

    def __init__(self, screen, clock: Callable[[], datetime] = datetime.now,
                 interval: float = 1.0):
        self.screen = screen
        self.clock = clock
        self.interval = interval
        self.status = ""
        self._config = {"mode": "Keyboard default", "text": "", "image": "",
                        "clock_format": "24-hour", "show_date": "Yes"}
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._last: bytes | None = None
        self._stats = SystemStats()

    def configure(self, **values) -> None:
        with self._lock:
            self._config.update(values)
            active = self._config["mode"] != "Keyboard default"
        self._wake.set()
        if active and not (self._thread and self._thread.is_alive()):
            self._thread = threading.Thread(target=self._loop, name="oled", daemon=True)
            self._thread.start()

    def render(self) -> bytes | None:
        """The frame for the current settings; None for "Keyboard default"."""
        with self._lock:
            cfg = dict(self._config)
        mode = cfg["mode"]
        if mode == "Clock":
            return render_clock(self.clock(), cfg["clock_format"], cfg["show_date"] == "Yes")
        if mode == "Text":
            return render_text(cfg["text"])
        if mode == "Image":
            return render_image(cfg["image"]) if cfg["image"] else render_text("No image chosen")
        if mode == "System monitor":
            return render_meters([("CPU", self._stats.cpu()), ("RAM", self._stats.memory())])
        return None

    def tick(self) -> bool:
        """Render and send if changed. False once there's nothing to show."""
        frame = self.render()
        if frame is None:
            return False
        if frame != self._last:
            try:
                self.screen.show(frame)
                self._last = frame
                self.status = ""
            except OSError as exc:
                self.status = (f"Can't write to the keyboard's screen ({exc}). Run "
                               "'sudo steelpanel --install-udev', then replug the keyboard.")
        return True

    def _loop(self) -> None:
        try:
            while self.tick():
                self._wake.wait(self.interval)
                self._wake.clear()
            self._clear()
        except Exception as exc:
            log.exception("screen updates stopped")
            self.status = f"Screen updates stopped: {exc}"

    def stop(self) -> None:
        with self._lock:
            self._config["mode"] = "Keyboard default"
        self._wake.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)
        self._thread = None
        self._clear()
        self.screen.close()

    def _clear(self) -> None:
        """Blank the screen if we drew on it (don't leave a frozen clock)."""
        if self._last is not None:
            try:
                self.screen.show(BLANK)
            except OSError:
                pass
            self._last = None
