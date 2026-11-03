"""Optogenetics settings window (port of utils_opto/Opto_GUI.mlapp).

Opens when "Optogenetics" is ticked. Left: pulse train, location and trial
probabilities. Right: the grid on the brain outline (planned locations,
locations already used in the current grid, the next one) and the pulse
train preview. "Assign bregma" unlocks bregma calibration and the
one-trial test pulse, which are only available between sessions.
"""

from __future__ import annotations

from typing import Callable

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtWidgets

from ..modules.opto import (
    BRAIN_OUTLINE_AP, BRAIN_OUTLINE_ML, ENVELOPES, GRIDS, OptoConfig, OptoModule, calibration_pulse,
    galvo_voltages, get_bregma, opto_waveform, set_bregma, stim_grid,
)
from ..rig.config import RigConfig
from . import theme

PLANNED = "#2a78d6"
DONE = "#1baf7a"
NEXT = "#e34948"


class OptoWindow(QtWidgets.QWidget):
    def __init__(self, main_window, rig_config: RigConfig, rig_factory: Callable):
        super().__init__(None)
        self.setWindowTitle("Optogenetics")
        self.main_window = main_window
        self.rig_config = rig_config
        self.rig_factory = rig_factory
        self.module: OptoModule | None = None
        self.resize(1050, 650)

        root = QtWidgets.QHBoxLayout(self)
        left = QtWidgets.QVBoxLayout()
        root.addLayout(left, 0)

        train = QtWidgets.QGroupBox("Pulse train")
        f = QtWidgets.QFormLayout(train)
        self.fields: dict[str, QtWidgets.QDoubleSpinBox] = {}
        for key, label, default, lo, hi, dec in (
            ("baseline_ms", "Baseline (ms)", 200, 0, 1e5, 0),
            ("amplitude", "Amplitude (V)", 0.1, -10, 10, 3),
            ("pulse_width_ms", "Pulse width (ms)", 4, 0, 1e4, 2),
            ("duration_ms", "Train duration (ms)", 500, 0, 1e5, 0),
            ("frequency", "Frequency (Hz)", 50, 0, 1e4, 1),
        ):
            w = QtWidgets.QDoubleSpinBox(decimals=dec)
            w.setRange(lo, hi)
            w.setValue(default)
            w.setKeyboardTracking(False)
            w.valueChanged.connect(self._changed)
            w.setAccessibleName(label)
            f.addRow(label, w)
            self.fields[key] = w
        self.shape = QtWidgets.QComboBox()
        self.shape.addItems(["Square", "Cosine"])
        self.env = QtWidgets.QComboBox()
        self.env.addItems(["None", "Gaussian", "Ramp down"])
        self.env.setToolTip("Ramp down: last 100 ms ramp to 0 (only at ≥ 40 Hz)")
        for w, label in ((self.shape, "Pulse shape"), (self.env, "Envelope")):
            w.currentIndexChanged.connect(self._changed)
            w.setAccessibleName(label)
            f.addRow(label, w)
        left.addWidget(train)

        where = QtWidgets.QGroupBox("Location (mm from bregma)")
        f = QtWidgets.QFormLayout(where)
        self.grid_box = QtWidgets.QComboBox()
        self.grid_box.addItems(GRIDS)
        self.grid_box.currentIndexChanged.connect(self._changed)
        self.grid_box.setAccessibleName("Grid")
        f.addRow("Grid", self.grid_box)
        for key, label in (("ap", "AP"), ("ml", "ML")):
            w = QtWidgets.QDoubleSpinBox(decimals=2, singleStep=0.25)
            w.setRange(-10, 10)
            w.setKeyboardTracking(False)
            w.valueChanged.connect(self._changed)
            w.setAccessibleName(label)
            w.setToolTip("Single location used when Grid is 'No Grid'")
            f.addRow(label, w)
            self.fields[key] = w
        left.addWidget(where)

        probs = QtWidgets.QGroupBox("Fraction of trials with opto")
        f = QtWidgets.QFormLayout(probs)
        for key, label in (("aud_proba", "Auditory"), ("wh_proba", "Whisker"),
                           ("nostim_proba", "No stimulus"), ("ctrl_proba", "Control (saved only)")):
            w = QtWidgets.QDoubleSpinBox(decimals=2, singleStep=0.1)
            w.setRange(0, 1)
            w.setValue(1)
            w.setKeyboardTracking(False)
            w.valueChanged.connect(self._changed)
            w.setAccessibleName(f"Opto fraction, {label}")
            f.addRow(label, w)
            self.fields[key] = w
        left.addWidget(probs)

        cal = QtWidgets.QGroupBox("Calibration (between sessions)")
        v = QtWidgets.QVBoxLayout(cal)
        self.assign = QtWidgets.QCheckBox("Assign bregma")
        self.set_bregma_btn = QtWidgets.QPushButton("Set bregma here")
        self.set_bregma_btn.setToolTip("Save the galvo voltage of the AP/ML above as this mouse's bregma")
        self.one_trial_btn = QtWidgets.QPushButton("Test: one pulse train now")
        self.one_trial_btn.setToolTip("Play 200 ms off + 500 ms of square pulses at the AP/ML above")
        self.bregma_label = QtWidgets.QLabel("", objectName="muted", wordWrap=True)
        self.assign.toggled.connect(self._update_enabled)
        self.set_bregma_btn.clicked.connect(self._set_bregma)
        self.one_trial_btn.clicked.connect(self._one_trial)
        for w in (self.assign, self.set_bregma_btn, self.one_trial_btn, self.bregma_label):
            v.addWidget(w)
        left.addWidget(cal)
        left.addStretch()

        plots = pg.GraphicsLayoutWidget()
        root.addWidget(plots, 1)
        self.grid_plot = plots.addPlot(row=0, col=0)
        theme.style_plot(self.grid_plot, left="ML (mm)", bottom="AP (mm)")
        self.grid_plot.setAspectLocked(True)
        self.grid_plot.setXRange(-6, 4, padding=0.02)
        self.grid_plot.setYRange(-1, 7, padding=0.02)
        self.grid_plot.setMouseEnabled(False, False)
        legend = self.grid_plot.addLegend(offset=(5, -5), labelTextColor=theme.TEXT_2)
        self.grid_plot.plot(BRAIN_OUTLINE_AP, BRAIN_OUTLINE_ML, pen=theme.pen(theme.TEXT_2, 1.5),
                            fillLevel=None, name="Cortex outline")
        self.grid_plot.plot([0], [0], pen=None, symbol="+", symbolSize=14, symbolPen=theme.pen(theme.TEXT, 2), name="Bregma")
        self.planned = self.grid_plot.plot([], [], pen=None, symbol="o", symbolSize=9, symbolBrush=None,
                                           symbolPen=theme.pen(PLANNED, 1.5), name="Grid location")
        self.done = self.grid_plot.plot([], [], pen=None, symbol="o", symbolSize=11, symbolBrush=DONE,
                                        symbolPen=pg.mkPen(theme.SURFACE, width=2), name="Used in this grid")
        self.next = self.grid_plot.plot([], [], pen=None, symbol="star", symbolSize=24, symbolBrush=NEXT,
                                        symbolPen=pg.mkPen(theme.SURFACE, width=2), name="This trial")
        self.grid_text = pg.TextItem(color=theme.TEXT, anchor=(1, 0))
        self.grid_plot.addItem(self.grid_text)
        self.grid_text.setPos(4, 7)
        _ = legend

        self.train_plot = plots.addPlot(row=1, col=0)
        theme.style_plot(self.train_plot, left="Laser command (V)", bottom="Time from trial start (ms)")
        self.train_curve = self.train_plot.plot(pen=theme.pen(theme.AUDITORY, 1.5))
        plots.ci.layout.setRowStretchFactor(0, 3)
        plots.ci.layout.setRowStretchFactor(1, 1)

        self._timer = QtCore.QTimer(self, interval=200, timeout=self._refresh_progress)
        self._update_enabled()
        self._changed()

    # ------------------------------------------------------------------------------
    def config(self) -> OptoConfig:
        v = {k: w.value() for k, w in self.fields.items()}
        return OptoConfig(grid=self.grid_box.currentText(), pulse_shape=("square", "cosine")[self.shape.currentIndex()],
                          envelope=ENVELOPES[self.env.currentIndex()], **v)

    def attach(self, module: OptoModule | None) -> None:
        """Called by the main window at session start (module) and stop (None)."""
        self.module = module
        running = module is not None and module.enabled
        self.grid_box.setEnabled(not running)  # as in MATLAB: grid is fixed during a session
        for k in ("ap", "ml"):
            self.fields[k].setEnabled(not running)
        if running:
            self.assign.setChecked(False)
            module.set_config(self.config())
            self._timer.start()
        else:
            self._timer.stop()
        self.assign.setEnabled(not running)
        self._update_enabled()
        self._refresh_progress()

    def _changed(self, *_):
        cfg = self.config()
        if self.module is not None and self.module.enabled:
            self.module.set_config(cfg)
        ap, ml = stim_grid(cfg.grid, cfg.ap, cfg.ml)
        self.planned.setData(ap, ml)
        if not (self.module and self.module.enabled):
            self.grid_text.setText(f"{cfg.grid}: {ap.size} location{'s' if ap.size != 1 else ''}")
        trial_ms = self.main_window.params().trial_duration if self.main_window else 2000
        fs = 10_000  # preview resolution
        y = opto_waveform(cfg, trial_ms / 1000, fs)
        self.train_curve.setData(np.arange(y.size) / fs * 1000, y)
        self._show_bregma()

    def _refresh_progress(self):
        m = self.module
        if m is None or not m.enabled or m.target is None or m.sequencer is None:
            self.done.setData([], [])
            self.next.setData([], [])
            return
        t = m.target
        ap, ml = m.sequencer.done(t.kind, t.block)
        self.done.setData(ap, ml)
        if t.is_opto:
            self.next.setData([t.ap], [t.ml])
            grid = "–" if t.grid_no != t.grid_no else f"{t.grid_no:.0f}"
            self.grid_text.setText(f"{ {'nostim': 'No-stim', 'aud': 'Auditory', 'wh': 'Whisker'}[t.kind]} grid {grid}\n"
                                   f"location {t.count}/{m.sequencer.size}")
        else:
            self.next.setData([], [])
            self.grid_text.setText(f"No opto this trial\n(non-opto trials: {t.count})")

    # --- calibration ------------------------------------------------------------------
    def _mouse(self) -> str:
        return self.main_window.params().mouse_name if self.main_window else "PB000"

    def _bregma_dir(self) -> str:
        return self.rig_config.opto.bregma_directory

    def _current_voltages(self) -> tuple[float, float]:
        cfg = self.config()
        bregma = get_bregma(self._bregma_dir(), self._mouse())
        return galvo_voltages(cfg.ap, cfg.ml, bregma, tuple(self.rig_config.opto.galvo_step_v_per_mm))

    def _show_bregma(self):
        try:
            bx, by = get_bregma(self._bregma_dir(), self._mouse())
            self.bregma_label.setText(f"{self._mouse()}: bregma at galvo x={bx:.3f} V, y={by:.3f} V\n"
                                      f"Step {self.rig_config.opto.galvo_step_v_per_mm} V/mm · table: {self._bregma_dir()}")
        except Exception as exc:  # unreachable network drive etc.
            self.bregma_label.setText(f"Cannot read bregma table: {exc}")

    def _update_enabled(self, *_):
        on = self.assign.isChecked() and self.assign.isEnabled()
        self.set_bregma_btn.setEnabled(on)
        self.one_trial_btn.setEnabled(on)

    def _set_bregma(self):
        vx, vy = self._current_voltages()
        mouse = self._mouse()
        old = get_bregma(self._bregma_dir(), mouse)
        msg = f"Save bregma for {mouse} at galvo x={vx:.3f} V, y={vy:.3f} V?"
        if old != (0.0, 0.0):
            msg += f"\n\nThis overwrites the saved value x={old[0]:.3f} V, y={old[1]:.3f} V."
        if QtWidgets.QMessageBox.question(self, "Set bregma", msg) != QtWidgets.QMessageBox.Yes:
            return
        try:
            set_bregma(self._bregma_dir(), mouse, vx, vy)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Could not save bregma", str(exc))
            return
        self.fields["ap"].setValue(0)
        self.fields["ml"].setValue(0)
        self._changed()

    def _one_trial(self):
        cfg = self.config()
        rate = self.rig_config.opto.rate
        laser = calibration_pulse(cfg, rate)
        vx, vy = self._current_voltages()
        out = np.column_stack([laser, np.full(laser.size, vx), np.full(laser.size, vy)])
        try:
            self.rig_factory().play_opto_now(out)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Test pulse failed", str(exc))
