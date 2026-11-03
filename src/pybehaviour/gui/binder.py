"""Bind Qt widgets to SessionParams fields by name.

The layout is written by hand (main_window.py); this only removes the
read/write boilerplate: ``binder.spin("min_iti")`` creates a spin box for that
field, and ``binder.read(params)`` returns a new SessionParams with every
bound widget's value.
"""

from __future__ import annotations

import dataclasses
from typing import Callable

from PySide6 import QtWidgets

from ..params import SessionParams

_TYPES = {f.name: f.type for f in dataclasses.fields(SessionParams)}
NEGATIVE_OK = {f for f in _TYPES if "amp" in f}


class Binder:
    def __init__(self, on_change: Callable[[], None]):
        self.widgets: dict[str, QtWidgets.QWidget] = {}
        self._on_change = on_change

    def spin(self, name: str, decimals: int = 2, maximum: float = 1e7, step: float = 1.0) -> QtWidgets.QAbstractSpinBox:
        minimum = -maximum if name in NEGATIVE_OK else 0
        if _TYPES[name] == "int":
            w = QtWidgets.QSpinBox()
            w.setRange(int(minimum), int(min(maximum, 2**31 - 1)))
        else:
            w = QtWidgets.QDoubleSpinBox()
            w.setDecimals(decimals)
            w.setRange(minimum, maximum)
            w.setSingleStep(step)
        w.setKeyboardTracking(False)  # emit on Enter / focus out, not every keystroke
        w.valueChanged.connect(lambda *_: self._on_change())
        self.widgets[name] = w
        return w

    def check(self, name: str, text: str) -> QtWidgets.QCheckBox:
        w = QtWidgets.QCheckBox(text)
        w.toggled.connect(lambda *_: self._on_change())
        self.widgets[name] = w
        return w

    def toggle_button(self, name: str, text: str) -> QtWidgets.QPushButton:
        w = QtWidgets.QPushButton(text)
        w.setCheckable(True)
        w.toggled.connect(lambda *_: self._on_change())
        self.widgets[name] = w
        return w

    def line(self, name: str) -> QtWidgets.QLineEdit:
        w = QtWidgets.QLineEdit()
        w.editingFinished.connect(self._on_change)
        self.widgets[name] = w
        return w

    def combo(self, name: str, items: list[str]) -> QtWidgets.QComboBox:
        w = QtWidgets.QComboBox()
        w.addItems(items)
        w.currentTextChanged.connect(lambda *_: self._on_change())
        self.widgets[name] = w
        return w

    # ------------------------------------------------------------------
    def read(self, base: SessionParams) -> SessionParams:
        values = {}
        for name, w in self.widgets.items():
            if isinstance(w, (QtWidgets.QCheckBox, QtWidgets.QPushButton)):
                values[name] = w.isChecked()
            elif isinstance(w, QtWidgets.QComboBox):
                values[name] = w.currentText()
            elif isinstance(w, QtWidgets.QLineEdit):
                values[name] = w.text()
            else:
                values[name] = w.value()
        return base.copy(**values)

    def write(self, params: SessionParams) -> None:
        for name, w in self.widgets.items():
            v = getattr(params, name)
            w.blockSignals(True)
            if isinstance(w, (QtWidgets.QCheckBox, QtWidgets.QPushButton)):
                w.setChecked(bool(v))
            elif isinstance(w, QtWidgets.QComboBox):
                w.setCurrentText(str(v))
            elif isinstance(w, QtWidgets.QLineEdit):
                w.setText(str(v))
            else:
                w.setValue(v)
            w.blockSignals(False)
