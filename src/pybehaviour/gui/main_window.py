"""Main window (port of Detection_GUI.mlapp).

Layout:
    top      session controls (Start / Pause / Resume / Stop / Manual reward) and status
    left     settings, in tabs (Session, Trial, Stimuli, Reward, Context)
    centre   live signals (10 s, linked time axis) above performance plots
    right    session summary tiles and the next trial

The window never touches hardware. It builds a SessionParams snapshot from its
widgets and hands it to the Session; it polls the session's event queue and
the logger's ring buffer on Qt timers (20 Hz).

Keyboard: F5 start, F6 pause, F7 resume, F8 manual reward, Ctrl+. stop.
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Callable

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets

from ..modules.context import ContextModule
from ..modules.opto import OptoConfig, OptoModule
from ..modules.pdco import PdcoConfig, PdcoModule
from ..params import SessionParams
from ..planner import CONTEXTS
from ..presets import Preset, PresetStore, check_name, load_session_settings
from ..results import compute_performance, compute_reward_volume, early_lick_rate, sliding_rates
from ..rig import Rig
from ..rig.config import RigConfig
from ..session import Session
from ..trial import Perf, TrialSpec
from . import theme
from .binder import Binder
from .calibration_window import CalibrationWindow
from .export_dialog import ExportDefaultsDialog
from .opto_window import OptoWindow
from .pdco_window import PdcoWindow
from .sound_window import SoundWindow
from .valve_window import ValveWindow

BEHAVIOUR_TYPES = ["free_licking", "auditory", "light", "whisker", "whisker_context", "whisker_psy",
                   "whisker_on_1", "whisker_off", "whisker_on_2", "light_psy", "light_on_1", "light_off", "light_on_2"]
DEFAULT_VOLUME_PER_REWARD_UL = 5  # used until the valve is calibrated (Tools ▸ Valve calibration)

OUTCOME_TEXT = {
    Perf.WHISKER_HIT: "Whisker hit", Perf.WHISKER_MISS: "Whisker miss",
    Perf.AUDITORY_HIT: "Auditory hit", Perf.AUDITORY_MISS: "Auditory miss",
    Perf.LIGHT_HIT: "Light hit", Perf.LIGHT_MISS: "Light miss",
    Perf.FALSE_ALARM: "False alarm", Perf.CORRECT_REJECTION: "Correct rejection",
}


class Form:
    """A two-column form inside a group box; labels are buddies of their field (screen readers)."""

    def __init__(self, title: str):
        self.box = QtWidgets.QGroupBox(title)
        self.grid = QtWidgets.QGridLayout(self.box)
        self.grid.setHorizontalSpacing(8)
        self.grid.setVerticalSpacing(6)
        self.grid.setColumnStretch(1, 1)

    def row(self, label: str, *widgets: QtWidgets.QWidget, tip: str = "") -> None:
        r = self.grid.rowCount()
        lab = QtWidgets.QLabel(label)
        lab.setBuddy(widgets[0])
        self.grid.addWidget(lab, r, 0)
        if len(widgets) == 1:
            self.grid.addWidget(widgets[0], r, 1)
        else:
            h = QtWidgets.QHBoxLayout()
            h.setSpacing(4)
            for w in widgets:
                h.addWidget(w)
            self.grid.addLayout(h, r, 1)
        for w in widgets:
            w.setAccessibleName(label)
            if tip:
                w.setToolTip(tip)

    def full(self, widget: QtWidgets.QWidget) -> None:
        self.grid.addWidget(widget, self.grid.rowCount(), 0, 1, 2)

    def pair(self, a: QtWidgets.QWidget, b: QtWidgets.QWidget) -> None:
        r = self.grid.rowCount()
        self.grid.addWidget(a, r, 0)
        self.grid.addWidget(b, r, 1)


class Tile(QtWidgets.QFrame):
    """A labelled number on the summary panel."""

    def __init__(self, label: str, swatch: str | None = None, dashed: bool = False):
        super().__init__(objectName="tile")
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(0)
        head = QtWidgets.QHBoxLayout()
        if swatch:
            sw = QtWidgets.QLabel("╌╌" if dashed else "━━")
            sw.setStyleSheet(f"color: {swatch}; font-weight: 900")
            head.addWidget(sw)
        self.label = QtWidgets.QLabel(label, objectName="tileLabel")
        head.addWidget(self.label)
        head.addStretch()
        self.value = QtWidgets.QLabel("–", objectName="tileValue")
        self.value.setAccessibleName(label)
        lay.addLayout(head)
        lay.addWidget(self.value)

    def set(self, text: str) -> None:
        self.value.setText(text)


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, rig_factory: Callable[[], Rig], defaults: SessionParams | None = None, title: str = "",
                 rig_config: RigConfig | None = None, presets: PresetStore | None = None,
                 preset: Preset | None = None, rig_defaults: SessionParams | None = None,
                 load_rig: Callable[[Path], None] | None = None):
        super().__init__()
        self.setWindowTitle(f"LSENS behaviour control {title}".strip())
        self.rig_factory = rig_factory
        self.load_rig = load_rig  # reopens the GUI on another rig file (Settings ▸ Load rig file…)
        self.rig_config = rig_config or RigConfig()
        self.session: Session | None = None
        self.rows: list[dict] = []
        self.pdco_window: PdcoWindow | None = None
        self.opto_window: OptoWindow | None = None
        self.opto_module: OptoModule | None = None
        self.calibration_window: CalibrationWindow | None = None
        self.valve_window: ValveWindow | None = None
        self.sound_window: SoundWindow | None = None
        self._volume_per_reward: tuple[float, bool] = (DEFAULT_VOLUME_PER_REWARD_UL, False)
        self.b = Binder(self._params_changed)
        self._defaults = defaults or SessionParams()
        self.presets = presets
        self.preset: Preset | None = preset  # loaded preset, None = rig defaults
        self._rig_defaults = rig_defaults or self._defaults  # what "Rig defaults" restores
        # module settings from a preset, applied when their window opens
        self._pending_opto: OptoConfig | None = preset.opto if preset else None
        self._pending_pdco: PdcoConfig | None = preset.pdco if preset else None
        self._started_at: float | None = None

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        outer = QtWidgets.QVBoxLayout(central)
        outer.setContentsMargins(10, 8, 10, 8)
        outer.setSpacing(8)
        outer.addWidget(self._header())
        # full-width strip under the header: which context block we are in (context task only)
        self.context_banner = QtWidgets.QLabel(objectName="contextBanner", alignment=QtCore.Qt.AlignCenter)
        self.context_banner.setAccessibleName("Current context")
        self.context_banner.setToolTip("Context block of the current trial (changes when a new block is planned)")
        f = self.context_banner.font()
        f.setPointSize(theme.BASE_POINT_SIZE + 5)
        self.context_banner.setFont(f)
        self.context_banner.setVisible(False)
        outer.addWidget(self.context_banner)
        self._menus()

        body = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        body.setChildrenCollapsible(False)
        body.addWidget(self._settings_panel())
        body.addWidget(self._plots_panel())
        body.addWidget(self._summary_panel())
        body.setStretchFactor(0, 0)
        body.setStretchFactor(1, 1)
        body.setStretchFactor(2, 0)
        body.setSizes([450, 1080, 260])
        outer.addWidget(body, 1)

        self.b.write(self._defaults.copy(date=datetime.now().strftime("%Y%m%d")))
        self._update_enabled()
        self._refresh_presets()
        self.refresh_reward_volume()
        QtCore.QTimer.singleShot(0, self._open_enabled_module_windows)
        self._set_running(False)
        self._set_status("Ready", "idle")
        self._shortcuts()
        self._add_setting_names_to_tooltips()

        self._event_timer = QtCore.QTimer(self, interval=50, timeout=self._poll_events)
        self._plot_timer = QtCore.QTimer(self, interval=50, timeout=self._update_live_plots)
        self._event_timer.start()

    # --- header ------------------------------------------------------------------
    def _header(self) -> QtWidgets.QWidget:
        w = QtWidgets.QFrame(objectName="card")
        h = QtWidgets.QHBoxLayout(w)
        h.setContentsMargins(10, 6, 10, 6)
        self.start_btn = QtWidgets.QPushButton("▶  Start  (F5)", objectName="primary")
        self.pause_btn = QtWidgets.QPushButton("❚❚  Pause  (F6)")
        self.resume_btn = QtWidgets.QPushButton("▶  Resume  (F7)")
        self.stop_btn = QtWidgets.QPushButton("■  Stop", objectName="danger")
        self.manual_reward_btn = QtWidgets.QPushButton("💧  Manual reward  (F8)")
        tips = {
            self.start_btn: "Create the session folder and start trials",
            self.pause_btn: "Finish the current trial, then wait",
            self.resume_btn: "Continue after a pause",
            self.stop_btn: "Stop the session and close the files (Ctrl+.)",
            self.manual_reward_btn: "Open the valve once now",
        }
        for btn, fn in ((self.start_btn, self._start), (self.pause_btn, self._pause), (self.resume_btn, self._resume),
                        (self.stop_btn, self._stop), (self.manual_reward_btn, self._manual_reward)):
            btn.clicked.connect(fn)
            btn.setMinimumHeight(38)
            btn.setToolTip(tips[btn])
            btn.setAccessibleName(tips[btn])
            h.addWidget(btn)
        h.addSpacing(16)
        self.status_pill = QtWidgets.QLabel(objectName="statusPill")
        self.status_pill.setAccessibleName("Session state")
        h.addWidget(self.status_pill)
        self.online_text = QtWidgets.QLabel(" ")
        f = self.online_text.font()
        f.setPointSize(theme.BASE_POINT_SIZE + 5)
        f.setBold(True)
        self.online_text.setFont(f)
        self.online_text.setAccessibleName("Last event")
        h.addWidget(self.online_text, 1)
        self.elapsed = QtWidgets.QLabel("", objectName="muted")
        h.addWidget(self.elapsed)
        return w

    def _menus(self) -> None:
        bar = self.menuBar()
        m = bar.addMenu("&Settings")
        self.save_preset_action = m.addAction("Save preset", self._save_preset, QtGui.QKeySequence.Save)
        m.addAction("Save preset as…", self._save_preset_as, QtGui.QKeySequence("Ctrl+Shift+S"))
        self.delete_preset_action = m.addAction("Delete preset…", self._delete_preset)
        m.addSeparator()
        m.addAction("Load settings from a previous session…", self._load_from_session)
        m.addAction("Reset to rig defaults", lambda: self._select_preset(None))
        m.addAction("Export settings as rig defaults…", self._export_defaults)
        m.addSeparator()
        self.load_rig_action = m.addAction("Load rig file…", self._load_rig_file, QtGui.QKeySequence("Ctrl+O"))
        self.load_rig_action.setEnabled(self.load_rig is not None)
        m.addSeparator()
        rig = self.rig_config.source or "(none: built-in rig settings)"
        for text in (f"Rig file: {rig}", f"Presets folder: {self.presets.directory if self.presets else '(none)'}"):
            m.addAction(text).setEnabled(False)
        t = bar.addMenu("&Tools")
        t.addAction("Coil calibration…", self._open_calibration)
        t.addAction("Valve calibration…", self._open_valve_calibration)
        t.addAction("Sound calibration…", self._open_sound_calibration)
        t.addAction("Optogenetics settings…", lambda: self._show_module_window("opto"))
        t.addAction("PdCO settings…", lambda: self._show_module_window("pdco"))

    def _preset_bar(self) -> QtWidgets.QWidget:
        w = QtWidgets.QFrame(objectName="card")
        h = QtWidgets.QHBoxLayout(w)
        h.setContentsMargins(8, 4, 8, 4)
        lab = QtWidgets.QLabel("Preset")
        self.preset_box = QtWidgets.QComboBox()
        self.preset_box.setMinimumWidth(150)
        self.preset_box.setAccessibleName("Settings preset")
        self.preset_box.setToolTip("Saved settings for this rig. Pick one to load it; it is remembered for next time.")
        lab.setBuddy(self.preset_box)
        self.preset_box.activated.connect(self._preset_chosen)
        self.preset_save_btn = QtWidgets.QPushButton("Save")
        self.preset_save_btn.setToolTip("Overwrite the selected preset with the current settings (Ctrl+S)")
        self.preset_save_btn.clicked.connect(self._save_preset)
        save_as = QtWidgets.QPushButton("Save as…")
        save_as.setToolTip("Save the current settings as a new preset (Ctrl+Shift+S)")
        save_as.clicked.connect(self._save_preset_as)
        self.preset_state = QtWidgets.QLabel("", objectName="muted")
        self.preset_state.setAccessibleName("Preset state")
        for x in (lab, self.preset_box, self.preset_save_btn, save_as):
            h.addWidget(x)
        h.addWidget(self.preset_state, 1)
        if self.presets is None:
            w.setEnabled(False)
        return w

    def _add_setting_names_to_tooltips(self) -> None:
        """Hovering a field shows its setting name (for rig defaults and presets, see docs/SETTINGS.md)."""
        for name, w in self.b.widgets.items():
            tip = w.toolTip()
            w.setToolTip(f"{tip}\n\nSetting name: {name}" if tip else f"Setting name: {name}")

    def _shortcuts(self) -> None:
        for key, fn in (("F5", self._start), ("F6", self._pause), ("F7", self._resume),
                        ("F8", self._manual_reward), ("Ctrl+.", self._stop)):
            QtGui.QShortcut(QtGui.QKeySequence(key), self, activated=fn)

    # --- settings (left) -----------------------------------------------------------
    def _settings_panel(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QWidget()
        pv = QtWidgets.QVBoxLayout(panel)
        pv.setContentsMargins(0, 0, 0, 0)
        pv.setSpacing(6)
        pv.addWidget(self._preset_bar())
        tabs = QtWidgets.QTabWidget()
        tabs.setMinimumWidth(430)
        pv.addWidget(tabs, 1)
        for title, builder in (("Session", self._tab_session), ("Trial", self._tab_trial), ("Stimuli", self._tab_stimuli),
                               ("Reward", self._tab_reward), ("Context", self._tab_context)):
            page = QtWidgets.QWidget()
            lay = QtWidgets.QVBoxLayout(page)
            lay.setContentsMargins(6, 6, 6, 6)
            for box in builder():
                lay.addWidget(box)
            lay.addStretch()
            scroll = QtWidgets.QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
            scroll.setWidget(page)
            tabs.addTab(scroll, title)
        self.settings_tabs = tabs
        return panel

    def _tab_session(self):
        b = self.b
        mouse = Form("Mouse")
        mouse.row("Mouse name", b.line("mouse_name"))
        mouse.row("Date", b.line("date"))
        b.widgets["date"].setReadOnly(True)
        mouse.row("Weight before (g)", b.spin("mouse_weight_before"))
        mouse.row("Weight after (g)", b.spin("mouse_weight_after"))
        mouse.row("Behaviour type", b.combo("behaviour_type", BEHAVIOUR_TYPES))
        browse = QtWidgets.QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        mouse.row("Data folder", b.line("behaviour_directory"), browse)
        self.update_config_btn = QtWidgets.QPushButton("Save current settings to session_config.json")
        self.update_config_btn.clicked.connect(self._update_config)
        mouse.full(self.update_config_btn)

        kind = Form("Session type")
        flags = [("twophoton_session", "Two-photon"), ("threephoton_session", "Three-photon"),
                 ("wf_session", "Widefield"), ("extracell_ephys_session", "Extracell. ephys"),
                 ("intracell_ephys_session", "Intracell. ephys"), ("chemo_session", "Chemo"),
                 ("pharma_session", "Pharma"), ("dummy_session_flag", "Dummy session")]
        grid = QtWidgets.QGridLayout()
        for i, (name, text) in enumerate(flags):
            grid.addWidget(b.check(name, text), i // 2, i % 2)
        b.widgets["wf_session"].setToolTip("Saved in session_config.json only: widefield control is not ported yet.")
        kind.grid.addLayout(grid, kind.grid.rowCount(), 0, 1, 2)
        self.opto_btn = QtWidgets.QPushButton("Settings…")
        self.opto_btn.clicked.connect(lambda: self._show_module_window("opto"))
        kind.pair(b.check("opto_session", "Optogenetics"), self.opto_btn)
        self.pdco_btn = QtWidgets.QPushButton("Settings…")
        self.pdco_btn.clicked.connect(lambda: self._show_module_window("pdco"))
        kind.pair(b.check("ttl_session", "PdCO (TTL)"), self.pdco_btn)
        b.widgets["opto_session"].toggled.connect(lambda on: on and self._show_module_window("opto"))
        b.widgets["ttl_session"].toggled.connect(lambda on: on and self._show_module_window("pdco"))

        cam = Form("Behaviour camera")
        cam.full(b.check("camera_flag", "Camera ON"))
        cam.row("Frame rate (Hz)", b.spin("camera_freq", 0))
        cam.row("Exposure (ms)", b.spin("camera_exposure_time", 1))
        cam.row("Start delay (s)", b.spin("camera_start_delay", 1))
        return [mouse.box, kind.box, cam.box]

    def _tab_trial(self):
        b = self.b
        tl = Form("Trial timeline (ms)")
        tl.row("Quiet window", b.spin("min_quiet_window", 1), QtWidgets.QLabel("to"), b.spin("max_quiet_window", 1),
               tip="No lick for this long before a trial can start (random in range)")
        tl.row("Inter-trial interval", b.spin("min_iti", 1), QtWidgets.QLabel("to"), b.spin("max_iti", 1))
        tl.row("Baseline window", b.spin("baseline_window", 0), tip="Trial start to stimulus; licks here abort the trial")
        tl.row("Artifact window", b.spin("artifact_window", 0), tip="After the stimulus; licks here are ignored")
        tl.row("Response window", b.spin("response_window", 0), tip="Licks here count as hits / false alarms")
        tl.row("Trial duration", b.spin("trial_duration", 0))

        mode = Form("Mode")
        mode.full(b.check("association_flag", "Association (free reward at stimulus)"))

        to = Form("Timeouts (ms)")
        to.row("False alarm", b.check("false_alarm_punish_flag", "on"), b.spin("false_alarm_timeout", 0))
        to.row("Early lick", b.check("early_lick_punish_flag", "on"), b.spin("early_lick_timeout", 0),
               tip="Added to the fixed 2 s pause after every early lick")

        passive = Form("Passive stimulation")
        passive.pair(b.check("passive_stim_flag_enable", "Enable"), b.toggle_button("passive_stim_flag", "Passive block ON"))
        passive.row("Trials", b.spin("passive_trial_max"))
        passive.row("Inter-trial interval (ms)", b.spin("passive_iti", 0))
        hint = QtWidgets.QLabel("Pause, prepare, switch on, then resume.", objectName="muted")
        passive.full(hint)
        return [tl.box, mode.box, to.box, passive.box]

    def _amp_table(self, form: Form, prefix: str, second: str, second_label: str, weight_label: str) -> None:
        hdr = form.grid.rowCount()
        for c, t in enumerate(["Amplitude (V)", second_label, weight_label]):
            lab = QtWidgets.QLabel(t, objectName="muted")
            form.grid.addWidget(lab, hdr, c + 1)
        form.grid.setColumnStretch(2, 1)
        form.grid.setColumnStretch(3, 1)
        for i in range(1, 6):
            r = form.grid.rowCount()
            form.grid.addWidget(QtWidgets.QLabel(f"#{i}"), r, 0)
            for c, name in enumerate((f"{prefix}_amp_{i}", f"{second}_{i}", f"{prefix}_weight_{i}")):
                w = self.b.spin(name, 3 if c == 0 else (2 if c == 1 else 0))
                w.setAccessibleName(f"{['Amplitude', second_label, weight_label][c]} {i}")
                form.grid.addWidget(w, r, c + 1)

    def _tab_stimuli(self):
        b = self.b
        wh = Form("Whisker")
        wh.row("Duration (ms)", b.spin("wh_stim_duration", 1))
        wh.row("Scaling factor", b.spin("wh_scaling_factor", 2, step=0.05), tip="Amplitude of the return phase")
        wh.full(b.check("wh_stim_amp_range", "Several amplitudes (psychometric)"))
        self._amp_table(wh, "wh_stim", "wh_stim_amp_mT", "Amplitude (mT)", "Weight")
        cal = QtWidgets.QPushButton("Calibrate coil…")
        cal.setToolTip("Measure the field (mT) for these amplitudes with the teslameter")
        cal.clicked.connect(self._open_calibration)
        wh.full(cal)
        aud = Form("Auditory")
        aud.row("Duration (ms)", b.spin("aud_stim_duration", 1))
        aud.row("Amplitude (V)", b.spin("aud_stim_amp", 3))
        aud.row("Frequency (Hz)", b.spin("aud_stim_freq", 0))
        aud.row("Weight", b.spin("aud_stim_weight", 0))
        sound_btn = QtWidgets.QPushButton("Calibrate speaker…")
        sound_btn.setToolTip("Play the tone every few seconds and read the dB meter")
        sound_btn.clicked.connect(self._open_sound_calibration)
        aud.full(sound_btn)
        light = Form("Light")
        light.row("Duration (ms)", b.spin("light_duration", 1))
        light.full(b.check("light_stim_amp_range", "Several amplitudes (psychometric)"))
        self._amp_table(light, "light_stim", "light_stim_power", "Power (mW)", "Weight")
        none = Form("No stimulus (catch)")
        none.row("Weight", b.spin("no_stim_weight", 0))
        # MATLAB field names for the amplitude columns
        return [wh.box, aud.box, light.box, none.box]

    def _tab_reward(self):
        b = self.b
        r = Form("Reward")
        r.row("Valve opening (ms)", b.spin("reward_valve_duration", 0))
        self.valve_volume_label = QtWidgets.QLabel("", objectName="muted", wordWrap=True)
        r.full(self.valve_volume_label)
        valve_btn = QtWidgets.QPushButton("Calibrate valve…")
        valve_btn.setToolTip("Open the valve N times and weigh the water to get µL per reward")
        valve_btn.clicked.connect(self._open_valve_calibration)
        r.full(valve_btn)
        r.row("Reward delay (ms)", b.check("reward_delay_flag", "on"), b.spin("reward_delay_time", 0),
              tip="Delay between stimulus and valve in association mode")
        r.row("Partial reward", b.check("partial_reward_flag", "on"), b.spin("reward_proba", 2, 1, 0.05),
              tip="Probability that a whisker hit is rewarded")
        rewarded = Form("Rewarded stimuli")
        rewarded.full(b.check("aud_reward", "Auditory"))
        rewarded.full(b.check("wh_reward", "Whisker"))
        rewarded.full(b.check("light_reward", "Light"))
        return [r.box, rewarded.box]

    def _tab_context(self):
        b = self.b
        c = Form("Context (pink / brown noise blocks)")
        c.full(b.check("context_flag", "Context task"))
        c.row("Max same-context blocks", b.spin("context_block_size"))
        c.row("Context table folder", b.line("context_table_directory"), tip="Folder with rewarded_context.csv")
        c.row("Noise folder", b.line("bckg_noise_directory"), tip="Folder with pink_noise.wav and brown_noise.wav")
        c.full(b.check("context_sound_on", "Play background noise"))
        c.row("Pink noise gain (dB)", b.spin("pink_noise_gain_db", 1, maximum=40, step=0.5),
              tip="Level of pink_noise.wav; applies immediately, also during a session")
        c.row("Brown noise gain (dB)", b.spin("brown_noise_gain_db", 1, maximum=40, step=0.5),
              tip="Level of brown_noise.wav; applies immediately, also during a session")
        noise_cal = QtWidgets.QPushButton("Calibrate noise levels…")
        noise_cal.setToolTip("Play each noise and set its gain with the dB meter")
        noise_cal.clicked.connect(lambda: self._open_sound_calibration(noise=True))
        c.full(noise_cal)
        note = QtWidgets.QLabel("In context mode the whisker reward follows the mouse's rewarded context.",
                                objectName="muted", wordWrap=True)
        c.full(note)
        return [c.box]

    # --- plots (centre) ---------------------------------------------------------------
    def _plots_panel(self) -> QtWidgets.QWidget:
        split = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        split.setChildrenCollapsible(False)

        live_card = QtWidgets.QFrame(objectName="card")
        lv = QtWidgets.QVBoxLayout(live_card)
        lv.setContentsMargins(8, 6, 8, 6)
        top = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("<b>Live signals</b> · last 10 s")
        top.addWidget(title)
        top.addStretch()
        lab = QtWidgets.QLabel("Lick threshold (V)")
        thr = self.b.spin("lick_threshold", 3, 10, 0.005)
        lab.setBuddy(thr)
        thr.setAccessibleName("Lick threshold (V)")
        top.addWidget(lab)
        top.addWidget(thr)
        lv.addLayout(top)

        self.live = pg.GraphicsLayoutWidget()
        self.live.ci.layout.setSpacing(3)
        self.live_plots: dict[str, pg.PlotItem] = {}
        self.live_labels: dict[str, pg.LabelItem] = {}
        # key, row label, y range (None = set from the threshold), relative height
        self.live_specs = [("lick", "Lick (V)", None, 4), ("trial_ttl", "Trial", (-0.5, 5.5), 1.4),
                           ("ttl", "PdCO TTL", (-0.5, 5.5), 1.2), ("cam1", "Camera 1", (-0.5, 5.5), 1),
                           ("cam2", "Camera 2", (-0.5, 5.5), 1), ("galvo", "Galvo (V)", (-2, 3), 1.2)]
        first = None
        for key, label, ylim, _ in self.live_specs:
            p = pg.PlotItem()
            theme.style_plot(p)
            p.setMouseEnabled(False, False)
            p.getAxis("left").setWidth(42)
            if ylim:
                p.setYRange(*ylim, padding=0)
            if first is None:
                first = p
            else:
                p.setXLink(first)
            self.live_plots[key] = p
            self.live_labels[key] = pg.LabelItem(label, color=theme.TEXT_2, justify="right")
            self.live_labels[key].setFixedWidth(96)
        ink = theme.pen(theme.TEXT, 1.5)
        self.curves = {
            "lick": self.live_plots["lick"].plot(pen=ink),
            "trial_ttl": self.live_plots["trial_ttl"].plot(pen=ink, name="Trial"),
            "context": self.live_plots["trial_ttl"].plot(pen=theme.pen(theme.CONTEXT), name="Context"),
            "ttl1": self.live_plots["ttl"].plot(pen=theme.pen(theme.TTL1), name="ch1 (ON)"),
            "ttl2": self.live_plots["ttl"].plot(pen=theme.pen(theme.TTL2, dashed=True), name="ch2 (OFF)"),
            "cam1": self.live_plots["cam1"].plot(pen=ink),
            "cam2": self.live_plots["cam2"].plot(pen=ink),
            "galvo": self.live_plots["galvo"].plot(pen=ink),
        }
        # series keys live in the row label, so no legend covers the traces
        key = self._key
        self.live_labels["trial_ttl"].setText("Trial" + key(theme.TEXT, "trial") + key(theme.CONTEXT, "context"),
                                              color=theme.TEXT_2)
        self.live_labels["ttl"].setText("PdCO TTL" + key(theme.TTL1, "ch1 ON") + key(theme.TTL2, "ch2 OFF", True),
                                        color=theme.TEXT_2)
        self.thr_line = pg.InfiniteLine(angle=0, pen=theme.pen(theme.THRESHOLD, 1.5, dashed=True),
                                        label="threshold", labelOpts={"color": theme.THRESHOLD, "position": 0.96,
                                                                      "anchors": [(1, 1), (1, 1)]})
        self.live_plots["lick"].addItem(self.thr_line)
        self._live_visible: tuple = ()
        first.setXRange(0, 10, padding=0)
        lv.addWidget(self.live, 1)
        split.addWidget(live_card)

        perf_card = QtWidgets.QFrame(objectName="card")
        pv = QtWidgets.QVBoxLayout(perf_card)
        pv.setContentsMargins(8, 6, 8, 6)
        top = QtWidgets.QHBoxLayout()
        perf_title = QtWidgets.QLabel("<b>Performance</b>")
        perf_title.setToolTip("Hit and false-alarm rates over a sliding window of completed trials")
        top.addWidget(perf_title)
        top.addSpacing(16)
        self.perf_key_labels = {}
        for key, text, color, dashed in (("aud", "Auditory hit", theme.AUDITORY, False), ("wh", "Whisker hit", theme.WHISKER, False),
                                         ("light", "Light hit", theme.LIGHT, False), ("fa", "False alarm", theme.FALSE_ALARM, True)):
            sw = QtWidgets.QLabel("╌╌" if dashed else "━━")
            sw.setStyleSheet(f"color: {color}; font-weight: 900")
            lab = QtWidgets.QLabel(text, objectName="muted")
            top.addWidget(sw)
            top.addWidget(lab)
            top.addSpacing(8)
            self.perf_key_labels[key] = lab
        top.addStretch()
        lab = QtWidgets.QLabel("Window (trials)")
        win = self.b.spin("last_recent_trials")
        lab.setBuddy(win)
        win.setAccessibleName("Performance window (trials)")
        top.addWidget(lab)
        top.addWidget(win)
        pv.addLayout(top)
        perf = pg.GraphicsLayoutWidget()
        self.perf_plot = perf.addPlot(row=0, col=0)
        theme.style_plot(self.perf_plot, left="Rate", bottom="Completed trial")
        self.perf_plot.getAxis("left").setLabel("")
        self.perf_plot.setYRange(-0.03, 1.08, padding=0)
        self.perf_plot.setMouseEnabled(False, False)
        pct_ticks = [[(v, f"{v * 100:.0f}%") for v in (0, 0.25, 0.5, 0.75, 1.0)]]
        self.perf_plot.getAxis("left").setTicks(pct_ticks)
        self.perf_plot.getAxis("left").setWidth(48)
        self.perf_curves = {
            "aud": self.perf_plot.plot(pen=theme.pen(theme.AUDITORY), name="Auditory hit"),
            "wh": self.perf_plot.plot(pen=theme.pen(theme.WHISKER), name="Whisker hit"),
            "light": self.perf_plot.plot(pen=theme.pen(theme.LIGHT), name="Light hit"),
            "fa": self.perf_plot.plot(pen=theme.pen(theme.FALSE_ALARM, dashed=True), name="False alarm"),
        }
        self.perf_end_labels = {k: pg.TextItem(color=c, anchor=(0, 0.5)) for k, c in
                                (("aud", theme.AUDITORY), ("wh", theme.WHISKER), ("light", theme.LIGHT), ("fa", theme.TEXT_2))}
        for item in self.perf_end_labels.values():
            self.perf_plot.addItem(item)
        self.perf_plot.setXRange(1, 20, padding=0)
        self._hover_line = pg.InfiniteLine(angle=90, pen=pg.mkPen(theme.BORDER))
        self._hover_text = pg.TextItem(color=theme.TEXT, fill=pg.mkBrush(255, 255, 255, 230), border=pg.mkPen(theme.BORDER), anchor=(0, 0))
        for it in (self._hover_line, self._hover_text):
            it.setVisible(False)
            self.perf_plot.addItem(it, ignoreBounds=True)
        self._hover_proxy = pg.SignalProxy(self.perf_plot.scene().sigMouseMoved, rateLimit=30, slot=self._perf_hover)

        self.early_plot = perf.addPlot(row=1, col=0)
        theme.style_plot(self.early_plot, left="", bottom="Trial (all attempts) · early-lick rate")
        self.early_plot.setYRange(-0.05, 1.1, padding=0)
        self.early_plot.setMouseEnabled(False, False)
        self.early_plot.getAxis("left").setTicks([[(0, "0%"), (0.5, "50%"), (1.0, "100%")]])
        self.early_plot.getAxis("left").setWidth(48)
        self.early_curve = self.early_plot.plot(pen=theme.pen(theme.TEXT_2))
        self.early_plot.setXRange(1, 20, padding=0)
        perf.ci.layout.setRowStretchFactor(0, 3)
        perf.ci.layout.setRowStretchFactor(1, 1)
        pv.addWidget(perf, 1)
        split.addWidget(perf_card)
        split.setSizes([600, 380])
        return split

    @staticmethod
    def _key(color: str, text: str, dashed: bool = False) -> str:
        """One series key line for a live-plot row label."""
        return f"<br><span style='color:{color}'>{'╌╌' if dashed else '━━'}</span> {text}"

    # --- summary (right) --------------------------------------------------------------
    def _summary_panel(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        w.setMinimumWidth(230)
        w.setMaximumWidth(320)
        v = QtWidgets.QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        nxt = QtWidgets.QFrame(objectName="card")
        nl = QtWidgets.QVBoxLayout(nxt)
        nl.setContentsMargins(10, 6, 10, 6)
        nl.addWidget(QtWidgets.QLabel("Next trial", objectName="tileLabel"))
        row = QtWidgets.QHBoxLayout()
        self.next_swatch = QtWidgets.QLabel("●")
        self.next_swatch.setStyleSheet(f"color: {theme.TEXT_3}; font-size: 16pt")
        row.addWidget(self.next_swatch)
        self.timeline_text = QtWidgets.QLabel("–", wordWrap=True)
        self.timeline_text.setAccessibleName("Next trial")
        f = self.timeline_text.font()
        f.setBold(True)
        self.timeline_text.setFont(f)
        row.addWidget(self.timeline_text, 1)
        nl.addLayout(row)
        self.timeline_detail = QtWidgets.QLabel("", objectName="muted", wordWrap=True)
        nl.addWidget(self.timeline_detail)
        v.addWidget(nxt)

        self.tiles = {
            "trials": Tile("Completed trials"),
            "early": Tile("Early licks"),
            "aud": Tile("Auditory hit rate", theme.AUDITORY),
            "wh": Tile("Whisker hit rate", theme.WHISKER),
            "light": Tile("Light hit rate", theme.LIGHT),
            "fa": Tile("False alarm rate", theme.FALSE_ALARM, dashed=True),
            "reward": Tile("Reward (µL, not calibrated)"),
        }
        for t in self.tiles.values():
            v.addWidget(t)
        self.reward_detail = QtWidgets.QLabel("", objectName="muted", wordWrap=True)
        v.addWidget(self.reward_detail)
        v.addStretch()
        self.state_text = QtWidgets.QLabel("", objectName="muted", wordWrap=True)
        self.state_text.setAccessibleName("Controller state")
        v.addWidget(self.state_text)
        return w

    # --- params ----------------------------------------------------------------------
    def params(self) -> SessionParams:
        return self.b.read(self._defaults)

    def set_params(self, params: SessionParams) -> None:
        self.b.write(params)
        self._update_enabled()
        self._update_preset_state()
        self._open_enabled_module_windows()
        if self.session is not None and self.session.running:
            self.session.set_params(self.params())

    def _open_enabled_module_windows(self) -> None:
        """Loaded settings with opto / PdCO on open their windows, like ticking the box."""
        p = self.params()
        if p.opto_session:
            self._show_module_window("opto")
        if p.ttl_session:
            self._show_module_window("pdco")

    def _params_changed(self) -> None:
        p = self.params()
        if p.trial_duration < p.baseline_window + p.response_window:
            self.b.widgets["trial_duration"].setValue(p.baseline_window + p.response_window)
            return  # setValue triggers another change
        self._update_enabled()
        self._update_preset_state()
        if p.reward_valve_duration != getattr(self, "_volume_for_ms", None):
            self._volume_for_ms = p.reward_valve_duration
            self.refresh_reward_volume()
        if self.session is not None and self.session.running:
            self.session.set_params(p)

    def _update_enabled(self) -> None:
        w, p = self.b.widgets, self.params()
        for i in range(2, 6):
            for f in (f"wh_stim_amp_{i}", f"wh_stim_amp_mT_{i}", f"wh_stim_weight_{i}"):
                w[f].setEnabled(p.wh_stim_amp_range)
            for f in (f"light_stim_amp_{i}", f"light_stim_power_{i}", f"light_stim_weight_{i}"):
                w[f].setEnabled(p.light_stim_amp_range)
        for f in ("camera_freq", "camera_exposure_time", "camera_start_delay"):
            w[f].setEnabled(p.camera_flag)
        w["reward_delay_time"].setEnabled(p.reward_delay_flag)
        w["reward_proba"].setEnabled(p.partial_reward_flag)
        w["false_alarm_timeout"].setEnabled(p.false_alarm_punish_flag)
        w["early_lick_timeout"].setEnabled(p.early_lick_punish_flag)
        for f in ("context_block_size", "context_table_directory", "bckg_noise_directory", "context_sound_on"):
            w[f].setEnabled(p.context_flag)
        if p.context_flag and not w["wh_reward"].isChecked():
            w["wh_reward"].setChecked(True)
        w["wh_reward"].setEnabled(not p.context_flag)
        for f in ("passive_stim_flag", "passive_trial_max", "passive_iti"):
            w[f].setEnabled(p.passive_stim_flag_enable)
        self.opto_btn.setEnabled(p.opto_session)
        self.pdco_btn.setEnabled(p.ttl_session)
        self._relayout_live(p)
        self.thr_line.setValue(p.lick_threshold)
        self.live_plots["lick"].setYRange(0, max(p.lick_threshold * 5, 1e-3), padding=0)

    def _relayout_live(self, p: SessionParams) -> None:
        """Show only the signal rows that apply to this session; the lick trace gets the room."""
        show = {"lick", "trial_ttl", "cam1", "cam2"}
        if p.ttl_session:
            show.add("ttl")
        if p.twophoton_session or p.threephoton_session:
            show.add("galvo")
        visible = tuple(k for k, *_ in self.live_specs if k in show)
        if visible == self._live_visible:
            return
        self._live_visible = visible
        ci = self.live.ci
        for item in list(ci.items.keys()):
            ci.removeItem(item)
        stretch = {k: s for k, _, _, s in self.live_specs}
        for row, key in enumerate(visible):
            ci.addItem(self.live_labels[key], row=row, col=0)
            ci.addItem(self.live_plots[key], row=row, col=1)
            ci.layout.setRowStretchFactor(row, int(stretch[key] * 10))
            last = row == len(visible) - 1
            self.live_plots[key].getAxis("bottom").setStyle(showValues=last)
            self.live_plots[key].setLabel("bottom", "Time (s)" if last else "", color=theme.TEXT_2)
            if not last:
                self.live_plots[key].getAxis("bottom").setLabel("")
        for row in range(len(visible), len(self.live_specs)):
            ci.layout.setRowStretchFactor(row, 0)

    # --- module windows ----------------------------------------------------------------
    def _show_module_window(self, which: str) -> None:
        if which == "opto":
            if self.opto_window is None:
                self.opto_window = OptoWindow(self, self.rig_config, self.rig_factory)
                if self._pending_opto is not None:
                    self.opto_window.set_config(self._pending_opto)
            win = self.opto_window
        else:
            if self.pdco_window is None:
                self.pdco_window = PdcoWindow(self)
                if self._pending_pdco is not None:
                    self.pdco_window.set_config(self._pending_pdco)
            win = self.pdco_window
        win.show()
        win.raise_()
        win.activateWindow()

    def opto_config(self) -> OptoConfig | None:
        return self.opto_window.config() if self.opto_window else self._pending_opto

    def pdco_config(self) -> PdcoConfig | None:
        return self.pdco_window.config() if self.pdco_window else self._pending_pdco

    def _load_rig_file(self) -> None:
        """Pick a rig file; the GUI reopens on it and uses it from now on (also at the next launch)."""
        if self.load_rig is None:
            return
        if self.session is not None and self.session.running:
            QtWidgets.QMessageBox.warning(self, "Session running", "Stop the session before loading another rig file.")
            return
        from ..app_state import rigs_dir

        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Load rig file", str(rigs_dir()), "Rig files (*.yaml *.yml)")
        if not path:
            return
        try:
            RigConfig.load(Path(path))  # check it before closing this window
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Cannot load rig file", f"{path}\n\n{exc}")
            return
        self.load_rig(Path(path))

    def _open_valve_calibration(self) -> None:
        if self.valve_window is None:
            self.valve_window = ValveWindow(self, self.rig_config, self.rig_factory,
                                            lambda: self.session is not None and self.session.running)
        else:
            self.valve_window.load_from_task()
        self.valve_window.show()
        self.valve_window.raise_()
        self.valve_window.activateWindow()

    def _open_sound_calibration(self, noise: bool = False) -> None:
        if self.sound_window is None:
            self.sound_window = SoundWindow(self, self.rig_config, self.rig_factory,
                                            lambda: self.session is not None and self.session.running)
        else:
            self.sound_window.load_from_task()
        if noise:
            self.sound_window.show_noise_tab()
        self.sound_window.show()
        self.sound_window.raise_()
        self.sound_window.activateWindow()

    def refresh_reward_volume(self) -> None:
        """µL per reward for the current valve opening, from the rig's valve log."""
        from ..valve_calibration import log_path, read_log, volume_per_opening

        valve_ms = self.params().reward_valve_duration
        try:
            ul = volume_per_opening(read_log(log_path(self.rig_config.calibration.directory, self.rig_config.name)), valve_ms)
        except Exception:
            ul = None
        self._volume_per_reward = (ul, True) if ul is not None else (DEFAULT_VOLUME_PER_REWARD_UL, False)
        if hasattr(self, "valve_volume_label"):
            self.valve_volume_label.setText(
                f"Calibrated: {ul:.2f} µL per reward at {valve_ms:g} ms." if ul is not None else
                f"Not calibrated at {valve_ms:g} ms: reward volumes assume {DEFAULT_VOLUME_PER_REWARD_UL} µL.")
        if hasattr(self, "tiles"):
            self.tiles["reward"].label.setText("Reward (µL)" if ul is not None else "Reward (µL, not calibrated)")
            if self.rows:
                self._update_performance()

    def _open_calibration(self) -> None:
        if self.calibration_window is None:
            self.calibration_window = CalibrationWindow(
                self, self.rig_config, self.rig_factory, lambda: self.session is not None and self.session.running)
        else:
            self.calibration_window.load_from_task()
        self.calibration_window.show()
        self.calibration_window.raise_()
        self.calibration_window.activateWindow()

    # --- presets -----------------------------------------------------------------------------
    RIG_DEFAULTS = "Rig defaults"

    def _refresh_presets(self) -> None:
        self.preset_box.blockSignals(True)
        self.preset_box.clear()
        self.preset_box.addItem(self.RIG_DEFAULTS)
        if self.presets is not None:
            self.preset_box.addItems(self.presets.names())
        self.preset_box.setCurrentText(self.preset.name if self.preset else self.RIG_DEFAULTS)
        self.preset_box.blockSignals(False)
        self._update_preset_state()

    def _update_preset_state(self) -> None:
        if not hasattr(self, "preset_state"):
            return
        p = self.params()
        if self.preset is None:
            changed = any(getattr(p, k) != getattr(self._rig_defaults, k) for k in self._rig_defaults.__dataclass_fields__
                          if k not in ("date", "session_time", "mouse_weight_before", "mouse_weight_after"))
            self.preset_state.setText("● modified" if changed else "")
            self.preset_save_btn.setEnabled(False)
        else:
            self.preset_state.setText("● modified, not saved" if self.preset.differs_from(p) else "saved")
            self.preset_save_btn.setEnabled(True)
        self.delete_preset_action.setEnabled(self.preset is not None)

    def _preset_chosen(self, index: int) -> None:
        name = self.preset_box.itemText(index)
        self._select_preset(None if name == self.RIG_DEFAULTS else name)

    def _select_preset(self, name: str | None) -> None:
        if self.session is not None and self.session.running:
            QtWidgets.QMessageBox.information(self, "Session running", "Stop the session before switching presets.")
            self._refresh_presets()
            return
        current = self.params()
        keep = {"behaviour_directory": current.behaviour_directory, "date": current.date}
        if name is None:
            self.preset = None
            params = self._rig_defaults.copy(**keep)
            self._pending_opto = self._pending_pdco = None
        else:
            try:
                self.preset = self.presets.load(name)
            except Exception as exc:
                QtWidgets.QMessageBox.critical(self, "Could not load preset", str(exc))
                self._refresh_presets()
                return
            params = self.preset.apply(self._rig_defaults).copy(date=current.date)
            self._pending_opto, self._pending_pdco = self.preset.opto, self.preset.pdco
        if self.opto_window is not None:
            self.opto_window.set_config(self._pending_opto or OptoConfig())
        if self.pdco_window is not None:
            self.pdco_window.set_config(self._pending_pdco or PdcoConfig())
        if self.presets is not None:
            self.presets.set_last_used(name)
        self.set_params(params)
        self._refresh_presets()
        self.online_text.setText(f"Loaded preset '{name}'" if name else "Rig defaults loaded")

    def _save_preset(self) -> None:
        if self.presets is None:
            return
        if self.preset is None:
            self._save_preset_as()
            return
        self._write_preset(self.preset.name)

    def _save_preset_as(self) -> None:
        if self.presets is None:
            return
        name, ok = QtWidgets.QInputDialog.getText(self, "Save preset", "Preset name (e.g. your name and task):",
                                                  text=self.preset.name if self.preset else "")
        if not ok or not name.strip():
            return
        try:
            name = check_name(name)
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, "Invalid name", str(exc))
            return
        if name in self.presets.names() and (self.preset is None or name != self.preset.name):
            if QtWidgets.QMessageBox.question(self, "Overwrite preset?", f"A preset called '{name}' exists. Replace it?"
                                              ) != QtWidgets.QMessageBox.Yes:
                return
        self._write_preset(name)

    def _write_preset(self, name: str) -> None:
        try:
            self.preset = self.presets.save(name, self.params(), opto=self.opto_config(), pdco=self.pdco_config(),
                                            rig=self.rig_config.name)
            self.presets.set_last_used(name)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Could not save preset", str(exc))
            return
        self._refresh_presets()
        self.online_text.setText(f"Saved preset '{name}'")

    def _delete_preset(self) -> None:
        if self.presets is None or self.preset is None:
            return
        name = self.preset.name
        if QtWidgets.QMessageBox.question(self, "Delete preset?", f"Delete the preset '{name}'? Current settings stay as they are."
                                          ) != QtWidgets.QMessageBox.Yes:
            return
        self.presets.delete(name)
        self.preset = None
        self._refresh_presets()

    def _export_defaults(self) -> None:
        ExportDefaultsDialog(self).exec()

    def rig_defaults_changed(self) -> None:
        """The rig file's defaults were rewritten: make them what 'Rig defaults' restores."""
        from ..presets import startup_params
        from ..rig.config import RigConfig

        try:
            cfg = RigConfig.load(self.rig_config.source)
        except Exception:
            return
        self.rig_config.defaults = cfg.defaults
        current = self.params()
        self._rig_defaults, _ = startup_params({"behaviour_directory": current.behaviour_directory, **cfg.defaults}, None)
        self._update_preset_state()

    def _load_from_session(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Load settings from session_config.json",
                                                        self.b.widgets["behaviour_directory"].text(),
                                                        "Session config (session_config.json *.json)")
        if not path:
            return
        try:
            params = load_session_settings(self.params(), path)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Could not read settings", str(exc))
            return
        self.set_params(params.copy(date=self.params().date))
        self.online_text.setText("Loaded settings from a previous session")

    # --- buttons ---------------------------------------------------------------------------
    def _browse(self) -> None:
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Behaviour data folder", self.b.widgets["behaviour_directory"].text())
        if d:
            self.b.widgets["behaviour_directory"].setText(d)
            self._params_changed()

    def _start(self) -> None:
        if self.session is not None and self.session.running:
            return
        p = self.params()
        problems = p.validate()
        if problems:
            QtWidgets.QMessageBox.warning(self, "Cannot start", "\n".join(problems))
            return
        oc = self.rig_config.opto
        self.opto_module = OptoModule(self.opto_config(),
                                      galvo_step=tuple(oc.galvo_step_v_per_mm), bregma_directory=oc.bregma_directory,
                                      setup_name=self.rig_config.name)
        modules = [ContextModule(), PdcoModule(self.pdco_config()), self.opto_module]
        try:
            rig = self.rig_factory()
            if p.opto_session and hasattr(rig, "cfg") and not oc.enabled:
                raise RuntimeError(f"Optogenetics is ticked but 'opto.enabled' is false in rig file '{self.rig_config.name}'.")
            self.session = Session(rig, p, modules=modules)
            self.b.widgets["date"].setText(self.session.params.date)
            self.rows = []
            self._clear_display()
            self.session.start()
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Session failed to start", str(exc))
            if self.session is not None:
                try:
                    self.session.stop()
                except Exception:
                    pass
            self.session = None
            self._set_status("Not started", "error")
            return
        if self.pdco_window:
            self.pdco_window.attach(modules[1])
        if self.opto_window:
            self.opto_window.attach(self.opto_module)
        self._started_at = time.monotonic()
        self._set_running(True)
        self._set_status("Running", "running")
        self._plot_timer.start()

    def _stop(self) -> None:
        if self.session is None or self.session._stopped:
            return
        self.session.stop()
        self._poll_events()
        self._plot_timer.stop()
        self._set_running(False)
        self._set_status("Stopped", "stopped")
        if self.opto_window:
            self.opto_window.attach(None)

    def _pause(self) -> None:
        if self.session and self.session.running:
            self.session.pause()
            self._set_status("Pausing after this trial", "paused")

    def _resume(self) -> None:
        if self.session and self.session.running:
            self.session.resume()
            self._set_status("Running", "running")

    def _manual_reward(self) -> None:
        if self.session and self.session.running:
            self.session.manual_reward()
            self.online_text.setText("Manual reward")

    def _update_config(self) -> None:
        if self.session:
            self.session.set_params(self.params())
            self.session.save_config()
            self.online_text.setText("Saved session_config.json")

    def _set_running(self, running: bool) -> None:
        self.start_btn.setEnabled(not running)
        for w in (self.stop_btn, self.pause_btn, self.resume_btn, self.manual_reward_btn, self.update_config_btn):
            w.setEnabled(running)
        for name in ("mouse_name", "behaviour_directory", "context_flag", "context_table_directory",
                     "ttl_session", "opto_session"):
            self.b.widgets[name].setEnabled(not running)
        if hasattr(self, "preset_box"):
            self.preset_box.setEnabled(not running and self.presets is not None)

    def _set_status(self, text: str, kind: str) -> None:
        color, icon = theme.STATUS[kind]
        self.status_pill.setText(f"{icon}  {text}")
        self.status_pill.setStyleSheet(f"background: {color}")

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        if self.session is not None and self.session.running:
            answer = QtWidgets.QMessageBox.question(self, "Stop session?", "A session is running. Stop it and quit?")
            if answer != QtWidgets.QMessageBox.Yes:
                event.ignore()
                return
            self._stop()
        for win in (self.opto_window, self.pdco_window, self.calibration_window, self.valve_window, self.sound_window):
            if win is not None:
                win.close()
        event.accept()

    # --- session events ----------------------------------------------------------------------
    def _poll_events(self) -> None:
        if self.session is None:
            return
        q = self.session.events
        while not q.empty():
            kind, payload = q.get()
            if kind == "status":
                self.online_text.setText(payload)
                if payload == "Session Paused":
                    self._set_status("Paused", "paused")
            elif kind == "next_trial":
                self._show_next_trial(payload)
            elif kind == "trial_started":
                if self.session.controller and not self.session.controller.paused:
                    self._set_status("Trial running", "trial")
            elif kind == "trial_saved":
                self.rows.append(payload)
                self._update_performance()
                if self.session.controller and not self.session.controller.paused:
                    self._set_status("Running", "running")
            elif kind == "passive_finished":
                self.b.widgets["passive_stim_flag"].setChecked(False)
            elif kind == "error":
                self._plot_timer.stop()
                self._set_status("Error", "error")
                QtWidgets.QMessageBox.critical(self, "Session error", payload)
                self._stop()
        c = self.session.controller
        if c is not None and self.session.running:
            self.state_text.setText(f"Controller: {c.state.value}" + ("\nPause requested" if c.paused else ""))
            mins, secs = divmod(int(time.monotonic() - (self._started_at or time.monotonic())), 60)
            self.elapsed.setText(f"Session time {mins:02d}:{secs:02d}")

    def _show_next_trial(self, t: TrialSpec) -> None:
        details = []
        if t.is_auditory:
            title, color = "Auditory", theme.AUDITORY
            details.append(f"{t.aud_stim_amp:g} V · {t.aud_stim_duration:g} ms · {t.aud_stim_freq:g} Hz")
            rewarded = t.aud_reward
        elif t.is_whisker:
            title, color = "Whisker", theme.WHISKER
            details.append(f"{t.wh_stim_amp:g} V ({t.wh_stim_amp_mT:g} mT) · {t.wh_stim_duration:g} ms")
            rewarded = t.is_reward and t.wh_reward
        elif t.is_light:
            title, color = "Light", theme.LIGHT
            details.append(f"{t.light_amp:g} V ({t.light_power:g} mW) · {t.light_duration:g} ms")
            rewarded = t.light_reward
        else:
            title, color, rewarded = "No stimulus", theme.TEXT_3, None
        if t.is_passive:
            details.append("Passive")
        elif t.association_flag:
            details.append("Association")
        elif rewarded is not None:
            details.append("Rewarded" if rewarded else "Not rewarded")
        if t.is_opto:
            details.append("Opto ON")
        if t.context_block in CONTEXTS:
            details.append("Rewarded context" if t.wh_reward else "Non-rewarded context")
        elif t.context_block not in ("NA", ""):
            details.append(f"Context: {t.context_block}")  # passive stimulation: active / passive
        if t.pdco_activation:
            details.append({1: "PdCO ON pulse", -1: "PdCO OFF pulse", 2: "PdCO ON + OFF"}[t.pdco_activation])
        self.timeline_text.setText(f"Trial {t.trial_number}: {title}")
        self.timeline_detail.setText(" · ".join(details))
        self.next_swatch.setStyleSheet(f"color: {color}; font-size: 16pt")
        self._show_context(t)

    def _show_context(self, t: TrialSpec) -> None:
        """Banner + context trace colour: green in the rewarded context, purple in the non-rewarded one.
        Named by reward, not by noise (which noise is rewarded differs per mouse; results.csv keeps
        the noise name). Updated when a trial is planned, which is also when the context noise switches."""
        if t.context_block not in CONTEXTS:  # no context task ("NA", or passive "active"/"passive")
            self.context_banner.setVisible(False)
            return
        if t.wh_reward:
            color, name = theme.CONTEXT_REWARDED, "rewarded"
        else:
            color, name = theme.CONTEXT_NOT_REWARDED, "non-rewarded"
        self.context_banner.setText(f"{name.upper()} CONTEXT")
        self.context_banner.setStyleSheet(f"background: {color}")
        self.context_banner.setVisible(True)
        self.curves["context"].setPen(theme.pen(color))
        self.live_labels["trial_ttl"].setText(
            "Trial" + self._key(theme.TEXT, "trial") + self._key(color, name), color=theme.TEXT_2)

    def _update_performance(self) -> None:
        p = self.params()
        perf = compute_performance(self.rows)
        completed = [r for r in self.rows if r["perf"] != Perf.EARLY_LICK_OR_ASSOCIATION]
        n_early = sum(1 for r in self.rows if r["early_lick"])

        def pct(x):
            return "–" if x != x else f"{x * 100:.0f}%"

        self.tiles["trials"].set(str(len(completed)))
        self.tiles["early"].set(f"{n_early}" + (f"  ({n_early / len(self.rows) * 100:.0f}%)" if self.rows else ""))
        self.tiles["aud"].set(f"{pct(perf['aud_hit_rate'])}  ({perf['aud_stim_number']})")
        self.tiles["wh"].set(f"{pct(perf['wh_hit_rate'])}  ({perf['wh_stim_number']})")
        self.tiles["light"].set(f"{pct(perf['light_hit_rate'])}  ({perf['light_stim_number']})")
        n_catch = sum(1 for r in self.rows if not r["association_flag"] and not r["is_stim"])
        self.tiles["fa"].set(f"{pct(perf['fa_rate'])}  ({n_catch})")
        self.tiles["wh"].label.setText("Whisker hit rate" if p.wh_reward else "Whisker hit rate (not rewarded)")
        vol = compute_reward_volume(self.rows, self._volume_per_reward[0])
        wh = vol["wh"] if p.wh_reward else 0
        light = vol["light"] if p.light_reward else 0
        self.tiles["reward"].set(f"{vol['aud'] + wh + light:g}")
        self.reward_detail.setText(f"Auditory {vol['aud']:g} · Whisker {wh:g} · Light {light:g} · Association {vol['asso']:g} µL")
        if self.rows:
            last = self.rows[-1]
            text = "Early lick" if last["early_lick"] else OUTCOME_TEXT.get(last["perf"], "Trial finished")
            self.online_text.setText(f"Trial {last['trial_number']}: {text}")

        rates = sliding_rates(self.rows, p.last_recent_trials)
        x = np.arange(1, len(rates["fa"]) + 1)
        self._rates = (x, rates)
        names = {"aud": "Auditory", "wh": "Whisker", "light": "Light", "fa": "FA"}
        ends = []
        for k, curve in self.perf_curves.items():
            y = rates[k]
            curve.setData(x, y, connect="finite")
            finite = np.flatnonzero(np.isfinite(y))
            if finite.size:
                i = finite[-1]
                ends.append([y[i], k, x[i], f" {names[k]} {y[i] * 100:.0f}%"])
            else:
                self.perf_end_labels[k].setVisible(False)
        # keep end labels at least 0.08 apart so they never overlap
        ends.sort(key=lambda e: e[0])
        gap, top = 0.12, 1.03
        for j in range(1, len(ends)):
            ends[j][0] = max(ends[j][0], ends[j - 1][0] + gap)
        if ends and ends[-1][0] > top:  # out of room at the top: stack downwards instead
            ends[-1][0] = top
            for j in range(len(ends) - 2, -1, -1):
                ends[j][0] = min(ends[j][0], ends[j + 1][0] - gap)
        for y_lab, k, x_end, text in ends:
            lab = self.perf_end_labels[k]
            lab.setText(text)
            lab.setPos(x_end, y_lab)
            lab.setVisible(True)
        self.perf_key_labels["wh"].setText("Whisker hit" if p.wh_reward else "Whisker hit (not rewarded)")
        if x.size:
            self.perf_plot.setXRange(1, max(x[-1] * 1.15, 5), padding=0)
        el = early_lick_rate(self.rows, p.last_recent_trials)
        self.early_curve.setData(np.arange(1, len(el) + 1), el)

    def _perf_hover(self, evt) -> None:
        pos = evt[0]
        vb = self.perf_plot.vb
        if not getattr(self, "_rates", None) or not self.perf_plot.sceneBoundingRect().contains(pos):
            self._hover_line.setVisible(False)
            self._hover_text.setVisible(False)
            return
        x, rates = self._rates
        if not x.size:
            return
        pt = vb.mapSceneToView(pos)
        i = int(np.clip(round(pt.x()) - 1, 0, x.size - 1))

        def fmt(v):
            return "–" if not np.isfinite(v) else f"{v * 100:.0f}%"

        self._hover_line.setPos(x[i])
        self._hover_text.setText(f"Trial {x[i]}\nAuditory {fmt(rates['aud'][i])}\nWhisker {fmt(rates['wh'][i])}\n"
                                 f"Light {fmt(rates['light'][i])}\nFalse alarm {fmt(rates['fa'][i])}")
        self._hover_text.setPos(x[i], 1.0)
        self._hover_line.setVisible(True)
        self._hover_text.setVisible(True)

    def _update_live_plots(self) -> None:
        if self.session is None or self.session.logger is None:
            return
        data = self.session.logger.snapshot()
        names = self.session.rig.log_channel_names
        t = np.arange(data.shape[1]) / self.session.rig.log_rate
        step = max(1, data.shape[1] // 2500)
        t = t[::step]

        def ch(name):
            return data[names.index(name), ::step] if name in names else None

        for key in ("trial_ttl", "cam1", "cam2", "galvo", "ttl1", "ttl2"):
            y = ch(key)
            if y is not None:
                self.curves[key].setData(t, y)
        lick = ch("lick")
        if lick is not None:
            self.curves["lick"].setData(t, np.abs(lick))
        ctx = ch("context")
        if ctx is not None and self.params().context_flag:
            self.curves["context"].setData(t, ctx)
        else:
            self.curves["context"].setData([], [])

    def _clear_display(self) -> None:
        self.online_text.setText(" ")
        self.context_banner.setVisible(False)
        self.timeline_text.setText("–")
        self.timeline_detail.setText("")
        self.state_text.setText("")
        self.reward_detail.setText("")
        for tile in self.tiles.values():
            tile.set("–")
        for c in list(self.perf_curves.values()) + [self.early_curve] + list(self.curves.values()):
            c.setData([], [])
        for lab in self.perf_end_labels.values():
            lab.setVisible(False)
        self._rates = None
