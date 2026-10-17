"""One editor widget per setting kind. Each emits `changed(value)` when the
user commits a new value."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QColorDialog, QComboBox, QCompleter, QDialog,
    QDialogButtonBox, QFileDialog, QFormLayout, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit,
    QPushButton, QSlider, QSpinBox, QTableWidget, QToolButton, QVBoxLayout, QWidget,
)

from .. import model, remap
from ..model import Setting

DEBOUNCE_MS = 300


class SettingEditor(QWidget):
    changed = Signal(object)

    def __init__(self, spec: Setting, parent=None):
        super().__init__(parent)
        self.spec = spec
        self._loading = False

    def set_value(self, value: Any) -> None:
        self._loading = True
        try:
            self._set(value)
        finally:
            self._loading = False

    def value(self) -> Any:
        raise NotImplementedError

    def _set(self, value: Any) -> None:
        raise NotImplementedError

    def _emit(self, *_):
        if not self._loading:
            self.changed.emit(self.value())

    def _debounced(self) -> QTimer:
        timer = QTimer(self, singleShot=True, interval=DEBOUNCE_MS)
        timer.timeout.connect(self._emit)
        return timer


class ChoiceEditor(SettingEditor):
    def __init__(self, spec, parent=None):
        super().__init__(spec, parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.combo = QComboBox()
        for choice in spec.choices:
            self.combo.addItem(str(choice), choice)
        self.combo.currentIndexChanged.connect(self._emit)
        lay.addWidget(self.combo)
        lay.addStretch()

    def value(self):
        return self.combo.currentData()

    def _set(self, value):
        idx = self.combo.findData(value)
        if idx < 0:  # profiles saved as JSON may turn values into strings
            idx = self.combo.findText(str(value))
        if idx >= 0:
            self.combo.setCurrentIndex(idx)


class RangeEditor(SettingEditor):
    def __init__(self, spec, parent=None):
        super().__init__(spec, parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        steps = max(1, (spec.maximum - spec.minimum) // spec.step)
        self.slider = QSlider(Qt.Horizontal, minimum=0, maximum=steps)
        self.spin = QSpinBox(minimum=spec.minimum, maximum=spec.maximum, singleStep=spec.step)
        if spec.unit:
            self.spin.setSuffix(f" {spec.unit}")
        if spec.minimum_label:
            self.spin.setSpecialValueText(spec.minimum_label)
        self.spin.setMinimumWidth(90)
        self._timer = self._debounced()
        self.slider.valueChanged.connect(
            lambda i: self.spin.setValue(spec.minimum + i * spec.step))
        self.spin.valueChanged.connect(self._on_spin)
        lay.addWidget(self.slider, 1)
        lay.addWidget(self.spin)

    def _on_spin(self, v):
        self.slider.blockSignals(True)
        self.slider.setValue((v - self.spec.minimum) // self.spec.step)
        self.slider.blockSignals(False)
        if not self._loading:
            self._timer.start()

    def value(self):
        return self.spin.value()

    def _set(self, value):
        if value is not None:
            self.spin.setValue(int(value))


class ColorEditor(SettingEditor):
    def __init__(self, spec, parent=None):
        super().__init__(spec, parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self._color = QColor("#ff0000")
        self.swatch = QPushButton()
        self.swatch.setFixedSize(64, 26)
        self.swatch.setCursor(Qt.PointingHandCursor)
        self.swatch.clicked.connect(self._pick)
        self.hex = QLabel()
        self.hex.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.swatch)
        lay.addWidget(self.hex)
        self.off = None
        if spec.allow_off:
            self.off = QCheckBox("Off")
            self.off.toggled.connect(self._on_off)
            lay.addWidget(self.off)
        lay.addStretch()
        self._refresh()

    def _refresh(self):
        disabled = self.off is not None and self.off.isChecked()
        self.swatch.setEnabled(not disabled)
        self.swatch.setStyleSheet(
            f"background:{self._color.name()}; border:1px solid palette(mid); border-radius:4px;")
        self.hex.setText("off" if disabled else self._color.name())

    def _pick(self):
        color = QColorDialog.getColor(self._color, self, self.spec.label)
        if color.isValid():
            self._color = color
            self._refresh()
            self._emit()

    def _on_off(self, _):
        self._refresh()
        self._emit()

    def value(self):
        if self.off is not None and self.off.isChecked():
            return None
        return self._color.name()

    def _set(self, value):
        if self.off is not None:
            self.off.setChecked(value is None)
        if value:
            self._color = QColor(value)
        self._refresh()


class DpiEditor(SettingEditor):
    """Editable list of DPI presets (1..max_presets), laid out in a grid so
    five presets still fit a narrow window."""

    COLUMNS = 3

    def __init__(self, spec, parent=None):
        super().__init__(spec, parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(8)
        outer.addLayout(self.grid)
        self.spins: list[QSpinBox] = []
        self._timer = self._debounced()
        buttons = QHBoxLayout()
        self.add_btn = QToolButton(text="+ Add preset", toolTip="Add a DPI preset")
        self.del_btn = QToolButton(text="− Remove", toolTip="Remove the last DPI preset")
        self.add_btn.clicked.connect(lambda: self._add(self.spins[-1].value() if self.spins else 800, emit=True))
        self.del_btn.clicked.connect(self._remove)
        buttons.addWidget(self.add_btn)
        buttons.addWidget(self.del_btn)
        buttons.addStretch()
        outer.addLayout(buttons)

    def _add(self, dpi: int, emit=False):
        spin = QSpinBox(minimum=self.spec.minimum, maximum=self.spec.maximum,
                        singleStep=self.spec.step, suffix=" dpi")
        spin.setMinimumWidth(100)
        spin.setValue(int(dpi))
        spin.valueChanged.connect(lambda *_: None if self._loading else self._timer.start())
        i = len(self.spins)
        self.grid.addWidget(spin, i // self.COLUMNS, i % self.COLUMNS)
        self.spins.append(spin)
        self._update_buttons()
        if emit:
            self._emit()

    def _remove(self):
        if len(self.spins) > 1:
            spin = self.spins.pop()
            self.grid.removeWidget(spin)
            spin.deleteLater()
            self._update_buttons()
            self._emit()

    def _update_buttons(self):
        self.add_btn.setEnabled(len(self.spins) < self.spec.max_presets)
        self.del_btn.setEnabled(len(self.spins) > 1)

    def value(self):
        return [s.value() for s in self.spins]

    def _set(self, value):
        if isinstance(value, (int, float)):
            value = [value]
        value = list(value or [800])[: self.spec.max_presets]
        while self.spins:
            spin = self.spins.pop()
            self.grid.removeWidget(spin)
            spin.deleteLater()
        for dpi in value:
            self._add(dpi)


class ButtonsEditor(SettingEditor):
    """Per-button action pickers; applied together with an explicit button
    because rivalcfg sends the whole mapping in one command."""

    def __init__(self, spec, parent=None):
        super().__init__(spec, parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        form = QFormLayout()
        self.combos: dict[str, QComboBox] = {}
        for name in spec.buttons:
            combo = QComboBox(editable=True)
            combo.addItems(spec.choices)
            combo.setToolTip("Pick an action or type a key name (e.g. F13, PlayPause)")
            self.combos[name] = combo
            form.addRow(name, combo)
        outer.addLayout(form)
        row = QHBoxLayout()
        apply_btn = QPushButton("Apply mapping")
        apply_btn.clicked.connect(self._emit)
        reset_btn = QPushButton("Reset to default")
        reset_btn.clicked.connect(self._reset)
        row.addWidget(apply_btn)
        row.addWidget(reset_btn)
        row.addStretch()
        outer.addLayout(row)

    def _reset(self):
        self.set_value({})
        self._emit()

    def value(self):
        return {name: c.currentText().strip() or "default" for name, c in self.combos.items()}

    def _set(self, value):
        value = value if isinstance(value, dict) else {}
        for name, combo in self.combos.items():
            combo.setCurrentText(value.get(name, "default"))


def key_picker(selected: str) -> QComboBox:
    """Searchable picker of every key / mouse button (type to filter)."""
    combo = QComboBox(editable=True, insertPolicy=QComboBox.NoInsert)
    for name in remap.all_key_names():
        label = remap.pretty(name)
        combo.addItem(label if label == name else f"{label}  ({name})", name)
    completer = combo.completer()
    completer.setFilterMode(Qt.MatchContains)
    completer.setCompletionMode(QCompleter.PopupCompletion)
    idx = combo.findData(selected)
    combo.setCurrentIndex(idx if idx >= 0 else 0)
    return combo


class CaptureDialog(QDialog):
    """Waits for the user to press a button on the mouse."""

    captured = Signal(str)  # emitted from the remapper thread; queued to GUI

    def __init__(self, device, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add button mapping")
        self.device = device
        self.result_name: str | None = None
        lay = QVBoxLayout(self)
        msg = QLabel(f"Press the button on your <b>{device.name}</b> that you want to remap.<br>"
                     "<small>The left button can't be remapped.</small>")
        msg.setWordWrap(True)
        lay.addWidget(msg)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color: #c0392b;")
        lay.addWidget(self.status)
        box = QDialogButtonBox(QDialogButtonBox.Cancel)
        box.rejected.connect(self.reject)
        lay.addWidget(box)
        self.captured.connect(self._on_captured)
        self._poll = QTimer(self, interval=500, timeout=self._show_status)
        self._poll.start()

    def exec(self) -> int:
        try:
            self.device.capture_button(self.captured.emit)
        except Exception as exc:
            self.status.setText(str(exc))
        try:
            return super().exec()
        finally:
            try:
                self.device.capture_button(None)
            except Exception:
                pass

    def _show_status(self):
        self.status.setText(self.device.remap_status())

    def _on_captured(self, name: str):
        self.result_name = name
        self.accept()


class KeymapEditor(SettingEditor):
    """Software remapping table: mouse button -> key/button it sends."""

    def __init__(self, spec, device, parent=None):
        super().__init__(spec, parent)
        self.device = device
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Mouse button", "Sends", ""])
        self.table.verticalHeader().hide()
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.setMinimumHeight(160)
        outer.addWidget(self.table)
        row = QHBoxLayout()
        add = QPushButton("Add mapping…")
        add.clicked.connect(self._add_interactive)
        row.addWidget(add)
        row.addStretch()
        outer.addLayout(row)
        note = QLabel("Remapping is done by SteelPanel and works while it runs "
                      "(closing the window keeps it running in the tray).")
        note.setWordWrap(True)
        note.setStyleSheet("color: palette(placeholder-text);")
        outer.addWidget(note)
        self.problem = QLabel(wordWrap=True)
        self.problem.setStyleSheet("color: #c0392b;")
        outer.addWidget(self.problem)
        self._rows: list[tuple[str, QComboBox]] = []
        self._status_timer = QTimer(self, interval=1000, timeout=self._show_status)
        self._status_timer.start()
        self._show_status()

    def _show_status(self):
        text = self.device.remap_status() if self.device else ""
        self.problem.setText(text)
        self.problem.setVisible(bool(text))

    def _add_row(self, source: str, target: str):
        for i, (src, combo) in enumerate(self._rows):
            if src == source:  # already mapped: just focus it
                combo.setFocus()
                return
        r = self.table.rowCount()
        self.table.insertRow(r)
        label = remap.pretty(source)
        item_label = QLabel(f"  {label}  ({source})  " if label != source else f"  {source}  ")
        item_label.setToolTip(source)
        self.table.setCellWidget(r, 0, item_label)
        combo = key_picker(target)
        combo.currentIndexChanged.connect(self._emit)
        self.table.setCellWidget(r, 1, combo)
        remove = QToolButton(text="Remove")
        remove.clicked.connect(lambda: self._remove(source))
        self.table.setCellWidget(r, 2, remove)
        self._rows.append((source, combo))

    def _remove(self, source: str):
        for i, (src, _) in enumerate(self._rows):
            if src == source:
                self.table.removeRow(i)
                del self._rows[i]
                break
        self._emit()

    def _add_interactive(self):
        dialog = CaptureDialog(self.device, self)
        if dialog.exec() == QDialog.Accepted and dialog.result_name:
            # Start with "Middle click" so the mapping does something visible.
            self._add_row(dialog.result_name, "BTN_MIDDLE")
            self._emit()

    def value(self):
        return {src: combo.currentData() for src, combo in self._rows if combo.currentData()}

    def _set(self, value):
        self.table.setRowCount(0)
        self._rows.clear()
        for src, dst in (value or {}).items():
            self._add_row(src, dst)


class TextEditor(SettingEditor):
    def __init__(self, spec, parent=None):
        super().__init__(spec, parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.edit = QLineEdit()
        self._timer = self._debounced()
        self.edit.textEdited.connect(lambda _: self._timer.start())
        lay.addWidget(self.edit)
        self.count = None
        if spec.max_length:
            self.edit.setMaxLength(spec.max_length)
            self.count = QLabel()
            self.count.setStyleSheet("color: palette(placeholder-text);")
            self.edit.textChanged.connect(self._update_count)
            lay.addWidget(self.count)
            self._update_count()

    def _update_count(self, *_):
        self.count.setText(f"{len(self.edit.text())}/{self.spec.max_length}")

    def value(self):
        return self.edit.text()

    def _set(self, value):
        self.edit.setText(value or "")


class FileEditor(SettingEditor):
    FILTER = "Images (*.png *.jpg *.jpeg *.bmp *.gif *.webp *.svg)"

    def __init__(self, spec, parent=None):
        super().__init__(spec, parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.edit = QLineEdit(readOnly=True, placeholderText="No file chosen")
        browse = QPushButton("Choose…")
        browse.clicked.connect(self._browse)
        self.clear = QPushButton("Clear")
        self.clear.clicked.connect(self._clear)
        self.edit.textChanged.connect(lambda text: self.clear.setEnabled(bool(text)))
        self.clear.setEnabled(False)
        lay.addWidget(self.edit, 1)
        lay.addWidget(browse)
        lay.addWidget(self.clear)

    def _clear(self):
        self.edit.clear()
        self._emit()

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(self, self.spec.label, self.edit.text(), self.FILTER)
        if path:
            self.edit.setText(path)
            self._emit()

    def value(self):
        return self.edit.text()

    def _set(self, value):
        self.edit.setText(value or "")


class ActionEditor(SettingEditor):
    def __init__(self, spec, parent=None):
        super().__init__(spec, parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        btn = QPushButton("Run")
        btn.clicked.connect(lambda: self.changed.emit(None))
        lay.addWidget(btn)
        lay.addStretch()

    def value(self):
        return None

    def _set(self, value):
        pass


EDITORS = {
    model.CHOICE: ChoiceEditor,
    model.RANGE: RangeEditor,
    model.COLOR: ColorEditor,
    model.DPI: DpiEditor,
    model.BUTTONS: ButtonsEditor,
    model.ACTION: ActionEditor,
    model.TEXT: TextEditor,
    model.FILE: FileEditor,
}


def make_editor(spec: Setting, value: Any, device=None) -> SettingEditor:
    if spec.kind == model.KEYMAP:
        editor = KeymapEditor(spec, device)
    else:
        editor = EDITORS[spec.kind](spec)
    editor.set_value(value)
    if spec.description:
        editor.setToolTip(spec.description)
    return editor
