"""PdCO settings window (port of utils_TTL/TTL_GUI.mlapp)."""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6 import QtWidgets

from ..modules.pdco import PdcoConfig, PdcoModule, PulseParams, trial_vectors
from ..trial import TrialSpec
from . import theme


class PdcoWindow(QtWidgets.QWidget):
    def __init__(self, main_window=None):
        super().__init__(None)
        self.setWindowTitle("PdCO / TTL settings")
        self.main_window = main_window
        self.module: PdcoModule | None = None
        lay = QtWidgets.QGridLayout(self)

        self.ch = {}
        for col, ch in enumerate(("ch1", "ch2")):
            box = QtWidgets.QGroupBox("TTL ch1 (ON pulse, from end of baseline)" if ch == "ch1" else "TTL ch2 (OFF pulse, from response window end)")
            g = QtWidgets.QFormLayout(box)
            fields = {}
            for key, label, default in (("delay", "Delay [ms]", 0), ("dur", "Duration [ms]", 100),
                                        ("period", "Period [ms] (0 = single)", 0), ("pw", "Pulse width [ms]", 10),
                                        ("amp", "Amplitude [V]", 5)):
                w = QtWidgets.QDoubleSpinBox()
                w.setRange(-1e5 if key == "delay" else 0, 1e5)
                w.setValue(default)
                w.setKeyboardTracking(False)
                w.valueChanged.connect(self._changed)
                g.addRow(label, w)
                fields[key] = w
            self.ch[ch] = fields
            lay.addWidget(box, 0, col)

        box = QtWidgets.QGroupBox("Experimental design")
        g = QtWidgets.QVBoxLayout(box)
        self.exp = {name: QtWidgets.QRadioButton(name or "None") for name in ("", "Block", "Trial")}
        self.exp[""].setChecked(True)
        for w in self.exp.values():
            w.toggled.connect(self._changed)
            g.addWidget(w)
        lay.addWidget(box, 1, 0)

        self.block_box = QtWidgets.QGroupBox("Block design")
        g = QtWidgets.QGridLayout(self.block_box)
        self.block = {n: QtWidgets.QRadioButton(n) for n in ("Alternate blocks", "First half", "Second half", "Continuous")}
        self.block["First half"].setChecked(True)
        for i, w in enumerate(self.block.values()):
            w.toggled.connect(self._changed)
            g.addWidget(w, i, 0)
        self.session_start = QtWidgets.QCheckBox("TTL ch1 from session start")
        self.next_block = QtWidgets.QCheckBox("TTL ch1 from next block")
        self.session_start.toggled.connect(lambda on: on and self.next_block.setChecked(False))
        self.next_block.toggled.connect(lambda on: on and self.session_start.setChecked(False))
        for w in (self.session_start, self.next_block):
            w.toggled.connect(self._changed)
        g.addWidget(self.session_start, 0, 1)
        g.addWidget(self.next_block, 1, 1)
        rep = QtWidgets.QGroupBox("Repeat TTL ch1 (Continuous)")
        rg = QtWidgets.QVBoxLayout(rep)
        self.repeat = {n: QtWidgets.QRadioButton(n) for n in ("Every N trials", "Every block start", "Never")}
        self.repeat["Never"].setChecked(True)
        for w in self.repeat.values():
            w.toggled.connect(self._changed)
            rg.addWidget(w)
        self.repeat_n = QtWidgets.QSpinBox(minimum=1, maximum=1000, value=1)
        self.repeat_n.valueChanged.connect(self._changed)
        rg.addWidget(self.repeat_n)
        g.addWidget(rep, 2, 1, 3, 1)
        self.stop_btn = QtWidgets.QPushButton("TTL ch2: stop at end of block")
        self.stop_btn.setCheckable(True)
        self.stop_btn.toggled.connect(self._changed)
        g.addWidget(self.stop_btn, 5, 0, 1, 2)
        lay.addWidget(self.block_box, 1, 1)

        self.trial_box = QtWidgets.QGroupBox("Trial design: probability of PdCO per trial type")
        g = QtWidgets.QFormLayout(self.trial_box)
        self.prob = {}
        for key, label in (("aud_prob", "Auditory"), ("wh_prob", "Whisker"), ("catch_prob", "Catch")):
            w = QtWidgets.QDoubleSpinBox(minimum=0, maximum=1, singleStep=0.1, decimals=2)
            w.valueChanged.connect(self._changed)
            g.addRow(label, w)
            self.prob[key] = w
        lay.addWidget(self.trial_box, 2, 0, 1, 2)

        self.preview = pg.PlotWidget()
        self.preview.setMinimumHeight(220)
        title = (f"Preview, one trial &nbsp; <span style='color:{theme.TTL1}'>━━</span> ch1 ON"
                 f" &nbsp; <span style='color:{theme.TTL2}'>╌╌</span> ch2 OFF (offset +0.1 for visibility)")
        self.preview.setTitle(title, color=theme.TEXT_2)
        theme.style_plot(self.preview.getPlotItem(), left="TTL", bottom="Time from trial start (ms)")
        self.preview_curves = (self.preview.plot(pen=theme.pen(theme.TTL1)),
                               self.preview.plot(pen=theme.pen(theme.TTL2, dashed=True)))
        lay.addWidget(self.preview, 3, 0, 1, 2)
        self._changed()

    def config(self) -> PdcoConfig:
        def pulse(ch):
            f = self.ch[ch]
            return PulseParams(delay_s=f["delay"].value() / 1000, dur_s=f["dur"].value() / 1000,
                               period_s=f["period"].value() / 1000, pw_s=f["pw"].value() / 1000, amp=f["amp"].value())

        exp = next(k for k, w in self.exp.items() if w.isChecked())
        start = "Session start" if self.session_start.isChecked() else "Next block" if self.next_block.isChecked() else ""
        return PdcoConfig(
            ch1=pulse("ch1"), ch2=pulse("ch2"), exp_design=exp,
            block_design=next(k for k, w in self.block.items() if w.isChecked()),
            ttl_ch1_start=start,
            repeat_ttl_ch1=next(k for k, w in self.repeat.items() if w.isChecked()),
            repeat_ttl_ch1_n=self.repeat_n.value(),
            ttl_ch2_start=self.stop_btn.isChecked(),
            **{k: w.value() for k, w in self.prob.items()},
        ).normalised()

    def attach(self, module: PdcoModule) -> None:
        self.module = module
        module.set_config(self.config())

    def _changed(self, *_):
        cfg = self.config()
        is_block = cfg.exp_design == "Block"
        self.block_box.setEnabled(is_block)
        self.trial_box.setEnabled(cfg.exp_design == "Trial")
        continuous = is_block and cfg.block_design == "Continuous"
        for w in list(self.repeat.values()) + [self.repeat_n, self.stop_btn]:
            w.setEnabled(continuous)
        if self.module is not None:
            self.module.set_config(cfg)
        self._draw_preview(cfg)

    def _draw_preview(self, cfg: PdcoConfig) -> None:
        if self.main_window is not None:
            p = self.main_window.params()
            trial = TrialSpec(baseline_window=p.baseline_window, artifact_window=p.artifact_window, response_window=p.response_window)
        else:
            trial = TrialSpec(baseline_window=1000, artifact_window=50, response_window=1000)
        v1, v2 = trial_vectors(cfg, trial)
        for curve, v, offset in ((self.preview_curves[0], v1, 0.0), (self.preview_curves[1], v2, 0.1)):
            t = np.arange(v.size) / cfg.fs * 1000
            curve.setData(t, v.astype(float) + offset)
