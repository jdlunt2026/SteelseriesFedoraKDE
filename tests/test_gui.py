import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from steelpanel.backends.openrgb import OpenRGBBackend
from steelpanel.backends.rival import RivalBackend
from steelpanel.gui.main_window import MainWindow
from steelpanel.gui.widgets import ColorEditor, DpiEditor
from steelpanel.profiles import ProfileStore

from fake_openrgb import FakeClient, FakeDevice


@pytest.fixture
def window(tmp_path, monkeypatch):
    monkeypatch.setenv("RIVALCFG_PROFILE", "1038:1824")
    app = QApplication.instance() or QApplication([])
    client = FakeClient(FakeDevice())
    backends = [RivalBackend(), OpenRGBBackend(client_factory=lambda *a: client)]
    w = MainWindow(backends, ProfileStore(tmp_path / "p.json"), use_tray=False)
    yield w, app
    w.close()


def test_window_lists_devices_and_edits_persist(window):
    w, app = window
    assert w.device_list.count() == 2
    mouse = w.devices[0]
    dpi = w.panel.editors["sensitivity"]
    assert isinstance(dpi, DpiEditor) and dpi.value() == [800, 1600]
    dpi.changed.emit([400, 1200, 3200])
    assert w.store.device_values("Default", mouse.id)["sensitivity"] == [400, 1200, 3200]

    # A failing apply must not be stored.
    w.on_setting_changed(mouse, "sensitivity", [400] * 9)
    assert w.store.device_values("Default", mouse.id)["sensitivity"] == [400, 1200, 3200]


def test_profile_switch_reloads_editors(window):
    w, app = window
    mouse = w.devices[0]
    w.on_setting_changed(mouse, "logo_color", "#00ff00")
    w.store.create("Night")
    w.switch_profile("Night")
    w.on_setting_changed(mouse, "logo_color", "#110000")
    w.switch_profile("Default")
    app.processEvents()
    editor = w.panel.editors["logo_color"]
    assert isinstance(editor, ColorEditor) and editor.value() == "#00ff00"


def test_keyboard_panel(window):
    w, app = window
    w.device_list.setCurrentRow(1)
    assert set(w.panel.editors) >= {"mode", "brightness", "speed", "color", "zone0"}
