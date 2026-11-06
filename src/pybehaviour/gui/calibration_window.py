"""Coil calibration window (port of calibration_coil.m), opened from Tools ▸ Coil calibration.

Plays the task's whisker pulse at each amplitude, records the teslameter, and
shows the field per trial, the field vs command voltage with a fitted mT/V,
and a results table. "Use for whisker amplitudes" fills the mT column of the
main window from the measurement.
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtWidgets

from ..calibration import CalibrationPlan, CalibrationRun, output_folder, save_result
from ..rig.config import RigConfig
from . import theme

# ordinal blue ramp (low → high amplitude), lightest step still ≥ 2:1 on the light surface
AMP_RAMP = ["#86b6ef", "#5598e7", "#2a78d6", "#1c5cab", "#104281"]


class CalibrationWindow(QtWidgets.QWidget):
    def __init__(self, main_window, rig_config: RigConfig, rig_factory: Callable, session_running: Callable[[], bool]):
        super().__init__(None)
        self.setWindowTitle("Coil calibration")
        self.main_window = main_window
        self.rig_config = rig_config
        self.rig_factory = rig_factory
        self.session_running = session_running
        self.run: CalibrationRun | None = None
        self.saved_to: str = ""
        self.resize(1200, 760)

        root = QtWidgets.QHBoxLayout(self)
        left = QtWidgets.QVBoxLayout()
        root.addLayout(left, 0)

        who = QtWidgets.QGroupBox("Calibration")
        f = QtWidgets.QFormLayout(who)
        self.name = QtWidgets.QLineEdit()
        self.name.setToolTip("Mouse (or coil) name used in the file names")
        f.addRow("Mouse / coil", self.name)
        self.folder = QtWidgets.QLineEdit(rig_config.calibration.directory)
        browse = QtWidgets.QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.folder)
        row.addWidget(browse)
        f.addRow("Save in", row)
        for w, label in ((self.name, "Mouse / coil"), (self.folder, "Save in")):
            w.setAccessibleName(label)
        left.addWidget(who)

        amps = QtWidgets.QGroupBox("Amplitudes to test (V)")
        g = QtWidgets.QGridLayout(amps)
        self.amp_checks, self.amp_values = [], []
        for i in range(5):
            c = QtWidgets.QCheckBox(f"#{i + 1}")
            v = QtWidgets.QDoubleSpinBox(decimals=3, singleStep=0.1)
            v.setRange(-10, 10)
            v.setAccessibleName(f"Amplitude {i + 1} (V)")
            c.toggled.connect(v.setEnabled)
            g.addWidget(c, i, 0)
            g.addWidget(v, i, 1)
            self.amp_checks.append(c)
            self.amp_values.append(v)
        from_task = QtWidgets.QPushButton("Copy from whisker settings")
        from_task.clicked.connect(self.load_from_task)
        g.addWidget(from_task, 5, 0, 1, 2)
        left.addWidget(amps)

        pulse = QtWidgets.QGroupBox("Pulse and timing")
        f = QtWidgets.QFormLayout(pulse)
        self.spins: dict[str, QtWidgets.QDoubleSpinBox] = {}
        for key, label, default, lo, hi, dec in (
            ("duration_ms", "Pulse duration (ms)", 3, 0.01, 100, 2),
            ("scaling_factor", "Scaling factor", 0.9, 0, 5, 2),
            ("repeats", "Repeats per amplitude", 3, 1, 100, 0),
            ("baseline_ms", "Baseline (ms)", 2000, 0, 60000, 0),
            ("trial_ms", "Trial length (ms)", 4000, 1, 60000, 0),
            ("pause_s", "Pause between trials (s)", 0.5, 0, 60, 2),
            ("sensor_scale", "Teslameter scale (mT/V)", rig_config.calibration.mT_per_volt, 0.001, 1e6, 3),
        ):
            w = QtWidgets.QDoubleSpinBox(decimals=dec)
            w.setRange(lo, hi)
            w.setValue(default)
            w.setAccessibleName(label)
            f.addRow(label, w)
            self.spins[key] = w
        note = QtWidgets.QLabel("Uses the task's whisker waveform, so the mT values are valid for the pulse the mouse gets.",
                                objectName="muted", wordWrap=True)
        f.addRow(note)
        left.addWidget(pulse)

        buttons = QtWidgets.QHBoxLayout()
        self.start_btn = QtWidgets.QPushButton("▶  Start", objectName="primary")
        self.stop_btn = QtWidgets.QPushButton("■  Stop", objectName="danger")
        self.start_btn.clicked.connect(self.start)
        self.stop_btn.clicked.connect(self.stop)
        buttons.addWidget(self.start_btn)
        buttons.addWidget(self.stop_btn)
        left.addLayout(buttons)
        self.progress = QtWidgets.QProgressBar()
        self.progress.setAccessibleName("Calibration progress")
        left.addWidget(self.progress)
        self.status = QtWidgets.QLabel("", wordWrap=True)
        self.status.setAccessibleName("Calibration status")
        left.addWidget(self.status)
        left.addStretch()

        right = QtWidgets.QVBoxLayout()
        root.addLayout(right, 1)
        plots = pg.GraphicsLayoutWidget()
        right.addWidget(plots, 3)
        self.stim_plot = plots.addPlot(row=0, col=0)
        theme.style_plot(self.stim_plot, left="Command (V)")
        self.field_plot = plots.addPlot(row=1, col=0)
        theme.style_plot(self.field_plot, left="Field (mT)", bottom="Time from pulse onset (ms)")
        self.field_plot.setXLink(self.stim_plot)
        self.fit_plot = plots.addPlot(row=0, col=1, rowspan=2)
        theme.style_plot(self.fit_plot, left="Peak field (mT)", bottom="Command amplitude (V)")
        self.fit_scatter = self.fit_plot.plot([], [], pen=None, symbol="o", symbolSize=9, symbolBrush=theme.AUDITORY,
                                              symbolPen=pg.mkPen(theme.SURFACE, width=2))
        self.fit_line = self.fit_plot.plot([], [], pen=theme.pen(theme.TEXT_2, 1.5))
        self.fit_text = pg.TextItem(color=theme.TEXT, anchor=(0, 0))
        self.fit_plot.addItem(self.fit_text)
        plots.ci.layout.setColumnStretchFactor(0, 3)
        plots.ci.layout.setColumnStretchFactor(1, 2)
        self.key = QtWidgets.QLabel("", objectName="muted")
        right.addWidget(self.key)

        self.table = QtWidgets.QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Amplitude (V)", "Mean peak (mT)", "SD (mT)", "Trials"])
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setAccessibleName("Calibration results")
        right.addWidget(self.table, 1)
        self.apply_btn = QtWidgets.QPushButton("Use for whisker amplitudes (fill the mT column)")
        self.apply_btn.clicked.connect(self.apply_to_task)
        right.addWidget(self.apply_btn)

        self._timer = QtCore.QTimer(self, interval=100, timeout=self._poll)
        self._n_drawn = 0
        self.load_from_task()
        self._set_running(False)

    # ------------------------------------------------------------------------
    def plan(self) -> CalibrationPlan:
        amps = [v.value() for c, v in zip(self.amp_checks, self.amp_values) if c.isChecked()]
        s = {k: w.value() for k, w in self.spins.items()}
        return CalibrationPlan(amplitudes_v=amps, repeats=int(s["repeats"]), duration_ms=s["duration_ms"],
                               scaling_factor=s["scaling_factor"], baseline_ms=s["baseline_ms"], trial_ms=s["trial_ms"],
                               pause_s=s["pause_s"], rate=self.rig_config.calibration.rate)

    def load_from_task(self) -> None:
        """Pre-fill from the main window's whisker settings."""
        if self.main_window is None:
            return
        p = self.main_window.params()
        self.name.setText(p.mouse_name)
        self.spins["duration_ms"].setValue(p.wh_stim_duration)
        self.spins["scaling_factor"].setValue(p.wh_scaling_factor)
        slots = p.whisker_amplitudes() if p.wh_stim_amp_range else [(p.wh_stim_amp_1, 0, 1)]
        for i, (c, v) in enumerate(zip(self.amp_checks, self.amp_values)):
            if i < len(slots) and slots[i][0] != 0:
                v.setValue(slots[i][0])
                c.setChecked(True)
            else:
                c.setChecked(False)
            v.setEnabled(c.isChecked())

    def start(self) -> None:
        if self.run is not None and not self.run.done.is_set():
            return
        if self.session_running():
            QtWidgets.QMessageBox.warning(self, "Session running", "Stop the behaviour session before calibrating the coil.")
            return
        plan = self.plan()
        problems = plan.validate()
        if not self.name.text().strip():
            problems.append("Give a mouse or coil name.")
        if problems:
            QtWidgets.QMessageBox.warning(self, "Cannot start", "\n".join(problems))
            return
        try:
            rig = self.rig_factory()
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Rig not available", str(exc))
            return
        self.run = CalibrationRun(rig, plan, self.spins["sensor_scale"].value())
        self._n_drawn = 0
        self.saved_to = ""
        self.stim_plot.clear()
        self.field_plot.clear()
        self.table.setRowCount(0)
        levels = sorted(set(plan.amplitudes_v))
        last = len(AMP_RAMP) - 1
        self._colors = {a: AMP_RAMP[round(i * last / (len(levels) - 1)) if len(levels) > 1 else 2]
                        for i, a in enumerate(levels)}
        self.key.setText("   ".join(f"<span style='color:{c}'>━━</span> {a:g} V" for a, c in self._colors.items()))
        self.key.setTextFormat(QtCore.Qt.RichText)
        self.progress.setRange(0, len(plan.trial_amplitudes()))
        self.progress.setValue(0)
        self.status.setText("Running…")
        self.run.start()
        self._set_running(True)
        self._timer.start()

    def stop(self) -> None:
        if self.run is not None:
            self.run.stop()

    def _set_running(self, running: bool) -> None:
        self.start_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)
        self.apply_btn.setEnabled(not running and self.run is not None and bool(self.run.result.trial_amps))

    def _poll(self) -> None:
        run = self.run
        if run is None:
            return
        n = len(run.result.trial_amps)
        plan = run.plan
        t = (np.arange(plan.n_samples) / plan.rate * 1000) - plan.baseline_ms
        window = (t >= -2) & (t <= plan.duration_ms + 15)
        scale = run.result.mT_per_volt_sensor
        n_base = int(plan.baseline_ms * plan.rate / 1000 * 0.9)
        for i in range(self._n_drawn, n):
            amp = run.result.trial_amps[i]
            color = self._colors.get(amp, theme.AUDITORY)
            flux = run.result.flux_v[i]
            offset = flux[:n_base].mean() if n_base else 0.0
            self.stim_plot.plot(t[window], plan.waveform(amp)[window], pen=theme.pen(color, 1.5))
            self.field_plot.plot(t[window], (flux[window] - offset) * scale, pen=theme.pen(color, 1.5))
        self._n_drawn = n
        self.progress.setValue(n)
        if n:
            self._show_results()
        if run.done.is_set():
            self._timer.stop()
            self._set_running(False)
            if run.error:
                self.status.setText(f"Stopped: {run.error}")
                QtWidgets.QMessageBox.critical(self, "Calibration failed", run.error)
            elif n:
                self._save()

    def _show_results(self) -> None:
        res = self.run.result
        rows = res.summary()
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, text in enumerate((f"{row['amplitude_V']:g}", f"{row['mean_mT']:.2f}", f"{row['sd_mT']:.2f}", str(row["n"]))):
                item = QtWidgets.QTableWidgetItem(text)
                item.setTextAlignment(QtCore.Qt.AlignCenter)
                self.table.setItem(r, c, item)
        x = np.array(res.trial_amps)
        y = res.peak_mT
        self.fit_scatter.setData(x, y)
        fit = res.fit()
        if np.isfinite(fit["mT_per_V"]):
            xs = np.array([min(0.0, x.min()), max(0.0, x.max())])
            self.fit_line.setData(xs, fit["mT_per_V"] * xs)
            r2 = "" if not np.isfinite(fit["r2"]) else f"\nR² = {fit['r2']:.3f}"
            self.fit_text.setText(f"{fit['mT_per_V']:.2f} mT per V{r2}")
            self.fit_text.setPos(0, float(np.nanmax(y)) if y.size else 0)

    def _save(self) -> None:
        res = self.run.result
        date = datetime.now().strftime("%Y%m%d")
        mouse = self.name.text().strip()
        try:
            folder = output_folder(self.folder.text(), mouse, date)
            paths = save_result(res, folder, mouse, date)
        except Exception as exc:
            self.status.setText(f"Not saved: {exc}")
            QtWidgets.QMessageBox.critical(self, "Could not save calibration", str(exc))
            return
        fit = res.fit()
        self.saved_to = str(paths["mat"])
        stopped = "" if len(res.trial_amps) == len(res.plan.trial_amplitudes()) else " (stopped early)"
        self.status.setText(f"Done{stopped}: {fit['mT_per_V']:.2f} mT/V.\nSaved in {folder}")

    def apply_to_task(self) -> None:
        if self.run is None or not self.run.result.trial_amps or self.main_window is None:
            return
        res = self.run.result
        p = self.main_window.params()
        n = 5 if p.wh_stim_amp_range else 1
        changes = {}
        for i in range(1, n + 1):
            amp = getattr(p, f"wh_stim_amp_{i}")
            if amp != 0:
                changes[f"wh_stim_amp_mT_{i}"] = round(res.mT_for(amp), 2)
        if not changes:
            return
        listing = "\n".join(f"#{k.rsplit('_', 1)[1]}: {getattr(p, 'wh_stim_amp_' + k.rsplit('_', 1)[1]):g} V → {v:g} mT"
                            for k, v in changes.items())
        if QtWidgets.QMessageBox.question(self, "Fill whisker mT values", f"Set these whisker amplitudes?\n\n{listing}"
                                          ) != QtWidgets.QMessageBox.Yes:
            return
        self.main_window.set_params(p.copy(**changes))
        self.main_window.remember_mouse_settings()  # the calibration belongs to this mouse
        self.status.setText(self.status.text() + "\nWhisker mT values updated.")

    def _browse(self) -> None:
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Calibration folder", self.folder.text())
        if d:
            self.folder.setText(d)

    def closeEvent(self, event) -> None:
        if self.run is not None and not self.run.done.is_set():
            self.run.stop()
            self.run.join(timeout=10)
        event.accept()
