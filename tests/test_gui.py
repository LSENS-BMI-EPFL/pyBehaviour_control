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
    w.close()


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
