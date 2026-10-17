from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFormLayout, QFrame, QHBoxLayout, QInputDialog, QLabel,
    QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox, QPushButton,
    QScrollArea, QSplitter, QSystemTrayIcon, QTabWidget, QToolBar, QVBoxLayout,
    QWidget,
)

from .. import autostart, model
from ..backends import discover_all
from ..model import Backend, Device
from ..profiles import ProfileStore, apply_profile
from .widgets import SettingEditor, make_editor

GROUP_ORDER = [model.PERFORMANCE, model.LIGHTING, model.SCREEN, model.MAPPING, model.POWER,
               model.GENERAL]
INFO_REFRESH_MS = 60_000  # battery level of wireless devices
ACCENT = "#ff5200"


def app_icon() -> QIcon:
    pix = QPixmap(64, 64)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(ACCENT))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(4, 4, 56, 56, 14, 14)
    p.setPen(QColor("white"))
    p.setFont(QFont("Sans", 30, QFont.Bold))
    p.drawText(pix.rect(), Qt.AlignCenter, "S")
    p.end()
    return QIcon(pix)


class DevicePanel(QWidget):
    """Settings of a single device, one tab per setting group."""

    def __init__(self, device: Device, window: "MainWindow"):
        super().__init__()
        self.device = device
        self.window_ = window
        self.editors: dict[str, SettingEditor] = {}
        lay = QVBoxLayout(self)

        title = QLabel(device.name)
        f = title.font()
        f.setPointSize(f.pointSize() + 5)
        f.setBold(True)
        title.setFont(f)
        lay.addWidget(title)
        self.info_label = QLabel()
        self.info_label.setStyleSheet("color: palette(placeholder-text);")
        lay.addWidget(self.info_label)
        self.refresh_info()
        self._info_timer = QTimer(self, interval=INFO_REFRESH_MS, timeout=self.refresh_info)
        self._info_timer.start()

        tabs = QTabWidget()
        values = window.store.device_values(window.store.active, device.id)
        by_group: dict[str, list[model.Setting]] = {}
        for spec in device.settings():
            by_group.setdefault(spec.group, []).append(spec)
        for group in sorted(by_group, key=lambda g: GROUP_ORDER.index(g) if g in GROUP_ORDER else 99):
            page = QWidget()
            form = QFormLayout(page)
            form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
            form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
            for spec in by_group[group]:
                editor = make_editor(spec, values.get(spec.key, spec.default), device)
                editor.changed.connect(lambda v, k=spec.key: window.on_setting_changed(device, k, v))
                self.editors[spec.key] = editor
                form.addRow(spec.label, editor)
            scroll = QScrollArea(widgetResizable=True, frameShape=QFrame.NoFrame)
            scroll.setWidget(page)
            tabs.addTab(scroll, group)
        lay.addWidget(tabs, 1)

        row = QHBoxLayout()
        reset = QPushButton("Reset to defaults")
        reset.clicked.connect(lambda: window.reset_device(device))
        row.addWidget(reset)
        row.addStretch()
        if device.supports_onboard_save:
            save = QPushButton("Save to device memory")
            save.setToolTip("Persist current settings in the mouse so they survive "
                            "replugging and work on other computers")
            save.clicked.connect(lambda: window.save_onboard(device))
            row.addWidget(save)
        lay.addLayout(row)

    def refresh_info(self) -> None:
        info = self.device.info()
        self.info_label.setText("   ·   ".join(f"{k}: {v}" for k, v in info.items()))
        self.info_label.setVisible(bool(info))


class MainWindow(QMainWindow):
    def __init__(self, backends: list[Backend], store: ProfileStore, use_tray: bool = True):
        super().__init__()
        self.backends = backends
        self.store = store
        self.devices: list[Device] = []
        self.panel: DevicePanel | None = None
        self.setWindowTitle("SteelPanel")
        self.setWindowIcon(app_icon())
        self.resize(900, 600)

        # Toolbar: profile management.
        tb = QToolBar("Profiles", movable=False)
        self.addToolBar(tb)
        tb.addWidget(QLabel(" Profile: "))
        self.profile_combo = QComboBox(minimumWidth=160)
        self.profile_combo.activated.connect(lambda _: self.switch_profile(self.profile_combo.currentText()))
        tb.addWidget(self.profile_combo)
        tb.addAction("New…", self.new_profile)
        tb.addAction("Rename…", self.rename_profile)
        tb.addAction("Delete", self.delete_profile)
        tb.addSeparator()
        tb.addAction("Re-apply", lambda: self.switch_profile(self.store.active))
        tb.addAction("Rescan devices", lambda: self.refresh_devices())
        tb.addSeparator()
        self.autostart_action = QAction("Start at login", self, checkable=True,
                                        checked=autostart.is_enabled())
        self.autostart_action.setToolTip("Start SteelPanel in the system tray when you log in, "
                                         "so remapping, angle snapping and the keyboard screen "
                                         "work without opening it")
        self.autostart_action.toggled.connect(self.set_autostart)
        tb.addAction(self.autostart_action)

        # Body: device list | settings.
        splitter = QSplitter()
        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)
        self.device_list = QListWidget()
        self.device_list.setStyleSheet("QListWidget::item { padding: 8px 6px; }")
        self.device_list.currentRowChanged.connect(self._show_device)
        left_lay.addWidget(self.device_list, 1)
        self.problems = QLabel(wordWrap=True)
        self.problems.setStyleSheet(
            "QLabel { background: rgba(255,82,0,0.12); border: 1px solid rgba(255,82,0,0.5);"
            " border-radius: 6px; padding: 8px; }")
        self.problems.setTextInteractionFlags(Qt.TextSelectableByMouse)
        left_lay.addWidget(self.problems)
        splitter.addWidget(left)

        self.right = QWidget()
        self.right_lay = QVBoxLayout(self.right)
        splitter.addWidget(self.right)
        splitter.setSizes([260, 640])
        self.setCentralWidget(splitter)

        self.tray = None
        if use_tray and QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(app_icon(), self)
            self.tray.setToolTip("SteelPanel")
            self.tray.activated.connect(
                lambda reason: self.show_and_raise() if reason == QSystemTrayIcon.Trigger else None)
            self.tray.show()

        self._reload_profiles()
        self.refresh_devices(apply=True)

    # -- devices -------------------------------------------------------------
    def refresh_devices(self, apply: bool = True) -> None:
        for dev in self.devices:
            dev.close()
        found = discover_all(self.backends)
        self.devices = found.devices
        self.problems.setText("\n\n".join(found.problems))
        self.problems.setVisible(bool(found.problems))
        if apply:
            self._report(apply_profile(self.store, self.store.active, self.devices))
        self.device_list.clear()
        for dev in self.devices:
            item = QListWidgetItem(f"{dev.name}\n{dev.kind.capitalize()}")
            self.device_list.addItem(item)
        if self.devices:
            self.device_list.setCurrentRow(0)
        else:
            self._show_device(-1)
        self.statusBar().showMessage(f"{len(self.devices)} device(s) found", 4000)

    def _show_device(self, row: int) -> None:
        self.panel = None
        while self.right_lay.count():
            w = self.right_lay.takeAt(0).widget()
            if w:
                w.hide()  # deleteLater is deferred; hide now to avoid overlap
                w.deleteLater()
        if 0 <= row < len(self.devices):
            self.panel = DevicePanel(self.devices[row], self)
            self.right_lay.addWidget(self.panel)
        else:
            empty = QLabel("No SteelSeries device detected.\n\n"
                           "Plug in a device and click “Rescan devices”.",
                           alignment=Qt.AlignCenter)
            self.right_lay.addWidget(empty)

    def on_setting_changed(self, device: Device, key: str, value: Any) -> None:
        try:
            device.apply(key, value)
        except Exception as exc:
            self.statusBar().showMessage(f"⚠ {device.name}: {exc}", 8000)
            return
        spec = next(s for s in device.settings() if s.key == key)
        if spec.kind != model.ACTION:  # one-shot commands are not profile state
            self.store.set_value(self.store.active, device.id, key, value)
            self.store.save()
        self.statusBar().showMessage(f"{device.name}: updated", 2000)

    def reset_device(self, device: Device) -> None:
        self.store.get(self.store.active).pop(device.id, None)
        errors = []
        for spec in device.settings():
            if spec.kind == model.ACTION or spec.default is None:
                continue
            try:
                device.apply(spec.key, spec.default)
            except Exception as exc:
                errors.append(f"{spec.label}: {exc}")
        self.store.save()
        self._report(errors)
        self._show_device(self.device_list.currentRow())

    def save_onboard(self, device: Device) -> None:
        try:
            device.save_onboard()
            self.statusBar().showMessage(f"Saved to {device.name}'s memory", 4000)
        except Exception as exc:
            QMessageBox.warning(self, "Save failed", str(exc))

    def set_autostart(self, enabled: bool) -> None:
        try:
            autostart.set_enabled(enabled)
        except OSError as exc:
            QMessageBox.warning(self, "Start at login", f"Could not change the autostart entry: {exc}")
            self.autostart_action.blockSignals(True)
            self.autostart_action.setChecked(autostart.is_enabled())
            self.autostart_action.blockSignals(False)
            return
        self.statusBar().showMessage(
            "SteelPanel will start in the tray at login" if enabled
            else "SteelPanel won't start at login", 4000)

    # -- profiles ------------------------------------------------------------
    def _reload_profiles(self) -> None:
        self.profile_combo.clear()
        self.profile_combo.addItems(self.store.names())
        self.profile_combo.setCurrentText(self.store.active)
        if self.tray:
            menu = QMenu(self)
            group = QActionGroup(menu)
            for name in self.store.names():
                act = QAction(name, menu, checkable=True, checked=name == self.store.active)
                act.triggered.connect(lambda _=False, n=name: self.switch_profile(n))
                group.addAction(act)
                menu.addAction(act)
            menu.addSeparator()
            menu.addAction(self.autostart_action)
            menu.addAction("Show SteelPanel", self.show_and_raise)
            menu.addAction("Quit", self._quit)
            self.tray.setContextMenu(menu)
            self._tray_menu = menu  # keep a reference

    def switch_profile(self, name: str) -> None:
        self.store.active = name
        self.store.save()
        self._report(apply_profile(self.store, name, self.devices))
        self._reload_profiles()
        self._show_device(self.device_list.currentRow())
        self.statusBar().showMessage(f"Profile “{name}” applied", 3000)

    def new_profile(self) -> None:
        name, ok = QInputDialog.getText(self, "New profile",
                                        "Name (starts as a copy of the current profile):")
        if ok and name:
            try:
                self.store.create(name, copy_from=self.store.active)
            except ValueError as exc:
                QMessageBox.warning(self, "New profile", str(exc))
                return
            self.switch_profile(name.strip())

    def rename_profile(self) -> None:
        old = self.store.active
        name, ok = QInputDialog.getText(self, "Rename profile", "New name:", text=old)
        if ok and name and name != old:
            try:
                self.store.rename(old, name)
            except ValueError as exc:
                QMessageBox.warning(self, "Rename profile", str(exc))
                return
            self.store.save()
            self._reload_profiles()

    def delete_profile(self) -> None:
        name = self.store.active
        if QMessageBox.question(self, "Delete profile", f"Delete profile “{name}”?") != QMessageBox.Yes:
            return
        try:
            self.store.delete(name)
        except ValueError as exc:
            QMessageBox.warning(self, "Delete profile", str(exc))
            return
        self.switch_profile(self.store.active)

    # -- misc ----------------------------------------------------------------
    def _report(self, errors: list[str]) -> None:
        if errors:
            self.statusBar().showMessage("⚠ " + " | ".join(errors), 10000)

    def show_and_raise(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _quit(self) -> None:
        self.tray = None
        self.close()
        QApplication.quit()

    def closeEvent(self, event) -> None:
        if self.tray is not None and self.tray.isVisible():
            self.hide()  # keep running in the tray for quick profile switching
            event.ignore()
            return
        for dev in self.devices:
            dev.close()
        super().closeEvent(event)
