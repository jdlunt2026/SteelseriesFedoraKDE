from steelpanel import autostart


def test_enable_disable_roundtrip(tmp_path):
    assert not autostart.is_enabled()
    autostart.set_enabled(True)
    entry = tmp_path / "config" / "autostart" / autostart.FILE_NAME
    text = entry.read_text()
    assert "--hidden" in text and "Exec=" in text
    exec_line = next(line for line in text.splitlines() if line.startswith("Exec="))
    assert exec_line.split("=", 1)[1].split()[0].endswith("steelpanel")
    assert autostart.is_enabled()
    autostart.set_enabled(False)
    assert not entry.exists() and not autostart.is_enabled()


def test_recognises_and_removes_entry_made_by_kde(tmp_path):
    d = tmp_path / "config" / "autostart"
    d.mkdir(parents=True)
    kde = d / "steelpanel.desktop"
    kde.write_text("[Desktop Entry]\nType=Application\nExec=steelpanel --hidden\n")
    other = d / "something-else.desktop"
    other.write_text("[Desktop Entry]\nExec=othertool\n")
    assert autostart.is_enabled()
    autostart.set_enabled(False)
    assert not kde.exists() and other.exists()


def test_disabled_entries_dont_count(tmp_path):
    d = tmp_path / "config" / "autostart"
    d.mkdir(parents=True)
    (d / autostart.FILE_NAME).write_text("[Desktop Entry]\nExec=steelpanel --hidden\nHidden=true\n")
    assert not autostart.is_enabled()


def test_window_toggle(tmp_path, monkeypatch):
    import pytest
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    from steelpanel.gui.main_window import MainWindow
    from steelpanel.profiles import ProfileStore

    app = QApplication.instance() or QApplication([])  # noqa: F841
    w = MainWindow([], ProfileStore(tmp_path / "p.json"), use_tray=False)
    assert not w.autostart_action.isChecked()
    w.autostart_action.setChecked(True)
    assert autostart.is_enabled()
    w.autostart_action.setChecked(False)
    assert not autostart.is_enabled()
    w.close()
