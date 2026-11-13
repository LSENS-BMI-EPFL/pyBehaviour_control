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


def test_amplitude_range_shows_extra_rows(window):
    w = window.b.widgets
    for prefix, second, flag in (("wh_stim", "wh_stim_amp_mT", "wh_stim_amp_range"),
                                 ("light_stim", "light_stim_power", "light_stim_amp_range")):
        assert not w[f"{prefix}_amp_1"].isHidden() and not w[f"{second}_1"].isHidden()  # the single stimulus
        assert all(w[f"{prefix}_amp_{i}"].isHidden() and w[f"{prefix}_weight_{i}"].isHidden() for i in range(2, 6))
        w[flag].setChecked(True)
        assert not any(w[f"{prefix}_amp_{i}"].isHidden() or w[f"{second}_{i}"].isHidden() for i in range(1, 6))
        w[flag].setChecked(False)
        assert w[f"{prefix}_amp_2"].isHidden()


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


def test_context_banner_shows_rewarded_and_not_rewarded_blocks(app, window, tmp_path):
    from pybehaviour.gui import theme

    (tmp_path / "rewarded_context.csv").write_text("MouseName,RewardedContext\nCTX01,pink\n", encoding="utf-8")
    window.set_params(quick_params(wh_stim_weight_1=1, no_stim_weight=1, min_iti=200, max_iti=200,
                                   min_quiet_window=0, max_quiet_window=0, baseline_window=0, artifact_window=0,
                                   response_window=300, trial_duration=400, mouse_name="CTX01",
                                   context_flag=True, context_block_size=1, context_sound_on=False,
                                   context_table_directory=str(tmp_path), behaviour_directory=str(tmp_path)))
    assert window.context_banner.isHidden()  # the window is never shown in tests: isVisible() is always False
    window._start()
    seen = {}
    end = time.time() + 8
    while len(seen) < 2 and time.time() < end:
        pump(app, 0.05)
        if not window.context_banner.isHidden():
            seen[window.context_banner.text()] = window.context_banner.styleSheet()
    window._stop()
    assert seen == {
        "REWARDED CONTEXT": f"background: {theme.CONTEXT_REWARDED}",
        "NON-REWARDED CONTEXT": f"background: {theme.CONTEXT_NOT_REWARDED}",
    }
    # results.csv keeps the noise names (same as MATLAB): pink was rewarded for this mouse
    saved = {(r["context_block"], bool(r["wh_reward"])) for r in window.rows}
    assert saved <= {("pink", True), ("brown", False)} and saved


def _context_rows(blocks):
    """results rows: (context_block, whisker rewarded, early lick) per trial."""
    from pybehaviour.results import make_row
    from pybehaviour.trial import Perf, TrialSpec

    rows = []
    for n, (block, rewarded, early) in enumerate(blocks, start=1):
        t = TrialSpec(trial_number=n, is_stim=True, is_whisker=True, context_block=block, wh_reward=rewarded)
        perf = Perf.EARLY_LICK_OR_ASSOCIATION if early else (Perf.WHISKER_HIT if rewarded else Perf.WHISKER_MISS)
        rows.append(make_row(t, perf=perf, trial_time=n, lick_flag=0, reaction_time=0, early_lick=int(early)))
    return rows


def test_performance_plot_shades_context_blocks(app, window):
    from pybehaviour.gui import theme

    window.rows = _context_rows([("pink", True, False)] * 3 + [("pink", True, True)]  # early lick: not plotted
                                + [("brown", False, False)] * 2 + [("pink", True, False)])
    window._update_performance()
    shown = [r for r in window._context_regions if r.isVisible()]
    assert [r.getRegion() for r in shown] == [(0.5, 3.5), (3.5, 5.5), (5.5, 6.5)]  # completed trials 1-3, 4-5, 6
    colors = [r.brush.color().name() for r in shown]
    assert colors == [theme.CONTEXT_REWARDED, theme.CONTEXT_NOT_REWARDED, theme.CONTEXT_REWARDED]
    assert all(not k.isHidden() for k in window.context_keys)
    window._clear_display()
    assert not any(r.isVisible() for r in window._context_regions) and window.context_keys[0].isHidden()


def test_no_shading_without_context_task(app, window):
    window.rows = _context_rows([("NA", True, False)] * 4)
    window._update_performance()
    assert not any(r.isVisible() for r in window._context_regions) and window.context_keys[0].isHidden()


def test_no_context_banner_without_context_task(app, window, tmp_path):
    window.set_params(quick_params(wh_stim_weight_1=1, no_stim_weight=1, min_iti=200, max_iti=200,
                                   min_quiet_window=0, max_quiet_window=0, baseline_window=0, artifact_window=0,
                                   response_window=300, trial_duration=400, behaviour_directory=str(tmp_path)))
    window._start()
    assert window.session is not None and window.session.running
    pump(app, 1.0)
    window._stop()
    assert window.timeline_text.text().startswith("Trial ")
    assert window.context_banner.isHidden()


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
    assert w.preset_box.currentText() == "Default settings"
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
    assert [w.preset_box.itemText(i) for i in range(w.preset_box.count())] == ["Default settings", "A", "B"]
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
    # the run just saved is listed with the amplitude predicted for the target field
    assert cw.past_table.rowCount() == 1
    cw.target.setValue(15)
    assert cw.past_table.horizontalHeaderItem(6).text() == "V for 15 mT"
    assert float(cw.past_table.item(0, 6).text()) == pytest.approx(1.5, rel=0.05)
    cw.target.setValue(30)  # 3 V, beyond the 2 V tested
    assert cw.past_table.item(0, 6).text().endswith("*") and "extrapolated" in cw.past_note.text()
    assert "30 mT ≈ " in cw.fit_text.toPlainText()
    for x in (cw, w):
        x.close()


def test_coil_calibration_lists_past_calibrations_on_open(app, tmp_path):
    from pybehaviour.calibration import CalibrationPlan, CalibrationResult, output_folder, save_result

    plan = CalibrationPlan(amplitudes_v=[1.0, 2.0], repeats=1, baseline_ms=50, trial_ms=100)
    for date, k in (("20261101", 10.0), ("20261110", 12.5)):
        res = CalibrationResult(plan, 100.0)
        for a in plan.trial_amplitudes():
            res.add(a, plan.waveform(a) * k / 100.0)
        save_result(res, output_folder(tmp_path / "cal", "PAST01", date), "PAST01", date)
    w = make_window(tmp_path, tmp_path / "presets")
    w.rig_config.calibration.directory = str(tmp_path / "cal")
    w.set_params(w.params().copy(mouse_name="PAST01"))
    w._open_calibration()
    cw = w.calibration_window
    t = cw.past_table
    assert [t.item(r, 0).text() for r in range(t.rowCount())] == ["2026-11-10", "2026-11-01"]
    assert t.item(0, 3).text() == "1, 2" and t.horizontalHeaderItem(6).text() == "V for 25 mT"
    assert float(t.item(0, 6).text()) == pytest.approx(2.0, rel=1e-3)
    assert t.item(1, 6).text() == "2.50 *"  # 10 mT/V: 2.5 V is beyond the 2 V tested
    assert "1 V: 12.50 ± 0.00 mT" in t.item(0, 0).toolTip()
    cw.name.setText("OTHER")
    cw.name.editingFinished.emit()
    assert t.rowCount() == 0 and "No saved calibration" in cw.past_note.text()
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

def test_noise_gains_from_the_sound_calibration_window(app, tmp_path):
    from pybehaviour.modules.context import SilentPlayer

    w = make_window(tmp_path, tmp_path / "presets")
    w.set_params(w.params().copy(bckg_noise_directory=str(tmp_path), pink_noise_gain_db=-7.6))
    w._open_sound_calibration(noise=True)
    sw = w.sound_window
    assert sw.tabs.currentIndex() == 1 and sw.noise_gain["pink"].value() == -7.6
    players = []
    sw.noise_player_factory = lambda folder: players.append(SilentPlayer()) or players[-1]
    sw.noise_buttons["brown"].click()
    assert players[0].played == ["brown"] and players[0].gains == {"pink": -7.6, "brown": 0.0}
    assert "Playing brown noise at +0 dB" in sw.noise_status.text()
    sw.noise_gain["brown"].setValue(-2.5)  # heard at once, and becomes the task setting
    assert players[0].gains["brown"] == -2.5 and w.params().brown_noise_gain_db == -2.5
    assert w.b.widgets["brown_noise_gain_db"].value() == -2.5
    sw.noise_buttons["pink"].click()
    assert players[0].played == ["brown", "pink"] and not sw.noise_buttons["brown"].isChecked()
    sw.stop_noise()
    assert players[0].played[-1] == "stop"
    for x in (sw, w):
        x.close()


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
    # the button opens the file dialog (clicked's bool must not be taken as the path)
    picked = tmp_path / "from_button.yaml"
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName", lambda *a, **k: (str(picked), ""))
    dlg.save_btn.click()
    assert yaml.safe_load(picked.read_text(encoding="utf-8"))["defaults"]["min_iti"] == 4321

    monkeypatch.setattr(QtWidgets.QMessageBox, "question", lambda *a, **k: QtWidgets.QMessageBox.Yes)
    dlg.write_rig_file()
    data = yaml.safe_load(rig.read_text(encoding="utf-8"))
    assert data["defaults"]["min_iti"] == 4321 and "# bench" in rig.read_text(encoding="utf-8")
    assert (tmp_path / "rig3.yaml.bak").exists()
    # "Default settings" now restores the exported values
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


def test_opto_row_only_in_opto_sessions(app, window):
    assert "opto" not in window._live_visible
    window.b.widgets["opto_session"].setChecked(True)
    assert "opto" in window._live_visible
    window.b.widgets["opto_session"].setChecked(False)
    assert "opto" not in window._live_visible


def test_live_rows_fill_the_panel_after_a_row_change(app, window):
    """On Windows, adding the opto row left the plots squeezed to the left until a resize."""
    from PySide6 import QtCore

    window.show()
    app.processEvents()
    ci = window.live.ci
    ci.setGeometry(QtCore.QRectF(0, 0, 150, 100))  # what Qt left after the rows were re-added
    window.b.widgets["opto_session"].setChecked(True)
    app.processEvents()
    assert ci.geometry().width() == window.live.width()
    assert ci.geometry().height() == window.live.height()


def test_short_pulses_survive_plot_downsampling(app, window):
    """The live view draws ~3000 points per trace (not 50 000), and a 2 ms camera pulse still shows."""
    import numpy as np

    class Logger:
        data = np.zeros((10, 50_000))
        data[3, 20_000:20_010] = 5.0  # cam1: one 2 ms pulse at 5 kHz

        def snapshot(self):
            return self.data.copy()

    class Rig:
        log_channel_names = ["lick", "galvo", "trial_ttl", "cam1", "cam2", "context", "ttl1", "ttl2", "opto", "dummy2"]
        log_rate = 5000

    class FakeSession:
        logger, rig = Logger(), Rig()

    window._event_timer.stop()
    window.session = FakeSession()
    try:
        window._update_live_plots()
    finally:
        window.session = None
    x, y = window.curves["cam1"].getData()
    assert x.size < 4000 and y.max() == 5.0
    assert not window.curves["cam1"].opts["antialias"]  # dense traces: drawing cost


# --- whisker settings per mouse ----------------------------------------------------

def test_mouse_name_loads_its_whisker_settings(app, tmp_path):
    from pybehaviour import mouse_settings

    w = make_window(tmp_path, tmp_path / "presets")
    mouse_settings.save("rigA", "JL015", SessionParams(wh_stim_amp_1=2.5, wh_stim_amp_mT_1=21))
    name = w.b.widgets["mouse_name"]
    name.setText("JL015")
    name.editingFinished.emit()
    assert (w.params().wh_stim_amp_1, w.params().wh_stim_amp_mT_1) == (2.5, 21)
    assert "JL015" in w.online_text.text()
    w.b.widgets["min_iti"].setValue(4321)
    w._write_preset("task")
    w._select_preset(None)
    w._select_preset("task")  # presets keep the mouse's whisker settings
    assert w.params().wh_stim_amp_1 == 2.5 and w.params().min_iti == 4321
    w.b.widgets["wh_stim_amp_1"].setValue(3.0)
    w.remember_mouse_settings()
    name.setText("JL016")
    name.editingFinished.emit()
    assert w.params().wh_stim_amp_1 == 3.0 and "New mouse" in w.online_text.text()  # nothing known: kept
    name.setText("JL015")
    name.editingFinished.emit()
    assert w.params().wh_stim_amp_1 == 3.0  # the value saved last for JL015
    w.close()


def test_browse_context_table_and_noise_folder(app, tmp_path, monkeypatch):
    table = tmp_path / "ctx" / "jules_contexts.csv"  # any file name
    table.parent.mkdir()
    table.write_text("MouseName,RewardedContext\nJL001,pink\n", encoding="utf-8")
    noise = tmp_path / "noise"
    noise.mkdir()
    for n in ("pink_noise.wav", "brown_noise.wav"):
        (noise / n).write_bytes(b"")
    monkeypatch.setattr(QtWidgets.QFileDialog, "getOpenFileName", lambda *a, **k: (str(table), ""))
    monkeypatch.setattr(QtWidgets.QFileDialog, "getExistingDirectory", lambda *a, **k: str(noise))
    warnings = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", lambda *a, **k: warnings.append(a[2]))
    w = make_window(tmp_path, tmp_path / "presets")
    w.b.widgets["context_flag"].setChecked(True)
    assert w.context_table_btn.isEnabled() and w.noise_dir_btn.isEnabled()
    w.context_table_btn.click()
    w.noise_dir_btn.click()
    assert w.params().context_table_directory == str(table)
    assert w.params().bckg_noise_directory == str(noise) and not warnings
    assert "csv" in w.b.widgets["context_table_directory"].placeholderText()
    other = tmp_path / "ctx" / "other.csv"
    other.write_text("a,b\n1,2\n", encoding="utf-8")
    monkeypatch.setattr(QtWidgets.QFileDialog, "getOpenFileName", lambda *a, **k: (str(other), ""))
    w.context_table_btn.click()
    assert warnings and w.params().context_table_directory == str(table)  # not a context table: refused
    w.close()


# --- live traces ------------------------------------------------------------------------

def test_peak_decimate_keeps_short_pulses_and_few_points():
    import numpy as np

    from pybehaviour.gui.main_window import LIVE_PLOT_BINS, peak_decimate

    fs, n = 5000, 50_000  # 10 s at 5 kHz, as in the live buffer
    t = np.arange(n) / fs
    y = np.zeros(n)
    for start in range(1234, n, 9000):
        y[start:start + 10] = 5.0  # 2 ms camera pulses
    y[40_007] = -1.0  # one-sample dip
    td, yd = peak_decimate(t, y, LIVE_PLOT_BINS)
    assert td.size < 2.1 * LIVE_PLOT_BINS and np.all(np.diff(td) >= 0)  # ~3000 points, in time order
    starts = range(1234, n, 9000)
    assert all(np.any((td >= s / fs - 0.01) & (td <= s / fs + 0.01) & (yd == 5.0)) for s in starts)
    assert yd.min() == -1.0 and yd.max() == 5.0
    short = np.arange(100.0)
    assert peak_decimate(short, short, LIVE_PLOT_BINS)[1] is short  # small inputs untouched


def test_copy_today_to_server_asks_which_session(window, app, tmp_path, monkeypatch):
    from datetime import date

    from test_server_copy import make_session
    from pybehaviour.gui.server_dialog import ServerCopyDialog

    data, server = tmp_path / "data", tmp_path / "server"
    server.mkdir()
    today = date.today().strftime("%Y%m%d")
    make_session(data, "AB01", f"{today}_090000", n_trials=2)
    make_session(data, "AB01", f"{today}_140000", n_trials=200)
    make_session(data, "AB02", f"{today}_100000")
    make_session(data, "AB02", "20200101_100000")  # not today
    window.rig_config.server_directory = str(server)
    window.b.widgets["behaviour_directory"].setText(str(data))
    monkeypatch.setattr(ServerCopyDialog, "exec", lambda self: 0)

    window._copy_to_server()
    dlg = window.server_dialog
    assert dlg.undecided() == ["AB01"] and not dlg.copy_btn.isEnabled()
    dlg.choose("AB01", 1)
    assert dlg.copy_btn.isEnabled()
    assert [s.name for s in dlg.chosen()] == [f"AB01_{today}_140000", f"AB02_{today}_100000"]
    dlg.start_copy()
    end = time.time() + 10
    while dlg.running() and time.time() < end:
        pump(app, 0.05)
    pump(app, 0.2)
    assert (server / "AB01" / "Training" / f"AB01_{today}_140000" / "log_continuous.bin").is_file()
    assert (server / "AB02" / "Training" / f"AB02_{today}_100000" / "results.csv").is_file()
    assert not (server / "AB01" / "Training" / f"AB01_{today}_090000").exists()
    assert dlg.status.text().startswith("Done.") and not dlg.problems.isVisible()
    dlg.reject()


def test_copy_to_server_without_server_directory(window, monkeypatch):
    shown = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "information", lambda *a, **k: shown.append(a[1]))
    window.rig_config.server_directory = ""
    window._copy_to_server()
    assert shown == ["No server folder"]


def test_copy_to_server_waits_for_a_slow_server_without_freezing(window, app, monkeypatch):
    from PySide6 import QtCore

    from pybehaviour import server_copy

    def slow_look_up(*_):
        time.sleep(0.6)  # like a disconnected network drive
        return False, []

    shown = []
    ticks = []
    monkeypatch.setattr(server_copy, "look_up", slow_look_up)
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", lambda *a, **k: shown.append(a[1]))
    QtCore.QTimer.singleShot(400, lambda: ticks.append(1))  # only fires if the GUI keeps running events
    window.rig_config.server_directory = "//nowhere/share"
    window._copy_to_server()
    assert shown == ["Server not found"] and ticks == [1]

    # Cancel in the wait box: give up quietly
    shown.clear()
    monkeypatch.setattr(QtWidgets.QProgressDialog, "wasCanceled", lambda self: True)
    window._copy_to_server()
    assert shown == []
