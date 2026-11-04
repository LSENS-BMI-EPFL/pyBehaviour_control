"""GUI smoke tests (headless). Skipped if PySide6 is not installed."""

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6 import QtGui  # noqa: E402

from conftest import quick_params  # noqa: E402
from pybehaviour.params import SessionParams  # noqa: E402
from pybehaviour.rig.fake import FakeRig, RandomLicker  # noqa: E402


@pytest.fixture(scope="module")
def app():
    from pybehaviour.gui import theme

    a = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    theme.apply_app_style(a)
    return a


@pytest.fixture
def window(app, tmp_path):
    from pybehaviour.gui.main_window import MainWindow

    from pybehaviour.rig.config import RigConfig

    cfg = RigConfig(name="test")
    cfg.opto.bregma_directory = str(tmp_path / "opto")
    w = MainWindow(lambda: FakeRig(lick_source=RandomLicker(rate_hz=2, seed=1), realtime=True),
                   SessionParams(behaviour_directory=str(tmp_path)), rig_config=cfg)
    yield w
    if w.session is not None:
        w._stop()
    # destroy Qt objects now, not during interpreter shutdown (where shiboken crashed on CI)
    for child in (w.opto_window, w.pdco_window, w):
        if child is not None:
            child.close()
            child.deleteLater()
    app.processEvents()


def pump(app, seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def test_every_param_field_round_trips_through_widgets(window):
    p = quick_params(wh_stim_weight_1=3, aud_stim_weight=2, mouse_name="RT01", association_flag=True,
                     behaviour_type="whisker", lick_threshold=0.045, passive_trial_max=12, wh_stim_amp_range=True,
                     wh_stim_amp_3=-1.5, behaviour_directory=window.params().behaviour_directory)
    window.set_params(p)
    assert window.params() == p


def test_every_session_param_has_a_widget(window):
    from pybehaviour.params import PARAM_FIELDS

    missing = set(PARAM_FIELDS) - set(window.b.widgets) - {"session_time"}
    assert not missing, f"no GUI widget for: {sorted(missing)}"


def test_amplitude_range_enables_extra_rows(window):
    w = window.b.widgets
    assert not w["wh_stim_amp_2"].isEnabled()
    w["wh_stim_amp_range"].setChecked(True)
    assert w["wh_stim_amp_2"].isEnabled()


def test_trial_duration_is_raised_to_fit_windows(window):
    w = window.b.widgets
    w["baseline_window"].setValue(2000)
    w["response_window"].setValue(4000)
    w["trial_duration"].setValue(1000)
    assert w["trial_duration"].value() == 6000


def test_simulated_session_from_gui(app, window, tmp_path):
    window.set_params(quick_params(wh_stim_weight_1=1, no_stim_weight=1, min_iti=200, max_iti=200,
                                   min_quiet_window=0, max_quiet_window=0, baseline_window=0, artifact_window=0,
                                   response_window=300, trial_duration=400, mouse_name="GUI01",
                                   behaviour_directory=str(tmp_path)))
    window._start()
    assert window.session is not None and window.session.running
    pump(app, 3.0)
    assert len(window.rows) >= 2
    assert window.tiles["trials"].value.text() != "–"
    assert window.timeline_text.text().startswith("Trial ")
    assert window.online_text.text().startswith("Trial ")
    window._stop()
    assert window.start_btn.isEnabled() and not window.stop_btn.isEnabled()
    assert (window.session.folder / "results.csv").exists()


def test_pdco_window_builds_config(app, window):
    from pybehaviour.gui.pdco_window import PdcoWindow

    pw = PdcoWindow(window)
    pw.exp["Block"].setChecked(True)
    pw.block["Second half"].setChecked(True)
    pw.session_start.setChecked(True)
    cfg = pw.config()
    assert (cfg.exp_design, cfg.block_design, cfg.ttl_ch1_start) == ("Block", "Second half", "Session start")
    assert cfg.repeat_ttl_ch1 == ""  # only for Continuous
    pw.next_block.setChecked(True)
    assert not pw.session_start.isChecked()


def test_ticking_opto_opens_opto_window(app, window):
    window.b.widgets["opto_session"].setChecked(True)
    assert window.opto_window is not None and window.opto_window.isVisible()
    assert window.opto_btn.isEnabled()
    window.opto_window.close()
    window.opto_btn.click()  # reopen from the settings button
    assert window.opto_window.isVisible()


def test_ticking_pdco_opens_pdco_window(app, window):
    window.b.widgets["ttl_session"].setChecked(True)
    assert window.pdco_window is not None and window.pdco_window.isVisible()


def test_opto_window_builds_config_and_plots_grid(app, window):
    from pybehaviour.gui.opto_window import OptoWindow

    ow = OptoWindow(window, window.rig_config, window.rig_factory)
    ow.grid_box.setCurrentText("Grid 4")
    ow.fields["frequency"].setValue(20)
    ow.env.setCurrentIndex(2)
    cfg = ow.config()
    assert (cfg.grid, cfg.frequency, cfg.envelope, cfg.pulse_shape) == ("Grid 4", 20, "Ramp", "square")
    assert len(ow.planned.getData()[0]) == 45
    assert not ow.set_bregma_btn.isEnabled()
    ow.assign.setChecked(True)
    assert ow.set_bregma_btn.isEnabled() and ow.one_trial_btn.isEnabled()


def test_opto_session_from_gui(app, window, tmp_path):
    window.set_params(quick_params(wh_stim_weight_1=1, no_stim_weight=1, min_iti=200, max_iti=200,
                                   min_quiet_window=0, max_quiet_window=0, baseline_window=0, artifact_window=0,
                                   response_window=300, trial_duration=400, mouse_name="OPTO01",
                                   behaviour_directory=str(tmp_path), opto_session=True))
    window.opto_window.grid_box.setCurrentText("Grid 4")
    window._start()
    assert not window.opto_window.grid_box.isEnabled()  # grid locked during the session
    pump(app, 2.0)
    window._stop()
    folder = window.session.folder
    assert (folder / "results_opto.csv").exists() and (folder / "opto_config.json").exists()
    assert window.opto_window.grid_box.isEnabled()


def test_every_setting_is_labelled_for_screen_readers(window):
    unnamed = [n for n, w in window.b.widgets.items() if not w.accessibleName() and not getattr(w, "text", lambda: "")()]
    assert not unnamed, unnamed


def test_keyboard_shortcuts(window):
    keys = {s.key().toString() for s in window.findChildren(QtGui.QShortcut)}
    assert {"F5", "F6", "F7", "F8"} <= keys


# --- presets ----------------------------------------------------------------------

def make_window(tmp_path, preset_dir, **kw):
    from pybehaviour.gui.main_window import MainWindow
    from pybehaviour.presets import PresetStore, startup_params
    from pybehaviour.rig.config import RigConfig

    cfg = RigConfig(name="rigA")
    store = PresetStore(preset_dir)
    rig_defaults = {"behaviour_directory": str(tmp_path), "lick_threshold": 0.05}
    params, preset = startup_params(rig_defaults, store)
    base, _ = startup_params(rig_defaults, None)
    return MainWindow(lambda: FakeRig(), params, rig_config=cfg, presets=store, preset=preset, rig_defaults=base, **kw)


def test_preset_is_restored_when_the_gui_reopens(app, tmp_path):
    from pybehaviour.modules.opto import OptoConfig

    w = make_window(tmp_path, tmp_path / "presets")
    assert w.params().lick_threshold == 0.05  # rig default
    assert w.preset_box.currentText() == "Rig defaults"
    w.b.widgets["min_iti"].setValue(1234)
    w.b.widgets["opto_session"].setChecked(True)
    w.opto_window.set_config(OptoConfig(grid="Grid 3", frequency=25))
    w._write_preset("Jules psy")
    assert w.preset_state.text() == "saved"
    w.b.widgets["min_iti"].setValue(999)
    assert "modified" in w.preset_state.text()
    w.close()

    w2 = make_window(tmp_path, tmp_path / "presets")  # reopening picks the last used preset
    assert w2.preset_box.currentText() == "Jules psy"
    assert w2.params().min_iti == 1234 and w2.params().opto_session
    app.processEvents()  # opto window opens for a preset with opto on
    assert w2.opto_window is not None and w2.opto_window.config().grid == "Grid 3"
    assert w2.opto_window.config().frequency == 25
    for x in (w2.opto_window, w2):
        x.close()


def test_switching_presets_and_back_to_rig_defaults(app, tmp_path):
    w = make_window(tmp_path, tmp_path / "presets")
    w.b.widgets["no_stim_weight"].setValue(7)
    w._write_preset("A")
    w.b.widgets["no_stim_weight"].setValue(3)
    w._write_preset("B")
    w._select_preset("A")
    assert w.params().no_stim_weight == 7 and w.presets.last_used() == "A"
    w._select_preset(None)
    assert w.params().no_stim_weight == 0 and w.params().lick_threshold == 0.05
    assert w.presets.last_used() is None
    assert [w.preset_box.itemText(i) for i in range(w.preset_box.count())] == ["Rig defaults", "A", "B"]
    w.close()


def test_load_settings_from_previous_session(app, tmp_path, monkeypatch):
    import json

    cfg = tmp_path / "session_config.json"
    cfg.write_text(json.dumps({"response_window": 777, "mouse_weight_before": 22}), encoding="utf-8")
    monkeypatch.setattr(QtWidgets.QFileDialog, "getOpenFileName", lambda *a, **k: (str(cfg), ""))
    w = make_window(tmp_path, tmp_path / "presets")
    w._load_from_session()
    assert w.params().response_window == 777 and w.params().mouse_weight_before == 0
    w.close()


# --- coil calibration ---------------------------------------------------------------

def test_coil_calibration_fills_whisker_mT(app, tmp_path, monkeypatch):
    w = make_window(tmp_path, tmp_path / "presets")
    w.rig_config.calibration.directory = str(tmp_path / "cal")
    w.set_params(w.params().copy(mouse_name="CAL01", wh_stim_amp_range=True, wh_stim_amp_1=1.0, wh_stim_amp_2=2.0,
                                 wh_stim_weight_1=1, wh_stim_weight_2=1))
    w._open_calibration()
    cw = w.calibration_window
    assert [c.isChecked() for c in cw.amp_checks] == [True, True, False, False, False]
    for key, value in (("baseline_ms", 50), ("trial_ms", 100), ("pause_s", 0), ("repeats", 2)):
        cw.spins[key].setValue(value)
    cw.start()
    end = time.time() + 10
    while not cw.run.done.is_set() and time.time() < end:
        pump(app, 0.05)
    pump(app, 0.3)
    assert cw.run.error is None and cw.table.rowCount() == 2
    assert (tmp_path / "cal" / "CAL01").exists() and cw.saved_to.endswith("_stim_coil_calibration.mat")
    monkeypatch.setattr(QtWidgets.QMessageBox, "question", lambda *a, **k: QtWidgets.QMessageBox.Yes)
    cw.apply_to_task()
    p = w.params()
    assert p.wh_stim_amp_mT_1 == pytest.approx(10, rel=0.05)  # FakeRig coil: 10 mT/V
    assert p.wh_stim_amp_mT_2 == pytest.approx(20, rel=0.05)
    for x in (cw, w):
        x.close()


def test_calibration_refused_during_session(app, window, tmp_path, monkeypatch):
    shown = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", lambda *a, **k: shown.append(a[1]))
    window.set_params(quick_params(wh_stim_weight_1=1, behaviour_directory=str(tmp_path)))
    window._start()
    window._open_calibration()
    window.calibration_window.start()
    assert shown == ["Session running"] and window.calibration_window.run is None
    window._stop()
    window.calibration_window.close()


def test_tooltips_show_setting_names(window):
    assert window.b.widgets["min_iti"].toolTip().endswith("Setting name: min_iti")
    assert "Setting name: baseline_window" in window.b.widgets["baseline_window"].toolTip()


# --- valve calibration ----------------------------------------------------------------

def test_valve_calibration_end_to_end(app, tmp_path):
    w = make_window(tmp_path, tmp_path / "presets")
    w.rig_config.calibration.directory = str(tmp_path / "cal")
    w.set_params(w.params().copy(reward_valve_duration=40))
    w.refresh_reward_volume()
    assert "Not calibrated at 40 ms" in w.valve_volume_label.text()
    w._open_valve_calibration()
    vw = w.valve_window
    assert vw.valve_ms.value() == 40 and vw.n.value() == 50  # defaults: current opening, 50 times
    vw.n.setValue(4)
    vw.interval.setValue(0.05)
    vw.start()
    end = time.time() + 5
    while not vw.run.done.is_set() and time.time() < end:
        pump(app, 0.02)
    pump(app, 0.2)
    assert vw.run.count == 4 and vw.save_btn.isEnabled()
    assert [c[2] for c in vw.run.rig.calls if c[1] == "valve_open"] == [40] * 4
    vw.weight.setValue(18)
    vw.save()
    assert "4.50 µL per opening" in vw.result.text()
    assert vw.table.rowCount() == 1
    assert "Calibrated: 4.50 µL per reward at 40 ms" in w.valve_volume_label.text()
    assert w.tiles["reward"].label.text() == "Reward (µL)"
    for x in (vw, w):
        x.close()


def test_valve_suggestion_sets_opening(app, tmp_path):
    from pybehaviour.valve_calibration import ValveMeasurement, append_log, log_path

    w = make_window(tmp_path, tmp_path / "presets")
    w.rig_config.calibration.directory = str(tmp_path / "cal")
    path = log_path(tmp_path / "cal", "rigA")
    append_log(path, ValveMeasurement(40, 50, 1, 200, rig="rigA"))  # 4 µL
    append_log(path, ValveMeasurement(80, 50, 1, 400, rig="rigA"))  # 8 µL
    w._open_valve_calibration()
    vw = w.valve_window
    vw.target.setValue(6)
    assert "60.0 ms" in vw.suggestion.text()
    vw.use_suggestion()
    assert w.params().reward_valve_duration == 60
    for x in (vw, w):
        x.close()


# --- sound calibration ----------------------------------------------------------------

def test_sound_calibration_end_to_end(app, tmp_path):
    w = make_window(tmp_path, tmp_path / "presets")
    w.rig_config.calibration.directory = str(tmp_path / "cal")
    w.set_params(w.params().copy(aud_stim_amp=2.0, aud_stim_freq=8000))
    w._open_sound_calibration()
    sw = w.sound_window
    assert sw.amplitude.value() == 2.0 and sw.freq.value() == 8000  # starts from the task's tone
    assert "needs a reading at 8000 Hz" in sw.suggestion.text()
    sw.duration.setValue(20)
    sw.interval.setValue(0.2)
    sw.start()
    end = time.time() + 5
    while sw.run.count < 1 and time.time() < end:
        pump(app, 0.02)
    sw.amplitude.setValue(1.0)  # applied from the next tone
    n = sw.run.count
    while sw.run.count < n + 1 and time.time() < end:
        pump(app, 0.02)
    sw.stop()
    while not sw.run.done.is_set() and time.time() < end:
        pump(app, 0.02)
    pump(app, 0.2)
    peaks = [c[3] for c in sw.run.rig.calls if c[1] == "play_sound_now"]
    assert peaks[0] == pytest.approx(2.0, abs=0.01) and peaks[-1] == pytest.approx(1.0, abs=0.01)
    assert sw.start_btn.isEnabled() and "Stopped after" in sw.status.text()

    sw.db.setValue(64)
    sw.save()
    assert sw.table.rowCount() == 1 and "64 dB SPL" in sw.result.text()
    sw.target.setValue(70)  # +6 dB: about double
    assert sw.use_btn.isEnabled()
    sw.use_suggestion()
    assert w.params().aud_stim_amp == pytest.approx(1.995, abs=0.001)
    assert w.params().aud_stim_freq == 8000
    for x in (sw, w):
        x.close()


# --- export as rig defaults ----------------------------------------------------------

def test_export_settings_as_rig_defaults(app, tmp_path, monkeypatch):
    import yaml

    from pybehaviour.gui.export_dialog import ExportDefaultsDialog

    rig = tmp_path / "rig3.yaml"
    rig.write_text("name: rigA  # bench\ndefaults:\n  lick_threshold: 0.05\n", encoding="utf-8")
    w = make_window(tmp_path, tmp_path / "presets")
    w.rig_config.source = str(rig)
    w.b.widgets["min_iti"].setValue(4321)
    w.b.widgets["aud_reward"].setChecked(False)
    dlg = ExportDefaultsDialog(w)
    text = dlg.preview.toPlainText()
    assert "min_iti: 4321" in text and "aud_reward: false" in text
    assert "behaviour_directory" not in text  # the rig file sets it at the top level
    dlg.all.setChecked(True)
    assert "max_iti:" in dlg.preview.toPlainText()
    dlg.changed_only.setChecked(True)

    dlg.save_to_file(str(tmp_path / "exported.yaml"))
    assert yaml.safe_load((tmp_path / "exported.yaml").read_text(encoding="utf-8"))["defaults"]["min_iti"] == 4321

    monkeypatch.setattr(QtWidgets.QMessageBox, "question", lambda *a, **k: QtWidgets.QMessageBox.Yes)
    dlg.write_rig_file()
    data = yaml.safe_load(rig.read_text(encoding="utf-8"))
    assert data["defaults"]["min_iti"] == 4321 and "# bench" in rig.read_text(encoding="utf-8")
    assert (tmp_path / "rig3.yaml.bak").exists()
    # "Rig defaults" now restores the exported values
    w.b.widgets["min_iti"].setValue(1000)
    w._select_preset(None)
    assert w.params().min_iti == 4321
    for x in (dlg, w):
        x.close()


# --- mouse wheel ------------------------------------------------------------------------

def _wheel(widget, steps=-3):
    from PySide6 import QtCore

    pos = QtCore.QPointF(widget.rect().center())
    ev = QtGui.QWheelEvent(pos, QtCore.QPointF(widget.mapToGlobal(pos.toPoint())), QtCore.QPoint(0, 0),
                           QtCore.QPoint(0, 120 * steps), QtCore.Qt.NoButton, QtCore.Qt.NoModifier,
                           QtCore.Qt.NoScrollPhase, False)
    QtWidgets.QApplication.sendEvent(widget, ev)


def test_mouse_wheel_does_not_change_settings(app, window):
    window.show()
    window.settings_tabs.setCurrentIndex(2)  # Stimuli: long enough to scroll
    app.processEvents()
    spin = window.b.widgets["wh_stim_duration"]
    combo = window.b.widgets["behaviour_type"]
    before = (spin.value(), combo.currentText())
    spin.setFocus()  # even with focus
    _wheel(spin)
    _wheel(spin.lineEdit())
    _wheel(combo)
    assert (spin.value(), combo.currentText()) == before
    area = window.settings_tabs.currentWidget()
    assert area.verticalScrollBar().value() > 0  # the wheel scrolled the panel instead


def test_keyboard_still_changes_values(app, window):
    from PySide6 import QtCore
    from PySide6.QtTest import QTest

    spin = window.b.widgets["min_iti"]
    spin.setFocus()
    v = spin.value()
    QTest.keyClick(spin, QtCore.Qt.Key_Up)
    assert spin.value() == v + spin.singleStep()
