"""Sound calibration window (Tools ▸ Sound calibration), two tabs.

Auditory tone: 1. Put the dB meter where the mouse's head is.  2. Start: the
auditory tone plays every few seconds; change the amplitude until the meter
shows the level you want.  3. Save the reading. The window suggests the
amplitude for a target level from the latest reading at that frequency.

Background noise: play the context task's pink or brown noise continuously and
set each one's gain until the meter shows the same level for both. The gains
are the task settings (Context tab), so they are used by the next session.
"""

from __future__ import annotations

import time
from typing import Callable

from PySide6 import QtCore, QtWidgets

from ..modules.context import SoundDevicePlayer, gains_db
from ..planner import CONTEXTS
from ..rig.config import RigConfig
from ..sound_calibration import (
    SoundReading, TonePlan, ToneRun, amplitude_for_level, append_log, log_path, read_log,
)


class SoundWindow(QtWidgets.QWidget):
    def __init__(self, main_window, rig_config: RigConfig, rig_factory: Callable, session_running: Callable[[], bool]):
        super().__init__(None)
        self.setWindowTitle("Sound calibration")
        self.main_window = main_window
        self.rig_config = rig_config
        self.rig_factory = rig_factory
        self.session_running = session_running
        self.run: ToneRun | None = None
        self._fs = rig_config.stim.rate
        self._suggested: float | None = None
        self._rows: list[dict] = []
        self.noise_player_factory: Callable = SoundDevicePlayer  # (noise folder) -> player; replaced in tests
        self.noise_player = None
        self._noise_player_folder = ""
        self.resize(680, 820)
        outer = QtWidgets.QVBoxLayout(self)
        self.tabs = QtWidgets.QTabWidget()
        outer.addWidget(self.tabs)
        tone_page = QtWidgets.QWidget()
        self.tabs.addTab(tone_page, "Auditory tone")
        lay = QtWidgets.QVBoxLayout(tone_page)

        steps = QtWidgets.QLabel(
            "1. Put the dB meter where the mouse's head is.<br>2. Start: the tone plays every few seconds. "
            "Changes apply from the next tone.<br>3. Enter the level the meter shows and save it.", wordWrap=True)
        lay.addWidget(steps)

        box = QtWidgets.QGroupBox("Tone")
        f = QtWidgets.QFormLayout(box)
        self.amplitude = QtWidgets.QDoubleSpinBox(decimals=3, minimum=0.001, maximum=10, value=1, singleStep=0.05)
        self.freq = QtWidgets.QDoubleSpinBox(decimals=0, minimum=1, maximum=self._fs / 2 - 1, value=10000, singleStep=500)
        self.duration = QtWidgets.QDoubleSpinBox(decimals=1, minimum=1, maximum=10000, value=1000, singleStep=100)
        self.interval = QtWidgets.QDoubleSpinBox(decimals=2, minimum=0.2, maximum=60, value=3, singleStep=0.5)
        for w, label in ((self.amplitude, "Amplitude (V)"), (self.freq, "Frequency (Hz)"),
                         (self.duration, "Duration (ms)"), (self.interval, "Every (s)")):
            w.setAccessibleName(label)
            w.valueChanged.connect(self._plan_changed)
            f.addRow(label, w)
        note = QtWidgets.QLabel(
            "The tone's level does not depend on its duration, but meters with Fast/Slow weighting read short "
            "tones low: measure with a long tone (e.g. 1000 ms) or use the meter's max hold.",
            objectName="muted", wordWrap=True)
        f.addRow(note)
        lay.addWidget(box)

        row = QtWidgets.QHBoxLayout()
        self.start_btn = QtWidgets.QPushButton("▶  Start", objectName="primary")
        self.stop_btn = QtWidgets.QPushButton("■  Stop", objectName="danger")
        self.start_btn.clicked.connect(self.start)
        self.stop_btn.clicked.connect(self.stop)
        row.addWidget(self.start_btn)
        row.addWidget(self.stop_btn)
        lay.addLayout(row)
        self.status = QtWidgets.QLabel("", wordWrap=True)
        lay.addWidget(self.status)

        reading = QtWidgets.QGroupBox("Reading")
        f = QtWidgets.QFormLayout(reading)
        self.db = QtWidgets.QDoubleSpinBox(decimals=1, minimum=0, maximum=200, value=0, suffix=" dB SPL")
        self.db.setAccessibleName("Measured level (dB SPL)")
        self.save_btn = QtWidgets.QPushButton("Save reading")
        self.save_btn.setToolTip("Log this level for the current amplitude and frequency")
        self.save_btn.clicked.connect(self.save)
        f.addRow("Meter shows", self.db)
        f.addRow(self.save_btn)
        self.result = QtWidgets.QLabel("", wordWrap=True)
        f.addRow(self.result)
        lay.addWidget(reading)

        hist = QtWidgets.QGroupBox("Readings on this rig")
        v = QtWidgets.QVBoxLayout(hist)
        self.table = QtWidgets.QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Date", "Amplitude (V)", "Frequency (Hz)", "Duration (ms)", "dB SPL"])
        self.table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setMinimumHeight(180)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setAccessibleName("Sound calibration history")
        v.addWidget(self.table)
        target_row = QtWidgets.QHBoxLayout()
        target_row.addWidget(QtWidgets.QLabel("Amplitude for"))
        self.target = QtWidgets.QDoubleSpinBox(decimals=1, minimum=0, maximum=200, value=70, suffix=" dB SPL")
        self.target.setAccessibleName("Target level (dB SPL)")
        self.target.valueChanged.connect(self._show_suggestion)
        target_row.addWidget(self.target)
        self.suggestion = QtWidgets.QLabel("", objectName="muted", wordWrap=True)
        target_row.addWidget(self.suggestion, 1)
        self.use_btn = QtWidgets.QPushButton("Use for the task")
        self.use_btn.setToolTip("Set the task's auditory amplitude and frequency")
        self.use_btn.clicked.connect(self.use_suggestion)
        target_row.addWidget(self.use_btn)
        v.addLayout(target_row)
        self.log_label = QtWidgets.QLabel("", objectName="muted", wordWrap=True)
        v.addWidget(self.log_label)
        lay.addWidget(hist, 1)

        self.tabs.addTab(self._noise_page(), "Background noise")
        self._timer = QtCore.QTimer(self, interval=100, timeout=self._poll)
        self.load_from_task()
        self._refresh_history()
        self._set_running(False)

    # ------------------------------------------------------------------------
    @property
    def log_file(self):
        return log_path(self.rig_config.calibration.directory, self.rig_config.name)

    def load_from_task(self) -> None:
        """Start from the task's tone (duration stays long, for the meter) and noise gains."""
        if self.main_window is not None:
            p = self.main_window.params()
            if p.aud_stim_amp > 0:
                self.amplitude.setValue(p.aud_stim_amp)
            if p.aud_stim_freq > 0:
                self.freq.setValue(p.aud_stim_freq)
            for name, db in gains_db(p).items():
                self.noise_gain[name].blockSignals(True)
                self.noise_gain[name].setValue(db)
                self.noise_gain[name].blockSignals(False)
            self.noise_folder.setText(f"Noise folder: {p.bckg_noise_directory or '(not set: Context tab)'}")

    # --- background noise ------------------------------------------------------
    def _noise_page(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(page)
        v.addWidget(QtWidgets.QLabel(
            "1. Put the dB meter where the mouse's head is.<br>2. Play one noise, read the meter, then the other.<br>"
            "3. Change the gains until both read the same. They are the task settings: the next session uses them.",
            wordWrap=True))
        self.noise_folder = QtWidgets.QLabel("", objectName="muted", wordWrap=True)
        v.addWidget(self.noise_folder)
        box = QtWidgets.QGroupBox("Noises")
        g = QtWidgets.QGridLayout(box)
        self.noise_buttons: dict[str, QtWidgets.QPushButton] = {}
        self.noise_gain: dict[str, QtWidgets.QDoubleSpinBox] = {}
        for r, name in enumerate(CONTEXTS):
            btn = QtWidgets.QPushButton(f"▶  Play {name}", checkable=True)
            btn.clicked.connect(lambda checked, n=name: self.play_noise(n) if checked else self.stop_noise())
            spin = QtWidgets.QDoubleSpinBox(decimals=1, minimum=-40, maximum=40, singleStep=0.5, suffix=" dB")
            spin.setKeyboardTracking(False)
            spin.setAccessibleName(f"{name.capitalize()} noise gain (dB)")
            spin.valueChanged.connect(self._noise_gain_changed)
            g.addWidget(btn, r, 0)
            g.addWidget(QtWidgets.QLabel("Gain"), r, 1)
            g.addWidget(spin, r, 2)
            self.noise_buttons[name], self.noise_gain[name] = btn, spin
        stop = QtWidgets.QPushButton("■  Stop", objectName="danger")
        stop.clicked.connect(self.stop_noise)
        g.addWidget(stop, len(CONTEXTS), 0)
        v.addWidget(box)
        self.noise_status = QtWidgets.QLabel("", wordWrap=True)
        v.addWidget(self.noise_status)
        v.addWidget(QtWidgets.QLabel(
            "Measure with the meter's weighting you want the noises matched on (e.g. dBA) and keep it the same for "
            "both. The noise loops, so it can play as long as needed.", objectName="muted", wordWrap=True))
        v.addStretch()
        return page

    def show_noise_tab(self) -> None:
        self.tabs.setCurrentIndex(1)

    def play_noise(self, name: str) -> None:
        if self.session_running():
            QtWidgets.QMessageBox.warning(self, "Session running",
                                          "Stop the behaviour session first; during a session, change the gains in the Context tab.")
            self.noise_buttons[name].setChecked(False)
            return
        folder = self.main_window.params().bckg_noise_directory if self.main_window else ""
        if self.noise_player is not None and folder != self._noise_player_folder:  # folder changed: reload
            self.noise_player.stop()
            if hasattr(self.noise_player, "close"):
                self.noise_player.close()
            self.noise_player = None
        if self.noise_player is None:
            self._noise_player_folder = folder
            try:
                self.noise_player = self.noise_player_factory(folder)
            except Exception as exc:
                self.noise_buttons[name].setChecked(False)
                QtWidgets.QMessageBox.critical(self, "Cannot play the noise",
                                               f"{exc}\n\nSet the noise folder in the Context tab.")
                return
        self.noise_player.set_gains_db(self._noise_gains())
        self.noise_player.play(name)
        for n, b in self.noise_buttons.items():
            b.setChecked(n == name)
        self._show_noise_status()

    def stop_noise(self) -> None:
        if self.noise_player is not None:
            self.noise_player.stop()
        for b in self.noise_buttons.values():
            b.setChecked(False)
        self.noise_status.setText("Stopped.")

    def _noise_gains(self) -> dict[str, float]:
        return {name: spin.value() for name, spin in self.noise_gain.items()}

    def _noise_gain_changed(self, *_) -> None:
        gains = self._noise_gains()
        if self.noise_player is not None:
            self.noise_player.set_gains_db(gains)  # heard within a fraction of a second
        if self.main_window is not None:
            self.main_window.set_params(self.main_window.params().copy(
                **{f"{name}_noise_gain_db": db for name, db in gains.items()}))
        self._show_noise_status()

    def _show_noise_status(self) -> None:
        playing = [n for n, b in self.noise_buttons.items() if b.isChecked()]
        gains = self._noise_gains()
        text = f"Playing {playing[0]} noise at {gains[playing[0]]:+g} dB." if playing else ""
        clipping = self.noise_player.clipping() if self.noise_player is not None else []
        if clipping:
            text += " Clipping: " + ", ".join(f"{n} at {gains[n]:+g} dB" for n in clipping) + ". Lower its gain."
        self.noise_status.setText(text)

    def plan(self) -> TonePlan:
        return TonePlan(amplitude_v=self.amplitude.value(), freq_hz=self.freq.value(),
                        duration_ms=self.duration.value(), interval_s=self.interval.value())

    def _plan_changed(self, *_):
        if self.run is not None and not self.run.done.is_set():
            plan = self.plan()
            problems = plan.validate(self._fs)
            if problems:
                self.status.setText("Not applied: " + " ".join(problems))
            else:
                self.run.plan = plan  # picked up by the next tone
        self._show_suggestion()

    def start(self) -> None:
        if self.run is not None and not self.run.done.is_set():
            return
        if self.session_running():
            QtWidgets.QMessageBox.warning(self, "Session running", "Stop the behaviour session before calibrating the speaker.")
            return
        plan = self.plan()
        problems = plan.validate(self._fs)
        if problems:
            QtWidgets.QMessageBox.warning(self, "Cannot start", "\n".join(problems))
            return
        try:
            rig = self.rig_factory()
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Rig not available", str(exc))
            return
        self.run = ToneRun(rig, plan)
        self.result.setText("")
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
        if run.done.is_set():
            self._timer.stop()
            self._set_running(False)
            if run.error:
                self.status.setText(f"Stopped: {run.error}")
                QtWidgets.QMessageBox.critical(self, "Sound calibration failed", run.error)
            else:
                self.status.setText(f"Stopped after {run.count} tones.")
            return
        p = run.plan
        if run.last_tone_at is not None:
            nxt = max(0.0, run.last_tone_at + p.interval_s - time.monotonic())
            self.status.setText(f"Tone {run.count}: {p.amplitude_v:g} V, {p.freq_hz:g} Hz, {p.duration_ms:g} ms. "
                                f"Next in {nxt:.1f} s")

    def _set_running(self, running: bool) -> None:
        self.start_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)

    def save(self) -> None:
        if self.db.value() <= 0:
            QtWidgets.QMessageBox.warning(self, "No reading", "Enter the level the meter shows.")
            return
        p = self.run.plan if self.run is not None else self.plan()
        r = SoundReading(amplitude_v=p.amplitude_v, freq_hz=p.freq_hz, duration_ms=p.duration_ms,
                         db_spl=self.db.value(), rig=self.rig_config.name)
        try:
            append_log(self.log_file, r)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Could not save", str(exc))
            return
        self.result.setText(f"Saved: <b>{r.db_spl:g} dB SPL</b> at {r.amplitude_v:g} V, {r.freq_hz:g} Hz.")
        self._refresh_history()

    def _refresh_history(self) -> None:
        rows = read_log(self.log_file)
        self.table.setRowCount(len(rows))
        for r, row in enumerate(reversed(rows)):  # newest first
            for c, text in enumerate((row["date_time"].replace("T", " "), f"{row['amplitude_v']:g}",
                                      f"{row['freq_hz']:g}", f"{row['duration_ms']:g}", f"{row['db_spl']:g}")):
                item = QtWidgets.QTableWidgetItem(text)
                item.setTextAlignment(QtCore.Qt.AlignCenter)
                self.table.setItem(r, c, item)
        self.log_label.setText(f"Log: {self.log_file}")
        self._rows = rows
        self._show_suggestion()

    def _show_suggestion(self, *_):
        freq = self.freq.value()
        found = amplitude_for_level(self._rows, freq, self.target.value())
        self._suggested = None
        if found is None:
            self.suggestion.setText(f"needs a reading at {freq:g} Hz")
        else:
            amp, ref = found
            if amp > 10:
                self.suggestion.setText(f"≈ {amp:.3g} V: above the 10 V output range")
            else:
                self._suggested = amp
                self.suggestion.setText(f"≈ {amp:.3f} V (from {ref['db_spl']:g} dB at {ref['amplitude_v']:g} V)")
        self.use_btn.setEnabled(self._suggested is not None and self.main_window is not None)

    def use_suggestion(self) -> None:
        if self._suggested is None or self.main_window is None:
            return
        amp = round(self._suggested, 3)
        self.main_window.set_params(self.main_window.params().copy(aud_stim_amp=amp, aud_stim_freq=self.freq.value()))
        self.amplitude.setValue(amp)
        self.status.setText(f"Task auditory stimulus set to {amp:g} V at {self.freq.value():g} Hz. "
                            "Check it with the meter.")

    def closeEvent(self, event) -> None:
        if self.run is not None and not self.run.done.is_set():
            self.run.stop()
            self.run.join(timeout=5)
        if self.noise_player is not None:
            self.noise_player.stop()
            if hasattr(self.noise_player, "close"):
                self.noise_player.close()
            self.noise_player = None
            for b in self.noise_buttons.values():
                b.setChecked(False)
        event.accept()
