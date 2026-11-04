"""Reward valve calibration window (Tools ▸ Valve calibration).

1. Put a tared tube under the spout.  2. Start: the valve opens N times at a
fixed interval.  3. Weigh the tube and save the weight. The measurement goes
into the rig's valve log; the main window then shows reward volumes from it.
"""

from __future__ import annotations

import time
from typing import Callable

from PySide6 import QtCore, QtWidgets

from ..rig.config import RigConfig
from ..valve_calibration import (
    ValveMeasurement, ValvePlan, ValveRun, append_log, log_path, opening_for_volume, read_log,
)


class ValveWindow(QtWidgets.QWidget):
    def __init__(self, main_window, rig_config: RigConfig, rig_factory: Callable, session_running: Callable[[], bool]):
        super().__init__(None)
        self.setWindowTitle("Valve calibration")
        self.main_window = main_window
        self.rig_config = rig_config
        self.rig_factory = rig_factory
        self.session_running = session_running
        self.run: ValveRun | None = None
        self._t0 = 0.0
        self.resize(680, 820)
        lay = QtWidgets.QVBoxLayout(self)

        steps = QtWidgets.QLabel(
            "1. Put a tared tube under the spout.<br>2. Start: the valve opens the number of times below.<br>"
            "3. Weigh the tube and save the weight (1 mg of water ≈ 1 µL).", wordWrap=True)
        lay.addWidget(steps)

        box = QtWidgets.QGroupBox("Openings")
        f = QtWidgets.QFormLayout(box)
        self.valve_ms = QtWidgets.QDoubleSpinBox(decimals=1, minimum=1, maximum=5000, value=50)
        self.n = QtWidgets.QSpinBox(minimum=1, maximum=10000, value=50)
        self.interval = QtWidgets.QDoubleSpinBox(decimals=2, minimum=0.05, maximum=60, value=1.0, singleStep=0.25)
        for w, label in ((self.valve_ms, "Valve opening (ms)"), (self.n, "Number of openings"),
                         (self.interval, "Interval between openings (s)")):
            w.setAccessibleName(label)
            f.addRow(label, w)
        self.duration = QtWidgets.QLabel("", objectName="muted")
        f.addRow("Takes", self.duration)
        for w in (self.n, self.interval):
            w.valueChanged.connect(self._show_duration)
        lay.addWidget(box)

        row = QtWidgets.QHBoxLayout()
        self.start_btn = QtWidgets.QPushButton("▶  Start", objectName="primary")
        self.stop_btn = QtWidgets.QPushButton("■  Stop", objectName="danger")
        self.start_btn.clicked.connect(self.start)
        self.stop_btn.clicked.connect(self.stop)
        row.addWidget(self.start_btn)
        row.addWidget(self.stop_btn)
        lay.addLayout(row)
        self.progress = QtWidgets.QProgressBar()
        self.progress.setAccessibleName("Openings done")
        self.progress.setFormat("%v / %m openings")
        lay.addWidget(self.progress)
        self.status = QtWidgets.QLabel("", wordWrap=True)
        lay.addWidget(self.status)

        weigh = QtWidgets.QGroupBox("Result")
        f = QtWidgets.QFormLayout(weigh)
        self.weight = QtWidgets.QDoubleSpinBox(decimals=1, minimum=0, maximum=100000, suffix=" mg")
        self.weight.setAccessibleName("Water collected (mg)")
        self.save_btn = QtWidgets.QPushButton("Save measurement")
        self.save_btn.clicked.connect(self.save)
        f.addRow("Water collected", self.weight)
        f.addRow(self.save_btn)
        self.result = QtWidgets.QLabel("", wordWrap=True)
        f.addRow(self.result)
        lay.addWidget(weigh)

        hist = QtWidgets.QGroupBox("Measurements on this rig")
        v = QtWidgets.QVBoxLayout(hist)
        self.table = QtWidgets.QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Date", "Opening (ms)", "Openings", "µL per opening"])
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setMinimumHeight(180)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setAccessibleName("Valve calibration history")
        v.addWidget(self.table)
        target_row = QtWidgets.QHBoxLayout()
        target_row.addWidget(QtWidgets.QLabel("Opening for"))
        self.target = QtWidgets.QDoubleSpinBox(decimals=1, minimum=0.1, maximum=1000, value=5.0, suffix=" µL")
        self.target.setAccessibleName("Target volume (µL)")
        self.target.valueChanged.connect(self._show_suggestion)
        target_row.addWidget(self.target)
        self.suggestion = QtWidgets.QLabel("", objectName="muted")
        target_row.addWidget(self.suggestion, 1)
        self.use_btn = QtWidgets.QPushButton("Use this opening")
        self.use_btn.setToolTip("Set the reward valve opening in the main window")
        self.use_btn.clicked.connect(self.use_suggestion)
        target_row.addWidget(self.use_btn)
        v.addLayout(target_row)
        self.log_label = QtWidgets.QLabel("", objectName="muted", wordWrap=True)
        v.addWidget(self.log_label)
        lay.addWidget(hist, 1)

        self._timer = QtCore.QTimer(self, interval=100, timeout=self._poll)
        self.load_from_task()
        self._show_duration()
        self._refresh_history()
        self._set_running(False)

    # ------------------------------------------------------------------------
    @property
    def log_file(self):
        return log_path(self.rig_config.calibration.directory, self.rig_config.name)

    def load_from_task(self) -> None:
        if self.main_window is not None:
            self.valve_ms.setValue(self.main_window.params().reward_valve_duration)

    def _show_duration(self, *_):
        s = self.n.value() * self.interval.value()
        self.duration.setText(f"about {int(s // 60)} min {s % 60:.0f} s" if s >= 60 else f"about {s:.0f} s")

    def plan(self) -> ValvePlan:
        return ValvePlan(n_openings=self.n.value(), valve_ms=self.valve_ms.value(), interval_s=self.interval.value())

    def start(self) -> None:
        if self.run is not None and not self.run.done.is_set():
            return
        if self.session_running():
            QtWidgets.QMessageBox.warning(self, "Session running", "Stop the behaviour session before calibrating the valve.")
            return
        plan = self.plan()
        problems = plan.validate()
        if problems:
            QtWidgets.QMessageBox.warning(self, "Cannot start", "\n".join(problems))
            return
        try:
            rig = self.rig_factory()
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Rig not available", str(exc))
            return
        self.run = ValveRun(rig, plan)
        self.progress.setRange(0, plan.n_openings)
        self.progress.setValue(0)
        self.result.setText("")
        self._t0 = time.monotonic()
        self.run.start()
        self._set_running(True)
        self._timer.start()

    def stop(self) -> None:
        if self.run is not None:
            self.run.stop()

    def _poll(self) -> None:
        run = self.run
        if run is None:
            return
        self.progress.setValue(run.count)
        left = max(0.0, run.plan.duration_s - (time.monotonic() - self._t0))
        self.status.setText(f"Opening the valve… about {left:.0f} s left")
        if run.done.is_set():
            self._timer.stop()
            self._set_running(False)
            if run.error:
                self.status.setText(f"Stopped: {run.error}")
                QtWidgets.QMessageBox.critical(self, "Valve calibration failed", run.error)
            elif run.count < run.plan.n_openings:
                self.status.setText(f"Stopped after {run.count} openings. Weigh the tube; the result uses {run.count} openings.")
            else:
                self.status.setText(f"Done: {run.count} openings. Weigh the tube and save the weight.")
            self.weight.setFocus()

    def _set_running(self, running: bool) -> None:
        self.start_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)
        can_save = not running and self.run is not None and self.run.count > 0 and self.run.error is None
        self.save_btn.setEnabled(can_save)
        self.weight.setEnabled(can_save)
        for w in (self.valve_ms, self.n, self.interval):
            w.setEnabled(not running)

    def save(self) -> None:
        run = self.run
        if run is None or run.count == 0:
            return
        if self.weight.value() <= 0:
            QtWidgets.QMessageBox.warning(self, "No weight", "Enter the weight of water collected.")
            return
        m = ValveMeasurement(valve_ms=run.plan.valve_ms, n_openings=run.count, interval_s=run.plan.interval_s,
                             total_weight_mg=self.weight.value(), rig=self.rig_config.name)
        try:
            append_log(self.log_file, m)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Could not save", str(exc))
            return
        self.result.setText(f"<b>{m.ul_per_opening:.2f} µL per opening</b> at {m.valve_ms:g} ms ({m.n_openings} openings).")
        self.save_btn.setEnabled(False)
        self._refresh_history()
        if self.main_window is not None:
            self.main_window.refresh_reward_volume()

    def _refresh_history(self) -> None:
        rows = read_log(self.log_file)
        self.table.setRowCount(len(rows))
        for r, row in enumerate(reversed(rows)):  # newest first
            for c, text in enumerate((row["date_time"].replace("T", " "), f"{row['valve_ms']:g}", str(row["n_openings"]),
                                      f"{row['ul_per_opening']:.2f}")):
                item = QtWidgets.QTableWidgetItem(text)
                item.setTextAlignment(QtCore.Qt.AlignCenter)
                self.table.setItem(r, c, item)
        self.log_label.setText(f"Log: {self.log_file}")
        self._rows = rows
        self._show_suggestion()

    def _show_suggestion(self, *_):
        ms = opening_for_volume(getattr(self, "_rows", []), self.target.value())
        self._suggested = ms
        if ms is None:
            self.suggestion.setText("needs measurements at two or more opening times")
        else:
            self.suggestion.setText(f"≈ {ms:.1f} ms (linear fit)")
        self.use_btn.setEnabled(ms is not None and ms > 0 and self.main_window is not None)

    def use_suggestion(self) -> None:
        if self._suggested is None or self.main_window is None:
            return
        ms = round(self._suggested)
        self.main_window.set_params(self.main_window.params().copy(reward_valve_duration=ms))
        self.valve_ms.setValue(ms)
        self.status.setText(f"Reward valve opening set to {ms} ms. Check it with a calibration run.")

    def closeEvent(self, event) -> None:
        if self.run is not None and not self.run.done.is_set():
            self.run.stop()
            self.run.join(timeout=5)
        event.accept()
