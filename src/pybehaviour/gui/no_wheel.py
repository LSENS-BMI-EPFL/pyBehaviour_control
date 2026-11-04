"""Stop the mouse wheel from changing settings.

By default Qt changes a spin box or drop-down when the wheel turns over it,
so scrolling the settings panel silently edits values. This application-wide
filter blocks that: the wheel scrolls the panel under the cursor instead, and
values change only by typing, the arrow buttons or the arrow keys.
"""

from __future__ import annotations

from PySide6 import QtCore, QtWidgets

VALUE_WIDGETS = (QtWidgets.QAbstractSpinBox, QtWidgets.QComboBox, QtWidgets.QAbstractSlider)


def _value_widget(obj) -> QtWidgets.QWidget | None:
    """The spin box / combo box the event is for (also when it lands on its inner line edit)."""
    if isinstance(obj, QtWidgets.QScrollBar):
        return None  # scroll bars keep scrolling
    if isinstance(obj, VALUE_WIDGETS):
        return obj
    parent = obj.parent() if isinstance(obj, QtWidgets.QLineEdit) else None
    return parent if isinstance(parent, VALUE_WIDGETS) else None


def _scroll_area(widget: QtWidgets.QWidget) -> QtWidgets.QAbstractScrollArea | None:
    p = widget.parentWidget()
    while p is not None and not isinstance(p, QtWidgets.QAbstractScrollArea):
        p = p.parentWidget()
    return p


class NoWheelOnValues(QtCore.QObject):
    def eventFilter(self, obj, event) -> bool:
        if event.type() != QtCore.QEvent.Wheel:
            return False
        widget = _value_widget(obj)
        if widget is None:
            return False
        area = _scroll_area(widget)
        if area is not None:  # scroll the panel instead, as if the cursor were on its background
            bar = area.verticalScrollBar()
            steps = event.angleDelta().y() / 120
            bar.setValue(bar.value() - round(steps * bar.singleStep() * QtWidgets.QApplication.wheelScrollLines()))
        return True  # the value widget never sees the wheel


def install(app: QtWidgets.QApplication) -> NoWheelOnValues:
    filt = NoWheelOnValues(app)  # parented to the app: lives as long as it
    app.installEventFilter(filt)
    return filt
