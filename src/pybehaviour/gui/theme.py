"""Colours, fonts and plot styling shared by all windows.

Series colours are a fixed categorical order validated for colour-vision
deficiency (dataviz reference palette, light mode): auditory, whisker, light
always get the same hue. Identity is never colour-only: every multi-series
plot has a legend and line-end labels, false alarms are dashed, and the
whisker legend says when whisker trials are not rewarded.
"""

from __future__ import annotations

import pyqtgraph as pg
from PySide6 import QtGui, QtWidgets

# surfaces and ink
SURFACE = "#fcfcfb"
PANEL = "#f3f2ef"
BORDER = "#d9d7d1"
TEXT = "#0b0b0b"
TEXT_2 = "#52514e"
TEXT_3 = "#77756f"
GRID_ALPHA = 0.12
HOVER = "#dce9f9"  # highlighted item in drop-down lists: light tint, text stays dark and readable

# categorical series (fixed order)
AUDITORY = "#2a78d6"  # slot 1 blue
WHISKER = "#eb6834"   # slot 2 orange
LIGHT = "#1baf7a"     # slot 3 aqua (below 3:1 contrast -> always labelled)
FALSE_ALARM = TEXT_2  # neutral ink, dashed
TTL1 = AUDITORY
TTL2 = WHISKER
CONTEXT = LIGHT
THRESHOLD = "#e34948"

# context blocks (lab convention: green = whisker rewarded, purple = not rewarded; always labelled)
CONTEXT_REWARDED = "#1f8a3c"
CONTEXT_NOT_REWARDED = "#7b3fa0"

# status (reserved, always shown with a label)
STATUS = {
    "idle": ("#77756f", "○"),
    "running": ("#1f7a3a", "●"),
    "trial": ("#2a78d6", "▶"),
    "timeout": ("#b26a00", "◔"),
    "paused": ("#b26a00", "❚❚"),
    "stopped": ("#77756f", "■"),
    "error": ("#c0322f", "!"),
}

BASE_POINT_SIZE = 11


def apply_app_style(app: QtWidgets.QApplication) -> None:
    app.setStyle("Fusion")
    font = app.font()
    font.setPointSize(BASE_POINT_SIZE)
    app.setFont(font)
    pal = QtGui.QPalette()
    pal.setColor(QtGui.QPalette.Window, QtGui.QColor(PANEL))
    pal.setColor(QtGui.QPalette.Base, QtGui.QColor("#ffffff"))
    pal.setColor(QtGui.QPalette.AlternateBase, QtGui.QColor(SURFACE))
    pal.setColor(QtGui.QPalette.Text, QtGui.QColor(TEXT))
    pal.setColor(QtGui.QPalette.WindowText, QtGui.QColor(TEXT))
    pal.setColor(QtGui.QPalette.ButtonText, QtGui.QColor(TEXT))
    pal.setColor(QtGui.QPalette.Button, QtGui.QColor("#ffffff"))
    pal.setColor(QtGui.QPalette.Highlight, QtGui.QColor(AUDITORY))
    pal.setColor(QtGui.QPalette.HighlightedText, QtGui.QColor("#ffffff"))
    pal.setColor(QtGui.QPalette.ToolTipBase, QtGui.QColor("#ffffff"))
    pal.setColor(QtGui.QPalette.ToolTipText, QtGui.QColor(TEXT))
    for role in (QtGui.QPalette.Text, QtGui.QPalette.WindowText, QtGui.QPalette.ButtonText):
        pal.setColor(QtGui.QPalette.Disabled, role, QtGui.QColor("#9a988f"))
    app.setPalette(pal)
    app.setStyleSheet(STYLESHEET)
    from . import no_wheel

    if not any(isinstance(c, no_wheel.NoWheelOnValues) for c in app.children()):
        no_wheel.install(app)  # the mouse wheel never changes settings
    pg.setConfigOptions(antialias=True, background=SURFACE, foreground=TEXT_2)


STYLESHEET = f"""
QGroupBox {{ font-weight: 600; border: 1px solid {BORDER}; border-radius: 6px; margin-top: 14px; padding: 8px 6px 6px 6px; background: {SURFACE}; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 8px; padding: 0 4px; color: {TEXT}; }}
QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 6px; background: {PANEL}; }}
QTabBar::tab {{ padding: 6px 12px; margin-right: 2px; border: 1px solid {BORDER}; border-bottom: none;
                border-top-left-radius: 6px; border-top-right-radius: 6px; background: {PANEL}; color: {TEXT_2}; }}
QTabBar::tab:selected {{ background: {SURFACE}; color: {TEXT}; font-weight: 600; }}
QPushButton {{ padding: 6px 12px; border: 1px solid {BORDER}; border-radius: 6px; background: #ffffff; }}
QPushButton:hover {{ border-color: {TEXT_3}; }}
QPushButton:focus, QAbstractSpinBox:focus, QLineEdit:focus, QComboBox:focus, QCheckBox:focus {{ outline: none; border: 2px solid {AUDITORY}; }}
QPushButton:disabled {{ color: #9a988f; background: {PANEL}; }}
QPushButton:checked {{ background: {AUDITORY}; color: white; border-color: {AUDITORY}; }}
QPushButton#primary {{ background: #1f7a3a; color: white; border-color: #1f7a3a; font-weight: 600; }}
QPushButton#primary:disabled {{ background: #b9d3c0; color: #f3f2ef; border-color: #b9d3c0; }}
QPushButton#danger {{ background: #c0322f; color: white; border-color: #c0322f; font-weight: 600; }}
QPushButton#danger:disabled {{ background: #e3b5b3; color: #f3f2ef; border-color: #e3b5b3; }}
QAbstractSpinBox, QLineEdit, QComboBox {{ padding: 3px 6px; border: 1px solid {BORDER}; border-radius: 4px; background: #ffffff; min-height: 22px; }}
QComboBox {{ selection-background-color: {HOVER}; selection-color: {TEXT}; }}
QComboBox QAbstractItemView {{ background: #ffffff; color: {TEXT}; border: 1px solid {BORDER}; outline: none;
                              selection-background-color: {HOVER}; selection-color: {TEXT}; }}
QComboBox QAbstractItemView::item {{ min-height: 24px; padding: 2px 6px; color: {TEXT}; }}
QComboBox QAbstractItemView::item:hover, QComboBox QAbstractItemView::item:selected {{ background: {HOVER}; color: {TEXT}; }}
QLabel#muted {{ color: {TEXT_2}; }}
QLabel#statusPill {{ padding: 6px 14px; border-radius: 14px; color: white; font-weight: 600; }}
QLabel#contextBanner {{ padding: 6px 14px; border-radius: 8px; color: white; font-weight: 700; }}
QFrame#tile {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 8px; }}
QLabel#tileValue {{ font-size: 20pt; font-weight: 600; color: {TEXT}; }}
QLabel#tileLabel {{ color: {TEXT_2}; }}
QFrame#card {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 8px; }}
QScrollArea {{ border: none; background: transparent; }}
"""


def style_plot(plot: pg.PlotItem, left: str = "", bottom: str = "") -> None:
    """Recessive axes and hairline grid."""
    for name in ("left", "bottom"):
        ax = plot.getAxis(name)
        ax.setPen(pg.mkPen(BORDER))
        ax.setTextPen(pg.mkPen(TEXT_2))
        ax.enableAutoSIPrefix(False)
    plot.showGrid(x=True, y=True, alpha=GRID_ALPHA)
    if left:
        plot.setLabel("left", left, color=TEXT_2)
    if bottom:
        plot.setLabel("bottom", bottom, color=TEXT_2)
    plot.hideButtons()
    plot.setMenuEnabled(False)


def pen(color: str, width: float = 2.0, dashed: bool = False) -> QtGui.QPen:
    from PySide6 import QtCore

    p = pg.mkPen(color, width=width)
    if dashed:
        p.setStyle(QtCore.Qt.DashLine)
    return p
