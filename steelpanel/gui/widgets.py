"""One editor widget per setting kind. Each emits `changed(value)` when the
user commits a new value."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QComboBox, QFormLayout, QGridLayout, QHBoxLayout, QLabel,
    QPushButton, QSlider, QSpinBox, QToolButton, QVBoxLayout, QWidget,
)

from .. import model
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
}


def make_editor(spec: Setting, value: Any) -> SettingEditor:
    editor = EDITORS[spec.kind](spec)
    editor.set_value(value)
    if spec.description:
        editor.setToolTip(spec.description)
    return editor
